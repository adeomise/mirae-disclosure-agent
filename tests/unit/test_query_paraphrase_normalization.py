"""질의 표현 정규화 회귀 테스트.

검증 대상:
- 같은 것을 다르게 쓴 표현이 같은 정본 값으로 옮겨지는가(T1-002·004·005·009)
- 넓힌 어휘가 다른 뜻으로 쓰인 표기를 끌어오지 않는가
- 넓히기 전과 같은 질문이 같은 계획으로 남는가

두 층위를 함께 본다:
    어휘 층(agent.query_vocabulary)은 "이 표기가 무엇을 가리키는가"만 본다. 계획 층
    (build_query_plan)은 그렇게 읽은 값이 실제 계획까지 도달하는지를 본다. 어휘만 넓히고
    계획 쪽 순서가 그것을 가로채면 표현을 받아들였다는 사실이 사용자에게 닿지 않는다.

실제 기업명을 쓰지 않는다. 인식 후보는 호출자가 넘기는 목록이므로 합성 이름으로 충분하다.
다만 Q02·Q12 계열은 기존 회귀가 쓰는 문자열을 그대로 이어 쓴다. 넓힌 어휘가 그 질문들을
가로채지 않는다는 것이 이 변경에서 지켜야 할 것이기 때문이다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.query_analyzer import (
    INTENT_CONTRACT_FACT,
    INTENT_CROSS_ENTITY_COMPARE,
    INTENT_DOCUMENT_SUMMARY,
    INTENT_SINGLE_FACT,
    OPERATION_COMPARE,
    SUPPORTED_CONTRACT_FIELDS,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.agent.query_vocabulary import (
    OPERATION_AGGREGATE,
    find_contract_fields,
    find_years,
    matches_product_phrase,
    normalize_aggregate_request,
)
from disclosure_agent.common.enums import MetricConcept, SectionScope

COMPANIES = ("테스트반도체", "테스트바이오")

ANCHORS = {"테스트반도체": 2025, "테스트바이오": 2025}

CONTRACT_COMPANIES = ("한화오션",)

AGGREGATE_COMPANIES = ("LG이노텍", "레인보우로보틱스")

CANONICAL_CONTRACT_FIELDS = (
    MetricConcept.CONTRACT_AMOUNT,
    MetricConcept.CONTRACT_START_DATE,
    MetricConcept.CONTRACT_END_DATE,
)


def plan_for(question: str):
    return build_query_plan(
        question,
        known_companies=COMPANIES,
        fiscal_year_anchors=ANCHORS,
    )


def contract_plan_for(question: str):
    return build_query_plan(question, known_companies=CONTRACT_COMPANIES)


# --------------------------------------------------------------------------
# 1. T1-002 계약 필드 표현
# --------------------------------------------------------------------------
#
# "계약 시작일"·"시작 날짜"는 '계약기간'과 같은 필드를 가리킨다. 표기가 다르다는 이유로
# 읽히지 않으면 답할 수 있는 질문이 unsupported_contract_fields로 막히고, 막힌 사유가
# 실제로 일어난 일(표기를 못 읽었다)과 다르다.


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("시작일", MetricConcept.CONTRACT_START_DATE),
        ("계약 시작일", MetricConcept.CONTRACT_START_DATE),
        ("시작 날짜", MetricConcept.CONTRACT_START_DATE),
        ("시작일자", MetricConcept.CONTRACT_START_DATE),
        ("종료일", MetricConcept.CONTRACT_END_DATE),
        ("계약 종료일", MetricConcept.CONTRACT_END_DATE),
        ("종료 날짜", MetricConcept.CONTRACT_END_DATE),
        ("종료일자", MetricConcept.CONTRACT_END_DATE),
    ],
)
def test_natural_date_expressions_become_canonical_contract_fields(
    expression: str,
    expected: MetricConcept,
) -> None:
    assert find_contract_fields(expression) == (expected,)


def test_contract_period_still_covers_both_ends() -> None:
    """'계약기간'은 시작일과 종료일 둘을 함께 가리킨다. 넓힌 어휘가 그 대응을 바꾸지 않는다."""
    assert find_contract_fields("계약기간") == (
        MetricConcept.CONTRACT_START_DATE,
        MetricConcept.CONTRACT_END_DATE,
    )

    assert find_contract_fields("계약 기간") == (
        MetricConcept.CONTRACT_START_DATE,
        MetricConcept.CONTRACT_END_DATE,
    )


def test_overlapping_expressions_do_not_duplicate_fields() -> None:
    """표현이 겹쳐도 같은 필드가 두 번 담기지 않는다."""
    assert find_contract_fields("계약기간 중 시작일") == (
        MetricConcept.CONTRACT_START_DATE,
        MetricConcept.CONTRACT_END_DATE,
    )


@pytest.mark.parametrize(
    "question",
    [
        "한화오션이 2025년 9월 Yang Ming과 체결한 계약의 계약금액과 계약기간은?",
        "한화오션이 2025년 9월 Yang Ming과 체결한 계약의 계약금액과 계약 시작일, 계약 종료일은?",
        "한화오션이 2025년 9월 Yang Ming과 체결한 계약의 금액과 시작 날짜, 종료 날짜는?",
        "한화오션이 2025년 9월 Yang Ming과 체결한 계약의 시작일자, 종료일자, 계약금액은?",
    ],
)
def test_same_contract_question_written_differently_makes_the_same_plan(question: str) -> None:
    """
    같은 세 필드를 요구하는 질문은 표기가 달라도 같은 계획이 된다.

    차례까지 고정하는 이유는, 표기에 따라 필드 차례가 달라지면 그 차이가 답 문장의 차이로
    새어 나가기 때문이다.
    """
    plan = contract_plan_for(question)

    assert plan.intent == INTENT_CONTRACT_FACT
    assert plan.requested_fields == CANONICAL_CONTRACT_FIELDS
    assert set(plan.requested_fields) == SUPPORTED_CONTRACT_FIELDS
    assert plan.counterparty == "Yang Ming"


@pytest.mark.parametrize(
    "question",
    [
        "한화오션의 2025년 9월 계약의 시작일은?",
        "한화오션의 2025년 9월 계약 종료 날짜 알려줘",
        "한화오션의 2025년 9월 계약의 시작일과 종료일은?",
    ],
)
def test_partial_contract_field_requests_stay_fail_closed(question: str) -> None:
    """
    날짜 표기를 읽게 되었다고 해서 지원 조합이 넓어지지는 않는다.

    답 문장을 만들 수 있는 조합은 계약금액·시작일·종료일 셋이 함께일 때 하나다. 표기를
    읽는 것과 조합을 여는 것은 다른 결정이며, 여는 쪽은 answer_builder가 그 형태를 고정한
    뒤가 먼저다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        contract_plan_for(question)

    assert error.value.reason == "unsupported_contract_fields"


# --------------------------------------------------------------------------
# 2. T1-004 보고서 종류가 붙은 연도
# --------------------------------------------------------------------------
#
# "2025 사업보고서"처럼 '년'을 생략한 표기가 실제로 온다. 그 숫자는 그 보고서의 회계연도
# 말고 다른 것을 가리키지 않으므로 연도로 읽는다. 네 자리 숫자 전반을 연도로 여는 것이
# 아니다.


@pytest.mark.parametrize(
    "question",
    [
        "2025 사업보고서",
        "2025 반기보고서",
        "2025 분기보고서",
        # '년'이 붙은 기존 표기도 그대로다.
        "2025년 사업보고서",
        # 띄어쓰기 표기가 달라도 같은 말이다.
        "2025사업보고서",
        "2025 사업 보고서",
    ],
)
def test_bare_year_resolves_next_to_a_report_type(question: str) -> None:
    assert find_years(question) == (2025,)


def test_report_context_year_is_not_counted_twice() -> None:
    """'년'과 보고서 종류가 함께 붙은 표기는 두 규칙에 모두 걸리지만 한 기간이다."""
    assert find_years("테스트바이오 2025년 사업보고서 정리해줘") == (2025,)


def test_report_context_years_keep_the_order_they_appear_in() -> None:
    """두 기간을 물은 질문에서 차례가 뒤집히면 어느 해를 먼저 물었는지가 사라진다."""
    assert find_years("2024 사업보고서랑 2025 사업보고서 비교해줘") == (2024, 2025)

    assert find_years("2025년 매출이랑 2024 사업보고서") == (2025, 2024)


@pytest.mark.parametrize(
    "question",
    [
        # 금액. 이 숫자를 연도로 세면 기간을 말하지 않은 질문에 기간이 생긴다.
        "테스트반도체 매출이 2025억원 맞아?",
        "테스트반도체 시설투자 2025억 규모야?",
        # 계약번호. 네 자리 숫자가 식별자로 쓰인 자리다.
        "테스트반도체 계약번호 2025-0001 알려줘",
        "계약번호 20250916 계약금액",
        # 보고서 이름 안의 숫자. 질문이 가리키는 기간이 아니다.
        "사업보고서 (2025.12) 요약",
        # 보고서 종류가 붙어 있지 않은 맨 숫자.
        "테스트반도체 2025 매출 알려줘",
        "직원 2025명",
    ],
)
def test_bare_numbers_without_a_report_context_are_not_years(question: str) -> None:
    """
    보고서 종류가 바로 뒤에 붙지 않은 네 자리 숫자는 연도로 읽지 않는다.

    읽으면 금액·식별자가 기간이 되고, 그 기간으로 고른 문서는 형식이 맞아 틀렸다는 사실이
    드러나지 않는다.
    """
    assert find_years(question) == ()


def test_amount_next_to_a_real_year_does_not_add_a_second_period() -> None:
    """
    "2024년 매출이 2025억원 맞아?"는 한 기간을 묻는 질문이다.

    2025를 두 번째 연도로 세면 답할 수 있는 질문이 시계열 비교로 오분류되어 막힌다.
    """
    assert find_years("2024년 매출이 2025억원 맞아?") == (2024,)


def test_bare_report_year_reaches_the_summary_plan() -> None:
    """어휘가 읽은 연도가 계획까지 도달한다. 이 질문은 이전에 missing_year로 막혔다."""
    plan = plan_for("테스트바이오 2025 사업보고서 설비 투자 정리해줘")

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.years == (2025,)
    assert plan.section_scope is SectionScope.FACILITY_INVESTMENT


def test_bare_report_year_reaches_the_single_fact_plan() -> None:
    plan = plan_for("테스트반도체 2025 사업보고서 연결 매출액 얼마야?")

    assert plan.intent == INTENT_SINGLE_FACT
    assert plan.years == (2025,)


@pytest.mark.parametrize(
    "question",
    [
        "테스트반도체 2025 반기보고서 매출 얼마야?",
        "테스트반도체 2025 분기보고서 매출 얼마야?",
    ],
)
def test_half_and_quarter_reports_are_still_out_of_scope(question: str) -> None:
    """
    연도를 읽게 되었다고 해서 반기·분기 보고서 조회가 열리지는 않는다.

    기간 규칙(_reject_out_of_scope_period)이 의도 분기보다 앞에서 그대로 거절한다. 어휘를
    넓히는 변경이 지원 범위를 함께 넓히지 않는다는 것을 여기서 고정한다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for(question)

    assert error.value.reason == "unsupported_period"


def test_amount_shaped_number_still_fails_closed_at_the_plan() -> None:
    """연도로 읽지 않은 숫자는 계획에도 기간을 만들지 않는다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트반도체 매출이 2025억원 맞아?")

    assert error.value.reason == "missing_year"


# --------------------------------------------------------------------------
# 3. T1-005 제품 표현
# --------------------------------------------------------------------------
#
# 주요 제품을 묻는 어순이 여럿이다. 표기 하나만 알아보면 같은 것을 물은 질문이 표기에 따라
# 요약 경로에 들어가기도 하고 값 조회 경로로 내려가 missing_year로 막히기도 한다.


@pytest.mark.parametrize(
    "expression",
    [
        # 기존 표기.
        "주력제품",
        "주요제품",
        "주력 제품",
        "주요 제품",
        # 넓힌 표기.
        "어떤 제품이 주력이야",
        "주력으로 파는 제품",
        "주요한 제품",
        "무슨 제품을 만들어",
        "대표 제품",
        "핵심 제품",
        "주된 제품",
        "무슨 제품이 주요야",
        "어떤 제품을 생산해",
    ],
)
def test_product_phrasings_are_recognized(expression: str) -> None:
    assert matches_product_phrase(expression)


@pytest.mark.parametrize(
    "expression",
    [
        # 제품이라는 말만 있다. 무엇을 묻는지가 없다.
        "제품",
        "제품 목록",
        # 제품과 무관한 '주력'.
        "주력 사업이 뭐야",
        "주력 사업부는 어디야",
        # 제품이 아닌 다른 것을 묻는다.
        "어떤 사업을 해",
        "생산 설비가 어디에 있어",
    ],
)
def test_unrelated_wording_is_not_read_as_a_product_request(expression: str) -> None:
    assert not matches_product_phrase(expression)


@pytest.mark.parametrize(
    "question",
    [
        "테스트반도체는 무슨 사업을 하고 어떤 제품이 주력이야?",
        "테스트반도체는 무슨 사업을 하고 주력으로 파는 제품이 뭐야?",
        "테스트반도체는 어떤 사업을 하고 주요한 제품이 뭐야?",
        "테스트반도체는 무슨 사업을 하고 무슨 제품을 만들어?",
        "테스트반도체는 어떤 사업을 하고 대표 제품이 뭐야?",
    ],
)
def test_product_paraphrases_open_the_business_profile_path(question: str) -> None:
    """넓힌 제품 표현이 기존 Q05 계획을 그대로 만든다. 새 의도를 만들지 않는다."""
    plan = plan_for(question)

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.section_scopes == (SectionScope.BUSINESS_OVERVIEW, SectionScope.PRODUCTS)
    assert plan.years == (2025,)
    assert plan.metric is None


@pytest.mark.parametrize(
    "question",
    [
        # 제품 표현만 있다. 사업 서술이 없으므로 기업 소개 질문이 아니다.
        "테스트반도체는 어떤 제품이 주력이야?",
        "테스트반도체 대표 제품이 뭐야?",
        "테스트반도체는 무슨 제품을 만들어?",
    ],
)
def test_product_phrase_alone_does_not_open_the_business_profile_path(question: str) -> None:
    """
    두 묶음을 함께 요구하던 규칙은 그대로다.

    한쪽만으로 열면 묻지 않은 범위까지 요약한 결과가 나간다. 이 질문들은 넓히기 전과 같이
    연도 없는 값 조회로 흘러가 missing_year로 막힌다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for(question)

    assert error.value.reason == "missing_year"


@pytest.mark.parametrize(
    "question",
    [
        "테스트반도체는 무슨 사업을 하고 대표 제품 매출액은 얼마야?",
        "테스트반도체는 어떤 사업을 하고 어떤 제품이 주력인지랑 계약 금액이 얼마야?",
    ],
)
def test_value_requests_are_not_claimed_by_the_widened_product_vocabulary(
    question: str,
) -> None:
    """
    값을 함께 묻는 질문은 넓힌 표기로도 요약 경로에 들어가지 않는다.

    요약으로 받으면 근거를 갖춘 채로 물은 값을 빼놓은 답이 나가고, 값을 물었다는 사실은
    결과 어디에도 남지 않는다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for(question)

    assert error.value.reason == "missing_year"


def test_business_unit_questions_are_still_not_business_profile() -> None:
    """'사업부'는 사업 서술이 아니다. 제품 표기를 넓혀도 그 판정은 그대로다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트반도체는 어떤 사업부가 대표 제품을 담당해?")

    assert error.value.reason == "missing_year"


# --------------------------------------------------------------------------
# 4. T1-009 집계 표현
# --------------------------------------------------------------------------
#
# "합치면"·"합쳐서"·"합산해서"·"총합"·"전부 더해서"는 모두 같은 요청이다. 표기 하나만
# 알아보면 같은 질문이 표기에 따라 집계 경로에 들어가기도 하고 빠지기도 한다.


@pytest.mark.parametrize(
    "expression",
    [
        "합치다",
        "합치면",
        "합쳐",
        "합쳐서",
        "합친",
        "합친 값",
        "합산",
        "합산해",
        "합산해서",
        "합계",
        "총합",
        "전부 더해",
        "전부 더해서",
        "모두 더한",
        "전체 더하기",
        "전부더해",
    ],
)
def test_aggregate_paraphrases_normalize_to_the_canonical_operation(expression: str) -> None:
    assert normalize_aggregate_request(expression) == OPERATION_AGGREGATE


@pytest.mark.parametrize(
    "expression",
    [
        # 집계가 아닌 비교 표현. '더'는 비교에 훨씬 자주 쓰인다.
        "작년보다 더 늘었어",
        "전부 더 하락했어",
        "어디가 더 커",
        # 공백을 걷어내야만 이 어휘가 생기는 표기. 원문에는 없다.
        "통합 계획",
        "종합 계획서",
        # 요약 요청이지 집계 요청이 아니다.
        "종합해서 알려줘",
        # 이 어휘와 글자만 겹치는 다른 말.
        "적합한 문서",
        "결합 재무제표",
    ],
)
def test_unrelated_wording_is_not_read_as_an_aggregate_request(expression: str) -> None:
    assert normalize_aggregate_request(expression) is None


@pytest.mark.parametrize(
    "aggregate_expression",
    [
        "다 합치면",
        "합쳐서",
        "합산해서",
        "합산하면",
        "총합으로",
        "전부 더해서",
        "모두 더하면",
    ],
)
def test_aggregate_paraphrases_reach_the_facility_aggregate_plan(
    aggregate_expression: str,
) -> None:
    """
    Q12 계열이 집계 표기 하나에 매이지 않는다.

    집계 신호만으로 이 경로가 열리지는 않는다. 시설투자 유형과 비교 표현이 함께 있어야
    하며, 그 요구는 넓히기 전과 같다.
    """
    question = (
        "2024년에 LG이노텍하고 레인보우로보틱스가 새로 발표한 시설투자, "
        f"회사별로 {aggregate_expression} 어디가 더 커?"
    )

    plan = build_query_plan(question, known_companies=AGGREGATE_COMPANIES)

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.operations == (OPERATION_AGGREGATE, OPERATION_COMPARE)
    assert plan.metric is MetricConcept.INVESTMENT_AMOUNT


def test_comparison_without_an_aggregate_request_does_not_gain_operations() -> None:
    """집계 어휘를 넓혀도 집계를 요청하지 않은 비교 질문은 그대로다."""
    plan = build_query_plan(
        "2024년에 LG이노텍하고 레인보우로보틱스가 새로 발표한 시설투자, 어디가 더 커?",
        known_companies=AGGREGATE_COMPANIES,
    )

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.operations == ()


def test_aggregate_request_without_a_facility_subtype_does_not_open_the_path() -> None:
    """
    집계·비교 표현만으로는 시설투자 집계 경로가 열리지 않는다.

    유형 신호가 빠진 질문을 집계로 추측하면 어느 공시를 더하는지가 계획에 남지 않는다.
    """
    plan = build_query_plan(
        "2024년에 LG이노텍하고 레인보우로보틱스 매출을 전부 더하면 어디가 더 커?",
        known_companies=AGGREGATE_COMPANIES,
    )

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.operations == ()
