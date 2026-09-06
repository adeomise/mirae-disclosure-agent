"""파생지표 대응표.

역할:
- 사용자가 요청한 파생 개념(영업이익률 등)이 어떤 원천 지표에서 나오는지 정의한다.
- 분자와 분모의 역할, 연산 종류, 결과 단위를 한자리에 둔다.

원천 지표와 같은 어휘에 담지 않는 이유:
    common.enums.MetricConcept는 문서에서 실제로 추출되는 값의 어휘다. 값마다 대응하는
    ACODE나 본표 라벨이 있고, 그 값을 담은 Fact에는 근거 문서가 붙는다.

    영업이익률은 그런 값이 아니다. 어느 사업보고서에도 적혀 있지 않고 두 원천 값에서
    계산으로 만들어진다. 두 어휘를 합치면 metric이 영업이익률인 Fact가 타입상 합법이 되고,
    원문에 없는 값이 근거를 갖춘 원천 사실의 모습으로 돌아갈 수 있다. 그래서 파생 개념은
    MetricConcept에 넣지 않고 agent 안에서만 쓰는 별도 어휘로 둔다.

왜 별도 모듈인가:
    "영업이익률은 영업이익을 매출로 나눈 비율"은 질문 해석 결과도, 검색 결과도, 계산 절차도
    아니다. 어떤 질문에도 참인 정적 사실이며, 질의 해석(무엇을 물었나)·기간별 확보(무엇을
    가져오나)·계산(어떻게 만드나) 세 쪽이 모두 읽어야 한다. 셋 중 하나에 두면 나머지 둘이
    그 모듈에 의존한다.

    Pattern별로 두지 않는 이유도 같다. 이 대응은 비교 축이 기간인지(Q08) 기업인지(Q11)와
    무관하다. Pattern마다 복제하면 두 벌이 되고, 갈라진 뒤에는 어느 쪽이 정본인지 알 수 없다.

이 모듈은 계산하지 않는다:
    연산 이름과 결과 단위를 기록만 한다. 실제 나눗셈, Decimal 정밀도, 분모가 0인 경우의
    처리, 계산 입력의 비교 가능성 검증은 calculator와 guard의 몫이며 아직 구현되지 않았다.

입력:
- DerivedMetric: 요청 가능한 파생 개념

출력:
- DerivedMetricSpec: 그 개념이 요구하는 원천 지표와 연산

TODO:
- 파생 개념이 늘어나면 연산 어휘를 Contract의 Calculation operation과 맞춘다
  (docs/agent_design/open_decisions.md DC5).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from disclosure_agent.common.enums import MetricConcept

__all__ = [
    "DerivedMetric",
    "DerivedMetricSpec",
    "get_derived_metric_spec",
    "required_source_metrics",
]

# 연산 이름. Calculation을 만드는 쪽이 쓸 값이며 이 모듈은 기록만 한다.
#
# docs/agent_design/query_plan.md의 operations 어휘와 같은 표기를 쓴다. 그 목록과 Contract의
# CalculationResult.operation이 같은 어휘를 쓸지는 아직 정해지지 않았다(DC5).
OPERATION_RATIO = "ratio"

# 비율의 결과 단위. 추출 계층이 이미 백분율에 쓰고 있는 표기와 같은 값이다.
UNIT_PERCENT = "percent"


class DerivedMetric(StrEnum):
    """계산으로 만들어지는 요청 개념. 문서에서 추출되는 값이 아니다."""

    OPERATING_MARGIN = "operating_margin"


@dataclass(frozen=True)
class DerivedMetricSpec:
    """
    파생 개념 하나가 무엇에서 어떻게 나오는지.

    분자와 분모를 이름으로 구분해 담는다. 두 지표를 순서 없는 집합으로만 담으면 계산하는
    쪽이 어느 쪽이 분자인지 다시 정해야 하고, 뒤집어도 그럴듯한 비율이 나오므로 틀렸다는
    사실이 결과에 드러나지 않는다.
    """

    numerator: MetricConcept
    denominator: MetricConcept
    operation: str
    output_unit: str


_SPECS: dict[DerivedMetric, DerivedMetricSpec] = {
    DerivedMetric.OPERATING_MARGIN: DerivedMetricSpec(
        numerator=MetricConcept.OPERATING_INCOME,
        denominator=MetricConcept.REVENUE,
        operation=OPERATION_RATIO,
        output_unit=UNIT_PERCENT,
    ),
}


def get_derived_metric_spec(metric: DerivedMetric) -> DerivedMetricSpec:
    """
    파생 개념의 대응을 읽는다.

    등록되지 않은 개념은 돌려줄 기본값을 만들지 않고 막는다. 추측한 분자·분모로 계산하면
    그럴듯한 숫자가 나오고, 그것이 틀렸다는 사실은 결과 어디에도 남지 않는다.
    """
    spec = _SPECS.get(metric)

    if spec is None:
        registered = ", ".join(sorted(item.value for item in _SPECS))

        raise ValueError(
            f"대응이 등록되지 않은 파생지표입니다: {metric!r}. "
            f"현재 등록된 파생지표: {registered}."
        )

    return spec


def required_source_metrics(metric: DerivedMetric) -> tuple[MetricConcept, ...]:
    """
    파생 개념을 만들기 위해 확보해야 하는 원천 지표. 분자, 분모 순서다.

    이 값은 어디에도 저장하지 않고 필요할 때마다 대응표에서 읽는다. QueryPlan에 함께 담아
    두면 대응표와 계획이 같은 질문에 서로 다른 답을 갖는 순간이 생긴다.
    """
    spec = get_derived_metric_spec(metric)

    # 분자와 분모가 같은 지표인 파생 개념이 생기면 같은 조합을 두 번 확보하게 된다.
    # 순서는 유지하면서 중복만 없앤다.
    return tuple(dict.fromkeys((spec.numerator, spec.denominator)))
