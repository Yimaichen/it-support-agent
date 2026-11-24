"""Single Pi entrypoint, historical display and independent shared-budget regressions."""

import pytest
from fastapi.testclient import TestClient

from src import pi_storage as journal
from src.api import create_app
from src.budget import BudgetExceeded, TurnBudget
from src.config import Settings
from src.storage import Store
from src.web_view import session_view


def test_former_backend_environment_cannot_enable_another_controller():
    config = Settings.from_env({"AGENT_BACKEND": "legacy"})
    assert not hasattr(config, "agent_backend")


@pytest.mark.parametrize("limit", ["model", "tool", "tokens", "deadline"])
def test_shared_budget_enforces_each_execution_limit(limit):
    now = [10.0]
    budget = TurnBudget(
        Settings(max_model_attempts=1, max_tool_calls=1, turn_timeout_seconds=5),
        clock=lambda: now[0],
    )
    if limit in {"model", "tool"}:
        operation = getattr(budget, limit)
        operation()
    elif limit == "tokens":
        budget.reported_usage = {"total_tokens": budget.settings.max_reported_tokens}
        operation = budget.remaining
    else:
        now[0] += 5
        operation = budget.remaining
    with pytest.raises(BudgetExceeded, match="budget_exhausted"):
        operation()
    assert budget.started_at == 10.0


def test_historical_chat_stays_visible_and_read_only(support_env):
    _, store, ctx = support_env
    journal.ensure_schema(store)
    store.append_message(ctx.user_id, ctx.session_id, {"role": "user", "content": "旧问题"})
    store.append_message(ctx.user_id, ctx.session_id, {"role": "assistant", "content": "旧答复"})
    view = session_view(store, ctx.user_id, ctx.session_id)
    assert view["read_only"]
    assert [message["content"] for message in view["messages"]] == ["旧问题", "旧答复"]


def test_api_uses_pi_and_history_reads_do_not_resubmit(tmp_path):
    calls = []

    class OfflinePi:
        def __init__(self, store, corpus, settings, on_progress):
            self.store = store

        def run(self, user_id, session_id, text, *, submission_id, action=None):
            calls.append((user_id, session_id, text, submission_id))
            turn = journal.claim(self.store, user_id, session_id, text, submission_id, action, 30)
            return journal.finish(
                self.store,
                user_id,
                turn,
                {"status": "completed", "reply": "离线 Pi 答复", "issue_id": None},
            )

    settings = Settings(api_key="offline-ui-key", database_path=tmp_path / "ui.sqlite")
    with TestClient(create_app(settings, agent_factory=OfflinePi)) as client:
        session = client.post("/api/sessions").json()["session_id"]
        result = client.post(
            f"/api/sessions/{session}/turns",
            json={"text": "VPN 断线", "submission_id": "ui-submit"},
        )
        assert '"event": "done"' in result.text and len(calls) == 1
        for _ in range(2):
            view = client.get(f"/api/sessions/{session}").json()
            assert view["backend"] == "pi" and not view["read_only"]
            assert [m["content"] for m in view["messages"]] == ["VPN 断线", "离线 Pi 答复"]
            client.get("/api/sessions")
        assert len(calls) == 1
    with Store(settings.database_path) as store:
        assert journal.is_pi_session(store, calls[0][0], calls[0][1])
        assert len(store.list_messages(calls[0][0], calls[0][1])) == 2


def test_historical_api_rejects_submission_and_new_chat_uses_pi(tmp_path):
    database = tmp_path / "history.sqlite"
    with Store(database) as store:
        session = store.create_session("EMP-001")["session_id"]
        store.append_message("EMP-001", session, {"role": "user", "content": "旧聊天"})

    def must_not_run(*args, **kwargs):
        raise AssertionError("Historical reads and rejected writes must not invoke Pi")

    settings = Settings(api_key="offline-ui-key", database_path=database)
    with TestClient(create_app(settings, agent_factory=must_not_run)) as client:
        view = client.get(f"/api/sessions/{session}").json()
        assert view["read_only"] and view["messages"][0]["content"] == "旧聊天"
        assert client.post(
            f"/api/sessions/{session}/turns", json={"text": "不要执行旧协议"}
        ).status_code == 409
        new_session = client.post("/api/sessions").json()["session_id"]
        view = client.get(f"/api/sessions/{new_session}").json()
        assert new_session != session and not view["read_only"] and view["backend"] == "pi"
