"""결정적 계산과 두 비교 가능성 guard를 검증한다.

무엇을 고정하는가:
    수치형 파생 결과는 Python 산술로만 만들어지고, 그 값이 어떤 입력에서 나왔는지가 결과에
    남는다. 그리고 함께 쓸 수 없는 값으로는 아예 만들어지지 않는다.

    코퍼스를 쓰지 않는다. agent.calculator는 확정된 Fact와 Calculation만 받으므로 계산 규칙
    전체를 합성 입력으로 시험할 수 있다. 이 파일이 코퍼스를 열어야 한다면 그것 자체가 계층
    경계가 무너졌다는 신호다.

무엇을 검증하지 않는가:
    무엇을 계산할지 고르는 판정은 보지 않는다. 파생지표 대응표를 읽고 연도를 짝짓는 일은
    Pattern C handler의 몫이고 tests/integration/test_pattern_c.py가 고정한다.

두 guard를 나눠 보는 이유:
    guard①은 한 연도 안에서 분자와 분모가 맞는지만 보고, 연도 사이의 어긋남은 보지 않는다.
    2024년 연결 기준과 2025년 별도 기준은 각 연도의 guard①을 모두 통과한다. 그 구멍을
    guard②가 막는다는 것을 4절이 고정한다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from disclosure_agent.agent.answer_builder import (
    REASON_INCOMPARABLE_CALCULATION_INPUTS,
    REASON_INCOMPARABLE_PERIODS,
    REASON_UNUSABLE_CALCULATION_INPUT,
)
from disclosure_agent.agent.calculator import (
    OPERATION_AGGREGATE,
    OPERATION_DIFF_PP,
    OPERATION_RATIO,
    UNIT_PERCENTAGE_POINT,
    CalculationBlockedError,
    aggregate,
    diff,
    ratio,
    validate_aggregation_inputs,
    validate_calculation_inputs,
    validate_temporal_comparability,
)
from disclosure_agent.common.enums import (
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import CalculationResult, Fact

COMPANY = "테스트반도체(주)"

UNIT_KRW = "원"

# 파생지표 대응표가 정하는 값이지만, 이 파일은 대응표를 읽지 않는다. calculator는 어느
# 지표가 분자인지 모르고 인자로 받는다는 것이 검증 대상이므로 상수로 둔다.
NUMERATOR_METRIC = MetricConcept.OPERATING_INCOME

DENOMINATOR_METRIC = MetricConcept.REVENUE

UNIT_PERCENT = "percent"


def make_fact(
    *,
    metric: MetricConcept,
    value: str | float | None,
    fiscal_year: int = 2025,
    company: str = COMPANY,
    accounting_scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
    normalized_unit: str | None = UNIT_KRW,
    evidence_ids: tuple[str, ...] | None = None,
    fact_id: str | None = None,
) -> Fact:
    """검증에 필요한 축만 바꿀 수 있는 Fact를 만든다."""
    resolved_id = fact_id or f"fact_{metric.value}_{fiscal_year}_{accounting_scope.value}"

    return Fact(
        fact_id=resolved_id,
        company=company,
        metric=metric,
        fiscal_year=fiscal_year,
        accounting_scope=accounting_scope,
        normalized_value=value,
        normalized_unit=normalized_unit,
        evidence_ids=list(evidence_ids or (f"evidence_{resolved_id}",)),
    )


def make_pair(
    *,
    fiscal_year: int,
    numerator_value: str = "300",
    denominator_value: str = "1000",
    company: str = COMPANY,
    accounting_scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
) -> tuple[Fact, Fact]:
    """한 회계연도의 분자·분모 짝."""
    return (
        make_fact(
            metric=NUMERATOR_METRIC,
            value=numerator_value,
            fiscal_year=fiscal_year,
            company=company,
            accounting_scope=accounting_scope,
        ),
        make_fact(
            metric=DENOMINATOR_METRIC,
            value=denominator_value,
            fiscal_year=fiscal_year,
            company=company,
            accounting_scope=accounting_scope,
        ),
    )


def make_ratio(
    *,
    fiscal_year: int,
    numerator_value: str = "300",
    denominator_value: str = "1000",
    company: str = COMPANY,
    accounting_scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
) -> tuple[CalculationResult, tuple[Fact, Fact]]:
    """한 회계연도의 비율과 그 원천."""
    numerator, denominator = make_pair(
        fiscal_year=fiscal_year,
        numerator_value=numerator_value,
        denominator_value=denominator_value,
        company=company,
        accounting_scope=accounting_scope,
    )

    calculation = ratio(
        calculation_id=f"calculation_margin_{fiscal_year}_{accounting_scope.value}",
        numerator=numerator,
        denominator=denominator,
        output_unit=UNIT_PERCENT,
    )

    return calculation, (numerator, denominator)


def check_inputs(numerator: Fact, denominator: Fact) -> None:
    """이 파일이 반복해 쓰는 guard① 호출 형태를 한곳에 둔다."""
    validate_calculation_inputs(
        numerator=numerator,
        denominator=denominator,
        numerator_metric=NUMERATOR_METRIC,
        denominator_metric=DENOMINATOR_METRIC,
    )


def test_aggregate_sums_exact_decimal_fact_values_and_preserves_inputs() -> None:
    facts = tuple(
        make_fact(
            metric=MetricConcept.INVESTMENT_AMOUNT,
            value=value,
            company=COMPANY,
            accounting_scope=FactAccountingScope.NOT_APPLICABLE,
            normalized_unit="KRW",
            fact_id=f"fact_investment_{index}",
        )
        for index, value in enumerate(("100", "200.50"), start=1)
    )
    validate_aggregation_inputs(
        facts,
        expected_company=COMPANY,
        expected_metric=MetricConcept.INVESTMENT_AMOUNT,
        expected_unit="KRW",
    )

    calculation = aggregate(
        calculation_id="calculation_investment_total",
        facts=facts,
        output_unit="KRW",
    )

    assert calculation.operation == OPERATION_AGGREGATE
    assert calculation.value == "300.5"
    assert [item.input_id for item in calculation.inputs] == [
        "fact_investment_1",
        "fact_investment_2",
    ]


def test_aggregation_guard_rejects_an_incompatible_unit() -> None:
    fact = make_fact(
        metric=MetricConcept.INVESTMENT_AMOUNT,
        value="100",
        company=COMPANY,
        accounting_scope=FactAccountingScope.NOT_APPLICABLE,
        normalized_unit="USD",
    )

    with pytest.raises(CalculationBlockedError, match="KRW"):
        validate_aggregation_inputs(
            (fact,),
            expected_company=COMPANY,
            expected_metric=MetricConcept.INVESTMENT_AMOUNT,
            expected_unit="KRW",
        )


# --------------------------------------------------------------------------
# 1. ratio
# --------------------------------------------------------------------------


def test_ratio_reports_the_percentage_of_numerator_over_denominator() -> None:
    numerator, denominator = make_pair(fiscal_year=2025)

    calculation = ratio(
        calculation_id="calculation_margin_2025",
        numerator=numerator,
        denominator=denominator,
        output_unit=UNIT_PERCENT,
    )

    assert calculation.calculation_id == "calculation_margin_2025"
    assert calculation.operation == OPERATION_RATIO
    assert calculation.value == "30.0000"
    assert calculation.unit == UNIT_PERCENT


@pytest.mark.parametrize(
    ("numerator_value", "denominator_value", "expected"),
    [
        # 나누어떨어지지 않는 값도 정해진 자릿수로 끝난다.
        ("1", "3", "33.3333"),
        ("2", "3", "66.6667"),
        # ROUND_HALF_UP: 정확히 절반이면 올린다. 33.33345는 Decimal 기본값인 은행가
        # 반올림(ROUND_HALF_EVEN)이면 앞자리가 짝수라 33.3334로 내려간다.
        ("0.3333345", "1", "33.3335"),
        # 음수 영업이익도 계산한다. 부호를 잃으면 적자가 흑자로 보인다.
        ("-250", "1000", "-25.0000"),
        # 실측 코퍼스와 같은 자릿수의 값.
        ("255391604854", "558917191547", "45.6940"),
        ("251390142142", "576684619527", "43.5923"),
    ],
)
def test_ratio_quantizes_to_four_decimal_places(
    numerator_value: str,
    denominator_value: str,
    expected: str,
) -> None:
    """
    자릿수와 반올림 방식을 고정한다.

    자릿수를 정하지 않으면 나눗셈 결과의 길이가 입력에 따라 달라지고, 같은 계산이 실행마다
    다른 표기를 낸다.
    """
    numerator, denominator = make_pair(
        fiscal_year=2025,
        numerator_value=numerator_value,
        denominator_value=denominator_value,
    )

    calculation = ratio(
        calculation_id="calculation_margin_2025",
        numerator=numerator,
        denominator=denominator,
        output_unit=UNIT_PERCENT,
    )

    assert calculation.value == expected


def test_ratio_does_not_round_through_float() -> None:
    """
    이진 부동소수를 거치지 않는다.

    float로 옮기면 0.1 + 0.2가 0.30000000000000004가 되는 종류의 오차가 끼어들고, 그 오차는
    자릿수를 줄이는 순간 사라져 눈에 띄지 않는다. 마지막 자리가 걸리는 값으로 확인한다.
    """
    numerator, denominator = make_pair(
        fiscal_year=2025,
        numerator_value="1000000000000000000001",
        denominator_value="10000000000000000000000",
    )

    calculation = ratio(
        calculation_id="calculation_margin_2025",
        numerator=numerator,
        denominator=denominator,
        output_unit=UNIT_PERCENT,
    )

    assert calculation.value == "10.0000"

    assert Decimal(calculation.value) == Decimal("10.0000")


def test_ratio_records_numerator_and_denominator_in_order() -> None:
    """
    순서를 잃으면 받는 쪽이 어느 쪽이 분자인지 다시 정해야 한다.

    뒤집어도 그럴듯한 비율이 나오므로 틀렸다는 사실이 값에 드러나지 않는다.
    """
    numerator, denominator = make_pair(fiscal_year=2025)

    calculation = ratio(
        calculation_id="calculation_margin_2025",
        numerator=numerator,
        denominator=denominator,
        output_unit=UNIT_PERCENT,
    )

    assert [(item.input_type, item.input_id) for item in calculation.inputs] == [
        (ResultInputType.FACT, numerator.fact_id),
        (ResultInputType.FACT, denominator.fact_id),
    ]


def test_ratio_merges_source_evidence_without_duplicates() -> None:
    """분자와 분모가 같은 표에서 읽히면 같은 근거가 두 번 들어온다."""
    shared = "evidence_shared_table"

    numerator = make_fact(
        metric=NUMERATOR_METRIC,
        value="300",
        evidence_ids=(shared, "evidence_numerator"),
    )

    denominator = make_fact(
        metric=DENOMINATOR_METRIC,
        value="1000",
        evidence_ids=(shared, "evidence_denominator"),
    )

    calculation = ratio(
        calculation_id="calculation_margin_2025",
        numerator=numerator,
        denominator=denominator,
        output_unit=UNIT_PERCENT,
    )

    # 순서를 지키며 중복만 없앤다. 정렬하면 같은 입력이 항상 같은 결과를 낸다는 성질은
    # 유지되지만, 어느 쪽 근거가 먼저였는지가 사라진다.
    assert calculation.evidence_ids == [
        shared,
        "evidence_numerator",
        "evidence_denominator",
    ]


def test_ratio_refuses_a_zero_denominator() -> None:
    """0으로 나눈 값은 정의되지 않는다. 예외를 삼키고 진행하면 없는 수치가 만들어진다."""
    numerator, denominator = make_pair(fiscal_year=2025, denominator_value="0")

    with pytest.raises(CalculationBlockedError) as error:
        ratio(
            calculation_id="calculation_margin_2025",
            numerator=numerator,
            denominator=denominator,
            output_unit=UNIT_PERCENT,
        )

    assert error.value.reason == REASON_UNUSABLE_CALCULATION_INPUT


@pytest.mark.parametrize("value", [None, "", "미상", "1,234", "NaN", "Infinity"])
def test_ratio_refuses_a_value_it_cannot_read(value: str | None) -> None:
    """
    수로 읽히지 않는 값은 추측하지 않는다.

    자릿수 구분 기호가 남은 값도 여기서 걸린다. 정규화 값은 이미 구분 기호가 제거된 표기여야
    하고, 남아 있다면 정규화가 끝나지 않은 값이다.
    """
    numerator = make_fact(metric=NUMERATOR_METRIC, value=value)

    denominator = make_fact(metric=DENOMINATOR_METRIC, value="1000")

    with pytest.raises(CalculationBlockedError) as error:
        ratio(
            calculation_id="calculation_margin_2025",
            numerator=numerator,
            denominator=denominator,
            output_unit=UNIT_PERCENT,
        )

    assert error.value.reason == REASON_UNUSABLE_CALCULATION_INPUT


# --------------------------------------------------------------------------
# 2. diff
# --------------------------------------------------------------------------


def test_diff_subtracts_the_old_value_from_the_new_one() -> None:
    old, _ = make_ratio(fiscal_year=2024, numerator_value="300")

    new, _ = make_ratio(fiscal_year=2025, numerator_value="450")

    change = diff(calculation_id="calculation_diff", old=old, new=new)

    assert old.value == "30.0000"
    assert new.value == "45.0000"
    assert change.value == "15.0000"


def test_diff_keeps_the_sign_of_a_decrease() -> None:
    """
    줄어든 변화는 음수로 남는다.

    절댓값만 남기면 "2.1017 달라졌다"가 되어 늘었는지 줄었는지가 사라진다.
    """
    old, _ = make_ratio(fiscal_year=2024, numerator_value="456", denominator_value="1000")

    new, _ = make_ratio(fiscal_year=2025, numerator_value="435", denominator_value="1000")

    change = diff(calculation_id="calculation_diff", old=old, new=new)

    assert change.value == "-2.1000"


def test_diff_is_computed_from_the_stored_values() -> None:
    """
    반올림 전 중간값이 아니라 저장된 값에서 뺀다.

    답변에 실리는 것은 저장된 두 값이므로, 사용자가 그 둘을 빼서 검산했을 때 결과가 같아야
    한다. 중간값에서 빼면 마지막 자리가 어긋나는 경우가 생긴다.
    """
    old, _ = make_ratio(fiscal_year=2024, numerator_value="1", denominator_value="3")

    new, _ = make_ratio(fiscal_year=2025, numerator_value="2", denominator_value="3")

    change = diff(calculation_id="calculation_diff", old=old, new=new)

    assert (old.value, new.value) == ("33.3333", "66.6667")

    assert change.value == str(Decimal(new.value) - Decimal(old.value))
    assert change.value == "33.3334"


def test_diff_is_reported_in_percentage_points() -> None:
    """
    백분율의 차이는 백분율이 아니다.

    두 값에 같은 단위를 쓰면 받는 쪽이 -2.1017을 비율로 읽는다.
    """
    old, _ = make_ratio(fiscal_year=2024)

    new, _ = make_ratio(fiscal_year=2025, numerator_value="450")

    change = diff(calculation_id="calculation_diff", old=old, new=new)

    assert change.operation == OPERATION_DIFF_PP
    assert change.unit == UNIT_PERCENTAGE_POINT
    assert change.unit != old.unit


def test_diff_records_the_two_calculations_in_old_then_new_order() -> None:
    """뒤집으면 부호가 뒤집히고, 부호가 뒤집힌 변화량도 그럴듯하다."""
    old, _ = make_ratio(fiscal_year=2024)

    new, _ = make_ratio(fiscal_year=2025, numerator_value="450")

    change = diff(calculation_id="calculation_diff", old=old, new=new)

    assert [(item.input_type, item.input_id) for item in change.inputs] == [
        (ResultInputType.CALCULATION, old.calculation_id),
        (ResultInputType.CALCULATION, new.calculation_id),
    ]


def test_diff_carries_every_source_evidence_forward() -> None:
    """
    변화량의 근거는 두 비율의 근거 전부다.

    한쪽만 실으면 그 값이 어느 연도에서 나왔는지 절반이 사라진다.
    """
    old, _ = make_ratio(fiscal_year=2024)

    new, _ = make_ratio(fiscal_year=2025, numerator_value="450")

    change = diff(calculation_id="calculation_diff", old=old, new=new)

    assert change.evidence_ids == [*old.evidence_ids, *new.evidence_ids]

    assert len(change.evidence_ids) == len(set(change.evidence_ids))


# --------------------------------------------------------------------------
# 3. guard① — 계산 입력 검증
# --------------------------------------------------------------------------


def test_matching_inputs_pass_the_calculation_guard() -> None:
    """가드가 과하게 잠기지 않았는지 확인하는 대조군이다."""
    numerator, denominator = make_pair(fiscal_year=2025)

    check_inputs(numerator, denominator)


def test_calculation_guard_rejects_a_numerator_of_the_wrong_metric() -> None:
    """
    분자와 분모가 뒤바뀌어도 그럴듯한 비율이 나온다.

    지표를 확인하지 않으면 매출을 영업이익으로 나눈 값이 영업이익률로 나간다.
    """
    numerator = make_fact(metric=DENOMINATOR_METRIC, value="1000")

    denominator = make_fact(metric=DENOMINATOR_METRIC, value="1000")

    with pytest.raises(CalculationBlockedError) as error:
        check_inputs(numerator, denominator)

    assert error.value.reason == REASON_INCOMPARABLE_CALCULATION_INPUTS


def test_calculation_guard_rejects_a_denominator_of_the_wrong_metric() -> None:
    numerator = make_fact(metric=NUMERATOR_METRIC, value="300")

    denominator = make_fact(metric=NUMERATOR_METRIC, value="300")

    with pytest.raises(CalculationBlockedError) as error:
        check_inputs(numerator, denominator)

    assert error.value.reason == REASON_INCOMPARABLE_CALCULATION_INPUTS


def test_calculation_guard_rejects_two_companies() -> None:
    numerator = make_fact(metric=NUMERATOR_METRIC, value="300", company="테스트반도체(주)")

    denominator = make_fact(metric=DENOMINATOR_METRIC, value="1000", company="테스트전자")

    with pytest.raises(CalculationBlockedError) as error:
        check_inputs(numerator, denominator)

    assert error.value.reason == REASON_INCOMPARABLE_CALCULATION_INPUTS


def test_calculation_guard_rejects_two_fiscal_years() -> None:
    """
    2025년 영업이익을 2024년 매출로 나눈 값도 비율의 모양을 갖는다.

    기간이 섞인 비율은 어느 해의 값도 아니다.
    """
    numerator = make_fact(metric=NUMERATOR_METRIC, value="300", fiscal_year=2025)

    denominator = make_fact(metric=DENOMINATOR_METRIC, value="1000", fiscal_year=2024)

    with pytest.raises(CalculationBlockedError) as error:
        check_inputs(numerator, denominator)

    assert error.value.reason == REASON_INCOMPARABLE_CALCULATION_INPUTS


def test_calculation_guard_rejects_mixed_accounting_scopes() -> None:
    """연결 영업이익을 별도 매출로 나눈 값은 어느 기준의 비율도 아니다."""
    numerator = make_fact(
        metric=NUMERATOR_METRIC,
        value="300",
        accounting_scope=FactAccountingScope.CONSOLIDATED,
    )

    denominator = make_fact(
        metric=DENOMINATOR_METRIC,
        value="1000",
        accounting_scope=FactAccountingScope.SEPARATE,
    )

    with pytest.raises(CalculationBlockedError) as error:
        check_inputs(numerator, denominator)

    assert error.value.reason == REASON_INCOMPARABLE_CALCULATION_INPUTS


def test_calculation_guard_rejects_an_unknown_accounting_scope() -> None:
    """
    두 값이 모두 unknown이면 '같다'는 검사는 통과한다.

    그러나 확정하지 못한 기준끼리 같다고 볼 수는 없다. 연결과 별도를 섞었을 가능성이 그대로
    남아 있고, 섞여도 그럴듯한 값이 나온다.
    """
    numerator, denominator = make_pair(
        fiscal_year=2025,
        accounting_scope=FactAccountingScope.UNKNOWN,
    )

    with pytest.raises(CalculationBlockedError) as error:
        check_inputs(numerator, denominator)

    assert error.value.reason == REASON_INCOMPARABLE_CALCULATION_INPUTS


def test_calculation_guard_rejects_mismatched_units() -> None:
    """
    단위가 다르면 자릿수 오답이 조용히 발생한다.

    백만원 단위의 영업이익을 원 단위의 매출로 나누면 비율이 100만분의 1이 된다.
    """
    numerator = make_fact(metric=NUMERATOR_METRIC, value="300", normalized_unit="백만원")

    denominator = make_fact(metric=DENOMINATOR_METRIC, value="1000", normalized_unit=UNIT_KRW)

    with pytest.raises(CalculationBlockedError) as error:
        check_inputs(numerator, denominator)

    assert error.value.reason == REASON_INCOMPARABLE_CALCULATION_INPUTS


@pytest.mark.parametrize("side", ["numerator", "denominator"])
def test_calculation_guard_rejects_a_missing_unit(side: str) -> None:
    """단위 없는 값은 다른 단위와 같다고도 다르다고도 말할 수 없다."""
    numerator = make_fact(
        metric=NUMERATOR_METRIC,
        value="300",
        normalized_unit=None if side == "numerator" else UNIT_KRW,
    )

    denominator = make_fact(
        metric=DENOMINATOR_METRIC,
        value="1000",
        normalized_unit=None if side == "denominator" else UNIT_KRW,
    )

    with pytest.raises(CalculationBlockedError) as error:
        check_inputs(numerator, denominator)

    assert error.value.reason == REASON_INCOMPARABLE_CALCULATION_INPUTS


def test_calculation_guard_rejects_a_zero_denominator() -> None:
    """
    계산 직전에 확인한다.

    나눗셈 자리에서 터지면 어느 입력이 문제였는지가 결과에 남지 않는다.
    """
    numerator, denominator = make_pair(fiscal_year=2025, denominator_value="0")

    with pytest.raises(CalculationBlockedError) as error:
        check_inputs(numerator, denominator)

    # 견줄 수 없는 것이 아니라 산술이 성립하지 않는 것이다. 사유가 갈려야 답변 가능성 판정도
    # 갈린다.
    assert error.value.reason == REASON_UNUSABLE_CALCULATION_INPUT


def test_calculation_guard_rejects_an_unreadable_value() -> None:
    numerator = make_fact(metric=NUMERATOR_METRIC, value=None)

    denominator = make_fact(metric=DENOMINATOR_METRIC, value="1000")

    with pytest.raises(CalculationBlockedError) as error:
        check_inputs(numerator, denominator)

    assert error.value.reason == REASON_UNUSABLE_CALCULATION_INPUT


# --------------------------------------------------------------------------
# 4. guard② — 비교 가능성 검증
# --------------------------------------------------------------------------


def check_comparability(
    old: tuple[CalculationResult, tuple[Fact, ...]],
    new: tuple[CalculationResult, tuple[Fact, ...]],
) -> None:
    """이 파일이 반복해 쓰는 guard② 호출 형태를 한곳에 둔다."""
    validate_temporal_comparability(
        left=old[0],
        right=new[0],
        left_sources=old[1],
        right_sources=new[1],
        expected_unit=UNIT_PERCENT,
    )


def test_two_periods_of_one_company_pass_the_comparability_guard() -> None:
    """대조군. 가드가 정상 입력을 막지 않는지 본다."""
    check_comparability(
        make_ratio(fiscal_year=2024),
        make_ratio(fiscal_year=2025, numerator_value="450"),
    )


def test_comparability_guard_rejects_two_companies() -> None:
    with pytest.raises(CalculationBlockedError) as error:
        check_comparability(
            make_ratio(fiscal_year=2024, company="테스트반도체(주)"),
            make_ratio(fiscal_year=2025, company="테스트전자"),
        )

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_comparability_guard_rejects_scopes_that_differ_across_years() -> None:
    """
    이것이 guard②만 잡을 수 있는 어긋남이다.

    각 연도 안에서는 분자와 분모의 기준이 같으므로 guard①은 두 번 다 통과한다. 연도 사이의
    어긋남은 한 연도만 보는 검사로는 보이지 않는다.
    """
    old = make_ratio(fiscal_year=2024, accounting_scope=FactAccountingScope.CONSOLIDATED)

    new = make_ratio(
        fiscal_year=2025,
        numerator_value="450",
        accounting_scope=FactAccountingScope.SEPARATE,
    )

    # 전제 확인: 연도별 guard①은 둘 다 통과한다.
    check_inputs(*old[1])
    check_inputs(*new[1])

    with pytest.raises(CalculationBlockedError) as error:
        check_comparability(old, new)

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_comparability_guard_rejects_different_source_metrics() -> None:
    """
    이름이 같아도 분자·분모가 다르면 정의가 다른 두 비율이다.

    그런 두 값의 차이는 지표가 달라진 양이지 기간이 달라진 양이 아니다.
    """
    old, old_sources = make_ratio(fiscal_year=2024)

    new, new_sources = make_ratio(fiscal_year=2025, numerator_value="450")

    swapped = (
        new_sources[0],
        make_fact(
            metric=MetricConcept.CONTRACT_AMOUNT,
            value="1000",
            fiscal_year=2025,
        ),
    )

    with pytest.raises(CalculationBlockedError) as error:
        check_comparability((old, old_sources), (new, swapped))

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_comparability_guard_rejects_a_result_unit_it_did_not_expect() -> None:
    """
    두 값이 백분율이라는 전제가 깨지면 변화량을 percentage point로 부를 수 없다.
    """
    old, old_sources = make_ratio(fiscal_year=2024)

    new, new_sources = make_ratio(fiscal_year=2025, numerator_value="450")

    with pytest.raises(CalculationBlockedError) as error:
        validate_temporal_comparability(
            left=old,
            right=new.model_copy(update={"unit": "KRW"}),
            left_sources=old_sources,
            right_sources=new_sources,
            expected_unit=UNIT_PERCENT,
        )

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_comparability_guard_rejects_the_same_fiscal_year_twice() -> None:
    """같은 기간끼리는 변화량이 정의되지 않는다. 값은 언제나 0이 되고 그 0은 사실이 아니다."""
    with pytest.raises(CalculationBlockedError) as error:
        check_comparability(
            make_ratio(fiscal_year=2025),
            make_ratio(fiscal_year=2025, numerator_value="450"),
        )

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_comparability_guard_rejects_sources_from_more_than_one_year() -> None:
    """기간별 값이 아니면 그 차이를 기간의 변화라고 부를 수 없다."""
    old, old_sources = make_ratio(fiscal_year=2024)

    new, new_sources = make_ratio(fiscal_year=2025, numerator_value="450")

    mixed = (new_sources[0], make_fact(metric=DENOMINATOR_METRIC, value="1000", fiscal_year=2023))

    with pytest.raises(CalculationBlockedError) as error:
        check_comparability((old, old_sources), (new, mixed))

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


def test_comparability_guard_rejects_a_derived_value_without_sources() -> None:
    """원천이 없으면 판정할 근거가 없다. 근거 없이 통과시키면 가드가 없는 것과 같다."""
    old, old_sources = make_ratio(fiscal_year=2024)

    new, _ = make_ratio(fiscal_year=2025, numerator_value="450")

    with pytest.raises(CalculationBlockedError) as error:
        check_comparability((old, old_sources), (new, ()))

    assert error.value.reason == REASON_INCOMPARABLE_PERIODS


# --------------------------------------------------------------------------
# 5. 계층 경계
# --------------------------------------------------------------------------


def test_calculator_does_not_know_the_derived_metric_registry() -> None:
    """
    영업이익률이라는 개념이 이 모듈에 없다.

    있으면 파생 개념이 늘 때마다 계산기가 함께 늘고, 대응표와 계산기가 같은 질문에 서로 다른
    답을 갖는 순간이 생긴다.
    """
    import ast
    from pathlib import Path

    source_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "disclosure_agent"
        / "agent"
        / "calculator.py"
    )

    imported = {
        node.module
        for node in ast.walk(ast.parse(source_path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "disclosure_agent.agent.derived_metrics" not in imported
    assert "disclosure_agent.agent.query_analyzer" not in imported
    assert "disclosure_agent.agent.period_retrieval" not in imported
