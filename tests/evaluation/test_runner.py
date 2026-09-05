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
        "q06",
        "q07",
        "q08",
        "q09",
        "q10",
        "q11",
        "q12",
        "q13",
        "q14",
        "q15",
        "q16",
        "q17",
        "q18",
    }
    assert sum(question.lifecycle == "future" for question in representative) == 0


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


Q06_SOURCE_DOC_ID = "exchange_20250429800933"
Q06_EVENT_ID = f"event_facility_investment_{Q06_SOURCE_DOC_ID}"

# All eleven fields the production handler resolves for this event, mirroring the
# actual runtime output. Only five are Q06's required summary fields; the rest are
# carried so the prediction shape (and its evidence count) matches the real Agent.
Q06_ALL_FIELDS: dict[str, dict[str, object]] = {
    "investment_type": {"value": "신규시설투자", "unit": None},
    "investment_target": {"value": "파주 AIDC 신규 구축", "unit": None},
    "investment_amount": {"value": 615600000000.0, "unit": "KRW"},
    "equity": {"value": 8768379152088.0, "unit": "KRW"},
    "equity_ratio": {"value": 7.02, "unit": "%"},
    "large_corporation": {"value": "해당", "unit": None},
    "investment_purpose": {"value": "고객 수요 증가에 따른 AIDC 신규 구축", "unit": None},
    "investment_start_date": {"value": "2025-05-07", "unit": None},
    "investment_end_date": {"value": "2027-05-25", "unit": None},
    "board_resolution_date": {"value": "2025-04-29", "unit": None},
    "notes": {"value": "예정일로서 향후 변동될 수 있습니다.", "unit": None},
}


def q06_prediction() -> PredictionCase:
    """Actual-corpus Q06 shape, with values observed from the production runtime."""
    return PredictionCase.model_validate(
        {
            "case_id": "q06",
            "execution_status": "COMPLETED",
            "answerability_status": "ANSWERABLE",
            "events": [
                {
                    "event_id": f"event_{Q06_SOURCE_DOC_ID}",
                    "event_type": "facility_investment",
                    "company": "LG유플러스",
                    "source_doc_id": Q06_SOURCE_DOC_ID,
                    "is_correction": False,
                }
            ],
            "resolved_event_states": [
                {
                    "event_id": Q06_EVENT_ID,
                    "event_type": "facility_investment",
                    "company": "LG유플러스",
                    "as_of": "2025-04-29",
                    "fields": {
                        field_name: {
                            "value": field["value"],
                            "unit": field["unit"],
                            "source_doc_id": Q06_SOURCE_DOC_ID,
                            "evidence_ids": [f"evidence_{Q06_SOURCE_DOC_ID}_{field_name}"],
                        }
                        for field_name, field in Q06_ALL_FIELDS.items()
                    },
                }
            ],
            "evidence": [
                {
                    "evidence_id": f"evidence_{Q06_SOURCE_DOC_ID}_{field_name}",
                    "doc_id": Q06_SOURCE_DOC_ID,
                    "location_type": "event_field",
                    "field_name": field_name,
                }
                for field_name in Q06_ALL_FIELDS
            ],
            "warnings": [
                "공시 판본은 질문이 정한 기준을 따랐습니다: chain_policy=latest_valid."
            ],
        }
    )


def test_active_q06_gold_pins_actual_event_summary_structure() -> None:
    loaded = load_dataset()
    questions = {question.case_id: question for question in loaded.questions}
    q06 = {gold.case_id: gold for gold in loaded.gold_answers}["q06"]

    assert questions["q06"].lifecycle == "active"
    assert q06.counts.model_dump(exclude_none=True) == {
        "facts": 0,
        "evidence": 11,
        "events": 1,
        "event_links": 0,
        "event_histories": 0,
        "resolved_event_states": 1,
        "calculations": 0,
        "comparisons": 0,
        "narrative_comparisons": 0,
        "document_summaries": 0,
        "warnings": 1,
        "conversion_gaps": 0,
    }
    assert compare_prediction(q06, q06_prediction()) == []


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("company", "무관한기업"),
        ("event_type", "supply_contract"),
        ("source_doc_id", "exchange_unrelated"),
    ],
)
def test_q06_rejects_wrong_event_identity(field: str, wrong_value: object) -> None:
    """A different event's identity must fail even though every count still matches."""
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q06"]
    actual = q06_prediction().model_dump(mode="python")
    actual["events"][0][field] = wrong_value

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("events:") for item in failures)


@pytest.mark.parametrize(
    ("field_name", "field", "wrong_value"),
    [
        ("investment_target", "value", "물류센터 증설"),
        ("investment_amount", "value", 1.0),
        ("investment_amount", "unit", "USD"),
        ("investment_purpose", "value", "무관한 목적"),
        ("investment_start_date", "value", "1999-01-01"),
        ("investment_end_date", "value", "1999-01-01"),
    ],
)
def test_q06_rejects_wrong_resolved_state_value(
    field_name: str, field: str, wrong_value: object
) -> None:
    """A same-shaped state that resolved a different disclosure's values must fail.

    Counts alone (events=1, resolved_event_states=1, evidence=11) do not change when
    only the field values differ, so this is the case count-only gold would let
    through as a false positive.
    """
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q06"]
    actual = q06_prediction().model_dump(mode="python")
    actual["resolved_event_states"][0]["fields"][field_name][field] = wrong_value

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("resolved_event_states:") for item in failures)


def test_q06_rejects_wrong_resolved_state_source_doc() -> None:
    """The latest-state source document identity is pinned, not just its value."""
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q06"]
    actual = q06_prediction().model_dump(mode="python")
    actual["resolved_event_states"][0]["fields"]["investment_amount"][
        "source_doc_id"
    ] = "exchange_unrelated"

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("resolved_event_states:") for item in failures)


def test_q06_rejects_a_same_count_different_event_prediction() -> None:
    """The core false-positive this gold must not let through.

    Swapping company, event_type, and every field value to a different disclosure's
    content leaves every collection count identical (events=1, resolved_event_states=1,
    evidence=11, all else 0) but must still fail on identity/reference semantics.
    """
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q06"]
    actual = q06_prediction().model_dump(mode="python")

    unrelated_doc_id = "exchange_unrelated900001"
    actual["events"][0]["company"] = "무관한기업"
    actual["events"][0]["event_type"] = "supply_contract"
    actual["events"][0]["source_doc_id"] = unrelated_doc_id
    actual["resolved_event_states"][0]["company"] = "무관한기업"
    actual["resolved_event_states"][0]["event_type"] = "supply_contract"
    for field_name, resolved in actual["resolved_event_states"][0]["fields"].items():
        resolved["value"] = f"무관한 값 {field_name}"
        resolved["source_doc_id"] = unrelated_doc_id
        resolved["evidence_ids"] = [f"evidence_{unrelated_doc_id}_{field_name}"]
    for evidence in actual["evidence"]:
        evidence["doc_id"] = unrelated_doc_id
        evidence["evidence_id"] = f"evidence_{unrelated_doc_id}_{evidence['field_name']}"

    prediction = PredictionCase.model_validate(actual)
    failures = compare_prediction(gold, prediction)

    assert any(item.startswith("events:") for item in failures)
    assert any(item.startswith("resolved_event_states:") for item in failures)


@pytest.mark.parametrize(
    ("collection", "item"),
    [
        (
            "event_links",
            {
                "link_id": "unrelated-link",
                "source_doc_id": "unrelated-doc",
                "relation": "correction_of",
                "raw_reference": {},
                "resolution_status": "resolved",
            },
        ),
        ("warnings", "unrelated warning"),
    ],
)
def test_q06_rejects_unrelated_structured_collections(
    collection: str, item: object
) -> None:
    gold = {entry.case_id: entry for entry in load_dataset().gold_answers}["q06"]
    actual = q06_prediction().model_dump(mode="python")
    actual[collection] = [*actual[collection], item]

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(failure.startswith("counts.") for failure in failures)


def q10_prediction() -> PredictionCase:
    """Actual-corpus Q10 shape, with values observed from the production runtime."""
    return PredictionCase.model_validate(
        {
            "case_id": "q10",
            "execution_status": "COMPLETED",
            "answerability_status": "ANSWERABLE",
            "facts": [
                {
                    "fact_id": "fact_periodic_20260320000859_revenue_table_0127_r1_c1",
                    "company": "에이치디현대중공업(주)",
                    "metric": "revenue",
                    "fiscal_year": 2025,
                    "accounting_scope": "consolidated",
                    "raw_value": "17,580,633,257",
                    "raw_unit": "천원",
                    "normalized_value": "17580633257000",
                    "normalized_unit": "원",
                    "evidence_ids": [
                        "evidence_periodic_20260320000859_table_0127_r1_c1"
                    ],
                },
                {
                    "fact_id": "fact_periodic_20260312001037_revenue_table_0102_r1_c1",
                    "company": "삼성중공업(주)",
                    "metric": "revenue",
                    "fiscal_year": 2025,
                    "accounting_scope": "consolidated",
                    "raw_value": "10,650,010,702,818",
                    "raw_unit": "원",
                    "normalized_value": "10650010702818",
                    "normalized_unit": "원",
                    "evidence_ids": [
                        "evidence_periodic_20260312001037_table_0102_r1_c1"
                    ],
                },
            ],
            "evidence": [
                {
                    "evidence_id": "evidence_periodic_20260320000859_table_0127_r1_c1",
                    "doc_id": "periodic_20260320000859",
                    "location_type": "table",
                },
                {
                    "evidence_id": "evidence_periodic_20260312001037_table_0102_r1_c1",
                    "doc_id": "periodic_20260312001037",
                    "location_type": "table",
                },
            ],
            "comparisons": [
                {
                    "comparison_id": "comparison_revenue_2025_HD현대중공업_vs_삼성중공업",
                    "left": {
                        "input_type": "fact",
                        "input_id": (
                            "fact_periodic_20260320000859_revenue_table_0127_r1_c1"
                        ),
                    },
                    "right": {
                        "input_type": "fact",
                        "input_id": (
                            "fact_periodic_20260312001037_revenue_table_0102_r1_c1"
                        ),
                    },
                    "relation": "left_greater",
                }
            ],
        }
    )


def test_active_q10_gold_pins_actual_source_comparison_structure() -> None:
    loaded = load_dataset()
    questions = {question.case_id: question for question in loaded.questions}
    q10 = {gold.case_id: gold for gold in loaded.gold_answers}["q10"]

    assert questions["q10"].lifecycle == "active"
    assert q10.counts.model_dump(exclude_none=True) == {
        "facts": 2,
        "evidence": 2,
        "events": 0,
        "event_links": 0,
        "event_histories": 0,
        "resolved_event_states": 0,
        "calculations": 0,
        "comparisons": 1,
        "narrative_comparisons": 0,
        "document_summaries": 0,
        "warnings": 0,
        "conversion_gaps": 0,
    }
    assert compare_prediction(q10, q10_prediction()) == []


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("company", "다른조선사(주)"),
        ("normalized_value", "1"),
        ("evidence_ids", ["evidence-unrelated"]),
    ],
)
def test_q10_rejects_wrong_fact_identity_value_or_source(
    field: str, wrong_value: object
) -> None:
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q10"]
    actual = q10_prediction().model_dump(mode="python")
    actual["facts"][0][field] = wrong_value

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("facts:") for item in failures)


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("relation", "right_greater"),
        (
            "right",
            {
                "input_type": "fact",
                "input_id": "fact_periodic_20260320000859_revenue_table_0127_r1_c1",
            },
        ),
    ],
)
def test_q10_rejects_wrong_comparison_semantics(
    field: str, wrong_value: object
) -> None:
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q10"]
    actual = q10_prediction().model_dump(mode="python")
    actual["comparisons"][0][field] = wrong_value

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("comparisons:") for item in failures)


@pytest.mark.parametrize(
    ("collection", "item"),
    [
        (
            "events",
            {
                "event_id": "unrelated-event",
                "event_type": "supply_contract",
                "company": "무관한 기업",
                "source_doc_id": "unrelated-doc",
            },
        ),
        ("warnings", "unrelated warning"),
        (
            "conversion_gaps",
            {"kind": "unrelated", "subject": "unrelated"},
        ),
    ],
)
def test_q10_rejects_unrelated_structured_collections(
    collection: str, item: object
) -> None:
    gold = {entry.case_id: entry for entry in load_dataset().gold_answers}["q10"]
    actual = q10_prediction().model_dump(mode="python")
    actual[collection] = [item]

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert f"counts.{collection}: expected 0, got 1" in failures


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


def test_active_q11_gold_pins_actual_operating_margin_comparison() -> None:
    loaded = load_dataset()
    questions = {question.case_id: question for question in loaded.questions}
    q11 = {gold.case_id: gold for gold in loaded.gold_answers}["q11"]

    assert questions["q11"].lifecycle == "active"
    assert q11.counts.model_dump(exclude_none=True) == {
        "facts": 4,
        "evidence": 4,
        "calculations": 2,
        "comparisons": 1,
    }
    assert [
        fact.model_dump(exclude_none=True, mode="json")
        for fact in q11.expected_facts()
    ] == [
        {
            "company": "에이치디현대중공업(주)",
            "metric": "revenue",
            "fiscal_year": 2025,
            "accounting_scope": "consolidated",
            "normalized_value": "17580633257000",
            "normalized_unit": "원",
        },
        {
            "company": "에이치디현대중공업(주)",
            "metric": "operating_income",
            "fiscal_year": 2025,
            "accounting_scope": "consolidated",
            "normalized_value": "2037510103000",
            "normalized_unit": "원",
        },
        {
            "company": "삼성중공업(주)",
            "metric": "revenue",
            "fiscal_year": 2025,
            "accounting_scope": "consolidated",
            "normalized_value": "10650010702818",
            "normalized_unit": "원",
        },
        {
            "company": "삼성중공업(주)",
            "metric": "operating_income",
            "fiscal_year": 2025,
            "accounting_scope": "consolidated",
            "normalized_value": "862210880029",
            "normalized_unit": "원",
        },
    ]
    assert [
        calculation.model_dump(exclude_none=True, mode="json")
        for calculation in q11.calculations
    ] == [
        {
            "calculation_id": "calculation_operating_margin_2025_HD현대중공업",
            "operation": "ratio",
            "unit": "percent",
            "value": "11.5895",
            "inputs": [
                {
                    "input_type": "fact",
                    "input_id": (
                        "fact_periodic_20260320000859_operating_income_table_0127_r5_c1"
                    ),
                },
                {
                    "input_type": "fact",
                    "input_id": "fact_periodic_20260320000859_revenue_table_0127_r1_c1",
                },
            ],
            "evidence_ids": [
                "evidence_periodic_20260320000859_table_0127_r5_c1",
                "evidence_periodic_20260320000859_table_0127_r1_c1",
            ],
        },
        {
            "calculation_id": "calculation_operating_margin_2025_삼성중공업",
            "operation": "ratio",
            "unit": "percent",
            "value": "8.0959",
            "inputs": [
                {
                    "input_type": "fact",
                    "input_id": (
                        "fact_periodic_20260312001037_operating_income_table_0102_r5_c1"
                    ),
                },
                {
                    "input_type": "fact",
                    "input_id": "fact_periodic_20260312001037_revenue_table_0102_r1_c1",
                },
            ],
            "evidence_ids": [
                "evidence_periodic_20260312001037_table_0102_r5_c1",
                "evidence_periodic_20260312001037_table_0102_r1_c1",
            ],
        },
    ]
    assert [
        comparison.model_dump(mode="json") for comparison in q11.comparisons
    ] == [
        {
            "left": {
                "input_type": "calculation",
                "input_id": "calculation_operating_margin_2025_HD현대중공업",
            },
            "right": {
                "input_type": "calculation",
                "input_id": "calculation_operating_margin_2025_삼성중공업",
            },
            "relation": "left_greater",
        }
    ]
    assert q11.integrity.fact_evidence_references is True
    assert q11.integrity.calculation_evidence_references is True
    assert q11.integrity.result_input_references is True
    assert q11.integrity.calculation_values_from_inputs is True


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
    assert q13.event_history.model_dump(exclude_none=True, mode="json") == {
        "link_resolution": "resolved",
        "chain_complete": True,
    }
    # The actual chain has three documents and document-level Evidence for each.
    # Do not freeze the producer's current exact Evidence count (9).
    assert q13.min_evidence_count == 3


def test_active_q16_gold_pins_actual_safety_gate_contract() -> None:
    """
    Q16 실측: F1 Gate가 코퍼스 접근 전에 판정한다(2026년 상반기 = 수집 상한 2026-03 밖).

    구조화 계층 9종 전부가 count=0으로 고정된다 — Gate가 retrieval 이전에 거부하므로
    fail-close 상태에서는 어떤 컬렉션도 채워지지 않는다. Q18과 같은 모양이나 사유는
    다르다(out_of_data_range/out_of_corpus_period vs out_of_scope_policy/investment_advice).
    """
    q16 = {gold.case_id: gold for gold in load_dataset().gold_answers}["q16"]
    questions = {question.case_id: question for question in load_dataset().questions}

    assert questions["q16"].lifecycle == "active"
    assert q16.execution_status.value == "COMPLETED"
    assert q16.status.value == "UNANSWERABLE"
    assert q16.reason.value == "out_of_data_range"
    assert q16.answerability_detail_reason == "out_of_corpus_period"
    assert q16.counts.model_dump(exclude_none=True) == {
        "facts": 0,
        "evidence": 0,
        "events": 0,
        "event_links": 0,
        "event_histories": 0,
        "resolved_event_states": 0,
        "calculations": 0,
        "comparisons": 0,
        "document_summaries": 0,
    }
    assert q16.min_evidence_count == 0


def test_active_q18_gold_pins_actual_safety_gate_contract() -> None:
    q18 = {gold.case_id: gold for gold in load_dataset().gold_answers}["q18"]

    assert q18.execution_status.value == "COMPLETED"
    assert q18.status.value == "UNANSWERABLE"
    assert q18.reason.value == "out_of_scope_policy"
    assert q18.answerability_detail_reason == "investment_advice"
    assert q18.counts.model_dump(exclude_none=True) == {"facts": 0, "evidence": 0}
    assert q18.min_evidence_count == 0


def test_active_q17_gold_pins_actual_safety_gate_structure() -> None:
    loaded = load_dataset()
    questions = {question.case_id: question for question in loaded.questions}
    q17 = {gold.case_id: gold for gold in loaded.gold_answers}["q17"]

    assert questions["q17"].question == "이번 수주 공시 나오고 나서 시장 반응은 어땠어?"
    assert questions["q17"].lifecycle == "active"
    assert q17.execution_status is ExecutionStatus.COMPLETED
    assert q17.status is AnswerabilityStatus.UNANSWERABLE
    assert q17.reason is AnswerabilityReason.OUT_OF_SCOPE_DATA
    assert q17.answerability_detail_reason == "external_information"
    assert q17.counts.model_dump(exclude_none=True) == {
        "facts": 0,
        "evidence": 0,
        "events": 0,
        "event_links": 0,
        "event_histories": 0,
        "resolved_event_states": 0,
        "calculations": 0,
        "comparisons": 0,
        "document_summaries": 0,
    }
    assert q17.min_evidence_count == 0


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


def test_no_narrative_future_gold_remains() -> None:
    """
    q09 was the last unresolved future placeholder; it is active now, like q06 before
    it. Every representative question is active, so no case relies any longer on the
    relaxed future-ANSWERABLE validation rule (that relaxed path is still covered
    directly by the synthetic-case tests below, e.g.
    test_future_answerable_with_counts_only_passes).

    Active gold pins facts explicitly instead of leaving it unset the way the old
    future placeholder could. q09's Evidence is not zero (it retains the one fiscal
    year it resolved), so it is asserted on its own rather than folded into the
    facts=0/evidence=0 group below.
    """
    loaded = load_dataset()
    representative = [
        question
        for question in loaded.questions
        if question.representative_question_id is not None
    ]
    gold_by_id = {gold.case_id: gold for gold in loaded.gold_answers}

    assert sum(question.lifecycle == "future" for question in representative) == 0
    assert gold_by_id["q09"].counts.facts == 0

    for case_id in ("q16", "q17", "q18"):
        counts = gold_by_id[case_id].counts
        assert counts.facts == 0
        assert counts.evidence == 0


def test_active_q15_gold_pins_corpus_verified_history_structure() -> None:
    loaded = load_dataset()
    questions = {question.case_id: question for question in loaded.questions}
    q15 = {gold.case_id: gold for gold in loaded.gold_answers}["q15"]

    assert questions["q15"].lifecycle == "active"
    assert q15.counts.model_dump(exclude_none=True) == {
        "facts": 0,
        "events": 0,
        "event_links": 3,
        "event_histories": 1,
        "resolved_event_states": 0,
        "calculations": 0,
        "comparisons": 0,
        "document_summaries": 0,
    }
    assert q15.event_history.model_dump(exclude_none=True, mode="json") == {
        "link_resolution": "resolved",
        "chain_complete": True,
        "document_count": 4,
    }
    assert q15.min_evidence_count == 4
    assert q15.integrity.event_history_evidence_references is True
    # "몇 번 바뀌었어"의 semantic count는 EventLink 수(3)다. document_count=4는 원공시 +
    # 정정 3건의 chain 길이이고, FieldChange 4건은 그 안의 세부 필드 변경 건수이므로 둘 다
    # 사용자 질문의 "몇 번"으로 쓰지 않는다.
    assert q15.semantic_count.model_dump(mode="json") == {
        "source": "event_links",
        "value": 3,
    }


def test_active_q14_gold_requires_ambiguity_instead_of_one_chosen_history() -> None:
    """
    Q14 질문은 counterparty="포드"를 담고 있지만, 현재 확정된 D8 정책에서는
    계약상대방 기반 root 확정(C3)을 수행하지 않는다.

    같은 기업·같은 공시 유형의 원공시가 여러 건이면 counterparty 본문 대조로
    하나를 임의 선택하지 않고 ambiguous_root로 fail-close한다.
    후속 정정·해지 체인이 한쪽에만 있다는 이유로 루트를 역으로 고르면,
    다른 계약의 이력을 질문이 가리킨 계약의 이력이라고 말하게 된다.

    그래서 gold는 EventHistory 1건을 요구하지 않는다. 요구하면 "하나를 골라라"가 정답이
    되어 위 판단이 뒤집힌다. 원공시 후보가 정확히 몇 건인지(실측 12건)도 여기 고정하지
    않는다 — 코퍼스에 같은 유형의 공시가 더 들어와도 판정(둘 이상이면 모호)은 바뀌지 않고,
    그 개수를 gold에 박아 두면 코퍼스가 늘 때마다 회귀가 깨진다.
    """
    loaded = load_dataset()
    questions = {question.case_id: question for question in loaded.questions}
    gold = {item.case_id: item for item in loaded.gold_answers}["q14"]

    assert questions["q14"].lifecycle == "active"
    assert gold.execution_status is ExecutionStatus.COMPLETED
    assert gold.status is AnswerabilityStatus.UNRESOLVED
    assert gold.reason is AnswerabilityReason.AMBIGUOUS_MATCH
    assert gold.answerability_detail_reason == "ambiguous_root"
    assert gold.counts.model_dump(exclude_none=True) == {
        "facts": 0,
        "evidence": 0,
        "events": 0,
        "event_links": 0,
        "event_histories": 0,
        "resolved_event_states": 0,
        "calculations": 0,
        "comparisons": 0,
        "narrative_comparisons": 0,
        "document_summaries": 0,
        "warnings": 0,
        "conversion_gaps": 1,
    }
    assert gold.min_evidence_count == 0
    assert gold.event_history is None


def q14_prediction(**overrides: object) -> PredictionCase:
    """
    Actual-shaped Q14 prediction: 12 root candidates, none chosen.

    root doc_id는 실측값을 쓰지 않는다. 이 자리의 목적은 "무엇이 담기는가"(개수 0, gap 1)를
    확인하는 것이지 "정확히 어느 문서들인가"가 아니다. 실측 12건을 여기 박으면 회귀가 코퍼스
    스냅샷에 묶인다.
    """
    values: dict[str, object] = {
        "case_id": "q14",
        "execution_status": "COMPLETED",
        "answerability_status": "UNRESOLVED",
        "answerability_reason": "ambiguous_match",
        "answerability_detail": {
            "stage": "filing_discovery",
            "reason": "ambiguous_root",
            "detail": "조건에 맞는 원공시가 12건이라 어느 건의 이력인지 특정할 수 없습니다.",
        },
        "conversion_gaps": [
            {
                "kind": "root_candidate_doc_ids",
                "subject": "LG에너지솔루션",
                "dropped": {
                    "root_candidate_doc_ids": [
                        "exchange_synthetic_root_1",
                        "exchange_synthetic_root_2",
                    ]
                },
                "detail": "질문이 가리키는 원공시 후보를 담을 자리가 없습니다.",
            }
        ],
    }
    values.update(overrides)
    return PredictionCase.model_validate(values)


def test_q14_actual_shaped_prediction_passes_the_comparator() -> None:
    """The exact shape production returns today must be a clean pass."""
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q14"]

    assert compare_prediction(gold, q14_prediction()) == []


def test_q14_rejects_answerable_status() -> None:
    """A prediction that claims to have resolved the question must fail."""
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q14"]
    actual = q14_prediction(answerability_status="ANSWERABLE").model_dump(mode="python")

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("answerability_status:") for item in failures)


@pytest.mark.parametrize("wrong_reason", ["ambiguous_link", "not_found"])
def test_q14_rejects_wrong_canonical_reason(wrong_reason: str) -> None:
    """
    A different canonical reason must fail, even one from the same family.

    AMBIGUOUS_LINK is not interchangeable with AMBIGUOUS_MATCH (open_decisions.md D8):
    one means the root itself is unresolved, the other means a resolved root's
    correction/link chain is unresolved. Q14 never reaches link resolution.
    """
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q14"]
    actual = q14_prediction(answerability_reason=wrong_reason).model_dump(mode="python")

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("answerability_reason:") for item in failures)


def test_q14_rejects_unverified_counterparty_as_the_blocking_reason() -> None:
    """
    unverified_counterparty is a real, adjacent Q14 outcome, but not this one.

    It only applies when exactly one root candidate exists and the question named a
    counterparty (C3 still missing). Today's actual corpus has 12 candidates, so the
    blocking reason must be ambiguous_root, not unverified_counterparty. A prediction
    that reports the wrong internal reason must fail even though the canonical
    (status, reason) pair is unchanged.
    """
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q14"]
    actual = q14_prediction().model_dump(mode="python")
    actual["answerability_detail"]["reason"] = "unverified_counterparty"

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("answerability_detail.reason:") for item in failures)


@pytest.mark.parametrize(
    "carrier",
    ["facts", "evidence", "events", "event_links", "event_histories", "resolved_event_states"],
)
def test_q14_rejects_any_structured_carrier_that_should_not_exist(carrier: str) -> None:
    """
    No structured carrier is populated when the root itself is unresolved.

    A handler that invented a Fact, an Evidence row, an Event, an EventLink, an
    EventHistory, or a ResolvedEventState off of an unconfirmed root must fail —
    each of those would look like a complete, sourced answer while actually
    resting on a document nobody confirmed was the one the question named.
    """
    minimal_by_carrier: dict[str, dict[str, object]] = {
        "facts": {
            "fact_id": "fact_synthetic_root_1_contract_amount",
            "company": "LG에너지솔루션",
            "metric": "contract_amount",
            "accounting_scope": "not_applicable",
        },
        "evidence": {
            "evidence_id": "evidence_synthetic_root_1_contract_amount",
            "doc_id": "exchange_synthetic_root_1",
            "location_type": "event_field",
        },
        "events": {
            "event_id": "event_synthetic_root_1",
            "event_type": "supply_contract",
            "company": "LG에너지솔루션",
            "source_doc_id": "exchange_synthetic_root_1",
        },
        "event_links": {
            "link_id": "link_synthetic_root_1",
            "source_doc_id": "exchange_synthetic_root_1",
            "relation": "correction_of",
            "raw_reference": {"document_title": "정정신고(안내공시)"},
            "resolution_status": "resolved",
        },
        "event_histories": {
            "event_id": "event_supply_contract_exchange_synthetic_root_1",
            "document_ids": ["exchange_synthetic_root_1"],
            "chain_complete": True,
        },
        "resolved_event_states": {
            "event_id": "event_supply_contract_exchange_synthetic_root_1",
            "event_type": "supply_contract",
            "company": "LG에너지솔루션",
            "as_of": "2024-01-26",
        },
    }
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q14"]
    actual = q14_prediction().model_dump(mode="python")
    actual[carrier] = [minimal_by_carrier[carrier]]

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith(f"counts.{carrier}:") for item in failures)


def test_q14_rejects_a_root_chosen_by_inverse_chain_inference() -> None:
    """
    The exact failure mode D8 forbids: picking a root because it has a chain.

    A handler must not narrow 12 candidates to 1 by observing that only one of them
    has follow-on corrections, then answer that one's EventHistory as if it were the
    contract the question named. That answer would be structurally complete
    (EventHistory + EventLink + Evidence all present, chain_complete=True) and
    indistinguishable in shape from a real Q13/Q15-style answer — which is exactly
    why gold must reject it on count alone, independent of the chain's content.
    """
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q14"]
    actual = q14_prediction(
        answerability_status="ANSWERABLE",
        answerability_reason=None,
        answerability_detail=None,
    ).model_dump(mode="python")
    actual["conversion_gaps"] = []
    actual["event_histories"] = [
        {
            "event_id": "event_supply_contract_exchange_synthetic_root_1",
            "document_ids": [
                "exchange_synthetic_root_1",
                "exchange_synthetic_correction_1",
            ],
            "chain_complete": True,
        }
    ]
    actual["event_links"] = [
        {
            "link_id": "link_synthetic_correction_1",
            "source_doc_id": "exchange_synthetic_correction_1",
            "relation": "correction_of",
            "raw_reference": {"document_title": "정정신고(안내공시)"},
            "resolved_doc_id": "exchange_synthetic_root_1",
            "resolution_status": "resolved",
        }
    ]
    actual["evidence"] = [
        {
            "evidence_id": "evidence_synthetic_root_1_contract_amount",
            "doc_id": "exchange_synthetic_root_1",
            "location_type": "event_field",
        }
    ]

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert failures
    assert any(item.startswith("answerability_status:") for item in failures)
    assert any(item.startswith("counts.event_histories:") for item in failures)


# --------------------------------------------------------------------------
# Q09 — narrative temporal compare, fail-closed on an ambiguous filing (D5)
# --------------------------------------------------------------------------

Q09_FY2023_RESOLVED_DOC = "periodic_20240307000835"
Q09_FY2025_ORIGINAL_DOC = "periodic_20260312001119"
Q09_FY2025_CORRECTION_DOC = "periodic_20260515001658"
Q09_SECTION_PATH = ["II. 사업의 내용", "1. 사업의 개요"]


def test_active_q09_gold_pins_ambiguous_filing_and_one_side_evidence_identity() -> None:
    """
    Q09 질문은 두 회계연도를 묻지만, 실제 코퍼스의 FY2025 사업보고서는 원본과 정정본이 함께
    있고 정기공시 판본 선택 정책(D5, open_decisions.md)이 아직 없다. handler는 그 해를
    하나로 확정하지 않고 filing_discovery 단계에서 ambiguous_filing으로 멈춘다.

    FY2023은 판본이 하나라 정상 확보되고, 그 Evidence만 결과에 남는다. gold는 그 Evidence
    3건이 실제로 FY2023 확정 문서(periodic_20240307000835)에서 나왔는지까지 doc_id로 pin한다
    — count만 pin하면 같은 개수의 FY2025 후보 Evidence가 섞여도 통과하기 때문이다
    (아래 mutation 테스트가 그 실패 모드를 직접 재현한다).

    항목별 짝짓기 규칙 부재로 멈추는 NOT_COMPARABLE(양쪽 Section을 모두 확보한 뒤의 판정)과
    다르다. Q09는 이번 코퍼스 스냅샷에서 그 단계에 도달하지 않는다.
    """
    loaded = load_dataset()
    questions = {question.case_id: question for question in loaded.questions}
    gold = {item.case_id: item for item in loaded.gold_answers}["q09"]

    assert questions["q09"].lifecycle == "active"
    assert questions["q09"].question == (
        "삼성바이오로직스 사업 구조가 2023년이랑 2025년이랑 어떻게 달라졌어?"
    )
    assert gold.execution_status is ExecutionStatus.COMPLETED
    assert gold.status is AnswerabilityStatus.UNRESOLVED
    assert gold.reason is AnswerabilityReason.AMBIGUOUS_MATCH
    assert gold.answerability_detail_reason == "ambiguous_filing"
    assert gold.answerability_detail_stage == "filing_discovery"
    assert gold.counts.model_dump(exclude_none=True) == {
        "facts": 0,
        "evidence": 3,
        "events": 0,
        "event_links": 0,
        "event_histories": 0,
        "resolved_event_states": 0,
        "calculations": 0,
        "comparisons": 0,
        "narrative_comparisons": 0,
        "document_summaries": 0,
        "warnings": 1,
        "conversion_gaps": 0,
    }
    assert gold.min_evidence_count == 3
    assert gold.evidence == [
        runner.ExpectedEvidence(doc_id=Q09_FY2023_RESOLVED_DOC, section_path=Q09_SECTION_PATH)
    ] * 3


def _q09_evidence_row(doc_id: str, index: int) -> dict[str, object]:
    return {
        "evidence_id": f"evidence_{doc_id}_row_{index}",
        "doc_id": doc_id,
        "location_type": "text",
        "section_path": Q09_SECTION_PATH,
    }


def q09_prediction(**overrides: object) -> PredictionCase:
    """
    Actual-shaped Q09 prediction: FY2023 resolved, FY2025 ambiguous (original +
    correction), only FY2023's three Evidence rows retained.
    """
    values: dict[str, object] = {
        "case_id": "q09",
        "execution_status": "COMPLETED",
        "answerability_status": "UNRESOLVED",
        "answerability_reason": "ambiguous_match",
        "answerability_detail": {
            "stage": "filing_discovery",
            "reason": "ambiguous_filing",
            "detail": (
                "2025 회계연도의 조건에 맞는 공시(정정본 포함)가 2건입니다: "
                f"{Q09_FY2025_ORIGINAL_DOC}, {Q09_FY2025_CORRECTION_DOC}. "
                "판본 선택 정책이 정해지지 않아 비교 대상 문서를 하나 골라 답하지 않습니다."
            ),
        },
        "evidence": [_q09_evidence_row(Q09_FY2023_RESOLVED_DOC, i) for i in range(3)],
        "warnings": [
            "질문이 2개 기간(2023, 2025)을 함께 묻고 있어 시계열 비교로 해석했습니다."
        ],
    }
    values.update(overrides)
    return PredictionCase.model_validate(values)


def test_q09_actual_shaped_prediction_passes_the_comparator() -> None:
    """The exact shape production returns today against the actual corpus must pass."""
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q09"]

    assert compare_prediction(gold, q09_prediction()) == []


def test_q09_rejects_answerable_status() -> None:
    """A prediction that claims to have picked a FY2025 version must fail."""
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q09"]
    actual = q09_prediction(
        answerability_status="ANSWERABLE",
        answerability_reason=None,
        answerability_detail=None,
    ).model_dump(mode="python")

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("answerability_status:") for item in failures)


@pytest.mark.parametrize("wrong_reason", ["not_found", "not_comparable"])
def test_q09_rejects_wrong_canonical_reason(wrong_reason: str) -> None:
    """
    not_found would claim no FY2025 filing exists at all (there are two); not_comparable
    would claim both years' Sections were secured and found unpairable — a judgment this
    corpus snapshot never reaches because FY2025 never resolves to one filing.
    """
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q09"]
    actual = q09_prediction(answerability_reason=wrong_reason).model_dump(mode="python")

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("answerability_reason:") for item in failures)


def test_q09_rejects_incomparable_narrative_sections_as_the_blocking_reason() -> None:
    """
    incomparable_narrative_sections is a real, adjacent Q09 outcome (both Sections
    secured, no pairing rule) but not this one — this corpus snapshot stops earlier,
    on the FY2025 filing choice itself. The canonical (status, reason) pair alone
    cannot tell the two apart; answerability_detail.reason must.
    """
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q09"]
    actual = q09_prediction().model_dump(mode="python")
    actual["answerability_detail"]["reason"] = "incomparable_narrative_sections"

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("answerability_detail.reason:") for item in failures)


def test_q09_rejects_a_regressed_stage_with_the_same_reason_and_shape() -> None:
    """
    The false positive this contract closes: canonical reason stays ambiguous_match,
    answerability_detail.reason stays ambiguous_filing, every collection count and the
    retained FY2023 Evidence stay exactly as production returns them today — only
    answerability_detail.stage regresses to a different stage. Reason alone cannot see
    this: filing_discovery and a hypothetical later stage can both carry a reason
    string named "ambiguous_filing" while meaning a different diagnosis. Stage identity
    must be pinned independently.
    """
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q09"]
    actual = q09_prediction().model_dump(mode="python")
    actual["answerability_detail"]["stage"] = "comparability"

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("answerability_detail.stage:") for item in failures)


def test_q09_rejects_a_missing_answerability_detail_when_gold_pins_stage() -> None:
    """A prediction with no answerability_detail at all cannot satisfy a pinned stage."""
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q09"]
    actual = q09_prediction(answerability_detail=None).model_dump(mode="python")

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("answerability_detail.stage:") for item in failures)


@pytest.mark.parametrize("evidence_count", [0, 1, 2, 5])
def test_q09_rejects_wrong_evidence_count(evidence_count: int) -> None:
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q09"]
    actual = q09_prediction(
        evidence=[
            _q09_evidence_row(Q09_FY2023_RESOLVED_DOC, i) for i in range(evidence_count)
        ]
    ).model_dump(mode="python")

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("counts.evidence:") for item in failures)


@pytest.mark.parametrize(
    "wrong_doc_id",
    [Q09_FY2025_ORIGINAL_DOC, Q09_FY2025_CORRECTION_DOC, "periodic_99999999999999"],
)
def test_q09_rejects_evidence_from_an_unresolved_or_unrelated_document(
    wrong_doc_id: str,
) -> None:
    """
    The count-only false positive this contract closes: three Evidence rows total, same
    as the gold's min_evidence_count and counts.evidence, but one of them actually came
    from a FY2025 candidate the handler never resolved (or an unrelated document
    entirely). A count-only gold cannot see this; doc_id identity must catch it.
    """
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q09"]
    actual = q09_prediction(
        evidence=[
            _q09_evidence_row(Q09_FY2023_RESOLVED_DOC, 0),
            _q09_evidence_row(Q09_FY2023_RESOLVED_DOC, 1),
            _q09_evidence_row(wrong_doc_id, 2),
        ]
    ).model_dump(mode="python")

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("evidence:") for item in failures)


def test_q09_rejects_evidence_entirely_from_the_wrong_document() -> None:
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q09"]
    actual = q09_prediction(
        evidence=[
            _q09_evidence_row("periodic_99999999999999", i) for i in range(3)
        ]
    ).model_dump(mode="python")

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("evidence:") for item in failures)


def test_q09_rejects_a_fabricated_narrative_comparison() -> None:
    """
    No NarrativeComparisonResult exists in this corpus snapshot's actual path (both
    Sections are never secured together). A handler that invented one — even an empty
    claims=[] one — would look like the pairing rule this contract explicitly does not
    have.
    """
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q09"]
    actual = q09_prediction().model_dump(mode="python")
    actual["narrative_comparisons"] = [
        {
            "comparison_id": "narrative_synthetic_1",
            "company": "삼성바이오로직스",
            "section_scope": "business_overview",
            "left_fiscal_year": 2023,
            "right_fiscal_year": 2025,
            "left_section_path": Q09_SECTION_PATH,
            "right_section_path": Q09_SECTION_PATH,
            "claims": [],
        }
    ]

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith("counts.narrative_comparisons:") for item in failures)


@pytest.mark.parametrize("carrier", ["document_summaries", "calculations", "comparisons"])
def test_q09_rejects_any_carrier_actual_production_never_populates(carrier: str) -> None:
    """
    The narrative path never builds a DocumentSummary (it is discarded so Pattern B's
    single-document summary path does not receive two), a Calculation, or a Comparison
    — Q09 never crosses the calculation guard. Any of those appearing means a different
    code path answered, not this one.
    """
    minimal_by_carrier: dict[str, dict[str, object]] = {
        "document_summaries": {
            "summary_id": "summary_synthetic_1",
            "doc_id": Q09_FY2023_RESOLVED_DOC,
            "company": "삼성바이오로직스",
            "report_name": "사업보고서",
            "section_scope": "business_overview",
            "section_path": Q09_SECTION_PATH,
            "items": [
                {
                    "item_id": "item_synthetic_1",
                    "item_type": "paragraph",
                    "label": "합성 항목",
                    "evidence_ids": ["evidence_synthetic_1"],
                }
            ],
        },
        "calculations": {
            "calculation_id": "calculation_synthetic_1",
            "operation": "delta",
            "inputs": [{"input_type": "fact", "input_id": "fact_synthetic_1"}],
            "value": 1,
            "unit": "원",
        },
        "comparisons": {
            "comparison_id": "comparison_synthetic_1",
            "left": {"input_type": "fact", "input_id": "fact_synthetic_1"},
            "right": {"input_type": "fact", "input_id": "fact_synthetic_2"},
            "relation": "left_greater",
        },
    }
    gold = {item.case_id: item for item in load_dataset().gold_answers}["q09"]
    actual = q09_prediction().model_dump(mode="python")
    actual[carrier] = [minimal_by_carrier[carrier]]

    failures = compare_prediction(gold, PredictionCase.model_validate(actual))

    assert any(item.startswith(f"counts.{carrier}:") for item in failures)


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


def event_history_prediction(
    *,
    after_evidence_id: str = "evidence-doc-2",
    off_chain_evidence: bool = False,
    link_source_doc_id: str = "doc-2",
    link_resolved_doc_id: str = "doc-1",
) -> PredictionCase:
    evidence = [
        {
            "evidence_id": "evidence-doc-1",
            "doc_id": "doc-1",
            "location_type": "event_field",
        },
        {
            "evidence_id": "evidence-doc-2",
            "doc_id": "doc-2",
            "location_type": "event_field",
        },
    ]
    if off_chain_evidence:
        evidence.append(
            {
                "evidence_id": "evidence-off-chain",
                "doc_id": "doc-off-chain",
                "location_type": "event_field",
            }
        )

    return prediction(
        "case",
        facts=[],
        event_links=[
            {
                "link_id": "link-1",
                "source_doc_id": link_source_doc_id,
                "relation": "correction_of",
                "raw_reference": {},
                "resolved_doc_id": link_resolved_doc_id,
                "resolution_status": "resolved",
            }
        ],
        event_histories=[
            {
                "event_id": "event-1",
                "document_ids": ["doc-1", "doc-2"],
                "changes": [
                    {
                        "field_name": "investment_amount",
                        "before": "100",
                        "after": "110",
                        "before_evidence_id": "evidence-doc-2",
                        "after_evidence_id": after_evidence_id,
                    }
                ],
                "chain_complete": True,
            }
        ],
        evidence=evidence,
    )


def test_event_history_structure_and_evidence_integrity_pass() -> None:
    gold = answerable_gold(
        "case",
        facts=None,
        event_history={
            "link_resolution": "resolved",
            "chain_complete": True,
            "document_count": 2,
            "change_count": 1,
        },
        integrity={"event_history_evidence_references": True},
    )

    assert compare_prediction(gold, event_history_prediction()) == []


def test_event_history_structure_mismatch_and_dangling_evidence_fail() -> None:
    gold = answerable_gold(
        "case",
        facts=None,
        event_history={
            "link_resolution": "resolved",
            "chain_complete": True,
            "document_count": 3,
            "change_count": 2,
        },
        integrity={"event_history_evidence_references": True},
    )

    failures = compare_prediction(
        gold, event_history_prediction(after_evidence_id="missing-evidence")
    )

    assert "event_history.document_count: expected 3, got [2]" in failures
    assert "event_history.change_count: expected 2, got [1]" in failures
    assert (
        "integrity.event_history_evidence_references: expected True, got False"
        in failures
    )


def test_event_history_change_evidence_from_an_unrelated_document_fails() -> None:
    gold = answerable_gold(
        "case",
        facts=None,
        integrity={"event_history_evidence_references": True},
    )

    failures = compare_prediction(
        gold,
        event_history_prediction(
            after_evidence_id="evidence-off-chain", off_chain_evidence=True
        ),
    )

    assert failures == [
        "integrity.event_history_evidence_references: expected True, got False"
    ]


def test_event_history_unreferenced_unrelated_evidence_is_allowed() -> None:
    gold = answerable_gold(
        "case",
        facts=None,
        integrity={"event_history_evidence_references": True},
    )

    assert compare_prediction(
        gold, event_history_prediction(off_chain_evidence=True)
    ) == []


@pytest.mark.parametrize(
    ("source_doc_id", "resolved_doc_id"),
    [("doc-off-chain", "doc-1"), ("doc-2", "doc-off-chain")],
)
def test_event_history_link_with_an_off_chain_document_fails(
    source_doc_id: str, resolved_doc_id: str
) -> None:
    gold = answerable_gold(
        "case",
        facts=None,
        integrity={"event_history_evidence_references": True},
    )

    failures = compare_prediction(
        gold,
        event_history_prediction(
            link_source_doc_id=source_doc_id,
            link_resolved_doc_id=resolved_doc_id,
        ),
    )

    assert failures == [
        "integrity.event_history_evidence_references: expected True, got False"
    ]


def test_event_history_links_are_checked_against_their_own_history() -> None:
    actual = event_history_prediction()
    actual.event_histories.append(
        actual.event_histories[0].model_copy(
            update={
                "event_id": "event-2",
                "document_ids": ["doc-3", "doc-4"],
                "changes": [],
            }
        )
    )
    actual.evidence.extend(
        [
            actual.evidence[0].model_copy(
                update={"evidence_id": "evidence-doc-3", "doc_id": "doc-3"}
            ),
            actual.evidence[0].model_copy(
                update={"evidence_id": "evidence-doc-4", "doc_id": "doc-4"}
            ),
        ]
    )
    actual.event_links.append(
        actual.event_links[0].model_copy(
            update={
                "link_id": "link-2",
                "source_doc_id": "doc-4",
                "resolved_doc_id": "doc-3",
            }
        )
    )
    gold = answerable_gold(
        "case",
        facts=None,
        integrity={"event_history_evidence_references": True},
    )

    assert compare_prediction(gold, actual) == []


def q15_like_prediction(*, event_link_count: int) -> PredictionCase:
    """
    Build a Q15-shaped facility-investment-history prediction: a fixed 4-document
    chain (root + 3 corrections) with 4 FieldChanges, and a configurable EventLink
    count.

    The document/FieldChange shape never changes with `event_link_count`, so tests
    using this fixture can vary EventLink cardinality alone and show that
    `semantic_count` tracks `event_links`, not `document_count` or FieldChange count.
    This is a generic fixture builder, not case_id-specific runner logic.
    """
    document_ids = [f"doc-{index}" for index in range(1, 5)]
    evidence = [
        {"evidence_id": f"evidence-{doc_id}", "doc_id": doc_id, "location_type": "event_field"}
        for doc_id in document_ids
    ]
    changes = [
        {
            "field_name": "investment_amount",
            "before": "100",
            "after": "110",
            "before_evidence_id": "evidence-doc-1",
            "after_evidence_id": "evidence-doc-2",
        },
        {
            "field_name": "investment_end_date",
            "before": "2025-12-15",
            "after": "2026-03-31",
            "before_evidence_id": "evidence-doc-2",
            "after_evidence_id": "evidence-doc-2",
        },
        {
            "field_name": "investment_amount",
            "before": "110",
            "after": "120",
            "before_evidence_id": "evidence-doc-2",
            "after_evidence_id": "evidence-doc-3",
        },
        {
            "field_name": "investment_amount",
            "before": "120",
            "after": "130",
            "before_evidence_id": "evidence-doc-3",
            "after_evidence_id": "evidence-doc-4",
        },
    ]
    chain_links = [
        {
            "link_id": "link-1",
            "source_doc_id": "doc-2",
            "relation": "correction_of",
            "raw_reference": {},
            "resolved_doc_id": "doc-1",
            "resolution_status": "resolved",
        },
        {
            "link_id": "link-2",
            "source_doc_id": "doc-3",
            "relation": "correction_of",
            "raw_reference": {},
            "resolved_doc_id": "doc-2",
            "resolution_status": "resolved",
        },
        {
            "link_id": "link-3",
            "source_doc_id": "doc-4",
            "relation": "correction_of",
            "raw_reference": {},
            "resolved_doc_id": "doc-3",
            "resolution_status": "resolved",
        },
    ]
    if event_link_count <= len(chain_links):
        links = chain_links[:event_link_count]
    else:
        links = [
            *chain_links,
            *(
                {**chain_links[-1], "link_id": f"link-extra-{index}"}
                for index in range(event_link_count - len(chain_links))
            ),
        ]

    return prediction(
        "q15_like",
        facts=[],
        event_links=links,
        event_histories=[
            {
                "event_id": "event-1",
                "document_ids": document_ids,
                "changes": changes,
                "chain_complete": True,
            }
        ],
        evidence=evidence,
    )


def test_active_q15_gold_accepts_a_matching_prediction() -> None:
    """
    A prediction shaped like the real Q15 corpus result (4-document chain, 3
    EventLinks, 4 FieldChanges) passes the real q15 gold record, including the new
    semantic_count contract, with no Q15-specific runner branch.
    """
    q15_gold = {gold.case_id: gold for gold in load_dataset().gold_answers}["q15"]

    assert compare_prediction(q15_gold, q15_like_prediction(event_link_count=3)) == []


def test_semantic_count_event_links_pass_regardless_of_field_change_count() -> None:
    """
    The gold's change_count (4) and semantic_count.value (3) are different numbers
    checked by different mechanisms; both hold at once for the same prediction. This
    is the contract test that "몇 번 바뀌었어" is answered by EventLink cardinality,
    not FieldChange cardinality, even though FieldChange count is also 4 here.
    """
    gold = answerable_gold(
        "case",
        facts=None,
        event_history={
            "link_resolution": "resolved",
            "chain_complete": True,
            "document_count": 4,
            "change_count": 4,
        },
        semantic_count={"source": "event_links", "value": 3},
    )

    assert compare_prediction(gold, q15_like_prediction(event_link_count=3)) == []


@pytest.mark.parametrize("event_link_count", [2, 4])
def test_semantic_count_fails_when_event_link_cardinality_differs(
    event_link_count: int,
) -> None:
    gold = answerable_gold(
        "case",
        facts=None,
        semantic_count={"source": "event_links", "value": 3},
    )

    failures = compare_prediction(
        gold, q15_like_prediction(event_link_count=event_link_count)
    )

    assert (
        f"semantic_count: expected 3 from event_links, got {event_link_count}"
        in failures
    )


def test_semantic_count_fails_even_when_document_count_still_matches() -> None:
    """
    document_count=4 is the chain length (root + corrections), not the semantic
    answer count. A prediction can get document_count right while getting the
    EventLink cardinality the question actually means wrong, and semantic_count must
    catch that independently of document_count.
    """
    gold = answerable_gold(
        "case",
        facts=None,
        event_history={
            "link_resolution": "resolved",
            "chain_complete": True,
            "document_count": 4,
        },
        semantic_count={"source": "event_links", "value": 3},
    )

    failures = compare_prediction(gold, q15_like_prediction(event_link_count=2))

    assert not any(failure.startswith("event_history.document_count") for failure in failures)
    assert "semantic_count: expected 3 from event_links, got 2" in failures


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


def q11_prediction(**overrides) -> PredictionCase:
    facts = [
        {
            "fact_id": "fact_periodic_20260320000859_revenue_table_0127_r1_c1",
            "company": "에이치디현대중공업(주)",
            "metric": "revenue",
            "fiscal_year": 2025,
            "accounting_scope": "consolidated",
            "normalized_value": "17580633257000",
            "normalized_unit": "원",
            "evidence_ids": ["evidence_periodic_20260320000859_table_0127_r1_c1"],
        },
        {
            "fact_id": "fact_periodic_20260320000859_operating_income_table_0127_r5_c1",
            "company": "에이치디현대중공업(주)",
            "metric": "operating_income",
            "fiscal_year": 2025,
            "accounting_scope": "consolidated",
            "normalized_value": "2037510103000",
            "normalized_unit": "원",
            "evidence_ids": ["evidence_periodic_20260320000859_table_0127_r5_c1"],
        },
        {
            "fact_id": "fact_periodic_20260312001037_revenue_table_0102_r1_c1",
            "company": "삼성중공업(주)",
            "metric": "revenue",
            "fiscal_year": 2025,
            "accounting_scope": "consolidated",
            "normalized_value": "10650010702818",
            "normalized_unit": "원",
            "evidence_ids": ["evidence_periodic_20260312001037_table_0102_r1_c1"],
        },
        {
            "fact_id": "fact_periodic_20260312001037_operating_income_table_0102_r5_c1",
            "company": "삼성중공업(주)",
            "metric": "operating_income",
            "fiscal_year": 2025,
            "accounting_scope": "consolidated",
            "normalized_value": "862210880029",
            "normalized_unit": "원",
            "evidence_ids": ["evidence_periodic_20260312001037_table_0102_r5_c1"],
        },
    ]
    evidence = [
        {
            "evidence_id": "evidence_periodic_20260320000859_table_0127_r1_c1",
            "doc_id": "periodic_20260320000859",
            "location_type": "table",
        },
        {
            "evidence_id": "evidence_periodic_20260320000859_table_0127_r5_c1",
            "doc_id": "periodic_20260320000859",
            "location_type": "table",
        },
        {
            "evidence_id": "evidence_periodic_20260312001037_table_0102_r1_c1",
            "doc_id": "periodic_20260312001037",
            "location_type": "table",
        },
        {
            "evidence_id": "evidence_periodic_20260312001037_table_0102_r5_c1",
            "doc_id": "periodic_20260312001037",
            "location_type": "table",
        },
    ]
    calculations = [
        {
            "calculation_id": "calculation_operating_margin_2025_HD현대중공업",
            "operation": "ratio",
            "inputs": [
                {
                    "input_type": "fact",
                    "input_id": (
                        "fact_periodic_20260320000859_operating_income_table_0127_r5_c1"
                    ),
                },
                {
                    "input_type": "fact",
                    "input_id": "fact_periodic_20260320000859_revenue_table_0127_r1_c1",
                },
            ],
            "value": "11.5895",
            "unit": "percent",
            "evidence_ids": [
                "evidence_periodic_20260320000859_table_0127_r5_c1",
                "evidence_periodic_20260320000859_table_0127_r1_c1",
            ],
        },
        {
            "calculation_id": "calculation_operating_margin_2025_삼성중공업",
            "operation": "ratio",
            "inputs": [
                {
                    "input_type": "fact",
                    "input_id": (
                        "fact_periodic_20260312001037_operating_income_table_0102_r5_c1"
                    ),
                },
                {
                    "input_type": "fact",
                    "input_id": "fact_periodic_20260312001037_revenue_table_0102_r1_c1",
                },
            ],
            "value": "8.0959",
            "unit": "percent",
            "evidence_ids": [
                "evidence_periodic_20260312001037_table_0102_r5_c1",
                "evidence_periodic_20260312001037_table_0102_r1_c1",
            ],
        },
    ]
    comparisons = [
        {
            "comparison_id": "comparison_operating_margin_2025_HD현대중공업_vs_삼성중공업",
            "left": {
                "input_type": "calculation",
                "input_id": "calculation_operating_margin_2025_HD현대중공업",
            },
            "right": {
                "input_type": "calculation",
                "input_id": "calculation_operating_margin_2025_삼성중공업",
            },
            "relation": "left_greater",
        }
    ]
    values = {
        "facts": facts,
        "evidence": evidence,
        "calculations": calculations,
        "comparisons": comparisons,
    }
    values.update(overrides)
    return prediction("q11", **values)


def test_q11_actual_shaped_prediction_passes_the_comparator() -> None:
    q11 = {gold.case_id: gold for gold in load_dataset().gold_answers}["q11"]

    assert compare_prediction(q11, q11_prediction()) == []


def test_q11_rejects_wrong_source_fact_with_internally_consistent_calculation() -> None:
    """
    영업이익 1원 오차의 회귀 방지.

    분모(매출)가 조 단위이므로 원 단위 오차는 %p 4자리 반올림 안에 흡수되어
    Calculation 값(8.0959)이 그대로 재현된다. calculation_values_from_inputs는
    "자기 자신이 참조한 Fact"로부터 재계산하므로 이 mutation을 잡지 못하고,
    실제로 값을 다시 확보했는지는 Fact 자체의 identity/value 고정만이 잡는다.
    """
    q11 = {gold.case_id: gold for gold in load_dataset().gold_answers}["q11"]
    actual = q11_prediction().model_dump(mode="python")
    for fact in actual["facts"]:
        if fact["company"] == "삼성중공업(주)" and fact["metric"] == "operating_income":
            # Deliberately wrong by one won while preserving every reference.
            fact["normalized_value"] = "862210880030"
    # Calculation value is unchanged: rounded to 4dp, off-by-one-won does not move it.
    assert actual["calculations"][1]["value"] == "8.0959"

    failures = compare_prediction(q11, PredictionCase.model_validate(actual))

    assert failures == ["facts: expected fields did not match distinct predicted Facts"]


def test_q11_swapped_calculation_ids_fail_even_when_other_fields_are_unchanged() -> None:
    q11 = {gold.case_id: gold for gold in load_dataset().gold_answers}["q11"]
    actual = q11_prediction().model_dump(mode="python")
    swapped_ids = [
        actual["calculations"][1]["calculation_id"],
        actual["calculations"][0]["calculation_id"],
    ]
    for calculation, calculation_id in zip(actual["calculations"], swapped_ids, strict=True):
        calculation["calculation_id"] = calculation_id

    failures = compare_prediction(q11, PredictionCase.model_validate(actual))

    assert any(item.startswith("calculations:") for item in failures)


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
