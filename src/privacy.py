"""Best-effort local redaction, NOT a guarantee of anonymity or a publication approval."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any

SECRET = "[REDACTED_SECRET]"
PERSONAL = "[REDACTED_PERSONAL]"
_SECRET_PATTERNS = [
    re.compile(
        r"(?i)(\b(?:my\s+)?(?:password|passphrase|passcode|secret|api[_ -]?key|access[_ -]?token|recovery[_ -]?code)\s+(?:happens\s+to\s+be|is|was|equals)\s+|(?:我的?|本人的?)?(?:口令|密码|密钥|访问令牌|恢复码|验证码)(?:\s*(?:是|为|等于|叫做)\s*))([\"'][^\"'\r\n]+[\"']|[^\s，。；;,\"'{}\[\]]+)"
    ),
    re.compile(r"\b(?:sk-[\w-]{8,}|AKIA[A-Z0-9]{16})\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"""(?i)((?<!\w)["']?(?:password|passwd|pwd|api[_ -]?key|token|otp|recovery[_ -]?code|密码|验证码|恢复码)["']?\s*(?:[:=：]|\bis\b|是|为)\s*)("(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|[^\s"',;}\]]+)"""
    ),
    re.compile(r"(?i)(?:\botp\b|验证码)\s+\d{4,8}\b"),
]
_PERSONAL_PATTERNS = [
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    re.compile(r"https?://[^\s<>\"']+", re.I),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:internal|local|corp)\b", re.I),
    re.compile(r"\b(?:EMP-\d+|DEMO-DEVICE-\d+)\b", re.I),
]
_PROTOCOL_PRIVATE = {"reasoning_content", "chain_of_thought", "thoughts"}


@lru_cache(maxsize=1)
def _entity_tokenizer():
    # Packaged local dictionary, no runtime network/model download. No user text cached.
    import jieba
    import jieba.posseg

    jieba.setLogLevel(logging.ERROR)
    tokenizer = jieba.Tokenizer()
    tokenizer.initialize()
    return jieba.posseg.POSTokenizer(tokenizer)


def local_person_names(text: str) -> set[str]:
    """Dictionary/POS detector; limited Chinese name coverage, not a general NER guarantee."""
    candidates = {
        word
        for word, tag in _entity_tokenizer().cut(text)
        if tag == "nr" and re.fullmatch(r"[\u4e00-\u9fff]{2,4}", word)
    }
    names = set()
    for word in candidates:
        for match in re.finditer(re.escape(word), text):
            before = text[max(0, match.start() - 12) : match.start()]
            after = text[match.end() : match.end() + 18]
            if re.search(
                r"员工|用户|联系人|姓名|名字|我叫|我是|同事|客户|申请人", before
            ) or re.match(r"(?:说|表示|报告|在排查|遇到|的(?:账号|账户|邮箱|VPN))", after):
                names.add(word)
    return names


def redact_text(text: str, *, personal: bool = False, identities=()) -> str:
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:

            def replacement(match):
                value = match.group(2)
                if value in {SECRET, SECRET[:-1], f'"{SECRET}"', f"'{SECRET}'"}:
                    return match.group(0)
                masked = value[0] + SECRET + value[0] if value.startswith(('"', "'")) else SECRET
                return match.group(1) + masked

            text = pattern.sub(replacement, text)
        else:
            text = pattern.sub(SECRET, text)
    if personal:
        names = local_person_names(text)
        for name in sorted(names, key=len, reverse=True):
            text = text.replace(name, PERSONAL)
        text = re.sub(
            r"(?i)(?:my name is|姓名(?:是|为|[:：]))\s*[A-Z][a-z]+(?:[ -][A-Z][a-z]+){0,2}",
            PERSONAL,
            text,
        )
        for pattern in _PERSONAL_PATTERNS:
            text = pattern.sub(PERSONAL, text)
        for identity in sorted(set(identities), key=len, reverse=True):
            if identity:
                text = re.sub(re.escape(identity), PERSONAL, text, flags=re.I)
    return text


def sanitize(value: Any, *, personal: bool = False, identities=()) -> Any:
    if isinstance(value, str):
        return redact_text(value, personal=personal, identities=identities)
    if isinstance(value, list):
        return [sanitize(v, personal=personal, identities=identities) for v in value]
    if isinstance(value, dict):
        return {
            k: SECRET
            if re.fullmatch(
                r"(?i)(password|passwd|pwd|passphrase|secret|api[_-]?key|access_token|token|otp|recovery_code|private_key|密码|口令|密钥|验证码)",
                k,
            )
            else sanitize(v, personal=personal, identities=identities)
            for k, v in value.items()
            if k not in _PROTOCOL_PRIVATE
        }
    return value
