"""Explicit, same-session provenance links; never infer issue ownership from text.

Original message/query rows stay unchanged. Target memory receives only selected,
existing receipts, keeping source and query times. Link rows document their origin.
"""

from __future__ import annotations

import json

from src.state import StateService, Status, UserReport
from src.storage import AccessDenied

SCHEMA = """
CREATE TABLE IF NOT EXISTS case_material_links (
    target_issue_id TEXT NOT NULL REFERENCES issues(issue_id),
    kind TEXT NOT NULL CHECK(kind IN ('message','query')),
    reference TEXT NOT NULL,
    source_issue_id TEXT REFERENCES issues(issue_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    linked_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY(target_issue_id,kind,reference)
);
"""


def ensure_schema(store):
    store._conn.execute(SCHEMA)


def materials(store, user_id, session_id):
    """Stable, session-scoped handles. Reading never changes selected issue."""
    store.get_session(user_id, session_id)
    messages = store.list_messages(user_id, session_id)
    queries = store._conn.execute(
        "SELECT rowid AS number,* FROM tool_requests WHERE session_id=? ORDER BY rowid",
        (session_id,),
    ).fetchall()
    return {
        "employee_messages": [
            {
                "sequence": m["sequence"],
                "issue_id": m["issue_id"],
                "text": m["message"]["content"][:800],
            }
            for m in messages
            if m["message"]["role"] == "user"
        ][-12:],
        "queries": [
            {
                "query": f"query_{q['number']}",
                "issue_id": q["issue_id"],
                "tool": q["tool"],
                "arguments": json.loads(q["arguments_json"]),
                "ok": json.loads(q["result_json"])["ok"],
            }
            for q in queries[-20:]
        ],
    }


def _selected(store, user_id, issue_id, message_sequences, query_handles):
    issue = store.get_issue(user_id, issue_id)
    session = issue["session_id"]
    if issue["status"] == Status.RESOLVED:
        raise ValueError("reopen a resolved case before linking material")
    messages = {m["sequence"]: m for m in store.list_messages(user_id, session)}
    selected = []
    for sequence in dict.fromkeys(message_sequences):
        m = messages.get(sequence)
        if not m or m["message"]["role"] != "user":
            raise AccessDenied("unavailable material")
        selected.append(("message", m["message_id"], m["issue_id"], m))
    for handle in dict.fromkeys(query_handles):
        if not handle.startswith("query_") or not handle[6:].isdigit():
            raise ValueError("invalid query handle")
        q = store._conn.execute(
            "SELECT * FROM tool_requests WHERE rowid=? AND session_id=?",
            (int(handle[6:]), session),
        ).fetchone()
        if not q:
            raise AccessDenied("unavailable material")
        selected.append(("query", q["invocation_id"], q["issue_id"], dict(q)))
    if not selected:
        raise ValueError("select existing messages or queries to associate")
    return issue, selected


def associate(store, user_id, issue_id, message_sequences, query_handles, reason):
    if not reason.strip():
        raise ValueError("association reason required")
    with store.transaction():
        issue, selected = _selected(store, user_id, issue_id, message_sequences, query_handles)
        state = StateService(store)
        # Validate ALL references before any mutation; partial association cannot commit.
        new = []
        for kind, reference, origin, row in selected:
            found = store._conn.execute(
                "SELECT 1 FROM case_material_links WHERE target_issue_id=? AND kind=? AND reference=?",
                (issue_id, kind, reference),
            ).fetchone()
            if found:
                continue
            store._conn.execute(
                "INSERT INTO case_material_links VALUES (?,?,?,?,?,?,?)",
                (issue_id, kind, reference, origin, issue["session_id"], store._now(), reason),
            )
            new.append((kind, row, origin))
        for kind, row, origin in new:
            if kind == "query" and origin and origin != issue_id:
                original = state.memory(user_id, origin)
                receipt = json.loads(row["result_json"])
                for eid in receipt.get("evidence_refs", []):
                    e = original.evidence.get(eid)
                    if e and e.invocation_id == row["invocation_id"]:
                        state.record_tool_result(
                            user_id,
                            issue_id,
                            tool=e.tool,
                            source_ref=e.source_ref,
                            source_at=e.source_at,
                            summary=e.summary,
                            success=e.success,
                            topic=e.topic,
                            subject=e.subject,
                            value=e.value,
                            invocation_id=e.invocation_id,
                            queried_at=e.queried_at,
                        )
                for attempt in original.tool_attempts:
                    if attempt.invocation_id == row["invocation_id"]:
                        state.record_tool_attempt(user_id, issue_id, attempt)
            elif kind == "message":
                current = store.get_issue(user_id, issue_id)
                memory = state.memory(user_id, issue_id)
                if not any(r.message_id == row["message_id"] for r in memory.user_reports):
                    memory.user_reports.append(
                        UserReport(
                            message_id=row["message_id"],
                            text=row["message"]["content"],
                            recorded_at=row["created_at"],
                        )
                    )
                    state._save(user_id, current, memory)
        return {"associated": len(new), "selected": len(selected)}


def links(store, user_id, issue_id):
    store.get_issue(user_id, issue_id)
    return [
        dict(r)
        for r in store._conn.execute(
            "SELECT kind,reference,source_issue_id,linked_at,reason FROM case_material_links "
            "WHERE target_issue_id=? ORDER BY rowid",
            (issue_id,),
        )
    ]
