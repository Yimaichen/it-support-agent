"""Small local multilingual BM25. Rebuild from authoritative data; never cache across users."""

from __future__ import annotations

import re
from collections import Counter

from rank_bm25 import BM25Plus

from src.data import Corpus, is_expired
from src.storage import Store


def tokenize(text: str) -> list[str]:
    text = text.casefold()
    latin = re.findall(r"[a-z0-9]+(?:[_.-][a-z0-9]+)*", text)
    chinese = re.findall(r"[\u3400-\u9fff]+", text)
    return latin + [
        piece
        for run in chinese
        for piece in (*list(run), *[run[i : i + 2] for i in range(len(run) - 1)])
    ]


def resolution_origin(record: dict) -> dict:
    """Ownership is established by Store access checks, never inferred from prose."""
    personal = record["visibility"] == "private"
    basis = record["document"].get("result_basis", "synthetic_seed")
    return {
        "visibility": record["visibility"],
        "result_basis": basis,
        "belongs_to_current_employee": personal,
        "usage_note": (
            "This employee's private historical case. Assistant suggestions are not proof of execution."
            if personal
            else "Public reference example; NOT this employee's past experience. "
            + (
                "Synthetic seed, not a real employee incident."
                if basis == "synthetic_seed"
                else "Check its recorded basis."
            )
        ),
    }


class Retrieval:
    def __init__(self, corpus: Corpus, store: Store):
        self.corpus, self.store = corpus, store

    def terms(self, text: str) -> list[str]:
        expanded = [text]
        for canonical, aliases in self.corpus.aliases.items():
            if any(
                re.search(r"(?<![a-z0-9_])" + re.escape(alias) + r"(?![a-z0-9_])", text, re.I)
                if re.search(r"[a-zA-Z]", alias)
                else alias in text
                for alias in [canonical, *aliases]
            ):
                expanded.extend([canonical, *aliases])
        return tokenize(" ".join(expanded))

    def _search(self, query: str, items: list[dict], top_k: int) -> list[dict]:
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            return []
        if not 1 <= top_k <= 10:
            raise ValueError("top_k must be between 1 and 10")
        if not items:
            return []
        terms = self.terms(query)
        if not terms:
            return []
        texts = [self.terms(item.pop("_search")) or ["__empty__"] for item in items]
        # BM25Plus avoids negative/zero IDF collapsing one-document private corpora.
        scores = BM25Plus(texts).get_scores(terms)
        candidates = [
            (float(score), item)
            for score, item, words in zip(scores, items, texts)
            if set(terms) & Counter(words).keys()
        ]
        candidates.sort(key=lambda pair: (-pair[0], pair[1]["source_id"]))
        return [
            {**item, "score": score, "score_type": "bm25_not_confidence"}
            for score, item in candidates[:top_k]
        ]

    def knowledge(self, query: str, *, top_k: int = 5, service_id: str | None = None) -> list[dict]:
        items = []
        for doc in self.corpus.knowledge:
            if service_id and service_id not in doc.service_ids:
                continue
            metadata = " ".join([doc.title, doc.title_zh, *doc.service_ids, *doc.tags])
            items.append(
                {
                    "source_id": doc.id,
                    "source_type": "guidance",
                    "title": doc.title,
                    "title_zh": doc.title_zh,
                    "category": doc.category,
                    "service_ids": doc.service_ids,
                    "policy_refs": doc.policy_refs,
                    "source_at": doc.updated_at.isoformat(),
                    "environment": doc.environment,
                    "valid_until": doc.valid_until.isoformat() if doc.valid_until else None,
                    "expired": is_expired(doc, self.corpus.reference_time),
                    "snippet": doc.body[:1200],
                    "_search": (metadata + " ") * 3 + doc.body,
                }
            )
        return self._search(query, items, top_k)

    def resolutions(
        self, user_id: str, query: str, *, top_k: int = 5, service_id: str | None = None
    ) -> list[dict]:
        # Ownership and validity filtering happen BEFORE candidate construction / ranking.
        items = []
        for record in self.store.list_resolutions(user_id):
            doc = record["document"]
            if service_id and service_id not in doc.get("service_ids", []):
                continue
            body = " ".join([doc.get("symptoms", ""), doc["body"], doc.get("result", "")])
            metadata = " ".join(
                [
                    doc.get("title", ""),
                    doc.get("title_zh", ""),
                    *doc.get("service_ids", []),
                    *doc.get("tags", []),
                ]
            )
            items.append(
                {
                    "source_id": record["resolution_id"],
                    "source_type": "historical",
                    "title": doc.get("title", ""),
                    "source_at": doc.get("resolved_at", record["created_at"]),
                    "environment": doc.get(
                        "environment",
                        "Historical employee-reported case; recheck current environment.",
                    ),
                    "service_ids": doc.get("service_ids", []),
                    "resolution_id": record["resolution_id"],
                    **resolution_origin(record),
                    "valid": record["valid"],
                    "summary": doc.get("summary", doc.get("symptoms", ""))[:300],
                    "result": doc.get("result", "")[:240],
                    "read_with": "read_resolution",
                    "_search": (metadata + " ") * 3 + body,
                }
            )
        # Independent quotas: public examples cannot crowd out personal experience.
        # top_k is a per-group maximum (up to 2 * top_k total).
        personal = [item for item in items if item["belongs_to_current_employee"]]
        public = [item for item in items if not item["belongs_to_current_employee"]]
        return self._search(query, personal, top_k) + self._search(query, public, top_k)
