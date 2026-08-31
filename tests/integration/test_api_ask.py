"""Integration tests for the HTTP-to-Agent service contract."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.api.service import (
    BLOCKED_PUBLIC_MESSAGE,
    FAILED_PUBLIC_MESSAGE,
    AgentAskService,
    MissingExecutionIssueError,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    AnswerResponse,
    AskRequest,
    Evidence,
    ExecutionIssue,
    Fact,
)

client = TestClient(app)

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"


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


def runtime_service(result: AgentExecutionResult) -> AgentAskService:
    def fake_answer_question(question: str, **_: object) -> AgentExecutionResult:
        assert question
        return result

    return AgentAskService(
        corpus_root=Path("unused-synthetic-corpus"),
        answer_question_fn=fake_answer_question,
    )


def answerable_execution_result() -> AgentExecutionResult:
    evidence = Evidence(
        evidence_id="synthetic-evidence-1",
        doc_id="synthetic-doc-1",
        location_type=EvidenceLocationType.TABLE,
    )
    fact = Fact(
        fact_id="synthetic-fact-1",
        company="Synthetic Company",
        metric=MetricConcept.REVENUE,
        fiscal_year=2025,
        accounting_scope=FactAccountingScope.CONSOLIDATED,
        raw_value="1000",
        raw_unit="synthetic-currency",
        evidence_ids=[evidence.evidence_id],
    )
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[fact],
        evidence=[evidence],
    )
def require_actual_corpus() -> None:
    raw_corpus_root = os.environ.get(CORPUS_ROOT_ENV)
    if not raw_corpus_root:
        pytest.skip(f"{CORPUS_ROOT_ENV} is not set")

    corpus_root = Path(raw_corpus_root).expanduser()
    if not corpus_root.is_dir():
        pytest.skip(f"corpus root is not a directory: {corpus_root}")

    raw_manifest = os.environ.get(CORPUS_MANIFEST_ENV)
    manifest_path = (
        Path(raw_manifest).expanduser()
        if raw_manifest is not None
        else corpus_root / "manifest.jsonl"
    )
    if not manifest_path.is_file():
        pytest.skip(f"corpus manifest does not exist: {manifest_path}")


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


def test_completed_answerable_agent_result_returns_200() -> None:
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(
        answerable_execution_result()
    )

    response = client.post("/api/ask", json={"question": "synthetic answerable"})

    assert response.status_code == 200
    assert response.json()["status"] == "ANSWERABLE"
    assert len(response.json()["facts"]) == 1


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (AnswerabilityStatus.UNANSWERABLE, AnswerabilityReason.NOT_FOUND),
        (AnswerabilityStatus.UNRESOLVED, AnswerabilityReason.AMBIGUOUS_MATCH),
    ],
)
def test_completed_non_answerable_agent_result_returns_200(
    status: AnswerabilityStatus,
    reason: AnswerabilityReason,
) -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=status,
        answerability_reason=reason,
    )
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic unavailable"})

    assert response.status_code == 200
    assert response.json()["status"] == status.value
    assert response.json()["reason"] == reason.value


@pytest.mark.parametrize(
    ("execution_status", "http_status"),
    [
        (ExecutionStatus.BLOCKED, 422),
        (ExecutionStatus.FAILED, 500),
    ],
)
def test_non_completed_agent_result_returns_error_envelope(
    execution_status: ExecutionStatus,
    http_status: int,
) -> None:
    issue = ExecutionIssue(
        stage="synthetic-stage",
        reason="synthetic-reason",
        detail="synthetic-detail",
    )
    result = AgentExecutionResult(
        execution_status=execution_status,
        execution_issue=issue,
    )
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic stopped"})

    assert response.status_code == http_status
    assert response.json() == {
        "execution_status": execution_status.value,
        "error": {
            "stage": issue.stage,
            "reason": issue.reason,
            "message": (
                BLOCKED_PUBLIC_MESSAGE
                if execution_status is ExecutionStatus.BLOCKED
                else FAILED_PUBLIC_MESSAGE
            ),
        },
    }
    assert issue.detail not in response.text


@pytest.mark.parametrize("execution_status", [ExecutionStatus.BLOCKED, ExecutionStatus.FAILED])
def test_non_completed_result_without_execution_issue_fails_fast(
    execution_status: ExecutionStatus,
) -> None:
    service = runtime_service(AgentExecutionResult(execution_status=execution_status))

    with pytest.raises(MissingExecutionIssueError, match="has no execution_issue"):
        service.answer(AskRequest(question="synthetic invalid result"))


def test_missing_execution_issue_is_sanitized_at_the_http_boundary() -> None:
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(
        AgentExecutionResult(execution_status=ExecutionStatus.FAILED)
    )

    response = client.post("/api/ask", json={"question": "synthetic invalid result"})

    assert response.status_code == 500
    assert response.json() == {
        "execution_status": "FAILED",
        "error": {
            "stage": "runtime",
            "reason": "unexpected_failure",
            "message": FAILED_PUBLIC_MESSAGE,
        },
    }
    assert "has no execution_issue" not in response.text


def test_unexpected_runtime_exception_returns_a_safe_failed_envelope() -> None:
    sensitive_detail = "sensitive path /private/corpus/source.xml"

    def raise_unexpected_error(question: str, **_: object) -> AgentExecutionResult:
        assert question
        raise RuntimeError(sensitive_detail)

    app.dependency_overrides[get_ask_service] = lambda: AgentAskService(
        corpus_root=Path("unused-synthetic-corpus"),
        answer_question_fn=raise_unexpected_error,
    )

    response = client.post("/api/ask", json={"question": "synthetic crash"})

    assert response.status_code == 500
    assert response.json() == {
        "execution_status": "FAILED",
        "error": {
            "stage": "runtime",
            "reason": "unexpected_failure",
            "message": FAILED_PUBLIC_MESSAGE,
        },
    }
    assert sensitive_detail not in response.text


def test_request_validation_422_remains_distinct_from_agent_blocked_422() -> None:
    response = client.post("/api/ask", json={})

    assert response.status_code == 422
    payload = response.json()
    assert "detail" in payload
    assert payload["detail"][0]["loc"] == ["body", "question"]
    assert "execution_status" not in payload
    assert "error" not in payload


def test_q18_runs_through_the_real_api_dependency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DISCLOSURE_CORPUS_ROOT", str(tmp_path / "unused-corpus"))

    response = client.post(
        "/api/ask",
        json={"question": "지금 한화오션 주식 사도 될까?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "UNANSWERABLE"
    assert response.json()["reason"] == "out_of_scope_policy"
    assert response.json()["facts"] == []
    assert response.json()["evidence"] == []


@pytest.mark.corpus
def test_q01_runs_through_actual_corpus_http_api() -> None:
    require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post(
        "/api/ask",
        json={"question": "한미반도체 2025년 연결 기준 매출액이 얼마야?"},
    )

    assert response.status_code == 200
    actual = AnswerResponse.model_validate(response.json())
    assert actual.status is AnswerabilityStatus.ANSWERABLE
    assert actual.reason is None
    assert len(actual.facts) == 1

    fact = actual.facts[0]
    assert fact.metric is MetricConcept.REVENUE
    assert fact.fiscal_year == 2025
    assert fact.accounting_scope is FactAccountingScope.CONSOLIDATED
    assert actual.evidence

    response_evidence_ids = {item.evidence_id for item in actual.evidence}
    assert set(fact.evidence_ids) <= response_evidence_ids


@pytest.mark.corpus
def test_q13_runs_through_actual_corpus_http_api() -> None:
    require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post(
        "/api/ask",
        json={
            "question": "HD현대일렉트릭이 2024년 8월에 공시한 공급계약, 나중에 바뀐 내용 있어?"
        },
    )

    assert response.status_code == 200
    actual = AnswerResponse.model_validate(response.json())
    assert actual.status is AnswerabilityStatus.ANSWERABLE
    assert actual.reason is None
    assert len(actual.event_histories) == 1

    history = actual.event_histories[0]
    assert history.chain_complete is True
    assert len(actual.event_links) >= 2
    assert actual.evidence

    response_evidence_ids = {item.evidence_id for item in actual.evidence}
    for change in history.changes:
        for evidence_id in (
            change.before_evidence_id,
            change.after_evidence_id,
        ):
            if evidence_id is not None:
                assert evidence_id in response_evidence_ids
