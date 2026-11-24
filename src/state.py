"""Per-issue memory and application-validated transitions; not an LLM or NLP parser.

Only the trusted executor records observations/confirmation events. The future model-facing
proposal schema deliberately cannot supply evidence, confirmation, or overwrite factual memory.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from functools import wraps
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field, model_validator

from src.context import ContextUpdate, ReportedContext, extract_context
from src.data import Contract
from src.legacy_state import StepAuthorization, action_catalog
from src.storage import AccessDenied, Store


class Status(StrEnum):
    INVESTIGATING = "investigating"
    AWAITING_USER = "awaiting_user"
    AWAITING_VERIFICATION = "awaiting_verification"
    AWAITING_SERVICE_RECOVERY = "awaiting_service_recovery"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


TERMINAL = {Status.RESOLVED, Status.ESCALATED}
UNFINISHED = set(Status) - TERMINAL
# Terminal problems require explicit user-triggered reopening, not a model proposal.
ALLOWED_TRANSITIONS = {s: UNFINISHED | TERMINAL for s in UNFINISHED} | {
    Status.RESOLVED: {Status.RESOLVED},
    Status.ESCALATED: {Status.ESCALATED},
}
TOOL_KINDS = {
    "check_service_status": "current_observation",
    "get_current_user": "current_observation",
    "search_knowledge_base": "guidance",
    "lookup_policy": "policy",
    "search_resolution_history": "historical",
    "read_resolution": "historical",
    "read_knowledge_document": "guidance",
    "get_my_support_history": "historical",
}
CURRENT_TOPICS = {
    "check_service_status": {"service_status"},
    "get_current_user": {"device", "permissions", "employee_profile"},
}


class ClarificationRequired(ValueError):
    """Do not guess which issue an ambiguous confirmation refers to."""


class Evidence(Contract):
    id: str
    kind: Literal["current_observation", "historical", "guidance", "policy"]
    tool: str
    source_ref: str
    source_at: AwareDatetime
    queried_at: AwareDatetime
    invocation_id: str = Field(min_length=1)
    topic: str | None = None
    subject: str | None = None
    value: str | None = None
    recorded_at: AwareDatetime
    summary: str
    success: bool

    @model_validator(mode="after")
    def receipt_shape(self) -> Evidence:
        if (
            TOOL_KINDS.get(self.tool) != self.kind
            or self.source_at > self.recorded_at
            or self.queried_at > self.recorded_at
        ):
            raise ValueError("evidence tool/kind/time mismatch")
        return self


class CurrentFact(Contract):
    topic: Literal["service_status", "device", "permissions", "employee_profile"]
    subject: str
    value: str | None
    evidence_ref: str
    source_at: AwareDatetime
    queried_at: AwareDatetime


class UserEvent(Contract):
    kind: Literal["confirmation", "reopen", "step_result"]
    message_id: str
    sequence: int = Field(gt=0)
    result: str | None = None


class HistoricalClue(Contract):
    summary: str
    evidence_ref: str


class UserReport(Contract):
    message_id: str
    text: str
    recorded_at: AwareDatetime


class Hypothesis(Contract):
    statement: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class Step(Contract):
    id: str
    instruction: str
    authorization: StepAuthorization | None = None
    action_id: str | None = None
    action_version: int | None = Field(default=None, ge=1, strict=True)
    result: Literal["suggested", "succeeded", "failed", "unknown"] = "suggested"
    confirmed_by: Literal["user"] | None = None
    message_id: str | None = None

    @model_validator(mode="after")
    def reported_result(self) -> Step:
        if (self.action_id is None) != (self.action_version is None):
            raise ValueError("action ID and version must be paired")
        if self.result in {"succeeded", "failed"} and (
            self.confirmed_by != "user" or not self.message_id
        ):
            raise ValueError("step outcomes require an employee report")
        return self


class AttemptUpdate(Contract):
    """Model interpretation of an employee's past attempt, never an app confirmation."""

    description: str = Field(min_length=1, max_length=300)
    result: Literal["succeeded", "failed", "unknown"]
    action_id: str | None = None
    message_id: str | None = None
    quote: str | None = Field(default=None, max_length=500)


class ReportedAttempt(AttemptUpdate):
    id: str
    message_id: str
    recorded_at: AwareDatetime


class Confirmation(Contract):
    message_id: str
    confirmed_at: AwareDatetime
    basis: Literal["employee_reported"] = "employee_reported"


class ToolAttempt(Contract):
    invocation_id: str
    tool: str
    outcome: Literal["hit", "empty", "failed"]
    source_refs: list[str] = Field(default_factory=list)
    service_id: str | None = None
    requested_service: str | None = None
    resource: str | None = None
    action: str | None = None
    recorded_at: AwareDatetime


class IssueMemory(Contract):
    schema_version: Literal[1] = 1
    symptoms: list[str] = Field(default_factory=list)
    tool_attempts: list[ToolAttempt] = Field(default_factory=list)
    reported_context: dict[str, ReportedContext] = Field(default_factory=dict)
    user_reports: list[UserReport] = Field(default_factory=list)
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    current_facts: dict[str, CurrentFact] = Field(default_factory=dict)
    historical_clues: list[HistoricalClue] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    attempted_steps: list[Step] = Field(default_factory=list)
    reported_attempts: list[ReportedAttempt] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    confirmation: Confirmation | None = None
    confirmation_after_sequence: int = Field(default=0, ge=0)
    latest_event_sequence: int = Field(default=0, ge=0)
    processed_events: dict[str, UserEvent] = Field(default_factory=dict)

    @model_validator(mode="after")
    def provenance(self) -> IssueMemory:
        if any(key != evidence.id for key, evidence in self.evidence.items()):
            raise ValueError("evidence registry key mismatch")
        for fact in self.current_facts.values():
            evidence = self.evidence.get(fact.evidence_ref)
            if (
                evidence is None
                or evidence.kind != "current_observation"
                or fact.topic not in CURRENT_TOPICS.get(evidence.tool, set())
                or fact.source_at != evidence.source_at
                or fact.queried_at != evidence.queried_at
                or fact.topic != evidence.topic
                or fact.subject != evidence.subject
                or fact.value != evidence.value
                or (fact.value is not None and not evidence.success)
            ):
                raise ValueError("current facts require a matching current-query receipt")
        reports = {r.message_id: r for r in self.user_reports}
        for key, context in self.reported_context.items():
            report = reports.get(context.message_id)
            if (
                key != context.field
                or report is None
                or (context.quote is not None and context.quote not in report.text)
                or context.recorded_at != report.recorded_at
            ):
                raise ValueError("reported context requires an original employee quote")
            if context.extraction == "legacy_rule":
                extracted = extract_context(report.text, report.message_id, report.recorded_at)
                if extracted.get(key) != context:
                    raise ValueError("reported context cannot override extracted employee facts")
        for attempt in self.reported_attempts:
            report = reports.get(attempt.message_id)
            if (
                report is None
                or (attempt.quote is not None and attempt.quote not in report.text)
                or attempt.recorded_at != report.recorded_at
            ):
                raise ValueError("reported attempts require an original employee quote")
        if len({a.id for a in self.reported_attempts}) != len(self.reported_attempts):
            raise ValueError("duplicate reported attempt")
        for clue in self.historical_clues:
            evidence = self.evidence.get(clue.evidence_ref)
            if evidence is None or evidence.kind != "historical" or not evidence.success:
                raise ValueError("historical clues require a successful historical receipt")
        for hypothesis in self.hypotheses:
            if not set(hypothesis.evidence_refs) <= self.evidence.keys():
                raise ValueError("hypothesis references unknown evidence")
        return self


class Proposal(Contract):
    """Future model output: no direct evidence, confirmation or current-fact writes."""

    status: Status | None = None
    hypotheses: list[Hypothesis] | None = None
    open_questions: list[str] | None = None


def resolve_evidence_refs(memory: IssueMemory, refs: list[str]) -> tuple[list[str], list[str]]:
    """Map human-readable source IDs (KB-*, POL-*, CHG-*, STATUS:*, service IDs) or EV-* IDs to real EV-* receipts."""
    import re

    resolved: list[str] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    user_msg_ids = {r.message_id for r in memory.user_reports}
    for raw in refs:
        ref = raw.strip()
        if not ref or ref in user_msg_ids:
            continue
        # Extract canonical prefix token if model appended a brief parenthetical note.
        extracted = re.search(
            r"\b(?:EV-[0-9a-fA-F-]+|KB-[A-Z]+-\d+|POL-\d+|HIST-\d+|CHG-\d+|EMP-\d+|STATUS:[a-z_]+(?::[A-Za-z_]+)?)\b",
            ref,
        )
        token = extracted.group(0) if extracted else ref
        matches: list[str] = []
        if token in memory.evidence:
            matches = [token]
        else:
            fold = token.casefold()
            matches = [
                eid for eid, ev in memory.evidence.items() if ev.source_ref.casefold() == fold
            ]
            if not matches and fold.startswith("status:"):
                matches = [
                    eid
                    for eid, ev in memory.evidence.items()
                    if ev.source_ref.casefold().startswith(fold + ":")
                ]
            if not matches and ":" not in token:
                matches = [
                    eid
                    for eid, ev in memory.evidence.items()
                    if ev.tool == "check_service_status"
                    and (ev.subject or "").split(":", 1)[0].casefold() == fold
                ]
            if not matches and token.upper().startswith(("CHG-", "INC-")):
                upper = token.upper()
                matches = [
                    eid
                    for eid, ev in memory.evidence.items()
                    if ev.tool == "check_service_status" and upper in ev.summary.upper()
                ]
            if not matches and token.upper().startswith("EMP-"):
                matches = [
                    eid
                    for eid, ev in memory.evidence.items()
                    if ev.tool == "get_current_user" and ev.success
                ]
        if matches:
            for eid in matches:
                if eid not in seen:
                    seen.add(eid)
                    resolved.append(eid)
        else:
            unresolved.append(ref)
    return resolved, unresolved


def _state_write(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.store.transaction():
            return method(self, *args, **kwargs)

    return wrapped


class StateService:
    def __init__(self, store: Store):
        self.store = store

    def _now(self) -> datetime:
        now = self.store.clock()
        if now.tzinfo is None:
            raise ValueError("state clock must be timezone-aware")
        return now

    def memory(self, user_id: str, issue_id: str) -> IssueMemory:
        return IssueMemory.model_validate(self.store.get_issue(user_id, issue_id)["state"])

    def _save(
        self, user_id: str, issue: dict, memory: IssueMemory, status: Status | str | None = None
    ) -> dict:
        # Revalidate mutations; Pydantic assignment validation alone cannot enforce cross-field rules.
        snapshot = IssueMemory.model_validate(memory.model_dump(mode="json"))
        return self.store.update_issue(
            user_id,
            issue["issue_id"],
            status=status or issue["status"],
            state=snapshot.model_dump(mode="json"),
            expected_revision=issue["revision"],
        )

    @_state_write
    def create_issue(
        self,
        user_id: str,
        session_id: str,
        title: str,
        category: str,
        *,
        symptoms: list[str] | None = None,
    ) -> dict:
        issue = self.store.create_issue(user_id, session_id, title, category)
        return self._save(user_id, issue, IssueMemory(symptoms=symptoms or []))

    def next_issue(self, user_id: str, session_id: str) -> dict | None:
        return next(
            (
                i
                for i in self.store.list_issues(user_id, session_id)
                if Status(i["status"]) not in TERMINAL
            ),
            None,
        )

    def select_target(self, user_id: str, session_id: str, issue_id: str | None = None) -> dict:
        self.store.get_session(user_id, session_id)
        if issue_id is not None:
            issue = self.store.get_issue(user_id, issue_id)
            if issue["session_id"] != session_id:
                raise AccessDenied("resource unavailable")
            return issue
        unfinished = [
            i
            for i in self.store.list_issues(user_id, session_id)
            if Status(i["status"]) not in TERMINAL
        ]
        if len(unfinished) != 1:
            raise ClarificationRequired("identify one issue before applying a confirmation")
        return unfinished[0]

    def _employee_message(self, user_id: str, issue: dict, message_id: str) -> dict:
        message = self.store.get_message(user_id, message_id)
        if (
            message["session_id"] != issue["session_id"]
            or message["message"]["role"] != "user"
            or not (message["message"].get("content") or "").strip()
            or message["issue_id"] not in (None, issue["issue_id"])
        ):
            raise ValueError("confirmation/report must be a user message for this issue")
        return message

    @_state_write
    def record_user_report(self, user_id: str, issue_id: str, message_id: str) -> dict:
        issue = self.store.get_issue(user_id, issue_id)
        message = self._employee_message(user_id, issue, message_id)
        memory = IssueMemory.model_validate(issue["state"])
        if not any(r.message_id == message_id for r in memory.user_reports):
            at = self._now()
            memory.user_reports.append(
                UserReport(
                    message_id=message_id, text=message["message"]["content"], recorded_at=at
                )
            )

        return self._save(user_id, issue, memory)

    @_state_write
    def record_context(self, user_id: str, issue_id: str, updates: list[ContextUpdate]) -> dict:
        """Validate provenance/ordering, not the semantic truth of model interpretation."""
        issue = self.store.get_issue(user_id, issue_id)
        memory = IssueMemory.model_validate(issue["state"])
        reports = {r.message_id: (index, r) for index, r in enumerate(memory.user_reports)}
        if len({u.field for u in updates}) != len(updates):
            raise ValueError("duplicate context field")
        for update in updates:
            found = reports.get(update.message_id or "")
            if found is None or not update.quote or update.quote not in found[1].text:
                raise ValueError("context source must be an employee report for this issue")
            if update.status == "reported" and not (update.value or "").strip():
                raise ValueError("reported context needs a value")
            if update.status != "reported" and update.value is not None:
                raise ValueError("unknown/unavailable context cannot carry a value")
            old = memory.reported_context.get(update.field)
            if old and reports[old.message_id][0] > found[0]:
                raise ValueError("older report cannot override newer context")
            memory.reported_context[update.field] = ReportedContext(
                **update.model_dump(), recorded_at=found[1].recorded_at, extraction="model"
            )
        return self._save(user_id, issue, memory)

    @_state_write
    def record_attempts(self, user_id: str, issue_id: str, updates: list[AttemptUpdate]) -> dict:
        issue = self.store.get_issue(user_id, issue_id)
        memory = IssueMemory.model_validate(issue["state"])
        reports = {r.message_id: r for r in memory.user_reports}
        actions = {a["id"] for a in action_catalog()}
        existing = {a.id for a in memory.reported_attempts}
        for update in updates:
            report = reports.get(update.message_id or "")
            if report is None or not update.quote or update.quote not in report.text:
                raise ValueError("attempt source must be an employee report for this issue")
            if update.action_id is not None and update.action_id not in actions:
                raise ValueError("unknown reported action")
            digest = hashlib.sha256(
                json.dumps(update.model_dump(), sort_keys=True).encode()
            ).hexdigest()
            if digest not in existing:
                memory.reported_attempts.append(
                    ReportedAttempt(
                        **update.model_dump(), id=digest, recorded_at=report.recorded_at
                    )
                )
                existing.add(digest)
        return self._save(user_id, issue, memory)

    @_state_write
    def record_working_notes(
        self,
        user_id: str,
        issue_id: str,
        default_message_id: str,
        *,
        context_updates: list[ContextUpdate] = (),
        attempt_updates: list[AttemptUpdate] = (),
    ) -> int:
        """Fault-isolated persistence of model working notes bound to real employee reports."""
        if not context_updates and not attempt_updates:
            return 0
        issue = self.store.get_issue(user_id, issue_id)
        memory = IssueMemory.model_validate(issue["state"])
        reports = {r.message_id: (idx, r) for idx, r in enumerate(memory.user_reports)}
        if default_message_id not in reports:
            return len(context_updates) + len(attempt_updates)
        dropped = 0
        changed = False
        for update in context_updates:
            idx, report = reports[default_message_id]
            old = memory.reported_context.get(update.field)
            if old and old.message_id in reports and reports[old.message_id][0] > idx:
                dropped += 1
                continue
            value = (update.value or "").strip()[:180] if update.status == "reported" else None
            if update.status == "reported" and not value:
                dropped += 1
                continue
            quote = update.quote if (update.quote and update.quote in report.text) else None
            memory.reported_context[update.field] = ReportedContext(
                field=update.field,
                value=value,
                note=update.note,
                status=update.status,
                message_id=report.message_id,
                quote=quote,
                recorded_at=report.recorded_at,
                extraction="model",
            )
            changed = True
        actions = {a["id"] for a in action_catalog()}
        existing_ids = {a.id for a in memory.reported_attempts}
        existing_keys = {
            (a.description.strip().casefold(), a.result, a.message_id)
            for a in memory.reported_attempts
        }
        for update in attempt_updates:
            _, report = reports[default_message_id]
            desc = update.description.strip()[:300]
            if not desc:
                dropped += 1
                continue
            semantic_key = (desc.casefold(), update.result, report.message_id)
            if semantic_key in existing_keys:
                continue
            action_id = update.action_id if update.action_id in actions else None
            quote = update.quote if (update.quote and update.quote in report.text) else None
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "description": desc.casefold(),
                        "result": update.result,
                        "message_id": report.message_id,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            if digest not in existing_ids:
                memory.reported_attempts.append(
                    ReportedAttempt(
                        description=desc,
                        result=update.result,
                        action_id=action_id,
                        message_id=report.message_id,
                        quote=quote,
                        id=digest,
                        recorded_at=report.recorded_at,
                    )
                )
                existing_ids.add(digest)
                existing_keys.add(semantic_key)
                changed = True
        if changed:
            self._save(user_id, issue, memory)
        return dropped

    @_state_write
    def record_tool_attempt(self, user_id: str, issue_id: str, attempt: ToolAttempt) -> dict:
        issue = self.store.get_issue(user_id, issue_id)
        memory = IssueMemory.model_validate(issue["state"])
        if not any(a.invocation_id == attempt.invocation_id for a in memory.tool_attempts):
            memory.tool_attempts.append(attempt)
            memory.tool_attempts = memory.tool_attempts[-200:]
        return self._save(user_id, issue, memory)

    @_state_write
    def record_tool_result(
        self,
        user_id: str,
        issue_id: str,
        *,
        tool: str,
        source_ref: str,
        source_at: datetime,
        summary: str,
        success: bool,
        topic: str | None = None,
        subject: str | None = None,
        value: str | None = None,
        invocation_id: str | None = None,
        queried_at: datetime | None = None,
    ) -> dict:
        """Called by the executor with observed output, never by an LLM-supplied receipt.

        source_at is the source timestamp, queried_at is captured at invocation start.
        Current observations require invocation metadata so late results cannot overwrite newer
        queries. Replayed receipts are idempotent; failed fresh queries mark the fact unknown.
        """
        if tool not in TOOL_KINDS:
            raise ValueError("unregistered evidence tool")
        if source_at.tzinfo is None or source_at > self._now():
            raise ValueError("source timestamp must be timezone-aware and not in the future")
        kind = TOOL_KINDS[tool]
        if kind == "current_observation":
            if not invocation_id or queried_at is None:
                raise ValueError("current observations require invocation ID and query-start time")
            if topic not in CURRENT_TOPICS[tool] or not subject:
                raise ValueError("current query requires a valid topic and subject")
            if success and value is None:
                raise ValueError("successful current observation requires a value")
        elif topic is not None or subject is not None or value is not None:
            raise ValueError("history/guidance/policy cannot populate current factual fields")
        queried_at = queried_at or self._now()
        if queried_at.tzinfo is None or queried_at > self._now():
            raise ValueError("query-start timestamp must be timezone-aware and not future")
        issue = self.store.get_issue(user_id, issue_id)
        memory = IssueMemory.model_validate(issue["state"])
        evidence = Evidence(
            id=f"EV-{uuid4()}",
            kind=kind,
            tool=tool,
            source_ref=source_ref,
            source_at=source_at,
            queried_at=queried_at,
            invocation_id=invocation_id or str(uuid4()),
            topic=topic,
            subject=subject,
            value=value if success else None,
            recorded_at=self._now(),
            summary=summary,
            success=success,
        )
        for existing in memory.evidence.values():
            if (
                existing.invocation_id,
                existing.tool,
                existing.source_ref,
                existing.topic,
                existing.subject,
            ) == (evidence.invocation_id, tool, source_ref, topic, subject):
                fields = {"id", "recorded_at"}
                if existing.model_dump(exclude=fields) != evidence.model_dump(exclude=fields):
                    from src.storage import ConflictError

                    raise ConflictError("invocation receipt replayed with different content")
                return issue
        memory.evidence[evidence.id] = evidence
        if kind == "current_observation":
            key = f"{topic}:{subject}"
            old = memory.current_facts.get(key)
            if old is None or (queried_at, source_at) >= (old.queried_at, old.source_at):
                memory.current_facts[key] = CurrentFact(
                    topic=topic,
                    subject=subject,
                    value=evidence.value,
                    evidence_ref=evidence.id,
                    source_at=source_at,
                    queried_at=queried_at,
                )
        elif kind == "historical" and success:
            memory.historical_clues.append(
                HistoricalClue(summary=summary, evidence_ref=evidence.id)
            )
        status = issue["status"]
        if status == Status.AWAITING_SERVICE_RECOVERY and not self._has_incident(memory):
            status = Status.INVESTIGATING
        return self._save(user_id, issue, memory, status)

    @staticmethod
    def _has_incident(memory: IssueMemory) -> bool:
        return any(
            f.topic == "service_status" and f.value in {"degraded", "outage", "maintenance"}
            for f in memory.current_facts.values()
        )

    @_state_write
    def propose(
        self, user_id: str, issue_id: str, proposal: Proposal | dict, *, expected_revision: int
    ) -> dict:
        proposed = Proposal.model_validate(proposal)
        issue = self.store.get_issue(user_id, issue_id)
        if issue["revision"] != expected_revision:
            from src.storage import ConflictError

            raise ConflictError("stale issue revision; reload before updating")
        memory = IssueMemory.model_validate(issue["state"])
        old = Status(issue["status"])
        target = proposed.status or old
        if target not in ALLOWED_TRANSITIONS[old]:
            raise ValueError("terminal issue requires explicit employee-triggered reopening")
        if proposed.hypotheses is not None:
            memory.hypotheses = proposed.hypotheses
        if proposed.open_questions is not None:
            memory.open_questions = proposed.open_questions
        if target == Status.RESOLVED:
            raise ValueError(
                "model proposals cannot resolve an issue; explicit confirmation required"
            )
        if target == Status.ESCALATED:
            self.store.get_handoff(user_id, issue_id)
        if target == Status.AWAITING_USER and not memory.open_questions:
            raise ValueError("awaiting_user requires an unanswered question")
        if target == Status.AWAITING_VERIFICATION and not any(
            s.result in {"suggested", "unknown"} for s in memory.attempted_steps
        ):
            raise ValueError("awaiting_verification requires provided steps pending verification")
        if target == Status.AWAITING_SERVICE_RECOVERY and not self._has_incident(memory):
            raise ValueError("waiting for recovery requires current service-incident evidence")
        return self._save(user_id, issue, memory, target)

    @_state_write
    def add_step(
        self,
        user_id: str,
        issue_id: str,
        instruction: str,
        *,
        authorization: StepAuthorization | None = None,
        action_id: str | None = None,
        action_version: int | None = None,
    ) -> dict:
        if not instruction.strip():
            raise ValueError("step instruction is required")
        issue = self.store.get_issue(user_id, issue_id)
        if Status(issue["status"]) in TERMINAL:
            raise ValueError("reopen terminal issue before adding steps")
        memory = IssueMemory.model_validate(issue["state"])
        memory.attempted_steps.append(
            Step(
                id=f"STEP-{uuid4()}",
                instruction=instruction,
                authorization=authorization,
                action_id=action_id,
                action_version=action_version,
            )
        )
        return self._save(user_id, issue, memory)

    @_state_write
    def report_step_result(
        self,
        user_id: str,
        issue_id: str,
        step_id: str,
        message_id: str,
        result: Literal["succeeded", "failed", "unknown"],
    ) -> dict:
        if result not in {"succeeded", "failed", "unknown"}:
            raise ValueError("invalid employee step result")
        issue = self.store.get_issue(user_id, issue_id)
        message = self._employee_message(user_id, issue, message_id)
        memory = IssueMemory.model_validate(issue["state"])
        event_key = f"step_result:{step_id}:{message_id}"
        if event_key in memory.processed_events:
            if memory.processed_events[event_key].result != result:
                from src.storage import ConflictError

                raise ConflictError("same employee event cannot change its result")
            return issue
        if message["sequence"] < memory.latest_event_sequence or (
            memory.confirmation
            and message["sequence"]
            <= self.store.get_message(user_id, memory.confirmation.message_id)["sequence"]
        ):
            raise ValueError("stale outcome cannot undo a later employee event")
        step = next((s for s in memory.attempted_steps if s.id == step_id), None)
        if step is None:
            raise ValueError("unknown step")
        step.result, step.confirmed_by, step.message_id = result, "user", message_id
        memory.processed_events[event_key] = UserEvent(
            kind="step_result", message_id=message_id, sequence=message["sequence"], result=result
        )
        memory.latest_event_sequence = max(memory.latest_event_sequence, message["sequence"])
        status = issue["status"]
        if result == "failed":
            status, memory.confirmation = Status.INVESTIGATING, None
            memory.confirmation_after_sequence = max(
                memory.confirmation_after_sequence, message["sequence"]
            )
        elif status == Status.AWAITING_VERIFICATION and not any(
            s.result in {"suggested", "unknown"} for s in memory.attempted_steps
        ):
            status = Status.INVESTIGATING
        return self._save(user_id, issue, memory, status)

    @_state_write
    def confirm_resolved(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
        *,
        issue_id: str | None = None,
        explicit_confirmation: bool = False,
    ) -> dict:
        """Trusted issue-specific user event, NOT a model tool or NLP intent parser.

        Bind implicit confirmations to their original issue persistently. An implicit retry
        must never advance to the next issue; intentional multi-issue confirmations need
        separately explicit targets from the controller after clarification.
        """
        if not explicit_confirmation:
            raise ValueError("explicit employee confirmation is required")
        event_key = f"confirmation:{message_id}:{issue_id or 'implicit'}"
        if issue_id is None:
            bindings = []
            for candidate in self.store.list_issues(user_id, session_id):
                events = IssueMemory.model_validate(candidate["state"]).processed_events.values()
                if any(e.kind == "confirmation" and e.message_id == message_id for e in events):
                    bindings.append(candidate)
            if len(bindings) == 1:
                return bindings[0]
            if len(bindings) > 1:
                raise ClarificationRequired(
                    "confirmation has multiple explicit targets; specify one"
                )
        issue = self.select_target(user_id, session_id, issue_id)
        message = self._employee_message(user_id, issue, message_id)
        memory = IssueMemory.model_validate(issue["state"])
        if event_key in memory.processed_events:
            return issue
        # A trusted employee confirmation may close a locally escalated case directly.
        # Reopening with this same message would incorrectly make the confirmation stale.
        if (
            message["sequence"] <= memory.confirmation_after_sequence
            or message["sequence"] < memory.latest_event_sequence
        ):
            raise ValueError("confirmation must follow the latest reopening/failure report")
        memory.confirmation = Confirmation(message_id=message_id, confirmed_at=self._now())
        memory.processed_events[event_key] = UserEvent(
            kind="confirmation", message_id=message_id, sequence=message["sequence"]
        )
        memory.latest_event_sequence = max(memory.latest_event_sequence, message["sequence"])
        memory.open_questions = []
        return self._save(user_id, issue, memory, Status.RESOLVED)

    @_state_write
    def reopen(self, user_id: str, issue_id: str, message_id: str) -> dict:
        issue = self.store.get_issue(user_id, issue_id)
        message = self._employee_message(user_id, issue, message_id)
        memory = IssueMemory.model_validate(issue["state"])
        event_key = f"reopen:{message_id}"
        if event_key in memory.processed_events:
            return issue
        if message["sequence"] <= memory.latest_event_sequence:
            raise ValueError("stale reopening cannot undo a later employee event")
        memory.confirmation = None
        memory.confirmation_after_sequence = max(
            memory.confirmation_after_sequence, message["sequence"]
        )
        memory.latest_event_sequence = message["sequence"]
        memory.processed_events[event_key] = UserEvent(
            kind="reopen", message_id=message_id, sequence=message["sequence"]
        )
        return self._save(user_id, issue, memory, Status.INVESTIGATING)
