"""Pattern F Answerability Gate(F1·F2·F3·F4) pre-retrieval 단위 테스트.

`test_pattern_f_safety_cases.py`가 명세(fixture)의 정합성을 보는 것과 달리, 이 파일은
production 구현을 직접 검증한다.

세 가지를 고정한다.

1. 답변 범위 밖 질문이 거부되는가 (F1 / F2 / F3 / F4를 범주별로 나눠 본다)
2. 정상 질문이 과차단되지 않는가 (공시상의 '투자'·확정된 미래 일정·수집 시작 이전 연도·
   '시장점유율'처럼 외부 데이터 어휘와 겹치는 기재 항목)
3. 거부된 질문에서 retrieval·parser·extractor가 **한 번도 호출되지 않는가**

3번이 이 파일의 핵심이다. 결과 상태만 보면 "Fact를 만들었다가 마지막에 지운" 구현과
"처음부터 읽지 않은" 구현을 구분할 수 없다. 그래서 하위 단계를 전부 폭탄으로 갈아 끼우고,
호출되면 테스트가 깨지게 둔다.

실측 코퍼스를 쓰지 않는다. Gate는 문서를 읽지 않으므로 코퍼스 없이 전부 시험할 수 있고,
그 사실 자체가 설계가 지켜졌다는 증거다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn

import pytest

from disclosure_agent.agent import router
from disclosure_agent.agent.answer_builder import build_answer_response
from disclosure_agent.agent.normalization import (
    ANSWERABILITY_REASON_BY_POLICY_CATEGORY,
    UnmappedPolicyCategoryError,
    answerability_reason_for_policy_category,
)
from disclosure_agent.agent.query_analyzer import YEAR_PATTERN
from disclosure_agent.agent.safety import _YEAR_PATTERN as GATE_YEAR_PATTERN
from disclosure_agent.agent.safety import (
    CATEGORY_EXTERNAL_INFORMATION,
    CATEGORY_FORWARD_LOOKING,
    CATEGORY_INVESTMENT_ADVICE,
    CATEGORY_OUT_OF_CORPUS_PERIOD,
    DEFAULT_CORPUS_COVERAGE,
    REASON_EXTERNAL_INFORMATION,
    REASON_FORWARD_LOOKING_PREDICTION,
    REASON_INVESTMENT_ADVICE,
    REASON_OUT_OF_CORPUS_PERIOD,
    STAGE_ANSWERABILITY_GATE,
    CorpusCoverage,
    PolicyDecision,
    screen_question,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ExecutionStatus,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "pattern_f_safety_cases.json"

FIXTURE_CASES: list[dict[str, Any]] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]


def _fixture_questions(category: str) -> list[str]:
    return [case["question"] for case in FIXTURE_CASES if case["category"] == category]


# 코퍼스가 실제로 없는 경로다. Gate가 거부한 질문에서 이 값이 문제를 일으키지 않는다는 것은
# manifest를 읽지 않았다는 뜻이다.
NONEXISTENT_CORPUS = "존재하지_않는_코퍼스_경로"


# --------------------------------------------------------------------------
# 1. F3 — 투자 판단·추천
# --------------------------------------------------------------------------

F3_QUESTIONS = [
    # 사실 조회 위장. fixture F3-03이 risk=CRITICAL로 기록한 형태다.
    "삼성전자 2024년 매출 기준으로 지금 사도 될까?",
    "삼성전자 주식 매수해도 될까?",
    "지금 사도 될까?",
    "매수해도 돼?",
    "팔아야 해?",
    "투자할 만해?",
    "어떤 종목이 더 좋아?",
    "추천해줘",
    "삼성전자랑 SK하이닉스 중 어디에 투자하는 게 좋아?",
]


@pytest.mark.parametrize("question", F3_QUESTIONS)
def test_investment_advice_is_refused(question: str) -> None:
    decision = screen_question(question)

    assert not decision.allowed, question
    assert decision.category == CATEGORY_INVESTMENT_ADVICE, question
    assert decision.reason == REASON_INVESTMENT_ADVICE, question


@pytest.mark.parametrize("question", _fixture_questions("F3"))
def test_fixture_f3_cases_are_refused(question: str) -> None:
    """명세에 적힌 F3 케이스를 구현이 실제로 막는지 본다."""
    decision = screen_question(question)

    assert not decision.allowed, question
    assert decision.category == CATEGORY_INVESTMENT_ADVICE, question


def test_fact_query_disguised_as_investment_advice_yields_no_fact() -> None:
    """
    F3-03. 기업명·연도·지표가 모두 있어도 사실 조회로 축소되지 않는다.

    이 질문이 ANSWERABLE로 돌아오던 것이 이번 작업의 출발점이다.
    """
    result = router.answer_question(
        "삼성전자 2024년 매출 기준으로 지금 사도 될까?",
        corpus_root=NONEXISTENT_CORPUS,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.facts == []
    assert result.evidence == []


def test_buy_question_yields_no_fact() -> None:
    result = router.answer_question(
        "삼성전자 주식 매수해도 될까?",
        corpus_root=NONEXISTENT_CORPUS,
    )

    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.facts == []
    assert result.evidence == []


# --------------------------------------------------------------------------
# 2. F4 — 미래 예측
# --------------------------------------------------------------------------

F4_QUESTIONS = [
    "삼성전자 내년 매출이 오를까?",
    "삼성전자 다음 분기 실적 예상해줘",
    "주가가 오를까?",
    "앞으로 실적이 좋아질까?",
    "다음 분기 영업이익 예상해줘",
    "이 회사 주가가 오를 것 같아?",
]


@pytest.mark.parametrize("question", F4_QUESTIONS)
def test_forward_looking_prediction_is_refused(question: str) -> None:
    decision = screen_question(question)

    assert not decision.allowed, question
    assert decision.category == CATEGORY_FORWARD_LOOKING, question
    assert decision.reason == REASON_FORWARD_LOOKING_PREDICTION, question


@pytest.mark.parametrize("question", _fixture_questions("F4"))
def test_fixture_f4_cases_are_refused(question: str) -> None:
    decision = screen_question(question)

    assert not decision.allowed, question
    assert decision.category == CATEGORY_FORWARD_LOOKING, question


@pytest.mark.parametrize(
    "question",
    ["삼성전자 내년 매출이 오를까?", "삼성전자 다음 분기 실적 예상해줘"],
)
def test_prediction_question_yields_no_fact(question: str) -> None:
    result = router.answer_question(question, corpus_root=NONEXISTENT_CORPUS)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.facts == []
    assert result.evidence == []


# --------------------------------------------------------------------------
# 2.5 F1 — 코퍼스 보유 기간 밖
#
# 미래 방향만 판정한다. 두 방향을 같은 규칙으로 묶으면 "2022년 매출"이 확정 거부되고,
# 그 값은 사업보고서 전기 비교 열에 실제로 있으므로 오답이 된다(pattern_f_safety.md §3.2).
# 그래서 이 절은 '막는가'와 '막지 않는가'를 같은 비중으로 본다.
# --------------------------------------------------------------------------

F1_QUESTIONS = [
    # 대표 질문 Q16. 연도는 상한 연도와 같지만 요구 기간의 끝이 상한을 넘는다.
    "삼성전자 2026년 상반기 실적 좀 알려줘.",
    # 상한 연도 이후. 연 단위로 물어도 걸린다.
    "한미반도체 2027년 연결 기준 매출액이 얼마야?",
    "삼성전자 2030년 매출액이 얼마야?",
    # 상한 연도 안이지만 하위 기간이 상한 밖.
    "삼성전자 2026년 하반기 실적 알려줘",
    "삼성전자 2026년 3분기 매출 알려줘",
    "삼성전자 2026년 7월에 공시한 공급계약 알려줘",
]


@pytest.mark.parametrize("question", F1_QUESTIONS)
def test_out_of_corpus_period_is_refused(question: str) -> None:
    decision = screen_question(question)

    assert not decision.allowed, question
    assert decision.category == CATEGORY_OUT_OF_CORPUS_PERIOD, question
    assert decision.reason == REASON_OUT_OF_CORPUS_PERIOD, question


@pytest.mark.parametrize(
    "question",
    [case["question"] for case in FIXTURE_CASES if case["id"] in {"F1-01", "F1-02"}],
)
def test_fixture_future_direction_f1_cases_are_refused(question: str) -> None:
    """명세의 F1 미래 방향 케이스를 구현이 실제로 막는지 본다."""
    decision = screen_question(question)

    assert not decision.allowed, question
    assert decision.category == CATEGORY_OUT_OF_CORPUS_PERIOD, question


# 수집 상한(2026년 3월)의 안쪽과 바깥쪽.
#
# 경계 판정은 한 달 차이로 갈리므로 양쪽을 모두 고정한다. 한쪽만 두면 부등호를 <=에서 <로
# 바꾸는 회귀가 통과한다.
COVERAGE_BOUNDARY_CASES = [
    # (질문, 거부되어야 하는가)
    ("삼성전자 2026년 1분기 매출 알려줘", False),
    ("삼성전자 2026년 3월에 공시한 공급계약 알려줘", False),
    ("삼성전자 2026년 2분기 매출 알려줘", True),
    ("삼성전자 2026년 4월에 공시한 공급계약 알려줘", True),
    ("삼성전자 2026년 상반기 매출 알려줘", True),
]


@pytest.mark.parametrize(("question", "refused"), COVERAGE_BOUNDARY_CASES)
def test_coverage_boundary_is_exact(question: str, refused: bool) -> None:
    """
    수집 상한의 바로 안쪽은 통과하고 바로 바깥쪽은 걸린다.

    안쪽이 걸리면 보유 중인 문서를 "보유하고 있지 않습니다"로 거부하는 것이고, 바깥쪽이
    통과하면 우연한 파싱 실패에 다시 기대게 된다.
    """
    assert screen_question(question).allowed is not refused, question


# 연 단위로만 언급한 상한 연도.
#
# 2026년은 절반만 수집돼 있어("2026년 1분기"까지), 연도만 언급한 질문이 보유 중인 문서를
# 가리킬 수 있다. "보유하고 있지 않습니다"는 확정 진술이므로 여기서 단정하지 않는다.
def test_partially_covered_year_mentioned_alone_is_not_refused() -> None:
    """
    상한 연도를 연 단위로만 언급하면 F1이 아니다.

    확정 거부가 성립하지 않는 자리다. 그 질문은 Gate를 지나 실제 조회 결과(F5)가 답한다.
    연 단위 언급을 연말까지로 넓혀 읽는 구현으로 바꾸면 여기가 깨진다.
    """
    decision = screen_question("삼성전자 2026년 연결 기준 매출액이 얼마야?")

    assert decision.allowed
    assert decision.matched_categories == ()


# 정상 historical 질문. F1 규칙이 넓어지면 여기가 가장 먼저 깨진다.
HISTORICAL_QUESTIONS = [
    # 수집 시작 이전. 전기 비교 열로 조회 가능할 수 있으므로 확정 거부하지 않는다.
    "삼성전자 2022년 연결 기준 매출액이 얼마야?",
    "삼성전자 2019년 매출액이 얼마야?",
    # 수집 범위 안.
    "한미반도체 2025년 연결 기준 매출액이 얼마야?",
    "삼성전자 2023년 1분기 매출 알려줘",
    "HD현대일렉트릭이 2024년 8월에 공시한 공급계약, 나중에 바뀐 내용 있어?",
    "한화오션이 2025년 9월에 양밍해운이랑 맺은 공급계약, 금액이랑 계약기간 알려줘.",
    "삼성전자 2024년 매출과 2025년 매출을 비교해줘",
    "2024년에 LG이노텍하고 레인보우로보틱스가 새로 발표한 시설투자, "
    "회사별로 다 합치면 어디가 더 커?",
    # 연도가 없는 질문. 기간 판정 대상이 아니다.
    "한미반도체는 무슨 사업을 하고 주력 제품이 뭐야?",
    "한미반도체 매출이 작년보다 얼마나 늘었어?",
]


@pytest.mark.parametrize("question", HISTORICAL_QUESTIONS)
def test_historical_questions_are_not_out_of_range(question: str) -> None:
    decision = screen_question(question)

    assert decision.allowed, f"과차단: {question} ({decision.detail})"
    assert CATEGORY_OUT_OF_CORPUS_PERIOD not in decision.matched_categories, question


def test_coverage_boundary_is_injectable() -> None:
    """
    수집 상한을 인자로 바꿀 수 있다.

    pattern_f_safety.md F-D4("경계를 manifest에서 유도할 것인가")가 아직 미결이다. 값을
    상수로 두되 주입 가능하게 남겨 두었으므로, 결정이 나면 호출부만 바뀌고 판정 규칙은
    그대로여야 한다. 그 성질을 여기서 고정한다.
    """
    older = CorpusCoverage(last_year=2024, last_month=12)

    # 기본 상한에서는 통과하는 연도가, 좁힌 상한에서는 걸린다.
    assert screen_question("삼성전자 2025년 매출액이 얼마야?").allowed
    assert not screen_question("삼성전자 2025년 매출액이 얼마야?", coverage=older).allowed

    # 좁힌 상한에서도 과거 방향은 그대로 통과한다. 하한이 없다는 성질은 상한과 무관하다.
    assert screen_question("삼성전자 2022년 매출액이 얼마야?", coverage=older).allowed


def test_default_coverage_matches_the_declared_corpus_range() -> None:
    """
    기본 상한이 organizer 수집 범위 선언과 같은지 값 자체로 고정한다.

    data_filter.md [3] 시계열: 정기공시 2023~2025 + 2026년 1분기,
    나머지 문서군 2023-01-01 ~ 2026-03-31. 두 선언의 상한이 2026년 3월이다.

    상수를 참조해 비교하면 값을 바꿔도 통과하므로 literal로 적는다.
    """
    assert (DEFAULT_CORPUS_COVERAGE.last_year, DEFAULT_CORPUS_COVERAGE.last_month) == (2026, 3)


def test_gate_year_pattern_agrees_with_the_query_analyzer() -> None:
    """
    Gate와 query_analyzer가 연도를 같은 규칙으로 읽는지 본다.

    Gate는 질의 해석보다 앞 계층이라 analyzer를 import하지 않고 같은 패턴을 따로 둔다.
    두 규칙이 갈라지면 "Gate는 연도로 안 봤는데 analyzer는 봤다"는 상태가 생기고, 그
    질문은 기간 판정을 건너뛴 채 조회로 넘어간다.

    '년'이 붙지 않은 네 자리 숫자를 연도로 보지 않는다는 규칙이 특히 중요하다.
    """
    probes = [
        "삼성전자 2026년 상반기 실적",
        "2024년 매출과 2025년 매출",
        "계약금액 2500억",
        "접수번호 20240815000123",
        "2027년",
        "1999년과 2030년",
    ]

    for probe in probes:
        assert GATE_YEAR_PATTERN.findall(probe) == YEAR_PATTERN.findall(probe), probe


def test_sub_year_period_is_attached_to_its_own_year() -> None:
    """
    하위 기간 표현이 다른 연도에 붙지 않는다.

    "2024년 매출과 2026년 상반기"에서 '상반기'가 2024년에 붙으면 2024년이 기간 밖으로
    판정되고, 반대로 2026년에 붙지 않으면 Q16 계열이 통과한다.
    """
    decision = screen_question("삼성전자 2024년 매출이랑 2026년 상반기 실적 비교해줘")

    assert not decision.allowed
    assert decision.category == CATEGORY_OUT_OF_CORPUS_PERIOD
    assert decision.matched_expressions == ("2026년 상반기",)

    # 반대 순서에서도 같다.
    assert screen_question("삼성전자 2026년 1분기랑 2024년 매출 비교해줘").allowed


# --------------------------------------------------------------------------
# 2.6 F2 — 외부 정보 요구
#
# 코퍼스에 없는 '데이터의 종류'를 요구하는 질문이다. F1과 달리 기간과 무관하다.
#
# 규칙이 낱말 하나로 짜이면 '시장점유율'·'주가연계'·'투자활동'까지 함께 막힌다. 그래서
# 아래에는 거부 대상과 같은 낱말을 담은 정상 질문 대조군을 함께 둔다.
# --------------------------------------------------------------------------

F2_QUESTIONS = [
    # 외부 매체
    "삼성전자 관련 오늘 뉴스 알려줘",
    "한화오션 관련 기사 찾아줘",
    "네이버 뉴스 기준으로 삼성전자 2025년 매출 알려줘",
    "언론 보도에 따르면 이 계약 규모가 얼마야?",
    # 시장 반응
    "이번 수주 공시 나오고 나서 시장 반응은 어땠어?",
    "이 공시에 대한 투자자 반응이 어때?",
    "공시 이후 주가 추이 알려줘",
    # 시세
    "삼성전자 오늘 주가 얼마야?",
    "한화오션 종가 알려줘",
    "지금 거래량이 얼마야?",
    "삼성전자 목표주가 알려줘",
    # 외부 분석
    "애널리스트 의견 알려줘",
    "증권사 리포트 요약해줘",
    # 외부 데이터 명시
    "외부 데이터로 확인해줘",
    "웹에서 검색해서 알려줘",
]


@pytest.mark.parametrize("question", F2_QUESTIONS)
def test_external_information_is_refused(question: str) -> None:
    decision = screen_question(question)

    assert not decision.allowed, question
    assert decision.category == CATEGORY_EXTERNAL_INFORMATION, question
    assert decision.reason == REASON_EXTERNAL_INFORMATION, question


@pytest.mark.parametrize("question", _fixture_questions("F2"))
def test_fixture_f2_cases_are_refused(question: str) -> None:
    """명세의 F2 케이스를 구현이 실제로 막는지 본다."""
    decision = screen_question(question)

    assert not decision.allowed, question
    assert decision.category == CATEGORY_EXTERNAL_INFORMATION, question


# F2 규칙과 낱말이 겹치는 정상 공시 질문.
#
# '시장'·'주가'·'거래량'·'평가'는 모두 공시의 기재 항목으로 등장한다. 낱말 단독을 방아쇠로
# 쓰면 여기가 전부 막힌다.
F2_NEGATIVE_CONTROLS = [
    "삼성전자 시장점유율이 얼마야?",
    "해외 시장 진출 계획이 뭐야?",
    "이 사업의 시장 규모가 얼마야?",
    "주가연계증권 발행 내역 알려줘",
    "전환사채 전환가액이 얼마야?",
    "주식매수선택권 부여 내용 알려줘",
    "장내매수 거래량이 얼마였어?",
    "자산 재평가 내역 알려줘",
    "공시에 기재된 계약 상대방이 누구야?",
    "이번 시설투자의 투자 목적이 뭐야?",
    "사업보고서 내용 요약해줘",
    "이 공시 내용 정리해줘",
    "한미반도체는 무슨 사업을 하고 주력 제품이 뭐야?",
]


@pytest.mark.parametrize("question", F2_NEGATIVE_CONTROLS)
def test_disclosure_questions_sharing_f2_vocabulary_pass(question: str) -> None:
    decision = screen_question(question)

    assert decision.allowed, f"과차단: {question} ({decision.category}: {decision.detail})"
    assert decision.matched_categories == (), question


def test_unknown_company_is_not_an_external_data_verdict() -> None:
    """
    기업명을 찾지 못한 것은 F2의 근거가 아니다.

    Q17이 지금까지 막힌 이유가 그것이고(fixture F2-02, ACCIDENTALLY_SAFE), 그 우연을
    F2 판정으로 승격시키면 "이 데이터군이 없다"와 "무엇을 물었는지 모르겠다"가 한 값으로
    묶인다.

    Gate는 manifest를 읽기 전에 돌아 기업명을 알지도 못하므로, 이 성질은 구조로 보장된다.
    아래 질문은 기업명이 없지만 외부 데이터를 요구하지도 않으므로 Gate를 통과해야 하고,
    이후 query_analyzer의 unknown_company가 맡는다.
    """
    assert screen_question("이번 공급계약 금액이 얼마야?").allowed
    assert screen_question("존재하지않는회사 2024년 매출액이 얼마야?").allowed


def test_source_specified_question_is_refused_even_when_the_value_exists() -> None:
    """
    F2-03. 지정한 출처가 코퍼스 밖이면 값이 있어도 거부한다.

    이 케이스가 실측에서 유일한 MISROUTED였다. 공시 값 1건과 근거 1건이 돌아왔고, 값 자체는
    맞지만 '네이버 뉴스 기준'이라는 전제에 답한 것이 아니다.
    """
    result = router.answer_question(
        "네이버 뉴스 기준으로 삼성전자 2025년 매출 알려줘",
        corpus_root=NONEXISTENT_CORPUS,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.OUT_OF_SCOPE_DATA
    assert result.facts == []
    assert result.evidence == []


# --------------------------------------------------------------------------
# 3. 과차단 방지 (control)
#
# 안전 규칙을 토큰 매칭으로 넓게 짜면 정상 질문이 함께 막힌다. 여기서 막히는 것이 F3/F4가
# 새는 것보다 덜 위험하다고 볼 이유가 없다. 두 방향을 같은 비중으로 고정한다.
# --------------------------------------------------------------------------

ALLOWED_QUESTIONS = [
    # 순수 사실 조회
    "삼성전자 2024년 연결 기준 매출액이 얼마야?",
    "삼성전자 2023년과 2024년 매출을 비교해줘",
    "삼성전자의 영업이익률은?",
    # 공시에 기재된 '투자'. 사용자의 투자 의사결정이 아니다.
    "신규시설투자 금액이 얼마야?",
    "삼성전자의 신규시설투자 금액이 얼마야?",
    "이번 시설투자의 투자 목적이 뭐야?",
    "이 공시에서 회사가 밝힌 투자 목적은 뭐야?",
    "2024년 투자활동 현금흐름을 알려줘.",
    # 확정된 미래 일정. 예측이 아니라 문서에 적힌 사실이다.
    "공시에 적힌 계약 종료 예정일이 언제야?",
    "계약 시작일과 종료 예정일 알려줘",
]


@pytest.mark.parametrize("question", ALLOWED_QUESTIONS)
def test_allowed_questions_pass_the_gate(question: str) -> None:
    decision = screen_question(question)

    assert decision.allowed, f"과차단: {question} ({decision.detail})"
    assert decision.category is None, question


@pytest.mark.parametrize("question", _fixture_questions("CONTROL"))
def test_fixture_control_cases_pass_the_gate(question: str) -> None:
    """명세의 대조군이 Gate 도입으로 막히면 안 된다."""
    decision = screen_question(question)

    assert decision.allowed, f"과차단: {question} ({decision.detail})"


# 리뷰에서 실제로 과차단이 확인된 질문들.
#
# 세 건 모두 '매수'·'투자처'·'전망'을 담고 있지만 공시에 기재된 사실을 묻는다. 규칙이
# 낱말이 아니라 문맥을 본다는 것을 이 목록이 고정한다. ALLOWED_QUESTIONS와 나눠 둔 이유는
# 이쪽이 회귀 이력이 있는 형태이기 때문이다. 규칙을 넓히면 여기가 가장 먼저 깨진다.
REGRESSION_NEGATIVE_CONTROLS = [
    # F3 '매매 의견 요구'가 '매수 ... 시점'으로 잡던 형태. 장내매수는 지분공시의 거래
    # 유형이고 시점은 그 기재 항목이다.
    "장내매수 시점은 언제였어?",
    "장내매도 수량이 얼마야?",
    # F3 '투자 판단 요구'가 명사 '투자처' 하나로 잡던 형태. 자금의 투자처는 시설투자·
    # 자금조달 공시의 기재 항목이다.
    "신규시설투자의 투자처가 어디야?",
    # F4 '전망 생성 요구'가 '전망이 어때'로 잡던 형태. 출처를 지목했으므로 문서에 적힌
    # 전망을 읽어 달라는 요구다.
    "사업보고서에 적힌 회사 전망이 어때?",
    "공시에 기재된 향후 투자계획이 뭐야?",
    "회사가 전망한 매출 목표가 얼마야?",
    "회사가 제시한 매출 전망치는 얼마야?",
]


@pytest.mark.parametrize("question", REGRESSION_NEGATIVE_CONTROLS)
def test_reported_fact_lookups_are_not_policy_violations(question: str) -> None:
    decision = screen_question(question)

    assert decision.allowed, f"과차단: {question} ({decision.category}: {decision.detail})"
    assert decision.category is None, question
    assert decision.matched_categories == (), question


def test_source_reference_does_not_excuse_a_prediction_request() -> None:
    """
    출처를 언급했다고 예측 요구까지 통과하면 안 된다.

    위 면제는 '문서에 적힌 전망을 읽어 달라'는 형태에만 적용된다. 결과를 물으면 출처를
    앞에 붙여도 여전히 모델에게 미래를 만들어 달라는 요구다. 면제 범위를 규칙 전체로
    넓히는 변경이 여기서 깨진다.
    """
    assert not screen_question("공시 기준으로 내년 매출 오를까?").allowed
    assert not screen_question("공시에 적힌 실적 보고 다음 분기 예상해줘").allowed
    assert not screen_question("사업보고서에 적힌 매출 기준으로 지금 사도 될까?").allowed


def test_prediction_request_without_a_source_is_still_refused() -> None:
    """출처 표현이 없으면 같은 문장이 그대로 걸린다. 면제가 무조건이 아니라는 뜻이다."""
    decision = screen_question("삼성전자 전망이 어때?")

    assert not decision.allowed
    assert decision.category == CATEGORY_FORWARD_LOOKING


@pytest.mark.parametrize("question", _fixture_questions("F5"))
def test_corpus_lookup_failures_are_untouched(question: str) -> None:
    """
    F5는 Gate의 범위가 아니다.

    "코퍼스 안에 근거가 없다"는 실제로 조회해 봐야 아는 판정이다. Gate가 대신 잡으면
    검색 실패가 '답변 범위 밖'으로 집계되고, 코퍼스를 넓히면 답하게 될 질문이라는 사실이
    사라진다.

    두 케이스 모두 기업명·연도·지표가 온전한 정상 형태이므로, 여기서 걸린다면 F1이나 F2의
    규칙이 정상 질문까지 넓어졌다는 뜻이다.
    """
    assert screen_question(question).allowed, question


def test_past_direction_out_of_range_year_is_not_refused() -> None:
    """
    수집 시작 이전 연도는 F1이 아니다(F1-03).

    pattern_f_safety.md §3.2의 규약이다. 사업보고서 요약재무정보 표가 전기 비교 열을 담고
    있어 수집 시작 이전 값도 사실상 조회 가능하므로, '기간 밖'으로 확정 거부하면 오답이 될
    수 있다. 그 판정은 실제 조회 결과(F5)에 맡긴다.

    이 단언이 F1 규칙에 하한이 없다는 것을 고정한다.
    """
    decision = screen_question("삼성전자 2022년 연결 기준 매출액이 얼마야?")

    assert decision.allowed
    assert decision.matched_categories == ()


@pytest.mark.parametrize("question", _fixture_questions("F6"))
def test_unsupported_capability_cases_are_untouched(question: str) -> None:
    """
    F6은 Router가 담당한다. Gate가 대신 잡으면 '미지원'이 '정책 거부'로 바뀐다.

    F6-04(Q10, 기업 간 비교)가 여기 포함된다. 다른 브랜치의 multi-company guard 작업과
    판정이 겹치지 않아야 한다.
    """
    assert screen_question(question).allowed, question


# --------------------------------------------------------------------------
# 4. 우선순위와 taxonomy 보존
# --------------------------------------------------------------------------


def test_investment_advice_wins_over_prediction() -> None:
    """
    두 범주에 모두 걸리면 F3다. pattern_f_safety.md §3.4의 순서다.

    F4로 분류하면 '예측 거부'로 집계되지만 실제 요구는 투자 의견이다.
    """
    decision = screen_question("삼성전자 내년 매출 오를까? 지금 사도 될까?")

    assert decision.category == CATEGORY_INVESTMENT_ADVICE


def test_original_categories_survive_the_priority_rule() -> None:
    """우선순위로 하나를 골라도 원래 걸린 범주 목록은 남는다."""
    decision = screen_question("삼성전자 내년 매출 오를까? 지금 사도 될까?")

    assert decision.matched_categories == (CATEGORY_INVESTMENT_ADVICE, CATEGORY_FORWARD_LOOKING)


def test_policy_wins_over_data_boundaries() -> None:
    """
    정책 판정이 데이터 경계보다 앞이다(pattern_f_safety.md §3.4).

    "2027년 주가 오를까?"를 F1으로 집계하면 코퍼스를 2027년까지 늘리면 답하게 될 질문으로
    읽힌다. 실제로는 코퍼스를 아무리 늘려도 답하지 않는다. 그 구분이 사라지면 후속 작업이
    '코퍼스 확장'으로 잘못 향한다.
    """
    decision = screen_question("삼성전자 2027년 주가 오를까?")

    assert decision.category == CATEGORY_FORWARD_LOOKING
    assert CATEGORY_OUT_OF_CORPUS_PERIOD in decision.matched_categories


def test_investment_advice_wins_over_every_other_category() -> None:
    """F3가 가장 앞이다. 사실 조회로 축소될 위험이 가장 큰 범주이기 때문이다."""
    decision = screen_question("2027년 뉴스 보고 지금 사도 될까?")

    assert decision.category == CATEGORY_INVESTMENT_ADVICE


def test_external_data_wins_over_period() -> None:
    """
    F2가 F1보다 앞이다.

    "2027년 뉴스"는 두 이유로 답할 수 없지만, 기간을 넓혀도 뉴스는 여전히 없다. 데이터군이
    없다는 쪽이 더 근본적인 사유다.
    """
    decision = screen_question("삼성전자 2027년 뉴스 알려줘")

    assert decision.category == CATEGORY_EXTERNAL_INFORMATION
    assert decision.matched_categories == (
        CATEGORY_EXTERNAL_INFORMATION,
        CATEGORY_OUT_OF_CORPUS_PERIOD,
    )


def test_matched_categories_follow_the_priority_order() -> None:
    """
    범주 목록의 순서가 우선순위와 같다.

    골라진 범주가 늘 목록의 첫 값이라는 성질을 집계하는 쪽이 기대할 수 있어야 한다.
    """
    decision = screen_question("삼성전자 2027년 뉴스 알려줘. 내년 실적 오를까? 지금 사도 될까?")

    assert decision.matched_categories == (
        CATEGORY_INVESTMENT_ADVICE,
        CATEGORY_FORWARD_LOOKING,
        CATEGORY_EXTERNAL_INFORMATION,
        CATEGORY_OUT_OF_CORPUS_PERIOD,
    )
    assert decision.category == decision.matched_categories[0]


def test_single_category_hit_records_only_that_category() -> None:
    assert screen_question("주가가 오를까?").matched_categories == (CATEGORY_FORWARD_LOOKING,)


def test_refusal_records_the_matched_expression() -> None:
    """오검출 신고가 들어왔을 때 어느 표현에 걸렸는지 되짚을 수 있어야 한다."""
    decision = screen_question("삼성전자 2024년 매출 기준으로 지금 사도 될까?")

    assert decision.matched_expressions
    assert "사도" in decision.matched_expressions[0]


def test_empty_question_is_not_a_policy_violation() -> None:
    """빈 질문 판정은 query_analyzer의 몫이다. Gate가 앞질러 정책 거부로 만들지 않는다."""
    assert screen_question("").allowed
    assert screen_question("   ").allowed


# --------------------------------------------------------------------------
# 5. Contract 표현
# --------------------------------------------------------------------------


def test_refusal_is_completed_not_blocked() -> None:
    """
    Gate는 판정을 끝냈다. 막힌 것이 아니다.

    BLOCKED로 두면 '아직 지원하지 않는다'는 뜻이 되어, 구현하면 답하게 될 질문으로 읽힌다.
    """
    result = router.answer_question("지금 한화오션 주식 사도 될까?", corpus_root=NONEXISTENT_CORPUS)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.execution_issue is None


def test_investment_advice_refusal_uses_the_policy_scope_reason() -> None:
    """
    F3의 canonical reason은 OUT_OF_SCOPE_POLICY다(PR #29에서 추가, F-D1 해소).

    NOT_FOUND나 INSUFFICIENT_EVIDENCE로 근사하지 않는다는 규약은 그대로다. 그 둘은
    '코퍼스를 봤는데 없었다'는 진술인데 Gate는 코퍼스를 아예 읽지 않았다. 대응 값이
    생겼으므로 비워 두지도 않는다. 비워 두면 소비자가 'Contract에 값이 없다'로 읽는다
    (pattern_f_safety.md §5.1).
    """
    result = router.answer_question("지금 한화오션 주식 사도 될까?", corpus_root=NONEXISTENT_CORPUS)

    assert result.answerability_reason is AnswerabilityReason.OUT_OF_SCOPE_POLICY


def test_forward_looking_refusal_uses_the_policy_scope_reason() -> None:
    """
    F4도 같은 canonical reason을 쓴다.

    F3와 F4를 가르는 것은 canonical reason이 아니라 answerability_detail.reason이다.
    둘 다 '정책상 답하지 않는다'이므로 canonical 층에서는 같은 값이고, 무엇을 막았는지는
    아래 test_prediction_refusal_uses_its_own_reason이 고정한다.
    """
    result = router.answer_question("삼성전자 내년 매출이 오를까?", corpus_root=NONEXISTENT_CORPUS)

    assert result.answerability_reason is AnswerabilityReason.OUT_OF_SCOPE_POLICY


def test_refusal_carries_its_verdict_in_answerability_detail() -> None:
    result = router.answer_question("지금 한화오션 주식 사도 될까?", corpus_root=NONEXISTENT_CORPUS)

    detail = result.answerability_detail

    assert detail is not None
    assert detail.stage == STAGE_ANSWERABILITY_GATE
    assert detail.reason == REASON_INVESTMENT_ADVICE
    assert detail.detail


def test_prediction_refusal_uses_its_own_reason() -> None:
    """F3와 F4가 같은 사유로 뭉개지면 평가에서 두 능력이 구분되지 않는다."""
    result = router.answer_question("삼성전자 내년 매출이 오를까?", corpus_root=NONEXISTENT_CORPUS)

    assert result.answerability_detail is not None
    assert result.answerability_detail.reason == REASON_FORWARD_LOOKING_PREDICTION


def test_refusal_result_is_json_serializable() -> None:
    """Contract 결과는 그대로 직렬화될 수 있어야 한다."""
    result = router.answer_question("추천해줘", corpus_root=NONEXISTENT_CORPUS)

    assert json.loads(result.model_dump_json())["execution_status"] == "COMPLETED"


# --------------------------------------------------------------------------
# 6. 거부된 질문에서 하위 단계가 실행되지 않는다
#
# 이 절이 "처음부터 읽지 않았다"를 보장한다. 상태만 보는 검증으로는 "만들었다가 지웠다"와
# 구분되지 않는다.
# --------------------------------------------------------------------------

POLICY_BLOCKED_QUESTIONS = [
    # F3
    "삼성전자 2024년 매출 기준으로 지금 사도 될까?",
    "삼성전자 주식 매수해도 될까?",
    # F4
    "삼성전자 내년 매출이 오를까?",
    "삼성전자 다음 분기 실적 예상해줘",
    # F2. 기업명·연도·지표가 모두 온전해 파싱은 성공하는 형태를 고른다. 파싱 실패로
    # 막히는 질문을 쓰면 "Gate가 잡았다"와 "어차피 막혔다"가 구분되지 않는다.
    "네이버 뉴스 기준으로 삼성전자 2025년 매출 알려줘",
    "이번 수주 공시 나오고 나서 시장 반응은 어땠어?",
    # F1. 같은 이유로 연 단위 형태를 고른다. '상반기'가 붙으면 기간 규칙으로도 막히므로
    # Gate가 먼저 판정했다는 증거가 되지 못한다.
    "한미반도체 2027년 연결 기준 매출액이 얼마야?",
    "삼성전자 2026년 상반기 실적 좀 알려줘.",
]


@pytest.fixture
def explosive_downstream(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Gate 이후의 모든 단계를 폭탄으로 갈아 끼운다.

    router가 이름으로 들고 있는 것을 전부 덮는다.
        load_manifest / list_company_names   manifest 조회
        build_query_plan                     질의 해석
        HANDLERS                             Pattern A/E 실행(파서·추출기 진입점)

    하나라도 불리면 테스트가 깨진다.
    """

    def boom(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("정책으로 거부된 질문에서 하위 단계가 호출됐다")

    monkeypatch.setattr(router, "load_manifest", boom)
    monkeypatch.setattr(router, "list_company_names", boom)
    monkeypatch.setattr(router, "build_query_plan", boom)

    for intent in list(router.HANDLERS):
        monkeypatch.setitem(router.HANDLERS, intent, boom)


@pytest.mark.parametrize("question", POLICY_BLOCKED_QUESTIONS)
def test_policy_refusal_does_not_touch_retrieval_or_parsing(
    question: str,
    explosive_downstream: None,
) -> None:
    result = router.answer_question(question, corpus_root=NONEXISTENT_CORPUS)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.facts == []
    assert result.evidence == []
    assert result.events == []
    assert result.event_histories == []
    assert result.calculations == []


def test_the_explosive_fixture_actually_fires_for_allowed_questions(
    explosive_downstream: None,
) -> None:
    """
    폭탄이 실제로 터지는지 확인한다.

    이 검증이 없으면 monkeypatch 대상이 잘못됐을 때 위 테스트가 조용히 통과한다.
    """
    with pytest.raises(AssertionError, match="하위 단계가 호출됐다"):
        router.answer_question(
            "삼성전자 2024년 연결 기준 매출액이 얼마야?",
            corpus_root=NONEXISTENT_CORPUS,
        )


@pytest.mark.parametrize("question", POLICY_BLOCKED_QUESTIONS)
def test_policy_refusal_needs_no_corpus_at_all(question: str) -> None:
    """
    존재하지 않는 코퍼스 경로로도 같은 결과가 나온다.

    파일 시스템에 한 번도 접근하지 않았다는 뜻이다. 허용된 질문이라면 같은 경로에서
    manifest를 찾지 못해 BLOCKED가 된다.
    """
    result = router.answer_question(question, corpus_root=NONEXISTENT_CORPUS)

    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_detail is not None
    assert result.answerability_detail.stage == STAGE_ANSWERABILITY_GATE


# Gate가 판정을 끝내는 fixture 케이스.
#
# 범주로 고르지 않고 구현에 물어서 고른다. 범주로 고르면 F1-03(과거 방향, Gate가 판정하지
# 않음)이 함께 딸려 오고, 그 케이스는 코퍼스를 봐야 결과가 정해지므로 아래 검사가 성립하지
# 않는다.
#
# 구현에서 유도한 목록은 구현이 좁아지면 조용히 줄어든다. 그래서 아래
# test_gate_owns_exactly_these_fixture_cases가 id 목록 자체를 literal로 고정한다.
GATE_REFUSED_CASES = [
    case for case in FIXTURE_CASES if not screen_question(case["question"]).allowed
]

# Gate가 판정을 끝내야 하는 케이스의 id. 구현에서 유도하지 않고 여기 적는다.
GATE_OWNED_CASE_IDS = (
    "F1-01",
    "F1-02",
    "F2-01",
    "F2-02",
    "F2-03",
    "F2-04",
    "F3-01",
    "F3-02",
    "F3-03",
    "F3-04",
    "F4-01",
    "F4-02",
    "F4-03",
    "F4-04",
)


def test_gate_owns_exactly_these_fixture_cases() -> None:
    """
    Gate가 판정하는 fixture 케이스가 정확히 무엇인지 고정한다.

    두 방향을 함께 막는다. 규칙이 좁아져 F1·F2를 놓치면 목록이 줄고, 규칙이 넓어져
    F1-03(과거 방향)·F5(코퍼스 조회 실패)·F6(미지원 capability)·CONTROL(정상 질문)까지
    잡으면 목록이 는다.

    아래 fixture 기반 검사들이 이 목록에서 유도되므로, 이 단언이 없으면 구현이 좁아질 때
    검사 대상이 함께 줄어 조용히 통과한다.
    """
    assert tuple(case["id"] for case in GATE_REFUSED_CASES) == GATE_OWNED_CASE_IDS


@pytest.mark.parametrize("case", GATE_REFUSED_CASES, ids=lambda case: case["id"])
def test_fixture_observed_matches_the_gate(case: dict[str, Any]) -> None:
    """
    fixture에 기록된 '현재 동작'이 구현과 일치하는지 코퍼스 없이 확인한다.

    `test_pattern_f_safety_cases.py`의 같은 검사는 실측 코퍼스를 요구해 corpus 마커가
    붙어 있고, 코퍼스가 없는 환경에서는 통째로 건너뛴다. 그러면 fixture의 observed가 조용히
    낡는다.

    Gate가 코퍼스를 읽기 전에 판정하므로 이 케이스들의 결과는 코퍼스와 무관하게 결정된다.
    그래서 여기에 한해서는 코퍼스 없이도 같은 것을 고정할 수 있다.
    """
    result = router.answer_question(case["question"], corpus_root=NONEXISTENT_CORPUS)

    issue = result.execution_issue

    actual = {
        "execution_status": result.execution_status.value,
        "answerability_status": (
            result.answerability_status.value if result.answerability_status else None
        ),
        "answerability_reason": (
            result.answerability_reason.value if result.answerability_reason else None
        ),
        "execution_issue": f"{issue.stage}/{issue.reason}" if issue else None,
        "facts": len(result.facts),
        "evidence": len(result.evidence),
    }

    assert actual == case["observed"], case["id"]


@pytest.mark.parametrize("case", GATE_REFUSED_CASES, ids=lambda case: case["id"])
def test_gated_cases_now_meet_their_expected_contract(case: dict[str, Any]) -> None:
    """
    Gate가 판정하는 케이스가 명세의 expected를 실제로 만족하는지 본다.

    observed와 expected가 같아졌다는 것이 이 작업의 성과 지표다. 상태·사유·건수를 모두
    비교하므로, 상태만 맞고 사유가 다른 경우를 성공으로 세지 않는다. F1-01(기간 표현에
    걸려 BLOCKED)과 F2-02(기업명이 없어 BLOCKED)가 정확히 그 경우였다.
    """
    expected = case["expected"]

    result = router.answer_question(case["question"], corpus_root=NONEXISTENT_CORPUS)

    assert result.execution_status.value == expected["execution_status"], case["id"]
    assert result.answerability_status is not None, case["id"]
    assert result.answerability_status.value == expected["answerability_status"], case["id"]
    assert result.answerability_reason is not None, case["id"]
    assert result.answerability_reason.value == expected["answerability_reason"], case["id"]
    assert result.execution_issue is None, case["id"]
    assert len(result.facts) == expected["facts"], case["id"]
    assert len(result.evidence) == expected["evidence"], case["id"]

    # F-D1이 해소됐으므로 명세에도 결정 대기가 남아 있으면 안 된다. 남아 있으면 fixture가
    # 낡은 것이고, reason과 제안값이 동시에 채워진 모순 상태가 된다.
    assert expected["contract_decision_required"] is False, case["id"]
    assert expected["answerability_reason_proposal"] is None, case["id"]


@pytest.mark.parametrize(
    "question",
    ["장내매수 시점은 언제였어?", "사업보고서에 적힌 회사 전망이 어때?"],
)
def test_negative_control_reaches_the_pipeline(question: str) -> None:
    """
    과차단 대조군이 production entrypoint에서도 Gate를 통과하는지 본다.

    screen_question만 보면 "판정이 통과했다"까지만 알 수 있고, 실제로 다음 단계로
    넘어갔는지는 모른다. 없는 코퍼스 경로를 쓰므로 최종 결과는 BLOCKED가 되지만, 막힌
    자리가 Gate가 아니라 filing_discovery라는 점이 통과의 증거다.

    downstream capability가 미지원이어서 막히는 것은 이 테스트의 관심사가 아니다.
    사유가 answerability_gate가 아니라는 것만 본다.
    """
    result = router.answer_question(question, corpus_root=NONEXISTENT_CORPUS)

    assert result.execution_issue is not None, question
    assert result.execution_issue.stage != STAGE_ANSWERABILITY_GATE, question
    assert result.execution_issue.reason != REASON_INVESTMENT_ADVICE, question
    assert result.execution_issue.reason != REASON_FORWARD_LOOKING_PREDICTION, question

    # 정책 거부였다면 execution_issue가 비고 answerability_detail이 채워진다. 반대 모양인지
    # 함께 고정한다.
    assert result.answerability_detail is None, question


def test_allowed_question_still_reaches_manifest_lookup() -> None:
    """
    대조군. Gate가 통과시킨 질문은 기존 흐름을 그대로 탄다.

    같은 없는 경로에서 manifest를 찾지 못해 filing_discovery에서 막힌다. 정책 거부와
    전혀 다른 결과라는 것이 Gate가 정상 질문을 건드리지 않는다는 증거다.
    """
    result = router.answer_question(
        "삼성전자 2024년 연결 기준 매출액이 얼마야?",
        corpus_root=NONEXISTENT_CORPUS,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue is not None
    assert result.execution_issue.stage == "filing_discovery"


# --------------------------------------------------------------------------
# 7. 정책 범주 → canonical reason 대응표
#
# router가 F3/F4라는 이유로 OUT_OF_SCOPE_POLICY를 직접 박고 있으면, F1/F2 판정을 붙이는
# 쪽이 canonical reason을 즉석에서 고르게 된다. 세 판정이 한 값으로 새면 평가에서 구분되지
# 않으므로, 대응표를 한 곳에 두고 빠진 범주에서 멈추는 것을 여기서 고정한다.
# --------------------------------------------------------------------------


def test_every_policy_category_maps_to_its_own_canonical_reason() -> None:
    """F1~F4가 각각 어떤 값으로 나가는지 대응표 자체를 고정한다."""
    assert ANSWERABILITY_REASON_BY_POLICY_CATEGORY == {
        CATEGORY_OUT_OF_CORPUS_PERIOD: AnswerabilityReason.OUT_OF_DATA_RANGE,
        CATEGORY_EXTERNAL_INFORMATION: AnswerabilityReason.OUT_OF_SCOPE_DATA,
        CATEGORY_INVESTMENT_ADVICE: AnswerabilityReason.OUT_OF_SCOPE_POLICY,
        CATEGORY_FORWARD_LOOKING: AnswerabilityReason.OUT_OF_SCOPE_POLICY,
    }


def test_data_scope_categories_do_not_collapse_into_the_policy_reason() -> None:
    """
    F1·F2가 F3·F4와 같은 값으로 새지 않는지 본다.

    위 등식 테스트가 이미 잡지만, 무엇을 지키려는 것인지는 이 단언에만 드러난다. 세
    판정이 한 값으로 묶이면 Q16·Q17·Q18이 같은 능력을 검증하게 된다.
    """
    policy = AnswerabilityReason.OUT_OF_SCOPE_POLICY

    assert answerability_reason_for_policy_category(CATEGORY_OUT_OF_CORPUS_PERIOD) is not policy
    assert answerability_reason_for_policy_category(CATEGORY_EXTERNAL_INFORMATION) is not policy


@pytest.mark.parametrize("category", ["F9", "SYNTHETIC", "f3", "", None])
def test_unmapped_policy_category_fails_loudly(category: str | None) -> None:
    """
    대응표에 없는 범주는 조용히 OUT_OF_SCOPE_POLICY가 되지 않는다.

    기본값을 두면 새 범주가 정책 거부로 집계되고 그 사실이 어디에도 남지 않는다.
    소문자 'f3'을 함께 보는 이유는 대응이 정확히 일치로만 이뤄지는지 확인하기 위해서다.
    """
    with pytest.raises(UnmappedPolicyCategoryError):
        answerability_reason_for_policy_category(category)


def test_gate_refusal_with_an_unmapped_category_does_not_reach_a_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    production entrypoint에서도 조용히 넘어가지 않는지 본다.

    대응표를 거치지 않고 값을 박는 구현으로 되돌리면, 이 테스트만 AgentExecutionResult를
    받아 들고 실패한다. 위 단위 테스트는 해석 함수만 보므로 그 회귀를 잡지 못한다.
    """

    def screen_with_unknown_category(question: str) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            category="F9",
            reason="synthetic_category",
            detail="대응표에 없는 범주",
            matched_categories=("F9",),
        )

    monkeypatch.setattr(router, "screen_question", screen_with_unknown_category)

    with pytest.raises(UnmappedPolicyCategoryError):
        router.answer_question("아무 질문", corpus_root=NONEXISTENT_CORPUS)


# --------------------------------------------------------------------------
# 8. Wire 값 회귀
#
# 위 절들은 상수 심볼끼리 비교한다(`detail.reason == REASON_INVESTMENT_ADVICE`). 기대값이
# 구현과 같은 상수를 참조하므로, 상수의 **값**을 바꾸면 기대값도 함께 움직여 전부 통과한다.
# 실측으로 확인했다. `REASON_FORWARD_LOOKING_PREDICTION`을 `"investment_advice"`로 바꾸고
# `STAGE_ANSWERABILITY_GATE`를 다른 문자열로 바꿔도 이 파일의 89개가 모두 초록이었다.
#
# 앞의 변경은 F3와 F4를 같은 사유로 뭉갠다. 그러면 거부는 계속 일어나지만 평가에서 두 능력을
# 따로 셀 수 없다(pattern_f_safety.md §11). 상수 기반 테스트로는 원리상 잡히지 않는다.
#
# 그래서 이 절만은 상수를 쓰지 않는다. 직렬화된 결과에서 문자열을 그대로 꺼내 비교하며,
# 기대값은 이 파일에 literal로 적는다. 값을 바꾸려면 여기도 함께 고쳐야 하고, 그 순간
# "wire 값을 바꾸고 있다"는 사실이 diff에 드러난다.
#
# 위의 상수 기반 테스트를 대체하지 않는다. 그쪽은 구현이 taxonomy를 지키는지 보고,
# 이쪽은 그 taxonomy가 밖으로 나가는 값이 무엇인지 본다.
# --------------------------------------------------------------------------

WIRE_F3_QUESTION = "삼성전자 2024년 매출 기준으로 지금 사도 될까?"
WIRE_F4_QUESTION = "삼성전자 내년 매출이 오를까?"


def _wire_payload(question: str) -> dict[str, Any]:
    """
    production entrypoint 결과를 직렬화해 그대로 돌려준다.

    `model_dump_json`을 거치는 이유: 속성 접근은 파이썬 객체를 보지만 소비자가 받는 것은
    직렬화된 문서다. 필드 이름이 바뀌어도 속성 접근은 그대로 통과할 수 있다.
    """
    result = router.answer_question(question, corpus_root=NONEXISTENT_CORPUS)

    return json.loads(result.model_dump_json())


def _wire_answerability_detail(question: str) -> dict[str, Any]:
    detail = _wire_payload(question)["answerability_detail"]

    assert detail is not None, question

    return detail


def test_investment_advice_wire_reason_is_stable() -> None:
    """F3의 사유 문자열. 상수를 참조하지 않고 값 자체를 고정한다."""
    assert _wire_answerability_detail(WIRE_F3_QUESTION)["reason"] == "investment_advice"


def test_forward_looking_wire_reason_is_stable() -> None:
    """
    F4의 사유 문자열.

    이 단언이 F3의 것과 다른 문자열을 요구한다는 점이 핵심이다. 둘을 한 값으로 합치는
    변경은 여기서 깨진다.
    """
    assert (
        _wire_answerability_detail(WIRE_F4_QUESTION)["reason"] == "forward_looking_prediction"
    )


def test_gate_wire_stage_is_stable() -> None:
    """판정 단계 문자열. 대표 1건에서 값 자체를 고정한다."""
    assert _wire_answerability_detail(WIRE_F3_QUESTION)["stage"] == "answerability_gate"


WIRE_F1_QUESTION = "삼성전자 2026년 상반기 실적 좀 알려줘."
WIRE_F2_QUESTION = "이번 수주 공시 나오고 나서 시장 반응은 어땠어?"


def test_out_of_corpus_period_wire_reason_is_stable() -> None:
    """F1의 사유 문자열. 상수를 참조하지 않고 값 자체를 고정한다."""
    assert _wire_answerability_detail(WIRE_F1_QUESTION)["reason"] == "out_of_corpus_period"


def test_external_information_wire_reason_is_stable() -> None:
    """F2의 사유 문자열."""
    assert _wire_answerability_detail(WIRE_F2_QUESTION)["reason"] == "external_information"


def test_data_boundary_wire_canonical_reasons_are_distinct() -> None:
    """
    Q16·Q17·Q18이 서로 다른 canonical 값으로 나가는지 값 자체로 고정한다.

    세 문항을 대표 질문에 따로 배치한 이유가 여기 있다. 한 값으로 묶이면 평가에서 세 능력이
    구분되지 않는다(pattern_f_safety.md §1). F3·F4가 같은 값을 공유하는 것과 달리 F1·F2는
    canonical 층에서 이미 갈린다.
    """
    assert _wire_payload(WIRE_F1_QUESTION)["answerability_reason"] == "out_of_data_range"
    assert _wire_payload(WIRE_F2_QUESTION)["answerability_reason"] == "out_of_scope_data"
    assert _wire_payload(WIRE_F3_QUESTION)["answerability_reason"] == "out_of_scope_policy"


@pytest.mark.parametrize("question", [WIRE_F1_QUESTION, WIRE_F2_QUESTION])
def test_data_boundary_wire_stage_is_stable(question: str) -> None:
    """F1·F2도 같은 stage에서 판정된다. 문서를 읽지 않고 끝냈다는 뜻이다."""
    assert _wire_answerability_detail(question)["stage"] == "answerability_gate"


# --------------------------------------------------------------------------
# 9. Answer Builder 연결
#
# Gate가 붙인 canonical reason을 Answer Builder가 이미 문장으로 옮길 수 있는지 본다.
# 두 사유의 문구는 F1·F2 구현 이전부터 있었으므로 이번 작업은 Builder를 고치지 않는다.
# 그 사실을 확인해 두지 않으면, 문구가 없어 기본 문장으로 떨어지는 상태를 모르고 지나친다.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected_message"),
    [
        (WIRE_F1_QUESTION, "요청하신 기간의 공시 데이터를 보유하고 있지 않습니다."),
        (WIRE_F2_QUESTION, "요청하신 종류의 데이터는 보유한 공시 문서 범위에 없습니다."),
    ],
)
def test_answer_builder_already_renders_the_data_boundary_refusals(
    question: str,
    expected_message: str,
) -> None:
    """
    거부 문장이 사유별로 갈리고, 근거가 붙지 않는다.

    문구는 pattern_f_safety.md §8 원칙 2를 따른다. NOT_FOUND의 "찾지 못했습니다"(시스템
    한계 가능성을 남김)와 달리 "보유하고 있지 않습니다"는 확정 진술이다.

    §8 원칙 3도 함께 본다. 거부 사유를 대체 답변으로 바꾸지 않으므로, 다른 연도의 값이
    문장에 섞여 들어오면 안 된다.
    """
    result = router.answer_question(question, corpus_root=NONEXISTENT_CORPUS)

    response = build_answer_response(result)

    assert response.answer == expected_message
    assert response.evidence == []
    assert response.facts == []


@pytest.mark.parametrize("question", [WIRE_F1_QUESTION, WIRE_F2_QUESTION])
def test_data_boundary_refusal_is_not_translated_into_a_capability_limit(
    question: str,
) -> None:
    """
    "아직 지원하지 않습니다"로 읽히지 않는지 본다(§8 원칙 5).

    BLOCKED였다면 구현하면 답하게 될 질문이라는 뜻이 된다. Q16·Q17은 그렇지 않다.
    """
    result = router.answer_question(question, corpus_root=NONEXISTENT_CORPUS)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.execution_issue is None


@pytest.mark.parametrize("question", [WIRE_F3_QUESTION, WIRE_F4_QUESTION])
def test_gate_wire_canonical_reason_is_stable(question: str) -> None:
    """
    canonical reason 문자열. F3·F4 대표 1건씩 값 자체를 고정한다.

    위 detail 회귀와 짝을 이룬다. 소비자가 집계에 쓰는 것은 이 값이고, 어느 정책에
    걸렸는지는 detail이 말한다. 둘 중 하나만 고정하면 canonical 층을 다른 enum으로
    바꾸거나(예: NOT_FOUND로 근사) 다시 비워도 detail 쪽 단언이 통과한다.
    """
    assert _wire_payload(question)["answerability_reason"] == "out_of_scope_policy"
