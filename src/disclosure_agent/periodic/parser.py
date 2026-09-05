"""정기공시 XML Parser 모듈.

역할:
- 사업·반기·분기보고서 XML을 읽고 Section, Subsection, Table 구조를 보존한다.

예정 입력:
- XML 파일 경로
- DocumentMetadata

예정 출력:
- ParsedDocument

TODO:
- Pilot XML 구조 분석 후 Parser 구현
- 네임스페이스와 태그 변형 처리
- 원문 값, 단위, 계층 보존 규칙 구현
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lxml import etree

SECTION_PATTERN = re.compile(r"SECTION-(\d+)$")
CELL_TAGS = {"TH", "TD", "TE", "TU"}
VALID_XML_ENTITY_PATTERN = re.compile(
    r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)"
)
TAG_LIKE_PATTERN = re.compile(r"<([^<>]+)>")
PROTECTED_XML_PATTERN = re.compile(
    r"<!\[CDATA\[.*?\]\]>|<!--.*?-->|<\?.*?\?>",
    re.DOTALL,
)
MALFORMED_PERCENT_COMPARISON_PATTERN = re.compile(
    r"<(?=[ \t](?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?[ \t]*%)"
)

# 제공된 dart3/dart4 XML의 실제 구조 tag vocabulary다. 이 목록 밖의
# ``<프로젝트>``/``<Connected Work>`` 같은 token은 원문 본문으로 보존한다.
DART_STRUCTURAL_TAGS = {
    "A",
    "BODY",
    "COL",
    "COLGROUP",
    "COMPANY-NAME",
    "COVER",
    "COVER-TITLE",
    "DOCUMENT",
    "DOCUMENT-NAME",
    "EXTRACTION",
    "FORMULA-VERSION",
    "IMAGE",
    "IMG",
    "IMG-CAPTION",
    "LIBRARY",
    "P",
    "PGBRK",
    "SECTION-1",
    "SECTION-2",
    "SECTION-3",
    "SPAN",
    "SUMMARY",
    "TABLE",
    "TABLE-GROUP",
    "TBODY",
    "TD",
    "TE",
    "TH",
    "THEAD",
    "TITLE",
    "TR",
    "TU",
}
_DART_STRUCTURAL_CLOSING_NAMES = "|".join(
    sorted(map(re.escape, DART_STRUCTURAL_TAGS), key=len, reverse=True)
)
MALFORMED_INCOMPLETE_KOREAN_TEXT_PATTERN = re.compile(
    rf"<(?=[가-힣]{{1,9}}[ \t]*"
    rf"</(?:{_DART_STRUCTURAL_CLOSING_NAMES}|SECTION-\d+)[ \t]*>)"
)
XML_ATTRIBUTE_NAME_PATTERN = re.compile(r"[A-Za-z_:][A-Za-z0-9_.:-]*")
MALFORMED_DUPLICATED_QUOTE_VALUE_START_PATTERN = re.compile(
    r"[^\s\"'=<>/]"
)


def _repair_malformed_attribute_syntax(fragment: str) -> str:
    """Audit-confirmed attribute syntax errors in structural start tags only.

    Two repairs are intentionally narrow:

    - ``NAME=""value"`` loses the duplicated opening quote only when the
      second quote is followed by a non-delimiter value character.  Therefore
      the valid empty value ``NAME=""`` is unchanged.
    - ``NAME="value"NEXT="other"`` gains one space only when the closing
      quote is immediately followed by a complete XML attribute name and
      ``=``.

    The small lexer must understand the whole start tag before applying any
    recorded edit.  Unknown or otherwise malformed attribute grammar remains
    untouched so the strict XML parser can reject it.
    """

    parts: list[str] = []
    unchanged_start = 0
    search_position = 0

    while True:
        tag_start = fragment.find("<", search_position)
        if tag_start == -1:
            break

        tag_name_match = XML_ATTRIBUTE_NAME_PATTERN.match(fragment, tag_start + 1)
        if tag_name_match is None:
            search_position = tag_start + 1
            continue

        tag_name = tag_name_match.group(0)
        if (
            tag_name not in DART_STRUCTURAL_TAGS
            and SECTION_PATTERN.fullmatch(tag_name) is None
        ):
            search_position = tag_start + 1
            continue

        position = tag_name_match.end()
        edits: list[tuple[int, int, str]] = []
        tag_end: int | None = None

        while position < len(fragment):
            while position < len(fragment) and fragment[position].isspace():
                position += 1

            if fragment.startswith("/>", position):
                tag_end = position + 2
                break
            if position < len(fragment) and fragment[position] == ">":
                tag_end = position + 1
                break

            attribute_match = XML_ATTRIBUTE_NAME_PATTERN.match(fragment, position)
            if attribute_match is None:
                break
            position = attribute_match.end()

            while position < len(fragment) and fragment[position].isspace():
                position += 1
            if position >= len(fragment) or fragment[position] != "=":
                break
            position += 1

            while position < len(fragment) and fragment[position].isspace():
                position += 1
            if position >= len(fragment) or fragment[position] not in {'"', "'"}:
                break

            quote = fragment[position]
            position += 1
            if (
                position + 1 < len(fragment)
                and fragment[position] == quote
                and MALFORMED_DUPLICATED_QUOTE_VALUE_START_PATTERN.match(
                    fragment,
                    position + 1,
                )
                is not None
            ):
                edits.append((position, position + 1, ""))
                position += 1

            closing_quote = fragment.find(quote, position)
            if closing_quote == -1:
                break
            position = closing_quote + 1

            if position >= len(fragment):
                break
            if fragment[position].isspace() or fragment[position] in {"/", ">"}:
                continue

            next_attribute = XML_ATTRIBUTE_NAME_PATTERN.match(fragment, position)
            if next_attribute is None:
                break
            equals_position = next_attribute.end()
            while (
                equals_position < len(fragment)
                and fragment[equals_position].isspace()
            ):
                equals_position += 1
            if (
                equals_position >= len(fragment)
                or fragment[equals_position] != "="
            ):
                break
            edits.append((position, position, " "))

        if tag_end is None:
            search_position = tag_start + 1
            continue

        if edits:
            parts.append(fragment[unchanged_start:tag_start])
            edit_start = tag_start
            for start, end, replacement in edits:
                parts.append(fragment[edit_start:start])
                parts.append(replacement)
                edit_start = end
            parts.append(fragment[edit_start:tag_end])
            unchanged_start = tag_end

        search_position = tag_end

    if not parts:
        return fragment
    parts.append(fragment[unchanged_start:])
    return "".join(parts)


def _repair_malformed_percent_comparisons_in_text(fragment: str) -> str:
    """Element text의 audit-confirmed malformed literal ``<``만 escape한다.

    호출자는 CDATA/comment/processing instruction을 제외한 fragment를 전달한다.
    start/end tag와 quoted attribute 안의 동일 문자열은 strict parser가 계속
    검증하도록 그대로 둔다. percent 비교식과 structural closing tag 직전의
    미완성 한글 token은 서로 독립된 좁은 문법으로 판정한다.
    """
    parts: list[str] = []
    unchanged_start = 0
    position = 0
    in_markup = False
    quote: str | None = None

    while position < len(fragment):
        if not in_markup:
            next_markup = fragment.find("<", position)
            if next_markup == -1:
                break
            position = next_markup
            comparison = MALFORMED_PERCENT_COMPARISON_PATTERN.match(
                fragment,
                position,
            )
            incomplete_korean_text = (
                MALFORMED_INCOMPLETE_KOREAN_TEXT_PATTERN.match(
                    fragment,
                    position,
                )
            )
            if comparison is not None or incomplete_korean_text is not None:
                parts.append(fragment[unchanged_start:position])
                parts.append("&lt;")
                position += 1
                unchanged_start = position
                continue

            in_markup = True
            position += 1
            continue

        character = fragment[position]
        if quote is not None:
            if character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == ">":
            in_markup = False
        position += 1

    if not parts:
        return fragment
    parts.append(fragment[unchanged_start:])
    return "".join(parts)


def sanitize_dart_xml_text(xml_text: str) -> str:
    """Audit-confirmed DART XML syntax errors only, preserving strict parsing.

    정상 entity는 그대로 두며, 구조 tag allowlist 밖의 ``<...>``는 text로
    escape한다. audit으로 확정한 percent 비교식과 미완성 한글 text의 raw
    ``<``만 text node에서 escape한다. 구조 start tag 안에서는 빈 attribute와
    구분 가능한 중복 opening quote 및 attribute 사이의 누락된 공백만 복구한다.
    XML 선언, comment, CDATA, processing instruction은 변경하지 않는다.
    """

    def sanitize_fragment(fragment: str) -> str:
        def escape_pseudo_tag(match: re.Match[str]) -> str:
            inner = match.group(1)
            stripped = inner.strip()
            candidate = stripped[1:].lstrip() if stripped.startswith("/") else stripped
            tag_name = re.split(r"[\s/]", candidate, maxsplit=1)[0]

            if tag_name in DART_STRUCTURAL_TAGS or SECTION_PATTERN.fullmatch(tag_name):
                return match.group(0)

            return f"&lt;{inner}&gt;"

        repaired_attributes = _repair_malformed_attribute_syntax(fragment)
        escaped_comparisons = _repair_malformed_percent_comparisons_in_text(
            repaired_attributes,
        )
        escaped_tags = TAG_LIKE_PATTERN.sub(
            escape_pseudo_tag,
            escaped_comparisons,
        )
        return VALID_XML_ENTITY_PATTERN.sub("&amp;", escaped_tags)

    parts: list[str] = []
    previous_end = 0
    for match in PROTECTED_XML_PATTERN.finditer(xml_text):
        parts.append(sanitize_fragment(xml_text[previous_end : match.start()]))
        parts.append(match.group(0))
        previous_end = match.end()
    parts.append(sanitize_fragment(xml_text[previous_end:]))
    return "".join(parts)


def parse_xml_root(xml_path: Path) -> etree._Element:
    """전처리한 XML을 strict mode로 파싱해 손상된 복구 tree 사용을 막는다."""
    try:
        xml_text = xml_path.read_text(encoding="utf-8")
        sanitized_text = sanitize_dart_xml_text(xml_text)
        parser = etree.XMLParser(recover=False, huge_tree=True)
        return etree.fromstring(sanitized_text.encode("utf-8"), parser)
    except (OSError, UnicodeError, etree.XMLSyntaxError) as exc:
        raise ValueError(f"XML 파싱에 실패했습니다: {xml_path}") from exc


def clean_text(text: str | None) -> str:
    """
    XML에서 추출한 문자열의 불필요한 공백을 정리한다.

    예:
        "삼성전자   주식회사" -> "삼성전자 주식회사"
    """
    if not text:
        return ""

    return " ".join(text.split())


def element_text(element: etree._Element) -> str:
    """
    element 내부의 모든 text를 합친다.

    P 내부에 SPAN이 여러 개 있더라도
    하나의 자연스러운 문자열로 반환한다.
    """
    return clean_text("".join(element.itertext()))


def get_direct_child(
    element: etree._Element,
    tag_name: str,
) -> etree._Element | None:
    """
    현재 element의 직접 자식 중 특정 tag를 하나 찾는다.

    재귀 탐색(find('.//TITLE'))을 하지 않는 이유:
    자식 SECTION의 TITLE을 잘못 가져오는 문제를 방지하기 위해서다.
    """
    for child in element:
        if child.tag == tag_name:
            return child

    return None


def get_section_level(tag: str) -> int | None:
    """
    SECTION-1 -> 1
    SECTION-2 -> 2
    SECTION-3 -> 3
    """
    match = SECTION_PATTERN.fullmatch(tag)

    if not match:
        return None

    return int(match.group(1))


def is_section(element: etree._Element) -> bool:
    return get_section_level(element.tag) is not None


def iter_transparent_children(container: etree._Element):
    """LIBRARY를 의미 없는 wrapper로 보고 직접 내용을 순서대로 펼친다."""
    for child in container:
        if child.tag == "LIBRARY":
            yield from iter_transparent_children(child)
        else:
            yield child


def extract_direct_paragraphs(section: etree._Element) -> list[str]:
    """
    현재 SECTION에 직접 포함된 P만 추출한다.

    자식 SECTION 아래 P까지 재귀적으로 가져오면
    부모 section과 자식 section에서 동일 본문이 중복되므로
    직접 자식 P만 대상으로 한다.

    LIBRARY는 투명 wrapper로 취급하되, 그 안의 자식 SECTION 본문은 포함하지 않는다.
    """
    paragraphs: list[str] = []

    for child in iter_transparent_children(section):
        if child.tag != "P":
            continue

        text = element_text(child)

        if text:
            paragraphs.append(text)

    return paragraphs


def extract_cell(
    cell: etree._Element,
    *,
    section_id: str,
    table_group_id: str | None,
    table_id: str,
    row_index: int,
    col_index: int,
) -> dict[str, Any]:
    """
    표 셀 하나를 파싱한다.

    TE / TU가 가지고 있는 구조화 속성은
    현재 단계에서는 정규화하지 않고 그대로 보존한다.
    """
    result: dict[str, Any] = {
        "type": cell.tag,
        "text": element_text(cell),
        # 행과 열 번호는 원문의 셀 위치를 나타내는 0-based 값이다.
        "provenance": {
            "section_id": section_id,
            "table_group_id": table_group_id,
            "table_id": table_id,
            "row_index": row_index,
            "col_index": col_index,
        },
    }

    if "ACODE" in cell.attrib:
        result["acode"] = cell.attrib["ACODE"]

    if "ACONTEXT" in cell.attrib:
        result["acontext"] = cell.attrib["ACONTEXT"]

    if "ANEGATED" in cell.attrib:
        result["anegated"] = cell.attrib["ANEGATED"]

    if "AUNIT" in cell.attrib:
        result["aunit"] = cell.attrib["AUNIT"]

    if "AUNITVALUE" in cell.attrib:
        result["aunit_value"] = cell.attrib["AUNITVALUE"]

    if "ROWSPAN" in cell.attrib:
        result["rowspan"] = cell.attrib["ROWSPAN"]

    if "COLSPAN" in cell.attrib:
        result["colspan"] = cell.attrib["COLSPAN"]

    return result


def extract_table_rows(
    table: etree._Element,
    *,
    section_id: str,
    table_group_id: str | None,
    table_id: str,
) -> list[list[dict[str, Any]]]:
    """
    TABLE 내부 TR을 찾아 행/셀 구조를 반환한다.
    """
    rows: list[list[dict[str, Any]]] = []

    for row_index, row in enumerate(table.iter("TR")):
        cells: list[dict[str, Any]] = []

        cell_elements = [child for child in row if child.tag in CELL_TAGS]
        for col_index, child in enumerate(cell_elements):
            cells.append(
                extract_cell(
                    child,
                    section_id=section_id,
                    table_group_id=table_group_id,
                    table_id=table_id,
                    row_index=row_index,
                    col_index=col_index,
                )
            )

        if cells:
            rows.append(cells)

    return rows


def detect_table_unit(table: etree._Element) -> str | None:
    """
    표 내부에서 '(단위 : ...)' 형태의 문자열을 찾아본다.

    1차 MVP에서는 단순 탐색만 한다.
    정교한 단위 표준화는 normalizer 단계에서 담당한다.
    """
    # container 전체의 합성 text는 다른 표의 값까지 섞을 수 있으므로 자손만 본다.
    for element in list(table.iter())[1:]:
        text = element_text(element)

        if "단위" in text and len(text) < 100:
            return text

    return None


def direct_context_texts(container: etree._Element) -> list[str]:
    """TABLE/TABLE-GROUP에서 안전하게 얻는 직접 P/TITLE 텍스트를 반환한다."""
    texts: list[str] = []
    for child in container:
        if child.tag not in {"P", "TITLE"}:
            continue
        text = element_text(child)
        if text:
            texts.append(text)
    return texts


def parse_table(
    table: etree._Element,
    *,
    section_id: str,
    section_path: list[str],
    table_group_id: str | None,
    inherited_unit: str | None,
    inherited_context: list[str],
    table_counter: list[int],
) -> dict[str, Any]:
    """TABLE을 한 번 파싱하고 계층/flat lookup 양쪽에서 재사용한다."""
    table_counter[0] += 1
    table_id = f"table_{table_counter[0]:04d}"
    title_element = get_direct_child(table, "TITLE")
    title = element_text(title_element) if title_element is not None else None
    own_unit = detect_table_unit(table)
    context_parts = [*inherited_context]
    if title:
        context_parts.append(title)

    return {
        "table_id": table_id,
        "title": title,
        "unit": own_unit or inherited_unit,
        # 상속된 group 단위와 표 자체의 단위를 구분할 수 있게 원문 탐지값도 보존한다.
        "own_unit": own_unit,
        "rows": extract_table_rows(
            table,
            section_id=section_id,
            table_group_id=table_group_id,
            table_id=table_id,
        ),
        "section_id": section_id,
        "table_group_id": table_group_id,
        "section_path": section_path.copy(),
        "raw_context": "\n".join(dict.fromkeys(context_parts)),
        "attributes": {
            key.lower(): value
            for key, value in table.attrib.items()
            if key in {"ACLASS", "AFIXTABLE"}
        },
    }


def extract_tables_from_container(
    container: etree._Element,
    section_id: str,
    section_path: list[str],
    table_counter: list[int],
    table_group_counter: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    현재 SECTION에 직접 속한 TABLE / TABLE-GROUP을 파싱한다.

    child SECTION 내부 TABLE은 제외하여
    section 간 표 중복을 방지한다.
    """
    tables: list[dict[str, Any]] = []
    table_groups: list[dict[str, Any]] = []

    for child in iter_transparent_children(container):
        # 자식 section의 내용은 자식 parser가 담당한다.
        if is_section(child):
            continue

        if child.tag == "TABLE":
            tables.append(
                parse_table(
                    child,
                    section_id=section_id,
                    section_path=section_path,
                    table_group_id=None,
                    inherited_unit=None,
                    inherited_context=section_path,
                    table_counter=table_counter,
                )
            )

        elif child.tag == "TABLE-GROUP":
            table_group_counter[0] += 1
            group_id = f"table_group_{table_group_counter[0]:04d}"
            title_element = get_direct_child(child, "TITLE")
            title = element_text(title_element) if title_element is not None else None
            group_unit = detect_table_unit(child)
            context_parts = [*section_path, *direct_context_texts(child)]
            aclass = child.attrib.get("ACLASS")
            if aclass:
                context_parts.append(aclass)
            group_tables = [
                parse_table(
                    table,
                    section_id=section_id,
                    section_path=section_path,
                    table_group_id=group_id,
                    inherited_unit=group_unit,
                    inherited_context=context_parts,
                    table_counter=table_counter,
                )
                for table in child
                if table.tag == "TABLE"
            ]
            table_groups.append(
                {
                    "table_group_id": group_id,
                    "section_id": section_id,
                    "title": title,
                    "aclass": aclass,
                    "adeletetable": child.attrib.get("ADELETETABLE"),
                    "attributes": {
                        key.lower(): value for key, value in child.attrib.items()
                    },
                    "section_path": section_path.copy(),
                    "raw_context": "\n".join(dict.fromkeys(context_parts)),
                    "unit": group_unit,
                    "tables": group_tables,
                }
            )
            tables.extend(group_tables)

    return tables, table_groups


def find_child_sections(container: etree._Element) -> list[etree._Element]:
    """
    현재 container 바로 아래의 SECTION-n을 찾는다.

    LIBRARY가 SECTION을 감싸는 경우도 있으므로
    LIBRARY 한 단계 아래까지 확인한다.
    """
    sections: list[etree._Element] = []

    for child in iter_transparent_children(container):
        if is_section(child):
            sections.append(child)

    return sections


def parse_section(
    section: etree._Element,
    parent_section_id: str | None,
    parent_path: list[str],
    section_counter: list[int],
    table_counter: list[int],
    table_group_counter: list[int],
    all_tables: list[dict[str, Any]],
    all_table_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    SECTION-n 하나와 하위 SECTION을 재귀적으로 파싱한다.
    """
    section_counter[0] += 1

    section_id = f"section_{section_counter[0]:04d}"

    level = get_section_level(section.tag)

    title_element = get_direct_child(section, "TITLE")

    title = (
        element_text(title_element)
        if title_element is not None
        else ""
    )

    current_path = parent_path.copy()

    if title:
        current_path.append(title)

    paragraphs = extract_direct_paragraphs(section)

    section_result = {
        "section_id": section_id,
        "title": title,
        "level": level,
        "parent_section_id": parent_section_id,
        "section_path": current_path,
        "paragraphs": paragraphs,
        "text": "\n".join(paragraphs),
    }

    results = [section_result]

    section_tables, section_table_groups = extract_tables_from_container(
        container=section,
        section_id=section_id,
        section_path=current_path,
        table_counter=table_counter,
        table_group_counter=table_group_counter,
    )

    all_tables.extend(section_tables)
    all_table_groups.extend(section_table_groups)

    child_sections = find_child_sections(section)

    for child_section in child_sections:
        child_results = parse_section(
            section=child_section,
            parent_section_id=section_id,
            parent_path=current_path,
            section_counter=section_counter,
            table_counter=table_counter,
            table_group_counter=table_group_counter,
            all_tables=all_tables,
            all_table_groups=all_table_groups,
        )

        results.extend(child_results)

    return results


def extract_metadata(
    root: etree._Element,
    source_path: Path,
) -> dict[str, Any]:
    """
    문서 기본 메타데이터를 추출한다.

    receipt_date / doc_id는 현재 XML filename을 기반으로
    최소한으로 생성한다.
    향후 manifest.jsonl과 결합하는 것이 더 적절하다.
    """
    document_name = root.find("DOCUMENT-NAME")
    company_name = root.find("COMPANY-NAME")

    report_name = (
        element_text(document_name)
        if document_name is not None
        else None
    )

    corp_name = (
        element_text(company_name)
        if company_name is not None
        else None
    )

    receipt_no = source_path.stem

    return {
        "doc_id": f"periodic_{receipt_no}",
        "corp_name": corp_name,
        "report_name": report_name,
        "receipt_no": receipt_no,
        "document_type": "periodic",
        "source_path": str(source_path),
    }


def parse_periodic_xml(xml_path: str | Path) -> dict[str, Any]:
    """
    DART 정기공시 XML 하나를 파싱한다.

    반환:
        {
            "metadata": {...},
            "sections": [...],
            "table_groups": [...],
            "tables": [...]
        }
    """
    xml_path = Path(xml_path)

    if not xml_path.exists():
        raise FileNotFoundError(
            f"XML 파일을 찾을 수 없습니다: {xml_path}"
        )

    root = parse_xml_root(xml_path)

    metadata = extract_metadata(
        root=root,
        source_path=xml_path,
    )

    body = root.find("BODY")

    if body is None:
        raise ValueError(
            "BODY 태그를 찾을 수 없습니다."
        )

    section_counter = [0]
    table_counter = [0]
    table_group_counter = [0]

    sections: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    table_groups: list[dict[str, Any]] = []

    top_sections = find_child_sections(body)

    for section in top_sections:
        parsed_sections = parse_section(
            section=section,
            parent_section_id=None,
            parent_path=[],
            section_counter=section_counter,
            table_counter=table_counter,
            table_group_counter=table_group_counter,
            all_tables=tables,
            all_table_groups=table_groups,
        )

        sections.extend(parsed_sections)

    return {
        "metadata": metadata,
        "sections": sections,
        "table_groups": table_groups,
        "tables": tables,
    }


def save_parsed_json(
    parsed_document: dict[str, Any],
    output_path: str | Path,
) -> None:
    """
    파싱 결과를 UTF-8 JSON으로 저장한다.
    """
    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            parsed_document,
            file,
            ensure_ascii=False,
            indent=2,
        )


def main() -> None:
    input_path = Path(
        "data/raw/periodic/20240312000736.xml"
    )

    output_path = Path(
        "data/processed/samsung_2023_parsed.json"
    )

    parsed_document = parse_periodic_xml(
        input_path
    )

    save_parsed_json(
        parsed_document,
        output_path,
    )

    print(
        f"Parsing completed: "
        f"{len(parsed_document['sections'])} sections, "
        f"{len(parsed_document['tables'])} tables"
    )

    print(
        f"Saved to: {output_path}"
    )


if __name__ == "__main__":
    main()
