"""기간 축 guard②의 Fact 경로와 원천 지표 증감 계산을 검증한다(Q07).

무엇을 고정하는가:
    - validate_temporal_fact_comparability가 막는 항목
    - delta / delta_pct가 만드는 CalculationResult의 값·단위·참조
    - 이전 값이 0인 경우의 fail-closed

왜 Calculation 경로와 따로 보는가:
    Q08은 기간별 Calculation 2건을 견주고, Q07은 Calculation을 거치지 않고 원천 Fact 2건에서
    바로 증감을 만든다. 두 경로가 판정하는 축은 같지만 판정할 것이 담긴 자리가 다르므로 함수가
    둘이며, 여기서 보는 것은 Fact 쪽이다. Calculation 쪽은
    tests/unit/test_multi_period_guard.py 계열이 맡는다.

    Q10이 기업 축에서 같은 구조를 갖는다
    (tests/unit/test_cross_entity_fact_comparability.py). Q07도 guard①을 지나지 않으므로
    단위 정규화 성공(C8)을 확인하는 자리가 이 함수 하나뿐이다.

두 정지 사유를 나누는 이유:
    incomparable_periods        값은 읽었으나 두 기간을 함께 쓸 수 없다.
    unusable_calculation_input  그 값으로는 산술 자체가 성립하지 않는다.

코퍼스를 읽지 않는다. 이 판정은 확정된 값만 받는 순수 함수이므로 합성 입력으로 전부 시험할
수 있다. 아래 숫자는 증감 관계만 의미를 가진다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.answer_builder import (
    REASON_INCOMPARABLE_PERIODS,
    REASON_UNUSABLE_CALCULATION_INPUT,
)
from disclosure_agent.agent.calculator import (
    OPERATION_DELTA,
    OPERATION_DELTA_PCT,
    UNIT_PERCENT,
    CalculationBlockedError,
    delta,
    delta_pct,
    validate_temporal_fact_comparability,
)
from disclosure_agent.common.enums import (
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import Fact

COMPANY = "테스트반도체(주)"

PREVIOUS_YEAR = 2024
CURRENT_YEAR = 2025


def _fact(
    fiscal_year: int,
    *,
    value: str | float | None = "100",
    unit: str | None = "KRW",
    company: str = COMPANY,
    metric: MetricConcept = MetricConcept.REVENUE,
    scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
    fiscal_period: str | None = None,
) -> Fact:
    return Fact(
        fact_id=f"fact_{company}_{metric.value}_{fiscal_year}",
        company=company,
        metric=metric,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        accounting_scope=scope,
        raw_value=str(value) if value is not None else None,
        raw_unit="백만원",
        normalized_value=value,
        normalized_unit=unit,
        evidence_ids=[f"evidence_{fiscal_year}"],
    )


def _validate(previous: Fact, current: Fact) -> None:
    validate_temporal_fact_comparability(
        previous=previous,
        current=current,
        expected_metric=MetricConcept.REVENUE,
        expected_previous_year=PREVIOUS_YEAR,
        expected_current_year=CURRENT_YEAR,
    )


# --------------------------------------------------------------------------
# 1. guard② — 견줄 수 있는 두 기간만 통과한다
# --------------------------------------------------------------------------


def test_matching_periods_pass() -> None:
    _validate(_fact(PREVIOUS_YEAR), _fact(CURRENT_YEAR))


def test_different_companies_are_blocked() -> None:
    with pytest.raises(CalculationBlockedError) as error:
        _validate(_fact(PREVIOUS_YEAR), _fact(CURRENT_YEAR, company="테스트전자"))

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_different_metrics_are_blocked() -> None:
    with pytest.raises(CalculationBlockedError) as error:
        _validate(
            _fact(PREVIOUS_YEAR),
            _fact(CURRENT_YEAR, metric=MetricConcept.OPERATING_INCOME),
        )

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_metric_other_than_the_requested_one_is_blocked() -> None:
    """
    두 Fact가 서로 일치하는 것만으로는 부족하다.

    둘 다 영업이익이면 서로는 일치하지만 매출을 물은 질문에는 답하지 않은 결과가 된다.
    """
    with pytest.raises(CalculationBlockedError) as error:
        _validate(
            _fact(PREVIOUS_YEAR, metric=MetricConcept.OPERATING_INCOME),
            _fact(CURRENT_YEAR, metric=MetricConcept.OPERATING_INCOME),
        )

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_reversed_year_order_is_blocked() -> None:
    """순서가 뒤바뀌면 증감의 부호가 뒤집히고, 뒤집힌 값도 그럴듯하다."""
    with pytest.raises(CalculationBlockedError) as error:
        validate_temporal_fact_comparability(
            previous=_fact(CURRENT_YEAR),
            current=_fact(PREVIOUS_YEAR),
            expected_metric=MetricConcept.REVENUE,
            expected_previous_year=CURRENT_YEAR,
            expected_current_year=PREVIOUS_YEAR,
        )

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_same_year_on_both_sides_is_blocked() -> None:
    with pytest.raises(CalculationBlockedError) as error:
        validate_temporal_fact_comparability(
            previous=_fact(CURRENT_YEAR),
            current=_fact(CURRENT_YEAR),
            expected_metric=MetricConcept.REVENUE,
            expected_previous_year=CURRENT_YEAR,
            expected_current_year=CURRENT_YEAR,
        )

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_years_other_than_the_requested_ones_are_blocked() -> None:
    """서로 일치해도 질문이 물은 두 해가 아니면 답하지 않은 결과가 된다."""
    with pytest.raises(CalculationBlockedError) as error:
        _validate(_fact(2022), _fact(2023))

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_missing_fiscal_year_is_blocked() -> None:
    with pytest.raises(CalculationBlockedError) as error:
        validate_temporal_fact_comparability(
            previous=_fact(PREVIOUS_YEAR).model_copy(update={"fiscal_year": None}),
            current=_fact(CURRENT_YEAR),
            expected_metric=MetricConcept.REVENUE,
            expected_previous_year=PREVIOUS_YEAR,
            expected_current_year=CURRENT_YEAR,
        )

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_different_fiscal_periods_are_blocked() -> None:
    """연간 값과 분기 값의 차이는 증감이 아니다."""
    with pytest.raises(CalculationBlockedError) as error:
        _validate(
            _fact(PREVIOUS_YEAR, fiscal_period="FY"),
            _fact(CURRENT_YEAR, fiscal_period="Q3"),
        )

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_different_accounting_scopes_are_blocked() -> None:
    with pytest.raises(CalculationBlockedError) as error:
        _validate(
            _fact(PREVIOUS_YEAR),
            _fact(CURRENT_YEAR, scope=FactAccountingScope.SEPARATE),
        )

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_unknown_accounting_scope_on_both_sides_is_blocked() -> None:
    """모두 unknown이면 서로는 같지만 연결과 별도가 섞였을 가능성이 그대로 남는다."""
    with pytest.raises(CalculationBlockedError) as error:
        _validate(
            _fact(PREVIOUS_YEAR, scope=FactAccountingScope.UNKNOWN),
            _fact(CURRENT_YEAR, scope=FactAccountingScope.UNKNOWN),
        )

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_missing_normalized_unit_is_blocked() -> None:
    with pytest.raises(CalculationBlockedError) as error:
        _validate(_fact(PREVIOUS_YEAR, unit=None), _fact(CURRENT_YEAR))

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_different_normalized_units_are_blocked() -> None:
    """정규화에 실패한 값끼리 빼면 자릿수가 어긋난 증감이 나간다."""
    with pytest.raises(CalculationBlockedError) as error:
        _validate(_fact(PREVIOUS_YEAR), _fact(CURRENT_YEAR, unit="USD"))

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


# --------------------------------------------------------------------------
# 2. 이전 값이 0이면 Q07 전체가 막힌다
# --------------------------------------------------------------------------


def test_zero_previous_value_is_blocked_by_the_guard() -> None:
    """
    증감액은 정의되지만 증감률은 정의되지 않는다.

    한쪽만 만들면 질문이 요구한 수치 두 건 중 하나만 남고, 남은 하나로 나머지를 되짚을 수도
    없다. 그래서 계산 직전에 한 번에 막는다.
    """
    with pytest.raises(CalculationBlockedError) as error:
        _validate(_fact(PREVIOUS_YEAR, value="0"), _fact(CURRENT_YEAR))

    assert error.value.reason == REASON_UNUSABLE_CALCULATION_INPUT


def test_zero_previous_value_is_blocked_again_at_the_calculation() -> None:
    """guard를 건너뛰고 불러도 증감률을 만들지 않는다."""
    with pytest.raises(CalculationBlockedError) as error:
        delta_pct(
            calculation_id="calculation_revenue_delta_pct",
            previous=_fact(PREVIOUS_YEAR, value="0"),
            current=_fact(CURRENT_YEAR),
        )

    assert error.value.reason == REASON_UNUSABLE_CALCULATION_INPUT


# --------------------------------------------------------------------------
# 3. delta — 증감액
# --------------------------------------------------------------------------


def test_delta_is_current_minus_previous() -> None:
    result = delta(
        calculation_id="calculation_revenue_delta",
        previous=_fact(PREVIOUS_YEAR, value="1000"),
        current=_fact(CURRENT_YEAR, value="1200"),
    )

    assert result.operation == OPERATION_DELTA
    assert result.value == "200.0000"


def test_delta_keeps_the_sign_when_the_value_fell() -> None:
    result = delta(
        calculation_id="calculation_revenue_delta",
        previous=_fact(PREVIOUS_YEAR, value="1200"),
        current=_fact(CURRENT_YEAR, value="1000"),
    )

    assert result.value == "-200.0000"


def test_delta_carries_the_shared_normalized_unit() -> None:
    """증감액의 단위는 정책이 아니라 입력의 성질이다. 원 단위 매출의 증감은 원이다."""
    result = delta(
        calculation_id="calculation_revenue_delta",
        previous=_fact(PREVIOUS_YEAR),
        current=_fact(CURRENT_YEAR),
    )

    assert result.unit == "KRW"


def test_delta_references_both_source_facts_in_order() -> None:
    previous = _fact(PREVIOUS_YEAR)
    current = _fact(CURRENT_YEAR)

    result = delta(
        calculation_id="calculation_revenue_delta",
        previous=previous,
        current=current,
    )

    assert [item.input_type for item in result.inputs] == [
        ResultInputType.FACT,
        ResultInputType.FACT,
    ]
    assert [item.input_id for item in result.inputs] == [
        previous.fact_id,
        current.fact_id,
    ]


def test_delta_merges_the_evidence_of_both_facts() -> None:
    result = delta(
        calculation_id="calculation_revenue_delta",
        previous=_fact(PREVIOUS_YEAR),
        current=_fact(CURRENT_YEAR),
    )

    assert result.evidence_ids == [
        f"evidence_{PREVIOUS_YEAR}",
        f"evidence_{CURRENT_YEAR}",
    ]


def test_delta_blocks_when_the_units_differ() -> None:
    """guard②가 이미 본 것이지만 값을 읽는 과정에서 드러나는 실패는 여기서도 막는다."""
    with pytest.raises(CalculationBlockedError):
        delta(
            calculation_id="calculation_revenue_delta",
            previous=_fact(PREVIOUS_YEAR),
            current=_fact(CURRENT_YEAR, unit="USD"),
        )


# --------------------------------------------------------------------------
# 4. delta_pct — 증감률
# --------------------------------------------------------------------------


def test_delta_pct_is_the_change_over_the_previous_value() -> None:
    result = delta_pct(
        calculation_id="calculation_revenue_delta_pct",
        previous=_fact(PREVIOUS_YEAR, value="1000"),
        current=_fact(CURRENT_YEAR, value="1200"),
    )

    assert result.operation == OPERATION_DELTA_PCT
    assert result.value == "20.0000"


def test_delta_pct_divides_by_the_previous_value_not_the_current_one() -> None:
    """이후 값으로 나누면 부호는 같고 크기만 다른 그럴듯한 값이 나온다."""
    result = delta_pct(
        calculation_id="calculation_revenue_delta_pct",
        previous=_fact(PREVIOUS_YEAR, value="200"),
        current=_fact(CURRENT_YEAR, value="250"),
    )

    # 50/200 = 25%. 50/250이면 20%가 된다.
    assert result.value == "25.0000"


def test_delta_pct_is_always_a_percentage() -> None:
    """증감률은 입력이 원이든 주식 수든 언제나 백분율이다."""
    result = delta_pct(
        calculation_id="calculation_revenue_delta_pct",
        previous=_fact(PREVIOUS_YEAR),
        current=_fact(CURRENT_YEAR),
    )

    assert result.unit == UNIT_PERCENT


def test_delta_pct_references_the_source_facts_not_the_delta() -> None:
    """
    근거를 되짚는 경로가 짧고, 증감액의 반올림이 증감률에 옮겨붙지 않는다.
    """
    previous = _fact(PREVIOUS_YEAR)
    current = _fact(CURRENT_YEAR)

    result = delta_pct(
        calculation_id="calculation_revenue_delta_pct",
        previous=previous,
        current=current,
    )

    assert [item.input_type for item in result.inputs] == [
        ResultInputType.FACT,
        ResultInputType.FACT,
    ]
    assert [item.input_id for item in result.inputs] == [
        previous.fact_id,
        current.fact_id,
    ]
