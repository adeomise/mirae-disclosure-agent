"""공급계약 공시 후보를 1건으로 확정하는 선택 규칙 테스트.

검증 대상:
- 같은 달의 여러 계약 중 질문이 지목한 상대방의 것만 고르는가
- 같은 상대방의 원공시가 둘이면 고르지 않고 막는가(접수번호가 큰 쪽을 고르지 않는가)
- 상대방이 없으면 다른 계약으로 대신하지 않는가
- 월 경계를 실제 말일로 잡는가

manifest/corpus를 읽지 않는다. 이 slice가 정하는 것은 문서 선택 규칙이고, 조회와 파싱은
이미 각자의 테스트가 고정하고 있다. 후보 목록과 본문 읽기를 대신 넣어 선택 규칙만 시험한다.

값은 합성이다. 실제 공시 수치를 저장소에 적지 않는다(AGENTS.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.agent import event_retrieval
from disclosure_agent.agent.event_retrieval import (
    ContractCandidate,
    ContractSelectionStatus,
    filing_date_window,
    select_by_counterparty,
    select_contract_filing,
)
from disclosure_agent.retrieval.metadata_filter import FilingRef

COMPANY = "테스트조선(주)"

CORPUS_ROOT = Path("corpus")


def _filing(receipt_no: str, *, is_correction: bool = False) -> FilingRef:
    """manifest에서 해석된 공시 1건의 합성 참조."""
    return FilingRef(
        doc_id=f"exchange_{receipt_no}",
        corp_name=COMPANY,
        corp_code="00000000",
        stock_code=None,
        report_name="단일판매ㆍ공급계약체결",
        receipt_no=receipt_no,
        receipt_date="20250916",
        document_class="event",
        doc_group="exchange",
        document_subtype="단일판매공급계약체결",
        base_year=None,
        base_month=None,
        is_correction=is_correction,
        directory=CORPUS_ROOT / receipt_no,
        main_document=CORPUS_ROOT / receipt_no / "main.xml",
    )


def _event(counterparty: str | None) -> dict[str, Any]:
    """Event Extractor가 공급계약 1건에서 내놓는 형태의 합성 결과."""
    fields: dict[str, dict[str, Any]] = {}

    if counterparty is not None:
        fields["counterparty"] = {
            "raw_group": "계약상대",
            "raw_field": "계약상대",
            "raw_value": counterparty,
            "normalized_value": counterparty,
        }

    return {"event_type": "supply_contract", "fields": fields}


def _candidate(receipt_no: str, counterparty: str | None) -> ContractCandidate:
    return ContractCandidate(filing=_filing(receipt_no), event=_event(counterparty))


@pytest.fixture
def corpus(monkeypatch: pytest.MonkeyPatch):
    """조회 결과와 본문 읽기를 대신 넣는다.

    돌려주는 함수에 (접수번호, 계약상대) 목록을 주면 그 후보들이 조회된 것처럼 동작한다.
    list_event_filings에 실제로 넘어간 인자는 calls에 남겨 검사한다.
    """
    calls: list[dict[str, Any]] = []

    def install(
        rows: list[tuple[str, str | None]],
        *,
        corrections: list[str] | None = None,
    ) -> None:
        events = {receipt_no: _event(counterparty) for receipt_no, counterparty in rows}

        correction_rows = corrections or []

        def fake_list_event_filings(**kwargs: Any) -> list[FilingRef]:
            calls.append(kwargs)

            filings = [_filing(receipt_no) for receipt_no, _ in rows]

            # 정정본은 호출자가 include_corrections로 요구했을 때만 후보에 들어간다.
            if kwargs.get("include_corrections"):
                filings += [
                    _filing(receipt_no, is_correction=True) for receipt_no in correction_rows
                ]

            return filings

        monkeypatch.setattr(
            event_retrieval, "list_event_filings", fake_list_event_filings
        )
        monkeypatch.setattr(
            event_retrieval, "_read_event", lambda filing: events[filing.receipt_no]
        )

    install.calls = calls  # type: ignore[attr-defined]

    return install


# --------------------------------------------------------------------------
# 1. 월 경계
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        (2025, 9, ("20250901", "20250930")),
        (2025, 2, ("20250201", "20250228")),
        (2024, 2, ("20240201", "20240229")),
        (2025, 12, ("20251201", "20251231")),
        (2025, None, ("20250101", "20251231")),
    ],
)
def test_filing_date_window_uses_the_real_last_day(
    year: int,
    month: int | None,
    expected: tuple[str, str],
) -> None:
    """
    말일을 30이나 31로 고정하면 2월이 없는 날짜를 가리키거나 3월 1일을 끌어들인다.

    윤년 2월을 함께 보는 이유는 28로 고정한 구현이 2024-02-29 공시를 놓치기 때문이다.
    """
    assert filing_date_window(year, month) == expected


# --------------------------------------------------------------------------
# 2. 같은 달의 여러 계약 중 하나를 고른다
# --------------------------------------------------------------------------


def test_requested_counterparty_is_selected_among_same_month_contracts(corpus) -> None:
    """
    같은 기업이 같은 달에 낸 공급계약 공시가 실제로 여러 건 있다.

    상대방으로 좁히지 못하면 다른 계약의 금액을 그 계약의 금액이라고 답하게 된다.
    """
    corpus(
        [
            ("SYNTHETIC0001", "테스트해운"),
            ("SYNTHETIC0002", "테스트로지스"),
            ("SYNTHETIC0003", "테스트가스"),
        ]
    )

    selection = select_contract_filing(
        company=COMPANY,
        year=2025,
        month=9,
        counterparty="테스트로지스",
        corpus_root=CORPUS_ROOT,
    )

    assert selection.status is ContractSelectionStatus.SELECTED
    assert selection.is_selected
    assert selection.selected is not None
    assert selection.selected.doc_id == "exchange_SYNTHETIC0002"

    # 좁히기 전 후보는 결과에 남는다.
    assert selection.candidate_doc_ids == (
        "exchange_SYNTHETIC0001",
        "exchange_SYNTHETIC0002",
        "exchange_SYNTHETIC0003",
    )
    assert selection.matched_doc_ids == ("exchange_SYNTHETIC0002",)


def test_counterparty_match_normalizes_legal_suffix(corpus) -> None:
    """
    법인격 표기 차이로 같은 상대방이 다른 이름으로 읽히면 있는 계약이 '없다'가 된다.

    부분 일치는 쓰지 않는다. 정규화한 전체 이름이 같아야 한다.
    """
    corpus([("SYNTHETIC0001", "테스트해운 주식회사")])

    selection = select_contract_filing(
        company=COMPANY,
        year=2025,
        month=9,
        counterparty="테스트해운(주)",
        corpus_root=CORPUS_ROOT,
    )

    assert selection.status is ContractSelectionStatus.SELECTED


def test_partial_name_does_not_match(corpus) -> None:
    """"테스트해운"이 "테스트해운로지스"에 걸리면 좁히려던 조건이 후보를 넓힌다."""
    corpus([("SYNTHETIC0001", "테스트해운로지스")])

    selection = select_contract_filing(
        company=COMPANY,
        year=2025,
        month=9,
        counterparty="테스트해운",
        corpus_root=CORPUS_ROOT,
    )

    assert selection.status is ContractSelectionStatus.NOT_FOUND


# --------------------------------------------------------------------------
# 3. 확정하지 못하면 고르지 않는다
# --------------------------------------------------------------------------


def test_duplicate_counterparty_roots_are_ambiguous(corpus) -> None:
    """
    같은 상대방의 원공시가 둘이면 질문이 어느 계약을 가리키는지 정할 근거가 없다.

    접수번호가 큰 쪽이나 공시일이 늦은 쪽을 고르지 않는다. 정렬은 순서를 고정할 뿐이고
    우선순위가 아니다.
    """
    corpus(
        [
            ("SYNTHETIC0001", "테스트해운"),
            ("SYNTHETIC0002", "테스트로지스"),
            ("SYNTHETIC0003", "테스트해운"),
        ]
    )

    selection = select_contract_filing(
        company=COMPANY,
        year=2025,
        month=9,
        counterparty="테스트해운",
        corpus_root=CORPUS_ROOT,
    )

    assert selection.status is ContractSelectionStatus.AMBIGUOUS
    assert selection.is_selected is False
    assert selection.selected is None

    # 어느 후보들 사이에서 막혔는지가 결과에 남아야 한다.
    assert selection.matched_doc_ids == (
        "exchange_SYNTHETIC0001",
        "exchange_SYNTHETIC0003",
    )


def test_no_matching_counterparty_is_not_found_with_candidates(corpus) -> None:
    """공시는 있었으나 그 상대방의 계약이 없었다. 다른 상대방의 계약으로 대신하지 않는다."""
    corpus(
        [
            ("SYNTHETIC0001", "테스트해운"),
            ("SYNTHETIC0002", "테스트로지스"),
        ]
    )

    selection = select_contract_filing(
        company=COMPANY,
        year=2025,
        month=9,
        counterparty="없는상대",
        corpus_root=CORPUS_ROOT,
    )

    assert selection.status is ContractSelectionStatus.NOT_FOUND
    assert selection.selected is None
    assert selection.matched_doc_ids == ()

    # 후보가 남아 있으면 "공시가 없다"가 아니라 "그 상대방의 계약이 없다"이다.
    assert selection.candidate_doc_ids == (
        "exchange_SYNTHETIC0001",
        "exchange_SYNTHETIC0002",
    )


def test_no_filing_in_window_is_not_found_without_candidates(corpus) -> None:
    """기간·유형 조건에 맞는 공시 자체가 없으면 후보 목록도 비어 있다."""
    corpus([])

    selection = select_contract_filing(
        company=COMPANY,
        year=2025,
        month=9,
        counterparty="테스트해운",
        corpus_root=CORPUS_ROOT,
    )

    assert selection.status is ContractSelectionStatus.NOT_FOUND
    assert selection.candidate_doc_ids == ()


def test_missing_counterparty_field_does_not_match(corpus) -> None:
    """본문에 상대방이 없는 문서를 상대방이 일치한다고 세지 않는다."""
    corpus([("SYNTHETIC0001", None)])

    selection = select_contract_filing(
        company=COMPANY,
        year=2025,
        month=9,
        counterparty="테스트해운",
        corpus_root=CORPUS_ROOT,
    )

    assert selection.status is ContractSelectionStatus.NOT_FOUND
    assert selection.candidate_doc_ids == ("exchange_SYNTHETIC0001",)


def test_empty_counterparty_is_refused(corpus) -> None:
    """상대방 없이는 같은 달의 여러 계약 중 어느 것인지 정할 근거가 없다."""
    corpus([("SYNTHETIC0001", "테스트해운")])

    with pytest.raises(ValueError):
        select_contract_filing(
            company=COMPANY,
            year=2025,
            month=9,
            counterparty="",
            corpus_root=CORPUS_ROOT,
        )


# --------------------------------------------------------------------------
# 4. 정정공시는 독립 후보가 아니다
# --------------------------------------------------------------------------


def test_corrections_are_not_counted_as_independent_roots(corpus) -> None:
    """
    정정공시는 같은 계약의 다른 판본이다.

    독립 후보로 세면 원공시 하나짜리 질문이 후보 2건으로 보여 ambiguous가 된다.
    조회 단계에서 include_corrections=False로 걸러야 한다.
    """
    corpus(
        [("SYNTHETIC0001", "테스트해운")],
        corrections=["SYNTHETIC0009"],
    )

    selection = select_contract_filing(
        company=COMPANY,
        year=2025,
        month=9,
        counterparty="테스트해운",
        corpus_root=CORPUS_ROOT,
    )

    assert selection.status is ContractSelectionStatus.SELECTED
    assert selection.candidate_doc_ids == ("exchange_SYNTHETIC0001",)

    call = corpus.calls[0]

    assert call["include_corrections"] is False
    assert call["date_range"] == ("20250901", "20250930")
    assert call["document_subtype"] == "단일판매공급계약체결"


# --------------------------------------------------------------------------
# 5. 순수 선택 규칙
# --------------------------------------------------------------------------


def test_select_by_counterparty_preserves_input_order() -> None:
    """순서를 바꾸면 호출부가 앞에서부터 하나를 고르고 싶어진다."""
    candidates = [
        _candidate("SYNTHETIC0003", "테스트해운"),
        _candidate("SYNTHETIC0001", "테스트로지스"),
        _candidate("SYNTHETIC0002", "테스트해운"),
    ]

    matched = select_by_counterparty(candidates, "테스트해운")

    assert [candidate.filing.receipt_no for candidate in matched] == [
        "SYNTHETIC0003",
        "SYNTHETIC0002",
    ]


def test_select_by_counterparty_refuses_empty_target() -> None:
    """빈 상대방은 전부 일치가 아니라 전부 불일치다."""
    candidates = [_candidate("SYNTHETIC0001", "테스트해운")]

    assert select_by_counterparty(candidates, "") == ()
