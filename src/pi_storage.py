"""Additive Pi journal. SQLite owns business writes, Pi owns its model transcript."""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

from src.storage import ConflictError, Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS pi_sessions (
 session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
 session_file TEXT, current_issue_id TEXT REFERENCES issues(issue_id)
);
CREATE TABLE IF NOT EXISTS pi_turns (
 turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
 submission_id TEXT NOT NULL, message_id TEXT NOT NULL REFERENCES messages(message_id),
 issue_id TEXT REFERENCES issues(issue_id), action_json TEXT,
 status TEXT NOT NULL, run_token TEXT, lease_until TEXT,
 response_json TEXT, budget_json TEXT NOT NULL DEFAULT '{}',
 UNIQUE(session_id,submission_id)
);
CREATE TABLE IF NOT EXISTS pi_tool_calls (
 turn_id TEXT NOT NULL REFERENCES pi_turns(turn_id), call_id TEXT NOT NULL,
 name TEXT NOT NULL, arguments_json TEXT NOT NULL, result_json TEXT NOT NULL,
 PRIMARY KEY(turn_id,call_id)
);
"""


def ensure_schema(store: Store):
    store._conn.executescript(SCHEMA)


def binding(store, user_id, session_id):
    store.get_session(user_id, session_id)
    row = store._conn.execute(
        "SELECT * FROM pi_sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    return dict(row) if row else None


def is_pi_session(store, user_id, session_id):
    store.get_session(user_id, session_id)
    exists = store._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pi_sessions'"
    ).fetchone()
    return bool(exists and binding(store, user_id, session_id))


def is_historical_session(store, user_id, session_id):
    """Identify stored pre-Pi history for read-only display, never select a backend.

    Shared fixtures may contain an unbound issue. Only actual messages or an old
    execution journal make a session historical; a fresh empty session uses Pi.
    """
    if is_pi_session(store, user_id, session_id):
        return False
    return bool(store.list_messages(user_id, session_id) or store.latest_turn(user_id, session_id))


def latest(store, user_id, session_id):
    if not binding(store, user_id, session_id):
        return None
    row = store._conn.execute(
        "SELECT submission_id,issue_id,status,response_json,lease_until FROM pi_turns WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["response"] = json.loads(result.pop("response_json") or "null")
    return result


def claim(store, user_id, session_id, text, submission_id, action, timeout, *, resume=False):
    """Never automatically replay an uncertain Pi run. A new submission continues the session."""
    store.get_session(user_id, session_id)
    action_json = json.dumps(action, sort_keys=True) if action else None
    with store.transaction():
        prior = store._conn.execute(
            "SELECT * FROM pi_turns WHERE session_id=? AND submission_id=?",
            (session_id, submission_id),
        ).fetchone()
        if prior:
            original = store.get_message(user_id, prior["message_id"])["message"]["content"]
            if (text is not None and text != original) or action_json != prior["action_json"]:
                raise ConflictError("submission ID cannot change input or action")
            if prior["response_json"]:
                return {"saved": json.loads(prior["response_json"])}
            if prior["lease_until"] and prior["lease_until"] > store._now():
                return {"busy": True}
            # Unknown outcome must not silently reprompt the same input.
            return {"interrupted": True}
        if resume:
            return {"interrupted": True}
        busy = store._conn.execute(
            "SELECT 1 FROM pi_turns WHERE session_id=? AND status='running' AND lease_until>?",
            (session_id, store._now()),
        ).fetchone()
        if busy:
            return {"busy": True}
        store._conn.execute(
            "UPDATE pi_turns SET status='interrupted',run_token=NULL,lease_until=NULL WHERE session_id=? AND status='running'",
            (session_id,),
        )
        store._conn.execute(
            "INSERT OR IGNORE INTO pi_sessions(session_id) VALUES (?)", (session_id,)
        )
        message = store.append_message(
            user_id, session_id, {"role": "user", "content": text}, submission_id=submission_id
        )
        token, turn_id = str(uuid4()), str(uuid4())
        store._conn.execute(
            "INSERT INTO pi_turns(turn_id,session_id,submission_id,message_id,action_json,status,run_token,lease_until) VALUES (?,?,?,?,?,'running',?,?)",
            (
                turn_id,
                session_id,
                submission_id,
                message["message_id"],
                action_json,
                token,
                (store.clock() + timedelta(seconds=timeout + 15)).isoformat(),
            ),
        )
        return dict(
            store._conn.execute("SELECT * FROM pi_turns WHERE turn_id=?", (turn_id,)).fetchone()
        )


def check_claim(store, turn):
    current = store._conn.execute(
        "SELECT run_token,lease_until FROM pi_turns WHERE turn_id=?", (turn["turn_id"],)
    ).fetchone()
    if (
        not current
        or current["run_token"] != turn["run_token"]
        or current["lease_until"] <= store._now()
    ):
        raise ConflictError("Pi run claim expired")


def record_assistant(store, user_id, turn, message_index, text, issue_id):
    """Persist one completed visible Pi message, not individual stream snapshots."""
    with store.transaction():
        check_claim(store, turn)
        return store.append_message(
            user_id,
            turn["session_id"],
            {"role": "assistant", "content": text},
            issue_id=issue_id,
            submission_id=f"pi-assistant:{turn['turn_id']}:{message_index}",
        )


def finish(store, user_id, turn, result, *, assistant_message_id=None):
    with store.transaction():
        check_claim(store, turn)
        if result.get("reply"):
            existing = (
                store.get_message(user_id, assistant_message_id) if assistant_message_id else None
            )
            if existing:
                if (
                    existing["session_id"] != turn["session_id"]
                    or existing["message"]["role"] != "assistant"
                    or existing["message"]["content"] != result["reply"]
                ):
                    raise ConflictError(
                        "final response must reference the published message unchanged"
                    )
            else:
                store.append_message(
                    user_id,
                    turn["session_id"],
                    {"role": "assistant", "content": result["reply"]},
                    issue_id=result.get("issue_id"),
                    submission_id="pi-response:" + turn["turn_id"],
                )
        store._conn.execute(
            "UPDATE pi_turns SET status=?,issue_id=?,response_json=?,run_token=NULL,lease_until=NULL WHERE turn_id=?",
            (
                result["status"],
                result.get("issue_id"),
                json.dumps(result, ensure_ascii=False),
                turn["turn_id"],
            ),
        )
    return result


def tool_audit(store, user_id, session_id):
    store.get_session(user_id, session_id)
    result = []
    for row in store._conn.execute(
        "SELECT t.issue_id,c.* FROM pi_tool_calls c JOIN pi_turns t USING(turn_id) WHERE t.session_id=? ORDER BY c.rowid",
        (session_id,),
    ):
        payload = json.loads(row["result_json"])
        result.append(
            {
                "message_id": row["call_id"],
                "tool_call_id": row["call_id"],
                "issue_id": row["issue_id"],
                "tool_name": row["name"],
                "arguments": json.loads(row["arguments_json"]),
                **payload,
            }
        )
    return result
