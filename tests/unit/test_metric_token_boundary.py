"""지표 어휘를 낱말 경계까지 맞춰 읽는지 검증한다.

무엇을 막는가:
    질의 해석이 지표를 부분문자열 포함으로만 읽던 동안, "매출원가"·"매출채권"·"매출총이익"·
    "금융수익"처럼 이번 범위가 답할 수 없는 개념이 전부 metric=revenue로 분류됐다. 그 계획은
    막히지 않고 실행돼 진짜 매출 Fact를 Evidence까지 갖춘 채 COMPLETED/ANSWERABLE로 돌려준다.

    형식이 맞으므로 받는 쪽에서는 틀렸다는 사실이 드러나지 않는다. 물은 것은 매출원가인데
    답은 매출이고, 바뀌었다는 기록이 warnings에도 conversion_gaps에도 남지 않는다.
    지원하지 않는다고 답하는 것보다 이쪽이 위험하다.

어디를 고치는가:
    추출 계층(periodic.extractor)은 라벨을 METRIC_ALIASES와 정확히 비교하므로 이미 안전하다.
    뚫려 있던 자리는 query_analyzer._find_metric 한 곳이며, 이 파일은 그 한 곳의 경계를
    고정한다.

경계 규약(_metric_token_pattern):
    왼쪽    앞 글자가 한글 음절이면 지표로 읽지 않는다. "기타영업수익"·"제품매출액".
    오른쪽  뒤에 한글 음절이 이어지면 읽지 않는다. 다만 METRIC_TOKEN_SUFFIXES의 조사·어미
            하나가 통째로 이어지고 그 뒤가 다시 경계이면 같은 낱말로 본다. "매출액이"는 읽고
            "매출채권"은 읽지 않는다.

    모르는 어형은 열지 않는다. "매출얼마야"처럼 조사를 생략하고 붙여 쓴 표기도 읽지 않으며,
    그것을 읽으려고 경계를 늘리면 위의 개념들이 같은 문으로 함께 들어온다.

무엇을 검증하지 않는가:
    지원하지 않는 개념을 어떻게 답할지는 여기서 정하지 않는다. 기존 계약(query_plan 단계의
    unsupported_metric, 비교 질문에서는 metric=None) 그대로 막히는 것까지만 고정한다.
    새 사유나 새 스키마를 만들지 않는다.

    BUSINESS_PROFILE_EXCLUDED_TOKENS의 부분문자열 판정도 건드리지 않는다. 그쪽은 '값을 함께
    물었는가'를 넓게 잡아 Q05 요약 경로를 닫는 판정이며, 넓게 잡는 것이 그 자리의 fail-closed다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent import query_analyzer
from disclosure_agent.agent.query_analyzer import (
    INTENT_CROSS_ENTITY_COMPARE,
    INTENT_SINGLE_FACT,
    INTENT_TEMPORAL_COMPARE,
    METRIC_REVENUE,
    UnsupportedQuestionError,
    build_query_plan,
)

KNOWN_COMPANIES = ("삼성전자", "한미반도체", "SK하이닉스")

# 감사에서 확인된 오분류 개념. 어느 것도 이번 범위의 원천 지표가 아니다.
#
# 앞의 여섯은 "매출"을, 뒤의 아홉은 "수익"·"영업수익"을 연속 문자열로 품고 있다. 부분문자열
# 판정에서는 전부 revenue로 읽혔다.
UNSUPPORTED_COMPOUND_CONCEPTS = (
    "매출원가",
    "매출채권",
    "매출총이익",
    "매출원가율",
    "매출채권회전율",
    "매출채권및기타채권",
    "제품매출액",
    "순매출액",
    "금융수익",
    "이자수익",
    "배당금수익",
    "기타수익",
    "수익성",
    "수익률",
    "수익증권",
    "기타영업수익",
    "보험영업수익",
)


def _plan(question: str) -> query_analyzer.QueryPlan:
    return build_query_plan(question, known_companies=KNOWN_COMPANIES)


# --------------------------------------------------------------------------
# 1. 지원하지 않는 복합 개념 — 단일 사실 조회
# --------------------------------------------------------------------------


@pytest.mark.parametrize("concept", UNSUPPORTED_COMPOUND_CONCEPTS)
def test_unsupported_compound_concepts_are_not_read_as_revenue(concept: str) -> None:
    """어휘를 품고만 있는 개념은 지표로 읽지 않는다."""
    assert query_analyzer._find_metric(concept) is None


@pytest.mark.parametrize("concept", UNSUPPORTED_COMPOUND_CONCEPTS)
def test_unsupported_compound_questions_block_with_unsupported_metric(
    concept: str,
) -> None:
    """
    기존 계약 그대로 막힌다.

    새 사유를 만들지 않는다. 지표를 읽지 못한 단일 사실 질문이 이미 가던 자리
    (query_plan 단계의 unsupported_metric)로 그대로 간다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        _plan(f"삼성전자 2024년 {concept}가 얼마야?")

    assert error.value.reason == "unsupported_metric"


def test_confirmed_bad_question_no_longer_returns_a_revenue_plan() -> None:
    """
    감사에서 확인된 대표 질문.

    이전에는 metric=revenue인 single_fact 계획이 만들어졌고, 그 계획은 실제로 삼성전자
    2024년 매출 Fact를 근거까지 갖춘 채 돌려줬다. 물은 것은 매출원가다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        _plan("삼성전자 2024년 매출원가가 얼마야?")

    assert error.value.reason == "unsupported_metric"


# --------------------------------------------------------------------------
# 2. 경계의 좌우 — 어느 쪽으로 늘어나도 다른 낱말이다
# --------------------------------------------------------------------------


def test_left_extension_is_not_revenue() -> None:
    """
    앞에 음절이 붙으면 다른 개념이다.

    "기타영업수익"은 "영업수익"을 오른쪽 끝에 그대로 품고 있다. 오른쪽 경계만 보면 통과한다.
    """
    assert query_analyzer._find_metric("기타영업수익") is None
    assert query_analyzer._find_metric("영업수익") == METRIC_REVENUE


def test_right_extension_is_not_revenue() -> None:
    """
    뒤에 음절이 붙어도 다른 개념이다.

    "매출채권"은 "매출"을 왼쪽 끝에 그대로 품고 있다. 왼쪽 경계만 보면 통과한다.
    """
    assert query_analyzer._find_metric("매출채권") is None
    assert query_analyzer._find_metric("매출") == METRIC_REVENUE


def test_particle_free_shorthand_stays_closed() -> None:
    """
    모르는 어형은 열지 않는다.

    "매출얼마야"를 읽으려면 오른쪽 경계를 조사 목록 밖으로 넓혀야 하고, 넓히면 "매출원가"가
    같은 문으로 들어온다. 답할 수 있는 표기 하나를 놓치는 쪽이 먼저다.
    """
    assert query_analyzer._find_metric("매출얼마야") is None


# --------------------------------------------------------------------------
# 3. 의도한 어휘와 조사 — 답하던 질문은 그대로 답한다
# --------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["매출", "매출액", "영업수익", "수익"])
def test_intended_tokens_are_still_read(token: str) -> None:
    """이번 PR은 어휘를 줄이지 않는다. 맨 "수익"의 존치 여부는 별건이다."""
    assert query_analyzer._find_metric(token) == METRIC_REVENUE


@pytest.mark.parametrize(
    "question",
    [
        "매출이 얼마야?",
        "매출액은?",
        "매출액인가요?",
        "영업수익을 알려줘",
        "매출 비교해줘",
        "연결 기준 매출액이 얼마야?",
        "매출액을 알려줘",
        "매출과 비교해줘",
        "매출도 알려줘",
        "매출이랑 알려줘",
        "매출의 추이는?",
        "매출액인가",
        "매출액입니다",
    ],
)
def test_ordinary_korean_particle_forms_are_read(question: str) -> None:
    """조사·어미가 붙은 평범한 질문이 경계 때문에 막히지 않는다."""
    assert query_analyzer._find_metric(question) == METRIC_REVENUE


def test_single_fact_plan_still_carries_revenue() -> None:
    """Q01 계열은 그대로다."""
    plan = _plan("삼성전자 2024년 연결 기준 매출액이 얼마야?")

    assert plan.intent == INTENT_SINGLE_FACT
    assert plan.metric == METRIC_REVENUE
    assert plan.years == (2024,)


# --------------------------------------------------------------------------
# 4. 비교 질의 — 축이 기간이든 기업이든 같은 판정을 쓴다
# --------------------------------------------------------------------------


def test_temporal_compare_reads_revenue() -> None:
    plan = _plan("삼성전자 2024년 매출과 2025년 매출 비교해줘")

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.metric == METRIC_REVENUE


def test_temporal_compare_does_not_read_compound_as_revenue() -> None:
    """
    비교 질의는 지표를 느슨하게 읽는다. 느슨한 것과 틀리게 읽는 것은 다르다.

    여기서 metric에 revenue가 담기면 Pattern C가 구현된 뒤 매출원가를 물은 질문이 매출
    시계열로 답해진다. 읽지 못한 것은 읽지 못한 채로 남겨 둔다.
    """
    plan = _plan("삼성전자 2024년 매출원가와 2025년 매출원가 비교해줘")

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.metric is None
    assert plan.derived_metric is None


def test_cross_entity_compare_reads_revenue() -> None:
    plan = _plan("삼성전자와 SK하이닉스 2024년 매출 비교해줘")

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.metric == METRIC_REVENUE


def test_cross_entity_compare_does_not_read_compound_as_revenue() -> None:
    plan = _plan("삼성전자와 SK하이닉스 2024년 매출채권 비교해줘")

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.metric is None
    assert plan.derived_metric is None


# --------------------------------------------------------------------------
# 5. 어휘 교체 — 패턴이 현재 어휘를 본다
# --------------------------------------------------------------------------


def test_pattern_follows_the_current_token_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    경계 패턴을 모듈 수준에서 한 번 컴파일해 두면 어휘 변경이 판정에 반영되지 않는다.

    그렇게 되면 REVENUE_TOKENS에 어휘를 늘려도 판정은 그대로인 상태가 조용히 만들어지고,
    늘린 쪽은 늘어난 줄 안다. 캐시를 쓰더라도 현재 어휘 자체가 키에 들어가야 한다.
    """
    assert query_analyzer._find_metric("영업이익이 얼마야?") is None

    monkeypatch.setattr(
        query_analyzer,
        "REVENUE_TOKENS",
        (*query_analyzer.REVENUE_TOKENS, "영업이익"),
    )

    assert query_analyzer._find_metric("영업이익이 얼마야?") == METRIC_REVENUE

    # 어휘를 늘려도 경계는 그대로다.
    assert query_analyzer._find_metric("영업이익률") is None


def test_business_profile_exclusion_keeps_substring_semantics() -> None:
    """
    이 PR은 Q05의 판정을 건드리지 않는다.

    BUSINESS_PROFILE_EXCLUDED_TOKENS는 '값을 함께 물었는가'를 넓게 잡아 요약 경로를 닫는
    쪽이 fail-closed다. 지표 판정과 같은 경계를 쓰면 그 자리가 오히려 열린다.
    """
    assert "매출액" in query_analyzer.BUSINESS_PROFILE_EXCLUDED_TOKENS
    assert not query_analyzer._looks_like_business_profile(
        "무슨 사업을 하고 주력 제품 매출원가가 뭐야?"
    )
