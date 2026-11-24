"""SQLite persistence; every personal read/write requires an application-bound employee ID.

Not authentication: callers must bind user_id from the trusted demo context, not model arguments.
Privacy filtering/publication and model-generated summaries belong to T06, not this module.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from src.data import Contract, Resolution, read_markdown
from src.privacy import redact_text, sanitize

STATUSES = (
    "investigating",
    "awaiting_user",
    "awaiting_verification",
    "awaiting_service_recovery",
    "resolved",
    "escalated",
)


class AccessDenied(LookupError):
    """Identical response for absent and non-owned resources."""


class ConflictError(ValueError):
    """Stale state or reuse of an idempotency key for different content."""


class IncompleteToolExchange(ValueError):
    pass


class FunctionCall(Contract):
    name: str = Field(min_length=1)
    arguments: str

    @model_validator(mode="after")
    def json_arguments(self) -> FunctionCall:
        if not isinstance(json.loads(self.arguments), dict):
            raise ValueError("tool arguments must be a JSON object")
        return self


class ToolCall(Contract):
    id: str = Field(min_length=1)
    type: Literal["function"] = "function"
    function: FunctionCall


class ChatMessage(Contract):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def shape(self) -> ChatMessage:
        if self.role == "tool":
            if not self.tool_call_id or self.content is None or self.tool_calls is not None:
                raise ValueError("tool result requires call ID and content only")
        elif self.tool_call_id is not None:
            raise ValueError("only tool messages may contain tool_call_id")
        if self.tool_calls is not None and (self.role != "assistant" or not self.tool_calls):
            raise ValueError("only assistant messages may contain nonempty tool calls")
        if self.content is None and not self.tool_calls:
            raise ValueError("message needs content or tool calls")
        return self


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _pending(messages: list[dict]) -> dict[str, dict]:
    pending: dict[str, dict] = {}
    seen = set()
    for raw in messages:
        message = ChatMessage.model_validate(raw)
        if message.role == "tool":
            if message.tool_call_id not in pending:
                raise ValueError("orphan or duplicate tool result")
            del pending[message.tool_call_id]
            continue
        if pending:
            raise IncompleteToolExchange("complete pending tools before adding another message")
        for call in message.tool_calls or []:
            if call.id in seen:
                raise ValueError("tool call IDs must be unique within a session")
            seen.add(call.id)
            pending[call.id] = call.model_dump()
    return pending


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_owner ON sessions(user_id);
CREATE TABLE IF NOT EXISTS tool_requests (
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    invocation_id TEXT NOT NULL, issue_id TEXT REFERENCES issues(issue_id),
    tool TEXT NOT NULL, arguments_json TEXT NOT NULL, result_json TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY(session_id, invocation_id)
);
CREATE TABLE IF NOT EXISTS session_preferences (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    preference TEXT NOT NULL DEFAULT 'auto', effective TEXT NOT NULL DEFAULT 'en'
);
CREATE TABLE IF NOT EXISTS protocol_versions (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id), config TEXT NOT NULL, generation INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS application_probes (
    turn_id TEXT NOT NULL REFERENCES agent_turns(turn_id),
    signature TEXT NOT NULL, result_json TEXT NOT NULL,
    PRIMARY KEY (turn_id, signature)
);
CREATE TABLE IF NOT EXISTS turn_budgets (
    turn_id TEXT PRIMARY KEY REFERENCES agent_turns(turn_id), payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issues (
    issue_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
    title TEXT NOT NULL, category TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('investigating','awaiting_user','awaiting_verification',
        'awaiting_service_recovery','resolved','escalated')),
    state_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE, session_id TEXT NOT NULL REFERENCES sessions(session_id),
    issue_id TEXT REFERENCES issues(issue_id), payload_json TEXT NOT NULL,
    submission_id TEXT, created_at TEXT NOT NULL,
    UNIQUE(session_id, submission_id)
);
CREATE TABLE IF NOT EXISTS resolution_history (
    resolution_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL CHECK(source_type IN ('synthetic_seed','live_session')),
    source_issue_id TEXT UNIQUE REFERENCES issues(issue_id), owner_user_id TEXT,
    visibility TEXT NOT NULL CHECK(visibility IN ('shared','private')),
    valid INTEGER NOT NULL DEFAULT 1 CHECK(valid IN (0,1)),
    document_json TEXT NOT NULL, created_at TEXT NOT NULL,
    CHECK ((source_type='synthetic_seed' AND source_issue_id IS NULL AND owner_user_id IS NULL
            AND visibility='shared') OR
           (source_type='live_session' AND source_issue_id IS NOT NULL AND owner_user_id IS NOT NULL
            AND visibility='private'))
);
CREATE TABLE IF NOT EXISTS handoffs (
    handoff_id TEXT PRIMARY KEY, issue_id TEXT NOT NULL UNIQUE REFERENCES issues(issue_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_turns (
    turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
    submission_id TEXT NOT NULL, message_id TEXT NOT NULL REFERENCES messages(message_id),
    issue_id TEXT NOT NULL REFERENCES issues(issue_id), status TEXT NOT NULL,
    response_json TEXT, run_token TEXT, lease_until TEXT,
    UNIQUE(session_id, submission_id)
);
"""


def _serialized_write(method):
    """Reserve the write lock BEFORE read/check/write, including idempotency checks.

    A plain `with connection` only starts a transaction at the first write, which is
    too late for tool-pair validation or checking whether a success is still valid.
    """

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.transaction():
            return method(self, *args, **kwargs)

    return wrapped


class Store:
    def __init__(self, path: Path | str, clock: Callable[[], datetime] | None = None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), timeout=5)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        if self._conn.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2):
            self._conn.close()
            raise ValueError("unsupported database schema version")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate_case_bodies()

    def _migrate_case_bodies(self):
        """One-time data migration; all readers use body after this transaction."""
        if self._conn.execute("PRAGMA user_version").fetchone()[0] >= 2:
            return
        with self.transaction():
            for row in self._conn.execute(
                "SELECT resolution_id,source_type,document_json FROM resolution_history"
            ).fetchall():
                doc = json.loads(row["document_json"])
                conversation = doc.pop("conversation", None)
                steps = doc.pop("steps", [])
                if conversation:
                    doc["body"] = (
                        "# Archived conversation\n\n"
                        "Assistant messages are recommendations, not evidence of execution. "
                        "Overall recovery and root cause are not independently verified.\n\n"
                        + "\n\n".join(f"## {m['role']}\n{m['content']}" for m in conversation)
                    )
                elif not doc.get("body"):
                    label = (
                        "Synthetic case narrative"
                        if row["source_type"] == "synthetic_seed"
                        else "Legacy reported case narrative (not independently verified)"
                    )
                    doc["body"] = (
                        f"# {label}\n\n## Symptoms\n{doc.get('symptoms', '')}"
                        "\n\n## Recorded narrative\n"
                        + "\n".join(f"- {step}" for step in steps)
                        + f"\n\n## Reported result\n{doc.get('result', '')}"
                    )
                if row["source_type"] == "synthetic_seed":
                    doc["result_basis"] = "synthetic_seed"
                self._conn.execute(
                    "UPDATE resolution_history SET document_json=? WHERE resolution_id=?",
                    (_json(doc), row["resolution_id"]),
                )
            self._conn.execute("PRAGMA user_version=2")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self):
        """Short write reservation, nestable via savepoints (never hold across API calls)."""
        if self._conn.in_transaction:
            savepoint = f"sp_{uuid4().hex}"
            self._conn.execute(f"SAVEPOINT {savepoint}")
            try:
                yield
            except BaseException:
                self._conn.execute(f"ROLLBACK TO {savepoint}")
                self._conn.execute(f"RELEASE {savepoint}")
                raise
            else:
                self._conn.execute(f"RELEASE {savepoint}")
        else:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                yield

    def _now(self) -> str:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("storage clock must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()

    def create_session(self, user_id: str) -> dict:
        if not user_id.strip():
            raise ValueError("employee ID is required")
        session_id, now = str(uuid4()), self._now()
        with self.transaction():
            self._conn.execute(
                "INSERT INTO sessions VALUES (?,?,?,?)", (session_id, user_id, now, now)
            )
        return self.get_session(user_id, session_id)

    def get_session(self, user_id: str, session_id: str) -> dict:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id=? AND user_id=?", (session_id, user_id)
        ).fetchone()
        if row is None:
            raise AccessDenied("resource unavailable")
        return dict(row)

    def language(self, user_id: str, session_id: str) -> dict:
        self.get_session(user_id, session_id)
        row = self._conn.execute(
            "SELECT preference,effective FROM session_preferences WHERE session_id=?", (session_id,)
        ).fetchone()
        return dict(row) if row else {"preference": "auto", "effective": "en"}

    def set_language(self, user_id: str, session_id: str, *, preference=None, text=None) -> dict:
        from src.language import requested_preference, resolve_language

        with self.transaction():
            old = self.language(user_id, session_id)
            preference = (
                preference
                if preference is not None
                else (requested_preference(text or "") or old["preference"])
            )
            if preference not in {"auto", "zh", "en"}:
                raise ValueError("invalid reply language")
            effective = resolve_language(text or "", preference, old["effective"])
            self._conn.execute(
                "INSERT INTO session_preferences VALUES (?,?,?) ON CONFLICT(session_id) "
                "DO UPDATE SET preference=excluded.preference,effective=excluded.effective",
                (session_id, preference, effective),
            )
        return {"preference": preference, "effective": effective}

    def list_sessions(self, user_id: str) -> list[dict]:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM sessions WHERE user_id=? ORDER BY updated_at DESC, rowid DESC",
                (user_id,),
            )
        ]

    def latest_turn(self, user_id: str, session_id: str) -> dict | None:
        """Owned UI projection; never expose execution tokens or raw protocol payloads."""
        self.get_session(user_id, session_id)
        row = self._conn.execute(
            "SELECT submission_id,issue_id,status,response_json,lease_until FROM agent_turns "
            "WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["response"] = json.loads(result.pop("response_json") or "null")
        return result

    def create_issue(self, user_id: str, session_id: str, title: str, category: str) -> dict:
        self.get_session(user_id, session_id)
        if not title.strip() or category not in {
            "account",
            "application",
            "connectivity",
            "access",
            "complex",
            "unknown",
        }:
            raise ValueError("issue title and valid category required")
        issue_id, now = str(uuid4()), self._now()
        with self.transaction():
            self._conn.execute(
                "INSERT INTO issues VALUES (?,?,?,?,?,?,0,?,?)",
                (
                    issue_id,
                    session_id,
                    redact_text(title),
                    category,
                    "investigating",
                    "{}",
                    now,
                    now,
                ),
            )
        return self.get_issue(user_id, issue_id)

    def get_issue(self, user_id: str, issue_id: str) -> dict:
        row = self._conn.execute(
            "SELECT i.* FROM issues i JOIN sessions s USING(session_id) "
            "WHERE i.issue_id=? AND s.user_id=?",
            (issue_id, user_id),
        ).fetchone()
        if row is None:
            raise AccessDenied("resource unavailable")
        result = dict(row)
        result["state"] = json.loads(result.pop("state_json"))
        return result

    def list_issues(self, user_id: str, session_id: str) -> list[dict]:
        self.get_session(user_id, session_id)
        return [
            self.get_issue(user_id, r[0])
            for r in self._conn.execute(
                "SELECT issue_id FROM issues WHERE session_id=? ORDER BY rowid", (session_id,)
            )
        ]

    def update_issue(
        self, user_id: str, issue_id: str, *, status: str, state: dict, expected_revision: int
    ) -> dict:
        """Persistence primitive. StateService enforces semantic transitions in T05."""
        old = self.get_issue(user_id, issue_id)
        if status not in STATUSES:
            raise ValueError("unknown issue state")
        now = self._now()
        with self.transaction():
            changed = self._conn.execute(
                "UPDATE issues SET status=?,state_json=?,revision=revision+1,updated_at=? "
                "WHERE issue_id=? AND revision=?",
                (status, _json(sanitize(state)), now, issue_id, expected_revision),
            )
            if changed.rowcount != 1:
                raise ConflictError("stale issue revision; reload before updating")
            if status != "resolved":
                self._conn.execute(
                    "UPDATE resolution_history SET valid=0 WHERE source_issue_id=?", (issue_id,)
                )
            self._conn.execute(
                "UPDATE sessions SET updated_at=? WHERE session_id=?", (now, old["session_id"])
            )
        return self.get_issue(user_id, issue_id)

    @_serialized_write
    def append_message(
        self,
        user_id: str,
        session_id: str,
        message: dict | ChatMessage,
        *,
        issue_id: str | None = None,
        submission_id: str | None = None,
    ) -> dict:
        self.get_session(user_id, session_id)
        if issue_id is not None and self.get_issue(user_id, issue_id)["session_id"] != session_id:
            raise AccessDenied("resource unavailable")
        parsed = ChatMessage.model_validate(message)
        payload = parsed.model_dump(exclude_none=True)
        if payload.get("content") is not None:
            payload["content"] = redact_text(payload["content"])
        for call in payload.get("tool_calls", []):
            call["function"]["arguments"] = _json(
                sanitize(json.loads(call["function"]["arguments"]))
            )
        if submission_id is not None:
            existing = self._conn.execute(
                "SELECT * FROM messages WHERE session_id=? AND submission_id=?",
                (session_id, submission_id),
            ).fetchone()
            if existing:
                if existing["payload_json"] != _json(payload) or existing["issue_id"] != issue_id:
                    raise ConflictError("submission ID reused for different content")
                return self.get_message(user_id, existing["message_id"])
        previous = [m["message"] for m in self.list_messages(user_id, session_id)]
        _pending([*previous, payload])
        message_id, now = str(uuid4()), self._now()
        self._conn.execute(
            "INSERT INTO messages(message_id,session_id,issue_id,payload_json,submission_id,"
            "created_at) VALUES (?,?,?,?,?,?)",
            (message_id, session_id, issue_id, _json(payload), submission_id, now),
        )
        self._conn.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (now, session_id))
        return self.get_message(user_id, message_id)

    @staticmethod
    def _message_row(row) -> dict:
        result = dict(row)
        result["message"] = sanitize(json.loads(result.pop("payload_json")))
        return result

    def get_message(self, user_id: str, message_id: str) -> dict:
        row = self._conn.execute(
            "SELECT m.* FROM messages m JOIN sessions s USING(session_id) "
            "WHERE m.message_id=? AND s.user_id=?",
            (message_id, user_id),
        ).fetchone()
        if row is None:
            raise AccessDenied("resource unavailable")
        return self._message_row(row)

    def list_messages(self, user_id: str, session_id: str) -> list[dict]:
        self.get_session(user_id, session_id)
        return [
            self._message_row(r)
            for r in self._conn.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY sequence", (session_id,)
            )
        ]

    def pending_tool_calls(self, user_id: str, session_id: str) -> list[dict]:
        return list(
            _pending([m["message"] for m in self.list_messages(user_id, session_id)]).values()
        )

    def model_messages(self, user_id: str, session_id: str) -> list[dict]:
        messages = [m["message"] for m in self.list_messages(user_id, session_id)]
        if _pending(messages):
            raise IncompleteToolExchange("tool results missing; recover before model request")
        return messages

    def import_markdown_seeds(self, directory: Path) -> int:
        paths = sorted(directory.glob("*.md"))
        if not paths:
            raise ValueError("no seed Markdown files found")
        return self.import_resolutions([read_markdown(path, Resolution) for path in paths])

    def import_resolutions(self, resolutions: list[Resolution]) -> int:
        # Validate the complete batch before opening a transaction.
        docs = [
            {
                **Resolution.model_validate(r).model_dump(mode="json"),
                "result_basis": "synthetic_seed",
            }
            for r in resolutions
        ]
        inserted = 0
        with self.transaction():
            for doc in docs:
                cursor = self._conn.execute(
                    "INSERT INTO resolution_history VALUES (?, 'synthetic_seed', NULL, NULL, "
                    "'shared', 1, ?, ?) ON CONFLICT(resolution_id) DO NOTHING",
                    (doc["id"], _json(doc), self._now()),
                )
                inserted += cursor.rowcount
        return inserted

    @_serialized_write
    def save_resolution(
        self,
        user_id: str,
        issue_id: str,
        *,
        symptoms: str,
        body: str,
        result: str,
        evidence_refs: list[str],
    ) -> dict:
        """Persist an already confirmed result. T06 adds summary/evidence/privacy validation."""
        issue = self.get_issue(user_id, issue_id)
        confirmation = issue["state"].get("confirmation")
        if issue["status"] != "resolved" or not confirmation:
            raise ValueError("only explicitly confirmed resolved issues may be archived")
        message = self.get_message(user_id, confirmation["message_id"])
        if (
            message["session_id"] != issue["session_id"]
            or message["message"]["role"] != "user"
            or message["issue_id"] not in (None, issue_id)
        ):
            raise ValueError("invalid employee confirmation reference")
        if not symptoms.strip() or not body.strip() or not result.strip():
            raise ValueError("successful summary requires symptoms, body and result")
        doc = {
            "title": redact_text(issue["title"], personal=True),
            "category": issue["category"],
            "symptoms": redact_text(symptoms, personal=True),
            "body": redact_text(body, personal=True),
            "result": redact_text(result, personal=True),
            "evidence_refs": evidence_refs,
            "result_basis": "employee_reported",
            "confirmation": confirmation,
        }
        row = self._conn.execute(
            "INSERT INTO resolution_history VALUES (?, 'live_session', ?, ?, 'private', 1, ?, ?) "
            "ON CONFLICT(source_issue_id) DO UPDATE SET document_json=excluded.document_json,valid=1 "
            "RETURNING resolution_id",
            (f"RES-{uuid4()}", issue_id, user_id, _json(doc), self._now()),
        ).fetchone()
        return self.get_resolution(user_id, row[0])

    @staticmethod
    def _resolution_row(row) -> dict:
        result = dict(row)
        result["document"] = json.loads(result.pop("document_json"))
        result["valid"] = bool(result["valid"])
        # Owner/session links are deliberately omitted from public/shared results.
        if result["visibility"] == "shared":
            result.pop("owner_user_id")
            result.pop("source_issue_id")
        return result

    def list_resolutions(self, user_id: str) -> list[dict]:
        return [
            self._resolution_row(r)
            for r in self._conn.execute(
                "SELECT * FROM resolution_history WHERE valid=1 AND "
                "(visibility='shared' OR owner_user_id=?) ORDER BY created_at,resolution_id",
                (user_id,),
            )
        ]

    def get_resolution(self, user_id: str, resolution_id: str) -> dict:
        row = self._conn.execute(
            "SELECT * FROM resolution_history WHERE resolution_id=? AND valid=1 AND "
            "(visibility='shared' OR owner_user_id=?)",
            (resolution_id, user_id),
        ).fetchone()
        if row is None:
            raise AccessDenied("resource unavailable")
        return self._resolution_row(row)

    @_serialized_write
    def save_handoff(
        self, user_id: str, issue_id: str, payload: dict, *, refresh: bool = False
    ) -> dict:
        """Keep one current local snapshot per issue; unchanged retries are idempotent.

        A genuine re-escalation refreshes the snapshot, retaining its ID/creation time
        and increasing revision. Prior agent-turn responses retain their own snapshots.
        """
        issue = self.get_issue(user_id, issue_id)
        metadata = {
            "handoff_id",
            "issue_id",
            "session_id",
            "employee_id",
            "created_at",
            "updated_at",
            "revision",
        }
        content = {k: v for k, v in sanitize(payload).items() if k not in metadata}
        row = self._conn.execute(
            "SELECT payload_json FROM handoffs WHERE issue_id=?", (issue_id,)
        ).fetchone()
        old = json.loads(row[0]) if row else None
        if old and (not refresh or {k: v for k, v in old.items() if k not in metadata} == content):
            return old
        hid = old["handoff_id"] if old else f"HO-{uuid4()}"
        now = self._now()
        created = old["created_at"] if old else now
        body = {
            **content,
            "handoff_id": hid,
            "issue_id": issue_id,
            "session_id": issue["session_id"],
            "employee_id": user_id,
            "created_at": created,
            "updated_at": now,
            "revision": int(old.get("revision", 1)) + 1 if old else 1,
        }
        self._conn.execute(
            "INSERT INTO handoffs VALUES (?,?,?,?,?) ON CONFLICT(issue_id) DO UPDATE SET payload_json=excluded.payload_json",
            (hid, issue_id, issue["session_id"], _json(body), created),
        )
        return self.get_handoff(user_id, issue_id)

    def get_handoff(self, user_id: str, issue_id: str) -> dict:
        self.get_issue(user_id, issue_id)
        row = self._conn.execute(
            "SELECT payload_json FROM handoffs WHERE issue_id=?", (issue_id,)
        ).fetchone()
        if row is None:
            raise AccessDenied("resource unavailable")
        return json.loads(row[0])

    def personal_history(self, user_id: str, *, session_id: str | None = None) -> list[dict]:
        """Return summaries or a single owned conversation, including unsuccessful issues."""
        sessions = (
            [self.get_session(user_id, session_id)]
            if session_id is not None
            else self.list_sessions(user_id)
        )
        result = []
        for session in sessions:
            item = {**session, "issues": self.list_issues(user_id, session["session_id"])}
            if session_id is not None:
                item["messages"] = self.list_messages(user_id, session_id)
            result.append(item)
        return result


if __name__ == "__main__":
    from dotenv import load_dotenv

    from src.config import PROJECT_ROOT, Settings

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    settings = Settings.from_env()
    with Store(settings.database_path) as database:
        count = database.import_markdown_seeds(settings.data_dir / "resolutions")
    print(f"Database initialized. Imported {count} new synthetic resolved cases. No API called.")
