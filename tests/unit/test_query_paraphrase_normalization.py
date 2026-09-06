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
    _business_profile_residual,
    build_query_plan,
)
from disclosure_agent.agent.query_vocabulary import (
    OPERATION_AGGREGATE,
    find_contract_fields,
    find_product_phrase_span,
    find_year_spans,
    find_years,
    matches_product_phrase,
    normalize_aggregate_request,
    normalize_comparison_request,
    normalize_scale_request,
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


def test_unnamed_quarter_report_is_still_out_of_scope() -> None:
    """
    연도를 읽게 되었다고 해서 지원하지 않는 기간 표기가 함께 열리지는 않는다.

    기간 규칙(_resolve_periodic_report_period)이 의도 분기보다 앞에서 그대로 거절한다.
    어휘를 넓히는 변경이 지원 범위를 함께 넓히지 않는다는 것을 여기서 고정한다.

    "분기보고서"는 몇 분기인지 말하지 않은 표기다. 1분기와 3분기 중 어느 쪽인지 정해지지
    않으므로 읽지 않는다. 읽는 것은 "1분기보고서"·"3분기보고서"처럼 분기를 밝힌 표기뿐이다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트반도체 2025 분기보고서 매출 얼마야?")

    assert error.value.reason == "unsupported_period"


def test_half_year_report_year_is_read_without_the_year_suffix() -> None:
    """
    "2025 반기보고서"는 연도도 보고 기간도 읽힌다.

    이 어휘 규칙(REPORT_CONTEXT_YEAR_PATTERN)이 '년' 없는 네 자리 숫자를 연도로 읽어 주는
    자리가 보고서 종류 바로 앞이고, 반기보고서가 그 자리에 있다. 연도만 읽고 기간을 놓치면
    같은 질문이 사업보고서를 가리키게 되므로 두 값을 함께 고정한다.

    바뀌기 전에는 이 질문이 unsupported_period였다. 반기보고서 조회가 합의된 계약에 들어오면서
    열린 경로이며, 값을 실제로 읽는 일은 추출 계층이 맡는다.
    """
    plan = plan_for("테스트반도체 2025 반기보고서 매출 얼마야?")

    assert plan.years == (2025,)
    assert plan.document_subtype == "half"
    assert plan.fiscal_base_month == 6


def test_amount_shaped_number_still_fails_closed_at_the_plan() -> None:
    """연도로 읽지 않은 숫자는 계획에도 기간을 만들지 않는다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트반도체 매출이 2025억원 맞아?")

    assert error.value.reason == "missing_year"


@pytest.mark.parametrize(
    "question",
    [
        "2025 사업보고서",
        "2025년 사업보고서",
        "2024년 매출과 2026년 상반기",
        "테스트반도체 매출이 2025억원 맞아?",
        "계약번호 20250916 계약금액",
        "테스트반도체 2025 매출 알려줘",
    ],
)
def test_year_spans_agree_with_the_years_they_are_reduced_to(question: str) -> None:
    """
    자리를 함께 돌려주는 헬퍼와 값만 돌려주는 헬퍼가 같은 것을 연도로 읽는다.

    find_year_spans는 안전 판정(agent.safety)이 하위 기간을 붙이려고 쓰고, find_years는
    질의 해석이 기간을 세려고 쓴다. 둘이 갈라지면 같은 질문이 두 계층에서 다른 기간을
    갖게 되며, 그 차이는 결과의 형식으로 드러나지 않는다.
    """
    spans = find_year_spans(question)

    assert tuple(dict.fromkeys(year for _, _, year in spans)) == find_years(question)

    # 돌려준 자리는 네 자리 숫자만 가리킨다. 보고서 종류는 자리에 들어가지 않는다.
    for start, end, year in spans:
        assert question[start:end] == str(year)


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
        # 제품이 질문의 머리임을 밝히는 꼬리가 붙은 표기.
        "대표 제품이 뭐야?",
        "주요한 제품이 뭐야?",
        "주력으로 파는 제품은?",
        "주력 제품은 무엇인가요?",
        "핵심 제품이 뭔지 알려줘",
        "주력 제품 알려줘",
        "대표 제품 소개해줘",
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
    "expression",
    [
        # 제품 표현이 다른 속성을 묻는 자리의 수식어로 쓰였다. 질문의 머리는 그 속성이다.
        "대표 제품 출시일 알려줘",
        "핵심 제품 생산량 알려줘",
        "대표 제품 가격 알려줘",
        "주요 제품 점유율 알려줘",
        "주력 제품 원가 알려줘",
        "대표 제품 매출액은 얼마야?",
        "주요 제품별 매출 비중",
        # 공백을 걷어내면 조사와 명사의 첫 글자가 같은 모양이 되는 자리.
        # '제품이름'의 '이'와 '제품가격'의 '가'는 조사가 아니다.
        "대표 제품 이름 알려줘",
        "핵심 제품 가격표 보여줘",
        # '설명'·'소개'는 명사로도 온다. 어간만 보면 요청 동사로 읽힌다.
        "대표 제품 설명서 가격 알려줘",
        "주력 제품 소개서 어디 있어",
        # 제품이 다른 것을 묻는 문장의 목적어다.
        "어떤 사업부가 주력 제품을 담당해",
    ],
)
def test_product_phrase_modifying_another_attribute_is_not_a_product_request(
    expression: str,
) -> None:
    """
    제품이라는 말이 나오는 것과 제품을 묻는 것은 다르다.

    가르지 않으면 다른 것을 물은 질문이 기업 소개 요약으로 들어가고, 요약은 사업 개요와
    주요 제품을 실어 돌려준다. 형식이 맞는 답이 나가므로 사용자가 물은 속성이 빠졌다는
    사실은 결과 어디에도 남지 않는다.
    """
    assert not matches_product_phrase(expression)


@pytest.mark.parametrize(
    "question",
    [
        "테스트반도체는 무슨 사업을 하고 어떤 제품이 주력이야?",
        "테스트반도체는 무슨 사업을 하고 주력으로 파는 제품이 뭐야?",
        "테스트반도체는 어떤 사업을 하고 주요한 제품이 뭐야?",
        "테스트반도체는 무슨 사업을 하고 무슨 제품을 만들어?",
        "테스트반도체는 어떤 사업을 하고 대표 제품이 뭐야?",
        "테스트반도체는 어떤 사업을 하고 주요한 제품이 뭐야?",
        "테스트반도체는 무슨 사업을 하고 핵심 제품이 뭔지 알려줘",
        "테스트반도체는 어떤 사업을 하고 주력 제품 알려줘",
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


@pytest.mark.parametrize(
    "question",
    [
        # 사업 서술과 제품 표현이 둘 다 있지만, 제품은 다른 속성을 묻는 자리의 수식어다.
        "테스트반도체는 무슨 사업을 하고 대표 제품 출시일도 알려줘?",
        "테스트반도체는 어떤 사업을 하고 핵심 제품 생산량 알려줘",
        "테스트반도체는 무슨 사업을 하고 대표 제품 가격 알려줘",
        "테스트반도체는 어떤 사업을 하고 주력 제품 원가 알려줘",
        "테스트반도체는 무슨 사업을 하고 대표 제품 이름 알려줘",
    ],
)
def test_product_attribute_requests_are_not_narrowed_into_the_summary_path(
    question: str,
) -> None:
    """
    제품을 수식어로 쓴 속성 요청은 기업 소개 요약으로 축소되지 않는다.

    축소되면 요약이 사업 개요와 주요 제품을 실어 돌아오고, 사용자가 물은 '출시일'·'생산량'은
    답에서 사라진다. 답할 수 없다는 사실조차 남지 않는 조용한 축소다.

    막는 자리를 요약 경로 앞에 두므로, 이 질문들은 이 경로가 생기기 전과 같은 경로로 흘러가
    그쪽 규칙으로 막힌다. 지원하지 않는 속성을 위한 새 의도를 여기서 만들지 않는다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for(question)

    assert error.value.reason == "missing_year"


def test_product_attribute_request_stays_blocked_even_with_a_year() -> None:
    """
    기간을 밝혀도 요약 경로가 열리지 않는다.

    위 회귀가 보는 missing_year는 기간이 없어서 걸린 사유다. 그것만 고정해 두면 기간을
    적은 같은 질문이 조용히 요약으로 들어가도 알아채지 못한다. 기간을 채운 질문은 요청한
    속성('출시일')을 지원하지 않는다는 사유로 막혀야 하며, 그 사유가 실제로 일어난 일이다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for("테스트반도체는 2025년에 무슨 사업을 하고 대표 제품 출시일 알려줘")

    assert error.value.reason == "unsupported_metric"


def test_product_identity_request_with_a_year_still_opens_the_summary_path() -> None:
    """가른 경계가 기간을 밝힌 제품 자체 질문까지 막지는 않는다."""
    plan = plan_for("테스트반도체는 2025년에 무슨 사업을 하고 대표 제품이 뭐야?")

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.section_scopes == (SectionScope.BUSINESS_OVERVIEW, SectionScope.PRODUCTS)
    assert plan.years == (2025,)


# --------------------------------------------------------------------------
# 3-1. 제품 요청 뒤에 남은 요청
# --------------------------------------------------------------------------
#
# 제품 표현이 질문의 머리인지만 보면 "대표 제품이 뭐고 출시일도 알려줘"가 통과한다. 앞 반은
# 이 경로가 답하는 질문이 맞고, 뒤에 붙은 요청은 답하지 않는데 결과는 정상적인 요약이다.
# 그래서 머리인지가 아니라 전부인지를 본다.


@pytest.mark.parametrize(
    ("question", "surviving"),
    [
        # 어순 1: 수식어가 앞. 꼬리는 만족하지만 뒤에 다른 요청이 이어진다.
        ("테스트반도체는 무슨 사업을 하고 대표 제품이 뭐고 출시일도 알려줘", "출시일"),
        ("테스트반도체는 어떤 사업을 하고 대표 제품 알려주고 생산량도 알려줘", "생산량"),
        ("테스트반도체는 무슨 사업을 하고 대표 제품이 뭔지랑 가격도 알려줘", "가격"),
        ("테스트반도체는 어떤 사업을 하고 핵심 제품은 무엇이고 원가는 어때?", "원가"),
        # 어순 3: 수식어가 뒤.
        ("테스트반도체는 무슨 사업을 하고 어떤 제품이 주력인지와 출시일 알려줘", "출시일"),
        # 어순 4: 만드는 것.
        (
            "테스트반도체는 어떤 사업을 하고 무슨 제품을 만들어서 파는지랑 가격도 알려줘",
            "가격",
        ),
        (
            "테스트반도체는 어떤 사업을 하고 무슨 제품을 생산하는지와 생산량도 알려줘",
            "생산량",
        ),
    ],
)
def test_conjoined_attribute_request_is_not_narrowed_into_the_summary_path(
    question: str,
    surviving: str,
) -> None:
    """
    제품 요청 뒤에 다른 요청이 이어지면 요약 경로를 열지 않는다.

    네 어순을 모두 본다. 꼬리 검사는 '제품' 바로 뒤만 보므로 어순 1·2에서도 이 모양이
    통과했다. 어순 3·4는 꼬리를 요구하지 않아 더 넓게 통과했다. 남은 요청을 세는 자리는
    어순과 무관하므로 넷을 같은 규칙으로 막는다.

    남은 낱말을 함께 고정한다. 무엇이 남아서 막혔는지가 회귀에 적혀 있지 않으면, 나중에
    이 검사가 다른 이유로 통과할 때(예: 제품 표현을 아예 못 읽어서) 그것을 알아채지 못한다.
    """
    assert surviving in _business_profile_residual(question, COMPANIES)

    with pytest.raises(UnsupportedQuestionError) as error:
        plan_for(question)

    assert error.value.reason == "missing_year"


def test_business_profile_residual_is_empty_for_a_pure_profile_question() -> None:
    """
    답하는 질문에서는 남는 말이 없다.

    기업명·기간·조사·어미·요청 표현은 이 경로가 답하지 않고도 남을 수 있는 말이므로 남은
    요청으로 세지 않는다. 그것들이 남는 것으로 세어지면 정상 Q05가 전부 막힌다.
    """
    for question in (
        "테스트반도체는 무슨 사업을 하고 대표 제품이 뭐야?",
        "테스트반도체는 2024년에 어떤 사업을 하고 주요한 제품이 뭐야?",
        "테스트반도체는 작년에 무슨 사업을 하고 주력 제품 알려줘",
        "테스트반도체는 어떤 사업을 하고 무슨 제품을 만들어?",
    ):
        assert _business_profile_residual(question, COMPANIES) == "", question


def test_product_phrase_span_covers_only_the_product_request() -> None:
    """
    구간이 제품 요청만 가져간다.

    구간이 넓으면 뒤에 이어진 요청까지 함께 걷혀 남은 것이 없다고 세어지고, 좁으면 정상
    질문에서 제품 표현 조각이 남은 요청으로 세어진다.
    """
    compacted = "".join("테스트반도체는 무슨 사업을 하고 대표 제품이 뭐고 출시일도 알려줘".split())

    span = find_product_phrase_span(compacted)

    assert span is not None
    assert compacted[span[0] : span[1]] == "대표제품이뭐"


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


# --------------------------------------------------------------------------
# 4b. '규모'는 집계 어휘와 나뉘어 있고, 부르는 자리에서만 집계로 읽힌다
#
# 사용자가 여러 건을 더해 달라고 말하는 방식이 '합치다' 계열만은 아니다. "시설투자 규모가
# 더 큰 기업"은 그 해의 시설투자 공시를 전부 더해 견주어 달라는 말이며, 정본 Q12의 "다
# 합치면"과 같은 요청이다.
#
# 그런데 '규모'는 값 하나의 크기를 부르는 자리에도 쓰인다("매출 규모 알려줘"). 그래서 이
# 낱말을 AGGREGATE_REQUEST_PATTERN에 넣지 않고 따로 두었고, 읽을지 말지는 부르는 쪽이
# 정한다. 아래 두 갈래가 그 분업을 고정한다.
#
#   어휘   normalize_scale_request는 '규모'를 집계로 옮긴다
#   자리   normalize_aggregate_request는 그것을 집계로 읽지 않는다
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expression",
    ["시설투자 규모", "투자 규모가 더 큰", "규모"],
)
def test_scale_wording_normalizes_to_the_canonical_aggregate_operation(
    expression: str,
) -> None:
    assert normalize_scale_request(expression) == OPERATION_AGGREGATE


@pytest.mark.parametrize(
    "expression",
    ["시설투자 규모", "매출 규모 알려줘", "계약 규모가 얼마야"],
)
def test_scale_wording_stays_out_of_the_general_aggregate_vocabulary(
    expression: str,
) -> None:
    """'규모'를 넣은 것이 전역 집계 어휘를 넓힌 것은 아니다.

    넓혔다면 값 하나를 물은 질문("매출 규모 알려줘")이 전부 집계 요청이 되고, 형식이 맞는
    답이 나가므로 틀렸다는 사실이 드러나지 않는다.
    """
    assert normalize_aggregate_request(expression) is None


@pytest.mark.parametrize(
    "facility_wording",
    ["시설투자", "설비투자", "설비 투자"],
)
def test_scale_wording_reaches_the_facility_aggregate_plan(
    facility_wording: str,
) -> None:
    """'규모'로 말한 시설투자 비교가 정본 Q12와 같은 연산·지표를 갖는다.

    시설투자를 부르는 세 표기를 함께 본다. 같은 공시를 가리키는 표기가 낱말 하나 차이로
    갈리면, 막힌 질문은 답할 수 없어서가 아니라 낱말을 못 알아봐서 막힌 것이 된다.
    """
    plan = build_query_plan(
        f"LG이노텍과 레인보우로보틱스 중 2024년 {facility_wording} 규모가 "
        "더 큰 기업은 어디야?",
        known_companies=AGGREGATE_COMPANIES,
    )

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.operations == (OPERATION_AGGREGATE, OPERATION_COMPARE)
    assert plan.metric is MetricConcept.INVESTMENT_AMOUNT


@pytest.mark.parametrize(
    "question",
    [
        # 유형 신호가 없다. '규모'만으로는 무엇을 더하는지가 계획에 남지 않는다.
        "2024년에 LG이노텍하고 레인보우로보틱스 매출 규모가 더 큰 기업은 어디야?",
        # 비교 신호가 없다.
        "2024년에 LG이노텍하고 레인보우로보틱스가 발표한 시설투자 규모를 알려줘.",
    ],
)
def test_scale_wording_alone_does_not_open_the_facility_aggregate_path(
    question: str,
) -> None:
    """'규모'를 읽는 자리는 시설투자 집계 하나뿐이며, 나머지 요구는 그대로다."""
    plan = build_query_plan(question, known_companies=AGGREGATE_COMPANIES)

    assert plan.operations == ()


def test_single_company_scale_question_is_not_read_as_an_aggregate() -> None:
    """문서 하나의 값을 '규모'로 물은 질문은 집계가 되지 않는다.

    이 낱말을 전역 집계 어휘에 넣었다면 이 질문이 집계 요청으로 읽혔을 자리다.
    """
    plan = build_query_plan(
        "테스트반도체 2025년 매출 규모 알려줘.",
        known_companies=COMPANIES,
    )

    assert plan.operations == ()
    assert plan.metric is MetricConcept.REVENUE


# --------------------------------------------------------------------------
# 5. 생산설비 표기와 문서 내용 요청 표현
# --------------------------------------------------------------------------
#
# 사업보고서에서 이 범위를 담은 Section의 실제 제목이 "3. 원재료 및 생산설비"이며, 사용자가
# 가장 자연스럽게 쓰는 낱말도 그 제목의 낱말이다. 그리고 그 절을 요청하는 말은 요약·정리
# 만이 아니다. 두 어휘 중 하나만 넓히면 나머지 하나 때문에 같은 질문이 여전히 막힌다.


@pytest.mark.parametrize(
    "facility_expression",
    [
        "생산설비",
        "생산 설비",
        "생산시설",
        "생산 시설",
        "제조시설",
        "주요 생산설비",
        "생산설비 현황",
        "설비 투자",
        "시설투자",
    ],
)
def test_production_facility_wordings_reach_the_facility_section_scope(
    facility_expression: str,
) -> None:
    """
    생산 관련 시설을 가리키는 표기가 같은 범위로 옮겨진다.

    "생산 설비"는 공백을 걷어내면 설비에 걸리지만 "생산 시설"은 어디에도 걸리지 않았다.
    같은 것을 가리키는 두 표기가 표기 하나 차이로 갈리면, 막힌 질문은 답할 수 없어서가
    아니라 낱말을 못 알아봐서 막힌 것이 된다.
    """
    plan = build_query_plan(
        f"테스트바이오 2025년 사업보고서에서 {facility_expression} 관련 내용 정리해줘.",
        known_companies=COMPANIES,
    )

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.section_scope is SectionScope.FACILITY_INVESTMENT
    assert plan.years == (2025,)


@pytest.mark.parametrize(
    "request_expression",
    [
        "관련 내용 정리해줘",
        "관련 내용 요약해줘",
        "관련해서 어떤 내용이 담겨 있어?",
        "관련해서 무슨 내용이 있어?",
        "관련 내용이 뭐야?",
        "관련 내용 알려줘",
        "관련 내용 설명해줘",
    ],
)
def test_document_content_requests_reach_the_summary_plan(request_expression: str) -> None:
    """
    문서에 무엇이 적혀 있는지를 묻는 표현이 요약 요청으로 읽힌다.

    요약·정리만 알아보면 보고서 종류와 문서 내 범위를 모두 밝힌 질문이 단일 사실 조회로
    내려가 "지원하는 지표(매출액)를 찾을 수 없습니다"로 막힌다. 사유가 실제로 일어난 일과
    다르다.
    """
    plan = build_query_plan(
        f"테스트바이오 2025년 사업보고서에 생산설비 {request_expression}",
        known_companies=COMPANIES,
    )

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.section_scope is SectionScope.FACILITY_INVESTMENT


@pytest.mark.parametrize(
    "question",
    [
        # 범위가 없다. 문서 전체 요약으로 열지 않는다.
        "테스트반도체 2025년 사업보고서 매출액 알려줘",
        "테스트반도체 2025년 사업보고서에 어떤 내용이 담겨 있어?",
        # 보고서 종류가 없다.
        "테스트반도체 2025년 설비 내용 알려줘",
    ],
)
def test_content_request_alone_does_not_open_the_summary_path(question: str) -> None:
    """
    내용 요청 표현 하나로는 요약 경로가 열리지 않는다.

    보고서 종류와 문서 내 범위를 함께 요구하는 것은 넓히기 전과 같다. 값 조회 질문이 요약
    경로로 끌려오면 돌아오는 것은 물어본 값이 아니라 그 절의 요약이고, 형식이 맞는 답이
    나가므로 값이 빠졌다는 사실이 드러나지 않는다.
    """
    try:
        plan = build_query_plan(question, known_companies=COMPANIES)
    except UnsupportedQuestionError as error:
        assert error.reason in {"unsupported_metric", "missing_year"}
        return

    assert plan.intent != INTENT_DOCUMENT_SUMMARY


def test_value_request_with_a_scope_word_is_not_taken_by_the_content_request() -> None:
    """
    범위 낱말이 있어도 값 이름이 요청의 머리면 요약이 아니다.

    알려·설명을 낱말 하나로 요약 요청에 넣지 않은 이유가 여기 있다. 넣었다면 이 질문이
    설비 절의 요약으로 돌아갔을 것이다.
    """
    plan = build_query_plan(
        "테스트반도체 2025년 사업보고서에서 설비 관련 매출 알려줘",
        known_companies=COMPANIES,
    )

    assert plan.intent == INTENT_SINGLE_FACT
    assert plan.metric == "revenue"


# --------------------------------------------------------------------------
# 6. 사업 서술 표현
# --------------------------------------------------------------------------
#
# "무슨 사업을 해"·"영위하는 사업이 뭐야"·"주요 사업이 뭐야"는 모두 같은 요청이다. 표기
# 하나만 알아보면 같은 질문이 표기에 따라 사업 개요 요약에 들어가기도 하고 빠지기도 한다.


@pytest.mark.parametrize(
    "expression",
    [
        "무슨 사업을 해?",
        "어떤 사업을 하는지 알려줘",
        "영위하는 사업이 뭐야?",
        "영위하는 사업은?",
        "영위하고 있는 사업 알려줘",
        "어떤 사업을 영위하고 있어?",
        "무슨 사업을 영위하나요?",
        "영위 중인 사업 설명해줘",
        "주요 사업이 뭐야?",
        "핵심 사업은 무엇인가요?",
        "주력 사업이 뭔지 알려줘",
    ],
)
def test_business_description_wordings_reach_the_business_overview_scope(
    expression: str,
) -> None:
    plan = build_query_plan(
        f"테스트반도체는 {expression}",
        known_companies=COMPANIES,
        fiscal_year_anchors={"테스트반도체": 2025},
    )

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.section_scopes == (SectionScope.BUSINESS_OVERVIEW,)
    assert plan.years == (2025,)


@pytest.mark.parametrize(
    "question",
    [
        # 사업부는 사업 서술이 아니다. 조사를 요구해 걸러 낸다.
        "테스트반도체는 어떤 사업부가 대표 제품을 담당해?",
        "테스트반도체 주요 사업부는 어디야?",
        # 값 조회가 섞였다.
        "테스트반도체 주요 사업 매출이 얼마야?",
        "테스트반도체가 영위하는 사업의 영업이익률 알려줘",
        # 답하지 않을 요청이 함께 왔다.
        "테스트반도체가 영위하는 사업의 상장 시점 알려줘",
        # 문서 지목의 사업보고서는 사업 서술이 아니다.
        "테스트반도체 2025년 사업보고서 매출액 알려줘",
    ],
)
def test_business_description_lookalikes_do_not_open_the_overview_scope(
    question: str,
) -> None:
    try:
        plan = build_query_plan(
            question,
            known_companies=COMPANIES,
            fiscal_year_anchors={"테스트반도체": 2025},
        )
    except UnsupportedQuestionError:
        return

    assert plan.section_scopes == ()


def test_business_and_product_together_still_summarize_both_scopes() -> None:
    """두 반을 함께 물으면 범위도 둘이다. 넓히기 전과 같다."""
    plan = build_query_plan(
        "테스트반도체는 무슨 사업을 하고 주력 제품이 뭐야?",
        known_companies=COMPANIES,
        fiscal_year_anchors={"테스트반도체": 2025},
    )

    assert plan.section_scopes == (SectionScope.BUSINESS_OVERVIEW, SectionScope.PRODUCTS)


# --------------------------------------------------------------------------
# 7. 비교 표현
# --------------------------------------------------------------------------
#
# "비교하면"·"견주면"·"더 크지"는 모두 같은 요청이다. 표기 하나만 알아보면 집계·비교 역량이
# 있는데도 표현 게이트에서 막힌다.


@pytest.mark.parametrize(
    "expression",
    [
        "비교",
        "비교해줘",
        "비교하면",
        "견주면",
        "견줘줘",
        "견주어",
        "견준다",
        "더 커",
        "더 큰",
        "더 크지",
        "더 클까",
        "더 높은",
        "더커",
    ],
)
def test_comparison_paraphrases_normalize_to_the_canonical_operation(
    expression: str,
) -> None:
    assert normalize_comparison_request(expression) == OPERATION_COMPARE


@pytest.mark.parametrize(
    "expression",
    [
        # 기간 표현이다. 비교로 읽으면 시계열 질문이 기업 간 비교 신호를 갖는다.
        "전년 대비 증가율",
        "작년 대비 어때",
        # 더가 크기 어간과 붙어 있지 않다.
        "전부 더 하락했어",
        "더 알려줘",
        # 용언을 꾸미는 부사형. 크기를 견주는 말이 아니라 시간축의 증가를 묻는 말이다.
        "더 많이",
        "더 많이 늘었어",
        "작년보다 더 많이 늘었어",
        "더 크게",
        "더 크게 늘었어",
        # 비교가 아닌 서술.
        "투자 규모가 커",
        "매출이 높아",
    ],
)
def test_unrelated_wording_is_not_read_as_a_comparison_request(expression: str) -> None:
    assert normalize_comparison_request(expression) is None


@pytest.mark.parametrize(
    "comparison_expression",
    [
        "어디가 더 커?",
        "어디가 더 크지?",
        "어느 쪽이 더 큰지 알려줘",
        "견주면 어디가 더 커?",
        "비교해줘",
        "견줘줘",
    ],
)
def test_comparison_paraphrases_reach_the_facility_aggregate_plan(
    comparison_expression: str,
) -> None:
    """
    Q12 계열이 비교 표기 하나에 매이지 않는다.

    비교 신호만으로 이 경로가 열리지는 않는다. 시설투자 유형과 집계 표현이 함께 있어야
    하며, 그 요구는 넓히기 전과 같다.
    """
    question = (
        "2024년에 LG이노텍하고 레인보우로보틱스가 각각 공시한 시설투자 금액을 "
        f"합쳐서 {comparison_expression}"
    )

    plan = build_query_plan(question, known_companies=AGGREGATE_COMPANIES)

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.operations == (OPERATION_AGGREGATE, OPERATION_COMPARE)
    assert plan.metric is MetricConcept.INVESTMENT_AMOUNT


def test_aggregate_without_a_comparison_request_does_not_gain_operations() -> None:
    """
    집계만 요청한 질문에 비교를 지어내지 않는다.

    비교 어휘를 넓힌 뒤에도 이 방향이 지켜져야 한다. 지어내면 묻지 않은 우열 판정이 근거를
    갖춘 채로 나간다.
    """
    plan = build_query_plan(
        "2024년에 LG이노텍하고 레인보우로보틱스가 새로 발표한 시설투자, 회사별로 다 합쳐줘",
        known_companies=AGGREGATE_COMPANIES,
    )

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.operations == ()


def test_comparison_of_individual_values_does_not_gain_an_aggregate() -> None:
    """
    개별 값을 견주라는 질문에 집계를 지어내지 않는다.

    두 신호를 함께 요구하는 것이 이 경로의 규약이며, 어느 한쪽 어휘를 넓혀도 그 요구는
    그대로다.
    """
    plan = build_query_plan(
        "2024년에 LG이노텍하고 레인보우로보틱스가 각각 공시한 시설투자 금액을 "
        "견주면 어디가 더 커?",
        known_companies=AGGREGATE_COMPANIES,
    )

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.operations == ()


# --------------------------------------------------------------------------
# 8. 내용 요청 표현이 값 조회를 가로채지 않는다
# --------------------------------------------------------------------------
#
# 내용은 값 이름 바로 뒤에도 붙는다("매출 내용 알려줘"). 보고서 종류와 범위 낱말까지 갖춘
# 질문에서 그 표현을 요약 요청으로 읽으면, 값으로 답할 수 있던 질문이 그 절의 요약으로
# 돌아가고 물어본 값은 결과 어디에도 남지 않는다.


@pytest.mark.parametrize(
    ("question", "expected_metric"),
    [
        ("테스트반도체 2025년 사업보고서에서 설비 관련 매출 내용 알려줘", "revenue"),
        ("테스트반도체 2025년 사업보고서에서 설비 관련 매출 내용이 뭐야?", "revenue"),
        ("테스트반도체 2025년 사업보고서에서 설비 관련 매출 내용 설명해줘", "revenue"),
    ],
)
def test_value_request_with_a_content_word_stays_a_value_request(
    question: str,
    expected_metric: str,
) -> None:
    """지표 이름이 함께 있으면 내용이 있어도 요약이 아니다."""
    plan = build_query_plan(question, known_companies=COMPANIES)

    assert plan.intent == INTENT_SINGLE_FACT
    assert plan.metric == expected_metric
    assert plan.section_scope is None


@pytest.mark.parametrize(
    "question",
    [
        "테스트반도체 2025년 사업보고서에서 설비 관련 영업이익 내용 알려줘",
        "테스트반도체 2025년 사업보고서에서 설비 투자 금액 내용이 뭐야?",
        "테스트반도체 2025년 사업보고서에서 설비 관련 영업이익률 내용 알려줘",
    ],
)
def test_unsupported_value_request_with_a_content_word_stays_fail_closed(
    question: str,
) -> None:
    """
    지원하지 않는 지표를 물은 질문은 계속 막힌다.

    요약으로 받으면 fail-closed가 묻지 않은 것에 답함으로 바뀐다. 형식이 맞는 요약이
    나가므로 물어본 지표가 빠졌다는 사실이 드러나지 않는다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(question, known_companies=COMPANIES)

    assert error.value.reason == "unsupported_metric"


def test_content_request_without_a_value_word_still_opens_the_summary() -> None:
    """값 이름이 없으면 내용 요청은 그대로 요약이다. BB05가 이 자리다."""
    plan = build_query_plan(
        "테스트바이오 2025년 사업보고서에 생산설비 관련해서 어떤 내용이 담겨 있어?",
        known_companies=COMPANIES,
    )

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.section_scope is SectionScope.FACILITY_INVESTMENT


def test_canonical_summary_tokens_keep_their_previous_behaviour() -> None:
    """
    요약·정리 경로는 값 배제를 거치지 않는다.

    "설비 관련 매출 정리해줘"가 요약으로 읽히는 것은 이 어휘를 넓히기 전부터의 동작이며,
    여기서 함께 고치지 않는다. 이 테스트는 그 동작이 이번 변경으로 바뀌지 않았다는 것을
    고정한다. 바람직한 동작을 고정하는 것이 아니라 변경 범위를 고정하는 것이다.
    """
    plan = build_query_plan(
        "테스트반도체 2025년 사업보고서에서 설비 관련 매출 정리해줘",
        known_companies=COMPANIES,
    )

    assert plan.intent == INTENT_DOCUMENT_SUMMARY


# --------------------------------------------------------------------------
# 9. 시간축 증가 표현이 기업 간 집계 비교를 만들지 않는다
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        # 집계 표현이 함께 있어도 더 많이는 비교 요청이 아니다.
        "2024년에 LG이노텍하고 레인보우로보틱스 시설투자를 합쳐서 작년보다 더 많이 늘었어?",
        "2024년에 LG이노텍하고 레인보우로보틱스 시설투자, 작년보다 더 많이 합쳐서 늘었어?",
        "2024년에 LG이노텍하고 레인보우로보틱스 시설투자를 합쳐서 작년보다 더 크게 늘었어?",
    ],
)
def test_temporal_growth_wording_does_not_create_a_cross_entity_comparison(
    question: str,
) -> None:
    """
    더 많이·더 크게는 용언을 꾸미는 말이다.

    이 질문이 묻는 것은 시간축의 증가이고 기업 간 우열이 아니다. 비교로 읽으면 집계 표현이
    함께 있는 순간 묻지 않은 우열 판정이 근거를 갖춘 채로 나간다.
    """
    plan = build_query_plan(question, known_companies=AGGREGATE_COMPANIES)

    assert plan.operations == ()
    assert plan.metric is not MetricConcept.INVESTMENT_AMOUNT
