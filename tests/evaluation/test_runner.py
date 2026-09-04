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
        # Q09 is UNRESOLVED / ambiguous_match against the actual corpus: FY2025 has an
        # original and a correction filing and no periodic-report version policy (D5)
        # exists, so it is counted in neither bucket below (same convention as Q14).
        "answerable_cases_requiring_evidence": 15,
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
        "q03",
        "q04",
        "q05",
        "q07",
        "q08",
        "q12",
        "q13",
        "q18",
    }
    assert sum(question.lifecycle == "future" for question in representative) == 9


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
            "company": "한화오션",
            "metric": "contract_amount",
            "normalized_value": "1933600000000",
            "normalized_unit": "KRW",
            "evidence_ids": [
                "evidence_exchange_20250917800008_contract_amount"
            ],
        },
        {
            "company": "한화오션",
            "metric": "contract_start_date",
            "normalized_value": "2025-09-16",
            "evidence_ids": [
                "evidence_exchange_20250917800008_contract_start_date"
            ],
        },
        {
            "company": "한화오션",
            "metric": "contract_end_date",
            "normalized_value": "2029-05-31",
            "evidence_ids": [
                "evidence_exchange_20250917800008_contract_end_date"
            ],
        },
    ]
    assert q02.min_evidence_count == 3
    assert q02.integrity.fact_evidence_references is True


def test_active_q05_gold_pins_two_scopes_without_freezing_evidence_count() -> None:
    loaded = load_dataset()
    questions = {question.case_id: question for question in loaded.questions}
    q05 = {gold.case_id: gold for gold in loaded.gold_answers}["q05"]

    assert questions["q05"].lifecycle == "active"
    assert q05.execution_status is ExecutionStatus.COMPLETED
    assert q05.status is AnswerabilityStatus.ANSWERABLE
    assert q05.reason is None
    assert q05.counts.model_dump(exclude_none=True) == {
        "facts": 0,
        "calculations": 0,
        "comparisons": 0,
        "document_summaries": 2,
    }
    assert [item.section_scope.value for item in q05.document_summaries] == [
        "business_overview",
        "products",
    ]
    assert [item.fiscal_year for item in q05.document_summaries] == [2025, 2025]
    assert q05.min_evidence_count == 1
    assert q05.counts.evidence is None
    assert q05.integrity.document_summary_evidence_references is True
    assert q05.integrity.document_summary_same_filing is True
    assert q05.integrity.document_summary_evidence_same_filing is True


def test_active_q04_gold_pins_facility_scope_without_corpus_values() -> None:
    loaded = load_dataset()
    questions = {question.case_id: question for question in loaded.questions}
    q04 = {gold.case_id: gold for gold in loaded.gold_answers}["q04"]

    assert questions["q04"].lifecycle == "active"
    assert q04.execution_status is ExecutionStatus.COMPLETED
    assert q04.status is AnswerabilityStatus.ANSWERABLE
    assert q04.reason is None
    assert q04.counts.model_dump(exclude_none=True) == {
        "facts": 0,
        "calculations": 0,
        "comparisons": 0,
        "document_summaries": 1,
    }
    assert [item.model_dump(exclude_none=True, mode="json") for item in q04.document_summaries] == [
        {
            "company": "삼성바이오로직스",
            "report_name": "사업보고서",
            "section_scope": "facility_investment",
            "fiscal_year": 2025,
        }
    ]
    assert q04.min_evidence_count == 1
    assert q04.counts.evidence is None
    assert q04.integrity.document_summary_evidence_references is True
    assert q04.integrity.document_summary_same_filing is True
    assert q04.integrity.document_summary_evidence_same_filing is True


def test_active_q07_gold_pins_temporal_delta_contract() -> None:
    q07 = {gold.case_id: gold for gold in load_dataset().gold_answers}["q07"]

    assert q07.counts.model_dump(exclude_none=True) == {
        "facts": 2,
        "calculations": 2,
        "comparisons": 0,
    }
    assert [(item.operation, item.unit) for item in q07.calculations] == [
        ("delta", "원"),
        ("delta_pct", "percent"),
    ]
    assert q07.integrity.fact_evidence_references is True
    assert q07.integrity.calculation_evidence_references is True
    assert q07.integrity.result_input_references is True
    assert q07.integrity.calculation_values_from_inputs is True


def test_active_q08_gold_pins_actual_operating_margin_inputs() -> None:
    q08 = {gold.case_id: gold for gold in load_dataset().gold_answers}["q08"]

    assert [
        fact.model_dump(exclude_none=True, mode="json")
        for fact in q08.expected_facts()
    ] == [
        {
            "company": "한미반도체(주)",
            "metric": "revenue",
            "fiscal_year": 2024,
            "accounting_scope": "consolidated",
            "normalized_value": "558917191547",
            "normalized_unit": "원",
        },
        {
            "company": "한미반도체(주)",
            "metric": "operating_income",
            "fiscal_year": 2024,
            "accounting_scope": "consolidated",
            "normalized_value": "255391604854",
            "normalized_unit": "원",
        },
        {
            "company": "한미반도체(주)",
            "metric": "revenue",
            "fiscal_year": 2025,
            "accounting_scope": "consolidated",
            "normalized_value": "576684619527",
            "normalized_unit": "원",
        },
        {
            "company": "한미반도체(주)",
            "metric": "operating_income",
            "fiscal_year": 2025,
            "accounting_scope": "consolidated",
            "normalized_value": "251390142142",
            "normalized_unit": "원",
        },
    ]


def test_active_q12_gold_pins_actual_aggregate_comparison_structure() -> None:
    loaded = load_dataset()
    questions = {question.case_id: question for question in loaded.questions}
    q12 = {gold.case_id: gold for gold in loaded.gold_answers}["q12"]

    assert questions["q12"].lifecycle == "active"
    assert q12.counts.model_dump(exclude_none=True) == {
        "facts": 3,
        "evidence": 3,
        "calculations": 2,
        "comparisons": 1,
    }
    assert [fact.normalized_value for fact in q12.expected_facts()] == [
        "383000000000",
        "375900000000",
        "28533520000",
    ]
    assert [
        calculation.model_dump(exclude_none=True, mode="json")
        for calculation in q12.calculations
    ] == [
        {
            "calculation_id": "calculation_investment_amount_aggregate_2024_LG이노텍",
            "operation": "aggregate",
            "unit": "KRW",
            "value": "758900000000",
            "inputs": [
                {
                    "input_type": "fact",
                    "input_id": (
                        "fact_event_facility_investment_exchange_20240220800842_"
                        "investment_amount"
                    ),
                },
                {
                    "input_type": "fact",
                    "input_id": (
                        "fact_event_facility_investment_exchange_20241121800444_"
                        "investment_amount"
                    ),
                },
            ],
            "evidence_ids": [
                "evidence_exchange_20240220800842_investment_amount",
                "evidence_exchange_20241121800444_investment_amount",
            ],
        },
        {
            "calculation_id": "calculation_investment_amount_aggregate_2024_레인보우로보틱스",
            "operation": "aggregate",
            "unit": "KRW",
            "value": "28533520000",
            "inputs": [
                {
                    "input_type": "fact",
                    "input_id": (
                        "fact_event_facility_investment_exchange_20240430900455_"
                        "investment_amount"
                    ),
                }
            ],
            "evidence_ids": [
                "evidence_exchange_20251205900454_investment_amount"
            ],
        },
    ]
    assert [
        comparison.model_dump(mode="json") for comparison in q12.comparisons
    ] == [
        {
            "left": {
                "input_type": "calculation",
                "input_id": "calculation_investment_amount_aggregate_2024_LG이노텍",
            },
            "right": {
                "input_type": "calculation",
                "input_id": "calculation_investment_amount_aggregate_2024_레인보우로보틱스",
            },
            "relation": "left_greater",
        }
    ]
    assert q12.integrity.fact_evidence_references is True
    assert q12.integrity.calculation_evidence_references is True
    assert q12.integrity.result_input_references is True
    assert q12.integrity.calculation_values_from_inputs is True


def test_active_q03_gold_pins_actual_holding_facts_and_evidence() -> None:
    loaded = load_dataset()
    questions = {question.case_id: question for question in loaded.questions}
    q03 = {gold.case_id: gold for gold in loaded.gold_answers}["q03"]

    assert questions["q03"].lifecycle == "active"
    assert q03.execution_status is ExecutionStatus.COMPLETED
    assert q03.status is AnswerabilityStatus.ANSWERABLE
    assert q03.counts.model_dump(exclude_none=True) == {"facts": 2, "evidence": 2}
    assert [
        fact.model_dump(exclude_none=True, mode="json") for fact in q03.expected_facts()
    ] == [
        {
            "company": "삼성전자",
            "metric": "holding_shares",
            "normalized_value": "1237964708",
            "normalized_unit": "shares",
            "evidence_ids": [
                "evidence_holding_20230120000563_table_0016_r3_c4_ths_stk_cnt"
            ],
        },
        {
            "company": "삼성전자",
            "metric": "holding_ratio",
            "normalized_value": "20.74",
            "normalized_unit": "percent",
            "evidence_ids": [
                "evidence_holding_20230120000563_table_0016_r3_c5_ths_stk_rt"
            ],
        },
    ]
    assert q03.min_evidence_count == 2
    assert q03.integrity.fact_evidence_references is True


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

    for case_id in ("q06", "q09"):
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
        ValueError,
        match="ANSWERABLE cases need a fact, document-summary, or event-history expectation",
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


def q05_gold() -> GoldAnswer:
    return GoldAnswer.model_validate(
        {
            "case_id": "case",
            "execution_status": "COMPLETED",
            "status": "ANSWERABLE",
            "counts": {
                "facts": 0,
                "calculations": 0,
                "comparisons": 0,
                "document_summaries": 2,
            },
            "document_summaries": [
                {"section_scope": "business_overview", "fiscal_year": 2025},
                {"section_scope": "products", "fiscal_year": 2025},
            ],
            "min_evidence_count": 1,
            "integrity": {
                "document_summary_evidence_references": True,
                "document_summary_same_filing": True,
                "document_summary_evidence_same_filing": True,
            },
        }
    )


def summary_prediction(
    *,
    filing_doc_id: str = "annual-filing-2025",
    filing_fiscal_year: int = 2025,
    product_doc_id: str | None = None,
    product_evidence_id: str = "evidence-products",
    extra_evidence_doc_id: str | None = None,
) -> PredictionCase:
    product_doc_id = product_doc_id or filing_doc_id
    evidence = [
        {"evidence_id": "evidence-overview", "doc_id": filing_doc_id, "location_type": "text"},
        {"evidence_id": "evidence-products", "doc_id": product_doc_id, "location_type": "table"},
    ]
    if extra_evidence_doc_id is not None:
        evidence.append(
            {
                "evidence_id": "evidence-extra",
                "doc_id": extra_evidence_doc_id,
                "location_type": "text",
            }
        )
    summaries = []
    for name, scope, doc_id, evidence_id in (
        ("overview", "business_overview", filing_doc_id, "evidence-overview"),
        ("products", "products", product_doc_id, product_evidence_id),
    ):
        summaries.append(
            {
                "summary_id": f"summary-{name}",
                "doc_id": doc_id,
                "company": "Synthetic Company",
                "report_name": "Annual Report",
                "section_scope": scope,
                "items": [
                    {
                        "item_id": f"{name}-item",
                        "item_type": "paragraph",
                        "label": name,
                        "evidence_ids": [evidence_id],
                    }
                ],
            }
        )
    actual = prediction("case", facts=[], evidence=evidence, document_summaries=summaries)
    actual.source_fiscal_years = {
        filing_doc_id: filing_fiscal_year,
        product_doc_id: filing_fiscal_year,
    }
    return actual


def test_document_summary_gold_matches_two_scopes_and_resolved_evidence() -> None:
    assert compare_prediction(q05_gold(), summary_prediction()) == []


def test_document_summary_gold_rejects_stale_filing_with_identical_structure() -> None:
    failures = compare_prediction(
        q05_gold(),
        summary_prediction(
            filing_doc_id="annual-filing-2024",
            filing_fiscal_year=2024,
        ),
    )

    assert failures == [
        "document_summaries: expected fields did not match distinct predicted summaries"
    ]


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("company", "다른기업"),
        ("report_name", "분기보고서"),
    ],
)
def test_q04_gold_rejects_wrong_document_identity(field: str, wrong_value: str) -> None:
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q04"]
    actual = summary_prediction()
    actual.document_summaries = actual.document_summaries[:1]
    actual.evidence = actual.evidence[:1]
    summary = actual.document_summaries[0]
    summary.company = "삼성바이오로직스"
    summary.report_name = "사업보고서"
    summary.section_scope = runner.SectionScope.FACILITY_INVESTMENT
    setattr(summary, field, wrong_value)

    failures = compare_prediction(gold, actual)

    assert failures == [
        "document_summaries: expected fields did not match distinct predicted summaries"
    ]


def test_document_summary_scope_expectations_are_distinct() -> None:
    actual = summary_prediction()
    actual.document_summaries[1].section_scope = actual.document_summaries[0].section_scope

    failures = compare_prediction(q05_gold(), actual)

    assert any(item.startswith("document_summaries:") for item in failures)


def test_document_summary_dangling_evidence_reference_is_not_vacuous() -> None:
    failures = compare_prediction(
        q05_gold(), summary_prediction(product_evidence_id="missing-evidence")
    )

    assert "integrity.document_summary_evidence_references: expected True, got False" in failures


def test_each_expected_document_summary_requires_items_and_resolved_evidence() -> None:
    actual = summary_prediction()
    actual.document_summaries[0].items = []

    failures = compare_prediction(q05_gold(), actual)

    assert actual.evidence
    assert {summary.section_scope.value for summary in actual.document_summaries} == {
        "business_overview",
        "products",
    }
    assert "integrity.document_summary_evidence_references: expected True, got False" in failures


def test_document_summaries_and_evidence_must_stay_in_selected_filing() -> None:
    split_summaries = compare_prediction(
        q05_gold(), summary_prediction(product_doc_id="other-filing")
    )
    stray_evidence = compare_prediction(
        q05_gold(), summary_prediction(extra_evidence_doc_id="other-filing")
    )

    assert "integrity.document_summary_same_filing: expected True, got False" in split_summaries
    assert (
        "integrity.document_summary_evidence_same_filing: expected True, got False"
        in stray_evidence
    )


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


def test_expected_fact_company_mismatch_fails() -> None:
    gold = answerable_gold(
        "case",
        facts=[{"company": "Expected Company", "metric": "revenue"}],
    )

    failures = compare_prediction(gold, prediction("case"))

    assert failures == ["facts: expected fields did not match distinct predicted Facts"]


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


@pytest.mark.parametrize("collection", ["fact", "calculation"])
def test_empty_evidence_references_fail_non_vacuous_integrity(collection: str) -> None:
    integrity = {f"{collection}_evidence_references": True}
    overrides = (
        {
            "facts": [
                {
                    "fact_id": "fact-1",
                    "company": "Synthetic Company",
                    "metric": "revenue",
                    "accounting_scope": "consolidated",
                    "raw_value": "100",
                    "evidence_ids": [],
                }
            ]
        }
        if collection == "fact"
        else {
            "calculations": [
                {
                    "calculation_id": "calculation-1",
                    "operation": "identity",
                    "inputs": [{"input_type": "fact", "input_id": "fact-1"}],
                    "evidence_ids": [],
                }
            ]
        }
    )

    failures = compare_prediction(
        answerable_gold("case", integrity=integrity), prediction("case", **overrides)
    )

    assert any("evidence_ids=['<missing>']" in item for item in failures)


def q07_prediction(*, delta_value: str = "50.0000") -> PredictionCase:
    facts = [
        {
            "fact_id": "revenue-2024",
            "company": "한미반도체(주)",
            "metric": "revenue",
            "fiscal_year": 2024,
            "accounting_scope": "consolidated",
            "normalized_value": "100",
            "normalized_unit": "원",
            "evidence_ids": ["evidence-1"],
        },
        {
            "fact_id": "revenue-2025",
            "company": "한미반도체(주)",
            "metric": "revenue",
            "fiscal_year": 2025,
            "accounting_scope": "consolidated",
            "normalized_value": "150",
            "normalized_unit": "원",
            "evidence_ids": ["evidence-2"],
        },
    ]
    inputs = [
        {"input_type": "fact", "input_id": "revenue-2024"},
        {"input_type": "fact", "input_id": "revenue-2025"},
    ]
    return prediction(
        "case",
        facts=facts,
        evidence=[
            {"evidence_id": "evidence-1", "doc_id": "doc-2024", "location_type": "table"},
            {"evidence_id": "evidence-2", "doc_id": "doc-2025", "location_type": "table"},
        ],
        calculations=[
            {
                "calculation_id": "delta",
                "operation": "delta",
                "inputs": inputs,
                "value": delta_value,
                "unit": "원",
                "evidence_ids": ["evidence-1", "evidence-2"],
            },
            {
                "calculation_id": "delta-pct",
                "operation": "delta_pct",
                "inputs": inputs,
                "value": "50.0000",
                "unit": "percent",
                "evidence_ids": ["evidence-1", "evidence-2"],
            },
        ],
    )


def test_q07_calculation_semantics_recompute_exact_values_from_ordered_facts() -> None:
    gold = answerable_gold(
        "case",
        calculations=[
            {"operation": "delta", "unit": "원"},
            {"operation": "delta_pct", "unit": "percent"},
        ],
        integrity={"calculation_values_from_inputs": True},
    )

    assert compare_prediction(gold, q07_prediction()) == []

    failures = compare_prediction(gold, q07_prediction(delta_value="49.9999"))

    assert any("expected value 50.0000, got 49.9999" in item for item in failures)


def test_q07_calculation_semantics_reject_reversed_fact_inputs() -> None:
    actual = q07_prediction().model_dump(mode="python")
    reversed_inputs = list(reversed(actual["calculations"][0]["inputs"]))
    for calculation in actual["calculations"]:
        calculation["inputs"] = reversed_inputs
        calculation["evidence_ids"] = ["evidence-2", "evidence-1"]
    actual["calculations"][0]["value"] = "-50.0000"
    actual["calculations"][1]["value"] = "-33.3333"

    failures = compare_prediction(
        answerable_gold(
            "case", integrity={"calculation_values_from_inputs": True}
        ),
        PredictionCase.model_validate(actual),
    )

    assert any("Fact inputs are not previous/current" in item for item in failures)
    assert not any("expected value" in item for item in failures)


def test_q07_calculation_semantics_reject_unrelated_valid_evidence() -> None:
    actual = q07_prediction().model_dump(mode="python")
    actual["evidence"].append(
        {"evidence_id": "evidence-unrelated", "doc_id": "doc-other", "location_type": "table"}
    )
    actual["calculations"][0]["evidence_ids"] = ["evidence-unrelated"]

    failures = compare_prediction(
        answerable_gold(
            "case",
            integrity={
                "calculation_evidence_references": True,
                "calculation_values_from_inputs": True,
            },
        ),
        PredictionCase.model_validate(actual),
    )

    assert not any("dangling Calculation" in item for item in failures)
    assert any(
        "expected input Evidence ['evidence-1', 'evidence-2'], "
        "got ['evidence-unrelated']" in item
        for item in failures
    )


def q08_prediction(*, change_value: str = "-5.0000") -> PredictionCase:
    facts = []
    evidence = []
    for year, operating_income, revenue in ((2024, "40", "100"), (2025, "45", "150")):
        for metric, value in (("operating_income", operating_income), ("revenue", revenue)):
            evidence_id = f"evidence-{metric}-{year}"
            facts.append(
                {
                    "fact_id": f"{metric}-{year}",
                    "company": "Synthetic Company",
                    "metric": metric,
                    "fiscal_year": year,
                    "accounting_scope": "consolidated",
                    "normalized_value": value,
                    "normalized_unit": "원",
                    "evidence_ids": [evidence_id],
                }
            )
            evidence.append(
                {"evidence_id": evidence_id, "doc_id": f"doc-{year}", "location_type": "table"}
            )
    calculations = []
    for year, value in ((2024, "40.0000"), (2025, "30.0000")):
        calculations.append(
            {
                "calculation_id": f"margin-{year}",
                "operation": "ratio",
                "inputs": [
                    {"input_type": "fact", "input_id": f"operating_income-{year}"},
                    {"input_type": "fact", "input_id": f"revenue-{year}"},
                ],
                "value": value,
                "unit": "percent",
                "evidence_ids": [
                    f"evidence-operating_income-{year}",
                    f"evidence-revenue-{year}",
                ],
            }
        )
    calculations.append(
        {
            "calculation_id": "margin-change",
            "operation": "diff_pp",
            "inputs": [
                {"input_type": "calculation", "input_id": "margin-2024"},
                {"input_type": "calculation", "input_id": "margin-2025"},
            ],
            "value": change_value,
            "unit": "percentage_point",
            "evidence_ids": [
                "evidence-operating_income-2024",
                "evidence-revenue-2024",
                "evidence-operating_income-2025",
                "evidence-revenue-2025",
            ],
        }
    )
    return prediction(
        "case", facts=facts, evidence=evidence, calculations=calculations
    )


def test_ratio_and_diff_pp_recompute_from_facts() -> None:
    gold = answerable_gold(
        "case", integrity={"calculation_values_from_inputs": True}
    )

    assert compare_prediction(gold, q08_prediction(change_value="-10.0000")) == []

    failures = compare_prediction(gold, q08_prediction())

    assert any("expected value -10.0000, got -5.0000" in item for item in failures)


def test_q08_rejects_wrong_source_fact_with_internally_consistent_calculations() -> None:
    q08 = {gold.case_id: gold for gold in load_dataset().gold_answers}["q08"]
    actual = q08_prediction(change_value="-2.1017").model_dump(mode="python")
    actual["case_id"] = "q08"
    values = {
        (2024, "revenue"): "558917191547",
        (2024, "operating_income"): "255391604854",
        (2025, "revenue"): "576684619527",
        # Deliberately wrong by one won while preserving every reference.
        (2025, "operating_income"): "251390142143",
    }
    for fact in actual["facts"]:
        fact["company"] = "한미반도체(주)"
        fact["normalized_value"] = values[(fact["fiscal_year"], fact["metric"])]
    actual["calculations"][0]["value"] = "45.6940"
    actual["calculations"][1]["value"] = "43.5923"
    actual["calculations"][2]["value"] = "-2.1017"

    failures = compare_prediction(q08, PredictionCase.model_validate(actual))

    assert failures == ["facts: expected fields did not match distinct predicted Facts"]


def aggregate_prediction() -> PredictionCase:
    facts = [
        {
            "fact_id": "left-1",
            "company": "Left Company",
            "metric": "investment_amount",
            "accounting_scope": "not_applicable",
            "normalized_value": "100",
            "normalized_unit": "KRW",
            "evidence_ids": ["evidence-left-1"],
        },
        {
            "fact_id": "left-2",
            "company": "Left Company",
            "metric": "investment_amount",
            "accounting_scope": "not_applicable",
            "normalized_value": "50",
            "normalized_unit": "KRW",
            "evidence_ids": ["evidence-left-2"],
        },
        {
            "fact_id": "right-1",
            "company": "Right Company",
            "metric": "investment_amount",
            "accounting_scope": "not_applicable",
            "normalized_value": "25",
            "normalized_unit": "KRW",
            "evidence_ids": ["evidence-right-1"],
        },
    ]
    evidence = [
        {"evidence_id": evidence_id, "doc_id": evidence_id, "location_type": "table"}
        for evidence_id in ("evidence-left-1", "evidence-left-2", "evidence-right-1")
    ]
    calculations = [
        {
            "calculation_id": "left-total",
            "operation": "aggregate",
            "inputs": [
                {"input_type": "fact", "input_id": "left-1"},
                {"input_type": "fact", "input_id": "left-2"},
            ],
            "value": "150",
            "unit": "KRW",
            "evidence_ids": ["evidence-left-1", "evidence-left-2"],
        },
        {
            "calculation_id": "right-total",
            "operation": "aggregate",
            "inputs": [{"input_type": "fact", "input_id": "right-1"}],
            "value": "25",
            "unit": "KRW",
            "evidence_ids": ["evidence-right-1"],
        },
    ]
    return prediction(
        "case",
        facts=facts,
        evidence=evidence,
        calculations=calculations,
        comparisons=[
            {
                "comparison_id": "aggregate-comparison",
                "left": {"input_type": "calculation", "input_id": "left-total"},
                "right": {"input_type": "calculation", "input_id": "right-total"},
                "relation": "left_greater",
            }
        ],
    )


def aggregate_gold() -> GoldAnswer:
    actual = aggregate_prediction()
    return answerable_gold(
        "case",
        facts=[
            {"company": fact.company, "metric": fact.metric}
            for fact in actual.facts
        ],
        counts={"facts": 3, "calculations": 2, "comparisons": 1},
        calculations=[
            {
                "operation": item.operation,
                "inputs": item.inputs,
                "value": item.value,
                "unit": item.unit,
                "evidence_ids": item.evidence_ids,
            }
            for item in actual.calculations
        ],
        comparisons=[
            {
                "left": actual.comparisons[0].left,
                "right": actual.comparisons[0].right,
                "relation": actual.comparisons[0].relation,
            }
        ],
        integrity={
            "fact_evidence_references": True,
            "calculation_evidence_references": True,
            "result_input_references": True,
            "calculation_values_from_inputs": True,
        },
    )


def test_q12_swapped_calculation_ids_fail_even_when_other_fields_are_unchanged() -> None:
    actual = aggregate_prediction().model_copy(update={"case_id": "q12"})
    calculation_ids = [
        "calculation_investment_amount_aggregate_2024_LG이노텍",
        "calculation_investment_amount_aggregate_2024_레인보우로보틱스",
    ]
    actual = actual.model_copy(
        update={
            "calculations": [
                item.model_copy(update={"calculation_id": calculation_id})
                for item, calculation_id in zip(
                    actual.calculations, calculation_ids, strict=True
                )
            ]
        }
    )
    gold = aggregate_gold().model_copy(
        update={
            "case_id": "q12",
            "calculations": [
                item.model_copy(update={"calculation_id": actual.calculation_id})
                for item, actual in zip(
                    aggregate_gold().calculations,
                    actual.calculations,
                    strict=True,
                )
            ]
        }
    )
    swapped = actual.model_dump(mode="python")
    first_id = swapped["calculations"][0]["calculation_id"]
    swapped["calculations"][0]["calculation_id"] = swapped["calculations"][1][
        "calculation_id"
    ]
    swapped["calculations"][1]["calculation_id"] = first_id

    failures = compare_prediction(gold, PredictionCase.model_validate(swapped))

    assert any(item.startswith("calculations:") for item in failures)


def test_aggregate_structure_and_provenance_pass() -> None:
    assert compare_prediction(aggregate_gold(), aggregate_prediction()) == []


def test_aggregate_missing_fact_fails() -> None:
    actual = aggregate_prediction().model_dump(mode="python")
    actual["facts"].pop()

    failures = compare_prediction(aggregate_gold(), PredictionCase.model_validate(actual))

    assert any(item.startswith("counts.facts:") for item in failures)
    assert any("fact:right-1" in item for item in failures)


def test_aggregate_wrong_value_fails_recomputation() -> None:
    actual = aggregate_prediction().model_dump(mode="python")
    actual["calculations"][0]["value"] = "149"

    failures = compare_prediction(aggregate_gold(), PredictionCase.model_validate(actual))

    assert any("expected value 150, got 149" in item for item in failures)


def test_aggregate_missing_calculation_input_fails() -> None:
    actual = aggregate_prediction().model_dump(mode="python")
    actual["calculations"][0]["inputs"].pop()

    failures = compare_prediction(aggregate_gold(), PredictionCase.model_validate(actual))

    assert any(item.startswith("calculations:") for item in failures)
    assert any("expected value 100, got 150" in item for item in failures)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("comparisons", []),
        ("relation", "right_greater"),
        ("right", {"input_type": "calculation", "input_id": "left-total"}),
    ],
)
def test_aggregate_comparison_shape_failures_are_detected(field: str, value: object) -> None:
    actual = aggregate_prediction().model_dump(mode="python")
    if field == "comparisons":
        actual[field] = value
    else:
        actual["comparisons"][0][field] = value

    failures = compare_prediction(aggregate_gold(), PredictionCase.model_validate(actual))

    assert any(
        item.startswith("counts.comparisons:") or item.startswith("comparisons:")
        for item in failures
    )


def test_aggregate_dangling_evidence_fails() -> None:
    actual = aggregate_prediction().model_dump(mode="python")
    actual["evidence"].pop()

    failures = compare_prediction(aggregate_gold(), PredictionCase.model_validate(actual))

    assert any("evidence-right-1" in item for item in failures)


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
