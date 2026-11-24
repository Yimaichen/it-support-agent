"""T12 cross-session/history/current-source boundaries, not model quality scores."""

import json

import pytest

from src.records import Records
from src.retrieval import Retrieval
from src.state import StateService
from src.storage import AccessDenied
from src.tools import ToolRunner
from tests.scenarios import archive_success, new_context


def test_old_personal_permissions_are_not_new_authorization(offline_env):
    corpus, store, ctx = offline_env
    store.append_message(
        ctx.user_id,
        ctx.session_id,
        {"role": "user", "content": "Last month I had production database admin and approval."},
    )
    fresh = new_context(offline_env)
    runner = ToolRunner(corpus, store)
    history = runner.execute(fresh, "get_my_support_history", {"session_id": ctx.session_id})
    state = StateService(store)
    assert history["ok"] and "database admin" in json.dumps(history["data"])
    memory = state.memory(fresh.user_id, fresh.issue_id)
    assert memory.historical_clues and memory.current_facts == {} and memory.confirmation is None
    before = memory.model_dump()
    with pytest.raises(ValueError, match="cannot populate"):
        state.record_tool_result(
            fresh.user_id,
            fresh.issue_id,
            tool="get_my_support_history",
            source_ref=ctx.session_id,
            source_at=corpus.reference_time,
            summary="Old grant",
            success=True,
            topic="permissions",
            subject="production_database",
            value="admin",
        )
    assert state.memory(fresh.user_id, fresh.issue_id).model_dump() == before
    profile = runner.execute(fresh, "get_current_user", {})
    assert "not live IAM or approval" in profile["data"]["limitations"]
    policy = runner.execute(
        fresh,
        "lookup_policy",
        {"service_id": "data_pipeline", "resource": "production_database", "action": "grant"},
    )
    assert policy["data"][0]["decision"] == "approval_required"
    assert store.get_issue(fresh.user_id, fresh.issue_id)["status"] == "investigating"
    assert {s["function"]["name"] for s in runner.schemas()}.isdisjoint(
        {"grant", "approve", "execute_sql"}
    )


def test_historical_success_and_old_baseline_do_not_become_current_facts(offline_env):
    corpus, store, ctx = offline_env
    archive_success(offline_env, "recurrentvpn42 old resolved case")
    fresh = new_context(offline_env)
    runner = ToolRunner(corpus, store)
    history = runner.execute(fresh, "search_resolution_history", {"query": "recurrentvpn42"})
    assert history["ok"] and any(
        r["source_type"] == "historical" for r in history["data"]["personal_history"]
    )
    old_history = runner.execute(
        fresh,
        "search_resolution_history",
        {"query": "Old VPN client upgrade resolved disconnects", "top_k": 10},
    )
    old_case = next(
        item
        for item in old_history["data"]["public_examples"]
        if item["resolution_id"] == "HIST-003"
    )
    assert "summary" in old_case and "document" not in old_case
    body = runner.execute(fresh, "read_resolution", {"resolution_id": old_case["resolution_id"]})
    assert "5.0" in json.dumps(body["data"], ensure_ascii=False)
    state = StateService(store)
    memory = state.memory(fresh.user_id, fresh.issue_id)
    assert memory.historical_clues and memory.current_facts == {} and memory.hypotheses == []
    assert memory.confirmation is None
    baseline = runner.execute(fresh, "check_service_status", {"service_id": "vpn"})
    assert baseline["data"]["status"] == "healthy" and "6.2" in json.dumps(baseline["data"])
    memory = state.memory(fresh.user_id, fresh.issue_id)
    fact = memory.current_facts["service_status:vpn:global"]
    assert fact.value == "healthy"
    assert memory.evidence[fact.evidence_ref].kind == "current_observation"
    assert {clue.evidence_ref for clue in memory.historical_clues}.isdisjoint({fact.evidence_ref})
    assert store.get_issue(fresh.user_id, fresh.issue_id)["status"] == "investigating"


def test_timeout_after_healthy_is_unknown_then_fresh_query_can_recover(offline_env):
    corpus, store, ctx = offline_env
    runner = ToolRunner(corpus, store)
    healthy = runner.execute(ctx, "check_service_status", {"service_id": "vpn"})
    failed = ToolRunner(corpus, store, faults={"check_service_status": "timeout"}).execute(
        ctx, "check_service_status", {"service_id": "vpn"}
    )
    state = StateService(store)
    fact = state.memory(ctx.user_id, ctx.issue_id).current_facts["service_status:vpn:global"]
    assert healthy["ok"] and failed["ok"] is False and failed["data"] is None
    assert failed["error"] == {"type": "timeout", "retryable": True}
    assert (
        fact.value is None
        and not state.memory(ctx.user_id, ctx.issue_id).evidence[fact.evidence_ref].success
    )
    assert failed["invocation_id"] != healthy["invocation_id"]
    recovered = runner.execute(ctx, "check_service_status", {"service_id": "vpn"})
    assert recovered["ok"]
    assert (
        state.memory(ctx.user_id, ctx.issue_id).current_facts["service_status:vpn:global"].value
        == "healthy"
    )
    assert store.get_issue(ctx.user_id, ctx.issue_id)["status"] != "resolved"


def test_expired_guidance_is_explicit_and_never_a_current_observation(offline_env):
    corpus, store, ctx = offline_env
    expired = next(
        doc
        for doc in corpus.knowledge
        if doc.valid_until and doc.valid_until < corpus.reference_time
    )
    result = ToolRunner(corpus, store).execute(
        ctx, "search_knowledge_base", {"query": expired.title, "top_k": 10}
    )
    match = next(item for item in result["data"] if item["source_id"] == expired.id)
    assert match["expired"] is True and match["source_at"] and match["valid_until"]
    memory = StateService(store).memory(ctx.user_id, ctx.issue_id)
    receipts = [e for e in memory.evidence.values() if e.source_ref == expired.id]
    assert receipts and all(e.kind == "guidance" for e in receipts)
    assert memory.current_facts == {} and memory.confirmation is None


def test_reopening_withdraws_success_from_retrieval_and_stale_confirmation(offline_env):
    corpus, store, ctx = offline_env
    record = archive_success(offline_env, "withdrawncase42 VPN")
    state = StateService(store)
    old_confirmation = state.memory(ctx.user_id, ctx.issue_id).confirmation.message_id
    message = store.append_message(
        ctx.user_id, ctx.session_id, {"role": "user", "content": "It failed again."}
    )
    state.reopen(ctx.user_id, ctx.issue_id, message["message_id"])
    assert Retrieval(corpus, store).resolutions(ctx.user_id, "withdrawncase42") == []
    with pytest.raises(AccessDenied):
        store.get_resolution(ctx.user_id, record["resolution_id"])
    original = record["document"]
    # Use real evidence from the previously successful archive. Rejection must
    # come from lifecycle state, not an empty-list schema failure.
    with pytest.raises(ValueError, match="only employee-confirmed success can be archived"):
        Records(store, corpus).archive(
            ctx.user_id,
            ctx.issue_id,
            {"symptoms": "VPN", "evidence_refs": original["evidence_refs"]},
        )
    # A replay can be a harmless no-op, but must never restore success.
    state.confirm_resolved(
        ctx.user_id,
        ctx.session_id,
        old_confirmation,
        issue_id=ctx.issue_id,
        explicit_confirmation=True,
    )
    assert store.get_issue(ctx.user_id, ctx.issue_id)["status"] == "investigating"
    assert state.memory(ctx.user_id, ctx.issue_id).confirmation is None
    assert Retrieval(corpus, store).resolutions(ctx.user_id, "withdrawncase42") == []


pytestmark = pytest.mark.legacy_ledger
