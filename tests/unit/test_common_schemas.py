"""Validation tests for contract v0.1 vertical-slice objects."""

from disclosure_agent.common.enums import EventType, FactAccountingScope, MetricConcept
from disclosure_agent.common.schemas import Event, EventHistory, Fact, FieldChange


def test_q01_dummy_fact_preserves_raw_and_normalized_values() -> None:
    fact = Fact(
        fact_id="fact_dummy_revenue",
        company="\ud55c\ubbf8\ubc18\ub3c4\uccb4",
        metric=MetricConcept.REVENUE,
        fiscal_year=2025,
        accounting_scope=FactAccountingScope.CONSOLIDATED,
        raw_value="dummy",
        raw_unit="\uc5b5\uc6d0",
        normalized_value="dummy-normalized",
        normalized_unit="KRW",
        evidence_ids=["evidence_dummy_revenue"],
    )

    assert fact.raw_value == "dummy"
    assert fact.raw_unit == "\uc5b5\uc6d0"
    assert fact.normalized_value == "dummy-normalized"
    assert fact.normalized_unit == "KRW"


def test_q02_dummy_supply_contract_event() -> None:
    event = Event(
        event_id="event_dummy_supply_contract",
        event_type=EventType.SUPPLY_CONTRACT,
        company="dummy-company",
        source_doc_id="doc_dummy_event",
        attributes={
            "contract_amount": "dummy-amount",
            "contract_start_date": "YYYY-MM-DD",
            "contract_end_date": "YYYY-MM-DD",
        },
    )

    assert event.attributes["contract_amount"] == "dummy-amount"
    assert event.is_correction is False


def test_q13_dummy_event_history_tracks_named_field_change() -> None:
    history = EventHistory(
        event_id="event_dummy_supply_contract",
        document_ids=["doc_dummy_original", "doc_dummy_correction"],
        changes=[
            FieldChange(
                field_name="contract_amount",
                before="dummy-before",
                after="dummy-after",
                unit="KRW",
            )
        ],
        chain_complete=True,
    )

    assert history.document_ids == ["doc_dummy_original", "doc_dummy_correction"]
    assert history.changes[0].field_name == "contract_amount"
    assert history.changes[0].before == "dummy-before"
    assert history.changes[0].after == "dummy-after"
