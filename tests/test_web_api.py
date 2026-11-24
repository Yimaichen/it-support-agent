"""HTTP/SSE adapter contracts using the real PiAgent with an offline SDK transport."""

import json
from dataclasses import replace
from functools import partial

from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.pi_agent import PiAgent
from src.storage import Store


class OfflineRuntime:
    calls = 0

    def __init__(self, timeout):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def open(self, init, dispatch):
        return {"session_file": init["root"] + "/offline.jsonl"}

    def prompt(self, text, context, submission_id, dispatch, notify):
        type(self).calls += 1
        notify("reasoning_snapshot", {"text": "PRIVATE_THOUGHT_MARKER"})
        notify("model_started", {})
        for name, args, call in [
            (
                "manage_issue",
                {"mode": "create", "title": "VPN 连接问题", "category": "connectivity"},
                "new",
            ),
            ("search_knowledge_base", {"query": "VPN"}, "search"),
            (
                "save_handoff",
                {"summary": "VPN 连接问题，需要人工继续排查。", "affected_services": ["VPN"]},
                "handoff",
            ),
        ]:
            result = dispatch(name, args, call)
            assert result["ok"], result
        notify("reply_snapshot", {"text": "记录已保存。", "message_index": 1})
        notify("reply_finished", {"text": "记录已保存。", "message_index": 1})
        return {"reply": "记录已保存。"}


def setup(tmp_path):
    settings = Settings(
        api_key="offline-adapter-key",
        database_path=tmp_path / "db.sqlite",
        pi_runtime_dir=tmp_path / "pi",
    )
    app = create_app(settings, agent_factory=partial(PiAgent, runtime_factory=OfflineRuntime))
    return settings, app


def test_real_pi_sse_idempotency_and_trusted_confirmation(tmp_path):
    settings, app = setup(tmp_path)
    OfflineRuntime.calls = 0
    with TestClient(app) as client:
        session = client.post("/api/sessions").json()["session_id"]
        body = {"text": "VPN 连接不稳定", "submission_id": "same-submission"}
        first = client.post(f"/api/sessions/{session}/turns", json=body)
        assert first.status_code == 200
        packets = [
            json.loads(line[6:]) for line in first.text.splitlines() if line.startswith("data: ")
        ]
        assert packets[-1]["event"] == "done", packets
        assert "PRIVATE_THOUGHT_MARKER" not in first.text
        assert "offline-adapter-key" not in first.text
        assert any(p["event"] == "reply_snapshot" for p in packets)
        again = client.post(f"/api/sessions/{session}/turns", json=body)
        assert '"event": "done"' in again.text
        assert OfflineRuntime.calls == 1
        view = client.get(f"/api/sessions/{session}").json()
        assert len([m for m in view["messages"] if m["role"] == "user"]) == 1
        issue = view["current_issue_id"]
        assert view["handoffs"][0]["summary"].startswith("VPN")
        downloaded = client.get(f"/api/handoffs/{issue}")
        assert (
            downloaded.status_code == 200
            and "attachment" in downloaded.headers["content-disposition"]
        )
        confirm = client.post(
            f"/api/sessions/{session}/turns",
            json={
                "text": "确认已解决",
                "submission_id": "confirm",
                "action": {"kind": "confirm", "issue_id": issue},
            },
        )
        assert '"event": "done"' in confirm.text
        assert OfflineRuntime.calls == 1  # Employee confirmation never calls a model.
        assert client.get(f"/api/sessions/{session}").json()["issues"][0]["status"] == "resolved"
    with Store(settings.database_path) as store:
        assert store.list_issues("EMP-001", session)[0]["status"] == "resolved"


def test_api_enforces_employee_and_session_scope(tmp_path):
    settings, app = setup(tmp_path)
    with TestClient(app) as client:
        session = client.post("/api/sessions").json()["session_id"]
        client.post(
            f"/api/sessions/{session}/turns", json={"text": "VPN 故障", "submission_id": "scope"}
        )
        view = client.get(f"/api/sessions/{session}").json()
        issue = view["issues"][0]["issue_id"]
        other = {"X-Employee-ID": "EMP-002"}
        assert client.get(f"/api/sessions/{session}", headers=other).status_code == 404
        assert client.get(f"/api/handoffs/{issue}", headers=other).status_code == 404
        assert (
            client.post(
                f"/api/sessions/{session}/turns", json={"text": "x"}, headers=other
            ).status_code
            == 404
        )
        assert client.get("/api/sessions", headers=other).json() == []
        own_second = client.post("/api/sessions").json()["session_id"]
        assert (
            client.post(
                f"/api/sessions/{own_second}/selection", json={"issue_id": issue}
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/sessions/{own_second}/turns",
                json={"text": "确认", "action": {"kind": "confirm", "issue_id": issue}},
            ).status_code
            == 404
        )
        assert (
            client.get("/api/bootstrap", headers={"X-Employee-ID": "not-an-employee"}).status_code
            == 404
        )
        assert client.get("/api/not-an-endpoint").status_code == 404
        assert (
            client.post("/api/sessions", headers={"Origin": "https://external.invalid"}).status_code
            == 403
        )


def test_unconfigured_model_keeps_history_and_library_available(tmp_path):
    settings, _ = setup(tmp_path)
    with TestClient(create_app(replace(settings, api_key=""))) as client:
        assert not client.get("/api/bootstrap").json()["model_configured"]
        session = client.post("/api/sessions").json()["session_id"]
        assert (
            client.post(f"/api/sessions/{session}/turns", json={"text": "帮助"}).status_code == 503
        )
        assert client.get(f"/api/sessions/{session}").status_code == 200
        assert client.get("/api/library/KB-ACC-001").status_code == 200
        assert client.get("/api/library/not-an-id").status_code == 404
