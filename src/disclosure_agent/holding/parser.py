"""Minimal structural parser for DART major-shareholding XML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from disclosure_agent.periodic.parser import CELL_TAGS, element_text, parse_xml_root


def parse_holding_xml(xml_path: str | Path, *, doc_id: str) -> dict[str, Any]:
    """Parse a holding document without assigning periodic document semantics."""
    source_path = Path(xml_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"XML 파일을 찾을 수 없습니다: {source_path}")
    if not doc_id.strip():
        raise ValueError("holding parser에는 명시적인 doc_id가 필요합니다.")

    root = parse_xml_root(source_path)
    tables: list[dict[str, Any]] = []

    for table_number, table in enumerate(root.iter("TABLE"), start=1):
        table_id = f"table_{table_number:04d}"
        rows: list[list[dict[str, Any]]] = []
        direct_rows = [
            row
            for row in table.iter("TR")
            if next(row.iterancestors("TABLE"), None) is table
        ]

        for row_index, row in enumerate(direct_rows):
            cells: list[dict[str, Any]] = []
            cell_elements = [child for child in row if child.tag in CELL_TAGS]
            for column_index, cell in enumerate(cell_elements):
                parsed_cell: dict[str, Any] = {
                    "type": cell.tag,
                    "text": element_text(cell),
                    "provenance": {
                        "table_id": table_id,
                        "row_index": row_index,
                        "column_index": column_index,
                    },
                }
                if "ACODE" in cell.attrib:
                    parsed_cell["acode"] = cell.attrib["ACODE"]
                cells.append(parsed_cell)
            if cells:
                rows.append(cells)

        tables.append({"table_id": table_id, "rows": rows})

    return {
        "metadata": {
            "doc_id": doc_id.strip(),
            "document_type": "holding",
            "source_path": str(source_path),
        },
        "tables": tables,
    }
