import pytest

from disclosure_agent.common.enums import (
    EventType,
    EvidenceLocationType,
)
from disclosure_agent.event.adapter import (
    adapt_extracted_event,
)


def test_adapt_supply_contract_event() -> None:
    extracted = {
        "event_id": "event_doc_dummy",
        "event_type": "supply_contract",
        "doc_id": "doc_dummy",
        "company": "dummy-company",
        "is_correction": False,
        "fields": {
            "contract_amount": {
                "raw_group": "계약내역",
                "raw_field": "계약금액(원)",
                "raw_value": "1,000",
                "raw_unit": "원",
                "normalized_value": 1000,
                "normalized_unit": "KRW",
                "provenance": {
                    "table_id": "table_dummy",
                    "row_index": 2,
                    "field_cell_index": 1,
                    "value_cell_index": 2,
                },
            },
            "contract_start_date": {
                "raw_group": "계약기간",
                "raw_field": "시작일",
                "raw_value": "2025-01-01",
                "raw_unit": None,
                "normalized_value": "2025-01-01",
                "normalized_unit": None,
                "provenance": {
                    "table_id": "table_dummy",
                    "row_index": 3,
                    "field_cell_index": 1,
                    "value_cell_index": 2,
                },
            },
        },
        "correction": None,
    }

    result = adapt_extracted_event(extracted)

    assert result.event.event_type is EventType.SUPPLY_CONTRACT
    assert result.event.company == "dummy-company"
    assert result.event.source_doc_id == "doc_dummy"
    assert result.event.is_correction is False

    assert result.event.attributes["contract_amount"] == 1000
    assert (
        result.event.attributes["contract_amount_unit"]
        == "KRW"
    )

    amount_evidence = next(
        evidence
        for evidence in result.evidence
        if evidence.field_name == "contract_amount"
    )

    assert (
        amount_evidence.location_type
        is EvidenceLocationType.EVENT_FIELD
    )
    assert amount_evidence.raw_text == "1,000"
    assert amount_evidence.provenance is not None
    assert (
        amount_evidence.provenance.table_id
        == "table_dummy"
    )
    assert amount_evidence.provenance.row_index == 2
    assert amount_evidence.provenance.column_index == 2

    assert result.field_changes == []
    assert result.raw_reference is None


def test_adapt_supply_contract_correction() -> None:
    extracted = {
        "event_id": "event_doc_correction",
        "event_type": "supply_contract",
        "doc_id": "doc_correction",
        "company": "dummy-company",
        "is_correction": True,
        "fields": {},
        "correction": {
            "correction_date": "2025-02-01",
            "original_report_name": "단일판매ㆍ공급계약 체결",
            "original_filing_date": "2025-01-01",
            "reason": "dummy-reason",
            "changes": [
                {
                    "canonical_field": "counterparty",
                    "raw_field": "3. 계약상대",
                    "before": "before-company",
                    "after": "after-company",
                    "raw_unit": None,
                    "provenance": {
                        "table_id": "table_correction",
                        "row_index": 5,
                        "field_cell_index": 0,
                        "before_cell_index": 1,
                        "after_cell_index": 2,
                    },
                }
            ],
        },
    }

    result = adapt_extracted_event(extracted)

    assert result.event.is_correction is True

    assert len(result.field_changes) == 1

    change = result.field_changes[0]

    assert change.field_name == "counterparty"
    assert change.before == "before-company"
    assert change.after == "after-company"

    assert change.before_evidence_id is not None
    assert change.after_evidence_id is not None

    before_evidence = next(
        evidence
        for evidence in result.evidence
        if evidence.evidence_id
        == change.before_evidence_id
    )

    after_evidence = next(
        evidence
        for evidence in result.evidence
        if evidence.evidence_id
        == change.after_evidence_id
    )

    assert before_evidence.provenance is not None
    assert before_evidence.provenance.column_index == 1

    assert after_evidence.provenance is not None
    assert after_evidence.provenance.column_index == 2

    assert result.raw_reference is not None
    assert (
        result.raw_reference.original_filing_date
        == "2025-01-01"
    )
    assert (
        result.raw_reference.correction_filing_date
        == "2025-02-01"
    )


def test_adapter_rejects_unsupported_event_type() -> None:
    extracted = {
        "event_type": "facility_investment",
        "doc_id": "doc_dummy",
        "company": "dummy-company",
        "fields": {},
    }

    with pytest.raises(
        ValueError,
        match="not supported by the common Contract",
    ):
        adapt_extracted_event(extracted)