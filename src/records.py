"""Application-only successful archives and structured LOCAL handoffs, not model tools."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from src import case_links
from src.data import Contract, Corpus, NonEmptyText
from src.privacy import sanitize
from src.state import StateService, Status
from src.storage import AccessDenied, Store
from src.tools import resolve_service_id


class ArchiveDraft(Contract):
    symptoms: str = Field(min_length=1, max_length=800)
    step_ids: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)


class HandoffIntake(Contract):
    """Local draft input, not an approval application. Only summary is required."""

    summary: NonEmptyText = Field(max_length=1000)
    affected_services: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="Only apps/devices actually involved. Names and aliases accepted, including unlisted apps such as Zoom; do not add unrelated catalog services.",
    )
    impact: str = Field(default="", max_length=500, description="Known impact; omit if unknown.")
    urgency: Literal["low", "normal", "high", "critical"] = "normal"
    urgency_reason: str = Field(default="", max_length=500)
    escalation_reason: str = Field(default="", max_length=800)


class HandoffDraft(HandoffIntake):
    # Application-only links. The Pi model never has to supply these IDs.
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    policy_refs: list[str] = Field(default_factory=list, max_length=10)
    suggested_team: str = Field(default="IT Support", min_length=1, max_length=120)


class Handoff(HandoffDraft):
    handoff_id: str
    issue_id: str
    session_id: str
    employee_id: str
    created_at: AwareDatetime
    updated_at: AwareDatetime | None = None
    revision: int = Field(default=1, ge=1)
    category: str
    summary_updated_at: AwareDatetime | None = None
    summary_through_sequence: int | None = None
    record_through_sequence: int | None = None
    summary_stale: bool = True
    approval_routes: list[dict] = Field(default_factory=list)
    observed_facts: list[dict]
    conversation: list[dict] = Field(default_factory=list)
    reported_attempts: list[dict] = Field(default_factory=list)
    tool_attempts: list[dict] = Field(default_factory=list)
    query_audit: list[dict] = Field(default_factory=list)
    unassigned_session_queries: list[dict] = Field(default_factory=list)
    material_links: list[dict] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def read_legacy(cls, value):
        # Read compatibility only: old SQLite payloads are never bulk-rewritten.
        if isinstance(value, dict):
            return {
                k: v
                for k, v in value.items()
                if k not in {"hypotheses", "attempted_steps", "open_questions"}
            }
        return value

    status: Literal["saved_locally"] = "saved_locally"
    delivery_note: Literal["Local record only; no IT team has been notified."] = (
        "Local record only; no IT team has been notified."
    )


class Records:
    def __init__(self, store: Store, corpus: Corpus):
        self.store, self.corpus = store, corpus
        self.state = StateService(store)
        case_links.ensure_schema(store)
        self.identities = [
            s for e in corpus.employees for s in [e.name, e.id, *[d.id for d in e.devices]]
        ]

    def archive(self, user_id: str, issue_id: str, draft: ArchiveDraft | dict) -> dict:
        draft = ArchiveDraft.model_validate(draft)
        with self.store.transaction():
            issue = self.store.get_issue(user_id, issue_id)
            memory = self.state.memory(user_id, issue_id)
            if issue["status"] != Status.RESOLVED or not memory.confirmation:
                raise ValueError("only employee-confirmed success can be archived")
            if any(
                ref not in memory.evidence or not memory.evidence[ref].success
                for ref in draft.evidence_refs
            ):
                raise ValueError("archive requires actual successful evidence")
            steps = {s.id: s for s in memory.attempted_steps}
            if any(
                s not in steps
                or steps[s].result != "succeeded"
                or not steps[s].message_id
                or self.store.get_message(user_id, steps[s].message_id)["sequence"]
                <= memory.confirmation_after_sequence
                for s in draft.step_ids
            ):
                raise ValueError(
                    "effective remediation requires employee-confirmed successful steps"
                )
            instructions = [steps[s].instruction for s in draft.step_ids] or [
                "Employee reported recovery; no effective remediation step was confirmed."
            ]
            # Outcome comes from the actual user event, never a model's assertion of success.
            message = self.store.get_message(user_id, memory.confirmation.message_id)
            result = "Employee reported resolved: " + message["message"]["content"][:800]

            def clean(value):
                return sanitize(value, personal=True, identities=self.identities)

            record = self.store.save_resolution(
                user_id,
                issue_id,
                symptoms=clean(draft.symptoms),
                body=clean(
                    "## Employee-confirmed successful actions\n"
                    + "\n".join(f"- {s}" for s in instructions)
                ),
                result=clean(result),
                evidence_refs=list(dict.fromkeys(draft.evidence_refs)),
            )
            # The title is also content, while owner/source links stay private metadata.
            document = clean(record["document"])
            self.store._conn.execute(
                "UPDATE resolution_history SET document_json=? WHERE resolution_id=?",
                (json.dumps(document, ensure_ascii=False), record["resolution_id"]),
            )
            return self.store.get_resolution(user_id, record["resolution_id"])

    def _conversation(self, user_id: str, issue_id: str) -> list[dict]:
        issue = self.store.get_issue(user_id, issue_id)
        memory = self.state.memory(user_id, issue_id)
        reports = {r.message_id for r in memory.user_reports}
        # A successful turn already records its selected issue, including turns
        # with no tools. Reuse that relation instead of requiring new bookkeeping.
        if self.store._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pi_turns'"
        ).fetchone():
            reports.update(
                r[0]
                for r in self.store._conn.execute(
                    "SELECT message_id FROM pi_turns WHERE issue_id=? AND status='completed'",
                    (issue_id,),
                )
            )
        return [
            {
                "role": m["message"]["role"],
                "content": m["message"]["content"],
                "sequence": m["sequence"],
            }
            for m in self.store.list_messages(user_id, issue["session_id"])
            if (m["issue_id"] == issue_id or m["message_id"] in reports)
            and m["sequence"] > memory.confirmation_after_sequence
            and m["message"]["role"] in {"user", "assistant"}
            and m["message"].get("content")
        ]

    def archive_conversation(self, user_id: str, issue_id: str) -> dict:
        """Archive the confirmed conversation, without asserting each suggestion worked."""
        with self.store.transaction():
            issue = self.store.get_issue(user_id, issue_id)
            memory = self.state.memory(user_id, issue_id)
            if issue["status"] != Status.RESOLVED or not memory.confirmation:
                raise ValueError("only employee-confirmed success can be archived")
            conversation = self._conversation(user_id, issue_id)

            def clean(value):
                return sanitize(value, personal=True, identities=self.identities)

            record = self.store.save_resolution(
                user_id,
                issue_id,
                symptoms=clean("; ".join(memory.symptoms) or issue["title"]),
                body=clean(
                    "# Archived conversation / 归档对话\n\n"
                    "Assistant messages are suggestions, not confirmed actions. "
                    "Overall recovery does not establish which suggestions were followed "
                    "or prove a root cause.\n\n"
                    + "\n\n".join(f"## {m['role']}\n{m['content']}" for m in conversation)
                ),
                result=clean(
                    "Employee confirmed overall recovery; individual actions and root cause are not independently verified."
                ),
                evidence_refs=[e.id for e in memory.evidence.values() if e.success],
            )
            document = clean(
                {
                    **record["document"],
                    "service_ids": sorted(
                        {t.service_id for t in memory.tool_attempts if t.service_id}
                    ),
                    "summary": "Employee-confirmed recovery. Read body for employee reports; assistant recommendations are not confirmed execution.",
                    "environment": "Employee reports in the archived conversation; recheck for this request.",
                }
            )
            self.store._conn.execute(
                "UPDATE resolution_history SET document_json=? WHERE resolution_id=?",
                (json.dumps(document, ensure_ascii=False), record["resolution_id"]),
            )
            return self.store.get_resolution(user_id, record["resolution_id"])

    def handoff(self, user_id: str, issue_id: str, draft: HandoffDraft | dict) -> dict:
        draft = HandoffDraft.model_validate(draft).model_copy(deep=True)
        with self.store.transaction():
            issue = self.store.get_issue(user_id, issue_id)
            if issue["status"] == Status.RESOLVED:
                raise ValueError("reopen a resolved issue before creating a handoff")
            memory = self.state.memory(user_id, issue_id)
            if not set(draft.evidence_refs) <= memory.evidence.keys():
                raise ValueError("handoff references unknown evidence")
            draft.affected_services = list(
                dict.fromkeys(
                    resolve_service_id(self.corpus, name) or name.strip()
                    for name in draft.affected_services
                    if name.strip()
                )
            )
            if not draft.evidence_refs:
                draft.evidence_refs = list(memory.evidence)[-30:]
            observed_policies = {
                e.source_ref for e in memory.evidence.values() if e.kind == "policy" and e.success
            }
            if not set(draft.policy_refs) <= observed_policies:
                raise ValueError("handoff policy must have actually been queried")
            selected = [p for p in self.corpus.policies if p.id in draft.policy_refs]
            if any(not set(p.service_ids) & set(draft.affected_services) for p in selected):
                raise ValueError("selected policy must match an affected service")
            draft.policy_refs = list(dict.fromkeys(draft.policy_refs))
            policies = [
                p for p in self.corpus.policies if p.id in draft.policy_refs and p.approval_team
            ]
            draft.suggested_team = policies[0].approval_team if policies else draft.suggested_team
            live = self._live_fields(user_id, issue_id)
            body = sanitize(
                {
                    **draft.model_dump(),
                    "approval_routes": [
                        {
                            "policy_id": p.id,
                            "services": [s for s in draft.affected_services if s in p.service_ids],
                            "resource": p.resource,
                            "approval_team": p.approval_team,
                            "required_information": p.required_information,
                        }
                        for p in self.corpus.policies
                        if p.id in draft.policy_refs and p.approval_team
                    ],
                    "category": issue["category"],
                    **live,
                    "summary_updated_at": self.store._now(),
                    "summary_through_sequence": live["record_through_sequence"],
                    "summary_stale": False,
                    "status": "saved_locally",
                    "delivery_note": "Local record only; no IT team has been notified.",
                }
            )
            result = Handoff.model_validate(
                self.store.save_handoff(user_id, issue_id, body, refresh=True)
            )
            return result.model_dump(mode="json")

    def _live_fields(self, user_id: str, issue_id: str) -> dict:
        memory = self.state.memory(user_id, issue_id)
        conversation = self._conversation(user_id, issue_id)
        issue = self.store.get_issue(user_id, issue_id)
        material_links = case_links.links(self.store, user_id, issue_id)
        linked_queries = {r["reference"] for r in material_links if r["kind"] == "query"}
        audit = [
            dict(row)
            for row in self.store._conn.execute(
                "SELECT invocation_id,issue_id,tool,result_json,created_at FROM tool_requests WHERE session_id=? ORDER BY rowid",
                (issue["session_id"],),
            )
        ]
        for row in audit:
            result = json.loads(row.pop("result_json"))
            row["ok"], row["error"] = result["ok"], result.get("error")
            row["evidence_refs"] = result.get("evidence_refs", [])
        return {
            "query_audit": [
                row
                for row in audit
                if row["issue_id"] == issue_id or row["invocation_id"] in linked_queries
            ],
            "material_links": material_links,
            "unassigned_session_queries": [row for row in audit if row["issue_id"] is None],
            "conversation": conversation,
            "record_through_sequence": max(
                (m["sequence"] for m in conversation if m["role"] == "user"), default=None
            ),
            "evidence_refs": list(memory.evidence)[-30:],
            "observed_facts": [f.model_dump(mode="json") for f in memory.current_facts.values()],
            "reported_attempts": [a.model_dump(mode="json") for a in memory.reported_attempts],
            "tool_attempts": [a.model_dump(mode="json") for a in memory.tool_attempts],
        }

    def sync_handoff(self, user_id: str, issue_id: str) -> dict | None:
        """Refresh an existing local record, never rewrite its model-authored summary."""
        with self.store.transaction():
            issue = self.store.get_issue(user_id, issue_id)
            try:
                old = self.store.get_handoff(user_id, issue_id)
            except AccessDenied:
                return None
            # Confirmation starts an archive boundary; keep the handed-off episode intact.
            if issue["status"] == Status.RESOLVED:
                return old
            live = self._live_fields(user_id, issue_id)
            body = {
                **{
                    k: v
                    for k, v in old.items()
                    if k not in {"hypotheses", "attempted_steps", "open_questions"}
                },
                **live,
                "summary_stale": (
                    old.get("summary_through_sequence") is None
                    or old["summary_through_sequence"] != live["record_through_sequence"]
                ),
            }
            return self.store.save_handoff(user_id, issue_id, sanitize(body), refresh=True)

    def export_handoff(self, user_id: str, issue_id: str) -> str:
        record = Handoff.model_validate(self.store.get_handoff(user_id, issue_id))
        return record.model_dump_json(indent=2)
