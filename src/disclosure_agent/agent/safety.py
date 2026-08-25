"""질문 단계 안전 정책 판정 모듈 (Pattern F 중 F3·F4).

역할:
- 자연어 질문 하나를 받아 "이 질문에 답해도 되는가"만 판정한다. 문서를 읽지 않고,
  검색하지 않으며, QueryPlan도 만들지 않는다.

이번 슬라이스가 다루는 범주:
    F3  투자 판단·추천    매수/매도/보유 의견, 종목 선택 요구
    F4  미래 예측        아직 공시되지 않은 결과에 대한 전망·예측 생성 요구

    docs/agent_design/pattern_f_safety.md의 taxonomy를 따른다. F1(기간 밖)·F2(외부 정보)·
    F5(근거 없음)·F6(미지원 capability)는 이번 범위 밖이며, 여기서 판정하지 않는다.

왜 검색 이전인가:
    "삼성전자 2024년 매출 기준으로 지금 사도 될까?"는 기업명·연도·지표 토큰이 모두 있어
    현재 파이프라인에서 single_fact로 라우팅되고, 근거를 붙인 Fact 1건이 ANSWERABLE로
    돌아온다(fixture F3-03, risk=CRITICAL). 매수 여부 질문에 수치를 제시하면 형식만 거부이고
    실질은 투자 의견이다.

    문서를 읽은 뒤에 거부하면 응답에 근거를 붙이고 싶은 유혹이 생기고,
    evidence_and_answerability.md 원칙 7("거부 응답에는 Evidence를 붙이지 않습니다")이 깨진다.
    그래서 판정을 retrieval 이전으로 끌어올린다.

왜 순수 함수인가:
    이 모듈은 Contract 타입을 만들지 않는다. 정책 판정과 결과 조립을 한 곳에서 하면
    "무엇을 막았는가"와 "어떤 값으로 표현했는가"가 섞여, Contract가 개정될 때 정책 규칙까지
    다시 읽어야 한다. 판정 결과를 AgentExecutionResult로 옮기는 일은
    agent.normalization.refused_execution이 맡는다.

    덕분에 이 모듈은 코퍼스도 스키마도 없이 문자열만으로 전부 시험할 수 있다.

입력:
- 자연어 질문 문자열

출력:
- PolicyDecision. 허용 여부와, 거부한 경우 그 범주·사유·근거 표현.

TODO:
- F1(코퍼스 기간 밖) / F2(외부 정보 요구) 판정 추가
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CATEGORY_EXTERNAL_INFORMATION",
    "CATEGORY_FORWARD_LOOKING",
    "CATEGORY_INVESTMENT_ADVICE",
    "CATEGORY_OUT_OF_CORPUS_PERIOD",
    "REASON_FORWARD_LOOKING_PREDICTION",
    "REASON_INVESTMENT_ADVICE",
    "STAGE_ANSWERABILITY_GATE",
    "PolicyDecision",
    "screen_question",
]

# 정책 판정이 일어난 단계.
#
# answer_builder.py의 STAGE_* 목록에 넣지 않았다. 그 목록은 "파이프라인이 막힌 지점"의 어휘이고
# execution_issue에 실린다. Gate는 막힌 것이 아니라 판정을 끝낸 것이므로 execution_issue를
# 채우지 않으며, 이 값은 answerability_detail.stage에만 실린다
# (pattern_f_safety.md §5.2).
STAGE_ANSWERABILITY_GATE = "answerability_gate"

# 평가 taxonomy가 쓰는 범주 ID. pattern_f_safety.md §3의 표와 같은 값이다.
#
# 코드 구현은 하나의 gate지만 범주는 합치지 않는다. F3와 F4는 원인이 다르고, 원인이 다르면
# 후속 작업과 평가 집계가 다르다.
CATEGORY_INVESTMENT_ADVICE = "F3"
CATEGORY_FORWARD_LOOKING = "F4"

# 아직 screen_question이 만들지 않는 범주.
#
# 값만 여기 둔다. 판정 규칙은 없고, 이 모듈은 두 값을 반환하지 않는다. 그래도 ID를 두는
# 이유는 범주 어휘의 출처를 한 곳으로 두기 위해서다. normalization의
# ANSWERABILITY_REASON_BY_POLICY_CATEGORY가 이 값들을 canonical reason에 잇고 있으며,
# 두 범주의 판정을 붙이는 쪽은 대응표가 아니라 규칙만 추가하면 된다.
CATEGORY_OUT_OF_CORPUS_PERIOD = "F1"
CATEGORY_EXTERNAL_INFORMATION = "F2"

# answerability_detail.reason에 실리는 사유 문자열.
#
# Contract의 AnswerabilityReason이 아니다. F3와 F4는 canonical reason으로는 둘 다
# OUT_OF_SCOPE_POLICY 하나에 대응하며(PR #29에서 추가), 그 값 하나만으로는 투자 판단 요구와
# 예측 요구를 구분할 수 없다. 두 판정을 나누는 것은 아래 두 문자열이고, 이 값들은
# answerability_detail.reason에만 실린다(pattern_f_safety.md §5.1).
REASON_INVESTMENT_ADVICE = "investment_advice"
REASON_FORWARD_LOOKING_PREDICTION = "forward_looking_prediction"


@dataclass(frozen=True)
class PolicyDecision:
    """
    질문 하나에 대한 정책 판정.

    matched_categories를 따로 두는 이유:
        한 질문이 F3와 F4에 모두 해당할 수 있다("내년 실적 보고 사도 될까?"). category는
        우선순위로 고른 하나이지만, 평가에서 "이 질문이 원래 어느 범주들에 걸렸는가"를
        보려면 고르기 전 목록이 남아 있어야 한다. 우선순위 적용으로 원 범주가 사라지면
        F3와 F4의 실제 탐지율을 분리해 셀 수 없다.
    """

    allowed: bool
    category: str | None = None
    reason: str | None = None
    detail: str = ""
    matched_categories: tuple[str, ...] = ()
    matched_expressions: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# F3 — 투자 판단·추천
#
# 규칙을 짜는 기준 하나: "투자"라는 낱말 자체는 절대 방아쇠로 쓰지 않는다.
#
#     시설투자 / 투자금액 / 투자목적 / 투자활동 현금흐름 / 신규시설투자
#
# 는 전부 공시에 기재된 사실이고 정상 조회 대상이다. 낱말 하나로 막으면 "이번 시설투자의
# 투자 목적이 뭐야?"까지 함께 막힌다. 그래서 아래 규칙은 모두 '행위 + 판단 요구' 형태를
# 요구하며, 명사 단독으로는 걸리지 않는다.
#
# 같은 이유로 매수·매도도 단독으로 쓰지 않는다. 지분공시에는 장내매수·장내매도가 기재
# 사실로 등장하므로("장내매수 수량이 얼마야?"), 단독 토큰으로 막으면 조회 질문을 막는다.
# --------------------------------------------------------------------------

_F3_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "지금 사도 될까?" / "사도 돼?"
    #
    # 앞에 '회'가 오는 경우를 뺀다. '자회사도', '계열회사도'가 '사도'로 끝나기 때문이다.
    ("매수 판단 요구", re.compile(r"(?<!회)사도\s*(?:될|돼|되)")),
    # "지금 사야 할까?" / "살까?" / "살 만한 종목"
    ("매수 판단 요구", re.compile(r"사야\s*(?:할|돼|되|하)|살까|살\s*만한")),
    # "팔아야 해?" / "팔아도 될까?"
    ("매도 판단 요구", re.compile(r"팔아(?:도|야)\s*(?:될|돼|되|할|해|하)|팔까")),
    # "매수해도 될까?" / "매도하는 게 나아?"
    #
    # 매수·매도 뒤에 판단을 요구하는 어형이 붙은 경우만 본다.
    #
    # 앞에 거래 방식 표기가 오면 제외한다. 장내매수·장외매도·시간외매수는 지분공시에
    # 기재된 거래 유형이며, 그 뒤에 오는 명사는 기재 항목이다("장내매수 시점은 언제였어?").
    # 거래 유형을 지목한 질문은 이미 특정 거래를 가리키고 있으므로 의견 요구가 아니다.
    #
    # '시점'은 목록에서 뺐다. 매매 타이밍 문의로 읽힐 수도 있지만 공시에 기재된 거래
    # 일시를 묻는 표현이기도 하고("매수 시점이 언제였어?"), 사실 조회를 막는 쪽이
    # 정책 위반을 놓치는 쪽보다 낫다고 볼 근거가 없다. 판단을 요구하는 형태는
    # '타이밍'·'추천'·'의견'과 아래 어미 규칙이 계속 잡는다.
    (
        "매매 의견 요구",
        re.compile(
            r"(?<!장내)(?<!장외)(?<!시간외)(?:매수|매도)"
            r"\s*(?:해도|해야|하는\s*게|할까|하면|타이밍|추천|의견)"
        ),
    ),
    # "투자할 만해?" / "투자해도 될까?" / "어디에 투자하는 게 좋아?"
    #
    # '투자처'는 방아쇠에서 뺐다. 명사 단독이라 이 목록의 '행위 + 판단 요구' 원칙을 스스로
    # 어긴다. 자금조달·시설투자 공시가 자금의 투자처를 기재 항목으로 담고 있어
    # "신규시설투자의 투자처가 어디야?"가 사실 조회인데도 걸렸다.
    #
    # 투자처를 고르라는 요구는 아래 '종목 선택 요구'가 계속 잡는다
    # ("더 좋은 투자처야?", "어디에 투자하는 게 좋아?").
    (
        "투자 판단 요구",
        re.compile(
            r"투자\s*(?:할\s*만|해도\s*(?:될|돼|되)|하는\s*게|하기\s*(?:좋|괜찮)"
            r"|매력|의견|가치가\s*있)"
        ),
    ),
    # "어떤 종목이 더 좋아?" / "어디에 투자?" / "어느 회사에 투자?"
    (
        "종목 선택 요구",
        re.compile(r"어떤\s*종목|(?:어디|어느\s*(?:쪽|기업|회사|종목))\s*에\s*투자"),
    ),
    # "더 좋은 투자처야?" / "더 나은 종목"
    ("종목 선택 요구", re.compile(r"더\s*(?:좋은|나은|괜찮은)\s*(?:투자|종목|주식)")),
    # "추천해줘" / "종목 추천" / "투자 추천"
    #
    # 추천 단독은 쓰지 않는다. '사외이사후보추천위원회'가 정기공시의 기재 사항이다.
    (
        "추천 요구",
        re.compile(r"(?:종목|주식|투자|매수)\s*추천|추천\s*(?:해\s*줘|해줘|해\s*봐|해봐|해\s*주|좀)"),
    ),
)


# --------------------------------------------------------------------------
# F4 — 미래 예측
#
# 겨냥하는 것은 "모델에게 미래 결과를 생성하라"는 요구다. 미래 시점을 가리키는 표현이
# 들어 있다는 사실만으로는 막지 않는다.
#
#     "공시에 적힌 계약 종료 예정일이 언제야?"
#
# 는 이미 확정돼 문서에 적힌 미래 일정이며 조회 질문이다. 그래서 '예정'은 방아쇠가 아니고,
# 상대 시점 표현(내년·향후)도 단독으로는 걸리지 않는다. 추측을 요구하는 어형이 함께 있어야
# 한다.
#
# CONTROL-02("나중에 바뀐 내용 있어?")가 F4로 새지 않아야 한다는 것도 같은 규칙으로
# 보장된다. 정정 이력은 과거 사실이고, '나중에'는 방아쇠 목록에 없다.
# --------------------------------------------------------------------------

# 미래 시점을 가리키는 상대 표현. 단독으로는 판정하지 않고 추측 어형과 함께 볼 때만 쓴다.
_FUTURE_HORIZON = r"내년|다음\s*(?:해|분기|년|년도)|차기|향후|앞으로|장래"

# 결과를 추측해 달라는 어형.
_SPECULATIVE_ENDING = r"될까|일까|할까|어떻게\s*될|어떨까|얼마나\s*될|예상|예측|전망"

# 문서나 회사가 이미 내놓은 진술을 가리키는 표현.
#
# "사업보고서에 적힌 회사 전망이 어때?"는 예측 요구가 아니다. 전망을 만들어 달라는 것이
# 아니라 문서에 기재된 전망을 읽어 달라는 것이다. 정기공시에는 회사가 제시한 전망·계획이
# 기재 항목으로 실리고, 그것을 조회하는 것은 사실 조회다.
#
# 이 표현은 F4를 통째로 면제하지 않는다. 아래 _F4_REPORTED_STATEMENT_PATTERNS에만
# 적용된다. "공시 기준으로 내년 매출 오를까?"처럼 출처를 언급하면서 결과를 물으면 여전히
# 예측 요구이고, 그쪽은 _F4_PATTERNS가 잡는다.
_SOURCE_BACKED = re.compile(
    r"(?:공시|사업보고서|보고서|공시서류|문서|본문|원문)\s*(?:에|에서)"
    r"\s*(?:적힌|적혀|기재된|기재돼|나온|실린|밝힌|제시한|공시된)"
    r"|회사가\s*(?:밝힌|제시한|전망한|발표한|기재한|공시한)"
)

_F4_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "주가 오를까?" / "실적이 좋아질까?" / "오를 것 같아?"
    #
    # 방향을 묻는 어간에 추측 어미가 붙은 형태만 본다. '증가할 예정'처럼 확정 서술은
    # 뒤에 오는 어미가 달라 걸리지 않는다.
    (
        "등락 예측 요구",
        re.compile(
            r"(?:오를|내릴|떨어질|상승할|하락할|올라갈|좋아질|나아질|개선될|악화될)"
            r"\s*(?:까|지|것\s*같|거\s*같|듯)"
        ),
    ),
    # "예상해줘" / "예측해봐" / "전망 좀 해줘"
    #
    # 모델에게 내리는 명령형이다. 출처를 언급하더라도 생성 요구이므로 면제하지 않는다.
    (
        "전망 생성 요구",
        re.compile(r"(?:예상|예측|전망|추정)\s*(?:해\s*줘|해줘|해\s*봐|해봐|해\s*주|좀\s*해|해\s*볼)"),
    ),
    # "내년 매출 얼마나 될까?" / "다음 분기 영업이익 예상"
    #
    # 미래 시점과 추측 어형이 모두 있어야 한다. 순서는 묻지 않는다.
    (
        "미래 시점 추정 요구",
        re.compile(rf"(?:{_FUTURE_HORIZON})[\s\S]{{0,30}}?(?:{_SPECULATIVE_ENDING})"),
    ),
)


# 출처를 지목한 질문에서는 판정하지 않는 규칙.
#
# "전망이 어때?"는 두 가지로 읽힌다. 모델에게 전망을 만들어 달라는 요구이거나, 문서에
# 적힌 전망을 읽어 달라는 요구다. 문장만으로는 갈리지 않고 출처 표현이 갈라 준다.
#
# 위 _F4_PATTERNS와 나눠 둔 이유가 여기 있다. 그쪽은 출처와 무관하게 결과를 묻는 형태라
# 면제 대상이 아니다.
_F4_REPORTED_STATEMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "전망이 어때?" / "예상은 어떻게 돼?"
    ("전망 생성 요구", re.compile(r"(?:예상|예측|전망)\s*(?:이|은|는)?\s*(?:어때|어떻|어떠)")),
)


def _first_match(
    question: str,
    patterns: tuple[tuple[str, re.Pattern[str]], ...],
) -> tuple[str, str] | None:
    """
    규칙 목록에서 처음 걸리는 것을 찾는다.

    무엇에 걸렸는지를 함께 돌려준다. 판정만 남기고 근거 표현을 버리면, 오검출 신고가
    들어왔을 때 어느 규칙이 걸렸는지 되짚을 수 없다.
    """
    for label, pattern in patterns:
        match = pattern.search(question)

        if match:
            return label, match.group(0).strip()

    return None


def screen_question(question: str) -> PolicyDecision:
    """
    질문 하나가 정책상 답변 가능한지 판정한다.

    문서를 읽지 않는다. 문자열만 본다. 따라서 코퍼스가 없어도 전부 시험할 수 있고,
    허용된 질문에 대해서는 아무 부작용도 남기지 않는다.

    F4는 두 벌의 규칙을 쓴다. 출처와 무관하게 결과를 묻는 형태(_F4_PATTERNS)와, 출처를
    지목하지 않았을 때만 판정하는 형태(_F4_REPORTED_STATEMENT_PATTERNS)다. "사업보고서에
    적힌 회사 전망이 어때?"는 뒤쪽에 걸리지만 출처 표현이 있어 통과하고, "삼성전자 전망이
    어때?"는 그대로 걸린다.

    F3와 F4가 동시에 걸리면 F3를 고른다. pattern_f_safety.md §3.4의 우선순위이며, 이유는
    사실 조회로 축소될 위험이 F3 쪽이 더 크기 때문이다. "내년 실적 보고 사도 될까?"를 F4로
    분류하면 '예측 거부'로 집계되지만 실제 요구는 투자 의견이다. 고르기 전의 목록은
    matched_categories에 남는다.
    """
    if not question or not question.strip():
        # 빈 질문은 정책 위반이 아니다. 그 판정은 query_analyzer의 몫이므로 통과시킨다.
        return PolicyDecision(allowed=True)

    f3 = _first_match(question, _F3_PATTERNS)
    f4 = _first_match(question, _F4_PATTERNS)

    if f4 is None and not _SOURCE_BACKED.search(question):
        # 출처를 지목하지 않았을 때만 본다. 지목했다면 문서에 적힌 전망을 읽어 달라는
        # 요구이므로 사실 조회다.
        f4 = _first_match(question, _F4_REPORTED_STATEMENT_PATTERNS)

    matched_categories = tuple(
        category
        for category, hit in (
            (CATEGORY_INVESTMENT_ADVICE, f3),
            (CATEGORY_FORWARD_LOOKING, f4),
        )
        if hit is not None
    )

    if f3 is not None:
        label, expression = f3

        return PolicyDecision(
            allowed=False,
            category=CATEGORY_INVESTMENT_ADVICE,
            reason=REASON_INVESTMENT_ADVICE,
            detail=(
                f"투자 판단·추천 요청은 답변 범위 밖입니다({label}: '{expression}'). "
                "공시에 기재된 사실 조회는 계속 지원합니다."
            ),
            matched_categories=matched_categories,
            matched_expressions=(expression,),
        )

    if f4 is not None:
        label, expression = f4

        return PolicyDecision(
            allowed=False,
            category=CATEGORY_FORWARD_LOOKING,
            reason=REASON_FORWARD_LOOKING_PREDICTION,
            detail=(
                f"아직 공시되지 않은 결과에 대한 예측 요청은 답변 범위 밖입니다"
                f"({label}: '{expression}'). 공시에 기재된 확정 사실·일정 조회는 계속 "
                "지원합니다."
            ),
            matched_categories=matched_categories,
            matched_expressions=(expression,),
        )

    return PolicyDecision(allowed=True)
