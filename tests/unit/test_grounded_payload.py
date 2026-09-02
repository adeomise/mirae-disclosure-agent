"""근거 payload 조립 테스트.

여기서 고정하는 것:
    - 결정론 경로가 답하지 못하는 결과 모양은 payload로도 나가지 않는다.
    - 일부 carrier만 골라 보내지 않는다. 표현할 수 없는 것이 섞이면 통째로 멈춘다.
    - 원문(raw_text)과 내부 진단(answerability_detail 등)은 payload 밖에 남는다.
    - 수치와 표기는 answer_builder 대응표가 만든 것을 그대로 옮긴다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.answer_builder import build_answer_response
from disclosure_agent.agent.derived_metrics import OPERATION_RATIO, UNIT_PERCENT
from disclosure_agent.agent.grounded_payload import (
    PayloadNotGroundableError,
    build_grounded_payload,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ComparisonRelation,
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
    Evidence,
    EvidenceProvenance,
    Fact,
    FieldChange,
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
