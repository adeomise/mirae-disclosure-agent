"""corpus 회귀 도구가 Pattern D 결과 모양을 실제로 판정하는지 검증한다.

무엇을 고정하는가:
    scripts/run_corpus_regression.py의 `comparisons` 건수 검사와 `result_input_linkage`
    참조 검사를 Q10·Q11의 최종 목표 모양으로 시험한다.

    - Q10 — Fact 2 · Calculation 0 · Comparison 1. Comparison은 Fact 두 건을 가리킨다.
    - Q11 — Fact 4 · Calculation 2 · Comparison 1. Calculation은 Fact를,
      Comparison은 Calculation 두 건을 가리킨다.

    두 모양 모두 결과 밖을 가리키는 입력이 없어야 한다(dangling = 0).

왜 필요한가:
    `evidence_linkage`는 "값의 근거가 어디인가"를 보고 `result_input_linkage`는 "이 값이
    무엇에서 나왔는가"를 본다. Pattern D는 ComparisonResult를 처음으로 만드는 Pattern이고,
    ComparisonResult에는 evidence_ids가 없다. 그래서 Comparison의 정합을 보는 검사는
    `result_input_linkage` 하나뿐이며, 이 검사가 고장 나면 비교 결과가 결과 밖의 값을
    가리켜도 회귀가 PASS로 나온다.

무엇을 검증하지 않는가:
    코퍼스를 읽지 않는다. 기대 대조는 순수 함수이므로 코퍼스 없이 전부 시험할 수 있다.
    Pattern D handler의 동작도 검증하지 않는다. 여기서 시험하는 것은 회귀 도구이며,
    handler가 실제로 무엇을 만드는지는 tests/integration/test_pattern_d_q10.py와
    test_pattern_d_q11.py가 맡는다.

    실제 공시 수치·접수번호는 쓰지 않는다. 합성 식별자와 합성 값만 사용한다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from disclosure_agent.common.enums import (
    AnswerabilityStatus,
    ComparisonRelation,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    CalculationResult,
    ComparisonResult,
    Evidence,
    Fact,
    ResultInputReference,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "run_corpus_regression.py"


def _load_script() -> ModuleType:
    """스크립트를 경로로 직접 읽어 모듈로 만든다. scripts/를 import 경로에 넣지 않는다."""
    name = "run_corpus_regression_pattern_d"

    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)

    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)

    sys.modules[name] = module

    spec.loader.exec_module(module)

    return module


harness = _load_script()

COMPANY_LEFT = "테스트반도체(주)"
COMPANY_RIGHT = "테스트전자"

FISCAL_YEAR = 2025


# --------------------------------------------------------------------------
# 합성 결과 조립기
#
# 아래 숫자는 대소 관계만 의미를 가지는 합성 값이다.
# --------------------------------------------------------------------------


def _evidence(evidence_id: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        doc_id=f"periodic_SYNTHETIC_{evidence_id}",
        location_type=EvidenceLocationType.TABLE,
    )


def _fact(
    fact_id: str,
    *,
    company: str,
    metric: MetricConcept,
    evidence_id: str,
    value: str,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        company=company,
        metric=metric,
        fiscal_year=FISCAL_YEAR,
        accounting_scope=FactAccountingScope.CONSOLIDATED,
        raw_value=value,
        raw_unit="원",
        normalized_value=value,
        normalized_unit="원",
        evidence_ids=[evidence_id],
    )


def _fact_input(fact_id: str) -> ResultInputReference:
    return ResultInputReference(input_type=ResultInputType.FACT, input_id=fact_id)


def _calculation_input(calculation_id: str) -> ResultInputReference:
    return ResultInputReference(
        input_type=ResultInputType.CALCULATION, input_id=calculation_id
    )


def q10_result() -> AgentExecutionResult:
    """Q10 최종 목표 모양. Fact 2 · Calculation 0 · Comparison 1."""
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        evidence=[_evidence("evidence_left"), _evidence("evidence_right")],
        facts=[
            _fact(
                "fact_left_revenue",
                company=COMPANY_LEFT,
                metric=MetricConcept.REVENUE,
                evidence_id="evidence_left",
                value="200",
            ),
            _fact(
                "fact_right_revenue",
                company=COMPANY_RIGHT,
                metric=MetricConcept.REVENUE,
                evidence_id="evidence_right",
                value="100",
            ),
        ],
        calculations=[],
        comparisons=[
            ComparisonResult(
                comparison_id="comparison_revenue",
                left=_fact_input("fact_left_revenue"),
                right=_fact_input("fact_right_revenue"),
                relation=ComparisonRelation.LEFT_GREATER,
            )
        ],
    )


def _margin(calculation_id: str, *, suffix: str, value: str) -> CalculationResult:
    return CalculationResult(
        calculation_id=calculation_id,
        operation="ratio",
        inputs=[
            _fact_input(f"fact_{suffix}_operating_income"),
            _fact_input(f"fact_{suffix}_revenue"),
        ],
        value=value,
        unit="percent",
        evidence_ids=[
            f"evidence_{suffix}_operating_income",
            f"evidence_{suffix}_revenue",
        ],
    )


def q11_result() -> AgentExecutionResult:
    """Q11 최종 목표 모양. Fact 4 · Calculation 2 · Comparison 1."""
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        evidence=[
            _evidence("evidence_left_revenue"),
            _evidence("evidence_left_operating_income"),
            _evidence("evidence_right_revenue"),
            _evidence("evidence_right_operating_income"),
        ],
        facts=[
            _fact(
                "fact_left_revenue",
                company=COMPANY_LEFT,
                metric=MetricConcept.REVENUE,
                evidence_id="evidence_left_revenue",
                value="200",
            ),
            _fact(
                "fact_left_operating_income",
                company=COMPANY_LEFT,
                metric=MetricConcept.OPERATING_INCOME,
                evidence_id="evidence_left_operating_income",
                value="20",
            ),
            _fact(
                "fact_right_revenue",
                company=COMPANY_RIGHT,
                metric=MetricConcept.REVENUE,
                evidence_id="evidence_right_revenue",
                value="100",
            ),
            _fact(
                "fact_right_operating_income",
                company=COMPANY_RIGHT,
                metric=MetricConcept.OPERATING_INCOME,
                evidence_id="evidence_right_operating_income",
                value="20",
            ),
        ],
        calculations=[
            _margin("calculation_left_margin", suffix="left", value="10"),
            _margin("calculation_right_margin", suffix="right", value="20"),
        ],
        comparisons=[
            ComparisonResult(
                comparison_id="comparison_margin",
                left=_calculation_input("calculation_left_margin"),
                right=_calculation_input("calculation_right_margin"),
                relation=ComparisonRelation.RIGHT_GREATER,
            )
        ],
    )


Q10_EXPECT: dict[str, object] = {
    "execution_status": "COMPLETED",
    "answerability_status": "ANSWERABLE",
    "answerability_reason": None,
    "facts": 2,
    "fact_accounting_scope": "consolidated",
    "min_evidence": 2,
    "calculations": 0,
    "comparisons": 1,
    "evidence_linkage": True,
    "result_input_linkage": True,
}

Q11_EXPECT: dict[str, object] = {
    "execution_status": "COMPLETED",
    "answerability_status": "ANSWERABLE",
    "answerability_reason": None,
    "facts": 4,
    "fact_accounting_scope": "consolidated",
    "min_evidence": 2,
    "calculations": 2,
    "calculation_units": ["percent", "percent"],
    "comparisons": 1,
    "evidence_linkage": True,
    "result_input_linkage": True,
}


# --------------------------------------------------------------------------
# 1. canonical target 모양은 그대로 통과한다
# --------------------------------------------------------------------------


def test_q10_shape_passes_every_expectation() -> None:
    assert harness.check_expectations(Q10_EXPECT, q10_result()) == []


def test_q11_shape_passes_every_expectation() -> None:
    assert harness.check_expectations(Q11_EXPECT, q11_result()) == []


@pytest.mark.parametrize(
    ("expect", "builder"), [(Q10_EXPECT, q10_result), (Q11_EXPECT, q11_result)]
)
def test_both_shapes_are_expressible_in_the_fixture(
    expect: dict[str, object], builder, tmp_path: Path
) -> None:
    """
    기대 key가 fixture에서 거절되지 않아야 한다.

    목록에 없는 key는 설정 오류로 막히므로, 회귀 케이스를 추가하기 전에 여기서 걸린다.
    """
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps(
            {"id": "SYNTH-D", "question": "합성 질문", "expect": expect},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cases = harness.load_cases(path)

    assert len(cases) == 1
    assert cases[0].expect == expect


# --------------------------------------------------------------------------
# 2. Comparison 건수
# --------------------------------------------------------------------------


def test_missing_comparison_is_reported() -> None:
    """비교형 질문인데 ComparisonResult가 없으면 걸려야 한다."""
    result = q10_result()
    result.comparisons = []

    failures = harness.check_expectations({"comparisons": 1}, result)

    assert failures and "comparisons" in failures[0]


def test_extra_comparison_is_reported() -> None:
    """두 기업을 견주는 질문의 판정은 한 건이다."""
    result = q10_result()
    result.comparisons = [*result.comparisons, *result.comparisons]

    assert harness.check_expectations({"comparisons": 1}, result)


# --------------------------------------------------------------------------
# 3. result_input_linkage — dangling = 0
#
# ComparisonResult에는 evidence_ids가 없다. Comparison의 정합을 보는 검사는 이것 하나다.
# --------------------------------------------------------------------------


def test_q10_comparison_referencing_an_absent_fact_is_detected() -> None:
    result = q10_result()
    result.comparisons[0].right = _fact_input("fact_that_is_not_here")

    failures = harness.check_expectations({"result_input_linkage": True}, result)

    assert failures
    assert "comparison_revenue" in failures[0]
    assert "fact:fact_that_is_not_here" in failures[0]


def test_q11_comparison_referencing_an_absent_calculation_is_detected() -> None:
    result = q11_result()
    result.comparisons[0].left = _calculation_input("calculation_that_is_not_here")

    failures = harness.check_expectations({"result_input_linkage": True}, result)

    assert failures
    assert "calculation:calculation_that_is_not_here" in failures[0]


def test_q11_calculation_referencing_an_absent_fact_is_detected() -> None:
    result = q11_result()
    result.calculations[0].inputs = [_fact_input("fact_that_is_not_here")]

    failures = harness.check_expectations({"result_input_linkage": True}, result)

    assert failures
    assert "calculation_left_margin" in failures[0]
    assert "fact:fact_that_is_not_here" in failures[0]


def test_calculation_without_any_input_is_detected() -> None:
    """무엇에서 나온 값인지 비어 있으면 되짚을 수 없다."""
    result = q11_result()
    result.calculations[0].inputs = []

    failures = harness.check_expectations({"result_input_linkage": True}, result)

    assert failures and "has no inputs" in failures[0]


def test_every_dangling_input_is_reported_not_just_the_first() -> None:
    """Comparison의 양쪽이 모두 끊긴 경우 둘 다 드러나야 한다."""
    result = q10_result()
    result.comparisons[0].left = _fact_input("fact_left_that_is_not_here")
    result.comparisons[0].right = _fact_input("fact_right_that_is_not_here")

    failures = harness.check_expectations({"result_input_linkage": True}, result)

    assert failures
    assert "fact:fact_left_that_is_not_here" in failures[0]
    assert "fact:fact_right_that_is_not_here" in failures[0]


# --------------------------------------------------------------------------
# 4. 건수만으로는 참조 정합이 증명되지 않는다
# --------------------------------------------------------------------------


def test_comparison_count_alone_does_not_prove_result_input_linkage() -> None:
    """
    Comparison 1건이라는 사실은 그 1건이 무엇을 가리키는지 말해 주지 않는다.

    Pattern D 회귀 케이스에 comparisons만 적고 result_input_linkage를 빼면, 결과 밖을
    가리키는 비교 결과가 PASS로 나온다.
    """
    result = q10_result()
    result.comparisons[0].right = _fact_input("fact_that_is_not_here")

    assert harness.check_expectations({"comparisons": 1}, result) == []
    assert harness.check_expectations(
        {"comparisons": 1, "result_input_linkage": True}, result
    )


def test_evidence_linkage_does_not_cover_comparison_inputs() -> None:
    """
    evidence_linkage는 Comparison을 보지 않는다.

    ComparisonResult에 evidence_ids가 없기 때문이며, 그래서 두 검사가 서로를 대신하지
    못한다는 것을 고정한다.
    """
    result = q10_result()
    result.comparisons[0].right = _fact_input("fact_that_is_not_here")

    assert harness.check_expectations({"evidence_linkage": True}, result) == []


# --------------------------------------------------------------------------
# 5. 결과 요약
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("builder", "comparisons", "calculations"),
    [(q10_result, 1, 0), (q11_result, 1, 2)],
)
def test_summary_reports_the_comparison_and_calculation_counts(
    builder, comparisons: int, calculations: int
) -> None:
    summary = harness.summarize_result(builder())

    assert summary["comparisons"] == comparisons
    assert summary["calculations"] == calculations

    # 실제 공시 값이 요약에 실리지 않아야 한다는 규칙은 기존 테스트가 맡는다.
    # 여기서는 직렬화만 확인한다.
    json.dumps(summary, ensure_ascii=False)
