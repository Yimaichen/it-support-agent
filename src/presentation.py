"""Display compatibility for historical paused conversations; no model control."""


def render_pause_notice(error: dict, language: str) -> str:
    """Application explanation only; never echo exceptions or rejected model text."""
    en = language == "en"
    reason = error.get("reason")
    kind = error.get("type")
    if reason in {"turn_budget", "repair_budget"} or kind in {"budget_exhausted", "timeout"}:
        return (
            "This turn reached its execution limit. Your input and collected evidence are saved, but the issue is unresolved. Another message can start a new turn; continuing this turn keeps its existing limit."
            if en
            else "本轮已达到执行时限或次数上限。你的描述和已查资料已保存，但问题尚未解决。可以补充信息开始新一轮；继续原轮次不会重置额度。"
        )
    if reason in {"investigation_incomplete", "guidance_health_required", "handoff_incomplete"}:
        return (
            "The available evidence is not sufficient to safely complete this response. Your input and investigation progress are saved; no new guidance or handoff was accepted. You may add details or explicitly continue."
            if en
            else "现有证据还不足以安全完成这次处理。你的描述和调查进度已保存，本次未采纳新的操作建议或交接结果。你可以补充信息，或手动继续调查。"
        )
    return (
        "This turn paused before a reliable response was accepted. Your input and investigation progress are saved; the issue is unresolved. No automatic retry will run."
        if en
        else "这轮处理未能形成可采纳的答复，调查已暂停。你的描述和已查资料已保存，问题尚未解决；系统不会自动重试。"
    )
