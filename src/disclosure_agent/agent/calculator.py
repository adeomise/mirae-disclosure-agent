"""결정적 계산 모듈.

역할:
- 이미 선택된 Fact와 Calculation에서 수치형 파생 결과를 Python으로 산출한다.
- 계산 직전에 입력이 함께 쓸 수 있는 값인지 판정한다(C9 Comparability Validation).
- 확정된 두 파생값의 대소 관계를 판정한다(Comparison Judgment).

관계 판정을 여기서 하는 이유:
    ComparisonResult는 수치형 파생 결과가 아니며 Calculation 개수에 더해지지 않는다
    (evidence_and_answerability.md Calculation cardinality). 그럼에도 이 모듈에 두는 것은,
    두 값의 대소를 가리려면 저장된 문자열을 다시 Decimal로 읽어야 하고, 그 규칙은 이미
    이 파일에 있기 때문이다. handler에 두면 같은 파싱 규칙이 두 벌이 되고, 한쪽이 float로
    비교하기 시작하는 순간 같은 두 값이 다르다고 판정된다.

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
    guard①  validate_calculation_inputs          Numeric Calculation 직전.
                                                 잘못된 Fact로 파생값을 만드는 것을 막는다.
    guard②  validate_temporal_comparability      두 파생값을 함께 쓰기 직전(기간 축).
    guard②  validate_cross_entity_comparability  두 파생값을 함께 쓰기 직전(기업 축).
    guard②  validate_temporal_fact_comparability
                                                 두 원천 Fact를 함께 쓰기 직전(기간 축).
    guard②  validate_cross_entity_fact_comparability
                                                 두 원천 Fact를 함께 쓰기 직전(기업 축).

    다섯 다 C9 하나이며 적용 시점만 다르다
    (docs/agent_design/agent_patterns.md#comparability-validation의-적용-지점).
    연도별 guard①이 각각 통과해도 연도 사이의 어긋남은 남으므로, guard②를 건너뛰면
    2024년 연결 기준과 2025년 별도 기준의 차이가 조용히 나간다.

guard②이 축마다 따로 있는 이유:
    두 함수가 요구하는 것이 정반대인 항목이 있다. 시계열 비교는 같은 기업의 다른 두 연도를
    요구하고, 기업 간 비교는 다른 두 기업의 같은 연도를 요구한다. 한 함수에 플래그로
    묶으면 그 플래그가 반대로 설정된 호출 하나가 같은 기업을 견주거나 같은 연도를
    시계열로 읽게 되고, 그 결과도 그럴듯하다. 공통 항목은 모듈 내부 primitive로 공유한다
    (agent_patterns.md Pattern D 검증 조건: "동일 기업(1)은 Pattern C 전용이며 상속되지
    않는다").

기업 축 guard②가 입력 타입마다 둘인 이유:
    Q11·Q12는 기업별 Calculation 2건을 견주고, Q10은 Calculation 없이 원천 Fact 2건을 바로
    견준다(evidence_and_answerability.md Calculation cardinality: Q10은 Calculation 0).
    두 경로가 판정하는 축은 같지만 판정할 것이 담긴 자리가 다르다.

        Calculation 경로  단위·연산은 CalculationResult에, 기업·기간·기준은 원천 Fact에.
        Fact 경로         전부 Fact 하나에 있다. 대신 Calculation에만 있는 연산·분모 개념은
                          판정할 대상 자체가 없다.

    한 함수가 두 입력 타입을 모두 받으면 "이 인자는 이쪽 경로에서만 의미가 있다"는 분기가
    함수 안에 쌓이고, 그 분기를 잘못 탄 호출 하나가 단위 검사를 건너뛴다. Q10은 guard①을
    지나지 않아 단위 정규화 성공을 확인하는 자리가 guard② 하나뿐이므로
    (agent_patterns.md "Q10처럼 Calculation 없이 Fact를 바로 견주는 경로에서는 여기가 유일한
    guard"), 그 자리를 건너뛰면 천원과 원이 그대로 견줘진다.

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
    REASON_INCOMPARABLE_ENTITIES,
    REASON_INCOMPARABLE_PERIODS,
    REASON_UNUSABLE_CALCULATION_INPUT,
)
from disclosure_agent.common.enums import (
    ComparisonRelation,
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import (
    CalculationResult,
    ComparisonResult,
    Fact,
    ResultInputReference,
)

__all__ = [
    "CalculationBlockedError",
    "OPERATION_DELTA",
    "OPERATION_DELTA_PCT",
    "OPERATION_DIFF_PP",
    "OPERATION_RATIO",
    "QUANTUM",
    "UNIT_PERCENT",
    "UNIT_PERCENTAGE_POINT",
    "compare_calculations",
    "compare_facts",
    "delta",
    "delta_pct",
    "diff",
    "ratio",
    "validate_calculation_inputs",
    "validate_cross_entity_comparability",
    "validate_cross_entity_fact_comparability",
    "validate_temporal_comparability",
    "validate_temporal_fact_comparability",
]

# 이 모듈이 수행할 수 있는 연산.
#
# docs/agent_design/query_plan.md의 operations 어휘와 같은 표기를 쓴다. 파생지표 대응표
# (agent.derived_metrics)도 같은 표기를 쓰지만, 그쪽은 "영업이익률은 ratio로 만든다"는
# 대응을 적는 자리이고 이쪽은 "ratio를 수행할 수 있다"는 능력을 적는 자리다. 두 값이
# 어긋나면 handler가 계산 전에 막는다(handlers/temporal_compare.py 참조).
OPERATION_RATIO = "ratio"
OPERATION_DIFF_PP = "diff_pp"

# 원천 값 두 건의 증감. 파생값을 거치지 않고 Fact에서 바로 나온다.
#
# diff_pp와 나누는 이유는 입력이 다르기 때문이다. diff_pp는 이미 백분율인 두 파생값의 차이를
# percentage point로 내고, delta는 같은 단위를 가진 두 원천 값의 차이를 그 단위 그대로 낸다.
# 한 이름으로 묶으면 결과 단위가 입력에 따라 달라지는 연산이 되고, 받는 쪽이 3.67이 %p인지
# 원인지 operation만 보고는 알 수 없다.
OPERATION_DELTA = "delta"
OPERATION_DELTA_PCT = "delta_pct"

# 백분율 두 값의 차이가 갖는 단위.
#
# 대응표에 자리가 없어 여기서 정한다. 비율의 단위(percent)를 그대로 쓰지 않는 이유는,
# 45.69%와 -2.10%p가 같은 단위가 되면 받는 쪽이 차이를 비율로 읽기 때문이다.
UNIT_PERCENTAGE_POINT = "percentage_point"

# 증감률의 단위.
#
# UNIT_PERCENTAGE_POINT와 같은 자리에서 같은 이유로 정한다. 증감률은 입력이 원이든 주식 수든
# 언제나 백분율이므로 파생지표 대응표가 정할 값이 아니고, 그 대응표에 delta_pct 항목도 없다.
#
# agent.derived_metrics에도 같은 표기의 상수가 있지만 그쪽에서 가져오지 않는다. 이 모듈은
# 파생 개념을 모르는 것이 전제이며(모듈 docstring), 대응표를 import하는 순간 "영업이익률의
# 결과 단위"와 "증감률의 단위"가 한 값을 공유하게 된다. 둘은 우연히 같은 문자열일 뿐이다.
UNIT_PERCENT = "percent"

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

# 비율 Calculation 하나가 갖는 입력 건수. 분자와 분모다.
#
# ratio가 그 순서로 적고 _ratio_denominator가 그 순서를 되짚는다. 두 곳이 같은 값을 봐야
# 하므로 상수로 둔다.
RATIO_INPUT_COUNT = 2


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
#
# 축마다 함수가 따로 있고, 두 함수가 똑같이 요구하는 항목만 아래 primitive가 나눠 갖는다.
# 공통 항목까지 각자 적으면 한쪽에만 검사가 추가되는 날이 오고, 그때 어느 쪽이 정본인지 알
# 수 없다. 반대로 축마다 다른 항목(같은 기업인가 / 다른 기업인가, 다른 연도인가 / 같은
# 연도인가)을 한 함수에 플래그로 묶으면, 플래그가 반대로 설정된 호출 하나가 조용히 통과한다.
# --------------------------------------------------------------------------


def _require_sources(
    left_sources: Sequence[Fact],
    right_sources: Sequence[Fact],
    *,
    reason: str,
) -> None:
    """원천 Fact 없이는 어느 항목도 판정할 수 없다."""
    if not left_sources or not right_sources:
        raise CalculationBlockedError(
            reason,
            "원천 Fact가 없는 파생값은 비교 가능성을 판정할 수 없습니다.",
        )


def _require_single_scope(
    sources: Sequence[Fact],
    *,
    reason: str,
    subject: str,
) -> FactAccountingScope:
    """두 파생값이 같은 회계기준에서 나왔는지 본다. 확정된 기준 하나를 돌려준다."""
    scopes = {fact.accounting_scope for fact in sources}

    if len(scopes) > 1:
        raise CalculationBlockedError(
            reason,
            f"{subject}의 회계기준이 다릅니다: "
            f"{', '.join(sorted(scope.value for scope in scopes))}. "
            "연결 기준과 별도 기준의 비율은 견줄 수 없습니다.",
        )

    return scopes.pop()


def _require_same_source_metrics(
    left_sources: Sequence[Fact],
    right_sources: Sequence[Fact],
    *,
    reason: str,
    subject: str,
) -> None:
    """
    같은 파생 개념에서 나왔는지를 원천 지표 구성으로 본다.

    CalculationResult에는 어느 파생 개념이었는지가 없다. 값·단위·연산·참조뿐이므로, 두 비율이
    같은 정의에서 나왔는지는 그 비율을 만든 원천 지표 집합으로만 확인할 수 있다. 분자·분모가
    다르면 이름만 같고 정의가 다른 두 비율을 견주게 된다.
    """
    left_metrics = {fact.metric for fact in left_sources}
    right_metrics = {fact.metric for fact in right_sources}

    if left_metrics != right_metrics:
        raise CalculationBlockedError(
            reason,
            f"{subject}의 원천 지표 구성이 다릅니다: "
            f"{', '.join(sorted(metric.value for metric in left_metrics))} / "
            f"{', '.join(sorted(metric.value for metric in right_metrics))}",
        )


def _require_same_operation(
    left: CalculationResult,
    right: CalculationResult,
    *,
    reason: str,
    subject: str,
) -> None:
    """연산이 다르면 두 값이 같은 것을 재고 있지 않다."""
    if left.operation != right.operation:
        raise CalculationBlockedError(
            reason,
            f"{subject}의 연산이 다릅니다: {left.operation} / {right.operation}",
        )


def _require_expected_unit(
    left: CalculationResult,
    right: CalculationResult,
    *,
    expected_unit: str,
    reason: str,
    subject: str,
) -> None:
    """
    두 값의 단위가 서로 같고, 호출자가 대응표에서 읽어 온 단위와도 같은지 본다.

    "서로 같다"만 보지 않는 이유는 둘 다 같은 값으로 잘못 만들어진 경우를 통과시키기
    때문이다. 기대 단위를 인자로 받는 이유는 이 파일이 파생 개념을 모르기 때문이며,
    영업이익률의 결과 단위가 percent라는 사실은 agent.derived_metrics의 대응표에만 있다.
    """
    if left.unit != expected_unit or right.unit != expected_unit:
        raise CalculationBlockedError(
            reason,
            f"{subject}의 단위가 {expected_unit}가 아닙니다: "
            f"{left.unit!r} / {right.unit!r}",
        )


def _require_single_fiscal_year(
    left_sources: Sequence[Fact],
    right_sources: Sequence[Fact],
    *,
    reason: str,
) -> tuple[set[int | None], set[int | None]]:
    """
    각 파생값이 회계연도 하나에서 나왔는지 본다.

    한쪽이 여러 연도의 Fact에서 나왔다면 그 값은 기간별 값이 아니고, 그것을 다른 기간이나
    다른 기업과 견주면 무엇과 무엇을 견줬는지 결과에 남지 않는다.
    """
    left_years = {fact.fiscal_year for fact in left_sources}
    right_years = {fact.fiscal_year for fact in right_sources}

    if len(left_years) > 1 or len(right_years) > 1:
        raise CalculationBlockedError(
            reason,
            "한 파생값이 여러 회계연도의 Fact에서 나왔습니다. "
            "기간별 값이 아니면 견줄 수 없습니다.",
        )

    return left_years, right_years


def _require_single_company(
    sources: Sequence[Fact],
    *,
    reason: str,
    subject: str,
) -> str:
    """한 파생값이 기업 하나에서 나왔는지 본다. 확정된 기업명을 돌려준다."""
    companies = {fact.company for fact in sources}

    if len(companies) > 1:
        raise CalculationBlockedError(
            reason,
            f"{subject}이 여러 기업의 Fact에서 나왔습니다: "
            f"{', '.join(sorted(companies))}. "
            "기업이 섞인 값은 어느 기업의 값도 아닙니다.",
        )

    return companies.pop()


def _ratio_denominator(
    calculation: CalculationResult,
    sources: Sequence[Fact],
    *,
    reason: str,
    subject: str,
) -> Fact:
    """
    비율 Calculation이 실제로 분모로 쓴 Fact를 참조에서 되짚는다.

    호출자가 넘긴 sources의 순서를 믿지 않는다. 순서로 고르면 sources를 뒤집어 넘긴 호출
    하나가 분자를 분모로 읽고, 그래도 검사는 통과한다. ratio가 inputs를 분자·분모 순서로
    적어 두므로 그 참조를 따라간다.
    """
    if len(calculation.inputs) != RATIO_INPUT_COUNT:
        raise CalculationBlockedError(
            reason,
            f"{subject}의 비율 Calculation 입력이 {RATIO_INPUT_COUNT}건이 아닙니다: "
            f"{len(calculation.inputs)}건. 분모를 특정할 수 없습니다.",
        )

    reference = calculation.inputs[1]

    if reference.input_type is not ResultInputType.FACT:
        raise CalculationBlockedError(
            reason,
            f"{subject}의 비율 분모가 Fact 참조가 아닙니다: {reference.input_type.value}.",
        )

    denominator = {fact.fact_id: fact for fact in sources}.get(reference.input_id)

    if denominator is None:
        raise CalculationBlockedError(
            reason,
            f"{subject}의 비율 분모 {reference.input_id!r}를 원천 Fact에서 찾지 못했습니다.",
        )

    return denominator


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

    이 함수 전용 조건은 둘이다. 같은 기업이어야 하고, 두 회계연도가 서로 달라야 한다. 둘 다
    기업 간 비교에서는 반대이므로 validate_cross_entity_comparability를 따로 둔다.

    Raises:
        CalculationBlockedError: 규칙 하나라도 어긋난 경우.
    """
    _require_sources(left_sources, right_sources, reason=REASON_INCOMPARABLE_PERIODS)

    sources = (*left_sources, *right_sources)

    companies = {fact.company for fact in sources}

    if len(companies) > 1:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"두 기간의 기업이 다릅니다: {', '.join(sorted(companies))}. "
            "시계열 비교는 같은 기업의 두 기간을 견줍니다.",
        )

    _require_single_scope(sources, reason=REASON_INCOMPARABLE_PERIODS, subject="두 기간")

    _require_same_source_metrics(
        left_sources,
        right_sources,
        reason=REASON_INCOMPARABLE_PERIODS,
        subject="두 기간",
    )

    _require_same_operation(
        left, right, reason=REASON_INCOMPARABLE_PERIODS, subject="두 기간"
    )

    _require_expected_unit(
        left,
        right,
        expected_unit=expected_unit,
        reason=REASON_INCOMPARABLE_PERIODS,
        subject="두 기간",
    )

    left_years, right_years = _require_single_fiscal_year(
        left_sources, right_sources, reason=REASON_INCOMPARABLE_PERIODS
    )

    if left_years == right_years:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"두 파생값의 회계연도가 같습니다: {left_years.pop()}. "
            "같은 기간끼리는 변화량이 정의되지 않습니다.",
        )


def validate_temporal_fact_comparability(
    *,
    previous: Fact,
    current: Fact,
    expected_metric: MetricConcept,
    expected_previous_year: int,
    expected_current_year: int,
) -> None:
    """
    같은 기업 두 기간의 원천 Fact를 함께 쓰기 직전에 견줄 수 있는지 본다(Q07).

    이 경로에서 guard가 하나뿐인 이유:
        Q07은 기간별 파생값을 만들지 않는다. 증감 수치의 입력이 곧 두 기간의 원천 Fact이므로
        guard①이 적용될 자리가 없고, 단위 정규화 성공(C8)을 확인하는 자리도 여기 하나다
        (agent_patterns.md Pattern C 수치 경로의 "Q07은 기간별 파생값 없음").

        Q10이 같은 이유로 guard②에서 단위를 함께 보는 것과 같은 구조이며, 축만 다르다.
        그쪽은 다른 두 기업의 같은 연도를 요구하고 이쪽은 같은 기업의 다른 두 연도를 요구한다.

    요청한 지표와 두 회계연도를 함께 받는 이유:
        두 Fact가 서로 일치하는 것만으로는 부족하다. 둘 다 2023년과 2024년 값이면 서로는
        일치하지만 FY2024 대 FY2025를 물은 질문에는 답하지 않은 결과가 되고, 그 사실이 결과
        어디에도 남지 않는다. validate_cross_entity_fact_comparability가 같은 이유로 지표와
        연도를 받는 것과 같은 자리다.

    연도 순서를 여기서 보는 이유:
        증감은 방향을 가진 값이다. previous와 current가 뒤바뀌면 부호가 뒤집히고, 부호가
        뒤집힌 증감도 그럴듯하다. 호출자가 sorted()로 넘긴다는 약속에 기대면 그 약속을 어긴
        호출 하나가 조용히 통과한다.

    이전 값이 0인 경우를 여기서 막는 이유:
        증감액은 정의되지만 증감률은 정의되지 않는다. 한쪽만 만들면 질문이 요구한 수치 두 건
        중 하나만 남고, 남은 하나로 나머지를 되짚을 수도 없다. 확보한 값으로 답할 수 없다는
        판정을 계산 직전에 한 번에 내린다.

    결산월 일치는 보지 않는다. Fact에도 Evidence에도 담을 자리가 없다
    (validate_cross_entity_fact_comparability의 같은 항목 참조).

    Raises:
        CalculationBlockedError: 규칙 하나라도 어긋난 경우.
    """
    # 판정 순서는 좁히는 순서를 따른다. 누구를 -> 무엇을 -> 언제 -> 어떤 기준으로 ->
    # 어떤 단위로 -> 어떤 값으로. 기업 간 경로와 같은 순서다.
    if previous.company != current.company:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"두 기간의 기업이 다릅니다: {previous.company} / {current.company}. "
            "시계열 비교는 같은 기업의 두 기간을 견줍니다.",
        )

    if previous.metric is not current.metric:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"두 기간의 지표가 다릅니다: "
            f"{previous.metric.value} / {current.metric.value}",
        )

    # 서로 같은 것만 보지 않는다. 둘 다 같은 값으로 잘못 확보된 경우를 통과시키기 때문이며,
    # _require_expected_unit이 Calculation 경로에서 기대 단위를 함께 보는 것과 같은 이유다.
    if previous.metric is not expected_metric:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"질문이 요청한 지표는 {expected_metric.value}인데 "
            f"{previous.metric.value}를 받았습니다.",
        )

    if previous.fiscal_year is None or current.fiscal_year is None:
        # 연도를 모르는 값끼리는 어느 쪽이 이전인지 정할 수 없다. 순서를 추측하면 부호가
        # 뒤집힌 증감이 나가고, 뒤집힌 값도 그럴듯하다.
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            "회계연도를 확정하지 못한 Fact로는 증감을 만들지 않습니다: "
            f"{previous.fact_id!r}={previous.fiscal_year!r} "
            f"{current.fact_id!r}={current.fiscal_year!r}",
        )

    if previous.fiscal_year == current.fiscal_year:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"두 Fact의 회계연도가 같습니다: {previous.fiscal_year}. "
            "같은 기간끼리는 증감이 정의되지 않습니다.",
        )

    if previous.fiscal_year > current.fiscal_year:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"이전 기간의 회계연도가 이후 기간보다 뒤입니다: "
            f"{previous.fiscal_year} > {current.fiscal_year}. "
            "순서가 뒤바뀌면 증감의 부호가 뒤집힙니다.",
        )

    if (previous.fiscal_year, current.fiscal_year) != (
        expected_previous_year,
        expected_current_year,
    ):
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"질문이 요청한 회계연도는 {expected_previous_year} -> "
            f"{expected_current_year}인데 "
            f"{previous.fiscal_year} -> {current.fiscal_year}의 값을 받았습니다.",
        )

    if previous.fiscal_period != current.fiscal_period:
        # 연간 값과 분기 값의 차이는 증감이 아니다. 현재 periodic.adapter는 이 자리를 채우지
        # 않아 양쪽 모두 None이지만(periodic/adapter.py), 채워지는 날 이 검사가 없으면
        # 사업보고서의 연간 값과 분기보고서의 분기 값이 조용히 견줘진다.
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"두 기간의 회계기간 구분이 다릅니다: "
            f"{previous.fiscal_period!r} / {current.fiscal_period!r}",
        )

    scope = _require_single_scope(
        (previous, current), reason=REASON_INCOMPARABLE_PERIODS, subject="두 기간"
    )

    # 기준을 확정하지 못한 값끼리는 "같다"고 말할 수 없다. 두 기간이 모두 unknown이면 위
    # 검사는 통과하지만, 한 해가 연결이고 다른 해가 별도일 가능성이 그대로 남는다.
    if scope is FactAccountingScope.UNKNOWN:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            "회계기준을 확정하지 못한 Fact로는 증감을 만들지 않습니다. "
            "연결과 별도를 섞어도 그럴듯한 증감이 나옵니다.",
        )

    if previous.normalized_unit is None or current.normalized_unit is None:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            "정규화 단위가 없는 Fact로는 증감을 만들지 않습니다: "
            f"{previous.fact_id!r}={previous.normalized_unit!r} "
            f"{current.fact_id!r}={current.normalized_unit!r}",
        )

    if previous.normalized_unit != current.normalized_unit:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_PERIODS,
            f"두 기간의 정규화 단위가 다릅니다: "
            f"{previous.normalized_unit} / {current.normalized_unit}. "
            "정규화에 실패한 값끼리 빼면 자릿수가 어긋난 증감이 나갑니다.",
        )

    # 값 자체가 쓸 수 있는지는 마지막에 본다. 계산 직전에 확인하지 않으면 뺄셈이나 나눗셈
    # 자리에서 터지고, 그때는 어느 입력이 문제였는지가 결과에 남지 않는다.
    #
    # 여기서 나가는 사유는 위와 다르다. unusable_calculation_input은 "그 값으로는 산술이
    # 성립하지 않는다"이고 대응표에 없으므로 실행 중단으로 남는다. 코퍼스에 답이 없다는
    # 판정이 아니기 때문이며, guard①이 같은 자리에서 같은 사유를 쓰는 것과 같다.
    _decimal(current.normalized_value, label=f"이후 Fact {current.fact_id!r}")

    if _decimal(
        previous.normalized_value, label=f"이전 Fact {previous.fact_id!r}"
    ).is_zero():
        raise CalculationBlockedError(
            REASON_UNUSABLE_CALCULATION_INPUT,
            f"이전 Fact {previous.fact_id!r}의 값이 0입니다. 증감률이 정의되지 않습니다.",
        )


def validate_cross_entity_comparability(
    *,
    left: CalculationResult,
    right: CalculationResult,
    left_sources: Sequence[Fact],
    right_sources: Sequence[Fact],
    expected_unit: str,
) -> None:
    """
    두 기업의 파생값을 함께 쓰기 직전에 견줄 수 있는지 본다.

    validate_temporal_comparability를 그대로 쓰지 않는 이유:
        그쪽은 "같은 기업"을 요구한다. 기업 간 비교에서는 서로 다른 두 기업이 정상 입력이므로
        그 조건을 상속하면 정상 질문이 전부 막힌다(agent_patterns.md Pattern D 검증 조건,
        evidence_and_answerability.md "동일 기업(1)은 Pattern C temporal comparison 전용").
        회계연도 조건도 정반대다. 시계열은 서로 달라야 하고 기업 간 비교는 같아야 한다.

    기업별 guard①으로는 잡히지 않는 것:
        guard①은 한 기업 안에서 분자와 분모의 기업·기간·기준·단위가 맞는지만 본다. A사가
        연결이고 B사가 별도여도, A사가 2025년이고 B사가 2024년이어도 각 기업의 검사는
        통과한다. 기업 사이의 어긋남은 여기서만 잡힌다.

    분모 개념을 따로 보는 이유(Pattern D 전용 조건 7):
        원천 지표 구성 검사는 집합으로 본다. 분자와 분모가 뒤바뀐 두 비율은 그 집합이 같아서
        통과하고, 뒤집힌 비율도 그럴듯한 백분율이다. 어느 Fact가 분모였는지는 Calculation의
        참조에만 남아 있으므로 그 참조를 따라간다.

    결산월 일치(조건 5)는 아직 보지 않는다:
        Fact에도 Evidence에도 결산월을 담을 자리가 없고, manifest의 base_month는 확보 계층이
        공시를 고를 때 쓰고 결과에 싣지 않는다. 없는 값을 추측해 검사하면 통과 여부가 실제
        결산월과 무관해지므로, 자리가 생길 때까지 검사하지 않는 사실을 남긴다.

    Raises:
        CalculationBlockedError: 규칙 하나라도 어긋난 경우.
    """
    _require_sources(left_sources, right_sources, reason=REASON_INCOMPARABLE_ENTITIES)

    # 판정 순서는 좁히는 순서를 따른다. 누구를 -> 언제 -> 어떤 기준으로 -> 무엇을 -> 어떻게.
    left_company = _require_single_company(
        left_sources, reason=REASON_INCOMPARABLE_ENTITIES, subject="좌측 파생값"
    )

    right_company = _require_single_company(
        right_sources, reason=REASON_INCOMPARABLE_ENTITIES, subject="우측 파생값"
    )

    if left_company == right_company:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_ENTITIES,
            f"두 파생값의 기업이 같습니다: {left_company}. "
            "기업 간 비교는 서로 다른 두 기업을 견줍니다.",
        )

    left_years, right_years = _require_single_fiscal_year(
        left_sources, right_sources, reason=REASON_INCOMPARABLE_ENTITIES
    )

    if left_years != right_years:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_ENTITIES,
            f"두 기업의 회계연도가 다릅니다: {left_years.pop()} / {right_years.pop()}. "
            "기업 간 비교는 같은 기간의 두 기업을 견줍니다.",
        )

    sources = (*left_sources, *right_sources)

    scope = _require_single_scope(
        sources, reason=REASON_INCOMPARABLE_ENTITIES, subject="두 기업"
    )

    # 기준을 확정하지 못한 값끼리는 "같다"고 말할 수 없다. 두 기업이 모두 unknown이면 위
    # 검사는 통과하지만, 한쪽이 연결이고 다른 쪽이 별도일 가능성이 그대로 남는다.
    #
    # guard①이 기업별로 이미 막는다. 그래도 여기서 다시 보는 이유는, 이 함수가 guard①을
    # 거치지 않은 입력으로도 불릴 수 있고 그때 이 검사가 유일한 방어선이기 때문이다.
    if scope is FactAccountingScope.UNKNOWN:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_ENTITIES,
            "회계기준을 확정하지 못한 Fact에서 나온 파생값은 견주지 않습니다. "
            "연결과 별도를 섞어도 그럴듯한 값이 나옵니다.",
        )

    _require_same_source_metrics(
        left_sources,
        right_sources,
        reason=REASON_INCOMPARABLE_ENTITIES,
        subject="두 기업",
    )

    _require_same_operation(
        left, right, reason=REASON_INCOMPARABLE_ENTITIES, subject="두 기업"
    )

    _require_expected_unit(
        left,
        right,
        expected_unit=expected_unit,
        reason=REASON_INCOMPARABLE_ENTITIES,
        subject="두 기업",
    )

    # 분모 개념 검사는 비율에만 적용된다. 집계(Q12)처럼 분모가 없는 연산에는 확인할 것이
    # 없고, 없는 것을 요구하면 그 연산을 붙이는 쪽이 이 guard 전체를 우회하게 된다.
    #
    # 위에서 두 연산이 같다는 것을 이미 확인했으므로 한쪽만 본다.
    if left.operation != OPERATION_RATIO:
        return

    left_denominator = _ratio_denominator(
        left, left_sources, reason=REASON_INCOMPARABLE_ENTITIES, subject="좌측 파생값"
    )

    right_denominator = _ratio_denominator(
        right, right_sources, reason=REASON_INCOMPARABLE_ENTITIES, subject="우측 파생값"
    )

    if left_denominator.metric is not right_denominator.metric:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_ENTITIES,
            f"두 기업의 분모 개념이 다릅니다: "
            f"{left_denominator.metric.value} / {right_denominator.metric.value}. "
            "분모가 다른 두 비율은 이름이 같아도 같은 지표가 아닙니다.",
        )


def validate_cross_entity_fact_comparability(
    *,
    left: Fact,
    right: Fact,
    expected_metric: MetricConcept,
    expected_fiscal_year: int,
) -> None:
    """
    두 기업의 원천 Fact를 함께 쓰기 직전에 견줄 수 있는지 본다(Q10).

    이 경로에서 guard가 하나뿐인 이유:
        Q10은 수치형 파생값을 만들지 않으므로 guard①을 지나지 않는다. 단위 정규화 성공(C8)을
        확인하는 자리가 여기 하나이며, 기업 간 단위 표기 차이는 실측 코퍼스에서 실제로
        관찰된다(한쪽은 천원 표기, 다른 쪽은 원 표기). 이 검사를 건너뛰면 자릿수가 세 자리
        어긋난 값끼리 견주고, 그 우열 판정도 그럴듯하다
        (agent_patterns.md Pattern D 검증 조건 4, representative_questions.md 유형 4).

    요청한 지표와 회계연도를 함께 받는 이유:
        두 Fact가 서로 일치하는 것만으로는 부족하다. 둘 다 2024년 값이면 서로는 일치하지만
        2025년을 물은 질문에는 답하지 않은 결과가 되고, 그 사실이 결과 어디에도 남지 않는다.
        확보 계층이 요청한 조합을 그대로 돌려주므로 정상 경로에서는 어긋나지 않지만, 확보
        계약이 바뀌었을 때 그 사실이 조용한 오답이 아니라 정지 지점으로 드러나야 한다.

        Calculation 경로가 expected_unit을 받는 것과 같은 자리다. 그쪽은 대응표가 정한 결과
        단위를 받고, 이쪽은 계획이 요청한 지표와 기간을 받는다. 어느 쪽도 이 파일이 정하지
        않는다.

    연산과 분모 개념은 판정하지 않는다. 원천 Fact에는 그 둘이 없다. 없는 것을 요구하면 이
    경로가 guard를 우회해야만 실행되고, 우회하는 순간 단위 검사도 함께 사라진다.

    결산월 일치(조건 5)는 아직 보지 않는다. Fact에도 Evidence에도 결산월을 담을 자리가 없고,
    manifest의 base_month는 확보 계층이 공시를 고를 때 쓰고 결과에 싣지 않는다. 없는 값을
    추측해 검사하면 통과 여부가 실제 결산월과 무관해진다. Calculation 경로가 같은 이유로
    남겨 둔 것과 같은 항목이다.

    Raises:
        CalculationBlockedError: 규칙 하나라도 어긋난 경우.
    """
    # 판정 순서는 좁히는 순서를 따른다. 누구를 -> 무엇을 -> 언제 -> 어떤 기준으로 ->
    # 어떤 단위로 -> 어떤 값으로. Calculation 경로와 같은 순서다.
    if left.company == right.company:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_ENTITIES,
            f"두 Fact의 기업이 같습니다: {left.company}. "
            "기업 간 비교는 서로 다른 두 기업을 견줍니다.",
        )

    if left.metric is not right.metric:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_ENTITIES,
            f"두 기업의 지표가 다릅니다: {left.metric.value} / {right.metric.value}",
        )

    # 서로 같은 것만 보지 않는다. 둘 다 같은 값으로 잘못 확보된 경우를 통과시키기 때문이며,
    # _require_expected_unit이 Calculation 경로에서 기대 단위를 함께 보는 것과 같은 이유다.
    if left.metric is not expected_metric:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_ENTITIES,
            f"질문이 요청한 지표는 {expected_metric.value}인데 "
            f"{left.metric.value}를 받았습니다.",
        )

    if left.fiscal_year != right.fiscal_year:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_ENTITIES,
            f"두 기업의 회계연도가 다릅니다: {left.fiscal_year} / {right.fiscal_year}. "
            "기업 간 비교는 같은 기간의 두 기업을 견줍니다.",
        )

    if left.fiscal_year != expected_fiscal_year:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_ENTITIES,
            f"질문이 요청한 회계연도는 {expected_fiscal_year}인데 "
            f"{left.fiscal_year} 회계연도의 값을 받았습니다.",
        )

    scope = _require_single_scope(
        (left, right), reason=REASON_INCOMPARABLE_ENTITIES, subject="두 기업"
    )

    # 기준을 확정하지 못한 값끼리는 "같다"고 말할 수 없다. 두 기업이 모두 unknown이면 위
    # 검사는 통과하지만, 한쪽이 연결이고 다른 쪽이 별도일 가능성이 그대로 남는다.
    if scope is FactAccountingScope.UNKNOWN:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_ENTITIES,
            "회계기준을 확정하지 못한 Fact로는 견주지 않습니다. "
            "연결과 별도를 섞어도 그럴듯한 대소 관계가 나옵니다.",
        )

    if left.normalized_unit is None or right.normalized_unit is None:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_ENTITIES,
            "정규화 단위가 없는 Fact로는 견주지 않습니다: "
            f"{left.fact_id!r}={left.normalized_unit!r} "
            f"{right.fact_id!r}={right.normalized_unit!r}",
        )

    if left.normalized_unit != right.normalized_unit:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_ENTITIES,
            f"두 기업의 정규화 단위가 다릅니다: "
            f"{left.normalized_unit} / {right.normalized_unit}. "
            "정규화에 실패한 값끼리 견주면 자릿수가 어긋난 우열이 나갑니다.",
        )

    # 값 자체가 쓸 수 있는지는 마지막에 본다. 판정 직전에 확인하지 않으면 대소를 가리는
    # 자리에서 터지고, 그때는 어느 입력이 문제였는지가 결과에 남지 않는다.
    #
    # 여기서 나가는 사유는 위와 다르다. unusable_calculation_input은 "그 값으로는 산술이
    # 성립하지 않는다"이고 대응표에 없으므로 실행 중단으로 남는다. 코퍼스에 답이 없다는
    # 판정이 아니기 때문이며, guard①이 같은 자리에서 같은 사유를 쓰는 것과 같다.
    _decimal(left.normalized_value, label=f"좌측 Fact {left.fact_id!r}")

    _decimal(right.normalized_value, label=f"우측 Fact {right.fact_id!r}")


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


def _shared_normalized_unit(previous: Fact, current: Fact) -> str:
    """
    두 원천 Fact가 함께 쓰는 정규화 단위를 읽는다.

    증감액의 단위는 정책이 아니라 입력의 성질이다. ratio가 output_unit을 인자로 받는 것과
    다른 자리인 이유가 그것이다. 비율의 결과 단위는 파생지표 대응표가 정하지만, 두 값을 뺀
    결과의 단위는 그 두 값이 이미 정해 두었다. 인자로 받으면 대응표에 없는 값을 호출자가
    지어내야 하고, 지어낸 단위와 실제 값이 어긋나도 결과에는 드러나지 않는다.

    guard②가 이미 같은 것을 확인한 뒤에 불린다. 그래도 다시 보는 이유는 ratio가 분모 0을
    다시 보는 이유와 같다. 값을 읽는 과정에서 드러나는 실패를 조용히 지나가면 잘못된 수치가
    만들어진다.

    Raises:
        CalculationBlockedError: 단위가 없거나 서로 다른 경우.
    """
    if previous.normalized_unit is None or current.normalized_unit is None:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_CALCULATION_INPUTS,
            "정규화 단위가 없는 Fact로는 증감을 만들지 않습니다: "
            f"{previous.fact_id!r}={previous.normalized_unit!r} "
            f"{current.fact_id!r}={current.normalized_unit!r}",
        )

    if previous.normalized_unit != current.normalized_unit:
        raise CalculationBlockedError(
            REASON_INCOMPARABLE_CALCULATION_INPUTS,
            f"두 기간의 정규화 단위가 다릅니다: "
            f"{previous.normalized_unit} / {current.normalized_unit}",
        )

    return previous.normalized_unit


def delta(
    *,
    calculation_id: str,
    previous: Fact,
    current: Fact,
) -> CalculationResult:
    """
    같은 기업 두 기간의 원천 Fact에서 증감액을 만든다.

        current - previous

    결과 단위는 두 Fact가 함께 쓰는 정규화 단위다. 원 단위 매출의 증감은 원이고, 그 값을
    percent나 percentage point로 옮기지 않는다.

    입력 검증은 하지 않는다. 호출자가 validate_temporal_fact_comparability를 먼저 부르며,
    두 판정을 한 함수에 섞으면 "검증만 하고 계산은 하지 않는" 경로를 시험할 수 없다. 다만
    값을 읽는 과정에서 발견되는 실패(값 없음·파싱 불가·단위 불일치)는 여기서도 예외로
    나간다. ratio가 같은 규칙을 쓰는 것과 같은 자리다.

    inputs는 previous, current 순서다. diff가 old, new 순서로 적는 것과 같은 이유이며,
    뒤집으면 부호가 뒤집힌 증감이 나오고 뒤집힌 값도 그럴듯하다.

    저장된 값이 아니라 Fact의 정규화 값에서 뺀다. 두 원천 값은 반올림을 거치지 않은 값이므로
    diff가 피하려던 어긋남(중간값과 표기값의 불일치)이 이 경로에는 없다.
    """
    previous_value = _decimal(
        previous.normalized_value, label=f"이전 Fact {previous.fact_id!r}"
    )

    current_value = _decimal(
        current.normalized_value, label=f"이후 Fact {current.fact_id!r}"
    )

    unit = _shared_normalized_unit(previous, current)

    with localcontext() as context:
        context.prec = PRECISION

        value = current_value - previous_value

    return CalculationResult(
        calculation_id=calculation_id,
        operation=OPERATION_DELTA,
        inputs=[_fact_reference(previous), _fact_reference(current)],
        value=str(_quantize(value)),
        unit=unit,
        evidence_ids=_merged_evidence_ids(previous.evidence_ids, current.evidence_ids),
    )


def delta_pct(
    *,
    calculation_id: str,
    previous: Fact,
    current: Fact,
) -> CalculationResult:
    """
    같은 기업 두 기간의 원천 Fact에서 증감률을 백분율로 만든다.

        (current - previous) / previous * 100

    증감액 Calculation을 참조하지 않고 원천 Fact 두 건을 직접 참조한다. 두 가지 이유가 있다.

        근거를 되짚는 경로가 짧다. 증감률이 어느 값에서 나왔는지 보려면 Calculation을 한 번 더
        거치지 않고 Fact로 바로 간다(evidence_and_answerability.md "입력 Fact 2건 전부 참조").

        증감액이 저장 자릿수로 반올림된 값이라는 점이 증감률에 옮겨붙지 않는다. 반올림된
        분자로 나누면 두 원천 값에서 직접 구한 비율과 미세하게 어긋날 수 있고, 그 어긋남은
        검산하는 쪽에서만 드러난다.

    분모는 이전 값이다. 이후 값으로 나누면 부호는 같고 크기만 다른 그럴듯한 값이 나온다.

    입력 검증은 delta와 같은 규칙을 따른다. 이전 값이 0이면 여기서도 막는다.
    validate_temporal_fact_comparability가 이미 같은 것을 보지만, ratio가 분모 0을 다시 보는
    것과 같은 이유로 계산 직전에도 확인한다.
    """
    previous_value = _decimal(
        previous.normalized_value, label=f"이전 Fact {previous.fact_id!r}"
    )

    current_value = _decimal(
        current.normalized_value, label=f"이후 Fact {current.fact_id!r}"
    )

    if previous_value.is_zero():
        raise CalculationBlockedError(
            REASON_UNUSABLE_CALCULATION_INPUT,
            f"이전 Fact {previous.fact_id!r}의 값이 0입니다. 증감률이 정의되지 않습니다.",
        )

    # 단위를 결과에 쓰지는 않지만 확인은 한다. 증감률은 언제나 백분율이므로 단위가 어긋나도
    # 결과 표기는 그대로 percent가 되고, 그때 틀렸다는 사실이 값에도 단위에도 남지 않는다.
    _shared_normalized_unit(previous, current)

    with localcontext() as context:
        context.prec = PRECISION

        value = (current_value - previous_value) / previous_value * PERCENT_SCALE

    return CalculationResult(
        calculation_id=calculation_id,
        operation=OPERATION_DELTA_PCT,
        inputs=[_fact_reference(previous), _fact_reference(current)],
        value=str(_quantize(value)),
        unit=UNIT_PERCENT,
        evidence_ids=_merged_evidence_ids(previous.evidence_ids, current.evidence_ids),
    )


# --------------------------------------------------------------------------
# 관계 판정
#
# 입력 타입마다 함수가 따로 있고(Calculation 2건 / Fact 2건), 대소를 가리는 규칙 자체는
# 아래 primitive 하나가 갖는다. 규칙을 각자 적으면 한쪽이 '같다'를 다루는 방식만 달라지는
# 날이 오고, 그때 어느 쪽이 정본인지 알 수 없다.
# --------------------------------------------------------------------------


def _relation(left: Decimal, right: Decimal) -> ComparisonRelation:
    """
    두 값의 대소 관계.

    같으면 equal이다. 표기가 달라도 값이 같으면 같다("100"과 "100.00"). Decimal의 비교가
    이미 그 규칙이므로 문자열을 맞추지 않는다.

    not_comparable·unknown 같은 값을 돌려주지 않는다. ComparisonResult는 비교가 정상적으로
    성공한 경우에만 존재하며, 견줄 수 없다는 판정은 guard②가 예외로 올린다
    (agent_patterns.md Comparison Judgment와 ComparisonResult).
    """
    if left > right:
        return ComparisonRelation.LEFT_GREATER

    if left < right:
        return ComparisonRelation.RIGHT_GREATER

    return ComparisonRelation.EQUAL


def compare_calculations(
    *,
    comparison_id: str,
    left: CalculationResult,
    right: CalculationResult,
) -> ComparisonResult:
    """
    확정된 두 파생값의 대소 관계를 판정한다.

    비교 가능성 검증은 하지 않는다. 호출자가 guard②를 먼저 부르며, 두 판정을 한 함수에 섞으면
    "검증만 하고 판정은 하지 않는" 경로를 시험할 수 없다. ratio가 guard①과 나뉘어 있는 것과
    같은 자리다.

    저장된 값에서 판정한다. 반올림 전 중간값으로 가리면 답변에 실린 두 수치로 검산했을 때
    관계가 뒤집혀 보일 수 있다. 11.5895와 11.5894처럼 저장 자릿수에서 갈리는 두 값이 그
    경우이며, diff가 같은 이유로 저장된 값에서 빼는 것과 같다.

    수치형 파생 결과를 만들지 않는다:
        두 값의 차이를 계산하지 않는다. Q11은 "어디가 더 높은가"를 물을 뿐 차이 수치를 묻지
        않으며, 묻지 않은 값을 만들면 Calculation 개수가 대응표와 어긋난다
        (evidence_and_answerability.md Calculation cardinality: Q11은 Calculation 2건).
        차이를 물은 질문이 생기면 그때 diff가 그 값을 만들고, 그 결과는 Calculation으로 센다.

    evidence_ids를 담지 않는다. ComparisonResult에 그 자리가 없으며, 근거는 이 판정이
    참조하는 두 Calculation이 이미 들고 있다.
    """
    left_value = _decimal(left.value, label=f"좌측 Calculation {left.calculation_id!r}")

    right_value = _decimal(
        right.value, label=f"우측 Calculation {right.calculation_id!r}"
    )

    return ComparisonResult(
        comparison_id=comparison_id,
        left=_calculation_reference(left),
        right=_calculation_reference(right),
        relation=_relation(left_value, right_value),
    )


def compare_facts(
    *,
    comparison_id: str,
    left: Fact,
    right: Fact,
) -> ComparisonResult:
    """
    확정된 두 원천 Fact의 대소 관계를 판정한다(Q10).

    비교 가능성 검증은 하지 않는다. 호출자가 validate_cross_entity_fact_comparability를 먼저
    부르며, 두 판정을 한 함수에 섞으면 "검증만 하고 판정은 하지 않는" 경로를 시험할 수 없다.
    compare_calculations가 guard②와 나뉘어 있는 것과 같은 자리다.

    정규화 값에서 판정한다. 원문 값(raw_value)으로 가리면 단위 표기가 다른 두 기업에서
    자릿수가 어긋난 우열이 나온다. 원문 표기를 답변에 병기하는 일은 answer_builder의 몫이고,
    이 판정이 보는 것은 정규화를 마친 값이다(agent_patterns.md Pattern D 출력 요구사항).

    수치형 파생 결과를 만들지 않는다:
        두 값의 차이를 계산하지 않는다. Q10은 "비교해줘"라고 물을 뿐 차이 수치를 묻지 않으며,
        묻지 않은 값을 만들면 Calculation 개수가 대응표와 어긋난다
        (evidence_and_answerability.md Calculation cardinality: Q10은 Calculation 0).

    evidence_ids를 담지 않는다. ComparisonResult에 그 자리가 없으며, 근거는 이 판정이
    참조하는 두 Fact가 이미 들고 있다.
    """
    left_value = _decimal(
        left.normalized_value, label=f"좌측 Fact {left.fact_id!r}"
    )

    right_value = _decimal(
        right.normalized_value, label=f"우측 Fact {right.fact_id!r}"
    )

    return ComparisonResult(
        comparison_id=comparison_id,
        left=_fact_reference(left),
        right=_fact_reference(right),
        relation=_relation(left_value, right_value),
    )
