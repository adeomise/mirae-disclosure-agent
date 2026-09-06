"""Integration tests for the HTTP-to-Agent service contract."""

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.agent.answer_builder import (
    REASON_INCOMPARABLE_ENTITIES,
    STAGE_COMPARABILITY,
    UnsupportedResultShapeError,
    build_answer_response,
)
from disclosure_agent.agent.derived_metrics import OPERATION_RATIO, UNIT_PERCENT
from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.api.service import (
    BLOCKED_PUBLIC_MESSAGE,
    FAILED_PUBLIC_MESSAGE,
    MISSING_YEAR_PUBLIC_MESSAGE,
    UNKNOWN_COMPANY_PUBLIC_MESSAGE,
    UNSUPPORTED_METRIC_PUBLIC_MESSAGE,
    AgentAskService,
    MissingExecutionIssueError,
    Verbalize,
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
    AnswerabilityDetail,
    AnswerResponse,
    AskRequest,
    CalculationResult,
    ComparisonResult,
    Evidence,
    ExecutionIssue,
    Fact,
    ResultInputReference,
    SourceDocument,
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


def _deterministic_verbalize(payload: object) -> str | None:
    """
    이 파일의 기본 verbalize_fn. 항상 None을 돌려줘 build_answer_response의 문장을 그대로 둔다.

    default_verbalize(설정을 읽고 실제 HyperCLOVA 클라이언트를 만드는 자리)를 대신한다.
    이 파일은 HTTP 경계의 구조적 계약만 고정하며, HCX가 문장을 바꾸는지는
    test_api_hcx_verbalization.py가 전담한다.

    default_verbalize에 맡겨 두면 로컬 .env나 프로세스 환경에 HyperCLOVA 자격 증명이
    채워져 있을 때(HYPERCLOVA_ENABLED=true 등) 이 파일의 assertion이 실제 모델 응답과
    부딪힌다. 이 함수로 verbalize_fn을 고정해 두면 바깥 환경이 무엇이든 default_verbalize/
    _cached_chat_client/실제 HCX 전송 계층을 아예 지나가지 않는다.
    """
    return None


def runtime_service(
    result: AgentExecutionResult,
    *,
    verbalize_fn: Verbalize = _deterministic_verbalize,
) -> AgentAskService:
    def fake_answer_question(question: str, **_: object) -> AgentExecutionResult:
        assert question
        return result

    return AgentAskService(
        corpus_root=Path("unused-synthetic-corpus"),
        answer_question_fn=fake_answer_question,
        verbalize_fn=verbalize_fn,
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
NOT_COMPARABLE_MESSAGE = "확보한 값들의 기준, 단위 또는 개념이 서로 달라 직접 비교할 수 없습니다."

# 진단 어휘가 응답 본문으로 새는지 보려면 그 문자열이 눈에 띄어야 한다.
NOT_COMPARABLE_DIAGNOSTIC_DETAIL = (
    "SYNTHETIC-DIAGNOSTIC: 두 기업의 정규화 단위가 다릅니다: 원 / 천원"
)


def not_comparable_execution_result() -> AgentExecutionResult:
    """
    비교 가능성 판정에서 멈춘 Pattern D 결과 하나.

    확보는 정상이었으므로 facts와 evidence가 남고, 파생값과 우열 판정은 남지 않는다
    (normalization._stopped_execution). 거부 응답에 근거를 붙이지 못하게 막는 사유 목록에
    NOT_COMPARABLE은 없다. 무엇이 견줄 수 없다고 판정됐는지 보려면 그 값이 있어야 하기
    때문이며, 이 테스트가 그 구조를 HTTP 경계까지 그대로 확인한다.
    """
    evidence = [
        Evidence(
            evidence_id=f"synthetic-evidence-{index}",
            doc_id=f"synthetic-doc-{index}",
            location_type=EvidenceLocationType.TABLE,
        )
        for index in (1, 2)
    ]
    facts = [
        Fact(
            fact_id=f"synthetic-fact-{index}",
            company=f"Synthetic Company {index}",
            metric=MetricConcept.REVENUE,
            fiscal_year=2025,
            accounting_scope=FactAccountingScope.CONSOLIDATED,
            raw_value="1000",
            raw_unit="synthetic-currency",
            evidence_ids=[item.evidence_id],
        )
        for index, item in enumerate(evidence, start=1)
    ]
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=AnswerabilityReason.NOT_COMPARABLE,
        answerability_detail=AnswerabilityDetail(
            stage=STAGE_COMPARABILITY,
            reason=REASON_INCOMPARABLE_ENTITIES,
            detail=NOT_COMPARABLE_DIAGNOSTIC_DETAIL,
        ),
        facts=facts,
        evidence=evidence,
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


def test_completed_answerable_agent_result_surfaces_source_documents() -> None:
    """source_documents는 additive Contract 필드다.

    새 필드가 없던 기존 소비자를 깨지 않으면서(다른 필드는 이전과 동일), Evidence.doc_id가
    가리키는 문서의 metadata를 /api/ask 응답에서 그대로 확인할 수 있어야 한다.
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
    source_document = SourceDocument(
        doc_id="synthetic-doc-1",
        company="Synthetic Company",
        report_name="사업보고서",
        fiscal_year=2025,
        document_subtype="annual",
        base_month=12,
        filing_date=date(2026, 3, 10),
        receipt_no="20260310002820",
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[fact],
        evidence=[evidence],
        source_documents=[source_document],
    )
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic answerable"})

    assert response.status_code == 200
    payload = response.json()

    assert payload["source_documents"] == [source_document.model_dump(mode="json")]
    assert payload["evidence"][0]["doc_id"] == payload["source_documents"][0]["doc_id"]


def test_agent_result_without_source_documents_defaults_to_an_empty_list() -> None:
    """이 필드를 채우지 않는 기존 producer는 지금까지와 똑같이 빈 목록을 받는다."""
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(
        answerable_execution_result()
    )

    response = client.post("/api/ask", json={"question": "synthetic answerable"})

    assert response.status_code == 200
    assert response.json()["source_documents"] == []


@pytest.mark.parametrize(
    "fiscal_period,period_phrase",
    [
        ("3M YTD", "2025년 1분기 누적"),
        ("6M YTD", "2025년 반기 누적"),
        ("9M YTD", "2025년 3분기 누적"),
    ],
)
def test_cumulative_period_result_returns_200_with_its_span(
    fiscal_period: str, period_phrase: str
) -> None:
    """분기·반기보고서의 누적 값이 HTTP 경계를 지난다.

    이 자리가 500이었다. 파이프라인은 ANSWERABLE까지 정상으로 끝나는데 문장을 만드는 자리가
    연간 외 기간을 거절해, 답이 있는 요청이 서버 오류로 나갔다.
    """
    result = answerable_execution_result()
    result.facts[0].fiscal_period = fiscal_period

    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic cumulative"})

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "ANSWERABLE"
    assert period_phrase in body["answer"]
    assert body["facts"][0]["fiscal_period"] == fiscal_period


@pytest.mark.parametrize("fiscal_period", ["3M YTD", "6M YTD", "9M YTD"])
def test_a_cumulative_answer_is_never_presented_as_an_annual_figure(
    fiscal_period: str,
) -> None:
    """500을 200으로 바꾸는 것만으로는 부족하다. 기간이 틀린 200이 더 나쁘다."""
    result = answerable_execution_result()
    result.facts[0].fiscal_period = fiscal_period

    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    answer = client.post("/api/ask", json={"question": "synthetic cumulative"}).json()["answer"]

    app.dependency_overrides[get_ask_service] = lambda: runtime_service(
        answerable_execution_result()
    )

    annual = client.post("/api/ask", json={"question": "synthetic annual"}).json()["answer"]

    assert "누적" in answer
    assert answer != annual


def test_an_unmapped_fiscal_period_still_fails_closed() -> None:
    """표기가 정해지지 않은 기간까지 열리지는 않는다. 'quarter'는 누적이 아니다."""
    result = answerable_execution_result()
    result.facts[0].fiscal_period = "quarter"

    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic unmapped period"})

    assert response.status_code == 500


def cumulative_comparison_result() -> AgentExecutionResult:
    """누적 기간 Fact 2건을 견준 결과. 단일 Fact 조회가 아닌 모양이다."""
    result = answerable_execution_result()

    # 우열 판정은 정규화 값에서 나온다. 원본 fixture에는 그 짝이 없으므로 여기서 채운다
    # (_fact_operand). 채우지 않으면 두 테스트가 기간 경계가 아니라 값이 없다는 이유로 멈추고,
    # 그러면 이 파일이 무엇을 고정했는지 알 수 없다.
    result.facts[0].normalized_value = "1000"
    result.facts[0].normalized_unit = "원"

    second = result.facts[0].model_copy(
        update={
            "fact_id": "synthetic-fact-2",
            "company": "Synthetic Rival",
            "normalized_value": "500",
        }
    )

    result.facts.append(second)

    for fact in result.facts:
        fact.fiscal_period = "6M YTD"

    result.comparisons.append(
        ComparisonResult(
            comparison_id="synthetic-comparison-1",
            left=ResultInputReference(
                input_type=ResultInputType.FACT, input_id="synthetic-fact-1"
            ),
            right=ResultInputReference(
                input_type=ResultInputType.FACT, input_id="synthetic-fact-2"
            ),
            relation=ComparisonRelation.LEFT_GREATER,
        )
    )

    return result


def test_a_cumulative_comparison_stays_fail_closed_at_the_http_boundary() -> None:
    """
    누적 기간이 열린 것은 단일 Fact 조회뿐이다.

    비교·계산 경로가 함께 열리면 계약이 정하지 않은 답이 나간다. 그 정지는 이 변경 이전과
    같은 자리이며, 여기서 200이 되는 것이 회귀다.
    """
    result = cumulative_comparison_result()

    # 같은 결과가 연간 값이면 답이 된다(아래 테스트). 그러므로 이 500은 값이 모자라서가 아니라
    # 누적 기간이 이 모양에서 열려 있지 않아서다.
    with pytest.raises(UnsupportedResultShapeError, match="누적 기간"):
        build_answer_response(result)

    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic cumulative comparison"})

    assert response.status_code == 500


def test_an_annual_comparison_is_unaffected_by_the_interim_boundary() -> None:
    """연간 값의 같은 모양은 그대로 답한다. 막는 것은 누적 기간뿐이다."""
    result = cumulative_comparison_result()

    for fact in result.facts:
        fact.fiscal_period = "FY"

    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic annual comparison"})

    assert response.status_code == 200
    assert response.json()["status"] == "ANSWERABLE"


def test_completed_partial_holding_shares_result_returns_200() -> None:
    evidence = Evidence(
        evidence_id="synthetic-holding-evidence-1",
        doc_id="synthetic-holding-doc-1",
        location_type=EvidenceLocationType.TABLE,
    )
    fact = Fact(
        fact_id="synthetic-holding-fact-1",
        company="Synthetic Issuer",
        metric=MetricConcept.HOLDING_SHARES,
        accounting_scope=FactAccountingScope.NOT_APPLICABLE,
        raw_value="1,234",
        raw_unit="주",
        evidence_ids=[evidence.evidence_id],
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.PARTIAL_SOURCE_SUPPORT,
        facts=[fact],
        evidence=[evidence],
    )
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic partial holding"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "PARTIALLY_ANSWERABLE"
    assert payload["reason"] == "partial_source_support"
    assert payload["facts"] == [fact.model_dump(mode="json")]
    assert payload["evidence"] == [evidence.model_dump(mode="json")]
    assert "1,234주" in payload["answer"]
    assert "보유비율" not in payload["answer"]


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (AnswerabilityStatus.UNANSWERABLE, AnswerabilityReason.NOT_FOUND),
        (AnswerabilityStatus.UNRESOLVED, AnswerabilityReason.AMBIGUOUS_MATCH),
        # 핵심 required field 전체가 원문에서 유보된 경우(open_decisions.md D9 case B).
        # 판정이 정상적으로 끝난 결과이므로 500이 아니라 200으로 나가야 한다.
        (AnswerabilityStatus.UNANSWERABLE, AnswerabilityReason.WITHHELD_IN_SOURCE),
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


def test_runtime_service_stays_isolated_from_hyperclova_even_when_the_environment_enables_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    회귀 방지: 바깥 프로세스 환경(.env 포함)이 HyperCLOVA를 켜 두고 자격 증명까지 채워도,
    runtime_service()로 만든 서비스는 실제 HCX 전송 계층에 닿지 않는다.

    HYPERCLOVA_ENABLED=true와 그럴듯한 자격 증명을 주입한 뒤 get_hyperclova_settings /
    _cached_chat_client의 lru_cache를 비운다. 비우지 않으면 이전 테스트가 이미 채운 캐시가
    이번 환경 변경을 가리고, 이 테스트가 아무것도 증명하지 못한다.

    HttpHyperClovaClient.complete 자체를 감시한다. 이 메서드가 실제 HCX 전송이 시작되는
    경계다(agent.hyperclova의 ChatCompletionClient 계약). runtime_service()가
    verbalize_fn을 제대로 갈아 끼우고 있다면(_deterministic_verbalize) default_verbalize를
    지나가지 않으므로 이 메서드는 한 번도 불리지 않는다.

    FastAPI TestClient도 내부적으로 httpx를 쓰므로 httpx.Client.post 자체를 감시하면
    테스트 요청까지 걸린다. HttpHyperClovaClient.complete만 감시해 그 문제를 피한다.

    service.py가 verbalize_fn의 예외를 삼키므로(정상 요청을 실패로 만들지 않기 위해) 응답
    자체는 실제 호출 여부와 무관하게 200으로 나갈 수 있다. 그래서 호출 여부는 응답 상태가
    아니라 이 sentinel의 호출 기록으로 직접 확인한다.

    테스트가 끝나면 캐시를 다시 비워, 여기서 주입한 가짜 자격 증명이 다른 테스트로 새지
    않게 한다.
    """
    from disclosure_agent.agent.hyperclova import HttpHyperClovaClient
    from disclosure_agent.api import service as service_module
    from disclosure_agent.common.config import get_hyperclova_settings

    monkeypatch.setenv("HYPERCLOVA_ENABLED", "true")
    monkeypatch.setenv("HYPERCLOVA_API_KEY", "synthetic-key")
    monkeypatch.setenv("HYPERCLOVA_API_URL", "https://example.invalid/v3/chat-completions")
    monkeypatch.setenv("HYPERCLOVA_MODEL", "HCX-005")

    get_hyperclova_settings.cache_clear()
    service_module._cached_chat_client.cache_clear()

    network_calls: list[object] = []

    def fail_if_called(self: HttpHyperClovaClient, *, system_prompt: str, user_message: str) -> str:
        network_calls.append((system_prompt, user_message))
        raise AssertionError(
            "real HyperCLOVA transport must not be reached from tests/integration/test_api_ask.py"
        )

    monkeypatch.setattr(HttpHyperClovaClient, "complete", fail_if_called)

    try:
        result = derived_metric_comparison_result()
        result.calculations[0].value = "1.00"
        result.calculations[1].value = "7.00"
        result.comparisons[0].relation = ComparisonRelation.RIGHT_GREATER

        app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

        response = client.post(
            "/api/ask",
            json={"question": "synthetic isolation guard"},
        )

        assert response.status_code == 200

        answer = response.json()["answer"]
        assert "1.00%" in answer
        assert "7.00%" in answer
        assert "12.50" not in answer
        assert network_calls == []
    finally:
        get_hyperclova_settings.cache_clear()
        service_module._cached_chat_client.cache_clear()


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


def test_not_comparable_result_returns_200_with_the_user_facing_sentence() -> None:
    """
    비교 불가 판정은 실행 실패가 아니다.

    실행은 COMPLETED로 끝났고 답할 수 없다고 판정한 것뿐이므로 200으로 나가야 한다.
    422/500 봉투로 나가면 받는 쪽이 재시도할 오류로 읽고, 판정 자체가 응답에서 사라진다.

    확보한 근거는 그대로 실린다. AnswerResponse에 facts와 evidence 자리가 있고
    build_answer_response가 판정과 무관하게 그것을 옮기므로, 무엇이 견줄 수 없다고
    판정됐는지가 응답에 남는다.

    진단 어휘는 실리지 않는다. 사용자 문장은 canonical 사유 하나가 정하며, 어느 축이
    어긋났는지(incomparable_entities)와 그 원문 detail은 내부 진단이다. AnswerResponse에는
    answerability_detail 자리가 아예 없고, 그 사실을 응답 본문 전체로 확인한다.
    """
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(
        not_comparable_execution_result()
    )

    response = client.post("/api/ask", json={"question": "synthetic not comparable"})

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] == AnswerabilityStatus.UNANSWERABLE.value
    assert payload["reason"] == AnswerabilityReason.NOT_COMPARABLE.value
    assert payload["answer"] == NOT_COMPARABLE_MESSAGE

    # 오류 봉투가 아니다. 두 키는 BLOCKED/FAILED 응답에만 있다.
    assert "execution_status" not in payload
    assert "error" not in payload

    # 확보한 값이 살아 있어야 무엇을 견주려 했는지 되짚을 수 있다.
    expected = not_comparable_execution_result()

    assert [fact["fact_id"] for fact in payload["facts"]] == [
        fact.fact_id for fact in expected.facts
    ]
    assert [item["evidence_id"] for item in payload["evidence"]] == [
        item.evidence_id for item in expected.evidence
    ]
    assert payload["facts"][0]["evidence_ids"] == [payload["evidence"][0]["evidence_id"]]

    # 파생값과 우열 판정은 producer가 이미 지웠고, AnswerResponse에는 자리도 없다.
    assert "calculations" not in payload
    assert "comparisons" not in payload

    # 내부 진단은 사용자 문장에도 응답 어디에도 없다.
    assert REASON_INCOMPARABLE_ENTITIES not in payload["answer"]
    assert STAGE_COMPARABILITY not in payload["answer"]
    assert NOT_COMPARABLE_DIAGNOSTIC_DETAIL not in response.text
    assert REASON_INCOMPARABLE_ENTITIES not in response.text


def test_partially_withheld_agent_result_returns_200_with_the_confirmed_part() -> None:
    """
    D9 case A — 일부 field만 유보. 확보분을 실어 200으로 나간다.

    Builder가 이 조합에서 멈추면 API가 500을 내고, 실제로는 답할 수 있었던 부분까지
    서버 오류로 사라진다.
    """
    confirmed = answerable_execution_result()
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.WITHHELD_IN_SOURCE,
        facts=confirmed.facts,
        evidence=confirmed.evidence,
    )
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic withheld"})

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] == "PARTIALLY_ANSWERABLE"
    assert payload["reason"] == "withheld_in_source"
    assert len(payload["facts"]) == 1
    assert payload["answer"]


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


def test_missing_year_blocked_result_returns_year_guidance_message() -> None:
    """
    reason == "missing_year"인 BLOCKED만 전용 안내문을 받는다.

    Query Analyzer의 판정(stage="query_plan", reason="missing_year")과 execution_status,
    HTTP 422는 이전과 같다. 바뀌는 것은 error.message 하나뿐이다.
    """
    issue = ExecutionIssue(
        stage="query_plan",
        reason="missing_year",
        detail="질문에서 대상 연도를 찾을 수 없습니다.",
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.BLOCKED,
        execution_issue=issue,
    )
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "공급계약 내용을 알려줘"})

    assert response.status_code == 422
    payload = response.json()
    assert payload["execution_status"] == "BLOCKED"
    assert payload["error"]["stage"] == "query_plan"
    assert payload["error"]["reason"] == "missing_year"
    assert payload["error"]["message"] == MISSING_YEAR_PUBLIC_MESSAGE
    assert "연도" in payload["error"]["message"]

    # 내부 진단(detail)이나 다른 사유의 generic 문장이 새지 않는다.
    assert issue.detail not in response.text
    assert BLOCKED_PUBLIC_MESSAGE not in response.text


def test_unknown_company_blocked_result_returns_company_guidance_message() -> None:
    """
    stage == "query_plan" and reason == "unknown_company"인 BLOCKED만 전용 안내문을 받는다.

    Query Analyzer가 어떤 이름을 왜 못 풀었는지(계약 상대방 vs 공시 기업명)는 이 계층이
    판단하지 않는다 — stage/reason/execution_status/HTTP status는 원형 그대로 두고
    message 하나만 갈아 끼운다. stage는 실제 계약(query_plan)과 같은 값을 쓴다 — synthetic
    stage로는 이 mapping이 (stage, reason) 조합을 보는지 확인할 수 없다.
    """
    issue = ExecutionIssue(
        stage="query_plan",
        reason="unknown_company",
        detail="회사명을 dictionary에서 찾지 못했습니다: Ford",
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.BLOCKED,
        execution_issue=issue,
    )
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "Ford 계약 내용을 알려줘"})

    assert response.status_code == 422
    payload = response.json()
    assert payload["execution_status"] == "BLOCKED"
    assert payload["error"]["stage"] == "query_plan"
    assert payload["error"]["reason"] == "unknown_company"
    assert payload["error"]["message"] == UNKNOWN_COMPANY_PUBLIC_MESSAGE
    assert payload["error"]["message"] == (
        "조회할 기업을 확인하지 못했습니다. 공시 기업명을 정확히 입력해 주세요."
    )

    # generic 문장과 내부 진단(회사명 dictionary lookup 실패 detail)이 새지 않는다.
    assert BLOCKED_PUBLIC_MESSAGE not in response.text
    assert issue.detail not in response.text
    assert "Ford" not in response.text


def test_unsupported_metric_at_query_plan_returns_metric_guidance_message() -> None:
    """
    stage == "query_plan" and reason == "unsupported_metric"만 전용 지표 안내문을 받는다.

    이 조합은 사용자가 질문을 고쳐 풀 수 있는 자리다(query_analyzer가 질문에서 지표를
    못 읽음). 지원 지표 목록을 여기서 나열하지 않으며, 질문의 실제 의도(투자계획 등)를
    재분류하지도 않는다 — message 하나만 갈아 끼운다.
    """
    issue = ExecutionIssue(
        stage="query_plan",
        reason="unsupported_metric",
        detail="지원하지 않는 지표 키워드: 투자계획",
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.BLOCKED,
        execution_issue=issue,
    )
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "내년 투자계획 알려줘"})

    assert response.status_code == 422
    payload = response.json()
    assert payload["execution_status"] == "BLOCKED"
    assert payload["error"]["stage"] == "query_plan"
    assert payload["error"]["reason"] == "unsupported_metric"
    assert payload["error"]["message"] == UNSUPPORTED_METRIC_PUBLIC_MESSAGE
    assert payload["error"]["message"] == (
        "요청하신 항목을 현재 지원하는 공시 지표로 확인하지 못했습니다. "
        "조회할 항목을 조금 더 구체적으로 입력해 주세요."
    )

    # generic 문장과 내부 진단(미지원 키워드 detail)이 새지 않는다.
    assert BLOCKED_PUBLIC_MESSAGE not in response.text
    assert issue.detail not in response.text
    assert "투자계획" not in response.text


def test_unsupported_metric_blocked_result_at_extraction_keeps_the_generic_message() -> None:
    """
    stage == "extraction" and reason == "unsupported_metric"은 전용 안내문을 받지 않는다.

    이 조합은 periodic.extractor가 문서에서 그 지표를 못 뽑아 나는 것이다
    (ExtractionBlockedError -> agent.period_retrieval의 STAGE_EXTRACTION). 질문은 이미
    지표를 정확히 짚었을 수 있으므로, "질문을 더 구체적으로 입력해 달라"는 안내는 맞지
    않는다 — 이 조합에서는 generic BLOCKED_PUBLIC_MESSAGE를 그대로 받아야 한다.

    reason 문자열만 같고 stage가 다른 경우이므로, mapping이 reason 하나만으로 판정하면
    (구 버전 버그) 이 테스트가 UNSUPPORTED_METRIC_PUBLIC_MESSAGE를 보고 실패한다.
    """
    issue = ExecutionIssue(
        stage="extraction",
        reason="unsupported_metric",
        detail="지원하지 않는 지표입니다: some-internal-metric-code",
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.BLOCKED,
        execution_issue=issue,
    )
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "합성회사 2025년 설비투자 내역"})

    assert response.status_code == 422
    payload = response.json()
    assert payload["execution_status"] == "BLOCKED"
    assert payload["error"]["stage"] == "extraction"
    assert payload["error"]["reason"] == "unsupported_metric"
    assert payload["error"]["message"] == BLOCKED_PUBLIC_MESSAGE

    # 지표 전용 안내문과 내부 진단이 새지 않는다.
    assert UNSUPPORTED_METRIC_PUBLIC_MESSAGE not in response.text
    assert issue.detail not in response.text


def test_other_blocked_reasons_keep_the_generic_public_message() -> None:
    """등록되지 않은 (stage, reason) 조합은 여전히 generic 문장을 받는다.

    다른 조합 전용 안내문(연도/기업/지표)이 여기로 새지 않는다.
    """
    issue = ExecutionIssue(
        stage="synthetic-stage",
        reason="synthetic-reason",
        detail="synthetic-detail",
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.BLOCKED,
        execution_issue=issue,
    )
    app.dependency_overrides[get_ask_service] = lambda: runtime_service(result)

    response = client.post("/api/ask", json={"question": "synthetic other blocked reason"})

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["reason"] == "synthetic-reason"
    assert payload["error"]["message"] == BLOCKED_PUBLIC_MESSAGE
    assert MISSING_YEAR_PUBLIC_MESSAGE not in response.text
    assert UNKNOWN_COMPANY_PUBLIC_MESSAGE not in response.text
    assert UNSUPPORTED_METRIC_PUBLIC_MESSAGE not in response.text
    assert UNKNOWN_COMPANY_PUBLIC_MESSAGE not in response.text
    assert UNSUPPORTED_METRIC_PUBLIC_MESSAGE not in response.text


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

    assert len(actual.source_documents) == 1
    source_document = actual.source_documents[0]
    assert source_document.doc_id == actual.evidence[0].doc_id
    assert source_document.fiscal_year == 2025
    assert source_document.document_subtype == "annual"
    assert source_document.base_month == 12
    assert source_document.report_name


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
