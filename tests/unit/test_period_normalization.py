"""정기공시 보고 기간 표현의 정규화와 되묻기를 검증한다.

무엇을 고정하는가:
    질문이 이름으로 지목한 보고 기간이 QueryPlan의 세부유형·base_month로 손실 없이
    옮겨지는 것, 그리고 하나의 기간으로 옮길 수 없는 표현이 추측 대신 되묻기로 막히는 것.

    바뀌기 전에는 '분기'·'반기'라는 글자가 보이기만 하면 전부 같은 unsupported_period였다.
    "2025년 1분기보고서"와 "2025년 3분기보고서"와 "2025년 상반기"가 결과에서 구분되지 않았고,
    무엇을 지원하지 않는지도 사유에 남지 않았다.

무엇을 검증하지 않는가:
    추출한 재무 수치는 보지 않는다. 분기·반기보고서에서 값을 읽는 일은 Team1의 추출 계층
    작업이며, 이 파일이 고정하는 것은 '질의 해석이 어느 문서를 가리켰는가'까지다.

    그래서 여기의 어떤 테스트도 코퍼스를 열지 않는다. known_companies만 주고 계획을 만든다.

'하반기'를 막는 이유:
    정기공시에는 하반기를 보고하는 서식이 없다. 3분기보고서는 7~9월이고 사업보고서는 한 해
    전체다. 어느 쪽으로 옮겨도 사용자가 말하지 않은 기간을 대신 고른 것이 되고, 그 답은
    근거를 갖춘 채로 질문과 다른 기간을 말한다. 추측보다 되묻기가 먼저다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.query_analyzer import (
    AMBIGUOUS_PERIOD_REASON,
    CONFLICTING_PERIOD_REASON,
    DOCUMENT_SUBTYPE_ANNUAL,
    DOCUMENT_SUBTYPE_HALF,
    DOCUMENT_SUBTYPE_QUARTER,
    FISCAL_BASE_MONTH_ANNUAL,
    FISCAL_BASE_MONTH_FIRST_QUARTER,
    FISCAL_BASE_MONTH_HALF,
    FISCAL_BASE_MONTH_THIRD_QUARTER,
    INTENT_DOCUMENT_SUMMARY,
    SUPPORTED_PERIODIC_REPORT_CHOICES,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.common.enums import Intent, MetricConcept

REAL_COMPANIES = ("삼성전자", "한미반도체")


def plan_for(question: str):
    """이 파일이 쓰는 유일한 계획 생성 경로."""
    return build_query_plan(question, known_companies=REAL_COMPANIES)


def blocked_by(question: str) -> UnsupportedQuestionError:
    """계획이 만들어지지 않는 질문의 정지 사유를 돌려준다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(question, known_companies=REAL_COMPANIES)

    return error.value


# --------------------------------------------------------------------------
# 1. 명시한 보고 기간
# --------------------------------------------------------------------------


def test_explicit_first_quarter_report() -> None:
    """
    1분기보고서는 quarter + base_month 3이다.

    base_month를 함께 보는 이유:
        manifest는 1분기와 3분기를 doc_subtype으로 가르지 않는다. 둘 다 "quarter"이므로
        세부유형만 검증하면 3분기보고서 계획과 구분되지 않고, 질문이 지목한 분기가 사라져도
        이 테스트는 통과한다.
    """
    plan = plan_for("삼성전자 2025년 1분기보고서 매출액 알려줘")

    assert plan.years == (2025,)
    assert plan.document_subtype == DOCUMENT_SUBTYPE_QUARTER
    assert plan.fiscal_base_month == FISCAL_BASE_MONTH_FIRST_QUARTER


def test_explicit_half_year_report() -> None:
    """반기보고서는 half + base_month 6이다."""
    plan = plan_for("삼성전자 2025년 반기보고서 매출액 알려줘")

    assert plan.years == (2025,)
    assert plan.document_subtype == DOCUMENT_SUBTYPE_HALF
    assert plan.fiscal_base_month == FISCAL_BASE_MONTH_HALF


def test_explicit_third_quarter_report() -> None:
    """3분기보고서는 quarter + base_month 9다. 1분기와 갈리는 자리가 여기다."""
    plan = plan_for("삼성전자 2025년 3분기보고서 매출액 알려줘")

    assert plan.years == (2025,)
    assert plan.document_subtype == DOCUMENT_SUBTYPE_QUARTER
    assert plan.fiscal_base_month == FISCAL_BASE_MONTH_THIRD_QUARTER


def test_first_and_third_quarter_reports_are_not_the_same_plan() -> None:
    """
    두 분기보고서가 같은 계획으로 무너지지 않는다.

    위 두 테스트가 각각 통과해도 이 성질은 따로 고정한다. 세부유형이 같은 값이므로,
    base_month를 계획에서 떨어뜨리는 변경이 들어오면 두 질문이 조용히 한 문서를 가리킨다.
    """
    first = plan_for("삼성전자 2025년 1분기보고서 매출액 알려줘")
    third = plan_for("삼성전자 2025년 3분기보고서 매출액 알려줘")

    assert first.document_subtype == third.document_subtype
    assert first.fiscal_base_month != third.fiscal_base_month


# --------------------------------------------------------------------------
# 2. 상반기 별칭
# --------------------------------------------------------------------------


def test_upper_half_normalizes_to_half_year_report() -> None:
    """'상반기'는 반기보고서로 옮긴다. 옮길 곳이 하나뿐이므로 추측이 아니다."""
    plan = plan_for("삼성전자 2025년 상반기 매출액 알려줘")

    assert plan.years == (2025,)
    assert plan.document_subtype == DOCUMENT_SUBTYPE_HALF
    assert plan.fiscal_base_month == FISCAL_BASE_MONTH_HALF


def test_upper_half_matches_explicit_half_year_report_exactly() -> None:
    """
    나머지가 같은 질문이면 '상반기'와 '반기보고서'의 계획이 기간 축에서 완전히 같다.

    필드를 하나씩 비교하는 이유:
        question 문자열은 서로 다르므로 계획 전체를 비교할 수 없다. 별칭이 보장하는 것은
        기간 해석과 그에 딸린 조회 조건이 같다는 것이고, 그 범위를 여기에 적어 둔다.
    """
    alias = plan_for("삼성전자 2025년 상반기 매출액 알려줘")
    explicit = plan_for("삼성전자 2025년 반기보고서 매출액 알려줘")

    assert alias.years == explicit.years
    assert alias.document_subtype == explicit.document_subtype
    assert alias.fiscal_base_month == explicit.fiscal_base_month
    assert alias.document_class == explicit.document_class
    assert alias.intent == explicit.intent
    assert alias.metric == explicit.metric
    assert alias.accounting_scope == explicit.accounting_scope
    assert alias.accounting_scope_explicit == explicit.accounting_scope_explicit


def test_upper_half_with_explicit_half_year_report_is_not_a_conflict() -> None:
    """
    같은 기간을 두 표기로 말한 질문은 충돌이 아니다.

    "2025년 상반기 반기보고서"는 두 표기가 모두 half를 가리킨다. 표기 수만 세어 막으면
    사용자가 같은 말을 두 번 한 질문이 거절된다.
    """
    plan = plan_for("삼성전자 2025년 상반기 반기보고서 매출액 알려줘")

    assert plan.document_subtype == DOCUMENT_SUBTYPE_HALF
    assert plan.fiscal_base_month == FISCAL_BASE_MONTH_HALF


# --------------------------------------------------------------------------
# 3. 사업보고서 비회귀
# --------------------------------------------------------------------------


def test_annual_report_is_unchanged() -> None:
    """사업보고서는 annual + base_month 12이며, 나머지 축은 지금까지와 같다."""
    plan = plan_for("삼성전자 2025년 사업보고서 매출액 알려줘")

    assert plan.years == (2025,)
    assert plan.document_subtype == DOCUMENT_SUBTYPE_ANNUAL
    assert plan.fiscal_base_month == FISCAL_BASE_MONTH_ANNUAL
    assert plan.intent == Intent.SINGLE_FACT
    assert plan.metric == MetricConcept.REVENUE


@pytest.mark.parametrize(
    "question",
    [
        "삼성전자 2025년 매출액 알려줘",
        "삼성전자 2025년 연간 매출액 알려줘",
    ],
)
def test_year_only_questions_still_mean_annual(question: str) -> None:
    """
    기간을 이름으로 말하지 않은 질문은 지금까지와 같이 사업보고서다.

    보고 기간 해석이 붙었다고 해서 기본값이 바뀌지 않는다는 것을 고정한다. 바뀌면 연도만
    적은 모든 질문이 다른 문서를 가리키게 되고, 그것은 이 변경의 범위가 아니다.

    base_month는 "사업보고서"라고 적은 질문과 같은 값이어야 한다. 같은 문서를 가리키는
    두 질문이 서로 다른 모양의 계획이 되면, 조회하는 쪽이 어느 쪽을 정본으로 볼지 정해야 한다.
    """
    plan = plan_for(question)

    assert plan.years == (2025,)
    assert plan.document_subtype == DOCUMENT_SUBTYPE_ANNUAL
    assert plan.fiscal_base_month == FISCAL_BASE_MONTH_ANNUAL


# --------------------------------------------------------------------------
# 4. 하반기 되묻기
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "삼성전자 2025년 하반기 매출액 알려줘",
        "삼성전자 2025년 하반기 실적 알려줘",
    ],
)
def test_lower_half_is_blocked(question: str) -> None:
    """'하반기'는 계획을 만들지 않는다."""
    error = blocked_by(question)

    assert error.reason == AMBIGUOUS_PERIOD_REASON


def test_lower_half_does_not_guess_a_filing() -> None:
    """
    '하반기'가 3분기보고서로도 사업보고서로도 옮겨지지 않는다.

    사유 코드만 보면 이 성질이 고정되지 않는다. 계획이 만들어지지 않는다는 것까지 확인해야
    '어딘가에서 기간을 추측한 계획이 흘러갔다'를 배제할 수 있다.
    """
    with pytest.raises(UnsupportedQuestionError):
        build_query_plan(
            "삼성전자 2025년 하반기 매출액 알려줘",
            known_companies=REAL_COMPANIES,
        )


def test_lower_half_clarification_lists_every_supported_choice() -> None:
    """
    되묻는 문장이 지원하는 보고 기간 넷을 모두 제시한다.

    사용자가 다음에 무엇이라고 말해야 하는지가 사유에 있어야 되묻기가 성립한다. 목록에서
    하나라도 빠지면 그 보고서는 지원하는데도 선택지에 없다.
    """
    error = blocked_by("삼성전자 2025년 하반기 매출액 알려줘")

    for choice in SUPPORTED_PERIODIC_REPORT_CHOICES:
        assert choice in error.detail

    assert "하반기" in error.detail


def test_lower_half_is_blocked_without_a_year() -> None:
    """연도를 적지 않아도 '하반기' 판정이 먼저다. 되묻는 내용이 연도가 아니기 때문이다."""
    error = blocked_by("삼성전자 하반기 매출액 알려줘")

    assert error.reason == AMBIGUOUS_PERIOD_REASON


# --------------------------------------------------------------------------
# 5. 아직 지원하지 않는 기간 표기
# --------------------------------------------------------------------------


BARE_PERIOD_QUESTIONS = [
    "삼성전자 2025년 1분기 매출액 알려줘",
    "삼성전자 2025년 반기 매출액 알려줘",
    "삼성전자 2025년 3분기 매출액 알려줘",
    "삼성전자 2025년 2분기 매출액 알려줘",
    "삼성전자 2025년 4분기 매출액 알려줘",
    "삼성전자 2025년 q1 매출액 알려줘",
    "삼성전자 2025년 분기보고서 매출액 알려줘",
]


@pytest.mark.parametrize("question", BARE_PERIOD_QUESTIONS)
def test_bare_period_tokens_remain_unsupported(question: str) -> None:
    """
    보고서 이름 없이 쓴 기간 표기는 지금까지와 같이 막힌다.

    자연스러워 보인다는 이유로 별칭을 늘리지 않는다. '2분기'·'4분기'에 대응하는 정기공시가
    없고, '1분기'처럼 대응이 있는 표기도 이번 계약이 정한 것은 보고서 이름으로 지목한
    경우뿐이다. 넓히는 것은 규칙 표에 줄을 더하는 별도 결정이다.
    """
    error = blocked_by(question)

    assert error.reason == "unsupported_period"


@pytest.mark.parametrize("question", BARE_PERIOD_QUESTIONS)
def test_bare_period_block_does_not_claim_the_filings_are_unsupported(
    question: str,
) -> None:
    """
    정지 문구가 '분기·반기 보고서는 지원하지 않는다'고 말하지 않는다.

    그 문장은 예전에는 참이었다. '분기'·'반기'라는 글자가 보이기만 하면 전부 이 자리에서
    막혔기 때문이다. 보고서 이름 해석이 열린 뒤로는 거짓이다 — "2025년 1분기보고서 매출액"은
    지금 답한다. 답하고 있는 조회를 두고 지원하지 않는다고 적으면, 사용자는 표기를 고쳐 다시
    물으면 된다는 것을 알 수 없고 질문 자체를 포기한다.

    사유 코드와 fail-closed는 이 테스트의 관심이 아니다. 그것은 위 테스트가 고정한다. 여기가
    고정하는 것은 사용자가 읽는 문장이 지금도 참인가 하나다.
    """
    detail = blocked_by(question).detail

    assert "분기·반기 보고서 조회는 이번 범위에 포함되지 않습니다" not in detail
    assert "포함되지 않습니다" not in detail


@pytest.mark.parametrize("question", BARE_PERIOD_QUESTIONS)
def test_bare_period_block_tells_the_user_what_to_say_instead(question: str) -> None:
    """
    정지 문구가 지원하는 보고서 이름 넷을 모두 제시한다.

    막기만 하고 다음에 무엇이라고 말해야 하는지를 주지 않으면 되묻기가 성립하지 않는다.
    4절의 '하반기' 되묻기와 6절의 충돌 판정이 같은 목록을 쓰며, 세 자리가 같은 목록을 쓰는
    것이 규약이다. 두 벌로 적으면 지원 보고서가 늘 때 한쪽만 늘어나고, 같은 질문이 어느
    자리에서 막혔느냐에 따라 다른 안내를 받는다.
    """
    detail = blocked_by(question).detail

    for choice in SUPPORTED_PERIODIC_REPORT_CHOICES:
        assert choice in detail


def test_bare_period_block_and_lower_half_offer_the_same_choices() -> None:
    """
    두 되묻기가 같은 선택지를 제시한다.

    목록을 한 곳에서 가져온다는 것을 결과로 확인한다. 어느 한쪽이 문자열을 직접 적기 시작하면
    이 단언이 먼저 깨진다.
    """
    bare = blocked_by("삼성전자 2025년 2분기 매출액 알려줘").detail
    lower_half = blocked_by("삼성전자 2025년 하반기 매출액 알려줘").detail

    for choice in SUPPORTED_PERIODIC_REPORT_CHOICES:
        assert choice in bare
        assert choice in lower_half


# --------------------------------------------------------------------------
# 6. 서로 다른 기간을 함께 지목한 질문
#
# 정책: 보고서 이름이 넓은 기간 표기를 이긴다. 이름끼리는 정해진 바가 없어 막는다.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "document_subtype", "fiscal_base_month"),
    [
        (
            "삼성전자 2025년 하반기 3분기보고서 매출액 알려줘",
            DOCUMENT_SUBTYPE_QUARTER,
            FISCAL_BASE_MONTH_THIRD_QUARTER,
        ),
        (
            "삼성전자 2025년 상반기 사업보고서 매출액 알려줘",
            DOCUMENT_SUBTYPE_ANNUAL,
            FISCAL_BASE_MONTH_ANNUAL,
        ),
        (
            "삼성전자 2025년 상반기 반기보고서 매출액 알려줘",
            DOCUMENT_SUBTYPE_HALF,
            FISCAL_BASE_MONTH_HALF,
        ),
    ],
)
def test_report_name_wins_over_a_broad_period_expression(
    question: str,
    document_subtype: str,
    fiscal_base_month: int,
) -> None:
    """
    보고서 이름이 함께 있으면 그 이름이 기간을 정한다.

    '상반기'·'하반기'는 보고서 이름을 말하지 않았을 때 쓰라고 둔 편의 표기다. 사용자가
    문서를 이름으로 지목했다면 그쪽이 더 분명한 지시이므로, 넓은 표기는 기간을 정하지 않는다.

    "상반기 반기보고서"가 함께 있는 이유:
        이 줄은 우선순위가 적용돼도 결과가 바뀌지 않는 대조군이다. 두 표기가 같은 문서를
        가리키므로, 이름이 이기든 편의 표기가 이기든 half/6이다. 우선순위를 잘못 구현해도
        이 줄만으로는 드러나지 않으며, 위 두 줄이 그것을 잡는다.
    """
    plan = plan_for(question)

    assert plan.years == (2025,)
    assert plan.document_subtype == document_subtype
    assert plan.fiscal_base_month == fiscal_base_month


def test_lower_half_with_a_report_name_does_not_ask_again() -> None:
    """
    '하반기'도 보고서 이름이 함께 있으면 되묻지 않는다.

    되물어야 할 것은 '어느 보고서인가'인데, 그 답이 질문에 이미 적혀 있다. 여기서 되물으면
    사용자가 이미 답한 것을 다시 묻는 셈이다.

    3분기보고서로 추측한 것이 아니다. 질문에 그렇게 적혀 있다.
    """
    plan = plan_for("삼성전자 2025년 하반기 3분기보고서 매출액 알려줘")

    assert plan.document_subtype == DOCUMENT_SUBTYPE_QUARTER
    assert plan.fiscal_base_month == FISCAL_BASE_MONTH_THIRD_QUARTER


def test_lower_half_alone_is_still_blocked() -> None:
    """
    보고서 이름이 없으면 '하반기'는 그대로 되묻기다.

    위 우선순위가 '하반기'를 통과시키는 규칙으로 넓어지지 않는다는 것을 고정한다.
    """
    error = blocked_by("삼성전자 2025년 하반기 매출액 알려줘")

    assert error.reason == AMBIGUOUS_PERIOD_REASON


def test_lower_half_glued_to_a_report_name_is_still_blocked() -> None:
    """
    "하반기보고서"는 보고서 이름을 지목한 것이 아니다.

    '하반기'를 먼저 걷어내지 않으면 남은 '반기보고서'가 이름으로 읽혀, 하반기를 물은 질문이
    반기보고서를 답하게 된다. 걷어내는 순서가 그것을 막는다.
    """
    error = blocked_by("삼성전자 2025년 하반기보고서 매출액 알려줘")

    assert error.reason == AMBIGUOUS_PERIOD_REASON


@pytest.mark.parametrize(
    "question",
    [
        "삼성전자 2025년 1분기보고서 3분기보고서 매출액 알려줘",
        "삼성전자 2025년 반기보고서 사업보고서 매출액 알려줘",
    ],
)
def test_two_conflicting_report_names_fail_closed(question: str) -> None:
    """
    서로 다른 보고서를 이름으로 둘 지목하면 고르지 않는다.

    이름과 넓은 표기 사이에는 우선순위가 있지만, 그 규칙은 이름이 하나일 때를 말한다.
    이름끼리의 우선순위는 정해진 바가 없고, 없는 규칙을 여기서 만들지 않는다.
    """
    error = blocked_by(question)

    assert error.reason == CONFLICTING_PERIOD_REASON


# --------------------------------------------------------------------------
# 7. 연도 표기
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        # 두 자리 연도는 이 저장소가 읽지 않는다. 보고 기간 해석이 그 규칙을 바꾸지 않는다.
        "삼성전자 25년 상반기 매출액 알려줘",
        # '년' 없는 네 자리 숫자는 보고서 종류가 바로 뒤에 붙은 자리에서만 연도로 읽는다
        # (query_vocabulary.REPORT_CONTEXT_YEAR_PATTERN). '상반기'는 그 자리가 아니다.
        "삼성전자 2025 상반기 매출액 알려줘",
        "삼성전자 상반기 매출액 알려줘",
    ],
)
def test_upper_half_without_a_readable_year_reports_missing_year(question: str) -> None:
    """
    연도를 읽지 못하면 기간 해석에 성공해도 missing_year다.

    기간을 읽었다는 이유로 연도를 채워 넣지 않는다. 채우면 실행한 날이나 코퍼스 상태에 따라
    같은 질문이 다른 해를 가리킨다.
    """
    error = blocked_by(question)

    assert error.reason == "missing_year"


def test_year_without_the_year_suffix_works_for_named_reports() -> None:
    """
    "2025 반기보고서"는 연도가 읽힌다. 보고서 종류가 바로 뒤에 붙은 자리이기 때문이다.

    이 통로는 이 변경이 만든 것이 아니라 기존 어휘 규칙이 이미 열어 둔 자리이며, 보고 기간
    해석이 그 자리를 막지 않는다는 것을 고정한다.
    """
    plan = plan_for("삼성전자 2025 반기보고서 매출액 알려줘")

    assert plan.years == (2025,)
    assert plan.document_subtype == DOCUMENT_SUBTYPE_HALF
    assert plan.fiscal_base_month == FISCAL_BASE_MONTH_HALF


# --------------------------------------------------------------------------
# 8. 정본 대응표
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "document_subtype", "fiscal_base_month"),
    [
        ("1분기보고서", DOCUMENT_SUBTYPE_QUARTER, FISCAL_BASE_MONTH_FIRST_QUARTER),
        ("반기보고서", DOCUMENT_SUBTYPE_HALF, FISCAL_BASE_MONTH_HALF),
        ("상반기", DOCUMENT_SUBTYPE_HALF, FISCAL_BASE_MONTH_HALF),
        ("3분기보고서", DOCUMENT_SUBTYPE_QUARTER, FISCAL_BASE_MONTH_THIRD_QUARTER),
        ("사업보고서", DOCUMENT_SUBTYPE_ANNUAL, FISCAL_BASE_MONTH_ANNUAL),
    ],
)
def test_canonical_period_table(
    expression: str,
    document_subtype: str,
    fiscal_base_month: int,
) -> None:
    """
    합의된 대응표를 한자리에 고정한다.

    개별 테스트가 이미 각 줄을 확인하지만, 표 전체를 한 곳에 두는 이유는 이 값들이 Team2와
    Team1의 경계이기 때문이다. 조회 계층은 이 두 값으로 manifest를 대조하므로, 표가 바뀌면
    양쪽이 함께 바뀌어야 한다는 사실이 이 테스트 하나로 드러나야 한다.

    manifest에 실제로 있는 조합만 담는다: annual/12, half/6, quarter/3, quarter/9.
    """
    plan = plan_for(f"삼성전자 2025년 {expression} 매출액 알려줘")

    assert plan.document_subtype == document_subtype
    assert plan.fiscal_base_month == fiscal_base_month


# --------------------------------------------------------------------------
# 9. 반기·분기보고서 요약 요청의 정지 사유
# --------------------------------------------------------------------------
#
# 이 절이 고정하는 것은 사유 하나다.
#
# 보고 기간 해석이 열리기 전, "2025년 반기보고서 요약해줘"는 기간을 이유로 막혔다. 해석이
# 열린 뒤 그 질문은 기간을 통과하고 단일 수치 조회로 내려가, 질문에 지표가 아예 없는데
# "지원하는 지표(매출액)를 찾을 수 없습니다"로 막혔다. 두 사유 모두 실제로 일어난 일이
# 아니다. 막힌 것은 기간도 지표도 아니고 요약이다.
#
# 왜 사유를 기간으로 되돌리지 않는가:
#     같은 반기보고서의 값 조회는 지금 답한다. 기간을 미지원이라고 적으면 답하고 있는 조회를
#     지원하지 않는다고 말하는 것이 되고, 반기·분기 보고서 지원이 실제로 늘어날 때 그 문장만
#     조용히 틀린 말로 남는다. 그래서 아래 테스트는 사유 코드와 함께 detail이 무엇을 말하지
#     않아야 하는지도 본다.


SUMMARY_TARGET_REASON = "unsupported_summary_target"

PERIODIC_REPORT_SUMMARY_QUESTIONS = (
    "삼성전자 2025년 1분기보고서 요약해줘",
    "삼성전자 2025년 반기보고서 요약해줘",
    "삼성전자 2025년 상반기 요약해줘",
    "삼성전자 2025년 3분기보고서 요약해줘",
)


@pytest.mark.parametrize("question", PERIODIC_REPORT_SUMMARY_QUESTIONS)
def test_periodic_report_summary_is_blocked_as_a_summary(question: str) -> None:
    """
    보고서 전체 요약 요청은 요약 사유로 막힌다.

    사유를 새로 만들지 않는다. unsupported_summary_target은 _build_document_summary_plan이
    요약 대상 문서를 확정하지 못할 때 이미 쓰는 사유이며, 그 builder를 직접 부르는 호출자도
    반기·분기보고서 질문에 대해 같은 사유로 막힌다. 두 입구가 같은 일에 같은 사유를 낸다.
    """
    error = blocked_by(question)

    assert error.reason == SUMMARY_TARGET_REASON


@pytest.mark.parametrize("question", PERIODIC_REPORT_SUMMARY_QUESTIONS)
def test_summary_block_does_not_call_the_period_unsupported(question: str) -> None:
    """
    정지 사유가 기간을 미지원이라고 말하지 않는다.

    이 단언이 사유 코드와 따로 있는 이유:
        코드만 고정하면 detail은 자유롭게 바뀐다. 사용자가 실제로 읽는 것은 detail이고,
        거기에 "분기·반기 보고서 조회는 이번 범위에 포함되지 않습니다"가 다시 적히면 사유
        코드가 무엇이든 사용자는 기간이 막혔다고 읽는다. 같은 보고서의 값 조회가 답하고 있는
        동안 그 문장은 틀린 말이다.

    무엇을 요구하는가:
        질문이 쓴 표기를 그대로 되돌려 주고(무엇이 막혔는지), 요약이라는 말이 있어야 한다
        (왜 막혔는지). 문장 전체를 고정하지는 않는다. 표현을 다듬는 일까지 이 테스트가 막으면
        고정한 것이 규칙이 아니라 문장이 된다.
    """
    error = blocked_by(question)

    assert "요약" in error.detail
    assert "조회는 이번 범위에 포함되지 않습니다" not in error.detail


def test_summary_block_names_the_expression_the_question_used() -> None:
    """막힌 대상을 질문이 쓴 말로 되돌려 준다. '상반기'라고 물었으면 '상반기'라고 답한다."""
    assert "상반기" in blocked_by("삼성전자 2025년 상반기 요약해줘").detail
    assert "반기보고서" in blocked_by("삼성전자 2025년 반기보고서 요약해줘").detail


def test_scoped_summary_of_a_periodic_report_is_blocked_the_same_way() -> None:
    """
    문서 내 범위를 밝힌 요약도 같은 사유로 막힌다.

    범위를 밝혔다고 답할 수 있게 되는 것이 아니다. 실행하는 쪽의 제약은 대상 문서에 걸려
    있으므로(handlers/document_summary.py), 범위 유무로 사유가 갈리면 같은 제약이 두 사유로
    보고된다.
    """
    error = blocked_by("삼성전자 2025년 반기보고서 설비 투자 정리해줘")

    assert error.reason == SUMMARY_TARGET_REASON


@pytest.mark.parametrize(
    ("expression", "document_subtype", "fiscal_base_month"),
    [
        ("1분기보고서", DOCUMENT_SUBTYPE_QUARTER, FISCAL_BASE_MONTH_FIRST_QUARTER),
        ("반기보고서", DOCUMENT_SUBTYPE_HALF, FISCAL_BASE_MONTH_HALF),
        ("3분기보고서", DOCUMENT_SUBTYPE_QUARTER, FISCAL_BASE_MONTH_THIRD_QUARTER),
        ("상반기", DOCUMENT_SUBTYPE_HALF, FISCAL_BASE_MONTH_HALF),
    ],
)
def test_value_questions_are_not_taken_by_the_summary_block(
    expression: str,
    document_subtype: str,
    fiscal_base_month: int,
) -> None:
    """
    같은 보고서의 값 조회는 요약 정지에 걸리지 않는다.

    이 절이 더한 판정은 요약 요청이 함께 온 경우에만 선다. 조건이 넓어지면 지금 답하고 있는
    조회가 요약 사유로 막히고, 그때 detail은 요약을 말하는데 사용자는 값을 물었다.

    8절의 정본 대응표와 같은 값을 본다. 그쪽은 표를 고정하고, 여기는 이 변경이 그 표를
    건드리지 않았다는 것을 고정한다.
    """
    plan = plan_for(f"삼성전자 2025년 {expression} 매출액 얼마야?")

    assert plan.intent == Intent.SINGLE_FACT
    assert plan.metric == MetricConcept.REVENUE
    assert plan.document_subtype == document_subtype
    assert plan.fiscal_base_month == fiscal_base_month


def test_annual_summary_is_not_taken_by_the_summary_block() -> None:
    """
    사업보고서 요약은 지금까지와 같이 요약 계획이 된다.

    이 변경은 사업보고서를 조건에서 제외한다. 연간 요약의 동작을 함께 바꾸면 이 변경의 범위가
    '반기·분기 요약의 사유'에서 '요약 경로 전체'로 넓어지고, 회귀가 생겼을 때 어느 쪽 때문인지
    가릴 수 없다.
    """
    plan = plan_for("삼성전자 2025년 사업보고서 설비 투자 정리해줘")

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.document_subtype == DOCUMENT_SUBTYPE_ANNUAL


def test_event_history_questions_keep_their_route() -> None:
    """
    이력 질문은 '정리해줘'와 기간 표기가 함께 있어도 이력 질문이다.

    새 판정이 이력 판정보다 뒤에 있다는 것을 고정한다. 앞에 두면 Pattern E가 답하던 질문이
    요약 미지원으로 막힌다.
    """
    plan = plan_for("삼성전자 2025년 상반기 시설투자 변경 내용 정리해줘")

    assert plan.intent == Intent.EVENT_HISTORY


def test_bare_quarter_summary_still_reports_an_unsupported_period() -> None:
    """
    보고서 이름으로 좁혀지지 않는 기간은 여전히 기간 사유로 막힌다.

    "2025년 분기보고서 요약해줘"는 1분기와 3분기 중 어느 쪽인지 질문에 없다. 여기서 막는 것은
    실제로 기간이며, 요약 사유로 바꾸면 이번에는 반대 방향으로 사유가 틀린다. 새 판정이
    해석된 보고 기간이 있을 때만 서는 이유가 이것이다.
    """
    error = blocked_by("삼성전자 2025년 분기보고서 요약해줘")

    assert error.reason == "unsupported_period"


def test_lower_half_summary_still_asks_again() -> None:
    """'하반기' 요약은 되묻기가 먼저다. 되묻기는 새 판정보다 앞에서 일어난다."""
    error = blocked_by("삼성전자 2025년 하반기 요약해줘")

    assert error.reason == AMBIGUOUS_PERIOD_REASON


# --------------------------------------------------------------------------
# 10. 대표 질문 비회귀
# --------------------------------------------------------------------------


# docs/agent_design/representative_questions.md의 Q01~Q18.
#
# 계획의 모든 필드를 여기에 적어 두지 않는다. 이 절이 막으려는 것은 하나이며 — 새 요약 판정이
# 대표 질문 중 하나를 가로채는 것 — 그것은 사유 하나로 드러난다. 계획 전체를 적으면 관계없는
# 필드가 늘 때마다 이 파일이 함께 바뀌고, 무엇을 지키는 테스트인지 흐려진다.
REPRESENTATIVE_QUESTIONS = (
    "한미반도체 2025년 연결 기준 매출액이 얼마야?",
    "한화오션이 2025년 9월에 양밍해운이랑 맺은 공급계약, 금액이랑 계약기간 알려줘.",
    (
        "삼성물산이 2023년 1월에 낸 대량보유 보고서 기준으로, "
        "이번 보고 시점의 삼성전자 보유주식 수와 보유비율이 얼마야?"
    ),
    "삼성바이오로직스 2025년 사업보고서에서 설비 투자 관련 내용 정리해줘.",
    "한미반도체는 무슨 사업을 하고 주력 제품이 뭐야?",
    "LG유플러스 파주 AIDC 투자 공시 내용 요약해줘.",
    "한미반도체 매출이 작년보다 얼마나 늘었어?",
    "한미반도체 영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?",
    "삼성바이오로직스 사업 구조가 2023년이랑 2025년이랑 어떻게 달라졌어?",
    "2025년 HD현대중공업이랑 삼성중공업 매출 비교해줘.",
    "HD현대중공업이랑 삼성중공업 중에 2025년 영업이익률이 더 높은 곳은 어디야?",
    (
        "2024년에 LG이노텍하고 레인보우로보틱스가 새로 발표한 시설투자, "
        "회사별로 다 합치면 어디가 더 커?"
    ),
    "HD현대일렉트릭이 2024년 8월에 공시한 공급계약, 나중에 바뀐 내용 있어?",
    "LG에너지솔루션이 포드랑 맺었던 배터리 공급계약, 지금 어떻게 됐어?",
    "레인보우로보틱스 사옥·제조시설 신축 투자, 그동안 계획이 몇 번 바뀌었어?",
    "삼성전자 2026년 상반기 실적 좀 알려줘.",
    "이번 수주 공시 나오고 나서 시장 반응은 어땠어?",
    "지금 한화오션 주식 사도 될까?",
)

REPRESENTATIVE_COMPANIES = (
    "한미반도체",
    "한화오션",
    "삼성물산",
    "삼성전자",
    "삼성바이오로직스",
    "LG유플러스",
    "HD현대중공업",
    "삼성중공업",
    "LG이노텍",
    "레인보우로보틱스",
    "HD현대일렉트릭",
    "LG에너지솔루션",
)


@pytest.mark.parametrize("question", REPRESENTATIVE_QUESTIONS)
def test_representative_questions_do_not_reach_the_summary_block(question: str) -> None:
    """
    Q01~Q18 중 어느 것도 새 요약 판정으로 막히지 않는다.

    이 변경이 만든 위험이 이것이다. 새 판정은 지표 판정보다 앞에 서므로, 조건이 의도보다 넓으면
    지금 답하고 있는 대표 질문이 이 자리에서 먼저 막힌다. 그 경우 막힌 사유는 요약을 말하는데
    질문은 요약을 청하지 않았다.

    Q04·Q06은 실제로 요약 질문이지만 각각 사업보고서와 거래소공시를 대상으로 하며, 둘 다 이
    판정의 조건(이름으로 지목한 반기·분기보고서)에 걸리지 않는다. 그 둘이 이 목록에 함께 있는
    것이 조건이 좁다는 증거다.

    답이 나오는지는 보지 않는다. 대표 질문 중에는 코퍼스 없이 계획 단계에서 막히는 것이 있고
    (Q16~Q18은 그렇게 막히도록 설계된 질문이다), 그 사유들은 이 변경 이전부터의 동작이다.
    """
    try:
        build_query_plan(question, known_companies=REPRESENTATIVE_COMPANIES)
    except UnsupportedQuestionError as error:
        assert error.reason != SUMMARY_TARGET_REASON
