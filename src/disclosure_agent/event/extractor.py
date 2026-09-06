"""거래소공시 Event Extractor.

Parser가 보존한 원문 row를 이용하여
공급계약 / 신규시설투자의 핵심 필드를 표준 이름으로 추출한다.

책임:
- 이벤트 유형 판별
- raw field -> canonical field 매핑
- 숫자 / 날짜 최소 정규화
- 정정공시 before / after 보존
- Parser provenance 전달

하지 않는 일:
- 원본/정정공시 chain 연결
- latest state 결정
- 공통 Contract Event 변환
- Evidence Contract 확정
"""


from __future__ import annotations

import argparse
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from disclosure_agent.event.correction_fields import resolve_correction_field

EVENT_SUPPLY_CONTRACT = "supply_contract"
EVENT_FACILITY_INVESTMENT = "facility_investment"


# ---------------------------------------------------------------------------
# field vocabulary
# ---------------------------------------------------------------------------

SUPPLY_CONTRACT_FIELDS = {
    ("판매ㆍ공급계약구분", "판매ㆍ공급계약구분"): "contract_type",
    ("판매ㆍ공급계약구분", "체결계약명"): "contract_name",

    ("계약내역", "계약금액"): "contract_amount",
    ("계약내역", "계약금액총액"): "contract_amount",
    ("계약내역", "최근매출액"): "recent_revenue",
    ("계약내역", "매출액대비"): "revenue_ratio",
    ("계약내역", "대규모법인여부"): "large_corporation",

    ("계약상대", "계약상대"): "counterparty",
    ("계약상대", "회사와의관계"): "counterparty_relation",
    ("계약상대방", "계약상대방"): "counterparty",

    ("판매ㆍ공급지역", "판매ㆍ공급지역"): "supply_region",

    ("계약기간", "시작일"): "contract_start_date",
    ("계약기간", "종료일"): "contract_end_date",

    ("계약(수주)일자", "계약(수주)일자"): "contract_date",
    ("계약(수주)일", "계약(수주)일"): "contract_date",

    ("공시유보관련내용", "유보사유"): "disclosure_hold_reason",
    ("공시유보관련내용", "유보기한"): "disclosure_hold_until",

    (
        "기타투자판단과관련한중요사항",
        "기타투자판단과관련한중요사항",
    ): "notes",
    (
        "기타투자판단에참고할사항",
        "기타투자판단에참고할사항",
    ): "notes",
}

# 이 alias들은 actual schema의 group/field 조합에서만 의미가 확정된다.
# field-only fallback 대상으로 넓히지 않는다.
SUPPLY_CONTRACT_EXACT_ONLY_FIELDS = {
    ("계약내역", "계약금액총액"),
    ("계약상대방", "계약상대방"),
    ("계약(수주)일", "계약(수주)일"),
}


FACILITY_INVESTMENT_FIELDS = {
    ("투자구분", "투자구분"): "investment_type",
    ("투자구분", "투자대상"): "investment_target",

    ("투자내역", "투자금액"): "investment_amount",
    ("투자내역", "자기자본"): "equity",
    ("투자내역", "자기자본대비"): "equity_ratio",
    ("투자내역", "대규모법인여부"): "large_corporation",

    ("투자목적", "투자목적"): "investment_purpose",

    ("투자기간", "시작일"): "investment_start_date",
    ("투자기간", "종료일"): "investment_end_date",

    (
        "이사회결의일(결정일)",
        "이사회결의일",
    ): "board_resolution_date",

    (
        "기타투자판단과관련한중요사항",
        "기타투자판단과관련한중요사항",
    ): "notes",
}


MONEY_FIELDS = {
    "contract_amount",
    "recent_revenue",
    "investment_amount",
    "equity",
}

PERCENT_FIELDS = {
    "revenue_ratio",
    "equity_ratio",
}

DATE_FIELDS = {
    "contract_start_date",
    "contract_end_date",
    "contract_date",
    "disclosure_hold_until",
    "investment_start_date",
    "investment_end_date",
    "board_resolution_date",
}


# ---------------------------------------------------------------------------
# text normalization
# ---------------------------------------------------------------------------

def normalize_label(text: str | None) -> str:
    """필드 비교를 위한 최소 label 정규화."""

    if not text:
        return ""

    value = text.strip()

    # 1. 계약상대 -> 계약상대
    value = re.sub(r"^\d+\.\s*", "", value)

    # - 체결계약명 -> 체결계약명
    value = re.sub(r"^-\s*", "", value)

    # 계약금액(원) -> 계약금액
    value = re.sub(r"\([^()]*\)$", "", value)

    # 공백 제거
    value = re.sub(r"\s+", "", value)

    return value


# ---------------------------------------------------------------------------
# event type
# ---------------------------------------------------------------------------

def detect_event_type(document: dict[str, Any]) -> str:
    """title / report_name을 이용하여 이벤트 유형을 판별한다."""

    metadata = document.get("metadata", {})

    text = " ".join(
        str(value or "")
        for value in (
            document.get("title"),
            metadata.get("report_name"),
        )
    )

    normalized = normalize_label(text)

    if "단일판매" in normalized and "공급계약" in normalized:
        return EVENT_SUPPLY_CONTRACT

    if (
        "신규시설투자" in normalized
        or "신규시설투자등" in normalized
    ):
        return EVENT_FACILITY_INVESTMENT

    raise ValueError(
        f"지원하지 않는 거래소공시 유형입니다: {text!r}"
    )


# ---------------------------------------------------------------------------
# canonical field mapping
# ---------------------------------------------------------------------------

def _match_field_with_priority(
    event_type: str,
    group: str | None,
    field: str | None,
) -> tuple[str | None, int]:
    """Parser field와 canonical field 및 match priority를 반환한다."""

    group_key = normalize_label(group)
    field_key = normalize_label(field)

    if event_type == EVENT_SUPPLY_CONTRACT:
        rules = SUPPLY_CONTRACT_FIELDS
        exact_only_fields = SUPPLY_CONTRACT_EXACT_ONLY_FIELDS
    elif event_type == EVENT_FACILITY_INVESTMENT:
        rules = FACILITY_INVESTMENT_FIELDS
        exact_only_fields = set()
    else:
        return None, 0

    # group + field exact match 우선
    canonical = rules.get((group_key, field_key))

    if canonical is not None:
        return canonical, 2

    # 단독 행은 parser의 current_group 영향으로 group이 조금 다를 수 있다.
    # field 자체가 유일한 경우에만 fallback한다.
    candidates = {
        result
        for (rule_group, rule_field), result in rules.items()
        if rule_field == field_key
        and (rule_group, rule_field) not in exact_only_fields
    }

    if len(candidates) == 1:
        return next(iter(candidates)), 1

    return None, 0


def match_field(
    event_type: str,
    group: str | None,
    field: str | None,
) -> str | None:
    """Parser의 group/field를 canonical field로 매핑한다."""
    canonical, _ = _match_field_with_priority(event_type, group, field)

    return canonical


# ---------------------------------------------------------------------------
# value normalization
# ---------------------------------------------------------------------------

def parse_decimal(raw_value: str) -> Decimal | None:
    value = raw_value.replace(",", "").strip()

    if not value or value == "-":
        return None

    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def normalize_date(raw_value: str) -> str | None:
    value = raw_value.strip()

    if not value or value == "-":
        return None

    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def normalize_value(
    canonical_field: str,
    raw_value: str,
    raw_unit: str | None,
) -> tuple[Any, str | None]:
    """원문은 유지하고 별도 normalized 값을 계산한다."""

    if canonical_field in MONEY_FIELDS:
        number = parse_decimal(raw_value)

        if number is None:
            return None, raw_unit

        if number == number.to_integral_value():
            normalized = int(number)
        else:
            normalized = float(number)

        normalized_unit = (
            "KRW"
            if raw_unit == "원"
            else raw_unit
        )

        return normalized, normalized_unit

    if canonical_field in PERCENT_FIELDS:
        number = parse_decimal(raw_value)

        if number is None:
            return None, raw_unit

        return float(number), "%"

    if canonical_field in DATE_FIELDS:
        return normalize_date(raw_value), None

    value = raw_value.strip()

    if value == "-":
        return None, raw_unit

    return value, raw_unit


# ---------------------------------------------------------------------------
# correction field mapping
# ---------------------------------------------------------------------------

def match_correction_field(
    event_type: str,
    raw_field: str,
) -> str | None:
    """
    정정사항의 field를 canonical field로 가능한 범위에서 매핑한다.

    확실하지 않은 값은 억지로 추론하지 않고 None으로 둔다.
    """

    return resolve_correction_field(raw_field, event_type=event_type)


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------

def extract_event(document: dict[str, Any]) -> dict[str, Any]:
    """Parsed exchange document 한 건에서 Event MVP를 생성한다."""

    metadata = document.get("metadata", {})

    doc_id = str(metadata.get("doc_id") or "")

    if not doc_id:
        raise ValueError(
            "Event 생성에는 metadata.doc_id가 필요합니다."
        )

    event_type = detect_event_type(document)

    fields: dict[str, dict[str, Any]] = {}
    field_match_priorities: dict[str, int] = {}
    unmapped_rows: list[dict[str, Any]] = []
    duplicate_fields: list[dict[str, Any]] = []

    for row in document.get("rows", []):
        canonical_field, match_priority = _match_field_with_priority(
            event_type,
            row.get("group"),
            row.get("field"),
        )

        if canonical_field is None:
            unmapped_rows.append(row)
            continue

        raw_value = str(row.get("value") or "")
        raw_unit = row.get("unit")

        normalized_value, normalized_unit = normalize_value(
            canonical_field,
            raw_value,
            raw_unit,
        )

        provenance = {
            "table_id": row.get("table_id"),
            "row_index": row.get("row_index"),
            "field_cell_index": row.get("field_cell_index"),
            "value_cell_index": row.get("value_cell_index"),
        }
        if row.get("field_row_index") is not None:
            provenance["field_row_index"] = row["field_row_index"]

        extracted = {
            "raw_group": row.get("group"),
            "raw_field": row.get("raw_field"),
            "raw_value": raw_value,
            "raw_unit": raw_unit,
            "normalized_value": normalized_value,
            "normalized_unit": normalized_unit,
            "provenance": provenance,
        }

        if canonical_field in fields:
            previous_priority = field_match_priorities[canonical_field]
            if (
                event_type == EVENT_SUPPLY_CONTRACT
                and match_priority > previous_priority
            ):
                duplicate_fields.append(
                    {
                        "canonical_field": canonical_field,
                        **fields[canonical_field],
                    }
                )
                fields[canonical_field] = extracted
                field_match_priorities[canonical_field] = match_priority
            else:
                duplicate_fields.append(
                    {
                        "canonical_field": canonical_field,
                        **extracted,
                    }
                )
            continue

        fields[canonical_field] = extracted
        field_match_priorities[canonical_field] = match_priority

    correction = extract_correction(
        document,
        event_type,
    )

    return {
        "event_id": f"event_{doc_id}",
        "event_type": event_type,
        "doc_id": doc_id,
        "company": metadata.get("corp_name"),
        "report_name": metadata.get("report_name"),
        "filing_date": metadata.get("receipt_date"),
        "is_correction": bool(metadata.get("is_correction")),
        "fields": fields,
        "correction": correction,

        # 개발/검증용.
        # Contract adapter 단계에서는 제거하거나 별도 debug 영역으로 이동 가능.
        "unmapped_rows": unmapped_rows,
        "duplicate_fields": duplicate_fields,
    }


def extract_correction(
    document: dict[str, Any],
    event_type: str,
) -> dict[str, Any] | None:
    correction = document.get("correction")

    if not correction:
        return None

    changes: list[dict[str, Any]] = []

    for change in correction.get("changes", []):
        raw_field = str(
            change.get("raw_field")
            or change.get("field")
            or ""
        )

        canonical_field = match_correction_field(
            event_type,
            raw_field,
        )

        changes.append(
            {
                "canonical_field": canonical_field,
                "raw_group": change.get("raw_group"),
                "raw_field": raw_field,

                # 여기서는 절대 before/after를 덮어쓰거나
                # latest state로 결정하지 않는다.
                "before": change.get("before"),
                "after": change.get("after"),

                "raw_unit": change.get("raw_unit"),
                "provenance": {
                    "table_id": change.get("table_id"),
                    "row_index": change.get("row_index"),
                    "field_cell_index": change.get("field_cell_index"),
                    "before_cell_index": change.get("before_cell_index"),
                    "after_cell_index": change.get("after_cell_index"),
                },
            }
        )

    return {
        "correction_date": correction.get(
            "correction_date"
        ),
        "original_report_name": correction.get(
            "original_report_name"
        ),

        "original_filing_date": correction.get(
            "original_filing_date"
        ),
        "reason": correction.get("reason"),
        "changes": changes,
    }


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def save_extracted_event(
    event: dict[str, Any],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            event,
            f,
            ensure_ascii=False,
            indent=2,
        )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "input",
        type=Path,
        help="Event Parser가 생성한 parsed JSON",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
    )

    args = parser.parse_args()

    with args.input.open(
        "r",
        encoding="utf-8",
    ) as f:
        document = json.load(f)

    event = extract_event(document)

    output_path = args.output

    if output_path is None:
        output_path = args.input.with_name(
            args.input.stem.replace(
                "_parsed",
                "",
            )
            + "_event.json"
        )

    save_extracted_event(
        event,
        output_path,
    )

    print(f"saved to: {output_path}")
    print(f"event_type: {event['event_type']}")
    print(
        "fields:",
        ", ".join(event["fields"]),
    )
    print(
        "is_correction:",
        event["is_correction"],
    )


if __name__ == "__main__":
    main()
