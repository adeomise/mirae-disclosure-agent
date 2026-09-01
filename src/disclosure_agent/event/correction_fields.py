"""Event correction field vocabulary and normalization."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator

EVENT_SUPPLY_CONTRACT = "supply_contract"
EVENT_FACILITY_INVESTMENT = "facility_investment"

FIELD_CONTRACT_AMOUNT = "contract_amount"
FIELD_CONTRACT_START_DATE = "contract_start_date"
FIELD_CONTRACT_END_DATE = "contract_end_date"
FIELD_CONTRACT_PERIOD = "contract_period"
FIELD_CONTRACT_DATE = "contract_date"
FIELD_COUNTERPARTY = "counterparty"
FIELD_SUPPLY_REGION = "supply_region"
FIELD_CONTRACT_DETAIL = "contract_detail"
FIELD_CONTRACT_NAME = "contract_name"
FIELD_REVENUE_RATIO = "revenue_ratio"
FIELD_DISCLOSURE_HOLD_UNTIL = "disclosure_hold_until"
FIELD_INVESTMENT_AMOUNT = "investment_amount"
FIELD_INVESTMENT_END_DATE = "investment_end_date"
FIELD_INVESTMENT_TYPE = "investment_type"
FIELD_INVESTMENT_TARGET = "investment_target"
FIELD_INVESTMENT_PURPOSE = "investment_purpose"
FIELD_BOARD_RESOLUTION_DATE = "board_resolution_date"
FIELD_NOTES = "notes"

HISTORY_CANONICAL_FIELDS = frozenset(
    {
        FIELD_CONTRACT_AMOUNT,
        FIELD_CONTRACT_START_DATE,
        FIELD_CONTRACT_END_DATE,
        FIELD_CONTRACT_PERIOD,
        FIELD_CONTRACT_DATE,
        FIELD_COUNTERPARTY,
        FIELD_SUPPLY_REGION,
        FIELD_CONTRACT_DETAIL,
        FIELD_INVESTMENT_AMOUNT,
        FIELD_INVESTMENT_END_DATE,
        FIELD_BOARD_RESOLUTION_DATE,
    }
)

CORRECTION_FIELD_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    EVENT_SUPPLY_CONTRACT: {
        FIELD_CONTRACT_AMOUNT: ("계약금액", "계약내역 - 계약금액"),
        FIELD_REVENUE_RATIO: ("매출액대비", "계약내역 - 매출액대비"),
        FIELD_CONTRACT_START_DATE: ("계약기간 시작일", "시작일"),
        FIELD_CONTRACT_END_DATE: ("계약기간 종료일", "종료일"),
        FIELD_CONTRACT_PERIOD: ("계약기간",),
        FIELD_CONTRACT_DATE: ("계약(수주)일", "계약(수주)일자"),
        FIELD_COUNTERPARTY: ("계약상대", "계약상대방"),
        FIELD_SUPPLY_REGION: ("판매ㆍ공급지역",),
        FIELD_CONTRACT_DETAIL: (
            "판매ㆍ공급계약 구분 및 세부내용",
            "판매ㆍ공급계약 구분",
            "세부내용",
        ),
        FIELD_CONTRACT_NAME: ("체결계약명",),
        FIELD_DISCLOSURE_HOLD_UNTIL: ("공시유보 관련내용 - 유보기한",),
        FIELD_NOTES: ("기타 투자판단과 관련한 중요사항",),
    },
    EVENT_FACILITY_INVESTMENT: {
        FIELD_INVESTMENT_AMOUNT: ("투자금액", "투자내역 - 투자금액"),
        FIELD_INVESTMENT_END_DATE: ("투자기간 - 종료일",),
        FIELD_INVESTMENT_TYPE: ("투자구분",),
        FIELD_INVESTMENT_TARGET: ("투자대상",),
        FIELD_INVESTMENT_PURPOSE: ("투자목적",),
        FIELD_BOARD_RESOLUTION_DATE: ("이사회결의일(결정일)",),
        FIELD_NOTES: ("기타 투자판단과 관련한 중요사항",),
    },
}

NUMBER_PREFIX_PATTERN = re.compile(r"^\s*(?:\d+\s*[\.\)]|[-─▶※])\s*")
TRAILING_PARENTHETICAL_PATTERN = re.compile(r"\([^()]*\)$")
BRACKET_PATTERN = re.compile(r"[()（）\[\]]")
SEPARATOR_PATTERN = re.compile(r"[\s ·ㆍ・,\.\-_/]+")


def normalize_correction_label(raw_field: str | None) -> str:
    """Normalize correction aliases and lookup labels with the same contract."""
    if not raw_field:
        return ""

    value = unicodedata.normalize("NFC", raw_field).strip()
    value = NUMBER_PREFIX_PATTERN.sub("", value)
    value = TRAILING_PARENTHETICAL_PATTERN.sub("", value)
    value = BRACKET_PATTERN.sub("", value)
    return SEPARATOR_PATTERN.sub("", value)


def iter_correction_aliases() -> Iterator[tuple[str, str, str]]:
    """Yield event type, raw alias, and canonical field for regression checks."""
    for event_type, vocabulary in CORRECTION_FIELD_ALIASES.items():
        for canonical_field, aliases in vocabulary.items():
            for alias in aliases:
                yield event_type, alias, canonical_field


def _build_alias_indexes() -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    by_event_type: dict[str, dict[str, str]] = {}
    global_candidates: dict[str, set[str]] = {}

    for event_type, alias, canonical_field in iter_correction_aliases():
        normalized = normalize_correction_label(alias)
        event_index = by_event_type.setdefault(event_type, {})

        existing = event_index.get(normalized)
        if existing is not None and existing != canonical_field:
            raise ValueError(
                "correction alias collision: "
                f"{alias!r} normalizes to {normalized!r} within "
                f"{event_type!r} for both {existing!r} and {canonical_field!r}"
            )
        event_index[normalized] = canonical_field
        global_candidates.setdefault(normalized, set()).add(canonical_field)

    combined = {
        alias: next(iter(canonical_fields))
        for alias, canonical_fields in global_candidates.items()
        if len(canonical_fields) == 1
    }

    return by_event_type, combined


ALIASES_BY_EVENT_TYPE, ALL_ALIASES = _build_alias_indexes()


def resolve_correction_field(
    raw_field: str | None,
    *,
    event_type: str | None = None,
) -> str | None:
    """Resolve a correction label from the shared canonical vocabulary."""
    normalized = normalize_correction_label(raw_field)
    aliases = ALL_ALIASES if event_type is None else ALIASES_BY_EVENT_TYPE.get(event_type, {})
    return aliases.get(normalized)


def resolve_history_correction_field(
    raw_field: str | None,
    *,
    event_type: str,
) -> str | None:
    """Resolve only canonical fields currently supported by EventHistory."""
    canonical_field = resolve_correction_field(raw_field, event_type=event_type)
    if canonical_field in HISTORY_CANONICAL_FIELDS:
        return canonical_field
    return None
