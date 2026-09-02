"""PII-safe extraction of Q03 holding targets from exact DART ACODEs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from disclosure_agent.common.enums import (
    EvidenceLocationType,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import Evidence, EvidenceProvenance, Fact

HOLDING_ACODE_ALLOWLIST = {
    "THS_STK_CNT": (MetricConcept.HOLDING_SHARES, "shares"),
    "THS_STK_RT": (MetricConcept.HOLDING_RATIO, "percent"),
}
SHARE_VALUE_PATTERN = re.compile(r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)$")


class HoldingExtractionError(Exception):
    """A holding target cannot be extracted without guessing."""


class DuplicateHoldingTargetError(HoldingExtractionError):
    """More than one cell has the same allowlisted target ACODE."""


class InvalidHoldingTargetValueError(HoldingExtractionError):
    """An allowlisted target cell does not contain a supported numeric value."""


@dataclass(frozen=True)
class HoldingExtractionResult:
    facts: tuple[Fact, ...]
    evidence: tuple[Evidence, ...]
    missing_metrics: tuple[MetricConcept, ...]


def _normalize_value(acode: str, raw_value: str) -> str:
    if acode == "THS_STK_CNT":
        if not SHARE_VALUE_PATTERN.fullmatch(raw_value):
            raise InvalidHoldingTargetValueError(f"invalid target value: acode={acode}")
        return str(int(raw_value.replace(",", "")))

    try:
        value = Decimal(raw_value)
    except InvalidOperation as error:
        raise InvalidHoldingTargetValueError(
            f"invalid target value: acode={acode}"
        ) from error
    if not value.is_finite():
        raise InvalidHoldingTargetValueError(f"invalid target value: acode={acode}")
    return format(value, "f")


def _target_cells(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    targets = {acode: [] for acode in HOLDING_ACODE_ALLOWLIST}
    for table in document.get("tables") or ():
        for row in table.get("rows") or ():
            for cell in row:
                acode = cell.get("acode")
                if acode in targets:
                    targets[acode].append(cell)
    return targets


def extract_holding_facts(
    document: dict[str, Any],
    *,
    target_company: str,
) -> HoldingExtractionResult:
    """Return only exact allowlisted current-report holding values."""
    metadata = document.get("metadata") or {}
    doc_id = str(metadata.get("doc_id") or "").strip()
    if not doc_id:
        raise HoldingExtractionError("holding extraction requires doc_id")
    company = target_company.strip()
    if not company:
        raise HoldingExtractionError("holding extraction requires target_company")

    facts: list[Fact] = []
    evidence_items: list[Evidence] = []
    missing_metrics: list[MetricConcept] = []
    target_cells = _target_cells(document)

    for acode, (metric, normalized_unit) in HOLDING_ACODE_ALLOWLIST.items():
        candidates = target_cells[acode]
        if not candidates:
            missing_metrics.append(metric)
            continue
        if len(candidates) > 1:
            positions = [
                (
                    candidate.get("provenance", {}).get("table_id"),
                    candidate.get("provenance", {}).get("row_index"),
                    candidate.get("provenance", {}).get("column_index"),
                )
                for candidate in candidates
            ]
            raise DuplicateHoldingTargetError(
                f"duplicate holding target: acode={acode}; count={len(candidates)}; "
                f"positions={positions}"
            )

        cell = candidates[0]
        raw_value = str(cell.get("text") or "").strip()
        normalized_value = _normalize_value(acode, raw_value)
        provenance = cell.get("provenance") or {}
        table_id = str(provenance.get("table_id") or "")
        row_index = int(provenance["row_index"])
        column_index = int(provenance["column_index"])
        suffix = f"{table_id}_r{row_index}_c{column_index}_{acode.lower()}"
        evidence_id = f"evidence_{doc_id}_{suffix}"

        evidence_items.append(
            Evidence(
                evidence_id=evidence_id,
                doc_id=doc_id,
                location_type=EvidenceLocationType.TABLE,
                raw_text=raw_value,
                provenance=EvidenceProvenance(
                    table_id=table_id,
                    row_index=row_index,
                    column_index=column_index,
                    acode=acode,
                ),
            )
        )
        facts.append(
            Fact(
                fact_id=f"fact_{doc_id}_{metric.value}_{suffix}",
                company=company,
                metric=metric,
                fiscal_year=None,
                fiscal_period=None,
                accounting_scope=FactAccountingScope.NOT_APPLICABLE,
                raw_value=raw_value,
                raw_unit=None,
                normalized_value=normalized_value,
                normalized_unit=normalized_unit,
                evidence_ids=[evidence_id],
            )
        )

    return HoldingExtractionResult(
        facts=tuple(facts),
        evidence=tuple(evidence_items),
        missing_metrics=tuple(missing_metrics),
    )
