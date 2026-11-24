import json

import pytest
from pydantic import ValidationError

from src.privacy import redact_text, sanitize
from src.records import Handoff, Records
from src.state import StateService
from src.storage import AccessDenied
from src.tools import ToolRunner


def resolve(env):
    corpus, store, ctx = env
    state = StateService(store)
    ToolRunner(corpus, store).execute(ctx, "search_knowledge_base", {"query": "VPN"})
    state.add_step(ctx.user_id, ctx.issue_id, "Use the managed VPN client.")
    msg = store.append_message(
        ctx.user_id, ctx.session_id, {"role": "user", "content": "It works now"}
    )
    state.report_step_result(
        ctx.user_id,
        ctx.issue_id,
        state.memory(ctx.user_id, ctx.issue_id).attempted_steps[0].id,
        msg["message_id"],
        "succeeded",
    )
    state.confirm_resolved(
        ctx.user_id, ctx.session_id, msg["message_id"], explicit_confirmation=True
    )
    mem = state.memory(ctx.user_id, ctx.issue_id)
    return {
        "symptoms": "VPN fails; email user@example.com host host.internal error VPN_DNS_FAIL version 6.2",
        "step_ids": [mem.attempted_steps[0].id],
        "evidence_refs": list(mem.evidence),
    }


def test_archive_only_success_and_private_idempotent(support_env):
    corpus, store, ctx = support_env
    service = Records(store, corpus)
    with pytest.raises(ValueError):
        service.archive(
            ctx.user_id, ctx.issue_id, {"symptoms": "VPN", "evidence_refs": ["invented"]}
        )
    draft = resolve(support_env)
    first = service.archive(ctx.user_id, ctx.issue_id, draft)
    assert (
        service.archive(ctx.user_id, ctx.issue_id, draft)["resolution_id"] == first["resolution_id"]
    )
    assert first["visibility"] == "private"
    assert first["document"]["result_basis"] == "employee_reported"
    text = json.dumps(first["document"])
    assert "user@example.com" not in text and "host.internal" not in text
    assert "VPN_DNS_FAIL" in text and "6.2" in text
    with pytest.raises(AccessDenied):
        store.get_resolution(corpus.employees[1].id, first["resolution_id"])
    msg = store.append_message(
        ctx.user_id, ctx.session_id, {"role": "user", "content": "still broken"}
    )
    StateService(store).reopen(ctx.user_id, ctx.issue_id, msg["message_id"])
    assert first["resolution_id"] not in {
        r["resolution_id"] for r in store.list_resolutions(ctx.user_id)
    }


@pytest.mark.parametrize(
    "mutation",
    [{"step_ids": ["invented"]}, {"evidence_refs": ["invented"]}, {"result": "machine verified"}],
)
def test_archive_rejects_fabricated_fields(support_env, mutation):
    corpus, store, ctx = support_env
    draft = {**resolve(support_env), **mutation}
    with pytest.raises(ValueError):
        Records(store, corpus).archive(ctx.user_id, ctx.issue_id, draft)


def test_structured_local_handoff_and_export(support_env):
    corpus, store, ctx = support_env
    result = ToolRunner(corpus, store).execute(
        ctx,
        "lookup_policy",
        {"service_id": "data_pipeline", "resource": "production_database", "action": "grant"},
    )
    records = Records(store, corpus)
    draft = {
        "affected_services": ["data_pipeline"],
        "summary": "Production access request",
        "impact": "Employee blocked",
        "urgency_reason": "Needs access; urgency does not bypass approval",
        "escalation_reason": "approval required",
        "policy_refs": ["POL-005"],
        "suggested_team": "Data Owner and Security",
        "evidence_refs": result["evidence_refs"],
    }
    handoff = records.handoff(ctx.user_id, ctx.issue_id, draft)
    assert (
        Handoff.model_validate_json(records.export_handoff(ctx.user_id, ctx.issue_id)).handoff_id
        == handoff["handoff_id"]
    )
    assert handoff["status"] == "saved_locally"
    assert "no IT team" in handoff["delivery_note"]
    assert records.handoff(ctx.user_id, ctx.issue_id, draft)["handoff_id"] == handoff["handoff_id"]
    with pytest.raises(AccessDenied):
        records.export_handoff(corpus.employees[1].id, ctx.issue_id)
    derived = records.handoff(ctx.user_id, ctx.issue_id, {**draft, "suggested_team": "Wrong Team"})
    assert derived["suggested_team"] == "Data Owner and Security"
    with pytest.raises(ValueError):
        records.handoff(ctx.user_id, ctx.issue_id, {**draft, "evidence_refs": ["fake"]})
    assert not [
        r for r in store.list_resolutions(ctx.user_id) if r["source_type"] == "live_session"
    ]


def test_secret_redaction_at_persistence_boundary(support_env):
    corpus, store, ctx = support_env
    raw = "password=hunter2 Bearer abcdefghijklmnop sk-123456789abcdefghijkl 验证码：123456"
    msg = store.append_message(ctx.user_id, ctx.session_id, {"role": "user", "content": raw})
    content = msg["message"]["content"]
    assert all(s not in content for s in ["hunter2", "abcdefghijklmnop", "sk-123", "123456"])
    assert "password reset guidance" == redact_text("password reset guidance")
    assert "reasoning_content" not in sanitize({"reasoning_content": "private", "ok": True})
    assert sanitize({"api_key": "arbitrary"})["api_key"] == "[REDACTED_SECRET]"
    assert "vpn_DNS_FAIL 6.2" == redact_text("vpn_DNS_FAIL 6.2", personal=True)
    with pytest.raises(ValidationError):
        Handoff.model_validate({"status": "sent_to_it"})


def test_string_json_redaction_preserves_valid_tool_json():
    raw = '{"password": "synthetic-secret-123", "otp": "123456", "code": "VPN_DNS_FAIL"}'
    clean = json.loads(redact_text(raw))
    assert clean["password"] == "[REDACTED_SECRET]" and clean["otp"] == "[REDACTED_SECRET]"
    assert clean["code"] == "VPN_DNS_FAIL"


def test_unattempted_or_pre_reopen_step_cannot_be_effective_remediation(support_env):
    corpus, store, ctx = support_env
    draft = resolve(support_env)
    state = StateService(store)
    later = store.append_message(
        ctx.user_id, ctx.session_id, {"role": "user", "content": "The fix did not last"}
    )
    state.reopen(ctx.user_id, ctx.issue_id, later["message_id"])
    confirmation = store.append_message(
        ctx.user_id,
        ctx.session_id,
        {"role": "user", "content": "Now recovered without taking that action again"},
    )
    state.confirm_resolved(
        ctx.user_id,
        ctx.session_id,
        confirmation["message_id"],
        issue_id=ctx.issue_id,
        explicit_confirmation=True,
    )
    with pytest.raises(ValueError, match="effective remediation"):
        Records(store, corpus).archive(ctx.user_id, ctx.issue_id, draft)
