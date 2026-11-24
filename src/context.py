"""Employee context contracts and legacy read validation.

New context is extracted by the model and bound to a genuine employee quote.
Legacy regex extraction remains only for reading/validating previous records.

Unsupported wording stays in the original report. These fields are employee claims,
not verified inventory, permission grants or incident/root-cause observations.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field

from src.data import Contract

REGIONS = {
    "london": "London",
    "伦敦": "London",
    "chicago": "Chicago",
    "芝加哥": "Chicago",
    "shanghai": "Shanghai",
    "上海": "Shanghai",
}
REGION_PATTERN = "|".join(REGIONS)

ContextField = Literal[
    "region",
    "operating_system",
    "client_version",
    "error",
    "run_id",
    "start_time",
    "impact",
    "attempted",
    "device_identity",
    "further_details",
    "browser",
    "urgency",
]


class ReportedContext(Contract):
    field: ContextField
    value: str | None = Field(default=None, max_length=180)
    note: str | None = Field(default=None, max_length=500)
    status: Literal["reported", "unavailable", "unknown"] = "reported"
    message_id: str
    quote: str | None = Field(default=None, max_length=500)
    recorded_at: AwareDatetime
    extraction: Literal["legacy_rule", "model"] = "legacy_rule"


def extract_context(text: str, message_id: str, at: datetime) -> dict[str, ReportedContext]:
    result = {}
    # Reject historical, quoted and uncertain clauses instead of treating them as current.
    for clause in re.split(r"[。！？!?;；\n]", text):
        if re.search(
            r"last\s+(?:week|month|year)|previous|used to|history|registered|directory|might|maybe|上次|之前|过去|登记|历史|可能|听说|[\"“”]",
            clause,
            re.I,
        ):
            continue
        patterns = {
            "device_identity": r"(?:current device|device (?:name|id)|设备(?:名称|编号)|当前设备)[：:\s]+([A-Za-z0-9][A-Za-z0-9_-]{2,79})",
            "region": rf"(?:\b(?:in|from|at)\s+|我(?:现在)?在|目前在|地点[：:]\s*|^\s*)({REGION_PATTERN})(?![a-z])",
            "operating_system": r"\b((?:Windows\s*\d+(?:\.\d+)?|macOS\s*\d+(?:\.\d+)*))\b",
            "client_version": r"(?:\bVPN(?:\s+client)?|客户端|client\s+version|版本)\s*(?:version\s*|版本\s*|is\s*|是\s*|[：:]\s*)?(\d+\.\d+(?:\.\d+)*)",
            "error": r"\b([A-Z][A-Z_]{3,}[A-Z])\b",
            "run_id": r"(?:run\s*id|运行\s*ID)[：:\s]+([A-Za-z0-9_-]{3,80})",
            "start_time": r"(?:started|began|开始(?:时间)?(?:是|为|于)?)[：:\s]*(.{1,160})",
            "impact": r"(?:impact(?: is)?|affected(?: users| systems)?|影响(?:范围)?(?:是|为)?)[：:\s]+(.{1,160})",
            "attempted": r"(?:I (?:already )?(?:tried|have tried)|已经(?:尝试|试过)|已(?:尝试|试过))[：:\s]*(.{1,160})",
        }
        for field, pattern in patterns.items():
            matches = list(re.finditer(pattern, clause, re.I if field != "error" else 0))
            values = {m.group(1).casefold() for m in matches}
            if len(values) > 1:
                result[field] = ReportedContext(
                    field=field,
                    status="unknown",
                    message_id=message_id,
                    quote=clause[:500],
                    recorded_at=at,
                )
                continue
            if len(values) != 1:
                continue
            match = matches[0]
            prefix = clause[max(0, match.start() - 20) : match.start()]
            if re.search(r"not\s+(?:using\s+)?$|不是\s*$|不用\s*$", prefix, re.I):
                result[field] = ReportedContext(
                    field=field,
                    status="unknown",
                    message_id=message_id,
                    quote=match.group(0),
                    recorded_at=at,
                )
                continue
            value = match.group(1)
            if field == "region":
                value = REGIONS[value.casefold()]
            result[field] = ReportedContext(
                field=field,
                value=value,
                message_id=message_id,
                quote=match.group(0),
                recorded_at=at,
            )
        unknown = re.search(
            r"(?:cannot|can't|do not|don't|unable to).{0,35}(?:run\s*id)|(?:没有|无法|拿不到|不知道).{0,15}运行\s*ID",
            clause,
            re.I,
        )
        if unknown:
            result["run_id"] = ReportedContext(
                field="run_id",
                status="unavailable",
                message_id=message_id,
                quote=unknown.group(0),
                recorded_at=at,
            )
    negated_fields = {
        "run_id": r"(?:run\s*id)|运行\s*ID",
        "start_time": r"(?:start time|when it started)|开始时间",
        "impact": r"(?:impact|affected users)|影响范围",
        "device_identity": r"(?:device (?:name|id))|设备(?:名称|编号)",
    }
    for field, label in negated_fields.items():
        unknown = re.search(
            rf"(?:(?:cannot|can't|do not|don't|unable to).{{0,35}}(?:{label})|(?:没有|无法|拿不到|不知道).{{0,15}}(?:{label}))",
            text,
            re.I,
        )
        if unknown:
            result[field] = ReportedContext(
                field=field,
                status="unavailable",
                message_id=message_id,
                quote=unknown.group(0),
                recorded_at=at,
            )
    decline = re.search(
        r"\bI (?:do not|don't) have (?:any )?(?:additional information|more (?:details|information))\b|\bI (?:have|can provide) no (?:further|more) (?:details|information)\b|(?:我(?:目前|现在)?(?:没有|无法提供)|我拿不到).{0,8}(?:更多|进一步).{0,6}(?:细节|信息)",
        text,
        re.I,
    )
    if decline and not re.search(r"[\"“”]|previous|last time|历史|上次", text, re.I):
        result["further_details"] = ReportedContext(
            field="further_details",
            status="unavailable",
            message_id=message_id,
            quote=decline.group(0),
            recorded_at=at,
        )
    return result


def question_fields(question: str) -> set[str]:
    if re.search(
        r"device (?:name|ID)|which.{0,30}(?:device|computer)|哪(?:台|个).{0,8}(?:设备|电脑)|设备(?:名称|编号)",
        question,
        re.I,
    ):
        return {"device_identity"}
    patterns = {
        "region": r"\b(?:where|location|region|city)\b|地区|地点|所在地|哪里|哪座城市",
        "operating_system": r"\b(?:operating system|OS|Windows|macOS)\b|操作系统",
        "client_version": r"\b(?:client version|VPN version)\b|客户端版本|VPN版本",
        "run_id": r"\brun\s*id\b|运行\s*ID",
        "start_time": r"\b(?:when|start time)\b|何时|什么时候|开始时间",
        "impact": r"\b(?:impact|affected users|how many users)\b|影响范围|多少人",
        "attempted": r"\b(?:already tried|tried so far)\b|已尝试|试过什么",
    }
    return {field for field, pattern in patterns.items() if re.search(pattern, question, re.I)}


class ContextUpdate(Contract):
    """Model interpretation of current employee context, never a verified observation."""

    field: ContextField
    value: str | None = Field(
        default=None,
        max_length=180,
        description="Canonical value only: region='London', client_version='6.2'. Put corrections/explanations in note, never in this identifier.",
    )
    note: str | None = Field(default=None, max_length=500)
    status: Literal["reported", "unavailable", "unknown"] = "reported"
    message_id: str | None = None
    quote: str | None = Field(default=None, max_length=500)
