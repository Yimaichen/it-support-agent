from datetime import datetime

import pytest
from pydantic import ValidationError

from src.data import Category, Corpus, is_expired, load_corpus, read_markdown


@pytest.fixture
def corpus():
    return load_corpus()


def test_complete_corpus_and_bilingual_metadata(corpus):
    assert len(corpus.knowledge) == 21
    assert len(corpus.resolutions) == 10
    assert len(corpus.employees) == 5
    assert len(corpus.services) == 8
    assert len(corpus.policies) == 11
    assert {d.category for d in corpus.knowledge} == set(Category)
    assert all(d.title_zh and d.tags and d.policy_refs for d in corpus.knowledge)
    assert all(r.outcome == "resolved" and r.synthetic for r in corpus.resolutions)


def test_reference_time_and_intentional_conflicts(corpus):
    assert corpus.reference_time.isoformat() == "2026-04-21T12:00:00+00:00"
    expired = [d.id for d in corpus.knowledge if is_expired(d, corpus.reference_time)]
    assert expired == ["KB-APP-004"]
    assert "5.0" in corpus.resolutions[2].body
    vpn = next(s for s in corpus.services if s.id == "vpn")
    assert vpn.supported_versions == ["6.2"]
    crm = next(s for s in corpus.services if s.id == "salesforce")
    assert crm.region_status["Chicago"] == "degraded"
    assert crm.region_status["London"] == "healthy"
    with pytest.raises(ValueError):
        is_expired(corpus.knowledge[0], datetime(2026, 4, 21))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c["employees"].append(c["employees"][0]),
        lambda c: c["knowledge"][0].update(service_ids=["missing"]),
        lambda c: c["knowledge"][0].update(policy_refs=["missing"]),
        lambda c: c["resolutions"][0].update(evidence_refs=["missing"]),
        lambda c: c["resolutions"][0].update(outcome="escalated"),
        lambda c: c["services"][0].update(checked_at="2026-04-21T12:00:00"),
        lambda c: c["employees"][0].update(updated_at="2030-01-01T00:00:00Z"),
        lambda c: c["changes"][0].update(ended_at="2020-01-01T00:00:00Z"),
    ],
)
def test_bad_metadata_or_dangling_references_rejected(corpus, mutation):
    raw = corpus.model_dump(mode="json")
    mutation(raw)
    with pytest.raises(ValidationError):
        Corpus.model_validate(raw)


def test_invalid_markdown_fails(tmp_path):
    path = tmp_path / "broken.md"
    path.write_text("# missing metadata")
    with pytest.raises(ValueError, match="front matter"):
        read_markdown(path)


def test_employee_multiple_devices_are_not_an_implicit_active_device(corpus):
    alex = next(e for e in corpus.employees if e.id == "EMP-001")
    assert [(d.id, d.os) for d in alex.devices] == [
        ("DEMO-DEVICE-001", "Windows 11"),
        ("DEMO-DEVICE-006", "macOS 15"),
    ]
    # The snapshot lists devices; it does not silently choose the affected one.
    assert "device" not in alex.model_dump()
    assert "os" not in alex.model_dump()
    assert all(d.updated_at <= alex.updated_at for d in alex.devices)


def test_employee_roles_have_exact_project_or_database_scope(corpus):
    research = next(e for e in corpus.employees if e.id == "EMP-002")
    finance = next(e for e in corpus.employees if e.id == "EMP-004")
    research_tableau = [p for p in research.permissions if p.service_id == "tableau"]
    finance_tableau = [p for p in finance.permissions if p.service_id == "tableau"]
    assert [(p.resource_type, p.resource_id, p.role) for p in research_tableau] == [
        ("project", "research_reporting", "viewer")
    ]
    assert [(p.resource_type, p.resource_id, p.role) for p in finance_tableau] == [
        ("project", "finance_reporting", "viewer")
    ]
    assert all(p.resource_id != "finance_reporting" for p in research_tableau)
    assert all(
        p.resource_type == "database" and "nonprod" in p.resource_id
        for e in corpus.employees
        for p in e.permissions
        if p.service_id == "data_pipeline"
    )


def test_empty_directory_details_are_allowed_without_inventing_records(corpus):
    raw = corpus.model_dump(mode="json")
    raw["employees"][0].update(devices=[], permissions=[])
    employee = Corpus.model_validate(raw).employees[0]
    assert employee.devices == []
    assert employee.permissions == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda e: e["devices"].append(e["devices"][0].copy()),
        lambda e: e["permissions"].append(e["permissions"][0].copy()),
        lambda e: e["devices"][0].update(id="  "),
        lambda e: e["devices"][0].update(os=""),
        lambda e: e["devices"][0].update(updated_at="2026-04-22T12:00:00Z"),
        lambda e: e["devices"][0].update(updated_at="2026-04-21T12:00:00"),
        lambda e: e["permissions"][0].update(service_id="missing"),
        lambda e: e["permissions"][0].update(resource_type="anything"),
        lambda e: e["permissions"][0].update(resource_id=" "),
        lambda e: e["permissions"][0].update(resource_id="*"),
        lambda e: e["permissions"][0].update(role=" "),
        lambda e: e["permissions"][0].update(updated_at="2026-04-22T12:00:00Z"),
    ],
)
def test_invalid_employee_devices_and_scoped_permissions_rejected(corpus, mutation):
    raw = corpus.model_dump(mode="json")
    mutation(raw["employees"][0])
    with pytest.raises(ValidationError):
        Corpus.model_validate(raw)


def test_legacy_employee_shape_rejected_instead_of_silently_broadening_access(corpus):
    raw = corpus.model_dump(mode="json")
    employee = raw["employees"][0]
    employee.pop("devices")
    employee.update(device="DEMO-DEVICE-001", os="Windows 11", permissions={"tableau": ["viewer"]})
    with pytest.raises(ValidationError):
        Corpus.model_validate(raw)
