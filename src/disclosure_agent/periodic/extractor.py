"""정기공시 재무정보 추출 모듈.

역할:
- 파싱된 표와 주석에서 재무제표 항목, 수치, 단위, 기간 후보를 추출한다.

예정 입력:
- ParsedDocument
- 추출 대상 항목 정의

예정 출력:
- Fact 목록과 원문 Evidence 참조

TODO:
- 재무제표와 주석 식별 규칙 합의
- 단위 및 기간 정규화 정책 구현
- 원문 값 손실 방지 검증 추가
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from disclosure_agent.common.enums import (
    EvidenceLocationType,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import Evidence, Fact

METRIC_ALIASES: dict[MetricConcept, set[str]] = {
    MetricConcept.REVENUE: {
        "매출",
        "매출액",
        "수익(매출액)",
        "영업수익",
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


def resolve_source_unit(
    table: dict[str, Any],
    table_group: dict[str, Any] | None,
) -> str | None:
    """행별 단위 문구를 표 전체 단위로 오인하지 않고 같은 source 안에서 결정한다."""
    for row in table.get("rows", []):
        if len(row) != 1:
            break
        text = cell_text(row[0])
        if "단위" in text:
            return extract_raw_unit(text)
    if table_group is not None:
        return extract_raw_unit(table_group.get("unit"))
    return extract_raw_unit(table.get("unit"))


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
        raw_unit = resolve_source_unit(table, table_group)
        accounting_scope = infer_accounting_scope(table, table_group)

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
