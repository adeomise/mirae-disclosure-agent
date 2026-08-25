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


def test_adapt_facility_investment_event() -> None:
    # facility_investment는 Contract에 추가됐지만 어댑터를 통과하는지는 어디서도
    # 확인하지 않고 있었다. 미지원 표본으로만 쓰이다가 지원 대상이 되면서, 거부도
    # 수용도 검증되지 않는 구간이 생겼다.
    extracted = {
        "event_id": "event_doc_facility",
        "event_type": "facility_investment",
        "doc_id": "doc_facility",
        "company": "dummy-company",
        "is_correction": False,
        "fields": {
            "investment_amount": {
                "raw_group": "투자내역",
                "raw_field": "투자금액(원)",
                "raw_value": "2,000",
                "raw_unit": "원",
                "normalized_value": 2000,
                "normalized_unit": "KRW",
                "provenance": {
                    "table_id": "table_facility",
                    "row_index": 0,
                    "field_cell_index": 1,
                    "value_cell_index": 2,
                },
            },
            "investment_start_date": {
                "raw_group": "투자기간",
                "raw_field": "시작일",
                "raw_value": "2025-03-01",
                "raw_unit": None,
                "normalized_value": "2025-03-01",
                "normalized_unit": None,
                "provenance": {
                    "table_id": "table_facility",
                    "row_index": 1,
                    "field_cell_index": 1,
                    "value_cell_index": 2,
                },
            },
        },
        "correction": None,
    }

    result = adapt_extracted_event(extracted)

    assert (
        result.event.event_type
        is EventType.FACILITY_INVESTMENT
    )
    assert result.event.source_doc_id == "doc_facility"
    assert result.event.is_correction is False

    assert (
        result.event.attributes["investment_amount"]
        == 2000
    )
    assert (
        result.event.attributes["investment_amount_unit"]
        == "KRW"
    )

    amount_evidence = next(
        evidence
        for evidence in result.evidence
        if evidence.field_name == "investment_amount"
    )

    assert (
        amount_evidence.location_type
        is EvidenceLocationType.EVENT_FIELD
    )
    assert amount_evidence.raw_text == "2,000"
    assert amount_evidence.provenance is not None
    assert (
        amount_evidence.provenance.table_id
        == "table_facility"
    )


def test_adapter_rejects_unsupported_event_type() -> None:
    # Contract에 실재하는 값을 미지원 표본으로 쓰면, 그 값이 나중에 지원 대상이 되는 순간
    # 이 테스트는 "거부하지 않는다"가 아니라 "표본이 낡았다"로 실패한다. 실제로
    # facility_investment가 EventType에 추가되면서 한 번 그렇게 깨졌다. 그래서 표본은
    # Contract 밖의 값으로 두고, 아래 가드가 그 전제를 매번 다시 확인한다.
    unsupported_event_type = "not_a_contract_event_type"

    assert unsupported_event_type not in set(EventType), (
        "미지원 표본이 EventType에 추가됐다. 이 테스트가 검증하려는 것은 Contract 밖의 "
        "event_type을 어댑터가 거부하는지이므로, 표본을 여전히 Contract 밖인 값으로 "
        "바꿔야 한다."
    )

    extracted = {
        "event_type": unsupported_event_type,
        "doc_id": "doc_dummy",
        "company": "dummy-company",
        "fields": {},
    }

    with pytest.raises(
        ValueError,
        match="not supported by the common Contract",
    ):
        adapt_extracted_event(extracted)