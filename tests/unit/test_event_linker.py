"""정정공시 연결과 체인 구성 테스트.

검증 대상:
- 원공시 후보가 1건일 때만 연결하는가
- 후보가 0건/복수일 때 사유를 나눠 답하는가
- 접수번호나 입력 순서로 임의 선택하지 않는가
- 원공시와 정정공시를 시간순으로 잇는가

manifest도 corpus도 읽지 않는다. 연결 규칙만 본다.
"""

from __future__ import annotations

import json

from disclosure_agent.event.diff import FIELD_CONTRACT_AMOUNT, FIELD_CONTRACT_END_DATE
from disclosure_agent.event.linker import (
    LINK_STATUS_AMBIGUOUS_LINK,
    LINK_STATUS_INCOMPLETE_CHAIN,
    LINK_STATUS_LINKED,
    LINK_STATUS_MISSING_LINK_FIELD,
    build_filing_chain,
    extract_original_filing_date,
    find_original_candidates,
    link_correction,
)

OTHER_SUBTYPE = "신규시설투자등"


# --------------------------------------------------------------------------
# 1. 원공시 제출일 읽기
# --------------------------------------------------------------------------


def test_original_filing_date_is_normalized(make_exchange_document) -> None:
    """본문은 '2025-08-30', manifest는 '20250830'을 쓴다."""
    document = make_exchange_document(
        original_filing_date="2025-08-30",
        correction_date="2025-10-08",
    )

    assert extract_original_filing_date(document) == "20250830"


def test_original_filing_date_is_none_for_original_filing(make_exchange_document) -> None:
    assert extract_original_filing_date(make_exchange_document()) is None


def test_correction_date_is_not_used_as_the_link_key(make_exchange_document) -> None:
    """정정일자는 이 문서가 제출된 날이지 대상 문서가 제출된 날이 아니다."""
    document = make_exchange_document(
        original_filing_date=None,
        correction_date="2025-10-08",
    )

    assert extract_original_filing_date(document) is None


# --------------------------------------------------------------------------
# 2. 후보 탐색
# --------------------------------------------------------------------------


def test_candidates_match_on_company_date_and_subtype(make_event_filing) -> None:
    original = make_event_filing("A0001", "20250830")

    other_company = make_event_filing("A0002", "20250830", corp_name="테스트전자")

    other_date = make_event_filing("A0003", "20250831")

    other_subtype = make_event_filing("A0004", "20250830", document_subtype=OTHER_SUBTYPE)

    matched = find_original_candidates(
        [original, other_company, other_date, other_subtype],
        corp_name="테스트반도체(주)",
        original_filing_date="20250830",
        document_subtype="단일판매공급계약체결",
    )

    assert [filing.receipt_no for filing in matched] == ["A0001"]


def test_candidates_exclude_the_correction_itself(make_event_filing) -> None:
    correction = make_event_filing("A0009", "20250830", is_correction=True)

    matched = find_original_candidates(
        [correction],
        corp_name="테스트반도체(주)",
        original_filing_date="20250830",
        exclude_receipt_no="A0009",
    )

    assert matched == []


def test_candidates_keep_corrections_for_multi_stage_chains(make_event_filing) -> None:
    """정정이 직전 정정을 가리키는 체인이 실제로 있다. 정정본을 후보에서 빼면 끊어진다."""
    first_correction = make_event_filing("A0002", "20251008", is_correction=True)

    matched = find_original_candidates(
        [first_correction],
        corp_name="테스트반도체(주)",
        original_filing_date="20251008",
        exclude_receipt_no="A0003",
    )

    assert [filing.receipt_no for filing in matched] == ["A0002"]


def test_unparsable_original_date_yields_no_candidates(make_event_filing) -> None:
    matched = find_original_candidates(
        [make_event_filing("A0001", "20250830")],
        corp_name="테스트반도체(주)",
        original_filing_date="작년 8월",
    )

    assert matched == []


# --------------------------------------------------------------------------
# 3. 연결 판정
# --------------------------------------------------------------------------


def test_single_candidate_is_linked(make_event_filing, make_exchange_document) -> None:
    original = make_event_filing("A0001", "20250830")

    correction = make_event_filing("A0002", "20251008", is_correction=True)

    result = link_correction(
        correction,
        document=make_exchange_document(
            original_filing_date="2025-08-30",
            correction_date="2025-10-08",
        ),
        candidates=[original, correction],
    )

    assert result.status == LINK_STATUS_LINKED
    assert result.is_linked is True
    assert result.original.receipt_no == "A0001"
    assert result.original_filing_date == "20250830"


def test_no_candidate_reports_incomplete_chain(
    make_event_filing,
    make_exchange_document,
) -> None:
    """원공시가 코퍼스 수집 범위 밖인 경우다."""
    correction = make_event_filing("A0002", "20251008", is_correction=True)

    result = link_correction(
        correction,
        document=make_exchange_document(
            original_filing_date="2019-03-15",
            correction_date="2025-10-08",
        ),
        candidates=[correction],
    )

    assert result.status == LINK_STATUS_INCOMPLETE_CHAIN
    assert result.original is None
    assert result.original_filing_date == "20190315"


def test_multiple_candidates_report_ambiguous_link(
    make_event_filing,
    make_exchange_document,
) -> None:
    """같은 날 같은 유형의 공시가 2건이면 대상을 특정할 수 없다."""
    first = make_event_filing("A0001", "20250830")

    second = make_event_filing("A0002", "20250830")

    correction = make_event_filing("A0003", "20251008", is_correction=True)

    result = link_correction(
        correction,
        document=make_exchange_document(
            original_filing_date="2025-08-30",
            correction_date="2025-10-08",
        ),
        candidates=[first, second, correction],
    )

    assert result.status == LINK_STATUS_AMBIGUOUS_LINK
    assert result.original is None
    assert [filing.receipt_no for filing in result.candidates] == ["A0001", "A0002"]


def test_ambiguous_link_does_not_pick_by_receipt_no(
    make_event_filing,
    make_exchange_document,
) -> None:
    """접수번호가 작은 쪽을 고르지 않는다. 그렇게 고르면 다른 계약의 이력을 답하게 된다."""
    correction = make_event_filing("A0003", "20251008", is_correction=True)

    document = make_exchange_document(
        original_filing_date="2025-08-30",
        correction_date="2025-10-08",
    )

    forward = link_correction(
        correction,
        document=document,
        candidates=[
            make_event_filing("A0001", "20250830"),
            make_event_filing("A0002", "20250830"),
            correction,
        ],
    )

    reversed_order = link_correction(
        correction,
        document=document,
        candidates=[
            make_event_filing("A0002", "20250830"),
            make_event_filing("A0001", "20250830"),
            correction,
        ],
    )

    assert forward.original is None
    assert reversed_order.original is None
    assert forward.status == reversed_order.status


def test_missing_link_field_is_distinct_from_incomplete_chain(
    make_event_filing,
    make_exchange_document,
) -> None:
    """원문이 연결 정보를 안 준 것과 원공시가 코퍼스 밖인 것은 후속 조치가 다르다."""
    correction = make_event_filing("A0002", "20251008", is_correction=True)

    result = link_correction(
        correction,
        document=make_exchange_document(
            original_filing_date=None,
            correction_date="2025-10-08",
        ),
        candidates=[make_event_filing("A0001", "20250830"), correction],
    )

    assert result.status == LINK_STATUS_MISSING_LINK_FIELD
    assert result.original is None


def test_link_result_serializes(make_event_filing, make_exchange_document) -> None:
    correction = make_event_filing("A0002", "20251008", is_correction=True)

    result = link_correction(
        correction,
        document=make_exchange_document(
            original_filing_date="2025-08-30",
            correction_date="2025-10-08",
        ),
        candidates=[make_event_filing("A0001", "20250830"), correction],
    )

    data = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))

    assert data["status"] == LINK_STATUS_LINKED
    assert data["original_receipt_no"] == "A0001"


# --------------------------------------------------------------------------
# 4. 체인 구성
# --------------------------------------------------------------------------


def _q13_shaped_chain(make_event_filing, make_exchange_document):
    """원공시 1건 + 같은 원공시를 가리키는 정정 2건."""
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
                ("계약기간 종료일", "2026-06-30", "2026-09-30"),
            ],
        ),
    }

    return [original, first, second], documents


def test_chain_orders_entries_by_filing_date(
    make_event_filing,
    make_exchange_document,
) -> None:
    filings, documents = _q13_shaped_chain(make_event_filing, make_exchange_document)

    chain = build_filing_chain(
        root_receipt_no="A0001",
        filings=filings,
        documents=documents,
        event_type="supply_contract",
    )

    assert [entry.filing.receipt_no for entry in chain.entries] == ["A0001", "A0002", "A0003"]
    assert chain.is_complete is True


def test_chain_is_independent_of_input_order(
    make_event_filing,
    make_exchange_document,
) -> None:
    """문서 순서가 결과를 바꾸면 안 된다."""
    filings, documents = _q13_shaped_chain(make_event_filing, make_exchange_document)

    forward = build_filing_chain(
        root_receipt_no="A0001",
        filings=filings,
        documents=documents,
        event_type="supply_contract",
    )

    backward = build_filing_chain(
        root_receipt_no="A0001",
        filings=list(reversed(filings)),
        documents=documents,
        event_type="supply_contract",
    )

    assert forward.to_dict() == backward.to_dict()


def test_chain_entries_carry_normalized_changes(
    make_event_filing,
    make_exchange_document,
) -> None:
    filings, documents = _q13_shaped_chain(make_event_filing, make_exchange_document)

    chain = build_filing_chain(
        root_receipt_no="A0001",
        filings=filings,
        documents=documents,
        event_type="supply_contract",
    )

    root, first, second = chain.entries

    assert root.changes == ()
    assert [change.field for change in first.changes] == [FIELD_CONTRACT_END_DATE]
    assert [change.field for change in second.changes] == [
        FIELD_CONTRACT_AMOUNT,
        FIELD_CONTRACT_END_DATE,
    ]

    # 원문 값은 그대로 남아 있어야 한다.
    assert second.changes[0].before == "1,000,000,000"


def test_chain_follows_multi_stage_corrections(
    make_event_filing,
    make_exchange_document,
) -> None:
    """정정 2가 정정 1을 가리키는 경우에도 체인이 이어져야 한다."""
    original = make_event_filing("A0001", "20250830")

    first = make_event_filing("A0002", "20251008", is_correction=True)

    second = make_event_filing("A0003", "20251218", is_correction=True)

    documents = {
        "A0002": make_exchange_document(
            original_filing_date="2025-08-30",
            correction_date="2025-10-08",
        ),
        "A0003": make_exchange_document(
            original_filing_date="2025-10-08",
            correction_date="2025-12-18",
        ),
    }

    chain = build_filing_chain(
        root_receipt_no="A0001",
        filings=[second, first, original],
        documents=documents,
        event_type="supply_contract",
    )

    assert [entry.filing.receipt_no for entry in chain.entries] == ["A0001", "A0002", "A0003"]


def test_chain_excludes_unrelated_corrections(
    make_event_filing,
    make_exchange_document,
) -> None:
    """다른 원공시를 가리키는 정정은 이 체인에 넣지 않는다."""
    filings, documents = _q13_shaped_chain(make_event_filing, make_exchange_document)

    unrelated_original = make_event_filing("B0001", "20250401")

    unrelated_correction = make_event_filing("B0002", "20250501", is_correction=True)

    documents["B0002"] = make_exchange_document(
        original_filing_date="2025-04-01",
        correction_date="2025-05-01",
    )

    chain = build_filing_chain(
        root_receipt_no="A0001",
        filings=[*filings, unrelated_original, unrelated_correction],
        documents=documents,
        event_type="supply_contract",
    )

    assert [entry.filing.receipt_no for entry in chain.entries] == ["A0001", "A0002", "A0003"]


def test_ambiguous_correction_is_reported_not_dropped(
    make_event_filing,
    make_exchange_document,
) -> None:
    """
    체인에 속할 수 있었으나 연결하지 못한 정정은 드러내야 한다.

    조용히 빼면 이력이 완결된 것처럼 보인다.
    """
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

    assert [entry.filing.receipt_no for entry in chain.entries] == ["A0001"]
    assert len(chain.unresolved_links) == 1
    assert chain.unresolved_links[0].status == LINK_STATUS_AMBIGUOUS_LINK
    assert chain.is_complete is False


def test_chain_with_unknown_root_is_empty(make_event_filing, make_exchange_document) -> None:
    filings, documents = _q13_shaped_chain(make_event_filing, make_exchange_document)

    chain = build_filing_chain(
        root_receipt_no="NOT_IN_CANDIDATES",
        filings=filings,
        documents=documents,
        event_type="supply_contract",
    )

    assert chain.entries == ()
    assert chain.is_complete is False


def test_chain_serializes(make_event_filing, make_exchange_document) -> None:
    filings, documents = _q13_shaped_chain(make_event_filing, make_exchange_document)

    chain = build_filing_chain(
        root_receipt_no="A0001",
        filings=filings,
        documents=documents,
        event_type="supply_contract",
    )

    data = json.loads(json.dumps(chain.to_dict(), ensure_ascii=False))

    assert data["is_complete"] is True
    assert len(data["entries"]) == 3


# --------------------------------------------------------------------------
# 5. retrieval FilingRef 호환
# --------------------------------------------------------------------------


def test_real_filing_ref_satisfies_the_linker_input(
    synthetic_manifest_path,
    nfd_corpus_root,
) -> None:
    """
    linker는 구조적 타입만 요구한다.

    retrieval이 만든 FilingRef를 변환 없이 그대로 넣을 수 있어야 handler에서 이어 붙는다.
    """
    from disclosure_agent.retrieval.metadata_filter import list_event_filings

    filings = list_event_filings(
        manifest_path=synthetic_manifest_path,
        corpus_root=nfd_corpus_root,
        company="테스트반도체",
        include_corrections=True,
        document_subtype="단일판매공급계약체결",
    )

    matched = find_original_candidates(
        filings,
        corp_name="테스트반도체(주)",
        original_filing_date="2025-08-30",
    )

    # 합성 manifest에는 같은 날 같은 유형의 공시가 2건 있다.
    assert [filing.receipt_no for filing in matched] == ["SYNTHETIC0011", "SYNTHETIC0012"]
