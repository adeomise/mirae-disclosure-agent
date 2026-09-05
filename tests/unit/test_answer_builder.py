"""AgentExecutionResult -> AnswerResponse 공개 변환 단위 테스트.

이 테스트는 합성 값만 쓴다. 실제 공시 수치와 접수번호는 쓰지 않는다(AGENTS.md).

무엇을 고정하는가:
    - COMPLETED만 변환하고 BLOCKED/FAILED는 fail-fast 한다.
    - 네 answerability 상태가 하나의 문장으로 뭉개지지 않는다.
    - 근거 없이 그럴듯한 문장을 만들지 않는다.
    - 여러 Fact 중 첫 건만 골라 답하지 않는다.
    - AnswerResponse에 자리가 있는 구조화 결과가 손실 없이 실린다.

문자열 리터럴을 그대로 고정하는 곳이 있다. 사용자에게 나가는 문장은 wire 값이며,
"찾지 못했습니다"와 "보유하고 있지 않습니다"의 구분처럼 뜻이 다른 표현이 조용히 같은
문장으로 수렴하면 다른 어떤 검사로도 잡히지 않는다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.answer_builder import (
    ACCOUNTING_SCOPE_LABELS,
    ALLOWED_REASONS_BY_STATUS,
    CALCULATION_UNIT_LABELS,
    COMPARISON_RELATION_TEMPLATES,
    DERIVED_METRIC_LABELS,
    FACT_NORMALIZED_UNIT_LABELS,
    LIMITATION_MESSAGE_BY_REASON,
    METRIC_LABELS,
    OPERATION_AGGREGATE,
    OPERATION_DELTA,
    OPERATION_DELTA_PCT,
    OPERATION_DIFF_PP,
    POLICY_REFUSAL_MESSAGE_BY_DETAIL_REASON,
    REASON_INCOMPARABLE_CALCULATION_INPUTS,
    REASON_INCOMPARABLE_PERIODS,
    REFUSAL_REASONS_WITHOUT_SUPPORTING_PAYLOAD,
    SCOPE_OMITTED_IN_ANSWER,
    STAGE_COMPARABILITY,
    STATUSES_ALLOWING_NO_REASON,
    UNAVAILABLE_MESSAGE_BY_REASON,
    UNIT_PERCENTAGE_POINT,
    UNMAPPED_ANSWERABILITY_REASONS,
    UNRESOLVED_WITHOUT_REASON_MESSAGE,
    MissingAnswerabilityStatusError,
    MissingAnswerSupportError,
    UnsupportedExecutionStatusError,
    UnsupportedResultShapeError,
    _topic_particle,
    build_answer_response,
)
from disclosure_agent.agent.calculator import (
    OPERATION_AGGREGATE as CALCULATOR_OPERATION_AGGREGATE,
)
from disclosure_agent.agent.calculator import (
    OPERATION_DELTA as CALCULATOR_OPERATION_DELTA,
)
from disclosure_agent.agent.calculator import (
    OPERATION_DELTA_PCT as CALCULATOR_OPERATION_DELTA_PCT,
)
from disclosure_agent.agent.calculator import (
    OPERATION_DIFF_PP as CALCULATOR_OPERATION_DIFF_PP,
)
from disclosure_agent.agent.calculator import (
    OPERATION_RATIO as CALCULATOR_OPERATION_RATIO,
)
from disclosure_agent.agent.calculator import (
    UNIT_PERCENTAGE_POINT as CALCULATOR_UNIT_PERCENTAGE_POINT,
)
from disclosure_agent.agent.derived_metrics import (
    OPERATION_RATIO,
    UNIT_PERCENT,
    DerivedMetric,
)
from disclosure_agent.agent.safety import (
    REASON_FORWARD_LOOKING_PREDICTION,
    REASON_INVESTMENT_ADVICE,
    STAGE_ANSWERABILITY_GATE,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ComparisonRelation,
    EventLinkRelation,
    EventLinkResolutionStatus,
    EventType,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    AnswerabilityDetail,
    CalculationResult,
    ComparisonResult,
    Event,
    EventHistory,
    EventLink,
    Evidence,
    ExecutionIssue,
    Fact,
    FieldChange,
    RawReference,
    ResultInputReference,
)
from disclosure_agent.periodic.extractor import BASE_CURRENCY_UNIT

SYNTHETIC_COMPANY = "테스트반도체(주)"
SYNTHETIC_DOC_ID = "periodic_SYNTHETIC0001"


# --------------------------------------------------------------------------
# 합성 입력
# --------------------------------------------------------------------------


def make_evidence(evidence_id: str = "evidence_SYNTHETIC_01") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        doc_id=SYNTHETIC_DOC_ID,
        location_type=EvidenceLocationType.TABLE,
        section_path=["SYNTHETIC-SECTION"],
        row_label="SYNTHETIC-ROW",
        raw_text="1,234",
    )


def make_fact(
    *,
    fact_id: str = "fact_SYNTHETIC_01",
    metric: MetricConcept = MetricConcept.REVENUE,
    fiscal_year: int | None = 2025,
    fiscal_period: str | None = None,
    accounting_scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
    raw_value: str | None = "1,234",
    raw_unit: str | None = "백만원",
    normalized_value: float | str | None = "1234000000",
    normalized_unit: str | None = "원",
    evidence_ids: list[str] | None = None,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        company=SYNTHETIC_COMPANY,
        metric=metric,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        accounting_scope=accounting_scope,
        raw_value=raw_value,
        raw_unit=raw_unit,
        normalized_value=normalized_value,
        normalized_unit=normalized_unit,
        evidence_ids=evidence_ids if evidence_ids is not None else ["evidence_SYNTHETIC_01"],
    )


def answerable_single_fact_result(**fact_kwargs) -> AgentExecutionResult:
    """Pattern A 성공 결과와 같은 모양(normalize_single_fact_result 참조)."""
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[make_fact(**fact_kwargs)],
        evidence=[make_evidence()],
    )


def refused_result(detail_reason: str) -> AgentExecutionResult:
    """Pattern F Gate 거부 결과와 같은 모양(normalization.refused_execution 참조)."""
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=AnswerabilityReason.OUT_OF_SCOPE_POLICY,
        answerability_detail=AnswerabilityDetail(
            stage=STAGE_ANSWERABILITY_GATE,
            reason=detail_reason,
            detail="SYNTHETIC-INTERNAL-DIAGNOSTIC('SYNTHETIC-EXPRESSION')",
        ),
    )


def make_event_history(
    *,
    document_ids: list[str],
    changes: list[FieldChange] | None = None,
    chain_complete: bool = True,
) -> EventHistory:
    return EventHistory(
        event_id="event_supply_contract_exchange_SYNTHETIC0011",
        document_ids=document_ids,
        changes=changes or [],
        chain_complete=chain_complete,
    )


# --------------------------------------------------------------------------
# A. COMPLETED + ANSWERABLE + 단일 Fact
# --------------------------------------------------------------------------


def test_single_fact_answer_is_deterministic_and_preserves_the_verdict() -> None:
    result = answerable_single_fact_result()

    response = build_answer_response(result)

    assert response.answer == f"{SYNTHETIC_COMPANY}의 2025년 연결 기준 매출액은 1,234백만원입니다."
    assert response.status is AnswerabilityStatus.ANSWERABLE
    assert response.reason is None
    assert response.facts == result.facts
    assert response.evidence == result.evidence
    assert response.facts[0].evidence_ids == [response.evidence[0].evidence_id]


def test_building_the_same_result_twice_yields_the_same_answer() -> None:
    result = answerable_single_fact_result()

    assert build_answer_response(result).answer == build_answer_response(result).answer


def test_separate_scope_is_not_rendered_as_consolidated() -> None:
    """Q01의 주요 실패 모드는 연결·별도 혼동이다. 문장 단계에서 다시 만들지 않는다."""
    result = answerable_single_fact_result(accounting_scope=FactAccountingScope.SEPARATE)

    answer = build_answer_response(result).answer

    assert "별도 기준" in answer
    assert "연결" not in answer


@pytest.mark.parametrize(
    "scope",
    sorted(SCOPE_OMITTED_IN_ANSWER, key=lambda item: item.value),
)
def test_unconfirmed_scope_is_omitted_instead_of_guessed(scope: FactAccountingScope) -> None:
    result = answerable_single_fact_result(accounting_scope=scope)

    answer = build_answer_response(result).answer

    assert "기준" not in answer
    assert "연결" not in answer
    assert "별도" not in answer
    assert answer == f"{SYNTHETIC_COMPANY}의 2025년 매출액은 1,234백만원입니다."


def test_raw_value_is_preferred_over_the_normalized_value() -> None:
    """정규화 값은 배수를 곱해 만든 파생값이다. 원문 표기가 있으면 그것을 쓴다."""
    answer = build_answer_response(answerable_single_fact_result()).answer

    assert "1,234백만원" in answer
    assert "1234000000" not in answer


def test_normalized_value_is_used_only_when_the_raw_value_is_absent() -> None:
    result = answerable_single_fact_result(raw_value=None, raw_unit=None)

    assert build_answer_response(result).answer.endswith("매출액은 1234000000원입니다.")


def test_raw_value_without_its_unit_falls_back_to_the_normalized_pair() -> None:
    """
    값과 단위는 짝이다.

    원문 값만 쓰면 "매출액은 1,234입니다"가 되어 자릿수를 알 수 없고, 원문 값에 정규화
    단위를 붙이면 배수가 맞지 않는 수치가 나간다.
    """
    result = answerable_single_fact_result(raw_value="1,234", raw_unit=None)

    answer = build_answer_response(result).answer

    assert answer.endswith("매출액은 1234000000원입니다.")
    assert "1,234원" not in answer


def test_raw_unit_without_its_value_falls_back_to_the_normalized_pair() -> None:
    result = answerable_single_fact_result(raw_value=None, raw_unit="백만원")

    assert build_answer_response(result).answer.endswith("매출액은 1234000000원입니다.")


def test_incomplete_raw_pair_without_a_complete_normalized_pair_fails_fast() -> None:
    result = answerable_single_fact_result(
        raw_value="1,234",
        raw_unit=None,
        normalized_value="1234000000",
        normalized_unit=None,
    )

    with pytest.raises(MissingAnswerSupportError):
        build_answer_response(result)


def test_normalized_value_without_its_unit_fails_fast() -> None:
    result = answerable_single_fact_result(
        raw_value=None,
        raw_unit=None,
        normalized_value="1234000000",
        normalized_unit=None,
    )

    with pytest.raises(MissingAnswerSupportError):
        build_answer_response(result)


def test_missing_fiscal_year_omits_the_period_phrase() -> None:
    result = answerable_single_fact_result(fiscal_year=None)

    assert build_answer_response(result).answer == (
        f"{SYNTHETIC_COMPANY}의 연결 기준 매출액은 1,234백만원입니다."
    )


def test_full_year_fiscal_period_keeps_the_year_only_wording() -> None:
    result = answerable_single_fact_result(fiscal_period="FY")

    assert build_answer_response(result).answer == (
        f"{SYNTHETIC_COMPANY}의 2025년 연결 기준 매출액은 1,234백만원입니다."
    )


def test_non_annual_fiscal_period_is_rejected_instead_of_written_as_a_year() -> None:
    """분기 값을 연간처럼 적으면 기간이 틀린 답이 된다."""
    result = answerable_single_fact_result(fiscal_period="Q1")

    with pytest.raises(UnsupportedResultShapeError, match="fiscal_period"):
        build_answer_response(result)


def test_supported_metric_labels_take_the_expected_particle() -> None:
    result = answerable_single_fact_result(metric=MetricConcept.OPERATING_INCOME)

    assert build_answer_response(result).answer == (
        f"{SYNTHETIC_COMPANY}의 2025년 연결 기준 영업이익은 1,234백만원입니다."
    )


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("매출액", "은"),
        ("영업이익", "은"),
        # 현재 지원 지표는 둘 다 받침이 있어 '은'만 나온다. 조사 계산을 남겨 두는 이유는
        # 받침 없는 표기가 추가되는 순간 하드코딩된 '은'이 "보유 주식수은"을 조용히
        # 만들어 내기 때문이다. 실패가 눈에 띄지 않는 쪽이 더 나쁘다.
        ("보유 주식수", "는"),
        ("보유 비율", "은"),
    ],
)
def test_topic_particle_follows_the_final_consonant(word: str, expected: str) -> None:
    assert _topic_particle(word) == expected


# --------------------------------------------------------------------------
# B~D. COMPLETED + UNANSWERABLE
# --------------------------------------------------------------------------


def test_not_found_uses_the_search_failure_wording() -> None:
    """
    NOT_FOUND는 시스템 한계 가능성을 남기는 표현을 쓴다.

    pattern_f_safety.md §8 원칙 2. 기간 밖 거부의 확정 표현과 섞이면 검색 실패와 데이터
    부재가 사용자에게 같은 문장으로 나간다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=AnswerabilityReason.NOT_FOUND,
        answerability_detail=AnswerabilityDetail(
            stage="filing_discovery",
            reason="no_matching_filing",
            detail="SYNTHETIC-DETAIL",
        ),
    )

    response = build_answer_response(result)

    assert response.answer == "요청하신 정보를 공시에서 찾지 못했습니다."
    assert response.status is AnswerabilityStatus.UNANSWERABLE
    assert response.reason is AnswerabilityReason.NOT_FOUND
    assert response.facts == []
    assert response.evidence == []


def test_out_of_data_range_uses_the_definitive_wording() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=AnswerabilityReason.OUT_OF_DATA_RANGE,
    )

    answer = build_answer_response(result).answer

    assert answer == "요청하신 기간의 공시 데이터를 보유하고 있지 않습니다."
    assert answer != UNAVAILABLE_MESSAGE_BY_REASON[AnswerabilityReason.NOT_FOUND]


def test_not_comparable_says_the_values_could_not_be_compared() -> None:
    """
    NOT_COMPARABLE은 "찾지 못했다"가 아니다.

    코퍼스를 끝까지 읽고 값을 확보한 뒤에 내린 판정이다(normalization의 STAGE_COMPARABILITY
    행). NOT_FOUND 문장과 섞이면 사용자는 다시 찾으면 된다고 읽지만, 실제로는 비교 전제가
    맞지 않아 질문을 바꿔야 하는 상황이다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=AnswerabilityReason.NOT_COMPARABLE,
        answerability_detail=AnswerabilityDetail(
            stage=STAGE_COMPARABILITY,
            reason=REASON_INCOMPARABLE_CALCULATION_INPUTS,
            detail="SYNTHETIC-DETAIL",
        ),
    )

    response = build_answer_response(result)

    assert response.answer == (
        "확보한 값들의 기준, 단위 또는 개념이 서로 달라 직접 비교할 수 없습니다."
    )
    assert response.status is AnswerabilityStatus.UNANSWERABLE
    assert response.reason is AnswerabilityReason.NOT_COMPARABLE
    assert response.answer != UNAVAILABLE_MESSAGE_BY_REASON[AnswerabilityReason.NOT_FOUND]
    assert response.facts == []
    assert response.evidence == []


@pytest.mark.parametrize(
    "detail_reason",
    [REASON_INCOMPARABLE_CALCULATION_INPUTS, REASON_INCOMPARABLE_PERIODS],
)
def test_not_comparable_sentence_does_not_follow_the_diagnostic_detail(
    detail_reason: str,
) -> None:
    """
    두 detail 사유가 같은 사용자 문장으로 나간다.

    normalization이 둘을 하나의 canonical 사유로 모은 것은 의도된 것이고, 문장은 그 사유를
    따라간다. detail로 문장을 갈라 쓰면 사용자 문구가 진단 어휘를 따라가고, 사유별 대응표가
    아니라 detail별 대응표가 하나 더 생긴다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=AnswerabilityReason.NOT_COMPARABLE,
        answerability_detail=AnswerabilityDetail(
            stage=STAGE_COMPARABILITY,
            reason=detail_reason,
            detail="SYNTHETIC-DETAIL",
        ),
    )

    answer = build_answer_response(result).answer

    assert answer == UNAVAILABLE_MESSAGE_BY_REASON[AnswerabilityReason.NOT_COMPARABLE]
    assert detail_reason not in answer


def test_investment_advice_refusal_is_deterministic() -> None:
    response = build_answer_response(refused_result(REASON_INVESTMENT_ADVICE))

    assert response.answer == (
        "투자 판단·추천 요청은 답변 범위 밖입니다. 공시에 기재된 사실 조회는 계속 지원합니다."
    )
    assert response.status is AnswerabilityStatus.UNANSWERABLE
    assert response.reason is AnswerabilityReason.OUT_OF_SCOPE_POLICY


def test_forward_looking_refusal_is_deterministic() -> None:
    response = build_answer_response(refused_result(REASON_FORWARD_LOOKING_PREDICTION))

    assert response.answer == (
        "아직 공시되지 않은 결과에 대한 예측 요청은 답변 범위 밖입니다. "
        "공시에 기재된 확정 사실·일정 조회는 계속 지원합니다."
    )
    assert response.status is AnswerabilityStatus.UNANSWERABLE
    assert response.reason is AnswerabilityReason.OUT_OF_SCOPE_POLICY


def test_two_policy_refusals_do_not_collapse_into_one_sentence() -> None:
    """F3와 F4는 canonical reason이 같다. 문장까지 같아지면 두 판정이 구분되지 않는다."""
    advice = build_answer_response(refused_result(REASON_INVESTMENT_ADVICE)).answer
    prediction = build_answer_response(refused_result(REASON_FORWARD_LOOKING_PREDICTION)).answer

    assert advice != prediction


def test_policy_refusal_does_not_leak_the_internal_diagnostic_detail() -> None:
    """
    safety.py의 detail에는 어떤 규칙과 어떤 표현에 걸렸는지가 들어 있다.

    그대로 노출하면 탐지 규칙이 응답으로 새어 나간다. 뜻은 같게 두고 진단 부분만 뺀다.
    """
    result = refused_result(REASON_INVESTMENT_ADVICE)

    answer = build_answer_response(result).answer

    assert result.answerability_detail is not None
    assert result.answerability_detail.detail not in answer
    assert "SYNTHETIC-INTERNAL-DIAGNOSTIC" not in answer
    assert "SYNTHETIC-EXPRESSION" not in answer


def test_unknown_policy_detail_reason_falls_back_without_inventing_a_cause() -> None:
    response = build_answer_response(refused_result("synthetic_unmapped_category"))

    assert response.answer == (
        UNAVAILABLE_MESSAGE_BY_REASON[AnswerabilityReason.OUT_OF_SCOPE_POLICY]
    )


def test_refusal_answer_never_offers_a_substitute_value() -> None:
    """거부 사유를 대체 답변으로 바꾸지 않는다(pattern_f_safety.md §8 원칙 3·4)."""
    for reason in (
        AnswerabilityReason.OUT_OF_DATA_RANGE,
        AnswerabilityReason.OUT_OF_SCOPE_DATA,
        AnswerabilityReason.OUT_OF_SCOPE_POLICY,
    ):
        result = AgentExecutionResult(
            execution_status=ExecutionStatus.COMPLETED,
            answerability_status=AnswerabilityStatus.UNANSWERABLE,
            answerability_reason=reason,
        )

        answer = build_answer_response(result).answer

        assert not any(character.isdigit() for character in answer), answer


def test_unanswerable_without_a_reason_fails_fast() -> None:
    """
    사유 없는 UNANSWERABLE은 producer가 만들지 않는다.

    refused_execution은 answerability_reason을 필수 키워드로 받고, _stopped_execution이
    쓰는 ANSWERABILITY_BY_STOP은 모든 항목이 사유를 채운다. 그런데도 사유 없이 들어오면
    "왜 답하지 못했는가"가 응답에서 사라지므로, 사유를 단정하지 않는 문장으로 덮지 않는다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
    )

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert "canonical 사유" in str(error.value)


# --------------------------------------------------------------------------
# E. COMPLETED + UNRESOLVED
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [AnswerabilityReason.AMBIGUOUS_MATCH, AnswerabilityReason.AMBIGUOUS_LINK],
)
def test_unresolved_says_the_candidates_could_not_be_narrowed(
    reason: AnswerabilityReason,
) -> None:
    """
    UNRESOLVED는 NOT_FOUND와 다르다.

    후보를 찾았는데 하나로 좁히지 못한 것이다. "정보가 없다"로 만들면 사용자가 질문을
    좁혀야 한다는 사실이 사라진다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNRESOLVED,
        answerability_reason=reason,
        answerability_detail=AnswerabilityDetail(
            stage="filing_discovery",
            reason="ambiguous_root",
            detail="SYNTHETIC-DETAIL",
        ),
    )

    response = build_answer_response(result)

    assert response.status is AnswerabilityStatus.UNRESOLVED
    assert response.reason is reason
    assert "확정할 수 없" in response.answer
    assert response.answer != UNAVAILABLE_MESSAGE_BY_REASON[AnswerabilityReason.NOT_FOUND]
    assert "찾지 못했" not in response.answer


def test_unresolved_without_a_reason_keeps_the_unresolved_meaning() -> None:
    """Pattern E가 루트를 확정하지 못한 경우다(execution_result_mapping.md §3)."""
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNRESOLVED,
        event_links=[
            EventLink(
                link_id="link_SYNTHETIC_01",
                source_doc_id="exchange_SYNTHETIC0013",
                relation=EventLinkRelation.CORRECTION_OF,
                raw_reference=RawReference(original_filing_date="YYYY-MM-DD"),
                candidate_doc_ids=["exchange_SYNTHETIC0011", "exchange_SYNTHETIC0012"],
                resolution_status=EventLinkResolutionStatus.AMBIGUOUS,
            )
        ],
    )

    response = build_answer_response(result)

    assert response.answer == UNRESOLVED_WITHOUT_REASON_MESSAGE
    assert "찾지 못했" not in response.answer
    assert response.event_links == result.event_links


# --------------------------------------------------------------------------
# F·G. BLOCKED / FAILED fail-fast
# --------------------------------------------------------------------------


def test_blocked_execution_is_rejected() -> None:
    """
    BLOCKED를 "답이 없습니다"로 번역하지 않는다(pattern_f_safety.md §8 원칙 5).

    GUARD-MULTI-COMPANY 회귀가 이 모양이다. 억지로 옮기면 두 기업 비교 질문이
    "코퍼스에 답이 없다"는 없던 판정으로 나간다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.BLOCKED,
        execution_issue=ExecutionIssue(
            stage="routing",
            reason="unimplemented_intent",
            detail="SYNTHETIC-DETAIL",
        ),
    )

    with pytest.raises(UnsupportedExecutionStatusError) as error:
        build_answer_response(result)

    assert error.value.execution_status is ExecutionStatus.BLOCKED


def test_failed_execution_is_rejected() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.FAILED,
        execution_issue=ExecutionIssue(
            stage="parsing",
            reason="parse_failed",
            detail="SYNTHETIC-DETAIL",
        ),
    )

    with pytest.raises(UnsupportedExecutionStatusError) as error:
        build_answer_response(result)

    assert error.value.execution_status is ExecutionStatus.FAILED


def test_completed_without_a_verdict_is_rejected() -> None:
    result = AgentExecutionResult(execution_status=ExecutionStatus.COMPLETED)

    with pytest.raises(MissingAnswerabilityStatusError):
        build_answer_response(result)


# --------------------------------------------------------------------------
# H. ANSWERABLE인데 근거가 없는 결과
# --------------------------------------------------------------------------


def test_answerable_without_any_structured_support_does_not_hallucinate() -> None:
    """Pattern A의 최소 불변식은 ANSWERABLE + facts >= 1이다."""
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
    )

    with pytest.raises(MissingAnswerSupportError):
        build_answer_response(result)


def test_answerable_fact_without_any_value_does_not_hallucinate() -> None:
    result = answerable_single_fact_result(
        raw_value=None,
        raw_unit=None,
        normalized_value=None,
        normalized_unit=None,
    )

    with pytest.raises(MissingAnswerSupportError):
        build_answer_response(result)


def test_events_only_result_has_no_defined_wording_yet() -> None:
    """현재 어느 Pattern도 Event를 만들지 않는다. 표현을 즉석에서 정하지 않는다."""
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        events=[
            Event(
                event_id="event_SYNTHETIC_01",
                event_type=EventType.SUPPLY_CONTRACT,
                company=SYNTHETIC_COMPANY,
                source_doc_id="exchange_SYNTHETIC0011",
            )
        ],
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


# --------------------------------------------------------------------------
# I. 여러 Fact
# --------------------------------------------------------------------------


def test_multiple_facts_are_rejected_instead_of_silently_taking_the_first() -> None:
    """
    첫 건만 고르면 여러 값을 물은 질문에 한 값만 답하고도 완전한 답으로 읽힌다.

    Router가 미등록 intent를 가장 가까운 handler로 대신 처리하지 않는 것과 같은 이유다.
    """
    first = make_fact(fact_id="fact_SYNTHETIC_01", fiscal_year=2025)
    second = make_fact(fact_id="fact_SYNTHETIC_02", fiscal_year=2024, raw_value="4,321")
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[first, second],
        evidence=[make_evidence()],
    )

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert "2건" in str(error.value)


def test_facts_and_event_histories_together_have_no_defined_wording_yet() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[make_fact()],
        event_histories=[make_event_history(document_ids=["exchange_SYNTHETIC0011"])],
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


# --------------------------------------------------------------------------
# Pattern D — 우열 판정(ComparisonResult)
#
# 이 절이 고정하는 것:
#     - 우열 판정이 실린 결과는 단일 Fact 문장보다 먼저 비교 문장으로 간다(Q10/Q11의 500 원인).
#     - 관계도 값도 여기서 다시 만들지 않는다. relation과 calculation.value가 정본이다.
#     - 표현 방법이 정해지지 않은 모양은 추측하지 않고 멈춘다.
#
# 합성 값의 수치는 실제 공시와 무관하다. 두 기업의 자릿수와 관계만 시험에 필요하다.
# --------------------------------------------------------------------------

LEFT_COMPANY = "합성조선(주)"
RIGHT_COMPANY = "합성중공업(주)"

# Contract 수준 Fact producer가 싣는 정규화 단위.
#
# periodic.extractor.normalize_value_and_unit이 만드는 값이며, event 계층과 Contract 예제도
# 같은 값을 쓴다. 사용자 문장에 그대로 실리면 "2000000000KRW"가 된다.
CANONICAL_NORMALIZED_UNIT = "KRW"

LEFT_MARGIN_ID = "calculation_operating_margin_2025_LEFT"
RIGHT_MARGIN_ID = "calculation_operating_margin_2025_RIGHT"
LEFT_AGGREGATE_ID = "calculation_investment_amount_LEFT"
RIGHT_AGGREGATE_ID = "calculation_investment_amount_RIGHT"


def make_comparison_fact(
    *,
    fact_id: str,
    company: str,
    metric: MetricConcept = MetricConcept.REVENUE,
    raw_value: str | None = "1,000",
    raw_unit: str | None = "백만원",
    normalized_value: float | str | None = "1000000000",
    normalized_unit: str | None = CANONICAL_NORMALIZED_UNIT,
    fiscal_year: int | None = 2025,
    accounting_scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
) -> Fact:
    """기업 축이 필요한 Fact. make_fact는 기업이 하나로 고정돼 있어 여기서는 쓸 수 없다."""
    return Fact(
        fact_id=fact_id,
        company=company,
        metric=metric,
        fiscal_year=fiscal_year,
        accounting_scope=accounting_scope,
        raw_value=raw_value,
        raw_unit=raw_unit,
        normalized_value=normalized_value,
        normalized_unit=normalized_unit,
        evidence_ids=["evidence_SYNTHETIC_01"],
    )


def fact_reference(fact_id: str) -> ResultInputReference:
    return ResultInputReference(input_type=ResultInputType.FACT, input_id=fact_id)


def calculation_reference(calculation_id: str) -> ResultInputReference:
    return ResultInputReference(input_type=ResultInputType.CALCULATION, input_id=calculation_id)


def make_margin(
    *,
    calculation_id: str,
    numerator: Fact,
    denominator: Fact,
    value: str,
    unit: str = UNIT_PERCENT,
) -> CalculationResult:
    """calculator.ratio가 만드는 것과 같은 모양(분자, 분모 순서)."""
    return CalculationResult(
        calculation_id=calculation_id,
        operation=OPERATION_RATIO,
        inputs=[fact_reference(numerator.fact_id), fact_reference(denominator.fact_id)],
        value=value,
        unit=unit,
        evidence_ids=["evidence_SYNTHETIC_01"],
    )


def q10_result(
    *,
    relation: ComparisonRelation = ComparisonRelation.LEFT_GREATER,
) -> AgentExecutionResult:
    """
    Q10 계열의 성공 결과 모양: Fact 2 · Calculation 0 · Comparison 1.

    handlers.cross_entity_compare._execute_source_metric_comparison이 만드는 것과 같은 개수이며,
    개수의 근거는 evidence_and_answerability.md의 Calculation cardinality다.
    """
    left = make_comparison_fact(
        fact_id="fact_SYNTHETIC_LEFT_REVENUE",
        company=LEFT_COMPANY,
        raw_value="2,000",
        normalized_value="2000000000",
    )
    right = make_comparison_fact(
        fact_id="fact_SYNTHETIC_RIGHT_REVENUE",
        company=RIGHT_COMPANY,
        raw_value="1,000",
        normalized_value="1000000000",
    )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[left, right],
        evidence=[make_evidence()],
        comparisons=[
            ComparisonResult(
                comparison_id="comparison_revenue_2025_LEFT_vs_RIGHT",
                left=fact_reference(left.fact_id),
                right=fact_reference(right.fact_id),
                relation=relation,
            )
        ],
    )


def q11_result(
    *,
    relation: ComparisonRelation = ComparisonRelation.LEFT_GREATER,
    left_margin: str = "12.50",
    right_margin: str = "5.00",
) -> AgentExecutionResult:
    """
    Q11 계열의 성공 결과 모양: Fact 4 · Calculation 2 · Comparison 1.

    원천 Fact의 값은 기업별 영업이익률이 12.50%와 5.00%가 되도록 잡았다. 기본값과 같게 두면
    "다시 계산해도 같은 값"이 되어 재계산 여부를 시험할 수 없으므로, 그것을 보는 테스트는
    left_margin/right_margin을 원천과 어긋나게 넘긴다.
    """
    left_income = make_comparison_fact(
        fact_id="fact_SYNTHETIC_LEFT_OPERATING_INCOME",
        company=LEFT_COMPANY,
        metric=MetricConcept.OPERATING_INCOME,
        raw_value="250",
        normalized_value="250000000",
    )
    left_revenue = make_comparison_fact(
        fact_id="fact_SYNTHETIC_LEFT_REVENUE",
        company=LEFT_COMPANY,
        raw_value="2,000",
        normalized_value="2000000000",
    )
    right_income = make_comparison_fact(
        fact_id="fact_SYNTHETIC_RIGHT_OPERATING_INCOME",
        company=RIGHT_COMPANY,
        metric=MetricConcept.OPERATING_INCOME,
        raw_value="50",
        normalized_value="50000000",
    )
    right_revenue = make_comparison_fact(
        fact_id="fact_SYNTHETIC_RIGHT_REVENUE",
        company=RIGHT_COMPANY,
        raw_value="1,000",
        normalized_value="1000000000",
    )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[left_income, left_revenue, right_income, right_revenue],
        evidence=[make_evidence()],
        calculations=[
            make_margin(
                calculation_id=LEFT_MARGIN_ID,
                numerator=left_income,
                denominator=left_revenue,
                value=left_margin,
            ),
            make_margin(
                calculation_id=RIGHT_MARGIN_ID,
                numerator=right_income,
                denominator=right_revenue,
                value=right_margin,
            ),
        ],
        comparisons=[
            ComparisonResult(
                comparison_id="comparison_operating_margin_2025_LEFT_vs_RIGHT",
                left=calculation_reference(LEFT_MARGIN_ID),
                right=calculation_reference(RIGHT_MARGIN_ID),
                relation=relation,
            )
        ],
    )


def q12_result(
    *,
    relation: ComparisonRelation = ComparisonRelation.LEFT_GREATER,
    left_total: str = "3000",
    right_total: str = "500",
) -> AgentExecutionResult:
    """Q12 shape: investment_amount Fact 2+1, aggregate 2, comparison 1."""
    left_first = make_comparison_fact(
        fact_id="fact_SYNTHETIC_LEFT_INVESTMENT_A",
        company=LEFT_COMPANY,
        metric=MetricConcept.INVESTMENT_AMOUNT,
        raw_value="1000",
        raw_unit="원",
        normalized_value="1000",
        fiscal_year=None,
        accounting_scope=FactAccountingScope.NOT_APPLICABLE,
    )
    left_second = make_comparison_fact(
        fact_id="fact_SYNTHETIC_LEFT_INVESTMENT_B",
        company=LEFT_COMPANY,
        metric=MetricConcept.INVESTMENT_AMOUNT,
        raw_value="2000",
        raw_unit="원",
        normalized_value="2000",
        fiscal_year=None,
        accounting_scope=FactAccountingScope.NOT_APPLICABLE,
    )
    right = make_comparison_fact(
        fact_id="fact_SYNTHETIC_RIGHT_INVESTMENT_A",
        company=RIGHT_COMPANY,
        metric=MetricConcept.INVESTMENT_AMOUNT,
        raw_value="500",
        raw_unit="원",
        normalized_value="500",
        fiscal_year=None,
        accounting_scope=FactAccountingScope.NOT_APPLICABLE,
    )
    left_aggregate = CalculationResult(
        calculation_id=LEFT_AGGREGATE_ID,
        operation=OPERATION_AGGREGATE,
        inputs=[
            fact_reference(left_first.fact_id),
            fact_reference(left_second.fact_id),
        ],
        value=left_total,
        unit=CANONICAL_NORMALIZED_UNIT,
        evidence_ids=["evidence_SYNTHETIC_01"],
    )
    right_aggregate = CalculationResult(
        calculation_id=RIGHT_AGGREGATE_ID,
        operation=OPERATION_AGGREGATE,
        inputs=[fact_reference(right.fact_id)],
        value=right_total,
        unit=CANONICAL_NORMALIZED_UNIT,
        evidence_ids=["evidence_SYNTHETIC_01"],
    )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[left_first, left_second, right],
        evidence=[make_evidence()],
        calculations=[left_aggregate, right_aggregate],
        comparisons=[
            ComparisonResult(
                comparison_id="comparison_investment_amount_LEFT_vs_RIGHT",
                left=calculation_reference(LEFT_AGGREGATE_ID),
                right=calculation_reference(RIGHT_AGGREGATE_ID),
                relation=relation,
            )
        ],
    )


def test_q10_shape_renders_both_values_and_the_relation() -> None:
    """Fact 2 · Calculation 0 · Comparison 1이 하나의 답변으로 나간다."""
    result = q10_result()

    response = build_answer_response(result)

    assert response.answer == (
        f"{LEFT_COMPANY}의 2025년 연결 기준 매출액은 2,000백만원(정규화 2000000000원), "
        f"{RIGHT_COMPANY}의 2025년 연결 기준 매출액은 1,000백만원(정규화 1000000000원)입니다. "
        f"{LEFT_COMPANY} 쪽이 더 큽니다."
    )
    assert response.status is AnswerabilityStatus.ANSWERABLE
    assert response.reason is None
    assert len(response.facts) == 2
    assert response.facts == result.facts
    assert response.evidence == result.evidence


def test_q11_shape_renders_the_derived_values_and_the_relation() -> None:
    """Fact 4 · Calculation 2 · Comparison 1이 하나의 답변으로 나간다."""
    result = q11_result()

    response = build_answer_response(result)

    assert response.answer == (
        f"{LEFT_COMPANY}의 2025년 연결 기준 영업이익률은 12.50%, "
        f"{RIGHT_COMPANY}의 2025년 연결 기준 영업이익률은 5.00%입니다. "
        f"{LEFT_COMPANY} 쪽이 더 큽니다."
    )
    assert response.status is AnswerabilityStatus.ANSWERABLE
    assert response.reason is None
    assert len(response.facts) == 4
    assert response.facts == result.facts


def test_q12_shape_renders_aggregate_values_and_preserves_the_result() -> None:
    """Builder는 합계와 우열을 다시 만들지 않고 Q12 결과를 읽기만 한다."""
    result = q12_result()
    before = result.model_copy(deep=True)

    response = build_answer_response(result)

    assert OPERATION_AGGREGATE == CALCULATOR_OPERATION_AGGREGATE
    assert response.answer == (
        f"{LEFT_COMPANY}의 시설투자 합계는 3000원이고, "
        f"{RIGHT_COMPANY}의 시설투자 합계는 500원입니다. "
        f"{LEFT_COMPANY} 쪽이 더 큽니다."
    )
    assert response.status is AnswerabilityStatus.ANSWERABLE
    assert response.reason is None
    assert response.facts == result.facts
    assert result == before


def test_q12_aggregate_values_are_not_recomputed_from_input_facts() -> None:
    result = q12_result(
        relation=ComparisonRelation.RIGHT_GREATER,
        left_total="17",
        right_total="29",
    )

    answer = build_answer_response(result).answer

    assert "17원" in answer
    assert "29원" in answer
    assert "3000원" not in answer
    assert "500원" not in answer
    assert answer.endswith(f"{RIGHT_COMPANY} 쪽이 더 큽니다.")


@pytest.mark.parametrize(
    ("relation", "expected_clause"),
    [
        (ComparisonRelation.LEFT_GREATER, f"{LEFT_COMPANY} 쪽이 더 큽니다."),
        (ComparisonRelation.RIGHT_GREATER, f"{RIGHT_COMPANY} 쪽이 더 큽니다."),
        (ComparisonRelation.EQUAL, "두 값은 같습니다."),
    ],
)
def test_q12_relation_is_read_without_comparing_aggregate_values(
    relation: ComparisonRelation,
    expected_clause: str,
) -> None:
    result = q12_result(
        relation=relation,
        left_total="3000",
        right_total="500",
    )

    answer = build_answer_response(result).answer

    assert "3000원" in answer
    assert "500원" in answer
    assert answer.endswith(expected_clause)


def test_q12_reference_chain_is_independent_of_fact_and_input_order() -> None:
    baseline = q12_result()
    expected_answer = build_answer_response(baseline).answer
    result = q12_result()
    result.facts.reverse()
    result.calculations[0].inputs.reverse()
    before = result.model_copy(deep=True)

    response = build_answer_response(result)

    comparison = result.comparisons[0]
    calculations = {
        item.calculation_id: item for item in result.calculations
    }

    assert response.answer == expected_answer
    assert comparison.left == calculation_reference(LEFT_AGGREGATE_ID)
    assert comparison.right == calculation_reference(RIGHT_AGGREGATE_ID)
    assert {
        item.input_id for item in calculations[LEFT_AGGREGATE_ID].inputs
    } == {
        "fact_SYNTHETIC_LEFT_INVESTMENT_A",
        "fact_SYNTHETIC_LEFT_INVESTMENT_B",
    }
    assert [
        item.input_id for item in calculations[RIGHT_AGGREGATE_ID].inputs
    ] == ["fact_SYNTHETIC_RIGHT_INVESTMENT_A"]
    assert all(
        item.input_type is ResultInputType.FACT
        for calculation in result.calculations
        for item in calculation.inputs
    )
    assert result == before


def _malform_q12_result(
    result: AgentExecutionResult,
    case: str,
) -> None:
    if case == "one-aggregate":
        result.calculations.pop()
    elif case == "no-comparison":
        result.comparisons.clear()
    elif case == "two-comparisons":
        result.comparisons.append(result.comparisons[0].model_copy(deep=True))
    elif case == "comparison-targets-fact":
        result.comparisons[0].left = fact_reference(result.facts[0].fact_id)
    elif case == "dangling-calculation":
        result.comparisons[0].right = calculation_reference(
            "calculation_SYNTHETIC_MISSING"
        )
    elif case == "nested-calculation-input":
        result.calculations[0].inputs = [
            calculation_reference(RIGHT_AGGREGATE_ID)
        ]
    elif case == "missing-input-fact":
        result.facts = [
            fact
            for fact in result.facts
            if fact.fact_id != "fact_SYNTHETIC_LEFT_INVESTMENT_A"
        ]
    elif case == "mixed-input-companies":
        result.facts[1].company = RIGHT_COMPANY
    elif case == "same-aggregate-company":
        result.facts[2].company = LEFT_COMPANY
    elif case == "unsupported-input-metric":
        result.facts[0].metric = MetricConcept.REVENUE
    elif case == "mixed-operation":
        result.calculations[0].operation = OPERATION_RATIO
    elif case == "missing-value":
        result.calculations[0].value = None
    elif case == "non-numeric-value":
        result.calculations[0].value = "SYNTHETIC-NOT-A-NUMBER"
    elif case == "missing-unit":
        result.calculations[0].unit = None
    elif case == "unsupported-unit":
        result.calculations[0].unit = "SYNTHETIC-UNIT"
    elif case == "mismatched-units":
        result.calculations[1].unit = BASE_CURRENCY_UNIT
    elif case == "same-comparison-target":
        result.comparisons[0].right = result.comparisons[0].left
    elif case == "unreferenced-fact":
        result.facts.append(
            make_comparison_fact(
                fact_id="fact_SYNTHETIC_UNUSED_INVESTMENT",
                company=LEFT_COMPANY,
                metric=MetricConcept.INVESTMENT_AMOUNT,
                fiscal_year=None,
                accounting_scope=FactAccountingScope.NOT_APPLICABLE,
            )
        )
    elif case == "duplicate-input-fact":
        result.calculations[0].inputs.append(
            result.calculations[0].inputs[0]
        )
    else:  # pragma: no cover - the parametrization is the exhaustive caller
        raise AssertionError(f"unknown synthetic malformed case: {case}")


@pytest.mark.parametrize(
    "case",
    [
        "one-aggregate",
        "no-comparison",
        "two-comparisons",
        "comparison-targets-fact",
        "dangling-calculation",
        "nested-calculation-input",
        "missing-input-fact",
        "mixed-input-companies",
        "same-aggregate-company",
        "unsupported-input-metric",
        "mixed-operation",
        "missing-value",
        "non-numeric-value",
        "missing-unit",
        "unsupported-unit",
        "mismatched-units",
        "same-comparison-target",
        "unreferenced-fact",
        "duplicate-input-fact",
    ],
)
def test_malformed_q12_aggregate_shapes_fail_closed(case: str) -> None:
    result = q12_result()
    _malform_q12_result(result, case)

    with pytest.raises((UnsupportedResultShapeError, MissingAnswerSupportError)):
        build_answer_response(result)


def test_building_the_same_comparison_twice_yields_the_same_answer() -> None:
    result = q11_result()

    assert build_answer_response(result).answer == build_answer_response(result).answer


@pytest.mark.parametrize("factory", [q10_result, q11_result], ids=["q10", "q11"])
def test_comparison_is_rendered_instead_of_the_single_fact_sentence(factory) -> None:
    """
    우열 판정이 있으면 Fact가 여러 건이어도 단일 Fact 경로로 가지 않는다.

    이 순서가 뒤집혀 있던 것이 Q10·Q11이 API에서 500으로 나가던 원인이다
    (test_multiple_facts_are_rejected_instead_of_silently_taking_the_first이 그 정지를 고정한다).
    판정을 무시하고 Fact 하나만 답하는 것도 같은 이유로 막는다.
    """
    result = factory()

    answer = build_answer_response(result).answer

    assert LEFT_COMPANY in answer
    assert RIGHT_COMPANY in answer
    assert "쪽이 더 큽니다" in answer


@pytest.mark.parametrize(
    ("relation", "expected_clause"),
    [
        (ComparisonRelation.LEFT_GREATER, f"{LEFT_COMPANY} 쪽이 더 큽니다."),
        (ComparisonRelation.RIGHT_GREATER, f"{RIGHT_COMPANY} 쪽이 더 큽니다."),
        (ComparisonRelation.EQUAL, "두 값은 같습니다."),
    ],
)
def test_relation_is_read_from_the_result_not_recomputed_from_the_values(
    relation: ComparisonRelation,
    expected_clause: str,
) -> None:
    """
    관계는 ComparisonResult.relation이 정본이다.

    합성 입력의 왼쪽 값이 오른쪽보다 크므로, Builder가 값을 다시 견주면 세 경우 모두 왼쪽이 큰
    문장이 나온다. 그렇게 되면 Agent가 내린 판정과 답변이 갈릴 수 있고, 그 어긋남은 응답 어디에도
    남지 않는다(agent_patterns.md "Agent computes and judges. Builder presents.").
    """
    result = q10_result(relation=relation)

    answer = build_answer_response(result).answer

    assert answer.endswith(expected_clause)


def test_derived_values_are_read_from_the_calculations_not_recomputed_from_the_facts() -> None:
    """
    Q11의 비율을 원천 Fact에서 다시 계산하지 않는다.

    합성 입력의 Fact는 12.50%와 5.00%를 가리키지만 결과에 실린 파생값은 1.00%와 7.00%다. 정상
    경로에서는 두 값이 같으므로, 어긋나게 두어야 어느 쪽을 읽었는지 드러난다. 다시 계산하면
    반올림 자릿수가 계산 계층과 달라질 수 있고, 그때 답변에 실린 수치와 우열 판정의 근거가 된
    수치가 서로 다른 값이 된다.
    """
    result = q11_result(
        relation=ComparisonRelation.RIGHT_GREATER,
        left_margin="1.00",
        right_margin="7.00",
    )

    answer = build_answer_response(result).answer

    assert "1.00%" in answer
    assert "7.00%" in answer
    assert "12.50" not in answer
    assert "5.00" not in answer
    assert answer.endswith(f"{RIGHT_COMPANY} 쪽이 더 큽니다.")


def test_q11_comparison_keeps_referencing_the_existing_calculations() -> None:
    """
    우열 판정이 참조하는 provenance를 Builder가 바꾸지 않는다.

    Comparison -> Calculation -> Fact 두 단계가 그대로 남아야 답변에 실린 비율이 어느 원천에서
    나왔는지 되짚을 수 있다. Builder가 Fact에서 비율을 다시 만들면 그 사슬의 가운데가 끊기고,
    답변의 수치와 결과의 수치가 서로 다른 계보를 갖는다.
    """
    result = q11_result()
    before = result.model_copy(deep=True)

    response = build_answer_response(result)

    comparison = result.comparisons[0]

    assert comparison.left == calculation_reference(LEFT_MARGIN_ID)
    assert comparison.right == calculation_reference(RIGHT_MARGIN_ID)

    left_margin, right_margin = result.calculations

    assert [item.input_id for item in left_margin.inputs] == [
        "fact_SYNTHETIC_LEFT_OPERATING_INCOME",
        "fact_SYNTHETIC_LEFT_REVENUE",
    ]
    assert [item.input_id for item in right_margin.inputs] == [
        "fact_SYNTHETIC_RIGHT_OPERATING_INCOME",
        "fact_SYNTHETIC_RIGHT_REVENUE",
    ]
    assert all(item.input_type is ResultInputType.FACT for item in left_margin.inputs)

    # Builder는 읽기만 한다. 계산을 더하지도, 참조를 고쳐 쓰지도 않는다.
    assert result == before
    assert len(result.calculations) == 2

    # AnswerResponse에는 calculations/comparisons 자리가 없다. 이번 범위에서 Contract를 넓히지
    # 않았다는 사실을 함께 고정한다.
    payload = response.model_dump()

    assert "calculations" not in payload
    assert "comparisons" not in payload


def test_comparison_and_event_histories_together_have_no_defined_wording_yet() -> None:
    result = q10_result()
    result.event_histories = [make_event_history(document_ids=["exchange_SYNTHETIC0011"])]

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_more_than_one_comparison_is_rejected_instead_of_taking_the_first() -> None:
    result = q10_result()
    result.comparisons = [result.comparisons[0], result.comparisons[0]]

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert "2건" in str(error.value)


def test_comparing_a_fact_with_a_calculation_has_no_defined_wording_yet() -> None:
    """원천 값과 파생값을 견준 판정이다. 한쪽 표기에 맞추면 다른 쪽이 무엇인지 사라진다."""
    result = q11_result()
    result.comparisons[0].left = fact_reference("fact_SYNTHETIC_LEFT_REVENUE")

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert "fact" in str(error.value)
    assert "calculation" in str(error.value)


def test_a_comparison_pointing_at_the_same_target_twice_is_rejected() -> None:
    """개수만 맞고 견준 것이 없는 판정이다. 그대로 옮기면 한 값이 자기보다 크다는 문장이 된다."""
    result = q10_result()
    result.comparisons[0].right = result.comparisons[0].left

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


@pytest.mark.parametrize(
    ("factory", "field"),
    [(q10_result, "facts"), (q11_result, "calculations")],
    ids=["dangling-fact", "dangling-calculation"],
)
def test_a_comparison_whose_target_is_missing_stops_instead_of_guessing(factory, field) -> None:
    """참조가 가리키는 값이 결과에 없으면 비슷한 값을 골라 채우지 않는다."""
    result = factory()
    setattr(result, field, [])

    with pytest.raises(MissingAnswerSupportError):
        build_answer_response(result)


def test_a_fact_without_a_normalized_pair_stops_the_comparison_sentence() -> None:
    """우열은 정규화 값에서 판정된다. 그 값이 없으면 판정의 근거를 문장에 적을 수 없다."""
    result = q10_result()
    result.facts[0].normalized_value = None

    with pytest.raises(MissingAnswerSupportError):
        build_answer_response(result)


def test_identical_raw_and_normalized_values_are_written_once() -> None:
    """
    같은 값을 괄호로 한 번 더 적으면 다른 값이 있다고 읽힌다.

    원문이 이미 기본 단위로 적힌 공시가 이 경우다. 원문 표기("원")와 정규화 단위를 옮긴 표기가
    같은 문자열이 되므로, 두 표기의 비교는 정규화 단위를 옮긴 뒤에 이뤄져야 한다. 옮기기 전에
    비교하면 "2000000000원(정규화 2000000000원)"처럼 같은 값이 두 번 적힌다.
    """
    result = q10_result()

    for fact in result.facts:
        fact.raw_value = fact.normalized_value
        fact.raw_unit = "원"

    answer = build_answer_response(result).answer

    assert "정규화" not in answer
    assert "2000000000원" in answer


def test_an_unregistered_calculation_unit_is_not_written_in_english() -> None:
    result = q11_result()
    result.calculations[0].unit = "synthetic-unit"

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert "synthetic-unit" in str(error.value)


def test_a_calculation_outside_the_derived_metric_table_is_not_given_a_generic_name() -> None:
    """정의를 되짚을 수 없는 값에 이름을 붙이면 다른 개념의 수치가 그 이름으로 읽힌다."""
    result = q11_result()
    result.calculations[0].operation = "synthetic-operation"

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert "synthetic-operation" in str(error.value)


def test_a_calculation_built_on_another_calculation_has_no_defined_wording_yet() -> None:
    result = q11_result()
    result.calculations[0].inputs = [calculation_reference(RIGHT_MARGIN_ID)]

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_a_calculation_whose_inputs_span_two_companies_is_rejected() -> None:
    """어느 기업의 값인지 한 구절로 부를 수 없다. 한쪽을 골라 적으면 나머지가 문장에서 사라진다."""
    result = q11_result()
    result.facts[0].company = "합성전자(주)"

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert "company" in str(error.value)


def test_comparison_relation_table_covers_the_contract() -> None:
    """
    관계 어휘가 늘면 문장을 먼저 정하게 한다.

    빠진 값이 있으면 _relation_clause가 실행 중단으로 떨어지고, 그 정지는 API에서 500이 된다.
    Contract enum과 대응표를 여기서 맞춰 둔다.
    """
    assert set(COMPARISON_RELATION_TEMPLATES) == set(ComparisonRelation)

    clauses = list(COMPARISON_RELATION_TEMPLATES.values())

    assert len(set(clauses)) == len(clauses)


def test_the_equal_relation_does_not_name_a_side() -> None:
    """어느 쪽도 크지 않은 판정이다. 한쪽을 가리키면 없던 우열이 생긴다."""
    template = COMPARISON_RELATION_TEMPLATES[ComparisonRelation.EQUAL]

    assert "{left}" not in template
    assert "{right}" not in template


def test_the_canonical_normalized_unit_is_not_exposed_in_the_comparison_sentence() -> None:
    """
    정규화 단위는 추출 계층의 어휘이지 사용자 문장의 말이 아니다.

    Q10의 비교 문장은 정규화 값을 반드시 병기하므로(우열 판정의 근거이므로) 그 단위가 그대로
    실리면 한국어 문장에 "2000000000KRW"가 나간다. 원문 표기는 공시에 적힌 그대로 남는다.
    """
    result = q10_result()

    assert {fact.normalized_unit for fact in result.facts} == {CANONICAL_NORMALIZED_UNIT}

    response = build_answer_response(result)

    assert response.answer == (
        f"{LEFT_COMPANY}의 2025년 연결 기준 매출액은 2,000백만원(정규화 2000000000원), "
        f"{RIGHT_COMPANY}의 2025년 연결 기준 매출액은 1,000백만원(정규화 1000000000원)입니다. "
        f"{LEFT_COMPANY} 쪽이 더 큽니다."
    )
    assert CANONICAL_NORMALIZED_UNIT not in response.answer

    # 구조화 결과의 값은 그대로다. 옮기는 것은 문장에 적을 표기뿐이다.
    assert [fact.normalized_unit for fact in response.facts] == [
        CANONICAL_NORMALIZED_UNIT,
        CANONICAL_NORMALIZED_UNIT,
    ]


@pytest.mark.parametrize(
    "normalized_unit",
    [CANONICAL_NORMALIZED_UNIT, BASE_CURRENCY_UNIT],
    ids=["contract-producer", "adapter-producer"],
)
def test_both_currency_producers_render_the_same_user_facing_unit(normalized_unit: str) -> None:
    """
    같은 재무 Fact인데 만든 쪽에 따라 정규화 단위가 다르다.

    periodic.extractor는 "KRW"를, periodic.adapter는 BASE_CURRENCY_UNIT("원")을 싣는다. 후자가
    현재 Pattern D가 지나는 경로이므로, 한쪽만 옮기면 지금 동작하는 경로가 멈추거나 앞으로
    바뀔 경로에서 영문 표기가 샌다. 문장은 어느 producer에서 왔는지 알 필요가 없어야 한다.
    """
    result = q10_result()

    for fact in result.facts:
        fact.normalized_unit = normalized_unit

    answer = build_answer_response(result).answer

    assert "(정규화 2000000000원)" in answer
    assert "(정규화 1000000000원)" in answer


def test_the_canonical_normalized_unit_is_not_exposed_in_the_single_fact_sentence() -> None:
    """
    같은 규칙이 단일 Fact 문장에도 적용된다.

    원문 짝이 없으면 그 문장도 정규화 표현으로 떨어진다(_fact_value). 두 자리에 서로 다른 표기
    규칙을 두면 같은 Fact가 질문에 따라 다른 단위 이름으로 불린다.
    """
    result = answerable_single_fact_result(
        raw_value=None,
        raw_unit=None,
        normalized_unit=CANONICAL_NORMALIZED_UNIT,
    )

    answer = build_answer_response(result).answer

    assert answer.endswith("매출액은 1234000000원입니다.")
    assert CANONICAL_NORMALIZED_UNIT not in answer


def test_a_complete_raw_pair_is_not_blocked_by_an_unmapped_normalized_unit() -> None:
    """
    원문으로 답할 수 있는 Fact는 정규화 단위 표기가 없어도 답한다.

    단일 Fact 문장은 원문 짝을 먼저 쓰고 정규화 쪽을 보지 않는다. 쓰지도 않을 값의 표기가
    없다는 이유로 멈추면, 답할 수 있는 질문이 표기 대응표 때문에 사라진다.
    """
    result = answerable_single_fact_result(normalized_unit="synthetic-unit")

    answer = build_answer_response(result).answer

    assert answer.endswith("매출액은 1,234백만원입니다.")
    assert "synthetic-unit" not in answer


def test_an_unregistered_normalized_unit_is_not_written_as_is() -> None:
    """
    표기를 정하지 않은 단위는 그대로 적지 않고 멈춘다.

    비교 문장은 정규화 값을 반드시 병기하므로 이 경로에서는 원문으로 우회할 수 없다. generic
    표기를 만들면 추출 계층이 새 단위를 늘린 사실이 문장에 조용히 실린다.
    """
    result = q10_result()
    result.facts[0].normalized_unit = "synthetic-unit"

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert "synthetic-unit" in str(error.value)


def test_the_two_unit_tables_stay_separate() -> None:
    """
    Fact 정규화 단위와 Calculation 단위는 서로 다른 계층의 어휘다.

    합치면 한 계층이 단위를 늘릴 때 다른 계층의 문장이 함께 바뀐다. 두 어휘에는 이미 같은
    문자열("percent")이 있고, 한 표에 담으면 원문에 적힌 비율과 agent가 만든 비율의 구분이
    사라진다.
    """
    assert CANONICAL_NORMALIZED_UNIT in FACT_NORMALIZED_UNIT_LABELS
    assert BASE_CURRENCY_UNIT in FACT_NORMALIZED_UNIT_LABELS
    assert UNIT_PERCENT in CALCULATION_UNIT_LABELS

    assert UNIT_PERCENT not in FACT_NORMALIZED_UNIT_LABELS
    assert CANONICAL_NORMALIZED_UNIT not in CALCULATION_UNIT_LABELS

    for label in FACT_NORMALIZED_UNIT_LABELS.values():
        assert label.isascii() is False


def test_derived_metric_labels_cover_the_shared_vocabulary() -> None:
    """
    파생 개념이 늘면 표기를 먼저 정하게 한다.

    정의는 agent.derived_metrics가 정본이고 이 파일에는 표기만 있다. 한쪽만 넓어지면 정의는
    있는데 부를 이름이 없는 값이 우열 판정에 실린다.
    """
    assert set(DERIVED_METRIC_LABELS) == set(DerivedMetric)

    for label in DERIVED_METRIC_LABELS.values():
        assert label.strip()


# --------------------------------------------------------------------------
# Pattern E — EventHistory
# --------------------------------------------------------------------------


def test_completed_chain_summary_counts_documents_and_changes() -> None:
    history = make_event_history(
        document_ids=["exchange_SYNTHETIC0011", "exchange_SYNTHETIC0013"],
        changes=[
            FieldChange(field_name="contract_amount", before="1", after="2"),
            FieldChange(field_name="contract_end_date"),
        ],
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        event_histories=[history],
        evidence=[make_evidence("evidence_SYNTHETIC_event")],
    )

    response = build_answer_response(result)

    assert response.answer == (
        "관련 공시 2건으로 이어지는 이력을 확인했습니다. 확인된 변경 항목은 2건입니다."
    )
    assert response.event_histories == [history]
    assert response.event_histories[0].chain_complete is True
    assert response.evidence == result.evidence


def test_chain_summary_does_not_leak_canonical_field_identifiers() -> None:
    """
    FieldChange.field_name은 canonical 영문 식별자다.

    한글 원문 라벨은 event.contract_adapter가 raw_field_name gap으로 보내므로 production
    결과의 FieldChange.raw_field는 비어 있다. 그 상태에서 이름을 문장에 넣으면 한국어
    응답에 contract_amount 같은 내부 식별자가 그대로 나간다.
    """
    history = make_event_history(
        document_ids=["exchange_SYNTHETIC0011", "exchange_SYNTHETIC0013"],
        changes=[
            FieldChange(field_name="contract_amount"),
            FieldChange(field_name="supply_region"),
        ],
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        event_histories=[history],
    )

    answer = build_answer_response(result).answer

    assert "contract_amount" not in answer
    assert "supply_region" not in answer
    assert not any("a" <= character.lower() <= "z" for character in answer), answer


def test_chain_without_any_change_says_so_only_when_the_chain_is_complete() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        event_histories=[
            make_event_history(
                document_ids=["exchange_SYNTHETIC0011", "exchange_SYNTHETIC0013"],
            )
        ],
    )

    assert build_answer_response(result).answer == (
        "관련 공시 2건으로 이어지는 이력을 확인했습니다. 확인된 변경 항목은 없습니다."
    )


def test_single_document_chain_states_that_no_change_was_found() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        event_histories=[make_event_history(document_ids=["exchange_SYNTHETIC0011"])],
    )

    assert build_answer_response(result).answer == (
        "관련 공시 1건을 확인했으며, 이후 변경된 내용은 확인되지 않았습니다."
    )


def test_partially_answerable_chain_states_the_limitation_and_claims_no_completeness() -> None:
    """
    체인 미완결은 PARTIALLY_ANSWERABLE + INCOMPLETE_CHAIN이다.

    확보분은 답하고 미확보분을 밝힌다. "변경이 없었다"는 확정 진술은 붙이지 않는다.
    """
    history = make_event_history(
        document_ids=["exchange_SYNTHETIC0013"],
        changes=[FieldChange(field_name="contract_amount")],
        chain_complete=False,
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.INCOMPLETE_CHAIN,
        event_histories=[history],
        evidence=[make_evidence("evidence_SYNTHETIC_event")],
    )

    response = build_answer_response(result)

    assert response.status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert response.reason is AnswerabilityReason.INCOMPLETE_CHAIN
    assert response.answer == (
        "관련 공시 1건까지 확인했습니다. 확인된 변경 항목은 1건입니다. "
        + LIMITATION_MESSAGE_BY_REASON[AnswerabilityReason.INCOMPLETE_CHAIN]
    )
    assert "변경된 내용은 확인되지 않았습니다" not in response.answer
    assert "확인된 변경 항목은 없습니다" not in response.answer
    assert response.event_histories == [history]



def test_partial_source_support_preserves_supported_fact_and_states_limitation() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.PARTIAL_SOURCE_SUPPORT,
        facts=[make_fact()],
        evidence=[make_evidence()],
    )

    response = build_answer_response(result)

    assert response.status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert response.reason is AnswerabilityReason.PARTIAL_SOURCE_SUPPORT
    assert response.facts == result.facts
    assert response.evidence == result.evidence
    assert response.answer.endswith(
        LIMITATION_MESSAGE_BY_REASON[
            AnswerabilityReason.PARTIAL_SOURCE_SUPPORT
        ]
    )


@pytest.mark.parametrize(
    ("metric", "raw_value", "expected_sentence", "absent_text"),
    [
        (
            MetricConcept.HOLDING_SHARES,
            "1,234",
            f"{SYNTHETIC_COMPANY}의 이번 보고 시점 보유주식 수는 1,234주입니다.",
            "보유비율",
        ),
        (
            MetricConcept.HOLDING_RATIO,
            "12.34",
            f"{SYNTHETIC_COMPANY}의 이번 보고 시점 보유비율은 12.34%입니다.",
            "보유주식 수",
        ),
    ],
)
def test_partial_holding_fact_uses_holding_specific_sentence(
    metric: MetricConcept,
    raw_value: str,
    expected_sentence: str,
    absent_text: str,
) -> None:
    evidence = make_evidence()
    fact = make_fact(
        metric=metric,
        fiscal_year=None,
        accounting_scope=FactAccountingScope.NOT_APPLICABLE,
        raw_value=raw_value,
        raw_unit="주" if metric is MetricConcept.HOLDING_SHARES else "%",
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.PARTIAL_SOURCE_SUPPORT,
        facts=[fact],
        evidence=[evidence],
    )

    response = build_answer_response(result)

    assert response.answer == (
        expected_sentence
        + " "
        + LIMITATION_MESSAGE_BY_REASON[AnswerabilityReason.PARTIAL_SOURCE_SUPPORT]
    )
    assert absent_text not in response.answer
    assert response.facts == [fact]
    assert response.evidence == [evidence]


def test_full_holding_fact_sentence_remains_unchanged() -> None:
    shares = make_fact(
        fact_id="fact_SYNTHETIC_shares",
        metric=MetricConcept.HOLDING_SHARES,
        fiscal_year=None,
        accounting_scope=FactAccountingScope.NOT_APPLICABLE,
        raw_value="1,234",
    )
    ratio = make_fact(
        fact_id="fact_SYNTHETIC_ratio",
        metric=MetricConcept.HOLDING_RATIO,
        fiscal_year=None,
        accounting_scope=FactAccountingScope.NOT_APPLICABLE,
        raw_value="12.34",
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[shares, ratio],
        evidence=[make_evidence()],
    )

    assert build_answer_response(result).answer == (
        f"{SYNTHETIC_COMPANY}의 이번 보고 시점 보유주식 수는 "
        "1,234주이고, 보유비율은 12.34%입니다."
    )



def test_event_history_without_documents_does_not_produce_a_sentence() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        event_histories=[make_event_history(document_ids=[])],
    )

    with pytest.raises(MissingAnswerSupportError):
        build_answer_response(result)


def test_multiple_event_histories_have_no_defined_wording_yet() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        event_histories=[
            make_event_history(document_ids=["exchange_SYNTHETIC0011"]),
            make_event_history(document_ids=["exchange_SYNTHETIC0012"]),
        ],
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


# --------------------------------------------------------------------------
# J. 구조화 결과 보존
# --------------------------------------------------------------------------


def test_every_collection_with_a_place_in_the_response_is_carried_over() -> None:
    """
    AnswerResponse에 자리가 있는 collection은 손실 없이 실린다.

    특히 evidence/provenance를 빠뜨리지 않는다. 근거가 사라지면 답을 원문과 대조할 수 없다.
    """
    evidence = make_evidence()
    event = Event(
        event_id="event_SYNTHETIC_01",
        event_type=EventType.SUPPLY_CONTRACT,
        company=SYNTHETIC_COMPANY,
        source_doc_id="exchange_SYNTHETIC0011",
        attributes={"synthetic_field": "SYNTHETIC-VALUE"},
    )
    link = EventLink(
        link_id="link_SYNTHETIC_01",
        source_doc_id="exchange_SYNTHETIC0013",
        relation=EventLinkRelation.CORRECTION_OF,
        raw_reference=RawReference(original_filing_date="YYYY-MM-DD"),
        resolved_doc_id="exchange_SYNTHETIC0011",
        resolution_status=EventLinkResolutionStatus.RESOLVED,
    )
    history = make_event_history(
        document_ids=["exchange_SYNTHETIC0011", "exchange_SYNTHETIC0013"],
        changes=[FieldChange(field_name="contract_amount", raw_field="계약금액")],
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        event_histories=[history],
        event_links=[link],
        events=[event],
        evidence=[evidence],
    )

    response = build_answer_response(result)

    assert response.events == [event]
    assert response.event_links == [link]
    assert response.event_histories == [history]
    assert response.evidence == [evidence]
    assert response.evidence[0].section_path == evidence.section_path
    assert response.evidence[0].raw_text == evidence.raw_text


def test_response_collections_do_not_alias_the_execution_result() -> None:
    """공개 응답을 고쳐도 실행 결과가 함께 바뀌지 않아야 한다."""
    result = answerable_single_fact_result()

    response = build_answer_response(result)
    response.facts.clear()

    assert len(result.facts) == 1


# --------------------------------------------------------------------------
# 결과 정합성
#
# Contract는 pydantic 모델이라 서로 모순되는 조합도 만들어진다. 그런 입력을 문장으로
# 옮기면 두 판정 중 하나가 조용히 사라지므로, 문장을 고르기 전에 멈춘다.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        AnswerabilityReason.NOT_FOUND,
        AnswerabilityReason.INSUFFICIENT_EVIDENCE,
        AnswerabilityReason.OUT_OF_DATA_RANGE,
        AnswerabilityReason.OUT_OF_SCOPE_DATA,
        AnswerabilityReason.OUT_OF_SCOPE_POLICY,
        AnswerabilityReason.INCOMPLETE_CHAIN,
    ],
)
def test_unresolved_with_an_incompatible_reason_fails_fast(
    reason: AnswerabilityReason,
) -> None:
    """
    UNRESOLVED + NOT_FOUND는 스키마상 유효하지만 두 판정을 동시에 말한다.

    사유 쪽 문장을 고르면 "후보를 좁히지 못했다"가 사라지고, 상태 쪽 문장을 고르면 사유가
    사라진다. 어느 쪽이든 판정 하나가 조용히 없어진다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNRESOLVED,
        answerability_reason=reason,
    )

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert reason.value in str(error.value)


@pytest.mark.parametrize(
    "reason",
    sorted(ALLOWED_REASONS_BY_STATUS[AnswerabilityStatus.UNRESOLVED], key=lambda r: r.value),
)
def test_unresolved_allows_only_the_ambiguity_reasons(reason: AnswerabilityReason) -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNRESOLVED,
        answerability_reason=reason,
    )

    assert build_answer_response(result).reason is reason


def test_answerable_must_not_carry_a_failure_reason() -> None:
    """evaluation.runner의 GoldAnswer 검증과 같은 규칙을 응답 경계에서도 고정한다."""
    result = answerable_single_fact_result()
    result.answerability_reason = AnswerabilityReason.NOT_FOUND

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_partially_answerable_with_an_unrelated_reason_fails_fast() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.NOT_FOUND,
        event_histories=[make_event_history(document_ids=["a"], chain_complete=False)],
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


@pytest.mark.parametrize(
    "reason",
    [AnswerabilityReason.AMBIGUOUS_MATCH, AnswerabilityReason.INCOMPLETE_CHAIN],
)
def test_unanswerable_does_not_take_reasons_that_belong_to_other_statuses(
    reason: AnswerabilityReason,
) -> None:
    """
    "답이 없다"와 "확정하지 못했다"가 한 값으로 묶이면 평가에서 두 실패가 구분되지 않는다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=reason,
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


# --------------------------------------------------------------------------
# Pattern E 체인 정합성
#
# chain_complete가 판정을 정하므로 둘은 같은 사실의 두 표현이다
# (handlers.event_history._judge_answerability, normalization._chain_reason).
# --------------------------------------------------------------------------


def test_answerable_chain_that_is_not_complete_fails_fast() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        event_histories=[
            make_event_history(
                document_ids=["exchange_SYNTHETIC0011", "exchange_SYNTHETIC0013"],
                chain_complete=False,
            )
        ],
    )

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert "chain_complete" in str(error.value)


def test_partially_answerable_chain_that_is_complete_fails_fast() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.INCOMPLETE_CHAIN,
        event_histories=[
            make_event_history(
                document_ids=["exchange_SYNTHETIC0011"],
                chain_complete=True,
            )
        ],
    )

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert "chain_complete" in str(error.value)


def test_partially_answerable_without_a_reason_fails_fast() -> None:
    """
    사유 없는 PARTIALLY_ANSWERABLE은 구조적으로 나올 수 없다.

    _judge_answerability와 _chain_reason이 같은 boolean(FilingChain.is_complete)에서
    갈리므로, PARTIALLY라면 INCOMPLETE_CHAIN이 반드시 함께 나온다. 사유를 비우면 무엇을
    확정하지 못했는지가 응답에서 사라진다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        event_histories=[
            make_event_history(document_ids=["exchange_SYNTHETIC0013"], chain_complete=False)
        ],
    )

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert AnswerabilityReason.INCOMPLETE_CHAIN.value in str(error.value)


def test_statuses_allowing_no_reason_are_pinned_to_actual_producers() -> None:
    """
    "미래에 생길 수도 있다"는 이유로 사유 없는 조합을 열어 두지 않는다.

    현재 사유 없이 나오는 것은 두 가지뿐이다. ANSWERABLE(정상)과, Pattern E가 루트를
    확정하지 못한 UNRESOLVED다. 새 capability가 붙으면 이 집합을 명시적으로 넓힌다.
    """
    assert STATUSES_ALLOWING_NO_REASON == {
        AnswerabilityStatus.ANSWERABLE,
        AnswerabilityStatus.UNRESOLVED,
    }


def test_chain_invariant_does_not_touch_results_without_an_event_history() -> None:
    """불변식의 근거는 Pattern E 하나다. EventHistory가 없는 결과까지 넓히지 않는다."""
    result = answerable_single_fact_result()

    assert build_answer_response(result).status is AnswerabilityStatus.ANSWERABLE


# --------------------------------------------------------------------------
# 거부 응답 payload 정합성
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    sorted(REFUSAL_REASONS_WITHOUT_SUPPORTING_PAYLOAD, key=lambda item: item.value),
)
def test_refusal_with_a_fact_fails_fast(reason: AnswerabilityReason) -> None:
    """
    거부 응답에 근거를 붙이면 부분 답변으로 읽힌다(evidence_and_answerability.md 원칙 7).

    걸러내지 않고 멈춘다. 여기서 지우면 producer가 원칙을 어긴 사실이 사라진다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=reason,
        facts=[make_fact()],
    )

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert reason.value in str(error.value)


@pytest.mark.parametrize(
    "reason",
    sorted(REFUSAL_REASONS_WITHOUT_SUPPORTING_PAYLOAD, key=lambda item: item.value),
)
def test_refusal_with_evidence_fails_fast(reason: AnswerabilityReason) -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=reason,
        evidence=[make_evidence()],
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_policy_refusal_from_the_gate_still_succeeds() -> None:
    """F3/F4의 정상 결과는 그대로 통과해야 한다."""
    for detail_reason in (REASON_INVESTMENT_ADVICE, REASON_FORWARD_LOOKING_PREDICTION):
        response = build_answer_response(refused_result(detail_reason))

        assert response.status is AnswerabilityStatus.UNANSWERABLE
        assert response.reason is AnswerabilityReason.OUT_OF_SCOPE_POLICY
        assert response.facts == []
        assert response.evidence == []


def test_not_found_may_still_carry_structured_payload() -> None:
    """
    이 불변식은 F1~F4 거부 사유에만 걸린다.

    pattern_f_safety.md §4가 명시한 것은 그 범위이고, Pattern E의 no_matching_filing 경로는
    UNANSWERABLE 결과에 event_links를 실어 보낼 수 있는 구조다. 추측으로 범위를 넓히면
    그 경로가 함께 막힌다.
    """
    link = EventLink(
        link_id="link_SYNTHETIC_01",
        source_doc_id="exchange_SYNTHETIC0013",
        relation=EventLinkRelation.CORRECTION_OF,
        raw_reference=RawReference(original_filing_date="YYYY-MM-DD"),
        resolved_doc_id="exchange_SYNTHETIC0011",
        resolution_status=EventLinkResolutionStatus.RESOLVED,
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=AnswerabilityReason.NOT_FOUND,
        event_links=[link],
    )

    response = build_answer_response(result)

    assert response.answer == UNAVAILABLE_MESSAGE_BY_REASON[AnswerabilityReason.NOT_FOUND]
    assert response.event_links == [link]


# --------------------------------------------------------------------------
# 대응표 완전성
#
# 표기와 문장이 대응표로 갈리므로, 새 enum 값이 늘 때 "문장이 없다"가 조용한 fallback으로
# 나타나지 않도록 여기서 고정한다.
# --------------------------------------------------------------------------


def test_supported_metric_family_is_pinned() -> None:
    """
    단일 Fact 문장이 지원하는 지표를 명시적으로 고정한다.

    "모든 MetricConcept에 표기가 있어야 한다"로 두지 않는다. 문장 템플릿이 재무제표
    축(회계연도 × 연결/별도)을 전제하므로, 그 축이 없는 지표까지 표기를 채우면 템플릿이
    설계되지 않은 지표에도 적용된다.
    """
    assert set(METRIC_LABELS) == {MetricConcept.REVENUE, MetricConcept.OPERATING_INCOME}


@pytest.mark.parametrize("metric", sorted(METRIC_LABELS, key=lambda item: item.value))
def test_supported_metric_renders_a_korean_label(metric: MetricConcept) -> None:
    result = answerable_single_fact_result(metric=metric)

    answer = build_answer_response(result).answer

    assert METRIC_LABELS[metric] in answer
    assert metric.value not in answer


@pytest.mark.parametrize(
    "metric",
    [
        MetricConcept.CONTRACT_AMOUNT,
        MetricConcept.CONTRACT_START_DATE,
        MetricConcept.CONTRACT_END_DATE,
        MetricConcept.HOLDING_SHARES,
        MetricConcept.HOLDING_RATIO,
    ],
)
def test_event_and_holding_metrics_fail_fast_instead_of_borrowing_the_template(
    metric: MetricConcept,
) -> None:
    """
    거래소공시·지분공시 지표는 어느 계약·어느 보유 내역인지 식별자를 요구한다.

    표기만 붙여 두면 fiscal_year와 scope가 생략된 채 "{회사}의 계약금액은 X원입니다"처럼
    형식은 자연스럽고 대상이 특정되지 않은 문장이 나간다. generic label로 덮지 않는다.
    """
    result = answerable_single_fact_result(
        metric=metric,
        fiscal_year=None,
        accounting_scope=FactAccountingScope.NOT_APPLICABLE,
    )

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert metric.value in str(error.value)


@pytest.mark.parametrize("scope", list(FactAccountingScope))
def test_every_accounting_scope_is_labelled_or_deliberately_omitted(
    scope: FactAccountingScope,
) -> None:
    assert scope in ACCOUNTING_SCOPE_LABELS or scope in SCOPE_OMITTED_IN_ANSWER


@pytest.mark.parametrize("reason", list(UNAVAILABLE_MESSAGE_BY_REASON))
def test_every_mapped_answerability_reason_has_a_sentence(
    reason: AnswerabilityReason,
) -> None:
    assert reason in UNAVAILABLE_MESSAGE_BY_REASON

    assert UNAVAILABLE_MESSAGE_BY_REASON[reason].strip()


def test_mapped_and_unmapped_reasons_exhaust_the_contract_without_overlap() -> None:
    mapped = set(UNAVAILABLE_MESSAGE_BY_REASON)
    unmapped = set(UNMAPPED_ANSWERABILITY_REASONS)

    assert mapped.isdisjoint(unmapped)
    assert mapped | unmapped == set(AnswerabilityReason)


def test_not_comparable_is_registered_in_both_places_that_gate_it() -> None:
    """
    문장 등록과 미등록 집합 제거는 한 결정의 두 절반이다.

    ALLOWED_REASONS_BY_STATUS[UNANSWERABLE]가 UNMAPPED_ANSWERABILITY_REASONS를 빼고 만들어지므로,
    문장만 넣고 집합에서 빼지 않으면 문장은 있는데 도달할 수 없는 사유가 남고, 반대로 집합에서만
    빼면 _message_for_reason이 실행 중단으로 떨어진다. 둘이 같은 것을 말하는지 고정한다.
    """
    assert AnswerabilityReason.NOT_COMPARABLE in UNAVAILABLE_MESSAGE_BY_REASON
    assert AnswerabilityReason.NOT_COMPARABLE not in UNMAPPED_ANSWERABILITY_REASONS
    assert (
        AnswerabilityReason.NOT_COMPARABLE
        in ALLOWED_REASONS_BY_STATUS[AnswerabilityStatus.UNANSWERABLE]
    )


def test_reason_sentences_are_distinct() -> None:
    """서로 다른 사유가 같은 문장으로 뭉개지면 평가에서 두 실패가 구분되지 않는다."""
    messages = list(UNAVAILABLE_MESSAGE_BY_REASON.values())

    assert len(set(messages)) == len(messages)


# --------------------------------------------------------------------------
# WITHHELD_IN_SOURCE — 원문에서 값이 공개되지 않은 경우 (open_decisions.md D9)
#
# 유보는 데이터 부재가 아니다. 문서도 항목도 있고 값만 비공개다. 그래서 Builder는 이 판정을
# 받아도 멈추지 않고, 유보된 값을 0·미공개·없음 같은 대체 값으로 바꾸지도 않는다.
# --------------------------------------------------------------------------

def test_partial_source_support_is_allowed_only_for_partial_answers() -> None:
    assert (
        AnswerabilityReason.PARTIAL_SOURCE_SUPPORT
        in ALLOWED_REASONS_BY_STATUS[
            AnswerabilityStatus.PARTIALLY_ANSWERABLE
        ]
    )

    for status in (
        AnswerabilityStatus.ANSWERABLE,
        AnswerabilityStatus.UNRESOLVED,
        AnswerabilityStatus.UNANSWERABLE,
    ):
        assert (
            AnswerabilityReason.PARTIAL_SOURCE_SUPPORT
            not in ALLOWED_REASONS_BY_STATUS[status]
        )


def test_partially_withheld_answer_states_the_limitation_without_inventing_a_value() -> None:
    """
    D9 case A — 질문에 필요한 field 일부가 유보.

    확보분은 답하고 유보분은 밝힌다. 확보한 Fact를 지우지도, 유보된 값을 채우지도 않는다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.WITHHELD_IN_SOURCE,
        facts=[make_fact()],
        evidence=[make_evidence()],
    )

    response = build_answer_response(result)

    assert response.status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert response.reason is AnswerabilityReason.WITHHELD_IN_SOURCE
    assert response.answer.endswith(
        LIMITATION_MESSAGE_BY_REASON[AnswerabilityReason.WITHHELD_IN_SOURCE]
    )
    assert response.facts == [make_fact()]
    for substitute in ("0원", "미공개", "없음", "0입니다"):
        assert substitute not in response.answer


def test_fully_withheld_answer_is_returned_instead_of_failing_the_builder() -> None:
    """D9 case B — 핵심 required field 전체가 유보. 확보분이 없어 UNANSWERABLE이다."""
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=AnswerabilityReason.WITHHELD_IN_SOURCE,
    )

    response = build_answer_response(result)

    assert response.status is AnswerabilityStatus.UNANSWERABLE
    assert response.reason is AnswerabilityReason.WITHHELD_IN_SOURCE
    assert response.answer == (
        UNAVAILABLE_MESSAGE_BY_REASON[AnswerabilityReason.WITHHELD_IN_SOURCE]
    )


def test_withheld_answer_is_not_worded_as_a_search_failure() -> None:
    """
    "찾지 못했다"로 쓰면 사용자가 질문을 좁히면 된다고 읽는다.

    좁혀도 원문에 값이 없다. 두 사유의 다음 행동이 다르므로 문장도 달라야 한다.
    """
    withheld = UNAVAILABLE_MESSAGE_BY_REASON[AnswerabilityReason.WITHHELD_IN_SOURCE]

    assert withheld != UNAVAILABLE_MESSAGE_BY_REASON[AnswerabilityReason.NOT_FOUND]
    assert withheld != UNAVAILABLE_MESSAGE_BY_REASON[AnswerabilityReason.OUT_OF_DATA_RANGE]
    assert "찾지 못했" not in withheld


def test_withheld_in_source_is_the_only_reason_shared_by_two_statuses() -> None:
    """
    유보 범위가 상태를 가르고 사유는 같다(D9 case A/B).

    다른 사유가 두 상태에 함께 실리면 "일부만 답했다"와 "답하지 못했다"의 경계가 사유
    하나로는 읽히지 않게 된다.
    """
    partial = ALLOWED_REASONS_BY_STATUS[AnswerabilityStatus.PARTIALLY_ANSWERABLE]
    unanswerable = ALLOWED_REASONS_BY_STATUS[AnswerabilityStatus.UNANSWERABLE]

    assert partial & unanswerable == {AnswerabilityReason.WITHHELD_IN_SOURCE}


@pytest.mark.parametrize(
    "status",
    [AnswerabilityStatus.ANSWERABLE, AnswerabilityStatus.UNRESOLVED],
)
def test_withheld_in_source_does_not_leak_into_unrelated_statuses(
    status: AnswerabilityStatus,
) -> None:
    """
    ANSWERABLE은 사유를 싣지 않고, UNRESOLVED는 대상을 좁히지 못한 판정이다.

    유보는 대상을 확정한 뒤 그 값이 비공개인 상태이므로 어느 쪽도 아니다.
    """
    assert (
        AnswerabilityReason.WITHHELD_IN_SOURCE not in ALLOWED_REASONS_BY_STATUS[status]
    )


def test_unresolved_with_a_withheld_reason_fails_fast() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNRESOLVED,
        answerability_reason=AnswerabilityReason.WITHHELD_IN_SOURCE,
    )

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert AnswerabilityReason.WITHHELD_IN_SOURCE.value in str(error.value)


def test_withholding_does_not_force_an_incomplete_chain() -> None:
    """
    체인 완결과 원문 유보는 독립된 축이다(D9).

    체인을 전부 확보하고도 그 안의 일부 field가 유보일 수 있다. 이 결과에
    chain_complete=False를 요구하면 완결된 체인을 미완결로 적어야 통과하게 된다.
    """
    history = make_event_history(
        document_ids=["exchange_SYNTHETIC0011", "exchange_SYNTHETIC0012"],
        changes=[FieldChange(field_name="contract_amount")],
        chain_complete=True,
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.WITHHELD_IN_SOURCE,
        event_histories=[history],
        evidence=[make_evidence("evidence_SYNTHETIC_event")],
    )

    response = build_answer_response(result)

    assert response.status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert response.event_histories == [history]
    assert response.event_histories[0].chain_complete is True


# --------------------------------------------------------------------------
# 체인 완결 서술과 판정 부분성은 서로 다른 축이다
#
# 아래 둘은 짝이다. 같은 PARTIALLY_ANSWERABLE인데 이력 서술이 서로 반대여야 한다.
# 한쪽만 두면 판정 상태로 완결 여부를 정하는 구현이 다시 들어와도 통과한다.
# --------------------------------------------------------------------------


def test_withheld_partial_answer_keeps_the_complete_chain_narrative() -> None:
    """
    유보는 체인을 미완결로 만들지 않는다(D9).

    체인은 끝까지 확보했고 그 안의 일부 field만 원문에서 유보된 결과다. 이력 서술은 완결
    문장이어야 하고, 유보 사실은 그 뒤 limitation이 따로 말한다.

    상태만 보고 서술을 고르면 "관련 공시 N건까지 확인했습니다"가 나간다. 끝까지 확인한
    이력을 중간에서 멈춘 것처럼 말하는 문장이며, 같은 응답에 실려 나가는
    chain_complete=True와 정면으로 어긋난다.
    """
    history = make_event_history(
        document_ids=["exchange_SYNTHETIC0011", "exchange_SYNTHETIC0012"],
        changes=[FieldChange(field_name="contract_amount")],
        chain_complete=True,
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.WITHHELD_IN_SOURCE,
        event_histories=[history],
        evidence=[make_evidence("evidence_SYNTHETIC_event")],
    )

    response = build_answer_response(result)

    assert response.answer == (
        "관련 공시 2건으로 이어지는 이력을 확인했습니다. 확인된 변경 항목은 1건입니다. "
        + LIMITATION_MESSAGE_BY_REASON[AnswerabilityReason.WITHHELD_IN_SOURCE]
    )

    # 미완결 어휘가 섞이지 않는다.
    assert "까지 확인했습니다" not in response.answer
    assert (
        LIMITATION_MESSAGE_BY_REASON[AnswerabilityReason.INCOMPLETE_CHAIN]
        not in response.answer
    )

    # 판정과 이력은 그대로 실려 나간다.
    assert response.status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert response.reason is AnswerabilityReason.WITHHELD_IN_SOURCE
    assert response.event_histories == [history]
    assert response.event_histories[0].chain_complete is True


def test_incomplete_chain_partial_answer_keeps_the_incomplete_narrative() -> None:
    """
    같은 PARTIALLY_ANSWERABLE이라도 체인이 미완결이면 서술이 달라진다.

    위 유보 테스트와 짝이다. 완결 여부를 history에서 읽게 바꾼 뒤에도 미완결 경로가 그대로
    남아 있어야 한다. "변경이 없었다"는 확정 진술은 여전히 붙이지 않는다.
    """
    history = make_event_history(
        document_ids=["exchange_SYNTHETIC0011", "exchange_SYNTHETIC0012"],
        changes=[FieldChange(field_name="contract_amount")],
        chain_complete=False,
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.INCOMPLETE_CHAIN,
        event_histories=[history],
        evidence=[make_evidence("evidence_SYNTHETIC_event")],
    )

    response = build_answer_response(result)

    assert response.answer == (
        "관련 공시 2건까지 확인했습니다. 확인된 변경 항목은 1건입니다. "
        + LIMITATION_MESSAGE_BY_REASON[AnswerabilityReason.INCOMPLETE_CHAIN]
    )

    # 완결 어휘와 유보 문장이 섞이지 않는다.
    assert "이어지는 이력을 확인했습니다" not in response.answer
    assert (
        LIMITATION_MESSAGE_BY_REASON[AnswerabilityReason.WITHHELD_IN_SOURCE]
        not in response.answer
    )
    assert "변경된 내용은 확인되지 않았습니다" not in response.answer

    assert response.status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert response.reason is AnswerabilityReason.INCOMPLETE_CHAIN
    assert response.event_histories[0].chain_complete is False


def test_the_two_partial_narratives_do_not_converge() -> None:
    """
    두 PARTIALLY_ANSWERABLE의 이력 서술이 같은 문장으로 수렴하지 않는다.

    수렴하면 완결 여부가 문장에서 사라지고, 판정 상태 하나로 서술을 고르는 구현으로 되돌아가도
    위 두 테스트만으로는 잡히지 않는다. 두 서술을 직접 맞대어 고정한다.
    """
    documents = ["exchange_SYNTHETIC0011", "exchange_SYNTHETIC0012"]
    changes = [FieldChange(field_name="contract_amount")]

    def answer_for(reason: AnswerabilityReason, *, chain_complete: bool) -> str:
        result = AgentExecutionResult(
            execution_status=ExecutionStatus.COMPLETED,
            answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
            answerability_reason=reason,
            event_histories=[
                make_event_history(
                    document_ids=documents,
                    changes=changes,
                    chain_complete=chain_complete,
                )
            ],
            evidence=[make_evidence("evidence_SYNTHETIC_event")],
        )
        return build_answer_response(result).answer

    withheld = answer_for(
        AnswerabilityReason.WITHHELD_IN_SOURCE, chain_complete=True
    )
    incomplete = answer_for(
        AnswerabilityReason.INCOMPLETE_CHAIN, chain_complete=False
    )

    assert withheld != incomplete

    # 이력 서술만 떼어 내도 다르다. 뒤에 붙는 limitation이 달라서 갈리는 것이 아니다.
    withheld_narrative = withheld.replace(
        LIMITATION_MESSAGE_BY_REASON[AnswerabilityReason.WITHHELD_IN_SOURCE], ""
    )
    incomplete_narrative = incomplete.replace(
        LIMITATION_MESSAGE_BY_REASON[AnswerabilityReason.INCOMPLETE_CHAIN], ""
    )

    assert withheld_narrative != incomplete_narrative


def test_withholding_on_an_incomplete_chain_reports_both_axes() -> None:
    """
    두 축은 함께 성립할 수 있다.

    체인도 미완결이고 확보한 부분에 유보도 있는 결과다. 이력 서술은 미완결을 따르고
    limitation은 유보를 말한다. 한쪽이 다른 쪽을 덮어쓰지 않는 것을 고정한다.
    """
    history = make_event_history(
        document_ids=["exchange_SYNTHETIC0011", "exchange_SYNTHETIC0012"],
        changes=[FieldChange(field_name="contract_amount")],
        chain_complete=False,
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.WITHHELD_IN_SOURCE,
        event_histories=[history],
        evidence=[make_evidence("evidence_SYNTHETIC_event")],
    )

    response = build_answer_response(result)

    assert response.answer == (
        "관련 공시 2건까지 확인했습니다. 확인된 변경 항목은 1건입니다. "
        + LIMITATION_MESSAGE_BY_REASON[AnswerabilityReason.WITHHELD_IN_SOURCE]
    )
    assert response.event_histories[0].chain_complete is False


def test_withheld_result_may_still_carry_the_evidence_of_the_withholding() -> None:
    """
    유보 사실 자체의 근거는 지우지 않는다.

    REFUSAL_REASONS_WITHOUT_SUPPORTING_PAYLOAD에 이 사유를 넣지 않은 이유다. 넣으면 "어느
    공시에서 유보였는지"를 보여 줄 수 없고, 뒤에 정정으로 값이 공개됐을 때 그 이력을 대조할
    근거도 남지 않는다.
    """
    assert (
        AnswerabilityReason.WITHHELD_IN_SOURCE
        not in REFUSAL_REASONS_WITHOUT_SUPPORTING_PAYLOAD
    )

    evidence = make_evidence("evidence_SYNTHETIC_withheld")
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=AnswerabilityReason.WITHHELD_IN_SOURCE,
        evidence=[evidence],
    )

    response = build_answer_response(result)

    assert response.evidence == [evidence]


def test_policy_refusal_table_covers_the_gate_categories() -> None:
    """safety.py가 만드는 두 사유는 반드시 고유한 문장을 가진다."""
    assert set(POLICY_REFUSAL_MESSAGE_BY_DETAIL_REASON) == {
        REASON_INVESTMENT_ADVICE,
        REASON_FORWARD_LOOKING_PREDICTION,
    }


# --------------------------------------------------------------------------
# Pattern C — 시계열 증감(Q07)
#
# Fact 2건과 Calculation 2건을 함께 실은 결과만 문장이 된다. 단일 Fact 경로의 가드는 그대로
# 두고 이 모양만 연다.
# --------------------------------------------------------------------------


def _delta_calculation(
    *,
    calculation_id: str = "calculation_revenue_delta_2024_2025",
    operation: str = OPERATION_DELTA,
    value: str = "200000000.0000",
    unit: str | None = "원",
    input_ids: tuple[str, str] = ("fact_2024", "fact_2025"),
) -> CalculationResult:
    return CalculationResult(
        calculation_id=calculation_id,
        operation=operation,
        inputs=[
            ResultInputReference(input_type=ResultInputType.FACT, input_id=input_id)
            for input_id in input_ids
        ],
        value=value,
        unit=unit,
        evidence_ids=["evidence_SYNTHETIC_01"],
    )


def temporal_delta_result(
    *,
    calculations: list[CalculationResult] | None = None,
    facts: list[Fact] | None = None,
) -> AgentExecutionResult:
    """Pattern C의 Q07 성공 결과와 같은 모양(normalize_temporal_compare_result 참조)."""
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=facts
        if facts is not None
        else [
            make_fact(
                fact_id="fact_2024",
                fiscal_year=2024,
                raw_value="1,000",
                normalized_value="1000000000",
            ),
            make_fact(
                fact_id="fact_2025",
                fiscal_year=2025,
                raw_value="1,200",
                normalized_value="1200000000",
            ),
        ],
        evidence=[make_evidence()],
        calculations=calculations
        if calculations is not None
        else [
            _delta_calculation(),
            _delta_calculation(
                calculation_id="calculation_revenue_delta_pct_2024_2025",
                operation=OPERATION_DELTA_PCT,
                value="20.0000",
                unit="percent",
            ),
        ],
    )


def test_temporal_delta_operation_names_match_the_calculator() -> None:
    """
    두 이름은 agent.calculator가 정본이다.

    순환 import 때문에 이 모듈이 그쪽에서 가져오지 못하므로 같은 문자열을 두 곳에 둔다.
    어긋나면 Q07 문장이 조용히 사라지므로 여기서 붙여 둔다.
    """
    assert OPERATION_DELTA == CALCULATOR_OPERATION_DELTA
    assert OPERATION_DELTA_PCT == CALCULATOR_OPERATION_DELTA_PCT


def test_temporal_delta_result_becomes_a_sentence() -> None:
    answer = build_answer_response(temporal_delta_result()).answer

    assert SYNTHETIC_COMPANY in answer
    assert "2024년" in answer
    assert "2025년" in answer
    assert "200,000,000원" in answer
    assert "20.0000%" in answer


def test_temporal_delta_wording_is_unchanged_by_the_derived_metric_path() -> None:
    """
    Q07 문장 전체를 리터럴로 고정한다.

    Q08 경로가 같은 보조 함수들(_period_phrase·_scope_phrase·_topic_particle·
    _calculation_value)을 함께 쓰므로, 그쪽 문구를 손볼 때 이 문장이 함께 흔들릴 수 있다.
    두 문항은 묻는 것이 다르고 답의 형태도 다르다. 한쪽 문구가 다른 쪽을 따라가면 증감액과
    변화량의 구분이 사라진다.
    """
    assert build_answer_response(temporal_delta_result()).answer == (
        f"{SYNTHETIC_COMPANY}의 연결 기준 매출액은 2024년 1,000백만원, 2025년 1,200백만원입니다. "
        "증감액은 200,000,000원, 증감률은 20.0000%입니다."
    )


def test_temporal_delta_sentence_is_deterministic() -> None:
    result = temporal_delta_result()

    assert build_answer_response(result).answer == build_answer_response(result).answer


def test_temporal_delta_reads_the_periods_from_the_calculation_inputs() -> None:
    """
    이전·이후를 Fact 목록 순서에서 읽지 않는다.

    목록 순서를 뒤집어도 증감액 Calculation의 inputs가 시간 순서를 정하므로 문장은 같다.
    """
    forward = temporal_delta_result()

    reversed_facts = temporal_delta_result(facts=list(reversed(forward.facts)))

    assert build_answer_response(reversed_facts).answer == (
        build_answer_response(forward).answer
    )


def test_two_facts_without_the_delta_calculations_still_stop() -> None:
    """일반 다중 Fact 가드는 그대로다. Calculation이 없으면 문장을 만들지 않는다."""
    result = temporal_delta_result(calculations=[])

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_two_facts_with_other_operations_still_stop() -> None:
    """연산 이름이 다르면 이 경로가 열리지 않는다."""
    result = temporal_delta_result(
        calculations=[
            _delta_calculation(operation="ratio"),
            _delta_calculation(
                calculation_id="calculation_other", operation="diff_pp"
            ),
        ]
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_delta_calculations_pointing_elsewhere_still_stop() -> None:
    """
    두 Calculation이 결과에 실린 Fact 2건만 참조해야 한다.

    다른 값을 가리키는 참조로 문장을 만들면 문장의 근거가 결과와 어긋난다.
    """
    result = temporal_delta_result(
        calculations=[
            _delta_calculation(input_ids=("fact_2024", "fact_9999")),
            _delta_calculation(
                calculation_id="calculation_revenue_delta_pct_2024_2025",
                operation=OPERATION_DELTA_PCT,
                value="20.0000",
                unit="percent",
            ),
        ]
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        # quantize가 만든 뜻 없는 소수 네 자리는 지운다. 값이 아니라 자리다.
        ("200000000.0000", "200,000,000원"),
        # 부호는 값에서 읽어 그대로 둔다. 지우면 감소가 증가로 읽힌다.
        ("-200000000.0000", "-200,000,000원"),
        # 0이 아닌 소수부는 남긴다. 반올림하면 응답에 실린 Calculation.value와 어긋난다.
        ("200000000.5000", "200,000,000.5원"),
        ("200000000.0001", "200,000,000.0001원"),
        # 세 자리 미만은 구분할 것이 없다.
        ("999.0000", "999원"),
        # 0으로 정리되는 값에 -를 붙이지 않는다.
        ("-0.0000", "0원"),
    ],
)
def test_delta_amount_groups_digits_and_drops_insignificant_zeroes(
    stored: str,
    expected: str,
) -> None:
    """
    증감액 표기 규칙.

    같은 문장의 원천 Fact 값이 공시 원문 표기(1,000백만원)라 쉼표를 쓰므로, 증감액만 쉼표 없이
    적으면 한 문장에 같은 축의 금액이 두 방식으로 실린다. 그 불일치가 외부 모델 경로에서
    실패로 나타났다(_grouped_amount docstring).

    값을 바꾸지 않는다. 지우는 것은 소수점 이하의 뜻 없는 0뿐이고, 0이 아닌 자리와 부호는
    그대로 둔다.
    """
    result = temporal_delta_result(
        calculations=[
            _delta_calculation(value=stored),
            _delta_calculation(
                calculation_id="calculation_revenue_delta_pct_2024_2025",
                operation=OPERATION_DELTA_PCT,
                value="20.0000",
                unit="percent",
            ),
        ]
    )

    assert expected in build_answer_response(result).answer


def test_delta_amount_does_not_change_the_stored_calculation() -> None:
    """표기를 정하는 일이 계산 결과를 고치는 일이 되지 않는다."""
    result = temporal_delta_result()

    stored = result.calculations[0].model_dump(mode="json")

    build_answer_response(result)

    assert result.calculations[0].model_dump(mode="json") == stored
    assert result.calculations[0].value == "200000000.0000"
    assert result.calculations[0].unit == "원"


def test_delta_amount_that_is_not_a_number_stops() -> None:
    """규칙을 지나지 못한 값을 그대로 흘리면 표기가 갈린 채 문장에 실린다."""
    result = temporal_delta_result(
        calculations=[
            _delta_calculation(value="약 이백억"),
            _delta_calculation(
                calculation_id="calculation_revenue_delta_pct_2024_2025",
                operation=OPERATION_DELTA_PCT,
                value="20.0000",
                unit="percent",
            ),
        ]
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_delta_amount_with_an_unlabelled_unit_stops() -> None:
    """정규화 단위 표기를 정하지 않은 채로 적으면 문장에 추출 계층의 어휘가 실린다."""
    result = temporal_delta_result(
        calculations=[
            _delta_calculation(unit="XYZ"),
            _delta_calculation(
                calculation_id="calculation_revenue_delta_pct_2024_2025",
                operation=OPERATION_DELTA_PCT,
                value="20.0000",
                unit="percent",
            ),
        ]
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_delta_and_delta_pct_must_reference_the_facts_in_the_same_order() -> None:
    """
    같은 Fact 2건을 쓰되 방향이 서로 반대인 결과는 문장이 되지 않는다.

    집합으로만 보면 통과한다. 그러면 증감액은 2024 -> 2025의 값이고 증감률은 2025 -> 2024의
    값인데, 문장 조립이 이전·이후를 증감액에서만 읽으므로 부호가 서로 어긋난 두 수치가 한
    문장에 나란히 실린다. 받는 쪽은 어느 쪽이 맞는지 알 수 없다.
    """
    result = temporal_delta_result(
        calculations=[
            _delta_calculation(input_ids=("fact_2024", "fact_2025")),
            _delta_calculation(
                calculation_id="calculation_revenue_delta_pct_2024_2025",
                operation=OPERATION_DELTA_PCT,
                value="20.0000",
                unit="percent",
                input_ids=("fact_2025", "fact_2024"),
            ),
        ]
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_delta_and_delta_pct_in_the_same_order_are_accepted() -> None:
    """대조군. 순서가 같으면 지금까지처럼 문장이 된다."""
    result = temporal_delta_result(
        calculations=[
            _delta_calculation(input_ids=("fact_2024", "fact_2025")),
            _delta_calculation(
                calculation_id="calculation_revenue_delta_pct_2024_2025",
                operation=OPERATION_DELTA_PCT,
                value="20.0000",
                unit="percent",
                input_ids=("fact_2024", "fact_2025"),
            ),
        ]
    )

    answer = build_answer_response(result).answer

    assert "200,000,000원" in answer
    assert "20.0000%" in answer


def test_delta_referencing_one_fact_twice_stops() -> None:
    """
    두 참조가 같은 Fact를 가리키면 견준 것이 없다.

    순서 검사만으로는 통과한다. 두 Calculation이 똑같이 어긋나 있기 때문이다.
    """
    result = temporal_delta_result(
        calculations=[
            _delta_calculation(input_ids=("fact_2024", "fact_2024")),
            _delta_calculation(
                calculation_id="calculation_revenue_delta_pct_2024_2025",
                operation=OPERATION_DELTA_PCT,
                value="20.0000",
                unit="percent",
                input_ids=("fact_2024", "fact_2024"),
            ),
        ]
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)



# --------------------------------------------------------------------------
# Pattern C — 기간별 파생지표의 변화(Q08)
#
# Fact 4건과 Calculation 3건(기간별 비율 2건 + 변화량 1건)을 함께 실은 결과만 문장이 된다.
# 단일 Fact 경로의 가드도, 위 Q07 경로도 그대로 두고 이 모양만 연다.
# --------------------------------------------------------------------------

Q08_COMPANY = "합성반도체(주)"
Q08_PREVIOUS_MARGIN_ID = "calculation_operating_margin_2024"
Q08_CURRENT_MARGIN_ID = "calculation_operating_margin_2025"
Q08_CHANGE_ID = "calculation_operating_margin_diff_pp_2024_2025"


def make_change(
    *,
    calculation_id: str = Q08_CHANGE_ID,
    operation: str = OPERATION_DIFF_PP,
    value: str = "-2.1017",
    unit: str | None = UNIT_PERCENTAGE_POINT,
    input_ids: tuple[str, str] = (Q08_PREVIOUS_MARGIN_ID, Q08_CURRENT_MARGIN_ID),
) -> CalculationResult:
    """calculator.diff가 만드는 것과 같은 모양(old, new 순서의 Calculation 참조)."""
    return CalculationResult(
        calculation_id=calculation_id,
        operation=operation,
        inputs=[calculation_reference(input_id) for input_id in input_ids],
        value=value,
        unit=unit,
        evidence_ids=["evidence_SYNTHETIC_01"],
    )


def temporal_derived_delta_result(
    *,
    previous_year: int = 2024,
    current_year: int = 2025,
    previous_margin: str = "45.6940",
    current_margin: str = "43.5923",
    change: CalculationResult | None = None,
) -> AgentExecutionResult:
    """
    Pattern C의 Q08 성공 결과와 같은 모양: Fact 4 · Calculation 3 · Comparison 0.

    개수와 참조 배선은 handlers.temporal_compare._derived_metric_calculations가 정본이다.
    우열 판정을 만들지 않는 것도 그쪽과 같다(evidence_and_answerability.md Calculation
    cardinality).

    비율 값을 원천 Fact에서 다시 계산해도 같은 값이 나오도록 두지 않는다. 같게 두면
    "다시 계산하지 않는다"를 시험할 수 없다.
    """
    previous_income = make_comparison_fact(
        fact_id="fact_SYNTHETIC_PREVIOUS_OPERATING_INCOME",
        company=Q08_COMPANY,
        metric=MetricConcept.OPERATING_INCOME,
        fiscal_year=previous_year,
        raw_value="250",
        normalized_value="250000000",
    )
    previous_revenue = make_comparison_fact(
        fact_id="fact_SYNTHETIC_PREVIOUS_REVENUE",
        company=Q08_COMPANY,
        fiscal_year=previous_year,
        raw_value="2,000",
        normalized_value="2000000000",
    )
    current_income = make_comparison_fact(
        fact_id="fact_SYNTHETIC_CURRENT_OPERATING_INCOME",
        company=Q08_COMPANY,
        metric=MetricConcept.OPERATING_INCOME,
        fiscal_year=current_year,
        raw_value="300",
        normalized_value="300000000",
    )
    current_revenue = make_comparison_fact(
        fact_id="fact_SYNTHETIC_CURRENT_REVENUE",
        company=Q08_COMPANY,
        fiscal_year=current_year,
        raw_value="2,500",
        normalized_value="2500000000",
    )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[previous_income, previous_revenue, current_income, current_revenue],
        evidence=[make_evidence()],
        calculations=[
            make_margin(
                calculation_id=Q08_PREVIOUS_MARGIN_ID,
                numerator=previous_income,
                denominator=previous_revenue,
                value=previous_margin,
            ),
            make_margin(
                calculation_id=Q08_CURRENT_MARGIN_ID,
                numerator=current_income,
                denominator=current_revenue,
                value=current_margin,
            ),
            change if change is not None else make_change(),
        ],
    )


def test_temporal_derived_delta_names_match_the_calculator() -> None:
    """
    변화량의 연산 이름과 단위는 agent.calculator가, 비율의 연산 이름은 대응표가 정본이다.

    순환 import 때문에 이 모듈이 calculator에서 가져오지 못하므로 같은 문자열을 두 곳에 둔다.
    어긋나면 Q08 문장이 조용히 사라지므로 여기서 붙여 둔다(Q07의 같은 검사와 같은 자리).
    """
    assert OPERATION_DIFF_PP == CALCULATOR_OPERATION_DIFF_PP
    assert UNIT_PERCENTAGE_POINT == CALCULATOR_UNIT_PERCENTAGE_POINT
    assert OPERATION_RATIO == CALCULATOR_OPERATION_RATIO


def test_q08_shape_renders_both_years_both_margins_and_the_change() -> None:
    """Fact 4 · Calculation 3이 하나의 답변으로 나간다."""
    result = temporal_derived_delta_result()

    response = build_answer_response(result)

    assert response.answer == (
        f"{Q08_COMPANY}의 연결 기준 영업이익률은 2024년 45.6940%, 2025년 43.5923%이며, "
        "두 연도의 차이는 -2.1017%p입니다."
    )
    assert response.status is AnswerabilityStatus.ANSWERABLE
    assert response.reason is None
    assert response.facts == result.facts
    assert response.evidence == result.evidence


def test_q08_answer_is_deterministic() -> None:
    result = temporal_derived_delta_result()

    assert build_answer_response(result).answer == build_answer_response(result).answer


def test_q08_values_are_read_from_the_calculations_not_recomputed_from_the_facts() -> None:
    """
    문장의 세 수치는 모두 Calculation에서 온다.

    원천 Fact로 다시 계산하면 12.50%와 12.00%가 나오는 결과에 계산 계층이 확정한 값을
    실었다. 문장이 다시 계산하면 응답에 실린 근거와 문장의 수치가 어긋난다.
    """
    answer = build_answer_response(temporal_derived_delta_result()).answer

    assert "45.6940%" in answer
    assert "43.5923%" in answer
    assert "12.50" not in answer
    assert "12.00" not in answer


def test_q08_ratio_values_are_written_in_percent() -> None:
    """기간별 영업이익률은 백분율이다. 두 값 모두 %로 적힌다."""
    answer = build_answer_response(temporal_derived_delta_result()).answer

    assert "2024년 45.6940%," in answer
    assert "2025년 43.5923%이며" in answer


def test_q08_change_is_written_in_percentage_points_not_percent() -> None:
    """
    영업이익률 45.69%와 그 차이 -2.10%p는 서로 다른 축의 값이다.

    차이에 %를 붙이면 "영업이익률이 2.1017% 달라졌다"가 된다. 그것은 45.69%가 46.65%가 됐다는
    뜻이고, 계산 계층이 만든 값(43.59%가 됐다)과 다른 사실이다.
    """
    answer = build_answer_response(temporal_derived_delta_result()).answer

    assert "두 연도의 차이는 -2.1017%p입니다" in answer
    assert "-2.1017%입니다" not in answer


def test_q08_change_value_is_never_followed_by_a_bare_percent_sign() -> None:
    """
    회귀 자리를 값 하나가 아니라 표기 규칙으로 고정한다.

    위 테스트는 현재 문장의 리터럴을 본다. 문구가 바뀌면 그 단언은 함께 바뀌고, 그때 %p가
    %로 되돌아가도 알아채지 못할 수 있다. 그래서 "변화량 값 뒤에는 반드시 p가 온다"를 따로
    고정한다.
    """
    change_value = "-2.1017"

    answer = build_answer_response(temporal_derived_delta_result()).answer

    assert f"{change_value}%p" in answer

    remainder = answer.split(f"{change_value}%", 1)[1]

    assert remainder.startswith("p")


def test_a_percentage_point_change_cannot_be_rendered_as_percent() -> None:
    """
    단위를 대응표에만 맡기지 않는다.

    대응표는 "이 단위를 어떻게 적는가"만 정한다. 변화량 자리에 percent가 실리면 그 값은 %로
    적혀 나가고, 문장은 형식이 정상이며 수치도 계산 계층의 값 그대로다. 축이 바뀐 사실이
    어디에도 남지 않으므로, 표기를 고르기 전에 자리별 기대 단위를 확인한다.
    """
    result = temporal_derived_delta_result(change=make_change(unit=UNIT_PERCENT))

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert UNIT_PERCENTAGE_POINT in str(error.value)


def test_a_period_ratio_carrying_percentage_points_stops() -> None:
    """반대 방향도 같다. 기간별 비율 자리에 percentage point가 오면 그 값은 비율이 아니다."""
    result = temporal_derived_delta_result()
    result.calculations[0].unit = UNIT_PERCENTAGE_POINT

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert UNIT_PERCENT in str(error.value)


def test_the_two_percent_axes_keep_separate_labels() -> None:
    """
    %와 %p가 같은 표기로 수렴하면 위 검사들이 모두 통과하면서 뜻이 사라진다.

    두 단위는 대응표의 이웃한 두 줄이라 한쪽을 고칠 때 다른 쪽에 맞춰 버리기 쉽다.
    """
    percent_label = CALCULATION_UNIT_LABELS[UNIT_PERCENT]
    percentage_point_label = CALCULATION_UNIT_LABELS[UNIT_PERCENTAGE_POINT]

    assert percent_label.strip()
    assert percentage_point_label.strip()
    assert percent_label != percentage_point_label


def test_q08_answer_does_not_add_a_direction_or_a_cause() -> None:
    """방향은 부호에 담겨 있고 원인은 결과에 없다. 둘 다 문장이 새로 만들지 않는다."""
    answer = build_answer_response(temporal_derived_delta_result()).answer

    for invented in ("늘", "줄", "증가", "감소", "때문", "영향", "덕분"):
        assert invented not in answer


def test_four_facts_without_the_derived_calculations_still_stop() -> None:
    """일반 다중 Fact 가드는 그대로다. Calculation이 없으면 문장을 만들지 않는다."""
    result = temporal_derived_delta_result()
    result.calculations = []

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_four_facts_with_three_unrelated_calculations_still_stop() -> None:
    """
    개수만 맞는 결과로 이 경로가 열리지 않는다.

    "Fact 4건과 Calculation 3건"만 보는 가드였다면 이 결과가 파생지표 변화 문장으로 나간다.
    """
    result = temporal_derived_delta_result()
    result.calculations[0].operation = OPERATION_DELTA
    result.calculations[1].operation = OPERATION_DELTA_PCT

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_change_pointing_at_something_other_than_the_two_ratios_stops() -> None:
    """변화량이 결과에 실린 두 비율만 참조해야 한다."""
    result = temporal_derived_delta_result(
        change=make_change(input_ids=(Q08_PREVIOUS_MARGIN_ID, "calculation_SYNTHETIC_9999"))
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_change_referencing_the_two_ratios_in_reverse_order_stops() -> None:
    """
    변화량의 inputs는 old, new 순서다(calculator.diff).

    뒤집힌 참조를 통과시키면 문장은 목록 순서대로 2024년, 2025년을 적으면서 부호가 반대인
    변화량을 나란히 싣는다. 부호가 뒤집힌 변화량도 그럴듯해서 받는 쪽이 알아챌 수 없다.
    """
    result = temporal_derived_delta_result(
        change=make_change(input_ids=(Q08_CURRENT_MARGIN_ID, Q08_PREVIOUS_MARGIN_ID))
    )

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_change_referencing_facts_instead_of_the_ratios_stops() -> None:
    """원천 Fact를 직접 뺀 값은 백분율의 변화량이 아니다."""
    result = temporal_derived_delta_result()
    result.calculations[2].inputs = [
        fact_reference("fact_SYNTHETIC_PREVIOUS_OPERATING_INCOME"),
        fact_reference("fact_SYNTHETIC_CURRENT_OPERATING_INCOME"),
    ]

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_ratio_inputs_pointing_outside_the_result_stop() -> None:
    """
    두 비율의 입력 넷이 결과에 실린 Fact 4건이어야 한다.

    결과에 없는 값을 가리키는 참조로 문장을 만들면 문장의 근거가 결과와 어긋난다.
    """
    result = temporal_derived_delta_result()
    result.calculations[0].inputs = [
        fact_reference("fact_SYNTHETIC_PREVIOUS_OPERATING_INCOME"),
        fact_reference("fact_SYNTHETIC_9999"),
    ]

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_a_ratio_referencing_one_fact_twice_stops() -> None:
    """분자와 분모가 같은 Fact이면 그 비율은 기간별 영업이익률이 아니다."""
    result = temporal_derived_delta_result()
    result.calculations[0].inputs = [
        fact_reference("fact_SYNTHETIC_PREVIOUS_OPERATING_INCOME"),
        fact_reference("fact_SYNTHETIC_PREVIOUS_OPERATING_INCOME"),
    ]

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_change_unit_without_a_label_stops() -> None:
    """표기를 정하지 않은 단위를 그대로 적으면 문장에 계산 계층의 어휘가 실린다."""
    result = temporal_derived_delta_result(change=make_change(unit="synthetic-unit"))

    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(result)

    assert "synthetic-unit" in str(error.value)


def test_two_ratios_of_the_same_year_stop() -> None:
    """같은 기간끼리는 변화량이 정의되지 않는다(guard②와 같은 규칙)."""
    result = temporal_derived_delta_result(previous_year=2025, current_year=2025)

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_two_ratios_from_two_companies_stop() -> None:
    """기업이 갈린 두 값은 한 기업의 시계열이 아니다. 한쪽을 골라 적지 않는다."""
    result = temporal_derived_delta_result()
    result.facts[0].company = "합성전자(주)"
    result.facts[1].company = "합성전자(주)"

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_a_ratio_whose_inputs_span_two_scopes_stops() -> None:
    """연결과 별도를 섞은 비율은 어느 기준의 값도 아니다."""
    result = temporal_derived_delta_result()
    result.facts[0].accounting_scope = FactAccountingScope.SEPARATE

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_a_period_without_a_fiscal_year_does_not_produce_an_answer() -> None:
    """어느 해의 값인지 적지 못하면 '두 해가 어떻게 달라졌나'의 답이 되지 않는다."""
    result = temporal_derived_delta_result()
    result.facts[0].fiscal_year = None
    result.facts[1].fiscal_year = None

    with pytest.raises(MissingAnswerSupportError):
        build_answer_response(result)


def test_a_non_annual_source_period_is_not_written_as_a_year() -> None:
    """분기 값이 섞인 비율을 연간 비율처럼 적지 않는다."""
    result = temporal_derived_delta_result()
    result.facts[0].fiscal_period = "Q1"

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_a_ratio_outside_the_derived_metric_table_stops() -> None:
    """정의를 되짚을 수 없는 값에 영업이익률이라는 이름을 붙이지 않는다."""
    result = temporal_derived_delta_result()
    result.facts[0].metric = MetricConcept.REVENUE

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(result)


def test_partial_source_support_has_a_partial_limitation_message() -> None:
    message = LIMITATION_MESSAGE_BY_REASON[
        AnswerabilityReason.PARTIAL_SOURCE_SUPPORT
    ]

    assert message.strip()
    assert "원문 근거" in message
