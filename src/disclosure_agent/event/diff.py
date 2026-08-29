"""이벤트공시 변경 비교 모듈.

역할:
- 정정공시가 원문에 적어 둔 정정 내역을 의미 단위 필드로 정리한다.

현재 구현 범위:
- event.parser가 뽑아 둔 correction.changes를 읽어 필드 이름만 통일한다.
- 원문 값(before/after)은 손대지 않는다. 숫자 파싱, 단위 정규화, 날짜 해석을 하지 않는다.

왜 이름만 통일하는가:
    같은 항목이 정정표와 본문에서 다른 이름으로 적힌다. 정정표는 '계약기간 종료일',
    본문 행은 '종료일'이라고 쓴다. 이름이 다르면 같은 필드의 변경 이력을 이어 붙일 수
    없고, 필드 단위로 무엇이 바뀌었는지 셀 수 없다.

    반대로 값까지 손대면 원문과 대조할 수 없게 된다. 값의 해석은 이 모듈의 책임이 아니다.

모르는 필드에 대한 정책:
    대응표에 없는 필드는 버리지 않고 field=None으로 남긴다. 조용히 빼면 "바뀐 것이 없다"와
    "바뀐 것을 우리가 모른다"가 같은 결과로 보인다.

입력:
- event.parser의 ParsedDocument dict, 또는 correction.changes 목록

출력:
- FieldChange 목록

TODO:
- 지원 이벤트 유형의 미매핑 정정 필드 어휘 확장
- extractor/diff 간 correction canonical mapping parity 보장
- 정정표와 본문 값의 일치 검증
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from disclosure_agent.event import correction_fields

# 기존 event.diff import 경로를 유지하되 canonical 값의 정본은 correction_fields에 둔다.
FIELD_CONTRACT_AMOUNT = correction_fields.FIELD_CONTRACT_AMOUNT
FIELD_CONTRACT_DATE = correction_fields.FIELD_CONTRACT_DATE
FIELD_CONTRACT_DETAIL = correction_fields.FIELD_CONTRACT_DETAIL
FIELD_CONTRACT_END_DATE = correction_fields.FIELD_CONTRACT_END_DATE
FIELD_CONTRACT_PERIOD = correction_fields.FIELD_CONTRACT_PERIOD
FIELD_CONTRACT_START_DATE = correction_fields.FIELD_CONTRACT_START_DATE
FIELD_COUNTERPARTY = correction_fields.FIELD_COUNTERPARTY
FIELD_INVESTMENT_AMOUNT = correction_fields.FIELD_INVESTMENT_AMOUNT
FIELD_INVESTMENT_END_DATE = correction_fields.FIELD_INVESTMENT_END_DATE
FIELD_SUPPLY_REGION = correction_fields.FIELD_SUPPLY_REGION


@dataclass(frozen=True)
class FieldChange:
    """정정공시가 밝힌 필드 하나의 변경."""

    field: str | None
    raw_field: str
    before: str
    after: str

    @property
    def is_mapped(self) -> bool:
        """의미 단위 이름을 붙일 수 있었는지."""
        return self.field is not None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)

        data["is_mapped"] = self.is_mapped

        return data


def resolve_field_name(raw_field: str) -> str | None:
    """
    원문 필드 이름에 대응하는 의미 단위 이름. 모르면 None.

    None은 '변경이 없다'가 아니라 '이 이름을 아직 모른다'는 뜻이다.
    """
    return correction_fields.resolve_history_correction_field(raw_field)


def normalize_changes(changes: Iterable[Mapping[str, Any]]) -> tuple[FieldChange, ...]:
    """
    event.parser가 뽑은 changes 목록을 FieldChange로 정리한다.

    원문 값은 그대로 옮긴다. 빈 값도 채우지 않는다. 공시유보처럼 값이 비어 있는 상태 자체가
    정보이므로 없는 값을 만들어 넣으면 안 된다.
    """
    normalized: list[FieldChange] = []

    for change in changes:
        raw_field = str(change.get("raw_field") or change.get("field") or "").strip()

        if not raw_field:
            continue

        normalized.append(
            FieldChange(
                field=resolve_field_name(raw_field),
                raw_field=raw_field,
                before=str(change.get("before") or ""),
                after=str(change.get("after") or ""),
            )
        )

    return tuple(normalized)


def diff_correction(document: Mapping[str, Any] | None) -> tuple[FieldChange, ...]:
    """
    파싱된 정정공시 하나에서 변경 목록을 만든다.

    정정 문서가 아니거나 정정 블록이 없으면 빈 결과를 돌려준다. 본문 값을 서로 비교해
    변경을 추정하지 않는다. 원문이 밝힌 정정 내역만 쓴다.
    """
    if not document:
        return ()

    correction = document.get("correction")

    if not correction:
        return ()

    return normalize_changes(correction.get("changes") or ())


def changed_field_names(changes: Iterable[FieldChange]) -> tuple[str, ...]:
    """
    변경된 의미 단위 필드 이름을 중복 없이 돌려준다.

    이름을 붙이지 못한 변경은 여기 나오지 않는다. 셀 때는 unmapped_changes로 따로 확인해야
    한다.
    """
    names: dict[str, None] = {}

    for change in changes:
        if change.field is not None:
            names.setdefault(change.field, None)

    return tuple(names)


def unmapped_changes(changes: Iterable[FieldChange]) -> tuple[FieldChange, ...]:
    """대응표에 없는 변경. 결과에 드러내야 하는 항목이다."""
    return tuple(change for change in changes if change.field is None)
