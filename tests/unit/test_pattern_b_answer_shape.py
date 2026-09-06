"""Pattern B 결과의 공개 응답 변환 단위 테스트.

합성 값만 쓴다. 실제 공시 수치와 접수번호는 쓰지 않는다(AGENTS.md).

무엇을 고정하는가:
    - DocumentSummary만 실린 결과가 문장이 되는가. Fact가 있어야만 문장을 만들 수 있던
      이전 규칙이 남아 있으면 이 경로가 API에서 500이 된다.
    - 어느 문서의 어느 범위였는지가 문장에 남는가.
    - 표기가 정해지지 않은 값을 영문 그대로 문장에 넣지 않는가.
    - 여러 문서를 실은 결과에서 첫 건만 골라 답하지 않는가.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.answer_builder import (
    SECTION_SCOPE_LABELS,
    SUMMARY_ITEM_TYPE_LABELS,
    MissingAnswerSupportError,
    UnsupportedResultShapeError,
    build_answer_response,
)
from disclosure_agent.common.enums import (
    AnswerabilityStatus,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
    SectionScope,
    SummaryItemType,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    DocumentSummary,
    Evidence,
    Fact,
    SummaryItem,
)

DOC_ID = "periodic_SYNTHETIC0001"

SECTION_PATH = ["II. 사업의 내용", "3. 원재료 및 생산설비"]


def evidence_for(item_id: str, *, location_type: EvidenceLocationType) -> Evidence:
    return Evidence(
        evidence_id=f"evidence_{item_id}",
        doc_id=DOC_ID,
        location_type=location_type,
        section_path=list(SECTION_PATH),
        raw_text="합성 발췌",
    )


def summary(
    *,
    labels: tuple[str, ...] = ("가. 합성 항목", "나. 합성 항목"),
    item_type: SummaryItemType = SummaryItemType.PARAGRAPH,
    section_scope: SectionScope = SectionScope.FACILITY_INVESTMENT,
    doc_id: str = DOC_ID,
) -> DocumentSummary:
    return DocumentSummary(
        summary_id=f"summary_{doc_id}_{section_scope.value}",
        doc_id=doc_id,
        company="테스트반도체(주)",
        report_name="사업보고서",
        section_scope=section_scope,
        section_path=list(SECTION_PATH),
        items=[
            SummaryItem(
                item_id=f"summary_item_{doc_id}_{index}",
                item_type=item_type,
                label=label,
                evidence_ids=[f"evidence_summary_item_{doc_id}_{index}"],
            )
            for index, label in enumerate(labels)
        ],
    )


def completed(summaries: list[DocumentSummary], **overrides) -> AgentExecutionResult:
    evidence = [
        evidence_for(
            item.item_id,
            location_type=(
                EvidenceLocationType.TEXT
                if item.item_type is SummaryItemType.PARAGRAPH
                else EvidenceLocationType.TABLE
            ),
        )
        for item in (single for entry in summaries for single in entry.items)
    ]

    payload = {
        "execution_status": ExecutionStatus.COMPLETED,
        "answerability_status": AnswerabilityStatus.ANSWERABLE,
        "document_summaries": summaries,
        "evidence": evidence,
    }

    return AgentExecutionResult(**{**payload, **overrides})


# --------------------------------------------------------------------------
# 1. 문장이 만들어진다
# --------------------------------------------------------------------------


def test_document_summary_alone_produces_an_answer() -> None:
    """
    Fact가 없어도 문장이 나온다.

    Pattern B의 최소 Fact 요건은 0이다. Fact를 요구하는 규칙이 남아 있으면 이 경로가
    MissingAnswerSupportError로 멈추고, API 경계에서 500이 된다.
    """
    response = build_answer_response(completed([summary()]))

    assert response.status is AnswerabilityStatus.ANSWERABLE
    assert response.reason is None
    assert response.answer


def test_answer_states_the_document_scope_and_item_counts() -> None:
    response = build_answer_response(
        completed([summary(labels=("가. 합성 항목", "나. 합성 항목", "다. 합성 항목"))])
    )

    assert "사업보고서" in response.answer
    assert "3. 원재료 및 생산설비" in response.answer
    assert SECTION_SCOPE_LABELS[SectionScope.FACILITY_INVESTMENT] in response.answer
    assert "3건" in response.answer
    assert SUMMARY_ITEM_TYPE_LABELS[SummaryItemType.PARAGRAPH] in response.answer


def test_answer_does_not_leak_contract_enum_values() -> None:
    """한국어 문장에 영문 enum 값이 섞이지 않는다."""
    response = build_answer_response(completed([summary()]))

    assert SectionScope.FACILITY_INVESTMENT.value not in response.answer
    assert SummaryItemType.PARAGRAPH.value not in response.answer


def test_answer_lists_every_item_label_in_source_order() -> None:
    """
    항목 이름을 원문 순서 그대로 전부 나열한다.

    앞의 몇 건만 적으면 그 몇 건이 나머지보다 중요하다는 뜻이 된다. 항목 순서는 원문 배치일
    뿐 중요도가 아니므로 결과가 그 주장을 보장하지 않는다. 그래서 고르는 대신 전부 적는다.
    전부 적으면 무엇이 더 중요하다는 주장을 하지 않으면서도 범위 안에 무엇이 있는지가 답변에
    남는다.

    건수만 적던 이전 문장은 "무슨 사업을 하느냐"에 "본문 6건입니다"로 답했다. 형식은 정상이고
    근거도 맞지만 질문에 답하지 않는다.
    """
    labels = tuple(f"항목{index}" for index in range(8))

    result = completed([summary(labels=labels)])

    response = build_answer_response(result)

    assert [label for label in labels if label in response.answer] == list(labels)

    positions = [response.answer.index(label) for label in labels]

    assert positions == sorted(positions)

    # 결과에도 그대로 남는다.
    assert [item.label for item in result.document_summaries[0].items] == list(labels)


def test_answer_does_not_claim_the_whole_range_is_the_requested_scope() -> None:
    """
    범위 선택과 그 범위의 내용을 한 문장으로 붙이지 않는다.

    "설비 투자 관련 내용 15건"이라고 적으면 15건 전부가 설비 투자 내용이라는 뜻이 된다.
    확보 단위는 Section이고 그 Section에는 요청 범위 밖의 내용도 함께 들어 있다.
    """
    scope_label = SECTION_SCOPE_LABELS[SectionScope.FACILITY_INVESTMENT]

    response = build_answer_response(completed([summary()]))

    assert f"{scope_label} 관련 내용" not in response.answer


def test_summary_evidence_is_carried_into_the_public_response() -> None:
    result = completed([summary()])

    response = build_answer_response(result)

    assert response.facts == []
    assert len(response.evidence) == len(result.evidence)


# --------------------------------------------------------------------------
# 2. 정해지지 않은 모양은 고르지 않고 멈춘다
# --------------------------------------------------------------------------


def test_every_section_scope_has_a_korean_label() -> None:
    """
    표기가 없는 범위를 만들면 문장에 영문 값이 그대로 섞인다.

    SectionScope를 넓히는 쪽이 표기를 함께 정하게 한다.
    """
    assert set(SECTION_SCOPE_LABELS) == set(SectionScope)


def test_every_summary_item_type_has_a_korean_label() -> None:
    assert set(SUMMARY_ITEM_TYPE_LABELS) == set(SummaryItemType)


def test_two_document_summaries_fail_fast() -> None:
    """
    문서 둘을 한 문장으로 잇는 형태는 정해지지 않았다.

    첫 건만 고르면 나머지 문서를 요약했다는 사실이 사라진다.
    """
    with pytest.raises(UnsupportedResultShapeError, match="DocumentSummary가 2건"):
        build_answer_response(
            completed([summary(), summary(doc_id="periodic_SYNTHETIC0002")])
        )


def test_summary_mixed_with_a_fact_fails_fast() -> None:
    """
    Pattern B는 Fact를 만들지 않는다. 함께 실린 결과를 만드는 producer가 없다.

    한쪽을 골라 문장을 만들면 나머지가 조용히 사라진다.
    """
    fact = Fact(
        fact_id="fact_synthetic",
        company="테스트반도체(주)",
        metric=MetricConcept.REVENUE,
        fiscal_year=2025,
        accounting_scope=FactAccountingScope.CONSOLIDATED,
        normalized_value="1",
        normalized_unit="KRW",
        evidence_ids=[],
    )

    with pytest.raises(UnsupportedResultShapeError, match="DocumentSummary"):
        build_answer_response(completed([summary()], facts=[fact]))


def test_answerable_without_any_support_still_fails_fast() -> None:
    """근거가 하나도 없는 ANSWERABLE에는 문장을 만들지 않는다."""
    with pytest.raises(MissingAnswerSupportError):
        build_answer_response(
            AgentExecutionResult(
                execution_status=ExecutionStatus.COMPLETED,
                answerability_status=AnswerabilityStatus.ANSWERABLE,
            )
        )


# --------------------------------------------------------------------------
# 3. 같은 문서의 범위 여러 건(Q05)
# --------------------------------------------------------------------------


def test_two_scopes_of_the_same_document_produce_one_answer() -> None:
    """
    같은 문서의 서로 다른 범위는 범위마다의 문장을 결과 순서대로 잇는다.

    문장을 새로 쓰지 않는다. 각 범위의 문장은 범위 하나일 때와 같은 형태다.
    """
    overview = summary(
        labels=("가. 합성 항목", "나. 합성 항목"),
        section_scope=SectionScope.BUSINESS_OVERVIEW,
    )
    products = summary(
        labels=("다. 합성 항목",),
        item_type=SummaryItemType.TABLE,
        section_scope=SectionScope.PRODUCTS,
    )

    result = completed([overview, products])
    response = build_answer_response(result)

    assert response.status is AnswerabilityStatus.ANSWERABLE
    assert response.reason is None
    assert response.facts == []
    assert len(response.evidence) == len(result.evidence) == 3

    overview_label = SECTION_SCOPE_LABELS[SectionScope.BUSINESS_OVERVIEW]
    products_label = SECTION_SCOPE_LABELS[SectionScope.PRODUCTS]

    assert overview_label in response.answer
    assert products_label in response.answer
    assert response.answer.index(overview_label) < response.answer.index(products_label)
    assert "2건" in response.answer
    assert "1건" in response.answer
    assert "business_overview" not in response.answer
    assert "products" not in response.answer


def test_two_summaries_from_different_documents_still_fail_fast() -> None:
    """범위가 달라도 문서가 다르면 이전과 같이 막는다."""
    with pytest.raises(UnsupportedResultShapeError, match="DocumentSummary가 2건"):
        build_answer_response(
            completed(
                [
                    summary(section_scope=SectionScope.BUSINESS_OVERVIEW),
                    summary(
                        section_scope=SectionScope.PRODUCTS,
                        doc_id="periodic_SYNTHETIC0002",
                    ),
                ]
            )
        )


def test_repeated_scope_of_the_same_document_fails_fast() -> None:
    with pytest.raises(UnsupportedResultShapeError, match="거듭"):
        build_answer_response(
            completed(
                [
                    summary(section_scope=SectionScope.PRODUCTS),
                    summary(labels=("다. 합성 항목",), section_scope=SectionScope.PRODUCTS),
                ]
            )
        )
