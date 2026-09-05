"""정기공시 내부 표현을 공통 Contract로 변환한다.

- Pattern A의 targeted FinancialFact -> Fact + Evidence
- Pattern B의 Section 범위 내용 -> DocumentSummary + Evidence

변환을 한 모듈에 모아 두는 이유는 정본을 하나로 두기 위해서다. 같은 변환이 agent 쪽에도
생기면 어느 쪽이 정본인지 알 수 없게 되고, Contract가 개정될 때 두 곳이 어긋난다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from disclosure_agent.common.enums import (
    EvidenceLocationType,
    FactAccountingScope,
    MetricConcept,
    SectionScope,
    SummaryItemType,
)
from disclosure_agent.common.schemas import (
    DocumentSummary,
    Evidence,
    EvidenceProvenance,
    Fact,
    SummaryItem,
)
from disclosure_agent.periodic.extractor import FinancialFact
from disclosure_agent.periodic.sections import SectionContentItem, SectionSelection

EVIDENCE_LOCATION_BY_ITEM_TYPE: dict[SummaryItemType, EvidenceLocationType] = {
    SummaryItemType.PARAGRAPH: EvidenceLocationType.TEXT,
    SummaryItemType.TABLE: EvidenceLocationType.TABLE,
}


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


def _contract_company(
    parsed_document: dict[str, Any],
    canonical_company: str | None,
) -> str:
    """Contract identity는 명시된 canonical company를 우선한다."""
    raw_company = _required_metadata(parsed_document, "corp_name")

    if canonical_company is None:
        return raw_company
    if not isinstance(canonical_company, str) or not canonical_company.strip():
        raise ValueError("canonical_company는 비어 있지 않은 문자열이어야 합니다.")

    return canonical_company.strip()


def adapt_financial_fact(
    financial_fact: FinancialFact,
    parsed_document: dict[str, Any],
    *,
    canonical_company: str | None = None,
) -> tuple[Fact, Evidence]:
    """확정된 targeted FinancialFact 하나를 Contract Fact/Evidence로 옮긴다.

    Agent가 확정한 canonical company가 있으면 Contract identity로 사용한다. 생략한
    low-level 호출은 기존처럼 parser metadata의 raw company를 사용한다.
    """
    doc_id = _required_metadata(parsed_document, "doc_id")
    company = _contract_company(parsed_document, canonical_company)
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


def adapt_section_summary(
    parsed_document: dict[str, Any],
    *,
    canonical_company: str | None = None,
    section_scope: SectionScope,
    selection: SectionSelection,
    items: list[SectionContentItem],
) -> tuple[DocumentSummary, list[Evidence]]:
    """확정된 Section 범위의 내용을 Contract DocumentSummary/Evidence로 옮긴다.

    항목마다 Evidence를 하나씩 만든다. 요약 항목 수만큼의 Evidence가 Pattern B의 확정
    요건이다(docs/agent_design/evidence_and_answerability.md 유형별 최소 요건).

    Fact는 만들지 않는다. 같은 표에서 Fact를 함께 뽑으면 요약 결과에 지표 값이 섞이고,
    그 값은 Pattern A가 회계기준·기간까지 확인해 고르는 값과 달라질 수 있다.

    식별자는 문서와 항목 위치로부터 결정론적으로 만든다. 같은 문서를 다시 처리하면 같은
    식별자가 나와야 결과를 대조할 수 있다.
    """
    doc_id = _required_metadata(parsed_document, "doc_id")
    company = _contract_company(parsed_document, canonical_company)
    report_name = _required_metadata(parsed_document, "report_name")

    if not items:
        raise ValueError("DocumentSummary 변환에는 요약 항목이 최소 한 건 필요합니다.")

    summary_items: list[SummaryItem] = []
    evidence_list: list[Evidence] = []

    for item in items:
        location_type = EVIDENCE_LOCATION_BY_ITEM_TYPE.get(item.item_type)

        if location_type is None:
            raise ValueError(f"지원하지 않는 요약 항목 종류입니다: {item.item_type!r}")

        source_suffix = (
            item.table_id
            if item.item_type is SummaryItemType.TABLE and item.table_id
            else f"{item.section_id}_p{item.ordinal}"
        )
        evidence_id = f"evidence_{doc_id}_{source_suffix}"
        item_id = f"summary_item_{doc_id}_{source_suffix}"

        provenance = (
            EvidenceProvenance(
                table_id=item.table_id,
                unit_declaration=item.unit_declaration,
                table_classification=item.table_classification,
            )
            if item.item_type is SummaryItemType.TABLE
            else None
        )

        evidence_list.append(
            Evidence(
                evidence_id=evidence_id,
                doc_id=doc_id,
                location_type=location_type,
                section_path=list(item.section_path),
                table_name=item.label if item.item_type is SummaryItemType.TABLE else None,
                field_name=(
                    f"section_id={item.section_id};paragraph_index={item.ordinal}"
                    if item.item_type is SummaryItemType.PARAGRAPH
                    else f"table_id={item.table_id}"
                ),
                raw_text=item.excerpt,
                provenance=provenance,
            )
        )

        summary_items.append(
            SummaryItem(
                item_id=item_id,
                item_type=item.item_type,
                label=item.label,
                evidence_ids=[evidence_id],
            )
        )

    summary = DocumentSummary(
        summary_id=f"summary_{doc_id}_{section_scope.value}",
        doc_id=doc_id,
        company=company,
        report_name=report_name,
        section_scope=section_scope,
        section_path=list(selection.section_path),
        items=summary_items,
    )

    return summary, evidence_list
