"""Offline ledger fixtures; no model controller or vendor transport."""

from src.records import Records
from src.state import StateService
from src.tools import ToolContext, ToolRunner


def new_context(env, user_id=None):
    corpus, store, ctx = env
    user_id = user_id or ctx.user_id
    session = store.create_session(user_id)["session_id"]
    issue = StateService(store).create_issue(user_id, session, "VPN repeat issue", "connectivity")
    return ToolContext(user_id, session, issue["issue_id"])


def archive_success(env, marker="synthetic-case"):
    corpus, store, ctx = env
    state = StateService(store)
    receipt = ToolRunner(corpus, store).execute(
        ctx, "search_knowledge_base", {"query": "VPN client upgrade"}
    )
    state.add_step(ctx.user_id, ctx.issue_id, "Upgrade through approved portal to 6.2.")
    step_id = state.memory(ctx.user_id, ctx.issue_id).attempted_steps[-1].id
    message = store.append_message(
        ctx.user_id,
        ctx.session_id,
        {"role": "user", "content": "Upgraded to 6.2; connection and internal resource work."},
        issue_id=ctx.issue_id,
    )["message_id"]
    state.report_step_result(ctx.user_id, ctx.issue_id, step_id, message, "succeeded")
    state.confirm_resolved(
        ctx.user_id, ctx.session_id, message, issue_id=ctx.issue_id, explicit_confirmation=True
    )
    return Records(store, corpus).archive(
        ctx.user_id,
        ctx.issue_id,
        {"symptoms": marker, "step_ids": [step_id], "evidence_refs": receipt["evidence_refs"]},
    )
