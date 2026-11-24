"""Regressions for the concrete tool, context and observability defects."""

import json
from pathlib import Path

import pytest

from src.records import Records
from src.run_logging import version_fingerprints
from src.state import StateService
from src.tools import ToolContext, ToolRunner
from tests.test_pi_agent import ScriptRuntime, make
from tests.test_records import resolve


@pytest.mark.parametrize("service", ["Snowflake", "Grafana"])
def test_known_unmonitored_preserves_directory_without_health(support_env, service):
    corpus, store, ctx = support_env
    result = ToolRunner(corpus, store).execute(ctx, "check_service_status", {"service_id": service})
    data = result["data"]
    assert result["ok"] and data["known_service"]
    assert not data["monitoring_available"] and data["health"] == "unknown"
    assert data["id"] == service.lower() and data["details"]
    assert not result["evidence_refs"]
    assert not StateService(store).memory(ctx.user_id, ctx.issue_id).current_facts


@pytest.mark.parametrize(
    "tool,args",
    [
        ("search_knowledge_base", {"query": "login"}),
        ("search_resolution_history", {"query": "login"}),
        ("lookup_policy", {}),
    ],
)
def test_unknown_filter_is_rejected_and_audited(support_env, tool, args):
    corpus, store, ctx = support_env
    result = ToolRunner(corpus, store).execute(ctx, tool, {"service_id": "Zoom", **args})
    assert not result["ok"] and result["error"]["type"] == "unknown_service"
    assert result["data"] is None and not result["evidence_refs"]
    row = store._conn.execute("SELECT result_json FROM tool_requests").fetchone()
    assert json.loads(row[0]) == result


def test_full_and_section_have_one_body_and_work_without_case(support_env):
    corpus, store, ctx = support_env
    runner = ToolRunner(corpus, store)
    unbound = ToolContext(ctx.user_id, ctx.session_id, None)
    full = runner.execute(unbound, "read_knowledge_document", {"source_id": "KB-CON-001"})
    partial = runner.execute(
        unbound, "read_knowledge_document", {"source_id": "KB-CON-001", "section": "Employee steps"}
    )
    assert full["sources"] and not full["evidence_refs"]
    assert "body" not in partial["data"]
    assert partial["data"]["section_content"] in full["data"]["body"]
    assert all("body" not in source for source in full["sources"] + partial["sources"])
    assert len(json.dumps(partial)) < len(json.dumps(full))
    missing = runner.execute(
        unbound, "read_knowledge_document", {"source_id": "KB-CON-001", "section": "invented"}
    )
    assert missing["error"]["type"] == "invalid_section"
    assert (
        store._conn.execute("SELECT count(*) FROM tool_requests WHERE issue_id IS NULL").fetchone()[
            0
        ]
        == 3
    )
    assert not StateService(store).memory(ctx.user_id, ctx.issue_id).evidence


def test_read_other_case_keeps_subsequent_queries_on_selected_case(support_env, tmp_path):
    corpus, store, ctx = support_env
    second = StateService(store).create_issue(ctx.user_id, ctx.session_id, "Okta locked", "account")
    agent = make(
        support_env,
        tmp_path,
        [
            ("select", "manage_issue", {"mode": "select", "case": "case_1"}),
            ("read", "manage_issue", {"mode": "read", "case": "case_2"}),
            ("query", "check_service_status", {"service_id": "vpn"}),
        ],
    )
    result = agent.run(
        ctx.user_id, ctx.session_id, "Compare without switching", submission_id="read-other"
    )
    assert result["status"] == "completed" and result["issue_id"] == ctx.issue_id
    assert not StateService(store).memory(ctx.user_id, second["issue_id"]).evidence
    assert StateService(store).memory(ctx.user_id, ctx.issue_id).current_facts
    snapshot = ScriptRuntime.prompts[-1][1]
    assert len(snapshot["cases"]) == 2


def test_history_quotas_and_business_time_survive_import(support_env):
    corpus, store, ctx = support_env
    draft = resolve(support_env)
    draft["symptoms"] = "VPN client disconnect"
    archive = Records(store, corpus).archive(ctx.user_id, ctx.issue_id, draft)
    runner = ToolRunner(corpus, store)
    result = runner.execute(ctx, "search_resolution_history", {"query": "VPN", "top_k": 1})
    assert len(result["data"]["personal_history"]) == 1
    assert len(result["data"]["public_examples"]) == 1
    seed = result["data"]["public_examples"][0]
    read = runner.execute(ctx, "read_resolution", {"resolution_id": seed["resolution_id"]})
    assert read["data"]["source_at"] == seed["source_at"]
    assert read["data"]["time_basis"] == "resolved_at"
    assert "document" not in read["sources"][0]
    assert archive["resolution_id"] == result["data"]["personal_history"][0]["resolution_id"]


def test_language_preference_persists_and_cancels(support_env):
    _, store, ctx = support_env
    assert (
        store.set_language(
            ctx.user_id, ctx.session_id, text="Please respond in English from now on."
        )["preference"]
        == "en"
    )
    assert (
        store.set_language(ctx.user_id, ctx.session_id, text="现在还是无法连接")["effective"]
        == "en"
    )
    assert (
        store.set_language(
            ctx.user_id, ctx.session_id, text="取消刚才固定用英文的要求，之后跟随我自然输入的语言。"
        )["preference"]
        == "auto"
    )
    assert store.set_language(ctx.user_id, ctx.session_id, text="现在仍然失败")["effective"] == "zh"


def test_versions_change_for_tool_kb_and_dependency_but_not_env(tmp_path):
    for name in ("src", "data", "pi-runtime", "scripts"):
        (tmp_path / name).mkdir()
    for name in ("src/tools.py", "data/KB.md", "uv.lock"):
        (tmp_path / name).write_text("before")
    first = version_fingerprints(tmp_path)
    for name, group in (
        ("src/tools.py", "code"),
        ("data/KB.md", "data"),
        ("uv.lock", "dependencies"),
    ):
        (tmp_path / name).write_text("after")
        after = version_fingerprints(tmp_path)
        assert after[group] != first[group] and after["build"] != first["build"]
        first = after
    (tmp_path / ".env").write_text("SECRET=never-hash-me")
    assert version_fingerprints(tmp_path) == first


@pytest.mark.parametrize(
    "query",
    json.loads((Path(__file__).parents[1] / "evals/robustness_queries.json").read_text()),
)
def test_retrieval_robustness_top_five(support_env, query):
    from src.retrieval import Retrieval

    corpus, store, _ = support_env
    found = Retrieval(corpus, store).knowledge(query["query"])
    if query["relevant"]:
        assert query["relevant"] in {row["source_id"] for row in found}
    else:
        assert not found


def test_readonly_reconciliation_finds_durable_mismatch(support_env, tmp_path, monkeypatch):
    from src.diagnostics import reconcile

    _, store, ctx = support_env
    logs = tmp_path / "logs"
    monkeypatch.setenv("RUN_LOG_DIR", str(logs))
    agent = make(support_env, tmp_path, [("query", "check_service_status", {"service_id": "vpn"})])
    agent.run(ctx.user_id, ctx.session_id, "Investigate", submission_id="audit")
    database = tmp_path / "support.sqlite"
    result = reconcile(database, logs / "events.jsonl")
    assert result["ok"] and result["checked"]["tool_results"] == 1
    store._conn.execute("UPDATE pi_tool_calls SET result_json='{}'")
    store._conn.commit()
    result = reconcile(database, logs / "events.jsonl")
    assert not result["ok"] and result["errors"][0]["kind"] == "tool_result_mismatch"


def test_nonapproval_handoff_team_matches_receipt_and_policy_team_wins(support_env, tmp_path):
    corpus, store, ctx = support_env
    agent = make(
        support_env,
        tmp_path,
        [
            ("select", "manage_issue", {"mode": "select", "case": "case_1"}),
            (
                "save",
                "save_handoff",
                {
                    "summary": "VPN failed; portal unavailable",
                    "affected_services": ["vpn"],
                    "suggested_team": "Network Support",
                },
            ),
        ],
    )
    assert (
        agent.run(
            ctx.user_id, ctx.session_id, "Save for network support", submission_id="network-handoff"
        )["status"]
        == "completed"
    )
    assert store.get_handoff(ctx.user_id, ctx.issue_id)["suggested_team"] == "Network Support"
    ToolRunner(corpus, store).execute(ctx, "lookup_policy", {"service_id": "snowflake"})
    record = Records(store, corpus).handoff(
        ctx.user_id,
        ctx.issue_id,
        {
            "summary": "Snowflake request, approval unknown",
            "affected_services": ["snowflake"],
            "policy_refs": ["POL-005"],
            "suggested_team": "Network Support",
        },
    )
    assert record["suggested_team"] == "Data Owner and Security"
    assert record["approval_routes"][0]["policy_id"] == "POL-005"


def test_cancel_fixed_english_uses_chinese_even_when_cancellation_names_english(support_env):
    _, store, ctx = support_env
    store.set_language(ctx.user_id, ctx.session_id, preference="en")
    result = store.set_language(
        ctx.user_id,
        ctx.session_id,
        text="取消固定使用英文的要求，之后跟随我自然输入的语言。现在还缺什么信息？",
    )
    assert result == {"preference": "auto", "effective": "zh"}


def test_handoff_receipt_omits_full_attachments_without_losing_durable_record(
    support_env, tmp_path
):
    import src.pi_storage as journal

    _, store, ctx = support_env
    agent = make(
        support_env,
        tmp_path,
        [
            ("select", "manage_issue", {"mode": "select", "case": "case_1"}),
            (
                "save",
                "save_handoff",
                {
                    "summary": "VPN fails",
                    "affected_services": ["vpn"],
                    "suggested_team": "Network Support",
                },
            ),
        ],
    )
    agent.run(
        ctx.user_id,
        ctx.session_id,
        "Please save a network handoff",
        submission_id="compact-receipt",
    )
    row = store._conn.execute(
        "SELECT result_json FROM pi_tool_calls WHERE call_id='save'"
    ).fetchone()
    receipt = json.loads(row[0])["data"]
    assert receipt["suggested_team"] == "Network Support"
    assert (
        not {"status", "revision", "summary_through_sequence", "record_through_sequence"}
        & receipt.keys()
    )
    assert "conversation" not in receipt and "query_audit" not in receipt
    saved = store.get_handoff(ctx.user_id, ctx.issue_id)
    assert saved["conversation"] and "query_audit" in saved
    assert not journal.is_historical_session(store, ctx.user_id, ctx.session_id)
