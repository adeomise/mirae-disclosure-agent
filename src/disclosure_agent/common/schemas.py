"""Pydantic models for the Disclosure Agent contract v0.1."""

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator

from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ChainPolicy,
    ComparisonRelation,
    DocumentClass,
    EventLinkRelation,
    EventLinkResolutionStatus,
    EventType,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    Intent,
    MetricConcept,
    QueryAccountingScope,
    ResultInputType,
    SectionScope,
    SummaryItemType,
)


class Entity(BaseModel):
    """An entity mention and its resolved identity."""

    surface: str
    resolved_name: str
    role: str


class Period(BaseModel):
    """A minimal temporal constraint extracted from a query."""

    kind: str
    year: int | None = None
    period_type: str | None = None
    range: str | None = None


class QueryPlan(BaseModel):
    """Structured interpretation of a user question."""

    query_id: str
    original_query: str
    intent: Intent
    entities: list[Entity] = Field(default_factory=list)
    periods: list[Period] = Field(default_factory=list)
    document_class: DocumentClass
    document_subtype: str | None = None
    accounting_scope: QueryAccountingScope
    metrics: list[MetricConcept] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)
    chain_policy: ChainPolicy | None = None


class EvidenceProvenance(BaseModel):
    """Source table and cell metadata used to produce evidence."""

    table_id: str | None = None
    row_index: int | None = None
    column_index: int | None = None
    acode: str | None = None
    acontext: str | None = None
    unit_declaration: str | None = None
    unit_source: str | None = None
    unit_multiplier: int | Decimal | None = None
    unit_trusted: bool | None = None
    aclass: str | None = None
    table_classification: str | None = None


class Evidence(BaseModel):
    """A traceable location in a source disclosure."""

    evidence_id: str
    doc_id: str
    location_type: EvidenceLocationType
    section_path: list[str] = Field(default_factory=list)
    table_name: str | None = None
    row_label: str | None = None
    column_label: str | None = None
    field_name: str | None = None
    raw_text: str | None = None
    provenance: EvidenceProvenance | None = None


class Fact(BaseModel):
    """A source value with separate raw and normalized representations."""

    fact_id: str
    company: str
    metric: MetricConcept
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    accounting_scope: FactAccountingScope
    raw_value: str | None = None
    raw_unit: str | None = None
    normalized_value: float | str | None = None
    normalized_unit: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class SummaryItem(BaseModel):
    """One bounded content unit inside a requested document scope.

    값을 담지 않는다. 요약 항목이 무엇이고 그 근거가 어디인지만 담으며, 원문 발췌는
    Evidence.raw_text 하나에만 둔다. 두 곳에 두면 어느 쪽이 정본인지 알 수 없게 된다
    (docs/agent_design/evidence_and_answerability.md의 Fact·Evidence 구분과 같은 이유).
    """

    item_id: str
    item_type: SummaryItemType
    label: str
    evidence_ids: list[str] = Field(min_length=1)


class DocumentSummary(BaseModel):
    """A single document's content collected for one requested section scope.

    Pattern B는 Fact를 만들지 않는다. 지정 범위 안의 항목과 그 항목마다의 Evidence가
    결과이며, 최소 Fact 요건이 0인 것이 확정 사항이다
    (docs/agent_design/evidence_and_answerability.md 유형별 최소 요건).
    """

    summary_id: str
    doc_id: str
    company: str
    report_name: str
    section_scope: SectionScope
    section_path: list[str] = Field(default_factory=list)
    items: list[SummaryItem] = Field(min_length=1)


class Event(BaseModel):
    """A minimally structured event disclosure."""

    event_id: str
    event_type: EventType
    company: str
    source_doc_id: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    is_correction: bool = False


class RawReference(BaseModel):
    """Reference text preserved from a source disclosure before linking."""

    document_title: str | None = None
    original_filing_date: str | None = None
    correction_filing_date: str | None = None
    correction_reason: str | None = None
    related_disclosures: list[dict[str, str]] = Field(default_factory=list)


class EventLink(BaseModel):
    """A resolved or unresolved relationship between disclosures."""

    link_id: str
    source_doc_id: str
    relation: EventLinkRelation
    raw_reference: RawReference
    resolved_doc_id: str | None = None
    candidate_doc_ids: list[str] = Field(default_factory=list)
    resolution_status: EventLinkResolutionStatus


class FieldChange(BaseModel):
    """A named event attribute change between disclosures."""

    field_name: str
    raw_field: str | None = None
    before: str | float | None = None
    after: str | float | None = None
    unit: str | None = None
    before_evidence_id: str | None = None
    after_evidence_id: str | None = None


class EventHistory(BaseModel):
    """Ordered documents and field changes for one event."""

    event_id: str
    document_ids: list[str] = Field(default_factory=list)
    changes: list[FieldChange] = Field(default_factory=list)
    chain_complete: bool


class ResolvedFieldValue(BaseModel):
    """A resolved field value with document and evidence provenance."""

    value: str | float | None = None
    unit: str | None = None
    source_doc_id: str
    evidence_ids: list[str] = Field(default_factory=list)


class ResolvedEventState(BaseModel):
    """An event's field-level state at a filing-date resolution cutoff.

    ``event_id`` is the correction-chain identity used by ``EventHistory.event_id``.
    It may therefore have a different meaning from an individual ``Event.event_id``.
    """

    event_id: str
    event_type: EventType
    company: str
    as_of: date
    fields: dict[str, ResolvedFieldValue] = Field(default_factory=dict)


class ExecutionIssue(BaseModel):
    """A reason the Agent execution was blocked or failed."""

    stage: str
    reason: str
    detail: str


class AnswerabilityDetail(BaseModel):
    """Original Agent diagnosis behind a canonical answerability reason."""

    stage: str | None = None
    reason: str
    detail: str | None = None


class ResultInputReference(BaseModel):
    """A typed reference to one semantic result input."""

    input_type: ResultInputType
    input_id: str


class CalculationResult(BaseModel):
    """A minimal deterministic calculation with its inputs and evidence linkage."""

    calculation_id: str
    operation: str
    inputs: list[ResultInputReference] = Field(min_length=1)
    value: float | str | None = None
    unit: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_direct_self_reference(self) -> "CalculationResult":
        if any(
            item.input_type is ResultInputType.CALCULATION
            and item.input_id == self.calculation_id
            for item in self.inputs
        ):
            raise ValueError("CalculationResult cannot directly reference itself")
        return self

class ComparisonResult(BaseModel):
    """A successful relation between two comparable inputs."""

    comparison_id: str
    left: ResultInputReference
    right: ResultInputReference
    relation: ComparisonRelation


class ConversionGap(BaseModel):
    """Structured information that cannot yet be represented by the contract."""

    kind: str
    subject: str
    dropped: dict[str, Any] = Field(default_factory=dict)
    detail: str = ""
    pending_decision: bool = False
    planned_contract_field: str | None = None
    is_temporary: bool = False
    contract_version: str = "0.1"


class AgentExecutionResult(BaseModel):
    """Structured Agent output before natural-language answer construction.

    Execution status is independent from answerability: a completed execution
    may still determine that the available evidence cannot answer the question.
    Blocked and failed executions use execution_issue for diagnostics.
    """

    execution_status: ExecutionStatus
    answerability_status: AnswerabilityStatus | None = None
    answerability_reason: AnswerabilityReason | None = None
    answerability_detail: AnswerabilityDetail | None = None
    execution_issue: ExecutionIssue | None = None
    facts: list[Fact] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    event_links: list[EventLink] = Field(default_factory=list)
    event_histories: list[EventHistory] = Field(default_factory=list)
    resolved_event_states: list[ResolvedEventState] = Field(default_factory=list)
    document_summaries: list[DocumentSummary] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    calculations: list[CalculationResult] = Field(default_factory=list)
    comparisons: list[ComparisonResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    conversion_gaps: list[ConversionGap] = Field(default_factory=list)


class AskRequest(BaseModel):
    """API request contract for asking a disclosure question."""

    question: str


class AnswerResponse(BaseModel):
    """Answer and all structured evidence-bearing results."""

    answer: str
    status: AnswerabilityStatus
    reason: AnswerabilityReason | None = None
    facts: list[Fact] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    event_links: list[EventLink] = Field(default_factory=list)
    event_histories: list[EventHistory] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
