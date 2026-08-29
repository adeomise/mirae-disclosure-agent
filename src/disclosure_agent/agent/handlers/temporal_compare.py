"""Pattern C — Temporal Compare handler.

역할:
- 같은 기업의 두 회계연도에서 파생지표를 각각 만들고, 그 변화량까지 확정한다.

현재 구현 범위:
- 대표 질문 Q08 계열("<기업> 영업이익률이 <연도>년이랑 <연도>년에 어떻게 달라졌어?").

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
- 상대 기간("작년")을 연도로 옮기지 않는다. 기준 시점 정책이 없고, 그 해석을 붙이면 Pattern F가
  막고 있는 미래 방향 질문이 함께 통과한다(coverage_status.md Q07).
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
- 원천 지표의 시계열 증감(delta·delta_pct)으로 Q07 확장. 상대 기간 해석과 Pattern F를 같은
  작업으로 묶어야 한다.
- 기간이 셋 이상인 추이 질문
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    OPERATION_DIFF_PP,
    OPERATION_RATIO,
    CalculationBlockedError,
    diff,
    ratio,
    validate_calculation_inputs,
    validate_temporal_comparability,
)
from disclosure_agent.agent.derived_metrics import (
    DerivedMetricSpec,
    get_derived_metric_spec,
    required_source_metrics,
)
from disclosure_agent.agent.normalization import normalize_temporal_compare_result
from disclosure_agent.agent.period_retrieval import collect_period_facts
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.common.enums import MetricConcept
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    CalculationResult,
    Evidence,
    Fact,
)

__all__ = [
    "PeriodDerivedValue",
    "TemporalCompareResult",
    "build_temporal_compare_result",
    "handle_temporal_compare",
]

# 이번 구현이 다루는 기간 수. 두 기간의 변화량 하나를 만든다.
COMPARED_PERIOD_COUNT = 2


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

    if plan.derived_metric is None:
        # metric과 derived_metric은 QueryPlan이 배타로 강제한다. 둘 다 비어 있는 경우와
        # 원천 지표만 있는 경우는 후속 작업이 다르므로 detail에서 나눈다.
        missing = (
            f"원천 지표({plan.metric})의 시계열 증감은 아직 구현되지 않았습니다."
            if plan.metric is not None
            else "질문에서 지표를 읽지 못했습니다."
        )

        return BlockedInfo(
            stage=STAGE_PATTERN_ENTRY,
            reason=REASON_UNSUPPORTED_COMPARISON_METRIC,
            detail=(
                f"이번 구현은 파생지표의 시계열 비교만 다룹니다. {missing}"
            ),
        )

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
    안에서만 유일하면 되고, 이번 Pattern은 기업 하나를 다루므로 파생 개념과 회계연도로
    충분하다. 기업 축이 필요한 Pattern D는 그 handler가 자기 축을 덧붙인다.
    """
    return f"calculation_{plan.derived_metric.value}_{suffix}"


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

    spec = get_derived_metric_spec(plan.derived_metric)

    # QueryPlan을 검색 조건으로 옮기는 일은 여기서 한다. agent.period_retrieval은 질문 해석
    # 결과를 모르고 primitive만 받는다.
    #
    # plan.year는 쓰지 않는다. 시계열 비교 계획에서는 그 통로가 막혀 있고, 막혀 있는 이유가
    # 곧 이 Pattern이 존재하는 이유다.
    collection = collect_period_facts(
        company=plan.company,
        fiscal_years=plan.years,
        metrics=required_source_metrics(plan.derived_metric),
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

    try:
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
        calculations=(old.calculation, new.calculation, change),
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
