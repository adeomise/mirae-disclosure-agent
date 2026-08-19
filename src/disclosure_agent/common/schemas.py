"""Pydantic models for the Disclosure Agent contract v0.1."""

from typing import Any

from pydantic import BaseModel, Field

from disclosure_agent.common.enums import (
    AnswerabilityStatus,
    ChainPolicy,
    DocumentClass,
    EventLinkRelation,
    EventLinkResolutionStatus,
    EventType,
    EvidenceLocationType,
    FactAccountingScope,
    Intent,
    MetricConcept,
    QueryAccountingScope,
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
    resolution_status: EventLinkResolutionStatus


class FieldChange(BaseModel):
    """A named event attribute change between disclosures."""

    field_name: str
    before: str | float | None = None
    after: str | float | None = None
    unit: str | None = None


class EventHistory(BaseModel):
    """Ordered documents and field changes for one event."""

    event_id: str
    document_ids: list[str] = Field(default_factory=list)
    changes: list[FieldChange] = Field(default_factory=list)
    chain_complete: bool


class AskRequest(BaseModel):
    """API request contract for asking a disclosure question."""

    question: str


class AnswerResponse(BaseModel):
    """Answer and all structured evidence-bearing results."""

    answer: str
    status: AnswerabilityStatus
    facts: list[Fact] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    event_histories: list[EventHistory] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
