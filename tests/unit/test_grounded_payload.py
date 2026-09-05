"""근거 payload 조립 테스트.

여기서 고정하는 것:
    - 결정론 경로가 답하지 못하는 결과 모양은 payload로도 나가지 않는다.
    - 일부 carrier만 골라 보내지 않는다. 표현할 수 없는 것이 섞이면 통째로 멈춘다.
    - 원문(raw_text)과 내부 진단(answerability_detail 등)은 payload 밖에 남는다.
    - 수치와 표기는 answer_builder 대응표가 만든 것을 그대로 옮긴다.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from disclosure_agent.agent.answer_builder import (
    OPERATION_DELTA,
    OPERATION_DELTA_PCT,
    OPERATION_DIFF_PP,
    UNIT_PERCENTAGE_POINT,
    build_answer_response,
)
from disclosure_agent.agent.derived_metrics import OPERATION_RATIO, UNIT_PERCENT
from disclosure_agent.agent.grounded_payload import (
    PayloadNotGroundableError,
    build_grounded_payload,
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
    SectionScope,
    SummaryItemType,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    AnswerabilityDetail,
    CalculationResult,
    ComparisonResult,
    DocumentSummary,
    Event,
    EventHistory,
    EventLink,
    Evidence,
    EvidenceProvenance,
    Fact,
    FieldChange,
    RawReference,
    ResultInputReference,
    SummaryItem,
)

QUESTION = "한미반도체 2025년 연결 기준 매출액이 얼마야?"

RAW_TEXT = "576,684,619,527"


def summary_item() -> SummaryItem:
    return SummaryItem(
        item_id="item_1",
        item_type=SummaryItemType.PARAGRAPH,
        label="설비 투자 개요",
        evidence_ids=["ev_revenue"],
    )


def evidence(evidence_id: str = "ev_revenue", doc_id: str = "doc_a") -> Evidence:
    """원문과 provenance가 실린 Evidence. payload가 무엇을 떨어뜨리는지 보려면 필요하다."""
    return Evidence(
        evidence_id=evidence_id,
        doc_id=doc_id,
        location_type=EvidenceLocationType.TABLE,
        table_name="연결 손익계산서",
        row_label="매출액",
        column_label="제55기",
        raw_text=RAW_TEXT,
        provenance=EvidenceProvenance(table_id="T-1", row_index=3, acode="revenue"),
    )


def fact(
    fact_id: str = "fact_revenue",
    *,
    company: str = "한미반도체",
    metric: MetricConcept = MetricConcept.REVENUE,
    fiscal_year: int | None = 2025,
    fiscal_period: str | None = "FY",
    scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
    raw_value: str | None = RAW_TEXT,
    raw_unit: str | None = "원",
    normalized_value: float | str | None = 576684619527,
    normalized_unit: str | None = "KRW",
    evidence_ids: list[str] | None = None,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        company=company,
        metric=metric,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        accounting_scope=scope,
        raw_value=raw_value,
        raw_unit=raw_unit,
        normalized_value=normalized_value,
        normalized_unit=normalized_unit,
        evidence_ids=evidence_ids if evidence_ids is not None else ["ev_revenue"],
    )


def completed(
    *,
    status: AnswerabilityStatus = AnswerabilityStatus.ANSWERABLE,
    **carriers: object,
) -> AgentExecutionResult:
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=status,
        **carriers,  # type: ignore[arg-type]
    )


def single_fact_result(**overrides: object) -> AgentExecutionResult:
    return completed(facts=[fact()], evidence=[evidence()], **overrides)


def grounded(result: AgentExecutionResult) -> object:
    return build_grounded_payload(result, question=QUESTION)


# --------------------------------------------------------------------------
# Fact
# --------------------------------------------------------------------------


def test_single_fact_is_grounded_with_the_deterministic_vocabulary() -> None:
    """표기는 answer_builder의 대응표가 정본이다. 여기서 새로 만들지 않는다."""
    payload = grounded(single_fact_result())

    [grounded_fact] = payload.facts  # type: ignore[attr-defined]

    assert grounded_fact.fact_id == "fact_revenue"
    assert grounded_fact.company == "한미반도체"
    assert grounded_fact.metric_label == "매출액"
    assert grounded_fact.fiscal_year == 2025
    assert grounded_fact.scope_label == "연결"
    assert grounded_fact.display_value == f"{RAW_TEXT}원"
    # Fact.normalized_value가 float이라 정규화 표현에 소수점이 남는다. 여기서 다듬지 않는다.
    # payload가 값을 손보면 결정론 문장과 다른 수치가 되고, 그 차이는 문장만 보면 보이지 않는다.
    assert grounded_fact.normalized_display == "576684619527.0원"


def test_fact_value_matches_the_deterministic_answer_sentence() -> None:
    """
    같은 Fact가 두 경로에서 다른 자릿수로 불리면 안 된다.

    HCX 답변의 수치 검증은 payload의 문자열을 화이트리스트로 쓴다. 그 문자열이 결정론 문장에
    없으면 대조군이 사라진다.
    """
    result = single_fact_result()

    payload = grounded(result)

    assert payload.facts[0].display_value in build_answer_response(result).answer  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "scope",
    [FactAccountingScope.UNKNOWN, FactAccountingScope.NOT_APPLICABLE],
)
def test_unconfirmed_accounting_scope_is_left_empty(scope: FactAccountingScope) -> None:
    """확정하지 못한 기준을 연결로 채우면 Q01의 주요 실패 모드를 payload에서 다시 만든다."""
    payload = grounded(completed(facts=[fact(scope=scope)], evidence=[evidence()]))

    assert payload.facts[0].scope_label is None  # type: ignore[attr-defined]
    assert "accounting_scope" not in payload.as_dict()["facts"][0]  # type: ignore[index,attr-defined]


def test_metric_without_a_defined_label_is_not_grounded() -> None:
    """대응표에 없는 지표에 generic 표기를 붙이지 않는다."""
    unsupported = fact(metric=MetricConcept.HOLDING_RATIO)

    with pytest.raises(PayloadNotGroundableError, match="metric"):
        grounded(completed(facts=[unsupported], evidence=[evidence()]))


def test_non_annual_period_is_not_grounded() -> None:
    """분기 Fact를 연도만 실어 보내면 모델이 그것을 연간 값으로 적는다."""
    quarterly = fact(fiscal_period="Q3")

    with pytest.raises(PayloadNotGroundableError, match="fiscal_period"):
        grounded(completed(facts=[quarterly], evidence=[evidence()]))


# --------------------------------------------------------------------------
# Calculation / Comparison
# --------------------------------------------------------------------------


def margin_facts() -> list[Fact]:
    """영업이익률의 입력. 순서는 대응표의 (분자, 분모)와 같아야 한다."""
    return [
        fact(
            "fact_oi",
            metric=MetricConcept.OPERATING_INCOME,
            raw_value="100",
            normalized_value=100,
            evidence_ids=["ev_oi"],
        ),
        fact("fact_rev", raw_value="1,000", normalized_value=1000, evidence_ids=["ev_revenue"]),
    ]


def margin_calculation(calculation_id: str = "calc_margin") -> CalculationResult:
    return CalculationResult(
        calculation_id=calculation_id,
        operation=OPERATION_RATIO,
        inputs=[
            ResultInputReference(input_type=ResultInputType.FACT, input_id="fact_oi"),
            ResultInputReference(input_type=ResultInputType.FACT, input_id="fact_rev"),
        ],
        value=10.0,
        unit=UNIT_PERCENT,
        evidence_ids=["ev_oi", "ev_revenue"],
    )


def comparison_result() -> AgentExecutionResult:
    """Q10 계열: 두 Fact를 견준 결과."""
    left = fact("fact_left", company="A사", evidence_ids=["ev_revenue"])
    right = fact("fact_right", company="B사", normalized_value=1, evidence_ids=["ev_b"])

    return completed(
        facts=[left, right],
        evidence=[evidence(), evidence("ev_b", "doc_b")],
        comparisons=[
            ComparisonResult(
                comparison_id="cmp_1",
                left=ResultInputReference(input_type=ResultInputType.FACT, input_id="fact_left"),
                right=ResultInputReference(input_type=ResultInputType.FACT, input_id="fact_right"),
                relation=ComparisonRelation.LEFT_GREATER,
            )
        ],
    )


def test_calculation_carries_the_derived_label_and_input_links_only() -> None:
    """입력 값을 함께 싣지 않는다. 실으면 모델이 검산할 표면이 생긴다."""
    result = completed(
        facts=margin_facts(),
        calculations=[margin_calculation()],
        evidence=[evidence(), evidence("ev_oi", "doc_a")],
        comparisons=[
            ComparisonResult(
                comparison_id="cmp_margin",
                left=ResultInputReference(
                    input_type=ResultInputType.CALCULATION, input_id="calc_margin"
                ),
                right=ResultInputReference(input_type=ResultInputType.FACT, input_id="fact_rev"),
                relation=ComparisonRelation.RIGHT_GREATER,
            )
        ],
    )

    payload = grounded(result)

    [calculation] = payload.calculations  # type: ignore[attr-defined]

    assert calculation.metric_label == "영업이익률"
    assert calculation.display_value == "10.0%"
    assert calculation.input_ids == ("fact_oi", "fact_rev")
    assert calculation.fiscal_year == 2025
    assert calculation.scope_label == "연결"


def test_comparison_relation_is_copied_without_recomputing() -> None:
    """우열은 이미 확정됐다. payload는 관계 이름만 옮긴다."""
    payload = grounded(comparison_result())

    [comparison] = payload.comparisons  # type: ignore[attr-defined]

    assert comparison.relation == ComparisonRelation.LEFT_GREATER.value
    assert (comparison.left_id, comparison.right_id) == ("fact_left", "fact_right")


def test_comparison_pointing_outside_the_payload_is_not_grounded() -> None:
    """가리키는 대상이 없으면 모델이 payload 밖에서 대상을 찾아야 한다. 그것은 추측이다."""
    result = comparison_result()

    result.comparisons[0].right.input_id = "fact_missing"

    with pytest.raises(PayloadNotGroundableError, match="cmp_1"):
        grounded(result)


def test_calculation_built_on_another_calculation_is_not_grounded() -> None:
    """파생 개념 이름을 정할 대응표가 아직 없다. 여기서 이름을 붙이지 않는다."""
    chained = margin_calculation("calc_change")

    chained.inputs[0].input_type = ResultInputType.CALCULATION

    with pytest.raises(PayloadNotGroundableError, match="calc_change"):
        grounded(
            completed(
                facts=margin_facts(),
                calculations=[chained],
                evidence=[evidence()],
                comparisons=[
                    ComparisonResult(
                        comparison_id="cmp_x",
                        left=ResultInputReference(
                            input_type=ResultInputType.FACT, input_id="fact_oi"
                        ),
                        right=ResultInputReference(
                            input_type=ResultInputType.FACT, input_id="fact_rev"
                        ),
                        relation=ComparisonRelation.EQUAL,
                    )
                ],
            )
        )


# --------------------------------------------------------------------------
# EventHistory
# --------------------------------------------------------------------------


def history_result(
    *,
    chain_complete: bool = True,
    status: AnswerabilityStatus = AnswerabilityStatus.ANSWERABLE,
    reason: AnswerabilityReason | None = None,
) -> AgentExecutionResult:
    history = EventHistory(
        event_id="evt_1",
        document_ids=["doc_a", "doc_b"],
        changes=[
            FieldChange(field_name="contract_amount", before=100.0, after=120.0, unit="원"),
        ],
        chain_complete=chain_complete,
    )

    return completed(
        status=status,
        answerability_reason=reason,
        event_histories=[history],
        evidence=[evidence()],
    )


def test_event_history_carries_counts_and_completeness_only() -> None:
    """변경 항목의 영문 이름과 전후 값은 싣지 않는다."""
    payload = grounded(history_result())

    [history] = payload.event_histories  # type: ignore[attr-defined]

    assert (history.document_count, history.change_count) == (2, 1)
    assert history.chain_complete is True

    body = payload.to_json()  # type: ignore[attr-defined]

    assert "contract_amount" not in body
    assert "120" not in body


def q15_shaped_result() -> AgentExecutionResult:
    """실측 Q15와 같은 모양. 공시 4건, 필드 변경 4건, EventLink 3건.

    사용자가 "몇 번 바뀌었어?"로 묻는 셈은 정정 이력의 개수이며 여기서는 3이다. payload가 싣는
    두 셈(4와 4)에는 그 값이 없다.
    """
    history = EventHistory(
        event_id="evt_facility",
        document_ids=["doc_original", "doc_fix_1", "doc_fix_2", "doc_fix_3"],
        changes=[
            FieldChange(field_name="investment_amount", before=100.0, after=120.0, unit="원"),
            FieldChange(field_name="investment_end_date", before="2025-01-01", after="2025-06-01"),
            FieldChange(field_name="investment_amount", before=120.0, after=140.0, unit="원"),
            FieldChange(field_name="investment_end_date", before="2025-06-01", after="2026-01-01"),
        ],
        chain_complete=True,
    )

    links = [
        EventLink(
            link_id=f"link_fix_{index}",
            source_doc_id=f"doc_fix_{index}",
            relation=EventLinkRelation.CORRECTION_OF,
            raw_reference=RawReference(
                document_title="신규시설투자등",
                original_filing_date="2024-04-30",
                correction_filing_date=f"2025-0{index}-01",
                correction_reason="정정",
            ),
            resolved_doc_id="doc_original",
            resolution_status=EventLinkResolutionStatus.RESOLVED,
        )
        for index in (1, 2, 3)
    ]

    return completed(
        status=AnswerabilityStatus.ANSWERABLE,
        event_histories=[history],
        event_links=links,
        evidence=[evidence()],
    )


def test_event_links_block_grounding() -> None:
    """
    답에 필요한 셈이 payload에 없는 모양은 외부 모델에 넘기지 않는다.

    "몇 번 바뀌었어?"의 답은 EventLink의 개수(3)이고 payload가 싣는 셈은 공시 4건과 필드 변경
    4건뿐이다. 그대로 보내면 모델은 갖고 있는 수로 답하며 "4번 바뀌었습니다"가 나간다. 그 4는
    payload 안의 수라서 수치 검증도 값-주인 짝 검사도 통과하고, 틀렸다는 사실이 남는 자리가
    없다.

    셈을 여기서 새로 싣지 않는다. 그 값을 무엇이라 부르고 어떤 표기로 적을지가 결정론 경로에도
    아직 없으므로, 그 계약이 생기기 전까지 이 모양은 통째로 결정론 경로에 맡긴다.
    """
    result = q15_shaped_result()

    # 문제의 전제. 답이 될 3은 payload가 싣는 두 셈 어디에도 없다.
    assert len(result.event_histories[0].document_ids) == 4
    assert len(result.event_histories[0].changes) == 4
    assert len(result.event_links) == 3

    with pytest.raises(PayloadNotGroundableError, match="EventLink"):
        grounded(result)


def test_q15_shaped_result_keeps_its_deterministic_answer_and_count() -> None:
    """
    막는 것은 외부 모델 경로뿐이다. 결정론 경로는 그대로 답하고, 사용자에게 답할 셈도 그대로
    남는다.

    EventLink는 구조화 응답에 실려 나가므로 "몇 번"의 값 3은 사라지지 않는다. 그 값을 문장으로
    옮기는 일은 이 PR의 범위가 아니다.
    """
    result = q15_shaped_result()

    response = build_answer_response(result)

    assert response.answer
    assert len(response.event_links) == 3


def test_event_history_without_links_is_still_grounded() -> None:
    """
    셈이 모두 표현된 이력은 지금까지와 같이 넘어간다.

    EventLink가 없으면 payload 밖에 남는 셈이 없다. 막는 기준은 이력이라는 모양 자체가 아니라
    표현하지 못하는 셈이 있는지다.
    """
    result = history_result()

    assert result.event_links == []

    payload = grounded(result)

    [history] = payload.event_histories  # type: ignore[attr-defined]

    assert (history.document_count, history.change_count) == (2, 1)


def test_partial_history_carries_the_deterministic_limitation_sentence() -> None:
    """한계 진술을 모델이 짓게 하지 않는다. 표현이 흔들리면 확인하지 못한 범위가 흔들린다."""
    payload = grounded(
        history_result(
            chain_complete=False,
            status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
            reason=AnswerabilityReason.INCOMPLETE_CHAIN,
        )
    )

    assert payload.limitation is not None  # type: ignore[attr-defined]
    assert payload.limitation.startswith("다만")  # type: ignore[attr-defined]


def test_answerable_result_has_no_limitation() -> None:
    assert grounded(single_fact_result()).limitation is None  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# 기간 변화 — Q07 증감, Q08 파생지표 변화
#
# 두 모양은 우열 판정 없이 Fact를 여러 건 싣는다. 그 조합은 원래 payload가 막던 자리이고,
# 여기서 고정하는 것은 "결정론 경로가 답하는 이 두 모양만 열렸다"이다.
#
# 수치를 결정론 문장과 대조한다. 같은 계산이 두 경로에서 다른 자릿수로 불리면 HCX 답변의 수치
# 검증이 쓰는 화이트리스트가 결정론 문장과 어긋나고, 그 어긋남은 문장만 보면 보이지 않는다.
# --------------------------------------------------------------------------


def revenue_fact(fact_id: str, *, fiscal_year: int, raw: str, normalized: int) -> Fact:
    return fact(
        fact_id,
        fiscal_year=fiscal_year,
        raw_value=raw,
        normalized_value=normalized,
        evidence_ids=[f"ev_rev_{fiscal_year}"],
    )


def operating_income_fact(fact_id: str, *, fiscal_year: int, raw: str, normalized: int) -> Fact:
    return fact(
        fact_id,
        metric=MetricConcept.OPERATING_INCOME,
        fiscal_year=fiscal_year,
        raw_value=raw,
        normalized_value=normalized,
        evidence_ids=[f"ev_oi_{fiscal_year}"],
    )


def fact_inputs(*fact_ids: str) -> list[ResultInputReference]:
    return [
        ResultInputReference(input_type=ResultInputType.FACT, input_id=fact_id)
        for fact_id in fact_ids
    ]


def calculation_inputs(*calculation_ids: str) -> list[ResultInputReference]:
    return [
        ResultInputReference(input_type=ResultInputType.CALCULATION, input_id=calculation_id)
        for calculation_id in calculation_ids
    ]


def temporal_delta_result(
    *,
    delta_value: str = "17767427980.0000",
    delta_pct_value: str = "3.1789",
    rate_inputs: list[ResultInputReference] | None = None,
) -> AgentExecutionResult:
    """Q07 모양. 원천 Fact 2건과 그 둘에서 나온 증감액·증감률.

    참조 순서는 calculator.delta의 계약대로 previous, current다. 두 Calculation이 같은 순서로
    같은 Fact를 가리켜야 결정론 경로가 이 모양을 알아본다(_is_temporal_delta_shape).
    """
    previous = revenue_fact(
        "fact_rev_2024", fiscal_year=2024, raw="558,917,191,547", normalized=558917191547
    )

    current = revenue_fact(
        "fact_rev_2025", fiscal_year=2025, raw="576,684,619,527", normalized=576684619527
    )

    ordered = fact_inputs("fact_rev_2024", "fact_rev_2025")

    amount = CalculationResult(
        calculation_id="calc_revenue_delta",
        operation=OPERATION_DELTA,
        inputs=ordered,
        value=delta_value,
        # 증감액의 단위는 정책이 아니라 두 Fact가 함께 쓰는 정규화 단위다
        # (calculator._shared_normalized_unit).
        unit="KRW",
        evidence_ids=["ev_rev_2024", "ev_rev_2025"],
    )

    rate = CalculationResult(
        calculation_id="calc_revenue_delta_pct",
        operation=OPERATION_DELTA_PCT,
        inputs=fact_inputs("fact_rev_2024", "fact_rev_2025")
        if rate_inputs is None
        else rate_inputs,
        value=delta_pct_value,
        unit=UNIT_PERCENT,
        evidence_ids=["ev_rev_2024", "ev_rev_2025"],
    )

    return completed(
        facts=[previous, current],
        calculations=[amount, rate],
        evidence=[
            evidence("ev_rev_2024", "doc_2024"),
            evidence("ev_rev_2025", "doc_2025"),
        ],
    )


def temporal_derived_delta_result(
    *,
    margin_2024_value: str = "45.6940",
    margin_2025_value: str = "43.5923",
    change_value: str = "-2.1017",
) -> AgentExecutionResult:
    """Q08 모양. 원천 Fact 4건, 기간별 비율 2건, 두 비율의 차이 1건.

    비율의 참조 순서는 파생지표 대응표의 (분자, 분모)와 같아야 하고, 차이의 참조 순서는
    calculator.diff의 계약대로 old, new다.
    """
    facts = [
        operating_income_fact(
            "fact_oi_2024", fiscal_year=2024, raw="255,391,604,854", normalized=255391604854
        ),
        revenue_fact(
            "fact_rev_2024", fiscal_year=2024, raw="558,917,191,547", normalized=558917191547
        ),
        operating_income_fact(
            "fact_oi_2025", fiscal_year=2025, raw="251,390,142,142", normalized=251390142142
        ),
        revenue_fact(
            "fact_rev_2025", fiscal_year=2025, raw="576,684,619,527", normalized=576684619527
        ),
    ]

    margin_2024 = CalculationResult(
        calculation_id="calc_margin_2024",
        operation=OPERATION_RATIO,
        inputs=fact_inputs("fact_oi_2024", "fact_rev_2024"),
        value=margin_2024_value,
        unit=UNIT_PERCENT,
        evidence_ids=["ev_oi_2024", "ev_rev_2024"],
    )

    margin_2025 = CalculationResult(
        calculation_id="calc_margin_2025",
        operation=OPERATION_RATIO,
        inputs=fact_inputs("fact_oi_2025", "fact_rev_2025"),
        value=margin_2025_value,
        unit=UNIT_PERCENT,
        evidence_ids=["ev_oi_2025", "ev_rev_2025"],
    )

    change = CalculationResult(
        calculation_id="calc_margin_diff",
        operation=OPERATION_DIFF_PP,
        inputs=calculation_inputs("calc_margin_2024", "calc_margin_2025"),
        value=change_value,
        unit=UNIT_PERCENTAGE_POINT,
        evidence_ids=["ev_oi_2024", "ev_rev_2024", "ev_oi_2025", "ev_rev_2025"],
    )

    return completed(
        facts=facts,
        calculations=[margin_2024, margin_2025, change],
        evidence=[
            evidence("ev_oi_2024", "doc_2024"),
            evidence("ev_rev_2024", "doc_2024"),
            evidence("ev_oi_2025", "doc_2025"),
            evidence("ev_rev_2025", "doc_2025"),
        ],
    )


def test_temporal_delta_carries_every_fact_calculation_and_evidence() -> None:
    """Q07의 근거가 하나도 빠지지 않고 실린다. 빠지면 그 값이 답변 어디에도 남지 않는다."""
    payload = grounded(temporal_delta_result())

    assert [item.fact_id for item in payload.facts] == [  # type: ignore[attr-defined]
        "fact_rev_2024",
        "fact_rev_2025",
    ]

    assert [
        item.calculation_id
        for item in payload.calculations  # type: ignore[attr-defined]
    ] == ["calc_revenue_delta", "calc_revenue_delta_pct"]

    assert [item.evidence_id for item in payload.evidence] == [  # type: ignore[attr-defined]
        "ev_rev_2024",
        "ev_rev_2025",
    ]

    # 우열 판정도 이력도 만들지 않는다. 없는 판정을 payload가 지어내지 않는다.
    assert payload.comparisons == ()  # type: ignore[attr-defined]
    assert payload.event_histories == ()  # type: ignore[attr-defined]
    assert payload.limitation is None  # type: ignore[attr-defined]


def test_temporal_delta_names_and_values_come_from_the_deterministic_tables() -> None:
    """증감액과 증감률의 이름·값·단위를 payload가 새로 만들지 않는다."""
    result = temporal_delta_result()

    payload = grounded(result)

    amount, rate = payload.calculations  # type: ignore[attr-defined]

    assert (amount.metric_label, rate.metric_label) == ("증감액", "증감률")

    # 증감액은 원천 Fact의 정규화 단위를, 증감률은 계산 계층의 결과 단위를 쓴다. 두 어휘를
    # 한 표기로 뭉개면 "3.1789원"이나 "17767427980.0000%"가 나온다.
    assert amount.display_value == "17,767,427,980원"
    assert rate.display_value == "3.1789%"

    sentence = build_answer_response(result).answer

    assert amount.display_value in sentence
    assert rate.display_value in sentence


def test_temporal_delta_preserves_input_order_and_leaves_the_year_empty() -> None:
    """
    참조 순서는 이전, 이후다. 뒤집히면 부호가 뒤집힌 증감이 되고 뒤집힌 값도 그럴듯하다.

    회계연도를 싣지 않는 이유는 증감이 두 해 사이의 값이기 때문이다. 한쪽 해를 골라 적으면
    그 값이 그 해의 값으로 읽힌다. 두 해는 원천 Fact가 갖고 있고 input_ids가 그것을 가리킨다.
    """
    payload = grounded(temporal_delta_result())

    ordered = ("fact_rev_2024", "fact_rev_2025")

    for calculation in payload.calculations:  # type: ignore[attr-defined]
        assert calculation.input_ids == ordered
        assert calculation.fiscal_year is None
        assert calculation.company == "한미반도체"
        assert calculation.scope_label == "연결"

    assert [item.fiscal_year for item in payload.facts] == [2024, 2025]  # type: ignore[attr-defined]

    body = payload.as_dict()  # type: ignore[attr-defined]

    assert all("fiscal_year" not in item for item in body["calculations"])


def test_temporal_delta_values_are_carried_not_recomputed() -> None:
    """계산 계층이 확정한 값을 그대로 옮긴다. 여기서 빼거나 나누지 않는다."""
    # 두 Fact의 차이(17,767,427,980)와 다른 값을 일부러 싣는다. payload가 검산해 고쳐 쓰면
    # 이 값이 살아남지 않는다.
    result = temporal_delta_result(delta_value="999.0000", delta_pct_value="0.0001")

    payload = grounded(result)

    amount, rate = payload.calculations  # type: ignore[attr-defined]

    assert amount.display_value == "999원"
    assert rate.display_value == "0.0001%"


def test_temporal_derived_delta_carries_every_fact_calculation_and_evidence() -> None:
    """Q08은 원천 Fact 4건과 계산 3건을 함께 싣는다. 비율만 보내면 근거가 끊긴다."""
    payload = grounded(temporal_derived_delta_result())

    assert [item.fact_id for item in payload.facts] == [  # type: ignore[attr-defined]
        "fact_oi_2024",
        "fact_rev_2024",
        "fact_oi_2025",
        "fact_rev_2025",
    ]

    assert [
        item.calculation_id
        for item in payload.calculations  # type: ignore[attr-defined]
    ] == ["calc_margin_2024", "calc_margin_2025", "calc_margin_diff"]

    assert [item.evidence_id for item in payload.evidence] == [  # type: ignore[attr-defined]
        "ev_oi_2024",
        "ev_rev_2024",
        "ev_oi_2025",
        "ev_rev_2025",
    ]

    assert payload.comparisons == ()  # type: ignore[attr-defined]
    assert payload.event_histories == ()  # type: ignore[attr-defined]


def test_temporal_derived_delta_names_and_values_come_from_the_deterministic_tables() -> None:
    """비율은 파생지표 대응표가, 차이는 기간 변화 대응표가 이름을 정한다."""
    result = temporal_derived_delta_result()

    payload = grounded(result)

    previous, current, change = payload.calculations  # type: ignore[attr-defined]

    assert (previous.metric_label, current.metric_label) == ("영업이익률", "영업이익률")
    assert change.metric_label == "차이"

    # 백분율과 percentage point는 축이 다르다. 차이에 %를 붙이면 비율 자체가 2.1017% 달라졌다는
    # 뜻이 되고, 그 문장은 형식이 정상이라 축이 바뀐 사실이 어디에도 남지 않는다.
    assert (previous.display_value, current.display_value) == ("45.6940%", "43.5923%")
    assert change.display_value == "-2.1017%p"

    sentence = build_answer_response(result).answer

    for calculation in payload.calculations:  # type: ignore[attr-defined]
        assert calculation.display_value in sentence


def test_temporal_derived_delta_preserves_the_reference_chain() -> None:
    """
    차이는 원천 Fact가 아니라 두 비율을 가리킨다. 그 배선이 무엇의 변화인지를 정한다.

    비율은 각자 한 해의 값이므로 회계연도를 싣고, 차이는 두 해 사이의 값이므로 비운다.
    """
    payload = grounded(temporal_derived_delta_result())

    previous, current, change = payload.calculations  # type: ignore[attr-defined]

    assert previous.input_ids == ("fact_oi_2024", "fact_rev_2024")
    assert current.input_ids == ("fact_oi_2025", "fact_rev_2025")
    assert change.input_ids == ("calc_margin_2024", "calc_margin_2025")

    assert (previous.fiscal_year, current.fiscal_year) == (2024, 2025)
    assert change.fiscal_year is None

    for calculation in payload.calculations:  # type: ignore[attr-defined]
        assert calculation.company == "한미반도체"
        assert calculation.scope_label == "연결"


def test_temporal_derived_delta_values_are_carried_not_recomputed() -> None:
    """비율도 차이도 계산 계층이 확정한 값이다. payload는 나누지도 빼지도 않는다."""
    # 어느 값도 입력에서 다시 만들어지지 않는다. 비율은 영업이익/매출이 아니고, 차이는 두
    # 비율의 뺄셈이 아니다. 검산해 고쳐 쓰는 경로가 있으면 이 값들이 살아남지 않는다.
    result = temporal_derived_delta_result(
        margin_2024_value="11.1111",
        margin_2025_value="22.2222",
        change_value="99.9999",
    )

    payload = grounded(result)

    previous, current, change = payload.calculations  # type: ignore[attr-defined]

    assert (previous.display_value, current.display_value) == ("11.1111%", "22.2222%")
    assert change.display_value == "99.9999%p"


@pytest.mark.parametrize(
    "build",
    [temporal_delta_result, temporal_derived_delta_result],
    ids=["q07", "q08"],
)
def test_temporal_shapes_do_not_mutate_the_execution_result(
    build: Callable[[], AgentExecutionResult],
) -> None:
    """
    payload를 만들어도 결정론 결과는 그대로다.

    같은 결과가 결정론 문장과 payload 양쪽에 쓰이므로, 여기서 한 글자라도 고치면 연동을 켰다는
    이유만으로 구조화 응답이 달라진다. 그 차이는 응답만 보면 어느 쪽이 원본인지 알 수 없다.
    """
    result = build()

    before = result.model_dump(mode="json")

    grounded(result)

    assert result.model_dump(mode="json") == before


# --------------------------------------------------------------------------
# 지원하지 않는 모양 — fail closed
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [AnswerabilityStatus.UNANSWERABLE, AnswerabilityStatus.UNRESOLVED],
)
def test_refusal_and_unresolved_statuses_are_not_grounded(status: AnswerabilityStatus) -> None:
    """거부·미해결 문장은 사유별로 이미 확정돼 있다. 표현을 바꿀 여지를 두지 않는다."""
    with pytest.raises(PayloadNotGroundableError):
        grounded(completed(status=status, facts=[fact()], evidence=[evidence()]))


def test_empty_result_is_not_grounded() -> None:
    with pytest.raises(PayloadNotGroundableError, match="옮길 근거가 없습니다"):
        grounded(completed(evidence=[evidence()]))


def test_document_summary_result_is_not_grounded() -> None:
    """Pattern B는 이번 범위 밖이다. 요약을 뺀 나머지만 보내지 않는다."""
    summary = DocumentSummary(
        summary_id="sum_1",
        company="한미반도체",
        doc_id="doc_a",
        report_name="사업보고서",
        section_scope=SectionScope.FACILITY_INVESTMENT,
        section_path=["II. 사업의 내용"],
        items=[summary_item()],
    )

    with pytest.raises(PayloadNotGroundableError, match="DocumentSummary"):
        grounded(completed(document_summaries=[summary], evidence=[evidence()]))


def test_document_summary_beside_facts_blocks_the_whole_payload() -> None:
    """일부 carrier만 골라 보내면 빠진 쪽이 답변 어디에도 남지 않는다."""
    summary = DocumentSummary(
        summary_id="sum_1",
        company="한미반도체",
        doc_id="doc_a",
        report_name="사업보고서",
        section_scope=SectionScope.FACILITY_INVESTMENT,
        section_path=["II. 사업의 내용"],
        items=[summary_item()],
    )

    with pytest.raises(PayloadNotGroundableError, match="DocumentSummary"):
        grounded(single_fact_result(document_summaries=[summary]))


def test_facts_and_event_histories_together_are_not_grounded() -> None:
    """결정론 경로가 거부하는 조합을 연동 쪽만 통과시키지 않는다."""
    combined = history_result()

    combined.facts.append(fact())

    with pytest.raises(PayloadNotGroundableError, match="EventHistory"):
        grounded(combined)


def test_multiple_facts_without_a_comparison_are_not_grounded() -> None:
    """비교 없는 다중 Fact는 결정론 경로도 문장으로 옮기지 않는다."""
    result = completed(
        facts=margin_facts(),
        calculations=[margin_calculation()],
        evidence=[evidence()],
    )

    with pytest.raises(PayloadNotGroundableError, match="우열 판정 없이"):
        grounded(result)


def test_delta_with_mismatched_input_order_is_not_grounded() -> None:
    """
    증감액과 증감률이 같은 Fact를 반대 순서로 가리키면 두 수치의 부호가 서로 어긋난다.

    결정론 경로가 그 모양을 알아보지 못하고 멈추므로(_is_temporal_delta_shape), payload도
    멈춘다. 개수만 맞는 결과가 연동 쪽에서만 답해지지 않는다.
    """
    reversed_rate = temporal_delta_result(
        rate_inputs=fact_inputs("fact_rev_2025", "fact_rev_2024")
    )

    with pytest.raises(PayloadNotGroundableError, match="우열 판정 없이"):
        grounded(reversed_rate)


def test_three_calculations_outside_the_derived_delta_shape_are_not_grounded() -> None:
    """계산 3건을 실은 임의의 결과가 Q08 경로로 열리지 않는다."""
    result = temporal_derived_delta_result()

    # 연산 이름의 순서를 어긋내면 _is_temporal_derived_delta_shape이 알아보지 못한다.
    result.calculations[0].operation = OPERATION_DIFF_PP

    with pytest.raises(PayloadNotGroundableError, match="우열 판정 없이"):
        grounded(result)


def test_change_calculation_outside_the_derived_delta_shape_is_not_grounded() -> None:
    """
    Calculation을 입력으로 갖는 값은 그 모양이 확인된 자리에서만 열린다.

    Q08 모양을 벗어나면 개념 이름을 정할 대응표가 없다. 여기서 이름을 붙이면 파생 개념 어휘가
    결정론 대응표 밖에 한 벌 더 생긴다.
    """
    result = temporal_derived_delta_result()

    # 우열 판정을 붙여 Q08 인식 경로를 지나가게 한다. 그러면 차이는 일반 경로로 내려간다.
    result.comparisons.append(
        ComparisonResult(
            comparison_id="cmp_margin",
            left=ResultInputReference(
                input_type=ResultInputType.CALCULATION, input_id="calc_margin_2024"
            ),
            right=ResultInputReference(
                input_type=ResultInputType.CALCULATION, input_id="calc_margin_2025"
            ),
            relation=ComparisonRelation.LEFT_GREATER,
        )
    )

    with pytest.raises(PayloadNotGroundableError, match="calc_margin_diff"):
        grounded(result)


# --------------------------------------------------------------------------
# 최소 전송
# --------------------------------------------------------------------------


def test_raw_evidence_text_and_provenance_never_leave_the_process() -> None:
    """원문을 주면 모델이 거기서 새 사실을 뽑을 표면이 생긴다."""
    body = grounded(single_fact_result()).to_json()  # type: ignore[attr-defined]

    assert "연결 손익계산서" not in body
    assert "제55기" not in body
    assert "T-1" not in body
    assert "acode" not in body

    assert "ev_revenue" in body
    assert "doc_a" in body


def test_internal_diagnostics_are_not_transmitted() -> None:
    """진단용 내부 서술은 사용자 문장의 재료가 아니다."""
    result = single_fact_result(
        answerability_detail=AnswerabilityDetail(
            stage="extraction", reason="internal_probe", detail="INTERNAL-DETAIL-STRING"
        ),
        warnings=["INTERNAL-WARNING-STRING"],
        events=[
            Event(
                event_id="evt_hidden",
                event_type=EventType.SUPPLY_CONTRACT,
                company="한미반도체",
                source_doc_id="doc_a",
                attributes={"secret": "INTERNAL-EVENT-STRING"},
            )
        ],
    )

    body = grounded(result).to_json()  # type: ignore[attr-defined]

    assert "INTERNAL-DETAIL-STRING" not in body
    assert "INTERNAL-WARNING-STRING" not in body
    assert "INTERNAL-EVENT-STRING" not in body
    assert "internal_probe" not in body


def test_transmitted_keys_are_a_fixed_allowlist() -> None:
    """
    새 carrier가 Contract에 붙었을 때 조용히 함께 나가지 않게 고정한다.

    payload에 자리를 만드는 것은 결정이고, 그 결정 없이 필드가 늘면 최소 전송 정책이 문서에만
    남는다.
    """
    body = grounded(comparison_result()).as_dict()  # type: ignore[attr-defined]

    assert set(body) == {
        "question",
        "answerability_status",
        "facts",
        "comparisons",
        "evidence",
    }

    assert set(body["evidence"][0]) == {"evidence_id", "doc_id"}  # type: ignore[index]


def test_status_is_copied_and_not_rejudged() -> None:
    payload = grounded(
        history_result(
            chain_complete=False,
            status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
            reason=AnswerabilityReason.INCOMPLETE_CHAIN,
        )
    )

    assert payload.answerability_status == AnswerabilityStatus.PARTIALLY_ANSWERABLE.value  # type: ignore[attr-defined]
