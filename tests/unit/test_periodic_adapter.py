from decimal import Decimal

import pytest

from disclosure_agent.common.enums import (
    EvidenceLocationType,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import Evidence, Fact
from disclosure_agent.periodic.adapter import adapt_financial_fact
from disclosure_agent.periodic.extractor import FactLocation, FinancialFact


@pytest.fixture
def financial_fact() -> FinancialFact:
    return FinancialFact(
        metric="revenue",
        acode="ifrs-full_Revenue",
        accounting_scope="consolidated",
        fiscal_year=2025,
        raw_value="1,234,567",
        value=Decimal("1234567"),
        unit_text="(단위 : 백만원)",
        unit_token="백만원",
        unit_multiplier=1_000_000,
        value_in_base_unit=Decimal("1234567000000"),
        base_unit="원",
        acontext="CFY2025dFY_ifrs-full_ConsolidatedMember",
        location=FactLocation(
            section_id="section_0002",
            section_path=("III. 재무에 관한 사항", "2. 연결재무제표"),
            table_id="table_0002",
            row_index=2,
            column_index=1,
            label_text="수익(매출액)",
        ),
    )


@pytest.fixture
def parsed_document() -> dict:
    return {
        "metadata": {
            "doc_id": "periodic_SYNTHETIC0001",
            "corp_name": "테스트기업",
            "report_name": "사업보고서",
        }
    }


def test_adapt_financial_fact_maps_contract_fields(
    financial_fact: FinancialFact,
    parsed_document: dict,
) -> None:
    fact, evidence = adapt_financial_fact(financial_fact, parsed_document)

    assert isinstance(fact, Fact)
    assert isinstance(evidence, Evidence)
    assert fact.company == "테스트기업"
    assert fact.metric is MetricConcept.REVENUE
    assert fact.fiscal_year == 2025
    assert fact.fiscal_period is None
    assert fact.accounting_scope is FactAccountingScope.CONSOLIDATED
    assert fact.raw_value == "1,234,567"
    assert fact.raw_unit == "백만원"
    assert fact.normalized_value == "1234567000000"
    assert fact.normalized_unit == "원"


def test_evidence_preserves_supported_table_provenance(
    financial_fact: FinancialFact,
    parsed_document: dict,
) -> None:
    _, evidence = adapt_financial_fact(financial_fact, parsed_document)

    assert evidence.doc_id == "periodic_SYNTHETIC0001"
    assert evidence.location_type is EvidenceLocationType.TABLE
    assert evidence.section_path == ["III. 재무에 관한 사항", "2. 연결재무제표"]
    assert evidence.table_name is None
    assert evidence.row_label == "수익(매출액)"
    assert evidence.column_label is None
    assert evidence.field_name == "table_id=table_0002;row_index=2;col_index=1"
    assert evidence.raw_text == "1,234,567"


def test_fact_references_returned_evidence(
    financial_fact: FinancialFact,
    parsed_document: dict,
) -> None:
    fact, evidence = adapt_financial_fact(financial_fact, parsed_document)
    assert fact.evidence_ids == [evidence.evidence_id]


def test_ids_are_deterministic(
    financial_fact: FinancialFact,
    parsed_document: dict,
) -> None:
    first_fact, first_evidence = adapt_financial_fact(financial_fact, parsed_document)
    second_fact, second_evidence = adapt_financial_fact(financial_fact, parsed_document)

    assert first_fact.fact_id == second_fact.fact_id
    assert first_evidence.evidence_id == second_evidence.evidence_id
    assert first_fact.fact_id == (
        "fact_periodic_SYNTHETIC0001_revenue_table_0002_r2_c1"
    )
    assert first_evidence.evidence_id == (
        "evidence_periodic_SYNTHETIC0001_table_0002_r2_c1"
    )


@pytest.mark.parametrize("missing_key", ["doc_id", "corp_name"])
def test_required_metadata_is_validated(
    financial_fact: FinancialFact,
    parsed_document: dict,
    missing_key: str,
) -> None:
    del parsed_document["metadata"][missing_key]

    with pytest.raises(ValueError, match=rf"metadata\.{missing_key}"):
        adapt_financial_fact(financial_fact, parsed_document)


def test_unsupported_contract_enum_value_is_rejected(
    financial_fact: FinancialFact,
    parsed_document: dict,
) -> None:
    unsupported = FinancialFact(
        **{**financial_fact.__dict__, "metric": "not_in_contract"}
    )

    with pytest.raises(ValueError, match="지원하지 않는 metric"):
        adapt_financial_fact(unsupported, parsed_document)
