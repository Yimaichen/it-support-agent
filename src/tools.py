"""Read-only business tools. Identity and evidence receipts come from trusted context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from pydantic import Field, Strict, StringConstraints, ValidationError, model_validator

from src.data import Contract, Corpus
from src.investigation import effective_status_region
from src.privacy import sanitize
from src.retrieval import Retrieval, resolution_origin
from src.state import StateService, ToolAttempt
from src.storage import AccessDenied, Store

ServiceName = Annotated[
    str, Strict(), StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
]


def _policy_view(policy) -> dict:
    """Return policy text plus objective state this local application can actually know."""
    identity_required = policy.approval_team == "Identity Support" and any(
        "identity" in item.casefold() for item in policy.required_information
    )
    return {
        **policy.model_dump(mode="json"),
        "source_type": "policy",
        "content_complete": True,
        # This build has no trusted identity-verification tool. A local handoff
        # draft is not verification and therefore cannot change this state.
        "identity_verification": {
            "required": identity_required,
            "completed": False if identity_required else None,
        },
    }


def resolve_service_id(corpus: Corpus, raw: str | None) -> str | None:
    """Resolve canonical service ID or registered alias; including known services without monitoring."""
    if not raw or not isinstance(raw, str):
        return None
    target = raw.strip().casefold()
    if not target:
        return None
    for service in corpus.services:
        candidates = [
            service.id,
            service.name,
            *service.aliases,
            *corpus.aliases.get(service.id, []),
        ]
        if any(c.strip().casefold() == target for c in candidates if c):
            return service.id
    return None


class ToolInputError(ValueError):
    def __init__(self, kind: str, detail: str):
        self.kind = kind
        super().__init__(detail)


class EmptyArgs(Contract):
    pass


class SearchArgs(Contract):
    service_id: ServiceName | None = Field(
        default=None,
        description="Optional service name or ID to filter candidates before ranking (aliases like Okta, Snowflake, Grafana supported); ownership filtering always applies first.",
    )
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=10, strict=True)


class StatusArgs(Contract):
    service_id: ServiceName = Field(
        description="Service ID or common name (e.g., sso, Okta, vpn, salesforce, jenkins, data_pipeline, Snowflake, tableau, Grafana, or an unlisted app/device name)."
    )
    region: str | None = Field(default=None, min_length=1, max_length=80)


class PolicyArgs(Contract):
    service_id: ServiceName | None = Field(
        default=None,
        description="Service name or ID for discovering policies. Use either this or policy_id, never both.",
    )
    policy_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Read one known policy ID in full, without service/resource/action filters.",
    )

    @model_validator(mode="after")
    def selector(self):
        if bool(self.service_id) == bool(self.policy_id):
            raise ValueError("Use exactly one of service_id or policy_id")
        if self.policy_id and (self.resource is not None or self.action is not None):
            raise ValueError("policy_id cannot be combined with discovery filters")
        return self

    resource: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description="Optional EXACT stored policy resource. Omit to discover labels for the service; employee permission scope is not a policy resource alias.",
    )
    action: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        description="Optional EXACT action label from a prior policy result. Omit when unknown; an empty filtered result is not authorization.",
    )


class HistoryArgs(Contract):
    session_id: str | None = Field(default=None, min_length=1, max_length=100)
    limit: int = Field(default=5, ge=1, le=10, strict=True)


class ReadResolutionArgs(Contract):
    resolution_id: str = Field(min_length=1, max_length=100)


class ReadKnowledgeArgs(Contract):
    source_id: str = Field(
        min_length=1,
        max_length=100,
        description="KB document ID such as KB-ACC-002. POL-... is a policy ID: lookup_policy already returns its full text.",
    )
    section: str | None = Field(default=None, min_length=1, max_length=200)


DEFINITIONS = {
    "search_knowledge_base": (
        SearchArgs,
        "Search candidate guides by lexical relevance, not applicability or authorization. Inspect scope, prerequisites, expiry and policy references; read the candidate before using its procedure.",
    ),
    "check_service_status": (
        StatusArgs,
        "Read a dated SYNTHETIC service/region snapshot and changes, not live monitoring or individual account state. queried_at does not make observed_at current.",
    ),
    "get_current_user": (
        EmptyArgs,
        "Read the bound employee's recorded devices and exact scoped permissions; never infer which device is in use or approval.",
    ),
    "lookup_policy": (
        PolicyArgs,
        "Discover policies using service_id with optional exact resource/action filters, or read one policy using policy_id alone. Returns complete text and applicability; a match does not itself authorize an action. Unknown IDs/scopes are explicit errors. Cannot grant or approve.",
    ),
    "search_resolution_history": (
        SearchArgs,
        "Search valid historical case HEADERS with up to top_k results PER group: personal_history and public_examples. Only personal_history belongs to this employee; never describe public examples as their experience. Use read_resolution with resolution_id from a header for full details before describing past actions. History is a clue, not current fact or permission.",
    ),
    "read_resolution": (
        ReadResolutionArgs,
        "Read the full visible, valid case body by resolution_id from search headers, preserving whether it belongs to this employee or is a public reference example. Distinguish assistant suggestions from employee reports; overall confirmation does not prove every suggestion was followed. Check current KB/policy separately.",
    ),
    "read_knowledge_document": (
        ReadKnowledgeArgs,
        "Read a KB-... document in full, or only an exact section heading. Policy text is returned by lookup_policy, not this tool. Returns section names and employee_sections; investigation/handoff notes are for the specialist. Expiry is explicit. Reading does not publish text; normally explain relevant content naturally.",
    ),
    "get_my_support_history": (
        HistoryArgs,
        "Read only the employee's past CHAT, including unresolved cases. This is not a confirmed action list; assistant suggestions do not establish employee execution. For past solutions use search_resolution_history then read_resolution. Optional owned session detail.",
    ),
}


@dataclass(frozen=True)
class ToolContext:
    user_id: str
    session_id: str
    issue_id: str | None


class ToolRunner:
    def __init__(self, corpus: Corpus, store: Store, *, faults: dict[str, str] | None = None):
        self.corpus, self.store = corpus, store
        self.state = StateService(store)
        self.retrieval = Retrieval(corpus, store)
        self.faults = faults or {}

    @staticmethod
    def schemas() -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": model.model_json_schema(),
                },
            }
            for name, (model, description) in DEFINITIONS.items()
        ]

    @staticmethod
    def _evidence_summary(data, source: dict) -> str:
        """Attach each citation to its own item, never the first unrelated hit."""
        if source.get("topic"):
            item = source
        else:
            candidates = data if isinstance(data, list) else [data]
            if isinstance(data, dict) and "personal_history" in data:
                candidates = data["personal_history"] + data["public_examples"]
            item = next(
                (
                    entry
                    for entry in candidates
                    if isinstance(entry, dict)
                    and source["source_id"]
                    in {entry.get("source_id"), entry.get("id"), entry.get("resolution_id")}
                ),
                source,
            )
        return json.dumps(sanitize(item), ensure_ascii=False)[:1500]

    def _dispatch(self, ctx: ToolContext, name: str, args) -> tuple[object, list[dict]]:
        if name == "search_knowledge_base":
            resolved = resolve_service_id(self.corpus, args.service_id) if args.service_id else None
            if args.service_id and resolved is None:
                raise ToolInputError(
                    "unknown_service",
                    "Service filter is not recognized; omit the filter for an explicit cross-service search.",
                )
            query = args.query
            data = self.retrieval.knowledge(query, top_k=args.top_k, service_id=resolved)
            return data, data
        if name == "search_resolution_history":
            resolved = resolve_service_id(self.corpus, args.service_id) if args.service_id else None
            if args.service_id and resolved is None:
                raise ToolInputError(
                    "unknown_service",
                    "Service filter is not recognized; omit the filter for an explicit cross-service search.",
                )
            query = args.query
            data = self.retrieval.resolutions(
                ctx.user_id, query, top_k=args.top_k, service_id=resolved
            )
            groups = {"personal_history": [], "public_examples": []}
            for item in data:
                group = (
                    "personal_history" if item["belongs_to_current_employee"] else "public_examples"
                )
                groups[group].append({k: v for k, v in item.items() if k != "source_id"})
            return groups, data
        if name == "read_resolution":
            record = self.store.get_resolution(ctx.user_id, args.resolution_id)
            data = {
                "resolution_id": record["resolution_id"],
                "source_type": "historical",
                **resolution_origin(record),
                "valid": record["valid"],
                "document": record["document"],
                "source_at": record["document"].get("resolved_at", record["created_at"]),
                "imported_at": record["created_at"],
                "time_basis": "resolved_at"
                if record["document"].get("resolved_at")
                else "record_created_at",
            }
            return data, [
                {
                    "source_id": record["resolution_id"],
                    "source_type": "historical",
                    "source_at": data["source_at"],
                    **resolution_origin(record),
                }
            ]
        if name == "read_knowledge_document":
            doc = next((d for d in self.corpus.knowledge if d.id == args.source_id), None)
            if doc is None:
                raise AccessDenied("resource unavailable")
            from src.data import is_expired
            from src.source_content import document_sections, employee_sections, read_section

            expired = is_expired(doc, self.corpus.reference_time)
            section_content = None
            if args.section:
                section_content = read_section(doc.body, args.section)
            data = {
                **doc.model_dump(mode="json", exclude={"body"} if args.section else set()),
                "read_mode": "section" if args.section else "full",
                "source_id": doc.id,
                "source_type": "guidance",
                "source_at": doc.updated_at.isoformat(),
                "expired": expired,
                "sections": list(document_sections(doc.body)),
                "employee_sections": employee_sections(doc.body),
                "section_content": section_content,
                "usage_note": (
                    "Expired source: investigation reference only, not current operational guidance. "
                    if expired
                    else ""
                )
                + "Source evidence only; nothing is displayed automatically. Explain supported actions in the employee's language; do not paste passages unless explicitly requested.",
            }
            return data, [
                {
                    k: data[k]
                    for k in ("source_id", "source_type", "source_at", "expired", "valid_until")
                }
            ]
        if name == "check_service_status":
            resolved = resolve_service_id(self.corpus, args.service_id)
            service = next((s for s in self.corpus.services if s.id == resolved), None)
            if service is None or service.status == "not_monitored":
                data = {
                    **(service.model_dump(mode="json") if service else {}),
                    "id": service.id if service else args.service_id,
                    "name": service.name if service else args.service_id,
                    "known_service": service is not None,
                    "monitoring_available": False,
                    "status": "not_monitored",
                    "health": "unknown",
                    "global_status": "unknown",
                    "requested_region": args.region,
                    "region_required": False,
                    "changes": [],
                    "data_mode": "synthetic_snapshot",
                    "details": service.details
                    if service
                    else "Service is absent from the directory; no health observation or internal runbook is available.",
                    "snapshot_note": "No monitoring observation; unknown health, not healthy or failed.",
                }
                return data, []
            effective_region = effective_status_region(service, args.region)
            if service.region_status:
                status = service.region_status.get(effective_region, "unknown")
            else:
                status = service.status
            data = {
                **service.model_dump(mode="json"),
                "status": status,
                "known_service": True,
                "monitoring_available": True,
                "health": status,
                "global_status": service.status,
                "requested_region": args.region,
                "effective_scope": effective_status_region(service, args.region) or "global",
                "region_required": bool(service.region_status) and status == "unknown",
                "changes": [
                    c.model_dump(mode="json")
                    for c in self.corpus.changes
                    if c.service_id == service.id
                    and (
                        not args.region
                        or "global" in c.regions
                        or args.region.casefold() in [r.casefold() for r in c.regions]
                    )
                ],
                "observed_at": service.checked_at.isoformat(),
                "data_mode": "synthetic_snapshot",
                "snapshot_note": "Dated synthetic snapshot, NOT live monitoring. queried_at is only retrieval time. Limited to effective_scope; individual/tenant/path failures are not excluded.",
            }
            source = {
                "source_id": f"STATUS:{service.id}:{effective_region or 'global'}",
                "source_at": service.checked_at.isoformat(),
                "topic": "service_status",
                "subject": f"{service.id}:{effective_region or 'global'}",
                "value": status,
            }
            return data, [
                source,
                *[{"source_id": c["id"], "source_at": c["started_at"]} for c in data["changes"]],
            ]
        if name == "get_current_user":
            employee = next(e for e in self.corpus.employees if e.id == ctx.user_id)
            data = {
                **employee.model_dump(mode="json"),
                "limitations": "Recorded snapshot, not live IAM or approval. Confirm active device; missing grants mean unknown, not denied.",
            }
            sources = [
                {
                    "source_id": employee.id,
                    "source_at": employee.updated_at.isoformat(),
                    "topic": "employee_profile",
                    "subject": employee.id,
                    "value": json.dumps(data, ensure_ascii=False),
                }
            ]
            for device in employee.devices:
                sources.append(
                    {
                        "source_id": employee.id,
                        "source_at": device.updated_at.isoformat(),
                        "topic": "device",
                        "subject": device.id,
                        "value": json.dumps(device.model_dump(mode="json")),
                    }
                )
            for permission in employee.permissions:
                sources.append(
                    {
                        "source_id": employee.id,
                        "source_at": permission.updated_at.isoformat(),
                        "topic": "permissions",
                        "subject": f"{permission.service_id}:{permission.resource_type}:{permission.resource_id}:{permission.role}",
                        "value": json.dumps(permission.model_dump(mode="json")),
                    }
                )
            return data, sources
        if name == "lookup_policy":
            if args.policy_id:
                selected = next((p for p in self.corpus.policies if p.id == args.policy_id), None)
                if selected is None:
                    raise ToolInputError(
                        "unknown_policy", "No policy with this ID; no broader search was performed."
                    )
                data = [_policy_view(selected)]
                return data, [
                    {"source_id": selected.id, "source_at": selected.updated_at.isoformat()}
                ]
            resolved = resolve_service_id(self.corpus, args.service_id)
            if resolved is None:
                raise ToolInputError(
                    "unknown_service",
                    "Service is not recognized; no policy scope was searched. Unknown policy is not permission.",
                )
            data = [
                _policy_view(p)
                for p in self.corpus.policies
                if resolved in p.service_ids
                and (args.resource is None or p.resource == args.resource)
                and (args.action is None or p.action == args.action)
            ]
            return data, [{"source_id": p["id"], "source_at": p["updated_at"]} for p in data]
        history = self.store.personal_history(ctx.user_id, session_id=args.session_id)[: args.limit]
        sources = []
        for session in history:
            sources.append({"source_id": session["session_id"], "source_at": session["updated_at"]})
            session["issues"] = [
                {k: issue[k] for k in ("issue_id", "title", "category", "status", "updated_at")}
                for issue in session["issues"]
            ]
            # Own detail only; bounded redacted excerpts, no opaque protocol/CoT fields.
            if "messages" in session:
                session["messages"] = [
                    {
                        "message_id": m["message_id"],
                        "role": m["message"]["role"],
                        "content": (m["message"].get("content") or "")[:1200],
                    }
                    for m in session["messages"][-20:]
                ]
        return history, sources

    @staticmethod
    def _source_metadata(sources):
        fields = {
            "source_id",
            "source_type",
            "source_at",
            "expired",
            "valid_until",
            "visibility",
            "belongs_to_current_employee",
            "result_basis",
        }
        return [{k: v for k, v in source.items() if k in fields} for source in sources]

    def execute(self, ctx, name, arguments, *, invocation_id=None):
        invocation_id = invocation_id or str(uuid4())
        # Authenticate before recording anything in a personal audit journal.
        try:
            self.store.get_session(ctx.user_id, ctx.session_id)
        except AccessDenied:
            return self._execute(ctx, name, arguments, invocation_id=invocation_id)
        issue_id = None
        if ctx.issue_id:
            try:
                issue = self.store.get_issue(ctx.user_id, ctx.issue_id)
                if issue["session_id"] == ctx.session_id:
                    issue_id = ctx.issue_id
            except AccessDenied:
                pass
        result = self._execute(ctx, name, arguments, invocation_id=invocation_id)
        with self.store.transaction():
            self.store._conn.execute(
                "INSERT OR IGNORE INTO tool_requests (session_id,invocation_id,issue_id,tool,arguments_json,result_json,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    ctx.session_id,
                    invocation_id,
                    issue_id,
                    name,
                    json.dumps(sanitize(arguments), ensure_ascii=False),
                    json.dumps(sanitize(result), ensure_ascii=False),
                    self.store._now(),
                ),
            )
            if issue_id and not result["ok"]:
                self.state.record_tool_attempt(
                    ctx.user_id,
                    issue_id,
                    ToolAttempt(
                        invocation_id=invocation_id,
                        tool=name,
                        outcome="failed",
                        recorded_at=self.store.clock(),
                    ),
                )
        return result

    def _execute(
        self, ctx: ToolContext, name: str, arguments: dict, *, invocation_id: str | None = None
    ) -> dict:
        invocation_id = invocation_id or str(uuid4())
        queried_at = self.store.clock()
        base = {
            "ok": False,
            "data": None,
            "sources": [],
            "evidence_refs": [],
            "queried_at": queried_at.isoformat(),
            "invocation_id": invocation_id,
            "source_type": "untrusted_tool_data",
            "error": None,
        }
        if name not in DEFINITIONS:
            return {**base, "error": {"type": "unknown_tool", "retryable": False}}
        try:
            self.store.get_session(ctx.user_id, ctx.session_id)
            if ctx.issue_id is not None:
                issue = self.store.get_issue(ctx.user_id, ctx.issue_id)
                if issue["session_id"] != ctx.session_id:
                    raise AccessDenied("resource unavailable")
            elif name not in {
                "get_current_user",
                "get_my_support_history",
                "search_resolution_history",
                "read_resolution",
                "read_knowledge_document",
            }:
                return {**base, "error": {"type": "no_current_case", "retryable": False}}
            if ctx.user_id not in {e.id for e in self.corpus.employees}:
                raise AccessDenied("resource unavailable")
            args = DEFINITIONS[name][0].model_validate(arguments)
        except ValidationError:
            return {**base, "error": {"type": "invalid_arguments", "retryable": False}}
        except AccessDenied:
            return {**base, "error": {"type": "unavailable", "retryable": False}}
        if name == "read_knowledge_document" and args.source_id.startswith("POL-"):
            return {
                **base,
                "error": {
                    "type": "wrong_source_type",
                    "retryable": False,
                    "detail": "POL-... is a policy ID, not a KB document. lookup_policy returns complete policy entries; use that result, or query lookup_policy for the service.",
                },
            }
        fault = self.faults.get(name)
        try:
            data, sources = self._dispatch(ctx, name, args)
        except AccessDenied:
            return {**base, "error": {"type": "unavailable", "retryable": False}}
        except ValueError as exc:
            return {
                **base,
                "error": {
                    "type": exc.kind if isinstance(exc, ToolInputError) else "invalid_section",
                    "detail": str(exc),
                    "retryable": False,
                },
            }
        if fault:
            data = None
            # Only attempted current-query sources create unknown observations; no fabricated
            # historical/guidance/policy citation for a failed request.
            sources = [s for s in sources if "topic" in s]
        if ctx.issue_id is None:
            # Identity-bound profile/history requests need no artificial support case.
            # These sources are not authorizing evidence for a later case.
            return sanitize(
                {
                    **base,
                    "ok": not bool(fault),
                    "data": data,
                    "sources": self._source_metadata(sources),
                    "error": {"type": fault, "retryable": False} if fault else None,
                }
            )
        receipts = []
        with self.store.transaction():
            if name == "get_current_user":
                prior = self.state.memory(ctx.user_id, ctx.issue_id)
                present = {(s.get("topic"), s.get("subject")) for s in sources}
                profile = next(s for s in sources if s.get("topic") == "employee_profile")
                # Disappearing grants/devices are UNKNOWN, not an enduring old grant or a denial.
                for fact in prior.current_facts.values():
                    if (
                        fact.topic in {"device", "permissions"}
                        and (fact.topic, fact.subject) not in present
                    ):
                        sources.append(
                            {
                                **profile,
                                "topic": fact.topic,
                                "subject": fact.subject,
                                "value": None,
                                "value_unavailable": True,
                            }
                        )
            outcome = (
                "failed"
                if fault
                else (
                    "empty"
                    if (
                        data == []
                        or (name == "search_resolution_history" and not any(data.values()))
                        or (isinstance(data, dict) and data.get("known_service") is False)
                    )
                    else "hit"
                )
            )
            self.state.record_tool_attempt(
                ctx.user_id,
                ctx.issue_id,
                ToolAttempt(
                    invocation_id=invocation_id,
                    tool=name,
                    outcome=outcome,
                    source_refs=[s["source_id"] for s in sources] if not fault else [],
                    service_id=resolve_service_id(self.corpus, getattr(args, "service_id", None)),
                    requested_service=getattr(args, "service_id", None),
                    resource=getattr(args, "resource", None),
                    action=getattr(args, "action", None),
                    recorded_at=self.store.clock(),
                ),
            )
            for source in sources:
                # Changes are evidence of events, not separate assertions of current health.
                if name in {"check_service_status", "get_current_user"} and "topic" not in source:
                    continue
                self.state.record_tool_result(
                    ctx.user_id,
                    ctx.issue_id,
                    tool=name,
                    source_ref=source["source_id"],
                    source_at=datetime.fromisoformat(source["source_at"].replace("Z", "+00:00")),
                    summary=self._evidence_summary(data, source)
                    if not fault and not source.get("value_unavailable")
                    else "Current value unavailable; state unknown.",
                    success=not bool(fault) and not source.get("value_unavailable", False),
                    invocation_id=invocation_id,
                    queried_at=queried_at,
                    topic=source.get("topic"),
                    subject=source.get("subject"),
                    value=source.get("value") if not fault else None,
                )
            memory = self.state.memory(ctx.user_id, ctx.issue_id)
            receipts = [e.id for e in memory.evidence.values() if e.invocation_id == invocation_id]
        return sanitize(
            {
                **base,
                "ok": not bool(fault),
                "data": data,
                "sources": self._source_metadata(sources),
                "evidence_refs": receipts,
                "error": {"type": fault, "retryable": fault in {"timeout", "unavailable"}}
                if fault
                else None,
            }
        )
