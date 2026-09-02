"""Validate evaluation datasets and deterministically compare Agent predictions."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from disclosure_agent.agent.router import answer_question
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    EventLinkResolutionStatus,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import AgentExecutionResult, Fact

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


class EventHistoryExpectation(BaseModel):
    """Legacy gold link and chain requirements for contract cases."""

    link_resolution: EventLinkResolutionStatus
    chain_complete: bool


class CollectionCounts(BaseModel):
    """Optional exact collection sizes."""

    facts: int | None = Field(default=None, ge=0)
    evidence: int | None = Field(default=None, ge=0)
    events: int | None = Field(default=None, ge=0)
    event_links: int | None = Field(default=None, ge=0)
    event_histories: int | None = Field(default=None, ge=0)
    resolved_event_states: int | None = Field(default=None, ge=0)
    document_summaries: int | None = Field(default=None, ge=0)
    calculations: int | None = Field(default=None, ge=0)
    comparisons: int | None = Field(default=None, ge=0)
    narrative_comparisons: int | None = Field(default=None, ge=0)


class IntegrityExpectation(BaseModel):
    """Expected reference-integrity outcomes."""

    fact_evidence_references: bool | None = None
    calculation_evidence_references: bool | None = None
    resolved_field_evidence_references: bool | None = None
    result_input_references: bool | None = None
    calculation_values_from_inputs: bool | None = None


class ExpectedCalculation(BaseModel):
    """Stable semantic fields for one deterministic calculation."""

    operation: str
    unit: str | None = None


class GoldAnswer(BaseModel):
    """Deterministic expectations independent of Pattern-internal result shapes."""

    case_id: str = Field(min_length=1)
    execution_status: ExecutionStatus | None = None
    status: AnswerabilityStatus
    reason: AnswerabilityReason | None = None
    fact: ExpectedFact | None = None
    facts: list[ExpectedFact] | None = None
    calculations: list[ExpectedCalculation] | None = None
    min_evidence_count: int = Field(ge=0)
    event_history: EventHistoryExpectation | None = None
    counts: CollectionCounts | None = None
    answerability_detail_reason: str | None = None
    integrity: IntegrityExpectation | None = None

    @model_validator(mode="after")
    def validate_answerability(self) -> GoldAnswer:
        answerable = {
            AnswerabilityStatus.ANSWERABLE,
            AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        }
        if self.status is AnswerabilityStatus.ANSWERABLE and self.reason is not None:
            raise ValueError("ANSWERABLE cases must not define a failure reason")
        if self.status not in answerable and self.reason is None:
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

    has_semantic_expectation = bool(gold.expected_facts()) or gold.event_history is not None
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
        raise ValueError("ANSWERABLE cases need a fact or event-history expectation")


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
    predictions: list[PredictionCase] = []
    for question in _active_regression_questions(dataset):
        try:
            result = answer_question_fn(
                question.question,
                corpus_root=corpus_root,
                manifest_path=manifest_path,
            )
            predictions.append(
                PredictionCase.model_validate(
                    {
                        "case_id": question.case_id,
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

    def assign(position: int, used: set[int]) -> bool:
        if position == len(candidates):
            return True
        return any(
            assign(position + 1, used | {index})
            for index in candidates[position]
            if index not in used
        )

    return assign(0, set())


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
    """Independently recompute supported calculations from their ordered Fact inputs."""
    facts = {fact.fact_id: fact for fact in result.facts}
    failures: list[str] = []
    for calculation in result.calculations:
        if calculation.operation not in {"delta", "delta_pct"}:
            failures.append(f"{calculation.calculation_id}: unsupported operation")
            continue
        if len(calculation.inputs) != 2 or any(
            item.input_type is not ResultInputType.FACT for item in calculation.inputs
        ):
            failures.append(f"{calculation.calculation_id}: expected two Fact inputs")
            continue
        previous = facts.get(calculation.inputs[0].input_id)
        current = facts.get(calculation.inputs[1].input_id)
        if previous is None or current is None:
            failures.append(f"{calculation.calculation_id}: input Fact is missing")
            continue
        if (
            previous.fiscal_year is None
            or current.fiscal_year is None
            or current.fiscal_year != previous.fiscal_year + 1
        ):
            failures.append(f"{calculation.calculation_id}: Fact inputs are not previous/current")
            continue
        try:
            previous_value = Decimal(str(previous.normalized_value))
            current_value = Decimal(str(current.normalized_value))
            with localcontext() as context:
                context.prec = 28
                expected = current_value - previous_value
                if calculation.operation == "delta_pct":
                    expected = expected / previous_value * Decimal(100)
                expected_value = str(expected.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
        except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
            failures.append(f"{calculation.calculation_id}: unusable Fact values")
            continue
        if str(calculation.value) != expected_value:
            failures.append(
                f"{calculation.calculation_id}: expected value {expected_value}, "
                f"got {calculation.value}"
            )
        expected_evidence = list(
            dict.fromkeys((*previous.evidence_ids, *current.evidence_ids))
        )
        if calculation.evidence_ids != expected_evidence:
            failures.append(
                f"{calculation.calculation_id}: expected input Evidence "
                f"{expected_evidence}, got {calculation.evidence_ids}"
            )
    return failures


def compare_prediction(gold: GoldAnswer, prediction: PredictionCase) -> list[str]:
    """Return stable, human-readable field failures for one prediction."""
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
        actual = (
            prediction.answerability_status.value
            if prediction.answerability_status is not None
            else None
        )
        failures.append(
            f"answerability_status: expected {gold.status.value}, got {actual}"
        )
    if prediction.answerability_reason is not gold.reason:
        actual = (
            prediction.answerability_reason.value
            if prediction.answerability_reason is not None
            else None
        )
        expected = gold.reason.value if gold.reason is not None else None
        failures.append(f"answerability_reason: expected {expected}, got {actual}")
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

    if gold.calculations is not None:
        expected = [(item.operation, item.unit) for item in gold.calculations]
        actual = [(item.operation, item.unit) for item in prediction.calculations]
        if actual != expected:
            failures.append(f"calculations: expected operation/unit {expected}, got {actual}")

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
