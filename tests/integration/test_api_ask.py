"""Integration tests for the HTTP-to-Agent service contract."""

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    EvidenceLocationType,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import (
    AnswerResponse,
    AskRequest,
    Evidence,
    Fact,
)

client = TestClient(app)


@dataclass
class StubAskService:
    """Represent an Agent that already returns the common contract."""

    response: AnswerResponse
    requests: list[AskRequest] = field(default_factory=list)

    def answer(self, request: AskRequest) -> AnswerResponse:
        self.requests.append(request)
        return self.response


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


def test_ask_returns_contract_valid_answer_with_linked_evidence() -> None:
    evidence = Evidence(
        evidence_id="synthetic-evidence-1",
        doc_id="synthetic-doc-1",
        location_type=EvidenceLocationType.TABLE,
        section_path=["synthetic-section"],
        table_name="synthetic-table",
        row_label="synthetic-revenue",
        column_label="2025",
        raw_text="synthetic-revenue: 1000 synthetic-currency",
    )
    fact = Fact(
        fact_id="synthetic-fact-1",
        company="Synthetic Company",
        metric=MetricConcept.REVENUE,
        fiscal_year=2025,
        fiscal_period="FY",
        accounting_scope=FactAccountingScope.CONSOLIDATED,
        raw_value="1000",
        raw_unit="synthetic-currency",
        evidence_ids=[evidence.evidence_id],
    )
    expected = AnswerResponse(
        answer="Synthetic Company reported synthetic revenue of 1000.",
        status=AnswerabilityStatus.ANSWERABLE,
        facts=[fact],
        evidence=[evidence],
    )
    service = StubAskService(expected)
    app.dependency_overrides[get_ask_service] = lambda: service

    response = client.post(
        "/api/ask",
        json={"question": "[SYNTHETIC] What was 2025 consolidated revenue?"},
    )

    assert response.status_code == 200
    actual = AnswerResponse.model_validate(response.json())
    assert actual == expected
    assert actual.facts[0].evidence_ids == [actual.evidence[0].evidence_id]
    assert service.requests == [
        AskRequest(question="[SYNTHETIC] What was 2025 consolidated revenue?")
    ]


def test_ask_preserves_agent_answerability_reason() -> None:
    expected = AnswerResponse(
        answer="No matching synthetic disclosure was found.",
        status=AnswerabilityStatus.UNANSWERABLE,
        reason=AnswerabilityReason.NOT_FOUND,
    )
    app.dependency_overrides[get_ask_service] = lambda: StubAskService(expected)

    response = client.post("/api/ask", json={"question": "[SYNTHETIC] missing"})

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")


def test_ask_is_unavailable_without_normalized_agent_service() -> None:
    response = client.post("/api/ask", json={"question": "test question"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": "The Agent does not yet expose the common AnswerResponse contract."
    }
