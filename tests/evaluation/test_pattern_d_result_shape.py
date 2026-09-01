"""Pattern D 최종 목표 결과 모양이 평가 비교기에서 실제로 판정되는지 검증한다.

무엇을 고정하는가:
    docs/agent_design/evidence_and_answerability.md의 Calculation Cardinality가 정한
    Q10·Q11의 최종 목표 모양을 evaluation/runner.py의 비교기가 판정할 수 있는지 본다.

    - Q10 — Fact 2 · Calculation 0 · Comparison 1.
      Comparison은 원천 Fact 두 건을 가리킨다.
    - Q11 — Fact 4 · Calculation 2 · Comparison 1.
      Calculation은 각 기업의 원천 Fact 두 건을, Comparison은 Calculation 두 건을 가리킨다.

    두 모양 모두 result_input 참조가 결과 밖을 가리키지 않아야 한다(dangling = 0).
    "몇 건인가"만 세면 Comparison이 결과에 없는 값을 가리켜도 통과하므로, 건수와 참조
    무결성을 함께 고정한다.

무엇을 검증하지 않는가:
    evaluation/gold_answers.jsonl의 내용은 보지 않는다. 평가 dataset의 소유권이 이 파일에
    있지 않으므로, gold가 지금 무엇을 선언하고 있는지는 여기서 고정하지 않는다. 여기서
    고정하는 것은 '이 기대를 gold에 적을 수 있고, 적으면 실제로 걸린다'까지다.

    Pattern D handler의 동작도 검증하지 않는다. 여기서 시험하는 것은 비교기이며,
    handler가 실제로 무엇을 만드는지는 tests/integration/test_pattern_d_q10.py와
    test_pattern_d_q11.py가 맡는다.

    실제 공시 수치는 쓰지 않는다. 값은 합성이며 대소 관계만 의미를 가진다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPOSITORY_ROOT / "evaluation" / "runner.py"
RUNNER_SPEC = importlib.util.spec_from_file_location(
    "evaluation_runner_pattern_d", RUNNER_PATH
)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = runner
RUNNER_SPEC.loader.exec_module(runner)

GoldAnswer = runner.GoldAnswer
PredictionCase = runner.PredictionCase
compare_prediction = runner.compare_prediction

# evaluation/questions.jsonl의 Q10·Q11이 묻는 두 기업.
COMPANY_LEFT = "HD현대중공업"
COMPANY_RIGHT = "삼성중공업"

FISCAL_YEAR = 2025


# --------------------------------------------------------------------------
# 합성 결과 조립기
#
# 실제 공시 값을 쓰지 않는다. 아래 숫자는 대소 관계만 의미를 가지는 합성 값이다.
# --------------------------------------------------------------------------


def _evidence(evidence_id: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "doc_id": f"periodic_SYNTHETIC_{evidence_id}",
        "location_type": "table",
    }


def _fact(
    fact_id: str,
    *,
    company: str,
    metric: str,
    evidence_id: str,
    normalized_value: str,
) -> dict:
    return {
        "fact_id": fact_id,
        "company": company,
        "metric": metric,
        "fiscal_year": FISCAL_YEAR,
        "accounting_scope": "consolidated",
        "raw_value": normalized_value,
        "raw_unit": "원",
        "normalized_value": normalized_value,
        "normalized_unit": "원",
        "evidence_ids": [evidence_id],
    }


def _input(input_type: str, input_id: str) -> dict:
    return {"input_type": input_type, "input_id": input_id}


def q10_prediction(**overrides) -> PredictionCase:
    """Q10 최종 목표 모양. Fact 2 · Calculation 0 · Comparison 1."""
    values = {
        "case_id": "q10",
        "execution_status": "COMPLETED",
        "answerability_status": "ANSWERABLE",
        "evidence": [_evidence("evidence-left"), _evidence("evidence-right")],
        "facts": [
            _fact(
                "fact-left-revenue",
                company=COMPANY_LEFT,
                metric="revenue",
                evidence_id="evidence-left",
                normalized_value="200",
            ),
            _fact(
                "fact-right-revenue",
                company=COMPANY_RIGHT,
                metric="revenue",
                evidence_id="evidence-right",
                normalized_value="100",
            ),
        ],
        "calculations": [],
        "comparisons": [
            {
                "comparison_id": "comparison-revenue",
                "left": _input("fact", "fact-left-revenue"),
                "right": _input("fact", "fact-right-revenue"),
                "relation": "left_greater",
            }
        ],
    }
    values.update(overrides)

    return PredictionCase.model_validate(values)


def _margin_calculation(calculation_id: str, *, company_suffix: str, value: str) -> dict:
    return {
        "calculation_id": calculation_id,
        "operation": "ratio",
        "inputs": [
            _input("fact", f"fact-{company_suffix}-operating-income"),
            _input("fact", f"fact-{company_suffix}-revenue"),
        ],
        "value": value,
        "unit": "percent",
        "evidence_ids": [
            f"evidence-{company_suffix}-operating-income",
            f"evidence-{company_suffix}-revenue",
        ],
    }


def q11_prediction(**overrides) -> PredictionCase:
    """Q11 최종 목표 모양. Fact 4 · Calculation 2 · Comparison 1."""
    values = {
        "case_id": "q11",
        "execution_status": "COMPLETED",
        "answerability_status": "ANSWERABLE",
        "evidence": [
            _evidence("evidence-left-revenue"),
            _evidence("evidence-left-operating-income"),
            _evidence("evidence-right-revenue"),
            _evidence("evidence-right-operating-income"),
        ],
        "facts": [
            _fact(
                "fact-left-revenue",
                company=COMPANY_LEFT,
                metric="revenue",
                evidence_id="evidence-left-revenue",
                normalized_value="200",
            ),
            _fact(
                "fact-left-operating-income",
                company=COMPANY_LEFT,
                metric="operating_income",
                evidence_id="evidence-left-operating-income",
                normalized_value="20",
            ),
            _fact(
                "fact-right-revenue",
                company=COMPANY_RIGHT,
                metric="revenue",
                evidence_id="evidence-right-revenue",
                normalized_value="100",
            ),
            _fact(
                "fact-right-operating-income",
                company=COMPANY_RIGHT,
                metric="operating_income",
                evidence_id="evidence-right-operating-income",
                normalized_value="20",
            ),
        ],
        "calculations": [
            _margin_calculation(
                "calculation-left-margin", company_suffix="left", value="10"
            ),
            _margin_calculation(
                "calculation-right-margin", company_suffix="right", value="20"
            ),
        ],
        "comparisons": [
            {
                "comparison_id": "comparison-margin",
                "left": _input("calculation", "calculation-left-margin"),
                "right": _input("calculation", "calculation-right-margin"),
                "relation": "right_greater",
            }
        ],
    }
    values.update(overrides)

    return PredictionCase.model_validate(values)


def q10_gold(**overrides) -> GoldAnswer:
    """Q10 canonical target. counts.comparisons와 result_input_references를 포함한다."""
    values = {
        "case_id": "q10",
        "execution_status": "COMPLETED",
        "status": "ANSWERABLE",
        "reason": None,
        "counts": {"facts": 2, "calculations": 0, "comparisons": 1},
        "facts": [
            {"company": COMPANY_LEFT, "metric": "revenue", "fiscal_year": FISCAL_YEAR},
            {"company": COMPANY_RIGHT, "metric": "revenue", "fiscal_year": FISCAL_YEAR},
        ],
        "min_evidence_count": 2,
        "integrity": {
            "fact_evidence_references": True,
            "result_input_references": True,
        },
    }
    values.update(overrides)

    return GoldAnswer.model_validate(values)


def q11_gold(**overrides) -> GoldAnswer:
    """Q11 canonical target. counts.comparisons와 result_input_references를 포함한다."""
    values = {
        "case_id": "q11",
        "execution_status": "COMPLETED",
        "status": "ANSWERABLE",
        "reason": None,
        "counts": {"facts": 4, "calculations": 2, "comparisons": 1},
        "facts": [
            {"company": COMPANY_LEFT, "metric": "revenue", "fiscal_year": FISCAL_YEAR},
            {
                "company": COMPANY_LEFT,
                "metric": "operating_income",
                "fiscal_year": FISCAL_YEAR,
            },
            {"company": COMPANY_RIGHT, "metric": "revenue", "fiscal_year": FISCAL_YEAR},
            {
                "company": COMPANY_RIGHT,
                "metric": "operating_income",
                "fiscal_year": FISCAL_YEAR,
            },
        ],
        "min_evidence_count": 2,
        "integrity": {
            "fact_evidence_references": True,
            "calculation_evidence_references": True,
            "result_input_references": True,
        },
    }
    values.update(overrides)

    return GoldAnswer.model_validate(values)


# --------------------------------------------------------------------------
# 1. canonical target 모양은 그대로 통과한다
# --------------------------------------------------------------------------


def test_q10_canonical_shape_passes_the_comparator() -> None:
    """Fact 2 · Calculation 0 · Comparison 1이 실패 없이 통과해야 한다."""
    assert compare_prediction(q10_gold(), q10_prediction()) == []


def test_q11_canonical_shape_passes_the_comparator() -> None:
    """Fact 4 · Calculation 2 · Comparison 1이 실패 없이 통과해야 한다."""
    assert compare_prediction(q11_gold(), q11_prediction()) == []


# --------------------------------------------------------------------------
# 2. Comparison 건수는 실제로 걸린다
#
# gold가 comparisons를 선언하지 않으면 Comparison이 하나도 없는 결과도 통과한다.
# 그 차이를 여기서 고정한다.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gold_builder", "prediction_builder"),
    [(q10_gold, q10_prediction), (q11_gold, q11_prediction)],
)
def test_missing_comparison_is_reported_when_the_count_is_declared(
    gold_builder, prediction_builder
) -> None:
    failures = compare_prediction(gold_builder(), prediction_builder(comparisons=[]))

    assert "counts.comparisons: expected 1, got 0" in failures


@pytest.mark.parametrize(
    ("gold_builder", "prediction_builder"),
    [(q10_gold, q10_prediction), (q11_gold, q11_prediction)],
)
def test_undeclared_comparison_count_lets_a_missing_comparison_pass(
    gold_builder, prediction_builder
) -> None:
    """
    이것이 comparisons를 gold에 적어야 하는 이유다.

    비교형 질문인데 ComparisonResult가 없는 결과가, 건수를 선언하지 않으면 그대로 통과한다.
    """
    gold = gold_builder(counts={"facts": None})

    assert compare_prediction(gold, prediction_builder(comparisons=[])) == []


# --------------------------------------------------------------------------
# 3. result_input 무결성 — dangling = 0
#
# Comparison이 무엇을 가리키는가는 건수로 드러나지 않는다. 결과 밖을 가리켜도 개수는 같다.
# --------------------------------------------------------------------------


def test_q10_comparison_referencing_an_absent_fact_is_reported() -> None:
    """Q10의 Comparison은 결과 안의 Fact를 가리켜야 한다."""
    broken = q10_prediction(
        comparisons=[
            {
                "comparison_id": "comparison-revenue",
                "left": _input("fact", "fact-left-revenue"),
                "right": _input("fact", "fact-that-is-not-here"),
                "relation": "left_greater",
            }
        ]
    )

    failures = compare_prediction(q10_gold(), broken)

    assert any("fact:fact-that-is-not-here" in item for item in failures)
    assert any(item.startswith("integrity.result_input_references") for item in failures)


def test_q11_comparison_referencing_an_absent_calculation_is_reported() -> None:
    """Q11의 Comparison은 결과 안의 Calculation을 가리켜야 한다."""
    broken = q11_prediction(
        comparisons=[
            {
                "comparison_id": "comparison-margin",
                "left": _input("calculation", "calculation-left-margin"),
                "right": _input("calculation", "calculation-that-is-not-here"),
                "relation": "right_greater",
            }
        ]
    )

    failures = compare_prediction(q11_gold(), broken)

    assert any("calculation:calculation-that-is-not-here" in item for item in failures)


def test_q11_calculation_referencing_an_absent_fact_is_reported() -> None:
    """Q11의 Calculation은 결과 안의 원천 Fact를 가리켜야 한다."""
    broken = q11_prediction(
        calculations=[
            {
                "calculation_id": "calculation-left-margin",
                "operation": "ratio",
                "inputs": [_input("fact", "fact-that-is-not-here")],
                "value": "10",
                "unit": "percent",
                "evidence_ids": ["evidence-left-revenue"],
            },
            _margin_calculation(
                "calculation-right-margin", company_suffix="right", value="20"
            ),
        ]
    )

    failures = compare_prediction(q11_gold(), broken)

    assert any("fact:fact-that-is-not-here" in item for item in failures)


@pytest.mark.parametrize(
    ("gold_builder", "prediction_builder"),
    [(q10_gold, q10_prediction), (q11_gold, q11_prediction)],
)
def test_dangling_result_inputs_are_zero_in_the_canonical_shape(
    gold_builder, prediction_builder
) -> None:
    """canonical 모양에는 결과 밖을 가리키는 입력이 하나도 없어야 한다."""
    assert runner._dangling_result_input_references(prediction_builder()) == []

    assert not [
        item
        for item in compare_prediction(gold_builder(), prediction_builder())
        if item.startswith("integrity.result_input_references")
    ]


def test_undeclared_result_input_integrity_lets_a_dangling_reference_pass() -> None:
    """
    이것이 result_input_references를 gold에 적어야 하는 이유다.

    건수만 맞으면 Comparison이 결과 밖을 가리켜도 통과한다.
    """
    broken = q10_prediction(
        comparisons=[
            {
                "comparison_id": "comparison-revenue",
                "left": _input("fact", "fact-left-revenue"),
                "right": _input("fact", "fact-that-is-not-here"),
                "relation": "left_greater",
            }
        ]
    )

    gold = q10_gold(integrity={"fact_evidence_references": True})

    assert compare_prediction(gold, broken) == []
