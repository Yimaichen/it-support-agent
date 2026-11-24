"""Shared privacy/ownership regressions retained during Pi convergence."""

import json

import pytest

from src.retrieval import Retrieval
from src.state import StateService
from src.storage import AccessDenied
from src.tools import ToolContext, ToolRunner
from src.web_view import session_view
from tests.scenarios import archive_success


def test_success_archive_is_redacted_private_and_searchable_only_to_owner(offline_env):
    corpus, store, ctx = offline_env
    person = corpus.employees[0]
    stranger = corpus.employees[1].id
    retrieval = Retrieval(corpus, store)
    baseline = retrieval.resolutions(stranger, "VPN")
    assert baseline  # A post-ranking filter could leak through changed public scores.
    record = archive_success(
        offline_env,
        f"privatecase42 {person.name} {person.id} {person.devices[0].id} "
        "person@example.com machine.internal https://private.example/issue 10.2.3.4 "
        "password=synthetic-secret VPN_DNS_FAIL version 6.2",
    )
    document = json.dumps(record["document"])
    for sensitive in [
        person.name,
        person.id,
        person.devices[0].id,
        "person@example.com",
        "machine.internal",
        "https://private.example/issue",
        "10.2.3.4",
        "synthetic-secret",
    ]:
        assert sensitive not in document
    assert "VPN_DNS_FAIL" in document and "6.2" in document
    assert record["visibility"] == "private" and record["owner_user_id"] == ctx.user_id
    assert retrieval.resolutions(stranger, "VPN") == baseline
    assert (
        retrieval.resolutions(ctx.user_id, "privatecase42")[0]["source_id"]
        == record["resolution_id"]
    )
    assert retrieval.resolutions(stranger, "privatecase42") == []
    assert record["resolution_id"] not in {
        r["resolution_id"] for r in store.list_resolutions(stranger)
    }
    with pytest.raises(AccessDenied):
        store.get_resolution(stranger, record["resolution_id"])
    with pytest.raises(AccessDenied):
        session_view(store, stranger, ctx.session_id)


@pytest.mark.parametrize("arguments", [{"user_id": "EMP-002"}, {"session_id": "invented"}])
def test_illegal_context_parameters_do_not_create_observations(offline_env, arguments):
    corpus, store, ctx = offline_env
    state = StateService(store)
    before = state.memory(ctx.user_id, ctx.issue_id).model_dump()
    result = ToolRunner(corpus, store).execute(ctx, "get_current_user", arguments)
    assert result["error"]["type"] == "invalid_arguments"
    assert result["evidence_refs"] == [] and result["sources"] == []
    after = state.memory(ctx.user_id, ctx.issue_id).model_dump()
    assert after.pop("tool_attempts")[-1]["outcome"] == "failed"
    before.pop("tool_attempts")
    assert after == before
    assert store._conn.execute("SELECT count(*) FROM tool_requests").fetchone()[0] == 1
    other = ToolContext(corpus.employees[1].id, ctx.session_id, ctx.issue_id)
    denied = ToolRunner(corpus, store).execute(other, "get_current_user", {})
    assert denied["error"]["type"] == "unavailable" and not denied["evidence_refs"]
