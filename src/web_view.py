"""Framework-independent safe employee display projection."""

import json

from src.presentation import render_pause_notice
from src.privacy import redact_text, sanitize
from src.records import Handoff
from src.storage import AccessDenied, Store


def session_view(store: Store, user_id: str, session_id: str) -> dict:
    """Explicit display allowlist: no system prompts, tool-call prose or reasoning fields."""
    store.get_session(user_id, session_id)
    messages, tools, calls = [], [], {}
    paused = {}
    for turn in store._conn.execute(
        "SELECT message_id,response_json FROM agent_turns WHERE session_id=? AND status='paused' AND response_json IS NOT NULL",
        (session_id,),
    ):
        response = json.loads(turn["response_json"])
        paused[turn["message_id"]] = render_pause_notice(
            response.get("error", {}), store.language(user_id, session_id)["effective"]
        )
    pending_notice = None
    for row in store.list_messages(user_id, session_id):
        message = row["message"]
        if message["role"] == "user":
            if pending_notice:
                messages.append(
                    {"role": "assistant", "content": pending_notice, "display_kind": "pause"}
                )
            pending_notice = paused.get(row["message_id"])
        if message.get("tool_calls"):
            for call in message["tool_calls"]:
                try:
                    calls[call["id"]] = json.loads(call["function"]["arguments"])
                except (KeyError, TypeError, ValueError):
                    continue
        if message["role"] == "tool":
            try:
                payload = json.loads(message["content"])
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                tools.append(
                    sanitize(
                        {
                            "message_id": row["message_id"],
                            "issue_id": row["issue_id"],
                            "tool_call_id": message.get("tool_call_id"),
                            "arguments": calls.get(message.get("tool_call_id"), {}),
                            **{
                                k: payload[k]
                                for k in (
                                    "tool_name",
                                    "ok",
                                    "data",
                                    "sources",
                                    "evidence_refs",
                                    "queried_at",
                                    "error",
                                )
                                if k in payload
                            },
                        }
                    )
                )
        elif message["role"] in {"user", "assistant"} and message.get("content"):
            messages.append(
                {"role": message["role"], "content": redact_text(message.get("content") or "")}
            )
    if pending_notice:
        messages.append({"role": "assistant", "content": pending_notice, "display_kind": "pause"})
    issues = store.list_issues(user_id, session_id)
    handoffs = []
    for issue in issues:
        try:
            handoffs.append(
                Handoff.model_validate(
                    store.get_handoff(user_id, issue["issue_id"])
                ).model_dump_json(indent=2)
            )
        except AccessDenied:
            pass
    from src.pi_storage import is_historical_session, is_pi_session, latest, tool_audit

    pi_session = is_pi_session(store, user_id, session_id)
    if pi_session:
        tools.extend(tool_audit(store, user_id, session_id))
    return {
        "messages": messages,
        "tools": tools,
        "issues": issues,
        "handoffs": handoffs,
        "latest": latest(store, user_id, session_id)
        if pi_session
        else store.latest_turn(user_id, session_id),
        "backend": "pi",
        "read_only": is_historical_session(store, user_id, session_id),
    }
