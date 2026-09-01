"""Contract v0.1 변환 테스트.

검증 대상:
- linker/diff 결과가 Contract 타입으로 옳게 옮겨지는가
- Contract에 자리가 없는 정보가 조용히 사라지지 않는가
- 정해지지 않은 정책을 adapter가 임의로 정하지 않는가

Contract 객체를 model_dump()해서 '없는 것은 정말 없다'까지 확인한다. pydantic이 여분 필드를
조용히 버리기 때문에, 손실이 gap에만 남는다는 사실을 회귀로 고정해 둔다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.common.enums import (
    EventLinkRelation,
    EventLinkResolutionStatus,
    EventType,
)
from disclosure_agent.event.contract_adapter import (
    GAP_PENDING_EVENT_ID,
    GAP_PENDING_RESOLUTION_STATUS,
    GAP_RAW_FIELD_NAME,
    GAP_UNMAPPED_FIELD_NAME,
    GAP_UNRESOLVED_LINKS,
    build_change_evidence_id,
    build_event_id,
    gap_kinds,
    pending_decision_gaps,
    temporary_gaps,
    to_event_history,
    to_event_link,
    to_field_change,
    to_raw_reference,
)
from disclosure_agent.event.diff import (
    FIELD_CONTRACT_AMOUNT,
    FIELD_CONTRACT_END_DATE,
)
from disclosure_agent.event.diff import (
    FieldChange as InternalFieldChange,
)
from disclosure_agent.event.linker import build_filing_chain, link_correction


def _gaps_of(kind: str, gaps) -> list:
    return [gap for gap in gaps if gap.kind == kind]


@pytest.fixture
def q13_chain(make_event_filing, make_exchange_document):
    """원공시 1건 + 같은 원공시를 가리키는 정정 2건. 두 번째 정정에 미매핑 필드가 있다."""
    original = make_event_filing("A0001", "20250830")

    first = make_event_filing("A0002", "20251008", is_correction=True)

    second = make_event_filing("A0003", "20251218", is_correction=True)

    documents = {
        "A0001": make_exchange_document(),
        "A0002": make_exchange_document(
            original_filing_date="2025-08-30",
            correction_date="2025-10-08",
            changes=[("계약기간 종료일", "2026-01-31", "2026-06-30")],
        ),
        "A0003": make_exchange_document(
            original_filing_date="2025-08-30",
            correction_date="2025-12-18",
            changes=[
                ("계약금액(원)", "1,000,000,000", "1,500,000,000"),
                ("8. 기타 투자판단과 관련한 중요사항", "해당사항 없음", "이사회 결의"),
            ],
        ),
    }

    chain = build_filing_chain(
        root_receipt_no="A0001",
        filings=[original, first, second],
        documents=documents,
        event_type="supply_contract",
    )

    return chain, documents


# --------------------------------------------------------------------------
# 1. RawReference
# --------------------------------------------------------------------------


def test_raw_reference_keeps_source_spelling(make_exchange_document) -> None:
    """연결 전 원문 참조이므로 날짜를 정규화하지 않는다."""
    document = make_exchange_document(
        original_filing_date="2025-08-30",
        correction_date="2025-10-08",
        reason="계약 조건 변경",
    )

    reference = to_raw_reference(document)

    assert reference.original_filing_date == "2025-08-30"
    assert reference.correction_filing_date == "2025-10-08"
    assert reference.correction_reason == "계약 조건 변경"
    assert reference.document_title == "단일판매ㆍ공급계약 체결"


def test_raw_reference_of_original_filing_is_empty(make_exchange_document) -> None:
    reference = to_raw_reference(make_exchange_document())

    assert reference.original_filing_date is None
    assert reference.correction_filing_date is None


# --------------------------------------------------------------------------
# 2. EventLink
# --------------------------------------------------------------------------


def test_resolved_link_becomes_event_link(make_event_filing, make_exchange_document) -> None:
    original = make_event_filing("A0001", "20250830")

    correction = make_event_filing("A0002", "20251008", is_correction=True)

    document = make_exchange_document(
        original_filing_date="2025-08-30",
        correction_date="2025-10-08",
    )

    result = link_correction(
        correction,
        document=document,
        candidates=[original, correction],
    )

    conversion = to_event_link(result, correction=correction, document=document)

    assert conversion.link is not None
    assert conversion.link.resolution_status is EventLinkResolutionStatus.RESOLVED
    assert conversion.link.source_doc_id == "exchange_A0002"
    assert conversion.link.resolved_doc_id == "exchange_A0001"
    assert conversion.link.relation is EventLinkRelation.CORRECTION_OF
    assert conversion.conversion_gaps == ()


def test_ambiguous_link_becomes_ambiguous_status(
    make_event_filing,
    make_exchange_document,
) -> None:
    correction = make_event_filing("A0003", "20251008", is_correction=True)

    document = make_exchange_document(
        original_filing_date="2025-08-30",
        correction_date="2025-10-08",
    )

    result = link_correction(
        correction,
        document=document,
        candidates=[
            make_event_filing("A0001", "20250830"),
            make_event_filing("A0002", "20250830"),
            correction,
        ],
    )

    conversion = to_event_link(result, correction=correction, document=document)

    assert conversion.link.resolution_status is EventLinkResolutionStatus.AMBIGUOUS
    assert conversion.link.resolved_doc_id is None


def test_ambiguous_candidates_are_preserved_in_contract(
    make_event_filing,
    make_exchange_document,
) -> None:
    """
    후보 목록은 EventLink.candidate_doc_ids에 그대로 실린다.

    이전에는 Contract에 자리가 없어 gap으로만 남겼다. 필드가 생겼으므로 gap을 만들지 않고
    Contract 안에서 보존한다. 후보를 병기하지 못하면 사용자가 어느 문서들 사이에서 모호한지
    알 수 없다.
    """
    correction = make_event_filing("A0003", "20251008", is_correction=True)

    document = make_exchange_document(
        original_filing_date="2025-08-30",
        correction_date="2025-10-08",
    )

    result = link_correction(
        correction,
        document=document,
        candidates=[
            make_event_filing("A0001", "20250830"),
            make_event_filing("A0002", "20250830"),
            correction,
        ],
    )

    conversion = to_event_link(result, correction=correction, document=document)

    assert conversion.link.resolution_status is EventLinkResolutionStatus.AMBIGUOUS
    assert conversion.link.candidate_doc_ids == ["exchange_A0001", "exchange_A0002"]

    # 후보를 하나로 좁히지 않는다.
    assert conversion.link.resolved_doc_id is None

    # 더 이상 gap으로 남기지 않는다.
    assert conversion.conversion_gaps == ()

    # model_dump()에도 그대로 실린다.
    assert conversion.link.model_dump()["candidate_doc_ids"] == [
        "exchange_A0001",
        "exchange_A0002",
    ]


def test_ambiguous_candidates_are_independent_of_input_order(
    make_event_filing,
    make_exchange_document,
) -> None:
    """후보 입력 순서가 결과를 바꾸면 안 된다. 접수번호나 문서 순서로 고르지 않는 원칙과 같다."""
    correction = make_event_filing("A0003", "20251008", is_correction=True)

    document = make_exchange_document(
        original_filing_date="2025-08-30",
        correction_date="2025-10-08",
    )

    def convert(candidates):
        result = link_correction(
            correction,
            document=document,
            candidates=candidates,
        )

        return to_event_link(result, correction=correction, document=document).link

    forward = convert(
        [
            make_event_filing("A0001", "20250830"),
            make_event_filing("A0002", "20250830"),
            correction,
        ]
    )

    reversed_order = convert(
        [
            correction,
            make_event_filing("A0002", "20250830"),
            make_event_filing("A0001", "20250830"),
        ]
    )

    assert forward.candidate_doc_ids == reversed_order.candidate_doc_ids
    assert forward.model_dump() == reversed_order.model_dump()


def test_resolved_link_has_no_candidates(
    make_event_filing,
    make_exchange_document,
) -> None:
    """모호하지 않게 연결된 경우 후보 목록은 비어 있다."""
    original = make_event_filing("A0001", "20250830")

    correction = make_event_filing("A0002", "20251008", is_correction=True)

    document = make_exchange_document(
        original_filing_date="2025-08-30",
        correction_date="2025-10-08",
    )

    result = link_correction(
        correction,
        document=document,
        candidates=[original, correction],
    )

    conversion = to_event_link(result, correction=correction, document=document)

    assert conversion.link.resolution_status is EventLinkResolutionStatus.RESOLVED
    assert conversion.link.resolved_doc_id == "exchange_A0001"
    assert conversion.link.candidate_doc_ids == []


@pytest.mark.parametrize(
    ("original_filing_date", "expected_link_status"),
    [("2019-03-15", "incomplete_chain"), (None, "missing_link_field")],
)
def test_undecided_statuses_are_not_converted_by_default(
    make_event_filing,
    make_exchange_document,
    original_filing_date: str | None,
    expected_link_status: str,
) -> None:
    """
    incomplete_chain / missing_link_field를 adapter가 임의로 매핑하지 않는다.

    Contract는 not_found와 out_of_corpus를 나누는데 그 기준이 아직 없다.
    """
    correction = make_event_filing("A0002", "20251008", is_correction=True)

    document = make_exchange_document(
        original_filing_date=original_filing_date,
        correction_date="2025-10-08",
    )

    result = link_correction(correction, document=document, candidates=[correction])

    assert result.status == expected_link_status

    conversion = to_event_link(result, correction=correction, document=document)

    assert conversion.link is None

    gaps = _gaps_of(GAP_PENDING_RESOLUTION_STATUS, conversion.conversion_gaps)

    assert len(gaps) == 1
    assert gaps[0].pending_decision is True
    assert gaps[0].is_temporary is False
    assert gaps[0].dropped["link_status"] == expected_link_status


def test_caller_can_supply_the_undecided_status(
    make_event_filing,
    make_exchange_document,
) -> None:
    """정책을 정한 호출자만 인자로 넘겨 쓴다."""
    correction = make_event_filing("A0002", "20251008", is_correction=True)

    document = make_exchange_document(
        original_filing_date="2019-03-15",
        correction_date="2025-10-08",
    )

    result = link_correction(correction, document=document, candidates=[correction])

    conversion = to_event_link(
        result,
        correction=correction,
        document=document,
        undecided_status=EventLinkResolutionStatus.OUT_OF_CORPUS,
    )

    assert conversion.link.resolution_status is EventLinkResolutionStatus.OUT_OF_CORPUS
    assert conversion.link.resolved_doc_id is None


def test_link_id_is_deterministic(make_event_filing, make_exchange_document) -> None:
    correction = make_event_filing("A0002", "20251008", is_correction=True)

    document = make_exchange_document(
        original_filing_date="2025-08-30",
        correction_date="2025-10-08",
    )

    result = link_correction(
        correction,
        document=document,
        candidates=[make_event_filing("A0001", "20250830"), correction],
    )

    first = to_event_link(result, correction=correction, document=document).link

    second = to_event_link(result, correction=correction, document=document).link

    assert first.link_id == second.link_id == "link_exchange_A0002_correction_of"


# --------------------------------------------------------------------------
# 3. FieldChange
# --------------------------------------------------------------------------


def test_mapped_change_uses_canonical_field_name() -> None:
    change = InternalFieldChange(
        field=FIELD_CONTRACT_END_DATE,
        raw_field="계약기간 종료일",
        before="2026-01-31",
        after="2026-06-30",
    )

    converted, _ = to_field_change(change, source_doc_id="exchange_A0002")

    assert converted.field_name == FIELD_CONTRACT_END_DATE
    assert converted.before == "2026-01-31"
    assert converted.after == "2026-06-30"
    assert converted.unit is None
    expected_evidence_id = build_change_evidence_id(
        "exchange_A0002", FIELD_CONTRACT_END_DATE
    )
    assert converted.before_evidence_id == expected_evidence_id
    assert converted.after_evidence_id == expected_evidence_id


def test_raw_field_survives_in_gaps() -> None:
    """원문 라벨을 담을 자리가 Contract에 없다."""
    change = InternalFieldChange(
        field=FIELD_CONTRACT_END_DATE,
        raw_field="계약기간 종료일",
        before="A",
        after="B",
    )

    converted, gaps = to_field_change(change, source_doc_id="exchange_A0002")

    raw_gaps = _gaps_of(GAP_RAW_FIELD_NAME, gaps)

    assert raw_gaps[0].dropped["raw_field"] == "계약기간 종료일"
    assert raw_gaps[0].is_temporary is True
    assert raw_gaps[0].planned_contract_field == "FieldChange.raw_field"

    assert "계약기간 종료일" not in str(converted.model_dump())


def test_change_provenance_uses_deterministic_row_evidence_id() -> None:
    """같은 정정 행의 before/after는 같은 결정론적 근거를 가리킨다."""
    change = InternalFieldChange(
        field=FIELD_CONTRACT_AMOUNT,
        raw_field="계약금액(원)",
        before="1",
        after="2",
    )

    converted, gaps = to_field_change(change, source_doc_id="exchange_A0003")

    expected = "evidence_exchange_A0003_contract_amount"

    assert converted.before_evidence_id == expected
    assert converted.after_evidence_id == expected
    assert all(gap.kind != "change_source_document" for gap in gaps)

    repeated, _ = to_field_change(change, source_doc_id="exchange_A0003")
    assert repeated.before_evidence_id == expected
    assert repeated.after_evidence_id == expected


def test_unmapped_change_is_excluded_not_forced_into_field_name() -> None:
    """원문 라벨을 field_name에 억지로 넣지 않는다."""
    change = InternalFieldChange(
        field=None,
        raw_field="8. 기타 투자판단과 관련한 중요사항",
        before="해당사항 없음",
        after="이사회 결의",
    )

    converted, gaps = to_field_change(change, source_doc_id="exchange_A0003")

    assert converted is None

    unmapped = _gaps_of(GAP_UNMAPPED_FIELD_NAME, gaps)

    assert len(unmapped) == 1


def test_unmapped_change_gap_can_be_reconstructed() -> None:
    """Contract가 보완되면 gap 기록만으로 원래 변경을 되살릴 수 있어야 한다."""
    change = InternalFieldChange(
        field=None,
        raw_field="8. 기타 투자판단과 관련한 중요사항",
        before="해당사항 없음",
        after="이사회 결의",
    )

    _, gaps = to_field_change(change, source_doc_id="exchange_A0003")

    dropped = gaps[0].dropped

    assert dropped["raw_field"] == "8. 기타 투자판단과 관련한 중요사항"
    assert dropped["before"] == "해당사항 없음"
    assert dropped["after"] == "이사회 결의"
    assert dropped["source_doc_id"] == "exchange_A0003"


def test_unmapped_gap_is_not_a_contract_limitation() -> None:
    """대응표를 넓히면 해소된다. Contract 개정 대기 항목이 아니다."""
    change = InternalFieldChange(
        field=None,
        raw_field="대규모법인여부",
        before="예",
        after="아니오",
    )

    _, gaps = to_field_change(change, source_doc_id="exchange_A0003")

    assert gaps[0].is_temporary is False
    assert gaps[0].pending_decision is False


# --------------------------------------------------------------------------
# 4. EventHistory
# --------------------------------------------------------------------------


def test_complete_chain_becomes_event_history(q13_chain) -> None:
    chain, documents = q13_chain

    conversion = to_event_history(chain, documents=documents)

    assert conversion.history is not None
    assert conversion.history.chain_complete is True
    assert conversion.history.document_ids == [
        "exchange_A0001",
        "exchange_A0002",
        "exchange_A0003",
    ]


def test_event_id_is_derived_from_event_type_and_root_doc_id(q13_chain) -> None:
    chain, documents = q13_chain

    conversion = to_event_history(chain, documents=documents)

    assert conversion.history.event_id == build_event_id(
        EventType.SUPPLY_CONTRACT, "exchange_A0001"
    )
    assert conversion.history.event_id == "event_supply_contract_exchange_A0001"


def test_event_id_is_stable_across_runs(q13_chain) -> None:
    chain, documents = q13_chain

    first = to_event_history(chain, documents=documents).history

    second = to_event_history(chain, documents=documents).history

    assert first.event_id == second.event_id


def test_ambiguous_root_does_not_get_a_canonical_event_id(q13_chain) -> None:
    """근거 없이 만든 event_id는 다른 계약의 이력을 같은 event로 묶는다."""
    chain, documents = q13_chain

    conversion = to_event_history(chain, documents=documents, root_is_ambiguous=True)

    assert conversion.history is None

    gaps = _gaps_of(GAP_PENDING_EVENT_ID, conversion.conversion_gaps)

    assert len(gaps) == 1
    assert gaps[0].pending_decision is True
    assert gaps[0].dropped["root_is_ambiguous"] is True


def test_missing_root_does_not_get_a_canonical_event_id(
    make_event_filing,
    make_exchange_document,
) -> None:
    chain = build_filing_chain(
        root_receipt_no="NOT_IN_CANDIDATES",
        filings=[make_event_filing("A0001", "20250830")],
        documents={},
        event_type="supply_contract",
    )

    conversion = to_event_history(chain)

    assert conversion.history is None
    assert gap_kinds(conversion.conversion_gaps) == (GAP_PENDING_EVENT_ID,)


def test_history_changes_carry_only_canonical_names(q13_chain) -> None:
    """한글 원문 라벨이 field_name에 섞이지 않는다."""
    chain, documents = q13_chain

    conversion = to_event_history(chain, documents=documents)

    field_names = [change.field_name for change in conversion.history.changes]

    assert field_names == [FIELD_CONTRACT_END_DATE, FIELD_CONTRACT_AMOUNT]


def test_history_reports_the_unmapped_change_it_excluded(q13_chain) -> None:
    """빠진 항목이 gap에 남아 조용히 사라지지 않는다."""
    chain, documents = q13_chain

    conversion = to_event_history(chain, documents=documents)

    unmapped = _gaps_of(GAP_UNMAPPED_FIELD_NAME, conversion.conversion_gaps)

    assert len(unmapped) == 1
    assert unmapped[0].dropped["source_doc_id"] == "exchange_A0003"
    assert unmapped[0].dropped["before"] == "해당사항 없음"


def test_history_returns_links_separately(q13_chain) -> None:
    """EventHistory에는 링크를 담을 자리가 없다."""
    chain, documents = q13_chain

    conversion = to_event_history(chain, documents=documents)

    assert [link.source_doc_id for link in conversion.links] == [
        "exchange_A0002",
        "exchange_A0003",
    ]
    assert all(
        link.resolution_status is EventLinkResolutionStatus.RESOLVED
        for link in conversion.links
    )


def test_incomplete_chain_is_reported(
    make_event_filing,
    make_exchange_document,
) -> None:
    """연결하지 못한 정정이 있으면 chain_complete=False이고 gap이 남는다."""
    original = make_event_filing("A0001", "20250830")

    twin = make_event_filing("A0002", "20250830")

    correction = make_event_filing("A0003", "20251008", is_correction=True)

    documents = {
        "A0003": make_exchange_document(
            original_filing_date="2025-08-30",
            correction_date="2025-10-08",
        ),
    }

    chain = build_filing_chain(
        root_receipt_no="A0001",
        filings=[original, twin, correction],
        documents=documents,
        event_type="supply_contract",
    )

    conversion = to_event_history(chain, documents=documents)

    assert conversion.history.chain_complete is False
    assert conversion.history.document_ids == ["exchange_A0001"]

    unresolved = _gaps_of(GAP_UNRESOLVED_LINKS, conversion.conversion_gaps)

    assert unresolved[0].dropped["unresolved_correction_receipt_nos"] == ["A0003"]


def test_history_is_serializable(q13_chain) -> None:
    chain, documents = q13_chain

    conversion = to_event_history(chain, documents=documents)

    dumped = conversion.history.model_dump()

    assert dumped["chain_complete"] is True
    assert len(dumped["changes"]) == 2


# --------------------------------------------------------------------------
# 5. gap 분류
# --------------------------------------------------------------------------


def test_temporary_and_pending_gaps_are_separable(q13_chain) -> None:
    """
    Contract 개정으로 사라질 것과 팀 결정이 필요한 것은 후속 조치가 다르다.
    """
    chain, documents = q13_chain

    gaps = to_event_history(chain, documents=documents).conversion_gaps

    temporary_kinds = {gap.kind for gap in temporary_gaps(gaps)}

    assert temporary_kinds == {GAP_RAW_FIELD_NAME}

    assert pending_decision_gaps(gaps) == ()


def test_every_temporary_gap_names_its_planned_field(q13_chain) -> None:
    chain, documents = q13_chain

    gaps = to_event_history(chain, documents=documents).conversion_gaps

    for gap in temporary_gaps(gaps):
        assert gap.planned_contract_field


def test_gap_serializes_with_contract_version(q13_chain) -> None:
    chain, documents = q13_chain

    gap = to_event_history(chain, documents=documents).conversion_gaps[0]

    data = gap.to_dict()

    assert data["contract_version"] == "0.1"
    assert data["is_temporary"] is True


def test_conversion_gaps_are_not_mixed_into_contract_objects(q13_chain) -> None:
    """gap은 adapter 내부 결과다. Contract 객체에 규격 밖 필드를 넣지 않는다."""
    chain, documents = q13_chain

    conversion = to_event_history(chain, documents=documents)

    assert set(conversion.history.model_dump()) == {
        "event_id",
        "document_ids",
        "changes",
        "chain_complete",
    }
