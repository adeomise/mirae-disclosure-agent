"""Validation tests for contract v0.1 vertical-slice objects."""

from decimal import Decimal

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
    AnswerabilityDetail,
    AnswerResponse,
    CalculationResult,
    ConversionGap,
    Event,
    EventHistory,
    EventLink,
    Evidence,
    EvidenceProvenance,
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


def test_facility_investment_event_type_validates_and_serializes() -> None:
    event = Event(
        event_id="event_dummy_facility_investment",
        event_type=EventType.FACILITY_INVESTMENT,
        company="dummy-company",
        source_doc_id="doc_dummy_event",
    )

    assert EventType.FACILITY_INVESTMENT.value == "facility_investment"
    assert event.event_type is EventType.FACILITY_INVESTMENT
    assert event.model_dump(mode="json")["event_type"] == "facility_investment"


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


def test_unresolved_ambiguous_match_preserves_answerability_detail() -> None:
    detail = AnswerabilityDetail(
        stage="fact_selection",
        reason="ambiguous_candidates",
        detail="Multiple source cells satisfy the same conditions.",
    )
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNRESOLVED,
        answerability_reason=AnswerabilityReason.AMBIGUOUS_MATCH,
        answerability_detail=detail,
    )

    assert AnswerabilityReason.AMBIGUOUS_MATCH.value == "ambiguous_match"
    assert result.answerability_status is AnswerabilityStatus.UNRESOLVED
    assert result.answerability_reason is AnswerabilityReason.AMBIGUOUS_MATCH
    assert result.answerability_detail == detail
    assert result.model_dump(mode="json")["answerability_detail"] == {
        "stage": "fact_selection",
        "reason": "ambiguous_candidates",
        "detail": "Multiple source cells satisfy the same conditions.",
    }


def test_answerability_detail_accepts_optional_diagnostic_fields() -> None:
    detail = AnswerabilityDetail(reason="ambiguous_candidates")

    assert detail.stage is None
    assert detail.detail is None


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
    first.conversion_gaps.append(
        ConversionGap(kind="dummy-gap", subject="dummy-subject")
    )

    assert second.warnings == []
    assert second.conversion_gaps == []


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


def test_evidence_preserves_structured_provenance() -> None:
    provenance = EvidenceProvenance(
        table_id="table_0002",
        row_index=2,
        column_index=1,
        acode="ifrs-full_Revenue",
        acontext="CFY2025dFY_ConsolidatedMember",
        unit_declaration="(unit: KRW millions)",
        unit_source="table_group",
        unit_multiplier=1_000_000,
        unit_trusted=True,
        aclass="{XBRL}IS_C1",
        table_classification="financial_statement",
    )
    evidence = Evidence(
        evidence_id="evidence_dummy_revenue",
        doc_id="doc_dummy_periodic",
        location_type=EvidenceLocationType.TABLE,
        provenance=provenance,
    )

    assert evidence.provenance == provenance
    assert provenance.unit_multiplier == 1_000_000
    assert provenance.unit_trusted is True
    assert provenance.unit_source == "table_group"
    assert provenance.table_id == "table_0002"
    assert provenance.row_index == 2
    assert provenance.column_index == 1
    assert provenance.acode == "ifrs-full_Revenue"
    assert provenance.acontext == "CFY2025dFY_ConsolidatedMember"
    assert provenance.aclass == "{XBRL}IS_C1"
    assert provenance.table_classification == "financial_statement"
    assert evidence.model_dump()["provenance"] == provenance.model_dump()


def test_evidence_provenance_accepts_partial_fields_and_decimal_multiplier() -> None:
    provenance = EvidenceProvenance(
        table_id="table_0002",
        unit_multiplier=Decimal("1000000"),
    )

    assert provenance.table_id == "table_0002"
    assert provenance.unit_multiplier == Decimal("1000000")
    assert provenance.row_index is None
    assert provenance.unit_trusted is None


def test_conversion_gap_preserves_contract_gap_serialization_fields() -> None:
    gap = ConversionGap(
        kind="raw_field_name",
        subject="contract_end_date",
        dropped={"raw_field": "raw contract end date"},
        detail="The source field name is not represented by the contract.",
        pending_decision=False,
        planned_contract_field="FieldChange.raw_field",
        is_temporary=True,
        contract_version="0.1",
    )

    assert gap.model_dump() == {
        "kind": "raw_field_name",
        "subject": "contract_end_date",
        "dropped": {"raw_field": "raw contract end date"},
        "detail": "The source field name is not represented by the contract.",
        "pending_decision": False,
        "planned_contract_field": "FieldChange.raw_field",
        "is_temporary": True,
        "contract_version": "0.1",
    }


def test_execution_result_serializes_to_json_compatible_contract() -> None:
    gap = ConversionGap(
        kind="change_source_document",
        subject="contract_amount",
        dropped={"source_doc_id": "doc_dummy_correction"},
        detail="Change provenance is not represented by the contract.",
        planned_contract_field=(
            "FieldChange.before_evidence_id / after_evidence_id"
        ),
        is_temporary=True,
    )
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
        conversion_gaps=[gap],
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
    assert dumped["conversion_gaps"] == [
        {
            "kind": "change_source_document",
            "subject": "contract_amount",
            "dropped": {"source_doc_id": "doc_dummy_correction"},
            "detail": "Change provenance is not represented by the contract.",
            "pending_decision": False,
            "planned_contract_field": (
                "FieldChange.before_evidence_id / after_evidence_id"
            ),
            "is_temporary": True,
            "contract_version": "0.1",
        }
    ]
    assert "answer" not in dumped
