"""State/record boundaries and exact policy reads, independent of model wording."""

import json
import re

import pytest
from pydantic import ValidationError

from src.pi_agent import SYSTEM
from src.records import Handoff, Records
from src.state import StateService
from src.tools import PolicyArgs, ToolRunner
from tests.test_pi_agent import make


def saved(env):
    corpus, store, ctx = env
    row = store.append_message(
        ctx.user_id,
        ctx.session_id,
        {"role": "user", "content": "VPN still fails; portal is unavailable."},
    )
    StateService(store).record_user_report(ctx.user_id, ctx.issue_id, row["message_id"])
    record = Records(store, corpus).handoff(
        ctx.user_id,
        ctx.issue_id,
        {
            "summary": "VPN still fails. Employee reports portal unavailable, so upgrade not performed. Cause unverified. Network Support should investigate the portal and connection.",
            "impact": "Remote work blocked",
            "urgency_reason": "Employee affected",
            "escalation_reason": "Employee requests human help",
            "suggested_team": "Network Support",
        },
    )
    return record


def test_handoff_has_one_narrative_and_keeps_audit_after_update(support_env):
    corpus, store, ctx = support_env
    before = saved(support_env)
    assert before["conversation"]
    assert not {"hypotheses", "attempted_steps", "open_questions"} & before.keys()
    msg = store.append_message(
        ctx.user_id,
        ctx.session_id,
        {"role": "user", "content": "Please explain the handoff status."},
    )
    StateService(store).record_user_report(ctx.user_id, ctx.issue_id, msg["message_id"])
    after = Records(store, corpus).sync_handoff(ctx.user_id, ctx.issue_id)
    assert after["summary"] == before["summary"]
    assert after["summary_stale"] is True
    assert after["record_through_sequence"] > after["summary_through_sequence"]
    assert after["status"] == "saved_locally"
    exported = json.loads(Records(store, corpus).export_handoff(ctx.user_id, ctx.issue_id))
    assert exported["summary"] == before["summary"]
    assert "query_audit" in exported and exported["conversation"]
    assert not {"hypotheses", "attempted_steps", "open_questions"} & exported.keys()


def test_old_handoff_export_does_not_rewrite_original(support_env):
    corpus, store, ctx = support_env
    record = saved(support_env)
    old = {
        **record,
        "hypotheses": [{"text": "legacy"}],
        "attempted_steps": [],
        "open_questions": ["legacy"],
    }
    store._conn.execute(
        "UPDATE handoffs SET payload_json=? WHERE issue_id=?", (json.dumps(old), ctx.issue_id)
    )
    normalized = Handoff.model_validate(old).model_dump(mode="json")
    assert "hypotheses" not in normalized
    output = json.loads(Records(store, corpus).export_handoff(ctx.user_id, ctx.issue_id))
    assert "open_questions" not in output
    assert store.get_handoff(ctx.user_id, ctx.issue_id)["open_questions"] == ["legacy"]
    refreshed = Records(store, corpus).sync_handoff(ctx.user_id, ctx.issue_id)
    assert refreshed["summary"] == record["summary"]
    assert "open_questions" not in refreshed


def test_model_receives_content_not_internal_revision_contract(support_env, tmp_path):
    saved(support_env)
    _, store, ctx = support_env
    agent = make(support_env, tmp_path)
    agent.user_id, agent.session_id, agent.current = ctx.user_id, ctx.session_id, ctx.issue_id
    snapshot = agent._snapshot()
    handoff = snapshot["current"]["handoff"]
    assert handoff["summary"]
    assert (
        not {
            "revision",
            "summary_through_sequence",
            "record_through_sequence",
            "summary_updated_at",
        }
        & handoff.keys()
    )
    assert not re.search(r"(?:POL|KB)-[A-Z0-9-]+|ACCOUNT_LOCKED", SYSTEM)


def test_exact_policy_read_has_full_source_and_current_audit(support_env):
    corpus, store, ctx = support_env
    result = ToolRunner(corpus, store).execute(ctx, "lookup_policy", {"policy_id": "POL-008"})
    assert result["ok"]
    assert [p["id"] for p in result["data"]] == ["POL-008"]
    assert result["data"][0]["applicability"]
    assert result["data"][0]["content_complete"]
    mem = StateService(store).memory(ctx.user_id, ctx.issue_id)
    assert "POL-008" in {p.source_ref for p in mem.evidence.values() if p.kind == "policy"}
    assert mem.tool_attempts[-1].source_refs == ["POL-008"]
    assert result["sources"][0]["source_id"] == "POL-008"
    assert corpus.policies


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"policy_id": "POL-008", "service_id": "sso"},
        {"policy_id": "POL-008", "action": "reset"},
        {"policy_id": ""},
    ],
)
def test_policy_selector_conflicts_are_explicit(args):
    with pytest.raises(ValidationError):
        PolicyArgs.model_validate(args)


def test_unknown_policy_never_falls_back_to_broad_search(support_env):
    corpus, store, ctx = support_env
    result = ToolRunner(corpus, store).execute(ctx, "lookup_policy", {"policy_id": "POL-NOT-FOUND"})
    assert not result["ok"] and result["error"]["type"] == "unknown_policy"
    assert not result["sources"]
