"""기업 축 비교 가능성 guard②와 관계 판정을 검증한다.

무엇을 고정하는가:
    서로 다른 두 기업의 파생값은 정상 입력이고, 그 밖의 축이 어긋나면 아예 견주지 않는다.
    그리고 우열 판정은 저장된 값의 Decimal 비교로만 나온다.

    코퍼스를 쓰지 않는다. agent.calculator는 확정된 Fact와 Calculation만 받으므로 규칙 전체를
    합성 입력으로 시험할 수 있다. 이 파일이 코퍼스를 열어야 한다면 그것 자체가 계층 경계가
    무너졌다는 신호다.

Pattern C guard②와 나눠 보는 이유:
    두 함수가 요구하는 것이 정반대인 항목이 둘이다.

        동일 기업      Pattern C는 요구하고 Pattern D는 금지한다.
        동일 회계연도  Pattern C는 금지하고 Pattern D는 요구한다.

    validate_temporal_comparability를 기업 간 비교에 재사용하면 정상 질문(Q11)이 전부
    '두 기간의 기업이 다릅니다'로 막힌다. 그 재사용이 실제로 막히는지를 1절이 고정한다
    (agent_patterns.md Pattern D 검증 조건, evidence_and_answerability.md
    "동일 기업(1)은 Pattern C temporal comparison 전용").

무엇을 검증하지 않는가:
    무엇을 계산할지 고르는 판정은 보지 않는다. 대응표를 읽고 기업을 짝짓는 일은 Pattern D
    handler의 몫이고 tests/integration/test_pattern_d_q11.py가 고정한다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from disclosure_agent.agent.answer_builder import (
    REASON_INCOMPARABLE_ENTITIES,
    REASON_UNUSABLE_CALCULATION_INPUT,
)
from disclosure_agent.agent.calculator import (
    OPERATION_DIFF_PP,
    OPERATION_RATIO,
    QUANTUM,
    CalculationBlockedError,
    compare_calculations,
    ratio,
    validate_calculation_inputs,
    validate_cross_entity_comparability,
    validate_temporal_comparability,
)
from disclosure_agent.common.enums import (
    ComparisonRelation,
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import CalculationResult, Fact, ResultInputReference

LEFT_COMPANY = "테스트조선(주)"

RIGHT_COMPANY = "테스트중공업(주)"

UNIT_KRW = "원"

UNIT_PERCENT = "percent"

FISCAL_YEAR = 2025

# 파생지표 대응표가 정하는 값이지만 이 파일은 대응표를 읽지 않는다. calculator가 어느 지표를
# 분자로 쓸지 모르고 인자로 받는다는 것이 검증 대상이므로 상수로 둔다.
NUMERATOR_METRIC = MetricConcept.OPERATING_INCOME

DENOMINATOR_METRIC = MetricConcept.REVENUE


def make_fact(
    *,
    metric: MetricConcept,
    value: str | float | None,
    company: str,
    fiscal_year: int = FISCAL_YEAR,
    accounting_scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
    normalized_unit: str | None = UNIT_KRW,
) -> Fact:
    """검증에 필요한 축만 바꿀 수 있는 Fact를 만든다."""
    fact_id = f"fact_{company}_{metric.value}_{fiscal_year}_{accounting_scope.value}"

    return Fact(
        fact_id=fact_id,
        company=company,
        metric=metric,
        fiscal_year=fiscal_year,
        accounting_scope=accounting_scope,
        normalized_value=value,
        normalized_unit=normalized_unit,
        evidence_ids=[f"evidence_{fact_id}"],
    )


def make_entity(
    *,
    company: str,
    numerator_value: str = "300",
    denominator_value: str = "1000",
    fiscal_year: int = FISCAL_YEAR,
    accounting_scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
    numerator_metric: MetricConcept = NUMERATOR_METRIC,
    denominator_metric: MetricConcept = DENOMINATOR_METRIC,
    output_unit: str = UNIT_PERCENT,
) -> tuple[CalculationResult, tuple[Fact, Fact]]:
    """한 기업의 비율 Calculation과 그 원천 Fact 짝."""
    numerator = make_fact(
        metric=numerator_metric,
        value=numerator_value,
        company=company,
        fiscal_year=fiscal_year,
        accounting_scope=accounting_scope,
    )

    denominator = make_fact(
        metric=denominator_metric,
        value=denominator_value,
        company=company,
        fiscal_year=fiscal_year,
        accounting_scope=accounting_scope,
    )

    calculation = ratio(
        calculation_id=f"calculation_operating_margin_{fiscal_year}_{company}",
        numerator=numerator,
        denominator=denominator,
        output_unit=output_unit,
    )

    return calculation, (numerator, denominator)


def validate(left_pair, right_pair, *, expected_unit: str = UNIT_PERCENT) -> None:
    """짝 두 개를 guard②에 그대로 넘긴다."""
    left, left_sources = left_pair
    right, right_sources = right_pair

    validate_cross_entity_comparability(
        left=left,
        right=right,
        left_sources=left_sources,
        right_sources=right_sources,
        expected_unit=expected_unit,
    )


# --------------------------------------------------------------------------
# 1. 서로 다른 두 기업이 정상 입력이다
#
# Pattern C의 guard②를 그대로 쓰면 정상 질문이 전부 막힌다. 그 사실을 먼저 고정한다.
# --------------------------------------------------------------------------


def test_two_different_companies_in_the_same_year_are_comparable() -> None:
    """Q11의 정상 입력이다. 여기가 막히면 대표 질문이 답을 내지 못한다."""
    validate(
        make_entity(company=LEFT_COMPANY),
        make_entity(company=RIGHT_COMPANY),
    )


def test_the_temporal_guard_rejects_what_the_cross_entity_guard_accepts() -> None:
    """
    Pattern C guard②를 재사용하지 못하는 이유를 실행으로 남긴다.

    같은 입력이 한쪽에서는 통과하고 다른 쪽에서는 막힌다. 조건이 정반대이기 때문이며,
    한 함수에 플래그로 묶으면 플래그가 반대로 설정된 호출 하나가 조용히 통과한다.
    """
    left, left_sources = make_entity(company=LEFT_COMPANY)
    right, right_sources = make_entity(company=RIGHT_COMPANY)

    with pytest.raises(CalculationBlockedError):
        validate_temporal_comparability(
            left=left,
            right=right,
            left_sources=left_sources,
            right_sources=right_sources,
            expected_unit=UNIT_PERCENT,
        )


def test_the_same_company_twice_is_not_a_cross_entity_comparison() -> None:
    """
    같은 기업을 두 번 담은 입력은 견줄 것이 없다.

    Pattern C가 같은 연도 두 개를 막는 것과 같은 자리이며, 축만 다르다.
    """
    with pytest.raises(CalculationBlockedError) as error:
        validate(
            make_entity(company=LEFT_COMPANY),
            make_entity(company=LEFT_COMPANY),
        )

    assert error.value.reason == REASON_INCOMPARABLE_ENTITIES


def test_a_derived_value_mixing_two_companies_is_rejected() -> None:
    """
    한 파생값의 원천이 두 기업에 걸쳐 있으면 그 값은 어느 기업의 값도 아니다.

    guard①이 분자·분모의 기업을 이미 보지만, 이 함수는 guard①을 거치지 않은 입력으로도
    불릴 수 있다. 그때 이 검사가 유일한 방어선이다.
    """
    left, (numerator, _) = make_entity(company=LEFT_COMPANY)

    other = make_fact(
        metric=DENOMINATOR_METRIC, value="1000", company="테스트해양(주)"
    )

    with pytest.raises(CalculationBlockedError) as error:
        validate_cross_entity_comparability(
            left=left,
            right=make_entity(company=RIGHT_COMPANY)[0],
            left_sources=(numerator, other),
            right_sources=make_entity(company=RIGHT_COMPANY)[1],
            expected_unit=UNIT_PERCENT,
        )

    assert error.value.reason == REASON_INCOMPARABLE_ENTITIES


# --------------------------------------------------------------------------
# 2. 기업 사이의 어긋남 — guard①로는 잡히지 않는 것들
#
# 아래 입력은 전부 기업별 guard①을 통과한다. 한 기업 안에서는 분자와 분모가 맞기 때문이다.
# 기업 사이의 어긋남은 guard②에서만 잡힌다.
# --------------------------------------------------------------------------


def test_each_side_passes_guard_one_before_the_mismatch_is_reached() -> None:
    """
    전제 확인. 아래 검사들이 guard①이 이미 막은 것을 다시 재는 것이 아니어야 한다.

    A사가 연결, B사가 별도인 입력에서 각 기업의 guard①은 통과한다. 이 사실이 무너지면
    아래 scope mismatch 검사가 guard②를 시험하지 않게 된다.
    """
    for company, scope in (
        (LEFT_COMPANY, FactAccountingScope.CONSOLIDATED),
        (RIGHT_COMPANY, FactAccountingScope.SEPARATE),
    ):
        _, (numerator, denominator) = make_entity(
            company=company, accounting_scope=scope
        )

        validate_calculation_inputs(
            numerator=numerator,
            denominator=denominator,
            numerator_metric=NUMERATOR_METRIC,
            denominator_metric=DENOMINATOR_METRIC,
        )


def test_accounting_scope_mismatch_across_companies_is_not_comparable() -> None:
    """연결 기준 비율과 별도 기준 비율은 견줄 수 없다."""
    with pytest.raises(CalculationBlockedError) as error:
        validate(
            make_entity(
                company=LEFT_COMPANY, accounting_scope=FactAccountingScope.CONSOLIDATED
            ),
            make_entity(
                company=RIGHT_COMPANY, accounting_scope=FactAccountingScope.SEPARATE
            ),
        )

    assert error.value.reason == REASON_INCOMPARABLE_ENTITIES


def test_unknown_accounting_scope_is_not_comparable_even_when_both_sides_match() -> None:
    """
    두 기업이 모두 unknown이면 '같다'는 검사는 통과한다.

    그래도 막는다. 한쪽이 연결이고 다른 쪽이 별도일 가능성이 그대로 남아 있고, 섞어도
    그럴듯한 값이 나오기 때문이다.
    """
    with pytest.raises(CalculationBlockedError) as error:
        validate(
            make_entity(
                company=LEFT_COMPANY, accounting_scope=FactAccountingScope.UNKNOWN
            ),
            make_entity(
                company=RIGHT_COMPANY, accounting_scope=FactAccountingScope.UNKNOWN
            ),
        )

    assert error.value.reason == REASON_INCOMPARABLE_ENTITIES


def test_fiscal_year_mismatch_across_companies_is_not_comparable() -> None:
    """
    기업 간 비교는 같은 기간의 두 기업을 견준다.

    2025년 A사와 2024년 B사를 견주면 우열 판정이 기업 차이인지 기간 차이인지 알 수 없다.
    Pattern C가 같은 연도를 막는 것과 정반대의 조건이다.
    """
    with pytest.raises(CalculationBlockedError) as error:
        validate(
            make_entity(company=LEFT_COMPANY, fiscal_year=2025),
            make_entity(company=RIGHT_COMPANY, fiscal_year=2024),
        )

    assert error.value.reason == REASON_INCOMPARABLE_ENTITIES


def test_a_derived_value_spanning_two_years_is_rejected() -> None:
    """한쪽이 여러 연도의 Fact에서 나왔으면 그 값은 기간별 값이 아니다."""
    left, (numerator, _) = make_entity(company=LEFT_COMPANY, fiscal_year=2025)

    stale = make_fact(
        metric=DENOMINATOR_METRIC,
        value="1000",
        company=LEFT_COMPANY,
        fiscal_year=2024,
    )

    with pytest.raises(CalculationBlockedError) as error:
        validate_cross_entity_comparability(
            left=left,
            right=make_entity(company=RIGHT_COMPANY)[0],
            left_sources=(numerator, stale),
            right_sources=make_entity(company=RIGHT_COMPANY)[1],
            expected_unit=UNIT_PERCENT,
        )

    assert error.value.reason == REASON_INCOMPARABLE_ENTITIES


def test_different_source_metric_composition_is_not_comparable() -> None:
    """
    이름만 같고 정의가 다른 두 비율은 견줄 수 없다.

    CalculationResult에는 어느 파생 개념이었는지가 없다. 같은 개념에서 나왔는지는 원천 지표
    구성으로만 확인할 수 있다.
    """
    with pytest.raises(CalculationBlockedError) as error:
        validate(
            make_entity(company=LEFT_COMPANY),
            make_entity(
                company=RIGHT_COMPANY,
                numerator_metric=MetricConcept.HOLDING_SHARES,
                denominator_metric=DENOMINATOR_METRIC,
            ),
        )

    assert error.value.reason == REASON_INCOMPARABLE_ENTITIES


def test_swapped_numerator_and_denominator_is_caught_by_the_denominator_check() -> None:
    """
    Pattern D 전용 조건 7. 원천 지표 구성 검사만으로는 잡히지 않는다.

    분자와 분모를 뒤바꾼 비율은 원천 지표 집합이 같아서 그 검사를 통과하고, 뒤집힌 값도
    그럴듯한 백분율이다. 어느 Fact가 분모였는지는 Calculation의 참조에만 남아 있다.
    """
    swapped = make_entity(
        company=RIGHT_COMPANY,
        numerator_metric=DENOMINATOR_METRIC,
        denominator_metric=NUMERATOR_METRIC,
        numerator_value="1000",
        denominator_value="300",
    )

    # 원천 지표 구성은 같다. 그 검사만으로는 막히지 않는다는 것을 먼저 확인한다.
    left_metrics = {fact.metric for fact in make_entity(company=LEFT_COMPANY)[1]}

    assert left_metrics == {fact.metric for fact in swapped[1]}

    with pytest.raises(CalculationBlockedError) as error:
        validate(make_entity(company=LEFT_COMPANY), swapped)

    assert error.value.reason == REASON_INCOMPARABLE_ENTITIES
    assert "분모" in error.value.detail


def test_operation_mismatch_is_not_comparable() -> None:
    """연산이 다르면 두 값이 같은 것을 재고 있지 않다."""
    left, left_sources = make_entity(company=LEFT_COMPANY)
    right, right_sources = make_entity(company=RIGHT_COMPANY)

    other_operation = CalculationResult(
        calculation_id=right.calculation_id,
        operation=OPERATION_DIFF_PP,
        inputs=list(right.inputs),
        value=right.value,
        unit=right.unit,
        evidence_ids=list(right.evidence_ids),
    )

    with pytest.raises(CalculationBlockedError) as error:
        validate_cross_entity_comparability(
            left=left,
            right=other_operation,
            left_sources=left_sources,
            right_sources=right_sources,
            expected_unit=UNIT_PERCENT,
        )

    assert error.value.reason == REASON_INCOMPARABLE_ENTITIES


@pytest.mark.parametrize("unit", ["percentage_point", "원", None])
def test_output_unit_other_than_the_expected_one_is_not_comparable(
    unit: str | None,
) -> None:
    """
    결과 단위가 대응표의 단위와 다르면 견주지 않는다.

    "서로 같다"만 보지 않는다. 두 값이 똑같이 잘못된 단위를 달고 있어도 막아야 하며,
    percentage point를 percent로 읽으면 받는 쪽이 차이를 비율로 읽는다.
    """
    left, left_sources = make_entity(company=LEFT_COMPANY)
    right, right_sources = make_entity(company=RIGHT_COMPANY)

    mislabelled = CalculationResult(
        calculation_id=right.calculation_id,
        operation=right.operation,
        inputs=list(right.inputs),
        value=right.value,
        unit=unit,
        evidence_ids=list(right.evidence_ids),
    )

    with pytest.raises(CalculationBlockedError) as error:
        validate_cross_entity_comparability(
            left=left,
            right=mislabelled,
            left_sources=left_sources,
            right_sources=right_sources,
            expected_unit=UNIT_PERCENT,
        )

    assert error.value.reason == REASON_INCOMPARABLE_ENTITIES


def test_both_sides_mislabelled_with_the_same_unit_is_still_rejected() -> None:
    """단위가 서로 같기만 하면 통과하는 검사였다면 여기가 새어 나간다."""
    with pytest.raises(CalculationBlockedError) as error:
        validate(
            make_entity(company=LEFT_COMPANY, output_unit="percentage_point"),
            make_entity(company=RIGHT_COMPANY, output_unit="percentage_point"),
        )

    assert error.value.reason == REASON_INCOMPARABLE_ENTITIES


def test_missing_sources_cannot_be_judged() -> None:
    """원천 Fact가 없으면 어느 항목도 판정할 수 없다. 통과시키지 않는다."""
    left, left_sources = make_entity(company=LEFT_COMPANY)
    right, _ = make_entity(company=RIGHT_COMPANY)

    with pytest.raises(CalculationBlockedError) as error:
        validate_cross_entity_comparability(
            left=left,
            right=right,
            left_sources=left_sources,
            right_sources=(),
            expected_unit=UNIT_PERCENT,
        )

    assert error.value.reason == REASON_INCOMPARABLE_ENTITIES


def test_a_denominator_reference_outside_the_given_sources_is_rejected() -> None:
    """
    분모 참조가 원천 밖을 가리키면 무엇으로 나눈 값인지 확인할 수 없다.

    추측해서 통과시키지 않는다. 통과시키면 분모 개념 검사가 있으나 마나가 된다.
    """
    left, left_sources = make_entity(company=LEFT_COMPANY)
    right, right_sources = make_entity(company=RIGHT_COMPANY)

    dangling = CalculationResult(
        calculation_id=right.calculation_id,
        operation=right.operation,
        inputs=[
            right.inputs[0],
            ResultInputReference(
                input_type=ResultInputType.FACT, input_id="fact_없는_것"
            ),
        ],
        value=right.value,
        unit=right.unit,
        evidence_ids=list(right.evidence_ids),
    )

    with pytest.raises(CalculationBlockedError) as error:
        validate_cross_entity_comparability(
            left=left,
            right=dangling,
            left_sources=left_sources,
            right_sources=right_sources,
            expected_unit=UNIT_PERCENT,
        )

    assert error.value.reason == REASON_INCOMPARABLE_ENTITIES


# --------------------------------------------------------------------------
# 3. 관계 판정
#
# 저장된 값의 Decimal 비교로만 나온다. 차이 수치는 만들지 않는다.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left_numerator", "right_numerator", "expected"),
    [
        ("300", "200", ComparisonRelation.LEFT_GREATER),
        ("200", "300", ComparisonRelation.RIGHT_GREATER),
        ("300", "300", ComparisonRelation.EQUAL),
    ],
)
def test_relation_follows_the_stored_values(
    left_numerator: str,
    right_numerator: str,
    expected: ComparisonRelation,
) -> None:
    """세 관계가 모두 나온다. 우열이 없으면 equal이며 판정을 비우지 않는다."""
    left, _ = make_entity(company=LEFT_COMPANY, numerator_value=left_numerator)
    right, _ = make_entity(company=RIGHT_COMPANY, numerator_value=right_numerator)

    comparison = compare_calculations(
        comparison_id="comparison_test", left=left, right=right
    )

    assert comparison.relation is expected


def test_relation_reverses_when_the_two_sides_are_swapped() -> None:
    """
    left와 right는 질문에 나온 순서이며 판정의 방향을 정한다.

    순서를 잃으면 받는 쪽이 어느 기업이 좌측인지 다시 정해야 하고, 뒤집힌 판정도 그럴듯하다.
    """
    left, _ = make_entity(company=LEFT_COMPANY, numerator_value="300")
    right, _ = make_entity(company=RIGHT_COMPANY, numerator_value="200")

    forward = compare_calculations(comparison_id="c", left=left, right=right)

    backward = compare_calculations(comparison_id="c", left=right, right=left)

    assert forward.relation is ComparisonRelation.LEFT_GREATER
    assert backward.relation is ComparisonRelation.RIGHT_GREATER


def test_comparison_points_at_the_two_calculations() -> None:
    """
    비교 입력은 Calculation이다. Q11은 원천 Fact가 아니라 기업별 비율을 견준다
    (evidence_and_answerability.md: Q10은 Fact 2건, Q11·Q12는 Calculation 2건).
    """
    left, _ = make_entity(company=LEFT_COMPANY)
    right, _ = make_entity(company=RIGHT_COMPANY)

    comparison = compare_calculations(
        comparison_id="comparison_test", left=left, right=right
    )

    assert comparison.left.input_type is ResultInputType.CALCULATION
    assert comparison.right.input_type is ResultInputType.CALCULATION
    assert comparison.left.input_id == left.calculation_id
    assert comparison.right.input_id == right.calculation_id


def test_relation_is_judged_at_the_stored_precision() -> None:
    """
    저장된 값에서 판정한다.

    반올림 전 중간값으로 가리면 답변에 실린 두 수치로 검산했을 때 관계가 뒤집혀 보인다.
    저장 자릿수에서 같아지는 두 입력이 그 경우이며, 여기서는 equal이어야 한다.
    """
    left, _ = make_entity(
        company=LEFT_COMPANY, numerator_value="1", denominator_value="3"
    )

    right, _ = make_entity(
        company=RIGHT_COMPANY, numerator_value="10000000", denominator_value="30000001"
    )

    assert left.value == right.value

    comparison = compare_calculations(comparison_id="c", left=left, right=right)

    assert comparison.relation is ComparisonRelation.EQUAL


def test_a_difference_smaller_than_the_stored_quantum_does_not_flip_the_relation() -> None:
    """
    대조군. 저장 자릿수에서 갈리는 두 값은 그 자릿수대로 판정된다.

    위 검사만 있으면 "항상 equal"인 구현도 통과한다.
    """
    left, _ = make_entity(company=LEFT_COMPANY, numerator_value="300")
    right, _ = make_entity(company=RIGHT_COMPANY, numerator_value="300.001")

    assert Decimal(right.value) - Decimal(left.value) == QUANTUM

    comparison = compare_calculations(comparison_id="c", left=left, right=right)

    assert comparison.relation is ComparisonRelation.RIGHT_GREATER


@pytest.mark.parametrize("value", [None, "N/A", "NaN", "Infinity"])
def test_an_unusable_stored_value_stops_the_relation_judgment(value: str | None) -> None:
    """
    값을 수로 읽지 못하면 관계를 추측하지 않는다.

    코퍼스에 대한 판정이 아니므로 사유는 unusable_calculation_input이며, 그 사유는
    답변 가능성 대응표에 없어 실행 중단으로 남는다.
    """
    left, _ = make_entity(company=LEFT_COMPANY)
    right, _ = make_entity(company=RIGHT_COMPANY)

    broken = CalculationResult(
        calculation_id=right.calculation_id,
        operation=right.operation,
        inputs=list(right.inputs),
        value=value,
        unit=right.unit,
        evidence_ids=list(right.evidence_ids),
    )

    with pytest.raises(CalculationBlockedError) as error:
        compare_calculations(comparison_id="c", left=left, right=broken)

    assert error.value.reason == REASON_UNUSABLE_CALCULATION_INPUT


def test_comparison_does_not_produce_a_numeric_result() -> None:
    """
    ComparisonResult는 Calculation이 아니다.

    차이 수치를 담을 자리가 없어야 한다. 있으면 Q11이 만들지 않기로 한 값이 그 자리로
    돌아오고, Calculation 개수는 2인데 결과에는 세 번째 수치가 실린다.
    """
    left, _ = make_entity(company=LEFT_COMPANY)
    right, _ = make_entity(company=RIGHT_COMPANY)

    comparison = compare_calculations(comparison_id="c", left=left, right=right)

    assert not hasattr(comparison, "value")
    assert not hasattr(comparison, "unit")
    assert not hasattr(comparison, "operation")


# --------------------------------------------------------------------------
# 4. guard①은 기업 축에서도 그대로다
#
# Pattern C가 쓰는 것과 같은 함수이며, Pattern D가 재정의하지 않는다는 것을 고정한다.
# --------------------------------------------------------------------------


def test_guard_one_still_rejects_a_zero_denominator() -> None:
    """분모가 0이면 비율이 정의되지 않는다. 기업 축이라고 달라지지 않는다."""
    numerator = make_fact(
        metric=NUMERATOR_METRIC, value="300", company=LEFT_COMPANY
    )

    denominator = make_fact(
        metric=DENOMINATOR_METRIC, value="0", company=LEFT_COMPANY
    )

    with pytest.raises(CalculationBlockedError) as error:
        validate_calculation_inputs(
            numerator=numerator,
            denominator=denominator,
            numerator_metric=NUMERATOR_METRIC,
            denominator_metric=DENOMINATOR_METRIC,
        )

    assert error.value.reason == REASON_UNUSABLE_CALCULATION_INPUT


@pytest.mark.parametrize("value", [None, "해당사항없음", "NaN"])
def test_guard_one_still_rejects_an_unreadable_numerator(value: str | None) -> None:
    """값을 수로 읽지 못하면 계산하지 않는다."""
    numerator = make_fact(
        metric=NUMERATOR_METRIC, value=value, company=LEFT_COMPANY
    )

    denominator = make_fact(
        metric=DENOMINATOR_METRIC, value="1000", company=LEFT_COMPANY
    )

    with pytest.raises(CalculationBlockedError) as error:
        validate_calculation_inputs(
            numerator=numerator,
            denominator=denominator,
            numerator_metric=NUMERATOR_METRIC,
            denominator_metric=DENOMINATOR_METRIC,
        )

    assert error.value.reason == REASON_UNUSABLE_CALCULATION_INPUT


def test_the_ratio_keeps_the_numerator_and_denominator_order() -> None:
    """
    inputs는 분자, 분모 순서다.

    guard②의 분모 개념 검사가 이 순서를 따라간다. 순서가 무너지면 그 검사가 분자를 분모로
    읽고, 그래도 통과한다.
    """
    calculation, (numerator, denominator) = make_entity(company=LEFT_COMPANY)

    assert calculation.operation == OPERATION_RATIO
    assert [item.input_id for item in calculation.inputs] == [
        numerator.fact_id,
        denominator.fact_id,
    ]
