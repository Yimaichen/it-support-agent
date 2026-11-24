"""Regressions from A01/A04 logs; synthetic runtime tests do not grade model prose."""

import json

import pytest

from src import pi_storage as journal
from src.pi_agent import SaveHandoff
from src.records import Records
from src.state import StateService
from src.storage import AccessDenied
from src.tools import ToolContext, ToolRunner
from tests.test_pi_agent import ScriptRuntime, make


def bind(store, ctx):
    journal.ensure_schema(store)
    store._conn.execute("INSERT INTO pi_sessions VALUES (?,NULL,?)", (ctx.session_id, ctx.issue_id))


def test_followup_syncs_without_save_call_but_keeps_summary_stale(support_env, tmp_path):
    corpus, store, ctx = support_env
    bind(store, ctx)
    agent = make(support_env, tmp_path, [("save", "save_handoff", {"summary": "Reset attempted"})])
    first_result = agent.run(ctx.user_id, ctx.session_id, "Reset once", submission_id="first")
    assert first_result["status"] == "completed"
    first = store.get_handoff(ctx.user_id, ctx.issue_id)
    assert first["summary_stale"] is False
    agent = make(support_env, tmp_path)  # Deliberately no save or read tool.
    result = agent.run(
        ctx.user_id, ctx.session_id, "Reset twice; still locked", submission_id="second"
    )
    current = store.get_handoff(ctx.user_id, ctx.issue_id)
    assert current["handoff_id"] == first["handoff_id"]
    assert current["revision"] > first["revision"]
    assert current["summary"] == first["summary"]
    assert current["summary_updated_at"] == first["summary_updated_at"]
    assert current["summary_stale"]
    assert current["record_through_sequence"] > current["summary_through_sequence"]
    assert any(m["content"] == "Reset twice; still locked" for m in current["conversation"])
    assert any(m["content"] == result["reply"] for m in current["conversation"])
    assert ScriptRuntime.prompts[-1][1]["current"]["handoff"]["summary_stale"]
    saved_revision = current["revision"]
    Records(store, corpus).sync_handoff(ctx.user_id, ctx.issue_id)
    assert store.get_handoff(ctx.user_id, ctx.issue_id)["revision"] == saved_revision
    again = agent.run(
        ctx.user_id, ctx.session_id, "Reset twice; still locked", submission_id="second"
    )
    assert again == result
    assert store.get_handoff(ctx.user_id, ctx.issue_id)["revision"] == saved_revision
    agent = make(
        support_env, tmp_path, [("update", "save_handoff", {"summary": "Reset twice; locked"})]
    )
    agent.run(ctx.user_id, ctx.session_id, "Please reorganize", submission_id="third")
    updated = json.loads(Records(store, corpus).export_handoff(ctx.user_id, ctx.issue_id))
    assert updated["summary"] == "Reset twice; locked"
    assert not updated["summary_stale"]
    assert updated["summary_through_sequence"] == updated["record_through_sequence"]


def test_sync_keeps_case_and_employee_boundaries_and_does_not_create(support_env, tmp_path):
    corpus, store, ctx = support_env
    bind(store, ctx)
    records = Records(store, corpus)
    assert records.sync_handoff(ctx.user_id, ctx.issue_id) is None
    make(support_env, tmp_path, [("save", "save_handoff", {"summary": "First topic"})]).run(
        ctx.user_id, ctx.session_id, "First topic detail", submission_id="original"
    )
    first = store.get_handoff(ctx.user_id, ctx.issue_id)
    make(
        support_env,
        tmp_path,
        [("new", "manage_issue", {"mode": "create", "title": "Different access request"})],
    ).run(ctx.user_id, ctx.session_id, "Secret second topic detail", submission_id="other-topic")
    assert store.get_handoff(ctx.user_id, ctx.issue_id) == first
    with pytest.raises(AccessDenied):
        records.sync_handoff(corpus.employees[1].id, ctx.issue_id)


def test_sync_attaches_new_tool_receipts_without_rewriting_summary(support_env, tmp_path):
    corpus, store, ctx = support_env
    bind(store, ctx)
    make(support_env, tmp_path, [("save", "save_handoff", {"summary": "VPN investigation"})]).run(
        ctx.user_id, ctx.session_id, "VPN fails", submission_id="save-first"
    )
    make(support_env, tmp_path, [("status", "check_service_status", {"service_id": "vpn"})]).run(
        ctx.user_id, ctx.session_id, "Check current evidence", submission_id="query-later"
    )
    record = store.get_handoff(ctx.user_id, ctx.issue_id)
    assert record["summary"] == "VPN investigation" and record["summary_stale"]
    assert record["evidence_refs"]
    assert record["tool_attempts"][-1]["tool"] == "check_service_status"


def test_selected_policies_route_broad_queries_without_inventing_requirements(
    support_env, tmp_path
):
    corpus, store, ctx = support_env
    bind(store, ctx)
    agent = make(
        support_env,
        tmp_path,
        [
            ("snow", "lookup_policy", {"service_id": "Snowflake"}),
            ("graf", "lookup_policy", {"service_id": "Grafana"}),
            (
                "save",
                "save_handoff",
                {
                    "summary": "Employee reports manager agreed in meeting; unverified",
                    "affected_services": ["Snowflake", "Grafana"],
                    "applicable_policy_ids": ["POL-005", "POL-011"],
                },
            ),
        ],
    )
    result = agent.run(ctx.user_id, ctx.session_id, "Save incomplete draft", submission_id="access")
    assert result["status"] == "completed"
    record = store.get_handoff(ctx.user_id, ctx.issue_id)
    assert record["policy_refs"] == ["POL-005", "POL-011"]
    routes = record["approval_routes"]
    assert {r["approval_team"] for r in routes} == {"Data Owner and Security", "Analytics Owner"}
    assert "Security Response" not in json.dumps(routes)
    for route in routes:
        policy = next(p for p in corpus.policies if p.id == route["policy_id"])
        assert route["required_information"] == policy.required_information
    assert "written" not in json.dumps(routes).lower()
    assert SaveHandoff.model_validate({"summary": "Unknown details"}).applicable_policy_ids == []


def test_policy_selection_must_be_queried_in_this_case_and_service_matched(support_env):
    corpus, store, ctx = support_env
    records = Records(store, corpus)
    draft = {"summary": "Access", "affected_services": ["Snowflake"], "policy_refs": ["POL-005"]}
    other = StateService(store).create_issue(ctx.user_id, ctx.session_id, "Other", "access")
    ToolRunner(corpus, store).execute(
        ToolContext(ctx.user_id, ctx.session_id, other["issue_id"]),
        "lookup_policy",
        {"service_id": "Snowflake"},
    )
    with pytest.raises(ValueError, match="actually been queried"):
        records.handoff(ctx.user_id, ctx.issue_id, draft)
    tools = ToolRunner(corpus, store)
    tools.execute(ctx, "lookup_policy", {"service_id": "Snowflake"})
    with pytest.raises(ValueError, match="match an affected service"):
        records.handoff(ctx.user_id, ctx.issue_id, {**draft, "affected_services": ["Grafana"]})
    unknown = records.handoff(ctx.user_id, ctx.issue_id, {"summary": "Save now"})
    assert unknown["approval_routes"] == []


def test_policy_results_are_complete_and_kb_error_explains_type(support_env):
    corpus, store, ctx = support_env
    tools = ToolRunner(corpus, store)
    policies = tools.execute(ctx, "lookup_policy", {"service_id": "Snowflake"})
    assert policies["ok"]
    assert all(p["source_type"] == "policy" and p["content_complete"] for p in policies["data"])
    result = tools.execute(ctx, "read_knowledge_document", {"source_id": "POL-005"})
    assert not result["ok"] and result["error"]["type"] == "wrong_source_type"
    assert "lookup_policy" in result["error"]["detail"]
    assert not result["sources"]
    normal = tools.execute(ctx, "read_knowledge_document", {"source_id": "KB-ACC-002"})
    assert normal["ok"]
