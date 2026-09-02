"""Tests for the public deterministic evaluation runner command."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ExecutionStatus,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
RUNNER_PATH = REPOSITORY_ROOT / "evaluation" / "runner.py"
RUNNER_SPEC = importlib.util.spec_from_file_location("evaluation_runner", RUNNER_PATH)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = runner
RUNNER_SPEC.loader.exec_module(runner)

EvaluationDataset = runner.EvaluationDataset
GoldAnswer = runner.GoldAnswer
AgentExecutionResult = runner.AgentExecutionResult
PredictionCase = runner.PredictionCase
QuestionCase = runner.QuestionCase
compare_prediction = runner.compare_prediction
evaluate_predictions = runner.evaluate_predictions
execute_agent_cases = runner.execute_agent_cases
load_dataset = runner.load_dataset
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
        "case_count": 21,
        "synthetic_case_count": 3,
        "paired_case_count": 21,
        "answerable_cases_requiring_evidence": 16,
        "unanswerable_cases_without_fact": 4,
        "event_history_case_count": 3,
    }


def test_data_root_does_not_enable_live_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))

    completed = run_runner()

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["mode"] == "case_validation"


def test_live_mode_requires_explicit_corpus_root() -> None:
    completed = run_runner("--run-agent")

    assert completed.returncode != 0
    assert "--corpus-root is required with --run-agent" in completed.stderr


def test_corpus_root_without_live_mode_is_rejected(tmp_path: Path) -> None:
    completed = run_runner("--corpus-root", str(tmp_path))

    assert completed.returncode != 0
    assert "--corpus-root and --manifest require --run-agent" in completed.stderr


def test_live_mode_rejects_invalid_corpus_root(tmp_path: Path) -> None:
    missing = tmp_path / "missing-corpus"

    completed = run_runner("--run-agent", "--corpus-root", str(missing))

    assert completed.returncode != 0
    assert f"corpus root is not a directory: {missing}" in completed.stderr


def test_live_mode_rejects_invalid_explicit_manifest(tmp_path: Path) -> None:
    missing = tmp_path / "missing-manifest.jsonl"

    completed = run_runner(
        "--run-agent",
        "--corpus-root",
        str(tmp_path),
        "--manifest",
        str(missing),
    )

    assert completed.returncode != 0
    assert f"manifest is not a file: {missing}" in completed.stderr


def test_default_dataset_registers_q01_through_q18_once() -> None:
    loaded = load_dataset()
    representative = [
        question
        for question in loaded.questions
        if question.representative_question_id is not None
    ]

    assert [question.representative_question_id for question in representative] == [
        f"Q{number:02d}" for number in range(1, 19)
    ]
    assert len({question.representative_question_id for question in representative}) == 18
    assert {question.case_id for question in loaded.questions} == {
        gold.case_id for gold in loaded.gold_answers
    }
    assert {question.case_id for question in representative if question.lifecycle == "active"} == {
        "q01",
        "q02",
        "q13",
        "q18",
    }
    assert sum(question.lifecycle == "future" for question in representative) == 14


def test_default_dataset_preserves_synthetic_contract_cases() -> None:
    loaded = load_dataset()
    synthetic = [question for question in loaded.questions if question.synthetic]

    assert [question.case_id for question in synthetic] == [
        "synthetic_q01_contract",
        "synthetic_unanswerable_contract",
        "synthetic_event_history_contract",
    ]
    assert all(question.evaluation_mode == "contract" for question in synthetic)


def test_future_representative_gold_has_no_source_identifiers_or_corpus_values() -> None:
    questions = {item.case_id: item for item in load_dataset().questions}
    records = [
        json.loads(line)
        for line in runner.DEFAULT_GOLD_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    representative = [
        record
        for record in records
        if record["case_id"].startswith("q")
        and questions[record["case_id"]].lifecycle == "future"
    ]
    serialized = json.dumps(representative, ensure_ascii=False)

    assert '"receipt_no"' not in serialized
    assert '"doc_id"' not in serialized
    assert '"raw_value"' not in serialized
    assert '"normalized_value"' not in serialized
    assert all(fact for record in representative for fact in record.get("facts", []))


def test_active_q01_gold_pins_corpus_verified_fact() -> None:
    gold_by_id = {gold.case_id: gold for gold in load_dataset().gold_answers}
    q01 = gold_by_id["q01"]

    assert q01.counts.model_dump(exclude_none=True) == {"facts": 1, "evidence": 1}
    assert [fact.model_dump(exclude_none=True, mode="json") for fact in q01.expected_facts()] == [
        {
            "company": "한미반도체(주)",
            "metric": "revenue",
            "fiscal_year": 2025,
            "accounting_scope": "consolidated",
            "raw_unit": "원",
            "normalized_value": "576684619527",
            "normalized_unit": "원",
        }
    ]
    assert q01.min_evidence_count == 1
    assert q01.integrity.fact_evidence_references is True


def test_active_q02_gold_pins_actual_contract_facts_and_evidence() -> None:
    loaded = load_dataset()
    questions = {question.case_id: question for question in loaded.questions}
    q02 = {gold.case_id: gold for gold in loaded.gold_answers}["q02"]

    assert questions["q02"].question == (
        "한화오션이 2025년 9월 Yang Ming과 체결한 계약의 계약금액과 계약기간은?"
    )
    assert q02.execution_status is ExecutionStatus.COMPLETED
    assert q02.status is AnswerabilityStatus.ANSWERABLE
    assert q02.reason is None
    assert q02.counts.model_dump(exclude_none=True) == {"facts": 3, "evidence": 3}
    actual_facts = [
        fact.model_dump(exclude_none=True, mode="json")
        for fact in q02.expected_facts()
    ]
    assert actual_facts == [
        {
            "metric": "contract_amount",
            "normalized_value": "1933600000000",
            "normalized_unit": "KRW",
            "evidence_ids": [
                "evidence_exchange_20250917800008_contract_amount"
            ],
        },
        {
            "metric": "contract_start_date",
            "normalized_value": "2025-09-16",
            "evidence_ids": [
                "evidence_exchange_20250917800008_contract_start_date"
            ],
        },
        {
            "metric": "contract_end_date",
            "normalized_value": "2029-05-31",
            "evidence_ids": [
                "evidence_exchange_20250917800008_contract_end_date"
            ],
        },
    ]
    assert q02.min_evidence_count == 3
    assert q02.integrity.fact_evidence_references is True


def test_active_q13_gold_pins_corpus_verified_chain_structure() -> None:
    q13 = {gold.case_id: gold for gold in load_dataset().gold_answers}["q13"]

    assert q13.counts.model_dump(exclude_none=True) == {
        "event_links": 2,
        "event_histories": 1,
    }
    assert q13.event_history.model_dump(mode="json") == {
        "link_resolution": "resolved",
        "chain_complete": True,
    }
    # The actual chain has three documents and document-level Evidence for each.
    # Do not freeze the producer's current exact Evidence count (9).
    assert q13.min_evidence_count == 3


def test_active_q18_gold_pins_actual_safety_gate_contract() -> None:
    q18 = {gold.case_id: gold for gold in load_dataset().gold_answers}["q18"]

    assert q18.execution_status.value == "COMPLETED"
    assert q18.status.value == "UNANSWERABLE"
    assert q18.reason.value == "out_of_scope_policy"
    assert q18.answerability_detail_reason == "investment_advice"
    assert q18.counts.model_dump(exclude_none=True) == {"facts": 0, "evidence": 0}
    assert q18.min_evidence_count == 0


def test_future_representative_gold_is_not_empty() -> None:
    loaded = load_dataset()
    gold_by_id = {gold.case_id: gold for gold in loaded.gold_answers}

    for question in loaded.questions:
        if question.representative_question_id is None or question.lifecycle != "future":
            continue
        gold = gold_by_id[question.case_id]
        has_counts = gold.counts is not None and bool(
            gold.counts.model_dump(exclude_none=True)
        )
        assert (
            gold.expected_facts()
            or gold.event_history
            or has_counts
            or gold.min_evidence_count > 0
        )


def test_representative_calculation_counts_match_final_targets() -> None:
    gold_by_id = {gold.case_id: gold for gold in load_dataset().gold_answers}

    # Numeric-only policy: comparisons are not Calculations. Q08 has two margins
    # and their percentage-point change; Q11/Q12 have two numeric derivations.
    assert {
        case_id: gold_by_id[case_id].counts.calculations
        for case_id in ("q08", "q10", "q11", "q12")
    } == {"q08": 3, "q10": 0, "q11": 2, "q12": 2}

    assert gold_by_id["q10"].integrity.calculation_evidence_references is None


def test_narrative_future_gold_does_not_require_zero_facts() -> None:
    gold_by_id = {gold.case_id: gold for gold in load_dataset().gold_answers}

    for case_id in ("q04", "q05", "q06", "q09"):
        counts = gold_by_id[case_id].counts
        assert counts is None or counts.facts is None

    for case_id in ("q16", "q17", "q18"):
        counts = gold_by_id[case_id].counts
        assert counts.facts == 0
        assert counts.evidence == 0


def test_event_history_gold_records_only_supported_structural_invariants() -> None:
    gold_by_id = {gold.case_id: gold for gold in load_dataset().gold_answers}

    # Remaining comparator gap: Q15 change cardinality cannot be asserted until
    # corpus-verified cardinality is available. Do not substitute guessed values
    # or link counts.
    assert gold_by_id["q15"].counts.event_histories == 1
    assert gold_by_id["q15"].counts.event_links is None


def test_q14_gold_requires_ambiguity_instead_of_one_chosen_history() -> None:
    """
    Q14 질문은 루트 계약을 구분할 조건을 담고 있지 않다(open_decisions.md D8).

    같은 기업·같은 제출일·같은 계약상대방으로 원공시가 2건 이상이면 어느 계약의 이력인지
    질문만으로 정해지지 않는다. 후속 정정·해지 체인이 한쪽에만 있다는 이유로 루트를 역으로
    고르면, 다른 계약의 이력을 그 계약의 이력이라고 말하게 된다.

    그래서 gold는 EventHistory 1건을 요구하지 않는다. 요구하면 "하나를 골라라"가 정답이
    되어 위 판단이 뒤집힌다.
    """
    gold = {gold.case_id: gold for gold in load_dataset().gold_answers}["q14"]

    assert gold.execution_status is ExecutionStatus.COMPLETED
    assert gold.status is AnswerabilityStatus.UNRESOLVED
    assert gold.reason is AnswerabilityReason.AMBIGUOUS_MATCH
    assert gold.counts.event_histories == 0
    assert gold.event_history is None


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


def test_execute_agent_cases_preserves_result_and_only_runs_active_regressions(
    tmp_path: Path,
) -> None:
    active = regression_question("active")
    future = regression_question("future", lifecycle="future")
    contract = QuestionCase(
        case_id="contract",
        question="[SYNTHETIC] contract",
        synthetic=True,
        evaluation_mode="contract",
    )
    evaluation = dataset(
        (active, answerable_gold("active")),
        (
            future,
            GoldAnswer(
                case_id="future",
                status="UNANSWERABLE",
                reason="not_found",
                min_evidence_count=0,
            ),
        ),
        (
            contract,
            GoldAnswer(
                case_id="contract",
                status="UNANSWERABLE",
                reason="not_found",
                min_evidence_count=0,
            ),
        ),
    )
    manifest = tmp_path / "custom-manifest.jsonl"
    calls: list[tuple[str, Path, Path | None]] = []

    def fake_answer_question(
        question: str, *, corpus_root: Path, manifest_path: Path | None
    ) -> AgentExecutionResult:
        calls.append((question, corpus_root, manifest_path))
        return AgentExecutionResult.model_validate(
            {
                "execution_status": "COMPLETED",
                "answerability_status": "ANSWERABLE",
                "facts": [
                    {
                        "fact_id": "synthetic-fact",
                        "company": "Synthetic Company",
                        "metric": "revenue",
                        "accounting_scope": "consolidated",
                        "evidence_ids": ["synthetic-evidence"],
                    }
                ],
                "evidence": [
                    {
                        "evidence_id": "synthetic-evidence",
                        "doc_id": "synthetic-document",
                        "location_type": "table",
                    }
                ],
            }
        )

    predictions = execute_agent_cases(
        evaluation,
        corpus_root=tmp_path,
        manifest_path=manifest,
        answer_question_fn=fake_answer_question,
    )

    assert calls == [(active.question, tmp_path, manifest)]
    assert len(predictions) == 1
    assert predictions[0].case_id == "active"
    assert predictions[0].execution_status.value == "COMPLETED"
    assert predictions[0].answerability_status.value == "ANSWERABLE"
    assert len(predictions[0].facts) == 1
    assert len(predictions[0].evidence) == 1


def test_live_agent_mismatch_reports_the_failing_case(tmp_path: Path) -> None:
    evaluation = dataset(
        (regression_question("live-case"), answerable_gold("live-case"))
    )

    def blocked_agent(_question: str, **_: object) -> AgentExecutionResult:
        return AgentExecutionResult.model_validate(
            {
                "execution_status": "BLOCKED",
                "answerability_status": "UNRESOLVED",
                "answerability_reason": "insufficient_evidence",
            }
        )

    predictions = execute_agent_cases(
        evaluation,
        corpus_root=tmp_path,
        answer_question_fn=blocked_agent,
    )
    summary = evaluate_predictions(evaluation, predictions)

    assert summary["failed_case_count"] == 1
    assert summary["failures"][0]["case_id"] == "live-case"
    assert any(
        reason.startswith("execution_status:")
        for reason in summary["failures"][0]["reasons"]
    )


def test_live_agent_exception_identifies_the_case(tmp_path: Path) -> None:
    evaluation = dataset(
        (regression_question("exploding-case"), answerable_gold("exploding-case"))
    )

    def exploding_agent(_question: str, **_: object) -> AgentExecutionResult:
        raise ValueError("synthetic failure")

    with pytest.raises(
        RuntimeError,
        match="exploding-case: live Agent execution failed: ValueError: synthetic failure",
    ):
        execute_agent_cases(
            evaluation,
            corpus_root=tmp_path,
            answer_question_fn=exploding_agent,
        )


def load_single_case(
    tmp_path: Path,
    *,
    lifecycle: str,
    gold: dict,
) -> EvaluationDataset:
    questions_path = tmp_path / "questions.jsonl"
    gold_path = tmp_path / "gold.jsonl"
    questions_path.write_text(
        json.dumps(
            {
                "case_id": "case",
                "question": "[SYNTHETIC] lifecycle validation",
                "synthetic": True,
                "evaluation_mode": "regression",
                "lifecycle": lifecycle,
            }
        ),
        encoding="utf-8",
    )
    gold_path.write_text(
        json.dumps({"case_id": "case", "status": "ANSWERABLE", **gold}),
        encoding="utf-8",
    )
    return load_dataset(questions_path, gold_path)


def test_active_answerable_without_semantic_expectation_fails(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError, match="ANSWERABLE cases need a fact or event-history expectation"
    ):
        load_single_case(
            tmp_path,
            lifecycle="active",
            gold={"min_evidence_count": 1},
        )


def test_future_answerable_with_counts_only_passes(tmp_path: Path) -> None:
    loaded = load_single_case(
        tmp_path,
        lifecycle="future",
        gold={"min_evidence_count": 0, "counts": {"events": 1}},
    )

    assert loaded.gold_answers[0].counts.events == 1


def test_future_answerable_with_minimum_evidence_only_passes(tmp_path: Path) -> None:
    loaded = load_single_case(
        tmp_path,
        lifecycle="future",
        gold={"min_evidence_count": 1},
    )

    assert loaded.gold_answers[0].min_evidence_count == 1


def test_future_answerable_without_any_expectation_fails(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match="future ANSWERABLE cases need a semantic or structural expectation",
    ):
        load_single_case(
            tmp_path,
            lifecycle="future",
            gold={"min_evidence_count": 0},
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



def test_comparisons_exact_count_match_passes() -> None:
    actual = prediction(
        "case",
        comparisons=[
            {
                "comparison_id": "comparison-1",
                "left": {"input_type": "fact", "input_id": "fact-1"},
                "right": {"input_type": "fact", "input_id": "fact-2"},
                "relation": "left_greater",
            }
        ],
    )

    assert compare_prediction(
        answerable_gold("case", counts={"comparisons": 1}), actual
    ) == []


def test_comparisons_exact_count_mismatch_fails() -> None:
    failures = compare_prediction(
        answerable_gold("case", counts={"comparisons": 1}), prediction("case")
    )

    assert "counts.comparisons: expected 1, got 0" in failures


def test_unspecified_comparisons_count_preserves_existing_behavior() -> None:
    actual = prediction(
        "case",
        comparisons=[
            {
                "comparison_id": "comparison-1",
                "left": {"input_type": "fact", "input_id": "fact-1"},
                "right": {"input_type": "fact", "input_id": "fact-2"},
                "relation": "equal",
            }
        ],
    )

    assert compare_prediction(answerable_gold("case"), actual) == []


def resolved_event_state(*, evidence_ids: list[str]) -> dict:
    return {
        "event_id": "event-chain-1",
        "event_type": "supply_contract",
        "company": "Synthetic Company",
        "as_of": "2025-03-14",
        "fields": {
            "contract_amount": {
                "value": 100.0,
                "unit": "KRW",
                "source_doc_id": "doc-1",
                "evidence_ids": evidence_ids,
            }
        },
    }


def test_resolved_event_states_exact_count_match_passes() -> None:
    actual = prediction(
        "case",
        resolved_event_states=[resolved_event_state(evidence_ids=["evidence-1"])],
    )

    assert compare_prediction(
        answerable_gold("case", counts={"resolved_event_states": 1}), actual
    ) == []


def test_resolved_event_states_exact_count_mismatch_fails() -> None:
    failures = compare_prediction(
        answerable_gold("case", counts={"resolved_event_states": 1}),
        prediction("case"),
    )

    assert "counts.resolved_event_states: expected 1, got 0" in failures


def test_omitted_resolved_event_states_count_is_ignored() -> None:
    actual = prediction(
        "case",
        resolved_event_states=[resolved_event_state(evidence_ids=["evidence-1"])],
    )

    assert compare_prediction(answerable_gold("case"), actual) == []


def test_existing_exact_collection_counts_still_pass() -> None:
    actual = prediction(
        "case",
        calculations=[
            {
                "calculation_id": "calculation-1",
                "operation": "difference",
                "inputs": [{"input_type": "fact", "input_id": "fact-1"}],
            }
        ],
    )

    assert compare_prediction(
        answerable_gold(
            "case",
            counts={"facts": 1, "evidence": 1, "calculations": 1},
        ),
        actual,
    ) == []


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


def test_expected_fact_evidence_ids_require_an_exact_non_dangling_match() -> None:
    gold = answerable_gold(
        "case",
        facts=[
            {
                "metric": "contract_amount",
                "normalized_value": "100",
                "normalized_unit": "KRW",
                "evidence_ids": ["evidence-expected"],
            }
        ],
        integrity={"fact_evidence_references": True},
    )

    def contract_prediction(evidence_id: str) -> PredictionCase:
        return prediction(
            "case",
            facts=[
                {
                    "fact_id": "fact-1",
                    "company": "Synthetic Company",
                    "metric": "contract_amount",
                    "accounting_scope": "not_applicable",
                    "normalized_value": "100",
                    "normalized_unit": "KRW",
                    "evidence_ids": [evidence_id],
                }
            ],
            evidence=[
                {
                    "evidence_id": evidence_id,
                    "doc_id": "doc-1",
                    "location_type": "table",
                }
            ],
        )

    assert compare_prediction(gold, contract_prediction("evidence-expected")) == []

    failures = compare_prediction(gold, contract_prediction("evidence-wrong"))

    assert failures == ["facts: expected fields did not match distinct predicted Facts"]


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
        calculations=[
            {
                "calculation_id": "calculation-1",
                "operation": "identity",
                "inputs": [{"input_type": "fact", "input_id": "fact-1"}],
                "evidence_ids": ["missing"],
            }
        ],
    )

    failures = compare_prediction(gold, actual)

    assert any("dangling Calculation evidence_ids=['missing']" in item for item in failures)


def test_resolved_field_evidence_reference_integrity_passes() -> None:
    gold = answerable_gold(
        "case", integrity={"resolved_field_evidence_references": True}
    )
    actual = prediction(
        "case",
        resolved_event_states=[resolved_event_state(evidence_ids=["evidence-1"])],
    )

    assert compare_prediction(gold, actual) == []


def test_dangling_resolved_field_evidence_reference_fails() -> None:
    gold = answerable_gold(
        "case", integrity={"resolved_field_evidence_references": True}
    )
    actual = prediction(
        "case",
        resolved_event_states=[resolved_event_state(evidence_ids=["missing"])],
    )

    failures = compare_prediction(gold, actual)

    assert any("dangling Resolved field evidence_ids=['missing']" in item for item in failures)


@pytest.mark.parametrize(
    ("input_type", "input_id"),
    (("fact", "missing-fact"), ("calculation", "missing-calculation")),
)
def test_dangling_calculation_input_reference_fails(
    input_type: str, input_id: str
) -> None:
    gold = answerable_gold("case", integrity={"result_input_references": True})
    actual = prediction(
        "case",
        calculations=[
            {
                "calculation_id": "calculation-1",
                "operation": "identity",
                "inputs": [{"input_type": input_type, "input_id": input_id}],
            }
        ],
    )

    failures = compare_prediction(gold, actual)

    assert any(f"{input_type}:{input_id}" in item for item in failures)


@pytest.mark.parametrize(
    ("input_type", "input_id"),
    (("fact", "missing-fact"), ("calculation", "missing-calculation")),
)
def test_dangling_comparison_input_reference_fails(
    input_type: str, input_id: str
) -> None:
    gold = answerable_gold("case", integrity={"result_input_references": True})
    actual = prediction(
        "case",
        comparisons=[
            {
                "comparison_id": "comparison-1",
                "left": {"input_type": "fact", "input_id": "fact-1"},
                "right": {"input_type": input_type, "input_id": input_id},
                "relation": "left_greater",
            }
        ],
    )

    failures = compare_prediction(gold, actual)

    assert any(f"{input_type}:{input_id}" in item for item in failures)


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
