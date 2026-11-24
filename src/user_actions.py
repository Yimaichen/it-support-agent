"""Trusted employee events. These are never model tools or model decisions."""

from typing import Literal

from src.data import Contract


class UserAction(Contract):
    kind: Literal["confirm", "reopen"]
    issue_id: str


def apply_user_action(state, user_id, session_id, message_id, action: UserAction):
    state.select_target(user_id, session_id, action.issue_id)
    if action.kind == "reopen":
        return state.reopen(user_id, action.issue_id, message_id)
    return state.confirm_resolved(
        user_id,
        session_id,
        message_id,
        issue_id=action.issue_id,
        explicit_confirmation=True,
    )
