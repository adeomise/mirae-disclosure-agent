"""Pattern C — Temporal Compare handler.

역할:
- 같은 기업의 두 회계연도에서 파생지표를 각각 만들고, 그 변화량까지 확정한다.

현재 구현 범위:
- 대표 질문 Q07 계열("<기업> 매출이 <연도>년이랑 <연도>년에 어떻게 달라졌어?").
- 대표 질문 Q08 계열("<기업> 영업이익률이 <연도>년이랑 <연도>년에 어떻게 달라졌어?").

  두 모양이 갈리는 자리는 resolve_shape이며, 만드는 Calculation의 개수가 다르다.
  Q07은 원천 Fact 2건에서 증감액·증감률 2건, Q08은 기간별 비율 2건과 그 변화량 1건이다.

    QueryPlan
      -> 계획 모양 확인          (이 모듈)
      -> 파생지표 대응표          (agent.derived_metrics)
      -> 기간별 원천 Fact 확보     (agent.period_retrieval)
      -> guard① 계산 입력 검증     (agent.calculator)
      -> 기간별 비율 Calculation   (agent.calculator)
      -> guard② 비교 가능성 검증   (agent.calculator)
      -> 변화량 Calculation        (agent.calculator)
      -> TemporalCompareResult
      -> AgentExecutionResult     (agent.normalization)

계획 모양을 입구에서 다시 보는 이유:
    Router는 intent 하나만 보고 handler를 고른다. temporal_compare로 분류되는 계획은 Q08
    하나가 아니다. 원천 지표를 두 기간에 걸쳐 묻는 질문("2024년 매출과 2025년 매출 비교해줘"),
    절대연도 하나에 해석하지 않은 상대 기간이 붙은 질문("2025년 매출이 전년보다 얼마나
    늘었어?"), 지표를 읽지 못한 질문이 모두 같은 intent로 들어온다.

    handler가 등록되기 전에는 Router가 그 전부를 routing/unimplemented_intent로 막았다.
    등록한 뒤에도 막는 것은 같아야 하고, 다만 막는 자리가 Router에서 이 입구로 옮겨온다.
    여기서 걸러내지 않으면 기간이 하나뿐인 계획으로 변화량을 만들거나, 원천 지표 두 건을
    영업이익률로 착각해 계산하게 된다. 둘 다 형식은 맞고 질문에는 답하지 않은 결과다.

    막을 때 사유를 넷으로 나눈다. 무엇을 구현하면 풀리는 질문인지가 결과에 남아야 후속
    작업을 고를 수 있기 때문이며, Router가 미구현 intent와 미지의 intent를 나눈 것과 같은
    이유다.

무엇을 하지 않는가:
- 우열을 판정하지 않는다. Q08은 변화량 자체를 묻고 최종 대소 관계를 요구하지 않으므로
  ComparisonResult를 만들지 않는다(evidence_and_answerability.md Calculation cardinality).
- 상대 기간("작년")을 연도로 옮기지 않는다. 그 해석은 이미 있으나 질의 해석 계층의 몫이며
  (query_analyzer의 기준 연도 규칙), 여기로 오는 계획에는 이미 옮겨진 절대연도가 담겨 있다.
  옮기지 못한 계획은 연도 개수 검사에서 막힌다.
- 자연어를 만들지 않는다. 문장 조립은 answer_builder의 몫이다.

문서군을 다시 확인하지 않는 이유:
    시계열 비교 계획은 document_class·document_subtype을 기본값(정기공시·사업보고서)으로만
    만든다. 다른 값이 들어오면 collect_period_facts가 해당 공시를 찾지 못해 filing_discovery
    에서 멈추며, 그 정지 사유가 실제로 일어난 일과 같다. 도달하지 않는 검사를 미리 두어
    사유만 늘리지 않는다.

입력:
- QueryPlan, corpus 루트, manifest 경로

출력:
- AgentExecutionResult. 실패해도 예외를 올리지 않고 실행 상태로 표현한다.
  Pattern 내부 표현(TemporalCompareResult)이 필요한 호출자는 build_temporal_compare_result를
  직접 쓴다.

TODO:
- 매출 외 원천 지표의 시계열 증감(SUPPORTED_SOURCE_METRICS 확장)
- 기간이 셋 이상인 추이 질문
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from disclosure_agent.agent.answer_builder import (
    REASON_AMBIGUOUS_FILING,
    REASON_INCOMPARABLE_NARRATIVE_SECTIONS,
    REASON_NO_MATCHING_FILING,
    REASON_PARSE_FAILED,
    REASON_UNSUPPORTED_COMPARISON_METRIC,
    REASON_UNSUPPORTED_COMPARISON_PERIOD,
    REASON_UNSUPPORTED_COMPARISON_SUBJECT,
    REASON_UNSUPPORTED_DERIVED_OPERATION,
    REASON_UNUSABLE_CALCULATION_INPUT,
    STAGE_COMPARABILITY,
    STAGE_FILING_DISCOVERY,
    STAGE_PARSING,
    STAGE_PATTERN_ENTRY,
    STAGE_SECTION_NAVIGATION,
    BlockedInfo,
)
from disclosure_agent.agent.calculator import (
    OPERATION_DELTA,
    OPERATION_DELTA_PCT,
    OPERATION_DIFF_PP,
    OPERATION_RATIO,
    CalculationBlockedError,
    delta,
    delta_pct,
    diff,
    ratio,
    validate_calculation_inputs,
    validate_temporal_comparability,
    validate_temporal_fact_comparability,
)
from disclosure_agent.agent.derived_metrics import (
    DerivedMetric,
    DerivedMetricSpec,
    get_derived_metric_spec,
    required_source_metrics,
)
from disclosure_agent.agent.normalization import normalize_temporal_compare_result
from disclosure_agent.agent.period_retrieval import collect_period_facts
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.common.enums import MetricConcept, SectionScope
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    CalculationResult,
    Evidence,
    Fact,
)
from disclosure_agent.periodic.adapter import adapt_section_summary
from disclosure_agent.periodic.sections import SectionNavigationError
from disclosure_agent.periodic.service import collect_scope_content, load_parsed_document
from disclosure_agent.retrieval.metadata_filter import (
    FilingNotFoundError,
    list_filings,
)

__all__ = [
    "NARRATIVE_SECTION_SCOPE",
    "PeriodDerivedValue",
    "TemporalCompareResult",
    "TemporalShape",
    "build_temporal_compare_result",
    "handle_temporal_compare",
    "is_narrative_plan",
    "resolve_shape",
    "source_metrics_for",
]

# 서술 비교로 다루는 문서 내 범위.
#
# 값 하나만 연다. Pattern C의 서술 경로가 실제로 확인한 Section 규칙이 business_overview
# 하나뿐이고(periodic.sections.SECTION_SCOPE_RULES), 다른 범위를 함께 열면 규칙을 확인하지
# 않은 Section을 견주게 된다.
NARRATIVE_SECTION_SCOPE = SectionScope.BUSINESS_OVERVIEW

# 이번 구현이 다루는 기간 수. 두 기간의 변화량 하나를 만든다.
COMPARED_PERIOD_COUNT = 2

# 이번 구현이 여는 원천 지표와 파생 개념.
#
# 허용 목록으로 둔다. "막을 것"을 세는 대신 "열 것"을 세는 이유는, 어휘가 넓어질 때 새로
# 들어온 지표가 조용히 통과하지 않게 하기 위해서다. Pattern D가 같은 자리에서 같은 방식을
# 쓴다(handlers/cross_entity_compare.SUPPORTED_SOURCE_METRICS).
SUPPORTED_SOURCE_METRICS: tuple[MetricConcept, ...] = (MetricConcept.REVENUE,)

SUPPORTED_DERIVED_METRICS: tuple[DerivedMetric, ...] = (DerivedMetric.OPERATING_MARGIN,)


class TemporalShape(StrEnum):
    """
    이번 구현이 실행할 수 있는 계획 모양.

    둘로 나누는 이유는 확보할 원천 지표와 만들 결과의 개수가 다르기 때문이다.

        SOURCE_METRIC   Q07. 기간별 원천 Fact 1건씩에서 증감액과 증감률을 만든다.
                        기간별 파생값이 없으므로 Calculation은 2건이다
                        (evidence_and_answerability.md Calculation cardinality).
        DERIVED_METRIC  Q08. 기간별로 비율 Calculation을 하나씩 만든 뒤 그 둘의 변화량을
                        만든다. Calculation 3건이다.

    둘 다 ComparisonResult를 만들지 않는다. Q07·Q08 모두 증감 수치 자체를 묻고 최종 우열
    판정을 요구하지 않는다. 그 점이 Pattern D와 갈리는 자리다.

    intent가 아니다. Router는 intent만 보고 handler를 고르며, 그 안에서 어떤 모양을 다룰지는
    이 Pattern이 안다. 공유 Contract의 Intent enum을 넓히지 않는 이유가 그것이다.
    Pattern D의 CrossEntityShape와 같은 구조이며 축만 다르다.
    """

    SOURCE_METRIC = "source_metric"
    DERIVED_METRIC = "derived_metric"


def resolve_shape(plan: QueryPlan) -> TemporalShape | None:
    """
    계획이 어느 모양인지 읽는다. 이번 범위 밖이면 None이며 추측하지 않는다.

    두 모양은 배타다. QueryPlan이 metric과 derived_metric을 함께 담지 못하게 이미 막고
    있으므로(QueryPlan.__post_init__), 여기서 둘 다 채워진 계획은 만들어질 수 없다.

    둘 다 비어 있는 계획은 어느 모양도 아니다. 질문에서 지표를 읽지 못한 경우이며, 한쪽을
    기본값으로 골라 주지 않는다. Pattern D의 같은 이름 함수와 같은 구조다.
    """
    if plan.derived_metric is not None:
        if plan.derived_metric in SUPPORTED_DERIVED_METRICS:
            return TemporalShape.DERIVED_METRIC

        return None

    if plan.metric is not None and plan.metric in SUPPORTED_SOURCE_METRICS:
        return TemporalShape.SOURCE_METRIC

    return None


def source_metrics_for(plan: QueryPlan) -> tuple[MetricConcept, ...]:
    """
    이 계획을 실행하려면 기간마다 어떤 원천 지표를 확보해야 하는가.

    파생 개념을 원천 지표로 펼치는 일은 대응표가 한다. 계획에 함께 담아 두면 같은 질문에
    대응표와 계획이 서로 다른 답을 갖는 순간이 생긴다(derived_metrics.required_source_metrics).

    Raises:
        ValueError: 이번 범위 밖의 계획. 호출하기 전에 _unsupported_plan을 통과시켜야 한다.
    """
    shape = resolve_shape(plan)

    if shape is TemporalShape.DERIVED_METRIC:
        return required_source_metrics(plan.derived_metric)

    if shape is TemporalShape.SOURCE_METRIC:
        return (MetricConcept(plan.metric),)

    raise ValueError(
        "이번 구현이 다루지 않는 계획에서 원천 지표를 읽을 수 없습니다: "
        f"metric={plan.metric!r}, derived_metric={plan.derived_metric!r}. "
        "_unsupported_plan을 먼저 통과시켜야 합니다."
    )


@dataclass(frozen=True)
class PeriodDerivedValue:
    """
    한 회계연도의 파생값과 그 값을 만든 원천 Fact.

    Calculation만 들고 다니지 않는 이유:
        CalculationResult는 값·단위·참조만 담는다. 두 기간의 파생값이 같은 기업의 같은
        회계기준에서 나왔는지는 그 안에 없고, 참조를 따라가려면 결과 전체가 필요하다.
        guard②가 판정에 쓸 원천을 여기서 함께 들고 간다.
    """

    fiscal_year: int
    sources: tuple[Fact, ...]
    calculation: CalculationResult


@dataclass(frozen=True)
class TemporalCompareResult:
    """
    Pattern C의 구조화 결과.

    공통 Contract가 아니다. 다만 Pattern A와 달리 Fact/Evidence를 이미 Contract 타입으로
    들고 있다. agent.period_retrieval이 변환까지 끝낸 값을 돌려주기 때문이며, 여기서 다시
    변환하면 같은 변환이 두 벌이 된다. Pattern E가 EventHistory를 그대로 싣는 것과 같다.

    blocked가 있으면 calculations는 비어 있다. 중간까지 만든 파생값을 남기지 않는 이유:
        guard②가 막은 것은 "두 값을 함께 쓸 수 없다"이다. 그런데 기간별 비율 2건이 결과에
        남아 있으면 받는 쪽이 그것으로 차이를 직접 계산할 수 있고, 그러면 guard가 막은 일이
        결과를 받는 쪽에서 그대로 일어난다.

    facts와 evidence는 blocked여도 남는다. 확보 자체는 정상이었고, 어느 값이 견줄 수 없다고
    판정됐는지 보려면 그 값이 있어야 한다.
    """

    plan: QueryPlan
    facts: tuple[Fact, ...] = field(default_factory=tuple)
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)
    calculations: tuple[CalculationResult, ...] = field(default_factory=tuple)
    blocked: BlockedInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        """공통 Contract로 옮기기 전의 진단용 표현."""
        return {
            "intent": self.plan.intent,
            "query": self.plan.to_dict(),
            "facts": [fact.model_dump() for fact in self.facts],
            "evidence": [item.model_dump() for item in self.evidence],
            "calculations": [item.model_dump() for item in self.calculations],
            "blocked": self.blocked.to_dict() if self.blocked else None,
        }


def _unsupported_plan(plan: QueryPlan) -> BlockedInfo | None:
    """
    이번 구현이 실행할 수 있는 계획인지 본다. 실행할 수 없으면 정지 지점을 돌려준다.

    Router가 열린 뒤 이 자리가 유일한 방어선이다. 판정 순서는 좁히는 순서를 따른다.
    누구를(기업) -> 언제(기간) -> 무엇을(지표) -> 어떻게(연산).
    """
    if len(plan.companies) != 1:
        return BlockedInfo(
            stage=STAGE_PATTERN_ENTRY,
            reason=REASON_UNSUPPORTED_COMPARISON_SUBJECT,
            detail=(
                f"시계열 비교는 기업 한 곳의 두 기간을 견줍니다. "
                f"계획에 담긴 기업: {', '.join(plan.companies)}"
            ),
        )

    if len(plan.years) != COMPARED_PERIOD_COUNT:
        # 절대연도가 하나뿐인 계획이 여기로 온다. 해석하지 않은 상대 기간 표현이 함께 있을
        # 수 있어(query_analyzer.QueryPlan.year 주석), 남은 한 연도로 답하면 두 기간을 물은
        # 질문에 한 기간만 답하게 된다. 그것이 F6-02에서 막은 동작이다.
        listed = ", ".join(str(year) for year in plan.years) or "없음"

        return BlockedInfo(
            stage=STAGE_PATTERN_ENTRY,
            reason=REASON_UNSUPPORTED_COMPARISON_PERIOD,
            detail=(
                f"이번 구현은 절대연도 {COMPARED_PERIOD_COUNT}개를 견주는 질문만 다룹니다. "
                f"계획에 담긴 회계연도: {listed}. "
                "상대 기간 표현을 연도로 옮기는 해석은 아직 구현되지 않았습니다."
            ),
        )

    if plan.section_scope is NARRATIVE_SECTION_SCOPE:
        if is_narrative_plan(plan):
            # 서술 경로는 지표를 요구하지 않는다. 아래 지표 검사에 걸리면 "다루는 지표가
            # 아니다"로 막히는데, 이 질문은 지표를 묻지 않았으므로 그 사유는 실제로 일어난
            # 일과 다르다. 기업·기간 검사는 두 경로가 함께 쓰므로 이 자리가 앞이 아니라 뒤다.
            return None

        # 서술 범위와 지표를 함께 담은 계획이다. 어느 경로로도 보내지 않는다.
        #
        # 아래 지표 검사로 흘려보내면 매출처럼 지원하는 지표는 그대로 통과해 수치 경로가
        # 답하고, 질문이 함께 밝힌 범위는 결과 어디에도 남지 않는다. 서술 경로로 보내면
        # 반대로 지표가 사라진다. 둘 다 형식은 맞고 질문의 절반에만 답한 결과다.
        requested = plan.derived_metric.value if plan.derived_metric else plan.metric

        return BlockedInfo(
            stage=STAGE_PATTERN_ENTRY,
            reason=REASON_UNSUPPORTED_COMPARISON_METRIC,
            detail=(
                f"서술 범위({plan.section_scope.value})와 지표({requested})를 함께 견주는 "
                "질문은 이번 구현이 다루지 않습니다. 서술 비교는 지표 없이, 수치 비교는 범위 "
                "없이 각각 따로 물어야 합니다. 한쪽 요청을 버리고 답하지 않습니다."
            ),
        )

    shape = resolve_shape(plan)

    if shape is None:
        # 지표를 읽지 못한 경우와 읽었으나 범위 밖인 경우를 detail에서 나눈다. 후속 작업이
        # 서로 다르기 때문이며, Pattern D가 같은 자리에서 하는 구분과 같다.
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
                f"이번 구현이 시계열 비교로 다루는 지표는 {supported}뿐입니다. {missing}"
            ),
        )

    if shape is TemporalShape.DERIVED_METRIC:
        spec = get_derived_metric_spec(plan.derived_metric)

        if spec.operation != OPERATION_RATIO:
            # 대응표에는 있으나 계산기가 아직 만들 수 없는 연산이다. 대응표만 넓히고 계산기를
            # 넓히지 않은 경우가 여기로 오며, 조용히 ratio로 처리하면 정의가 다른 값이 나간다.
            return BlockedInfo(
                stage=STAGE_PATTERN_ENTRY,
                reason=REASON_UNSUPPORTED_DERIVED_OPERATION,
                detail=(
                    f"파생지표 {plan.derived_metric.value}의 연산은 {spec.operation}이며, "
                    f"이번 구현이 만들 수 있는 연산은 {OPERATION_RATIO}뿐입니다."
                ),
            )

    return None


def _calculation_id(plan: QueryPlan, *, suffix: str) -> str:
    """
    결정적 Calculation 식별자.

    같은 질문을 두 번 실행하면 같은 값이 나와야 하므로 난수를 쓰지 않는다. 한 실행 결과
    안에서만 유일하면 되고, 이번 Pattern은 기업 하나를 다루므로 요청 개념과 회계연도로
    충분하다. 기업 축이 필요한 Pattern D는 그 handler가 자기 축을 덧붙인다.

    개념 표기는 계획이 담은 쪽에서 읽는다. Q07은 derived_metric이 비어 있으므로 그쪽을 바로
    참조하면 식별자를 만들 수 없다. 두 모양이 같은 이름 공간을 쓰지만 원천 지표와 파생 개념의
    어휘가 겹치지 않으므로(MetricConcept / DerivedMetric) 한 실행 안에서 충돌하지 않는다.
    """
    concept = plan.derived_metric.value if plan.derived_metric else plan.metric

    return f"calculation_{concept}_{suffix}"


def _by_year_and_metric(facts: tuple[Fact, ...]) -> dict[tuple[int | None, str], Fact]:
    """확보한 Fact를 (회계연도, 지표)로 찾을 수 있게 둔다."""
    return {(fact.fiscal_year, fact.metric): fact for fact in facts}


def _require_fact(
    index: dict[tuple[int | None, str], Fact],
    *,
    fiscal_year: int,
    metric: MetricConcept,
) -> Fact:
    """
    요청한 조합의 Fact를 읽는다. 없으면 막는다.

    collect_period_facts는 요청한 조합을 전부 확보하거나 하나도 담지 않으므로 정상 경로에서는
    비지 않는다. 그래도 확인하는 이유는, 확보 계약이 바뀌었을 때 그 사실이 값이 아니라
    KeyError로 드러나면 어느 조합이 빠졌는지 결과에 남지 않기 때문이다.
    """
    fact = index.get((fiscal_year, metric))

    if fact is None:
        raise CalculationBlockedError(
            REASON_UNUSABLE_CALCULATION_INPUT,
            f"{fiscal_year} 회계연도의 {metric.value}를 확보 결과에서 찾지 못했습니다.",
        )

    return fact


def _build_period_value(
    plan: QueryPlan,
    *,
    spec: DerivedMetricSpec,
    index: dict[tuple[int | None, str], Fact],
    fiscal_year: int,
) -> PeriodDerivedValue:
    """
    한 회계연도의 파생값을 만든다. guard①을 통과한 뒤에만 계산한다.

    Raises:
        CalculationBlockedError: 확보 결과가 어긋났거나 guard①이 막은 경우.
    """
    numerator = _require_fact(index, fiscal_year=fiscal_year, metric=spec.numerator)

    denominator = _require_fact(index, fiscal_year=fiscal_year, metric=spec.denominator)

    validate_calculation_inputs(
        numerator=numerator,
        denominator=denominator,
        numerator_metric=spec.numerator,
        denominator_metric=spec.denominator,
    )

    return PeriodDerivedValue(
        fiscal_year=fiscal_year,
        sources=(numerator, denominator),
        calculation=ratio(
            calculation_id=_calculation_id(plan, suffix=str(fiscal_year)),
            numerator=numerator,
            denominator=denominator,
            output_unit=spec.output_unit,
        ),
    )


def _source_metric_calculations(
    plan: QueryPlan,
    *,
    index: dict[tuple[int | None, str], Fact],
    old_year: int,
    new_year: int,
) -> tuple[CalculationResult, ...]:
    """
    원천 지표 두 기간에서 증감액과 증감률을 만든다(Q07).

    guard①을 지나지 않는다. 기간별 파생값이 없어 계산 입력 검증이 적용될 자리가 없고, 단위
    정규화 성공(C8) 확인은 guard②가 함께 본다
    (calculator.validate_temporal_fact_comparability의 "guard가 하나뿐인 이유").

    두 Calculation 모두 원천 Fact 2건을 직접 참조한다. 증감률이 증감액을 참조하지 않는
    이유는 calculator.delta_pct가 적어 둔 두 가지와 같다.

    Raises:
        CalculationBlockedError: 확보 결과가 어긋났거나 guard②가 막은 경우.
    """
    (metric,) = source_metrics_for(plan)

    previous = _require_fact(index, fiscal_year=old_year, metric=metric)

    current = _require_fact(index, fiscal_year=new_year, metric=metric)

    validate_temporal_fact_comparability(
        previous=previous,
        current=current,
        expected_metric=metric,
        expected_previous_year=old_year,
        expected_current_year=new_year,
    )

    return (
        delta(
            calculation_id=_calculation_id(
                plan, suffix=f"{OPERATION_DELTA}_{old_year}_{new_year}"
            ),
            previous=previous,
            current=current,
        ),
        delta_pct(
            calculation_id=_calculation_id(
                plan, suffix=f"{OPERATION_DELTA_PCT}_{old_year}_{new_year}"
            ),
            previous=previous,
            current=current,
        ),
    )


def _derived_metric_calculations(
    plan: QueryPlan,
    *,
    index: dict[tuple[int | None, str], Fact],
    old_year: int,
    new_year: int,
) -> tuple[CalculationResult, ...]:
    """
    기간별 비율 둘과 그 변화량을 만든다(Q08).

    Raises:
        CalculationBlockedError: 확보 결과가 어긋났거나 guard①·guard②가 막은 경우.
    """
    spec = get_derived_metric_spec(plan.derived_metric)

    old = _build_period_value(plan, spec=spec, index=index, fiscal_year=old_year)

    new = _build_period_value(plan, spec=spec, index=index, fiscal_year=new_year)

    validate_temporal_comparability(
        left=old.calculation,
        right=new.calculation,
        left_sources=old.sources,
        right_sources=new.sources,
        expected_unit=spec.output_unit,
    )

    change = diff(
        calculation_id=_calculation_id(
            plan, suffix=f"{OPERATION_DIFF_PP}_{old_year}_{new_year}"
        ),
        old=old.calculation,
        new=new.calculation,
    )

    return (old.calculation, new.calculation, change)


# --------------------------------------------------------------------------
# 서술 경로(Q09)
#
# 값을 하나도 만들지 않는다. 두 연도의 같은 의미 Section을 확보해 Evidence로 남기고,
# 그 둘을 항목 단위로 견줄 수 있는지까지만 판정한다.
#
# 이번 구현이 변화 항목(NarrativeComparisonClaim)을 만들지 않는 이유:
#     변화 항목은 양쪽 연도의 Evidence를 모두 요구한다(common.schemas). 그러려면 두 해의
#     서술을 항목 단위로 짝지어야 하고, 짝지으려면 해마다 달라지지 않는 항목 식별자가 있어야
#     한다. 현재 SectionContentItem이 가진 것은 셋뿐이며 어느 것도 그 식별자가 아니다.
#
#         label/excerpt  본문 텍스트 자체다. 같으면 내용이 같다는 뜻이므로 변화 항목이
#                        나올 수 없고, 다르면 짝이 지어지지 않는다.
#         ordinal        원문 순서다. 앞에 단락이 하나 끼어들면 그 뒤가 전부 밀린다. 밀린
#                        짝으로 만든 변화 항목은 양쪽 Evidence를 갖춘 채로 틀린 말을 한다.
#         table 내용     수치다. Q09는 수치 비교가 아니다.
#
#     실제 코퍼스에서도 같은 결론이 나왔다. 삼성바이오로직스의 사업의 개요는 2023·2024년에
#     "1) 2) 3) 4)", 2025년에 "가. 나. 다."로 적혀 있어 두 해가 공유하는 항목 표기가 없다.
#     양쪽에 함께 있는 유일한 이름표는 "[사업부문별 요약 재무현황]"이고 그 내용은 수치다.
#
# 그래서 변화가 없다고 답하지 않는다:
#     짝을 짓지 못한 것과 짝을 지어 보니 같았던 것은 다르다. 둘을 같은 답으로 내보내는 것이
#     이 문항의 주요 실패 모드이며(docs/agent_design/representative_questions.md Q09),
#     claims=[]는 후자의 표현이다. 전자는 NOT_COMPARABLE로 나간다.
# --------------------------------------------------------------------------


def is_narrative_plan(plan: QueryPlan) -> bool:
    """
    서술 비교로 다룰 계획인가. 순수한 서술 모양일 때만 참이다.

    세 조건을 함께 요구한다. 범위가 서술 범위이고, 원천 지표도 파생 개념도 묻지 않았을 때다.

    범위만으로 판정하지 않는 이유:
        범위와 지표를 함께 담은 계획("사업 개요"와 "매출"을 한 질문에)은 두 경로 중 어느
        쪽으로 보내도 한쪽 요청이 조용히 사라진다. 서술 경로로 보내면 지표가, 수치 경로로
        보내면 범위가 버려지며, 어느 쪽 결과도 형식은 맞는다. 그런 계획은 두 경로 모두
        받지 않고 _unsupported_plan이 입구에서 막는다.

    지표의 부재만으로 판정하지 않는 이유:
        아직 어휘가 없는 원천 지표를 물은 질문도 지표가 비어 있다. 그 질문까지 서술 경로로
        보내면 묻지도 않은 Section을 견주게 된다. 범위를 밝혔다는 사실이 여전히 필요하다.
    """
    return (
        plan.section_scope is NARRATIVE_SECTION_SCOPE
        and plan.metric is None
        and plan.derived_metric is None
    )


@dataclass(frozen=True)
class _NarrativeSide:
    """한 회계연도에서 확보한 서술 Section."""

    fiscal_year: int
    doc_id: str
    section_path: tuple[str, ...]
    evidence: tuple[Evidence, ...]


def _collect_narrative_side(
    plan: QueryPlan,
    *,
    fiscal_year: int,
    corpus_root: str | Path,
    manifest_path: str | Path | None,
) -> tuple[_NarrativeSide | None, BlockedInfo | None]:
    """
    한 회계연도의 서술 Section을 확보한다.

    공시는 1건으로 확정될 때만 진행한다. Pattern B가 쓰는 규칙과 같다. locate_filing으로
    접수번호가 가장 큰 것을 고르면 어느 판본을 견줬는지가 결과에 남지 않으며, 두 해를 견주는
    질문에서는 그 선택이 곧 비교 대상을 바꾼다.

    정정본을 후보에 포함한다:
        판본 선택 정책(docs/agent_design/open_decisions.md D5)은 아직 미정이고, 이 handler가
        그 정책을 새로 정하지 않는다. 정정본을 조회에서 빼면 원본과 정정본이 함께 있는
        연도가 원본 1건으로 보여 "유일하게 확정됐다"고 진행하게 된다. 그것은 정책 없이 원본을
        고른 것이며, 고른 사실이 결과에 남지 않는다. 그래서 정정본까지 후보로 보고, 같은
        연도에 판본이 둘 이상이면 어느 것도 고르지 않고 ambiguous_filing으로 멈춘다.

    Section 확정 실패는 periodic.sections의 사유를 그대로 옮긴다. 여기서 사유를 새로 만들면
    Pattern B와 같은 상황이 두 이름을 갖게 된다.
    """
    resolved_manifest = (
        Path(manifest_path) if manifest_path else Path(corpus_root) / "manifest.jsonl"
    )

    try:
        candidates = list_filings(
            manifest_path=resolved_manifest,
            corpus_root=corpus_root,
            company=plan.company,
            document_class=plan.document_class,
            document_subtype=plan.document_subtype,
            year=fiscal_year,
            include_corrections=True,
        )

    except FilingNotFoundError as error:
        return None, BlockedInfo(
            stage=STAGE_FILING_DISCOVERY,
            reason=error.reason,
            detail=error.detail,
        )

    if not candidates:
        return None, BlockedInfo(
            stage=STAGE_FILING_DISCOVERY,
            reason=REASON_NO_MATCHING_FILING,
            detail=(
                "manifest에서 조건에 맞는 공시를 찾지 못했습니다: "
                f"{plan.company} / {fiscal_year} / {plan.document_class} / "
                f"{plan.document_subtype}"
            ),
        )

    if len(candidates) > 1:
        listed = ", ".join(candidate.doc_id for candidate in candidates)

        return None, BlockedInfo(
            stage=STAGE_FILING_DISCOVERY,
            reason=REASON_AMBIGUOUS_FILING,
            detail=(
                f"{fiscal_year} 회계연도의 조건에 맞는 공시(정정본 포함)가 "
                f"{len(candidates)}건입니다: {listed}. 판본 선택 정책이 정해지지 않아 비교 "
                "대상 문서를 하나 골라 답하지 않습니다."
            ),
        )

    filing = candidates[0]

    try:
        document = load_parsed_document(filing.main_document)

    except (OSError, ValueError) as error:
        return None, BlockedInfo(
            stage=STAGE_PARSING,
            reason=REASON_PARSE_FAILED,
            detail=f"{fiscal_year} 회계연도 문서를 열지 못했습니다: {error}",
        )

    try:
        selection, items = collect_scope_content(
            document,
            section_scope=NARRATIVE_SECTION_SCOPE,
        )

    except SectionNavigationError as error:
        return None, BlockedInfo(
            stage=STAGE_SECTION_NAVIGATION,
            reason=error.reason,
            detail=f"{fiscal_year} 회계연도: {error.detail}",
        )

    # Evidence 변환은 periodic.adapter가 한다. Pattern B가 쓰는 변환과 같은 것을 쓰며,
    # 여기서 다시 만들면 같은 항목이 두 벌의 evidence_id를 갖게 된다.
    #
    # DocumentSummary는 버린다. 이 Pattern의 답은 요약이 아니라 비교이고, 결과에 실으면
    # answer_builder의 단일 문서 요약 경로가 두 건을 받아 멈춘다.
    _, evidence = adapt_section_summary(
        document,
        section_scope=NARRATIVE_SECTION_SCOPE,
        selection=selection,
        items=list(items),
    )

    return (
        _NarrativeSide(
            fiscal_year=fiscal_year,
            doc_id=filing.doc_id,
            section_path=tuple(selection.section_path),
            evidence=tuple(evidence),
        ),
        None,
    )


def _build_narrative_result(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> TemporalCompareResult:
    """
    서술 비교 계획 하나를 구조화 결과까지 처리한다.

    한쪽이라도 확보하지 못하면 그 자리의 사유로 멈춘다. 확보한 한쪽만으로 답하지 않는 이유는
    수치 경로와 같다. 한 해의 서술은 "무엇이 적혀 있다"까지만 말하며, 질문이 물은 것은
    "무엇이 달라졌다"이다.
    """
    old_year, new_year = sorted(plan.years)

    sides: list[_NarrativeSide] = []

    for fiscal_year in (old_year, new_year):
        side, blocked = _collect_narrative_side(
            plan,
            fiscal_year=fiscal_year,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )

        if side is None:
            # 앞선 연도에서 확보한 Evidence는 남긴다. 어디까지 갔다가 멈췄는지가 결과에
            # 남아야 하며, 수치 경로가 확보한 Fact를 남기는 것과 같은 자리다.
            return TemporalCompareResult(
                plan=plan,
                evidence=tuple(
                    item for collected in sides for item in collected.evidence
                ),
                blocked=blocked,
            )

        sides.append(side)

    left, right = sides

    # 양쪽을 모두 확보한 뒤에도 변화 항목을 만들지 않는다.
    #
    # 판정의 근거는 문서가 아니라 이 구현이 가진 규칙이다. 승인된 서술 항목 짝짓기 규칙이
    # 아직 없으므로, 두 기재의 내용이 무엇이든 결과는 같다. 그래서 문서에서 무엇을 세어
    # 판정한 것처럼 적지 않는다.
    #
    # 항목 이름이 몇 건 겹치는지를 세어 판정을 가르지 않는 이유:
    #     이름이 겹친다는 것은 그 항목의 본문이 같다는 뜻이다(SectionContentItem.label은
    #     본문 앞부분이다). 겹치면 변화가 없고 겹치지 않으면 짝이 없으므로, 어느 쪽이든
    #     변화 항목은 나오지 않는다. 그 수를 판정 근거처럼 적으면 "N건이 겹쳐서 짝지을 수
    #     없다"가 되는데, N이 0보다 클 때 그 문장은 성립하지 않는다.
    return TemporalCompareResult(
        plan=plan,
        evidence=(*left.evidence, *right.evidence),
        blocked=BlockedInfo(
            stage=STAGE_COMPARABILITY,
            reason=REASON_INCOMPARABLE_NARRATIVE_SECTIONS,
            detail=(
                f"{plan.company}의 {left.fiscal_year}·{right.fiscal_year} 회계연도 "
                f"'{' > '.join(right.section_path)}' 부분을 양쪽 모두 확보했습니다"
                f"({left.doc_id}, {right.doc_id}). "
                "다만 두 기재의 서술 항목을 같은 항목끼리 짝지을 수 있는 규칙이 이 구현에 "
                "아직 없어 변화 항목을 만들지 않습니다. 순서로 짝짓거나 비슷한 문구를 같은 "
                "항목으로 보지 않습니다. 변화가 없다는 판정이 아닙니다."
            ),
        ),
    )


def build_temporal_compare_result(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> TemporalCompareResult:
    """
    시계열 파생지표 비교 계획 하나를 구조화 결과까지 처리한다.

    실패 시 예외를 올리지 않고 blocked를 채운 결과를 돌려준다. 어느 단계에서 왜 막혔는지
    호출자가 그대로 볼 수 있게 하기 위해서다.
    """
    unsupported = _unsupported_plan(plan)

    if unsupported is not None:
        return TemporalCompareResult(plan=plan, blocked=unsupported)

    if is_narrative_plan(plan):
        return _build_narrative_result(
            plan,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )

    # QueryPlan을 검색 조건으로 옮기는 일은 여기서 한다. agent.period_retrieval은 질문 해석
    # 결과를 모르고 primitive만 받는다.
    #
    # plan.year는 쓰지 않는다. 시계열 비교 계획에서는 그 통로가 막혀 있고, 막혀 있는 이유가
    # 곧 이 Pattern이 존재하는 이유다.
    collection = collect_period_facts(
        company=plan.company,
        fiscal_years=plan.years,
        metrics=source_metrics_for(plan),
        accounting_scope=plan.accounting_scope,
        document_class=plan.document_class,
        document_subtype=plan.document_subtype,
        corpus_root=corpus_root,
        manifest_path=manifest_path,
    )

    if collection.blocked is not None:
        # 하나라도 확보하지 못하면 Fact를 담지 않는 것이 확보 계층의 계약이다. 부분 결과로
        # 답하지 않는 판정을 여기서 다시 내리지 않는다.
        return TemporalCompareResult(plan=plan, blocked=collection.blocked)

    index = _by_year_and_metric(collection.facts)

    # 변화량은 시간 순서로 만든다. 질문에 적힌 순서("2025년이랑 2024년")를 그대로 쓰면 같은
    # 변화에 부호가 뒤집힌 값이 나오고, 뒤집힌 값도 그럴듯하다.
    old_year, new_year = sorted(plan.years)

    # 모양에 따라 만드는 결과의 개수가 다르다. 어느 쪽이든 하나라도 막히면 파생값을 전부
    # 버린다는 규칙은 같다(TemporalCompareResult docstring).
    build = (
        _source_metric_calculations
        if resolve_shape(plan) is TemporalShape.SOURCE_METRIC
        else _derived_metric_calculations
    )

    try:
        calculations = build(
            plan, index=index, old_year=old_year, new_year=new_year
        )

    except CalculationBlockedError as error:
        # 확보한 Fact와 Evidence는 남기고 파생값은 하나도 남기지 않는다.
        return TemporalCompareResult(
            plan=plan,
            facts=collection.facts,
            evidence=collection.evidence,
            blocked=BlockedInfo(
                stage=STAGE_COMPARABILITY,
                reason=error.reason,
                detail=error.detail,
            ),
        )

    return TemporalCompareResult(
        plan=plan,
        facts=collection.facts,
        evidence=collection.evidence,
        calculations=calculations,
    )


def handle_temporal_compare(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> AgentExecutionResult:
    """
    Router가 부르는 진입점.

    공통 Contract인 AgentExecutionResult를 돌려준다. Pattern 내부 표현이 필요한 호출자는
    build_temporal_compare_result를 직접 쓴다.
    """
    return normalize_temporal_compare_result(
        build_temporal_compare_result(
            plan,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )
    )
