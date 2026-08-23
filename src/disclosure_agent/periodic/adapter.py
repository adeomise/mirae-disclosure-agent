"""Targeted periodic FinancialFact를 공통 Contract로 변환한다."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from disclosure_agent.common.enums import (
    EvidenceLocationType,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import Evidence, Fact
from disclosure_agent.periodic.extractor import FinancialFact


def _required_metadata(parsed_document: dict[str, Any], key: str) -> str:
    metadata = parsed_document.get("metadata") or {}
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"FinancialFact 변환에는 metadata.{key}가 필요합니다.")
    return value.strip()


def _contract_enum[EnumType: StrEnum](
    value: str,
    enum_type: type[EnumType],
    field_name: str,
) -> EnumType:
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"지원하지 않는 {field_name} 값입니다: {value!r}") from exc


def adapt_financial_fact(
    financial_fact: FinancialFact,
    parsed_document: dict[str, Any],
) -> tuple[Fact, Evidence]:
    """확정된 targeted FinancialFact 하나를 Contract Fact/Evidence로 옮긴다."""
    doc_id = _required_metadata(parsed_document, "doc_id")
    company = _required_metadata(parsed_document, "corp_name")
    metric = _contract_enum(financial_fact.metric, MetricConcept, "metric")
    accounting_scope = _contract_enum(
        financial_fact.accounting_scope,
        FactAccountingScope,
        "accounting_scope",
    )
    location = financial_fact.location
    source_suffix = (
        f"{location.table_id}_r{location.row_index}_c{location.column_index}"
    )
    evidence_id = f"evidence_{doc_id}_{source_suffix}"
    fact_id = f"fact_{doc_id}_{metric.value}_{source_suffix}"

    evidence = Evidence(
        evidence_id=evidence_id,
        doc_id=doc_id,
        location_type=EvidenceLocationType.TABLE,
        section_path=list(location.section_path),
        table_name=None,
        row_label=location.label_text,
        field_name=(
            f"table_id={location.table_id};row_index={location.row_index};"
            f"col_index={location.column_index}"
        ),
        raw_text=financial_fact.raw_value,
    )
    fact = Fact(
        fact_id=fact_id,
        company=company,
        metric=metric,
        fiscal_year=financial_fact.fiscal_year,
        fiscal_period=None,
        accounting_scope=accounting_scope,
        raw_value=financial_fact.raw_value,
        raw_unit=financial_fact.unit_token,
        normalized_value=str(financial_fact.value_in_base_unit),
        normalized_unit=financial_fact.base_unit,
        evidence_ids=[evidence_id],
    )
    return fact, evidence
