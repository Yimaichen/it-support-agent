import pytest

from src.state import StateService
from src.tools import ToolContext, ToolRunner


@pytest.mark.parametrize(
    "name,args",
    [
        ("shell", {"command": "ls"}),
        ("get_current_user", {"user_id": "EMP-002"}),
        ("search_knowledge_base", {"query": "VPN", "top_k": "5"}),
        ("check_service_status", {"service_id": "   "}),
        ("lookup_policy", {"service_id": "vpn", "admin_override": True}),
    ],
)
def test_tool_validation(support_env, name, args):
    corpus, store, ctx = support_env
    result = ToolRunner(corpus, store).execute(ctx, name, args)
    assert not result["ok"] and not result["evidence_refs"]
    assert result["error"]["type"] in {"unknown_tool", "invalid_arguments"}


def test_open_world_service_status_and_exam_aliases(support_env):
    corpus, store, ctx = support_env
    runner = ToolRunner(corpus, store)
    unlisted = runner.execute(ctx, "check_service_status", {"service_id": "unknown"})
    assert unlisted["ok"] and not unlisted["evidence_refs"]
    assert unlisted["data"]["status"] == "not_monitored"
    assert unlisted["data"]["known_service"] is False
    assert (
        runner.execute(ctx, "lookup_policy", {"service_id": "Zoom"})["error"]["type"]
        == "unknown_service"
    )

    okta = runner.execute(ctx, "check_service_status", {"service_id": "Okta"})
    assert okta["ok"] and okta["data"]["id"] == "sso"
    snowflake = runner.execute(ctx, "lookup_policy", {"service_id": "Snowflake"})
    assert any(p["id"] == "POL-005" for p in snowflake["data"])
    grafana = runner.execute(ctx, "lookup_policy", {"service_id": "Grafana"})
    assert any(p["id"] == "POL-011" for p in grafana["data"])


def test_employee_devices_resource_scope_no_approval_inference(support_env):
    corpus, store, ctx = support_env
    result = ToolRunner(corpus, store).execute(ctx, "get_current_user", {})
    assert result["ok"]
    assert len(result["data"]["devices"]) == len(corpus.employees[0].devices)
    assert "resource_id" in result["data"]["permissions"][0]
    assert "not live IAM or approval" in result["data"]["limitations"]
    assert result["evidence_refs"]
    assert all(
        e.kind == "current_observation"
        for e in StateService(store).memory(ctx.user_id, ctx.issue_id).evidence.values()
    )


def test_regions_and_failure_do_not_infer_healthy(support_env):
    corpus, store, ctx = support_env
    runner = ToolRunner(corpus, store)
    for region, expected in [
        ("Chicago", "degraded"),
        ("London", "healthy"),
        ("Unknown", "unknown"),
        (None, "unknown"),
    ]:
        result = runner.execute(
            ctx, "check_service_status", {"service_id": "salesforce", "region": region}
        )
        assert result["data"]["status"] == expected
        if region == "Chicago":
            assert any(c["id"] == "CHG-003" for c in result["data"]["changes"])
    failed = ToolRunner(corpus, store, faults={"check_service_status": "timeout"}).execute(
        ctx, "check_service_status", {"service_id": "salesforce", "region": "Chicago"}
    )
    assert failed["ok"] is False and failed["data"] is None and failed["error"]["type"] == "timeout"
    facts = StateService(store).memory(ctx.user_id, ctx.issue_id).current_facts
    assert facts["service_status:salesforce:Chicago"].value is None


def test_policy_structured_match_not_every_rule_escalates(support_env):
    corpus, store, ctx = support_env
    runner = ToolRunner(corpus, store)
    result = runner.execute(
        ctx, "lookup_policy", {"service_id": "vpn", "resource": "vpn_client", "action": "upgrade"}
    )
    assert [p["id"] for p in result["data"]] == ["POL-004"]
    assert result["data"][0]["decision"] == "guide"
    no_match = runner.execute(
        ctx, "lookup_policy", {"service_id": "vpn", "action": "grant_superpowers"}
    )
    assert no_match["ok"] and no_match["data"] == [] and no_match["evidence_refs"] == []
    failed = ToolRunner(corpus, store, faults={"lookup_policy": "unavailable"}).execute(
        ctx, "lookup_policy", {"service_id": "vpn"}
    )
    assert not failed["ok"] and failed["sources"] == []


def test_identity_requirement_is_not_completed_by_local_handoff(support_env):
    corpus, store, ctx = support_env
    runner = ToolRunner(corpus, store)
    before = runner.execute(ctx, "lookup_policy", {"policy_id": "POL-002"})
    assert before["data"][0]["identity_verification"] == {
        "required": True,
        "completed": False,
    }
    store.save_handoff(ctx.user_id, ctx.issue_id, {"summary": "Awaiting Identity Support"})
    after = runner.execute(ctx, "lookup_policy", {"policy_id": "POL-002"})
    assert after["data"][0]["identity_verification"]["completed"] is False
    routine = runner.execute(ctx, "lookup_policy", {"policy_id": "POL-004"})
    assert routine["data"][0]["identity_verification"] == {
        "required": False,
        "completed": None,
    }


def test_personal_history_new_session_but_no_cross_owner(support_env):
    corpus, store, ctx = support_env
    old = store.create_session(ctx.user_id)["session_id"]
    store.append_message(ctx.user_id, old, {"role": "user", "content": "Prior failed VPN attempt"})
    runner = ToolRunner(corpus, store)
    result = runner.execute(ctx, "get_my_support_history", {"session_id": old})
    assert "Prior failed" in result["data"][0]["messages"][0]["content"]
    memory = StateService(store).memory(ctx.user_id, ctx.issue_id)
    assert memory.historical_clues and not memory.current_facts
    stranger = store.create_session(corpus.employees[1].id)["session_id"]
    denied = runner.execute(ctx, "get_my_support_history", {"session_id": stranger})
    absent = runner.execute(ctx, "get_my_support_history", {"session_id": "does-not-exist"})
    assert denied["error"] == absent["error"]
    wrong_context = ToolContext(corpus.employees[1].id, ctx.session_id, ctx.issue_id)
    assert not runner.execute(wrong_context, "get_current_user", {})["ok"]
    assert {t["function"]["name"] for t in runner.schemas()} == {
        "search_knowledge_base",
        "search_resolution_history",
        "read_resolution",
        "read_knowledge_document",
        "get_current_user",
        "lookup_policy",
        "get_my_support_history",
        "check_service_status",
    }


def test_removed_device_or_permission_becomes_unknown(support_env):
    corpus, store, ctx = support_env
    runner = ToolRunner(corpus, store)
    runner.execute(ctx, "get_current_user", {})
    employee = corpus.employees[0]
    removed = employee.permissions[0]
    subject = f"{removed.service_id}:{removed.resource_type}:{removed.resource_id}:{removed.role}"
    corpus.employees[0] = employee.model_copy(update={"permissions": employee.permissions[1:]})
    runner.execute(ctx, "get_current_user", {})
    fact = (
        StateService(store)
        .memory(ctx.user_id, ctx.issue_id)
        .current_facts[f"permissions:{subject}"]
    )
    assert fact.value is None
