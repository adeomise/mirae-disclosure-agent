"""정기공시 Section 탐색 단위 테스트.

검증 대상:
- 지정 범위를 Section 하나로 확정하는가
- 확정하지 못하는 세 경우(규칙 없음 / 후보 0건 / 후보 복수)를 서로 다른 사유로 구분하는가
- 확정한 Section 밖의 내용을 끌어오지 않는가

코퍼스도 manifest도 열지 않는다. ParsedDocument dict 하나만 받는 모듈이라 합성 문서로 규칙
전부를 시험할 수 있다.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from disclosure_agent.common.enums import SectionScope, SummaryItemType
from disclosure_agent.periodic.sections import (
    EXCERPT_MAX_LENGTH,
    LABEL_MAX_LENGTH,
    REASON_AMBIGUOUS_SECTION,
    REASON_EMPTY_SECTION,
    REASON_NO_MATCHING_SECTION,
    REASON_UNSUPPORTED_SECTION_SCOPE,
    SECTION_SCOPE_RULES,
    SectionNavigationError,
    collect_section_content,
    select_scope_section,
)

FACILITY_SECTION_ID = "section_0003"


# --------------------------------------------------------------------------
# 1. 범위 확정
# --------------------------------------------------------------------------


def test_facility_scope_selects_the_production_facility_section(
    summary_document: dict[str, Any],
) -> None:
    selection = select_scope_section(
        summary_document,
        section_scope=SectionScope.FACILITY_INVESTMENT,
    )

    assert selection.section_id == FACILITY_SECTION_ID
    assert selection.section_path == ("II. 사업의 내용", "3. 원재료 및 생산설비")


def test_scope_without_a_rule_is_rejected(summary_document: dict[str, Any]) -> None:
    """
    Section 규칙이 없는 범위는 비슷한 Section으로 대신 처리하지 않는다.

    이 경로가 열려 있어야 SectionScope 어휘를 넓히는 일과 규칙을 확인하는 일이 같은 작업이
    된다. 규칙 없이 어휘만 늘면 확인되지 않은 Section을 요약해 놓고 형식만 맞는 결과가 나간다.
    """
    unmapped = next(
        scope for scope in SectionScope if scope not in SECTION_SCOPE_RULES
    )

    with pytest.raises(SectionNavigationError) as error:
        select_scope_section(summary_document, section_scope=unmapped)

    assert error.value.reason == REASON_UNSUPPORTED_SECTION_SCOPE


def test_missing_section_does_not_fall_back_to_the_whole_document(
    summary_document: dict[str, Any],
) -> None:
    """
    범위를 찾지 못하면 상위 Section이나 문서 전체로 넓히지 않는다.

    docs/agent_design/retrieval_strategy.md Pattern B의 Fallback이 그 대체를 금지한다.
    """
    document = copy.deepcopy(summary_document)

    document["sections"] = [
        section
        for section in document["sections"]
        if section["section_id"] != FACILITY_SECTION_ID
    ]

    with pytest.raises(SectionNavigationError) as error:
        select_scope_section(document, section_scope=SectionScope.FACILITY_INVESTMENT)

    assert error.value.reason == REASON_NO_MATCHING_SECTION


def test_multiple_matching_sections_are_not_narrowed_by_order(
    summary_document: dict[str, Any],
) -> None:
    """
    후보가 여럿이면 첫 매치를 고르지 않는다.

    어느 Section을 골랐는지는 요약 결과만 봐서는 드러나지 않으므로, 고르면 근거를 갖춘 채로
    다른 범위를 요약하게 된다.
    """
    document = copy.deepcopy(summary_document)

    duplicate = copy.deepcopy(
        next(
            section
            for section in document["sections"]
            if section["section_id"] == FACILITY_SECTION_ID
        )
    )
    duplicate["section_id"] = "section_0099"
    duplicate["section_path"] = ["II. 사업의 내용", "4. 생산설비 보충"]

    document["sections"].append(duplicate)

    with pytest.raises(SectionNavigationError) as error:
        select_scope_section(document, section_scope=SectionScope.FACILITY_INVESTMENT)

    assert error.value.reason == REASON_AMBIGUOUS_SECTION


def test_matching_title_without_the_required_ancestor_is_not_selected(
    summary_document: dict[str, Any],
) -> None:
    """
    상위 Section 조건이 없으면 같은 낱말을 담은 다른 Section이 함께 걸린다.

    Q04의 주요 실패 모드가 그것이며(재무제표 주석의 투자 항목까지 끌어오는 것), 상위 Section을
    함께 요구하는 규칙이 그 확산을 막는다.
    """
    document = copy.deepcopy(summary_document)

    for section in document["sections"]:
        if section["section_id"] == FACILITY_SECTION_ID:
            section["section_path"] = ["XI. 그 밖의 사항", "3. 원재료 및 생산설비"]

    with pytest.raises(SectionNavigationError) as error:
        select_scope_section(document, section_scope=SectionScope.FACILITY_INVESTMENT)

    assert error.value.reason == REASON_NO_MATCHING_SECTION


# --------------------------------------------------------------------------
# 2. 범위 내 내용 수집
# --------------------------------------------------------------------------


def collect(document: dict[str, Any]):
    selection = select_scope_section(
        document,
        section_scope=SectionScope.FACILITY_INVESTMENT,
    )

    return collect_section_content(document, selection=selection)


def test_collected_items_stay_inside_the_selected_section(
    summary_document: dict[str, Any],
) -> None:
    """확정한 Section 밖의 본문·표는 담기지 않는다."""
    items = collect(summary_document)

    assert {item.section_id for item in items} == {FACILITY_SECTION_ID}

    joined = " ".join(item.excerpt for item in items)

    assert "요약 범위 밖" not in joined


def test_collected_items_cover_paragraphs_and_tables(
    summary_document: dict[str, Any],
) -> None:
    items = collect(summary_document)

    paragraphs = [item for item in items if item.item_type is SummaryItemType.PARAGRAPH]
    tables = [item for item in items if item.item_type is SummaryItemType.TABLE]

    assert len(paragraphs) == 3
    assert len(tables) == 1

    assert tables[0].table_id == "table_0002"
    assert tables[0].unit_declaration == "(단위 : 백만원)"
    assert tables[0].table_classification == "NORMAL"


def test_unit_declaration_only_tables_are_not_summary_items(
    summary_document: dict[str, Any],
) -> None:
    """
    "(단위 : 백만원)" 한 줄만 담긴 표는 항목으로 세지 않는다.

    항목 수는 늘지만 내용은 늘지 않으며, 그러면 "요약 항목 수만큼의 Evidence"라는 요건이
    형식만 남는다.
    """
    items = collect(summary_document)

    assert "table_0001" not in {item.table_id for item in items}


def test_paragraph_order_follows_the_source(summary_document: dict[str, Any]) -> None:
    """항목 순서는 원문 순서다. 여기서 순서를 다시 정하지 않는다."""
    paragraphs = [
        item for item in collect(summary_document) if item.item_type is SummaryItemType.PARAGRAPH
    ]

    assert [item.ordinal for item in paragraphs] == [0, 1, 2]
    assert paragraphs[0].label.startswith("가.")
    assert paragraphs[1].label.startswith("나.")


def test_excerpt_and_label_are_bounded(summary_document: dict[str, Any]) -> None:
    """
    발췌와 이름에는 상한이 있다.

    공개 응답에 원문 전문을 싣지 않기 위해서다. 상한이 없으면 항목 하나가 문서 한 절을
    통째로 담는다.
    """
    document = copy.deepcopy(summary_document)

    for section in document["sections"]:
        if section["section_id"] == FACILITY_SECTION_ID:
            section["paragraphs"] = ["라." + "가" * 5_000]

    items = collect(document)

    assert all(len(item.excerpt) <= EXCERPT_MAX_LENGTH + 1 for item in items)
    assert all(len(item.label) <= LABEL_MAX_LENGTH + 1 for item in items)


def test_empty_section_is_reported_instead_of_being_filled(
    summary_document: dict[str, Any],
) -> None:
    """
    범위는 찾았는데 그 안이 비어 있으면 다른 Section의 내용으로 채우지 않는다.
    """
    document = copy.deepcopy(summary_document)

    for section in document["sections"]:
        if section["section_id"] == FACILITY_SECTION_ID:
            section["paragraphs"] = []

    document["tables"] = [
        table for table in document["tables"] if table["section_id"] != FACILITY_SECTION_ID
    ]

    with pytest.raises(SectionNavigationError) as error:
        collect(document)

    assert error.value.reason == REASON_EMPTY_SECTION


# --------------------------------------------------------------------------
# 4. 주요 제품(products) 범위
# --------------------------------------------------------------------------

PRODUCTS_SECTION_ID = "section_0002"


def test_products_scope_selects_the_major_products_section(
    summary_document: dict[str, Any],
) -> None:
    selection = select_scope_section(summary_document, section_scope=SectionScope.PRODUCTS)

    assert selection.section_id == PRODUCTS_SECTION_ID
    assert selection.section_path == ("II. 사업의 내용", "2. 주요 제품 및 서비스")

    items = collect_section_content(summary_document, selection=selection)

    assert items
    assert all(item.section_id == PRODUCTS_SECTION_ID for item in items)


def test_products_scope_without_a_matching_section_fails_closed(
    summary_document: dict[str, Any],
) -> None:
    document = copy.deepcopy(summary_document)

    document["sections"] = [
        section
        for section in document["sections"]
        if section["section_id"] != PRODUCTS_SECTION_ID
    ]

    with pytest.raises(SectionNavigationError) as error:
        select_scope_section(document, section_scope=SectionScope.PRODUCTS)

    assert error.value.reason == REASON_NO_MATCHING_SECTION


def test_products_scope_with_two_matching_sections_is_ambiguous(
    summary_document: dict[str, Any],
) -> None:
    document = copy.deepcopy(summary_document)

    duplicate = copy.deepcopy(
        next(
            section
            for section in document["sections"]
            if section["section_id"] == PRODUCTS_SECTION_ID
        )
    )
    duplicate["section_id"] = "section_0099"

    document["sections"].append(duplicate)

    with pytest.raises(SectionNavigationError) as error:
        select_scope_section(document, section_scope=SectionScope.PRODUCTS)

    assert error.value.reason == REASON_AMBIGUOUS_SECTION


def test_products_title_without_the_business_ancestor_is_not_selected(
    summary_document: dict[str, Any],
) -> None:
    """제목에 '주요 제품'이 있어도 상위 Section이 '사업의 내용'이 아니면 고르지 않는다."""
    document = copy.deepcopy(summary_document)

    for section in document["sections"]:
        if section["section_id"] == PRODUCTS_SECTION_ID:
            section["section_path"] = ["III. 재무에 관한 사항", "2. 주요 제품 및 서비스"]

    with pytest.raises(SectionNavigationError) as error:
        select_scope_section(document, section_scope=SectionScope.PRODUCTS)

    assert error.value.reason == REASON_NO_MATCHING_SECTION
