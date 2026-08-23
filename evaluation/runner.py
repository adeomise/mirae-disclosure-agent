"""Validate synthetic evaluation cases without interpreting Agent internals."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    EventLinkResolutionStatus,
    FactAccountingScope,
    MetricConcept,
)

EVALUATION_DIR = Path(__file__).parent
DEFAULT_QUESTIONS_PATH = EVALUATION_DIR / "questions.jsonl"
DEFAULT_GOLD_PATH = EVALUATION_DIR / "gold_answers.jsonl"


class QuestionCase(BaseModel):
    """One explicitly synthetic question used for contract evaluation."""

    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    synthetic: Literal[True]
    evaluation_mode: Literal["contract"]


class ExpectedFact(BaseModel):
    """Gold fields for a synthetic fact result."""

    company: str = Field(min_length=1)
    metric: MetricConcept
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    accounting_scope: FactAccountingScope
    raw_value: str | None = None
    raw_unit: str | None = None


class EventHistoryExpectation(BaseModel):
    """Gold link and chain requirements for an event-history case."""

    link_resolution: EventLinkResolutionStatus
    chain_complete: bool


class GoldAnswer(BaseModel):
    """Deterministic expectations independent of Pattern result shapes."""

    case_id: str = Field(min_length=1)
    status: AnswerabilityStatus
    reason: AnswerabilityReason | None = None
    fact: ExpectedFact | None = None
    min_evidence_count: int = Field(ge=0)
    event_history: EventHistoryExpectation | None = None

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
        if self.status is AnswerabilityStatus.UNANSWERABLE and self.fact is not None:
            raise ValueError("UNANSWERABLE cases must not contain a supported fact")
        if (
            self.status is AnswerabilityStatus.ANSWERABLE
            and self.fact is None
            and self.event_history is None
        ):
            raise ValueError("ANSWERABLE cases need a fact or event-history expectation")
        return self


@dataclass(frozen=True)
class EvaluationDataset:
    """Paired question and gold-answer records."""

    questions: tuple[QuestionCase, ...]
    gold_answers: tuple[GoldAnswer, ...]


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from error
    if not records:
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
    """Load and pair validated synthetic question and gold records."""
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


def evaluate_dataset(dataset: EvaluationDataset) -> dict[str, int | str]:
    """Return deterministic case-contract metrics."""
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
            item.status is AnswerabilityStatus.UNANSWERABLE and item.fact is None
            for item in dataset.gold_answers
        ),
        "event_history_case_count": sum(
            item.event_history is not None for item in dataset.gold_answers
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS_PATH)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_PATH)
    args = parser.parse_args()
    summary = evaluate_dataset(load_dataset(args.questions, args.gold))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
