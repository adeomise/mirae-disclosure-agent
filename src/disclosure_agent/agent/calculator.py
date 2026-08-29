"""결정적 계산 모듈.

역할:
- 이미 선택된 Fact와 Calculation에서 수치형 파생 결과를 Python으로 산출한다.
- 계산 직전에 입력이 함께 쓸 수 있는 값인지 판정한다(C9 Comparability Validation).

무엇을 모르는가:
    질문 해석 결과(QueryPlan)도, 코퍼스도, 파생지표 대응표도 모른다. 무엇을 계산할지 고르는
    일은 handler가 하고, 이 모듈은 "받은 두 값으로 이 연산을 해도 되는가"와 "하면 얼마인가"만
    답한다.

    그래서 영업이익률이라는 개념이 이 파일에 없다. 분자와 분모가 어느 지표여야 하는지는
    호출자가 대응표에서 읽어 인자로 넘긴다. 여기에 하드코딩하면 대응표와 계산기가 같은
    질문에 서로 다른 답을 갖는 순간이 생기고, 파생 개념이 늘 때마다 이 파일이 함께 늘어난다.

    같은 이유로 코퍼스를 읽지 않는다. 값 확보는 agent.period_retrieval이 끝냈고, 이 모듈은
    확정된 값만 받는다. 덕분에 코퍼스 없이 계산 규칙 전체를 시험할 수 있다.

LLM이 수치를 만들지 않는다:
    이 파일의 모든 값은 Decimal 산술의 결과다. 문자열을 만들어 채우는 경로가 없고, 입력이
    규칙을 만족하지 못하면 값을 추측하지 않고 예외로 멈춘다.

두 guard의 구분:
    guard①  validate_calculation_inputs      Numeric Calculation 직전.
                                             잘못된 Fact로 파생값을 만드는 것을 막는다.
    guard②  validate_temporal_comparability  두 파생값을 함께 쓰기 직전.
                                             서로 견줄 수 없는 두 값을 견주는 것을 막는다.

    둘 다 C9 하나이며 적용 시점만 다르다
    (docs/agent_design/agent_patterns.md#comparability-validation의-적용-지점).
    연도별 guard①이 각각 통과해도 연도 사이의 어긋남은 남으므로, guard②를 건너뛰면
    2024년 연결 기준과 2025년 별도 기준의 차이가 조용히 나간다.

입력:
- Contract의 Fact / CalculationResult

출력:
- Contract의 CalculationResult

TODO:
- delta·delta_pct·aggregate 등 나머지 수치형 연산(Q07·Q12)
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from disclosure_agent.agent.answer_builder import (
    REASON_INCOMPARABLE_CALCULATION_INPUTS,
    REASON_INCOMPARABLE_PERIODS,
    REASON_UNUSABLE_CALCULATION_INPUT,
)
from disclosure_agent.common.enums import (
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import (
    CalculationResult,
    Fact,
    ResultInputReference,
)

__all__ = [
    "CalculationBlockedError",
    "OPERATION_DIFF_PP",
    "OPERATION_RATIO",
    "QUANTUM",
    "UNIT_PERCENTAGE_POINT",
    "diff",
    "ratio",
    "validate_calculation_inputs",
    "validate_temporal_comparability",
]

# 이 모듈이 수행할 수 있는 연산.
#
# docs/agent_design/query_plan.md의 operations 어휘와 같은 표기를 쓴다. 파생지표 대응표
# (agent.derived_metrics)도 같은 표기를 쓰지만, 그쪽은 "영업이익률은 ratio로 만든다"는
# 대응을 적는 자리이고 이쪽은 "ratio를 수행할 수 있다"는 능력을 적는 자리다. 두 값이
# 어긋나면 handler가 계산 전에 막는다(handlers/temporal_compare.py 참조).
OPERATION_RATIO = "ratio"
OPERATION_DIFF_PP = "diff_pp"

# 백분율 두 값의 차이가 갖는 단위.
#
# 대응표에 자리가 없어 여기서 정한다. 비율의 단위(percent)를 그대로 쓰지 않는 이유는,
# 45.69%와 -2.10%p가 같은 단위가 되면 받는 쪽이 차이를 비율로 읽기 때문이다.
UNIT_PERCENTAGE_POINT = "percentage_point"

# 비율을 백분율로 옮기는 배수.
PERCENT_SCALE = Decimal(100)

# 저장하는 소수 자릿수와 반올림 방식.
#
# 자릿수를 고정하는 이유는 나눗셈 결과가 무한소수일 수 있기 때문이다. 그대로 두면 값의
# 길이가 입력에 따라 달라지고, 같은 계산을 두 번 해도 표기가 흔들린다.
QUANTUM = Decimal("0.0001")
ROUNDING = ROUND_HALF_UP

# 나눗셈에 쓰는 유효자릿수. 기본 context에 기대지 않고 명시한다.
#
# 호출자가 전역 decimal context를 바꿔 두면 같은 입력이 다른 값을 낸다. 계산이 결정적이어야
# 한다는 것이 이 모듈의 전제이므로 context를 국소적으로 고정한다.
PRECISION = 28


class CalculationBlockedError(RuntimeError):
    """계산 입력이 규칙을 만족하지 못해 진행할 수 없는 경우.

    값을 추측하지 않고 상위 단계가 정지 지점으로 보고할 수 있게 한다. 어느 규칙이
    어긋났는지는 detail에 원문으로 남는다.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)

        self.reason = reason
        self.detail = detail


def _decimal(value: float | str | None, *, label: str) -> Decimal:
    """
    확정된 값을 Decimal로 읽는다.

    float를 거치지 않는다. str()로 한 번 옮기고 Decimal이 직접 파싱하게 두어야 이진 부동소수
    반올림이 끼어들지 않는다. Fact.normalized_value는 Contract상 float도 될 수 있으므로
    이 경로를 통과시킨다.

    NaN과 무한대는 파싱에 성공하므로 따로 막는다. 그대로 두면 뒤이은 quantize가 예외를 내고,
    실패 지점이 값이 아니라 반올림으로 보인다.
    """
    if value is None:
        raise CalculationBlockedError(
            REASON_UNUSABLE_CALCULATION_INPUT,
            f"{label}에 값이 없습니다. 값 없이 계산하지 않습니다.",
        )

    try:
        number = Decimal(str(value))

    except (InvalidOperation, ValueError) as error:
        raise CalculationBlockedError(
            REASON_UNUSABLE_CALCULATION_INPUT,
            f"{label}의 값을 수로 읽지 못했습니다: {value!r}",
        ) from error

    if not number.is_finite():
        raise CalculationBlockedError(
            REASON_UNUSABLE_CALCULATION_INPUT,
            f"{label}의 값이 유한한 수가 아닙니다: {value!r}",
        )

    return number


def _quantize(value: Decimal) -> Decimal:
    """저장할 자릿수로 반올림한다."""
    with localcontext() as context:
        context.prec = PRECISION

        return value.quantize(QUANTUM, rounding=ROUNDING)


def _merged_evidence_ids(*groups: Iterable[str]) -> list[str]:
    """
    입력들의 근거를 순서를 지키며 합친다.

    중복을 없애는 이유는 분자와 분모가 같은 표에서 읽힐 수 있기 때문이고, 순서를 지키는
    이유는 같은 입력이 항상 같은 결과를 내야 하기 때문이다.
    """
    return list(dict.fromkeys(item for group in groups for item in group))


def _fact_reference(fact: Fact) -> ResultInputReference:
    return ResultInputReference(input_type=ResultInputType.FACT, input_id=fact.fact_id)


def _calculation_reference(calculation: CalculationResult) -> ResultInputReference:
    return ResultInputReference(
        input_type=ResultInputType.CALCULATION,
        input_id=calculation.calculation_id,
    )


# --------------------------------------------------------------------------
# guard① — 계산 입력 검증
# --------------------------------------------------------------------------


def validate_calculation_inputs(
    *,
    numerator: Fact,
    denominator: Fact,
    numerator_metric: MetricConcept,
    denominator_metric: MetricConcept,
) -> None:
    """
    비율 계산 직전에 분자와 분모가 함께 쓸 수 있는 값인지 본다.

    어느 지표가 분자이고 분모인지는 호출자가 대응표에서 읽어 넘긴다. 여기서 정하면 파생
    개념이 늘 때마다 이 함수가 함께 늘어난다.

    두 사유로 나누는 이유:
        incomparable_calculation_inputs  값은 읽었으나 서로 다른 대상·기간·기준·단위여서
                                         함께 쓸 수 없다. 답변 가능성 판정으로 나간다.
        unusable_calculation_input       그 값으로는 산술이 성립하지 않는다. 코퍼스에 대한
                                         판정이 아니므로 실행 중단으로 남는다.

    Raises:
        CalculationBlockedError: 규칙 하나라도 어긋난 경우.
    """
    if numerator.metric is not numerator_metric:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_CALCULATION_INPUTS,
            f"분자로 요구된 지표는 {numerator_metric.value}인데 "
            f"{numerator.metric.value}를 받았습니다.",
        )

    if denominator.metric is not denominator_metric:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_CALCULATION_INPUTS,
            f"분모로 요구된 지표는 {denominator_metric.value}인데 "
            f"{denominator.metric.value}를 받았습니다.",
        )

    if numerator.company != denominator.company:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_CALCULATION_INPUTS,
            f"분자와 분모의 기업이 다릅니다: {numerator.company} / {denominator.company}",
        )

    if numerator.fiscal_year != denominator.fiscal_year:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_CALCULATION_INPUTS,
            f"분자와 분모의 회계연도가 다릅니다: "
            f"{numerator.fiscal_year} / {denominator.fiscal_year}",
        )

    if numerator.accounting_scope is not denominator.accounting_scope:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_CALCULATION_INPUTS,
            f"분자와 분모의 회계기준이 다릅니다: "
            f"{numerator.accounting_scope.value} / {denominator.accounting_scope.value}",
        )

    # 기준을 확정하지 못한 값은 "같다"고 말할 수 없다. 두 값이 모두 unknown이면 위 검사는
    # 통과하지만, 연결과 별도를 섞어 계산했을 가능성이 그대로 남는다.
    if numerator.accounting_scope is FactAccountingScope.UNKNOWN:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_CALCULATION_INPUTS,
            "회계기준을 확정하지 못한 Fact로는 계산하지 않습니다. "
            "연결과 별도를 섞어도 그럴듯한 값이 나옵니다.",
        )

    if numerator.normalized_unit is None or denominator.normalized_unit is None:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_CALCULATION_INPUTS,
            "정규화 단위가 없는 Fact로는 계산하지 않습니다: "
            f"분자={numerator.normalized_unit!r} 분모={denominator.normalized_unit!r}",
        )

    if numerator.normalized_unit != denominator.normalized_unit:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_CALCULATION_INPUTS,
            f"분자와 분모의 정규화 단위가 다릅니다: "
            f"{numerator.normalized_unit} / {denominator.normalized_unit}",
        )

    # 값 자체가 쓸 수 있는지는 여기서 함께 본다. 계산 직전에 확인하지 않으면 나눗셈 자리에서
    # 터지고, 그때는 어느 입력이 문제였는지가 결과에 남지 않는다.
    _decimal(numerator.normalized_value, label=f"분자 Fact {numerator.fact_id!r}")

    if _decimal(
        denominator.normalized_value, label=f"분모 Fact {denominator.fact_id!r}"
    ).is_zero():
        raise CalculationBlockedError(
            REASON_UNUSABLE_CALCULATION_INPUT,
            f"분모 Fact {denominator.fact_id!r}의 값이 0입니다. 비율이 정의되지 않습니다.",
        )


# --------------------------------------------------------------------------
# guard② — 비교 가능성 검증
# --------------------------------------------------------------------------


def validate_temporal_comparability(
    *,
    left: CalculationResult,
    right: CalculationResult,
    left_sources: Sequence[Fact],
    right_sources: Sequence[Fact],
    expected_unit: str,
) -> None:
    """
    두 기간의 파생값을 함께 쓰기 직전에 견줄 수 있는지 본다.

    원천 Fact를 함께 받는 이유:
        CalculationResult는 값과 단위와 참조만 담는다. 두 파생값이 같은 기업의 같은 기준에서
        나왔는지는 그 안에 없다. 참조를 따라가 확인하려면 이 함수가 결과 전체를 알아야 하므로,
        판정에 필요한 원천을 호출자가 직접 넘긴다.

    연도별 guard①으로는 잡히지 않는 것:
        guard①은 한 연도 안에서 분자와 분모의 기준이 같은지만 본다. 2024년이 연결이고
        2025년이 별도여도 각 연도의 검사는 통과한다. 연도 사이의 어긋남은 여기서만 잡힌다.

    Raises:
        CalculationBlockedError: 규칙 하나라도 어긋난 경우.
    """
    if not left_sources or not right_sources:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            "원천 Fact가 없는 파생값은 비교 가능성을 판정할 수 없습니다.",
        )

    sources = (*left_sources, *right_sources)

    companies = {fact.company for fact in sources}

    if len(companies) > 1:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"두 기간의 기업이 다릅니다: {', '.join(sorted(companies))}. "
            "시계열 비교는 같은 기업의 두 기간을 견줍니다.",
        )

    scopes = {fact.accounting_scope for fact in sources}

    if len(scopes) > 1:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"두 기간의 회계기준이 다릅니다: "
            f"{', '.join(sorted(scope.value for scope in scopes))}. "
            "연결 기준과 별도 기준의 비율은 견줄 수 없습니다.",
        )

    # 같은 파생 개념에서 나왔는지는 원천 지표 구성으로 본다. 분자·분모가 다르면 이름만 같고
    # 정의가 다른 두 비율을 견주게 된다.
    left_metrics = {fact.metric for fact in left_sources}
    right_metrics = {fact.metric for fact in right_sources}

    if left_metrics != right_metrics:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"두 기간의 원천 지표 구성이 다릅니다: "
            f"{', '.join(sorted(metric.value for metric in left_metrics))} / "
            f"{', '.join(sorted(metric.value for metric in right_metrics))}",
        )

    if left.operation != right.operation:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"두 기간의 연산이 다릅니다: {left.operation} / {right.operation}",
        )

    if left.unit != expected_unit or right.unit != expected_unit:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"두 기간의 단위가 {expected_unit}가 아닙니다: "
            f"{left.unit!r} / {right.unit!r}",
        )

    left_years = {fact.fiscal_year for fact in left_sources}
    right_years = {fact.fiscal_year for fact in right_sources}

    if len(left_years) > 1 or len(right_years) > 1:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            "한 파생값이 여러 회계연도의 Fact에서 나왔습니다. "
            "기간별 값이 아니면 변화량을 만들 수 없습니다.",
        )

    if left_years == right_years:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"두 파생값의 회계연도가 같습니다: {left_years.pop()}. "
            "같은 기간끼리는 변화량이 정의되지 않습니다.",
        )


# --------------------------------------------------------------------------
# 수치형 파생 결과
# --------------------------------------------------------------------------


def ratio(
    *,
    calculation_id: str,
    numerator: Fact,
    denominator: Fact,
    output_unit: str,
) -> CalculationResult:
    """
    Fact 두 건에서 백분율 비율을 만든다.

        numerator / denominator * 100

    입력 검증은 하지 않는다. 호출자가 validate_calculation_inputs를 먼저 부르며, 두 판정을
    한 함수에 섞으면 "검증만 하고 계산은 하지 않는" 경로를 시험할 수 없다. 다만 값을 읽는
    과정에서 발견되는 실패(값 없음·파싱 불가·분모 0)는 여기서도 예외로 나간다. 그 자리에서
    조용히 진행하면 잘못된 수치가 만들어지기 때문이다.

    inputs는 분자, 분모 순서다. 순서를 잃으면 받는 쪽이 어느 쪽이 분자인지 다시 정해야 하고,
    뒤집어도 그럴듯한 비율이 나오므로 틀렸다는 사실이 결과에 드러나지 않는다.
    """
    numerator_value = _decimal(
        numerator.normalized_value, label=f"분자 Fact {numerator.fact_id!r}"
    )

    denominator_value = _decimal(
        denominator.normalized_value, label=f"분모 Fact {denominator.fact_id!r}"
    )

    if denominator_value.is_zero():
        raise CalculationBlockedError(
            REASON_UNUSABLE_CALCULATION_INPUT,
            f"분모 Fact {denominator.fact_id!r}의 값이 0입니다. 비율이 정의되지 않습니다.",
        )

    with localcontext() as context:
        context.prec = PRECISION

        value = numerator_value / denominator_value * PERCENT_SCALE

    return CalculationResult(
        calculation_id=calculation_id,
        operation=OPERATION_RATIO,
        inputs=[_fact_reference(numerator), _fact_reference(denominator)],
        value=str(_quantize(value)),
        unit=output_unit,
        evidence_ids=_merged_evidence_ids(numerator.evidence_ids, denominator.evidence_ids),
    )


def diff(
    *,
    calculation_id: str,
    old: CalculationResult,
    new: CalculationResult,
) -> CalculationResult:
    """
    백분율 두 건의 변화량을 percentage point로 만든다.

        new - old

    저장된 값에서 뺀다. 반올림 전 중간값에서 빼면 답변에 실린 두 수치의 뺄셈과 결과가
    어긋날 수 있고, 그러면 받는 쪽이 검산했을 때 틀린 것처럼 보인다.

    inputs는 old, new 순서다. 뒤집으면 부호가 뒤집히고, 부호가 뒤집힌 변화량도 그럴듯하다.
    """
    old_value = _decimal(old.value, label=f"이전 Calculation {old.calculation_id!r}")

    new_value = _decimal(new.value, label=f"이후 Calculation {new.calculation_id!r}")

    with localcontext() as context:
        context.prec = PRECISION

        value = new_value - old_value

    return CalculationResult(
        calculation_id=calculation_id,
        operation=OPERATION_DIFF_PP,
        inputs=[_calculation_reference(old), _calculation_reference(new)],
        value=str(_quantize(value)),
        unit=UNIT_PERCENTAGE_POINT,
        evidence_ids=_merged_evidence_ids(old.evidence_ids, new.evidence_ids),
    )
