"""Q06 거래소공시 단건 요약의 질의 해석 테스트.

고정하려는 것:
    1. 회계연도를 밝히지 않은 Q06이 missing_year로 막히지 않는다.
    2. 대상 공시는 질문에 적힌 내용(기업 + 주제어)으로 좁힌다. 문서 식별자를 계획에 담지
       않는다.
    3. 원공시 시점을 지목한 표현은 원공시 판본, 그렇지 않으면 최신 유효 판본이다.
    4. 정기공시 요약의 회계연도 요건은 그대로다. 연도를 밝히지 않은 모든 요약 질문이
       합법이 되지는 않는다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.derived_metrics import DerivedMetric
from disclosure_agent.agent.query_analyzer import (
    INTENT_DOCUMENT_SUMMARY,
    INTENT_EVENT_HISTORY,
    INTENT_EVENT_SUMMARY,
    INTENT_SINGLE_FACT,
    INTENT_TEMPORAL_COMPARE,
    QueryPlan,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.common.enums import ChainPolicy, SectionScope

# Q05는 기간을 밝히지 않는 질문이라 기준 연도 대응표가 있어야 계획이 만들어진다.
# 실제 실행에서는 Router가 manifest에서 유도해 넘긴다.
FISCAL_YEAR_ANCHORS = {"한미반도체": 2025}

# manifest에서 오는 목록을 흉내 낸다. 같은 기업이 표기를 달리해 두 번 들어 있는 상태까지
# 재현한다. 실제 manifest에서 거래소공시는 'LG유플러스', 정기공시는 '(주)LG유플러스'다.
KNOWN_COMPANIES = (
    "LG유플러스",
    "(주)LG유플러스",
    "삼성바이오로직스",
    "한미반도체",
    "HD현대일렉트릭",
    "레인보우로보틱스",
    "HD현대중공업",
    "삼성중공업",
)

Q06 = "LG유플러스 파주 AIDC 투자 공시 내용 요약해줘."

ORIGINAL_STATE_QUESTIONS = (
    "LG유플러스 파주 AIDC 투자 원공시 당시 내용 요약해줘.",
    "LG유플러스 파주 AIDC 투자 처음 공시했을 때 내용 알려줘.",
)


def plan_for(question: str) -> QueryPlan:
    return build_query_plan(question, KNOWN_COMPANIES)


# --------------------------------------------------------------------------
# A. 표준 Q06
# --------------------------------------------------------------------------


def test_q06_is_not_rejected_for_missing_fiscal_year() -> None:
    """회계연도가 없다는 이유로 막히지 않는다.

    거래소공시 manifest 레코드에는 회계연도가 없다. 연도를 요구하면 사용자가 알 수 없는
    값을 요구하게 된다.
    """
    plan = plan_for(Q06)

    assert plan.intent == INTENT_EVENT_SUMMARY
    assert plan.years == ()


def test_q06_plan_targets_the_event_document_class() -> None:
    plan = plan_for(Q06)

    assert plan.document_class == "event"
    assert plan.metric is None
    assert plan.derived_metric is None
    assert plan.accounting_scope == "not_applicable"


def test_q06_selects_the_event_by_question_content() -> None:
    """대상 공시를 좁히는 조건이 질문에 적힌 말에서 온다."""
    plan = plan_for(Q06)

    assert plan.company == "LG유플러스"
    assert plan.event_subject_tokens == ("파주", "AIDC", "투자")


def test_q06_defaults_to_the_latest_effective_state() -> None:
    """시점을 밝히지 않은 질문은 지금 유효한 내용을 묻는 것이다."""
    assert plan_for(Q06).chain_policy is ChainPolicy.LATEST_VALID


# --------------------------------------------------------------------------
# B. 원공시 시점을 지목한 표현
# --------------------------------------------------------------------------


@pytest.mark.parametrize("question", ORIGINAL_STATE_QUESTIONS)
def test_original_state_wording_selects_the_root_disclosure(question: str) -> None:
    plan = plan_for(question)

    assert plan.intent == INTENT_EVENT_SUMMARY
    assert plan.chain_policy is ChainPolicy.ORIGINAL_ONLY


@pytest.mark.parametrize("question", ORIGINAL_STATE_QUESTIONS)
def test_original_state_wording_keeps_the_same_event_subject(question: str) -> None:
    """판본 표현이 주제어를 오염시키지 않는다.

    '원공시', '처음' 같은 시점 표기가 주제어에 남으면 본문 대조에서 걸리지 않아, 표준 Q06과
    같은 이벤트를 가리키는 질문이 서로 다른 문서를 찾게 된다.
    """
    assert plan_for(question).event_subject_tokens == plan_for(Q06).event_subject_tokens


# --------------------------------------------------------------------------
# C. 문서 식별자를 담지 않는다
# --------------------------------------------------------------------------


@pytest.mark.parametrize("question", (Q06, *ORIGINAL_STATE_QUESTIONS))
def test_plan_carries_no_document_identifier(question: str) -> None:
    """계획 어디에도 접수번호나 doc_id가 없다.

    식별자를 담으면 그 질문 하나만 답하고 같은 모양의 다른 질문은 답하지 못한다. 코퍼스가
    바뀌면 조용히 다른 문서를 가리키기도 한다.
    """
    rendered = repr(plan_for(question).to_dict())

    assert "20250429800933" not in rendered
    assert "exchange_" not in rendered
    assert not any(token.isdigit() for token in plan_for(question).event_subject_tokens)


def test_same_shape_question_for_another_company_also_plans() -> None:
    """대표 질문 문자열이 아니라 질문의 모양을 알아본다."""
    plan = plan_for("레인보우로보틱스 사옥 신축 공시 내용 요약해줘.")

    assert plan.intent == INTENT_EVENT_SUMMARY
    assert plan.company == "레인보우로보틱스"
    assert plan.event_subject_tokens == ("사옥", "신축")


# --------------------------------------------------------------------------
# 질문이 좁힌 조건을 버리지 않는다
# --------------------------------------------------------------------------


def test_declared_year_is_kept_as_a_filing_anchor() -> None:
    """연도를 밝힌 질문에서 그 연도를 버리지 않는다.

    버리면 사용자가 좁힌 범위가 조용히 사라진 채로 다른 해의 공시가 답이 된다.
    """
    plan = plan_for("LG유플러스 2024년 파주 AIDC 투자 공시 내용 요약해줘.")

    assert plan.years == (2024,)
    assert "2024년" not in plan.event_subject_tokens


def test_declared_subtype_narrows_the_plan() -> None:
    plan = plan_for("레인보우로보틱스 사옥 신축 시설투자 공시 내용 요약해줘.")

    assert plan.document_subtype == "신규시설투자등"


def test_subtype_is_absent_when_the_question_does_not_name_one() -> None:
    """유형을 추측해 채우지 않는다. 추측이 후보를 잘못 좁힌다."""
    assert plan_for(Q06).document_subtype is None


def test_two_periods_are_rejected_instead_of_choosing_one() -> None:
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("LG유플러스 파주 AIDC 투자 2024년이랑 2025년 공시 내용 요약해줘.")

    assert error.value.reason == "unsupported_event_summary_period"


def test_relative_period_is_not_resolved_for_event_summary() -> None:
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("LG유플러스 파주 AIDC 투자 작년 공시 내용 요약해줘.")

    assert error.value.reason == "unsupported_event_summary_period"


# --------------------------------------------------------------------------
# 주제어 없이는 열리지 않는다
# --------------------------------------------------------------------------


def test_subjectless_disclosure_summary_keeps_the_previous_outcome() -> None:
    """어느 공시인지 가릴 근거가 없으면 이 경로를 열지 않는다.

    연도를 밝히지 않은 모든 요약 질문이 합법이 되지는 않는다는 것이 이 테스트의 요점이다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("LG유플러스 공시 내용 요약해줘.")

    assert error.value.reason == "missing_year"


def test_event_summary_plan_cannot_be_built_without_subject_tokens() -> None:
    """계획을 직접 만드는 호출자에게도 같은 규칙이 걸린다."""
    with pytest.raises(ValueError, match="event_subject_tokens"):
        QueryPlan(
            question="LG유플러스 공시 내용 요약해줘.",
            companies=("LG유플러스",),
            years=(),
            intent=INTENT_EVENT_SUMMARY,
            document_class="event",
            document_subtype=None,
            accounting_scope="not_applicable",
            metric=None,
            chain_policy=ChainPolicy.LATEST_VALID,
        )


def test_event_subject_tokens_are_rejected_on_other_intents() -> None:
    """다른 의도에 실리면 그 handler가 읽지 않아 조건이 조용히 사라진다."""
    with pytest.raises(ValueError, match="event_subject_tokens"):
        QueryPlan(
            question="한미반도체 2025년 매출액이 얼마야?",
            companies=("한미반도체",),
            years=(2025,),
            intent=INTENT_SINGLE_FACT,
            event_subject_tokens=("파주",),
        )


# --------------------------------------------------------------------------
# H. 회귀 통제 — 다른 대표 질문의 해석이 달라지지 않는다
# --------------------------------------------------------------------------


def test_q04_document_summary_plan_is_unchanged() -> None:
    plan = plan_for("삼성바이오로직스 2025년 사업보고서에서 설비 투자 관련 내용 정리해줘.")

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.years == (2025,)
    assert plan.section_scope is SectionScope.FACILITY_INVESTMENT
    assert plan.event_subject_tokens == ()


def test_q05_business_profile_plan_is_unchanged() -> None:
    plan = build_query_plan(
        "한미반도체는 무슨 사업을 하고 주력 제품이 뭐야?",
        KNOWN_COMPANIES,
        fiscal_year_anchors=FISCAL_YEAR_ANCHORS,
    )

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.section_scopes == (SectionScope.BUSINESS_OVERVIEW, SectionScope.PRODUCTS)
    assert plan.event_subject_tokens == ()


def test_q08_temporal_derived_plan_is_unchanged() -> None:
    plan = plan_for("한미반도체 영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?")

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.years == (2024, 2025)
    assert plan.derived_metric is DerivedMetric.OPERATING_MARGIN


def test_q11_cross_entity_plan_is_unchanged() -> None:
    plan = plan_for("HD현대중공업이랑 삼성중공업 중에 2025년 영업이익률이 더 높은 곳은 어디야?")

    assert plan.intent == "cross_entity_compare"
    assert plan.companies == ("HD현대중공업", "삼성중공업")
    assert plan.derived_metric is DerivedMetric.OPERATING_MARGIN


def test_q13_event_history_plan_is_unchanged() -> None:
    plan = plan_for("HD현대일렉트릭이 2024년 8월에 공시한 공급계약, 나중에 바뀐 내용 있어?")

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.document_subtype == "단일판매공급계약체결"
    assert plan.chain_policy is ChainPolicy.FULL_CHAIN
    assert plan.years == (2024,)
    assert plan.month == 8


def test_q15_event_history_plan_is_unchanged() -> None:
    plan = plan_for("레인보우로보틱스 사옥·제조시설 신축 투자, 그동안 계획이 몇 번 바뀌었어?")

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.document_subtype == "신규시설투자등"
    assert plan.chain_policy is ChainPolicy.FULL_CHAIN


def test_history_question_about_disclosure_content_stays_with_pattern_e() -> None:
    """이력 표현이 함께 있는 질문은 계속 Pattern E가 받는다.

    요약 판정이 이력 판정보다 앞에 오면 이 질문을 가로챈다.
    """
    plan = plan_for("HD현대일렉트릭 2024년 공급계약 공시 내용 어떻게 바뀌었는지 정리해줘.")

    assert plan.intent == INTENT_EVENT_HISTORY


def test_yearless_periodic_summary_still_requires_a_fiscal_year() -> None:
    """정기공시 요약의 기간 정책은 그대로다(open_decisions.md D5-a).

    Q06의 완화가 정기공시 쪽으로 새지 않는다는 것을 고정한다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("삼성바이오로직스 사업보고서에서 설비 투자 관련 내용 정리해줘.")

    assert error.value.reason == "missing_year"
