"""Integration tests for the development fixture-backed ask endpoint."""

from pathlib import Path

from fastapi.testclient import TestClient
from tests.helpers.synthetic_corpus import (
    DUMMY_OTHER_REVENUE,
    DUMMY_REVENUE,
    Q01_QUESTION,
    Q02_QUESTION,
    Q13_QUESTION,
    Q13_TITLE,
    write_q01_periodic_xml,
    write_q02_event_document,
    write_q13_event_document,
)

from disclosure_agent.agent.query_analyzer import (
    analyze_q01_question,
    analyze_q02_question,
    analyze_q13_question,
)
from disclosure_agent.agent.router import (
    Q01VerticalSliceRouter,
    Q02VerticalSliceRouter,
    Q13VerticalSliceRouter,
)
from disclosure_agent.api.dependencies import get_development_fixture_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.api.mock_service import DevelopmentFixtureAskService
from disclosure_agent.common.enums import (
    AnswerabilityStatus,
    ChainPolicy,
    EventLinkResolutionStatus,
    EventType,
    FactAccountingScope,
    MetricConcept,
    QueryAccountingScope,
)
from disclosure_agent.common.schemas import AnswerResponse
from disclosure_agent.event.linker import link_supply_contract_correction
from disclosure_agent.event.service import EventQ02Service, EventQ13Service
from disclosure_agent.periodic.service import PeriodicQ01Service
from disclosure_agent.retrieval.service import retrieve_event_history_documents

client = TestClient(app)


def post_question(question: str) -> AnswerResponse:
    response = client.post("/api/ask", json={"question": question})
    assert response.status_code == 200
    return AnswerResponse.model_validate(response.json())


def build_vertical_slice_service(
    periodic_corpus_dir: Path,
    event_corpus_dir: Path,
) -> DevelopmentFixtureAskService:
    return DevelopmentFixtureAskService(
        q01_router=Q01VerticalSliceRouter(
            PeriodicQ01Service(periodic_corpus_dir)
        ),
        q02_router=Q02VerticalSliceRouter(EventQ02Service(event_corpus_dir)),
        q13_router=Q13VerticalSliceRouter(EventQ13Service(event_corpus_dir)),
    )


def post_q01_from_corpus(corpus_dir: Path) -> AnswerResponse:
    service = build_vertical_slice_service(corpus_dir, corpus_dir)
    app.dependency_overrides[get_development_fixture_ask_service] = lambda: service
    try:
        return post_question(Q01_QUESTION)
    finally:
        app.dependency_overrides.pop(get_development_fixture_ask_service, None)


def post_q02_from_corpus(corpus_dir: Path) -> AnswerResponse:
    service = build_vertical_slice_service(corpus_dir, corpus_dir)
    app.dependency_overrides[get_development_fixture_ask_service] = lambda: service
    try:
        return post_question(Q02_QUESTION)
    finally:
        app.dependency_overrides.pop(get_development_fixture_ask_service, None)


def post_q13_from_corpus(corpus_dir: Path) -> AnswerResponse:
    service = build_vertical_slice_service(corpus_dir, corpus_dir)
    app.dependency_overrides[get_development_fixture_ask_service] = lambda: service
    try:
        return post_question(Q13_QUESTION)
    finally:
        app.dependency_overrides.pop(get_development_fixture_ask_service, None)


def test_q01_question_is_structured_for_periodic_revenue() -> None:
    query_plan = analyze_q01_question(Q01_QUESTION)

    assert query_plan is not None
    assert query_plan.entities[0].resolved_name == "\ud55c\ubbf8\ubc18\ub3c4\uccb4"
    assert query_plan.periods[0].year == 2025
    assert query_plan.accounting_scope is QueryAccountingScope.CONSOLIDATED
    assert query_plan.metrics == [MetricConcept.REVENUE]


def test_q02_question_is_structured_for_supply_contract() -> None:
    query_plan = analyze_q02_question(Q02_QUESTION)

    assert query_plan is not None
    assert query_plan.document_subtype == EventType.SUPPLY_CONTRACT.value
    assert query_plan.accounting_scope is QueryAccountingScope.NOT_APPLICABLE
    assert query_plan.periods[0].range == "2025-09"
    assert query_plan.metrics == [
        MetricConcept.CONTRACT_AMOUNT,
        MetricConcept.CONTRACT_START_DATE,
        MetricConcept.CONTRACT_END_DATE,
    ]


def test_q13_question_is_structured_for_full_event_history() -> None:
    query_plan = analyze_q13_question(Q13_QUESTION)

    assert query_plan is not None
    assert query_plan.intent.value == "event_history"
    assert query_plan.document_subtype == EventType.SUPPLY_CONTRACT.value
    assert query_plan.accounting_scope is QueryAccountingScope.NOT_APPLICABLE
    assert query_plan.chain_policy is ChainPolicy.FULL_CHAIN
    assert query_plan.periods[0].range == "2025"


def test_q01_uses_periodic_retrieval_parser_and_extractor(tmp_path: Path) -> None:
    write_q01_periodic_xml(tmp_path)
    answer = post_q01_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.ANSWERABLE
    assert len(answer.facts) == 1
    assert len(answer.evidence) == 1
    fact = answer.facts[0]
    evidence = answer.evidence[0]
    assert fact.accounting_scope is FactAccountingScope.CONSOLIDATED
    assert fact.raw_value == DUMMY_REVENUE
    assert fact.raw_unit == "dummy-unit"
    assert evidence.doc_id.startswith("periodic_")
    assert evidence.table_name == "\uc5f0\uacb0 \uc190\uc775\uacc4\uc0b0\uc11c"
    assert evidence.row_label == "\ub9e4\ucd9c\uc561"
    assert evidence.column_label == "2025"
    assert fact.evidence_ids == [evidence.evidence_id]


def test_q01_does_not_fall_back_to_a_different_year_column(tmp_path: Path) -> None:
    write_q01_periodic_xml(
        tmp_path,
        tables=[
            (
                "\uc5f0\uacb0 \uc7ac\ubb34\uc81c\ud45c",
                "\uc5f0\uacb0 \uc190\uc775\uacc4\uc0b0\uc11c",
                "2024",
                DUMMY_OTHER_REVENUE,
            )
        ],
    )

    answer = post_q01_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.UNRESOLVED
    assert not answer.facts
    assert DUMMY_OTHER_REVENUE not in answer.answer


def test_q01_selects_explicit_consolidated_table_over_separate_table(
    tmp_path: Path,
) -> None:
    write_q01_periodic_xml(
        tmp_path,
        tables=[
            (
                "\ubcc4\ub3c4 \uc7ac\ubb34\uc81c\ud45c",
                "\ubcc4\ub3c4 \uc190\uc775\uacc4\uc0b0\uc11c",
                "2025",
                DUMMY_OTHER_REVENUE,
            ),
            (
                "\uc5f0\uacb0 \uc7ac\ubb34\uc81c\ud45c",
                "\uc5f0\uacb0 \uc190\uc775\uacc4\uc0b0\uc11c",
                "2025",
                DUMMY_REVENUE,
            ),
        ],
    )

    answer = post_q01_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.ANSWERABLE
    assert answer.facts[0].raw_value == DUMMY_REVENUE
    assert answer.facts[0].accounting_scope is FactAccountingScope.CONSOLIDATED


def test_q01_rejects_separate_only_table(tmp_path: Path) -> None:
    write_q01_periodic_xml(
        tmp_path,
        tables=[
            (
                "\ubcc4\ub3c4 \uc7ac\ubb34\uc81c\ud45c",
                "\ubcc4\ub3c4 \uc190\uc775\uacc4\uc0b0\uc11c",
                "2025",
                DUMMY_OTHER_REVENUE,
            )
        ],
    )

    answer = post_q01_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.UNRESOLVED
    assert not answer.facts


def test_q01_rejects_unknown_scope_table(tmp_path: Path) -> None:
    write_q01_periodic_xml(
        tmp_path,
        tables=[
            (
                "\uc7ac\ubb34\uc81c\ud45c",
                "\uc190\uc775\uacc4\uc0b0\uc11c",
                "2025",
                DUMMY_OTHER_REVENUE,
            )
        ],
    )

    answer = post_q01_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.UNRESOLVED
    assert not answer.facts


def test_q01_selects_only_annual_business_report(tmp_path: Path) -> None:
    write_q01_periodic_xml(
        tmp_path,
        filename="annual.xml",
        report_name="\uc0ac\uc5c5\ubcf4\uace0\uc11c (2025)",
    )
    write_q01_periodic_xml(
        tmp_path,
        filename="half-year.xml",
        report_name="\ubc18\uae30\ubcf4\uace0\uc11c (2025)",
        tables=[
            (
                "\uc5f0\uacb0 \uc7ac\ubb34\uc81c\ud45c",
                "\uc5f0\uacb0 \uc190\uc775\uacc4\uc0b0\uc11c",
                "2025",
                DUMMY_OTHER_REVENUE,
            )
        ],
    )
    write_q01_periodic_xml(
        tmp_path,
        filename="quarter.xml",
        report_name="\ubd84\uae30\ubcf4\uace0\uc11c (2025)",
        tables=[
            (
                "\uc5f0\uacb0 \uc7ac\ubb34\uc81c\ud45c",
                "\uc5f0\uacb0 \uc190\uc775\uacc4\uc0b0\uc11c",
                "2025",
                DUMMY_OTHER_REVENUE,
            )
        ],
    )

    answer = post_q01_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.ANSWERABLE
    assert answer.facts[0].raw_value == DUMMY_REVENUE
    assert answer.evidence[0].doc_id == "periodic_annual"


def test_q01_rejects_ambiguous_annual_document_versions(tmp_path: Path) -> None:
    write_q01_periodic_xml(tmp_path, filename="annual-version-a.xml")
    write_q01_periodic_xml(tmp_path, filename="annual-version-b.xml")

    answer = post_q01_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.UNRESOLVED
    assert not answer.facts
    assert not answer.evidence


def test_q02_uses_event_parser_and_preserves_contract_fields(tmp_path: Path) -> None:
    write_q02_event_document(tmp_path)

    answer = post_q02_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.ANSWERABLE
    assert len(answer.events) == 1
    assert len(answer.facts) == 1
    event = answer.events[0]
    fact = answer.facts[0]
    assert event.event_type is EventType.SUPPLY_CONTRACT
    assert event.attributes["contract_amount"] == "7654321"
    assert event.attributes["contract_start_date"] == "2025-09-01"
    assert event.attributes["contract_end_date"] == "2026-08-31"
    assert event.attributes["counterparty"] == "\uc591\ubc0d\ud574\uc6b4"
    assert fact.metric is MetricConcept.CONTRACT_AMOUNT
    assert fact.accounting_scope is FactAccountingScope.NOT_APPLICABLE
    assert fact.raw_value == "7654321"
    assert fact.raw_unit == "dummy-currency"
    assert fact.normalized_value is None
    assert fact.normalized_unit is None
    amount_evidence = next(
        item for item in answer.evidence if item.field_name == "contract_amount"
    )
    assert event.source_doc_id == amount_evidence.doc_id
    assert fact.evidence_ids == [amount_evidence.evidence_id]


def test_q02_rejects_non_supply_contract_document(tmp_path: Path) -> None:
    write_q02_event_document(
        tmp_path,
        title="\uc2e0\uaddc\uc2dc\uc124\ud22c\uc790\ub4f1",
    )

    answer = post_q02_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.UNRESOLVED
    assert not answer.events
    assert not answer.facts


def test_q02_rejects_ambiguous_matching_documents(tmp_path: Path) -> None:
    write_q02_event_document(tmp_path, filename="event-a.xml")
    write_q02_event_document(tmp_path, filename="event-b.xml")

    answer = post_q02_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.UNRESOLVED
    assert not answer.events
    assert not answer.facts


def test_q02_does_not_treat_correction_as_original_event(tmp_path: Path) -> None:
    write_q02_event_document(tmp_path, correction=True)

    answer = post_q02_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.UNRESOLVED
    assert not answer.events
    assert not answer.facts


def test_q13_resolves_full_chain_and_preserves_both_sides_evidence(
    tmp_path: Path,
) -> None:
    write_q13_event_document(
        tmp_path,
        filename="original.xml",
        receipt_date="2025-09-15",
        amount="100",
        end_date="2026-01-01",
    )
    write_q13_event_document(
        tmp_path,
        filename="correction.xml",
        receipt_date="2025-10-01",
        amount="120",
        end_date="2026-06-01",
        correction=True,
        original_filing_date="2025-09-15",
        chain_complete=True,
        related_disclosures=[
            {
                "document_title": Q13_TITLE,
                "filing_date": "2025-09-15",
            }
        ],
    )

    answer = post_q13_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.ANSWERABLE
    assert len(answer.events) == 2
    assert len(answer.event_histories) == 1
    history = answer.event_histories[0]
    assert history.chain_complete is True
    changes = {
        change.field_name: (change.before, change.after)
        for change in history.changes
    }
    assert changes == {
        "contract_amount": ("100", "120"),
        "contract_end_date": ("2026-01-01", "2026-06-01"),
    }
    assert {evidence.doc_id for evidence in answer.evidence} == {
        "exchange_original",
        "exchange_correction",
    }
    amount_evidence = [
        evidence
        for evidence in answer.evidence
        if evidence.field_name == "contract_amount"
    ]
    assert {evidence.raw_text for evidence in amount_evidence} == {
        "\uacc4\uc57d\uae08\uc561(dummy-currency): 100",
        "\uacc4\uc57d\uae08\uc561(dummy-currency): 120",
    }

    query_plan = analyze_q13_question(Q13_QUESTION)
    assert query_plan is not None
    documents = retrieve_event_history_documents(query_plan, tmp_path)
    correction = next(doc for doc in documents if doc["metadata"]["is_correction"])
    originals = [doc for doc in documents if not doc["metadata"]["is_correction"]]
    link = link_supply_contract_correction(correction, originals)
    assert link.relation.value == "correction_of"
    assert link.resolution_status is EventLinkResolutionStatus.RESOLVED
    assert link.resolved_doc_id == "exchange_original"
    assert link.raw_reference.original_filing_date == "2025-09-15"
    assert link.raw_reference.correction_reason == "dummy correction reason"


def test_q13_omits_unchanged_fields(tmp_path: Path) -> None:
    write_q13_event_document(
        tmp_path,
        filename="original.xml",
        receipt_date="2025-09-15",
        amount="100",
        end_date="2026-01-01",
    )
    write_q13_event_document(
        tmp_path,
        filename="correction.xml",
        receipt_date="2025-10-01",
        amount="100",
        end_date="2026-06-01",
        correction=True,
        original_filing_date="2025-09-15",
        chain_complete=True,
    )

    answer = post_q13_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.ANSWERABLE
    fields = {change.field_name for change in answer.event_histories[0].changes}
    assert "contract_amount" not in fields
    assert fields == {"contract_end_date"}


def test_q13_rejects_ambiguous_original_link(tmp_path: Path) -> None:
    for filename in ("original-a.xml", "original-b.xml"):
        write_q13_event_document(
            tmp_path,
            filename=filename,
            receipt_date="2025-09-15",
            amount="100",
            end_date="2026-01-01",
        )
    write_q13_event_document(
        tmp_path,
        filename="correction.xml",
        receipt_date="2025-10-01",
        amount="120",
        end_date="2026-06-01",
        correction=True,
        original_filing_date="2025-09-15",
        chain_complete=True,
    )

    answer = post_q13_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.UNRESOLVED
    assert not answer.event_histories
    assert not answer.evidence


def test_q13_returns_partial_for_explicitly_incomplete_chain(tmp_path: Path) -> None:
    write_q13_event_document(
        tmp_path,
        filename="original.xml",
        receipt_date="2025-09-15",
        amount="100",
        end_date="2026-01-01",
    )
    write_q13_event_document(
        tmp_path,
        filename="correction.xml",
        receipt_date="2025-10-01",
        amount="120",
        end_date="2026-06-01",
        correction=True,
        original_filing_date="2025-09-15",
        chain_complete=False,
        related_disclosures=[
            {
                "document_title": Q13_TITLE,
                "filing_date": "2025-09-25",
            }
        ],
    )

    answer = post_q13_from_corpus(tmp_path)

    assert answer.status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert answer.event_histories[0].chain_complete is False
    assert answer.event_histories[0].changes
    assert answer.evidence
    assert "INCOMPLETE_CHAIN" in answer.answer


def test_unsupported_question_is_explicitly_unanswerable() -> None:
    answer = post_question("unsupported development question")

    assert answer.status is AnswerabilityStatus.UNANSWERABLE
    assert not answer.facts
    assert not answer.events
    assert not answer.event_histories
    assert not answer.evidence
