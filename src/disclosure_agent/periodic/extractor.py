"""정기공시 재무정보 추출 모듈.

역할:
- 파싱된 표와 주석에서 재무제표 항목, 수치, 단위, 기간 후보를 추출한다.

현재 구현 범위:
- 연결/별도 기준 매출액(ifrs-full_Revenue) 단일 Fact 추출.

입력 계약(Parser에 대한 최소 요구사항):
    이 모듈은 ParsedDocument dict만 입력으로 받으며 원문 XML을 다시 열지 않는다.
    아래 정보가 parser output에 존재한다고 전제한다.

        document["metadata"]  : doc_id / receipt_no / source_path 등 문서 식별자
        document["sections"]  : section_id, section_path
        document["tables"][i] : table_id, section_id, unit, rows
                                그리고 그 표가 재무제표 본표인지 주석인지 구분할 수 있는
                                분류 정보(classify_table 참고)
        cell                  : text, acode, acontext

    이 중 cell.acontext와 표 단위(table["unit"])의 정확한 귀속은 현재 parser가 제공하지
    않는다(조사 보고서의 D-2 / D-3). 표 분류 정보도 현재는 없다(D-4). 해당 정보가 없으면
    추측하지 않고 ExtractionBlockedError를 올려 상위 단계가 BLOCKED로 보고하게 한다.

출력:
- FinancialFact와 원문 위치 참조

TODO:
- 영업이익·당기순이익 등 지표 확장
- 분기·반기 컨텍스트(dQTR 등) 지원
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from disclosure_agent.agent.query_analyzer import (
    METRIC_REVENUE,
    SCOPE_CONSOLIDATED,
    SCOPE_SEPARATE,
)

# 매출액에 대응하는 DART/IFRS 항목 코드.
REVENUE_ACODES: tuple[str, ...] = ("ifrs-full_Revenue",)

METRIC_ACODES: dict[str, tuple[str, ...]] = {
    METRIC_REVENUE: REVENUE_ACODES,
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
# Parser가 최종적으로 어떤 키를 쓸지 확정되지 않았으므로 연결 지점을 한 곳에 모아 둔다.
TABLE_CLASS_FIELDS: tuple[str, ...] = ("table_group_class",)

# Parser 표기를 내부 분류로 옮기는 잠정 대응표. 최종 Data Contract가 아니다.
# 여기에 없는 표기는 분류하지 않는다. 모르는 값을 본표로 간주하지 않기 위해서다.
# 원문 TABLE-GROUP의 ACLASS 문자열을 그대로 정답으로 박아 두지 않는 것도 같은 이유다.
TABLE_CLASS_ALIASES: dict[str, str] = {
    "financial_statement": TABLE_CLASS_PRIMARY_STATEMENT,
    "primary_statement": TABLE_CLASS_PRIMARY_STATEMENT,
    "note": TABLE_CLASS_NOTE,
    "footnote": TABLE_CLASS_NOTE,
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

UNIT_MULTIPLIERS: dict[str, int] = {
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
    acode: str
    accounting_scope: str
    fiscal_year: int
    raw_value: str
    value: Decimal
    unit_text: str
    unit_token: str
    unit_multiplier: int
    value_in_base_unit: Decimal
    base_unit: str
    acontext: str
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

        if candidate in UNIT_MULTIPLIERS:
            token = candidate
            break

    if token is None:
        return None

    return UnitSpec(
        text=text,
        token=token,
        multiplier=UNIT_MULTIPLIERS[token],
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

    text = text.replace(",", "").replace(" ", "").replace(" ", "")

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


def classify_table(table: dict[str, Any]) -> str | None:
    """
    Parser가 준 표 metadata를 내부 분류(본표/주석)로 바꾼다.

    근거가 없거나 모르는 표기이면 None을 돌려준다.
    None은 '주석이 아니다'가 아니라 '판단하지 않는다'는 뜻이다.

    Parser가 실제로 어떤 키와 값으로 표 분류를 넘겨줄지는 아직 정해지지 않았다.
    연결 지점을 이 함수 하나로 제한해 두고, 확정되면 TABLE_CLASS_FIELDS와
    TABLE_CLASS_ALIASES만 바꾼다.

    section 제목이나 문서 순서로 본표 여부를 유추하지 않는다.
    """
    attributes = table.get("attributes") or {}

    for field_name in TABLE_CLASS_FIELDS:
        raw = table.get(field_name) or attributes.get(field_name)

        if not raw:
            continue

        return TABLE_CLASS_ALIASES.get(str(raw).strip().casefold())

    return None


def table_class_priority(table_class: str | None) -> int | None:
    """내부 분류에 대응하는 우선순위. 분류를 모르면 None."""
    if table_class is None:
        return None

    return TABLE_CLASS_PRIORITY.get(table_class)


@dataclass(frozen=True)
class _Candidate:
    table: dict[str, Any]
    row_index: int
    column_index: int
    cell: dict[str, Any]
    context: AccountingContext
    label: str


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

    acode_hits = 0

    for table in document.get("tables", []):
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
                    )
                )

    return candidates, acode_hits


def _describe_candidate(document: dict[str, Any], candidate: _Candidate) -> str:
    """후보를 값 없이 위치 정보만으로 요약한다. 막힌 이유를 사람이 읽기 위한 문자열이다."""
    section_id = candidate.table.get("section_id")

    section_path = _lookup_section_path(document, section_id)

    path_text = " > ".join(section_path) if section_path else "section 경로 없음"

    table_class = classify_table(candidate.table) or "unclassified"

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

    priorities = [table_class_priority(classify_table(c.table)) for c in shortlist]

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
    """
    acodes = METRIC_ACODES.get(metric)

    if not acodes:
        raise ExtractionBlockedError(
            reason="unsupported_metric",
            detail=f"지원하지 않는 지표입니다: {metric}",
        )

    if accounting_scope not in SCOPE_MEMBERS:
        raise ExtractionBlockedError(
            reason="unsupported_accounting_scope",
            detail=f"지원하지 않는 회계 기준입니다: {accounting_scope}",
        )

    candidates, acode_hits = _collect_candidates(document, acodes)

    if acode_hits == 0:
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

    unit = parse_unit(selected.table.get("unit"))

    if unit is None:
        raise ExtractionBlockedError(
            reason="missing_table_unit",
            detail=(
                f"표 {selected.table.get('table_id')}에 해석 가능한 단위가 없습니다. "
                "Parser가 표 단위를 표 단위로 귀속해 제공해야 합니다."
            ),
        )

    if not unit.trusted:
        raise ExtractionBlockedError(
            reason="untrusted_table_unit",
            detail=(
                f"표 {selected.table.get('table_id')}의 단위 문자열이 단위 선언 형태가 "
                f"아닙니다: {unit.text!r}. 표 본문에서 잘못 수집된 값으로 보이며 "
                "그대로 쓰면 자릿수 오류가 발생합니다."
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
