"""Read-only reconciliation of an acceptance event log with its SQLite journal."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def reconcile(database: Path, events: Path) -> dict:
    errors = []
    checked = {
        "inputs": 0,
        "tool_results": 0,
        "turn_results": 0,
        "assistant_messages": 0,
        "handoffs": 0,
        "interrupted": 0,
    }
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        for line_number, line in enumerate(events.read_text().splitlines(), 1):
            row = json.loads(line)
            turn_id = row.get("turn_id")
            if not turn_id:
                continue
            event = row["event"]
            if event == "turn_started":
                checked["inputs"] += 1
                saved = db.execute(
                    "SELECT m.payload_json,t.submission_id,t.session_id FROM pi_turns t JOIN messages m ON m.message_id=t.message_id WHERE t.turn_id=?",
                    (turn_id,),
                ).fetchone()
                if (
                    not saved
                    or json.loads(saved[0]).get("content") != row["text"]
                    or saved[1] != row["submission_id"]
                    or saved[2] != row["session_id"]
                ):
                    errors.append(
                        {"line": line_number, "kind": "input_identity_mismatch", "turn_id": turn_id}
                    )
            elif event == "tool_result":
                saved = db.execute(
                    "SELECT result_json FROM pi_tool_calls WHERE turn_id=? AND call_id=?",
                    (turn_id, row["call_id"]),
                ).fetchone()
                checked["tool_results"] += 1
                if not saved or json.loads(saved[0]) != row["result"]:
                    errors.append(
                        {
                            "line": line_number,
                            "kind": "tool_result_mismatch",
                            "turn_id": turn_id,
                            "call_id": row["call_id"],
                        }
                    )
            elif event == "turn_finished":
                if row["result"].get("outcome") == "interrupted":
                    checked["interrupted"] += 1
                saved = db.execute(
                    "SELECT response_json FROM pi_turns WHERE turn_id=?", (turn_id,)
                ).fetchone()
                checked["turn_results"] += 1
                if not saved or json.loads(saved[0] or "null") != row["result"]:
                    errors.append(
                        {"line": line_number, "kind": "turn_result_mismatch", "turn_id": turn_id}
                    )
            elif event == "runtime_progress" and row.get("progress_event") == "reply_finished":
                checked["assistant_messages"] += 1
                text = row["details"]["text"]
                messages = db.execute(
                    "SELECT payload_json FROM messages WHERE session_id=?", (row["session_id"],)
                )
                if not any(
                    json.loads(message[0]).get("role") == "assistant"
                    and json.loads(message[0]).get("content") == text
                    for message in messages
                ):
                    errors.append(
                        {"line": line_number, "kind": "assistant_not_saved", "turn_id": turn_id}
                    )
        for saved in db.execute("SELECT payload_json FROM handoffs"):
            checked["handoffs"] += 1
            record = json.loads(saved[0])
            user_messages = [m for m in record.get("conversation", []) if m.get("role") == "user"]
            if (record.get("summary_through_sequence") or 0) > (
                record.get("record_through_sequence") or 0
            ):
                errors.append(
                    {"kind": "handoff_sequence_order", "handoff_id": record["handoff_id"]}
                )
            if (
                user_messages
                and max(m["sequence"] for m in user_messages)
                > (record.get("summary_through_sequence") or 0)
                and not record.get("summary_stale")
            ):
                errors.append(
                    {"kind": "handoff_stale_unmarked", "handoff_id": record["handoff_id"]}
                )
    return {"ok": not errors, "checked": checked, "errors": errors}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    args = parser.parse_args()
    result = reconcile(args.database, args.events)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
