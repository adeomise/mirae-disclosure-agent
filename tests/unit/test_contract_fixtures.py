"""Validation tests for the Q01, Q02, and Q13 JSON fixtures."""

from pathlib import Path

from pydantic import BaseModel

from disclosure_agent.common.enums import (
    EvidenceLocationType,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import Event, EventHistory, Evidence, Fact

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures"


def load_fixture[ModelT: BaseModel](filename: str, model_type: type[ModelT]) -> ModelT:
    fixture_path = FIXTURE_DIR / filename
    return model_type.model_validate_json(fixture_path.read_text(encoding="utf-8"))


def test_q01_fact_and_evidence_fixtures_validate() -> None:
    fact = load_fixture("q01_fact.json", Fact)
    evidence = load_fixture("q01_evidence.json", Evidence)

    assert fact.metric is MetricConcept.REVENUE
    assert fact.accounting_scope is FactAccountingScope.CONSOLIDATED
    assert fact.fiscal_year == 2025
    assert evidence.location_type is EvidenceLocationType.TABLE
    assert evidence.evidence_id in fact.evidence_ids


def test_q02_event_and_evidence_fixtures_validate() -> None:
    event = load_fixture("q02_event.json", Event)
    evidence = load_fixture("q02_evidence.json", Evidence)

    assert {
        "contract_amount",
        "contract_start_date",
        "contract_end_date",
    } <= event.attributes.keys()
    assert evidence.location_type is EvidenceLocationType.EVENT_FIELD
    assert evidence.doc_id == event.source_doc_id


def test_q13_event_history_and_evidence_fixtures_validate() -> None:
    history = load_fixture("q13_event_history.json", EventHistory)
    evidence = load_fixture("q13_evidence.json", Evidence)

    assert history.chain_complete is True
    assert history.changes[0].field_name == "contract_amount"
    assert history.changes[0].before != history.changes[0].after
    assert evidence.location_type is EvidenceLocationType.EVENT_FIELD
    assert evidence.doc_id in history.document_ids
