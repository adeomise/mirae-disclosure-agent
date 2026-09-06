"""Pattern D 공통 실행 골격 — 입구 guard와 정규화 계약.

무엇을 고정하는가:
    1. Router가 cross_entity_compare를 Pattern D handler로 보낸다.
    2. 이번 구현이 여는 두 모양(Q10·Q11)이 입구를 통과하고 서로 구분된다.
    3. 그 밖의 cross_entity_compare 계획은 값을 만들기 전에 막힌다(fail-closed).
    4. 막힌 계획은 코퍼스를 한 번도 열지 않는다.
    5. 구조화 결과 하나가 공통 실행 Contract로 어떻게 옮겨지는가.

무엇을 검증하지 않는가:
    수치 실행 단계가 실제로 만드는 값과 개수는 보지 않는다. 그것은 Pattern D의 실행
    테스트가 맡는다. 여기서 보는 것은 '어떤 계획이 그 단계까지 갈 수 있는가'와 '거기서
    나온 결과가 Contract의 어느 자리로 가는가'까지다.

    그래서 정지 단계가 옮겨져도 이 파일은 흔들리지 않아야 한다. 실행이 열리는 모양에
    대해서는 '입구를 통과한다'까지만 보고 그 뒤의 결과를 재지 않는다.

    실제 공시 수치·접수번호는 쓰지 않는다. 모든 값은 합성이다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent import router
from disclosure_agent.agent.answer_builder import (
    REASON_INCOMPARABLE_ENTITIES,
    REASON_NO_MATCHING_FILING,
    REASON_UNSUPPORTED_COMPARISON_METRIC,
    REASON_UNSUPPORTED_COMPARISON_PERIOD,
    REASON_UNSUPPORTED_COMPARISON_SUBJECT,
    STAGE_COMPARABILITY,
    STAGE_FILING_DISCOVERY,
    STAGE_PATTERN_ENTRY,
    BlockedInfo,
)
from disclosure_agent.agent.derived_metrics import DerivedMetric
from disclosure_agent.agent.handlers import cross_entity_compare
from disclosure_agent.agent.handlers.cross_entity_compare import (
    CrossEntityCompareResult,
    CrossEntityShape,
    _unsupported_plan,
    build_cross_entity_compare_result,
    handle_cross_entity_compare,
    resolve_shape,
    source_metrics_for,
)
from disclosure_agent.agent.normalization import (
    ANSWERABILITY_BY_STOP,
    normalize_cross_entity_compare_result,
)
from disclosure_agent.agent.query_analyzer import (
    EVENT_SUBTYPE_FACILITY_INVESTMENT,
    INTENT_CROSS_ENTITY_COMPARE,
    OPERATION_AGGREGATE,
    OPERATION_COMPARE,
    PREVIOUS_YEAR_OFFSET,
    QueryPlan,
    RelativePeriod,
    build_query_plan,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ChainPolicy,
    ComparisonRelation,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import (
    CalculationResult,
    ComparisonResult,
    Evidence,
    Fact,
    ResultInputReference,
)

# 합성 기업. manifest를 보지 않는 계획 단위 테스트이므로 실재하지 않아도 된다.
LEFT_COMPANY = "테스트반도체(주)"

RIGHT_COMPANY = "테스트전자"

FISCAL_YEAR = 2025

# 코퍼스를 열지 않았다는 것을 확인하려고 일부러 존재하지 않는 경로를 준다.
ABSENT_CORPUS_ROOT = "존재하지_않는_코퍼스"


def _plan(**overrides) -> QueryPlan:
    """기업 간 비교 계획 하나. 기본값이 Q10 모양이다."""
    fields = {
        "question": "합성 질문",
        "companies": (LEFT_COMPANY, RIGHT_COMPANY),
        "years": (FISCAL_YEAR,),
        "intent": INTENT_CROSS_ENTITY_COMPARE,
        "metric": MetricConcept.REVENUE,
    }

    fields.update(overrides)

    return QueryPlan(**fields)


def _q10_plan(**overrides) -> QueryPlan:
    return _plan(**overrides)


def _q11_plan(**overrides) -> QueryPlan:
    return _plan(metric=None, derived_metric=DerivedMetric.OPERATING_MARGIN, **overrides)


# --------------------------------------------------------------------------
# 1. Router 등록
#
# 등록되지 않으면 아래 판정은 전부 도달하지 않는다. 먼저 확인한다.
# --------------------------------------------------------------------------


def test_router_sends_cross_entity_compare_to_pattern_d() -> None:
    assert router.resolve_handler(INTENT_CROSS_ENTITY_COMPARE) is handle_cross_entity_compare


def test_cross_entity_compare_is_listed_as_executable() -> None:
    assert INTENT_CROSS_ENTITY_COMPARE in router.supported_intents()


# --------------------------------------------------------------------------
# 2. 이번 구현이 여는 두 모양
#
# '통과한다'까지만 본다. 그 뒤에 무엇이 만들어지는지는 여기서 재지 않는다. 재면 수치 실행
# 단계가 열릴 때마다 이 파일이 함께 깨지고, 그때 입구 판정까지 같이 지워질 수 있다.
# --------------------------------------------------------------------------


def test_q10_shape_passes_the_entry_guard() -> None:
    """기업 2곳 · 연도 1개 · 원천 지표(매출)."""
    plan = _q10_plan()

    assert _unsupported_plan(plan) is None
    assert resolve_shape(plan) is CrossEntityShape.SOURCE_METRIC


def test_q11_shape_passes_the_entry_guard() -> None:
    """기업 2곳 · 연도 1개 · 파생 개념(영업이익률)."""
    plan = _q11_plan()

    assert _unsupported_plan(plan) is None
    assert resolve_shape(plan) is CrossEntityShape.DERIVED_METRIC


def test_the_two_shapes_are_told_apart() -> None:
    """
    한쪽이 다른 쪽으로 대신 처리되지 않으려면 먼저 구분돼야 한다.

    Q10을 파생지표 경로로 흘려보내면 매출을 물은 질문에 영업이익률이 돌아가고, 형식은
    맞으므로 틀렸다는 사실이 결과에 드러나지 않는다.
    """
    assert resolve_shape(_q10_plan()) is not resolve_shape(_q11_plan())


def test_each_shape_declares_what_must_be_retrieved() -> None:
    """
    확보할 원천 지표는 계획과 대응표에서 나온다. 여기서 새로 정하지 않는다.

    Q10은 물은 지표 그대로 1건이고, Q11은 대응표가 펼친 분자·분모 2건이다.
    """
    assert source_metrics_for(_q10_plan()) == (MetricConcept.REVENUE,)

    assert source_metrics_for(_q11_plan()) == (
        MetricConcept.OPERATING_INCOME,
        MetricConcept.REVENUE,
    )


def test_source_metrics_are_not_guessed_for_an_unsupported_plan() -> None:
    """
    입구를 통과하지 않은 계획에서는 확보 목록을 만들지 않는다.

    기본값을 돌려주면 지표를 읽지 못한 질문이 매출 조회로 흘러간다.
    """
    with pytest.raises(ValueError):
        source_metrics_for(_plan(metric=None))


# --------------------------------------------------------------------------
# 3. 그 밖의 계획은 값을 만들기 전에 막힌다
#
# 사유를 축마다 나누는 이유는 후속 작업이 서로 다르기 때문이다. 결과만 보고 무엇을
# 구현하면 풀리는 질문인지 알 수 있어야 한다.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("companies", "label"),
    [
        ((LEFT_COMPANY,), "한 곳"),
        ((LEFT_COMPANY, RIGHT_COMPANY, "테스트화학"), "세 곳"),
    ],
)
def test_company_count_other_than_two_is_blocked(
    companies: tuple[str, ...],
    label: str,
) -> None:
    """기업이 정확히 둘이 아니면 견줄 축이 성립하지 않는다."""
    blocked = _unsupported_plan(_plan(companies=companies))

    assert blocked is not None
    assert blocked.stage == STAGE_PATTERN_ENTRY
    assert blocked.reason == REASON_UNSUPPORTED_COMPARISON_SUBJECT


def test_more_than_one_year_is_blocked() -> None:
    """
    기업 축과 기간 축이 함께 여럿인 질문이 여기로 온다.

    한 연도를 골라 답하면 두 기간을 물은 질문에 한 기간만 답하게 된다.
    """
    blocked = _unsupported_plan(_plan(years=(2024, 2025)))

    assert blocked is not None
    assert blocked.stage == STAGE_PATTERN_ENTRY
    assert blocked.reason == REASON_UNSUPPORTED_COMPARISON_PERIOD


def test_unsupported_source_metric_is_blocked() -> None:
    """
    허용 목록에 없는 원천 지표는 통과하지 않는다.

    막을 것을 세지 않고 열 것을 세는 이유는, 어휘가 넓어질 때 새로 들어온 지표가 조용히
    통과하지 않게 하기 위해서다.
    """
    blocked = _unsupported_plan(_plan(metric=MetricConcept.OPERATING_INCOME))

    assert blocked is not None
    assert blocked.stage == STAGE_PATTERN_ENTRY
    assert blocked.reason == REASON_UNSUPPORTED_COMPARISON_METRIC


def test_plan_without_any_metric_is_blocked() -> None:
    """지표를 읽지 못한 계획에 기본값을 채우지 않는다."""
    blocked = _unsupported_plan(_plan(metric=None))

    assert blocked is not None
    assert blocked.reason == REASON_UNSUPPORTED_COMPARISON_METRIC


def test_subject_is_judged_before_the_metric() -> None:
    """
    좁히는 순서를 고정한다. 누구를 -> 언제 -> 무엇을.

    기업이 셋인데 지표까지 범위 밖이면 사유는 기업이어야 한다. 순서가 뒤집히면 같은
    질문이 실행마다 다른 사유로 막힐 수 있고, 그러면 사유로 후속 작업을 고를 수 없다.
    """
    blocked = _unsupported_plan(
        _plan(companies=(LEFT_COMPANY, RIGHT_COMPANY, "테스트화학"), metric=None)
    )

    assert blocked is not None
    assert blocked.reason == REASON_UNSUPPORTED_COMPARISON_SUBJECT


# --------------------------------------------------------------------------
# 4. Q12는 명시적인 event aggregate 모양에서만 열린다
#
# 기업 2곳·연도 1개만으로는 부족하다. 시설투자 subtype, investment_amount,
# aggregate+compare, full_chain이 모두 있을 때만 해소되고, 하나라도 비면 입구에서 막힌다.
#
# 질문 문자열부터 실행 결과까지는 tests/integration/test_pattern_d_q12_guard.py가 맡는다.
# --------------------------------------------------------------------------

Q12 = (
    "2024년에 LG이노텍하고 레인보우로보틱스가 새로 발표한 시설투자, "
    "회사별로 다 합치면 어디가 더 커?"
)

Q12_COMPANIES = ("LG이노텍", "레인보우로보틱스")


@pytest.fixture
def q12_plan() -> QueryPlan:
    return build_query_plan(Q12, known_companies=Q12_COMPANIES)


def test_q12_reaches_pattern_d_with_the_same_subject_and_period_shape(
    q12_plan: QueryPlan,
) -> None:
    """
    Q12가 기업 2곳·root 선택 연도 1개를 잃지 않고 Pattern D에 도달한다.
    """
    assert q12_plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert len(q12_plan.companies) == cross_entity_compare.COMPARED_COMPANY_COUNT
    assert len(q12_plan.years) == cross_entity_compare.COMPARED_PERIOD_COUNT


def test_q12_shape_is_explicitly_resolvable(q12_plan: QueryPlan) -> None:
    """시설투자·집계·비교·최신 chain 신호가 모두 계획에 남는다."""
    assert q12_plan.metric is MetricConcept.INVESTMENT_AMOUNT
    assert q12_plan.derived_metric is None
    assert q12_plan.document_class == "event"
    assert q12_plan.document_subtype == EVENT_SUBTYPE_FACILITY_INVESTMENT
    assert q12_plan.operations == (OPERATION_AGGREGATE, OPERATION_COMPARE)
    assert q12_plan.chain_policy is ChainPolicy.FULL_CHAIN
    assert resolve_shape(q12_plan) is CrossEntityShape.FACILITY_EVENT_AGGREGATE


def test_q12_passes_pattern_entry(q12_plan: QueryPlan) -> None:
    assert _unsupported_plan(q12_plan) is None


@pytest.mark.parametrize(
    "question",
    [
        "2024년에 LG이노텍하고 레인보우로보틱스 경영 현황은 어디가 더 커?",
        "2024년에 LG이노텍하고 레인보우로보틱스 회사별로 다 합치면 어디가 더 커?",
        (
            "2024년에 LG이노텍하고 레인보우로보틱스 시설투자와 매출을 "
            "회사별로 다 합치면 어디가 더 커?"
        ),
    ],
)
def test_q12_entry_does_not_open_for_missing_or_mixed_semantics(question: str) -> None:
    plan = build_query_plan(question, known_companies=Q12_COMPANIES)

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert resolve_shape(plan) is None
    assert _unsupported_plan(plan).reason == REASON_UNSUPPORTED_COMPARISON_METRIC


# --------------------------------------------------------------------------
# 4b. 같은 시설투자 집계 질문을 다르게 말해도 같은 계획이 된다
#
# 사용자가 이 공시를 부르는 이름은 '시설투자' 하나가 아니고('설비투자'), 더해 달라고 말하는
# 방식도 '다 합치면' 하나가 아니다('규모가 더 큰'). 표기가 다르다는 이유로 계획이 갈리면
# 같은 질문이 낱말에 따라 답과 차단으로 나뉘고, 차단 사유(지표를 읽지 못했다)는 실제로
# 일어난 일과 다르다.
#
# 그래서 정본 표기 하나만 통과하는지가 아니라, 아래 표기가 전부 '같은 계획'이 되는지를
# 고정한다. 계획을 통째로 견주는 이유는 필드를 골라 견주면 고르지 않은 필드가 갈려도
# 드러나지 않기 때문이다. 질문 문자열은 계획마다 다르므로 그 자리만 뺀다.
# --------------------------------------------------------------------------

# 이 공시 집계를 묻는 같은 질문의 여러 표기.
#
#   1. 정본 Q12          '시설투자' + '다 합치면' + '더 커'
#   2. 규모 표현         '시설투자' + '규모' + '더 큰'
#   3. 설비투자 표현     '설비투자' + '규모' + '더 큰'
#   4. 띄어 쓴 설비투자  '설비 투자' + '규모' + '더 큰'
#   5. 공시명 + 합계     '신규시설투자등' + '합계' + '비교'
FACILITY_AGGREGATE_PHRASINGS = (
    Q12,
    "LG이노텍과 레인보우로보틱스 중 2024년 시설투자 규모가 더 큰 기업은 어디야?",
    "LG이노텍과 레인보우로보틱스 중 2024년 설비투자 규모가 더 큰 기업은 어디야?",
    "LG이노텍과 레인보우로보틱스 중 2024년 설비 투자 규모가 더 큰 기업은 어디야?",
    (
        "LG이노텍과 레인보우로보틱스의 2024년 신규시설투자등 공시 "
        "투자금액 합계를 비교해줘."
    ),
)


@pytest.mark.parametrize("question", FACILITY_AGGREGATE_PHRASINGS)
def test_facility_aggregate_phrasings_build_the_same_plan(question: str) -> None:
    """표기가 달라도 정본 Q12와 같은 계획이 나오고 같은 모양으로 해소된다."""
    plan = build_query_plan(question, known_companies=Q12_COMPANIES)
    canonical = build_query_plan(Q12, known_companies=Q12_COMPANIES)

    # 질문 문자열은 표기마다 다르다. 그 자리만 빼고 나머지를 통째로 견준다.
    assert {k: v for k, v in plan.__dict__.items() if k != "question"} == {
        k: v for k, v in canonical.__dict__.items() if k != "question"
    }

    assert resolve_shape(plan) is CrossEntityShape.FACILITY_EVENT_AGGREGATE
    assert _unsupported_plan(plan) is None


def test_facility_aggregate_resolves_the_listed_name_without_a_new_alias() -> None:
    """'하이닉스'는 manifest가 준 목록만으로 SK하이닉스가 된다.

    이 경로를 열면서 별칭을 새로 적지 않았다는 것을 고정한다. 여기서 별칭을 적으면
    해석기와 두 벌이 되고, 한쪽만 늘어난 날 같은 질문이 경로에 따라 갈린다.
    """
    plan = build_query_plan(
        "삼성전자와 하이닉스 중 2025년 설비투자 규모가 더 큰 기업은 어디야?",
        known_companies=("삼성전자", "SK하이닉스"),
    )

    assert plan.companies == ("삼성전자", "SK하이닉스")
    assert plan.document_subtype == EVENT_SUBTYPE_FACILITY_INVESTMENT
    assert resolve_shape(plan) is CrossEntityShape.FACILITY_EVENT_AGGREGATE


# 이 경로가 받지 않는 지표 표현.
#
# 이 경로가 세는 것은 신규시설투자등 공시의 투자금액 합계 하나다. 아래 표현은 전부 그것보다
# 넓은 개념(전체 CAPEX, 현금흐름표의 유형자산 취득, 사업보고서의 설비투자액)이며, 값의
# 출처도 크기도 다르다. 시설투자로 옮겨 답하면 형식이 맞는 답이 나가므로 물어본 것과 다른
# 값을 받았다는 사실이 드러나지 않는다.
#
# '설비투자액'을 여기 두는 이유:
#     '설비투자'를 이 공시 유형으로 읽게 했으므로 유형 자체는 맞다. 그러나 이 질문은
#     사업보고서를 지목했고 집계를 요청하지 않았다. 유형 표기 하나로 집계 경로가 열리지
#     않는다는 것을 이 자리에서 함께 고정한다.
UNSUPPORTED_INVESTMENT_METRIC_QUESTIONS = (
    "LG이노텍과 레인보우로보틱스의 2024년 CAPEX를 비교해줘.",
    "LG이노텍과 레인보우로보틱스의 2024년 현금흐름표상 유형자산 취득액을 비교해줘.",
    "LG이노텍과 레인보우로보틱스의 2024년 사업보고서 설비투자액을 비교해줘.",
)


@pytest.mark.parametrize("question", UNSUPPORTED_INVESTMENT_METRIC_QUESTIONS)
def test_broader_investment_metrics_do_not_reach_the_facility_aggregate(
    question: str,
) -> None:
    """전체 CAPEX 계열은 이 경로로 새지 않고 입구에서 막힌 채로 남는다."""
    plan = build_query_plan(question, known_companies=Q12_COMPANIES)

    assert resolve_shape(plan) is None
    assert _unsupported_plan(plan).reason == REASON_UNSUPPORTED_COMPARISON_METRIC


@pytest.mark.parametrize(
    "overrides",
    [
        {"operations": ()},
        {"chain_policy": None},
        {"document_subtype": "단일판매공급계약체결"},
        {"metric": MetricConcept.REVENUE},
    ],
)
def test_q12_like_plan_missing_one_exact_signal_is_blocked(
    q12_plan: QueryPlan, overrides: dict
) -> None:
    fields = {
        **q12_plan.__dict__,
        **overrides,
    }
    plan = QueryPlan(**fields)

    assert resolve_shape(plan) is None
    assert _unsupported_plan(plan).reason == REASON_UNSUPPORTED_COMPARISON_METRIC


# --------------------------------------------------------------------------
# 5. 막힌 계획은 코퍼스를 열지 않는다
#
# "만들었다가 마지막에 지웠다"와 "한 번도 만들지 않았다"는 다르다. 확보 계층을 폭발하는
# 함수로 바꿔 두고, 막히는 계획이 그 함수에 닿지 않는다는 것을 확인한다.
# --------------------------------------------------------------------------


@pytest.fixture
def exploding_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**kwargs):
        raise AssertionError("막힌 계획이 코퍼스를 열었습니다.")

    monkeypatch.setattr(cross_entity_compare, "collect_period_facts", _boom)


@pytest.mark.parametrize(
    ("plan_factory", "expected_reason"),
    [
        (lambda: _plan(companies=(LEFT_COMPANY,)), REASON_UNSUPPORTED_COMPARISON_SUBJECT),
        (lambda: _plan(years=(2024, 2025)), REASON_UNSUPPORTED_COMPARISON_PERIOD),
        (lambda: _plan(metric=None), REASON_UNSUPPORTED_COMPARISON_METRIC),
        (
            lambda: _plan(metric=MetricConcept.OPERATING_INCOME),
            REASON_UNSUPPORTED_COMPARISON_METRIC,
        ),
    ],
)
def test_unsupported_shape_never_reaches_retrieval(
    plan_factory,
    expected_reason: str,
    exploding_retrieval: None,
) -> None:
    result = build_cross_entity_compare_result(
        plan_factory(),
        corpus_root=ABSENT_CORPUS_ROOT,
    )

    assert result.blocked is not None
    assert result.blocked.reason == expected_reason
    assert result.facts == ()
    assert result.evidence == ()


# --------------------------------------------------------------------------
# 6. 구조화 결과를 공통 Contract로 옮긴다
#
# 확보와 계산을 거치지 않고 결과를 직접 만들어 시험한다. 정규화는 문서를 읽지 않는 순수
# 함수이므로 코퍼스 없이 모든 갈래를 볼 수 있다.
# --------------------------------------------------------------------------


def _fact(company: str) -> Fact:
    return Fact(
        fact_id=f"fact_{company}",
        company=company,
        metric=MetricConcept.REVENUE,
        fiscal_year=FISCAL_YEAR,
        accounting_scope=FactAccountingScope.CONSOLIDATED,
        raw_value="1000000",
        normalized_value="1000000",
        normalized_unit="KRW",
        evidence_ids=[f"evidence_{company}"],
    )


def _evidence(company: str) -> Evidence:
    return Evidence(
        evidence_id=f"evidence_{company}",
        doc_id=f"periodic_{company}",
        location_type=EvidenceLocationType.TABLE,
        table_name="손익계산서",
        row_label="매출액",
    )


def _calculation(company: str) -> CalculationResult:
    return CalculationResult(
        calculation_id=f"calculation_{company}",
        operation="ratio",
        inputs=[
            ResultInputReference(
                input_type=ResultInputType.FACT, input_id=f"fact_{company}"
            )
        ],
        value="33.3333",
        unit="percent",
    )


def _comparison() -> ComparisonResult:
    return ComparisonResult(
        comparison_id="comparison_synthetic",
        left=ResultInputReference(
            input_type=ResultInputType.CALCULATION,
            input_id=f"calculation_{LEFT_COMPANY}",
        ),
        right=ResultInputReference(
            input_type=ResultInputType.CALCULATION,
            input_id=f"calculation_{RIGHT_COMPANY}",
        ),
        relation=ComparisonRelation.LEFT_GREATER,
    )


def _completed_result(**overrides) -> CrossEntityCompareResult:
    fields = {
        "plan": _q11_plan(warnings=("합성 경고",)),
        "facts": (_fact(LEFT_COMPANY), _fact(RIGHT_COMPANY)),
        "evidence": (_evidence(LEFT_COMPANY), _evidence(RIGHT_COMPANY)),
        "calculations": (_calculation(LEFT_COMPANY), _calculation(RIGHT_COMPANY)),
        "comparisons": (_comparison(),),
    }

    fields.update(overrides)

    return CrossEntityCompareResult(**fields)


def test_successful_result_is_completed_and_answerable() -> None:
    """
    성공 경로는 COMPLETED + ANSWERABLE이며 사유를 싣지 않는다.

    ANSWERABLE은 사유를 갖지 않는다는 규칙을 answer_builder가 이미 고정하고 있다.
    """
    result = normalize_cross_entity_compare_result(_completed_result())

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None
    assert result.execution_issue is None


def test_successful_result_carries_the_comparison_verdict() -> None:
    """
    Pattern D는 ComparisonResult를 싣는 첫 Pattern이다.

    우열 판정이 결과에 실리지 않으면 '어디가 더 큰가'를 물은 질문에 답이 없다.
    """
    result = normalize_cross_entity_compare_result(_completed_result())

    assert len(result.facts) == 2
    assert len(result.evidence) == 2
    assert len(result.calculations) == 2
    assert len(result.comparisons) == 1


def test_plan_warnings_survive_normalization() -> None:
    """계획 단계의 진단이 실행 결과에서 사라지지 않는다."""
    result = normalize_cross_entity_compare_result(_completed_result())

    assert "합성 경고" in result.warnings


def test_pattern_entry_stop_stays_blocked_without_an_answerability_verdict() -> None:
    """
    코퍼스를 열기 전에 막힌 결과는 답변 가능성을 판정하지 않는다.

    UNANSWERABLE로 채우면 '코퍼스에 답이 없다'는 확정 진술이 되어 사실과 달라진다.
    """
    result = normalize_cross_entity_compare_result(
        CrossEntityCompareResult(
            plan=_q10_plan(warnings=("합성 경고",)),
            blocked=BlockedInfo(
                stage=STAGE_PATTERN_ENTRY,
                reason=REASON_UNSUPPORTED_COMPARISON_METRIC,
                detail="합성 사유",
            ),
        )
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.answerability_reason is None
    assert result.execution_issue is not None
    assert result.execution_issue.stage == STAGE_PATTERN_ENTRY
    assert result.warnings == ["합성 경고"]


def test_retrieval_stop_is_normalized_by_the_shared_table() -> None:
    """
    확보 단계의 정지 사유는 Pattern마다 다시 해석하지 않는다.

    manifest 전체와 대조한 결과이므로 실행은 끝났고 답이 없을 뿐이다.
    """
    result = normalize_cross_entity_compare_result(
        CrossEntityCompareResult(
            plan=_q10_plan(),
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=REASON_NO_MATCHING_FILING,
                detail="합성 사유",
            ),
        )
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_FOUND
    assert result.execution_issue is None


def test_comparability_failure_is_completed_and_not_comparable() -> None:
    """
    비교 가능성 판정은 값을 다 확보한 뒤에 내린다. 실행은 끝났다.

    확보한 값으로는 답할 수 없다는 판정이므로 COMPLETED + UNANSWERABLE이며, 어느 축이
    어긋났는지는 answerability_detail의 원문이 구분한다.
    """
    result = normalize_cross_entity_compare_result(
        _completed_result(
            blocked=BlockedInfo(
                stage=STAGE_COMPARABILITY,
                reason=REASON_INCOMPARABLE_ENTITIES,
                detail="합성 사유",
            )
        )
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_COMPARABLE
    assert result.answerability_detail is not None
    assert result.answerability_detail.reason == REASON_INCOMPARABLE_ENTITIES


def test_comparability_failure_keeps_the_evidence_and_drops_the_verdict() -> None:
    """
    무엇이 견줄 수 없다고 판정됐는지 보려면 그 값이 남아야 한다.

    반대로 기업별 파생값과 우열 판정은 남기지 않는다. 남아 있으면 받는 쪽이 guard가 막은
    비교를 그대로 이어서 할 수 있고, 그러면 guard가 막은 일이 결과를 받는 쪽에서 일어난다.
    """
    result = normalize_cross_entity_compare_result(
        _completed_result(
            blocked=BlockedInfo(
                stage=STAGE_COMPARABILITY,
                reason=REASON_INCOMPARABLE_ENTITIES,
                detail="합성 사유",
            )
        )
    )

    assert len(result.facts) == 2
    assert len(result.evidence) == 2
    assert result.calculations == []
    assert result.comparisons == []


def test_entity_comparability_reason_is_registered_in_the_table() -> None:
    """
    새 비교 가능성 사유는 대응표에 함께 등록돼야 한다.

    등록하지 않으면 값을 다 확보하고 내린 판정이 BLOCKED로 나가 '판정에 이르지 못하고
    멈췄다'가 되고, 실제로 일어난 일과 달라진다.
    """
    outcome = ANSWERABILITY_BY_STOP[(STAGE_COMPARABILITY, REASON_INCOMPARABLE_ENTITIES)]

    assert outcome.status is AnswerabilityStatus.UNANSWERABLE
    assert outcome.reason is AnswerabilityReason.NOT_COMPARABLE


# --------------------------------------------------------------------------
# 9. 해석하지 않은 상대 기간은 계획이 답한다
#
# 이 절이 지키는 것은 판정의 출처다. 예전 구현은 plan.question에 "전년"이 들어 있는지를
# 부분문자열로 봤다. 그 방식은 두 방향으로 틀린다. 계획에 기록이 없으면 막지 못하고,
# 해석에 성공한 계획도 글자가 남아 있다는 이유로 막는다.
# --------------------------------------------------------------------------


def test_unresolved_relative_period_is_blocked_at_the_entry() -> None:
    """
    절대연도 하나에 해석하지 않은 상대 기간이 붙은 계획은 값을 만들지 않는다.

    개수 검사만으로는 걸리지 않는다. years에는 절대연도 하나만 남아 있기 때문이다. 통과시키면
    "2025년 매출이 전년보다 어때"가 2025년만 견준 답으로 돌아가고, 사라진 기간이 결과 어디에도
    남지 않는다.
    """
    plan = _plan(
        relative_period=RelativePeriod(
            expression="전년",
            offset=PREVIOUS_YEAR_OFFSET,
        )
    )

    assert plan.has_unresolved_relative_period

    blocked = _unsupported_plan(plan)

    assert blocked is not None
    assert blocked.stage == STAGE_PATTERN_ENTRY
    assert blocked.reason == REASON_UNSUPPORTED_COMPARISON_PERIOD


def test_the_verdict_comes_from_the_plan_not_from_the_question_text() -> None:
    """
    질문 문자열에 상대 기간 어휘가 남아 있어도, 계획이 해석을 마쳤으면 막지 않는다.

    부분문자열 판정으로 되돌아가면 여기가 깨진다. "작년"은 옮긴 뒤에도 질문에 그대로 남아
    있으므로, 글자로 판정하면 옮기기 전과 옮긴 뒤를 가릴 수 없다. 그때는 답할 수 있는 질문을
    막는 쪽으로 틀린다.

    이 계획은 기준 연도가 정해져 있어 해석이 끝난 상태다. 기간 축이 여럿이라는 이유로 막히는
    것은 years가 답할 몫이며, 여기서는 그 축을 건드리지 않는다.
    """
    plan = _plan(
        question=f"{LEFT_COMPANY}랑 {RIGHT_COMPANY} 작년 매출 비교해줘",
        relative_period=RelativePeriod(
            expression="작년",
            offset=PREVIOUS_YEAR_OFFSET,
            anchor_year=FISCAL_YEAR,
            anchor_source="latest_annual_filing",
        ),
    )

    assert "작년" in plan.question
    assert not plan.has_unresolved_relative_period
    assert _unsupported_plan(plan) is None


def test_the_analyzer_records_the_relative_period_on_a_cross_entity_plan() -> None:
    """
    막는 쪽이 읽는 값을 실제로 채우는 자리가 있어야 한다.

    query_analyzer는 다중 기업 판정을 기간 판정보다 먼저 한다. 그래서 이 기록을
    _build_cross_entity_compare_plan이 직접 남기지 않으면, 위 guard가 읽을 값이 늘 비어 있고
    검사는 조용히 통과한다. guard와 그 값을 채우는 자리를 함께 고정한다.
    """
    plan = build_query_plan(
        "HD현대중공업이랑 삼성중공업 2025년 매출이 전년보다 어때?",
        known_companies=("HD현대중공업", "삼성중공업"),
    )

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.years == (2025,)
    assert plan.relative_period is not None
    assert plan.relative_period.expression == "전년"
    assert not plan.relative_period.resolved
    assert plan.has_unresolved_relative_period


def test_a_plain_cross_entity_plan_records_no_relative_period() -> None:
    """
    상대 기간 어휘가 없는 질문에는 그 기록이 붙지 않는다.

    Q10과 Q11이 이 모양이다. 여기에 값이 붙으면 두 대표 질문이 전부 입구에서 막힌다.
    """
    for question in (
        "HD현대중공업과 삼성중공업의 2025년 매출액을 비교해줘.",
        "HD현대중공업과 삼성중공업 중 2025년 영업이익률이 더 높은 곳은 어디야?",
    ):
        plan = build_query_plan(
            question,
            known_companies=("HD현대중공업", "삼성중공업"),
        )

        assert plan.relative_period is None
        assert not plan.has_unresolved_relative_period
        assert _unsupported_plan(plan) is None
    ChainPolicy,
