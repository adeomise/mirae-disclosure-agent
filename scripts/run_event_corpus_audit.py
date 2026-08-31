"""Audit Parser -> Extractor -> Adapter coverage for supported exchange events."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from disclosure_agent.event.adapter import adapt_extracted_event
from disclosure_agent.event.extractor import extract_event
from disclosure_agent.event.parser import parse_exchange_document
from disclosure_agent.retrieval.metadata_filter import load_manifest, resolve_filing_ref

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"
EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
DEFAULT_FAILURE_SAMPLES = 3
MAX_ERROR_MESSAGE_LENGTH = 240

SUPPORTED_SUBTYPES = {
    "단일판매공급계약체결": "supply_contract",
    "신규시설투자등": "facility_investment",
}
STAGES = ("source_resolution", "parser", "extractor", "adapter")


class AuditConfigError(Exception):
    """The corpus configuration is invalid, so the audit cannot start."""


def _bounded_message(error: Exception) -> str:
    message = " ".join(str(error).split())
    if len(message) > MAX_ERROR_MESSAGE_LENGTH:
        return f"{message[: MAX_ERROR_MESSAGE_LENGTH - 3]}..."
    return message


def _failure_detail(record: dict[str, Any], stage: str, error: Exception) -> dict[str, Any]:
    return {
        "doc_id": str(record.get("doc_id") or ""),
        "corp_name": str(record.get("corp_name") or ""),
        "doc_subtype": str(record.get("doc_subtype") or ""),
        "is_correction": bool(record.get("is_correction")),
        "stage": stage,
        "exception_type": type(error).__name__,
        "message": _bounded_message(error),
    }


def _new_summary(total: int) -> dict[str, Any]:
    return {
        "total_manifest_records": total,
        "supported_documents": 0,
        "by_event_type": {event_type: 0 for event_type in SUPPORTED_SUBTYPES.values()},
        "by_correction_status": {"original": 0, "correction": 0},
        "by_event_type_and_correction_status": {
            event_type: {"original": 0, "correction": 0}
            for event_type in SUPPORTED_SUBTYPES.values()
        },
        "source_resolve_success": 0,
        "source_resolve_failure": 0,
        "parser_success": 0,
        "parser_failure": 0,
        "extractor_success": 0,
        "extractor_failure": 0,
        "adapter_success": 0,
        "adapter_failure": 0,
        "correction_documents": 0,
        "mapped_field_changes": 0,
        "unmapped_field_changes": 0,
        "unmapped_raw_field_counts": {},
        "failure_by_stage": {stage: 0 for stage in STAGES},
        "failure_samples": {stage: [] for stage in STAGES},
    }


def audit_records(
    records: Iterable[dict[str, Any]],
    *,
    corpus_root: Path,
    failure_sample_limit: int = DEFAULT_FAILURE_SAMPLES,
    resolver: Callable[..., Any] = resolve_filing_ref,
    parser: Callable[..., dict[str, Any]] = parse_exchange_document,
    extractor: Callable[[dict[str, Any]], dict[str, Any]] = extract_event,
    adapter: Callable[[dict[str, Any]], Any] = adapt_extracted_event,
) -> dict[str, Any]:
    """Run every supported record, isolating a document at its first failed stage."""
    record_list = list(records)
    summary = _new_summary(len(record_list))
    unmapped_counts: Counter[str] = Counter()

    for record in record_list:
        event_type = SUPPORTED_SUBTYPES.get(record.get("doc_subtype"))
        if record.get("doc_group") != "exchange" or event_type is None:
            continue

        correction_status = "correction" if bool(record.get("is_correction")) else "original"
        summary["supported_documents"] += 1
        summary["by_event_type"][event_type] += 1
        summary["by_correction_status"][correction_status] += 1
        summary["by_event_type_and_correction_status"][event_type][correction_status] += 1
        if correction_status == "correction":
            summary["correction_documents"] += 1

        try:
            filing = resolver(record, corpus_root=corpus_root)
        except Exception as error:  # a corpus defect is an audit result
            _record_failure(summary, record, "source_resolution", error, failure_sample_limit)
            continue
        summary["source_resolve_success"] += 1

        try:
            parsed = parser(
                filing.main_document,
                corp_name=filing.corp_name,
                report_name=filing.report_name,
                receipt_date=filing.receipt_date,
            )
        except Exception as error:
            _record_failure(summary, record, "parser", error, failure_sample_limit)
            continue
        summary["parser_success"] += 1

        try:
            extracted = extractor(parsed)
        except Exception as error:
            _record_failure(summary, record, "extractor", error, failure_sample_limit)
            continue
        summary["extractor_success"] += 1

        if correction_status == "correction":
            correction = extracted.get("correction") or {}
            for change in correction.get("changes") or ():
                if change.get("canonical_field"):
                    summary["mapped_field_changes"] += 1
                else:
                    summary["unmapped_field_changes"] += 1
                    unmapped_counts[str(change.get("raw_field") or "<empty>")] += 1

        try:
            adapter(extracted)
        except Exception as error:
            _record_failure(summary, record, "adapter", error, failure_sample_limit)
            continue
        summary["adapter_success"] += 1

    summary["unmapped_raw_field_counts"] = dict(
        sorted(unmapped_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    return summary


def _record_failure(
    summary: dict[str, Any],
    record: dict[str, Any],
    stage: str,
    error: Exception,
    sample_limit: int,
) -> None:
    counter = "source_resolve_failure" if stage == "source_resolution" else f"{stage}_failure"
    summary[counter] += 1
    summary["failure_by_stage"][stage] += 1
    samples = summary["failure_samples"][stage]
    if len(samples) < sample_limit:
        samples.append(_failure_detail(record, stage, error))


def run_audit(
    *, corpus_root: Path, manifest_path: Path, failure_sample_limit: int = DEFAULT_FAILURE_SAMPLES
) -> dict[str, Any]:
    try:
        records = load_manifest(manifest_path)
    except Exception as error:
        message = f"cannot load manifest {manifest_path}: {_bounded_message(error)}"
        raise AuditConfigError(message) from error
    return audit_records(
        records, corpus_root=corpus_root, failure_sample_limit=failure_sample_limit
    )


def render_summary(summary: dict[str, Any]) -> str:
    lines = [
        "Event corpus audit",
        "==================",
        f"manifest records: {summary['total_manifest_records']}",
        f"supported documents: {summary['supported_documents']}",
        "",
    ]
    for event_type in ("supply_contract", "facility_investment"):
        counts = summary["by_event_type_and_correction_status"][event_type]
        lines.extend(
            [
                f"{event_type}:",
                f"  original: {counts['original']}",
                f"  correction: {counts['correction']}",
            ]
        )
    lines.extend(["", "pipeline:"])
    for label, prefix in (
        ("source resolve", "source_resolve"),
        ("parser", "parser"),
        ("extractor", "extractor"),
        ("adapter", "adapter"),
    ):
        lines.append(
            f"  {label}: success {summary[prefix + '_success']}, "
            f"failure {summary[prefix + '_failure']}"
        )
    lines.extend(
        [
            "",
            "correction fields:",
            f"  documents: {summary['correction_documents']}",
            f"  mapped: {summary['mapped_field_changes']}",
            f"  unmapped: {summary['unmapped_field_changes']}",
            "",
            "top unmapped raw fields:",
        ]
    )
    items = list(summary["unmapped_raw_field_counts"].items())[:10]
    lines.extend([f"  {raw_field}: {count}" for raw_field, count in items] or ["  (none)"])
    lines.extend(["", "failure samples:"])
    for stage in STAGES:
        lines.append(f"  {stage}: {summary['failure_by_stage'][stage]}")
        for sample in summary["failure_samples"][stage]:
            lines.append(
                f"    - {sample['doc_id'] or '-'} {sample['exception_type']}: {sample['message']}"
            )
    return "\n".join(lines)


def resolve_corpus_root(argument: str | None) -> Path:
    raw = argument or os.environ.get(CORPUS_ROOT_ENV)
    if not raw:
        raise AuditConfigError(
            f"corpus root is required: pass --corpus-root or set {CORPUS_ROOT_ENV}"
        )
    path = Path(raw).expanduser()
    if not path.is_dir():
        raise AuditConfigError(f"corpus root is not a directory: {path}")
    return path


def resolve_manifest(argument: str | None, corpus_root: Path) -> Path:
    raw = argument or os.environ.get(MANIFEST_ENV)
    path = Path(raw).expanduser() if raw else corpus_root / "manifest.jsonl"
    if not path.is_file():
        raise AuditConfigError(f"manifest not found: {path}")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", help=f"Corpus root; defaults to {CORPUS_ROOT_ENV}.")
    parser.add_argument(
        "--manifest", help="Manifest path; defaults to <corpus-root>/manifest.jsonl."
    )
    parser.add_argument(
        "--json-out", help="Write the aggregate summary and bounded samples as JSON."
    )
    parser.add_argument("--failure-samples", type=int, default=DEFAULT_FAILURE_SAMPLES)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.failure_samples < 0:
            raise AuditConfigError("--failure-samples must be non-negative")
        corpus_root = resolve_corpus_root(args.corpus_root)
        manifest_path = resolve_manifest(args.manifest, corpus_root)
        summary = run_audit(
            corpus_root=corpus_root,
            manifest_path=manifest_path,
            failure_sample_limit=args.failure_samples,
        )
        if args.json_out:
            output = Path(args.json_out).expanduser()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    except (AuditConfigError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    print(render_summary(summary))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
