"""Q01 adapter from parsed periodic tables to Fact and Evidence contracts."""

import re
from typing import Any

from disclosure_agent.common.enums import (
    EvidenceLocationType,
    FactAccountingScope,
    MetricConcept,
    QueryAccountingScope,
)
from disclosure_agent.common.schemas import Evidence, Fact, QueryPlan

NUMERIC_PATTERN = re.compile(r"^\(?[-+]?\d[\d,]*(?:\.\d+)?\)?$")
UNIT_PATTERN = re.compile(r"\ub2e8\uc704\s*[:\uff1a]\s*([^)\]\n]+)")


def extract_q01_revenue(
    parsed_document: dict[str, Any],
    query_plan: QueryPlan,
) -> tuple[Fact, Evidence] | None:
    """Extract one consolidated revenue value while retaining its raw location."""
    if (
        query_plan.accounting_scope is not QueryAccountingScope.CONSOLIDATED
        or MetricConcept.REVENUE not in query_plan.metrics
        or not query_plan.periods
        or query_plan.periods[0].year is None
    ):
        return None

    fiscal_year = query_plan.periods[0].year
    sections = {
        section["section_id"]: section
        for section in parsed_document.get("sections", [])
    }

    for table in parsed_document.get("tables", []):
        section = sections.get(table.get("section_id"), {})
        section_path = list(section.get("section_path") or [])
        detected_scope = _detect_accounting_scope(table, section_path)
        if detected_scope is not FactAccountingScope.CONSOLIDATED:
            continue

        extracted = _extract_from_table(table, fiscal_year)
        if extracted is None:
            continue

        row_index, label_index, value_index = extracted
        rows = table["rows"]
        row = rows[row_index]
        label_cell = row[label_index]
        value_cell = row[value_index]
        raw_unit = _extract_raw_unit(table, value_cell)
        if not raw_unit:
            continue

        metadata = parsed_document.get("metadata", {})
        doc_id = str(metadata.get("doc_id") or "")
        company = str(metadata.get("corp_name") or "")
        table_id = str(table.get("table_id") or "unknown_table")
        if not doc_id or not company:
            continue

        evidence_id = f"evidence_{doc_id}_{table_id}_r{row_index}_c{value_index}"
        evidence = Evidence(
            evidence_id=evidence_id,
            doc_id=doc_id,
            location_type=EvidenceLocationType.TABLE,
            section_path=section_path,
            table_name=table.get("title"),
            row_label=str(label_cell.get("text") or ""),
            column_label=_column_label(rows, row_index, value_index),
            raw_text=" | ".join(str(cell.get("text") or "") for cell in row),
        )
        fact = Fact(
            fact_id=f"fact_{doc_id}_revenue_{fiscal_year}",
            company=company,
            metric=MetricConcept.REVENUE,
            fiscal_year=fiscal_year,
            fiscal_period="FY",
            accounting_scope=detected_scope,
            raw_value=str(value_cell.get("text") or ""),
            raw_unit=raw_unit,
            evidence_ids=[evidence_id],
        )
        return fact, evidence

    return None


def _detect_accounting_scope(
    table: dict[str, Any],
    section_path: list[str],
) -> FactAccountingScope:
    """Conservatively infer scope at the adapter boundary from explicit labels.

    The parser contract does not yet expose an accounting-scope field. Ambiguous
    or unlabeled tables therefore remain UNKNOWN instead of inheriting the query.
    """
    context = "".join(
        [
            *section_path,
            str(table.get("title") or ""),
        ]
    )
    normalized_context = re.sub(r"\s+", "", context).casefold()
    consolidated_markers = (
        "\uc5f0\uacb0\uc7ac\ubb34",
        "\uc5f0\uacb0\uc190\uc775",
        "\uc5f0\uacb0\ud3ec\uad04\uc190\uc775",
        "consolidatedfinancial",
        "consolidatedincome",
    )
    separate_markers = (
        "\ubcc4\ub3c4\uc7ac\ubb34",
        "\ubcc4\ub3c4\uc190\uc775",
        "\uac1c\ubcc4\uc7ac\ubb34",
        "\uac1c\ubcc4\uc190\uc775",
        "separatefinancial",
        "separateincome",
        "individualfinancial",
        "individualincome",
    )
    is_consolidated = any(marker in normalized_context for marker in consolidated_markers)
    is_separate = any(marker in normalized_context for marker in separate_markers)

    if is_consolidated == is_separate:
        return FactAccountingScope.UNKNOWN
    if is_consolidated:
        return FactAccountingScope.CONSOLIDATED
    return FactAccountingScope.SEPARATE


def _extract_from_table(
    table: dict[str, Any],
    fiscal_year: int,
) -> tuple[int, int, int] | None:
    rows = table.get("rows") or []
    for row_index, row in enumerate(rows):
        label_index = _revenue_label_index(row)
        if label_index is None:
            continue

        value_index = _value_column(rows, row_index, label_index, fiscal_year)
        if value_index is not None:
            return row_index, label_index, value_index

    return None


def _revenue_label_index(row: list[dict[str, Any]]) -> int | None:
    for index, cell in enumerate(row):
        text = str(cell.get("text") or "").replace(" ", "")
        acode = str(cell.get("acode") or "").casefold()
        if "\ub9e4\ucd9c\uc561" in text or "revenue" in acode:
            return index
    return None


def _value_column(
    rows: list[list[dict[str, Any]]],
    row_index: int,
    label_index: int,
    fiscal_year: int,
) -> int | None:
    target_row = rows[row_index]
    for header_row in rows[:row_index]:
        for column_index, cell in enumerate(header_row):
            if (
                str(fiscal_year) in str(cell.get("text") or "")
                and column_index != label_index
                and column_index < len(target_row)
                and _is_numeric_cell(target_row[column_index])
            ):
                return column_index
    return None


def _is_numeric_cell(cell: dict[str, Any]) -> bool:
    text = str(cell.get("text") or "").replace(" ", "")
    return NUMERIC_PATTERN.fullmatch(text) is not None


def _extract_raw_unit(table: dict[str, Any], value_cell: dict[str, Any]) -> str | None:
    table_unit = str(table.get("unit") or "")
    unit_match = UNIT_PATTERN.search(table_unit)
    if unit_match:
        return unit_match.group(1).strip()

    for key in ("aunit_value", "aunit"):
        value = value_cell.get(key)
        if value:
            return str(value)
    return table_unit or None


def _column_label(
    rows: list[list[dict[str, Any]]],
    row_index: int,
    value_index: int,
) -> str | None:
    labels = [
        str(row[value_index].get("text") or "")
        for row in rows[:row_index]
        if value_index < len(row) and row[value_index].get("text")
    ]
    return " / ".join(labels) or None
