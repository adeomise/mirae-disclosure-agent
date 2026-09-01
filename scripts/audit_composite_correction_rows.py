"""Audit unmapped composite correction rows against their source XML structure."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from disclosure_agent.event.correction_fields import normalize_correction_label
from disclosure_agent.event.extractor import extract_event
from disclosure_agent.event.parser import extract_table_id, parse_exchange_document
from disclosure_agent.retrieval.metadata_filter import load_manifest, resolve_filing_ref

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
DEFAULT_SAMPLE_LIMIT = 5
SUPPORTED_SUBTYPES = {
    "단일판매공급계약체결": "supply_contract",
    "신규시설투자등": "facility_investment",
}

PATTERN_BY_LABEL = {
    normalize_correction_label(
        "2. 계약내역 - 계약금액(원) - 매출액대비(%)"
    ): "contract_amount_and_revenue_ratio",
    normalize_correction_label("2. 계약내역"): "contract_details_container",
    normalize_correction_label(
        "8. 공시유보 관련내용 - 유보사유 - 유보기한"
    ): "disclosure_reason_and_hold_until",
    normalize_correction_label("5. 계약기간 - 시작일 - 종료일"): "contract_period_start_and_end",
}
PATTERN_ORDER = tuple(dict.fromkeys(PATTERN_BY_LABEL.values()))


def detect_pattern(raw_field: str | None) -> str | None:
    """Return a target pattern only for an exact normalized composite label."""
    return PATTERN_BY_LABEL.get(normalize_correction_label(raw_field))


def _direct_rows(table: Tag) -> list[Tag]:
    return [row for row in table.find_all("tr") if row.find_parent("table") is table]


def inspect_source_row(xml_path: Path, *, table_id: str, row_index: int) -> dict[str, Any]:
    """Return bounded structural metadata for one parser-addressed source row."""
    root = BeautifulSoup(xml_path.read_bytes(), "html.parser")
    table = next(
        (
            candidate
            for index, candidate in enumerate(root.find_all("table"))
            if extract_table_id(candidate, index) == table_id
        ),
        None,
    )
    if table is None:
        raise ValueError(f"source table not found: {table_id}")
    rows = _direct_rows(table)
    if row_index < 0 or row_index >= len(rows):
        raise ValueError(f"source row not found: {table_id}[{row_index}]")

    row = rows[row_index]
    cells = row.find_all("td", recursive=False)
    parent = row.parent if isinstance(row.parent, Tag) else None
    header_rows = []
    for preceding in rows[:row_index]:
        texts = [
            cell.get_text(" ", strip=True) for cell in preceding.find_all("td", recursive=False)
        ]
        if "정정항목" in texts and "정정전" in texts and "정정후" in texts:
            header_rows.append(texts)

    return {
        "td_count": len(cells),
        "cells": [
            {
                "index": index,
                "text": " ".join(cell.get_text(" ", strip=True).split()),
                "colspan": cell.get("colspan"),
                "rowspan": cell.get("rowspan"),
                "nested_table": cell.find("table") is not None,
            }
            for index, cell in enumerate(cells)
        ],
        "has_colspan": any(cell.get("colspan") for cell in cells),
        "has_rowspan": any(cell.get("rowspan") for cell in cells),
        "has_nested_table": any(cell.find("table") is not None for cell in cells),
        "section": (
            parent.name if parent and parent.name in {"thead", "tbody", "tfoot"} else "table"
        ),
        "correction_header": header_rows[-1] if header_rows else [],
    }


def classify_occurrence(pattern: str, row_shape: Mapping[str, Any]) -> str:
    """Classify from semantic target plus actual row structure, never value keywords."""
    if pattern == "contract_details_container":
        return "ambiguous_container"
    if row_shape.get("has_nested_table") or int(row_shape.get("td_count", 0)) > 3:
        return "parser_splittable"
    return "source_composite"


def make_occurrence(
    *,
    record: Mapping[str, Any],
    filing: Any,
    event_type: str,
    parsed_change: Mapping[str, Any],
    extracted_change: Mapping[str, Any],
    row_shape: Mapping[str, Any],
) -> dict[str, Any] | None:
    pattern = detect_pattern(str(parsed_change.get("raw_field") or ""))
    if pattern is None or extracted_change.get("canonical_field") is not None:
        return None
    table_id = str(parsed_change.get("table_id") or "")
    row_index = int(parsed_change.get("row_index", -1))
    receipt_no = str(getattr(filing, "receipt_no", "") or Path(filing.main_document).stem)
    return {
        "doc_id": str(record.get("doc_id") or f"exchange_{receipt_no}"),
        "receipt_no": receipt_no,
        "corp_name": str(record.get("corp_name") or getattr(filing, "corp_name", "") or ""),
        "event_type": event_type,
        "is_correction": bool(record.get("is_correction")),
        "report_nm": str(record.get("report_nm") or getattr(filing, "report_name", "") or ""),
        "normalized_pattern": pattern,
        "raw_group": parsed_change.get("raw_group"),
        "raw_field": parsed_change.get("raw_field"),
        "raw_unit": parsed_change.get("raw_unit"),
        "before": parsed_change.get("before"),
        "after": parsed_change.get("after"),
        "table_id": table_id,
        "row_index": row_index,
        "cell_indexes": {
            "field": parsed_change.get("field_cell_index"),
            "before": parsed_change.get("before_cell_index"),
            "after": parsed_change.get("after_cell_index"),
        },
        "parser_output": dict(parsed_change),
        "extractor_output": dict(extracted_change),
        "raw_xml_row_shape": dict(row_shape),
        "classification": classify_occurrence(pattern, row_shape),
    }


def aggregate_occurrences(
    occurrences: Iterable[Mapping[str, Any]], *, sample_limit: int = DEFAULT_SAMPLE_LIMIT
) -> dict[str, Any]:
    """Aggregate deterministically, deduplicating company/receipt sample coverage."""
    ordered = sorted(
        (dict(item) for item in occurrences),
        key=lambda item: (
            PATTERN_ORDER.index(item["normalized_pattern"]),
            item["receipt_no"],
            item["row_index"],
            item["raw_field"],
        ),
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in ordered:
        grouped[item["normalized_pattern"]].append(item)

    patterns: dict[str, Any] = {}
    for pattern in PATTERN_ORDER:
        items = grouped.get(pattern, [])
        classification_counts = Counter(item["classification"] for item in items)
        receipts = sorted({item["receipt_no"] for item in items})
        companies = sorted({item["corp_name"] for item in items if item["corp_name"]})
        raw_field_counts = Counter(str(item["raw_field"]) for item in items)
        event_type_counts = Counter(str(item["event_type"]) for item in items)
        report_name_counts = Counter(str(item["report_nm"]) for item in items)
        samples: list[dict[str, Any]] = []
        used_receipts: set[str] = set()
        used_companies: set[str] = set()
        # Prefer distinct companies, then fill with distinct receipts.
        for prefer_new_company in (True, False):
            for item in items:
                if len(samples) >= sample_limit:
                    break
                if item["receipt_no"] in used_receipts:
                    continue
                if prefer_new_company and item["corp_name"] in used_companies:
                    continue
                samples.append(item)
                used_receipts.add(item["receipt_no"])
                used_companies.add(item["corp_name"])
        patterns[pattern] = {
            "occurrences": len(items),
            "unique_receipts": len(receipts),
            "unique_company_count": len(companies),
            "companies": companies,
            "raw_field_counts": dict(sorted(raw_field_counts.items())),
            "event_type_counts": dict(sorted(event_type_counts.items())),
            "report_name_counts": dict(sorted(report_name_counts.items())),
            "classification_counts": dict(sorted(classification_counts.items())),
            "mixed_template": len(classification_counts) > 1,
            "representative_samples": samples,
        }

    return {
        "composite_candidate_count": len(ordered),
        "classification_counts": dict(
            sorted(Counter(item["classification"] for item in ordered).items())
        ),
        "patterns": patterns,
    }


def audit_records(
    records: Iterable[dict[str, Any]],
    *,
    corpus_root: Path,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    resolver: Callable[..., Any] = resolve_filing_ref,
    parser: Callable[..., dict[str, Any]] = parse_exchange_document,
    extractor: Callable[[dict[str, Any]], dict[str, Any]] = extract_event,
    row_inspector: Callable[..., dict[str, Any]] = inspect_source_row,
) -> dict[str, Any]:
    supported = corrections = unmapped = 0
    by_event_type = Counter()
    occurrences = []
    for record in records:
        event_type = SUPPORTED_SUBTYPES.get(record.get("doc_subtype"))
        if record.get("doc_group") != "exchange" or event_type is None:
            continue
        supported += 1
        by_event_type[event_type] += 1
        if not record.get("is_correction"):
            continue
        corrections += 1
        filing = resolver(record, corpus_root=corpus_root)
        parsed = parser(
            filing.main_document,
            corp_name=filing.corp_name,
            report_name=filing.report_name,
            receipt_date=filing.receipt_date,
        )
        extracted = extractor(parsed)
        parsed_changes = (parsed.get("correction") or {}).get("changes") or []
        extracted_changes = (extracted.get("correction") or {}).get("changes") or []
        if len(parsed_changes) != len(extracted_changes):
            raise ValueError(f"parser/extractor correction mismatch: {record.get('doc_id')}")
        for parsed_change, extracted_change in zip(parsed_changes, extracted_changes, strict=True):
            if extracted_change.get("canonical_field") is None:
                unmapped += 1
            if detect_pattern(parsed_change.get("raw_field")) is None:
                continue
            shape = row_inspector(
                filing.main_document,
                table_id=parsed_change["table_id"],
                row_index=parsed_change["row_index"],
            )
            occurrence = make_occurrence(
                record=record,
                filing=filing,
                event_type=event_type,
                parsed_change=parsed_change,
                extracted_change=extracted_change,
                row_shape=shape,
            )
            if occurrence is not None:
                occurrences.append(occurrence)

    result = {
        "supported_documents": supported,
        "supported_by_event_type": dict(sorted(by_event_type.items())),
        "correction_documents": corrections,
        "total_unmapped_changes": unmapped,
    }
    result.update(aggregate_occurrences(occurrences, sample_limit=sample_limit))
    return result


def render_summary(result: Mapping[str, Any]) -> str:
    lines = [
        "Composite correction audit",
        "==========================",
        f"supported documents: {result['supported_documents']}",
        f"correction documents: {result['correction_documents']}",
        f"total unmapped changes: {result['total_unmapped_changes']}",
        f"composite candidates: {result['composite_candidate_count']}",
        "",
        "by pattern:",
    ]
    for pattern in PATTERN_ORDER:
        details = result["patterns"][pattern]
        lines.extend(
            [
                f"  {pattern}",
                f"    occurrences: {details['occurrences']}",
                f"    companies: {details['unique_company_count']}",
                f"    classifications: {details['classification_counts']}",
                f"    mixed_template: {details['mixed_template']}",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--corpus-root")
    argument_parser.add_argument("--manifest")
    argument_parser.add_argument("--json-out")
    argument_parser.add_argument("--sample-limit", type=int, default=DEFAULT_SAMPLE_LIMIT)
    args = argument_parser.parse_args()
    raw_root = args.corpus_root or os.environ.get(CORPUS_ROOT_ENV)
    if not raw_root:
        argument_parser.error(f"pass --corpus-root or set {CORPUS_ROOT_ENV}")
    corpus_root = Path(raw_root).expanduser()
    manifest = Path(args.manifest).expanduser() if args.manifest else corpus_root / "manifest.jsonl"
    result = audit_records(
        load_manifest(manifest), corpus_root=corpus_root, sample_limit=args.sample_limit
    )
    print(render_summary(result))
    if args.json_out:
        output = Path(args.json_out).expanduser()
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
