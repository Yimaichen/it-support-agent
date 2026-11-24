"""Evaluation arithmetic and actual offline runner controls; not model-effect tests."""

import json
from pathlib import Path

import pytest

from evals.contracts import Query
from evals.retrieval import (
    evaluate_queries,
    main,
    render_markdown,
    run_evaluation,
    score_ranking,
    summarize,
)
from src.config import PROJECT_ROOT


@pytest.mark.parametrize(
    "ranked,relevant,recall,rr,rank",
    [
        ([], ["A"], 0, 0, None),
        (["X"], ["A"], 0, 0, None),
        (["A"], ["A", "B"], 0.5, 1, 1),
        (["X", "A", "B"], ["A", "B"], 1, 0.5, 2),
        (["X", "Y", "Z", "W", "A"], ["A"], 1, 0.2, 5),
        (["X", "Y", "Z", "W", "V", "A"], ["A"], 0, 0, None),
        (["A"], ["A", "A"], 1, 1, 1),
    ],
)
def test_recall_and_rr_arithmetic(ranked, relevant, recall, rr, rank):
    result = score_ranking(ranked, relevant)
    assert result["recall_at_5"] == recall
    assert result["reciprocal_rank_at_5"] == rr
    assert result["first_relevant_rank"] == rank
    assert set(result["matched_ids"]) | set(result["missing_relevant_ids"]) == set(relevant)


def test_invalid_labels_and_duplicate_ranking_rejected():
    with pytest.raises(ValueError, match="nonempty"):
        score_ranking(["A"], [])
    with pytest.raises(ValueError, match="duplicate"):
        score_ranking(["A", "A"], ["A"])
    assert summarize([]) == {
        "n_queries": 0,
        "hit_queries": 0,
        "complete_recall_queries": 0,
        "recall_at_5": None,
        "mrr_at_5": None,
        "retrieval_errors": 0,
    }


def make_query(id_, *, corpus="knowledge", language="en", relevant=None):
    return Query(
        id=id_,
        corpus=corpus,
        language=language,
        split="tuning",
        equivalence_group="arithmetic-control",
        query=id_,
        relevant_ids=relevant or ["A"],
    )


def test_group_denominators_errors_and_multi_relevance():
    seen_users = []

    class Stub:
        def knowledge(self, query, *, top_k):
            assert top_k == 5
            return [
                {"source_id": i, "score": 1, "source_type": "guidance"}
                for i in (["X", "A"] if query == "multi" else [])
            ]

        def resolutions(self, user_id, query, *, top_k):
            seen_users.append(user_id)
            raise RuntimeError("synthetic error body must not enter report")

    result = evaluate_queries(
        [
            make_query("multi", relevant=["A", "B"]),
            make_query("empty", language="zh"),
            make_query("error", corpus="resolutions", language="zh"),
        ],
        Stub(),
        "EMP-001",
    )
    assert seen_users == ["EMP-001"]
    assert result["summary"]["n_queries"] == 3
    assert result["summary"]["recall_at_5"] == pytest.approx(1 / 6)
    assert result["summary"]["mrr_at_5"] == pytest.approx(1 / 6)
    assert result["summary"]["retrieval_errors"] == 1
    assert result["incomplete_recall_query_ids"] == ["multi", "empty", "error"]
    assert result["queries"][0]["unjudged_ids_at_5"] == ["X"]
    assert result["by_corpus"]["knowledge"]["n_queries"] == 2
    empty_group = next(
        g for g in result["groups"] if g["corpus"] == "resolutions" and g["language"] == "en"
    )
    assert empty_group["n_queries"] == 0 and empty_group["mrr_at_5"] is None
    assert "synthetic error body" not in json.dumps(result)


def test_actual_tuning_runner_is_seed_only_deterministic_and_temporary(offline_env, monkeypatch):
    import evals.retrieval as module

    original, opened = module.Store, []

    class CheckedStore(original):
        def __init__(self, path, *args, **kwargs):
            assert Path(path).name == "seed.sqlite3"
            assert Path(path).parent.name.startswith("it-retrieval-eval-")
            opened.append(Path(path))
            super().__init__(path, *args, **kwargs)

    monkeypatch.setattr(module, "Store", CheckedStore)
    first, second = run_evaluation(), run_evaluation()
    assert len(opened) == 2 and all(not p.exists() for p in opened)
    assert first["summary"]["n_queries"] == 20
    assert first["metadata"]["shared_seed_resolutions"] == 10
    assert first["metadata"]["knowledge_documents"] == 21
    assert first["metadata"]["evaluation"] == "actual_offline_bm25_not_llm"
    for name in ("summary", "queries", "groups"):
        assert first[name] == second[name]
    assert all(q["split"] == "tuning" for q in first["queries"])
    for name in ("corpus_sha256", "queries_sha256", "retrieval_source_sha256"):
        assert len(first["metadata"][name]) == 64
        assert first["metadata"][name] == second["metadata"][name]


def test_cli_preserves_reports_and_fails_on_invalid_selection(offline_env, tmp_path, capsys):
    path = tmp_path / "report.json"
    assert main(["--output", str(path)]) == 0
    saved = path.read_bytes()
    assert json.loads(saved)["summary"]["n_queries"] == 20
    assert str(path) in capsys.readouterr().out
    assert path.with_suffix(".md").read_text() == render_markdown(json.loads(saved))
    with pytest.raises(SystemExit) as error:
        main(["--output", str(path)])
    assert error.value.code == 2 and path.read_bytes() == saved
    with pytest.raises(ValueError, match="unknown split"):
        run_evaluation(split="invalid")


def test_unknown_targets_rejected_before_retrieval(offline_env, tmp_path):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps([make_query("wrong", relevant=["KB-NOT-REAL"]).model_dump()]))
    with pytest.raises(ValueError, match="unknown query target"):
        run_evaluation(queries_path=path)


def test_error_exit_keeps_zero_score_denominator_report(offline_env, monkeypatch, tmp_path):
    import evals.retrieval as module

    def broken(*args, **kwargs):
        raise TimeoutError("synthetic transport details not for publication")

    monkeypatch.setattr(module.Retrieval, "knowledge", broken)
    path = tmp_path / "errors.json"
    assert main(["--output", str(path)]) == 1
    report = json.loads(path.read_text())
    assert report["summary"]["n_queries"] == 20
    assert report["summary"]["retrieval_errors"] == 10
    assert report["by_corpus"]["knowledge"]["recall_at_5"] == 0
    assert "synthetic transport details" not in path.read_text()


def test_markdown_collision_never_overwrites_existing_report(offline_env, tmp_path):
    path = tmp_path / "report.json"
    companion = path.with_suffix(".md")
    companion.write_text("previous report")
    with pytest.raises(SystemExit) as error:
        main(["--output", str(path)])
    assert error.value.code == 2
    assert not path.exists() and companion.read_text() == "previous report"
    with pytest.raises(SystemExit):
        main(["--output", str(tmp_path / "bad.md")])


def test_recorded_artifacts_match_actual_seed_only_results(offline_env):
    folder = PROJECT_ROOT / "evals/reports/t13"
    actual = run_evaluation(split="all")
    final = json.loads((folder / "final-all.json").read_text())
    assert len(actual["queries"]) == len(final["queries"])
    # Historical rankings are immutable; added resource text changes BM25 scores.
    assert final["summary"] == summarize(final["queries"])
    assert {q["id"] for q in actual["queries"]} == {q["id"] for q in final["queries"]}
    for filename, split in (("baseline-tuning.json", "tuning"), ("holdout.json", "holdout")):
        previous = json.loads((folder / filename).read_text())
        expected_rows = [q for q in final["queries"] if q["split"] == split]
        assert previous["queries"] == expected_rows
        assert previous["summary"] == summarize(expected_rows)
        for key in ("corpus_sha256", "queries_sha256", "retrieval_source_sha256", "uv_lock_sha256"):
            assert previous["metadata"][key] == final["metadata"][key]
            # Historical source/lock fingerprints are immutable snapshots.
            if key == "queries_sha256":
                assert actual["metadata"][key] == final["metadata"][key]
        assert (folder / filename).with_suffix(".md").read_text() == render_markdown(previous)
    assert (folder / "final-all.md").read_text() == render_markdown(final)
    # Policies gained explicit read-only handoff checks; historical artifacts stay immutable.
    latest = json.loads((folder / "v11-all.json").read_text())
    for key in ("queries", "summary", "groups"):
        assert actual[key] == latest[key]
    # Ranking results remain comparable; source and dependency fingerprints are historical.
    historical_fields = {
        "generated_at",
        "retrieval_source_sha256",
        "storage_source_sha256",
        "uv_lock_sha256",
    }
    assert {k: v for k, v in actual["metadata"].items() if k not in historical_fields} == {
        k: v for k, v in latest["metadata"].items() if k not in historical_fields
    }
    assert (folder / "v11-all.md").read_text() == render_markdown(latest)


def test_split_union_independent_aggregates_and_v2_provenance(offline_env):
    import hashlib

    runs = {split: run_evaluation(split=split) for split in ("tuning", "holdout", "all")}
    ids = {s: {q["id"] for q in r["queries"]} for s, r in runs.items()}
    assert len(ids["tuning"]) == len(ids["holdout"]) == 20
    assert not ids["tuning"] & ids["holdout"]
    assert ids["all"] == ids["tuning"] | ids["holdout"]
    combined = {q["id"]: q for q in runs["all"]["queries"]}
    for split, report in runs.items():
        saved_path = PROJECT_ROOT / f"evals/reports/t13/v11-{split}.json"
        saved = json.loads(saved_path.read_text())
        for key in ("queries", "summary", "groups"):
            assert report[key] == saved[key]
        assert saved_path.with_suffix(".md").read_text() == render_markdown(saved)
        for key, source in (
            ("evaluator_source_sha256", "evals/retrieval.py"),
            ("storage_source_sha256", "src/storage.py"),
        ):
            expected = hashlib.sha256((PROJECT_ROOT / source).read_bytes()).hexdigest()
            assert report["metadata"][key] == expected
            if key == "evaluator_source_sha256":
                assert saved["metadata"][key] == expected
        assert report["metadata"]["schema_version"] == 2
        assert all(q == combined[q["id"]] for q in report["queries"])
        # Recalculate from raw IDs, not from score_ranking()/summarize() or saved metric fields.
        selected_groups = [(report["queries"], report["summary"])]
        for group in report["groups"]:
            selected_groups.append(
                (
                    [
                        q
                        for q in report["queries"]
                        if all(q[key] == group[key] for key in ("split", "corpus", "language"))
                    ],
                    group,
                )
            )
        for rows, summary in selected_groups:
            recalls, reciprocals = [], []
            for q in rows:
                ranked = [r["source_id"] for r in q["retrieved"]][:5]
                relevant = set(q["relevant_ids"])
                recalls.append(len(set(ranked) & relevant) / len(relevant))
                positions = [ranked.index(i) + 1 for i in relevant if i in ranked]
                reciprocals.append(1 / min(positions) if positions else 0)
            assert summary["n_queries"] == len(rows)
            assert summary["recall_at_5"] == pytest.approx(sum(recalls) / len(rows))
            assert summary["mrr_at_5"] == pytest.approx(sum(reciprocals) / len(rows))
