"""Pattern D — Cross-entity Compare handler.

역할:
- 같은 기간의 두 기업을 견주고, 그 우열을 ComparisonResult 1건으로 확정한다.

현재 구현 범위:
- 대표 질문 Q10 계열("2025년 <기업>이랑 <기업> 매출 비교해줘") — 원천 지표 직접 비교
- 대표 질문 Q11 계열("<기업>이랑 <기업> 중에 <연도> 영업이익률이 더 높은 곳은 어디야?")
  — 기업별 파생지표 비교

    QueryPlan
      -> 계획 모양 확인            (이 모듈, _unsupported_plan)
      -> Q10/Q11 모양 분기          (이 모듈, resolve_shape)
      -> 기업별 원천 Fact 확보       (agent.period_retrieval)
      -> guard① 계산 입력 검증       (agent.calculator, Q11만)
      -> 기업별 비율 Calculation     (agent.calculator, Q11만)
      -> guard② 비교 가능성 검증     (agent.calculator)
      -> ComparisonResult           (agent.calculator)
      -> CrossEntityCompareResult
      -> AgentExecutionResult       (agent.normalization)

두 모양이 만드는 것의 개수가 다르다:
    Q10  Fact 2 · Calculation 0 · Comparison 1
    Q11  Fact 4 · Calculation 2 · Comparison 1

    개수는 evidence_and_answerability.md의 Calculation cardinality가 정한다. 최종 우열
    판정은 Calculation이 아니므로 어느 쪽도 Calculation 개수에 더해지지 않는다.

    Q10이 Calculation 0인 것은 계산 능력이 없어서가 아니라 질문이 수치형 파생 결과를
    요구하지 않기 때문이다. 두 값의 차이를 만들어 실으면 개수가 대응표와 어긋나고, 묻지
    않은 수치가 답변에 실린다.

guard가 걸리는 자리가 모양마다 다르다:
    Q11은 기업별 비율을 만들기 전에 guard①이 걸리고, 두 비율을 견주기 전에 guard②가
    걸린다. Q10은 만들 파생값이 없어 guard①을 지나지 않으므로 guard② 하나가 유일한
    방어선이며, 단위 정규화 성공(C8) 확인이 그 안에 함께 있다
    (agent_patterns.md "Q10처럼 Calculation 없이 Fact를 바로 견주는 경로에서는 여기가
    유일한 guard").

계획 모양을 입구에서 보는 이유:
    Router는 intent 하나만 보고 handler를 고른다. cross_entity_compare로 분류되는 계획은
    Q10·Q11 둘이 아니다. 기업이 셋인 질문, 기간이 함께 여럿인 질문, 이번 범위에 없는 지표를
    묻는 질문, 그리고 거래소공시 집계가 필요한 Q12가 모두 같은 intent로 들어온다.

    handler가 등록되기 전에는 Router가 그 전부를 routing/unimplemented_intent로 막았다.
    등록한 뒤에도 막는 것은 같아야 하고, 다만 막는 자리가 Router에서 이 입구로 옮겨온다.
    Pattern C가 같은 이유로 같은 구조를 택했다(handlers/temporal_compare.py).

Q12를 열지 않는 이유:
    Q12("2024년에 LG이노텍하고 레인보우로보틱스가 새로 발표한 시설투자, 회사별로 다 합치면
    어디가 더 커?")는 기업 2곳·연도 1개라는 점에서 Q10·Q11과 같은 모양이다. 앞의 두 검사만
    두면 그대로 통과한다.

    그러나 Q12는 정기공시가 아니라 거래소공시를 읽고, 기업별로 여러 이벤트를 모아 합산한
    뒤에 견준다(agent_patterns.md Pattern D의 거래소공시 집계 경로, C6·C7). 그 경로는 이
    파일에 없다. 지표 판정을 통과시키면 시설투자를 물은 질문이 매출 조회 경로로 흘러
    형식만 맞고 근거가 틀린 답이 된다.

    지금은 query_analyzer의 원천 지표 어휘에 시설투자가 없어 metric도 derived_metric도
    None으로 남고, 아래 지표 판정이 그것을 unsupported_comparison_metric으로 막는다.
    허용 목록으로 판정하므로, 어휘가 넓어져 시설투자가 metric에 담기더라도 이 자리는
    계속 막는다.

무엇을 하지 않는가:
- 기업을 하나로 줄이지 않는다. 두 기업 중 하나를 골라 답하면 나머지 기업이 사라진 사실이
  결과 어디에도 남지 않는다(pattern_f_safety_cases.json F6-04).
- 자연어를 만들지 않는다. 문장 조립은 answer_builder의 몫이다.

문서군을 다시 확인하지 않는 이유:
    기업 간 비교 계획은 document_class·document_subtype을 기본값(정기공시·사업보고서)으로만
    만든다. Pattern C와 같은 자리이며, 다른 값이 들어오면 확보 계층이 해당 공시를 찾지 못해
    filing_discovery에서 멈춘다. 도달하지 않는 검사를 미리 두어 사유만 늘리지 않는다.

입력:
- QueryPlan, corpus 루트, manifest 경로

출력:
- AgentExecutionResult. 실패해도 예외를 올리지 않고 실행 상태로 표현한다.
  Pattern 내부 표현(CrossEntityCompareResult)이 필요한 호출자는
  build_cross_entity_compare_result를 직접 쓴다.

TODO:
- 결산월 일치 검사(agent_patterns.md Pattern D 검증 조건 5). Fact에도 Evidence에도 결산월을
  담을 자리가 없어 guard②가 아직 보지 못한다.
- Q12(거래소공시 집계 경로)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from disclosure_agent.agent.answer_builder import (
    REASON_UNSUPPORTED_COMPARISON_METRIC,
    REASON_UNSUPPORTED_COMPARISON_PERIOD,
    REASON_UNSUPPORTED_COMPARISON_SUBJECT,
    REASON_UNSUPPORTED_DERIVED_OPERATION,
    REASON_UNUSABLE_CALCULATION_INPUT,
    STAGE_COMPARABILITY,
    STAGE_PATTERN_ENTRY,
    BlockedInfo,
)
from disclosure_agent.agent.calculator import (
    OPERATION_RATIO,
    CalculationBlockedError,
    compare_calculations,
    compare_facts,
    ratio,
    validate_calculation_inputs,
    validate_cross_entity_comparability,
    validate_cross_entity_fact_comparability,
)
from disclosure_agent.agent.derived_metrics import (
    DerivedMetric,
    DerivedMetricSpec,
    get_derived_metric_spec,
    required_source_metrics,
)
from disclosure_agent.agent.normalization import normalize_cross_entity_compare_result
from disclosure_agent.agent.period_retrieval import collect_period_facts
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.common.enums import MetricConcept
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    CalculationResult,
    ComparisonResult,
    Evidence,
    Fact,
)

__all__ = [
    "COMPARED_COMPANY_COUNT",
    "COMPARED_PERIOD_COUNT",
    "SUPPORTED_DERIVED_METRICS",
    "SUPPORTED_SOURCE_METRICS",
    "CrossEntityCompareResult",
    "CrossEntityShape",
    "EntityDerivedValue",
    "build_cross_entity_compare_result",
    "handle_cross_entity_compare",
    "resolve_shape",
    "source_metrics_for",
]

# 이번 구현이 견주는 축의 크기.
#
# 기업은 정확히 둘, 기간은 정확히 하나다. Pattern C가 기업 1곳 · 기간 2개인 것과 정확히
# 반대이며, 두 Pattern이 같은 대응표(agent.derived_metrics)를 쓰면서도 서로 다른 축을 보는
# 것이 여기서 드러난다.
COMPARED_COMPANY_COUNT = 2
COMPARED_PERIOD_COUNT = 1

# 이번 구현이 여는 원천 지표와 파생 개념.
#
# 허용 목록으로 둔다. "막을 것"을 세는 대신 "열 것"을 세는 이유는, 어휘가 넓어질 때 새로
# 들어온 지표가 조용히 통과하지 않게 하기 위해서다. Q12의 시설투자가 그 경우다.
SUPPORTED_SOURCE_METRICS: tuple[MetricConcept, ...] = (MetricConcept.REVENUE,)

SUPPORTED_DERIVED_METRICS: tuple[DerivedMetric, ...] = (DerivedMetric.OPERATING_MARGIN,)


class CrossEntityShape(StrEnum):
    """
    이번 구현이 실행할 수 있는 계획 모양.

    둘로 나누는 이유는 확보할 원천 지표와 만들 결과의 개수가 다르기 때문이다.

        SOURCE_METRIC   Q10. 기업별 원천 Fact 1건씩을 그대로 견준다. 수치형 파생값이
                        없으므로 Calculation은 0건이고 ComparisonResult 1건만 남는다
                        (evidence_and_answerability.md Calculation cardinality).
        DERIVED_METRIC  Q11. 기업별로 비율 Calculation을 하나씩 만든 뒤 그 둘을 견준다.
                        Calculation 2건 + ComparisonResult 1건이다.

    intent가 아니다. Router는 intent만 보고 handler를 고르며, 그 안에서 어떤 모양을 다룰지는
    이 Pattern이 안다. 공유 Contract의 Intent enum을 넓히지 않는 이유가 그것이다.
    """

    SOURCE_METRIC = "source_metric"
    DERIVED_METRIC = "derived_metric"


@dataclass(frozen=True)
class EntityDerivedValue:
    """
    한 기업의 파생값과 그 값을 만든 원천 Fact.

    Calculation만 들고 다니지 않는 이유:
        CalculationResult는 값·단위·참조만 담는다. 두 기업의 파생값이 같은 회계연도의 같은
        회계기준에서 나왔는지는 그 안에 없고, 참조를 따라가려면 결과 전체가 필요하다.
        guard②가 판정에 쓸 원천을 여기서 함께 들고 간다.

    Pattern C의 PeriodDerivedValue와 같은 구조이며 축만 다르다. 그쪽은 회계연도를 들고
    이쪽은 기업을 든다. 하나로 합치지 않는 이유는 두 Pattern이 서로 다른 축을 견주기
    때문이며, 합치면 어느 축이 비교 대상인지 타입에서 사라진다.
    """

    company: str
    sources: tuple[Fact, ...]
    calculation: CalculationResult


@dataclass(frozen=True)
class CrossEntityCompareResult:
    """
    Pattern D의 구조화 결과.

    공통 Contract가 아니다. Pattern C와 같이 Fact/Evidence/CalculationResult/ComparisonResult를
    이미 Contract 타입으로 들고 있으며, 확보·계산 계층이 변환까지 끝낸 값을 그대로 담는다.

    blocked가 있으면 calculations와 comparisons는 비어 있다. Pattern C가 같은 자리에서
    파생값을 남기지 않는 것과 같은 이유다. 비교 가능성 판정이 막은 것은 "두 값을 함께 쓸 수
    없다"인데, 기업별 값이 결과에 남아 있으면 받는 쪽이 그것으로 우열을 직접 판정할 수 있고,
    그러면 guard가 막은 일이 결과를 받는 쪽에서 그대로 일어난다.

    facts와 evidence는 blocked여도 남는다. 확보 자체는 정상이었고, 어느 값이 견줄 수 없다고
    판정됐는지 보려면 그 값이 있어야 한다.
    """

    plan: QueryPlan
    facts: tuple[Fact, ...] = field(default_factory=tuple)
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)
    calculations: tuple[CalculationResult, ...] = field(default_factory=tuple)
    comparisons: tuple[ComparisonResult, ...] = field(default_factory=tuple)
    blocked: BlockedInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        """공통 Contract로 옮기기 전의 진단용 표현."""
        return {
            "intent": self.plan.intent,
            "query": self.plan.to_dict(),
            "facts": [fact.model_dump() for fact in self.facts],
            "evidence": [item.model_dump() for item in self.evidence],
            "calculations": [item.model_dump() for item in self.calculations],
            "comparisons": [item.model_dump() for item in self.comparisons],
            "blocked": self.blocked.to_dict() if self.blocked else None,
        }


def resolve_shape(plan: QueryPlan) -> CrossEntityShape | None:
    """
    계획이 어느 모양인지 읽는다. 이번 범위 밖이면 None이며 추측하지 않는다.

    두 모양은 배타다. QueryPlan이 metric과 derived_metric을 함께 담지 못하게 이미 막고
    있으므로(QueryPlan.__post_init__), 여기서 둘 다 채워진 계획은 만들어질 수 없다.

    둘 다 비어 있는 계획은 어느 모양도 아니다. 질문에서 지표를 읽지 못한 경우이며, Q12가
    지금 그 상태다. 한쪽을 기본값으로 골라 주지 않는다.
    """
    if plan.derived_metric is not None:
        if plan.derived_metric in SUPPORTED_DERIVED_METRICS:
            return CrossEntityShape.DERIVED_METRIC

        return None

    if plan.metric is not None and plan.metric in SUPPORTED_SOURCE_METRICS:
        return CrossEntityShape.SOURCE_METRIC

    return None


def source_metrics_for(plan: QueryPlan) -> tuple[MetricConcept, ...]:
    """
    이 계획을 실행하려면 기업마다 어떤 원천 지표를 확보해야 하는가.

    파생 개념을 원천 지표로 펼치는 일은 대응표가 한다. 계획에 함께 담아 두면 같은 질문에
    대응표와 계획이 서로 다른 답을 갖는 순간이 생긴다(derived_metrics.required_source_metrics).

    확보 요청 자체는 여기서 하지 않는다. 이 함수는 "무엇이 필요한가"까지만 답하고, 그것을
    agent.period_retrieval에 넘기는 일은 수치 실행 단계의 몫이다.

    Raises:
        ValueError: 이번 범위 밖의 계획. 호출하기 전에 _unsupported_plan을 통과시켜야 한다.
    """
    shape = resolve_shape(plan)

    if shape is CrossEntityShape.DERIVED_METRIC:
        return required_source_metrics(plan.derived_metric)

    if shape is CrossEntityShape.SOURCE_METRIC:
        return (MetricConcept(plan.metric),)

    raise ValueError(
        "이번 구현이 다루지 않는 계획에서 원천 지표를 읽을 수 없습니다: "
        f"metric={plan.metric!r}, derived_metric={plan.derived_metric!r}. "
        "_unsupported_plan을 먼저 통과시켜야 합니다."
    )


def _unsupported_plan(plan: QueryPlan) -> BlockedInfo | None:
    """
    이번 구현이 실행할 수 있는 계획인지 본다. 실행할 수 없으면 정지 지점을 돌려준다.

    Router가 열린 뒤 이 자리가 유일한 방어선이다. 판정 순서는 Pattern C와 같이 좁히는
    순서를 따른다. 누구를(기업) -> 언제(기간) -> 무엇을(지표).

    코퍼스를 열기 전에 전부 판정한다. 문서를 한 번도 읽지 않으므로 여기서 막힌 결과에는
    Fact도 Evidence도 없고, 답변 가능성도 판정하지 않는다.
    """
    if len(plan.companies) != COMPARED_COMPANY_COUNT:
        return BlockedInfo(
            stage=STAGE_PATTERN_ENTRY,
            reason=REASON_UNSUPPORTED_COMPARISON_SUBJECT,
            detail=(
                f"이번 구현은 기업 {COMPARED_COMPANY_COUNT}곳을 견주는 질문만 다룹니다. "
                f"계획에 담긴 기업: {', '.join(plan.companies)}"
            ),
        )

    if len(set(plan.companies)) != COMPARED_COMPANY_COUNT:
        # 같은 기업을 두 번 담은 계획이다. 개수만 세면 통과한다. 그대로 확보를 진행하면 같은
        # fact_id를 가진 Fact가 두 벌 실리고, 우열 판정의 left와 right가 같은 값을 가리킨다.
        # 그 결과는 개수만 맞고 견준 것이 없다. guard②도 같은 어긋남을 잡지만 그때는 이미
        # 문서를 두 번 연 뒤이므로, 코퍼스를 열기 전인 여기서 막는다.
        #
        # 정상 경로에서는 만들어지지 않는 계획이다. query_analyzer가 같은 기업의 중복 매치를
        # 하나로 세기 때문이며(_extract_companies), 그래도 막는 이유는 이 handler가 계획을
        # 직접 받는 호출부에도 열려 있기 때문이다.
        return BlockedInfo(
            stage=STAGE_PATTERN_ENTRY,
            reason=REASON_UNSUPPORTED_COMPARISON_SUBJECT,
            detail=(
                "기업 간 비교는 서로 다른 두 기업을 견줍니다. "
                f"계획에 담긴 기업: {', '.join(plan.companies)}"
            ),
        )

    if len(plan.years) != COMPARED_PERIOD_COUNT:
        # 기업 축과 기간 축이 함께 여럿인 질문이 여기로 온다. 기간 하나를 골라 답하면 두
        # 기간을 물은 질문에 한 기간만 답하게 되고, 그것이 F6-02에서 막은 동작이다.
        listed = ", ".join(str(year) for year in plan.years) or "없음"

        return BlockedInfo(
            stage=STAGE_PATTERN_ENTRY,
            reason=REASON_UNSUPPORTED_COMPARISON_PERIOD,
            detail=(
                f"이번 구현은 절대연도 {COMPARED_PERIOD_COUNT}개를 기준으로 두 기업을 "
                f"견주는 질문만 다룹니다. 계획에 담긴 회계연도: {listed}."
            ),
        )

    if plan.has_unresolved_relative_period:
        # 해석하지 않은 상대 기간 표현("전년")이 절대연도 하나에 붙은 계획이다. 위 개수
        # 검사만으로는 걸리지 않는다. 기간이 둘이라는 사실이 years에 남지 않기 때문이다.
        #
        # 판정은 계획에게 묻는다. 질문 문자열을 다시 읽으면 해석에 성공한 계획도 "작년"이라는
        # 글자가 남아 있다는 이유로 막히고, 그때는 답할 수 있는 질문을 막는 쪽으로 틀린다
        # (QueryPlan.has_unresolved_relative_period). 그 기록을 남기는 자리는
        # query_analyzer._build_cross_entity_compare_plan이다.
        #
        # 개수만 보고 통과시키면 "HD현대중공업이랑 삼성중공업 2025년 매출이 전년보다 어때"
        # 같은 질문이 2025년만 견준 답으로 돌아가고, 사라진 기간이 결과 어디에도 남지 않는다.
        # Pattern C가 같은 이유로 QueryPlan.year 통로를 개수로 판정하지 않는 것과 같은 자리다.
        return BlockedInfo(
            stage=STAGE_PATTERN_ENTRY,
            reason=REASON_UNSUPPORTED_COMPARISON_PERIOD,
            detail=(
                "질문이 절대연도와 상대 기간 표현을 함께 쓰고 있습니다. "
                f"계획에 담긴 회계연도: {plan.years[0]}. "
                "상대 기간 표현을 연도로 옮기는 해석은 아직 구현되지 않았으며, 그대로 "
                "실행하면 질문이 가리키는 기간 중 하나만 견주게 됩니다."
            ),
        )

    if resolve_shape(plan) is None:
        # 지표를 읽지 못한 경우와 읽었으나 범위 밖인 경우를 detail에서 나눈다. 후속 작업이
        # 서로 다르기 때문이며, Pattern C가 같은 자리에서 하는 구분과 같다.
        requested = plan.derived_metric.value if plan.derived_metric else plan.metric

        missing = (
            f"계획에 담긴 지표: {requested}."
            if requested is not None
            else "질문에서 지표를 읽지 못했습니다."
        )

        supported = ", ".join(
            (
                *(metric.value for metric in SUPPORTED_SOURCE_METRICS),
                *(metric.value for metric in SUPPORTED_DERIVED_METRICS),
            )
        )

        return BlockedInfo(
            stage=STAGE_PATTERN_ENTRY,
            reason=REASON_UNSUPPORTED_COMPARISON_METRIC,
            detail=(
                f"이번 구현이 기업 간 비교로 다루는 지표는 {supported}뿐입니다. {missing} "
                "거래소공시를 모아 합산한 뒤 견주는 경로(시설투자 등)는 아직 없습니다."
            ),
        )

    return None


def _calculation_id(plan: QueryPlan, *, company: str) -> str:
    """
    결정적 Calculation 식별자.

    같은 질문을 두 번 실행하면 같은 값이 나와야 하므로 난수를 쓰지 않는다. 한 실행 결과
    안에서만 유일하면 되고, 이번 Pattern은 기간 하나를 다루므로 파생 개념·회계연도·기업으로
    충분하다.

    기업을 붙이는 자리가 여기다. Pattern C가 "기업 축이 필요한 Pattern D는 그 handler가 자기
    축을 덧붙인다"고 남겨 둔 자리이며(handlers/temporal_compare._calculation_id), 회계연도만
    쓰면 두 기업의 비율이 같은 식별자를 갖는다.

    기업명은 계획에 담긴 표기를 그대로 쓴다. Fact 쪽 표기를 쓰면 확보 결과에 따라 식별자가
    달라지고, 같은 질문이 같은 식별자를 낸다는 성질이 확보 계층에 매인다.
    """
    return f"calculation_{plan.derived_metric.value}_{plan.years[0]}_{company}"


def _requested_concept(plan: QueryPlan) -> str:
    """
    계획이 요청한 개념의 표기. 식별자에 쓴다.

    metric과 derived_metric은 QueryPlan이 배타로 강제하므로 둘 중 하나만 채워져 있다
    (QueryPlan.__post_init__). 어느 쪽인지에 따라 표기를 고르며, 둘 다 비어 있는 계획은
    _unsupported_plan이 이미 막았으므로 여기 도달하지 않는다.

    Raises:
        ValueError: 어느 개념도 담기지 않은 계획. 입구 guard를 통과시키지 않은 호출이다.
    """
    if plan.derived_metric is not None:
        return plan.derived_metric.value

    if plan.metric is not None:
        return plan.metric

    raise ValueError(
        "요청한 개념이 없는 계획에서 식별자를 만들 수 없습니다. "
        "_unsupported_plan을 먼저 통과시켜야 합니다."
    )


def _comparison_id(plan: QueryPlan) -> str:
    """
    결정적 ComparisonResult 식별자.

    두 기업을 모두 담는다. 한 실행 결과에는 판정이 하나뿐이므로 담지 않아도 유일하지만,
    식별자만 보고 무엇과 무엇을 견줬는지 알 수 없으면 결과를 되짚을 때 참조를 두 번 따라가야
    한다. 순서는 질문에 나온 순서이며 그것이 곧 left/right의 순서다.

    난수를 쓰지 않는다. 같은 질문을 두 번 실행하면 같은 식별자가 나와야 하며, 그 성질이
    없으면 평가와 회귀가 결과를 대조할 때마다 다른 값을 본다.

    두 모양이 같은 함수를 쓴다. Q10은 원천 지표(revenue), Q11은 파생 개념(operating_margin)이
    앞자리에 들어가므로 식별자가 서로 겹치지 않는다. 기업명은 계획에 담긴 표기를 그대로
    쓰며, 그 이유는 _calculation_id와 같다.
    """
    left, right = plan.companies

    return f"comparison_{_requested_concept(plan)}_{plan.years[0]}_{left}_vs_{right}"


def _require_fact(
    facts: tuple[Fact, ...],
    *,
    company: str,
    metric: MetricConcept,
) -> Fact:
    """
    한 기업의 확보 결과에서 요청한 지표의 Fact를 읽는다. 없으면 막는다.

    collect_period_facts는 요청한 조합을 전부 확보하거나 하나도 담지 않으므로 정상 경로에서는
    비지 않는다. 그래도 확인하는 이유는, 확보 계약이 바뀌었을 때 그 사실이 값이 아니라
    StopIteration으로 드러나면 어느 조합이 빠졌는지 결과에 남지 않기 때문이다.
    """
    for fact in facts:
        if fact.metric is metric:
            return fact

    raise CalculationBlockedError(
        REASON_UNUSABLE_CALCULATION_INPUT,
        f"{company}의 {metric.value}를 확보 결과에서 찾지 못했습니다.",
    )


def _build_entity_value(
    plan: QueryPlan,
    *,
    spec: DerivedMetricSpec,
    company: str,
    facts: tuple[Fact, ...],
) -> EntityDerivedValue:
    """
    한 기업의 파생값을 만든다. guard①을 통과한 뒤에만 계산한다.

    Raises:
        CalculationBlockedError: 확보 결과가 어긋났거나 guard①이 막은 경우.
    """
    numerator = _require_fact(facts, company=company, metric=spec.numerator)

    denominator = _require_fact(facts, company=company, metric=spec.denominator)

    validate_calculation_inputs(
        numerator=numerator,
        denominator=denominator,
        numerator_metric=spec.numerator,
        denominator_metric=spec.denominator,
    )

    return EntityDerivedValue(
        company=company,
        sources=(numerator, denominator),
        calculation=ratio(
            calculation_id=_calculation_id(plan, company=company),
            numerator=numerator,
            denominator=denominator,
            output_unit=spec.output_unit,
        ),
    )


def _collect_by_company(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None,
) -> tuple[tuple[tuple[str, tuple[Fact, ...], tuple[Evidence, ...]], ...], BlockedInfo | None]:
    """
    기업마다 원천 Fact를 확보한다. 하나라도 막히면 아무것도 담지 않는다.

    기업별로 따로 부르는 이유:
        collect_period_facts는 기업 하나를 받는다. 그 경계는 확보 계층이 "무엇을 찾을지"만
        받는다는 규약에서 나온 것이고, 기업 축을 그쪽에 넣으면 Pattern마다 다른 축이 확보
        계층으로 새어 들어간다.

    한쪽만 확보된 결과를 남기지 않는 이유:
        A사는 찾았는데 B사 사업보고서가 없을 때 A사 Fact가 결과에 남아 있으면, 받는 쪽이
        그것만으로 답을 조립할 수 있다. 그러면 두 기업을 물은 질문에 한 기업만 답하는 결과가
        되고, 그것이 F6-04에서 막은 동작이다. 확보 계층이 (연도 x 지표)에 대해 이미 지키는
        전부-또는-전무를 기업 축으로 넓힌다.

    어느 기업에서 막혔는지는 detail에만 덧붙인다. stage와 reason은 확보 계층이 내린 판정의
    정본이고, agent.normalization의 대응표가 그 값으로 답변 가능성을 판정한다.
    """
    collected: list[tuple[str, tuple[Fact, ...], tuple[Evidence, ...]]] = []

    for company in plan.companies:
        collection = collect_period_facts(
            company=company,
            fiscal_years=(plan.years[0],),
            metrics=source_metrics_for(plan),
            accounting_scope=plan.accounting_scope,
            document_class=plan.document_class,
            document_subtype=plan.document_subtype,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )

        if collection.blocked is not None:
            return (), BlockedInfo(
                stage=collection.blocked.stage,
                reason=collection.blocked.reason,
                detail=f"{company}: {collection.blocked.detail}",
            )

        collected.append((company, collection.facts, collection.evidence))

    return tuple(collected), None


def _execute_derived_metric_comparison(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None,
) -> CrossEntityCompareResult:
    """
    Q11 계열의 수치 실행 단계.

        기업 A 원천 Fact -> guard① -> 영업이익률 Calculation
        기업 B 원천 Fact -> guard① -> 영업이익률 Calculation
        -> guard② 기업 간 비교 가능성 -> ComparisonResult

    Calculation은 정확히 2건이고 ComparisonResult는 1건이다
    (evidence_and_answerability.md Calculation cardinality). 두 비율의 차이(%p)는 만들지
    않는다. 질문이 묻는 것은 "어디가 더 높은가"이고 차이 수치가 아니며, 묻지 않은 값을
    만들면 개수가 대응표와 어긋난다. Q08이 차이를 묻기 때문에 Calculation 3건인 것과
    정확히 대비되는 자리다.

    left/right는 질문에 나온 기업 순서다. 정렬하지 않는다. 정렬하면 "A랑 B 중에"라고 물은
    질문에 B가 left인 판정이 돌아가고, 관계 이름이 뒤집혀도 그럴듯하다. Pattern C가 변화량의
    부호를 지키려고 연도를 시간순으로 정렬한 것과 같은 이유이며, 방향만 반대다.
    """
    spec = get_derived_metric_spec(plan.derived_metric)

    if spec.operation != OPERATION_RATIO:
        # 대응표에는 있으나 계산기가 아직 만들 수 없는 연산이다. 대응표만 넓히고 계산기를
        # 넓히지 않은 경우가 여기로 오며, 조용히 ratio로 처리하면 정의가 다른 값이 나간다.
        # 코퍼스를 열기 전이므로 답변 가능성은 판정하지 않는다.
        return CrossEntityCompareResult(
            plan=plan,
            blocked=BlockedInfo(
                stage=STAGE_PATTERN_ENTRY,
                reason=REASON_UNSUPPORTED_DERIVED_OPERATION,
                detail=(
                    f"파생지표 {plan.derived_metric.value}의 연산은 {spec.operation}이며, "
                    f"이번 구현이 만들 수 있는 연산은 {OPERATION_RATIO}뿐입니다."
                ),
            ),
        )

    collected, blocked = _collect_by_company(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    if blocked is not None:
        return CrossEntityCompareResult(plan=plan, blocked=blocked)

    facts = tuple(fact for _, company_facts, _ in collected for fact in company_facts)

    evidence = tuple(item for _, _, company_evidence in collected for item in company_evidence)

    try:
        left, right = (
            _build_entity_value(plan, spec=spec, company=company, facts=company_facts)
            for company, company_facts, _ in collected
        )

        validate_cross_entity_comparability(
            left=left.calculation,
            right=right.calculation,
            left_sources=left.sources,
            right_sources=right.sources,
            expected_unit=spec.output_unit,
        )

        comparison = compare_calculations(
            comparison_id=_comparison_id(plan),
            left=left.calculation,
            right=right.calculation,
        )

    except CalculationBlockedError as error:
        # 확보한 Fact와 Evidence는 남기고 파생값과 우열 판정은 하나도 남기지 않는다.
        return CrossEntityCompareResult(
            plan=plan,
            facts=facts,
            evidence=evidence,
            blocked=BlockedInfo(
                stage=STAGE_COMPARABILITY,
                reason=error.reason,
                detail=error.detail,
            ),
        )

    return CrossEntityCompareResult(
        plan=plan,
        facts=facts,
        evidence=evidence,
        calculations=(left.calculation, right.calculation),
        comparisons=(comparison,),
    )


def _execute_source_metric_comparison(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None,
) -> CrossEntityCompareResult:
    """
    Q10 계열의 수치 실행 단계.

        기업 A 원천 Fact
        기업 B 원천 Fact
        -> guard② 기업 간 비교 가능성(단위 정규화 성공 포함) -> ComparisonResult

    Calculation은 0건이고 ComparisonResult는 1건이다
    (evidence_and_answerability.md Calculation cardinality). 두 값의 차이를 만들지 않는다.
    질문이 묻는 것은 "비교해줘"이고 차이 수치가 아니며, 묻지 않은 값을 만들면 개수가
    대응표와 어긋난다. Q11이 기업별 비율 2건을 만드는 것과 대비되는 자리다.

    guard①을 지나지 않는다:
        만들 파생값이 없으므로 계산 입력 검증이 적용될 자리가 없다. 그래서 단위 정규화
        성공(C8)을 확인하는 자리가 guard② 하나뿐이며, 그 검사는
        validate_cross_entity_fact_comparability 안에 있다
        (agent_patterns.md "Q10은 Numeric Calculation이 없으므로 ②만 걸린다").

    left/right는 질문에 나온 기업 순서다. 정렬하지 않는다. 정렬하면 "A랑 B 비교해줘"라고
    물은 질문에 B가 left인 판정이 돌아가고, 관계 이름이 뒤집혀도 그럴듯하다. Q11이 같은
    규칙을 쓰는 것과 같은 이유다.

    확보한 Fact는 blocked여도 남고 ComparisonResult는 남지 않는다. 무엇이 견줄 수 없다고
    판정됐는지 보려면 값이 있어야 하지만, 우열 판정이 함께 남으면 guard가 막은 비교가
    결과를 받는 쪽에서 그대로 성립한다.
    """
    collected, blocked = _collect_by_company(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    if blocked is not None:
        return CrossEntityCompareResult(plan=plan, blocked=blocked)

    facts = tuple(fact for _, company_facts, _ in collected for fact in company_facts)

    evidence = tuple(item for _, _, company_evidence in collected for item in company_evidence)

    # 확보를 요청한 지표를 그대로 다시 읽는다. 계획에서 두 번 읽으면 요청과 판정이 서로 다른
    # 값을 볼 수 있고, 그 어긋남은 결과에 드러나지 않는다.
    (metric,) = source_metrics_for(plan)

    try:
        left, right = (
            _require_fact(company_facts, company=company, metric=metric)
            for company, company_facts, _ in collected
        )

        validate_cross_entity_fact_comparability(
            left=left,
            right=right,
            expected_metric=metric,
            expected_fiscal_year=plan.years[0],
        )

        comparison = compare_facts(
            comparison_id=_comparison_id(plan),
            left=left,
            right=right,
        )

    except CalculationBlockedError as error:
        return CrossEntityCompareResult(
            plan=plan,
            facts=facts,
            evidence=evidence,
            blocked=BlockedInfo(
                stage=STAGE_COMPARABILITY,
                reason=error.reason,
                detail=error.detail,
            ),
        )

    return CrossEntityCompareResult(
        plan=plan,
        facts=facts,
        evidence=evidence,
        comparisons=(comparison,),
    )


def _execute_comparison(
    plan: QueryPlan,
    shape: CrossEntityShape,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None,
) -> CrossEntityCompareResult:
    """
    모양 판정을 통과한 계획의 수치 실행 단계.

    모양마다 만드는 것이 다르므로 여기서 갈린다. 파생지표 경로(Q11)는 기업별 비율
    Calculation 2건과 ComparisonResult 1건을 만들고, 원천 지표 경로(Q10)는 Calculation 없이
    Fact 2건을 바로 견준다.

    한쪽으로 대신 처리하지 않는다. Q10을 파생지표 경로로 흘려보내면 매출을 물은 질문에
    영업이익률이 돌아가고, 형식은 맞으므로 틀렸다는 사실이 결과에 드러나지 않는다. 반대
    방향도 같다.

    if/elif로 나열하고 대응표를 쓰지 않는다. 모양이 둘뿐이기 때문이다.

    마지막 갈래는 값을 돌려주지 않고 예외로 멈춘다. CrossEntityShape에 모양을 추가하고 실행
    단계를 붙이지 않은 경우이며, 그것은 계획의 문제가 아니라 구현의 누락이다. 정지 지점으로
    표현하면 실행할 수 없는 모양이 답변 경로의 판정처럼 보이고, 가장 가까운 모양으로 대신
    실행하면 묻지 않은 값이 답변에 실린다. 둘 다 하지 않는다.

    Raises:
        ValueError: 실행 단계가 붙어 있지 않은 모양.
    """
    if shape is CrossEntityShape.SOURCE_METRIC:
        return _execute_source_metric_comparison(
            plan,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )

    if shape is CrossEntityShape.DERIVED_METRIC:
        return _execute_derived_metric_comparison(
            plan,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )

    raise ValueError(
        f"실행 단계가 없는 모양입니다: {shape.value}. "
        "CrossEntityShape에 모양을 추가할 때는 이 분기도 함께 넓혀야 합니다."
    )


def build_cross_entity_compare_result(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> CrossEntityCompareResult:
    """
    기업 간 비교 계획 하나를 구조화 결과까지 처리한다.

    실패 시 예외를 올리지 않고 blocked를 채운 결과를 돌려준다. 어느 단계에서 왜 막혔는지
    호출자가 그대로 볼 수 있게 하기 위해서다.
    """
    unsupported = _unsupported_plan(plan)

    if unsupported is not None:
        return CrossEntityCompareResult(plan=plan, blocked=unsupported)

    shape = resolve_shape(plan)

    if shape is None:
        # _unsupported_plan을 통과한 계획은 반드시 모양이 확정된다. 두 판정이 같은
        # resolve_shape을 보므로 정상 경로에서는 도달하지 않는다. 그래도 확인하는 이유는,
        # 나중에 guard와 모양 판정이 서로 다른 기준을 보게 됐을 때 그 사실이 값이 아니라
        # 예외로 드러나야 하기 때문이다. 여기서 한쪽 모양을 골라 이어가면 열지 않기로 한
        # 계획이 실행된다.
        raise ValueError(
            "입구 guard를 통과했는데 모양이 확정되지 않았습니다: "
            f"metric={plan.metric!r}, derived_metric={plan.derived_metric!r}."
        )

    return _execute_comparison(
        plan,
        shape,
        corpus_root=corpus_root,
        manifest_path=manifest_path,
    )


def handle_cross_entity_compare(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> AgentExecutionResult:
    """
    Router가 부르는 진입점.

    공통 Contract인 AgentExecutionResult를 돌려준다. Pattern 내부 표현이 필요한 호출자는
    build_cross_entity_compare_result를 직접 쓴다.
    """
    return normalize_cross_entity_compare_result(
        build_cross_entity_compare_result(
            plan,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )
    )
