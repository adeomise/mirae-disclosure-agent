"""Minimal local document retrieval for the Q01 and Q02 vertical slices."""

import json
from pathlib import Path
from typing import Any

from disclosure_agent.common.enums import DocumentClass
from disclosure_agent.common.schemas import QueryPlan
from disclosure_agent.event.parser import parse_exchange_document
from disclosure_agent.periodic.parser import parse_periodic_xml
from disclosure_agent.retrieval.metadata_filter import (
    matches_event_history_metadata,
    matches_event_metadata,
    matches_periodic_metadata,
)


def retrieve_periodic_documents(
    query_plan: QueryPlan,
    corpus_dir: str | Path,
) -> list[dict[str, Any]]:
    """Parse and select local XML documents matching Q01 metadata."""
    if query_plan.document_class is not DocumentClass.PERIODIC:
        return []

    corpus_path = Path(corpus_dir)
    if not corpus_path.exists():
        return []

    matches: list[dict[str, Any]] = []
    xml_paths = sorted(
        path for path in corpus_path.rglob("*") if path.is_file() and path.suffix.lower() == ".xml"
    )

    for xml_path in xml_paths:
        try:
            parsed_document = parse_periodic_xml(xml_path)
        except (OSError, ValueError):
            continue

        if matches_periodic_metadata(parsed_document["metadata"], query_plan):
            matches.append(parsed_document)

    return matches


def retrieve_event_documents(
    query_plan: QueryPlan,
    corpus_dir: str | Path,
) -> list[dict[str, Any]]:
    """Parse event XML only when an explicit matching metadata sidecar exists."""
    if query_plan.document_class is not DocumentClass.EVENT:
        return []

    corpus_path = Path(corpus_dir)
    if not corpus_path.exists():
        return []

    matches: list[dict[str, Any]] = []
    xml_paths = sorted(
        path for path in corpus_path.rglob("*") if path.is_file() and path.suffix.lower() == ".xml"
    )
    for xml_path in xml_paths:
        metadata_path = xml_path.with_suffix(".meta.json")
        if not metadata_path.exists():
            continue

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict) or not matches_event_metadata(metadata, query_plan):
            continue

        try:
            parsed_document = parse_exchange_document(
                xml_path,
                corp_name=str(metadata["corp_name"]),
                report_name=str(metadata["report_name"]),
                receipt_date=str(metadata["receipt_date"]),
            )
        except (KeyError, OSError, ValueError):
            continue
        matches.append(parsed_document)

    return matches


def retrieve_event_history_documents(
    query_plan: QueryPlan,
    corpus_dir: str | Path,
) -> list[dict[str, Any]]:
    """Parse all explicit original/correction candidates for one Q13 chain."""
    if query_plan.document_class is not DocumentClass.EVENT:
        return []

    corpus_path = Path(corpus_dir)
    if not corpus_path.exists():
        return []

    matches: list[dict[str, Any]] = []
    xml_paths = sorted(
        path
        for path in corpus_path.rglob("*")
        if path.is_file() and path.suffix.lower() == ".xml"
    )
    for xml_path in xml_paths:
        metadata_path = xml_path.with_suffix(".meta.json")
        if not metadata_path.exists():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict) or not matches_event_history_metadata(
            metadata,
            query_plan,
        ):
            continue
        try:
            parsed_document = parse_exchange_document(
                xml_path,
                corp_name=str(metadata["corp_name"]),
                report_name=str(metadata["report_name"]),
                receipt_date=str(metadata["receipt_date"]),
            )
        except (KeyError, OSError, ValueError):
            continue
        related = metadata.get("related_disclosures")
        parsed_document["metadata"]["related_disclosures"] = (
            related if isinstance(related, list) else []
        )
        chain_complete = metadata.get("chain_complete")
        parsed_document["metadata"]["chain_complete"] = (
            chain_complete if isinstance(chain_complete, bool) else None
        )
        matches.append(parsed_document)
    return matches
