"""Offline, seed-only BM25 evaluation. Does not instantiate an LLM or open runtime SQLite."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

from evals.contracts import EvaluationSuite, Query
from src.config import PROJECT_ROOT
from src.data import load_corpus
from src.retrieval import Retrieval
from src.storage import Store

K = 5


def score_ranking(retrieved_ids: list[str], relevant_ids: list[str]) -> dict:
    """Recall and reciprocal rank at K=5, including empty results."""
    relevant = set(relevant_ids)
    if not relevant or any(not isinstance(i, str) or not i for i in relevant):
        raise ValueError("nonempty relevant IDs required")
    if len(set(retrieved_ids)) != len(retrieved_ids):
        raise ValueError("retriever returned duplicate IDs")
    ranked = retrieved_ids[:K]
    hits = [i for i in ranked if i in relevant]
    rank = next((n for n, i in enumerate(ranked, 1) if i in relevant), None)
    return {
        "recall_at_5": len(hits) / len(relevant),
        "reciprocal_rank_at_5": 1 / rank if rank else 0.0,
        "first_relevant_rank": rank,
        "matched_ids": hits,
        "missing_relevant_ids": sorted(relevant - set(hits)),
        "unjudged_ids_at_5": [i for i in ranked if i not in relevant],
    }


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    return {
        "n_queries": n,
        "hit_queries": sum(r["first_relevant_rank"] is not None for r in rows),
        "complete_recall_queries": sum(r["recall_at_5"] == 1 for r in rows),
        "recall_at_5": sum(r["recall_at_5"] for r in rows) / n if n else None,
        "mrr_at_5": sum(r["reciprocal_rank_at_5"] for r in rows) / n if n else None,
        "retrieval_errors": sum(r["error"] is not None for r in rows),
    }


def evaluate_queries(queries: list[Query], retrieval: Retrieval, user_id: str) -> dict:
    rows = []
    for q in queries:
        error = None
        try:
            found = (
                retrieval.knowledge(q.query, top_k=K)
                if q.corpus == "knowledge"
                else retrieval.resolutions(user_id, q.query, top_k=K)
            )
            ranking = score_ranking([r["source_id"] for r in found], q.relevant_ids)
        except Exception as exc:
            # A broken query still counts in the denominator; never emit exception text.
            error, found = type(exc).__name__, []
            ranking = score_ranking([], q.relevant_ids)
        rows.append(
            {
                **q.model_dump(),
                **ranking,
                "error": error,
                "retrieved": [
                    {
                        "rank": rank,
                        "source_id": r["source_id"],
                        "score": r["score"],
                        "source_type": r["source_type"],
                        "expired": r.get("expired"),
                    }
                    for rank, r in enumerate(found, 1)
                ],
            }
        )
    groups = []
    for split in sorted({r["split"] for r in rows}):
        for corpus in ("knowledge", "resolutions"):
            for language in ("en", "zh"):
                selected = [
                    r
                    for r in rows
                    if (r["split"], r["corpus"], r["language"]) == (split, corpus, language)
                ]
                groups.append(
                    {"split": split, "corpus": corpus, "language": language, **summarize(selected)}
                )
    return {
        "summary": summarize(rows),
        "by_split": {
            s: summarize([r for r in rows if r["split"] == s])
            for s in sorted({r["split"] for r in rows})
        },
        "by_corpus": {
            c: summarize([r for r in rows if r["corpus"] == c])
            for c in ("knowledge", "resolutions")
        },
        "by_language": {
            lang: summarize([r for r in rows if r["language"] == lang]) for lang in ("en", "zh")
        },
        "groups": groups,
        "incomplete_recall_query_ids": [r["id"] for r in rows if r["recall_at_5"] < 1],
        "non_top1_query_ids": [r["id"] for r in rows if r["first_relevant_rank"] != 1],
        "queries": rows,
    }


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def run_evaluation(
    *,
    split: str = "tuning",
    data_dir: Path = PROJECT_ROOT / "data",
    queries_path: Path = PROJECT_ROOT / "evals/retrieval_queries.json",
) -> dict:
    if split not in {"tuning", "holdout", "all"}:
        raise ValueError("unknown split")
    corpus = load_corpus(data_dir)
    raw_queries = queries_path.read_bytes()
    suite = EvaluationSuite(queries=json.loads(raw_queries), scenarios=[])
    suite.validate_against(corpus)
    queries = [q for q in suite.queries if split == "all" or q.split == split]
    if not queries:
        raise ValueError("selected split has no queries")
    # No employee sessions/private live cases, model client, dotenv or runtime DB access.
    with TemporaryDirectory(prefix="it-retrieval-eval-") as folder:
        store = Store(Path(folder) / "seed.sqlite3", clock=lambda: corpus.reference_time)
        try:
            imported = store.import_resolutions(corpus.resolutions)
            result = evaluate_queries(queries, Retrieval(corpus, store), corpus.employees[0].id)
        finally:
            store.close()
    snapshot = corpus.model_dump_json().encode()
    return {
        "metadata": {
            "schema_version": 2,
            "evaluation": "actual_offline_bm25_not_llm",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "split": split,
            "k": K,
            "recall_definition": "mean(|relevant intersect top5| / |relevant|), macro per query",
            "mrr_definition": "mean(1 / first relevant rank within top5), zero for absent/error",
            "relevance_note": "Unjudged candidates are not proven irrelevant; labels may be incomplete.",
            "holdout_note": "Predefined paraphrases, not blind/new-intent or unseen-corpus evaluation.",
            "data_reference_time": corpus.reference_time.isoformat(),
            "knowledge_documents": len(corpus.knowledge),
            "shared_seed_resolutions": imported,
            "employee_context": corpus.employees[0].id,
            "database": "fresh_temporary_seed_only",
            "python": platform.python_version(),
            "rank_bm25_version": version("rank-bm25"),
            "corpus_sha256": _digest(snapshot),
            "queries_sha256": _digest(raw_queries),
            "retrieval_source_sha256": _digest((PROJECT_ROOT / "src/retrieval.py").read_bytes()),
            "uv_lock_sha256": _digest((PROJECT_ROOT / "uv.lock").read_bytes()),
            "evaluator_source_sha256": _digest(Path(__file__).read_bytes()),
            "storage_source_sha256": _digest((PROJECT_ROOT / "src/storage.py").read_bytes()),
        },
        **result,
    }


def render_markdown(report: dict) -> str:
    """Human-readable projection of computed rows; JSON remains the complete artifact."""

    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("<", "&lt;")

    def number(value):
        return "n/a" if value is None else f"{value:.6f}"

    meta, summary = report["metadata"], report["summary"]
    lines = [
        "# Offline BM25 retrieval results",
        "",
        f"Split: `{meta['split']}`; cutoff: {meta['k']}; "
        f"queries: {summary['n_queries']}; retrieval errors: {summary['retrieval_errors']}.",
        f"Recall@5: **{number(summary['recall_at_5'])}**; "
        f"MRR@5: **{number(summary['mrr_at_5'])}**.",
        "",
        "Actual offline retrieval, not model evaluation. Fresh seed-only temporary SQLite.",
        "Macro recall includes all relevant labels; MRR is truncated at 5. Empty/error queries score zero.",
        "Predefined paraphrases are not blind/new-intent evaluation. Unjudged candidates are not proven irrelevant.",
        "",
        "| Split | Corpus | Language | N | Hits@5 | Recall@5 | MRR@5 | Errors |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for group in report["groups"]:
        lines.append(
            f"| {group['split']} | {group['corpus']} | {group['language']} | "
            f"{group['n_queries']} | {group['hit_queries']} | "
            f"{number(group['recall_at_5'])} | {number(group['mrr_at_5'])} | {group['retrieval_errors']} |"
        )
    lines += ["", "## Incomplete recall / errors", ""]
    misses = [q for q in report["queries"] if q["recall_at_5"] < 1 or q["error"]]
    if not misses:
        lines.append("None in this query set.")
    for q in misses:
        lines.append(
            f"- {cell(q['id'])}: missing {cell(q['missing_relevant_ids'])}; error {cell(q['error'])}."
        )
    lines += [
        "",
        "## Non-top-1 results (not all are top-5 misses)",
        "",
        "| Query ID | Query | First relevant rank | Retrieved IDs in order |",
        "|---|---|---:|---|",
    ]
    for q in report["queries"]:
        if q["first_relevant_rank"] != 1:
            ids = ", ".join(r["source_id"] for r in q["retrieved"])
            lines.append(
                f"| {cell(q['id'])} | {cell(q['query'])} | {q['first_relevant_rank']} | {cell(ids)} |"
            )
    lines += ["", "## Snapshot fingerprints", ""]
    for key in (
        "data_reference_time",
        "corpus_sha256",
        "queries_sha256",
        "retrieval_source_sha256",
        "uv_lock_sha256",
        "evaluator_source_sha256",
        "storage_source_sha256",
    ):
        if key in meta:  # Preserve the projection of historical v1 artifacts.
            lines.append(f"- {key}: `{meta[key]}`")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["tuning", "holdout", "all"], default="tuning")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument(
        "--queries", type=Path, default=PROJECT_ROOT / "evals/retrieval_queries.json"
    )
    parser.add_argument(
        "--output", type=Path, help="New report path; existing files are never overwritten"
    )
    args = parser.parse_args(argv)
    if args.output and args.output.suffix != ".json":
        parser.error("output must have .json suffix; a companion .md report is also written")
    if args.output and (args.output.exists() or args.output.with_suffix(".md").exists()):
        parser.error("output exists; choose a new path to preserve previous results")
    report = run_evaluation(split=args.split, data_dir=args.data_dir, queries_path=args.queries)
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(text)
        with args.output.with_suffix(".md").open("x", encoding="utf-8") as output:
            output.write(render_markdown(report))
        print(json.dumps({"output": str(args.output), **report["summary"]}, ensure_ascii=False))
    else:
        print(text, end="")
    return 1 if report["summary"]["retrieval_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
