from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.data import load_corpus
from src.storage import AccessDenied, ConflictError, IncompleteToolExchange, Store

NOW = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "nested" / "support.sqlite", clock=lambda: NOW) as value:
        yield value


def new_issue(store, user="EMP-001", session=None, title="VPN", category="connectivity"):
    session = session or store.create_session(user)["session_id"]
    return store.create_issue(user, session, title, category)


def resolve_fixture(store, issue):
    """Low-level T04 fixture; semantic confirmation gates are tested in T05."""
    message = store.append_message(
        "EMP-001",
        issue["session_id"],
        {"role": "user", "content": "VPN is working now"},
        issue_id=issue["issue_id"],
    )
    return store.update_issue(
        "EMP-001",
        issue["issue_id"],
        status="resolved",
        state={"confirmation": {"message_id": message["message_id"]}},
        expected_revision=issue["revision"],
    )


def archive(store, issue):
    return store.save_resolution(
        "EMP-001",
        issue["issue_id"],
        symptoms="VPN disconnected",
        body="Employee reported updating the client",
        result="Employee reports restored",
        evidence_refs=["KB-CON-001"],
    )


def test_restart_restores_messages_issues_handoffs_and_idempotency(tmp_path):
    path = tmp_path / "support.sqlite"
    with Store(path) as db:
        issue = new_issue(db)
        sid = issue["session_id"]
        message = db.append_message(
            "EMP-001",
            sid,
            {"role": "user", "content": "VPN down"},
            issue_id=issue["issue_id"],
            submission_id="client-1",
        )
        assert (
            db.append_message(
                "EMP-001",
                sid,
                {"role": "user", "content": "VPN down"},
                issue_id=issue["issue_id"],
                submission_id="client-1",
            )
            == message
        )
        handoff = db.save_handoff("EMP-001", issue["issue_id"], {"summary": "VPN down"})
    with Store(path) as db:
        assert db.list_sessions("EMP-001")[0]["session_id"] == sid
        assert db.list_issues("EMP-001", sid)[0]["issue_id"] == issue["issue_id"]
        assert db.model_messages("EMP-001", sid) == [{"role": "user", "content": "VPN down"}]
        assert db.get_handoff("EMP-001", issue["issue_id"]) == handoff
        assert db.save_handoff("EMP-001", issue["issue_id"], {"summary": "retry"}) == handoff
        with pytest.raises(ConflictError):
            db.append_message(
                "EMP-001", sid, {"role": "user", "content": "different"}, submission_id="client-1"
            )


def test_seed_import_is_idempotent_and_shared_without_owner_links(store):
    seeds = load_corpus().resolutions
    assert store.import_resolutions(seeds) == 10
    assert store.import_resolutions(seeds) == 0
    assert len(store.list_resolutions("EMP-002")) == 10
    record = store.get_resolution("EMP-002", seeds[0].id)
    assert record["source_type"] == "synthetic_seed"
    assert "owner_user_id" not in record and "source_issue_id" not in record


def test_seed_import_rolls_back_on_mid_batch_failure(store):
    calls = 0

    def broken_clock():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected clock failure")
        return NOW

    store.clock = broken_clock
    with pytest.raises(RuntimeError):
        store.import_resolutions(load_corpus().resolutions)
    assert store.list_resolutions("EMP-001") == []


def test_two_issues_one_session_archive_independently_and_idempotently(store):
    a = new_issue(store)
    b = new_issue(store, session=a["session_id"], title="Login", category="account")
    a, b = resolve_fixture(store, a), resolve_fixture(store, b)
    first, second = archive(store, a), archive(store, b)
    assert first["resolution_id"] != second["resolution_id"]
    assert archive(store, a)["resolution_id"] == first["resolution_id"]
    assert len(store.list_resolutions("EMP-001")) == 2
    assert store.list_resolutions("EMP-002") == []
    with pytest.raises(AccessDenied):
        store.get_resolution("EMP-002", first["resolution_id"])


def test_unresolved_or_escalated_issue_cannot_enter_resolution_history(store):
    issue = new_issue(store)
    with pytest.raises(ValueError, match="confirmed"):
        archive(store, issue)
    store.update_issue(
        "EMP-001", issue["issue_id"], status="escalated", state={}, expected_revision=0
    )
    with pytest.raises(ValueError, match="confirmed"):
        archive(store, issue)
    assert store.list_resolutions("EMP-001") == []


def test_reopened_issue_invalidates_success_and_can_be_reconfirmed(store):
    issue = resolve_fixture(store, new_issue(store))
    rid = archive(store, issue)["resolution_id"]
    reopened = store.update_issue(
        "EMP-001",
        issue["issue_id"],
        status="investigating",
        state={},
        expected_revision=issue["revision"],
    )
    with pytest.raises(AccessDenied):
        store.get_resolution("EMP-001", rid)
    assert archive(store, resolve_fixture(store, reopened))["resolution_id"] == rid


def test_optimistic_revision_rejects_lost_updates(store):
    issue = new_issue(store)
    store.update_issue(
        "EMP-001", issue["issue_id"], status="awaiting_user", state={}, expected_revision=0
    )
    with pytest.raises(ConflictError):
        store.update_issue(
            "EMP-001", issue["issue_id"], status="resolved", state={}, expected_revision=0
        )
    assert store.get_issue("EMP-001", issue["issue_id"])["status"] == "awaiting_user"


def test_all_personal_reads_and_mutations_check_owner(store):
    issue = resolve_fixture(store, new_issue(store))
    archive(store, issue)
    message = store.list_messages("EMP-001", issue["session_id"])[0]
    store.save_handoff("EMP-001", issue["issue_id"], {})
    actions = [
        lambda: store.get_session("EMP-002", issue["session_id"]),
        lambda: store.get_issue("EMP-002", issue["issue_id"]),
        lambda: store.list_issues("EMP-002", issue["session_id"]),
        lambda: store.list_messages("EMP-002", issue["session_id"]),
        lambda: store.get_message("EMP-002", message["message_id"]),
        lambda: store.model_messages("EMP-002", issue["session_id"]),
        lambda: store.pending_tool_calls("EMP-002", issue["session_id"]),
        lambda: store.personal_history("EMP-002", session_id=issue["session_id"]),
        lambda: store.append_message(
            "EMP-002", issue["session_id"], {"role": "user", "content": "fake"}
        ),
        lambda: store.update_issue(
            "EMP-002",
            issue["issue_id"],
            status="resolved",
            state={},
            expected_revision=issue["revision"],
        ),
        lambda: store.create_issue("EMP-002", issue["session_id"], "Fake", "unknown"),
        lambda: store.save_resolution(
            "EMP-002", issue["issue_id"], symptoms="x", body="y", result="z", evidence_refs=[]
        ),
        lambda: store.save_handoff("EMP-002", issue["issue_id"], {}),
        lambda: store.get_handoff("EMP-002", issue["issue_id"]),
    ]
    for action in actions:
        with pytest.raises(AccessDenied, match="resource unavailable"):
            action()
    assert store.personal_history("EMP-002") == []
    assert store.list_sessions("EMP-002' OR 1=1 --") == []


def test_cannot_attach_message_to_issue_in_another_session(store):
    issue = new_issue(store)
    other_session = store.create_session("EMP-001")["session_id"]
    with pytest.raises(AccessDenied):
        store.append_message(
            "EMP-001", other_session, {"role": "user", "content": "x"}, issue_id=issue["issue_id"]
        )


def test_tool_pairs_survive_restart_and_block_incomplete_model_replay(tmp_path):
    path = tmp_path / "tools.sqlite"
    with Store(path) as db:
        sid = db.create_session("EMP-001")["session_id"]
        db.append_message("EMP-001", sid, {"role": "user", "content": "Investigate"})
        calls = [
            {
                "id": f"call-{i}",
                "type": "function",
                "function": {"name": "check_status", "arguments": '{"service":"vpn"}'},
            }
            for i in (1, 2)
        ]
        db.append_message("EMP-001", sid, {"role": "assistant", "tool_calls": calls})
        with pytest.raises(IncompleteToolExchange):
            db.model_messages("EMP-001", sid)
        with pytest.raises(IncompleteToolExchange):
            db.append_message("EMP-001", sid, {"role": "user", "content": "continue"})
    with Store(path) as db:
        assert len(db.pending_tool_calls("EMP-001", sid)) == 2
        for call in reversed(calls):
            db.append_message(
                "EMP-001",
                sid,
                {"role": "tool", "tool_call_id": call["id"], "content": '{"status":"healthy"}'},
            )
        db.append_message("EMP-001", sid, {"role": "assistant", "content": "Please verify"})
        assert len(db.model_messages("EMP-001", sid)) == 5
        with pytest.raises(ValueError, match="orphan"):
            db.append_message(
                "EMP-001", sid, {"role": "tool", "tool_call_id": "call-1", "content": "duplicate"}
            )
        with pytest.raises(ValueError, match="unique"):
            db.append_message("EMP-001", sid, {"role": "assistant", "tool_calls": calls})


@pytest.mark.parametrize(
    "message",
    [
        {"role": "tool", "content": "no call ID"},
        {"role": "user", "content": "hello", "tool_call_id": "fake"},
        {"role": "assistant", "tool_calls": []},
        {
            "role": "assistant",
            "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "not json"}}],
        },
        {
            "role": "assistant",
            "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "[]"}}],
        },
    ],
)
def test_invalid_tool_message_shapes_rejected(store, message):
    sid = store.create_session("EMP-001")["session_id"]
    with pytest.raises((ValidationError, ValueError)):
        store.append_message("EMP-001", sid, message)


def test_personal_history_is_cross_session_not_copied_state(store):
    old = new_issue(store)
    store.update_issue(
        "EMP-001", old["issue_id"], status="escalated", state={"old": "facts"}, expected_revision=0
    )
    new = new_issue(store)
    history = store.personal_history("EMP-001")
    assert len(history) == 2
    assert {s["issues"][0]["status"] for s in history} == {"investigating", "escalated"}
    assert store.get_issue("EMP-001", new["issue_id"])["state"] == {}


def test_markdown_seed_entrypoint_parses_and_imports_idempotently(store):
    from src.config import PROJECT_ROOT

    directory = PROJECT_ROOT / "data" / "resolutions"
    assert store.import_markdown_seeds(directory) == 10
    assert store.import_markdown_seeds(directory) == 0


def test_concurrent_identical_submissions_and_archives_are_idempotent(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / "concurrent.sqlite"
    with Store(path) as database:
        issue = resolve_fixture(database, new_issue(database))
    start = Barrier(2)

    def submit(_):
        with Store(path) as database:
            start.wait(timeout=5)
            return database.append_message(
                "EMP-001",
                issue["session_id"],
                {"role": "user", "content": "Same submission"},
                submission_id="same-id",
            )["message_id"]

    def save(_):
        with Store(path) as database:
            start.wait(timeout=5)
            return archive(database, issue)["resolution_id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(set(pool.map(submit, range(2)))) == 1
        assert len(set(pool.map(save, range(2)))) == 1


def test_tool_validation_and_insert_hold_one_write_reservation(tmp_path, monkeypatch):
    import sqlite3

    import src.storage as module

    path = tmp_path / "tool-race.sqlite"
    with Store(path) as a, Store(path) as b:
        b._conn.execute("PRAGMA busy_timeout=0")
        sid = a.create_session("EMP-001")["session_id"]
        assistant = {
            "role": "assistant",
            "tool_calls": [
                {"id": "call-race", "function": {"name": "check_status", "arguments": "{}"}}
            ],
        }
        original = module._pending
        intercepted = False
        blocked = False

        def interleave(messages):
            nonlocal intercepted, blocked
            result = original(messages)
            if not intercepted:
                intercepted = True
                try:
                    b.append_message("EMP-001", sid, assistant)
                except sqlite3.OperationalError as exc:
                    assert "locked" in str(exc)
                    blocked = True
            return result

        monkeypatch.setattr(module, "_pending", interleave)
        a.append_message("EMP-001", sid, {"role": "user", "content": "Investigate"})
        assert blocked, "another writer must not slip between transcript validation and insertion"
        b.append_message("EMP-001", sid, assistant)
        b.append_message(
            "EMP-001", sid, {"role": "tool", "tool_call_id": "call-race", "content": "{}"}
        )
        assert len(a.model_messages("EMP-001", sid)) == 3


def test_archive_check_and_write_cannot_race_reopening(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "archive-race.sqlite"
    with Store(path) as a, Store(path) as b:
        b._conn.execute("PRAGMA busy_timeout=0")
        issue = resolve_fixture(a, new_issue(a))
        original = a.get_issue
        blocked = False

        def interleave(user_id, issue_id):
            nonlocal blocked
            result = original(user_id, issue_id)
            try:
                b.update_issue(
                    user_id,
                    issue_id,
                    status="investigating",
                    state={},
                    expected_revision=issue["revision"],
                )
            except sqlite3.OperationalError as exc:
                assert "locked" in str(exc)
                blocked = True
            return result

        monkeypatch.setattr(a, "get_issue", interleave)
        record = archive(a, issue)
        assert blocked, "resolved check and archive write must be in the same transaction"
        b.update_issue(
            "EMP-001",
            issue["issue_id"],
            status="investigating",
            state={},
            expected_revision=issue["revision"],
        )
        with pytest.raises(AccessDenied):
            b.get_resolution("EMP-001", record["resolution_id"])


def test_nested_transactions_isolate_inner_rollback_and_preserve_outer_atomicity(store):
    with store.transaction():
        first = store.create_session("EMP-001")
        with pytest.raises(RuntimeError, match="inner rollback"):
            with store.transaction():
                discarded = store.create_session("EMP-001")
                raise RuntimeError("inner rollback")
        last = store.create_session("EMP-001")
    assert {s["session_id"] for s in store.list_sessions("EMP-001")} == {
        first["session_id"],
        last["session_id"],
    }
    with pytest.raises(AccessDenied):
        store.get_session("EMP-001", discarded["session_id"])

    with pytest.raises(RuntimeError, match="outer rollback"):
        with store.transaction():
            store.create_session("EMP-001")
            with store.transaction():
                store.create_session("EMP-001")
            raise RuntimeError("outer rollback")
    assert len(store.list_sessions("EMP-001")) == 2
    assert not store._conn.in_transaction
