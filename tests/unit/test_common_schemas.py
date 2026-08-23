"""Validation tests for contract v0.1 vertical-slice objects."""

from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    EventLinkRelation,
    EventLinkResolutionStatus,
    EventType,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    AnswerResponse,
    CalculationResult,
    Event,
    EventHistory,
    EventLink,
    Evidence,
    ExecutionIssue,
    Fact,
    FieldChange,
    RawReference,
)
from disclosure_agent.event.identity import make_event_id


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


def test_additional_source_metric_concepts_validate_as_facts() -> None:
    metrics = (
        MetricConcept.OPERATING_INCOME,
        MetricConcept.HOLDING_SHARES,
        MetricConcept.HOLDING_RATIO,
    )

    for metric in metrics:
        fact = Fact(
            fact_id=f"fact_dummy_{metric.value}",
            company="dummy-company",
            metric=metric,
            accounting_scope=FactAccountingScope.NOT_APPLICABLE,
        )
        assert fact.metric is metric


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


def test_answer_response_preserves_reason_and_event_link() -> None:
    link = EventLink(
        link_id="link_dummy_correction",
        source_doc_id="doc_dummy_correction",
        relation=EventLinkRelation.CORRECTION_OF,
        raw_reference=RawReference(original_filing_date="YYYY-MM-DD"),
        resolved_doc_id="doc_dummy_original",
        resolution_status=EventLinkResolutionStatus.RESOLVED,
    )
    partial = AnswerResponse(
        answer="dummy partial answer",
        status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        reason=AnswerabilityReason.INCOMPLETE_CHAIN,
        event_links=[link],
    )
    unresolved = AnswerResponse(
        answer="dummy unresolved answer",
        status=AnswerabilityStatus.UNRESOLVED,
        reason=AnswerabilityReason.AMBIGUOUS_LINK,
    )

    assert partial.reason is AnswerabilityReason.INCOMPLETE_CHAIN
    assert partial.event_links == [link]
    assert unresolved.reason is AnswerabilityReason.AMBIGUOUS_LINK


def test_ambiguous_event_link_preserves_candidate_doc_ids() -> None:
    link = EventLink(
        link_id="link_dummy_ambiguous",
        source_doc_id="doc_dummy_correction",
        relation=EventLinkRelation.CORRECTION_OF,
        raw_reference=RawReference(original_filing_date="YYYY-MM-DD"),
        candidate_doc_ids=["doc_dummy_a", "doc_dummy_b"],
        resolution_status=EventLinkResolutionStatus.AMBIGUOUS,
    )

    assert link.resolved_doc_id is None
    assert link.candidate_doc_ids == ["doc_dummy_a", "doc_dummy_b"]


def test_field_change_preserves_before_and_after_evidence_ids() -> None:
    change = FieldChange(
        field_name="contract_amount",
        raw_field="\uacc4\uc57d\uae08\uc561(\uc6d0)",
        before="dummy-before",
        after="dummy-after",
        before_evidence_id="evidence_dummy_before",
        after_evidence_id="evidence_dummy_after",
    )

    assert change.raw_field == "\uacc4\uc57d\uae08\uc561(\uc6d0)"
    assert change.before_evidence_id == "evidence_dummy_before"
    assert change.after_evidence_id == "evidence_dummy_after"


def test_event_id_is_deterministic_for_canonical_root_document() -> None:
    first = make_event_id(EventType.SUPPLY_CONTRACT, "doc_root_a")
    repeated = make_event_id(EventType.SUPPLY_CONTRACT, "doc_root_a")
    different_root = make_event_id(EventType.SUPPLY_CONTRACT, "doc_root_b")

    assert first == repeated
    assert first != different_root


def test_completed_answerable_execution_result() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.execution_issue is None


def test_completed_unanswerable_execution_result_with_reason() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=AnswerabilityReason.INSUFFICIENT_EVIDENCE,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.INSUFFICIENT_EVIDENCE


def test_blocked_execution_result_with_issue() -> None:
    issue = ExecutionIssue(
        stage="retrieval",
        reason="unsupported_scope",
        detail="The requested scope is not supported.",
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.BLOCKED,
        execution_issue=issue,
    )

    assert result.answerability_status is None
    assert result.execution_issue == issue


def test_failed_execution_result_with_issue() -> None:
    issue = ExecutionIssue(
        stage="execution",
        reason="unexpected_failure",
        detail="An unexpected technical failure occurred.",
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.FAILED,
        execution_issue=issue,
    )

    assert result.answerability_status is None
    assert result.execution_issue == issue


def test_execution_result_default_lists_are_not_shared() -> None:
    first = AgentExecutionResult(execution_status=ExecutionStatus.COMPLETED)
    second = AgentExecutionResult(execution_status=ExecutionStatus.COMPLETED)
    collection_fields = (
        "facts",
        "events",
        "event_links",
        "event_histories",
        "evidence",
        "calculations",
        "warnings",
        "conversion_gaps",
    )

    for field_name in collection_fields:
        assert getattr(first, field_name) is not getattr(second, field_name)

    first.warnings.append("first-only")
    assert second.warnings == []


def test_execution_result_accepts_existing_common_contract_objects() -> None:
    evidence = Evidence(
        evidence_id="evidence_dummy_revenue",
        doc_id="doc_dummy_periodic",
        location_type=EvidenceLocationType.TABLE,
        table_name="dummy-table",
        row_label="revenue",
        column_label="2025",
    )
    fact = Fact(
        fact_id="fact_dummy_revenue",
        company="dummy-company",
        metric=MetricConcept.REVENUE,
        fiscal_year=2025,
        accounting_scope=FactAccountingScope.CONSOLIDATED,
        raw_value="dummy",
        raw_unit="dummy-unit",
        evidence_ids=[evidence.evidence_id],
    )
    history = EventHistory(
        event_id="event_dummy_supply_contract",
        document_ids=["doc_dummy_original"],
        chain_complete=True,
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[fact],
        event_histories=[history],
        evidence=[evidence],
    )

    assert result.facts == [fact]
    assert result.event_histories == [history]
    assert result.evidence == [evidence]


def test_execution_result_serializes_to_json_compatible_contract() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        calculations=[
            CalculationResult(
                operation="identity",
                value="dummy",
                unit="dummy-unit",
                evidence_ids=["evidence_dummy"],
            )
        ],
        warnings=["dummy-warning"],
        conversion_gaps=["dummy-gap"],
    )

    dumped = result.model_dump(mode="json")

    assert dumped["execution_status"] == "COMPLETED"
    assert dumped["answerability_status"] == "ANSWERABLE"
    assert dumped["calculations"] == [
        {
            "operation": "identity",
            "value": "dummy",
            "unit": "dummy-unit",
            "evidence_ids": ["evidence_dummy"],
        }
    ]
    assert dumped["warnings"] == ["dummy-warning"]
    assert dumped["conversion_gaps"] == ["dummy-gap"]
    assert "answer" not in dumped
