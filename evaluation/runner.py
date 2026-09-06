"""Validate evaluation datasets and deterministically compare Agent predictions."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from disclosure_agent.agent.router import answer_question
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ComparisonRelation,
    EventLinkResolutionStatus,
    EventType,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
    SectionScope,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    Event,
    Evidence,
    Fact,
    ResolvedEventState,
    ResolvedFieldValue,
    ResultInputReference,
)

EVALUATION_DIR = Path(__file__).parent
DEFAULT_QUESTIONS_PATH = EVALUATION_DIR / "questions.jsonl"
DEFAULT_GOLD_PATH = EVALUATION_DIR / "gold_answers.jsonl"


class QuestionCase(BaseModel):
    """One contract or regression question."""

    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    synthetic: bool
    evaluation_mode: Literal["contract", "regression"]
    representative_question_id: str | None = None
    lifecycle: Literal["active", "future"] = "active"
    pattern: str | None = None
    as_of_date: str | None = None
    corpus_snapshot: str | None = None
    tags: list[str] = Field(default_factory=list)


class ExpectedFact(BaseModel):
    """Fields that one distinct predicted Fact must match."""

    company: str | None = None
    metric: MetricConcept | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    accounting_scope: FactAccountingScope | None = None
    raw_value: str | None = None
    raw_unit: str | None = None
    normalized_value: float | str | None = None
    normalized_unit: str | None = None
    evidence_ids: list[str] | None = None


class ExpectedEvent(BaseModel):
    """Fields that one distinct predicted Event must match."""

    event_type: EventType | None = None
    company: str | None = None
    source_doc_id: str | None = None
    is_correction: bool | None = None


class ExpectedEvidence(BaseModel):
    """Fields that one distinct predicted Evidence must match.

    Every other Expected* type pins identity through a typed carrier that already
    references Evidence by id (Fact, Calculation, DocumentSummary, EventHistory), so
    `integrity.*_evidence_references` can confirm those references resolve. A result
    can also carry Evidence that no typed carrier references at all — Pattern C's
    narrative-compare path does this when it retains Evidence for a fiscal year it
    resolved before stopping on a later year it could not (Q09). `counts.evidence`
    alone cannot tell that retained Evidence apart from same-count Evidence pulled
    from the wrong filing: two results with three Evidence rows are indistinguishable
    to a count-only gold even when one result's rows come from an unresolved
    candidate document. `doc_id` is the identity that distinguishes them; it is
    required rather than optional because an ExpectedEvidence without it would not
    pin anything a bare `counts.evidence` integer doesn't already pin.

    `section_path` is compared for exact list equality, mirroring how `DocumentSummary`
    and `Fact` gold already compare their own fields. `raw_text` has no field here:
    committing source excerpts to gold would recreate the values this repository's
    no-source-values rule already forbids elsewhere (D6, `evaluation/README.md`), and
    doc_id + section_path already say which filing and which part of it the row came
    from without quoting it.
    """

    doc_id: str
    location_type: EvidenceLocationType | None = None
    section_path: list[str] | None = None


class ExpectedResolvedField(BaseModel):
    """Fields that one distinct ResolvedFieldValue must match."""

    value: float | str | None = None
    unit: str | None = None
    source_doc_id: str | None = None


class ExpectedResolvedEventState(BaseModel):
    """Fields that one distinct predicted ResolvedEventState must match.

    ``fields`` pins a subset of the state's field-level values, each against its
    own ``ResolvedFieldValue`` (value/unit/source_doc_id). A count-only check on
    ``resolved_event_states`` cannot tell a correctly resolved event apart from a
    same-shaped result that resolved a different disclosure (same collection
    sizes, different company/subject/amount): the identity lives in the field
    values and their source document, not in how many states or fields exist.
    """

    event_type: EventType | None = None
    company: str | None = None
    event_id: str | None = None
    as_of: date | None = None
    fields: dict[str, ExpectedResolvedField] | None = None


class EventHistoryExpectation(BaseModel):
    """Structural link and chain requirements for event-history results."""

    link_resolution: EventLinkResolutionStatus
    chain_complete: bool
    document_count: int | None = Field(default=None, ge=0)
    change_count: int | None = Field(default=None, ge=0)


class CollectionCounts(BaseModel):
    """Optional exact collection sizes."""

    facts: int | None = Field(default=None, ge=0)
    evidence: int | None = Field(default=None, ge=0)
    events: int | None = Field(default=None, ge=0)
    event_links: int | None = Field(default=None, ge=0)
    event_histories: int | None = Field(default=None, ge=0)
    resolved_event_states: int | None = Field(default=None, ge=0)
    calculations: int | None = Field(default=None, ge=0)
    comparisons: int | None = Field(default=None, ge=0)
    narrative_comparisons: int | None = Field(default=None, ge=0)
    document_summaries: int | None = Field(default=None, ge=0)
    warnings: int | None = Field(default=None, ge=0)
    conversion_gaps: int | None = Field(default=None, ge=0)


class SemanticCountExpectation(BaseModel):
    """Declares which structured collection cardinality answers the question's count.

    A question that asks "how many times" can be answered by more than one
    structured collection in a result (e.g. an EventHistory's document chain
    length vs. its FieldChange count vs. its EventLink count). `counts` alone
    cannot express which of those cardinalities is the actual semantic answer
    to the user's question; it only pins raw collection sizes. This expectation
    is the same collection-cardinality check, plus an explicit claim that
    `source`'s length is the count the user's question means. `source` is
    restricted to structured collections this contract currently commits to as
    a valid answer source, not every collection on the result.
    """

    source: Literal["event_links"]
    value: int = Field(ge=0)


class IntegrityExpectation(BaseModel):
    """Expected reference-integrity outcomes."""

    fact_evidence_references: bool | None = None
    calculation_evidence_references: bool | None = None
    resolved_field_evidence_references: bool | None = None
    result_input_references: bool | None = None
    calculation_values_from_inputs: bool | None = None
    document_summary_evidence_references: bool | None = None
    document_summary_same_filing: bool | None = None
    document_summary_evidence_same_filing: bool | None = None
    event_history_evidence_references: bool | None = None


class ExpectedCalculation(BaseModel):
    """Stable semantic fields for one deterministic calculation."""

    calculation_id: str | None = None
    operation: str
    unit: str | None = None
    value: float | str | None = None
    inputs: list[ResultInputReference] | None = None
    evidence_ids: list[str] | None = None


class ExpectedComparison(BaseModel):
    """Stable semantic fields for one deterministic comparison."""

    left: ResultInputReference
    right: ResultInputReference
    relation: ComparisonRelation


class ExpectedDocumentSummary(BaseModel):
    """Stable semantic fields for one document-scope summary."""

    company: str | None = None
    report_name: str | None = None
    section_scope: SectionScope | None = None
    fiscal_year: int | None = None


def _validate_execution_outcome(
    *,
    execution_status: ExecutionStatus | None,
    status: AnswerabilityStatus | None,
    reason: AnswerabilityReason | None,
    execution_issue: object | None,
    allow_failed: bool,
) -> None:
    """Reject Gold combinations that the Agent execution contract cannot emit."""
    if execution_status is None:
        return
    if execution_status is ExecutionStatus.FAILED:
        if allow_failed:
            return
        raise ValueError("FAILED is not supported in acceptable_outcomes")
    if execution_status is ExecutionStatus.BLOCKED:
        if status is not None:
            raise ValueError("BLOCKED outcomes require status=None")
        if reason is not None:
            raise ValueError("BLOCKED outcomes require reason=None")
        if execution_issue is None:
            raise ValueError("BLOCKED outcomes require execution_issue")
        return
    if status is None:
        raise ValueError("COMPLETED outcomes require a non-null status")
    if execution_issue is not None:
        raise ValueError("COMPLETED outcomes must not define execution_issue")


class ExecutionIssueExpectation(BaseModel):
    """Stable execution-boundary fields for one blocked or failed result."""

    stage: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AcceptableOutcome(BaseModel):
    """One exact execution/answerability boundary accepted by a Gold case."""

    execution_status: ExecutionStatus
    status: AnswerabilityStatus | None
    reason: AnswerabilityReason | None = None
    execution_issue: ExecutionIssueExpectation | None = None

    @model_validator(mode="after")
    def validate_answerability(self) -> AcceptableOutcome:
        _validate_execution_outcome(
            execution_status=self.execution_status,
            status=self.status,
            reason=self.reason,
            execution_issue=self.execution_issue,
            allow_failed=False,
        )
        if self.status in {
            AnswerabilityStatus.ANSWERABLE,
            AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        }:
            raise ValueError("acceptable outcomes are limited to fail-closed results")
        if self.status is None and self.reason is not None:
            raise ValueError("null status must not define an answerability reason")
        if self.status is not None and self.reason is None:
            raise ValueError("non-answerable outcomes must define a reason")
        return self


class GoldAnswer(BaseModel):
    """Deterministic expectations independent of Pattern-internal result shapes."""

    case_id: str = Field(min_length=1)
    execution_status: ExecutionStatus | None = None
    status: AnswerabilityStatus | None = None
    reason: AnswerabilityReason | None = None
    execution_issue: ExecutionIssueExpectation | None = None
    acceptable_outcomes: list[AcceptableOutcome] | None = Field(
        default=None,
        min_length=2,
    )
    fact: ExpectedFact | None = None
    facts: list[ExpectedFact] | None = None
    calculations: list[ExpectedCalculation] | None = None
    document_summaries: list[ExpectedDocumentSummary] | None = None
    comparisons: list[ExpectedComparison] | None = None
    min_evidence_count: int = Field(ge=0)
    evidence: list[ExpectedEvidence] | None = None
    events: list[ExpectedEvent] | None = None
    resolved_event_states: list[ExpectedResolvedEventState] | None = None
    event_history: EventHistoryExpectation | None = None
    counts: CollectionCounts | None = None
    answerability_detail_reason: str | None = None
    answerability_detail_stage: str | None = None
    integrity: IntegrityExpectation | None = None
    semantic_count: SemanticCountExpectation | None = None

    @model_validator(mode="after")
    def validate_answerability(self) -> GoldAnswer:
        answerable = {
            AnswerabilityStatus.ANSWERABLE,
            AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        }
        if self.acceptable_outcomes is not None:
            boundary_fields = {
                "execution_status",
                "status",
                "reason",
                "execution_issue",
            }
            duplicated = boundary_fields & self.model_fields_set
            if duplicated:
                raise ValueError(
                    "acceptable_outcomes cannot be combined with top-level boundary "
                    f"fields: {sorted(duplicated)}"
                )
        _validate_execution_outcome(
            execution_status=self.execution_status,
            status=self.status,
            reason=self.reason,
            execution_issue=self.execution_issue,
            allow_failed=True,
        )
        if self.status is AnswerabilityStatus.ANSWERABLE and self.reason is not None:
            raise ValueError("ANSWERABLE cases must not define a failure reason")
        if self.status is None and self.reason is not None:
            raise ValueError("null status must not define an answerability reason")
        if self.status is not None and self.status not in answerable and self.reason is None:
            raise ValueError("non-answerable cases must define a reason")
        if self.status is AnswerabilityStatus.UNANSWERABLE and (
            self.fact is not None or self.facts
        ):
            raise ValueError("UNANSWERABLE cases must not contain a supported fact")
        if self.fact is not None and self.facts is not None:
            raise ValueError("use either legacy fact or facts, not both")
        if self.answerability_detail_reason == "forward_looking":
            raise ValueError("use forward_looking_prediction, not forward_looking")
        return self

    def expected_facts(self) -> tuple[ExpectedFact, ...]:
        if self.facts is not None:
            return tuple(self.facts)
        if self.fact is not None:
            return (self.fact,)
        return ()


class PredictionCase(AgentExecutionResult):
    """A case ID paired with one canonical AgentExecutionResult prediction."""

    case_id: str = Field(min_length=1)
    source_fiscal_years: dict[str, int] = Field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationDataset:
    """Paired question and gold-answer records."""

    questions: tuple[QuestionCase, ...]
    gold_answers: tuple[GoldAnswer, ...]


AgentExecutor = Callable[..., AgentExecutionResult]


def _read_jsonl(
    path: Path, *, allow_empty: bool = False
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from error
    if not records and not allow_empty:
        raise ValueError(f"{path}: evaluation dataset is empty")
    return records


def _index_unique(records, *, label: str):
    indexed = {}
    for record in records:
        if record.case_id in indexed:
            raise ValueError(f"duplicate {label} case_id: {record.case_id}")
        indexed[record.case_id] = record
    return indexed


def _validate_paired_gold(question: QuestionCase, gold: GoldAnswer) -> None:
    """Apply lifecycle-aware rules after a question and its gold are paired."""
    if gold.status is not AnswerabilityStatus.ANSWERABLE:
        return

    has_semantic_expectation = (
        bool(gold.expected_facts())
        or gold.event_history is not None
        or bool(gold.document_summaries)
        or bool(gold.events)
        or bool(gold.resolved_event_states)
    )
    has_counts = (
        gold.counts is not None
        and bool(gold.counts.model_dump(exclude_none=True))
    )
    is_future_regression = (
        question.evaluation_mode == "regression" and question.lifecycle == "future"
    )

    if is_future_regression:
        if not (
            has_semantic_expectation
            or has_counts
            or gold.min_evidence_count > 0
        ):
            raise ValueError(
                f"{question.case_id}: future ANSWERABLE cases need a semantic or "
                "structural expectation"
            )
        return

    if gold.min_evidence_count < 1:
        raise ValueError("answerable cases must require evidence")
    if not has_semantic_expectation:
        raise ValueError(
            "ANSWERABLE cases need a fact, document-summary, or event-history expectation"
        )


def load_dataset(
    questions_path: Path = DEFAULT_QUESTIONS_PATH,
    gold_path: Path = DEFAULT_GOLD_PATH,
) -> EvaluationDataset:
    """Load and pair validated question and gold records."""
    questions = tuple(
        QuestionCase.model_validate(record) for record in _read_jsonl(questions_path)
    )
    gold_answers = tuple(
        GoldAnswer.model_validate(record) for record in _read_jsonl(gold_path)
    )
    question_index = _index_unique(questions, label="question")
    gold_index = _index_unique(gold_answers, label="gold")
    if question_index.keys() != gold_index.keys():
        missing_gold = sorted(question_index.keys() - gold_index.keys())
        missing_questions = sorted(gold_index.keys() - question_index.keys())
        raise ValueError(
            "unpaired evaluation cases: "
            f"missing_gold={missing_gold}, missing_questions={missing_questions}"
        )
    for question in questions:
        _validate_paired_gold(question, gold_index[question.case_id])
    ordered_gold = tuple(gold_index[item.case_id] for item in questions)
    return EvaluationDataset(questions=questions, gold_answers=ordered_gold)


def load_predictions(path: Path) -> tuple[PredictionCase, ...]:
    """Load canonical AgentExecutionResult predictions and reject duplicate IDs."""
    predictions = tuple(
        PredictionCase.model_validate(record)
        for record in _read_jsonl(path, allow_empty=True)
    )
    _index_unique(predictions, label="prediction")
    return predictions


def evaluate_dataset(dataset: EvaluationDataset) -> dict[str, int | str]:
    """Return the backward-compatible deterministic contract summary."""
    answerable = {
        AnswerabilityStatus.ANSWERABLE,
        AnswerabilityStatus.PARTIALLY_ANSWERABLE,
    }
    return {
        "mode": "case_validation",
        "real_agent_execution": "BLOCKED",
        "case_count": len(dataset.questions),
        "synthetic_case_count": sum(item.synthetic for item in dataset.questions),
        "paired_case_count": len(dataset.gold_answers),
        "answerable_cases_requiring_evidence": sum(
            item.status in answerable and item.min_evidence_count > 0
            for item in dataset.gold_answers
        ),
        "unanswerable_cases_without_fact": sum(
            item.status is AnswerabilityStatus.UNANSWERABLE
            and not item.expected_facts()
            for item in dataset.gold_answers
        ),
        "event_history_case_count": sum(
            item.event_history is not None for item in dataset.gold_answers
        ),
    }


def _active_regression_questions(
    dataset: EvaluationDataset,
) -> tuple[QuestionCase, ...]:
    return tuple(
        question
        for question in dataset.questions
        if question.evaluation_mode == "regression" and question.lifecycle == "active"
    )


def execute_agent_cases(
    dataset: EvaluationDataset,
    *,
    corpus_root: Path,
    manifest_path: Path | None = None,
    answer_question_fn: AgentExecutor = answer_question,
) -> tuple[PredictionCase, ...]:
    """Execute scored cases through the public Agent entry point."""
    resolved_manifest = manifest_path or corpus_root / "manifest.jsonl"
    source_fiscal_years = _source_fiscal_years(resolved_manifest)
    predictions: list[PredictionCase] = []
    for question in _active_regression_questions(dataset):
        try:
            result = answer_question_fn(
                question.question,
                corpus_root=corpus_root,
                manifest_path=manifest_path,
            )
            used_doc_ids = {
                item.doc_id for item in (*result.document_summaries, *result.evidence)
            }
            predictions.append(
                PredictionCase.model_validate(
                    {
                        "case_id": question.case_id,
                        "source_fiscal_years": {
                            doc_id: source_fiscal_years[doc_id]
                            for doc_id in used_doc_ids
                            if doc_id in source_fiscal_years
                        },
                        **result.model_dump(mode="python"),
                    }
                )
            )
        except Exception as error:
            raise RuntimeError(
                f"{question.case_id}: live Agent execution failed: "
                f"{type(error).__name__}: {error}"
            ) from error
    return tuple(predictions)


def _source_fiscal_years(manifest_path: Path) -> dict[str, int]:
    """Map public document IDs to fiscal years for source-filing evaluation."""
    if not manifest_path.is_file():
        return {}
    years: dict[str, int] = {}
    for record in _read_jsonl(manifest_path):
        fiscal_year = record.get("base_year")
        if not isinstance(fiscal_year, int):
            continue
        doc_id = record.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id:
            receipt_no = record.get("rcept_no")
            if not isinstance(receipt_no, str) or not receipt_no:
                continue
            doc_id = f"periodic_{receipt_no}"
        years[doc_id] = fiscal_year
    return years


def _match_distinctly(candidates: list[list[int]]) -> bool:
    """Return whether each expectation's candidate indices admit a distinct assignment."""

    def assign(position: int, used: set[int]) -> bool:
        if position == len(candidates):
            return True
        return any(
            assign(position + 1, used | {index})
            for index in candidates[position]
            if index not in used
        )

    return assign(0, set())


def _fact_matches(expected: ExpectedFact, actual: Fact) -> bool:
    expected_fields = expected.model_dump(exclude_none=True)
    return all(getattr(actual, field) == value for field, value in expected_fields.items())


def _facts_match_distinctly(
    expected: tuple[ExpectedFact, ...], actual: list[Fact]
) -> bool:
    """Return whether every expectation can match a distinct actual Fact."""
    candidates = [
        [index for index, fact in enumerate(actual) if _fact_matches(item, fact)]
        for item in expected
    ]
    return _match_distinctly(candidates)


def _document_summaries_match_distinctly(
    expected: list[ExpectedDocumentSummary], prediction: PredictionCase
) -> bool:
    """Return whether every scope expectation matches a distinct summary."""
    actual = prediction.document_summaries
    candidates = [
        [
            index
            for index, summary in enumerate(actual)
            if all(
                (
                    prediction.source_fiscal_years.get(summary.doc_id) == value
                    if field == "fiscal_year"
                    else getattr(summary, field) == value
                )
                for field, value in item.model_dump(exclude_none=True).items()
            )
        ]
        for item in expected
    ]
    return _match_distinctly(candidates)


def _event_matches(expected: ExpectedEvent, actual: Event) -> bool:
    expected_fields = expected.model_dump(exclude_none=True)
    return all(getattr(actual, field) == value for field, value in expected_fields.items())


def _events_match_distinctly(expected: list[ExpectedEvent], actual: list[Event]) -> bool:
    """Return whether every expectation can match a distinct actual Event."""
    candidates = [
        [index for index, event in enumerate(actual) if _event_matches(item, event)]
        for item in expected
    ]
    return _match_distinctly(candidates)


def _evidence_matches(expected: ExpectedEvidence, actual: Evidence) -> bool:
    expected_fields = expected.model_dump(exclude_none=True)
    return all(getattr(actual, field) == value for field, value in expected_fields.items())


def _evidence_match_distinctly(
    expected: list[ExpectedEvidence], actual: list[Evidence]
) -> bool:
    """Return whether every expectation can match a distinct actual Evidence.

    Same distinct-matching contract as facts/events/resolved_event_states: each
    ExpectedEvidence must claim its own actual row, so N identical expectations
    (e.g. "3 rows from doc_id=X") require N actual rows that satisfy that identity,
    not one matching row counted three times.
    """
    candidates = [
        [index for index, item in enumerate(actual) if _evidence_matches(expectation, item)]
        for expectation in expected
    ]
    return _match_distinctly(candidates)


def _resolved_field_matches(expected: ExpectedResolvedField, actual: ResolvedFieldValue) -> bool:
    expected_fields = expected.model_dump(exclude_none=True)
    return all(getattr(actual, field) == value for field, value in expected_fields.items())


def _resolved_event_state_matches(
    expected: ExpectedResolvedEventState, actual: ResolvedEventState
) -> bool:
    expected_fields = expected.model_dump(exclude={"fields"}, exclude_none=True)
    if not all(getattr(actual, field) == value for field, value in expected_fields.items()):
        return False
    if expected.fields is None:
        return True
    return all(
        (resolved := actual.fields.get(field_name)) is not None
        and _resolved_field_matches(expected_field, resolved)
        for field_name, expected_field in expected.fields.items()
    )


def _resolved_event_states_match_distinctly(
    expected: list[ExpectedResolvedEventState], actual: list[ResolvedEventState]
) -> bool:
    """Return whether every expectation can match a distinct actual ResolvedEventState."""
    candidates = [
        [index for index, state in enumerate(actual) if _resolved_event_state_matches(item, state)]
        for item in expected
    ]
    return _match_distinctly(candidates)


def _document_summary_integrity(result: PredictionCase) -> dict[str, bool]:
    """Check summary links and filing boundaries without assuming Evidence cardinality."""
    evidence_by_id = {item.evidence_id: item for item in result.evidence}
    references_by_summary = [
        [
            evidence_id
            for item in summary.items
            for evidence_id in item.evidence_ids
        ]
        for summary in result.document_summaries
    ]
    references = [
        evidence_id
        for summary_references in references_by_summary
        for evidence_id in summary_references
    ]
    summary_doc_ids = {summary.doc_id for summary in result.document_summaries}
    referenced_evidence = [evidence_by_id.get(evidence_id) for evidence_id in references]
    return {
        "document_summary_evidence_references": bool(references_by_summary)
        and all(
            summary.items
            and summary_references
            and all(evidence_id in evidence_by_id for evidence_id in summary_references)
            for summary, summary_references in zip(
                result.document_summaries, references_by_summary, strict=True
            )
        ),
        "document_summary_same_filing": len(summary_doc_ids) == 1,
        "document_summary_evidence_same_filing": bool(result.evidence)
        and len(summary_doc_ids) == 1
        and all(evidence.doc_id in summary_doc_ids for evidence in result.evidence)
        and all(
            evidence is not None and evidence.doc_id in summary_doc_ids
            for evidence in referenced_evidence
        ),
    }


def _event_history_integrity(result: PredictionCase) -> bool:
    """Check history, change, and link provenance against each event chain."""
    evidence_by_id = {item.evidence_id: item for item in result.evidence}
    evidence_doc_ids = {item.doc_id for item in result.evidence}
    history_document_ids = [set(history.document_ids) for history in result.event_histories]

    histories_are_grounded = bool(result.event_histories) and all(
        document_ids
        and document_ids <= evidence_doc_ids
        and all(
            change.before_evidence_id is not None
            and change.after_evidence_id is not None
            and (before_evidence := evidence_by_id.get(change.before_evidence_id)) is not None
            and (after_evidence := evidence_by_id.get(change.after_evidence_id)) is not None
            and before_evidence.doc_id in document_ids
            and after_evidence.doc_id in document_ids
            for change in history.changes
        )
        for history, document_ids in zip(
            result.event_histories, history_document_ids, strict=True
        )
    )
    links_stay_on_chain = all(
        link.resolved_doc_id is not None
        and any(
            link.source_doc_id in document_ids
            and link.resolved_doc_id in document_ids
            for document_ids in history_document_ids
        )
        for link in result.event_links
    )
    return histories_are_grounded and links_stay_on_chain


def _event_history_failures(
    expected: EventHistoryExpectation, prediction: PredictionCase
) -> list[str]:
    """Compare generic structural expectations across returned histories and links."""
    failures: list[str] = []
    resolutions = {link.resolution_status for link in prediction.event_links}
    if not prediction.event_links or resolutions != {expected.link_resolution}:
        actual = sorted(item.value for item in resolutions)
        failures.append(
            "event_history.link_resolution: "
            f"expected {expected.link_resolution.value}, got {actual}"
        )

    chain_states = {history.chain_complete for history in prediction.event_histories}
    if not prediction.event_histories or chain_states != {expected.chain_complete}:
        failures.append(
            "event_history.chain_complete: "
            f"expected {expected.chain_complete}, got {sorted(chain_states)}"
        )

    for field, attribute in (
        ("document_count", "document_ids"),
        ("change_count", "changes"),
    ):
        expected_count = getattr(expected, field)
        if expected_count is None:
            continue
        actual_counts = [len(getattr(history, attribute)) for history in prediction.event_histories]
        if not actual_counts or any(count != expected_count for count in actual_counts):
            failures.append(
                f"event_history.{field}: expected {expected_count}, got {actual_counts}"
            )
    return failures


def _dangling_references(result: PredictionCase, collection: str) -> list[str]:
    evidence_ids = {item.evidence_id for item in result.evidence}
    if collection == "facts":
        items = result.facts
    elif collection == "calculations":
        items = result.calculations
    else:
        items = [
            field
            for state in result.resolved_event_states
            for field in state.fields.values()
        ]
    dangling = {
        evidence_id
        for item in items
        for evidence_id in item.evidence_ids
        if evidence_id not in evidence_ids
    }
    if any(not item.evidence_ids for item in items):
        dangling.add("<missing>")
    return sorted(dangling)


def _dangling_result_input_references(result: PredictionCase) -> list[str]:
    known_ids = {
        "fact": {item.fact_id for item in result.facts},
        "calculation": {item.calculation_id for item in result.calculations},
    }
    references = [
        reference
        for calculation in result.calculations
        for reference in calculation.inputs
    ] + [
        reference
        for comparison in result.comparisons
        for reference in (comparison.left, comparison.right)
    ]
    return sorted(
        {
            f"{reference.input_type.value}:{reference.input_id}"
            for reference in references
            if reference.input_id not in known_ids[reference.input_type.value]
        }
    )


def _calculation_value_failures(result: PredictionCase) -> list[str]:
    """Independently recompute supported calculations from their ordered inputs."""
    facts = {fact.fact_id: fact for fact in result.facts}
    recomputed: dict[str, Decimal] = {}
    failures: list[str] = []
    for calculation in result.calculations:
        if calculation.operation == "aggregate":
            if any(
                item.input_type is not ResultInputType.FACT
                for item in calculation.inputs
            ):
                failures.append(
                    f"{calculation.calculation_id}: expected only Fact inputs"
                )
                continue
            sources = [facts.get(item.input_id) for item in calculation.inputs]
            if any(source is None for source in sources):
                failures.append(f"{calculation.calculation_id}: input Fact is missing")
                continue
            resolved_sources = [source for source in sources if source is not None]
            if any(
                source.normalized_unit != calculation.unit
                for source in resolved_sources
            ):
                failures.append(
                    f"{calculation.calculation_id}: Fact input units do not match result"
                )
                continue
            try:
                expected = sum(
                    (Decimal(str(source.normalized_value)) for source in resolved_sources),
                    Decimal(0),
                )
                actual = Decimal(str(calculation.value))
            except (InvalidOperation, TypeError, ValueError):
                failures.append(f"{calculation.calculation_id}: unusable Fact values")
                continue
            if actual != expected:
                failures.append(
                    f"{calculation.calculation_id}: expected value {expected}, "
                    f"got {calculation.value}"
                )
            expected_evidence = list(
                dict.fromkeys(
                    evidence_id
                    for source in resolved_sources
                    for evidence_id in source.evidence_ids
                )
            )
            if calculation.evidence_ids != expected_evidence:
                failures.append(
                    f"{calculation.calculation_id}: expected input Evidence "
                    f"{expected_evidence}, got {calculation.evidence_ids}"
                )
            continue
        if calculation.operation not in {"delta", "delta_pct", "ratio", "diff_pp"}:
            failures.append(f"{calculation.calculation_id}: unsupported operation")
            continue
        try:
            if calculation.operation == "diff_pp":
                if len(calculation.inputs) != 2 or any(
                    item.input_type is not ResultInputType.CALCULATION
                    for item in calculation.inputs
                ):
                    failures.append(
                        f"{calculation.calculation_id}: expected two Calculation inputs"
                    )
                    continue
                old = recomputed.get(calculation.inputs[0].input_id)
                new = recomputed.get(calculation.inputs[1].input_id)
                if old is None or new is None:
                    failures.append(
                        f"{calculation.calculation_id}: input Calculation is missing or not prior"
                    )
                    continue
                expected_evidence = list(
                    dict.fromkeys(
                        evidence_id
                        for item in calculation.inputs
                        for source in result.calculations
                        if source.calculation_id == item.input_id
                        for evidence_id in source.evidence_ids
                    )
                )
                expected = new - old
            else:
                if len(calculation.inputs) != 2 or any(
                    item.input_type is not ResultInputType.FACT for item in calculation.inputs
                ):
                    failures.append(f"{calculation.calculation_id}: expected two Fact inputs")
                    continue
                first = facts.get(calculation.inputs[0].input_id)
                second = facts.get(calculation.inputs[1].input_id)
                if first is None or second is None:
                    failures.append(f"{calculation.calculation_id}: input Fact is missing")
                    continue
                first_value = Decimal(str(first.normalized_value))
                second_value = Decimal(str(second.normalized_value))
                expected_evidence = list(
                    dict.fromkeys((*first.evidence_ids, *second.evidence_ids))
                )
                if calculation.operation in {"delta", "delta_pct"}:
                    if (
                        first.fiscal_year is None
                        or second.fiscal_year is None
                        or second.fiscal_year != first.fiscal_year + 1
                    ):
                        failures.append(
                            f"{calculation.calculation_id}: Fact inputs are not previous/current"
                        )
                        continue
                    expected = second_value - first_value
                    if calculation.operation == "delta_pct":
                        expected = expected / first_value * Decimal(100)
                else:
                    expected = first_value / second_value * Decimal(100)
            with localcontext() as context:
                context.prec = 28
                expected = expected.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
            failures.append(f"{calculation.calculation_id}: unusable input values")
            continue
        expected_value = str(expected)
        recomputed[calculation.calculation_id] = expected
        if str(calculation.value) != expected_value:
            failures.append(
                f"{calculation.calculation_id}: expected value {expected_value}, "
                f"got {calculation.value}"
            )
        if calculation.evidence_ids != expected_evidence:
            failures.append(
                f"{calculation.calculation_id}: expected input Evidence "
                f"{expected_evidence}, got {calculation.evidence_ids}"
            )
    return failures


def _value(value: ExecutionStatus | AnswerabilityStatus | AnswerabilityReason | None):
    return value.value if value is not None else None


def _outcome_failures(
    outcome: AcceptableOutcome,
    prediction: PredictionCase,
) -> list[str]:
    """Compare every field in one complete top-level outcome."""
    failures: list[str] = []
    if prediction.execution_status is not outcome.execution_status:
        failures.append(
            "execution_status: "
            f"expected {outcome.execution_status.value}, "
            f"got {prediction.execution_status.value}"
        )
    if prediction.answerability_status is not outcome.status:
        failures.append(
            "answerability_status: "
            f"expected {_value(outcome.status)}, "
            f"got {_value(prediction.answerability_status)}"
        )
    if prediction.answerability_reason is not outcome.reason:
        failures.append(
            "answerability_reason: "
            f"expected {_value(outcome.reason)}, "
            f"got {_value(prediction.answerability_reason)}"
        )

    expected_issue = outcome.execution_issue
    actual_issue = prediction.execution_issue
    if expected_issue is None:
        if actual_issue is not None:
            failures.append("execution_issue: expected None, got an issue")
        return failures
    if actual_issue is None:
        failures.append("execution_issue: expected an issue, got None")
        return failures
    if actual_issue.stage != expected_issue.stage:
        failures.append(
            "execution_issue.stage: "
            f"expected {expected_issue.stage}, got {actual_issue.stage}"
        )
    if actual_issue.reason != expected_issue.reason:
        failures.append(
            "execution_issue.reason: "
            f"expected {expected_issue.reason}, got {actual_issue.reason}"
        )
    return failures


def _single_outcome_failures(
    gold: GoldAnswer,
    prediction: PredictionCase,
) -> list[str]:
    """Preserve the pre-extension comparison semantics for a single Gold outcome."""
    failures: list[str] = []
    if (
        gold.execution_status is not None
        and prediction.execution_status is not gold.execution_status
    ):
        failures.append(
            "execution_status: "
            f"expected {gold.execution_status.value}, got {prediction.execution_status.value}"
        )
    if prediction.answerability_status is not gold.status:
        failures.append(
            "answerability_status: "
            f"expected {_value(gold.status)}, "
            f"got {_value(prediction.answerability_status)}"
        )
    if prediction.answerability_reason is not gold.reason:
        failures.append(
            "answerability_reason: "
            f"expected {_value(gold.reason)}, "
            f"got {_value(prediction.answerability_reason)}"
        )
    if gold.execution_issue is not None:
        actual_issue = prediction.execution_issue
        if actual_issue is None:
            failures.append("execution_issue: expected an issue, got None")
        else:
            if actual_issue.stage != gold.execution_issue.stage:
                failures.append(
                    "execution_issue.stage: "
                    f"expected {gold.execution_issue.stage}, got {actual_issue.stage}"
                )
            if actual_issue.reason != gold.execution_issue.reason:
                failures.append(
                    "execution_issue.reason: "
                    f"expected {gold.execution_issue.reason}, got {actual_issue.reason}"
                )
    return failures


def compare_prediction(gold: GoldAnswer, prediction: PredictionCase) -> list[str]:
    """Return stable, human-readable field failures for one prediction."""
    if gold.acceptable_outcomes is None:
        failures = _single_outcome_failures(gold, prediction)
    else:
        outcome_failures = [
            _outcome_failures(outcome, prediction)
            for outcome in gold.acceptable_outcomes
        ]
        failures = []
        if all(outcome_failures):
            details = "; ".join(
                f"outcome[{index}]: {', '.join(items)}"
                for index, items in enumerate(outcome_failures)
            )
            failures.append(
                "acceptable_outcomes: prediction did not match any outcome; " + details
            )

    if gold.answerability_detail_reason is not None:
        actual = (
            prediction.answerability_detail.reason
            if prediction.answerability_detail is not None
            else None
        )
        if actual != gold.answerability_detail_reason:
            failures.append(
                "answerability_detail.reason: "
                f"expected {gold.answerability_detail_reason}, got {actual}"
            )
    if gold.answerability_detail_stage is not None:
        actual = (
            prediction.answerability_detail.stage
            if prediction.answerability_detail is not None
            else None
        )
        if actual != gold.answerability_detail_stage:
            failures.append(
                "answerability_detail.stage: "
                f"expected {gold.answerability_detail_stage}, got {actual}"
            )

    if gold.counts is not None:
        for field, expected in gold.counts.model_dump(exclude_none=True).items():
            actual = len(getattr(prediction, field))
            if actual != expected:
                failures.append(f"counts.{field}: expected {expected}, got {actual}")

    if len(prediction.evidence) < gold.min_evidence_count:
        failures.append(
            "min_evidence_count: "
            f"expected at least {gold.min_evidence_count}, got {len(prediction.evidence)}"
        )

    expected_facts = gold.expected_facts()
    if expected_facts and not _facts_match_distinctly(expected_facts, prediction.facts):
        failures.append("facts: expected fields did not match distinct predicted Facts")

    if gold.evidence is not None and not _evidence_match_distinctly(
        gold.evidence, prediction.evidence
    ):
        failures.append("evidence: expected fields did not match distinct predicted Evidence")

    if gold.document_summaries is not None and not _document_summaries_match_distinctly(
        gold.document_summaries, prediction
    ):
        failures.append(
            "document_summaries: expected fields did not match distinct predicted summaries"
        )

    if gold.events is not None and not _events_match_distinctly(gold.events, prediction.events):
        failures.append("events: expected fields did not match distinct predicted Events")

    if gold.resolved_event_states is not None and not _resolved_event_states_match_distinctly(
        gold.resolved_event_states, prediction.resolved_event_states
    ):
        failures.append(
            "resolved_event_states: expected fields did not match distinct predicted "
            "ResolvedEventStates"
        )

    if gold.calculations is not None:
        expected = [item.model_dump(exclude_none=True, mode="json") for item in gold.calculations]
        calculations_by_id = {
            item.calculation_id: item for item in prediction.calculations
        }
        actual = [
            {
                field: value
                for field, value in (
                    calculations_by_id.get(expected_item["calculation_id"], item)
                    if "calculation_id" in expected_item
                    else item
                ).model_dump(mode="json").items()
                if field in expected[index]
            }
            for index, (expected_item, item) in enumerate(
                zip(expected, prediction.calculations, strict=True)
            )
        ] if len(prediction.calculations) == len(expected) else []
        if actual != expected:
            failures.append(f"calculations: expected fields {expected}, got {actual}")

    if gold.comparisons is not None:
        expected = [item.model_dump(mode="json") for item in gold.comparisons]
        actual = [
            {
                "left": item.left.model_dump(mode="json"),
                "right": item.right.model_dump(mode="json"),
                "relation": item.relation.value,
            }
            for item in prediction.comparisons
        ]
        if actual != expected:
            failures.append(f"comparisons: expected fields {expected}, got {actual}")

    if gold.event_history is not None:
        failures.extend(_event_history_failures(gold.event_history, prediction))

    if gold.semantic_count is not None:
        source = gold.semantic_count.source
        expected_value = gold.semantic_count.value
        actual_value = len(getattr(prediction, source))
        if actual_value != expected_value:
            failures.append(
                f"semantic_count: expected {expected_value} from {source}, "
                f"got {actual_value}"
            )

    if gold.integrity is not None:
        checks = {
            "fact_evidence_references": ("facts", "Fact"),
            "calculation_evidence_references": ("calculations", "Calculation"),
            "resolved_field_evidence_references": (
                "resolved_event_states",
                "Resolved field",
            ),
        }
        for field, (collection, label) in checks.items():
            expected = getattr(gold.integrity, field)
            if expected is None:
                continue
            dangling = _dangling_references(prediction, collection)
            actual = not dangling
            if actual != expected:
                failures.append(
                    f"integrity.{field}: expected {expected}, got {actual}; "
                    f"dangling {label} evidence_ids={dangling}"
                )
        expected = gold.integrity.result_input_references
        if expected is not None:
            dangling = _dangling_result_input_references(prediction)
            actual = not dangling
            if actual != expected:
                failures.append(
                    "integrity.result_input_references: "
                    f"expected {expected}, got {actual}; dangling inputs={dangling}"
                )
        expected = gold.integrity.calculation_values_from_inputs
        if expected is not None:
            value_failures = _calculation_value_failures(prediction)
            actual = not value_failures
            if actual != expected:
                failures.append(
                    "integrity.calculation_values_from_inputs: "
                    f"expected {expected}, got {actual}; failures={value_failures}"
                )
        summary_integrity = _document_summary_integrity(prediction)
        for field, actual in summary_integrity.items():
            expected = getattr(gold.integrity, field)
            if expected is not None and actual != expected:
                failures.append(f"integrity.{field}: expected {expected}, got {actual}")
        expected = gold.integrity.event_history_evidence_references
        if expected is not None:
            actual = _event_history_integrity(prediction)
            if actual != expected:
                failures.append(
                    "integrity.event_history_evidence_references: "
                    f"expected {expected}, got {actual}"
                )
    return failures


def evaluate_predictions(
    dataset: EvaluationDataset,
    predictions: tuple[PredictionCase, ...],
) -> dict[str, Any]:
    """Score active regression cases while reporting future cases separately."""
    prediction_index = _index_unique(predictions, label="prediction")
    question_index = _index_unique(dataset.questions, label="question")
    unknown = sorted(prediction_index.keys() - question_index.keys())
    if unknown:
        raise ValueError(f"unknown prediction case_id: {unknown}")

    gold_index = _index_unique(dataset.gold_answers, label="gold")
    active = _active_regression_questions(dataset)
    future = [
        question
        for question in dataset.questions
        if question.evaluation_mode == "regression" and question.lifecycle == "future"
    ]
    missing = sorted(
        question.case_id for question in active if question.case_id not in prediction_index
    )
    if missing:
        raise ValueError(f"missing active prediction case_id: {missing}")

    failures = []
    for question in active:
        reasons = compare_prediction(
            gold_index[question.case_id], prediction_index[question.case_id]
        )
        if reasons:
            failures.append({"case_id": question.case_id, "reasons": reasons})

    return {
        "mode": "prediction_evaluation",
        "active_regression_case_count": len(active),
        "future_case_count": len(future),
        "scored_case_count": len(active),
        "passed_case_count": len(active) - len(failures),
        "failed_case_count": len(failures),
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS_PATH)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_PATH)
    execution_mode = parser.add_mutually_exclusive_group()
    execution_mode.add_argument("--predictions", type=Path)
    execution_mode.add_argument(
        "--run-agent",
        action="store_true",
        help="execute active regression cases through the real Agent",
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        help="explicit corpus directory required by --run-agent",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="optional manifest path passed to the Agent in live mode",
    )
    args = parser.parse_args()
    if args.run_agent:
        if args.corpus_root is None:
            parser.error("--corpus-root is required with --run-agent")
        args.corpus_root = args.corpus_root.expanduser()
        if not args.corpus_root.is_dir():
            parser.error(f"corpus root is not a directory: {args.corpus_root}")
        if args.manifest is not None:
            args.manifest = args.manifest.expanduser()
            if not args.manifest.is_file():
                parser.error(f"manifest is not a file: {args.manifest}")
    elif args.corpus_root is not None or args.manifest is not None:
        parser.error("--corpus-root and --manifest require --run-agent")

    dataset = load_dataset(args.questions, args.gold)
    summary: dict[str, Any]
    if args.predictions is not None:
        summary = evaluate_predictions(dataset, load_predictions(args.predictions))
    elif args.run_agent:
        predictions = execute_agent_cases(
            dataset,
            corpus_root=args.corpus_root,
            manifest_path=args.manifest,
        )
        summary = evaluate_predictions(dataset, predictions)
        summary["mode"] = "live_agent_evaluation"
        summary["real_agent_execution"] = "ENABLED"
    else:
        summary = evaluate_dataset(dataset)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if summary.get("failed_case_count", 0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
