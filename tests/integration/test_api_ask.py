"""Integration tests for the HTTP-to-Agent service contract."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.agent.derived_metrics import OPERATION_RATIO, UNIT_PERCENT
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
    ComparisonRelation,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    AnswerResponse,
    AskRequest,
    CalculationResult,
    ComparisonResult,
    Evidence,
    ExecutionIssue,
    Fact,
    ResultInputReference,
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


# Contract 수준 Fact producer가 싣는 정규화 단위.
#
# periodic.extractor.normalize_value_and_unit이 만드는 값이다. 사용자 문장에 그대로 실리면
# "2000000000KRW"가 된다.
CANONICAL_NORMALIZED_UNIT = "KRW"


def comparison_fact(
    *,
    fact_id: str,
    company: str,
    metric: MetricConcept,
    raw_value: str,
    normalized_value: str,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        company=company,
        metric=metric,
        fiscal_year=2025,
        accounting_scope=FactAccountingScope.CONSOLIDATED,
        raw_value=raw_value,
        raw_unit="백만원",
        normalized_value=normalized_value,
        normalized_unit=CANONICAL_NORMALIZED_UNIT,
        evidence_ids=["synthetic-evidence-1"],
    )


def fact_reference(fact_id: str) -> ResultInputReference:
    return ResultInputReference(input_type=ResultInputType.FACT, input_id=fact_id)


def calculation_reference(calculation_id: str) -> ResultInputReference:
    return ResultInputReference(input_type=ResultInputType.CALCULATION, input_id=calculation_id)


def source_metric_comparison_result() -> AgentExecutionResult:
    """
    Q10 계열의 실행 결과 모양: Fact 2 · Calculation 0 · Comparison 1.

    handlers.cross_entity_compare가 성공 경로에서 만드는 개수와 같다. 그 handler를 부르지 않는
    이유는 이 테스트가 고정하려는 것이 코퍼스 실행이 아니라 HTTP 경계이기 때문이다. 확정된 결과
    하나가 200으로 나가는지만 본다.
    """
    evidence = Evidence(
        evidence_id="synthetic-evidence-1",
        doc_id="synthetic-doc-1",
        location_type=EvidenceLocationType.TABLE,
    )
    left = comparison_fact(
        fact_id="synthetic-fact-left-revenue",
        company="합성조선(주)",
        metric=MetricConcept.REVENUE,
        raw_value="2,000",
        normalized_value="2000000000",
    )
    right = comparison_fact(
        fact_id="synthetic-fact-right-revenue",
        company="합성중공업(주)",
        metric=MetricConcept.REVENUE,
        raw_value="1,000",
        normalized_value="1000000000",
    )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[left, right],
        evidence=[evidence],
        comparisons=[
            ComparisonResult(
                comparison_id="synthetic-comparison-1",
                left=fact_reference(left.fact_id),
                right=fact_reference(right.fact_id),
                relation=ComparisonRelation.LEFT_GREATER,
            )
        ],
    )


def derived_metric_comparison_result() -> AgentExecutionResult:
    """Q11 계열의 실행 결과 모양: Fact 4 · Calculation 2 · Comparison 1."""
    evidence = Evidence(
        evidence_id="synthetic-evidence-1",
        doc_id="synthetic-doc-1",
        location_type=EvidenceLocationType.TABLE,
    )
    facts = [
        comparison_fact(
            fact_id="synthetic-fact-left-operating-income",
            company="합성조선(주)",
            metric=MetricConcept.OPERATING_INCOME,
            raw_value="250",
            normalized_value="250000000",
        ),
        comparison_fact(
            fact_id="synthetic-fact-left-revenue",
            company="합성조선(주)",
            metric=MetricConcept.REVENUE,
            raw_value="2,000",
            normalized_value="2000000000",
        ),
        comparison_fact(
            fact_id="synthetic-fact-right-operating-income",
            company="합성중공업(주)",
            metric=MetricConcept.OPERATING_INCOME,
            raw_value="50",
            normalized_value="50000000",
        ),
        comparison_fact(
            fact_id="synthetic-fact-right-revenue",
            company="합성중공업(주)",
            metric=MetricConcept.REVENUE,
            raw_value="1,000",
            normalized_value="1000000000",
        ),
    ]
    calculations = [
        CalculationResult(
            calculation_id="synthetic-calculation-left-margin",
            operation=OPERATION_RATIO,
            inputs=[fact_reference(facts[0].fact_id), fact_reference(facts[1].fact_id)],
            value="12.50",
            unit=UNIT_PERCENT,
            evidence_ids=[evidence.evidence_id],
        ),
        CalculationResult(
            calculation_id="synthetic-calculation-right-margin",
            operation=OPERATION_RATIO,
            inputs=[fact_reference(facts[2].fact_id), fact_reference(facts[3].fact_id)],
            value="5.00",
            unit=UNIT_PERCENT,
            evidence_ids=[evidence.evidence_id],
        ),
    ]

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=facts,
        evidence=[evidence],
        calculations=calculations,
        comparisons=[
            ComparisonResult(
                comparison_id="synthetic-comparison-1",
                left=calculation_reference(calculations[0].calculation_id),
                right=calculation_reference(calculations[1].calculation_id),
                relation=ComparisonRelation.LEFT_GREATER,
            )
        ],
    )


def test_q10_shaped_comparison_result_returns_200() -> None:
    """
    Fact 2 · Calculation 0 · Comparison 1이 500이 아니라 답변으로 나간다.

    이 모양은 Answer Builder가 단일 Fact 문장으로만 옮기던 동안 UnsupportedResultShapeError로
    떨어졌고, 그 예외가 HTTP 경계에서 unexpected_failure 500이 됐다. 실행은 정상적으로 끝나
    ANSWERABLE로 판정된 결과였다.
    """
    result = source_metric_comparison_result()
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic cross-entity source metric"})

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] == "ANSWERABLE"
    assert payload["reason"] is None
    assert len(payload["facts"]) == 2
    assert "합성조선(주)" in payload["answer"]
    assert "합성중공업(주)" in payload["answer"]
    assert payload["answer"].endswith("합성조선(주) 쪽이 더 큽니다.")
    assert "(정규화 2000000000원)" in payload["answer"]


def test_the_canonical_normalized_unit_never_reaches_the_user_facing_answer() -> None:
    """
    정규화 단위는 추출 계층의 어휘다. 문장에 그대로 실리면 한국어 답변에 "KRW"가 나간다.

    검사 대상은 응답 전체가 아니라 answer 필드다. facts[].normalized_unit은 "KRW" 그대로
    남아야 한다. Builder는 표현 계층이며 구조화 결과의 값을 고치지 않는다. 두 곳을 함께 검사하면
    "문장에서 옮긴다"와 "값을 바꾼다"가 구분되지 않는다.
    """
    result = source_metric_comparison_result()

    assert {fact.normalized_unit for fact in result.facts} == {CANONICAL_NORMALIZED_UNIT}

    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic canonical unit"})

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] == "ANSWERABLE"
    assert CANONICAL_NORMALIZED_UNIT not in payload["answer"]
    assert "(정규화 2000000000원)" in payload["answer"]
    assert "(정규화 1000000000원)" in payload["answer"]

    # 원문 표기는 공시에 적힌 그대로 남는다. 옮기는 것은 정규화 쪽뿐이다.
    assert "2,000백만원" in payload["answer"]

    # 구조화 결과는 Agent가 실은 값 그대로다.
    assert [fact["normalized_unit"] for fact in payload["facts"]] == [
        CANONICAL_NORMALIZED_UNIT,
        CANONICAL_NORMALIZED_UNIT,
    ]


def test_q11_shaped_comparison_result_returns_200() -> None:
    """Fact 4 · Calculation 2 · Comparison 1도 같은 경계를 지난다."""
    result = derived_metric_comparison_result()
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic cross-entity derived metric"})

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] == "ANSWERABLE"
    assert payload["reason"] is None
    assert len(payload["facts"]) == 4
    assert "12.50%" in payload["answer"]
    assert "5.00%" in payload["answer"]
    assert payload["answer"].endswith("합성조선(주) 쪽이 더 큽니다.")


def test_q11_response_does_not_recalculate_the_margins_from_the_facts() -> None:
    """
    답변에 실린 비율은 CalculationResult의 값이다.

    원천 Fact는 12.50%와 5.00%를 가리키지만 실행 결과에 실린 값을 1.00%와 7.00%로 바꿔 둔다.
    정상 경로에서는 둘이 같으므로, 어긋나게 두어야 API를 지나온 문장이 어느 쪽을 읽었는지 드러난다.
    관계도 마찬가지로 결과에 적힌 relation을 따른다.
    """
    result = derived_metric_comparison_result()
    result.calculations[0].value = "1.00"
    result.calculations[1].value = "7.00"
    result.comparisons[0].relation = ComparisonRelation.RIGHT_GREATER

    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic no recalculation"})

    assert response.status_code == 200

    answer = response.json()["answer"]

    assert "1.00%" in answer
    assert "7.00%" in answer
    assert "12.50" not in answer
    assert answer.endswith("합성중공업(주) 쪽이 더 큽니다.")


def test_not_comparable_result_still_returns_200_with_the_unavailable_sentence() -> None:
    """
    비교 불가 판정은 실행이 끝난 결과다. 우열 문장이 생겼다고 이 경로가 달라지지 않는다.

    NOT_COMPARABLE은 UNANSWERABLE이므로 comparisons가 비어 있고(normalization의 STAGE_COMPARABILITY
    경로), 문장은 사유별 대응표에서 온다. 두 경로가 섞이면 견줄 수 없다고 판정한 결과에 우열
    문장이 붙는다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=AnswerabilityReason.NOT_COMPARABLE,
        facts=source_metric_comparison_result().facts,
    )
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic not comparable"})

    assert response.status_code == 200

    payload = response.json()

    assert payload["answer"] == (
        "확보한 값들의 기준, 단위 또는 개념이 서로 달라 직접 비교할 수 없습니다."
    )
    assert payload["status"] == "UNANSWERABLE"
    assert payload["reason"] == "not_comparable"
    assert "쪽이 더 큽니다" not in payload["answer"]


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
