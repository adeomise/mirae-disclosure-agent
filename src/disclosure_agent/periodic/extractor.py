"""정기공시 재무정보 추출 모듈.

역할:
- 파싱된 표와 주석에서 재무제표 항목, 수치, 단위, 기간 후보를 추출한다.

이 모듈은 목적이 다른 두 진입점을 함께 제공한다. 이름이 비슷하므로 주의한다.

    extract_financial_fact(document, metric=, fiscal_year=, accounting_scope=)
        -> FinancialFact
        질문에 대응하는 수치 하나를 고른다(targeted). ACODE로 항목을, ACONTEXT로
        연도와 연결/별도를 판정한다. 고를 근거가 없으면 추측하지 않고
        ExtractionBlockedError를 올려 상위 단계가 BLOCKED로 보고하게 한다.
        Q01(Pattern A)이 이 경로를 쓴다.

        FinancialFact는 Pattern A 내부의 추출 표현으로 사용한다.
        Retrieval / Agent / API / Evaluation으로 전달되는 최종 Integration output은
        공통 Contract의 Fact + Evidence를 사용하며, FinancialFact를 Fact/Evidence로
        변환하는 책임은 별도 adapter에서 담당한다.

    extract_financial_facts_with_evidence(parsed_document)
        -> (list[Fact], list[Evidence])
        문서 전체에서 Fact를 모은다(bulk). 항목은 한글 라벨로, 연도는 표 머리글로
        찾고 Contract v0.1의 Fact/Evidence를 만든다. 조건에 맞지 않는 셀은 막지
        않고 건너뛴다.

    두 경로는 현재 서로 다른 추출 신호를 사용한다.
    targeted는 XBRL 속성(ACODE/ACONTEXT)에, bulk는 표의 한글 텍스트와 머리글에
    주로 의존한다.

    두 진입점의 내부 구현을 즉시 하나로 합치지는 않는다. targeted의 FinancialFact는
    Pattern A 내부 표현으로 유지하고, 시스템 Integration boundary에서는
    공통 Contract의 Fact + Evidence를 사용한다.

입력 계약(Parser에 대한 요구사항):
    이 모듈은 ParsedDocument dict만 입력으로 받으며 원문 XML을 다시 열지 않는다.

        document["metadata"]     : doc_id / corp_name / report_name 등 문서 식별자
        document["sections"]     : section_id, section_path
        document["tables"][i]    : table_id, section_id, table_group_id, unit,
                                   own_unit, section_path, raw_context,
                                   attributes, rows
        document["table_groups"] : table_group_id, aclass, unit, tables
        cell                     : text, acode, acontext, anegated, provenance,
                                   rowspan, colspan

    parser는 cell.acontext와 표 단위, TABLE-GROUP의 ACLASS 등 추출에 필요한
    원문 정보를 보존한다. 따라서 조사 보고서의 D-2 / D-3 전제는 해소되었다.

    targeted 경로는 그 정보를 TABLE-GROUP까지 따라가 소비한다.

    - 단위: table["unit"]은 표 본문의 행을 단위 선언으로 잘못 집어올 수 있다.
      resolve_table_unit이 표 자신의 선언 -> TABLE-GROUP 선언 -> 표 상속 단위
      순서로 신뢰 가능한 선언을 고른다. parse_unit의 trusted 검사는 그대로 두며,
      모든 위치가 신뢰 불가능하면 값을 만들지 않고 막는다.

    - 표 분류: 본표/주석의 원문 근거는 table_groups[].aclass에 있다.
      classify_table이 표에 직접 붙은 표기를 먼저 보고 없으면 그룹 ACLASS를 읽는다.
      {XBRL} 이름공간의 접두 토큰만 해석하며(IS/BS/CF/EF -> 본표, NT -> 주석),
      모르는 표기는 분류하지 않는다.

출력:
- FinancialFact와 원문 위치 참조 (targeted, Pattern A 내부 표현)
- Contract v0.1 Fact / Evidence (bulk)
- 시스템 Integration boundary의 공통 출력은 Fact + Evidence를 기준으로 한다.

TODO:
- targeted FinancialFact -> Contract Fact/Evidence adapter 연결
- ACODE-less targeted fallback의 지원 지표 확장
- 당기순이익 등 지표 확장
- 분기·반기 컨텍스트(dQTR 등) 지원
- targeted / bulk 추출 신호의 공통화 검토
"""


from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from disclosure_agent.common.enums import (
    EvidenceLocationType,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import Evidence, EvidenceProvenance, Fact

# 지표·회계기준 어휘는 Contract v0.1의 enum을 그대로 쓴다.
#
# 이전에는 이 모듈이 같은 뜻의 문자열 상수를 따로 정의했다. 계약이 합의되기 전이라
# 그랬는데, common/enums.py가 들어오면서 그 전제가 없어졌다. StrEnum이므로 값 비교와
# dict key 사용은 문자열일 때와 동일하고, query_analyzer 등 기존 import 경로도 그대로
# 유지된다.
METRIC_REVENUE = MetricConcept.REVENUE
METRIC_OPERATING_INCOME = MetricConcept.OPERATING_INCOME

SCOPE_CONSOLIDATED = FactAccountingScope.CONSOLIDATED
SCOPE_SEPARATE = FactAccountingScope.SEPARATE


# ---------------------------------------------------------------------------
# targeted 추출: 질문에 대응하는 수치 하나를 고른다.
# ---------------------------------------------------------------------------

# 매출액에 대응하는 DART/IFRS 항목 코드.
REVENUE_ACODES: tuple[str, ...] = ("ifrs-full_Revenue",)

METRIC_ACODES: dict[str, tuple[str, ...]] = {
    METRIC_REVENUE: REVENUE_ACODES,
}

LABEL_FALLBACK_METRICS = {
    METRIC_REVENUE,
    METRIC_OPERATING_INCOME,
}

# ACONTEXT 안에서 연결/별도를 구분하는 축 멤버.
CONSOLIDATED_MEMBER = "ifrs-full_ConsolidatedMember"
SEPARATE_MEMBER = "ifrs-full_SeparateMember"

SCOPE_MEMBERS: dict[str, str] = {
    SCOPE_CONSOLIDATED: CONSOLIDATED_MEMBER,
    SCOPE_SEPARATE: SEPARATE_MEMBER,
}

# 같은 지표/연도/기준을 가진 값이 재무제표 본표와 주석에 동시에 존재할 수 있다.
# 이때 어느 표를 쓸지는 표의 성격으로만 정한다.
TABLE_CLASS_PRIMARY_STATEMENT = "primary_statement"
TABLE_CLASS_NOTE = "note"

# 값이 작을수록 우선한다.
TABLE_CLASS_PRIORITY: dict[str, int] = {
    TABLE_CLASS_PRIMARY_STATEMENT: 0,
    TABLE_CLASS_NOTE: 1,
}

# Parser output에서 표 분류를 읽을 위치 후보.
#
# parser는 TABLE-GROUP의 ACLASS를 table_groups[].aclass로 보존한다. 그 값은 표 자체가
# 아니라 그룹에 있으므로, 표에서 직접 읽는 경로와 그룹에서 읽는 경로를 모두 둔다.
TABLE_CLASS_FIELDS: tuple[str, ...] = ("table_group_class",)

# TABLE-GROUP의 ACLASS를 읽을 위치 후보.
TABLE_GROUP_CLASS_FIELDS: tuple[str, ...] = ("aclass",)

# Parser 표기를 내부 분류로 옮기는 잠정 대응표. 최종 Data Contract가 아니다.
# 여기에 없는 표기는 분류하지 않는다. 모르는 값을 본표로 간주하지 않기 위해서다.
TABLE_CLASS_ALIASES: dict[str, str] = {
    "financial_statement": TABLE_CLASS_PRIMARY_STATEMENT,
    "primary_statement": TABLE_CLASS_PRIMARY_STATEMENT,
    "note": TABLE_CLASS_NOTE,
    "footnote": TABLE_CLASS_NOTE,
}

# 원문 TABLE-GROUP ACLASS의 XBRL 이름공간 표기. 예: "{XBRL}IS_C1", "{XBRL}NT_C_D831150"
#
# 이 접두사를 요구하는 이유:
#     같은 문서에 DART 서식 고유의 ACLASS(INC_STAT, SUB_BLC, IAM_TBL1 등)가 함께 있다.
#     그것들은 XBRL 재무제표 표가 아니므로 재무제표 어휘로 읽으면 안 된다. 실측 corpus
#     annual 47종에서 XBRL 아닌 ACLASS만 89종이었고 INC_STAT처럼 재무제표로 오인하기
#     쉬운 표기도 있었다. 이름공간을 확인해 그 경로를 아예 열지 않는다.
XBRL_ACLASS_PREFIX = "{XBRL}"

# ACLASS 접두 토큰(첫 '_' 앞)이 표의 성격을 가른다. 실측 corpus의 {XBRL} ACLASS는
# 173종이었지만 접두 토큰은 다섯 개뿐이었다: IS(6) BS(2) CF(2) EF(2) NT(161).
#
#     IS 손익계산서 / BS 재무상태표 / CF 현금흐름표 / EF 자본변동표 -> 본표
#     NT 주석                                                    -> 주석
#
# 접미부(_C1, _S2, _C_D831150 등)는 연결/별도와 서식 변형을 나타낼 뿐이라 읽지 않는다.
# 여기에 없는 접두 토큰은 분류하지 않는다. 새 표기를 본표로 넘겨짚지 않기 위해서다.
XBRL_ACLASS_TOKEN_PATTERN = re.compile(r"^([A-Za-z]+)")

XBRL_ACLASS_CLASSES: dict[str, str] = {
    "IS": TABLE_CLASS_PRIMARY_STATEMENT,
    "BS": TABLE_CLASS_PRIMARY_STATEMENT,
    "CF": TABLE_CLASS_PRIMARY_STATEMENT,
    "EF": TABLE_CLASS_PRIMARY_STATEMENT,
    "NT": TABLE_CLASS_NOTE,
}

# 예: "CFY2025dFY" + "_" + "<...>Axis_<...>ConsolidatedMember"
#      ^prefix ^year ^period      ^axes
ACONTEXT_PATTERN = re.compile(
    r"^(?P<prefix>[A-Z]+)FY(?P<year>\d{4})(?P<period>[A-Za-z]+)(?:_(?P<axes>.+))?$"
)

AXIS_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9\-]+_[A-Za-z0-9]*(?:Axis|Member)")

# 기간 전체(연간)를 뜻하는 컨텍스트 접미사. 기말 시점(eFY)과 구분한다.
DURATION_PERIOD_CODE = "dFY"

UNIT_DECLARATION_PATTERN = re.compile(r"단위\s*[:：]\s*([^)\]]*)")

# 표 단위 문자열이 '단위 선언'만 담고 있는지 확인한다.
CLEAN_UNIT_PATTERN = re.compile(r"^[(\[]?\s*단위\s*[:：][^()\[\]]*[)\]]?$")

# targeted 경로가 쓰는 통화 배수표.
#
# 아래 bulk 경로의 UNIT_MULTIPLIERS와 이름이 겹쳐서 이쪽을 CURRENCY_MULTIPLIERS로
# 분리했다. 둘은 수록 단위(만원·십억원·조원의 유무)와 값 타입(int / Decimal)이 다르고,
# 곱셈 결과를 쓰는 방식도 다르다. 하나로 합치는 것은 두 경로의 단위 처리를 통합할 때
# 함께 다룬다.
CURRENCY_MULTIPLIERS: dict[str, int] = {
    "원": 1,
    "천원": 1_000,
    "만원": 10_000,
    "백만원": 1_000_000,
    "십억원": 1_000_000_000,
    "억원": 100_000_000,
    "조원": 1_000_000_000_000,
}

NEGATIVE_PREFIXES = ("△", "▲", "-", "−", "–", "—")

NUMERIC_PATTERN = re.compile(r"^\d+(?:\.\d+)?$")

BASE_CURRENCY_UNIT = "원"


class ExtractionBlockedError(RuntimeError):
    """추출에 필요한 입력이 parser output에 없어 진행할 수 없는 경우.

    값을 추측하지 않고 상위 단계가 BLOCKED로 보고할 수 있게 한다.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)

        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class AccountingContext:
    """ACONTEXT 문자열을 구조화한 결과."""

    raw: str
    prefix: str
    fiscal_year: int
    period_code: str
    accounting_scope: str | None
    axes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def axis_depth(self) -> int:
        """부가 축(Axis)의 개수. 값이 작을수록 더 집계된 수치다."""
        return sum(1 for axis in self.axes if axis.endswith("Axis"))

    @property
    def is_full_year_duration(self) -> bool:
        return self.period_code == DURATION_PERIOD_CODE


@dataclass(frozen=True)
class UnitSpec:
    """표 단위 문자열 해석 결과."""

    text: str
    token: str
    multiplier: int
    trusted: bool


@dataclass(frozen=True)
class FactLocation:
    """Fact가 나온 원문 위치."""

    section_id: str | None
    section_path: tuple[str, ...]
    table_id: str
    row_index: int
    column_index: int
    label_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FinancialFact:
    """원문에서 추출한 단일 재무 수치."""

    metric: str
    acode: str | None
    accounting_scope: str
    fiscal_year: int
    raw_value: str
    value: Decimal
    unit_text: str
    unit_token: str
    unit_multiplier: int
    value_in_base_unit: Decimal
    base_unit: str
    acontext: str | None
    location: FactLocation

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "acode": self.acode,
            "accounting_scope": self.accounting_scope,
            "fiscal_year": self.fiscal_year,
            "raw_value": self.raw_value,
            "value": str(self.value),
            "unit_text": self.unit_text,
            "unit_token": self.unit_token,
            "unit_multiplier": self.unit_multiplier,
            "value_in_base_unit": str(self.value_in_base_unit),
            "base_unit": self.base_unit,
            "acontext": self.acontext,
            "location": self.location.to_dict(),
        }


def parse_accounting_context(raw: str) -> AccountingContext | None:
    """
    ACONTEXT 문자열에서 회계연도와 연결/별도 구분을 읽는다.

    예:
        "CFY2025dFY_..._ifrs-full_ConsolidatedMember"
        -> fiscal_year=2025, period_code="dFY", accounting_scope="consolidated"
    """
    if not raw:
        return None

    match = ACONTEXT_PATTERN.match(raw.strip())

    if not match:
        return None

    axes_text = match.group("axes") or ""

    axes = tuple(AXIS_SEGMENT_PATTERN.findall(axes_text))

    scope: str | None = None

    if CONSOLIDATED_MEMBER in axes:
        scope = SCOPE_CONSOLIDATED

    elif SEPARATE_MEMBER in axes:
        scope = SCOPE_SEPARATE

    return AccountingContext(
        raw=raw.strip(),
        prefix=match.group("prefix"),
        fiscal_year=int(match.group("year")),
        period_code=match.group("period"),
        accounting_scope=scope,
        axes=axes,
    )


def parse_unit(unit_text: str | None) -> UnitSpec | None:
    """
    표 단위 문자열에서 통화 단위와 배수를 읽는다.

    '(단위 : 백만원, %, 회)'처럼 여러 단위가 나열되면 첫 통화 단위를 쓴다.

    unit_text가 단위 선언 외의 내용까지 담고 있으면 trusted=False로 표시한다.
    표 안의 다른 행에서 '단위'라는 낱말을 잘못 집어온 경우를 걸러내기 위한 방어 장치다
    (조사 보고서 D-3).
    """
    if not unit_text:
        return None

    text = unit_text.strip()

    match = UNIT_DECLARATION_PATTERN.search(text)

    if not match:
        return None

    token: str | None = None

    for candidate in match.group(1).split(","):
        candidate = candidate.strip()

        if candidate in CURRENCY_MULTIPLIERS:
            token = candidate
            break

    if token is None:
        return None

    return UnitSpec(
        text=text,
        token=token,
        multiplier=CURRENCY_MULTIPLIERS[token],
        trusted=bool(CLEAN_UNIT_PATTERN.match(text)),
    )


def parse_numeric_value(raw: str, negated: bool = False) -> Decimal | None:
    """
    표 셀의 원문 문자열을 수치로 바꾼다.

    '1,234' / '(1,234)' / '△1,234' 형태를 처리하며
    변환할 수 없으면 None을 돌려준다.
    """
    if raw is None:
        return None

    text = str(raw).strip()

    if not text:
        return None

    negative = bool(negated)

    if text.startswith("(") and text.endswith(")"):
        negative = not negative
        text = text[1:-1].strip()

    for prefix in NEGATIVE_PREFIXES:
        if text.startswith(prefix):
            negative = not negative
            text = text[len(prefix):].strip()
            break

    text = text.replace(",", "").replace(" ", "").replace(" ", "")

    if not NUMERIC_PATTERN.match(text):
        return None

    try:
        value = Decimal(text)

    except InvalidOperation:
        return None

    return -value if negative else value


def _row_label(row: list[dict[str, Any]]) -> str:
    """행의 첫 텍스트 셀을 항목명으로 본다."""
    for cell in row:
        text = (cell.get("text") or "").strip()

        if text:
            return text

    return ""


def normalize_table_class(raw: str | None) -> str | None:
    """
    Parser 표기 하나를 내부 분류(본표/주석)로 바꾼다.

    두 표기를 받는다.

        잠정 별칭      "financial_statement", "note" 등. synthetic fixture가 쓴다.
        원문 ACLASS    "{XBRL}IS_C1", "{XBRL}NT_C_D831150" 등. 실제 parser output이 쓴다.

    모르는 표기이면 None이다. None은 '주석이 아니다'가 아니라 '판단하지 않는다'는 뜻이다.
    """
    if not raw:
        return None

    text = str(raw).strip()

    alias = TABLE_CLASS_ALIASES.get(text.casefold())

    if alias is not None:
        return alias

    if not text.startswith(XBRL_ACLASS_PREFIX):
        # XBRL 이름공간이 아니면 재무제표 어휘로 읽지 않는다.
        return None

    token = XBRL_ACLASS_TOKEN_PATTERN.match(text[len(XBRL_ACLASS_PREFIX) :])

    if token is None:
        return None

    return XBRL_ACLASS_CLASSES.get(token.group(1).upper())


def classify_table(
    table: dict[str, Any],
    table_group: dict[str, Any] | None = None,
) -> str | None:
    """
    Parser가 준 표 metadata를 내부 분류(본표/주석)로 바꾼다.

    표 자체의 표기를 먼저 보고, 없으면 표가 속한 TABLE-GROUP의 ACLASS를 본다. 실제
    parser output에서 근거는 그룹에만 있으므로 그룹을 넘겨야 판정이 선다. 표에 직접 붙은
    표기를 우선하는 이유는 그쪽이 더 구체적인 선언이기 때문이다.

    근거가 없거나 모르는 표기이면 None을 돌려준다.
    None은 '주석이 아니다'가 아니라 '판단하지 않는다'는 뜻이다.

    section 제목이나 문서 순서로 본표 여부를 유추하지 않는다.
    """
    attributes = table.get("attributes") or {}

    for field_name in TABLE_CLASS_FIELDS:
        raw = table.get(field_name) or attributes.get(field_name)

        if raw:
            return normalize_table_class(raw)

    if table_group is None:
        return None

    group_attributes = table_group.get("attributes") or {}

    for field_name in TABLE_GROUP_CLASS_FIELDS:
        raw = table_group.get(field_name) or group_attributes.get(field_name)

        if raw:
            return normalize_table_class(raw)

    return None


def table_class_priority(table_class: str | None) -> int | None:
    """내부 분류에 대응하는 우선순위. 분류를 모르면 None."""
    if table_class is None:
        return None

    return TABLE_CLASS_PRIORITY.get(table_class)


def build_table_group_index(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    table_id로 그 표가 속한 TABLE-GROUP을 찾을 수 있게 한다.

    parser는 같은 표 dict을 table_groups[].tables와 flat tables 양쪽에 넣는다. 그래서
    flat tables를 순회하는 쪽도 이 색인으로 그룹 metadata(단위, ACLASS)에 닿을 수 있다.
    """
    index: dict[str, dict[str, Any]] = {}

    for table_group in document.get("table_groups") or []:
        for table in table_group.get("tables") or []:
            table_id = table.get("table_id")

            if table_id and table_id not in index:
                index[str(table_id)] = table_group

    return index


def unit_declaration_sources(
    table: dict[str, Any],
    table_group: dict[str, Any] | None,
) -> tuple[str | None, ...]:
    """
    단위 선언이 있을 수 있는 위치를 우선순위대로 돌려준다.

    구조적으로 '단위 선언'인 자리만 본다. 숫자 셀이나 본문 문장에서 단위를 유추하지 않는다.

        table["own_unit"]     표 자신이 선언한 단위. 가장 구체적이다.
        table_group["unit"]   표가 속한 그룹이 선언한 단위. 본표 묶음이 여기에 선언한다.
        table["unit"]         own_unit or inherited_unit. 그룹이 없는 독립 표를 위한 자리.

    own_unit을 table["unit"]보다 먼저 보는 이유는 둘이 다른 값일 수 없어서가 아니라,
    table["unit"]이 own_unit을 이미 흡수해 그룹 단위를 가리기 때문이다. parser의
    detect_table_unit은 표 자손 전체에서 '단위'가 든 문자열을 찾으므로, 손익계산서의
    '기본주당이익 (단위 : 원) ...' 행을 표 단위로 잘못 집어온다. 그 값이 table["unit"]에
    들어가면 그룹이 선언한 '(단위 : 백만원)'을 덮는다. 순서를 이렇게 두면 오염된 값이
    trusted 검사에서 걸러진 뒤 그룹 선언으로 넘어간다.
    """
    sources: list[str | None] = [table.get("own_unit")]

    if table_group is not None:
        sources.append(table_group.get("unit"))

    sources.append(table.get("unit"))

    return tuple(sources)


def resolve_table_unit(
    table: dict[str, Any],
    table_group: dict[str, Any] | None = None,
) -> tuple[UnitSpec | None, tuple[UnitSpec, ...]]:
    """
    표에 적용할 신뢰 가능한 단위를 고른다.

    반환:
        (신뢰 가능한 UnitSpec 또는 None, 신뢰할 수 없어 건너뛴 UnitSpec 목록)

    신뢰 검사(UnitSpec.trusted)는 그대로 둔다. 오염된 문자열을 대신 쓰지 않고 다음
    선언 위치로 넘어갈 뿐이며, 모든 위치가 비었거나 신뢰할 수 없으면 None을 돌려준다.
    호출자는 그때 막는다. 건너뛴 목록을 함께 돌려주는 이유는 '단위 선언이 아예 없었다'와
    '있었지만 신뢰할 수 없었다'를 호출자가 구분해 보고하기 위해서다.
    """
    rejected: list[UnitSpec] = []

    for raw in unit_declaration_sources(table, table_group):
        unit = parse_unit(raw)

        if unit is None:
            continue

        if unit.trusted:
            return unit, tuple(rejected)

        rejected.append(unit)

    return None, tuple(rejected)


@dataclass(frozen=True)
class _Candidate:
    table: dict[str, Any]
    row_index: int
    column_index: int
    cell: dict[str, Any]
    context: AccountingContext
    label: str
    # 표가 속한 TABLE-GROUP. 단위 선언과 ACLASS가 여기에 있다. 독립 표이면 None.
    table_group: dict[str, Any] | None = None

    @property
    def table_class(self) -> str | None:
        return classify_table(self.table, self.table_group)


def _collect_candidates(
    document: dict[str, Any],
    acodes: tuple[str, ...],
) -> tuple[list[_Candidate], int]:
    """
    지정된 ACODE를 가진 셀을 모은다.

    반환:
        (ACONTEXT까지 해석된 후보 목록, ACODE는 맞았던 셀의 총 개수)
    """
    candidates: list[_Candidate] = []

    group_index = build_table_group_index(document)

    acode_hits = 0

    for table in document.get("tables", []):
        table_group = group_index.get(str(table.get("table_id")))

        rows = table.get("rows") or []

        for row_index, row in enumerate(rows):
            label = _row_label(row)

            for column_index, cell in enumerate(row):
                if cell.get("acode") not in acodes:
                    continue

                acode_hits += 1

                context = parse_accounting_context(cell.get("acontext") or "")

                if context is None:
                    continue

                candidates.append(
                    _Candidate(
                        table=table,
                        row_index=row_index,
                        column_index=column_index,
                        cell=cell,
                        context=context,
                        label=label,
                        table_group=table_group,
                    )
                )

    return candidates, acode_hits


def _describe_candidate(document: dict[str, Any], candidate: _Candidate) -> str:
    """후보를 값 없이 위치 정보만으로 요약한다. 막힌 이유를 사람이 읽기 위한 문자열이다."""
    section_id = candidate.table.get("section_id")

    section_path = _lookup_section_path(document, section_id)

    path_text = " > ".join(section_path) if section_path else "section 경로 없음"

    table_class = candidate.table_class or "unclassified"

    return f"{candidate.table.get('table_id')}[{table_class}] {section_id}({path_text})"


def _select_candidate(
    document: dict[str, Any],
    matched: list[_Candidate],
    *,
    metric: str,
    fiscal_year: int,
    accounting_scope: str,
) -> _Candidate:
    """
    조건을 만족하는 후보 중 하나를 고른다.

    1. 부가 축이 가장 적은(가장 집계된) 후보만 남긴다.
    2. 그래도 여럿이면 표 분류로 본표를 주석보다 우선한다.
    3. 분류 근거가 없거나 우선순위가 같으면 고르지 않고 막는다.

    문서 순회 순서, 행 번호, 값의 크기, 단위로는 고르지 않는다.
    실제 사업보고서에는 본표와 주석 합계가 같은 ACONTEXT와 같은 행 번호로 동시에
    존재하는 경우가 있었고, 그때 순서로 고르면 근거 없이 주석 값을 정답으로 답하게 된다.
    """
    min_depth = min(candidate.context.axis_depth for candidate in matched)

    shortlist = [
        candidate for candidate in matched if candidate.context.axis_depth == min_depth
    ]

    if len(shortlist) == 1:
        return shortlist[0]

    priorities = [table_class_priority(c.table_class) for c in shortlist]

    if all(priority is not None for priority in priorities):
        best = min(priority for priority in priorities if priority is not None)

        preferred = [
            candidate
            for candidate, priority in zip(shortlist, priorities, strict=True)
            if priority == best
        ]

        if len(preferred) == 1:
            return preferred[0]

    summary = " | ".join(_describe_candidate(document, c) for c in shortlist)

    raise ExtractionBlockedError(
        reason="ambiguous_candidates",
        detail=(
            f"{fiscal_year} 회계연도 / {accounting_scope} 기준 {metric} 후보가 "
            f"{len(shortlist)}개 남았지만 하나를 고를 근거가 없습니다. "
            "표 분류 정보가 없거나 후보들의 우선순위가 같습니다. "
            "Parser가 표를 본표와 주석으로 구분할 수 있는 정보를 제공해야 합니다. "
            f"후보: {summary}"
        ),
    )


def extract_financial_fact(
    document: dict[str, Any],
    *,
    metric: str,
    fiscal_year: int,
    accounting_scope: str,
) -> FinancialFact:
    """
    parsed document에서 단일 재무 수치를 추출한다.

    선택 규칙:
    1. metric에 대응하는 ACODE를 가진 셀만 본다.
    2. ACONTEXT의 회계연도와 연결/별도 구분이 요청과 일치해야 한다.
    3. 연간 기간(dFY) 컨텍스트만 쓴다.
    4. 부가 축이 가장 적은(가장 집계된) 후보만 남긴다.
    5. 남은 후보가 여럿이면 표 분류로 본표를 우선하고, 구분할 수 없으면 막는다.
    6. 단위는 표와 TABLE-GROUP의 선언에서만 고른다. 신뢰 가능한 선언이 없으면 막는다.
    """

    if accounting_scope not in SCOPE_MEMBERS:
        raise ExtractionBlockedError(
            reason="unsupported_accounting_scope",
            detail=f"지원하지 않는 회계 기준입니다: {accounting_scope}",
        )

    acodes = METRIC_ACODES.get(metric)

    if acodes is None:
        if metric in LABEL_FALLBACK_METRICS:
            return _extract_financial_fact_from_label_fallback(
                document,
                metric=metric,
                fiscal_year=fiscal_year,
                accounting_scope=accounting_scope,
            )

        raise ExtractionBlockedError(
            reason="unsupported_metric",
            detail=f"지원하지 않는 지표입니다: {metric}",
        )

    candidates, acode_hits = _collect_candidates(document, acodes)

    if acode_hits == 0:
        if metric in LABEL_FALLBACK_METRICS:
            return _extract_financial_fact_from_label_fallback(
                document,
                metric=metric,
                fiscal_year=fiscal_year,
                accounting_scope=accounting_scope,
            )

        raise ExtractionBlockedError(
            reason="no_metric_acode",
            detail=(
                f"parsed document에서 ACODE {list(acodes)} 를 가진 셀을 찾지 못했습니다."
            ),
        )

    if not candidates:
        raise ExtractionBlockedError(
            reason="missing_acontext",
            detail=(
                f"ACODE {list(acodes)} 셀 {acode_hits}개를 찾았지만 해석 가능한 ACONTEXT가 "
                "없습니다. Parser가 cell.acontext를 보존해야 연도와 연결/별도 기준을 "
                "결정론적으로 판정할 수 있습니다."
            ),
        )

    matched = [
        candidate
        for candidate in candidates
        if candidate.context.fiscal_year == fiscal_year
        and candidate.context.accounting_scope == accounting_scope
        and candidate.context.is_full_year_duration
    ]

    if not matched:
        raise ExtractionBlockedError(
            reason="no_matching_context",
            detail=(
                f"{fiscal_year} 회계연도 / {accounting_scope} 기준에 해당하는 "
                f"{metric} 컨텍스트를 찾지 못했습니다."
            ),
        )

    selected = _select_candidate(
        document,
        matched,
        metric=metric,
        fiscal_year=fiscal_year,
        accounting_scope=accounting_scope,
    )

    unit, rejected_units = resolve_table_unit(selected.table, selected.table_group)

    if unit is None and rejected_units:
        raise ExtractionBlockedError(
            reason="untrusted_table_unit",
            detail=(
                f"표 {selected.table.get('table_id')}에 신뢰할 수 있는 단위 선언이 "
                "없습니다. 표 본문에서 잘못 수집된 것으로 보이는 문자열만 있고 표나 "
                "TABLE-GROUP에 정상 선언이 없습니다: "
                f"{[spec.text for spec in rejected_units]}. 그대로 쓰면 자릿수 오류가 "
                "발생합니다."
            ),
        )

    if unit is None:
        raise ExtractionBlockedError(
            reason="missing_table_unit",
            detail=(
                f"표 {selected.table.get('table_id')}에 해석 가능한 단위가 없습니다. "
                "Parser가 표 단위를 표 또는 TABLE-GROUP 단위로 귀속해 제공해야 합니다."
            ),
        )

    raw_value = (selected.cell.get("text") or "").strip()

    negated = str(selected.cell.get("anegated", "")).upper() == "Y"

    value = parse_numeric_value(raw_value, negated=negated)

    if value is None:
        raise ExtractionBlockedError(
            reason="unparsable_value",
            detail=f"셀 값을 수치로 변환하지 못했습니다: {raw_value!r}",
        )

    section_path = _lookup_section_path(document, selected.table.get("section_id"))

    return FinancialFact(
        metric=metric,
        acode=str(selected.cell.get("acode")),
        accounting_scope=accounting_scope,
        fiscal_year=fiscal_year,
        raw_value=raw_value,
        value=value,
        unit_text=unit.text,
        unit_token=unit.token,
        unit_multiplier=unit.multiplier,
        value_in_base_unit=value * unit.multiplier,
        base_unit=BASE_CURRENCY_UNIT,
        acontext=selected.context.raw,
        location=FactLocation(
            section_id=selected.table.get("section_id"),
            section_path=section_path,
            table_id=str(selected.table.get("table_id")),
            row_index=selected.row_index,
            column_index=selected.column_index,
            label_text=selected.label,
        ),
    )


def _lookup_section_path(document: dict[str, Any], section_id: str | None) -> tuple[str, ...]:
    if not section_id:
        return ()

    for section in document.get("sections", []):
        if section.get("section_id") == section_id:
            return tuple(section.get("section_path") or ())

    return ()


def extract_revenue_fact(
    document: dict[str, Any],
    *,
    fiscal_year: int,
    accounting_scope: str = SCOPE_CONSOLIDATED,
) -> FinancialFact:
    """매출액 Fact를 추출한다."""
    return extract_financial_fact(
        document,
        metric=METRIC_REVENUE,
        fiscal_year=fiscal_year,
        accounting_scope=accounting_scope,
    )


# ---------------------------------------------------------------------------
# bulk 추출: 문서 전체에서 Contract v0.1의 Fact / Evidence를 만든다.
# ---------------------------------------------------------------------------

METRIC_ALIASES: dict[MetricConcept, set[str]] = {
    MetricConcept.REVENUE: {
        "매출",
        "매출액",
        "수익(매출액)",
        "영업수익",
    },
    MetricConcept.OPERATING_INCOME: {
        "Ⅴ.영업이익",
        "Ⅲ.영업이익(손실)",
        "영업이익",
        "영업이익(손실)",
        "영업손익",
    },
}

NOTE_PATTERN = re.compile(r"\s*\(주\s*\d[^)]*\)\s*$")
NUMBER_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?$")
FISCAL_YEAR_PATTERN = re.compile(
    r"제\s*(?P<term>\d+)\s*기(?:\s*(?:반기|분기))?\s*(?P<year>20\d{2})"
)
TERM_PATTERN = re.compile(r"제\s*(?P<term>\d+)\s*기")
YEAR_PATTERN = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
UNIT_PATTERN = re.compile(r"단위\s*:\s*([^)]+)")
FINANCIAL_CONTEXT_MARKERS = ("재무제표", "손익계산서", "요약재무정보")

# bulk 경로가 쓰는 통화 배수표. 위 CURRENCY_MULTIPLIERS와 수록 단위·값 타입이 다르다.
UNIT_MULTIPLIERS = {
    "원": Decimal(1),
    "천원": Decimal(1_000),
    "백만원": Decimal(1_000_000),
    "억원": Decimal(100_000_000),
}


def cell_text(cell: Any) -> str:
    """
    parser.py의 table cell이
    문자열이거나 {"type": ..., "text": ...} 형태여도
    text만 꺼낼 수 있게 처리한다.
    """
    if isinstance(cell, dict):
        return str(cell.get("text", "")).strip()

    return str(cell).strip()


def normalize_number(value: str) -> str:
    """
    숫자 문자열 최소 정규화.
    예: '1,234,567' -> '1234567'
    """
    return value.replace(",", "").strip()


def normalize_metric_label(label: str) -> str:
    """주석 번호와 공백만 제거해 제한적인 exact label 비교에 사용한다."""
    without_note = NOTE_PATTERN.sub("", label.strip())
    return "".join(without_note.split())


def match_metric(label: str) -> MetricConcept | None:
    normalized = normalize_metric_label(label)
    for concept, aliases in METRIC_ALIASES.items():
        if normalized in {normalize_metric_label(alias) for alias in aliases}:
            return concept
    return None


def parse_raw_number(raw_value: str) -> Decimal | None:
    """원문을 바꾸지 않고 별도의 deterministic normalization 값만 계산한다."""
    candidate = normalize_number(raw_value)
    negative = candidate.startswith("(") and candidate.endswith(")")
    if negative:
        candidate = candidate[1:-1].strip()
    if not NUMBER_PATTERN.fullmatch(candidate):
        return None
    try:
        number = Decimal(candidate)
    except InvalidOperation:
        return None
    return -number if negative else number


def extract_raw_unit(unit_context: str | None) -> str | None:
    if not unit_context:
        return None
    match = UNIT_PATTERN.search(unit_context)
    return match.group(1).strip() if match else unit_context.strip()

@dataclass(frozen=True)
class SourceUnitResolution:
    raw_unit: str | None
    declaration: str | None
    source: str | None
    multiplier: int | Decimal | None
    trusted: bool | None

def _build_source_unit_resolution(
    declaration: str | None,
    source: str,
) -> SourceUnitResolution:
    if not declaration:
        return SourceUnitResolution(
            raw_unit=None,
            declaration=None,
            source=None,
            multiplier=None,
            trusted=None,
        )

    raw_unit = extract_raw_unit(declaration)

    if raw_unit is None:
        return SourceUnitResolution(
            raw_unit=None,
            declaration=declaration,
            source=source,
            multiplier=None,
            trusted=None,
        )

    # 기존 targeted 경로의 신뢰도 판정이 가능한 통화 단위라면
    # 동일한 판정 기준을 재사용한다.
    parsed_unit = parse_unit(declaration)

    if parsed_unit is not None:
        multiplier: int | Decimal | None = parsed_unit.multiplier
        trusted: bool | None = parsed_unit.trusted
    else:
        multiplier = UNIT_MULTIPLIERS.get(raw_unit)
        trusted = None

    return SourceUnitResolution(
        raw_unit=raw_unit,
        declaration=declaration,
        source=source,
        multiplier=multiplier,
        trusted=trusted,
    )

def resolve_source_unit_details(
    table: dict[str, Any],
    table_group: dict[str, Any] | None,
) -> SourceUnitResolution:
    """bulk 단위와 그 단위를 선택한 provenance를 함께 반환한다."""

    fallback_resolution: SourceUnitResolution | None = None

    for row in table.get("rows", []):
        if len(row) != 1:
            break

        text = cell_text(row[0])

        if "단위" in text:
            resolution = _build_source_unit_resolution(
                text,
                "table_row",
            )
            if resolution.trusted is True:
                return resolution
            if resolution.raw_unit is not None:
                fallback_resolution = resolution

    if table_group is not None:
        resolution = _build_source_unit_resolution(
            table_group.get("unit"),
            "table_group",
        )
        if resolution.trusted is True:
            return resolution
        if fallback_resolution is None and resolution.raw_unit is not None:
            fallback_resolution = resolution

    resolution = _build_source_unit_resolution(
        table.get("unit"),
        "table",
    )
    if resolution.trusted is True:
        return resolution
    if fallback_resolution is None and resolution.raw_unit is not None:
        fallback_resolution = resolution

    return fallback_resolution or resolution

def resolve_source_unit(
    table: dict[str, Any],
    table_group: dict[str, Any] | None,
) -> str | None:
    """행별 단위 문구를 표 전체 단위로 오인하지 않고 같은 source 안에서 결정한다."""
    return resolve_source_unit_details(
        table,
        table_group,
    ).raw_unit


def normalize_value_and_unit(
    raw_value: str,
    raw_unit: str | None,
) -> tuple[str | None, str | None]:
    number = parse_raw_number(raw_value)
    if number is None or raw_unit is None:
        return None, None
    if raw_unit in UNIT_MULTIPLIERS:
        normalized = number * UNIT_MULTIPLIERS[raw_unit]
        return format(normalized, "f"), "KRW"
    if raw_unit == "주":
        return format(number, "f"), "shares"
    if raw_unit == "%":
        return format(number, "f"), "percent"
    return None, None


def infer_accounting_scope(
    table: dict[str, Any],
    table_group: dict[str, Any] | None,
) -> FactAccountingScope:
    """명시적 문구와 XBRL ACLASS가 일치하는 강한 signal만 사용한다."""
    group = table_group or {}
    context_parts = [
        *table.get("section_path", []),
        group.get("title"),
        group.get("raw_context"),
        table.get("title"),
        table.get("raw_context"),
    ]
    context = " ".join(str(part) for part in context_parts if part)
    aclass = str(group.get("aclass") or table.get("attributes", {}).get("aclass") or "")

    if "연결" in context or re.search(r"_C(?:_|\d|$)", aclass):
        return FactAccountingScope.CONSOLIDATED
    if any(marker in context for marker in ("별도", "개별")) or re.search(
        r"_S(?:_|\d|$)", aclass
    ):
        return FactAccountingScope.SEPARATE
    return FactAccountingScope.UNKNOWN


def is_financial_context(
    table: dict[str, Any],
    table_group: dict[str, Any] | None,
) -> bool:
    group = table_group or {}
    context = " ".join(
        str(part)
        for part in (
            *table.get("section_path", []),
            group.get("title"),
            group.get("raw_context"),
            table.get("title"),
            table.get("raw_context"),
        )
        if part
    )
    aclass = str(group.get("aclass") or "")
    return any(marker in context for marker in FINANCIAL_CONTEXT_MARKERS) or aclass.startswith(
        "{XBRL}"
    )


def build_logical_rows(rows: list[list[dict[str, Any]]]) -> list[dict[int, dict[str, Any]]]:
    """ROWSPAN/COLSPAN을 반영해 셀의 논리 column 위치를 복원한다."""
    logical_rows: list[dict[int, dict[str, Any]]] = []
    occupied: dict[int, int] = {}
    for row in rows:
        logical: dict[int, dict[str, Any]] = {}
        col_index = 0
        for cell in row:
            while occupied.get(col_index, 0) > 0:
                col_index += 1
            colspan = max(int(cell.get("colspan", 1)), 1)
            rowspan = max(int(cell.get("rowspan", 1)), 1)
            for offset in range(colspan):
                logical[col_index + offset] = cell
                if rowspan > 1:
                    occupied[col_index + offset] = rowspan
            col_index += colspan
        logical_rows.append(logical)
        occupied = {
            column: remaining - 1
            for column, remaining in occupied.items()
            if remaining > 1
        }
    return logical_rows


def header_labels_by_column(
    logical_rows: list[dict[int, dict[str, Any]]],
    metric_row_index: int,
) -> dict[int, str]:
    labels: dict[int, list[str]] = {}
    for row in logical_rows[:metric_row_index]:
        for column, cell in row.items():
            if cell.get("type") != "TH":
                continue
            text = cell_text(cell)
            if text and text not in labels.setdefault(column, []):
                labels[column].append(text)
    return {column: " ".join(parts) for column, parts in labels.items()}


def fiscal_term_years(table_group: dict[str, Any] | None) -> dict[str, int]:
    if table_group is None:
        return {}
    context = " ".join(
        str(value)
        for value in (
            table_group.get("unit"),
            table_group.get("title"),
            table_group.get("raw_context"),
        )
        if value
    )
    for table in table_group.get("tables", []):
        context += " " + " ".join(cell_text(cell) for row in table.get("rows", []) for cell in row)
    return {
        match.group("term"): int(match.group("year"))
        for match in FISCAL_YEAR_PATTERN.finditer(context)
    }


def infer_period(
    column_label: str,
    term_years: dict[str, int],
    report_name: str | None,
) -> tuple[int | None, str | None]:
    year_match = YEAR_PATTERN.search(column_label)
    fiscal_year = int(year_match.group(1)) if year_match else None
    term_match = TERM_PATTERN.search(column_label)
    if fiscal_year is None and term_match:
        fiscal_year = term_years.get(term_match.group("term"))

    if "3개월" in column_label:
        fiscal_period = "quarter"
    elif "누적" in column_label:
        fiscal_period = "half-year"
    elif report_name and "반기" in report_name:
        fiscal_period = "half-year"
    elif report_name and "분기" in report_name:
        fiscal_period = "quarter"
    elif report_name and "사업보고서" in report_name:
        fiscal_period = "annual"
    else:
        fiscal_period = None
    return fiscal_year, fiscal_period


def iter_unique_tables(
    parsed_document: dict[str, Any],
):
    """TABLE-GROUP을 우선 사용하고 flat tables는 standalone fallback으로 순회한다."""
    seen_table_ids: set[str] = set()
    for table_group in parsed_document.get("table_groups", []):
        for table in table_group.get("tables", []):
            table_id = table.get("table_id")
            if table_id and table_id not in seen_table_ids:
                seen_table_ids.add(table_id)
                yield table, table_group
    for table in parsed_document.get("tables", []):
        table_id = table.get("table_id")
        if table_id and table_id not in seen_table_ids:
            seen_table_ids.add(table_id)
            yield table, None


def extract_financial_facts_with_evidence(
    parsed_document: dict[str, Any],
) -> tuple[list[Fact], list[Evidence]]:
    metadata = parsed_document.get("metadata", {})
    doc_id = str(metadata.get("doc_id") or "")
    company = str(metadata.get("corp_name") or "")
    report_name = metadata.get("report_name")
    if not doc_id or not company:
        raise ValueError("Fact 생성에는 metadata.doc_id와 metadata.corp_name이 필요합니다.")

    facts: list[Fact] = []
    evidence: list[Evidence] = []
    seen_sources: set[tuple[str, str, str, int, int]] = set()

    for table, table_group in iter_unique_tables(parsed_document):
        if not is_financial_context(table, table_group):
            continue
        table_id = str(table["table_id"])
        rows = table.get("rows", [])
        logical_rows = build_logical_rows(rows)
        term_years = fiscal_term_years(table_group)
        unit_resolution = resolve_source_unit_details(
            table,
            table_group,
        )
        if unit_resolution.trusted is not True:
            continue
        raw_unit = unit_resolution.raw_unit

        accounting_scope = infer_accounting_scope(
            table,
            table_group,
        )

        group = table_group or {}

        aclass = (
            group.get("aclass")
            or table.get("attributes", {}).get("aclass")
        )

        table_classification = classify_table(
            table,
            table_group,
        )

        for row_index, (row, logical_row) in enumerate(zip(rows, logical_rows, strict=True)):
            if not row:
                continue
            label_cell = logical_row.get(0)
            if label_cell is None:
                continue
            row_label = cell_text(label_cell)
            metric = match_metric(row_label)
            if metric is None:
                continue

            column_labels = header_labels_by_column(logical_rows, row_index)
            for logical_column, value_cell in logical_row.items():
                if logical_column == 0 or value_cell is label_cell:
                    continue
                raw_value = cell_text(value_cell)
                if parse_raw_number(raw_value) is None:
                    continue
                provenance = value_cell.get("provenance", {})
                source_row = int(provenance.get("row_index", row_index))
                source_column = int(provenance.get("col_index", logical_column))
                source_key = (
                    doc_id,
                    metric.value,
                    table_id,
                    source_row,
                    source_column,
                )
                if source_key in seen_sources:
                    continue
                seen_sources.add(source_key)

                column_label = column_labels.get(logical_column, "")
                fiscal_year, fiscal_period = infer_period(
                    column_label,
                    term_years,
                    report_name,
                )
                # Contract상 optional이지만, 이번 financial MVP는 값-기간 연결이
                # 확인된 경우에만 Fact로 확정한다.
                if fiscal_year is None:
                    continue
                normalized_value, normalized_unit = normalize_value_and_unit(
                    raw_value,
                    raw_unit,
                )
                source_suffix = f"{table_id}_r{source_row}_c{source_column}"
                evidence_id = f"evidence_{doc_id}_{source_suffix}"
                fact_id = f"fact_{doc_id}_{metric.value}_{source_suffix}"
                table_name = (
                    (table_group or {}).get("title")
                    or table.get("title")
                    or (table_group or {}).get("aclass")
                )

                evidence.append(
                    Evidence(
                        evidence_id=evidence_id,
                        doc_id=doc_id,
                        location_type=EvidenceLocationType.TABLE,
                        section_path=table.get("section_path", []),
                        table_name=table_name,
                        row_label=row_label,
                        column_label=column_label or None,
                        field_name=(
                            f"table_id={table_id};row_index={source_row};"
                            f"col_index={source_column};"
                            f"anegated={value_cell.get('anegated')}"
                        ),
                        raw_text=raw_value,
                        provenance=EvidenceProvenance(
                            table_id=table_id,
                            row_index=source_row,
                            column_index=source_column,
                            acode=value_cell.get("acode"),
                            acontext=value_cell.get("acontext"),
                            unit_declaration=unit_resolution.declaration,
                            unit_source=unit_resolution.source,
                            unit_multiplier=unit_resolution.multiplier,
                            unit_trusted=unit_resolution.trusted,
                            aclass=str(aclass) if aclass else None,
                            table_classification=table_classification,
                        ),
                    )
                )
                facts.append(
                    Fact(
                        fact_id=fact_id,
                        company=company,
                        metric=metric,
                        fiscal_year=fiscal_year,
                        fiscal_period=fiscal_period,
                        accounting_scope=accounting_scope,
                        raw_value=raw_value,
                        raw_unit=raw_unit,
                        normalized_value=normalized_value,
                        normalized_unit=normalized_unit,
                        evidence_ids=[evidence_id],
                    )
                )

    return facts, evidence

def _extract_financial_fact_from_label_fallback(
    document: dict[str, Any],
    *,
    metric: str,
    fiscal_year: int,
    accounting_scope: str,
) -> FinancialFact:
    """
    ACODE/ACONTEXT가 없는 지표를 bulk Fact/Evidence 결과에서 보수적으로 선택한다.

    fallback은 공통 Contract까지 검증된 bulk 신호를 재사용한다.

    선택 조건:
    - metric 일치
    - fiscal_year 일치
    - accounting_scope 일치
    - Evidence가 primary statement로 분류된 값

    하나만 남을 때만 반환하며, 0개 또는 여러 개면 추측하지 않는다.
    """
    try:
        metric_concept = MetricConcept(metric)
    except ValueError as exc:
        raise ExtractionBlockedError(
            reason="unsupported_metric",
            detail=f"지원하지 않는 지표입니다: {metric}",
        ) from exc

    try:
        scope = FactAccountingScope(accounting_scope)
    except ValueError as exc:
        raise ExtractionBlockedError(
            reason="unsupported_accounting_scope",
            detail=f"지원하지 않는 회계 기준입니다: {accounting_scope}",
        ) from exc

    facts, evidence = extract_financial_facts_with_evidence(document)

    evidence_by_id = {
        item.evidence_id: item
        for item in evidence
    }

    candidates: list[tuple[Fact, Evidence]] = []

    for fact in facts:
        if fact.metric is not metric_concept:
            continue

        if fact.fiscal_year != fiscal_year:
            continue

        if fact.accounting_scope is not scope:
            continue

        if len(fact.evidence_ids) != 1:
            continue

        item = evidence_by_id.get(fact.evidence_ids[0])

        if item is None or item.provenance is None:
            continue

        if (
            item.provenance.table_classification
            != TABLE_CLASS_PRIMARY_STATEMENT
        ):
            continue

        aclass = item.provenance.aclass or ""

        if not aclass.startswith("{XBRL}IS"):
            continue

        candidates.append((fact, item))

    if not candidates:
        raise ExtractionBlockedError(
            reason="no_matching_label_fallback",
            detail=(
                f"{fiscal_year} 회계연도 / {accounting_scope} 기준 "
                f"{metric} 본표 후보를 찾지 못했습니다."
            ),
        )

    if len(candidates) > 1:
        locations = [
            (
                f"{item.provenance.table_id}"
                f"[r{item.provenance.row_index}"
                f",c{item.provenance.column_index}]"
            )
            for _, item in candidates
            if item.provenance is not None
        ]

        raise ExtractionBlockedError(
            reason="ambiguous_label_fallback",
            detail=(
                f"{fiscal_year} 회계연도 / {accounting_scope} 기준 "
                f"{metric} 본표 후보가 {len(candidates)}개 남았습니다. "
                "하나를 고를 추가 근거가 없어 추출을 중단합니다. "
                f"후보: {' | '.join(locations)}"
            ),
        )

    fact, item = candidates[0]

    provenance = item.provenance

    if provenance is None:
        raise ExtractionBlockedError(
            reason="missing_fallback_provenance",
            detail="label fallback 후보에 provenance가 없습니다.",
        )

    raw_value = fact.raw_value

    if raw_value is None:
        raise ExtractionBlockedError(
            reason="missing_raw_value",
            detail="label fallback 후보에 raw_value가 없습니다.",
        )

    value = parse_numeric_value(raw_value)

    if value is None:
        raise ExtractionBlockedError(
            reason="unparsable_value",
            detail=f"셀 값을 수치로 변환하지 못했습니다: {raw_value!r}",
        )

    raw_unit = fact.raw_unit

    if raw_unit is None:
        raise ExtractionBlockedError(
            reason="missing_table_unit",
            detail="label fallback 후보에 단위가 없습니다.",
        )

    multiplier = provenance.unit_multiplier

    if multiplier is None:
        raise ExtractionBlockedError(
            reason="missing_unit_multiplier",
            detail="label fallback 후보의 단위 배수를 확인할 수 없습니다.",
        )

    if provenance.unit_trusted is not True:
        raise ExtractionBlockedError(
            reason="untrusted_table_unit",
            detail="label fallback 후보의 단위 선언을 신뢰할 수 없습니다.",
        )

    table_id = provenance.table_id
    row_index = provenance.row_index
    column_index = provenance.column_index

    if (
        table_id is None
        or row_index is None
        or column_index is None
    ):
        raise ExtractionBlockedError(
            reason="missing_fallback_location",
            detail="label fallback 후보의 셀 위치가 불완전합니다.",
        )

    return FinancialFact(
        metric=metric,
        acode=provenance.acode,
        accounting_scope=accounting_scope,
        fiscal_year=fiscal_year,
        raw_value=raw_value,
        value=value,
        unit_text=(
            provenance.unit_declaration
            or raw_unit
        ),
        unit_token=raw_unit,
        unit_multiplier=int(multiplier),
        value_in_base_unit=value * int(multiplier),
        base_unit=BASE_CURRENCY_UNIT,
        acontext=provenance.acontext,
        location=FactLocation(
            section_id=None,
            section_path=tuple(item.section_path),
            table_id=table_id,
            row_index=row_index,
            column_index=column_index,
            label_text=item.row_label or "",
        ),
    )

def extract_financial_facts(parsed_document: dict[str, Any]) -> list[Fact]:
    """기존 진입점을 유지하면서 Contract v0.1 Fact 목록을 반환한다."""
    facts, _ = extract_financial_facts_with_evidence(parsed_document)
    return facts


def extract_file(input_path: Path, output_path: Path) -> None:
    with input_path.open("r", encoding="utf-8") as f:
        parsed_document = json.load(f)

    facts, evidence = extract_financial_facts_with_evidence(parsed_document)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "facts": [fact.model_dump(mode="json") for fact in facts],
                "evidence": [item.model_dump(mode="json") for item in evidence],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"extracted facts: {len(facts)}")
    print(f"saved to: {output_path}")


if __name__ == "__main__":
    input_path = Path(
        "data/processed/samsung_2023_parsed.json"
    )

    output_path = Path(
        "data/processed/samsung_2023_facts.json"
    )

    extract_file(input_path, output_path)
