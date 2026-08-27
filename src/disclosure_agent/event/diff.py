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
- 신규시설투자 등 다른 이벤트 유형의 필드 어휘 추가
- 정정표와 본문 값의 일치 검증
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

# 의미 단위 필드 이름.
#
# Q13(공급계약 정정 추적)에 필요한 최소 어휘만 둔다. 원문에 등장하는 모든 필드를 미리
# 이름 붙이지 않는다. 쓰지 않는 이름은 검증되지 않은 채로 남고, 나중에 실제 필요한 형태와
# 어긋나면 고치는 비용만 생긴다.
FIELD_CONTRACT_AMOUNT = "contract_amount"
FIELD_CONTRACT_START_DATE = "contract_start_date"
FIELD_CONTRACT_END_DATE = "contract_end_date"
FIELD_CONTRACT_PERIOD = "contract_period"
FIELD_CONTRACT_DATE = "contract_date"
FIELD_COUNTERPARTY = "counterparty"
FIELD_SUPPLY_REGION = "supply_region"
FIELD_CONTRACT_DETAIL = "contract_detail"
FIELD_INVESTMENT_AMOUNT = "investment_amount"
FIELD_INVESTMENT_END_DATE = "investment_end_date"

# 원문 표기 -> 의미 단위 필드.
#
# key는 _normalize_field_key를 통과한 형태다. 공백과 구분 기호를 없애고 단위 괄호를 뗀
# 결과이므로 '계약금액(원)'과 '계약 금액'이 같은 key가 된다.
#
# 정정표 표기와 본문 행 표기가 모두 들어 있다. 둘을 같은 필드로 모으는 것이 이 표의 목적이다.
FIELD_ALIASES: dict[str, str] = {
    # 정정표: '계약금액(원)' / 본문 행: '계약금액'
    "계약금액": FIELD_CONTRACT_AMOUNT,
    # 정정표: '계약기간 시작일' / 본문 행: '시작일'
    "계약기간시작일": FIELD_CONTRACT_START_DATE,
    "시작일": FIELD_CONTRACT_START_DATE,
    # 정정표: '계약기간 종료일' / 본문 행: '종료일'
    "계약기간종료일": FIELD_CONTRACT_END_DATE,
    "종료일": FIELD_CONTRACT_END_DATE,
    # 시작일과 종료일을 한 행으로 함께 정정하는 경우가 있다.
    # 시작·종료 어느 쪽이 바뀌었는지 원문만으로는 알 수 없으므로 쪼개지 않고 그대로 둔다.
    "계약기간": FIELD_CONTRACT_PERIOD,
    "계약수주일": FIELD_CONTRACT_DATE,
    "계약상대": FIELD_COUNTERPARTY,
    "계약상대방": FIELD_COUNTERPARTY,
    "판매공급지역": FIELD_SUPPLY_REGION,
    "판매공급계약구분및세부내용": FIELD_CONTRACT_DETAIL,
    "판매공급계약구분": FIELD_CONTRACT_DETAIL,
    "세부내용": FIELD_CONTRACT_DETAIL,
    "투자내역투자금액": FIELD_INVESTMENT_AMOUNT,
    "투자기간종료일": FIELD_INVESTMENT_END_DATE,
}

# 필드 이름 끝에 붙는 단위 표기. 의미가 아니라 표기이므로 key에서 뗀다.
UNIT_SUFFIX_PATTERN = re.compile(r"\((?:원|%|주|천원|만원|백만원|억원|조원)\)$")

# 항목 번호와 머리 기호. event.parser가 이미 떼지만 정정표 표기가 남는 경우가 있다.
NUMBER_PREFIX_PATTERN = re.compile(r"^\s*(?:\d+\s*[\.\)]|[-─▶※])\s*")

# 공백과 가운뎃점류 구분 기호. 표기 차이일 뿐 의미를 바꾸지 않는다.
SEPARATOR_PATTERN = re.compile(r"[\s ·ㆍ・,\.\-_/]+")

# 괄호 자체는 지우고 안의 글자는 남긴다. '계약(수주)일'과 '계약 수주일'을 같게 본다.
# 단위 괄호는 이 단계 전에 내용까지 함께 떼어 낸다.
BRACKET_PATTERN = re.compile(r"[()（）\[\]]")


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


def _normalize_field_key(raw_field: str) -> str:
    """
    필드 이름을 대응표 조회용 key로 바꾼다.

    - 유니코드 정규화(NFC)
    - 항목 번호와 머리 기호 제거
    - 끝에 붙은 단위 괄호는 내용까지 제거. '계약금액(원)'의 '(원)'이 여기 해당한다
    - 남은 괄호는 기호만 제거하고 안의 글자는 남긴다. '계약(수주)일'의 '수주'가 여기 해당한다
    - 공백과 구분 기호 제거

    예:
        "계약금액(원)"      -> "계약금액"
        "계약기간 종료일"    -> "계약기간종료일"
        "계약(수주)일"       -> "계약수주일"
    """
    if not raw_field:
        return ""

    text = unicodedata.normalize("NFC", raw_field).strip()

    text = NUMBER_PREFIX_PATTERN.sub("", text)

    text = UNIT_SUFFIX_PATTERN.sub("", text)

    text = BRACKET_PATTERN.sub("", text)

    return SEPARATOR_PATTERN.sub("", text)


def resolve_field_name(raw_field: str) -> str | None:
    """
    원문 필드 이름에 대응하는 의미 단위 이름. 모르면 None.

    None은 '변경이 없다'가 아니라 '이 이름을 아직 모른다'는 뜻이다.
    """
    return FIELD_ALIASES.get(_normalize_field_key(raw_field))


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
