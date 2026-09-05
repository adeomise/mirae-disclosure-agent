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


class NarrativeComparisonClaim(BaseModel):
    """One observed difference between two fiscal years' narrative sections.

    양쪽 연도의 Evidence를 모두 요구한다:
        서술 비교는 "무엇이 달라졌는가"를 말한다. 한쪽 연도의 근거만으로는 그 연도에 무엇이
        적혀 있었는지까지만 말할 수 있고, 달라졌다는 진술은 다른 연도를 함께 봐야 성립한다.
        한쪽이 비어도 통과하면 한 해의 서술을 변화로 옮겨 적은 항목이 형식만 맞는 채로 남는다
        (docs/agent_design/evidence_and_answerability.md 유형 3 "변화 항목마다 해당 연도
        Evidence").

    값도 원인도 담지 않는다:
        Q09는 계산 경로를 지나지 않으므로 수치를 담을 자리가 없고(agent_patterns.md Pattern C
        서술 경로), 왜 달라졌는지는 원문이 말하지 않는 한 추론이다. 담을 자리를 만들어 두면
        비워 두기로 한 판단이 다음 구현에서 조용히 채워진다.
    """

    claim_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    left_evidence_ids: list[str] = Field(min_length=1)
    right_evidence_ids: list[str] = Field(min_length=1)


class NarrativeComparisonResult(BaseModel):
    """Two fiscal years of one company's same semantic section, compared as narrative.

    claims가 비어 있는 것과 결과가 없는 것은 다르다:
        claims=[]는 "양쪽을 모두 읽었고 의미 있는 변화를 찾지 못했다"이며 그 자체가 답이다.
        그래서 최소 길이를 두지 않는다. 반대로 어느 한쪽 Section을 확정하지 못한 경우는 이
        결과를 만들지 않고 상위 단계가 막는다. 둘을 같은 모양으로 두면 '변화 없음'과 '보지
        못함'이 구분되지 않으며, 그것이 이 문항의 주요 실패 모드다
        (docs/agent_design/representative_questions.md Q09).

    section_path를 양쪽 모두 요구하는 이유:
        같은 의미 범위를 견줬다는 사실은 결과에 남아야 확인할 수 있다. 경로가 없으면 어느
        Section을 읽었는지 결과만 봐서는 드러나지 않는다.

    CalculationResult·ComparisonResult와 잇지 않는다:
        Q09는 수치 비교가 아니다. 참조를 담을 자리를 만들면 서술 결과가 수치 결과의 근거로
        쓰일 수 있게 되고, 그 경로는 이 Pattern이 만들지 않는다.
    """

    comparison_id: str = Field(min_length=1)
    company: str = Field(min_length=1)
    section_scope: SectionScope
    left_fiscal_year: int
    right_fiscal_year: int
    left_section_path: list[str] = Field(min_length=1)
    right_section_path: list[str] = Field(min_length=1)
    claims: list[NarrativeComparisonClaim] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_ordered_distinct_years(self) -> "NarrativeComparisonResult":
        """
        두 회계연도가 서로 다르고 오래된 쪽이 left인지 확인한다.

        순서를 고정하는 이유는 Pattern C의 수치 경로가 변화량을 시간 순서로 만드는 것과 같다
        (handlers/temporal_compare의 ``sorted(plan.years)``). 질문에 적힌 순서를 그대로 쓰면
        같은 변화가 방향이 뒤집힌 채로 서술되고, 뒤집힌 서술도 그럴듯하다.

        같은 연도를 막는 이유는, 한 해를 자기 자신과 견준 결과는 변화가 없다는 판정처럼
        보이지만 실제로는 비교를 하지 않은 것이기 때문이다.
        """
        if self.left_fiscal_year == self.right_fiscal_year:
            raise ValueError(
                "NarrativeComparisonResult는 서로 다른 두 회계연도를 요구합니다: "
                f"{self.left_fiscal_year}"
            )

        if self.left_fiscal_year > self.right_fiscal_year:
            raise ValueError(
                "NarrativeComparisonResult의 left_fiscal_year는 right_fiscal_year보다 "
                f"앞서야 합니다: {self.left_fiscal_year} > {self.right_fiscal_year}"
            )

        return self


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
    # 서술 비교(Q09)의 결과. comparisons와 나눠 둔다. ComparisonResult는 두 값의 우열이고
    # 이쪽은 두 서술의 변화 항목이라, 한 목록에 담으면 받는 쪽이 어느 것이 수치 판정인지
    # 항목마다 확인해야 한다.
    narrative_comparisons: list[NarrativeComparisonResult] = Field(default_factory=list)
    # 질문이 요청한 결정적 처리. QueryPlan.operations와 같은 어휘를 그대로 옮긴다.
    #
    # 계획이 표현 계층까지 넘어오지 않으므로, 무엇을 요청했는지는 이 자리로만 전달된다.
    # 값을 만들지 않는다. 세는 일도 여기서 하지 않으며, 담기는 것은 요청의 이름뿐이다.
    #
    # 기본값이 비어 있어 이 필드를 채우지 않는 기존 producer는 지금까지와 똑같이 동작한다.
    operations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    conversion_gaps: list[ConversionGap] = Field(default_factory=list)


class AskRequest(BaseModel):
    """API request contract for asking a disclosure question."""

    question: str


class AnswerResponse(BaseModel):
    """Answer and all structured evidence-bearing results.

    ``resolved_event_states`` carries the field-level state an event disclosure was
    resolved to at one cutoff. 이벤트 단건 요약(Q06)의 답은 그 상태 자체이며, 자리가 없으면
    답의 본체가 문장에만 남고 구조화 결과에서 사라진다. 최신 상태와 원공시 상태 중 어느
    시점인지는 ``as_of``와 각 필드의 ``source_doc_id``가 밝힌다.
    """

    answer: str
    status: AnswerabilityStatus
    reason: AnswerabilityReason | None = None
    facts: list[Fact] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    event_links: list[EventLink] = Field(default_factory=list)
    event_histories: list[EventHistory] = Field(default_factory=list)
    resolved_event_states: list[ResolvedEventState] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
