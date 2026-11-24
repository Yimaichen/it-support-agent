"""Regression coverage for one case format and read-only source evidence."""

import json

import pytest

from src import pi_storage as journal
from src.retrieval import Retrieval
from src.source_content import employee_procedure
from src.storage import AccessDenied, Store
from src.tools import ToolRunner
from tests.test_pi_agent import ScriptRuntime, make


def test_database_migrates_once_without_changing_access_or_validity(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    cases = {
        "chat": {
            "conversation": [
                {"role": "assistant", "content": "Upgrade, restart, then reconnect."},
                {"role": "user", "content": "Upgraded and reconnected; stable for 30 minutes."},
            ],
            "steps": ["assistant: Upgrade, restart, then reconnect."],
        },
        "old": {"steps": ["Recorded update"], "result": "Employee reported recovery"},
        "seed": {"body": "# Existing synthetic narrative", "steps": ["duplicate"]},
    }
    with Store(path) as db:
        sid = db.create_session("EMP-001")["session_id"]
        for name, doc in cases.items():
            issue = (
                db.create_issue("EMP-001", sid, name, "connectivity") if name != "seed" else None
            )
            db._conn.execute(
                "INSERT INTO resolution_history VALUES (?,?,?,?,?,?,?,?)",
                (
                    name,
                    "synthetic_seed" if name == "seed" else "live_session",
                    issue["issue_id"] if issue else None,
                    None if name == "seed" else "EMP-001",
                    "shared" if name == "seed" else "private",
                    int(name != "old"),
                    json.dumps(doc),
                    db._now(),
                ),
            )
        db._conn.execute("PRAGMA user_version=1")
        db._conn.commit()
    with Store(path) as db:
        rows = [
            dict(r)
            for r in db._conn.execute("SELECT * FROM resolution_history ORDER BY resolution_id")
        ]
        for row in rows:
            doc = json.loads(row["document_json"])
            assert doc["body"] and not {"steps", "conversation"} & doc.keys()
        chat = db.get_resolution("EMP-001", "chat")["document"]
        assert "## assistant\nUpgrade, restart" in chat["body"]
        assert "## user\nUpgraded and reconnected" in chat["body"]
        assert "not evidence of execution" in chat["body"]
        assert [r["resolution_id"] for r in db.list_resolutions("EMP-001")] == ["chat", "seed"]
        with pytest.raises(AccessDenied):
            db.get_resolution("EMP-002", "chat")
        assert db.get_resolution("EMP-002", "seed")["document"]["body"] == cases["seed"]["body"]
        assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 2
    with Store(path) as db:
        assert db._conn.total_changes == 0
        assert [
            dict(r)
            for r in db._conn.execute("SELECT * FROM resolution_history ORDER BY resolution_id")
        ] == rows


def test_source_text_is_literal_and_fabricated_sections_are_rejected(support_env):
    corpus, store, ctx = support_env
    runner = ToolRunner(corpus, store)
    read = runner.execute(ctx, "read_knowledge_document", {"source_id": "KB-APP-002"})
    assert read["ok"] and not read["data"]["section_content"]
    assert "Safe investigation / 安全排查" in read["data"]["sections"]
    assert read["data"]["employee_sections"] == ["Employee steps / 员工操作"]
    shown = runner.execute(
        ctx,
        "read_knowledge_document",
        {
            "source_id": "KB-APP-002",
            "section": "Employee steps",
        },
    )
    assert shown["ok"]
    text = shown["data"]["section_content"]
    assert text == employee_procedure(read["data"]["body"], "Employee steps")
    assert "hotspot" not in text and "Do not clear all browser data" in text
    for args in [
        {"source_id": "KB-APP-002", "section": "Switch to hotspot"},
    ]:
        assert not runner.execute(ctx, "read_knowledge_document", args)["ok"]
    for section in ("Clarify", "Safe investigation", "Handoff"):
        result = runner.execute(
            ctx, "read_knowledge_document", {"source_id": "KB-APP-002", "section": section}
        )
        assert result["ok"] and result["data"]["section_content"].startswith("## " + section)
    expired = runner.execute(
        ctx, "read_knowledge_document", {"source_id": "KB-APP-004", "section": "Safe investigation"}
    )
    assert expired["ok"] and expired["data"]["expired"]
    assert "not current operational guidance" in expired["data"]["usage_note"]


def test_source_read_never_publishes_and_evidence_survives_reload(support_env, tmp_path):
    _, store, ctx = support_env
    args = {"source_id": "KB-CON-001", "section": "Employee steps"}
    agent = make(
        support_env,
        tmp_path,
        [
            ("case", "manage_issue", {"mode": "select", "case": "case_1"}),
            ("source", "read_knowledge_document", args),
            ("source", "read_knowledge_document", args),
        ],
    )
    events = []
    agent.notify = lambda event, detail: events.append((event, detail))
    result = agent.run(
        ctx.user_id, ctx.session_id, "请用中文解释我应该怎么做。", submission_id="source"
    )
    assert result["status"] == "completed"
    messages = store.list_messages(ctx.user_id, ctx.session_id)
    assert len(messages) == 2
    assert [m["message"]["role"] for m in messages] == ["user", "assistant"]
    assert not any(e[0] == "source_document" for e in events)
    from src.web_view import session_view

    visible = session_view(store, ctx.user_id, ctx.session_id)["messages"]
    assert not any("Employee steps" in m["content"] for m in visible)
    assert not any("Source excerpt" in m["content"] for m in visible)
    row = store._conn.execute(
        "SELECT result_json FROM pi_tool_calls WHERE call_id=?", ("source",)
    ).fetchone()
    evidence = json.loads(row[0])["data"]["section_content"]
    assert "6.2" in evidence and "Employee steps" in evidence
    assert not agent.state.memory(ctx.user_id, ctx.issue_id).attempted_steps


def test_confirmed_archive_keeps_reports_separate_from_suggestions(support_env, tmp_path):
    corpus, store, ctx = support_env

    class NarrativeRuntime(ScriptRuntime):
        def prompt(self, text, context, submission_id, dispatch, notify):
            if submission_id == "suggestion":
                dispatch("manage_issue", {"mode": "select", "case": "case_1"}, "case")
                return {"reply": "Upgrade, restart the Mac, then reconnect."}
            return {"reply": "Thanks for reporting the recovery; please confirm when ready."}

    agent = make(support_env, tmp_path)
    agent.runtime_factory = NarrativeRuntime
    agent.run(ctx.user_id, ctx.session_id, "VPN fails", submission_id="suggestion")
    agent.run(
        ctx.user_id,
        ctx.session_id,
        "Upgraded and reconnected, stable for 30 minutes.",
        submission_id="report",
    )
    result = agent.run(
        ctx.user_id,
        ctx.session_id,
        "I confirm the whole problem is resolved.",
        submission_id="confirm",
        action={"kind": "confirm", "issue_id": ctx.issue_id},
    )
    document = store.get_resolution(ctx.user_id, result["resolution_id"])["document"]
    assert not {"steps", "conversation"} & document.keys()
    assert "## assistant\nUpgrade, restart" in document["body"]
    assert "## user\nUpgraded and reconnected" in document["body"]
    assert "not confirmed actions" in document["body"]
    headers = Retrieval(corpus, store).resolutions(ctx.user_id, "stable 30 minutes")
    assert any(h["source_id"] == result["resolution_id"] for h in headers)
    assert all("body" not in h for h in headers)
    assert journal.binding(store, ctx.user_id, ctx.session_id)
