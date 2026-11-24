import pytest

from src.records import Records
from src.retrieval import Retrieval, tokenize
from src.state import StateService
from tests.test_records import resolve


def test_bilingual_retrieval_preserves_codes(support_env):
    corpus, store, _ = support_env
    retrieval = Retrieval(corpus, store)
    doc = corpus.knowledge[0]
    for query in [doc.title, doc.title_zh]:
        found = retrieval.knowledge(query)
        assert doc.id in {r["source_id"] for r in found}
        assert all(r["score_type"] == "bm25_not_confidence" for r in found)
    assert "vpn_dns_fail" in tokenize("VPN_DNS_FAIL version 6.2")
    assert "6.2" in tokenize("VPN_DNS_FAIL version 6.2")
    for query in ["VPN 客户端版本", "VPN client version"]:
        assert any(r["source_id"].startswith("KB-CON-") for r in retrieval.knowledge(query))
    assert retrieval.knowledge("zzquuxnonexistent927364") == []
    assert retrieval.knowledge("") == []
    with pytest.raises(ValueError):
        retrieval.knowledge("VPN", top_k=11)


def test_expiry_is_visible_not_silently_current(support_env):
    corpus, store, _ = support_env
    retrieval = Retrieval(corpus, store)
    expired = next(
        d for d in corpus.knowledge if d.valid_until and d.valid_until < corpus.reference_time
    )
    result = next(
        r for r in retrieval.knowledge(expired.title, top_k=10) if r["source_id"] == expired.id
    )
    assert result["expired"] is True
    assert result["source_at"] and result["environment"]


def test_live_index_refresh_and_prefilter_ownership(support_env):
    corpus, store, ctx = support_env
    retrieval = Retrieval(corpus, store)
    draft = resolve(support_env)
    draft["symptoms"] = "privatetokenuniquexyz VPN issue"
    record = Records(store, corpus).archive(ctx.user_id, ctx.issue_id, draft)
    own = retrieval.resolutions(ctx.user_id, "privatetokenuniquexyz")
    assert own[0]["source_id"] == record["resolution_id"]
    assert retrieval.resolutions(corpus.employees[1].id, "privatetokenuniquexyz") == []
    msg = store.append_message(
        ctx.user_id, ctx.session_id, {"role": "user", "content": "not fixed"}
    )
    StateService(store).reopen(ctx.user_id, ctx.issue_id, msg["message_id"])
    assert retrieval.resolutions(ctx.user_id, "privatetokenuniquexyz") == []
