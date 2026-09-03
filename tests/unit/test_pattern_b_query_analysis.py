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


@pytest.mark.parametrize(
    ("question", "expected_intent"),
    [
        # 값 조회에 요약 표현만 붙은 질문. 보고서 표기도 범위도 없다.
        ("테스트반도체 2025년 매출액 정리해줘", INTENT_SINGLE_FACT),
        # 시계열 비교에 요약 표현만 붙은 질문.
        (
            "테스트반도체 2024년과 2025년 매출 비교해서 정리해줘",
            INTENT_TEMPORAL_COMPARE,
        ),
        # 보고서 표기는 있으나 범위가 없다. 문서 전체 요약으로 열지 않는다.
        ("테스트반도체 2025년 사업보고서 매출액 정리해줘", INTENT_SINGLE_FACT),
    ],
)
def test_summary_expression_alone_does_not_hijack_other_patterns(
    question: str,
    expected_intent: str,
) -> None:
    """
    "정리해줘"·"요약해줘"는 값 조회 질문에도 붙는다.

    표현 하나로 요약 경로를 열면 Pattern A·C가 이미 답하던 질문이 끌려오고, 요약 대상도
    범위도 없으므로 결국 막힌다. 그때 남는 사유는 '요약 대상을 확정할 수 없다'가 되어 실제로
    일어난 일(답할 수 있던 질문을 가로챘다)과 달라진다.
    """
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

    사유가 요약 경로의 것이 아닌 이유:
        정기공시 보고서 표기가 없으면 요약 판정 자체가 서지 않아 나머지 판정으로 넘어간다
        (_looks_like_document_summary). 넘겨받은 쪽이 Pattern B가 생기기 전과 같은 사유로
        막으며, 그것이 이 질문에서 실제로 일어난 일이다. 요약 경로가 잡았다가 놓아준 것처럼
        보이는 사유를 붙이지 않는다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트전자 합성단지 투자 공시 내용 요약해줘.")

    assert error.value.reason == "missing_year"


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

    범위를 읽지 못한 질문은 요약 판정이 서지 않아 나머지 판정으로 넘어가고, 그쪽에서 막힌다
    (_looks_like_document_summary). 어느 경로로 막히든 문서 전체를 요약하지 않는 것은 같으며,
    이 테스트가 고정하는 것은 그 사실이다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트바이오 2025년 사업보고서 내용 정리해줘")

    assert error.value.reason == "unsupported_metric"


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


# --------------------------------------------------------------------------
# 4. 기업 소개 요약(Q05 계열)
# --------------------------------------------------------------------------
#
# "<기업>는 무슨 사업을 하고 주력 제품이 뭐야?" 모양이다. 요약 요청 표현도 보고서 종류도
# 없지만 사업 개요와 주요 제품을 함께 묻고 있으므로, 사업보고서 1건의 두 범위를 요약하는
# 계획이 된다. 기간이 없으면 이 모양에 한해 코퍼스 기준 최신 사업보고서 회계연도로 옮긴다.


@pytest.mark.parametrize(
    "question",
    [
        "테스트반도체는 무슨 사업을 하고 주력 제품이 뭐야?",
        "테스트반도체는 어떤 사업을 하고 주요 제품이 뭐야?",
        # 띄어쓰기 표기가 달라도 같은 모양이다.
        "테스트반도체는 무슨사업을 하고 주력제품이 뭐야?",
    ],
)
def test_business_profile_question_becomes_a_two_scope_summary(question: str) -> None:
    plan = plan_for(question)

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.companies == ("테스트반도체",)
    assert plan.document_class == "periodic"
    assert plan.document_subtype == "annual"
    assert plan.section_scope is None
    assert plan.section_scopes == (SectionScope.BUSINESS_OVERVIEW, SectionScope.PRODUCTS)
    assert plan.metric is None
    assert plan.derived_metric is None
    assert plan.accounting_scope == QueryAccountingScope.NOT_APPLICABLE.value


def test_business_profile_without_a_year_uses_the_company_anchor() -> None:
    """
    기간이 없으면 주입받은 기준 연도(코퍼스의 최신 사업보고서 회계연도)로 옮긴다.

    옮겼다는 사실은 warnings에 남는다. 실행 시각을 쓰지 않았다는 것도 함께 적는다.
    """
    plan = build_query_plan(
        "테스트반도체는 무슨 사업을 하고 주력 제품이 뭐야?",
        known_companies=COMPANIES,
        fiscal_year_anchors={"테스트반도체": 2024},
    )

    assert plan.years == (2024,)
    assert plan.relative_period is None
    assert any("FY2024" in warning and "테스트반도체" in warning for warning in plan.warnings)


def test_business_profile_with_an_explicit_year_uses_that_year() -> None:
    plan = plan_for("테스트반도체는 2024년에 무슨 사업을 하고 주력 제품이 뭐야?")

    assert plan.years == (2024,)
    assert plan.section_scopes == (SectionScope.BUSINESS_OVERVIEW, SectionScope.PRODUCTS)
    assert plan.warnings == ()


def test_business_profile_without_an_anchor_is_blocked() -> None:
    """기준 연도 대응표가 없거나 그 기업이 없으면 오늘 연도로 대신하지 않고 막는다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(
            "테스트반도체는 무슨 사업을 하고 주력 제품이 뭐야?",
            known_companies=COMPANIES,
        )

    assert error.value.reason == "missing_fiscal_year_anchor"

    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(
            "테스트반도체는 무슨 사업을 하고 주력 제품이 뭐야?",
            known_companies=COMPANIES,
            fiscal_year_anchors={"테스트전자": 2025},
        )

    assert error.value.reason == "missing_fiscal_year_anchor"


def test_business_profile_over_two_periods_is_blocked() -> None:
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트반도체는 2024년이랑 2025년에 무슨 사업을 하고 주력 제품이 뭐야?")

    assert error.value.reason == "unsupported_summary_period"


def test_business_profile_with_a_relative_period_is_blocked() -> None:
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트반도체는 작년에 무슨 사업을 하고 주력 제품이 뭐야?")

    assert error.value.reason == "unsupported_summary_period"


@pytest.mark.parametrize(
    "question",
    [
        # 사업 서술 표현만 있다. 제품을 묻지 않았으므로 이 모양이 아니다.
        "테스트반도체는 무슨 사업을 해?",
        # 제품 표현만 있다. 값 조회 어휘와 섞이는 경우가 여기 해당한다.
        "테스트반도체 주요 제품 매출액이 얼마야?",
    ],
)
def test_one_semantic_group_alone_does_not_open_the_business_profile_path(
    question: str,
) -> None:
    """
    두 묶음 중 하나만으로는 기업 소개 요약으로 보지 않는다.

    한쪽만으로 열면 묻지 않은 범위까지 요약하거나, 값 조회 질문이 요약 경로로 끌려온다.
    기간이 없는 이 질문들은 이전과 같이 missing_year로 막힌다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for(question)

    assert error.value.reason == "missing_year"


def test_generic_summary_without_a_period_is_still_blocked() -> None:
    """
    기간 기본값은 기업 소개 요약 모양에만 적용한다.

    일반 요약(Q04 모양)에서 기간이 빠진 질문은 이전과 같이 missing_year다. D5-a는 여전히
    미정이며 이 변경이 그 정책을 정하지 않는다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트바이오 사업보고서에서 사업 개요 정리해줘")

    assert error.value.reason == "missing_year"


# --------------------------------------------------------------------------
# 5. 값 조회가 섞인 기업 소개 질문은 요약으로 받지 않는다
# --------------------------------------------------------------------------
#
# 두 묶음이 있어도 값을 함께 묻는 질문은 순수한 요약 요청이 아니다. 요약으로 받으면 물은 값이
# 조용히 빠진 답이 나간다. 이런 질문은 Q05 경로가 생기기 전과 같은 경로로 흘러가며, 그쪽
# 규칙으로 답하거나 막힌다. 두 반을 함께 답하는 능력은 만들지 않는다.


def test_business_profile_with_a_revenue_request_is_not_claimed_as_a_summary() -> None:
    """
    대표 질문 문구에 연도와 매출 조회가 덧붙은 질문.

    Q05 경로가 생기기 전과 같이 단일 사실 조회로 흘러간다. 요약 계획이 아니며 범위 묶음도
    비어 있다. 값 조회 쪽이 사업 소개 반을 답하지 않는 것은 이 변경 전부터의 동작이고,
    여기서 고정하는 것은 "요약 경로가 값 조회를 가로채지 않는다"이다.
    """
    plan = build_query_plan(
        "한미반도체는 무슨 사업을 하고 주력 제품이 뭐고 2025년 매출은 얼마야?",
        known_companies=("한미반도체",),
        fiscal_year_anchors={"한미반도체": 2025},
    )

    assert plan.intent == INTENT_SINGLE_FACT
    assert plan.years == (2025,)
    assert plan.metric == "revenue"
    assert plan.section_scopes == ()
    assert plan.section_scope is None


def test_business_profile_with_an_operating_margin_request_is_not_claimed() -> None:
    """파생지표 조회가 섞이면 이전과 같이 단일 사실 경로의 지표 판정에서 막힌다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트반도체는 무슨 사업을 하고 주력 제품이 뭐고 2025년 영업이익률은 얼마야?")

    assert error.value.reason == "unsupported_metric"


@pytest.mark.parametrize(
    "question",
    [
        "테스트반도체는 무슨 사업을 하고 주요 제품 매출액은 얼마야?",
        "테스트반도체 주요 제품별 매출 비중이 무슨 사업부에서 나와?",
        "테스트반도체는 어떤 사업을 하고 주요 제품 계약 금액이 얼마야?",
    ],
)
def test_value_requests_without_a_year_stay_fail_closed(question: str) -> None:
    """
    연도 없는 값 조회는 기준 연도로 옮기지 않는다.

    Q05의 기간 기본값은 순수한 기업 소개 질문에만 적용한다. 값 조회가 섞인 질문에 그 기본값을
    주면 물은 값을 빼놓은 요약이 근거를 갖춘 채로 나간다. 이전과 같이 missing_year다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for(question)

    assert error.value.reason == "missing_year"


# --------------------------------------------------------------------------
# 6. '사업부'는 사업 서술이 아니다
# --------------------------------------------------------------------------
#
# 사업 서술 표기는 조사까지 요구한다("무슨 사업을", "어떤 사업을", "어떤 사업인지"). '무슨
# 사업'만으로 보면 사업부를 묻는 질문이 함께 걸리고, 그 질문은 기업 소개가 아니다.


@pytest.mark.parametrize(
    "question",
    [
        "테스트반도체는 어떤 사업부가 주력 제품을 담당해?",
        "테스트반도체는 무슨 사업부의 주요 제품이야?",
    ],
)
def test_business_unit_questions_do_not_open_the_business_profile_path(
    question: str,
) -> None:
    """사업부를 묻는 질문은 이전과 같이 연도 없는 값 조회로 흘러가 missing_year로 막힌다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for(question)

    assert error.value.reason == "missing_year"


@pytest.mark.parametrize(
    "question",
    [
        "한미반도체는 무슨 사업을 하고 주력 제품이 뭐야?",
        "한미반도체는 어떤 사업을 하고 주요 제품이 뭐야?",
        "한미반도체는 2024년에 어떤 사업을 하고 주요 제품이 뭐야?",
    ],
)
def test_canonical_business_profile_wordings_still_open_the_path(question: str) -> None:
    plan = build_query_plan(
        question,
        known_companies=("한미반도체",),
        fiscal_year_anchors={"한미반도체": 2025},
    )

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.section_scopes == (SectionScope.BUSINESS_OVERVIEW, SectionScope.PRODUCTS)
    assert plan.years == ((2024,) if "2024년" in question else (2025,))
