"""거래소공시 retrieval primitive 테스트.

검증 대상:
- Agent 설계 어휘(`document_class="event"`)와 manifest 표기(`doc_group="exchange"`) 연결
- 공시일 기준 기간 필터. 거래소공시 레코드는 base_year가 비어 있다
- 정정 포함 여부를 호출자가 명시하는가
- 후보가 여럿일 때 임의로 1건을 고르지 않는가

Pattern E handler와 chain resolution은 아직 만들지 않는다. 여기서는 후보를 어떻게 모으는지만
검증한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.retrieval.metadata_filter import (
    DOCUMENT_CLASS_EVENT,
    DOCUMENT_CLASS_HOLDING,
    DOCUMENT_CLASS_PERIODIC,
    PERIOD_KIND_FILING,
    PERIOD_KIND_FISCAL,
    FilingNotFoundError,
    filter_filings,
    list_event_filings,
    list_filings,
    locate_filing,
    normalize_receipt_date,
    resolve_doc_group,
    resolve_period_kind,
)

EVENT_SUBTYPE_SUPPLY_CONTRACT = "단일판매공급계약체결"

EVENT_SUBTYPE_FACILITY_INVESTMENT = "신규시설투자등"

SYNTHETIC_COMPANY = "테스트반도체"


def _receipt_nos(records: list[dict[str, Any]]) -> list[str]:
    return [record["rcept_no"] for record in records]


# --------------------------------------------------------------------------
# 1. 어휘 대응
# --------------------------------------------------------------------------


def test_event_document_class_maps_to_exchange_doc_group() -> None:
    """설계는 event, manifest는 exchange다. 이 대응이 없으면 문서를 한 건도 못 찾는다."""
    assert resolve_doc_group(DOCUMENT_CLASS_EVENT) == "exchange"


def test_periodic_and_holding_document_classes_map_to_same_name() -> None:
    assert resolve_doc_group(DOCUMENT_CLASS_PERIODIC) == "periodic"
    assert resolve_doc_group(DOCUMENT_CLASS_HOLDING) == "holding"


def test_unknown_document_class_is_rejected_instead_of_returning_empty() -> None:
    """어휘가 틀린 것과 문서가 없는 것을 같은 빈 결과로 보이게 하지 않는다."""
    with pytest.raises(FilingNotFoundError) as error:
        resolve_doc_group("exchange")

    assert error.value.reason == "unknown_document_class"


def test_default_period_kind_follows_document_class() -> None:
    """거래소·지분공시는 회계연도가 없으므로 기본 판정 기준이 공시일이어야 한다."""
    assert resolve_period_kind(DOCUMENT_CLASS_PERIODIC) == PERIOD_KIND_FISCAL
    assert resolve_period_kind(DOCUMENT_CLASS_EVENT) == PERIOD_KIND_FILING
    assert resolve_period_kind(DOCUMENT_CLASS_HOLDING) == PERIOD_KIND_FILING


def test_explicit_period_kind_overrides_default() -> None:
    assert resolve_period_kind(DOCUMENT_CLASS_EVENT, PERIOD_KIND_FISCAL) == PERIOD_KIND_FISCAL


def test_unknown_period_kind_is_rejected() -> None:
    with pytest.raises(FilingNotFoundError) as error:
        resolve_period_kind(DOCUMENT_CLASS_EVENT, "quarterly")

    assert error.value.reason == "unknown_period_kind"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20240830", "20240830"),
        ("2024-08-30", "20240830"),
        ("2024.08.30", "20240830"),
        ("2024/08/30", "20240830"),
        (None, None),
        ("", None),
        ("2024-08", None),
        ("작년 8월", None),
    ],
)
def test_normalize_receipt_date(raw: Any, expected: str | None) -> None:
    """manifest는 '20240830', 본문은 '2024-08-30'을 쓴다. 비교하려면 형태를 맞춰야 한다."""
    assert normalize_receipt_date(raw) == expected


# --------------------------------------------------------------------------
# 2. 기간 필터
# --------------------------------------------------------------------------


def test_fiscal_year_filter_finds_no_event_records(
    manifest_records: list[dict[str, Any]],
) -> None:
    """
    회계연도로 거르면 거래소공시가 전건 탈락한다.

    이 동작이 이번 작업에서 고친 문제 그 자체이므로 회귀로 고정해 둔다.
    """
    matched = filter_filings(
        manifest_records,
        company=SYNTHETIC_COMPANY,
        document_class=DOCUMENT_CLASS_EVENT,
        year=2025,
        period_kind=PERIOD_KIND_FISCAL,
    )

    assert matched == []


def test_filing_year_filter_finds_event_records(
    manifest_records: list[dict[str, Any]],
) -> None:
    """같은 조건이라도 공시일 기준이면 찾아진다. 기본값이 이쪽이어야 한다."""
    matched = filter_filings(
        manifest_records,
        company=SYNTHETIC_COMPANY,
        document_class=DOCUMENT_CLASS_EVENT,
        year=2025,
    )

    assert _receipt_nos(matched) == ["SYNTHETIC0006", "SYNTHETIC0011", "SYNTHETIC0012"]


def test_filing_year_filter_excludes_other_years(
    manifest_records: list[dict[str, Any]],
) -> None:
    matched = filter_filings(
        manifest_records,
        company=SYNTHETIC_COMPANY,
        document_class=DOCUMENT_CLASS_EVENT,
        year=2026,
    )

    assert _receipt_nos(matched) == ["SYNTHETIC0015"]


def test_date_range_filter_narrows_to_a_single_day(
    manifest_records: list[dict[str, Any]],
) -> None:
    matched = filter_filings(
        manifest_records,
        company=SYNTHETIC_COMPANY,
        document_class=DOCUMENT_CLASS_EVENT,
        date_range=("2025-08-30", "2025-08-30"),
    )

    assert _receipt_nos(matched) == ["SYNTHETIC0011", "SYNTHETIC0012"]


def test_date_range_accepts_manifest_style_dates(
    manifest_records: list[dict[str, Any]],
) -> None:
    matched = filter_filings(
        manifest_records,
        company=SYNTHETIC_COMPANY,
        document_class=DOCUMENT_CLASS_EVENT,
        date_range=("20250601", "20250830"),
        include_corrections=True,
    )

    assert _receipt_nos(matched) == ["SYNTHETIC0006", "SYNTHETIC0011", "SYNTHETIC0012"]


def test_unparsable_date_range_is_rejected(
    manifest_records: list[dict[str, Any]],
) -> None:
    with pytest.raises(FilingNotFoundError) as error:
        filter_filings(
            manifest_records,
            company=SYNTHETIC_COMPANY,
            document_class=DOCUMENT_CLASS_EVENT,
            date_range=("작년 8월", "올해 3월"),
        )

    assert error.value.reason == "unparsable_date_range"


# --------------------------------------------------------------------------
# 3. 세부유형과 정정 포함 여부
# --------------------------------------------------------------------------


def test_subtype_none_does_not_filter_subtypes(
    manifest_records: list[dict[str, Any]],
) -> None:
    """질문이 유형을 지정하지 않는 경우가 있다."""
    matched = filter_filings(
        manifest_records,
        company=SYNTHETIC_COMPANY,
        document_class=DOCUMENT_CLASS_EVENT,
        document_subtype=None,
        include_corrections=True,
    )

    assert len(matched) == 6


def test_subtype_filters_when_given(
    manifest_records: list[dict[str, Any]],
) -> None:
    matched = filter_filings(
        manifest_records,
        company=SYNTHETIC_COMPANY,
        document_class=DOCUMENT_CLASS_EVENT,
        document_subtype=EVENT_SUBTYPE_FACILITY_INVESTMENT,
        include_corrections=True,
    )

    assert _receipt_nos(matched) == ["SYNTHETIC0015"]


def test_include_corrections_false_drops_the_correction_chain(
    manifest_records: list[dict[str, Any]],
) -> None:
    matched = filter_filings(
        manifest_records,
        company=SYNTHETIC_COMPANY,
        document_class=DOCUMENT_CLASS_EVENT,
        document_subtype=EVENT_SUBTYPE_SUPPLY_CONTRACT,
        include_corrections=False,
    )

    assert _receipt_nos(matched) == ["SYNTHETIC0006", "SYNTHETIC0011", "SYNTHETIC0012"]


def test_include_corrections_true_keeps_the_correction_chain(
    manifest_records: list[dict[str, Any]],
) -> None:
    """Pattern E는 정정본이 대상 자체이므로 제외하면 이력이 통째로 사라진다."""
    matched = filter_filings(
        manifest_records,
        company=SYNTHETIC_COMPANY,
        document_class=DOCUMENT_CLASS_EVENT,
        document_subtype=EVENT_SUBTYPE_SUPPLY_CONTRACT,
        include_corrections=True,
    )

    assert _receipt_nos(matched) == [
        "SYNTHETIC0006",
        "SYNTHETIC0011",
        "SYNTHETIC0012",
        "SYNTHETIC0013",
        "SYNTHETIC0014",
    ]


# --------------------------------------------------------------------------
# 4. 후보 목록 반환
# --------------------------------------------------------------------------


def test_list_event_filings_requires_explicit_include_corrections(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """정정 포함 여부는 기본값으로 정할 문제가 아니라 호출자가 밝힐 문제다."""
    with pytest.raises(TypeError):
        list_event_filings(  # type: ignore[call-arg]
            manifest_path=synthetic_manifest_path,
            corpus_root=nfd_corpus_root,
            company=SYNTHETIC_COMPANY,
        )


def test_list_event_filings_returns_every_same_day_candidate(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    같은 날 같은 유형의 공시가 2건이면 2건 모두 돌려준다.

    문서 순서로 하나를 고르면 근거 없이 다른 계약을 답하게 된다. 어느 것인지는 본문 대조로
    정할 일이며 retrieval이 정하지 않는다.
    """
    filings = list_event_filings(
        manifest_path=synthetic_manifest_path,
        corpus_root=nfd_corpus_root,
        company=SYNTHETIC_COMPANY,
        include_corrections=False,
        document_subtype=EVENT_SUBTYPE_SUPPLY_CONTRACT,
        date_range=("2025-08-30", "2025-08-30"),
    )

    assert [filing.receipt_no for filing in filings] == ["SYNTHETIC0011", "SYNTHETIC0012"]


def test_list_event_filings_includes_corrections_when_asked(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    filings = list_event_filings(
        manifest_path=synthetic_manifest_path,
        corpus_root=nfd_corpus_root,
        company=SYNTHETIC_COMPANY,
        include_corrections=True,
        document_subtype=EVENT_SUBTYPE_SUPPLY_CONTRACT,
    )

    assert [filing.is_correction for filing in filings] == [False, False, False, True, True]


def test_list_event_filings_is_ordered_deterministically(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """정렬은 순서를 고정할 뿐이고 우선순위가 아니다."""
    filings = list_event_filings(
        manifest_path=synthetic_manifest_path,
        corpus_root=nfd_corpus_root,
        company=SYNTHETIC_COMPANY,
        include_corrections=True,
        document_subtype=EVENT_SUBTYPE_SUPPLY_CONTRACT,
    )

    receipt_dates = [filing.receipt_date for filing in filings]

    assert receipt_dates == sorted(receipt_dates)


def test_event_filing_ref_reports_both_vocabularies(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """설계 어휘와 manifest 표기를 모두 남겨 어느 레코드에서 왔는지 확인할 수 있게 한다."""
    filings = list_event_filings(
        manifest_path=synthetic_manifest_path,
        corpus_root=nfd_corpus_root,
        company=SYNTHETIC_COMPANY,
        include_corrections=False,
        document_subtype=EVENT_SUBTYPE_FACILITY_INVESTMENT,
    )

    assert len(filings) == 1

    filing = filings[0]

    assert filing.document_class == DOCUMENT_CLASS_EVENT
    assert filing.doc_group == "exchange"
    assert filing.document_subtype == EVENT_SUBTYPE_FACILITY_INVESTMENT
    assert filing.base_year is None
    assert filing.main_document.exists()


def test_list_filings_reports_unresolved_paths_instead_of_dropping_them(
    tmp_path: Path,
    synthetic_manifest_path: Path,
) -> None:
    """
    체인을 다루는 Pattern E에서 문서 한 건이 말없이 빠지면 이력이 완결된 것처럼 보인다.

    corpus에 파일이 없는 상태에서 조회하면 빈 목록이 아니라 오류가 나야 한다.
    """
    empty_corpus = tmp_path / "empty_corpus"

    empty_corpus.mkdir()

    with pytest.raises(FilingNotFoundError) as error:
        list_event_filings(
            manifest_path=synthetic_manifest_path,
            corpus_root=empty_corpus,
            company=SYNTHETIC_COMPANY,
            include_corrections=True,
        )

    assert error.value.reason == "path_unresolved"


def test_no_matching_event_returns_empty_list_not_error(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """조건에 맞는 문서가 없는 것은 오류가 아니다. 판정은 호출자가 한다."""
    filings = list_event_filings(
        manifest_path=synthetic_manifest_path,
        corpus_root=nfd_corpus_root,
        company=SYNTHETIC_COMPANY,
        include_corrections=True,
        year=2019,
    )

    assert filings == []


# --------------------------------------------------------------------------
# 5. 정기공시 회귀
# --------------------------------------------------------------------------


def test_periodic_locate_filing_is_unchanged(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """event 지원을 붙여도 Q01 경로의 단건 확정 동작은 그대로여야 한다."""
    filing = locate_filing(
        manifest_path=synthetic_manifest_path,
        corpus_root=nfd_corpus_root,
        company=SYNTHETIC_COMPANY,
        document_class=DOCUMENT_CLASS_PERIODIC,
        document_subtype="annual",
        year=2025,
    )

    assert filing.doc_id == "periodic_SYNTHETIC0001"
    assert filing.document_class == DOCUMENT_CLASS_PERIODIC
    assert filing.doc_group == "periodic"
    assert filing.is_correction is False


def test_periodic_list_filings_does_not_pick_one(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """단건 확정과 후보 조회는 다른 함수다. 정정본을 포함하면 후보가 2건이다."""
    filings = list_filings(
        manifest_path=synthetic_manifest_path,
        corpus_root=nfd_corpus_root,
        company=SYNTHETIC_COMPANY,
        document_class=DOCUMENT_CLASS_PERIODIC,
        document_subtype="annual",
        year=2025,
        include_corrections=True,
    )

    assert [filing.receipt_no for filing in filings] == ["SYNTHETIC0001", "SYNTHETIC0009"]
