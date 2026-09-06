"""Regression tests for the organizer-facing GET /answer adapter.

주최측(미래에셋 AI Festival) 평가 규격: GET /answer?question_id=...&question=... 가
question_id/question/retrieved_context/think_trace/answer 5개 string 필드를 정확히
돌려주는지, 그리고 그 경로가 /api/ask와 같은 AgentAskService/DI를 재사용하는지 고정한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.api.organizer import (
    OrganizerAnswerResponse,
    build_process_trace,
    build_retrieved_context,
)
from disclosure_agent.api.service import AgentAskService
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
    SourceDocument,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


@dataclass
class StubAskService:
    """/api/ask 테스트와 같은 자리의 최소 test double. answer_with_execution_result가 없다."""

    response: AnswerResponse
    requests: list[AskRequest] = field(default_factory=list)

    def answer(self, request: AskRequest) -> AnswerResponse:
        self.requests.append(request)
        return self.response


def counting_runtime_service(result: AgentExecutionResult) -> tuple[AgentAskService, list[str]]:
    """answer_question_fn 호출 횟수를 세는 AgentAskService.

    organizer route가 별도 pipeline을 새로 열지 않고 정확히 한 번만 실행하는지 보기 위함이다.
    """
    calls: list[str] = []

    def fake_answer_question(question: str, **_: object) -> AgentExecutionResult:
        calls.append(question)
        return result

    service = AgentAskService(
        corpus_root=Path("unused-synthetic-corpus"),
        answer_question_fn=fake_answer_question,
    )
    return service, calls


def answerable_execution_result() -> AgentExecutionResult:
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


# ---------------------------------------------------------------------------
# 1. 기본 계약: 200, 정확히 5개 필드, 전부 str, question_id/question echo.
# ---------------------------------------------------------------------------


def test_answer_returns_exact_five_string_fields_and_echoes_request() -> None:
    service, calls = counting_runtime_service(answerable_execution_result())
    app.dependency_overrides[get_ask_service] = lambda: service

    response = client.get(
        "/answer",
        params={"question_id": "Q-001", "question": "[SYNTHETIC] revenue question"},
    )

    assert response.status_code == 200
    payload = response.json()

    assert set(payload.keys()) == {
        "question_id",
        "question",
        "retrieved_context",
        "think_trace",
        "answer",
    }
    for key, value in payload.items():
        assert isinstance(value, str), f"{key} must be a string, got {type(value)!r}"

    assert payload["question_id"] == "Q-001"
    assert payload["question"] == "[SYNTHETIC] revenue question"
    assert payload["answer"]
    assert calls == ["[SYNTHETIC] revenue question"]


def test_answer_does_not_leak_source_documents_into_the_five_field_contract() -> None:
    """
    source_documents는 /api/ask의 additive Contract 필드다.

    organizer 5-field 계약(question_id/question/retrieved_context/think_trace/answer)은
    이 변경으로 넓어지지 않는다 — AnswerResponse에 새 필드가 생겨도 organizer는 그 5개만
    그대로 유지해야 한다.
    """
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
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[fact],
        evidence=[evidence],
        source_documents=[
            SourceDocument(
                doc_id="synthetic-doc-1",
                company="Synthetic Company",
                report_name="사업보고서",
                fiscal_year=2025,
                document_subtype="annual",
                base_month=12,
                filing_date=date(2026, 3, 10),
                receipt_no="20260310002820",
            )
        ],
    )
    service, _ = counting_runtime_service(result)
    app.dependency_overrides[get_ask_service] = lambda: service

    response = client.get(
        "/answer", params={"question_id": "Q-010", "question": "synthetic source document"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {
        "question_id",
        "question",
        "retrieved_context",
        "think_trace",
        "answer",
    }
    assert "source_documents" not in response.text
    assert "사업보고서" not in response.text


# ---------------------------------------------------------------------------
# 2. 기존 AgentAskService/DI 재사용, 별도 pipeline을 새로 열지 않음.
# ---------------------------------------------------------------------------


def test_answer_reuses_the_same_ask_service_dependency_as_api_ask() -> None:
    """organizer route와 /api/ask가 같은 FastAPI dependency 함수를 공유한다."""
    from disclosure_agent.api import organizer, routes

    assert organizer.get_ask_service is routes.get_ask_service


def test_answer_executes_the_production_pipeline_exactly_once() -> None:
    """answer_question_fn이 두 번 불리면 별도 pipeline이 생긴 것이다."""
    service, calls = counting_runtime_service(answerable_execution_result())
    app.dependency_overrides[get_ask_service] = lambda: service

    response = client.get(
        "/answer",
        params={"question_id": "Q-002", "question": "synthetic single execution"},
    )

    assert response.status_code == 200
    assert calls == ["synthetic single execution"]


def test_answer_falls_back_gracefully_for_a_non_agent_ask_service_double() -> None:
    """AgentAskService가 아닌 AskService 구현이 주입돼도 5-field 계약은 깨지지 않는다."""
    expected = AnswerResponse(
        answer="[SYNTHETIC] stub answer",
        status=AnswerabilityStatus.ANSWERABLE,
    )
    app.dependency_overrides[get_ask_service] = lambda: StubAskService(expected)

    response = client.get(
        "/answer",
        params={"question_id": "Q-003", "question": "synthetic stub"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "[SYNTHETIC] stub answer"
    assert payload["retrieved_context"] == ""


# ---------------------------------------------------------------------------
# 3. evidence가 있을 때 retrieved_context가 deterministic.
# ---------------------------------------------------------------------------


def test_retrieved_context_is_deterministic_and_reflects_the_actual_evidence() -> None:
    result = answerable_execution_result()
    service, _ = counting_runtime_service(result)
    app.dependency_overrides[get_ask_service] = lambda: service

    first = client.get("/answer", params={"question_id": "Q-004", "question": "synthetic"})
    second = client.get("/answer", params={"question_id": "Q-004", "question": "synthetic"})

    assert first.status_code == second.status_code == 200
    assert first.json()["retrieved_context"] == second.json()["retrieved_context"]

    context = first.json()["retrieved_context"]
    assert "synthetic-doc-1" in context
    assert "synthetic-section" in context
    assert "synthetic-revenue: 1000 synthetic-currency" in context

    # 직접 unit test로도 같은 함수의 출력이 순서를 보존하는지 고정한다.
    evidence = result.evidence
    assert build_retrieved_context(evidence) == build_retrieved_context(evidence)
    assert build_retrieved_context([]) == ""


def test_retrieved_context_omits_fields_the_evidence_does_not_have() -> None:
    """존재하지 않는 필드를 invent하지 않는다."""
    evidence = Evidence(
        evidence_id="synthetic-evidence-bare",
        doc_id="synthetic-doc-bare",
        location_type=EvidenceLocationType.TEXT,
    )

    context = build_retrieved_context([evidence])

    assert "synthetic-doc-bare" in context
    assert "table=" not in context
    assert "row=" not in context
    assert "text=" not in context


# ---------------------------------------------------------------------------
# 4. safe think_trace: structured metadata 기반, hidden chain-of-thought 없음.
# ---------------------------------------------------------------------------


def test_think_trace_is_structured_metadata_not_free_form_reasoning() -> None:
    result = answerable_execution_result()
    service, _ = counting_runtime_service(result)
    app.dependency_overrides[get_ask_service] = lambda: service

    response = client.get("/answer", params={"question_id": "Q-005", "question": "synthetic"})

    assert response.status_code == 200
    trace = response.json()["think_trace"]

    assert "execution_status=COMPLETED" in trace
    assert "answerability_status=ANSWERABLE" in trace
    assert "evidence_count=1" in trace
    assert "fact_count=1" in trace
    assert "calculation_performed=false" in trace
    assert "comparison_performed=false" in trace
    assert "synthetic-doc-1" in trace  # documents_used


def test_think_trace_never_carries_a_raw_hidden_chain_of_thought_field() -> None:
    """OrganizerAnswerResponse 스키마 자체에 hidden CoT를 담을 자리가 없다."""
    assert set(OrganizerAnswerResponse.model_fields) == {
        "question_id",
        "question",
        "retrieved_context",
        "think_trace",
        "answer",
    }

    trace = build_process_trace(
        AnswerResponse(answer="synthetic", status=AnswerabilityStatus.ANSWERABLE),
        execution_result=None,
    )
    # 각 줄이 "key=value" 형태의 구조화 요약이지 자연어 추론 문장이 아니다.
    for line in trace.splitlines():
        assert "=" in line


# ---------------------------------------------------------------------------
# 5. controlled UNANSWERABLE/UNRESOLVED/BLOCKED에서도 organizer schema 유지.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (AnswerabilityStatus.UNANSWERABLE, AnswerabilityReason.NOT_FOUND),
        (AnswerabilityStatus.UNRESOLVED, AnswerabilityReason.AMBIGUOUS_MATCH),
    ],
)
def test_completed_non_answerable_results_keep_the_organizer_schema(
    status: AnswerabilityStatus,
    reason: AnswerabilityReason,
) -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=status,
        answerability_reason=reason,
    )
    service, _ = counting_runtime_service(result)
    app.dependency_overrides[get_ask_service] = lambda: service

    response = client.get("/answer", params={"question_id": "Q-006", "question": "synthetic"})

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {
        "question_id",
        "question",
        "retrieved_context",
        "think_trace",
        "answer",
    }
    for value in payload.values():
        assert isinstance(value, str)
    assert payload["answer"]
    assert f"answerability_status={status.value}" in payload["think_trace"]
    assert f"answerability_reason={reason.value}" in payload["think_trace"]


def test_partially_answerable_result_keeps_the_organizer_schema() -> None:
    """
    D9 case A(open_decisions.md) — 일부 field만 유보.

    build_answer_response는 PARTIALLY_ANSWERABLE에서 확보한 부분(facts/evidence)이 실제로
    있어야 문장을 만든다 — 전부 비어 있으면 근거 없이 답할 수 없다며 멈춘다
    (agent.answer_builder.MissingAnswerSupportError). 그래서 다른 두 케이스처럼 빈
    AgentExecutionResult로 파라미터화하지 않고, /api/ask의 동등 테스트
    (test_api_ask.py::test_partially_withheld_agent_result_returns_200_with_the_confirmed_part)
    와 같은 방식으로 확보분을 함께 싣는다.
    """
    confirmed = answerable_execution_result()
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.WITHHELD_IN_SOURCE,
        facts=confirmed.facts,
        evidence=confirmed.evidence,
    )
    service, _ = counting_runtime_service(result)
    app.dependency_overrides[get_ask_service] = lambda: service

    response = client.get(
        "/answer", params={"question_id": "Q-006b", "question": "synthetic partial"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {
        "question_id",
        "question",
        "retrieved_context",
        "think_trace",
        "answer",
    }
    for value in payload.values():
        assert isinstance(value, str)
    assert payload["answer"]
    assert "answerability_status=PARTIALLY_ANSWERABLE" in payload["think_trace"]
    assert "answerability_reason=withheld_in_source" in payload["think_trace"]


def test_blocked_execution_is_reported_as_200_with_the_organizer_schema() -> None:
    """BLOCKED은 /api/ask에서 422 오류 봉투지만, organizer 계약은 5-field 200을 요구한다."""
    issue = ExecutionIssue(
        stage="synthetic-stage",
        reason="synthetic-reason",
        detail="synthetic-detail-not-for-public-eyes",
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.BLOCKED,
        execution_issue=issue,
    )
    service, _ = counting_runtime_service(result)
    app.dependency_overrides[get_ask_service] = lambda: service

    response = client.get("/answer", params={"question_id": "Q-007", "question": "synthetic"})

    assert response.status_code == 200
    payload = response.json()
    assert set(payload.keys()) == {
        "question_id",
        "question",
        "retrieved_context",
        "think_trace",
        "answer",
    }
    for value in payload.values():
        assert isinstance(value, str)

    assert payload["retrieved_context"] == ""
    assert "execution_status=BLOCKED" in payload["think_trace"]
    assert "synthetic-stage" in payload["think_trace"]
    assert "synthetic-reason" in payload["think_trace"]
    assert issue.detail not in response.text


def test_failed_execution_keeps_the_existing_5xx_envelope_not_the_organizer_schema() -> None:
    """실제 실행 실패는 200으로 위장하지 않는다 — 기존 5xx semantics를 그대로 유지."""
    issue = ExecutionIssue(stage="synthetic-stage", reason="synthetic-reason", detail="x")
    result = AgentExecutionResult(execution_status=ExecutionStatus.FAILED, execution_issue=issue)
    service, _ = counting_runtime_service(result)
    app.dependency_overrides[get_ask_service] = lambda: service

    response = client.get("/answer", params={"question_id": "Q-008", "question": "synthetic"})

    assert response.status_code == 500
    payload = response.json()
    assert payload["execution_status"] == "FAILED"
    assert "question_id" not in payload


def test_unexpected_exception_returns_the_existing_failed_envelope() -> None:
    def raise_unexpected_error(question: str, **_: object) -> AgentExecutionResult:
        assert question
        raise RuntimeError("sensitive synthetic detail")

    app.dependency_overrides[get_ask_service] = lambda: AgentAskService(
        corpus_root=Path("unused-synthetic-corpus"),
        answer_question_fn=raise_unexpected_error,
    )

    response = client.get("/answer", params={"question_id": "Q-009", "question": "synthetic"})

    assert response.status_code == 500
    assert "sensitive synthetic detail" not in response.text


# ---------------------------------------------------------------------------
# 6. 기존 POST /api/ask 회귀 — organizer 라우터 추가가 기존 경로를 건드리지 않는다.
# ---------------------------------------------------------------------------


def test_existing_post_api_ask_still_works_after_the_organizer_router_is_mounted() -> None:
    expected = AnswerResponse(
        answer="[SYNTHETIC] unaffected",
        status=AnswerabilityStatus.ANSWERABLE,
    )
    app.dependency_overrides[get_ask_service] = lambda: StubAskService(expected)

    response = client.post("/api/ask", json={"question": "[SYNTHETIC] unaffected question"})

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
