"""Validate evaluation datasets and deterministically compare Agent predictions."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    EventLinkResolutionStatus,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
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
    calculations: int | None = Field(default=None, ge=0)


class IntegrityExpectation(BaseModel):
    """Expected reference-integrity outcomes."""

    fact_evidence_references: bool | None = None
    calculation_evidence_references: bool | None = None


class GoldAnswer(BaseModel):
    """Deterministic expectations independent of Pattern-internal result shapes."""

    case_id: str = Field(min_length=1)
    execution_status: ExecutionStatus | None = None
    status: AnswerabilityStatus
    reason: AnswerabilityReason | None = None
    fact: ExpectedFact | None = None
    facts: list[ExpectedFact] | None = None
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
        if self.status in answerable and self.min_evidence_count < 1:
            raise ValueError("answerable cases must require evidence")
        if self.status is AnswerabilityStatus.ANSWERABLE and self.reason is not None:
            raise ValueError("ANSWERABLE cases must not define a failure reason")
        if self.status not in answerable and self.reason is None:
            raise ValueError("non-answerable cases must define a reason")
        if self.status is AnswerabilityStatus.UNANSWERABLE and (
            self.fact is not None or self.facts
        ):
            raise ValueError("UNANSWERABLE cases must not contain a supported fact")
        if (
            self.status is AnswerabilityStatus.ANSWERABLE
            and self.fact is None
            and not self.facts
            and self.event_history is None
        ):
            raise ValueError("ANSWERABLE cases need a fact or event-history expectation")
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
    items = result.facts if collection == "facts" else result.calculations
    return sorted(
        {
            evidence_id
            for item in items
            for evidence_id in item.evidence_ids
            if evidence_id not in evidence_ids
        }
    )


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

    if gold.integrity is not None:
        checks = {
            "fact_evidence_references": ("facts", "Fact"),
            "calculation_evidence_references": ("calculations", "Calculation"),
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
    active = [
        question
        for question in dataset.questions
        if question.evaluation_mode == "regression" and question.lifecycle == "active"
    ]
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
    parser.add_argument("--predictions", type=Path)
    args = parser.parse_args()
    dataset = load_dataset(args.questions, args.gold)
    summary: dict[str, Any]
    if args.predictions is None:
        summary = evaluate_dataset(dataset)
    else:
        summary = evaluate_predictions(dataset, load_predictions(args.predictions))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if summary.get("failed_case_count", 0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
