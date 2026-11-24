"""Real boundary regressions for employee procedures and multi-resource handoffs."""

from src.records import Records
from src.tools import ToolRunner
from tests.test_pi_agent import make


def test_investigation_sections_are_readable_but_not_employee_procedures(support_env):
    corpus, store, ctx = support_env
    runner = ToolRunner(corpus, store)
    for doc in corpus.knowledge:
        result = runner.execute(ctx, "read_knowledge_document", {"source_id": doc.id})
        assert result["ok"]
        assert "Clarify" in result["data"]["body"]
        assert "Clarify / 澄清" not in result["data"]["employee_sections"]
        section = runner.execute(
            ctx,
            "read_knowledge_document",
            {"source_id": doc.id, "section": "Clarify"},
        )
        assert section["ok"]
        assert section["data"]["section_content"].startswith("## Clarify")
        assert "Clarify / 澄清" not in section["data"]["employee_sections"]


def test_grafana_has_distinct_policy_resource(support_env):
    corpus, store, ctx = support_env
    runner = ToolRunner(corpus, store)
    grafana = runner.execute(ctx, "lookup_policy", {"service_id": "grafana"})["data"]
    tableau = runner.execute(ctx, "lookup_policy", {"service_id": "tableau"})["data"]
    assert any(p["id"] == "POL-011" and p["resource"] == "grafana_dashboard" for p in grafana)
    assert not any(p["resource"] == "tableau_project" for p in grafana)
    assert any(p["id"] == "POL-010" and p["resource"] == "tableau_project" for p in tableau)


def test_handoff_derives_primary_and_preserves_distinct_approval_routes(support_env, tmp_path):
    _, store, ctx = support_env
    draft = {
        "affected_services": ["snowflake", "grafana"],
        "summary": "Read access to production data and dashboards",
        "applicable_policy_ids": ["POL-005", "POL-011"],
        "impact": "New employee cannot perform assigned reporting",
        "urgency_reason": "Normal onboarding; approvals still apply",
        "escalation_reason": "Both resource owners must approve",
    }
    agent = make(
        support_env,
        tmp_path,
        [
            ("case", "manage_issue", {"mode": "select", "case": "case_1"}),
            (
                "db",
                "lookup_policy",
                {"service_id": "snowflake", "resource": "production_database", "action": "grant"},
            ),
            (
                "dashboard",
                "lookup_policy",
                {"service_id": "grafana", "resource": "grafana_dashboard", "action": "grant"},
            ),
            ("save", "save_handoff", draft),
        ],
    )
    result = agent.run(
        ctx.user_id, ctx.session_id, "Please prepare my access request", submission_id="multi"
    )
    assert result["status"] == "completed"
    handoff = store.get_handoff(ctx.user_id, ctx.issue_id)
    assert handoff["suggested_team"] == "Data Owner and Security"
    assert {r["policy_id"] for r in handoff["approval_routes"]} == {"POL-005", "POL-011"}
    assert [r["services"] for r in handoff["approval_routes"]] == [["snowflake"], ["grafana"]]
    assert "data_pipeline" not in handoff["affected_services"]
    exported = Records(store, support_env[0]).export_handoff(ctx.user_id, ctx.issue_id)
    assert "Analytics Owner" in exported and "grafana_dashboard" in exported
