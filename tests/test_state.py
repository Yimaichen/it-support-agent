from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.state import ClarificationRequired, IssueMemory, StateService, Status
from src.storage import AccessDenied, ConflictError, Store

NOW = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)


@pytest.fixture
def setup(tmp_path):
    with Store(tmp_path / "state.sqlite", clock=lambda: NOW) as store:
        service = StateService(store)
        sid = store.create_session("EMP-001")["session_id"]
        a = service.create_issue("EMP-001", sid, "VPN", "connectivity", symptoms=["Disconnects"])
        yield store, service, sid, a


def user_message(store, sid, text="VPN now works", issue_id=None):
    return store.append_message(
        "EMP-001", sid, {"role": "user", "content": text}, issue_id=issue_id
    )["message_id"]


def propose(store, service, issue, **proposal):
    current = store.get_issue("EMP-001", issue["issue_id"])
    return service.propose(
        "EMP-001", issue["issue_id"], proposal, expected_revision=current["revision"]
    )


def observe(service, issue, **overrides):
    data = {
        "tool": "check_service_status",
        "source_ref": "vpn",
        "source_at": NOW,
        "summary": "VPN available",
        "success": True,
        "topic": "service_status",
        "subject": "vpn:London",
        "value": "healthy",
        "invocation_id": str(uuid4()),
        "queried_at": NOW,
    }
    return service.record_tool_result("EMP-001", issue["issue_id"], **(data | overrides))


def test_multiple_issues_need_explicit_confirmation_target(setup):
    store, service, sid, a = setup
    b = service.create_issue("EMP-001", sid, "Production access", "access")
    assert service.next_issue("EMP-001", sid)["issue_id"] == a["issue_id"]
    msg = user_message(store, sid, "好了")
    with pytest.raises(ClarificationRequired):
        service.confirm_resolved("EMP-001", sid, msg, explicit_confirmation=True)
    assert all(i["status"] == Status.INVESTIGATING for i in store.list_issues("EMP-001", sid))
    resolved = service.confirm_resolved(
        "EMP-001", sid, msg, issue_id=a["issue_id"], explicit_confirmation=True
    )
    assert resolved["status"] == Status.RESOLVED
    assert service.next_issue("EMP-001", sid)["issue_id"] == b["issue_id"]
    assert store.get_issue("EMP-001", b["issue_id"])["status"] == Status.INVESTIGATING
    assert (
        service.confirm_resolved(
            "EMP-001", sid, msg, issue_id=a["issue_id"], explicit_confirmation=True
        )["revision"]
        == resolved["revision"]
    )


def test_suggestions_silence_and_model_proposals_cannot_resolve(setup):
    store, service, sid, a = setup
    service.add_step("EMP-001", a["issue_id"], "Check client version")
    assert (
        propose(store, service, a, status="awaiting_verification")["status"]
        == "awaiting_verification"
    )
    with pytest.raises(ValueError, match="proposals cannot resolve"):
        propose(store, service, a, status="resolved")
    msg = user_message(store, sid, "I will try later")
    with pytest.raises(ValueError, match="explicit"):
        service.confirm_resolved("EMP-001", sid, msg)
    assert store.get_issue("EMP-001", a["issue_id"])["status"] == "awaiting_verification"


@pytest.mark.parametrize("role", ["assistant", "system"])
def test_confirmation_must_reference_employee_not_model(setup, role):
    store, service, sid, a = setup
    msg = store.append_message("EMP-001", sid, {"role": role, "content": "Solved"})
    with pytest.raises(ValueError, match="user message"):
        service.confirm_resolved("EMP-001", sid, msg["message_id"], explicit_confirmation=True)


def test_confirmation_cannot_reference_other_session_or_issue(setup):
    store, service, sid, a = setup
    second = store.create_session("EMP-001")["session_id"]
    msg = user_message(store, second)
    with pytest.raises(ValueError):
        service.confirm_resolved("EMP-001", sid, msg, explicit_confirmation=True)
    b = service.create_issue("EMP-001", sid, "App", "application")
    msg = user_message(store, sid, issue_id=b["issue_id"])
    with pytest.raises(ValueError):
        service.confirm_resolved(
            "EMP-001", sid, msg, issue_id=a["issue_id"], explicit_confirmation=True
        )
    with pytest.raises(AccessDenied):
        service.select_target("EMP-001", second, a["issue_id"])


def test_historical_receipts_never_become_current_facts(setup):
    _, service, _, a = setup
    old = NOW - timedelta(days=30)
    service.record_tool_result(
        "EMP-001",
        a["issue_id"],
        tool="search_resolution_history",
        source_ref="HIST-003",
        source_at=old,
        summary="Old VPN supported 5.0",
        success=True,
    )
    memory = service.memory("EMP-001", a["issue_id"])
    assert len(memory.historical_clues) == 1 and memory.current_facts == {}
    assert next(iter(memory.evidence.values())).source_at == old
    with pytest.raises(ValueError, match="cannot populate"):
        service.record_tool_result(
            "EMP-001",
            a["issue_id"],
            tool="get_my_support_history",
            source_ref="old-session",
            source_at=old,
            summary="Old permissions",
            success=True,
            topic="permissions",
            subject="employee",
            value="admin",
        )
    raw = memory.model_dump(mode="json")
    ref = next(iter(memory.evidence))
    raw["current_facts"] = {
        "device:vpn": {
            "topic": "device",
            "subject": "vpn",
            "value": "5.0",
            "evidence_ref": ref,
            "source_at": old.isoformat(),
            "queried_at": NOW.isoformat(),
        }
    }
    with pytest.raises(ValidationError, match="current-query"):
        IssueMemory.model_validate(raw)


def test_query_failure_marks_unknown_instead_of_reusing_old_value(setup):
    _, service, _, a = setup
    observe(service, a)
    assert (
        service.memory("EMP-001", a["issue_id"]).current_facts["service_status:vpn:London"].value
        == "healthy"
    )
    observe(service, a, success=False, summary="Timed out", value="healthy")
    fact = service.memory("EMP-001", a["issue_id"]).current_facts["service_status:vpn:London"]
    assert fact.value is None
    assert len(service.memory("EMP-001", a["issue_id"]).evidence) == 2


def test_user_reports_remain_reports_not_verified_directory_facts(setup):
    store, service, sid, a = setup
    msg = user_message(store, sid, "I'm an administrator now")
    service.record_user_report("EMP-001", a["issue_id"], msg)
    service.record_user_report("EMP-001", a["issue_id"], msg)
    memory = service.memory("EMP-001", a["issue_id"])
    assert len(memory.user_reports) == 1
    assert memory.current_facts == {}


def test_waiting_states_require_their_actual_prerequisites(setup):
    store, service, _, a = setup
    with pytest.raises(ValueError, match="question"):
        propose(store, service, a, status="awaiting_user")
    assert propose(store, service, a, status="awaiting_user", open_questions=["Which version?"])
    with pytest.raises(ValueError, match="provided steps"):
        propose(store, service, a, status="awaiting_verification")
    observe(service, a)
    with pytest.raises(ValueError, match="incident evidence"):
        propose(store, service, a, status="awaiting_service_recovery")
    observe(service, a, value="degraded", summary="Regional degradation")
    assert (
        propose(store, service, a, status="awaiting_service_recovery")["status"]
        == "awaiting_service_recovery"
    )
    assert service.memory("EMP-001", a["issue_id"]).confirmation is None


def test_handoff_is_not_a_solution_and_is_required_before_escalation(setup):
    store, service, sid, a = setup
    with pytest.raises(AccessDenied):
        propose(store, service, a, status="escalated")
    store.save_handoff("EMP-001", a["issue_id"], {"summary": "Investigate VPN"})
    assert propose(store, service, a, status="escalated")["status"] == "escalated"
    assert service.memory("EMP-001", a["issue_id"]).confirmation is None
    assert store.list_resolutions("EMP-001") == []
    confirmed = service.confirm_resolved(
        "EMP-001",
        sid,
        user_message(store, sid),
        explicit_confirmation=True,
        issue_id=a["issue_id"],
    )
    assert confirmed["status"] == "resolved"
    assert store.list_resolutions("EMP-001") == []  # Archiving is a separate application action.
    assert service.next_issue("EMP-001", sid) is None


def test_failed_step_reopens_and_invalidates_prior_success_atomically(setup):
    store, service, sid, a = setup
    with_step = service.add_step("EMP-001", a["issue_id"], "Update VPN client")
    step_id = with_step["state"]["attempted_steps"][0]["id"]
    old_confirmation = user_message(store, sid)
    service.confirm_resolved("EMP-001", sid, old_confirmation, explicit_confirmation=True)
    saved = store.save_resolution(
        "EMP-001",
        a["issue_id"],
        symptoms="Disconnects",
        body="Employee reported updating the client",
        result="Employee reports restored",
        evidence_refs=[],
    )
    failed_msg = user_message(store, sid, "Actually, the update did not fix it")
    current = service.report_step_result("EMP-001", a["issue_id"], step_id, failed_msg, "failed")
    assert current["status"] == "investigating"
    assert current["state"]["confirmation"] is None
    assert current["state"]["attempted_steps"][0]["result"] == "failed"
    with pytest.raises(AccessDenied):
        store.get_resolution("EMP-001", saved["resolution_id"])
    replay = service.confirm_resolved("EMP-001", sid, old_confirmation, explicit_confirmation=True)
    assert replay["status"] == "investigating"  # A replay is a no-op, never a new success.
    new_msg = user_message(store, sid, "After the next step it works")
    service.confirm_resolved("EMP-001", sid, new_msg, explicit_confirmation=True)
    assert (
        store.save_resolution(
            "EMP-001",
            a["issue_id"],
            symptoms="Disconnects",
            body="Employee reported a new step",
            result="Employee reports restored",
            evidence_refs=[],
        )["resolution_id"]
        == saved["resolution_id"]
    )


def test_step_success_alone_is_not_problem_resolution(setup):
    store, service, sid, a = setup
    issue = service.add_step("EMP-001", a["issue_id"], "Check version")
    step = issue["state"]["attempted_steps"][0]["id"]
    service.report_step_result(
        "EMP-001",
        a["issue_id"],
        step,
        user_message(store, sid, "Version check completed"),
        "succeeded",
    )
    assert store.get_issue("EMP-001", a["issue_id"])["status"] == "investigating"


def test_personal_history_in_new_session_does_not_initialize_old_state(setup):
    store, service, sid, a = setup
    observe(service, a, value="outage")
    new_session = store.create_session("EMP-001")["session_id"]
    fresh = service.create_issue("EMP-001", new_session, "VPN again", "connectivity")
    assert len(store.personal_history("EMP-001")) == 2
    memory = service.memory("EMP-001", fresh["issue_id"])
    assert memory.current_facts == {} and memory.historical_clues == []
    assert memory.confirmation is None and memory.attempted_steps == []
    assert len(store.personal_history("EMP-001", session_id=sid)[0]["issues"]) == 1


def test_proposals_cannot_forge_facts_confirmations_or_unknown_evidence(setup):
    store, service, _, a = setup
    for changes in ({"current_facts": {}}, {"confirmation": {}}, {"evidence": {}}):
        with pytest.raises(ValidationError):
            propose(store, service, a, **changes)
    with pytest.raises(ValidationError, match="unknown evidence"):
        propose(
            store,
            service,
            a,
            hypotheses=[{"statement": "VPN issue", "evidence_refs": ["invented"]}],
        )
    assert service.memory("EMP-001", a["issue_id"]).hypotheses == []


def test_cross_user_state_access_and_reopen_are_rejected(setup):
    store, service, sid, a = setup
    msg = user_message(store, sid)
    for action in (
        lambda: service.memory("EMP-002", a["issue_id"]),
        lambda: service.next_issue("EMP-002", sid),
        lambda: service.confirm_resolved("EMP-002", sid, msg, explicit_confirmation=True),
        lambda: service.reopen("EMP-002", a["issue_id"], msg),
        lambda: service.record_user_report("EMP-002", a["issue_id"], msg),
    ):
        with pytest.raises(AccessDenied):
            action()


def test_stale_proposal_and_future_receipt_rejected(setup):
    store, service, _, a = setup
    observe(service, a)
    with pytest.raises(ConflictError):
        service.propose("EMP-001", a["issue_id"], {}, expected_revision=a["revision"])
    with pytest.raises(ValueError, match="timestamp"):
        observe(service, a, source_at=NOW + timedelta(seconds=1))
    with pytest.raises(ValueError, match="timestamp"):
        observe(service, a, source_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="unregistered"):
        observe(service, a, tool="grant_admin")


def test_reopen_requires_new_confirmation_and_preserves_other_issue(setup):
    store, service, sid, a = setup
    b = service.create_issue("EMP-001", sid, "Access", "access")
    done = user_message(store, sid)
    service.confirm_resolved(
        "EMP-001", sid, done, issue_id=a["issue_id"], explicit_confirmation=True
    )
    failed = user_message(store, sid, "VPN is broken again")
    service.reopen("EMP-001", a["issue_id"], failed)
    replay = service.confirm_resolved(
        "EMP-001", sid, done, issue_id=a["issue_id"], explicit_confirmation=True
    )
    assert replay["status"] == "investigating"
    assert store.get_issue("EMP-001", b["issue_id"])["status"] == "investigating"


@pytest.mark.parametrize("explicit_first", [False, True])
def test_confirmation_replay_cannot_advance_to_new_issue(setup, explicit_first):
    store, service, sid, a = setup
    done = user_message(store, sid)
    service.confirm_resolved(
        "EMP-001",
        sid,
        done,
        explicit_confirmation=True,
        issue_id=a["issue_id"] if explicit_first else None,
    )
    b = service.create_issue("EMP-001", sid, "A separate new issue", "account")
    replay = service.confirm_resolved("EMP-001", sid, done, explicit_confirmation=True)
    assert replay["issue_id"] == a["issue_id"]
    assert store.get_issue("EMP-001", b["issue_id"])["status"] == "investigating"


def test_intentional_multi_issue_confirmation_requires_explicit_targets(setup):
    store, service, sid, a = setup
    b = service.create_issue("EMP-001", sid, "App", "application")
    done = user_message(store, sid, "VPN and the app both work now")
    for issue in (a, b):
        service.confirm_resolved(
            "EMP-001", sid, done, issue_id=issue["issue_id"], explicit_confirmation=True
        )
    with pytest.raises(ClarificationRequired, match="multiple explicit targets"):
        service.confirm_resolved("EMP-001", sid, done, explicit_confirmation=True)
    assert all(i["status"] == "resolved" for i in store.list_issues("EMP-001", sid))


def test_late_observation_cannot_overwrite_newer_unknown(setup):
    _, service, _, a = setup
    observe(service, a, success=False, summary="Current timeout", invocation_id="new-query")
    observe(
        service,
        a,
        source_at=NOW - timedelta(days=1),
        queried_at=NOW - timedelta(minutes=5),
        invocation_id="old-query",
    )
    memory = service.memory("EMP-001", a["issue_id"])
    fact = memory.current_facts["service_status:vpn:London"]
    assert fact.value is None and fact.queried_at == NOW
    assert memory.evidence[fact.evidence_ref].invocation_id == "new-query"
    assert len(memory.evidence) == 2  # Late data remains auditable, not authoritative.


def test_receipt_replays_are_idempotent_and_cannot_change_content(setup):
    store, service, _, a = setup
    first = observe(service, a, invocation_id="stable-invocation")
    replay = observe(service, a, invocation_id="stable-invocation")
    assert first["revision"] == replay["revision"]
    with pytest.raises(ConflictError, match="different content"):
        observe(service, a, invocation_id="stable-invocation", value="outage")
    assert len(service.memory("EMP-001", a["issue_id"]).evidence) == 1
    assert store.get_issue("EMP-001", a["issue_id"])["revision"] == first["revision"]


def test_replayed_failure_or_reopening_cannot_undo_new_success(setup):
    store, service, sid, a = setup
    first = service.add_step("EMP-001", a["issue_id"], "Update client")
    step = first["state"]["attempted_steps"][0]["id"]
    stale = user_message(store, sid, "An older failure report")
    failure = user_message(store, sid, "Update failed")
    service.report_step_result("EMP-001", a["issue_id"], step, failure, "failed")
    done = user_message(store, sid)
    resolved = service.confirm_resolved("EMP-001", sid, done, explicit_confirmation=True)
    entry = store.save_resolution(
        "EMP-001",
        a["issue_id"],
        symptoms="VPN",
        body="Employee reported a new approach",
        result="Employee reports restored",
        evidence_refs=[],
    )
    replay = service.report_step_result("EMP-001", a["issue_id"], step, failure, "failed")
    assert replay["revision"] == resolved["revision"] and replay["status"] == "resolved"
    assert store.get_resolution("EMP-001", entry["resolution_id"])["valid"]
    with pytest.raises(ValueError, match="stale outcome"):
        service.report_step_result("EMP-001", a["issue_id"], step, stale, "failed")
    reopen_msg = user_message(store, sid, "Broken again")
    service.reopen("EMP-001", a["issue_id"], reopen_msg)
    resolved = service.confirm_resolved(
        "EMP-001", sid, user_message(store, sid), explicit_confirmation=True
    )
    assert service.reopen("EMP-001", a["issue_id"], reopen_msg)["revision"] == resolved["revision"]


@pytest.mark.parametrize("healthy", [True, False])
def test_waiting_for_recovery_reconciles_when_current_incident_is_no_longer_known(setup, healthy):
    store, service, _, a = setup
    observe(service, a, value="outage", queried_at=NOW - timedelta(seconds=1))
    propose(store, service, a, status="awaiting_service_recovery")
    updated = observe(service, a, value="healthy", success=healthy)
    assert updated["status"] == "investigating"
    assert updated["state"]["confirmation"] is None


def test_failed_steps_are_not_pending_verification_and_retry_is_explicit(setup):
    store, service, sid, a = setup
    first = service.add_step("EMP-001", a["issue_id"], "Try reconnecting")
    step = first["state"]["attempted_steps"][0]["id"]
    service.report_step_result(
        "EMP-001", a["issue_id"], step, user_message(store, sid, "Still broken"), "failed"
    )
    with pytest.raises(ValueError, match="pending verification"):
        propose(store, service, a, status="awaiting_verification")
    second = service.add_step("EMP-001", a["issue_id"], "Retry after checking network")
    assert len(second["state"]["attempted_steps"]) == 2
    assert second["state"]["attempted_steps"][0]["result"] == "failed"
    assert (
        propose(store, service, a, status="awaiting_verification")["status"]
        == "awaiting_verification"
    )


def test_state_creation_rolls_back_if_memory_contract_is_invalid(setup):
    store, service, sid, _ = setup
    with pytest.raises(ValidationError):
        service.create_issue("EMP-001", sid, "Invalid input", "account", symptoms=[123])
    assert len(store.list_issues("EMP-001", sid)) == 1


def test_confirmation_binding_survives_restart(tmp_path):
    path = tmp_path / "confirmation-events.sqlite"
    with Store(path, clock=lambda: NOW) as store:
        service = StateService(store)
        sid = store.create_session("EMP-001")["session_id"]
        a = service.create_issue("EMP-001", sid, "VPN", "connectivity")
        done = user_message(store, sid)
        service.confirm_resolved("EMP-001", sid, done, explicit_confirmation=True)
        b = service.create_issue("EMP-001", sid, "New login issue", "account")
    with Store(path, clock=lambda: NOW) as store:
        result = StateService(store).confirm_resolved(
            "EMP-001", sid, done, explicit_confirmation=True
        )
        assert result["issue_id"] == a["issue_id"]
        assert store.get_issue("EMP-001", b["issue_id"])["status"] == "investigating"


def test_memory_provenance_and_pending_work_survive_restart(tmp_path):
    path = tmp_path / "full-memory.sqlite"
    with Store(path, clock=lambda: NOW) as store:
        service = StateService(store)
        sid = store.create_session("EMP-001")["session_id"]
        issue = service.create_issue("EMP-001", sid, "VPN", "connectivity")
        report = user_message(store, sid, "VPN fails on my laptop")
        service.record_user_report("EMP-001", issue["issue_id"], report)
        service.record_tool_result(
            "EMP-001",
            issue["issue_id"],
            tool="search_resolution_history",
            source_ref="HIST-003",
            source_at=NOW - timedelta(days=30),
            summary="A previous case used a different VPN client",
            success=True,
        )
        observe(service, issue, invocation_id="health-before-restart")
        observe(service, issue, success=False, invocation_id="timeout-before-restart")
        service.add_step("EMP-001", issue["issue_id"], "Check the VPN client version")
        pending = propose(store, service, issue, status="awaiting_verification")
        snapshot = service.memory("EMP-001", issue["issue_id"])

    with Store(path, clock=lambda: NOW) as store:
        service = StateService(store)
        restored = service.memory("EMP-001", issue["issue_id"])
        assert restored == snapshot
        assert store.get_issue("EMP-001", issue["issue_id"])["revision"] == pending["revision"]
        assert service.next_issue("EMP-001", sid)["status"] == "awaiting_verification"
        assert restored.current_facts["service_status:vpn:London"].value is None
        assert len(restored.historical_clues) == 1
        assert len(restored.user_reports) == 1
        assert restored.attempted_steps[0].result == "suggested"
        assert restored.confirmation is None
        fresh_sid = store.create_session("EMP-001")["session_id"]
        fresh = service.create_issue("EMP-001", fresh_sid, "VPN again", "connectivity")
        assert service.memory("EMP-001", fresh["issue_id"]).current_facts == {}
        assert len(store.personal_history("EMP-001")) == 2


pytestmark = pytest.mark.legacy_ledger
