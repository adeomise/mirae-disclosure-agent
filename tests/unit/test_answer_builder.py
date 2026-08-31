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
    LIMITATION_MESSAGE_BY_REASON,
    METRIC_LABELS,
    POLICY_REFUSAL_MESSAGE_BY_DETAIL_REASON,
    REASON_INCOMPARABLE_CALCULATION_INPUTS,
    REASON_INCOMPARABLE_PERIODS,
    REFUSAL_REASONS_WITHOUT_SUPPORTING_PAYLOAD,
    SCOPE_OMITTED_IN_ANSWER,
    STAGE_COMPARABILITY,
    STATUSES_ALLOWING_NO_REASON,
    UNAVAILABLE_MESSAGE_BY_REASON,
    UNMAPPED_ANSWERABILITY_REASONS,
    UNRESOLVED_WITHOUT_REASON_MESSAGE,
    MissingAnswerabilityStatusError,
    MissingAnswerSupportError,
    UnsupportedExecutionStatusError,
    UnsupportedResultShapeError,
    _topic_particle,
    build_answer_response,
)
from disclosure_agent.agent.safety import (
    REASON_FORWARD_LOOKING_PREDICTION,
    REASON_INVESTMENT_ADVICE,
    STAGE_ANSWERABILITY_GATE,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    EventLinkRelation,
    EventLinkResolutionStatus,
    EventType,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    AnswerabilityDetail,
    Event,
    EventHistory,
    EventLink,
    Evidence,
    ExecutionIssue,
    Fact,
    FieldChange,
    RawReference,
)

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


def test_policy_refusal_table_covers_the_gate_categories() -> None:
    """safety.py가 만드는 두 사유는 반드시 고유한 문장을 가진다."""
    assert set(POLICY_REFUSAL_MESSAGE_BY_DETAIL_REASON) == {
        REASON_INVESTMENT_ADVICE,
        REASON_FORWARD_LOOKING_PREDICTION,
    }
