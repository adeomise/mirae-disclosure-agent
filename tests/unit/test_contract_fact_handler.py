"""Q02 계약 단건 사실 조회 handler 테스트.

검증 대상:
- 확정한 공시 1건에서 요구한 값이 전부 Fact가 되고 각 Fact가 자기 Evidence를 가리키는가
- 후보를 확정하지 못했을 때 하나를 고르지 않는가(0건·N건)
- 문서는 확정했는데 요구한 값이 없을 때 부분 답을 만들지 않는가
- 후보를 읽지 못한 실패를 조용히 넘기지 않는가
- Router가 기존 Pattern을 이 경로로 보내지 않는가

manifest/corpus를 읽지 않는다. 문서 선택은 agent.event_retrieval이, 값 변환은
event.adapter가 이미 각자 고정하고 있다. 여기서 보는 것은 그 둘을 잇는 순서와 판정이다.

값은 합성이다. 실제 공시 수치를 저장소에 적지 않는다(AGENTS.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.agent import router
from disclosure_agent.agent.event_retrieval import (
    ContractCandidate,
    ContractCandidateReadError,
    ContractSelection,
    ContractSelectionStatus,
)
from disclosure_agent.agent.handlers import contract_fact
from disclosure_agent.agent.handlers.contract_fact import handle_contract_fact
from disclosure_agent.agent.query_analyzer import (
    EVENT_SUBTYPE_SUPPLY_CONTRACT,
    INTENT_CONTRACT_FACT,
    QueryPlan,
    build_query_plan,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ExecutionStatus,
    MetricConcept,
    QueryAccountingScope,
)
from disclosure_agent.retrieval.metadata_filter import DOCUMENT_CLASS_EVENT, FilingRef

COMPANY = "한화오션"

CORP_NAME = "한화오션(주)"

COUNTERPARTY = "Yang Ming"

CORPUS_ROOT = Path("corpus")

REQUESTED = (
    MetricConcept.CONTRACT_AMOUNT,
    MetricConcept.CONTRACT_START_DATE,
    MetricConcept.CONTRACT_END_DATE,
)


def _plan(**overrides: Any) -> QueryPlan:
    """대표 Q02 질문이 만드는 모양의 계획."""
    values: dict[str, Any] = {
        "question": "한화오션이 2025년 9월 Yang Ming과 체결한 계약의 계약금액과 계약기간은?",
        "companies": (COMPANY,),
        "years": (2025,),
        "intent": INTENT_CONTRACT_FACT,
        "document_class": DOCUMENT_CLASS_EVENT,
        "document_subtype": EVENT_SUBTYPE_SUPPLY_CONTRACT,
        "accounting_scope": QueryAccountingScope.NOT_APPLICABLE.value,
        "metric": None,
        "requested_fields": REQUESTED,
        "counterparty": COUNTERPARTY,
        "month": 9,
    }

    values.update(overrides)

    return QueryPlan(**values)


def _filing(receipt_no: str = "SYNTHETIC0001") -> FilingRef:
    return FilingRef(
        doc_id=f"exchange_{receipt_no}",
        corp_name=CORP_NAME,
        corp_code="00000000",
        stock_code=None,
        report_name="단일판매ㆍ공급계약체결",
        receipt_no=receipt_no,
        receipt_date="20250916",
        document_class="event",
        doc_group="exchange",
        document_subtype=EVENT_SUBTYPE_SUPPLY_CONTRACT,
        base_year=None,
        base_month=None,
        is_correction=False,
        directory=CORPUS_ROOT / receipt_no,
        main_document=CORPUS_ROOT / receipt_no / "main.xml",
    )


def _field(raw_value: str, normalized: Any, unit: str | None = None) -> dict[str, Any]:
    return {
        "raw_group": "계약내역",
        "raw_field": "항목",
        "raw_value": raw_value,
        "raw_unit": "원" if unit else None,
        "normalized_value": normalized,
        "normalized_unit": unit,
        "provenance": {"table_id": "T0", "row_index": 2, "value_cell_index": 1},
    }


def _event(*, drop: str | None = None) -> dict[str, Any]:
    """event.extractor.extract_event가 내놓는 형태의 합성 결과.

    doc_id를 담는 이유: extract_event는 metadata.doc_id가 없으면 애초에 이벤트를 만들지
    않는다. 그 값이 없는 이벤트는 실제 경로에 존재하지 않는다.
    """
    fields = {
        "counterparty": _field(COUNTERPARTY, COUNTERPARTY),
        "contract_amount": _field("1,111,100,000,000", 1111100000000, "KRW"),
        "contract_start_date": _field("2025-09-16", "2025-09-16"),
        "contract_end_date": _field("2029-05-31", "2029-05-31"),
    }

    if drop is not None:
        del fields[drop]

    return {
        "event_id": "event_exchange_SYNTHETIC0001",
        "event_type": "supply_contract",
        "doc_id": "exchange_SYNTHETIC0001",
        "fields": fields,
    }


@pytest.fixture
def selection(monkeypatch: pytest.MonkeyPatch):
    """select_contract_filing의 판정을 대신 넣는다."""

    def install(result: ContractSelection | Exception) -> None:
        def fake(**kwargs: Any) -> ContractSelection:
            install.kwargs.update(kwargs)

            if isinstance(result, Exception):
                raise result

            return result

        monkeypatch.setattr(contract_fact, "select_contract_filing", fake)

    install.kwargs = {}  # type: ignore[attr-defined]

    return install


def _selected(event: dict[str, Any]) -> ContractSelection:
    return ContractSelection(
        status=ContractSelectionStatus.SELECTED,
        selected=ContractCandidate(filing=_filing(), event=event),
        candidate_doc_ids=("exchange_SYNTHETIC0001",),
        matched_doc_ids=("exchange_SYNTHETIC0001",),
    )


def _run(plan: QueryPlan | None = None):
    return handle_contract_fact(plan or _plan(), corpus_root=CORPUS_ROOT)


# --------------------------------------------------------------------------
# 1. 정상 1건
# --------------------------------------------------------------------------


def test_selected_filing_yields_every_requested_fact(selection) -> None:
    """요구한 세 값이 전부 Fact가 되고, 각 Fact가 결과 안의 Evidence를 가리켜야 한다."""
    selection(_selected(_event()))

    result = _run()

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE

    assert [fact.metric for fact in result.facts] == list(REQUESTED)

    known = {item.evidence_id for item in result.evidence}

    for fact in result.facts:
        assert fact.evidence_ids
        assert set(fact.evidence_ids) <= known

    # 회사명은 manifest가 확정한 값이다. 거래소공시 본문은 회사명을 담지 않는다.
    assert {fact.company for fact in result.facts} == {CORP_NAME}


def test_plan_is_translated_into_retrieval_primitives(selection) -> None:
    """계획을 검색 조건으로 옮기는 일은 handler가 한다. retrieval은 QueryPlan을 모른다."""
    selection(_selected(_event()))

    _run()

    assert selection.kwargs["company"] == COMPANY
    assert selection.kwargs["year"] == 2025
    assert selection.kwargs["month"] == 9
    assert selection.kwargs["counterparty"] == COUNTERPARTY


# --------------------------------------------------------------------------
# 2. 0건 / N건은 고르지 않는다
# --------------------------------------------------------------------------


def test_no_filing_in_window_is_not_found(selection) -> None:
    """기간·유형 조건에 맞는 공시가 없다. 다른 문서로 대신하지 않는다."""
    selection(
        ContractSelection(
            status=ContractSelectionStatus.NOT_FOUND,
            detail="해당 기간에 원공시가 없습니다.",
        )
    )

    result = _run()

    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_FOUND
    assert result.facts == []


def test_no_matching_counterparty_is_not_found_with_candidates(selection) -> None:
    """공시는 있었으나 그 상대방의 계약이 없었다. 다른 상대방의 계약으로 대신하지 않는다."""
    selection(
        ContractSelection(
            status=ContractSelectionStatus.NOT_FOUND,
            candidate_doc_ids=("exchange_A", "exchange_B"),
            detail="후보 2건 중 상대방이 일치하는 공시가 없습니다.",
        )
    )

    result = _run()

    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_FOUND
    assert result.answerability_detail is not None
    assert result.answerability_detail.reason == "no_matching_counterparty"
    assert result.facts == []

    # 어느 후보들 사이에서 막혔는지가 결과에 남아야 한다.
    assert "exchange_A, exchange_B" in result.conversion_gaps[0].subject


def test_ambiguous_candidates_are_not_narrowed_by_the_handler(selection) -> None:
    """
    retrieval이 좁히지 못한 것을 handler가 고르면, 막으려고 만든 규칙이 여기서 뚫린다.

    접수번호가 큰 쪽을 고르지 않는다.
    """
    selection(
        ContractSelection(
            status=ContractSelectionStatus.AMBIGUOUS,
            candidate_doc_ids=("exchange_A", "exchange_B"),
            matched_doc_ids=("exchange_A", "exchange_B"),
            detail="상대방이 같은 공시가 2건입니다.",
        )
    )

    result = _run()

    assert result.answerability_status is AnswerabilityStatus.UNRESOLVED
    assert result.answerability_reason is AnswerabilityReason.AMBIGUOUS_MATCH
    assert result.facts == []
    assert "exchange_A, exchange_B" in result.conversion_gaps[0].subject


def test_candidate_read_failure_is_not_swallowed(selection) -> None:
    """읽지 못한 후보를 빼고 세면 후보가 둘인 상황이 하나로 보인다."""
    selection(ContractCandidateReadError("exchange_A", OSError("깨진 파일")))

    result = _run()

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue is not None
    assert result.execution_issue.reason == "parse_failed"
    assert result.facts == []


# --------------------------------------------------------------------------
# 3. 값이 없으면 부분 답을 만들지 않는다
# --------------------------------------------------------------------------


def test_missing_requested_field_blocks_the_whole_answer(selection) -> None:
    """
    세 값을 물은 질문에 두 값만 답하면 사라진 값이 어디에도 남지 않는다.

    문서를 확정했다는 것과 그 문서가 답을 담고 있다는 것은 다르다.
    """
    selection(_selected(_event(drop="contract_end_date")))

    result = _run()

    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.INSUFFICIENT_EVIDENCE
    assert result.answerability_detail is not None
    assert result.answerability_detail.reason == "missing_contract_field"

    # 확보한 두 값도 싣지 않는다. 부분 결과로 읽히면 안 된다.
    assert result.facts == []
    assert result.evidence == []


def test_unsupported_metric_is_blocked(selection) -> None:
    """대응표에 없는 지표를 비슷한 필드로 대신 읽지 않는다."""
    selection(_selected(_event()))

    result = _run(_plan(requested_fields=(MetricConcept.REVENUE,)))

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue is not None
    assert result.execution_issue.reason == "unsupported_contract_metric"


def test_missing_counterparty_does_not_pick_a_filing(selection) -> None:
    """상대방 없이 같은 달의 여러 계약 중 하나를 고르지 않는다."""
    selection(_selected(_event()))

    result = _run(_plan(counterparty=None))

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue is not None
    assert result.execution_issue.reason == "missing_counterparty"

    # 문서 선택 단계에 도달하지 않는다.
    assert selection.kwargs == {}


# --------------------------------------------------------------------------
# 4. Router
# --------------------------------------------------------------------------


def test_router_dispatches_contract_fact_to_this_handler() -> None:
    assert router.resolve_handler(INTENT_CONTRACT_FACT) is handle_contract_fact


def test_router_still_dispatches_the_existing_patterns() -> None:
    """계약 handler를 붙였다고 기존 Pattern의 경로가 달라지면 안 된다."""
    from disclosure_agent.agent.handlers.event_history import handle_event_history
    from disclosure_agent.agent.handlers.single_fact import handle_single_fact

    assert router.resolve_handler("single_fact") is handle_single_fact
    assert router.resolve_handler("event_history") is handle_event_history


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("한화오션의 2025년 매출액은?", "single_fact"),
        ("한화오션의 2025년 9월 공급계약 정정 이력은?", "event_history"),
        (
            "한화오션이 2025년 9월 Yang Ming과 체결한 계약의 계약금액과 계약기간은?",
            INTENT_CONTRACT_FACT,
        ),
    ],
)
def test_question_reaches_the_intended_handler(question: str, expected: str) -> None:
    """질문 해석부터 handler 선택까지가 이어지는지 본다. 실행은 하지 않는다."""
    plan = build_query_plan(question, (COMPANY,))

    assert plan.intent == expected
    assert router.resolve_handler(plan.intent) is not None
