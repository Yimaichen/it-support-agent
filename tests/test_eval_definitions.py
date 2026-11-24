import json
from collections import defaultdict

import pytest

from evals.contracts import EvaluationSuite, load_suite
from src.config import PROJECT_ROOT
from src.data import Category, load_corpus


def test_suite_has_coverage_and_no_run_claims():
    suite = load_suite()
    assert len(suite.queries) == 40
    assert len(suite.scenarios) == 16
    core = [s for s in suite.scenarios if s.scenario_type == "core"]
    assert len(core) == 10
    assert {s.category for s in core} == set(Category)
    groups = defaultdict(list)
    for s in core:
        groups[s.equivalence_group].append(s)
        assert {c.method for c in s.checks} == {"automatic", "human"}
    for pair in groups.values():
        assert {s.language for s in pair} == {"en", "zh"}
        assert pair[0].allowed_final_states == pair[1].allowed_final_states
        assert pair[0].evidence_groups == pair[1].evidence_groups
    assert {s.scenario_type for s in suite.scenarios if s.scenario_type != "core"} == {
        "missing",
        "conflict",
        "expired",
        "tool_failure",
        "historical_state",
        "override",
    }
    assert {q.split for q in suite.queries} == {"tuning", "holdout"}
    for corpus in ("knowledge", "resolutions"):
        for lang in ("en", "zh"):
            assert any(q.corpus == corpus and q.language == lang for q in suite.queries)


@pytest.mark.parametrize("target", ["query", "scenario"])
def test_dangling_eval_reference_rejected(target):
    raw = load_suite().model_dump(mode="json")
    if target == "query":
        raw["queries"][0]["relevant_ids"] = ["missing"]
    else:
        raw["scenarios"][0]["evidence_groups"] = [["missing"]]
    with pytest.raises(ValueError, match="unknown"):
        EvaluationSuite.model_validate(raw).validate_against(load_corpus())


def test_regression_requirements_documented():
    cases = json.loads((PROJECT_ROOT / "evals/regression_cases.json").read_text())
    assert len({r["id"] for r in cases}) == len(cases) == 12
    assert {"multiple_devices", "resource_scoped_permissions"} <= {r["topic"] for r in cases}
    assert all(r["given"] and r["when"] and r["expected"] for r in cases)
