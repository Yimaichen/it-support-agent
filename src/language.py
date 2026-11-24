"""Reply language is a session preference, independent of interface labels."""

import re


def resolve_language(text: str, preference: str, previous: str = "en") -> str:
    if preference in {"zh", "en"}:
        return preference
    cancelling = requested_preference(text) == "auto"
    if not cancelling and re.search(
        r"(?:用|使用|请以)(?:中文|汉语)|(?:reply|respond|answer) in Chinese", text, re.I
    ):
        return "zh"
    if not cancelling and re.search(
        r"(?:用|使用|请以)英(?:文|语)|(?:reply|respond|answer) in English", text, re.I
    ):
        return "en"
    if re.fullmatch(
        r"\s*(?:好了|好的|好|是|否|谢谢|ok|yes|no|done|thanks|[A-Z_\d.:-]+)[.!！。\s]*", text, re.I
    ):
        return previous
    if len(re.findall(r"[\u4e00-\u9fff]", text)) >= 2:
        return "zh"
    if len(re.findall(r"\b[a-zA-Z]{2,}\b", text)) >= 3:
        return "en"
    return previous


def obvious_language_mismatch(text: str, language: str) -> bool:
    prose = re.sub(r"`[^`]*`|https?://\S+|\b[A-Z][A-Z_\d.-]+\b", "", text)
    if len(re.findall(r"[\u0600-\u06ff\u0400-\u04ff]", prose)) >= 15:
        return True
    han = len(re.findall(r"[\u4e00-\u9fff]", prose))
    words = len(re.findall(r"\b[a-zA-Z]{2,}\b", prose))
    return (language == "en" and han >= 10 and han > words * 2) or (
        language == "zh" and words >= 12 and han == 0
    )


def requested_preference(text: str) -> str | None:
    """Recognize explicit preferences only; content language is still auto-detected."""
    if re.search(
        r"取消.*(?:英文|中文|语言)|(?:follow|match).*(?:my|input|natural).*language|(?:cancel|stop).*(?:English|Chinese|language)",
        text,
        re.I,
    ):
        return "auto"
    if re.search(
        r"(?:请|以后|之后|接下来).*(?:用|使用)英(?:文|语)|(?:please\s+)?(?:(?:reply|respond|answer) in|use|switch to) English",
        text,
        re.I,
    ):
        return "en"
    if re.search(
        r"(?:请|以后|之后|接下来).*(?:用|使用)(?:中文|汉语)|(?:please\s+)?(?:(?:reply|respond|answer) in|use|switch to) Chinese",
        text,
        re.I,
    ):
        return "zh"
    return None
