"""FilingRef -> SourceDocument 변환 모듈 테스트.

무엇을 고정하는가:
    - 값을 새로 만들지 않고 선택된 FilingRef의 metadata를 그대로 옮기는가(loss-less
      propagation). manifest를 다시 조회하지 않는다는 원칙을 여기서는 "FilingRef 하나만
      받는다"는 함수 시그니처로 확인한다 — 조회할 manifest 자체를 인자로 받지 않는다.
    - 정기공시 세부유형별(FY/Q1/H1/Q3)로 report_name·document_subtype·base_month·
      fiscal_year·filing_date가 실제 값 그대로 나뉘는가.
    - 같은 문서를 가리키는 FilingRef가 여러 번 들어와도 source document는 한 번만
      담기는가(dedup), 그리고 같은 doc_id에 서로 다른 metadata가 들어오면 조용히 하나를
      고르지 않고 멈추는가(fail-close).
    - 여러 문서가 섞이면 doc_id별로 모두 담기고 순서가 호출자가 넘긴 순서를 따르는가.

manifest도 corpus도 열지 않는다. FilingRef를 손으로 만들어 시험한다.

repository에 common/dates.py 전용 테스트 파일이 따로 없어(검색 결과 없음), 이 모듈이
쓰는 parse_filing_date의 달력 유효성 회귀도 맨 아래 절에서 여기 같이 고정한다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from disclosure_agent.agent.source_documents import (
    ConflictingSourceDocumentError,
    build_source_document,
    collect_source_documents,
)
from disclosure_agent.common.dates import parse_filing_date
from disclosure_agent.retrieval.metadata_filter import FilingRef

CORPUS_ROOT = Path("corpus")


def _filing(
    *,
    doc_id: str = "periodic_SYNTHETIC0001",
    corp_name: str = "테스트반도체",
    report_name: str = "사업보고서",
    receipt_no: str = "SYNTHETIC0001",
    receipt_date: str | None = "20260310",
    document_subtype: str = "annual",
    base_year: int | None = 2025,
    base_month: int | None = 12,
) -> FilingRef:
    return FilingRef(
        doc_id=doc_id,
        corp_name=corp_name,
        corp_code="00000001",
        stock_code="000001",
        report_name=report_name,
        receipt_no=receipt_no,
        receipt_date=receipt_date,
        document_class="periodic",
        doc_group="periodic",
        document_subtype=document_subtype,
        base_year=base_year,
        base_month=base_month,
        is_correction=False,
        directory=CORPUS_ROOT / receipt_no,
        main_document=CORPUS_ROOT / receipt_no / f"{receipt_no}.xml",
    )


# --------------------------------------------------------------------------
# 1. 손실 없는 변환
# --------------------------------------------------------------------------


def test_build_source_document_copies_filing_metadata_losslessly() -> None:
    filing = _filing()

    document = build_source_document(filing)

    assert document.doc_id == filing.doc_id
    assert document.company == filing.corp_name
    assert document.report_name == filing.report_name
    assert document.fiscal_year == filing.base_year
    assert document.document_subtype == filing.document_subtype
    assert document.base_month == filing.base_month
    assert document.filing_date == date(2026, 3, 10)
    assert document.receipt_no == filing.receipt_no


def test_build_source_document_maps_empty_manifest_strings_to_none() -> None:
    """FilingRef의 기본값은 빈 문자열이다("" or None). Contract의 optional 규약(None)에 맞춘다."""
    filing = _filing(document_subtype="", receipt_no="")

    document = build_source_document(filing)

    assert document.document_subtype is None
    assert document.receipt_no is None


def test_build_source_document_does_not_invent_a_fiscal_axis_for_exchange_filings() -> None:
    """거래소공시는 manifest의 base_year/base_month가 비어 있다. 지어내지 않고 그대로 None."""
    filing = _filing(
        doc_id="exchange_SYNTHETIC0006",
        report_name="단일판매ㆍ공급계약체결",
        document_subtype="단일판매공급계약체결",
        base_year=None,
        base_month=None,
    )

    document = build_source_document(filing)

    assert document.fiscal_year is None
    assert document.base_month is None


def test_build_source_document_leaves_filing_date_unset_when_unparsable() -> None:
    filing = _filing(receipt_date=None)

    document = build_source_document(filing)

    assert document.filing_date is None


@pytest.mark.parametrize(
    "receipt_date",
    ["20260231", "20251310"],
    ids=["invalid-day", "invalid-month"],
)
def test_build_source_document_leaves_filing_date_unset_for_calendar_invalid_receipt_date(
    receipt_date: str,
) -> None:
    """
    "20260231"·"20251310"은 YYYYMMDD 자릿수는 맞지만 실제 달력에 없는 날짜다.

    parse_filing_date가 이 값으로 date(...)를 만들다 ValueError를 내면 여기서 그대로
    올라가 SourceDocument 생성 전체가 실패한다 — Fact/Evidence는 이미 만들어진 뒤인
    표시용 metadata 변환 단계에서 응답 전체가 500으로 죽는 것이다. filing_date만
    None이 되고 나머지 metadata는 FilingRef 값 그대로 남아야 한다.
    """
    filing = _filing(receipt_date=receipt_date)

    document = build_source_document(filing)

    assert document.filing_date is None
    assert document.doc_id == filing.doc_id
    assert document.company == filing.corp_name
    assert document.report_name == filing.report_name
    assert document.fiscal_year == filing.base_year
    assert document.document_subtype == filing.document_subtype
    assert document.base_month == filing.base_month
    assert document.receipt_no == filing.receipt_no


def test_build_source_document_prefers_canonical_company_over_the_manifest_spelling() -> None:
    """
    Fact.company·DocumentSummary.company와 같은 canonical 표기를 쓴다.

    그러지 않으면 같은 doc_id를 가리키는 Fact와 source document가 같은 회사를 서로 다른
    표기(법인격 표기 유무)로 부르게 된다.
    """
    filing = _filing(corp_name="테스트반도체(주)")

    document = build_source_document(filing, canonical_company="테스트반도체")

    assert document.company == "테스트반도체"


def test_build_source_document_falls_back_to_manifest_spelling_without_canonical_company() -> None:
    """Q02처럼 canonical 표기가 없는 경로는 manifest 원 표기를 그대로 쓴다."""
    filing = _filing(corp_name="테스트반도체(주)")

    document = build_source_document(filing)

    assert document.company == "테스트반도체(주)"


# --------------------------------------------------------------------------
# 2. 정기공시 세부유형별 구분(FY/Q1/H1/Q3)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("document_subtype", "base_month", "report_name", "receipt_date", "expected_filing_date"),
    [
        ("annual", 12, "사업보고서", "20260310", date(2026, 3, 10)),
        ("quarter", 3, "분기보고서", "20250515", date(2025, 5, 15)),
        ("half", 6, "반기보고서", "20250814", date(2025, 8, 14)),
        ("quarter", 9, "분기보고서", "20251114", date(2025, 11, 14)),
    ],
    ids=["FY", "Q1", "H1", "Q3"],
)
def test_build_source_document_distinguishes_periodic_report_variants(
    document_subtype: str,
    base_month: int,
    report_name: str,
    receipt_date: str,
    expected_filing_date: date,
) -> None:
    filing = _filing(
        document_subtype=document_subtype,
        base_month=base_month,
        report_name=report_name,
        receipt_date=receipt_date,
    )

    document = build_source_document(filing)

    assert document.document_subtype == document_subtype
    assert document.base_month == base_month
    assert document.report_name == report_name
    assert document.fiscal_year == 2025
    assert document.filing_date == expected_filing_date


# --------------------------------------------------------------------------
# 3. 여러 FilingRef 모으기: dedup, 충돌, 순서, 복수 문서
# --------------------------------------------------------------------------


def test_collect_source_documents_deduplicates_the_same_document() -> None:
    """같은 문서에서 여러 Fact/Evidence를 확보해도 source document는 한 번만 담는다."""
    filing = _filing()

    documents = collect_source_documents([filing, filing])

    assert len(documents) == 1
    assert documents[0] == build_source_document(filing)


def test_collect_source_documents_raises_on_conflicting_metadata_for_the_same_doc_id() -> None:
    """같은 doc_id에 서로 다른 metadata가 들어오면 조용히 하나를 고르지 않는다."""
    first = _filing(base_year=2025)
    second = _filing(base_year=2024)  # 같은 doc_id, 다른 회계연도. 실제로는 일어나면 안 된다.

    with pytest.raises(ConflictingSourceDocumentError):
        collect_source_documents([first, second])


def test_collect_source_documents_keeps_the_callers_deterministic_order() -> None:
    """정렬하지 않는다. 호출자가 넘긴 순서가 이미 실행 결과·Evidence의 결정적 순서다."""
    older = _filing(
        doc_id="periodic_SYNTHETIC0002",
        receipt_no="SYNTHETIC0002",
        base_year=2024,
        receipt_date="20250313",
    )
    newer = _filing()

    assert [doc.doc_id for doc in collect_source_documents([newer, older])] == [
        newer.doc_id,
        older.doc_id,
    ]
    assert [doc.doc_id for doc in collect_source_documents([older, newer])] == [
        older.doc_id,
        newer.doc_id,
    ]


def test_collect_source_documents_includes_one_entry_per_distinct_document() -> None:
    """복수 문서 응답에서는 문서별로 각각 담긴다."""
    first = _filing()
    second = _filing(
        doc_id="periodic_SYNTHETIC0005",
        corp_name="테스트전자",
        receipt_no="SYNTHETIC0005",
    )

    documents = collect_source_documents([first, second])

    assert {doc.doc_id for doc in documents} == {first.doc_id, second.doc_id}


def test_collect_source_documents_returns_an_empty_list_for_no_filings() -> None:
    assert collect_source_documents([]) == []


# --------------------------------------------------------------------------
# 4. parse_filing_date: 달력상 유효하지 않은 YYYYMMDD
# --------------------------------------------------------------------------


def test_parse_filing_date_parses_a_calendar_valid_date() -> None:
    assert parse_filing_date("20260310") == date(2026, 3, 10)


@pytest.mark.parametrize(
    "value",
    ["20260231", "20251310"],
    ids=["invalid-day", "invalid-month"],
)
def test_parse_filing_date_returns_none_for_calendar_invalid_dates(value: str) -> None:
    """
    "20260231"(2월 31일 없음)·"20251310"(13월 없음)은 normalize_filing_date의 자릿수
    패턴은 통과하지만 date(...)로 만들면 ValueError가 난다. 추측하지 않고 None.
    """
    assert parse_filing_date(value) is None
