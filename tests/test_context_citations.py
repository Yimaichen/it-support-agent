from src.state import StateService
from src.tools import ToolRunner
from tests.test_pi_agent import ScriptRuntime, make


def test_each_search_and_policy_citation_has_its_own_summary(support_env):
    corpus, store, ctx = support_env
    runner = ToolRunner(corpus, store)
    for name, args in [
        ("search_knowledge_base", {"query": "login VPN", "top_k": 5}),
        ("lookup_policy", {"service_id": "sso"}),
    ]:
        result = runner.execute(ctx, name, args)
        assert result["ok"] and len(result["sources"]) > 1
        evidence = StateService(store).memory(ctx.user_id, ctx.issue_id).evidence
        for ref in result["evidence_refs"]:
            receipt = evidence[ref]
            assert f'"{receipt.source_ref}"' in receipt.summary[:90]


def test_case_context_restores_structured_current_facts(support_env, tmp_path):
    corpus, store, ctx = support_env
    ToolRunner(corpus, store).execute(
        ctx, "check_service_status", {"service_id": "vpn", "region": "London"}
    )
    agent = make(
        support_env, tmp_path, [("choose", "manage_issue", {"mode": "select", "case": "case_1"})]
    )
    assert (
        agent.run(ctx.user_id, ctx.session_id, "继续原问题", submission_id="restore-facts")[
            "status"
        ]
        == "completed"
    )
    # The next prompt restores SQLite facts independently of Pi's conversational memory.
    agent = make(support_env, tmp_path)
    assert (
        agent.run(ctx.user_id, ctx.session_id, "继续", submission_id="restore-facts-2")["status"]
        == "completed"
    )
    facts = ScriptRuntime.prompts[-1][1]["current"]["facts"]
    vpn = next(f for f in facts if f["topic"] == "service_status")
    assert vpn["subject"] == "vpn:global" and vpn["value"] == "healthy"
    assert vpn["source_at"] and vpn["queried_at"] and vpn["evidence_ref"]
