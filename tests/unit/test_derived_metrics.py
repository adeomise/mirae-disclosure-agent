"""파생지표를 원천 지표와 섞지 않고 계획에 담는지 검증한다.

무엇을 고정하는가:
    "영업이익률"은 어느 사업보고서에도 적혀 있지 않다. 두 원천 값(영업이익, 매출)에서
    계산으로 만들어지는 요청 개념이다. 그 사실이 계획에 드러나야 다음 단계가 무엇을
    확보하고 무엇을 계산할지 정할 수 있다.

    metric에 담기면 그 값이 그대로 추출 계층으로 넘어간다. 계산으로 답할 수 있는 질문이
    '지원하지 않는 지표'로 막히고, 막힌 이유가 실제 이유와 달라진다.

무엇을 검증하지 않는가:
    계산하지 않는다. 영업이익률 값도, 두 연도의 변화량도 만들지 않는다. Pattern C/D handler가
    없으므로 Q08·Q11은 여전히 routing 단계에서 막힌다. 여기서 고정하는 것은 '무엇을 물었는지가
    계획에 남는다'까지다.

판정 순서:
    파생 개념 판정이 원천 지표 판정보다 앞이다. "영업이익률"이 "영업이익"을 품고 있어서,
    순서가 뒤집히면 파생지표 질문이 원천 지표 질문으로 조용히 바뀐다. 3절에서 고정한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from disclosure_agent.agent import query_analyzer
from disclosure_agent.agent.derived_metrics import (
    DerivedMetric,
    get_derived_metric_spec,
    required_source_metrics,
)
from disclosure_agent.agent.query_analyzer import (
    INTENT_CROSS_ENTITY_COMPARE,
    INTENT_SINGLE_FACT,
    INTENT_TEMPORAL_COMPARE,
    METRIC_REVENUE,
    QueryPlan,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.agent.router import (
    answer_question,
    supported_intents,
)
from disclosure_agent.common.enums import (
    AnswerabilityStatus,
    ExecutionStatus,
    MetricConcept,
)

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"

DEFAULT_CORPUS_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "3.공시" / "3.공시" / "corpus"
)

# 대표 질문 원문. evaluation/questions.jsonl의 표기와 같아야 한다.
Q08 = "한미반도체 영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?"
Q09 = "삼성바이오로직스 사업 구조가 2023년이랑 2025년이랑 어떻게 달라졌어?"
Q10 = "2025년 HD현대중공업이랑 삼성중공업 매출 비교해줘."
Q11 = "HD현대중공업이랑 삼성중공업 중에 2025년 영업이익률이 더 높은 곳은 어디야?"

# 기업명 인식 목록은 테스트가 직접 넘긴다. 코퍼스를 읽지 않고 계획만 보기 위해서다.
REAL_COMPANIES = (
    "한미반도체",
    "삼성바이오로직스",
    "삼성전자",
    "HD현대중공업",
    "삼성중공업",
)

# 합성 manifest에 실제로 있는 기업명. 2024·2025 사업보고서를 모두 가지고 있다.
SYNTHETIC_COMPANY = "테스트반도체(주)"


def _resolve_corpus_root() -> Path | None:
    raw = os.environ.get(CORPUS_ROOT_ENV)

    candidate = Path(raw) if raw else DEFAULT_CORPUS_ROOT

    if candidate.is_dir() and (candidate / "manifest.jsonl").is_file():
        return candidate

    return None


def _plan(question: str) -> QueryPlan:
    return build_query_plan(question, known_companies=REAL_COMPANIES)


# --------------------------------------------------------------------------
# 1. 대응표
# --------------------------------------------------------------------------


def test_operating_margin_spec_keeps_numerator_and_denominator_apart() -> None:
    """
    분자와 분모를 이름으로 구분해 담는다.

    순서 없는 집합으로만 담으면 계산하는 쪽이 어느 쪽이 분자인지 다시 정해야 한다.
    영업이익/매출과 매출/영업이익은 서로 다른 값이고, 뒤집혀도 둘 다 그럴듯한 비율로 보여
    틀렸다는 사실이 결과에 드러나지 않는다.
    """
    spec = get_derived_metric_spec(DerivedMetric.OPERATING_MARGIN)

    assert spec.numerator is MetricConcept.OPERATING_INCOME
    assert spec.denominator is MetricConcept.REVENUE
    assert spec.operation == "ratio"
    assert spec.output_unit == "percent"


def test_required_source_metrics_are_derived_from_the_spec() -> None:
    """확보해야 할 원천 지표는 대응표에서 읽는다. 분자, 분모 순서다."""
    assert required_source_metrics(DerivedMetric.OPERATING_MARGIN) == (
        MetricConcept.OPERATING_INCOME,
        MetricConcept.REVENUE,
    )


def test_operating_margin_is_not_a_source_metric_concept() -> None:
    """
    파생 개념은 원천 지표 어휘에 없어야 한다.

    MetricConcept에 들어가면 metric이 영업이익률인 Fact가 타입상 합법이 되고, 원문에 없는
    값이 근거를 갖춘 원천 사실의 모습으로 돌아갈 수 있다.
    """
    assert "operating_margin" not in {item.value for item in MetricConcept}


def test_unregistered_derived_metric_fails_fast() -> None:
    """
    대응이 없는 파생 개념은 기본값을 만들어 주지 않는다.

    추측한 분자·분모로 계산하면 그럴듯한 숫자가 나오고, 그것이 틀렸다는 사실은 결과 어디에도
    남지 않는다.
    """
    with pytest.raises(ValueError):
        get_derived_metric_spec("net_margin")  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        required_source_metrics("net_margin")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 2. Q08 / Q11 — 같은 대응표를 공유한다
# --------------------------------------------------------------------------


def test_q08_plan_carries_the_derived_concept() -> None:
    """Q08의 영업이익률은 원천 지표가 아니라 요청 개념으로 담긴다."""
    plan = _plan(Q08)

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.companies == ("한미반도체",)
    assert plan.years == (2024, 2025)
    assert plan.metric is None
    assert plan.derived_metric is DerivedMetric.OPERATING_MARGIN


def test_q11_plan_carries_the_same_derived_concept() -> None:
    """비교 축이 기업이어도 요청 개념은 같다."""
    plan = _plan(Q11)

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert len(plan.companies) == 2
    assert plan.years == (2025,)
    assert plan.metric is None
    assert plan.derived_metric is DerivedMetric.OPERATING_MARGIN


def test_q08_and_q11_resolve_to_one_shared_dependency() -> None:
    """
    Pattern마다 대응표를 따로 두지 않는다.

    "영업이익률이 무엇에서 나오는가"는 비교 축이 기간인지 기업인지와 무관하다. Pattern별로
    복제하면 두 벌이 되고, 갈라진 뒤에는 어느 쪽이 정본인지 알 수 없다.
    """
    q08_metric = _plan(Q08).derived_metric
    q11_metric = _plan(Q11).derived_metric

    assert q08_metric is q11_metric
    assert required_source_metrics(q08_metric) == required_source_metrics(q11_metric)


# --------------------------------------------------------------------------
# 3. 판정 순서 — 파생 개념이 원천 지표보다 앞
# --------------------------------------------------------------------------


def test_derived_detection_runs_before_source_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    원천 어휘에 "영업이익"이 추가돼도 Q08이 원천 지표 질문으로 바뀌지 않아야 한다.

    "영업이익률"은 "영업이익"을 품고 있다. 원천 지표를 먼저 읽으면 영업이익률을 물은 질문이
    영업이익으로 분류되고, 그렇게 나온 답은 형식이 맞아 틀렸다는 사실이 드러나지 않는다.

    이번 범위는 "영업이익"을 원천 어휘에 넣지 않는다(F6-01이 고정한 상태를 바꾸는 별건이다).
    그래서 그 토큰이 추가된 상황을 여기서 만들어 두고, 나중에 실제로 추가하는 작업이 이 판정을
    조용히 뒤집지 못하게 막는다.
    """
    monkeypatch.setattr(
        query_analyzer,
        "REVENUE_TOKENS",
        (*query_analyzer.REVENUE_TOKENS, "영업이익"),
    )

    plan = _plan(Q08)

    assert plan.derived_metric is DerivedMetric.OPERATING_MARGIN
    assert plan.metric is None


def test_source_and_derived_together_are_not_silently_narrowed() -> None:
    """
    두 지표를 함께 물으면 한쪽을 골라 답하지 않는다.

    조용히 하나를 고르면 나머지를 물었다는 사실이 결과 어디에도 남지 않는다. C-0이
    절대연도와 상대 기간이 함께 있는 질문을 막은 것과 같은 이유다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        _plan("한미반도체 2024년이랑 2025년 매출이랑 영업이익률 어떻게 달라졌어?")

    assert error.value.reason == "unsupported_metric"
    assert "operating_margin" in error.value.detail


# --------------------------------------------------------------------------
# 4. 계획 불변식과 직렬화
# --------------------------------------------------------------------------


def test_query_plan_rejects_both_metric_channels_at_once() -> None:
    """
    두 정본이 공존할 수 있는 상태를 만들지 않는다.

    둘 다 담기면 이 계획을 읽는 쪽이 무엇을 답할지 스스로 골라야 하고, 무엇을 골랐는지는
    계획 어디에도 남지 않는다.
    """
    with pytest.raises(ValueError):
        QueryPlan(
            question="합성",
            companies=("합성",),
            years=(2025,),
            metric=METRIC_REVENUE,
            derived_metric=DerivedMetric.OPERATING_MARGIN,
        )


def test_serialized_plan_carries_the_derived_concept() -> None:
    """
    파생 개념이 진단 dict에 남아야 한다.

    표기는 metric과 같다. 둘 다 StrEnum이므로 asdict가 담는 모습이 서로 다르면 읽는 쪽이
    두 자리를 다르게 다뤄야 한다.
    """
    dumped = _plan(Q08).to_dict()

    assert dumped["derived_metric"] is DerivedMetric.OPERATING_MARGIN
    assert dumped["metric"] is None

    # 기존 표현이 그대로여야 한다.
    assert dumped["years"] == (2024, 2025)
    assert dumped["companies"] == ("한미반도체",)
    assert "year" not in dumped

    source_only = _plan(Q10).to_dict()

    assert source_only["metric"] is METRIC_REVENUE
    assert source_only["derived_metric"] is None


# --------------------------------------------------------------------------
# 5. 오염 차단 — 서술 비교와 Pattern A는 그대로
# --------------------------------------------------------------------------


def test_q09_is_not_read_as_a_derived_metric_question() -> None:
    """
    Q09는 수치를 묻지 않는다. 영업이익률로 읽히면 안 된다.

    지표가 비어 있다는 것만으로 서술 비교라고 단정하지도 않는다. 아직 어휘가 없는 원천
    지표를 물었을 수도 있기 때문이다. 그래서 여기서는 '파생 개념이 아니다'까지만 고정한다.
    """
    plan = _plan(Q09)

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.metric is None
    assert plan.derived_metric is None


def test_q10_still_reads_a_source_metric() -> None:
    """같은 Pattern D 경로의 원천 지표 질문은 그대로여야 한다."""
    plan = _plan(Q10)

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.metric is METRIC_REVENUE
    assert plan.derived_metric is None


def test_pattern_a_still_reads_a_source_metric() -> None:
    """Q01 계열은 손대지 않았다."""
    plan = _plan("한미반도체 2025년 연결 기준 매출액이 얼마야?")

    assert plan.intent == INTENT_SINGLE_FACT
    assert plan.metric is METRIC_REVENUE
    assert plan.derived_metric is None


@pytest.mark.parametrize(
    "question",
    [
        # 단일 기간 파생지표. 계산 지원은 별도 capability이며 이번 범위가 아니다.
        "한미반도체 2025년 영업이익률이 얼마야?",
        # F6-01. 원천 지표 "영업이익"은 이번 PR에서 어휘에 넣지 않는다.
        "삼성전자 2024년 연결 기준 영업이익이 얼마야?",
    ],
)
def test_single_period_metric_questions_still_stop_at_the_query_plan(
    question: str,
) -> None:
    """
    Pattern A는 파생 개념을 인식하도록 넓히지 않았다.

    넓히면 계획에는 담기지만 계산할 handler가 없어, 코퍼스를 다 읽은 뒤 추출 단계에서
    막힌다. 막히는 자리가 뒤로 밀리고 이유도 실제 이유와 달라진다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        _plan(question)

    assert error.value.reason == "unsupported_metric"


# --------------------------------------------------------------------------
# 6. 실행 — 같은 대응표를 쓰는 두 Pattern의 상태가 다르다
#
# 두 축 모두 handler가 등록됐고 같은 대응표(agent.derived_metrics)에서 영업이익률을 읽는다.
# 다만 그것을 확보하는 축이 다르다. Pattern C는 한 기업의 두 기간을, Pattern D는 두 기업의
# 한 기간을 모은다. 대응표는 하나이지만 그것을 읽어 실행하는 쪽은 Pattern마다 따로 붙는다는
# 것을 여기서 고정한다.
#
# 각 Pattern이 실제로 만드는 값과 개수는 tests/integration/test_pattern_c.py와 Pattern D의
# 전용 테스트가 맡는다. 이 파일은 대응표와 계획을 보는 자리이므로 실행 결과의 세부까지 다시
# 재지 않는다.
# --------------------------------------------------------------------------


def test_both_comparison_axes_are_routable() -> None:
    """두 축 모두 handler가 붙었다. 실행 범위가 같아진 것은 아니다."""
    assert supported_intents() == (
        "cross_entity_compare",
        "event_history",
        "single_fact",
        "temporal_compare",
    )


def test_cross_entity_derived_metric_question_invents_no_value_without_sources(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    기업 축 비교도 대응표를 원천 지표로 펼쳐 기업마다 확보한다.

    합성 코퍼스에는 영업이익 본표가 없어 확보가 끝나지 않는다. 그때 값을 하나도 만들지
    않는다는 것이 여기서 고정하는 성질이다. 하나라도 남으면 확보하지 못한 기업을 빼고
    남은 한 곳으로 답했다는 뜻이다.

    정지 단계는 고정하지 않는다. 확보가 어디에서 멈추는지는 합성 fixture가 무엇을 담고
    있느냐에 달렸고, 그 자리가 옮겨져도 '값을 만들지 않는다'는 성질은 그대로여야 한다.
    Pattern D 자체의 정지 지점은 그 Pattern의 전용 테스트가 맡는다.
    """
    result = answer_question(
        f"{SYNTHETIC_COMPANY}랑 테스트전자 중에 2025년 영업이익률이 더 높은 곳은 어디야?",
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.answerability_status is not AnswerabilityStatus.ANSWERABLE
    assert result.facts == []
    assert result.evidence == []
    assert result.calculations == []
    assert result.comparisons == []


@pytest.mark.corpus
def test_both_axes_answer_the_same_derived_metric_in_the_actual_corpus() -> None:
    """
    대표 질문 원문 그대로 실행해 두 축이 같은 대응표로 각각 답하는지 본다.

    Q08은 한 기업의 두 기간을, Q11은 두 기업의 한 기간을 견준다. 무엇을 확보하고 무엇을
    나눌지는 둘 다 agent.derived_metrics의 같은 항목에서 나온다.

    개수는 여기서 재지 않는다. Q08의 Calculation 3건과 Q11의 Calculation 2건 · Comparison
    1건은 각 Pattern의 전용 테스트가 고정한다. 이 파일이 보는 것은 '대응표 하나가 두 축
    모두를 먹인다'까지다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    temporal = answer_question(Q08, corpus_root=corpus_root)

    assert temporal.execution_status is ExecutionStatus.COMPLETED
    assert temporal.execution_issue is None
    assert temporal.calculations

    cross_entity = answer_question(Q11, corpus_root=corpus_root)

    assert cross_entity.execution_status is ExecutionStatus.COMPLETED
    assert cross_entity.execution_issue is None
    assert cross_entity.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert cross_entity.calculations
    assert cross_entity.comparisons
