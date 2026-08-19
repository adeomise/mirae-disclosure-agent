"""Structural evaluation skeleton for the Q01, Q02, and Q13 slices."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from tests.helpers.synthetic_corpus import (
    Q13_TITLE,
    write_q01_periodic_xml,
    write_q02_event_document,
    write_q13_event_document,
)

from disclosure_agent.agent.router import (
    Q01VerticalSliceRouter,
    Q02VerticalSliceRouter,
    Q13VerticalSliceRouter,
)
from disclosure_agent.api.dependencies import get_development_fixture_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.api.mock_service import DevelopmentFixtureAskService
from disclosure_agent.common.schemas import AnswerResponse
from disclosure_agent.event.service import EventQ02Service, EventQ13Service
from disclosure_agent.periodic.service import PeriodicQ01Service

CASE_DIR = Path(__file__).parent / "cases"
CASE_FILES = sorted(CASE_DIR.glob("*.json"))
client = TestClient(app)


class ExpectedFact(BaseModel):
    metric: str
    accounting_scope: str


class ExpectedResult(BaseModel):
    status: str
    reason: str | None = None
    fact: ExpectedFact | None = None
    event_type: str | None = None
    required_event_attributes: list[str] = Field(default_factory=list)
    requires_event_history: bool = False
    chain_complete: bool | None = None
    requires_event_link: bool = False
    link_resolution_status: str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    min_evidence_count: int = 0


class EvaluationCase(BaseModel):
    case_id: str
    question: str
    expected: ExpectedResult


def load_case(path: Path) -> EvaluationCase:
    return EvaluationCase.model_validate_json(path.read_text(encoding="utf-8"))


def build_service(corpus_dir: Path) -> DevelopmentFixtureAskService:
    return DevelopmentFixtureAskService(
        q01_router=Q01VerticalSliceRouter(PeriodicQ01Service(corpus_dir)),
        q02_router=Q02VerticalSliceRouter(EventQ02Service(corpus_dir)),
        q13_router=Q13VerticalSliceRouter(EventQ13Service(corpus_dir)),
    )


def populate_synthetic_corpus(case_id: str, corpus_dir: Path) -> None:
    if case_id == "Q01":
        write_q01_periodic_xml(corpus_dir)
        return
    if case_id == "Q02":
        write_q02_event_document(corpus_dir)
        return

    original_count = 2 if case_id == "Q13_AMBIGUOUS_LINK" else 1
    for index in range(original_count):
        write_q13_event_document(
            corpus_dir,
            filename=f"original-{index}.xml",
            receipt_date="2025-09-15",
            amount="100",
            end_date="2026-01-01",
        )

    incomplete = case_id == "Q13_INCOMPLETE_CHAIN"
    related_date = "2025-09-25" if incomplete else "2025-09-15"
    write_q13_event_document(
        corpus_dir,
        filename="correction.xml",
        receipt_date="2025-10-01",
        amount="120",
        end_date="2026-06-01",
        correction=True,
        original_filing_date="2025-09-15",
        chain_complete=not incomplete,
        related_disclosures=[
            {
                "document_title": Q13_TITLE,
                "filing_date": related_date,
            }
        ],
    )


def ask_with_corpus(case: EvaluationCase, corpus_dir: Path) -> AnswerResponse:
    service = build_service(corpus_dir)
    app.dependency_overrides[get_development_fixture_ask_service] = lambda: service
    try:
        response = client.post("/api/ask", json={"question": case.question})
        assert response.status_code == 200
        return AnswerResponse.model_validate(response.json())
    finally:
        app.dependency_overrides.pop(get_development_fixture_ask_service, None)


def assert_fact(actual: AnswerResponse, expected: ExpectedResult) -> None:
    if expected.fact is None:
        return
    fact = next(
        item for item in actual.facts if item.metric.value == expected.fact.metric
    )
    assert fact.accounting_scope.value == expected.fact.accounting_scope
    evidence_ids = {item.evidence_id for item in actual.evidence}
    assert fact.evidence_ids
    assert set(fact.evidence_ids) <= evidence_ids


def assert_event(actual: AnswerResponse, expected: ExpectedResult) -> None:
    if expected.event_type is None:
        return
    event = next(
        item for item in actual.events if item.event_type.value == expected.event_type
    )
    assert set(expected.required_event_attributes) <= event.attributes.keys()


def assert_event_history(actual: AnswerResponse, expected: ExpectedResult) -> None:
    if not expected.requires_event_history:
        assert not actual.event_histories
        return
    assert actual.event_histories
    history = actual.event_histories[0]
    assert history.chain_complete is expected.chain_complete
    assert actual.events
    assert {event.event_id for event in actual.events} == {history.event_id}
    changes = {change.field_name: change for change in history.changes}
    assert set(expected.changed_fields) <= changes.keys()

    evidence = {item.evidence_id: item for item in actual.evidence}
    for field_name in expected.changed_fields:
        change = changes[field_name]
        assert change.raw_field
        assert change.before_evidence_id in evidence
        assert change.after_evidence_id in evidence
        assert evidence[change.before_evidence_id].doc_id != evidence[
            change.after_evidence_id
        ].doc_id


def assert_event_links(actual: AnswerResponse, expected: ExpectedResult) -> None:
    if not expected.requires_event_link:
        assert not actual.event_links
        return
    assert actual.event_links
    assert all(link.relation.value == "correction_of" for link in actual.event_links)
    statuses = {link.resolution_status.value for link in actual.event_links}
    assert expected.link_resolution_status in statuses
    if expected.link_resolution_status == "ambiguous":
        assert "resolved" not in statuses
        ambiguous = next(
            link
            for link in actual.event_links
            if link.resolution_status.value == "ambiguous"
        )
        assert ambiguous.resolved_doc_id is None
        assert len(ambiguous.candidate_doc_ids) >= 2


@pytest.mark.parametrize("case_path", CASE_FILES, ids=lambda path: path.stem)
def test_vertical_slice_evaluation(case_path: Path, tmp_path: Path) -> None:
    case = load_case(case_path)
    populate_synthetic_corpus(case.case_id, tmp_path)

    actual = ask_with_corpus(case, tmp_path)

    assert actual.status.value == case.expected.status
    actual_reason = actual.reason.value if actual.reason is not None else None
    assert actual_reason == case.expected.reason
    assert len(actual.evidence) >= case.expected.min_evidence_count
    assert_fact(actual, case.expected)
    assert_event(actual, case.expected)
    assert_event_history(actual, case.expected)
    assert_event_links(actual, case.expected)
