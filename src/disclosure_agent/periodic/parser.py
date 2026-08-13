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


def extract_direct_paragraphs(section: etree._Element) -> list[str]:
    """
    현재 SECTION에 직접 포함된 P만 추출한다.

    자식 SECTION 아래 P까지 재귀적으로 가져오면
    부모 section과 자식 section에서 동일 본문이 중복되므로
    직접 자식 P만 대상으로 한다.

    단, LIBRARY 같은 wrapper 안의 P는 현재 MVP에서는 별도 처리하지 않는다.
    """
    paragraphs: list[str] = []

    for child in section:
        if child.tag != "P":
            continue

        text = element_text(child)

        if text:
            paragraphs.append(text)

    return paragraphs


def extract_cell(cell: etree._Element) -> dict[str, Any]:
    """
    표 셀 하나를 파싱한다.

    TE / TU가 가지고 있는 구조화 속성은
    현재 단계에서는 정규화하지 않고 그대로 보존한다.
    """
    result: dict[str, Any] = {
        "type": cell.tag,
        "text": element_text(cell),
    }

    if "ACODE" in cell.attrib:
        result["acode"] = cell.attrib["ACODE"]

    if "AUNIT" in cell.attrib:
        result["aunit"] = cell.attrib["AUNIT"]

    if "AUNITVALUE" in cell.attrib:
        result["aunit_value"] = cell.attrib["AUNITVALUE"]

    if "ROWSPAN" in cell.attrib:
        result["rowspan"] = cell.attrib["ROWSPAN"]

    if "COLSPAN" in cell.attrib:
        result["colspan"] = cell.attrib["COLSPAN"]

    return result


def extract_table_rows(table: etree._Element) -> list[list[dict[str, Any]]]:
    """
    TABLE 내부 TR을 찾아 행/셀 구조를 반환한다.
    """
    rows: list[list[dict[str, Any]]] = []

    for row in table.iter("TR"):
        cells: list[dict[str, Any]] = []

        for child in row:
            if child.tag in CELL_TAGS:
                cells.append(extract_cell(child))

        if cells:
            rows.append(cells)

    return rows


def detect_table_unit(table: etree._Element) -> str | None:
    """
    표 내부에서 '(단위 : ...)' 형태의 문자열을 찾아본다.

    1차 MVP에서는 단순 탐색만 한다.
    정교한 단위 표준화는 normalizer 단계에서 담당한다.
    """
    for element in table.iter():
        text = element_text(element)

        if "단위" in text and len(text) < 100:
            return text

    return None


def extract_tables_from_container(
    container: etree._Element,
    section_id: str,
    table_counter: list[int],
) -> list[dict[str, Any]]:
    """
    현재 SECTION에 직접 속한 TABLE / TABLE-GROUP을 파싱한다.

    child SECTION 내부 TABLE은 제외하여
    section 간 표 중복을 방지한다.
    """
    tables: list[dict[str, Any]] = []

    for child in container:
        # 자식 section의 내용은 자식 parser가 담당한다.
        if is_section(child):
            continue

        candidates: list[etree._Element] = []

        if child.tag == "TABLE":
            candidates = [child]

        elif child.tag == "TABLE-GROUP":
            candidates = [
                table
                for table in child
                if table.tag == "TABLE"
            ]

        elif child.tag == "LIBRARY":
            # LIBRARY가 단순 wrapper인 경우 내부 TABLE을 처리
            candidates = [
                element
                for element in child
                if element.tag == "TABLE"
            ]

        for table in candidates:
            table_counter[0] += 1

            table_id = f"table_{table_counter[0]:04d}"

            table_title_element = get_direct_child(table, "TITLE")
            table_title = (
                element_text(table_title_element)
                if table_title_element is not None
                else None
            )

            tables.append(
                {
                    "table_id": table_id,
                    "title": table_title,
                    "unit": detect_table_unit(table),
                    "rows": extract_table_rows(table),
                    "section_id": section_id,
                    "attributes": {
                        key.lower(): value
                        for key, value in table.attrib.items()
                        if key in {"ACLASS", "AFIXTABLE"}
                    },
                }
            )

    return tables


def find_child_sections(container: etree._Element) -> list[etree._Element]:
    """
    현재 container 바로 아래의 SECTION-n을 찾는다.

    LIBRARY가 SECTION을 감싸는 경우도 있으므로
    LIBRARY 한 단계 아래까지 확인한다.
    """
    sections: list[etree._Element] = []

    for child in container:
        if is_section(child):
            sections.append(child)

        elif child.tag == "LIBRARY":
            for library_child in child:
                if is_section(library_child):
                    sections.append(library_child)

    return sections


def parse_section(
    section: etree._Element,
    parent_section_id: str | None,
    parent_path: list[str],
    section_counter: list[int],
    table_counter: list[int],
    all_tables: list[dict[str, Any]],
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

    section_tables = extract_tables_from_container(
        container=section,
        section_id=section_id,
        table_counter=table_counter,
    )

    all_tables.extend(section_tables)

    child_sections = find_child_sections(section)

    for child_section in child_sections:
        child_results = parse_section(
            section=child_section,
            parent_section_id=section_id,
            parent_path=current_path,
            section_counter=section_counter,
            table_counter=table_counter,
            all_tables=all_tables,
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
            "tables": [...]
        }
    """
    xml_path = Path(xml_path)

    if not xml_path.exists():
        raise FileNotFoundError(
            f"XML 파일을 찾을 수 없습니다: {xml_path}"
        )

    try:
        parser = etree.XMLParser(
            recover=True,
            encoding="utf-8",
            huge_tree=True,
        )

        tree = etree.parse(
            str(xml_path),
            parser,
        )

    except etree.XMLSyntaxError as exc:
        raise ValueError(
            f"XML 파싱에 실패했습니다: {xml_path}"
        ) from exc

    root = tree.getroot()

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

    sections: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []

    top_sections = find_child_sections(body)

    for section in top_sections:
        parsed_sections = parse_section(
            section=section,
            parent_section_id=None,
            parent_path=[],
            section_counter=section_counter,
            table_counter=table_counter,
            all_tables=tables,
        )

        sections.extend(parsed_sections)

    return {
        "metadata": metadata,
        "sections": sections,
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
        "data/raw/20240312000736.xml"
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