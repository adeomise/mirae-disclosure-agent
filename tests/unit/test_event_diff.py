"""정정 내역 필드 정규화 테스트.

검증 대상:
- 정정표 표기와 본문 행 표기를 같은 의미 단위 필드로 모으는가
- 원문 값(before/after)을 그대로 보존하는가
- 모르는 필드를 조용히 버리지 않는가

값 해석(숫자 파싱, 단위 정규화, 날짜 변환)은 이 모듈의 책임이 아니므로 검증하지 않는다.
"""

from __future__ import annotations

from typing import Any

import pytest

from disclosure_agent.event.correction_fields import (
    FIELD_BOARD_RESOLUTION_DATE,
    FIELD_DISCLOSURE_HOLD_UNTIL,
    FIELD_REVENUE_RATIO,
    HISTORY_CANONICAL_FIELDS,
    UNSAFE_AMBIGUOUS_CONTAINER,
    UNSAFE_SOURCE_COMPOSITE,
    classify_latest_state_blocking_correction,
    iter_correction_aliases,
    normalize_correction_label,
    resolve_correction_field,
)
from disclosure_agent.event.diff import (
    FIELD_CONTRACT_AMOUNT,
    FIELD_CONTRACT_DATE,
    FIELD_CONTRACT_DETAIL,
    FIELD_CONTRACT_END_DATE,
    FIELD_CONTRACT_PERIOD,
    FIELD_CONTRACT_START_DATE,
    FIELD_COUNTERPARTY,
    FIELD_INVESTMENT_AMOUNT,
    FIELD_INVESTMENT_END_DATE,
    FIELD_SUPPLY_REGION,
    FieldChange,
    changed_field_names,
    diff_correction,
    normalize_changes,
    resolve_field_name,
    unmapped_changes,
)
from disclosure_agent.event.extractor import match_correction_field

# --------------------------------------------------------------------------
# 1. 필드 이름 대응
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event_type", "raw_field", "expected"),
    [
        # 정정표 표기와 본문 행 표기가 같은 필드로 모여야 한다.
        ("supply_contract", "계약기간 종료일", FIELD_CONTRACT_END_DATE),
        ("supply_contract", "종료일", FIELD_CONTRACT_END_DATE),
        ("supply_contract", "계약기간 시작일", FIELD_CONTRACT_START_DATE),
        ("supply_contract", "시작일", FIELD_CONTRACT_START_DATE),
        ("supply_contract", "계약금액(원)", FIELD_CONTRACT_AMOUNT),
        ("supply_contract", "계약금액", FIELD_CONTRACT_AMOUNT),
        ("supply_contract", "계약기간", FIELD_CONTRACT_PERIOD),
        ("supply_contract", "계약(수주)일", FIELD_CONTRACT_DATE),
        ("supply_contract", "계약상대", FIELD_COUNTERPARTY),
        ("supply_contract", "판매ㆍ공급지역", FIELD_SUPPLY_REGION),
        ("supply_contract", "판매ㆍ공급계약 구분 및 세부내용", FIELD_CONTRACT_DETAIL),
        ("supply_contract", "세부내용", FIELD_CONTRACT_DETAIL),
        ("facility_investment", "2. 투자내역 - 투자금액(원)", FIELD_INVESTMENT_AMOUNT),
        ("facility_investment", "4. 투자기간 - 종료일", FIELD_INVESTMENT_END_DATE),
        ("facility_investment", "이사회결의일(결정일)", FIELD_BOARD_RESOLUTION_DATE),
    ],
)
def test_field_aliases_resolve_to_semantic_names(
    event_type: str, raw_field: str, expected: str
) -> None:
    assert resolve_field_name(raw_field, event_type=event_type) == expected


def test_correction_table_and_body_row_names_meet_at_the_same_field() -> None:
    """이름이 다르면 같은 필드의 이력을 이어 붙일 수 없다."""
    assert resolve_field_name(
        "계약기간 종료일", event_type="supply_contract"
    ) == resolve_field_name("종료일", event_type="supply_contract")
    assert resolve_field_name(
        "계약금액(원)", event_type="supply_contract"
    ) == resolve_field_name("계약금액", event_type="supply_contract")


def test_history_resolution_is_scoped_by_event_type() -> None:
    assert (
        resolve_field_name(
            "이사회결의일(결정일)",
            event_type="facility_investment",
        )
        == FIELD_BOARD_RESOLUTION_DATE
    )
    assert (
        resolve_field_name(
            "이사회결의일(결정일)",
            event_type="supply_contract",
        )
        is None
    )


def test_board_resolution_alias_uses_one_normalization_contract() -> None:
    assert normalize_correction_label("이사회결의일(결정일)") == "이사회결의일"
    assert (
        resolve_correction_field(
            " 5.  이사회결의일(결정일) ",
            event_type="facility_investment",
        )
        == FIELD_BOARD_RESOLUTION_DATE
    )


@pytest.mark.parametrize(
    ("raw_field", "canonical_field", "history_supported"),
    [
        ("2. 계약내역 - 계약금액(원)", FIELD_CONTRACT_AMOUNT, True),
        ("2.계약내역 - 계약금액(원)", FIELD_CONTRACT_AMOUNT, True),
        ("2. 계약내역 - 매출액대비(%)", FIELD_REVENUE_RATIO, False),
        ("2.계약내역 - 매출액대비(%)", FIELD_REVENUE_RATIO, False),
        ("매출액대비(%)", FIELD_REVENUE_RATIO, False),
        ("매출액대비", FIELD_REVENUE_RATIO, False),
        ("8. 공시유보 관련내용 - 유보기한", FIELD_DISCLOSURE_HOLD_UNTIL, False),
        ("8.공시유보 관련내용 - 유보기한", FIELD_DISCLOSURE_HOLD_UNTIL, False),
    ],
)
def test_corpus_single_field_aliases_are_shared_and_event_scoped(
    raw_field: str,
    canonical_field: str,
    history_supported: bool,
) -> None:
    assert (
        resolve_correction_field(raw_field, event_type="supply_contract")
        == canonical_field
    )
    assert match_correction_field("supply_contract", raw_field) == canonical_field
    assert resolve_field_name(raw_field, event_type="supply_contract") == (
        canonical_field if history_supported else None
    )
    assert (
        resolve_correction_field(raw_field, event_type="facility_investment")
        is None
    )
    assert match_correction_field("facility_investment", raw_field) is None


@pytest.mark.parametrize(
    "raw_field",
    [
        "2. 계약내역 - 계약금액(원) - 매출액대비(%)",
        "2.계약내역 - 계약금액(원) - 매출액대비(%)",
        "2. 계약내역",
        "2.계약내역",
        "8. 공시유보 관련내용 - 유보사유 - 유보기한",
    ],
)
def test_composite_corpus_labels_remain_fail_closed(raw_field: str) -> None:
    assert resolve_correction_field(raw_field, event_type="supply_contract") is None
    assert match_correction_field("supply_contract", raw_field) is None
    assert resolve_field_name(raw_field, event_type="supply_contract") is None


@pytest.mark.parametrize(
    ("raw_field", "category"),
    [
        (
            "2. 계약내역 - 계약금액(원) - 매출액대비(%)",
            UNSAFE_SOURCE_COMPOSITE,
        ),
        (
            "2.계약내역 - 계약금액(원) - 매출액대비(%)",
            UNSAFE_SOURCE_COMPOSITE,
        ),
        ("2. 계약내역", UNSAFE_AMBIGUOUS_CONTAINER),
        ("2.계약내역", UNSAFE_AMBIGUOUS_CONTAINER),
        (
            "8. 공시유보 관련내용 - 유보사유 - 유보기한",
            UNSAFE_SOURCE_COMPOSITE,
        ),
        ("5.계약기간 - 시작일 - 종료일", UNSAFE_SOURCE_COMPOSITE),
    ],
)
def test_latest_state_blocking_policy_uses_exact_normalized_labels(
    raw_field: str,
    category: str,
) -> None:
    assert (
        classify_latest_state_blocking_correction(
            raw_field,
            event_type="supply_contract",
        )
        == category
    )


@pytest.mark.parametrize(
    "raw_field",
    [
        "기타 투자판단과 관련한 중요사항",
        "완전히 모르는 정정항목",
        "계약금액",
    ],
)
def test_ordinary_unsupported_fields_are_not_latest_state_blocking(raw_field: str) -> None:
    assert (
        classify_latest_state_blocking_correction(
            raw_field,
            event_type="supply_contract",
        )
        is None
    )


def test_latest_state_blocking_policy_is_event_scoped() -> None:
    assert (
        classify_latest_state_blocking_correction(
            "2. 계약내역 - 계약금액(원) - 매출액대비(%)",
            event_type="facility_investment",
        )
        is None
    )


@pytest.mark.parametrize(
    ("event_type", "alias", "canonical_field"),
    list(iter_correction_aliases()),
)
def test_shared_correction_aliases_round_trip(
    event_type: str,
    alias: str,
    canonical_field: str,
) -> None:
    assert (
        resolve_correction_field(alias, event_type=event_type)
        == canonical_field
    )

@pytest.mark.parametrize(
    ("event_type", "alias", "canonical_field"),
    list(iter_correction_aliases()),
)
def test_extractor_and_history_diff_share_supported_correction_vocabulary(
    event_type: str,
    alias: str,
    canonical_field: str,
) -> None:
    assert match_correction_field(event_type, alias) == canonical_field

    if canonical_field in HISTORY_CANONICAL_FIELDS:
        assert resolve_field_name(alias, event_type=event_type) == canonical_field
    else:
        assert resolve_field_name(alias, event_type=event_type) is None


@pytest.mark.parametrize(
    "raw_field",
    ["  계약금액  ", "계약 금액", "1. 계약금액", "- 계약금액", "계약금액(원)"],
)
def test_field_key_normalization_ignores_spacing_and_prefixes(raw_field: str) -> None:
    assert resolve_field_name(raw_field, event_type="supply_contract") == FIELD_CONTRACT_AMOUNT


def test_unit_suffix_is_stripped_only_at_the_end() -> None:
    """'계약(수주)일'의 가운데 괄호는 단위가 아니므로 떼면 안 된다."""
    assert resolve_field_name(
        "계약(수주)일", event_type="supply_contract"
    ) == FIELD_CONTRACT_DATE


@pytest.mark.parametrize(
    "raw_field",
    ["대규모법인여부", "최근매출액", ""],
)
def test_unknown_field_names_resolve_to_none(raw_field: str) -> None:
    """모른다는 사실을 None으로 남긴다. '변경 없음'과 다르다."""
    assert resolve_field_name(raw_field, event_type="supply_contract") is None


# --------------------------------------------------------------------------
# 2. 원문 값 보존
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("1,234,567,000", "2,345,678,000"),
        ("2025-12-31", "2026-06-30"),
        ("△1,000", "(2,000)"),
        ("", "신규 기재"),
        ("공시유보", ""),
    ],
)
def test_raw_values_are_preserved_verbatim(before: str, after: str) -> None:
    """값을 해석하거나 보정하지 않는다. 원문과 대조할 수 있어야 한다."""
    changes = normalize_changes(
        [{"raw_field": "계약금액(원)", "before": before, "after": after}],
        event_type="supply_contract",
    )

    assert changes[0].before == before
    assert changes[0].after == after


def test_facility_investment_changes_are_mapped_without_reversing_values() -> None:
    changes = normalize_changes(
        [
            {
                "raw_field": "4. 투자기간 - 종료일",
                "before": "2025-09-30",
                "after": "2025-04-01",
            },
            {
                "raw_field": "2. 투자내역 - 투자금액(원)",
                "before": "1,980,100,000,000",
                "after": "2,009,800,000,000",
            },
        ],
        event_type="facility_investment",
    )

    assert [change.field for change in changes] == [
        FIELD_INVESTMENT_END_DATE,
        FIELD_INVESTMENT_AMOUNT,
    ]
    assert all(change.is_mapped for change in changes)
    assert changes[0].before == "2025-09-30"
    assert changes[0].after == "2025-04-01"
    assert changes[1].before == "1,980,100,000,000"
    assert changes[1].after == "2,009,800,000,000"


def test_unmapped_change_still_keeps_its_values() -> None:
    """이름을 몰라도 무엇이 어떻게 바뀌었는지는 남아야 한다."""
    changes = normalize_changes(
        [
            {
                "raw_field": "대규모법인여부",
                "before": "해당사항 없음",
                "after": "이사회 결의일 변경",
            }
        ],
        event_type="supply_contract",
    )

    assert changes[0].field is None
    assert changes[0].raw_field == "대규모법인여부"
    assert changes[0].before == "해당사항 없음"
    assert changes[0].after == "이사회 결의일 변경"


def test_raw_field_keeps_original_spelling() -> None:
    changes = normalize_changes(
        [{"raw_field": "계약기간 종료일", "before": "A", "after": "B"}],
        event_type="supply_contract",
    )

    assert changes[0].raw_field == "계약기간 종료일"
    assert changes[0].field == FIELD_CONTRACT_END_DATE


def test_missing_values_become_empty_strings_not_placeholders() -> None:
    """빈 값도 정보다. 없는 값을 지어내지 않는다."""
    changes = normalize_changes(
        [{"raw_field": "계약금액(원)"}], event_type="supply_contract"
    )

    assert changes[0].before == ""
    assert changes[0].after == ""


def test_entries_without_a_field_name_are_skipped() -> None:
    changes = normalize_changes(
        [
            {"raw_field": "", "before": "A", "after": "B"},
            {"raw_field": "계약금액(원)", "before": "A", "after": "B"},
        ],
        event_type="supply_contract",
    )

    assert len(changes) == 1


def test_field_change_falls_back_to_field_key_when_raw_field_absent() -> None:
    changes = normalize_changes(
        [{"field": "종료일", "before": "A", "after": "B"}],
        event_type="supply_contract",
    )

    assert changes[0].field == FIELD_CONTRACT_END_DATE


# --------------------------------------------------------------------------
# 3. 문서 단위 diff
# --------------------------------------------------------------------------


def test_diff_correction_reads_the_correction_block(make_exchange_document) -> None:
    document = make_exchange_document(
        original_filing_date="2025-08-30",
        correction_date="2025-10-08",
        changes=[("계약기간 종료일", "2026-01-31", "2026-06-30")],
    )

    changes = diff_correction(document, event_type="supply_contract")

    assert len(changes) == 1
    assert changes[0].field == FIELD_CONTRACT_END_DATE
    assert changes[0].before == "2026-01-31"
    assert changes[0].after == "2026-06-30"


def test_diff_correction_returns_empty_for_original_filing(make_exchange_document) -> None:
    """원공시에는 정정 블록이 없다. 본문끼리 비교해 변경을 추정하지 않는다."""
    document = make_exchange_document()

    assert diff_correction(document, event_type="supply_contract") == ()


@pytest.mark.parametrize("document", [None, {}, {"correction": None}])
def test_diff_correction_handles_absent_document(document: Any) -> None:
    assert diff_correction(document, event_type="supply_contract") == ()


def test_changed_field_names_deduplicates_and_keeps_order() -> None:
    changes = normalize_changes(
        [
            {"raw_field": "계약금액(원)", "before": "A", "after": "B"},
            {"raw_field": "계약기간 종료일", "before": "C", "after": "D"},
            {"raw_field": "계약금액", "before": "E", "after": "F"},
        ],
        event_type="supply_contract",
    )

    assert changed_field_names(changes) == (FIELD_CONTRACT_AMOUNT, FIELD_CONTRACT_END_DATE)


def test_unmapped_changes_are_reportable() -> None:
    """집계에서 빠진 항목은 드러나야 한다."""
    changes = normalize_changes(
        [
            {"raw_field": "계약금액(원)", "before": "A", "after": "B"},
            {"raw_field": "대규모법인여부", "before": "C", "after": "D"},
        ],
        event_type="supply_contract",
    )

    unmapped = unmapped_changes(changes)

    assert len(unmapped) == 1
    assert unmapped[0].raw_field == "대규모법인여부"


def test_field_change_serializes(make_exchange_document) -> None:
    change = FieldChange(
        field=FIELD_CONTRACT_AMOUNT,
        raw_field="계약금액(원)",
        before="1",
        after="2",
    )

    data = change.to_dict()

    assert data["field"] == FIELD_CONTRACT_AMOUNT
    assert data["is_mapped"] is True
