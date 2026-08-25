"""Tests for the public deterministic evaluation runner command."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
RUNNER_PATH = REPOSITORY_ROOT / "evaluation" / "runner.py"
RUNNER_SPEC = importlib.util.spec_from_file_location("evaluation_runner", RUNNER_PATH)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = runner
RUNNER_SPEC.loader.exec_module(runner)

EvaluationDataset = runner.EvaluationDataset
GoldAnswer = runner.GoldAnswer
PredictionCase = runner.PredictionCase
QuestionCase = runner.QuestionCase
compare_prediction = runner.compare_prediction
evaluate_predictions = runner.evaluate_predictions
load_predictions = runner.load_predictions


def run_runner(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "evaluation.runner", *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def test_default_dataset_reports_deterministic_metrics() -> None:
    completed = run_runner()

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "mode": "case_validation",
        "real_agent_execution": "BLOCKED",
        "case_count": 3,
        "synthetic_case_count": 3,
        "paired_case_count": 3,
        "answerable_cases_requiring_evidence": 2,
        "unanswerable_cases_without_fact": 1,
        "event_history_case_count": 1,
    }


def test_runner_rejects_answerable_case_without_evidence(tmp_path: Path) -> None:
    questions = tmp_path / "questions.jsonl"
    gold = tmp_path / "gold.jsonl"
    questions.write_text(
        json.dumps(
            {
                "case_id": "invalid",
                "question": "[SYNTHETIC] question",
                "synthetic": True,
                "evaluation_mode": "contract",
            }
        ),
        encoding="utf-8",
    )
    gold.write_text(
        json.dumps(
            {
                "case_id": "invalid",
                "status": "ANSWERABLE",
                "fact": {
                    "company": "Synthetic Company",
                    "metric": "revenue",
                    "accounting_scope": "consolidated",
                },
                "min_evidence_count": 0,
            }
        ),
        encoding="utf-8",
    )

    completed = run_runner("--questions", str(questions), "--gold", str(gold))

    assert completed.returncode != 0
    assert "answerable cases must require evidence" in completed.stderr


def test_runner_rejects_unpaired_case_ids(tmp_path: Path) -> None:
    questions = tmp_path / "questions.jsonl"
    gold = tmp_path / "gold.jsonl"
    questions.write_text(
        json.dumps(
            {
                "case_id": "question-only",
                "question": "[SYNTHETIC] question",
                "synthetic": True,
                "evaluation_mode": "contract",
            }
        ),
        encoding="utf-8",
    )
    gold.write_text(
        json.dumps(
            {
                "case_id": "gold-only",
                "status": "UNANSWERABLE",
                "reason": "not_found",
                "min_evidence_count": 0,
            }
        ),
        encoding="utf-8",
    )

    completed = run_runner("--questions", str(questions), "--gold", str(gold))

    assert completed.returncode != 0
    assert "unpaired evaluation cases" in completed.stderr


def regression_question(case_id: str, *, lifecycle: str = "active") -> QuestionCase:
    return QuestionCase(
        case_id=case_id,
        question=f"[SYNTHETIC] {case_id}",
        synthetic=True,
        evaluation_mode="regression",
        lifecycle=lifecycle,
    )


def answerable_gold(case_id: str, **overrides) -> GoldAnswer:
    values = {
        "case_id": case_id,
        "execution_status": "COMPLETED",
        "status": "ANSWERABLE",
        "reason": None,
        "facts": [{"metric": "revenue"}],
        "min_evidence_count": 1,
    }
    values.update(overrides)
    return GoldAnswer.model_validate(values)


def prediction(case_id: str, **overrides) -> PredictionCase:
    values = {
        "case_id": case_id,
        "execution_status": "COMPLETED",
        "answerability_status": "ANSWERABLE",
        "facts": [
            {
                "fact_id": "fact-1",
                "company": "Synthetic Company",
                "metric": "revenue",
                "accounting_scope": "consolidated",
                "raw_value": "100",
                "evidence_ids": ["evidence-1"],
            }
        ],
        "evidence": [
            {
                "evidence_id": "evidence-1",
                "doc_id": "doc-1",
                "location_type": "table",
            }
        ],
    }
    values.update(overrides)
    return PredictionCase.model_validate(values)


def dataset(*pairs: tuple[QuestionCase, GoldAnswer]) -> EvaluationDataset:
    return EvaluationDataset(
        questions=tuple(item[0] for item in pairs),
        gold_answers=tuple(item[1] for item in pairs),
    )


def test_active_prediction_passes_and_future_is_excluded_from_denominator() -> None:
    active = regression_question("active")
    future = regression_question("future", lifecycle="future")
    evaluation = dataset(
        (active, answerable_gold("active")),
        (
            future,
            GoldAnswer(
                case_id="future",
                execution_status="COMPLETED",
                status="UNANSWERABLE",
                reason="out_of_scope_policy",
                min_evidence_count=0,
            ),
        ),
    )

    summary = evaluate_predictions(evaluation, (prediction("active"),))

    assert summary["scored_case_count"] == 1
    assert summary["passed_case_count"] == 1
    assert summary["failed_case_count"] == 0
    assert summary["future_case_count"] == 1


@pytest.mark.parametrize(
    ("gold_overrides", "prediction_overrides", "failure_field"),
    [
        ({"execution_status": "COMPLETED"}, {"execution_status": "BLOCKED"}, "execution_status"),
        ({}, {"answerability_status": "UNRESOLVED"}, "answerability_status"),
        (
            {
                "status": "UNANSWERABLE",
                "reason": "not_found",
                "facts": None,
                "min_evidence_count": 0,
            },
            {
                "answerability_status": "UNANSWERABLE",
                "answerability_reason": "insufficient_evidence",
            },
            "answerability_reason",
        ),
    ],
)
def test_status_and_reason_mismatches_report_field(
    gold_overrides, prediction_overrides, failure_field
) -> None:
    gold = answerable_gold("case", **gold_overrides)
    actual = prediction("case", **prediction_overrides)

    failures = compare_prediction(gold, actual)

    assert any(item.startswith(failure_field) for item in failures)


@pytest.mark.parametrize(
    ("counts", "failure_field"),
    [({"facts": 2}, "counts.facts"), ({"evidence": 2}, "counts.evidence")],
)
def test_exact_collection_count_mismatch_fails(counts, failure_field) -> None:
    failures = compare_prediction(
        answerable_gold("case", counts=counts), prediction("case")
    )

    assert any(item.startswith(failure_field) for item in failures)


def test_multiple_fact_expectations_match_distinct_facts() -> None:
    gold = answerable_gold(
        "case",
        facts=[
            {"metric": "revenue", "fiscal_year": 2024},
            {"metric": "revenue", "fiscal_year": 2025},
        ],
        counts={"facts": 2},
    )
    actual = prediction(
        "case",
        facts=[
            {
                "fact_id": "fact-2025",
                "company": "Synthetic Company",
                "metric": "revenue",
                "fiscal_year": 2025,
                "accounting_scope": "consolidated",
            },
            {
                "fact_id": "fact-2024",
                "company": "Synthetic Company",
                "metric": "revenue",
                "fiscal_year": 2024,
                "accounting_scope": "consolidated",
            },
        ],
    )

    assert compare_prediction(gold, actual) == []


def test_multiple_fact_expectations_fail_without_distinct_matches() -> None:
    gold = answerable_gold(
        "case",
        facts=[{"metric": "revenue"}, {"metric": "revenue"}],
    )

    failures = compare_prediction(gold, prediction("case"))

    assert any(item.startswith("facts:") for item in failures)


def test_dangling_fact_evidence_reference_fails() -> None:
    gold = answerable_gold(
        "case", integrity={"fact_evidence_references": True}
    )
    actual = prediction("case", evidence=[])

    failures = compare_prediction(gold, actual)

    assert any("dangling Fact evidence_ids=['evidence-1']" in item for item in failures)


def test_calculation_evidence_reference_integrity_is_supported() -> None:
    gold = answerable_gold(
        "case", integrity={"calculation_evidence_references": True}
    )
    actual = prediction(
        "case",
        calculations=[{"operation": "difference", "evidence_ids": ["missing"]}],
    )

    failures = compare_prediction(gold, actual)

    assert any("dangling Calculation evidence_ids=['missing']" in item for item in failures)


def test_duplicate_prediction_case_id_fails(tmp_path: Path) -> None:
    path = tmp_path / "predictions.jsonl"
    record = prediction("duplicate").model_dump(mode="json")
    path.write_text(
        "\n".join(json.dumps(record) for _ in range(2)), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="duplicate prediction case_id: duplicate"):
        load_predictions(path)


def test_missing_active_prediction_fails(tmp_path: Path) -> None:
    evaluation = dataset(
        (regression_question("active"), answerable_gold("active"))
    )
    path = tmp_path / "empty-predictions.jsonl"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="missing active prediction case_id"):
        evaluate_predictions(evaluation, load_predictions(path))


@pytest.mark.parametrize(
    "reason",
    ["out_of_data_range", "out_of_scope_data", "out_of_scope_policy"],
)
def test_pattern_f_canonical_reasons_validate(reason: str) -> None:
    gold = GoldAnswer(
        case_id="pattern-f",
        execution_status="COMPLETED",
        status="UNANSWERABLE",
        reason=reason,
        min_evidence_count=0,
        counts={"facts": 0, "evidence": 0},
    )

    assert gold.reason.value == reason


def test_f4_forward_looking_prediction_detail_is_compared() -> None:
    gold = GoldAnswer(
        case_id="f4",
        execution_status="COMPLETED",
        status="UNANSWERABLE",
        reason="out_of_scope_policy",
        min_evidence_count=0,
        answerability_detail_reason="forward_looking_prediction",
    )
    actual = prediction(
        "f4",
        answerability_status="UNANSWERABLE",
        answerability_reason="out_of_scope_policy",
        answerability_detail={"reason": "forward_looking_prediction"},
        facts=[],
        evidence=[],
    )

    assert compare_prediction(gold, actual) == []


def test_forward_looking_detail_alias_is_rejected() -> None:
    with pytest.raises(ValueError, match="forward_looking_prediction"):
        GoldAnswer(
            case_id="f4",
            status="UNANSWERABLE",
            reason="out_of_scope_policy",
            min_evidence_count=0,
            answerability_detail_reason="forward_looking",
        )
