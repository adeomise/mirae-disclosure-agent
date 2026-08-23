"""이벤트공시 변경 비교 모듈.

역할:
- 연결된 원본과 정정 Event의 필드를 비교해 변경 내역을 만든다.

예정 입력:
- 원본 Event
- 정정 Event

예정 출력:
- EventChange 목록과 양쪽 Evidence 참조

TODO:
- 필드별 비교와 정규화 정책 구현
- 누락과 삭제를 구분하는 규칙 정의
- 원문 값과 변경 근거 보존 검증
"""

from typing import Any

from disclosure_agent.common.schemas import FieldChange

FIELD_SPECS = {
    "contract_amount": ("계약금액", None),
    "contract_start_date": ("시작일", "계약기간"),
    "contract_end_date": ("종료일", "계약기간"),
}


def diff_supply_contract_documents(
    before_document: dict[str, Any],
    after_document: dict[str, Any],
) -> list[FieldChange]:
    """Compare selected structured rows without numeric or unit normalization."""
    changes: list[FieldChange] = []
    for field_name, (parser_field, required_group) in FIELD_SPECS.items():
        before_row = _find_row(before_document, parser_field, required_group)
        after_row = _find_row(after_document, parser_field, required_group)
        if before_row is None or after_row is None:
            continue

        before = _clean(before_row.get("value"))
        after = _clean(after_row.get("value"))
        if before == after:
            continue

        before_unit = _clean(before_row.get("unit"))
        after_unit = _clean(after_row.get("unit"))
        unit = before_unit if before_unit == after_unit and before_unit else None
        changes.append(
            FieldChange(
                field_name=field_name,
                raw_field=_clean(
                    after_row.get("raw_field")
                    or before_row.get("raw_field")
                    or parser_field
                ),
                before=before,
                after=after,
                unit=unit,
            )
        )
    return changes


def find_change_row(
    document: dict[str, Any],
    field_name: str,
) -> dict[str, Any] | None:
    """Return the parser row behind one Contract field name."""
    spec = FIELD_SPECS.get(field_name)
    if spec is None:
        return None
    return _find_row(document, *spec)


def _find_row(
    document: dict[str, Any],
    field_name: str,
    required_group: str | None,
) -> dict[str, Any] | None:
    for row in document.get("rows") or []:
        if _compact(_clean(row.get("field"))) != _compact(field_name):
            continue
        if required_group and _compact(_clean(row.get("group"))) != _compact(
            required_group
        ):
            continue
        if _clean(row.get("value")):
            return row
    return None


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _compact(value: str) -> str:
    return "".join(value.split())
