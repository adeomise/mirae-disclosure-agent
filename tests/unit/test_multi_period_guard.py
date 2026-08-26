"""다중 기간 질문이 한 기간으로 축소되지 않는지 검증한다.

무엇을 막는가:
    "삼성전자 2024년 매출과 2025년 매출 비교해줘"처럼 두 기간을 묻는 질문이 single_fact로
    흘러가면, 첫 연도의 Fact 1건이 Evidence까지 갖춘 채 COMPLETED/ANSWERABLE로 돌아간다.
    형식은 맞지만 질문에는 답하지 않은 결과이고, 두 번째 기간이 사라졌다는 사실이
    warnings에도 conversion_gaps에도 남지 않는다.

    원인은 _extract_year가 regex search로 첫 매치 하나만 돌려준 것이었다. Router는 intent만
    보므로 방어 위치가 아니며, 판정은 query_analyzer에서 나야 한다.

    지원하지 않는다고 답하는 것보다 이쪽이 위험하다. 받는 쪽이 틀렸다는 것을 알 수 없기
    때문이다. 그래서 '조용히 틀린 답'이 아니라 'routing 단계에서 막혔다'로 답하게 한다.

무엇을 검증하지 않는가:
    Pattern C(시계열 비교)의 실제 동작은 검증하지 않는다. handler가 없기 때문이다.
    여기서 고정하는 것은 '축소해서 답하지 않는다'까지다.

false positive:
    같은 연도가 문장에 여러 번 나오는 것은 두 기간이 아니다. 값으로 중복을 제거하며,
    그 대조군을 아래 3절에 둔다.

판정 순서:
    이 가드는 다중 기업 판정과 이력 판정보다 뒤, 지표 판정보다 앞에 있다. 세 경계가 모두
    의미를 가지므로 4절에서 각각 고정한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from disclosure_agent.agent.answer_builder import STAGE_ROUTING
from disclosure_agent.agent.query_analyzer import (
    INTENT_CROSS_ENTITY_COMPARE,
    INTENT_EVENT_HISTORY,
    INTENT_SINGLE_FACT,
    INTENT_TEMPORAL_COMPARE,
    KNOWN_INTENTS,
    QueryPlan,
    UnsupportedQuestionError,
    _extract_years,
    build_query_plan,
)
from disclosure_agent.agent.router import (
    REASON_UNIMPLEMENTED_INTENT,
    answer_question,
    supported_intents,
)
from disclosure_agent.common.enums import ExecutionStatus

# 합성 manifest에 실제로 있는 기업명. 2024·2025 사업보고서를 모두 가지고 있다.
SYNTHETIC_COMPANIES = ("테스트반도체(주)", "테스트전자")

# 실측 코퍼스 질문의 표기. 기업명 인식 목록은 테스트가 직접 넘긴다.
REAL_COMPANIES = ("삼성전자", "한미반도체")

# pattern_f_safety_cases.json F6-02 원문과 그 변형.
MULTI_PERIOD_QUESTIONS = (
    "삼성전자 2024년 매출과 2025년 매출 비교해줘",
    "삼성전자 2024년 대비 2025년 매출 증가율은?",
    "한미반도체 2024년이랑 2025년 매출 알려줘",
)

# 절대연도 하나 + 상대기간 표현. 절대연도 개수만 세면 하나이므로 가드를 통과한다.
MIXED_PERIOD_QUESTIONS = (
    "삼성전자 2025년 매출이 전년보다 얼마나 늘었어?",
    "삼성전자 2025년 매출이 작년보다 얼마나 늘었어?",
    "삼성전자 2025년 매출이 지난해보다 얼마나 늘었어?",
)


# --------------------------------------------------------------------------
# 1. 절대연도를 전부 읽는다
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("삼성전자 2024년 매출과 2025년 매출 비교해줘", (2024, 2025)),
        ("삼성전자 2025년 매출 알려줘", (2025,)),
        ("삼성전자 매출 알려줘", ()),
        # 등장 순서를 지킨다. 정렬하지 않는다.
        ("삼성전자 2025년과 2023년 매출 비교해줘", (2025, 2023)),
        # 셋 이상도 전부 읽는다.
        ("삼성전자 2023년 2024년 2025년 매출 추이 보여줘", (2023, 2024, 2025)),
        # 조사가 붙어도 읽는다.
        ("한미반도체 2024년이랑 2025년에 어떻게 달라졌어?", (2024, 2025)),
    ],
)
def test_extract_years_reads_every_absolute_year(
    question: str,
    expected: tuple[int, ...],
) -> None:
    """
    첫 매치 하나만 돌려주지 않는다.

    이 함수가 첫 매치만 돌려주던 것이 F6-02 silent-wrong의 근본 원인이다.
    """
    assert _extract_years(question) == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # 금액은 기간이 아니다.
        ("삼성전자 2024년 매출이 2025억원 맞아?", (2024,)),
        ("삼성전자 2024년 매출 2025억원이 맞나?", (2024,)),
        # 더 긴 숫자의 일부를 연도로 읽지 않는다.
        ("삼성전자 2024년 매출이 12025원 맞아?", (2024,)),
        # '년'이 붙지 않은 네 자리 숫자는 연도가 아니다.
        ("삼성전자 2024년 매출액 2025 맞아?", (2024,)),
    ],
)
def test_extract_years_does_not_read_numbers_without_a_year_suffix(
    question: str,
    expected: tuple[int, ...],
) -> None:
    """
    금액을 연도로 세지 않는다.

    네 자리 숫자를 그대로 연도로 보면 "2024년 매출이 2025억원 맞아?"의 2025가 두 번째 연도가
    되어 답할 수 있는 질문이 시계열 비교로 오분류되고 막힌다. 막는 쪽으로 틀리는 것도
    틀리는 것이므로 대조군을 둔다.
    """
    assert _extract_years(question) == expected


def test_extract_years_allows_whitespace_before_the_year_suffix() -> None:
    """'년'을 요구하되 그 앞의 공백은 허용한다."""
    assert _extract_years("삼성전자 2025 년 매출 알려줘") == (2025,)


def test_extract_years_deduplicates_by_value() -> None:
    """
    같은 연도를 두 번 적은 것은 두 기간이 아니다.

    값으로 중복을 제거하지 않으면 "2024년 매출과 2024년 영업이익"이 다중 기간으로 오판되어
    답할 수 있는 질문이 막힌다.
    """
    assert _extract_years("삼성전자 2024년 매출과 2024년 영업이익 알려줘") == (2024,)


def test_extract_years_ignores_relative_period_expressions() -> None:
    """
    상대기간은 읽지 않는다. 해석하려면 기준 시점을 정해야 하고 그것은 이번 범위 밖이다.

    읽지 못한 결과는 missing_year이므로 답이 축소되지는 않는다. 이 케이스가 고정하는 것은
    '상대기간을 절대연도로 추측하지 않는다'까지다.
    """
    assert _extract_years("한미반도체 매출이 작년보다 얼마나 늘었어?") == ()


# --------------------------------------------------------------------------
# 2. 다중 기간 질문은 temporal_compare로 분류된다
# --------------------------------------------------------------------------


@pytest.mark.parametrize("question", MULTI_PERIOD_QUESTIONS)
def test_multi_period_question_is_classified_as_temporal_compare(question: str) -> None:
    """
    기존 어휘를 쓴다.

    temporal_compare는 이미 KNOWN_INTENTS에 있다. 새 intent를 만들면 Router가 '어휘에 없는
    intent'로 보고 후속 작업이 달라진다.
    """
    plan = build_query_plan(question, known_companies=REAL_COMPANIES)

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.intent in KNOWN_INTENTS
    assert plan.intent not in supported_intents()


@pytest.mark.parametrize("question", MULTI_PERIOD_QUESTIONS)
def test_multi_period_plan_carries_a_diagnostic_warning(question: str) -> None:
    """
    다중 기간으로 해석했다는 진단이 남는다.

    존재만 본다. 무엇을 어떤 문자열로 담는지는 고정하지 않는다.

    warnings는 C-0이 쓰는 임시 통로다. QueryPlan이 아직 스칼라 year만 담아 다른 자리가
    없어서 쓰는 것이며, C-1이 다중 기간의 정본 표현을 도입하면 기간 정보는 그쪽으로
    옮겨간다. 지금 문자열 형태까지 고정하면 C-1이 이 테스트를 깨뜨리는데, 그때 깨지는 것은
    안전 불변식이 아니라 임시 표현의 세부다. 구현 세부를 contract로 굳히지 않는다.

    이 파일이 고정하는 안전 불변식은 넷이며 전부 다른 케이스가 맡는다.
        1. distinct 절대연도가 둘 이상이면 temporal_compare   (2절)
        2. single_fact로 축소되지 않는다                      (2절·3절)
        3. Router가 routing/unimplemented_intent로 막는다     (5절)
        4. facts와 evidence가 비어 있다                       (5절)
    """
    plan = build_query_plan(question, known_companies=REAL_COMPANIES)

    assert plan.warnings


@pytest.mark.parametrize("question", MIXED_PERIOD_QUESTIONS)
def test_absolute_year_with_relative_period_is_classified_as_temporal_compare(
    question: str,
) -> None:
    """
    절대연도 하나 + 상대기간 표현도 다중 기간이다.

    절대연도 개수만 세면 하나이므로 이 질문들이 가드를 통과해 single_fact로 축소된다.
    "2025년 매출이 전년보다 얼마나 늘었어?"에 2025년 값 하나만 답하면 F6-02와 같은 형태의
    조용히 틀린 답이 된다.
    """
    plan = build_query_plan(question, known_companies=REAL_COMPANIES)

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.intent != INTENT_SINGLE_FACT


def test_relative_period_is_detected_but_not_resolved() -> None:
    """
    상대기간을 연도로 옮기지 않는다.

    "전년"을 2024로 바꾸려면 기준 시점(as_of) 정책을 먼저 정해야 하고, 정하지 않은 채 오늘
    날짜로 대신하면 같은 질문이 실행 시점에 따라 다른 해를 가리킨다. 이번 범위는 기간이
    둘이라는 사실만 확정한다.

    _extract_years가 절대연도만 돌려주는 것이 그 경계다. 여기에 2024가 나타나면 해석을
    시작했다는 뜻이고, 그것은 이 PR의 범위를 넘는다.
    """
    question = "삼성전자 2025년 매출이 전년보다 얼마나 늘었어?"

    assert _extract_years(question) == (2025,)

    plan = build_query_plan(question, known_companies=REAL_COMPANIES)

    assert plan.intent == INTENT_TEMPORAL_COMPARE


def test_numeric_value_is_not_read_as_a_second_period() -> None:
    """
    금액이 두 번째 연도로 인식되지 않는다.

    이 질문이 최종적으로 답변 가능한지는 보지 않는다. 지원 여부는 다른 사정이고, 여기서
    고정하는 것은 '2025억원을 기간으로 세지 않는다'는 불변식 하나다.
    """
    question = "삼성전자 2024년 매출이 2025억원 맞아?"

    assert _extract_years(question) == (2024,)

    plan = build_query_plan(question, known_companies=REAL_COMPANIES)

    assert plan.intent != INTENT_TEMPORAL_COMPARE


def test_multi_period_plan_reads_metric_loosely() -> None:
    """
    지표를 모른다는 이유로 먼저 막지 않는다.

    Q08("영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?")은 이번 범위가 읽지 못하는
    지표를 쓴다. 여기서 거절하면 막힌 이유가 'Pattern C 미구현'이 아니라 '지표를 모르겠다'로
    바뀌어, 무엇을 구현하면 풀리는 질문인지가 결과에서 사라진다. Pattern D가 같은 이유로
    같은 선택을 했다.
    """
    plan = build_query_plan(
        "한미반도체 영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?",
        known_companies=REAL_COMPANIES,
    )

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.metric is None


# --------------------------------------------------------------------------
# 3. 대조군 — 축소되지 않아야 할 질문까지 막지 않는다
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "삼성전자 2024년 매출 알려줘",
        "한미반도체 2025년 연결 기준 매출액이 얼마야?",
    ],
)
def test_single_year_question_stays_single_fact(question: str) -> None:
    """단일 연도 질문의 기존 동작은 그대로다."""
    plan = build_query_plan(question, known_companies=REAL_COMPANIES)

    assert plan.intent == INTENT_SINGLE_FACT
    assert plan.year in _extract_years(question)


def test_same_year_twice_is_not_a_multi_period_question() -> None:
    """
    distinct 연도가 하나면 다중 기간이 아니다.

    값으로 중복을 제거하지 않으면 답할 수 있는 질문이 막힌다. 막는 쪽으로 틀리는 것도
    틀리는 것이다.
    """
    plan = build_query_plan(
        "삼성전자 2024년 매출과 2024년 매출액 알려줘",
        known_companies=REAL_COMPANIES,
    )

    assert plan.intent == INTENT_SINGLE_FACT
    assert plan.year == 2024


def test_relative_period_question_still_reports_missing_year() -> None:
    """
    상대기간 질문은 이 가드가 다루지 않는다.

    Q07("작년보다")은 연도를 하나도 읽지 못해 막힌다. 답을 축소하지 않으므로 silent-wrong이
    아니며, 상대기간 해석은 Pattern C 구현 범위다. 이 케이스는 그 경계를 고정한다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(
            "한미반도체 매출이 작년보다 얼마나 늘었어?",
            known_companies=REAL_COMPANIES,
        )

    assert error.value.reason == "missing_year"


# --------------------------------------------------------------------------
# 4. 판정 순서 — 세 경계를 각각 고정한다
# --------------------------------------------------------------------------


def test_multi_company_wins_over_multi_period() -> None:
    """
    기업 축과 기간 축이 함께 여럿이면 Pattern D가 먼저다.

    Pattern D 판정이 이미 그 자리에 있고 순서를 바꿀 이유가 없다. 어느 쪽으로 가든 막히지만,
    막힌 이유가 후속 작업을 고르는 근거이므로 한쪽으로 고정해 둔다.
    """
    plan = build_query_plan(
        "2024년과 2025년 삼성전자랑 한미반도체 매출 비교해줘",
        known_companies=REAL_COMPANIES,
    )

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE


def test_event_history_wins_over_multi_period() -> None:
    """
    거래소공시 이력 질문을 가로채지 않는다.

    이력 질문은 원공시 연도와 정정 연도를 함께 적을 수 있다. 다중 기간 판정을 이력 판정보다
    앞에 두면 실제로 답하고 있던 Pattern E 질문이 막힌다. 미구현을 막는 가드가 구현된 기능을
    깨뜨리면 안 된다.
    """
    plan = build_query_plan(
        "테스트반도체가 2024년 8월에 공시한 공급계약, 2025년에 바뀐 내용 있어?",
        known_companies=SYNTHETIC_COMPANIES,
    )

    assert plan.intent == INTENT_EVENT_HISTORY


def test_multi_period_wins_over_unsupported_metric() -> None:
    """
    지표 판정보다 앞이다.

    Q08이 이 경계에 걸린다. 지표를 먼저 보면 막힌 이유가 'Pattern C 미구현'이 아니라
    '지표를 모르겠다'가 되어, 다중 기간이라는 더 근본적인 구조가 결과에서 사라진다.
    """
    plan = build_query_plan(
        "한미반도체 영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?",
        known_companies=REAL_COMPANIES,
    )

    assert plan.intent == INTENT_TEMPORAL_COMPARE


def test_out_of_scope_period_still_wins_over_multi_period() -> None:
    """
    분기·반기 표현은 의도 분기보다 먼저 본다.

    의도와 무관한 기간 규칙이므로 공통 경로에 있고, 이 가드가 그 순서를 바꾸지 않는다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(
            "삼성전자 2024년 1분기와 2025년 1분기 매출 비교해줘",
            known_companies=REAL_COMPANIES,
        )

    assert error.value.reason == "unsupported_period"


# --------------------------------------------------------------------------
# 5. 질문 문자열부터 실행 결과까지
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "테스트반도체 2024년 매출과 2025년 매출 비교해줘",
        "테스트반도체 2024년 대비 2025년 매출 증가율은?",
        "테스트반도체 2024년이랑 2025년 매출 알려줘",
    ],
)
def test_multi_period_question_is_blocked_at_routing(
    question: str,
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    질문 문자열부터 실행 결과까지 한 번에 본다.

    facts와 evidence가 비어 있어야 한다. 하나라도 남으면 '두 기간 중 하나를 골라 답했다'는
    뜻이고, 그것이 이 가드가 막으려는 동작이다.

    answerability_status도 비어 있어야 한다. 코퍼스를 보지 않았으므로 답이 있는지 없는지
    판정하지 않았다.
    """
    result = answer_question(
        question,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.answerability_reason is None
    assert result.facts == []
    assert result.evidence == []

    issue = result.execution_issue

    assert issue is not None
    assert issue.stage == STAGE_ROUTING
    assert issue.reason == REASON_UNIMPLEMENTED_INTENT


@pytest.mark.parametrize(
    "question",
    [
        "테스트반도체 2025년 매출이 전년보다 얼마나 늘었어?",
        "테스트반도체 2025년 매출이 작년보다 얼마나 늘었어?",
    ],
)
def test_absolute_plus_relative_question_is_blocked_at_routing(
    question: str,
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    절대연도 + 상대기간 질문도 값을 만들어 내지 않는다.

    이것이 이번 라운드에서 막는 구멍이다. 절대연도가 하나뿐이라 다중 기간 판정을 통과하고,
    Pattern A가 그 한 연도의 Fact를 근거까지 갖춰 ANSWERABLE로 돌려주던 경로였다.
    """
    result = answer_question(
        question,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.answerability_reason is None
    assert result.facts == []
    assert result.evidence == []

    issue = result.execution_issue

    assert issue is not None
    assert issue.stage == STAGE_ROUTING
    assert issue.reason == REASON_UNIMPLEMENTED_INTENT


def test_blocked_multi_period_result_names_the_missing_capability(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """무엇을 구현하면 풀리는 질문인지가 결과에 남아야 후속 작업을 고를 수 있다."""
    result = answer_question(
        "테스트반도체 2024년 매출과 2025년 매출 비교해줘",
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    issue = result.execution_issue

    assert issue is not None
    assert INTENT_TEMPORAL_COMPARE in issue.detail


def test_single_year_question_still_reaches_the_pattern_a_handler(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    가드가 단일 기간 흐름을 가로채지 않는다.

    routing에서 막히지 않았다는 것만 본다. Pattern A의 실제 추출 결과는 이 파일의 관심사가
    아니며 test_q01_slice.py가 맡는다.
    """
    result = answer_question(
        "테스트반도체 2025년 연결 기준 매출액이 얼마야?",
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    issue = result.execution_issue

    assert issue is None or issue.stage != STAGE_ROUTING


# --------------------------------------------------------------------------
# 6. canonical years — 다중 기간 정보가 구조에 남는가
# --------------------------------------------------------------------------


def test_multi_period_plan_keeps_every_absolute_year_in_years() -> None:
    """
    두 연도가 계획 구조에 남는다.

    C-0까지는 scalar year 하나만 있어서 2025년이 warnings 문자열 안에만 존재했다. 기계가
    읽을 수 있는 자리에 없으면 Pattern C handler가 그것을 근거로 쓸 수 없다.
    """
    plan = build_query_plan(
        "삼성전자 2024년 매출과 2025년 매출 비교해줘",
        known_companies=REAL_COMPANIES,
    )

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.years == (2024, 2025)


def test_reading_year_on_a_multi_period_plan_fails_fast() -> None:
    """
    다중 기간 계획에서 year를 읽으면 막힌다.

    이 파일에서 가장 중요한 구조적 회귀다. C-0가 제거한 silent first-year truncation이
    compatibility property를 통해 되살아나지 않는다는 것을 고정한다.

    company와 달리 첫 값을 돌려주지 않는 이유:
        여러 기업 중 하나를 고르는 일은 Router가 막아 handler에 도달하지 않지만, Pattern C
        handler가 생기면 다중 기간 계획은 handler 안까지 들어온다. 그때 year가 첫 연도를
        돌려주면 두 기간을 물은 질문에 한 연도만 답하는 결과가 되고, 그것이 F6-02다.
    """
    plan = build_query_plan(
        "삼성전자 2024년 매출과 2025년 매출 비교해줘",
        known_companies=REAL_COMPANIES,
    )

    with pytest.raises(ValueError) as error:
        plan.year  # noqa: B018 - 접근 자체가 검사 대상이다

    # 무엇을 대신 읽어야 하는지가 메시지에 있어야 후속 작업을 고를 수 있다.
    assert "years" in str(error.value)


def test_year_fail_fast_is_not_swallowed_by_getattr() -> None:
    """
    가드가 hasattr/getattr에 삼켜지지 않는다.

    AttributeError로 막으면 hasattr(plan, "year")가 False를 돌려주고
    getattr(plan, "year", 기본값)이 조용히 기본값을 준다. 값을 고르지 않으려고 만든 가드가
    그렇게 삼켜지면 가드가 없는 것과 같다. 그래서 ValueError를 쓴다.
    """
    plan = build_query_plan(
        "삼성전자 2024년 매출과 2025년 매출 비교해줘",
        known_companies=REAL_COMPANIES,
    )

    with pytest.raises(ValueError):
        getattr(plan, "year", 2024)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("삼성전자 2024년 매출 알려줘", (2024,)),
        ("한미반도체 2025년 연결 기준 매출액이 얼마야?", (2025,)),
        # 같은 연도를 두 번 적어도 기간은 하나다.
        ("삼성전자 2024년 매출과 2024년 매출액 알려줘", (2024,)),
    ],
)
def test_single_period_plan_exposes_year(
    question: str,
    expected: tuple[int, ...],
) -> None:
    """단일 기간에서는 year가 기존과 똑같이 동작한다."""
    plan = build_query_plan(question, known_companies=REAL_COMPANIES)

    assert plan.years == expected
    assert plan.year == expected[0]
    assert plan.intent == INTENT_SINGLE_FACT


def test_mixed_period_plan_keeps_only_the_absolute_year() -> None:
    """
    상대기간을 연도로 바꾸지 않는다.

    "전년"이 2024로 들어가면 해석을 시작한 것이고, 그것은 Pattern C 범위다. years에 2025만
    남아야 하며, 그래도 intent가 temporal_compare라 fail-closed는 유지된다.
    """
    plan = build_query_plan(
        "삼성전자 2025년 매출이 전년보다 얼마나 늘었어?",
        known_companies=REAL_COMPANIES,
    )

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.years == (2025,)
    assert 2024 not in plan.years


def test_event_history_plan_keeps_a_single_anchor_year() -> None:
    """
    Pattern E는 연도가 둘 보여도 기준점 하나만 갖는다.

    Pattern E의 연도는 비교 대상 기간이 아니라 원공시를 찾는 공시일 기준점이다. handler가
    filing_date_window로 원공시 후보만 좁히고 정정본은 기간 제한 없이 문서에 적힌 연결을
    따라가므로, 2025년은 그 체인이 이미 포함하는 서술이다.

    그래서 이 계획에서는 year가 막히지 않고 정상적으로 기준점을 돌려줘야 한다. 막히면
    실제로 답하고 있던 Q13 계열이 깨진다.
    """
    plan = build_query_plan(
        "테스트반도체가 2024년 8월에 공시한 공급계약, 2025년에 바뀐 내용 있어?",
        known_companies=SYNTHETIC_COMPANIES,
    )

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.years == (2024,)
    assert plan.year == 2024
    assert plan.month == 8


# --------------------------------------------------------------------------
# 7. 직렬화 — 정본만 싣는다
# --------------------------------------------------------------------------


def test_serialized_plan_carries_years_only() -> None:
    """
    파생값을 함께 실으면 읽는 쪽이 무엇이 정본인지 알 수 없게 된다.

    companies/company가 이미 같은 규칙을 따른다. year는 property이므로 asdict가 담지 않고,
    그것이 의도한 결과다. 진단 dict는 공개 응답(AnswerResponse)에 실리지 않는다.
    """
    plan = build_query_plan(
        "삼성전자 2024년 매출과 2025년 매출 비교해줘",
        known_companies=REAL_COMPANIES,
    )

    dumped = plan.to_dict()

    assert dumped["years"] == (2024, 2025)
    assert "year" not in dumped


def test_serialized_single_period_plan_also_carries_years() -> None:
    """단일 기간에서도 정본은 years 하나다. 기존 shape을 되살리지 않는다."""
    dumped = build_query_plan(
        "삼성전자 2024년 매출 알려줘",
        known_companies=REAL_COMPANIES,
    ).to_dict()

    assert dumped["years"] == (2024,)
    assert "year" not in dumped


def test_query_plan_rejects_a_bare_year_int() -> None:
    """
    정수를 그대로 받으면 순회되지 않아 다른 자리에서 터진다.

    companies가 문자열 하나를 거부하는 것과 같은 이유로, 생성 시점에 막는다.
    """
    with pytest.raises(TypeError):
        QueryPlan(question="합성", companies=("합성",), years=2025)  # type: ignore[arg-type]


def test_query_plan_rejects_an_empty_year_set() -> None:
    """기간이 없는 계획은 만들 수 없다."""
    with pytest.raises(ValueError):
        QueryPlan(question="합성", companies=("합성",), years=())
