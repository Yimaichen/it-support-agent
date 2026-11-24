"""Evaluation definitions only. Running this validates fixtures, not model quality."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from src.config import PROJECT_ROOT
from src.data import Category, Contract, Corpus, load_corpus

STATES = {
    "investigating",
    "awaiting_user",
    "awaiting_verification",
    "awaiting_service_recovery",
    "resolved",
    "escalated",
}


class Query(Contract):
    id: str
    corpus: Literal["knowledge", "resolutions"]
    language: Literal["en", "zh"]
    split: Literal["tuning", "holdout"]
    equivalence_group: str
    query: str = Field(min_length=3)
    relevant_ids: list[str] = Field(min_length=1)


class Check(Contract):
    id: str
    method: Literal["automatic", "human"]
    rule: str
    description: str


class Followup(Contract):
    topic: str
    answer: str


class Scenario(Contract):
    id: str
    language: Literal["en", "zh"]
    category: Category
    scenario_type: Literal[
        "core", "missing", "conflict", "expired", "tool_failure", "historical_state", "override"
    ]
    equivalence_group: str | None
    employee_id: str
    initial_message: str
    followups: list[Followup]
    final_user_confirmation: str | None
    allowed_final_states: list[str] = Field(min_length=1)
    evidence_groups: list[list[str]]
    forbidden_behaviors: list[str] = Field(min_length=1)
    checks: list[Check] = Field(min_length=1)
    environment: dict[str, str]

    @model_validator(mode="after")
    def known_states(self) -> Scenario:
        if not set(self.allowed_final_states) <= STATES:
            raise ValueError("unknown expected state")
        if any(not group for group in self.evidence_groups):
            raise ValueError("empty evidence group")
        return self


class EvaluationSuite(Contract):
    queries: list[Query]
    scenarios: list[Scenario]

    def validate_against(self, corpus: Corpus) -> None:
        query_ids = [q.id for q in self.queries]
        scenario_ids = [s.id for s in self.scenarios]
        if len(set(query_ids)) != len(query_ids) or len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("duplicate evaluation IDs")
        kb = {d.id for d in corpus.knowledge}
        cases = {d.id for d in corpus.resolutions}
        evidence = (
            kb
            | cases
            | {s.id for s in corpus.services}
            | {p.id for p in corpus.policies}
            | {c.id for c in corpus.changes}
        )
        users = {u.id for u in corpus.employees}
        for query in self.queries:
            if not set(query.relevant_ids) <= (kb if query.corpus == "knowledge" else cases):
                raise ValueError(f"unknown query target in {query.id}")
        for scenario in self.scenarios:
            if scenario.employee_id not in users:
                raise ValueError(f"unknown employee in {scenario.id}")
            if any(not set(group) <= evidence for group in scenario.evidence_groups):
                raise ValueError(f"unknown evidence in {scenario.id}")
            if scenario.environment.get("reference_time") != corpus.reference_time.isoformat():
                raise ValueError(f"scenario clock mismatch in {scenario.id}")


def load_suite(root: Path = PROJECT_ROOT / "evals", corpus: Corpus | None = None):
    suite = EvaluationSuite.model_validate(
        {
            "queries": json.loads((root / "retrieval_queries.json").read_text()),
            "scenarios": json.loads((root / "scenarios.json").read_text()),
        }
    )
    suite.validate_against(corpus or load_corpus())
    return suite


if __name__ == "__main__":
    suite = load_suite()
    print(
        f"Validated definitions: {len(suite.queries)} retrieval queries, "
        f"{len(suite.scenarios)} scenarios. NOT executed against any model."
    )
