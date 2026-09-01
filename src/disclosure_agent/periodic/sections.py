"""정기공시 Section 탐색 모듈.

역할:
- 질문이 지정한 범위(`section_scope`)를 정기공시의 Section 경로로 옮기고, 그 Section이
  직접 담고 있는 본문·표를 순서대로 모은다.

이 모듈이 periodic에 있는 이유:
- "사업보고서에서 생산설비 내용이 어느 Section에 있는가"는 문서 서식에 대한 지식이다.
  질문 해석 결과(QueryPlan)를 모르고, 어느 문서를 볼지도 정하지 않는다. 그 둘은 agent
  계층이 정한다(tests/unit/test_layering.py).
- 그래서 이 모듈은 ParsedDocument와 SectionScope만 받는다. 코퍼스도 manifest도 열지 않으며,
  합성 문서만으로 전부 시험할 수 있다.

무엇을 하지 않는가:
- 요약하지 않는다. 자연어를 만들지 않고, 원문을 다시 쓰지도 않는다. 모으는 것은 원문
  발췌이며, 문장으로 옮기는 일은 상위 계층의 몫이다.
- 의미 검색을 하지 않는다. 구조 기반 탐색이 1순위라는 것이
  docs/agent_design/retrieval_strategy.md Pattern B의 규약이고, Vector Search 도입 여부는
  open_decisions.md D7에서 아직 미정이다.
- 범위를 찾지 못했을 때 문서 전체로 넓히지 않는다. 같은 문서의 상위 Section으로 한 단계
  넓히는 fallback도 두지 않았다. 지금 쓰는 규칙은 이미 상위 Section("사업의 내용")을
  조건으로 요구하고 있어 한 단계 위가 곧 문서 전체이며, 그것은 위 문서가 금지한 대체다.

Section을 하나로 확정하는 이유:
- 후보가 여럿이면 고르지 않고 알린다. 어느 Section을 골랐는지는 요약 결과만 봐서는
  드러나지 않으므로, 첫 매치를 고르면 근거를 갖춘 채로 다른 범위를 요약하게 된다.

입력:
- ParsedDocument dict (periodic.parser), SectionScope

출력:
- SectionSelection, SectionContentItem 목록

TODO:
- business_overview·products·financial_statement의 Section 규칙 확정. 지금은 어휘만 있고
  규칙이 없어 unsupported_section_scope로 막힌다(SECTION_SCOPE_RULES 참조).
- Section 내부의 항목 단위(가./나./(1)) 분할. Parser가 본문 P와 TABLE의 문서 내 순서를
  함께 남기지 않아 지금은 Section 경계가 곧 범위 경계다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from disclosure_agent.common.enums import SectionScope, SummaryItemType

__all__ = [
    "EXCERPT_MAX_LENGTH",
    "LABEL_MAX_LENGTH",
    "REASON_AMBIGUOUS_SECTION",
    "REASON_EMPTY_SECTION",
    "REASON_NO_MATCHING_SECTION",
    "REASON_UNSUPPORTED_SECTION_SCOPE",
    "SECTION_SCOPE_RULES",
    "SectionContentItem",
    "SectionNavigationError",
    "SectionScopeRule",
    "SectionSelection",
    "collect_section_content",
    "select_scope_section",
]

# 원문 발췌의 상한. 근거로 되짚기에 충분하면서 원문을 통째로 옮기지 않는 길이다.
#
# 상한을 두는 이유는 두 가지다. 공개 응답에 원문 전문을 싣지 않기 위해서이고
# (AGENTS.md의 원문 전문 금지), 항목 하나가 문서 한 절을 통째로 담으면 "항목마다의
# Evidence"라는 Pattern B의 요건이 형식만 남기 때문이다.
EXCERPT_MAX_LENGTH = 300

# 항목 이름의 상한. 목록으로 읽히는 길이여야 한다.
LABEL_MAX_LENGTH = 60

# 잘렸다는 사실을 남긴다. 발췌를 원문으로 착각하지 않게 하기 위한 표시다.
TRUNCATION_MARK = "…"

# 표 항목의 이름을 만들 때 읽는 머리 행의 셀 수와, 발췌에 담을 행 수.
TABLE_LABEL_CELL_LIMIT = 6
TABLE_EXCERPT_ROW_LIMIT = 3
TABLE_EXCERPT_CELL_LIMIT = 8
TABLE_CELL_SEPARATOR = " | "
TABLE_ROW_SEPARATOR = " / "

REASON_UNSUPPORTED_SECTION_SCOPE = "unsupported_section_scope"
REASON_NO_MATCHING_SECTION = "no_matching_section"
REASON_AMBIGUOUS_SECTION = "ambiguous_section"
REASON_EMPTY_SECTION = "empty_section"


class SectionNavigationError(LookupError):
    """지정한 범위를 Section 하나로 확정하지 못한 경우."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)

        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class SectionScopeRule:
    """범위 하나를 Section 경로로 알아보는 규칙.

    두 조건을 함께 요구한다.

    ancestor_tokens
        상위 Section 제목이 담고 있어야 하는 표현이다. 사업보고서에는 같은 낱말이 여러
        Section에 나온다. '투자'는 재무제표 주석에도, 이사의 경영진단에도 있으므로 상위
        Section을 함께 요구하지 않으면 요약 범위가 문서 전체로 번진다
        (docs/agent_design/representative_questions.md Q04의 주요 실패 모드).

    title_tokens
        대상 Section 제목이 담고 있어야 하는 표현이다.

    둘 다 '어느 하나라도 있으면'으로 본다. 같은 뜻의 표기 변형을 담기 위해서이며, 조건
    자체는 둘 다 만족해야 한다.
    """

    scope: SectionScope
    ancestor_tokens: tuple[str, ...]
    title_tokens: tuple[str, ...]

    def matches(self, section_path: list[str]) -> bool:
        if len(section_path) < 2:
            # 상위 Section이 없으면 이 규칙이 요구하는 조건을 확인할 수 없다.
            return False

        title = section_path[-1]
        ancestors = section_path[:-1]

        return any(token in title for token in self.title_tokens) and any(
            token in ancestor for token in self.ancestor_tokens for ancestor in ancestors
        )


# 범위 -> Section 규칙.
#
# 지금 담긴 것은 facility_investment 하나다. 나머지 SectionScope 값은 어휘로만 있고 규칙이
# 없어 unsupported_section_scope로 막힌다. 규칙을 실제 코퍼스로 확인하지 않은 채 넣으면,
# 확인되지 않은 Section을 요약해 놓고 형식만 맞는 결과가 나간다.
#
# facility_investment의 근거:
#     docs/agent_design/retrieval_strategy.md Pattern B가 "사업의 내용 Section 아래 설비
#     관련 하위 Section으로 범위를 먼저 좁혀야 한다"고 적고 있다. 표준 서식에서 그 자리는
#     "II. 사업의 내용 / 3. 원재료 및 생산설비"이며, 이 규칙은 그 경로를 가리킨다.
SECTION_SCOPE_RULES: dict[SectionScope, SectionScopeRule] = {
    SectionScope.FACILITY_INVESTMENT: SectionScopeRule(
        scope=SectionScope.FACILITY_INVESTMENT,
        ancestor_tokens=("사업의 내용",),
        title_tokens=("생산설비",),
    ),
}


@dataclass(frozen=True)
class SectionSelection:
    """지정 범위로 확정된 Section 하나."""

    scope: SectionScope
    section_id: str
    section_path: tuple[str, ...]


@dataclass(frozen=True)
class SectionContentItem:
    """확정된 Section이 직접 담고 있는 내용 하나.

    ordinal은 같은 종류 안에서의 원문 순서다. 본문과 표를 하나의 순서로 섞지 않는 이유는
    Parser가 그 둘의 문서 내 순서를 함께 남기지 않기 때문이다. 없는 순서를 만들어 붙이면
    읽는 쪽이 그것을 원문 배치로 읽는다.
    """

    item_type: SummaryItemType
    ordinal: int
    label: str
    excerpt: str
    section_id: str
    section_path: tuple[str, ...] = field(default_factory=tuple)
    table_id: str | None = None
    unit_declaration: str | None = None
    table_classification: str | None = None


def _truncate(text: str, limit: int) -> str:
    """상한을 넘는 문자열을 잘라 표시를 붙인다."""
    collapsed = " ".join(text.split())

    if len(collapsed) <= limit:
        return collapsed

    return collapsed[:limit] + TRUNCATION_MARK


def select_scope_section(
    document: dict[str, Any],
    *,
    section_scope: SectionScope,
) -> SectionSelection:
    """
    지정 범위에 해당하는 Section을 문서에서 하나로 확정한다.

    Raises:
        SectionNavigationError: 규칙이 없거나, 후보가 0건이거나, 하나로 좁히지 못한 경우.
    """
    rule = SECTION_SCOPE_RULES.get(section_scope)

    if rule is None:
        supported = ", ".join(sorted(scope.value for scope in SECTION_SCOPE_RULES))

        raise SectionNavigationError(
            reason=REASON_UNSUPPORTED_SECTION_SCOPE,
            detail=(
                f"'{section_scope.value}' 범위를 Section 경로로 옮기는 규칙이 아직 "
                f"없습니다. 현재 규칙이 있는 범위: {supported}. "
                "규칙 없이 비슷한 Section을 골라 요약하지 않습니다."
            ),
        )

    matched = [
        section
        for section in document.get("sections") or []
        if rule.matches(list(section.get("section_path") or []))
    ]

    if not matched:
        raise SectionNavigationError(
            reason=REASON_NO_MATCHING_SECTION,
            detail=(
                f"문서에서 '{section_scope.value}' 범위에 해당하는 Section을 찾지 "
                "못했습니다. 상위 Section이나 문서 전체 요약으로 대체하지 않습니다."
            ),
        )

    if len(matched) > 1:
        listed = " / ".join(
            " > ".join(section.get("section_path") or []) for section in matched
        )

        raise SectionNavigationError(
            reason=REASON_AMBIGUOUS_SECTION,
            detail=(
                f"'{section_scope.value}' 범위에 해당하는 Section이 {len(matched)}건입니다: "
                f"{listed}. 하나를 골라 요약하지 않습니다."
            ),
        )

    section = matched[0]

    return SectionSelection(
        scope=section_scope,
        section_id=str(section.get("section_id") or ""),
        section_path=tuple(section.get("section_path") or []),
    )


def _paragraph_items(section: dict[str, Any]) -> list[SectionContentItem]:
    section_id = str(section.get("section_id") or "")
    section_path = tuple(section.get("section_path") or [])

    items: list[SectionContentItem] = []

    for ordinal, paragraph in enumerate(section.get("paragraphs") or []):
        text = " ".join(str(paragraph).split())

        if not text:
            continue

        items.append(
            SectionContentItem(
                item_type=SummaryItemType.PARAGRAPH,
                ordinal=ordinal,
                label=_truncate(text, LABEL_MAX_LENGTH),
                excerpt=_truncate(text, EXCERPT_MAX_LENGTH),
                section_id=section_id,
                section_path=section_path,
            )
        )

    return items


def _cell_texts(row: list[dict[str, Any]], limit: int) -> list[str]:
    texts = [" ".join(str(cell.get("text") or "").split()) for cell in row[:limit]]

    return [text for text in texts if text]


def is_unit_declaration_table(table: dict[str, Any]) -> bool:
    """
    표 안에 단위 선언 말고는 아무것도 없는가.

    사업보고서 서식에서는 "(단위 : 억원)" 한 줄이 별도의 TABLE로 들어가고 실제 표가 그
    뒤에 이어지는 배치가 흔하다. 그 한 줄을 요약 항목으로 세면 항목 수는 늘지만 내용은
    늘지 않는다.

    표 자신이 선언한 단위(``own_unit``)가 있고 셀에 그 문자열밖에 없을 때만 참이다.
    상속받은 단위로는 판정하지 않는다. 값을 담은 표가 위쪽 선언을 물려받았을 뿐인 경우까지
    함께 버려지기 때문이다.
    """
    own_unit = table.get("own_unit")

    if not own_unit:
        return False

    marker = " ".join(str(own_unit).split())

    texts = [
        " ".join(str(cell.get("text") or "").split())
        for row in table.get("rows") or []
        for cell in row
    ]
    non_empty = [text for text in texts if text]

    return bool(non_empty) and all(text == marker for text in non_empty)


def _table_label(table: dict[str, Any]) -> str:
    """표 항목의 이름. 제목이 없으면 머리 행의 셀을 잇는다."""
    title = table.get("title")

    if isinstance(title, str) and title.strip():
        return _truncate(title, LABEL_MAX_LENGTH)

    rows = table.get("rows") or []

    if rows:
        header = _cell_texts(rows[0], TABLE_LABEL_CELL_LIMIT)

        if header:
            return _truncate(TABLE_CELL_SEPARATOR.join(header), LABEL_MAX_LENGTH)

    return _truncate(str(table.get("table_id") or "표"), LABEL_MAX_LENGTH)


def _table_excerpt(table: dict[str, Any]) -> str:
    rendered = [
        TABLE_CELL_SEPARATOR.join(_cell_texts(row, TABLE_EXCERPT_CELL_LIMIT))
        for row in (table.get("rows") or [])[:TABLE_EXCERPT_ROW_LIMIT]
    ]

    joined = TABLE_ROW_SEPARATOR.join(part for part in rendered if part)

    return _truncate(joined, EXCERPT_MAX_LENGTH)


def _table_items(
    document: dict[str, Any],
    section_id: str,
) -> list[SectionContentItem]:
    items: list[SectionContentItem] = []
    ordinal = 0

    for table in document.get("tables") or []:
        if table.get("section_id") != section_id:
            continue

        if is_unit_declaration_table(table):
            continue

        excerpt = _table_excerpt(table)

        if not excerpt:
            continue

        attributes = table.get("attributes") or {}

        items.append(
            SectionContentItem(
                item_type=SummaryItemType.TABLE,
                ordinal=ordinal,
                label=_table_label(table),
                excerpt=excerpt,
                section_id=section_id,
                section_path=tuple(table.get("section_path") or []),
                table_id=str(table.get("table_id") or ""),
                unit_declaration=table.get("unit"),
                table_classification=attributes.get("aclass"),
            )
        )

        ordinal += 1

    return items


def collect_section_content(
    document: dict[str, Any],
    *,
    selection: SectionSelection,
) -> list[SectionContentItem]:
    """
    확정된 Section이 직접 담고 있는 본문과 표를 모은다.

    하위 Section의 내용은 담기지 않는다. Parser가 SECTION 하나의 직접 자식만 그 Section에
    담기 때문이며(periodic.parser.extract_direct_paragraphs), 그 경계를 그대로 범위 경계로
    쓴다.

    Raises:
        SectionNavigationError: Section은 찾았으나 담고 있는 내용이 없는 경우.
    """
    section = next(
        (
            item
            for item in document.get("sections") or []
            if item.get("section_id") == selection.section_id
        ),
        None,
    )

    if section is None:
        raise SectionNavigationError(
            reason=REASON_NO_MATCHING_SECTION,
            detail=(
                f"확정한 Section '{selection.section_id}'을 문서에서 다시 찾지 못했습니다."
            ),
        )

    items = [*_paragraph_items(section), *_table_items(document, selection.section_id)]

    if not items:
        raise SectionNavigationError(
            reason=REASON_EMPTY_SECTION,
            detail=(
                f"'{selection.scope.value}' 범위로 확정한 Section "
                f"'{' > '.join(selection.section_path)}'에 본문도 표도 없습니다. "
                "다른 Section의 내용으로 채우지 않습니다."
            ),
        )

    return items
