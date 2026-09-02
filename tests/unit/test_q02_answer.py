"""Q02 계약 조회 결과의 결정론적 답변 문장 테스트.

검증 대상:
- 확보한 계약 값들이 하나의 근거 있는 문장으로 나가는가
- 회계연도·연결기준처럼 없는 축을 문장이 지어내지 않는가
- 어느 계약인지 특정되지 않는 결과를 문장으로 만들지 않는가
- Fact/Evidence와 답변 가능성 판정이 그대로 실려 나가는가
- 답할 수 없다고 판정된 경우 기존 일반 문장을 그대로 쓰는가

문장만 본다. 문서 선택은 agent.event_retrieval이, 값 변환은 event.adapter가, 실행 조립은
agent.handlers.contract_fact가 이미 각자 고정하고 있다.

값은 합성이다. 실제 공시 수치를 저장소에 적지 않는다(AGENTS.md).
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.answer_builder import (
    UnsupportedResultShapeError,
    build_answer_response,
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
    AnswerabilityDetail,
    Evidence,
    Fact,
)

COMPANY = "한화오션(주)"

DOC_ID = "exchange_SYNTHETIC0001"

AMOUNT_TEXT = "1,111,100,000,000"

START_DATE = "2025-09-16"

END_DATE = "2029-05-31"


def _fact(
    metric: MetricConcept,
    raw_value: str,
    *,
    raw_unit: str | None = None,
    normalized_unit: str | None = None,
) -> Fact:
    """event.adapter.adapt_contract_facts가 만드는 모양의 Fact."""
    return Fact(
        fact_id=f"fact_{DOC_ID}_{metric.value}",
        company=COMPANY,
        metric=metric,
        # 계약금액과 계약기간은 회계기간의 값이 아니고, 거래소공시에는 연결·별도 구분이 없다.
        fiscal_year=None,
        fiscal_period=None,
        accounting_scope=FactAccountingScope.NOT_APPLICABLE,
        raw_value=raw_value,
        raw_unit=raw_unit,
        normalized_value=raw_value.replace(",", "") if raw_unit else raw_value,
        normalized_unit=normalized_unit,
        evidence_ids=[f"evidence_{DOC_ID}_{metric.value}"],
    )


def _evidence(metric: MetricConcept) -> Evidence:
    return Evidence(
        evidence_id=f"evidence_{DOC_ID}_{metric.value}",
        doc_id=DOC_ID,
        location_type=EvidenceLocationType.EVENT_FIELD,
        field_name=metric.value,
    )


AMOUNT = _fact(
    MetricConcept.CONTRACT_AMOUNT,
    AMOUNT_TEXT,
    raw_unit="원",
    normalized_unit="KRW",
)

START = _fact(MetricConcept.CONTRACT_START_DATE, START_DATE)

END = _fact(MetricConcept.CONTRACT_END_DATE, END_DATE)

BUNDLE = [AMOUNT, START, END]


def _answerable(facts: list[Fact]) -> AgentExecutionResult:
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=facts,
        evidence=[_evidence(fact.metric) for fact in facts],
    )


# --------------------------------------------------------------------------
# 1. 확보한 값이 문장이 된다
# --------------------------------------------------------------------------


def test_contract_bundle_becomes_one_grounded_sentence() -> None:
    """세 값을 물은 질문에 세 값이 모두 담긴 문장 하나로 답한다."""
    answer = build_answer_response(_answerable(BUNDLE)).answer

    assert COMPANY in answer
    assert f"{AMOUNT_TEXT}원" in answer
    assert START_DATE in answer
    assert END_DATE in answer

    # 계약기간은 두 날짜의 관계가 드러나야 한다. 따로 적으면 같은 계약의 양 끝이라는 사실이
    # 문장에서 사라진다.
    assert f"{START_DATE}부터 {END_DATE}까지" in answer


def test_sentence_does_not_invent_an_accounting_axis() -> None:
    """
    계약금액은 회계연도의 값이 아니고 거래소공시에는 연결·별도 구분이 없다.

    "2025년 연결 기준 계약금액"으로 적으면 형식은 자연스럽고 사실과 다른 문장이 나간다.
    """
    answer = build_answer_response(_answerable(BUNDLE)).answer

    assert "연결" not in answer
    assert "별도" not in answer
    assert "기준" not in answer
    assert "년 계약금액" not in answer


def test_sentence_carries_no_internal_vocabulary() -> None:
    """추출 계층의 어휘(contract_amount, KRW)가 사용자 문장에 실리지 않는다."""
    answer = build_answer_response(_answerable(BUNDLE)).answer

    for token in ("contract_amount", "contract_start_date", "contract_end_date", "KRW"):
        assert token not in answer


def test_period_only_result_renders() -> None:
    """계약기간만 물은 질문에도 답한다. 묻지 않은 금액은 문장에 나타나지 않는다."""
    answer = build_answer_response(_answerable([START, END])).answer

    assert f"{START_DATE}부터 {END_DATE}까지" in answer
    assert AMOUNT_TEXT not in answer


# --------------------------------------------------------------------------
# 2. 근거와 판정을 그대로 싣는다
# --------------------------------------------------------------------------


def test_facts_and_evidence_survive_the_answer_boundary() -> None:
    """
    문장만 나가고 근거가 사라지면 사용자가 원문과 대조할 수 없다.

    각 Fact의 참조가 응답 안에서 실제로 풀리는지 본다. 개수만 세면 연결이 끊긴 채로도 통과한다.
    """
    response = build_answer_response(_answerable(BUNDLE))

    assert response.status is AnswerabilityStatus.ANSWERABLE
    assert response.reason is None

    assert [fact.metric for fact in response.facts] == [
        MetricConcept.CONTRACT_AMOUNT,
        MetricConcept.CONTRACT_START_DATE,
        MetricConcept.CONTRACT_END_DATE,
    ]

    known = {item.evidence_id for item in response.evidence}

    for fact in response.facts:
        assert fact.evidence_ids
        assert set(fact.evidence_ids) <= known


# --------------------------------------------------------------------------
# 3. 특정되지 않는 결과는 문장으로 만들지 않는다
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "facts",
    [
        pytest.param([AMOUNT], id="amount_only"),
        pytest.param([AMOUNT, START], id="start_without_end"),
        pytest.param([AMOUNT, END], id="end_without_start"),
    ],
)
def test_result_without_a_contract_period_is_refused(facts: list[Fact]) -> None:
    """
    한 기업은 한 해에 여러 건의 공급계약을 공시한다.

    "{회사}의 계약금액은 X원입니다"는 형식은 자연스럽지만 어느 계약인지 특정되지 않은
    문장이고, 읽는 쪽은 그 기업의 유일한 계약으로 읽는다. 계약기간이 그 계약을 구분해 준다.
    """
    with pytest.raises(UnsupportedResultShapeError) as error:
        build_answer_response(_answerable(facts))

    assert "계약기간" in str(error.value)


def test_duplicate_contract_field_is_refused() -> None:
    """같은 필드가 두 건이면 어느 값이 정본인지 정해지지 않았다. 하나를 골라 적지 않는다."""
    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(_answerable([AMOUNT, AMOUNT, START, END]))


def test_two_companies_in_one_contract_sentence_are_refused() -> None:
    """한 계약을 서술하는 문장이므로 기업은 한 곳이어야 한다."""
    other = START.model_copy(update={"company": "다른조선(주)"})

    with pytest.raises(UnsupportedResultShapeError):
        build_answer_response(_answerable([AMOUNT, other, END]))


# --------------------------------------------------------------------------
# 4. 답할 수 없는 경우는 기존 일반 문장을 쓴다
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (AnswerabilityStatus.UNANSWERABLE, AnswerabilityReason.NOT_FOUND),
        (AnswerabilityStatus.UNANSWERABLE, AnswerabilityReason.INSUFFICIENT_EVIDENCE),
        (AnswerabilityStatus.UNRESOLVED, AnswerabilityReason.AMBIGUOUS_MATCH),
    ],
)
def test_unanswered_results_reuse_the_generic_sentences(
    status: AnswerabilityStatus,
    reason: AnswerabilityReason,
) -> None:
    """
    Q02 전용 문장을 새로 만들지 않는다.

    사유별 문장은 이미 모든 Pattern이 공유하고 있고, 여기에 계약 전용 표현을 더하면 같은
    사유에 두 벌의 문장이 생긴다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=status,
        answerability_reason=reason,
        answerability_detail=AnswerabilityDetail(
            stage="filing_discovery",
            reason="no_matching_counterparty",
            detail="후보 중 상대방이 일치하는 공시가 없습니다.",
        ),
    )

    response = build_answer_response(result)

    assert response.status is status
    assert response.reason is reason
    assert response.answer
    assert response.facts == []

    # 내부 진단 어휘가 사용자 문장에 새어 나가지 않는다.
    assert "no_matching_counterparty" not in response.answer


# --------------------------------------------------------------------------
# 5. handler 결과에서 문장까지 이어진다
# --------------------------------------------------------------------------


def test_handler_result_reaches_a_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    실행 결과와 답변 문장이 같은 값을 말하는지 끝까지 확인한다.

    두 계층을 각각만 시험하면 사이에서 어긋나는 것을 놓친다.
    """
    from pathlib import Path

    from disclosure_agent.agent.event_retrieval import (
        ContractCandidate,
        ContractSelection,
        ContractSelectionStatus,
    )
    from disclosure_agent.agent.handlers import contract_fact
    from disclosure_agent.agent.handlers.contract_fact import handle_contract_fact
    from disclosure_agent.agent.query_analyzer import build_query_plan
    from disclosure_agent.retrieval.metadata_filter import FilingRef

    def _field(raw_value: str, normalized: object, unit: str | None = None) -> dict:
        return {
            "raw_group": "계약내역",
            "raw_field": "항목",
            "raw_value": raw_value,
            "raw_unit": "원" if unit else None,
            "normalized_value": normalized,
            "normalized_unit": unit,
            "provenance": {"table_id": "T0", "row_index": 2, "value_cell_index": 1},
        }

    filing = FilingRef(
        doc_id=DOC_ID,
        corp_name=COMPANY,
        corp_code="00000000",
        stock_code=None,
        report_name="단일판매ㆍ공급계약체결",
        receipt_no="SYNTHETIC0001",
        receipt_date="20250916",
        document_class="event",
        doc_group="exchange",
        document_subtype="단일판매공급계약체결",
        base_year=None,
        base_month=None,
        is_correction=False,
        directory=Path("corpus"),
        main_document=Path("corpus/main.xml"),
    )

    event = {
        "event_id": f"event_{DOC_ID}",
        "event_type": "supply_contract",
        "doc_id": DOC_ID,
        "fields": {
            "counterparty": _field("Yang Ming", "Yang Ming"),
            "contract_amount": _field(AMOUNT_TEXT, 1111100000000, "KRW"),
            "contract_start_date": _field(START_DATE, START_DATE),
            "contract_end_date": _field(END_DATE, END_DATE),
        },
    }

    monkeypatch.setattr(
        contract_fact,
        "select_contract_filing",
        lambda **kwargs: ContractSelection(
            status=ContractSelectionStatus.SELECTED,
            selected=ContractCandidate(filing=filing, event=event),
            candidate_doc_ids=(DOC_ID,),
            matched_doc_ids=(DOC_ID,),
        ),
    )

    plan = build_query_plan(
        "한화오션이 2025년 9월 Yang Ming과 체결한 계약의 계약금액과 계약기간은?",
        ("한화오션",),
    )

    result = handle_contract_fact(plan, corpus_root=Path("corpus"))

    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE

    response = build_answer_response(result)

    assert f"{AMOUNT_TEXT}원" in response.answer
    assert f"{START_DATE}부터 {END_DATE}까지" in response.answer
    assert len(response.facts) == 3
    assert len(response.evidence) == 3
