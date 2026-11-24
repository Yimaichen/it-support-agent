"""Typed synthetic corpus contracts and reference validation (no retrieval or tools)."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from src.config import PROJECT_ROOT


class Category(StrEnum):
    ACCOUNT = "account"
    APPLICATION = "application"
    CONNECTIVITY = "connectivity"
    ACCESS = "access"
    COMPLEX = "complex"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class EmployeeDevice(Contract):
    """A recorded device, not proof that it is the device used in this conversation."""

    id: NonEmptyText
    os: NonEmptyText
    updated_at: AwareDatetime


class EmployeePermission(Contract):
    """An exact recorded entitlement, not an approval or an authorization engine."""

    service_id: NonEmptyText
    resource_type: Literal["service", "project", "database", "network"]
    resource_id: NonEmptyText
    role: NonEmptyText
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def exact_resource(self) -> EmployeePermission:
        if "*" in self.resource_id:
            raise ValueError("employee permissions require an exact resource, not a wildcard")
        return self


class Employee(Contract):
    id: str
    name: str
    department: str
    location: str
    devices: list[EmployeeDevice]
    permissions: list[EmployeePermission]
    updated_at: AwareDatetime
    synthetic: Literal[True] = True

    @model_validator(mode="after")
    def snapshot_consistency(self) -> Employee:
        device_ids = [device.id for device in self.devices]
        if len(device_ids) != len(set(device_ids)):
            raise ValueError("duplicate device in employee snapshot")
        grants = [(p.service_id, p.resource_type, p.resource_id, p.role) for p in self.permissions]
        if len(grants) != len(set(grants)):
            raise ValueError("duplicate permission in employee snapshot")
        if any(item.updated_at > self.updated_at for item in [*self.devices, *self.permissions]):
            raise ValueError("device/permission timestamp exceeds employee snapshot")
        return self


class Service(Contract):
    id: str
    name: str
    aliases: list[str]
    status: Literal["healthy", "degraded", "outage", "maintenance", "not_monitored"]
    region_status: dict[str, Literal["healthy", "degraded", "outage", "maintenance"]]
    checked_at: AwareDatetime
    details: str
    supported_versions: list[str] = Field(default_factory=list)


class Change(Contract):
    id: str
    service_id: str
    started_at: AwareDatetime
    ended_at: AwareDatetime | None
    summary: str
    regions: list[str]

    @model_validator(mode="after")
    def ordered(self) -> Change:
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("change end precedes start")
        return self


class Policy(Contract):
    id: str
    title: str
    title_zh: str
    service_ids: list[str]
    resource: str
    action: str
    decision: Literal["guide", "approval_required", "handoff_required"]
    approval_team: str | None
    required_information: list[str]
    prohibited_actions: list[str]
    required_status_checks: list[str] = Field(default_factory=list)
    applicability: str = Field(default="", max_length=2400)
    updated_at: AwareDatetime


class Document(Contract):
    id: str
    title: str
    title_zh: str
    category: Category
    service_ids: list[str] = Field(min_length=1)
    tags: list[str] = Field(min_length=1)
    updated_at: AwareDatetime
    valid_until: AwareDatetime | None = None
    environment: str
    policy_refs: list[str]
    body: str = Field(min_length=20)


class Resolution(Document):
    source_type: Literal["synthetic_seed"]
    outcome: Literal["resolved"]
    visibility: Literal["shared"]
    synthetic: Literal[True]
    resolved_at: AwareDatetime
    symptoms: str
    result: str
    evidence_refs: list[str] = Field(min_length=1)


class Corpus(Contract):
    reference_time: AwareDatetime
    employees: list[Employee]
    services: list[Service]
    changes: list[Change]
    policies: list[Policy]
    knowledge: list[Document]
    resolutions: list[Resolution]
    aliases: dict[str, list[str]]

    @model_validator(mode="after")
    def references(self) -> Corpus:
        groups = (
            self.employees,
            self.services,
            self.changes,
            self.policies,
            self.knowledge,
            self.resolutions,
        )
        ids = [item.id for group in groups for item in group]
        if len(ids) != len(set(ids)):
            raise ValueError("corpus IDs must be globally unique")
        services = {s.id for s in self.services}
        policies = {p.id for p in self.policies}
        known = set(ids)
        for employee in self.employees:
            if not {p.service_id for p in employee.permissions} <= services:
                raise ValueError(f"unknown service in employee {employee.id}")
        for change in self.changes:
            if change.service_id not in services:
                raise ValueError(f"unknown service in change {change.id}")
        for policy in self.policies:
            if not set(policy.required_status_checks) <= set(policy.service_ids):
                raise ValueError("policy status checks must belong to its services")
        for item in [*self.policies, *self.knowledge, *self.resolutions]:
            if not set(item.service_ids) <= services:
                raise ValueError(f"unknown service in {item.id}")
        for doc in [*self.knowledge, *self.resolutions]:
            if not set(doc.policy_refs) <= policies:
                raise ValueError(f"unknown policy in {doc.id}")
        for case in self.resolutions:
            if not set(case.evidence_refs) <= known:
                raise ValueError(f"unknown evidence in {case.id}")
        for group in groups:
            for item in group:
                for key in ("updated_at", "checked_at", "started_at", "resolved_at"):
                    value = getattr(item, key, None)
                    if value is not None and value > self.reference_time:
                        raise ValueError(f"future {key} in {item.id}")
        return self


def read_markdown(path: Path, model: type[Document] = Document) -> Document:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError(f"Missing YAML front matter: {path.name}")
    metadata = yaml.safe_load(parts[1])
    if not isinstance(metadata, dict):
        raise ValueError(f"Invalid front matter: {path.name}")
    return model.model_validate({**metadata, "body": parts[2].strip()})


def load_corpus(root: Path = PROJECT_ROOT / "data") -> Corpus:
    def read(name: str):
        return json.loads((root / name).read_text(encoding="utf-8"))

    manifest = read("manifest.json")
    return Corpus.model_validate(
        {
            "reference_time": manifest["reference_time"],
            "employees": read("employees.json"),
            "services": read("services.json"),
            "changes": read("changes.json"),
            "policies": read("policies.json"),
            "knowledge": [read_markdown(p) for p in sorted((root / "knowledge").glob("*.md"))],
            "resolutions": [
                read_markdown(p, Resolution) for p in sorted((root / "resolutions").glob("*.md"))
            ],
            "aliases": read("aliases.json"),
        }
    )


def is_expired(doc: Document, at: datetime) -> bool:
    if at.tzinfo is None:
        raise ValueError("expiration checks require a timezone-aware clock")
    return doc.valid_until is not None and doc.valid_until < at


if __name__ == "__main__":
    corpus = load_corpus()
    print(
        f"Validated synthetic corpus at {corpus.reference_time.isoformat()}: "
        f"{len(corpus.knowledge)} guides, {len(corpus.resolutions)} resolved seeds, "
        f"{len(corpus.employees)} employees, {len(corpus.services)} services, "
        f"{len(corpus.policies)} policies, {len(corpus.changes)} changes."
    )
