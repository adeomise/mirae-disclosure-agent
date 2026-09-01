"""Pattern B(단일 문서 요약) 질의 해석 테스트.

검증 대상:
- 요약을 요청한 질문이 document_summary로 분류되는가
- 그 분류가 이미 답하고 있던 다른 Pattern의 질문을 가로채지 않는가
- 정책이 정해지지 않은 요약 질문(Q05·Q06 계열)이 여전히 fail-closed인가

여기서는 문서를 읽지 않는다. 질문 문자열에서 QueryPlan까지만 본다.

대표 질문 문자열을 그대로 알아보는 규칙을 쓰지 않는다:
    문항 번호나 기업명으로 가르면 그 문장 하나만 통과하고 같은 모양의 다른 질문은 막힌다.
    아래 테스트가 같은 의미 구조를 가진 합성 질문으로 같은 분류를 요구하는 이유다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.query_analyzer import (
    INTENT_CROSS_ENTITY_COMPARE,
    INTENT_DOCUMENT_SUMMARY,
    INTENT_EVENT_HISTORY,
    INTENT_SINGLE_FACT,
    INTENT_TEMPORAL_COMPARE,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.common.enums import QueryAccountingScope, SectionScope

# 실제 기업명을 쓰지 않는다. 인식 후보는 호출자가 넘기는 목록이므로 합성 이름으로 충분하다.
COMPANIES = ("테스트반도체", "테스트전자", "테스트바이오")

ANCHORS = {"테스트반도체": 2025, "테스트전자": 2025, "테스트바이오": 2025}


def plan_for(question: str):
    return build_query_plan(
        question,
        known_companies=COMPANIES,
        fiscal_year_anchors=ANCHORS,
    )


# --------------------------------------------------------------------------
# 1. 요약 질문의 분류
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        # Q04와 같은 의미 구조: 기업 + 회계연도 + 보고서 종류 + 문서 내 범위 + 요약 요청.
        "테스트바이오 2025년 사업보고서에서 설비 투자 관련 내용 정리해줘.",
        "테스트바이오 2025년 사업보고서 설비 투자 요약해줘",
        # 어순이 달라도 같은 구조다.
        "2025년 테스트바이오 사업보고서의 시설투자 내용을 정리해 줘",
        # 띄어쓰기 표기가 달라도 같은 범위를 가리킨다.
        "테스트바이오 2025년 사업보고서 설비투자 정리해줘",
    ],
)
def test_summary_shaped_question_becomes_document_summary(question: str) -> None:
    plan = plan_for(question)

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.section_scope is SectionScope.FACILITY_INVESTMENT
    assert plan.years == (2025,)
    assert plan.companies == ("테스트바이오",)


def test_summary_plan_targets_the_annual_periodic_filing() -> None:
    """요약 대상은 질문이 밝힌 보고서 종류로 정한다."""
    plan = plan_for("테스트바이오 2025년 사업보고서에서 설비 투자 관련 내용 정리해줘.")

    assert plan.document_class == "periodic"
    assert plan.document_subtype == "annual"


def test_summary_plan_does_not_carry_a_metric() -> None:
    """
    요약 질문은 값 하나를 묻지 않는다.

    metric에 기본값(매출액)이 남으면 이 계획을 실행하는 쪽이 재무 수치를 함께 뽑을 수 있고,
    그 값은 질문이 요청한 범위 밖에서 나온다.
    """
    plan = plan_for("테스트바이오 2025년 사업보고서에서 설비 투자 관련 내용 정리해줘.")

    assert plan.metric is None
    assert plan.derived_metric is None
    assert plan.accounting_scope == QueryAccountingScope.NOT_APPLICABLE.value


# --------------------------------------------------------------------------
# 2. 다른 Pattern을 가로채지 않는다
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected_intent"),
    [
        # Pattern A. 값 하나를 묻는 질문은 요약 표현이 없다.
        ("테스트반도체 2025년 연결 기준 매출액이 얼마야?", INTENT_SINGLE_FACT),
        # Pattern C. 두 기간을 견주는 질문.
        (
            "테스트반도체 영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?",
            INTENT_TEMPORAL_COMPARE,
        ),
        # Pattern C. 상대 기간만 쓴 질문.
        ("테스트반도체 매출이 작년보다 얼마나 늘었어?", INTENT_TEMPORAL_COMPARE),
        # Pattern D. 두 기업을 견주는 질문.
        (
            "2025년 테스트반도체랑 테스트전자 매출 비교해줘",
            INTENT_CROSS_ENTITY_COMPARE,
        ),
        # Pattern E. 이력을 묻는 질문.
        (
            "테스트반도체가 2024년 8월에 공시한 공급계약, 나중에 바뀐 내용 있어?",
            INTENT_EVENT_HISTORY,
        ),
    ],
)
def test_existing_intents_are_unchanged(question: str, expected_intent: str) -> None:
    assert plan_for(question).intent == expected_intent


def test_event_history_wins_over_summary_when_both_expressions_are_present() -> None:
    """
    이력 표현과 요약 표현이 함께 있으면 Pattern E다.

    요약 판정이 이력 판정보다 앞에 있으면 Pattern E가 이미 답하던 질문을 가로챈다. 그때
    돌아오는 것은 이력이 아니라 문서 한 절의 요약이며, 형식은 정상이다.
    """
    plan = plan_for("테스트반도체 시설투자 계획이 2024년에 바뀐 내용 정리해줘")

    assert plan.intent == INTENT_EVENT_HISTORY


def test_multi_company_summary_stays_on_the_cross_entity_path() -> None:
    """
    기업이 둘이면 Pattern D다. 요약 표현이 있어도 마찬가지다.

    단일 문서 요약은 문서 1건을 대상으로 하므로, 기업 하나를 골라 요약하면 나머지 기업을
    물었다는 사실이 사라진다.
    """
    plan = plan_for("테스트반도체랑 테스트전자 2025년 사업보고서 설비 투자 정리해줘")

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE


# --------------------------------------------------------------------------
# 3. 정책이 정해지지 않은 요약 질문은 계획을 만들지 않는다
# --------------------------------------------------------------------------


def test_summary_without_a_report_type_is_blocked() -> None:
    """
    Q06 계열. 거래소공시 단건 요약은 판본 선택 정책(D5)이 미정이라 열려 있지 않다.

    가까운 문서를 골라 요약하면 원공시와 정정본 중 무엇을 요약했는지가 결과에 남지 않는다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트전자 합성단지 투자 공시 내용 요약해줘.")

    assert error.value.reason == "unsupported_summary_target"


def test_summary_without_a_period_is_blocked() -> None:
    """
    기간을 밝히지 않은 요약 질문은 기본 문서를 고르지 않는다(D5-a 미정).

    최신 사업보고서를 기본값으로 채우면 그 선택이 곧 정책이 된다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트바이오 사업보고서에서 설비 투자 정리해줘")

    assert error.value.reason == "missing_year"


def test_summary_with_a_relative_period_is_blocked() -> None:
    """
    상대 기간은 요약 대상 문서를 고르는 기준으로 쓰지 않는다.

    D8이 정한 기준 연도는 시계열 비교의 것이며, 요약 대상 선택까지 정하지 않았다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트바이오 작년 사업보고서 설비 투자 정리해줘")

    assert error.value.reason == "unsupported_summary_period"


def test_summary_over_two_periods_is_blocked() -> None:
    """단일 문서 요약은 문서 1건이 대상이다. 한 기간을 골라 답하지 않는다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트바이오 2024년이랑 2025년 사업보고서 설비 투자 정리해줘")

    assert error.value.reason == "unsupported_summary_period"


def test_summary_without_a_section_scope_is_blocked() -> None:
    """
    범위를 읽지 못하면 문서 전체 요약으로 대체하지 않는다.

    docs/agent_design/retrieval_strategy.md Pattern B의 Fallback이 그 대체를 금지한다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트바이오 2025년 사업보고서 내용 정리해줘")

    assert error.value.reason == "missing_section_scope"


def test_quarterly_summary_is_rejected_before_intent_branching() -> None:
    """
    분기·반기 표현은 의도 분기보다 앞에서 거절된다.

    Pattern이 늘어도 같은 검사를 다시 붙이지 않아야 하므로 공통 경로에 둔 규칙이며,
    요약 경로가 그 규칙을 우회하지 않는지 본다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트바이오 2025년 반기보고서 설비 투자 정리해줘")

    assert error.value.reason == "unsupported_period"


def test_unknown_company_summary_is_blocked() -> None:
    """인식 목록에 없는 기업을 비슷한 이름으로 대체하지 않는다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("존재하지않는회사 2025년 사업보고서 설비 투자 정리해줘")

    assert error.value.reason == "unknown_company"
