"""Compatibility data for stored pre-Pi issues; never a model execution path.

Pi does not register or authorize steps. Existing ledger APIs still read these
fields so historical records and their provenance remain accessible.
"""

from typing import Literal

from pydantic import Field

from src.data import Contract


class StepAuthorization(Contract):
    service_id: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    action: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    document_refs: list[str] = Field(
        min_length=1,
        max_length=4,
        description="KB source IDs actually returned for this issue; not history IDs or EV IDs.",
    )


class ActionSpec(Contract):
    id: str = Field(min_length=1)
    version: int = Field(ge=1, strict=True)
    service_id: str
    resource: str
    action: str
    policy_id: str
    document_refs: list[str] = Field(min_length=1, max_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment: str
    text: dict[Literal["zh", "en"], str]
    source_max_age_seconds: int = Field(default=900, ge=1, le=86400, strict=True)
    query_max_age_seconds: int = Field(default=300, ge=1, le=3600, strict=True)
    supported_versions: list[str] | None = None
    verification: str = Field(min_length=1, max_length=300)
    failure_exit: Literal["report_result_then_handoff"] = "report_result_then_handoff"


def action_catalog() -> list[dict]:
    import json
    from pathlib import Path

    raw = json.loads(Path(__file__).with_name("legacy_actions.json").read_text())
    actions = [ActionSpec.model_validate(a).model_dump(exclude_none=True) for a in raw]
    if len({a["id"] for a in actions}) != len(actions) or any(
        set(a["text"]) != {"zh", "en"} or not all(a["text"].values()) for a in actions
    ):
        raise ValueError("action catalog requires unique IDs and bilingual instructions")
    return actions
