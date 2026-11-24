from datetime import timedelta

import pytest

from src.data import load_corpus
from src.state import StateService
from src.storage import Store
from src.tools import ToolContext


@pytest.fixture
def support_env(tmp_path):
    corpus = load_corpus()
    store = Store(
        tmp_path / "support.sqlite", clock=lambda: corpus.reference_time + timedelta(seconds=1)
    )
    store.import_resolutions(corpus.resolutions)
    user = corpus.employees[0].id
    session = store.create_session(user)["session_id"]
    state = StateService(store)
    issue = state.create_issue(
        user, session, "VPN connection issue", "connectivity", symptoms=["VPN disconnects"]
    )["issue_id"]
    yield corpus, store, ToolContext(user, session, issue)
    store.close()


@pytest.fixture
def offline_env(support_env, monkeypatch):
    """T12 scenarios fail closed on accidental sockets or real sleeping.

    Existing module/UI tests retain their own transports. This opt-in guard does
    not change the future T14 live-model runner or touch the demo database.
    """
    import socket
    import time

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("network and real sleeping are disabled in offline regression")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(time, "sleep", forbidden)
    yield support_env
