"""Late-created cases retain actual material without moving original records."""

import json

import pytest

from src import case_links
from src import pi_storage as journal
from src.pi_agent import ManageIssue
from src.records import Records
from src.state import StateService
from src.storage import AccessDenied, Store
from src.tools import ToolContext, ToolRunner
from tests.test_pi_agent import make


def late_case(env):
    corpus, store, ctx = env
    records = Records(store, corpus)
    msg = store.append_message(
        ctx.user_id,
        ctx.session_id,
        {"role": "user", "content": "My account shows ACCOUNT_LOCKED; VPN is a separate issue."},
        issue_id=ctx.issue_id,
    )
    state = StateService(store)
    state.record_user_report(ctx.user_id, ctx.issue_id, msg["message_id"])
    receipt = ToolRunner(corpus, store).execute(
        ctx, "lookup_policy", {"service_id": "sso"}, invocation_id="account-policy"
    )
    other = state.create_issue(ctx.user_id, ctx.session_id, "Account locked", "account")
    return records, msg, receipt, other


def test_late_association_repairs_real_handoff_material_and_keeps_original(support_env):
    _, store, ctx = support_env
    records, msg, receipt, other = late_case(support_env)
    before = records.handoff(ctx.user_id, other["issue_id"], {"summary": "Account needs review."})
    assert not before["query_audit"] and not before["conversation"]  # Reproduces pre-link gap.
    source = store.get_issue(ctx.user_id, ctx.issue_id)
    journal.ensure_schema(store)
    index = case_links.materials(store, ctx.user_id, ctx.session_id)
    q = next(q["query"] for q in index["queries"] if q["tool"] == "lookup_policy")
    outcome = case_links.associate(
        store,
        ctx.user_id,
        other["issue_id"],
        [msg["sequence"]],
        [q],
        "Account report and its policy were investigated before its case was opened.",
    )
    assert outcome["associated"] == 2
    after = records.sync_handoff(ctx.user_id, other["issue_id"])
    assert after["summary"] == before["summary"]
    assert [r["content"] for r in after["conversation"]] == [msg["message"]["content"]]
    assert after["query_audit"][0]["invocation_id"] == receipt["invocation_id"]
    assert len(after["material_links"]) == 2
    assert store.get_message(ctx.user_id, msg["message_id"])["issue_id"] == ctx.issue_id
    assert store.get_issue(ctx.user_id, ctx.issue_id) == source
    imported = StateService(store).memory(ctx.user_id, other["issue_id"])
    original = StateService(store).memory(ctx.user_id, ctx.issue_id)
    assert imported.evidence and imported.tool_attempts
    assert {
        (e.source_ref, e.source_at, e.queried_at, e.invocation_id)
        for e in imported.evidence.values()
    } == {
        (e.source_ref, e.source_at, e.queried_at, e.invocation_id)
        for e in original.evidence.values()
    }
    revision = store.get_issue(ctx.user_id, other["issue_id"])["revision"]
    assert (
        case_links.associate(
            store, ctx.user_id, other["issue_id"], [msg["sequence"]], [q], "retry"
        )["associated"]
        == 0
    )
    assert store.get_issue(ctx.user_id, other["issue_id"])["revision"] == revision
    exported = json.loads(records.export_handoff(ctx.user_id, other["issue_id"]))
    assert exported["material_links"] == after["material_links"]


def test_foreign_query_and_invalid_message_roll_back_all_association(support_env):
    corpus, store, ctx = support_env
    _, msg, _, other = late_case(support_env)
    for user in [ctx.user_id, corpus.employees[1].id]:
        session = store.create_session(user)["session_id"]
        ToolRunner(corpus, store).execute(
            ToolContext(user, session, None),
            "get_current_user",
            {},
            invocation_id=f"foreign-{user}",
        )
    row = store._conn.execute(
        "SELECT rowid FROM tool_requests WHERE invocation_id LIKE 'foreign-%' ORDER BY rowid DESC"
    ).fetchone()
    before = store.get_issue(ctx.user_id, other["issue_id"])
    with pytest.raises(AccessDenied):
        case_links.associate(
            store,
            ctx.user_id,
            other["issue_id"],
            [msg["sequence"]],
            [f"query_{row[0]}"],
            "invalid cross-session material",
        )
    assert not case_links.links(store, ctx.user_id, other["issue_id"])
    assert store.get_issue(ctx.user_id, other["issue_id"]) == before
    with pytest.raises(AccessDenied):
        case_links.associate(
            store,
            corpus.employees[1].id,
            other["issue_id"],
            [msg["sequence"]],
            [],
            "wrong employee",
        )


def test_associated_receipts_survive_restart_without_entire_session_copy(support_env):
    corpus, store, ctx = support_env
    _, msg, _, other = late_case(support_env)
    irrelevant = store.append_message(
        ctx.user_id,
        ctx.session_id,
        {"role": "user", "content": "VPN drops every ten minutes."},
        issue_id=ctx.issue_id,
    )
    case_links.associate(
        store, ctx.user_id, other["issue_id"], [msg["sequence"]], [], "account report only"
    )
    reopened = Store(store._conn.execute("PRAGMA database_list").fetchone()[2], clock=store.clock)
    try:
        result = Records(reopened, corpus).handoff(
            ctx.user_id, other["issue_id"], {"summary": "Account needs review."}
        )
        texts = [m["content"] for m in result["conversation"]]
        assert msg["message"]["content"] in texts
        assert irrelevant["message"]["content"] not in texts
        assert result["material_links"][0]["source_issue_id"] == ctx.issue_id
    finally:
        reopened.close()


def test_query_target_and_read_keep_selection_consistent(support_env, tmp_path):
    _, store, ctx = support_env
    agent = make(
        support_env,
        tmp_path,
        [
            (
                "create-account",
                "manage_issue",
                {"mode": "create", "title": "Account", "category": "account"},
            ),
            ("target-vpn", "check_service_status", {"case": "case_1", "service_id": "vpn"}),
            ("read-account", "manage_issue", {"mode": "read", "case": "case_2"}),
        ],
    )
    result = agent.run(
        ctx.user_id, ctx.session_id, "Check VPN and account separately.", submission_id="target"
    )
    assert not result["error"]
    assert result["issue_id"] == ctx.issue_id
    saved = store._conn.execute(
        "SELECT issue_id FROM tool_requests WHERE invocation_id='target-vpn'"
    ).fetchone()
    assert saved[0] == ctx.issue_id
    before = len(store.list_messages(ctx.user_id, ctx.session_id))
    replay = agent.run(
        ctx.user_id, ctx.session_id, "Check VPN and account separately.", submission_id="target"
    )
    assert replay == result
    assert len(store.list_messages(ctx.user_id, ctx.session_id)) == before


def test_create_with_selected_material_is_atomic_and_replay_safe(support_env, tmp_path):
    _, store, ctx = support_env
    agent = make(
        support_env,
        tmp_path,
        [
            ("policy", "lookup_policy", {"service_id": "sso"}),
            ("index", "manage_issue", {"mode": "read"}),
            (
                "new",
                "manage_issue",
                {
                    "mode": "create",
                    "title": "Account",
                    "category": "account",
                    "message_sequences": [1],
                    "queries": ["query_1"],
                    "association_reason": "Original account report and receipt.",
                },
            ),
            ("save", "save_handoff", {"summary": "Account requires review."}),
        ],
    )
    result = agent.run(
        ctx.user_id, ctx.session_id, "ACCOUNT_LOCKED; keep VPN separate.", submission_id="late"
    )
    assert not result["error"] and result["issue_id"] != ctx.issue_id
    record = store.get_handoff(ctx.user_id, result["issue_id"])
    assert record["conversation"][0]["content"].startswith("ACCOUNT_LOCKED")
    assert record["query_audit"][0]["invocation_id"] == "policy"
    assert len(record["material_links"]) == 2
    assert (
        store.get_message(
            ctx.user_id, store.list_messages(ctx.user_id, ctx.session_id)[0]["message_id"]
        )["issue_id"]
        is None
    )
    agent.run(
        ctx.user_id, ctx.session_id, "ACCOUNT_LOCKED; keep VPN separate.", submission_id="late"
    )
    assert store._conn.execute("SELECT COUNT(*) FROM case_material_links").fetchone()[0] == 2


def test_read_cannot_associate_and_same_turn_distinct_category_is_not_renamed(
    support_env, tmp_path
):
    _, store, ctx = support_env
    with pytest.raises(ValueError):
        ManageIssue(mode="read", message_sequences=[1], extra_unknown=True)
    agent = make(
        support_env,
        tmp_path,
        [
            ("profile", "get_current_user", {}),
            (
                "network",
                "manage_issue",
                {"mode": "create", "title": "VPN", "category": "connectivity"},
            ),
            ("status", "check_service_status", {"service_id": "vpn"}),
            (
                "account",
                "manage_issue",
                {"mode": "create", "title": "Account", "category": "account"},
            ),
        ],
    )
    agent.run(ctx.user_id, ctx.session_id, "VPN and account problems.", submission_id="two")
    assert [i["category"] for i in store.list_issues(ctx.user_id, ctx.session_id)] == [
        "connectivity",
        "connectivity",
        "account",
    ]


def test_rejected_create_rolls_back_case_and_in_memory_selection(support_env, tmp_path):
    from tests.test_pi_agent import ScriptRuntime

    class RejectRuntime(ScriptRuntime):
        def prompt(self, text, context, submission_id, dispatch, notify):
            result = dispatch(
                "manage_issue",
                {
                    "mode": "create",
                    "title": "Account",
                    "category": "account",
                    "message_sequences": [1],
                    "queries": ["query_999999"],
                    "association_reason": "invalid query reference",
                },
                "bad-link",
            )
            assert not result["ok"]
            after = dispatch("manage_issue", {"mode": "read"}, "read-after-rollback")
            assert after["ok"] and "current" not in after["data"]
            assert not any(c["selected"] for c in after["data"]["cases"])
            return {"reply": "No new case was created."}

    _, store, ctx = support_env
    agent = make(support_env, tmp_path)
    agent.runtime_factory = RejectRuntime
    response = agent.run(ctx.user_id, ctx.session_id, "Account problem", submission_id="reject")
    assert not response["error"]
    assert response["issue_id"] is None
    assert len(store.list_issues(ctx.user_id, ctx.session_id)) == 1
    assert store._conn.execute("SELECT COUNT(*) FROM case_material_links").fetchone()[0] == 0
