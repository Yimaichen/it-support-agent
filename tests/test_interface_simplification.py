"""Regressions for the actual logged failures, without paid model calls."""

import json

import pytest
from pydantic import ValidationError

from src.pi_agent import SaveHandoff, schemas
from src.records import Handoff, Records
from src.state import StateService
from src.storage import AccessDenied
from src.tools import ToolContext, ToolRunner
from tests.test_pi_agent import make


@pytest.mark.parametrize(
    "names,expected",
    [
        (["Snowflake", "Grafana"], ["snowflake", "grafana"]),
        (["Zoom"], ["Zoom"]),
        (["printer"], ["printer"]),
        (["Okta", "sso"], ["sso"]),
    ],
)
def test_handoff_keeps_real_service_names(support_env, names, expected):
    corpus, store, ctx = support_env
    record = Records(store, corpus).handoff(
        ctx.user_id, ctx.issue_id, {"summary": "Please investigate", "affected_services": names}
    )
    assert record["affected_services"] == expected
    assert record["policy_refs"] == [] and record["approval_routes"] == []
    assert record["suggested_team"] == "IT Support"


def test_summary_only_pi_draft_updates_same_record_and_is_exportable(support_env, tmp_path):
    corpus, store, ctx = support_env
    agent = make(
        support_env,
        tmp_path,
        [
            ("select", "manage_issue", {"mode": "select", "case": "case_1"}),
            ("save", "save_handoff", {"summary": "VPN fails; other details unknown"}),
        ],
    )
    result = agent.run(ctx.user_id, ctx.session_id, "Save what you know", submission_id="minimal")
    assert result["status"] == "completed"
    records = Records(store, corpus)
    first = store.get_handoff(ctx.user_id, ctx.issue_id)
    assert first["impact"] == first["urgency_reason"] == first["escalation_reason"] == ""
    second = records.handoff(ctx.user_id, ctx.issue_id, {"summary": "VPN still fails on macOS 15"})
    assert first["handoff_id"] == second["handoff_id"]
    assert second["revision"] == first["revision"] + 1
    assert Handoff.model_validate_json(records.export_handoff(ctx.user_id, ctx.issue_id))
    with pytest.raises(AccessDenied):
        records.export_handoff(corpus.employees[1].id, ctx.issue_id)
    for invalid in ({}, {"summary": "   "}, {"summary": "x", "employee_id": "EMP-002"}):
        with pytest.raises(ValidationError):
            SaveHandoff.model_validate(invalid)
    schema = next(t for t in schemas() if t["name"] == "save_handoff")["parameters"]
    assert schema["required"] == ["summary"]
    assert not {"evidence_refs", "policy_refs", "employee_id"} & schema["properties"].keys()


def test_draft_automatically_attaches_empty_failed_and_successful_queries(support_env):
    corpus, store, ctx = support_env
    tools = ToolRunner(corpus, store)
    empty = tools.execute(ctx, "search_knowledge_base", {"query": "zzunlistedprinterzz"})
    unmonitored = tools.execute(ctx, "check_service_status", {"service_id": "Zoom"})
    failure = ToolRunner(corpus, store, faults={"check_service_status": "timeout"}).execute(
        ctx, "check_service_status", {"service_id": "vpn"}
    )
    profile = tools.execute(ctx, "get_current_user", {})
    other = StateService(store).create_issue(
        ctx.user_id, ctx.session_id, "Separate request", "account"
    )
    unrelated = tools.execute(
        ToolContext(ctx.user_id, ctx.session_id, other["issue_id"]),
        "lookup_policy",
        {"service_id": "grafana"},
    )
    handoff = Records(store, corpus).handoff(
        ctx.user_id, ctx.issue_id, {"summary": "Needs investigation"}
    )
    attempts = {a["invocation_id"]: a for a in handoff["tool_attempts"]}
    assert attempts[empty["invocation_id"]]["outcome"] == "empty"
    assert attempts[unmonitored["invocation_id"]]["requested_service"] == "Zoom"
    assert attempts[failure["invocation_id"]]["outcome"] == "failed"
    assert unrelated["invocation_id"] not in attempts
    assert set(profile["evidence_refs"]) <= set(handoff["evidence_refs"])
    assert set(failure["evidence_refs"]) <= set(handoff["evidence_refs"])
    assert not set(unrelated["evidence_refs"]) & set(handoff["evidence_refs"])
    assert handoff["policy_refs"] == []
    assert "zzunlistedprinterzz" not in json.dumps(handoff["observed_facts"])


def test_policy_routing_uses_only_queried_policies_for_involved_services(support_env):
    corpus, store, ctx = support_env
    tools = ToolRunner(corpus, store)
    tools.execute(ctx, "lookup_policy", {"service_id": "Snowflake"})
    tools.execute(ctx, "lookup_policy", {"service_id": "Grafana"})
    records = Records(store, corpus)
    broad = records.handoff(
        ctx.user_id,
        ctx.issue_id,
        {"summary": "Dashboard access", "affected_services": ["Grafana"]},
    )
    assert broad["policy_refs"] == [] and broad["approval_routes"] == []
    assert broad["suggested_team"] == "IT Support"
    tools.execute(
        ctx,
        "lookup_policy",
        {"service_id": "Snowflake", "resource": "production_database", "action": "grant"},
    )
    tools.execute(
        ctx,
        "lookup_policy",
        {"service_id": "Grafana", "resource": "grafana_dashboard", "action": "grant"},
    )
    record = records.handoff(
        ctx.user_id,
        ctx.issue_id,
        {
            "summary": "Dashboard access",
            "affected_services": ["Grafana"],
            "policy_refs": ["POL-011"],
        },
    )
    assert record["policy_refs"] == ["POL-011"]
    assert record["suggested_team"] == "Analytics Owner"
    assert [r["services"] for r in record["approval_routes"]] == [["grafana"]]


def test_history_groups_and_body_keep_ownership_and_same_resolution_id(support_env):
    corpus, store, ctx = support_env
    confirmation = store.append_message(
        ctx.user_id, ctx.session_id, {"role": "user", "content": "It works now"}
    )
    StateService(store).confirm_resolved(
        ctx.user_id, ctx.session_id, confirmation["message_id"], explicit_confirmation=True
    )
    record = store.save_resolution(
        ctx.user_id,
        ctx.issue_id,
        symptoms="VPN disconnects uniquepersonalexample",
        body="Employee reported reconnecting",
        result="Employee reported recovery",
        evidence_refs=[],
    )
    runner = ToolRunner(corpus, store)
    search = runner.execute(ctx, "search_resolution_history", {"query": "VPN", "top_k": 10})
    assert search["ok"]
    personal = search["data"]["personal_history"]
    public = search["data"]["public_examples"]
    assert any(h["resolution_id"] == record["resolution_id"] for h in personal)
    assert public and all(not h["belongs_to_current_employee"] for h in public)
    for header in personal + public:
        assert "source_id" not in header
        body = runner.execute(ctx, "read_resolution", {"resolution_id": header["resolution_id"]})
        assert body["ok"] and body["data"]["resolution_id"] == header["resolution_id"]
        assert body["data"]["belongs_to_current_employee"] == header["belongs_to_current_employee"]
        assert body["data"]["usage_note"] == header["usage_note"]
    other_user = corpus.employees[1].id
    other_session = store.create_session(other_user)["session_id"]
    other_ctx = ToolContext(other_user, other_session, None)
    denied = runner.execute(
        other_ctx, "read_resolution", {"resolution_id": record["resolution_id"]}
    )
    assert not denied["ok"] and denied["error"]["type"] == "unavailable"
    search = runner.execute(
        other_ctx, "search_resolution_history", {"query": "uniquepersonalexample"}
    )
    assert search["data"]["personal_history"] == []
    assert record["resolution_id"] not in json.dumps(search)


def test_empty_grouped_history_is_recorded_as_empty(support_env):
    corpus, store, ctx = support_env
    result = ToolRunner(corpus, store).execute(
        ctx, "search_resolution_history", {"query": "zznothingmatcheszz"}
    )
    assert result["data"] == {"personal_history": [], "public_examples": []}
    attempt = StateService(store).memory(ctx.user_id, ctx.issue_id).tool_attempts[-1]
    assert attempt.outcome == "empty"
