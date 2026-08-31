"""기업 축 guard②의 Fact 경로와 원천 Fact 우열 판정을 검증한다(Q10).

무엇을 고정하는가:
    - validate_cross_entity_fact_comparability가 막는 항목 전부
    - compare_facts가 만드는 ComparisonResult의 모양과 관계

왜 Calculation 경로와 따로 보는가:
    Q11·Q12는 기업별 Calculation 2건을 견주고, Q10은 Calculation 없이 원천 Fact 2건을 바로
    견준다(evidence_and_answerability.md Calculation cardinality: Q10은 Calculation 0).
    두 경로가 판정하는 축은 같지만 판정할 것이 담긴 자리가 다르므로 함수가 둘이며, 여기서
    보는 것은 Fact 쪽이다. Calculation 쪽은 tests/unit/test_cross_entity_comparability.py가 맡는다.

    특히 단위 검사는 이 경로에서 의미가 다르다. Q10은 guard①을 지나지 않으므로 단위 정규화
    성공(C8)을 확인하는 자리가 이 함수 하나뿐이다
    (agent_patterns.md "Q10처럼 Calculation 없이 Fact를 바로 견주는 경로에서는 여기가 유일한
    guard").

두 정지 사유를 나누는 이유:
    incomparable_entities       값은 읽었으나 두 Fact를 함께 쓸 수 없다. 코퍼스를 끝까지 읽고
                                내린 판정이므로 답변 가능성 판정(NOT_COMPARABLE)으로 나간다.
    unusable_calculation_input  그 값으로는 대소 비교 자체가 성립하지 않는다. 코퍼스에 대한
                                판정이 아니므로 실행 중단으로 남는다.

    두 사유가 섞이면 "코퍼스에 답이 없다"와 "그 값을 읽지 못했다"가 한 지표로 묶인다.

코퍼스를 읽지 않는다. 이 판정은 확정된 값만 받는 순수 함수이므로 합성 입력으로 전부 시험할
수 있다. 실제 공시 수치는 쓰지 않으며 아래 숫자는 대소 관계만 의미를 가진다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.answer_builder import (
    REASON_INCOMPARABLE_ENTITIES,
    REASON_UNUSABLE_CALCULATION_INPUT,
)
from disclosure_agent.agent.calculator import (
    CalculationBlockedError,
    compare_facts,
    validate_cross_entity_fact_comparability,
)
from disclosure_agent.common.enums import (
    ComparisonRelation,
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import Fact

COMPANY_LEFT = "테스트반도체(주)"
COMPANY_RIGHT = "테스트전자"

FISCAL_YEAR = 2025


def _fact(
    company: str,
    *,
    value: str | float | None = "100",
    unit: str | None = "원",
    metric: MetricConcept = MetricConcept.REVENUE,
    fiscal_year: int | None = FISCAL_YEAR,
    scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
) -> Fact:
    return Fact(
        fact_id=f"fact_{company}_{metric.value}_{fiscal_year}",
        company=company,
        metric=metric,
        fiscal_year=fiscal_year,
        accounting_scope=scope,
        raw_value=str(value) if value is not None else None,
        raw_unit="백만원",
        normalized_value=value,
        normalized_unit=unit,
        evidence_ids=[f"evidence_{company}_{metric.value}"],
    )


def _validate(left: Fact, right: Fact, **overrides) -> None:
    kwargs: dict[str, object] = {
        "expected_metric": MetricConcept.REVENUE,
        "expected_fiscal_year": FISCAL_YEAR,
    }

    kwargs.update(overrides)

    validate_cross_entity_fact_comparability(left=left, right=right, **kwargs)


# --------------------------------------------------------------------------
# 1. 견줄 수 있는 두 Fact는 통과한다
#
# 여기가 무너지면 정상 질문까지 막힌다. 막는 규칙보다 이쪽의 회귀 비용이 크다.
# --------------------------------------------------------------------------


def test_two_distinct_companies_in_the_same_period_pass() -> None:
    """서로 다른 두 기업이 정상 입력이다. Pattern C의 '동일 기업' 조건은 상속되지 않는다."""
    _validate(_fact(COMPANY_LEFT), _fact(COMPANY_RIGHT))


def test_unknown_scope_on_neither_side_is_required() -> None:
    """연결이든 별도든 둘이 같기만 하면 된다."""
    _validate(
        _fact(COMPANY_LEFT, scope=FactAccountingScope.SEPARATE),
        _fact(COMPANY_RIGHT, scope=FactAccountingScope.SEPARATE),
    )


def test_float_normalized_values_are_accepted() -> None:
    """Contract상 normalized_value는 float일 수 있다."""
    _validate(_fact(COMPANY_LEFT, value=100.5), _fact(COMPANY_RIGHT, value=99.5))


# --------------------------------------------------------------------------
# 2. incomparable_entities — 값은 읽었으나 함께 쓸 수 없다
# --------------------------------------------------------------------------


def _assert_blocked(
    excinfo: pytest.ExceptionInfo[CalculationBlockedError],
    *,
    reason: str,
    contains: str,
) -> None:
    assert excinfo.value.reason == reason
    assert contains in excinfo.value.detail


def test_the_same_company_on_both_sides_is_rejected() -> None:
    """
    자기 자신과의 비교는 언제나 equal이다.

    그 답은 형식만 맞고 질문에는 답하지 않은 결과이며, 받는 쪽이 틀렸다는 것을 알 수 없다.
    """
    with pytest.raises(CalculationBlockedError) as excinfo:
        _validate(_fact(COMPANY_LEFT), _fact(COMPANY_LEFT, value="200"))

    _assert_blocked(
        excinfo, reason=REASON_INCOMPARABLE_ENTITIES, contains="기업이 같습니다"
    )


def test_different_metrics_are_rejected() -> None:
    with pytest.raises(CalculationBlockedError) as excinfo:
        _validate(
            _fact(COMPANY_LEFT),
            _fact(COMPANY_RIGHT, metric=MetricConcept.OPERATING_INCOME),
        )

    _assert_blocked(excinfo, reason=REASON_INCOMPARABLE_ENTITIES, contains="지표가 다릅니다")


def test_a_metric_other_than_the_requested_one_is_rejected() -> None:
    """
    서로 같은 것만 보면 부족하다.

    둘 다 영업이익이 돌아왔다면 서로는 일치하지만 매출을 물은 질문에는 답하지 않은 결과가
    된다. 요청한 지표를 함께 받는 이유가 이것이다.
    """
    with pytest.raises(CalculationBlockedError) as excinfo:
        _validate(
            _fact(COMPANY_LEFT, metric=MetricConcept.OPERATING_INCOME),
            _fact(COMPANY_RIGHT, metric=MetricConcept.OPERATING_INCOME),
            expected_metric=MetricConcept.REVENUE,
        )

    _assert_blocked(
        excinfo, reason=REASON_INCOMPARABLE_ENTITIES, contains="요청한 지표는"
    )


def test_different_fiscal_years_are_rejected() -> None:
    """기업 간 비교는 같은 기간의 두 기업을 견준다. 시계열 축과 섞이면 안 된다."""
    with pytest.raises(CalculationBlockedError) as excinfo:
        _validate(_fact(COMPANY_LEFT), _fact(COMPANY_RIGHT, fiscal_year=2024))

    _assert_blocked(
        excinfo, reason=REASON_INCOMPARABLE_ENTITIES, contains="회계연도가 다릅니다"
    )


def test_a_fiscal_year_other_than_the_requested_one_is_rejected() -> None:
    """둘 다 2024년이면 서로는 일치하지만 2025년을 물은 질문에는 답하지 않은 결과다."""
    with pytest.raises(CalculationBlockedError) as excinfo:
        _validate(
            _fact(COMPANY_LEFT, fiscal_year=2024),
            _fact(COMPANY_RIGHT, fiscal_year=2024),
            expected_fiscal_year=FISCAL_YEAR,
        )

    _assert_blocked(
        excinfo, reason=REASON_INCOMPARABLE_ENTITIES, contains="요청한 회계연도는"
    )


def test_mixed_accounting_scopes_are_rejected() -> None:
    with pytest.raises(CalculationBlockedError) as excinfo:
        _validate(
            _fact(COMPANY_LEFT, scope=FactAccountingScope.CONSOLIDATED),
            _fact(COMPANY_RIGHT, scope=FactAccountingScope.SEPARATE),
        )

    _assert_blocked(
        excinfo, reason=REASON_INCOMPARABLE_ENTITIES, contains="회계기준이 다릅니다"
    )


def test_unknown_accounting_scope_on_both_sides_is_rejected() -> None:
    """
    "서로 같은가"만 보면 통과한다.

    기준을 확정하지 못한 두 값은 한쪽이 연결이고 다른 쪽이 별도일 수 있고, 섞어도 그럴듯한
    대소 관계가 나온다.
    """
    with pytest.raises(CalculationBlockedError) as excinfo:
        _validate(
            _fact(COMPANY_LEFT, scope=FactAccountingScope.UNKNOWN),
            _fact(COMPANY_RIGHT, scope=FactAccountingScope.UNKNOWN),
        )

    _assert_blocked(
        excinfo, reason=REASON_INCOMPARABLE_ENTITIES, contains="확정하지 못한"
    )


@pytest.mark.parametrize(
    ("left_unit", "right_unit"),
    [(None, "원"), ("원", None), (None, None)],
)
def test_a_missing_normalized_unit_is_rejected(
    left_unit: str | None, right_unit: str | None
) -> None:
    with pytest.raises(CalculationBlockedError) as excinfo:
        _validate(
            _fact(COMPANY_LEFT, unit=left_unit), _fact(COMPANY_RIGHT, unit=right_unit)
        )

    _assert_blocked(
        excinfo,
        reason=REASON_INCOMPARABLE_ENTITIES,
        contains="정규화 단위가 없는",
    )


def test_mismatched_normalized_units_are_rejected() -> None:
    """
    이 검사가 Q10에서 가장 중요한 자리다.

    실측 코퍼스에서 두 기업의 원문 단위 표기가 서로 다르다(천원 / 원). 정규화 뒤에도 단위가
    갈린 채로 견주면 자릿수가 세 자리 어긋난 우열이 그대로 나간다.
    """
    with pytest.raises(CalculationBlockedError) as excinfo:
        _validate(
            _fact(COMPANY_LEFT, unit="천원"), _fact(COMPANY_RIGHT, unit="원")
        )

    _assert_blocked(
        excinfo, reason=REASON_INCOMPARABLE_ENTITIES, contains="정규화 단위가 다릅니다"
    )


# --------------------------------------------------------------------------
# 3. unusable_calculation_input — 그 값으로는 판정이 성립하지 않는다
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "해당사항없음", "1,234", "NaN", "Infinity"])
def test_unusable_numeric_values_are_rejected_with_the_other_reason(
    value: str | None,
) -> None:
    """
    답변 불가와 나눈다.

    근거가 없는 것이 아니므로 INSUFFICIENT_EVIDENCE가 아니고, 코퍼스에 답이 없다는 판정도
    아니다. agent.calculator가 같은 자리에서 쓰는 사유를 그대로 쓴다.
    """
    with pytest.raises(CalculationBlockedError) as excinfo:
        _validate(_fact(COMPANY_LEFT), _fact(COMPANY_RIGHT, value=value))

    assert excinfo.value.reason == REASON_UNUSABLE_CALCULATION_INPUT


def test_the_failing_side_is_named_in_the_detail() -> None:
    """어느 입력이 문제였는지가 결과에 남아야 후속 조치를 고를 수 있다."""
    right = _fact(COMPANY_RIGHT, value="해당사항없음")

    with pytest.raises(CalculationBlockedError) as excinfo:
        _validate(_fact(COMPANY_LEFT), right)

    assert right.fact_id in excinfo.value.detail


def test_unit_mismatch_is_reported_before_an_unusable_value() -> None:
    """
    판정 순서가 규약이다.

    단위가 어긋난 값과 읽을 수 없는 값이 함께 있으면 앞의 것이 사유가 된다. 순서가 흔들리면
    같은 입력이 실행에 따라 UNANSWERABLE과 BLOCKED를 오간다.
    """
    with pytest.raises(CalculationBlockedError) as excinfo:
        _validate(
            _fact(COMPANY_LEFT, unit="천원"),
            _fact(COMPANY_RIGHT, unit="원", value=None),
        )

    assert excinfo.value.reason == REASON_INCOMPARABLE_ENTITIES


# --------------------------------------------------------------------------
# 4. compare_facts — 관계와 참조
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left_value", "right_value", "expected"),
    [
        ("200", "100", ComparisonRelation.LEFT_GREATER),
        ("100", "200", ComparisonRelation.RIGHT_GREATER),
        ("100", "100", ComparisonRelation.EQUAL),
        # 표기가 달라도 값이 같으면 같다.
        ("100", "100.00", ComparisonRelation.EQUAL),
        # 음수도 다루어야 한다. 영업이익이 음수인 기업이 실제로 있다.
        ("-10", "-20", ComparisonRelation.LEFT_GREATER),
        ("-20", "10", ComparisonRelation.RIGHT_GREATER),
        # 자릿수가 큰 값에서 부동소수 반올림에 기대면 여기서 갈린다.
        ("576684619527", "576684619528", ComparisonRelation.RIGHT_GREATER),
    ],
)
def test_relation_follows_the_normalized_values(
    left_value: str, right_value: str, expected: ComparisonRelation
) -> None:
    comparison = compare_facts(
        comparison_id="comparison_synthetic",
        left=_fact(COMPANY_LEFT, value=left_value),
        right=_fact(COMPANY_RIGHT, value=right_value),
    )

    assert comparison.relation is expected


def test_comparison_references_both_facts_in_order() -> None:
    left = _fact(COMPANY_LEFT, value="200")

    right = _fact(COMPANY_RIGHT, value="100")

    comparison = compare_facts(
        comparison_id="comparison_synthetic", left=left, right=right
    )

    assert comparison.comparison_id == "comparison_synthetic"

    assert comparison.left.input_type is ResultInputType.FACT
    assert comparison.left.input_id == left.fact_id

    assert comparison.right.input_type is ResultInputType.FACT
    assert comparison.right.input_id == right.fact_id


def test_swapping_the_inputs_swaps_the_relation() -> None:
    """순서가 관계 이름을 정한다. 뒤집힌 관계도 그럴듯하므로 이 성질을 고정한다."""
    left = _fact(COMPANY_LEFT, value="200")

    right = _fact(COMPANY_RIGHT, value="100")

    forward = compare_facts(comparison_id="c", left=left, right=right)

    backward = compare_facts(comparison_id="c", left=right, right=left)

    assert forward.relation is ComparisonRelation.LEFT_GREATER
    assert backward.relation is ComparisonRelation.RIGHT_GREATER


def test_comparison_carries_no_numeric_value() -> None:
    """
    ComparisonResult는 수치형 파생 결과가 아니다.

    두 값의 차이를 실을 자리가 Contract에 없으며, 만들었다면 Calculation으로 세어야 한다.
    Q10의 Calculation 개수가 0이라는 계약이 여기서 지켜진다.
    """
    comparison = compare_facts(
        comparison_id="c",
        left=_fact(COMPANY_LEFT, value="200"),
        right=_fact(COMPANY_RIGHT, value="100"),
    )

    assert set(comparison.model_dump()) == {
        "comparison_id",
        "left",
        "right",
        "relation",
    }


def test_compare_facts_still_refuses_an_unusable_value() -> None:
    """
    guard를 부르지 않고 바로 판정하려 해도 값을 추측하지 않는다.

    검증과 판정을 나눈 것은 시험을 위해서이지, 판정 쪽이 무엇이든 받아들이라는 뜻이 아니다.
    """
    with pytest.raises(CalculationBlockedError) as excinfo:
        compare_facts(
            comparison_id="c",
            left=_fact(COMPANY_LEFT, value=None),
            right=_fact(COMPANY_RIGHT),
        )

    assert excinfo.value.reason == REASON_UNUSABLE_CALCULATION_INPUT
