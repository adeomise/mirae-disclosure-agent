"""Deterministic latest-state resolution for an event correction chain."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from disclosure_agent.common.dates import normalize_filing_date
from disclosure_agent.common.schemas import (
    Event,
    EventHistory,
    Evidence,
    ResolvedEventState,
    ResolvedFieldValue,
)
from disclosure_agent.event.adapter import EventAdapterResult
from disclosure_agent.event.contract_adapter import build_event_id
from disclosure_agent.event.linker import ChainEntry, FilingChain


def resolve_latest_event_state(
    *,
    root_event: Event,
    history: EventHistory,
    chain: FilingChain,
    documents: Mapping[str, EventAdapterResult],
) -> ResolvedEventState:
    """Apply explicit changes to the root event's normalized field state.

    ``documents`` is keyed by Contract ``doc_id``. A correction change is only a
    provenance signal: its value and unit come from the corresponding adapted
    correction Event, never from the raw before/after strings.
    """
    entries = _ordered_entries(chain)
    root_entry = _root_entry(chain, entries)
    root_doc_id = _doc_id(root_entry)

    if entries[0] is not root_entry:
        raise ValueError("FilingChain root is not the earliest filing")
    if not chain.is_complete or not history.chain_complete:
        raise ValueError("latest state requires a complete correction chain")
    if root_event.source_doc_id != root_doc_id:
        raise ValueError("root Event does not match the FilingChain root")

    expected_event_id = build_event_id(
        root_event.event_type,
        root_doc_id,
    )
    if history.event_id != expected_event_id:
        raise ValueError(
            "EventHistory event_id does not match the FilingChain root"
        )

    ordered_doc_ids = [_doc_id(entry) for entry in entries]
    if history.document_ids != ordered_doc_ids:
        raise ValueError("EventHistory documents do not match FilingChain chronology")

    root_result = _document_result(documents, root_doc_id)
    _validate_event(root_result.event, root_event)
    if root_result.event != root_event:
        raise ValueError("root Event does not match its adapted source document")

    fields = _initial_fields(root_event, root_result.evidence)

    for entry in entries:
        if not entry.is_correction:
            continue

        source_doc_id = _doc_id(entry)
        result = _document_result(documents, source_doc_id)
        _validate_event(result.event, root_event)

        for change in entry.changes:
            if change.field is None:
                continue
            if change.field not in fields:
                raise ValueError(
                    f"changed field {change.field!r} is absent from the root state"
                )
            if change.field not in result.event.attributes:
                raise ValueError(
                    f"changed field {change.field!r} is absent from {source_doc_id!r}"
                )

            fields[change.field] = _resolved_field(
                event=result.event,
                evidence=result.evidence,
                field_name=change.field,
            )

    as_of = _filing_date(entries[-1])
    return ResolvedEventState(
        event_id=history.event_id,
        event_type=root_event.event_type,
        company=root_event.company,
        as_of=as_of,
        fields=fields,
    )


def _ordered_entries(chain: FilingChain) -> tuple[ChainEntry, ...]:
    if not chain.entries:
        raise ValueError("latest state requires a root Event and FilingChain entries")

    return tuple(
        sorted(
            chain.entries,
            key=lambda entry: (
                normalize_filing_date(entry.filing.receipt_date) or "",
                entry.filing.receipt_no,
            ),
        )
    )


def _root_entry(chain: FilingChain, entries: Sequence[ChainEntry]) -> ChainEntry:
    matches = [
        entry for entry in entries if entry.filing.receipt_no == chain.root_receipt_no
    ]
    if len(matches) != 1 or matches[0].is_correction:
        raise ValueError("FilingChain does not contain one non-correction root")
    return matches[0]


def _doc_id(entry: ChainEntry) -> str:
    doc_id = getattr(entry.filing, "doc_id", "")
    if not doc_id:
        raise ValueError(f"filing {entry.filing.receipt_no!r} has no doc_id")
    return str(doc_id)


def _document_result(
    documents: Mapping[str, EventAdapterResult],
    doc_id: str,
) -> EventAdapterResult:
    result = documents.get(doc_id)
    if result is None:
        raise ValueError(f"source document {doc_id!r} is absent from resolver inputs")
    if result.event.source_doc_id != doc_id:
        raise ValueError(f"document key {doc_id!r} does not match its Event source_doc_id")
    return result


def _validate_event(event: Event, root_event: Event) -> None:
    if event.event_type != root_event.event_type or event.company != root_event.company:
        raise ValueError("source Event identity does not match the root Event")


def _initial_fields(
    root_event: Event,
    evidence: Sequence[Evidence],
) -> dict[str, ResolvedFieldValue]:
    unit_keys = {
        key
        for key in root_event.attributes
        if key.endswith("_unit") and key.removesuffix("_unit") in root_event.attributes
    }
    return {
        field_name: _resolved_field(
            event=root_event,
            evidence=evidence,
            field_name=field_name,
        )
        for field_name in root_event.attributes
        if field_name not in unit_keys
    }


def _resolved_field(
    *,
    event: Event,
    evidence: Sequence[Evidence],
    field_name: str,
) -> ResolvedFieldValue:
    evidence_ids = [
        item.evidence_id
        for item in evidence
        if item.doc_id == event.source_doc_id and item.field_name == field_name
    ][:1]
    return ResolvedFieldValue(
        value=event.attributes[field_name],
        unit=event.attributes.get(f"{field_name}_unit"),
        source_doc_id=event.source_doc_id,
        evidence_ids=evidence_ids,
    )


def _filing_date(entry: ChainEntry) -> date:
    normalized = normalize_filing_date(entry.filing.receipt_date)
    if normalized is None:
        raise ValueError(f"filing {entry.filing.receipt_no!r} has no valid filing date")
    return date.fromisoformat(
        f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:]}"
    )