"""Adapter from Event Extractor output to common Contract objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from disclosure_agent.common.enums import (
    EventType,
    EvidenceLocationType,
)
from disclosure_agent.common.schemas import (
    Event,
    Evidence,
    EvidenceProvenance,
    FieldChange,
    RawReference,
)


@dataclass(slots=True)
class EventAdapterResult:
    """Common Contract objects produced from one extracted event."""

    event: Event
    evidence: list[Evidence]
    field_changes: list[FieldChange]
    raw_reference: RawReference | None = None


def _field_evidence_id(
    doc_id: str,
    field_name: str,
) -> str:
    return f"evidence_{doc_id}_{field_name}"


def _change_evidence_id(
    doc_id: str,
    field_name: str,
    side: str,
) -> str:
    return f"evidence_{doc_id}_{field_name}_{side}"


def _make_provenance(
    provenance: dict[str, Any] | None,
    *,
    column_key: str,
) -> EvidenceProvenance | None:
    """Convert Extractor provenance into common EvidenceProvenance."""

    if not provenance:
        return None

    table_id = provenance.get("table_id")
    row_index = provenance.get("row_index")
    column_index = provenance.get(column_key)

    if (
        table_id is None
        and row_index is None
        and column_index is None
    ):
        return None

    return EvidenceProvenance(
        table_id=table_id,
        row_index=row_index,
        column_index=column_index,
    )


def _make_field_evidence(
    *,
    doc_id: str,
    field_name: str,
    field: dict[str, Any],
) -> Evidence:
    """Create Evidence for one normal extracted event field."""

    raw_group = field.get("raw_group")
    raw_value = field.get("raw_value")

    return Evidence(
        evidence_id=_field_evidence_id(
            doc_id,
            field_name,
        ),
        doc_id=doc_id,
        location_type=EvidenceLocationType.EVENT_FIELD,
        section_path=(
            [str(raw_group)]
            if raw_group
            else []
        ),
        row_label=field.get("raw_field"),
        field_name=field_name,
        raw_text=(
            str(raw_value)
            if raw_value is not None
            else None
        ),
        provenance=_make_provenance(
            field.get("provenance"),
            column_key="value_cell_index",
        ),
    )


def _make_event_attributes(
    fields: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build common Event attributes from canonical Extractor fields."""

    attributes: dict[str, Any] = {}

    for field_name, field in fields.items():
        attributes[field_name] = field.get(
            "normalized_value"
        )

        normalized_unit = field.get(
            "normalized_unit"
        )

        if normalized_unit is not None:
            attributes[
                f"{field_name}_unit"
            ] = normalized_unit

    return attributes


def _adapt_correction(
    *,
    doc_id: str,
    correction: dict[str, Any],
) -> tuple[
    list[FieldChange],
    list[Evidence],
    RawReference,
]:
    """Convert correction before/after rows into common Contracts."""

    changes: list[FieldChange] = []
    evidence: list[Evidence] = []

    for change in correction.get("changes", []):
        raw_field = change.get("raw_field")
        field_name = (
            change.get("canonical_field")
            or raw_field
            or "unknown"
        )

        provenance = change.get("provenance")

        before_evidence_id = _change_evidence_id(
            doc_id,
            str(field_name),
            "before",
        )
        after_evidence_id = _change_evidence_id(
            doc_id,
            str(field_name),
            "after",
        )

        before = change.get("before")
        after = change.get("after")

        before_evidence = Evidence(
            evidence_id=before_evidence_id,
            doc_id=doc_id,
            location_type=EvidenceLocationType.EVENT_FIELD,
            row_label=raw_field,
            field_name=str(field_name),
            raw_text=(
                str(before)
                if before is not None
                else None
            ),
            provenance=_make_provenance(
                provenance,
                column_key="before_cell_index",
            ),
        )

        after_evidence = Evidence(
            evidence_id=after_evidence_id,
            doc_id=doc_id,
            location_type=EvidenceLocationType.EVENT_FIELD,
            row_label=raw_field,
            field_name=str(field_name),
            raw_text=(
                str(after)
                if after is not None
                else None
            ),
            provenance=_make_provenance(
                provenance,
                column_key="after_cell_index",
            ),
        )

        evidence.extend(
            [
                before_evidence,
                after_evidence,
            ]
        )

        changes.append(
            FieldChange(
                field_name=str(field_name),
                raw_field=raw_field,
                before=before,
                after=after,
                unit=change.get("raw_unit"),
                before_evidence_id=before_evidence_id,
                after_evidence_id=after_evidence_id,
            )
        )

    raw_reference = RawReference(
        document_title=correction.get(
            "original_report_name"
        ),
        original_filing_date=correction.get(
            "original_filing_date"
        ),
        correction_filing_date=correction.get(
            "correction_date"
        ),
        correction_reason=correction.get("reason"),
    )

    return changes, evidence, raw_reference


def adapt_extracted_event(
    extracted: dict[str, Any],
) -> EventAdapterResult:
    """Adapt one Event Extractor result to the common Contract."""

    doc_id = str(extracted.get("doc_id") or "")
    company = str(extracted.get("company") or "")
    raw_event_type = str(
        extracted.get("event_type") or ""
    )

    if not doc_id:
        raise ValueError(
            "Event Adapter requires doc_id."
        )

    if not company:
        raise ValueError(
            "Event Adapter requires company."
        )

    try:
        event_type = EventType(raw_event_type)
    except ValueError as exc:
        raise ValueError(
            "Event type is not supported by the "
            f"common Contract: {raw_event_type!r}"
        ) from exc

    fields = extracted.get("fields") or {}

    evidence = [
        _make_field_evidence(
            doc_id=doc_id,
            field_name=field_name,
            field=field,
        )
        for field_name, field in fields.items()
    ]

    event = Event(
        event_id=str(
            extracted.get("event_id")
            or f"event_{doc_id}"
        ),
        event_type=event_type,
        company=company,
        source_doc_id=doc_id,
        attributes=_make_event_attributes(fields),
        is_correction=bool(
            extracted.get("is_correction")
        ),
    )

    field_changes: list[FieldChange] = []
    raw_reference: RawReference | None = None

    correction = extracted.get("correction")

    if correction:
        (
            field_changes,
            correction_evidence,
            raw_reference,
        ) = _adapt_correction(
            doc_id=doc_id,
            correction=correction,
        )

        evidence.extend(correction_evidence)

    return EventAdapterResult(
        event=event,
        evidence=evidence,
        field_changes=field_changes,
        raw_reference=raw_reference,
    )