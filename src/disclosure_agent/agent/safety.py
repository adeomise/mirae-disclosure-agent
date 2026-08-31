"""질문 단계 안전 정책 판정 모듈 (Pattern F 중 F1·F2·F3·F4).

역할:
- 자연어 질문 하나를 받아 "이 질문에 답해도 되는가"만 판정한다. 문서를 읽지 않고,
  검색하지 않으며, QueryPlan도 만들지 않는다.

이 모듈이 다루는 범주:
    F1  코퍼스 보유 기간 밖  요구 기간이 organizer 코퍼스의 수집 상한을 넘어섬
    F2  외부 정보 요구       요구하는 데이터 종류 자체가 코퍼스에 없음(뉴스·시세·시장 반응)
    F3  투자 판단·추천       매수/매도/보유 의견, 종목 선택 요구
    F4  미래 예측           아직 공시되지 않은 결과에 대한 전망·예측 생성 요구

    docs/agent_design/pattern_f_safety.md의 taxonomy를 따른다. F5(근거 없음)·F6(미지원
    capability)는 여기서 판정하지 않는다. 그 둘은 코퍼스를 보거나 capability를 확인한
    뒤에야 갈리며, 각각 normalization과 query_analyzer/router가 담당한다.

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

왜 F1·F2도 여기인가:
    두 범주는 정책 위반이 아니라 데이터 경계지만, 판정에 코퍼스가 필요하지 않다는 점이
    F3·F4와 같다. "2027년"이 수집 상한 밖이라는 것도, "시장 반응"이 수집 대상 문서군이
    아니라는 것도 질문 문자열과 수집 범위 선언만으로 갈린다.

    지금까지 Q16·Q17이 거부된 것은 이 판정 때문이 아니었다. Q16은 '상반기'라는 미지원 기간
    표현에, Q17은 기업명을 찾지 못한 것에 걸렸다(fixture F1-01·F2-02, verdict
    ACCIDENTALLY_SAFE). 둘 다 질문을 조금만 바꾸면 사라지는 방어이고, 사라진 뒤에는
    BLOCKED가 아니라 값이 실린 답이 나온다(F2-03이 실제로 그랬다). 그래서 우연한 파싱 실패를
    의미 판정으로 바꾼다.

입력:
- 자연어 질문 문자열
- 코퍼스 보유 기간 상한(CorpusCoverage). 기본값은 organizer 수집 범위 선언을 따른다.

출력:
- PolicyDecision. 허용 여부와, 거부한 경우 그 범주·사유·근거 표현.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CATEGORY_EXTERNAL_INFORMATION",
    "CATEGORY_FORWARD_LOOKING",
    "CATEGORY_INVESTMENT_ADVICE",
    "CATEGORY_OUT_OF_CORPUS_PERIOD",
    "DEFAULT_CORPUS_COVERAGE",
    "REASON_EXTERNAL_INFORMATION",
    "REASON_FORWARD_LOOKING_PREDICTION",
    "REASON_INVESTMENT_ADVICE",
    "REASON_OUT_OF_CORPUS_PERIOD",
    "STAGE_ANSWERABILITY_GATE",
    "CorpusCoverage",
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
CATEGORY_OUT_OF_CORPUS_PERIOD = "F1"
CATEGORY_EXTERNAL_INFORMATION = "F2"

# answerability_detail.reason에 실리는 사유 문자열.
#
# Contract의 AnswerabilityReason이 아니다. F3와 F4는 canonical reason으로는 둘 다
# OUT_OF_SCOPE_POLICY 하나에 대응하며(PR #29에서 추가), 그 값 하나만으로는 투자 판단 요구와
# 예측 요구를 구분할 수 없다. 두 판정을 나누는 것은 아래 문자열이고, 이 값들은
# answerability_detail.reason에만 실린다(pattern_f_safety.md §5.1).
#
# F1·F2는 canonical reason만으로도 서로 구분된다(OUT_OF_DATA_RANGE / OUT_OF_SCOPE_DATA).
# 그래도 같은 자리에 사유 문자열을 둔다. 네 범주 중 둘만 detail을 비우면 소비자가 "detail이
# 비었다"를 "판정 근거가 없다"로 읽게 되고, 범주별 집계도 두 층을 번갈아 봐야 한다.
REASON_INVESTMENT_ADVICE = "investment_advice"
REASON_FORWARD_LOOKING_PREDICTION = "forward_looking_prediction"
REASON_OUT_OF_CORPUS_PERIOD = "out_of_corpus_period"
REASON_EXTERNAL_INFORMATION = "external_information"


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


# --------------------------------------------------------------------------
# F2 — 외부 정보 요구
#
# 겨냥하는 것은 "코퍼스에 없는 종류의 데이터"를 요구하는 질문이다. F1과 나누는 기준은
# 기간이 아니라 데이터의 종류다. 2025년 뉴스도, 2023년 종가도 기간과 무관하게 없다.
#
# F3·F4와 같은 규칙을 지킨다. 낱말 하나로는 막지 않는다.
#
#     주가        전환가액 조정·주식매수선택권 공시가 기재 사실로 담는다
#     시장        시장점유율·해외시장·시장 규모가 정기공시의 기재 항목이다
#     거래량      지분공시의 거래 내역 표기와 겹친다
#
# 그래서 아래 규칙은 '외부 매체·시세를 조회 대상으로 지목한 형태'만 본다. 명사 단독으로는
# 걸리지 않는다.
#
# 기업명을 찾지 못한 것은 F2의 근거가 아니다. Q17이 지금 막히는 이유가 그것이지만
# (fixture F2-02, verdict ACCIDENTALLY_SAFE), 기업명 부재는 "이 데이터군이 없다"가 아니라
# "무엇을 물었는지 모르겠다"이다. 그 판정은 query_analyzer의 unknown_company가 계속 맡는다.
# 이 Gate는 manifest를 읽기 전에 돌므로 기업명을 알지도 못한다. 규칙이 기업 인식에 기대지
# 않는다는 것이 구조로 보장된다.
# --------------------------------------------------------------------------

# 코퍼스가 수집하지 않는 매체. data_filter.md [2] 문서 종류는 공시 문서만 담는다.
_EXTERNAL_MEDIA = r"뉴스|기사|신문|언론\s*보도|보도자료|블로그|커뮤니티|게시판|유튜브|SNS"

# 코퍼스가 수집하지 않는 시장 데이터.
#
# 두 벌로 나눈다. 시점 표현이 앞에 붙으면("오늘 거래량") 시세 조회가 확정되지만, 조회
# 어미만 붙은 형태("거래량이 얼마야?")는 갈리지 않는다. 지분공시가 장내 거래 내역을 기재
# 사실로 담고 있어 "장내매수 거래량이 얼마였어?"가 사실 조회이기 때문이다.
#
# 그래서 애매한 낱말은 시점 표현과 함께일 때만 쓴다. 위 F3의 '장내매수' 예외와 같은 판단이다.
_MARKET_DATA_WITH_TIMEPOINT = r"주가|종가|시초가|시세|주식\s*가격|거래량"

_MARKET_QUOTE = r"주가|종가|시초가|시세|주식\s*가격"

_F2_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "네이버 뉴스 기준으로 ..." — 답변의 근거 출처를 코퍼스 밖으로 지정한 형태.
    #
    # 값 자체는 코퍼스에 있을 수 있다(F2-03의 '2025년 매출'이 그렇다). 그래도 거부한다.
    # 지정한 출처를 무시하고 공시 값을 돌려주면 출처를 잘못 귀속한 답이 된다.
    (
        "외부 출처 지정",
        re.compile(rf"(?:{_EXTERNAL_MEDIA})\s*(?:기준|에\s*따르면|에\s*나온|에서\s*본|참고)"),
    ),
    # "오늘 뉴스 알려줘" / "관련 기사 찾아줘"
    (
        "외부 매체 조회",
        re.compile(
            rf"(?:{_EXTERNAL_MEDIA})\s*(?:[는은이가를을]\s*)?"
            r"(?:알려|찾아|검색|정리|요약|보여|말해|어때|어땠|뭐야|있어)"
        ),
    ),
    # "시장 반응은 어땠어?" (Q17)
    #
    # '시장' 단독이 아니라 '시장 + 반응 계열'을 요구한다. 시장점유율·해외시장이 걸리지
    # 않는 것은 이 결합 때문이다.
    (
        "시장 반응 조회",
        re.compile(r"(?:시장|투자자|주주|여론|업계)\s*(?:의\s*)?(?:반응|평가|분위기|시선|심리)"),
    ),
    ("시장 반응 조회", re.compile(r"주가\s*(?:반응|추이|흐름|변동)")),
    # "오늘 주가 얼마야?"
    #
    # 시점 표현이 앞에 오면 시세 조회다. 공시에 기재된 주가는 특정 시점의 기재 사실이지
    # '오늘·지금'의 값이 아니다.
    (
        "시세 조회",
        re.compile(
            rf"(?:오늘|지금|현재|어제|최근|실시간)\s*(?:의\s*)?(?:{_MARKET_DATA_WITH_TIMEPOINT})"
        ),
    ),
    # "주가 얼마야?" / "종가 알려줘"
    (
        "시세 조회",
        re.compile(rf"(?:{_MARKET_QUOTE})\s*(?:[는은이가를을]\s*)?(?:얼마|어때|어떻|알려)"),
    ),
    ("시세 조회", re.compile(r"목표\s*주가")),
    # "애널리스트 의견" / "증권사 리포트"
    #
    # 이 낱말들은 공시의 기재 항목으로 등장하지 않는다. 위 '낱말 단독 금지' 원칙이 겨냥한
    # 것은 공시에 사실로 실리는 낱말이며, 여기 해당하지 않는다.
    ("외부 분석 요구", re.compile(r"애널리스트|증권가|증권사\s*(?:리포트|보고서|의견|전망)")),
    # "외부 데이터로 확인해줘" / "웹에서 검색해줘"
    (
        "외부 데이터 요구",
        re.compile(r"외부\s*(?:데이터|자료|정보|출처)|웹\s*(?:검색|에서)|인터넷\s*(?:검색|에서)"),
    ),
)


# --------------------------------------------------------------------------
# F1 — 코퍼스 보유 기간 밖
#
# 미래 방향만 판정한다. pattern_f_safety.md §3.2의 규약이며, 이유는 두 방향의 확실성이
# 다르기 때문이다.
#
#     미래 방향   수집 상한 이후. 문서가 존재할 수 없으므로 확정 거부가 성립한다.
#     과거 방향   수집 시작 이전. 사업보고서 요약재무정보 표가 전기 비교 열을 담고 있어
#                 수집 시작 이전 값도 사실상 조회 가능하다. 확정 거부하면 오답이 된다.
#
# 그래서 아래 규칙에는 하한이 아예 없다. "2022년 매출"은 어떤 경로로도 F1이 되지 않으며,
# 그 판정은 지금처럼 실제 조회 결과(F5/NOT_FOUND)에 맡긴다. 하한을 두지 않는 것이
# 과거 방향 정책을 지키는 가장 확실한 방법이다.
#
# 규칙은 두 벌이다. 절대 기간("2027년", "2026년 상반기")은 수집 상한과 대조하고,
# 미래 방향 상대기간("내년", "다음 분기")은 방향만 보고 판정한다. 뒤쪽은 연도로 옮기지
# 않으므로 coverage를 쓰지 않는다.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusCoverage:
    """
    organizer 코퍼스가 담고 있는 기간의 상한.

    상한 하나만 둔다. 하한을 두면 과거 방향까지 같은 규칙으로 묶게 되고, 위 주석이 말한
    이유로 그것은 오답을 만든다. 없는 필드는 잘못 쓸 수도 없다.

    월 단위인 이유:
        data_filter.md [3] 시계열의 상한이 연 경계가 아니다. 정기공시는 2023~2025 회계연도에
        더해 2026년 1분기까지, 나머지 문서군은 2026-03-31까지다. 연 단위로 반올림하면
        2026년 1분기 조회가 함께 막히거나(내림) 2026년 하반기가 통과한다(올림).
    """

    last_year: int
    last_month: int

    def covers(self, year: int, month: int) -> bool:
        """(year, month)가 수집 상한 안에 있는가."""
        return (year, month) <= (self.last_year, self.last_month)


# 코퍼스 수집 범위 선언. data/raw/.../corpus/data_filter.md [3] 시계열이 출처다.
#
#     정기공시                        2023 · 2024 · 2025 회계연도 + 2026년 1분기
#     주요사항·거래소·지분공시        2023-01-01 ~ 2026-03-31
#
# 두 선언의 상한이 같은 지점(2026년 3월)이라 값 하나로 둔다.
#
# 상수로 둔 것에 대하여 — pattern_f_safety.md F-D4는 이 경계를 manifest에서 유도할 것을
# 미결 사항으로 남겨 두었고, 그 지적은 유효하다. 코퍼스가 갱신되면 이 값은 조용히 틀린다.
# 그럼에도 지금 manifest를 읽지 않는 이유는 §3.4가 요구하는 순서 때문이다. Gate는 manifest
# 조회보다 앞에서 돌아야 하고, 여기서 파일을 열면 "정책 판정에는 코퍼스를 읽지 않는다"가
# 깨진다.
#
# 그래서 값을 상수로 두되 screen_question의 인자로 뽑아 두었다. manifest에서 유도한 경계를
# 넘기고 싶어지면 호출부에서 주입하면 되고, 아래 판정 규칙은 한 줄도 바뀌지 않는다.
DEFAULT_CORPUS_COVERAGE = CorpusCoverage(last_year=2026, last_month=3)

# 절대연도. '년'이 붙지 않은 네 자리 숫자는 연도로 보지 않는다.
#
# query_analyzer.YEAR_PATTERN과 같은 규칙이다. import하지 않고 같은 값을 둔 이유는 Gate가
# 질의 해석보다 앞 계층이기 때문이다. 대신 두 패턴이 갈라지지 않는지는 테스트가 고정한다
# (test_pattern_f_safety_gate.py의 year pattern 동기화 검사).
_YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?=\s*년)")

# 연도 안의 하위 기간 표현. 요구 기간의 '끝'이 어디인지를 정한다.
#
# 끝을 보는 이유: 코퍼스 상한이 연 경계가 아니어서, 시작만 보면 2026년 상반기(Q16)가
# 통과한다. 2026-01-01은 상한 안이지만 2026년 상반기를 보고하는 문서는 상한 밖이다.
_SUB_YEAR_PERIOD = re.compile(
    r"(?P<half>상반기|하반기|반기)"
    r"|(?P<quarter>[1-4])\s*분기"
    r"|[qQ](?P<q>[1-4])(?![0-9])"
    r"|(?P<month>1[0-2]|0?[1-9])\s*월"
)


def _sub_year_end_month(match: re.Match[str]) -> int:
    """하위 기간 표현이 가리키는 마지막 달."""
    half = match.group("half")

    if half:
        # '반기'만 쓰면 반기보고서, 즉 상반기다.
        return 12 if half == "하반기" else 6

    quarter = match.group("quarter") or match.group("q")

    if quarter:
        return int(quarter) * 3

    return int(match.group("month"))


def _requested_periods(question: str) -> tuple[tuple[int, int | None, str], ...]:
    """
    질문이 지목한 기간을 (연도, 마지막 달, 원문 표현)으로 읽는다.

    마지막 달이 None이면 하위 기간 표현 없이 연도만 쓴 경우다. 추측해서 12월로 채우지
    않는다. 연 단위 언급과 연말까지를 요구하는 언급은 다른 진술이고, 그 차이가 아래
    판정에서 실제로 갈린다.

    연도 하나에 하위 기간 하나를 붙이는 방식:
        다음 연도 표현이 나오기 전까지의 구간에서 처음 걸리는 하위 기간 표현을 그 연도의
        것으로 본다. "2024년 매출과 2025년 1분기"에서 '1분기'가 2024년에 붙지 않게 하려면
        구간을 나눠야 한다.

        오귀속의 영향 범위는 좁다. 연도가 상한 연도보다 작으면 어떤 달이 붙어도 상한
        안이고, 크면 어떤 달이 붙어도 밖이다. 하위 기간 표현이 결과를 바꾸는 것은 연도가
        상한 연도와 같을 때뿐이다.
    """
    matches = list(_YEAR_PATTERN.finditer(question))

    periods: list[tuple[int, int | None, str]] = []

    for index, match in enumerate(matches):
        window_end = matches[index + 1].start() if index + 1 < len(matches) else len(question)

        sub_year = _SUB_YEAR_PERIOD.search(question, match.end(), window_end)

        year = int(match.group(0))

        if sub_year is None:
            periods.append((year, None, f"{year}년"))

            continue

        periods.append((year, _sub_year_end_month(sub_year), f"{year}년 {sub_year.group(0)}"))

    return tuple(periods)


# 미래 방향 상대기간 표현.
#
# 절대연도가 없어도 미래를 가리키는 표현이다. 위 절대 기간 규칙만으로는 이 형태가 통과한다.
#
#     "삼성전자 내년 매출 알려줘"        -> query_analyzer의 missing_year로 막혔다
#     "삼성전자 다음 분기 실적 알려줘"    -> unsupported_period로 막혔다
#
# 둘 다 기간 판정이 아니라 파싱 실패다. F4가 잡지 못하는 것은 추측 어형이 없기 때문이다.
# "내년 매출 얼마나 될까?"는 예측 요구라서 F4지만, "내년 매출 알려줘"는 예측을 요구하지
# 않는 단순 사실 조회이고, 그 사실이 수집 상한 밖에 있을 뿐이다.
#
# 연도로 옮기지 않는다:
#     옮기려면 기준 시점이 필요하다. 시스템 시각을 쓰면 같은 질문이 실행 시점에 따라 다른
#     해를 가리키고, 같은 fixture가 내년에 다른 결과를 낸다. 이 규칙이 확정하는 것은
#     "요구 기간이 발화 시점 이후"라는 방향 하나뿐이며, F1에는 그것으로 충분하다.
#     코퍼스는 고정된 과거 스냅샷이므로 발화 시점 이후를 가리키는 요구를 담고 있을 수 없다.
#
# 과거 방향 상대기간(작년·전년·지난해)은 여기 없다. 그쪽은 Q07의 relative-period resolver
# 영역이고 최신 회계연도를 anchor로 해석되므로, 여기서 막으면 그 작업이 답할 질문을 미리
# 없앤다. query_analyzer.RELATIVE_PERIOD_TOKENS가 같은 세 낱말만 담고 있는 것과 짝이다.

# 문서에 이미 기재된 미래 진술을 가리키는 낱말.
#
# 계획·목표·전망·가이던스·예정은 정기공시의 기재 항목이다. 회사가 스스로 내놓은 미래
# 진술이며, 그것을 읽어 달라는 요구는 미래의 실제 결과를 요구하는 것이 아니다.
#
# 이 낱말들은 단독으로는 아무것도 면제하지 않는다. "내년 매출 목표를 세워줘"는 '목표'를
# 담고 있지만 문서를 가리키지 않는다. 아래 _is_source_backed_future_request가
# _SOURCE_BACKED와 함께일 때만 쓴다.
_REPORTED_FUTURE_OBJECT = re.compile(r"계획|목표|전망|가이던스|예정")


def _is_source_backed_future_request(request: str) -> bool:
    """
    요청 하나가 문서에 이미 기재된 미래 진술을 조회하는가.

    **질문 전체가 아니라 요청 단위 하나를 받는다.** 인자 이름이 question이 아닌 이유이며,
    호출부는 _request_units가 나눠 준 조각을 넘긴다. 질문 전체를 넘기면 아래 두 조건이 서로
    다른 절에서 하나씩 발견돼도 참이 되고, 그 순간 면제가 다른 요청까지 샌다.

    두 조건을 함께 요구한다.

        출처 표현      _SOURCE_BACKED. F4가 쓰는 것과 같은 규칙을 그대로 쓴다.
        진술 대상      계획·목표·전망·가이던스·예정

    F4의 면제와 조건 수가 다른 이유:
        F4가 면제하는 것은 _F4_REPORTED_STATEMENT_PATTERNS, 즉 "전망이 어때?" 한 갈래다.
        그 규칙은 이미 진술 대상을 어간에 담고 있어 출처 표현만 더하면 갈린다.

        F1의 방아쇠는 시점 표현('내년')뿐이고 무엇을 묻는지는 담고 있지 않다. 출처 표현만
        보고 면제하면 "공시에 기재된 내년 매출 알려줘"까지 빠진다. 그 질문이 요구하는 것은
        아직 존재하지 않는 실적값이지 문서에 적힌 진술이 아니다. 그래서 대상까지 본다.

    절대 기간에는 적용하지 않는다. "공시에 기재된 2027년 투자계획"은 이 함수를 거치지
    않으며, 수집 상한 대조가 그대로 판정한다. 이번 범위는 상대기간 오검출뿐이다.
    """
    return bool(_SOURCE_BACKED.search(request) and _REPORTED_FUTURE_OBJECT.search(request))


# 한 질문 안에서 요청이 갈리는 자리.
#
# 한국어 절 분석기를 만들지 않는다. 여기서 필요한 것은 "이 미래 표현이 어느 요청에 속하는가"
# 하나뿐이고, 그 판정에는 명시적인 요청 연결 표현만으로 충분하다.
#
# 목록을 좁게 두는 이유:
#     '하고'를 단독으로 넣으면 정상 질문이 잘린다. "LG이노텍하고 레인보우로보틱스가"(Q12)와
#     "무슨 사업을 하고 주력 제품이 뭐야?"(Q05)가 전부 명사·동사 연결이다. 그래서 요청을
#     끝내는 동사에 붙은 형태만 본다.
#
#     '와/과'도 넣지 않았다. "매출과 영업이익"처럼 명사 나열이 훨씬 흔하다.
#
# 나누기에 실패하면 두 요청이 한 단위로 남는다. 그때는 면제 조건이 더 넓은 범위에서 평가되긴
# 하지만, 아래 _first_future_relative_period가 '안전하지 않은 요청이 하나라도 있으면 F1'로
# 접근하므로 판정이 느슨해지는 방향은 여기 한 곳뿐이다. 알려진 한계이며 대표 질문·fixture에는
# 해당 형태가 없다.
_REQUEST_SEPARATOR = re.compile(
    r"[,;]"
    r"|그리고"
    r"|(?:알려|말해|보여|설명해|정리해|요약해|비교해|확인해)\s*주고"
    r"|비교하고"
)


def _request_units(question: str) -> tuple[str, ...]:
    """
    질문을 요청 단위로 나눈다.

    구분자 자체는 어느 쪽에도 넣지 않는다. 출처 표현도 진술 대상도 연도도 담고 있지 않아
    판정에 쓰이지 않는다.

    구분자가 없으면 질문 하나가 그대로 단위 하나다. 지금까지의 단일 질문 동작이 그대로
    유지되는 것은 그 때문이다.
    """
    units: list[str] = []

    start = 0

    for separator in _REQUEST_SEPARATOR.finditer(question):
        if separator.start() > start:
            units.append(question[start : separator.start()])

        start = separator.end()

    if start < len(question):
        units.append(question[start:])

    return tuple(units)


# 발화 시점을 스스로 가리키는 표현. anchor를 붙일 수 없다.
#
# "2024년 내년"이라고 써도 '2024년의 다음 해'가 되지 않는다. 그래서 절대연도가 함께 있어도
# 그대로 판정한다.
_FUTURE_RELATIVE_DEICTIC: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("미래 상대기간 요구", re.compile(r"내후년|내년")),
)

# 앞의 기간을 anchor로 읽힐 수 있는 표현.
#
# "2024년 다음 해 매출"은 FY2025를 뜻할 수 있다. 그 해석은 코퍼스 안이므로, anchor를 무시하고
# 미래로 단정하면 답할 수 있는 질문에 "보유하고 있지 않습니다"라고 답하게 된다. 그래서
# 절대연도가 함께 있으면 이 규칙을 적용하지 않고 통과시킨다(→ _first_future_relative_period).
#
# anchored relative를 해석하지는 않는다. 이번 범위는 판정을 보류하는 것까지이고, 해석은
# query_analyzer의 몫이다.
#
# '다음' 단독이나 '분기' 단독은 방아쇠가 아니다. 결합형만 본다.
#
# '보고서'가 뒤에 붙으면 제외한다. "다음 분기보고서 제출 예정일"은 문서를 가리키는 표현이고,
# 제출 예정일은 공시에 기재될 수 있는 확정 일정이다. F3의 '장내매수' 예외와 같은 판단이다.
#
# '다음 해'는 뒤따르는 글자를 본다. 그러지 않으면 "다음 해당 공시"의 '해당'에서 '해'만
# 떼어 읽는다.
_FUTURE_RELATIVE_ANCHORABLE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("미래 상대기간 요구", re.compile(r"다음\s*(?:연도|년도)")),
    ("미래 상대기간 요구", re.compile(r"다음\s*(?:반기|분기)(?!\s*보고서)")),
    ("미래 상대기간 요구", re.compile(r"다음\s*[해년](?=[\s.,?!]|[에의는도와과]|$)")),
)


def _first_future_relative_in_request(request: str) -> tuple[str, str] | None:
    """
    요청 하나 안에서 미래 방향 상대기간 표현을 찾는다. 없으면 None.

    절대연도가 **같은 요청 안에** 있으면 anchor를 붙일 수 있는 표현은 보지 않는다. 그
    조합에서 무엇을 뜻하는지는 anchored relative 해석이 있어야 정해지고, 이 모듈은 그것을
    하지 않는다. 판정을 보류하면 기존 경로가 그대로 처리하지만, 미래로 단정하면 되돌릴 수
    없다.

    연도를 같은 요청 안에서만 보는 것이 요점이다. 질문 전체에서 찾으면 "2025년 투자계획도
    알려주고 다음 분기 실적도 알려줘"에서 앞 절의 2025가 뒤 절의 '다음 분기'를 anchored로
    만들어 버린다. 두 표현은 서로 다른 요청에 속하므로 anchor 관계가 성립하지 않는다.

    deictic 표현은 그 영향을 받지 않는다. '내년'은 앞에 무엇이 오든 발화 시점의 다음 해다.
    """
    deictic = _first_match(request, _FUTURE_RELATIVE_DEICTIC)

    if deictic is not None:
        return deictic

    if _YEAR_PATTERN.search(request):
        # 같은 요청 안에 절대연도가 있으면 anchored relative일 수 있다. 단정하지 않는다.
        return None

    return _first_match(request, _FUTURE_RELATIVE_ANCHORABLE)


def _first_future_relative_period(question: str) -> tuple[str, str] | None:
    """
    미래 방향 상대기간 요구를 찾는다. 없으면 None.

    요청 단위마다 따로 판정한다. 질문 하나에 미래 표현이 여러 개 있을 수 있고, 그 각각이
    서로 다른 성격일 수 있기 때문이다.

        "공시에 기재된 2025년 투자계획도 알려주고, 내년 매출도 알려줘"

    앞 요청은 문서에 적힌 계획 조회이고 뒤 요청은 아직 없는 실적값 요구다. 면제를 질문
    전체에 걸면 앞 요청의 출처 표현이 뒤 요청까지 덮어, 답할 수 없는 요구가 통과한다.
    그 뒤에 남는 것은 '2025 + 매출'뿐이라 single_fact로 축소되어 조용히 틀린 답이 된다.

    판정 원칙은 하나다. **안전하지 않은 미래 요구가 하나라도 있으면 F1이다.**
    모든 미래 요구가 문서에 적힌 진술 조회일 때만 통과시킨다. 부분적으로 답할 수 있다는
    이유로 통과시키면, 답할 수 없는 쪽이 응답에서 소리 없이 사라진다.

    돌려주는 근거 표현은 처음 만난 '안전하지 않은' 요구의 것이다. 면제된 요청의 표현을
    남기면 오검출 신고를 되짚을 때 엉뚱한 자리를 보게 된다.
    """
    for request in _request_units(question):
        hit = _first_future_relative_in_request(request)

        if hit is None:
            continue

        if _is_source_backed_future_request(request):
            # 이 요청은 문서에 이미 적힌 미래 진술을 읽어 달라는 것이다. 기간 밖이 아니다.
            # 다른 요청까지 함께 면제하지 않으므로 순회를 계속한다.
            continue

        return hit

    return None


def _first_out_of_range_period(
    question: str,
    coverage: CorpusCoverage,
) -> tuple[str, str] | None:
    """
    수집 상한을 넘는 기간 표현을 찾는다. 없으면 None.

    두 가지를 다르게 본다.

        연도만 쓴 경우      연도가 상한 연도보다 커야 막는다.
        하위 기간을 쓴 경우 그 기간의 끝이 상한을 넘으면 막는다.

    연도만 쓴 경우를 느슨하게 두는 것은 상한 연도(2026)에서 갈린다. 그 해는 일부만 수집돼
    있어("2026년 1분기"까지), 연도만 언급한 질문이 보유 중인 문서를 가리킬 수 있다.
    "보유하고 있지 않습니다"는 확정 진술이므로, 절반이 있는 해에 대해 그렇게 답하면 거짓이
    된다. 그런 질문은 막지 않고 실제 조회 결과(F5)에 맡긴다.

    반대로 하위 기간을 명시했으면 그 기간을 보고하는 문서가 있는지 없는지가 확정된다.
    "2026년 상반기"(Q16)를 담은 정기공시는 존재할 수 없다.

    절대 기간에서 걸리지 않으면 미래 방향 상대기간을 본다. 순서를 이렇게 두는 이유는 절대
    기간이 더 확정적인 근거이기 때문이다. "2027년 다음 분기 실적"에서 남길 근거 표현은
    '다음 분기'가 아니라 '2027년'이다.
    """
    for year, end_month, expression in _requested_periods(question):
        if end_month is None:
            if year > coverage.last_year:
                return "요구 연도가 수집 상한 밖", expression

            continue

        if not coverage.covers(year, end_month):
            return "요구 기간이 수집 상한 밖", expression

    return _first_future_relative_period(question)


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


# 범주별 판정 근거 문장.
#
# 사용자에게 나가는 문장이 아니다. answerability_detail.detail에 실리는 진단 원문이며,
# 어느 규칙과 어느 표현에 걸렸는지를 담는다. 사용자 문장은 Answer Builder가 따로 만든다
# (answer_builder.UNAVAILABLE_MESSAGE_BY_REASON / POLICY_REFUSAL_MESSAGE_BY_DETAIL_REASON).
# 그 경계 때문에 여기 문구를 고쳐도 응답 문장은 바뀌지 않는다.
_DETAIL_BY_CATEGORY: dict[str, str] = {
    CATEGORY_INVESTMENT_ADVICE: (
        "투자 판단·추천 요청은 답변 범위 밖입니다({label}: '{expression}'). "
        "공시에 기재된 사실 조회는 계속 지원합니다."
    ),
    CATEGORY_FORWARD_LOOKING: (
        "아직 공시되지 않은 결과에 대한 예측 요청은 답변 범위 밖입니다"
        "({label}: '{expression}'). 공시에 기재된 확정 사실·일정 조회는 계속 "
        "지원합니다."
    ),
    CATEGORY_EXTERNAL_INFORMATION: (
        "요청하신 종류의 데이터는 보유한 공시 문서 범위에 없습니다"
        "({label}: '{expression}'). 수집 대상은 공시 문서이며 뉴스·시세·시장 반응은 "
        "포함되지 않습니다."
    ),
    CATEGORY_OUT_OF_CORPUS_PERIOD: (
        "요청하신 기간의 공시 데이터를 보유하고 있지 않습니다"
        "({label}: '{expression}'). 보유 범위는 {coverage}까지입니다."
    ),
}


def screen_question(
    question: str,
    *,
    coverage: CorpusCoverage = DEFAULT_CORPUS_COVERAGE,
) -> PolicyDecision:
    """
    질문 하나가 답변 범위 안인지 판정한다.

    문서를 읽지 않는다. 질문 문자열과 coverage 선언만 본다. 따라서 코퍼스가 없어도 전부
    시험할 수 있고, 허용된 질문에 대해서는 아무 부작용도 남기지 않는다.

    F4는 두 벌의 규칙을 쓴다. 출처와 무관하게 결과를 묻는 형태(_F4_PATTERNS)와, 출처를
    지목하지 않았을 때만 판정하는 형태(_F4_REPORTED_STATEMENT_PATTERNS)다. "사업보고서에
    적힌 회사 전망이 어때?"는 뒤쪽에 걸리지만 출처 표현이 있어 통과하고, "삼성전자 전망이
    어때?"는 그대로 걸린다.

    우선순위는 pattern_f_safety.md §3.4를 그대로 따른다.

        F3 정책(투자 판단) -> F4 정책(미래 예측) -> F2 데이터군 -> F1 기간

    정책이 데이터 경계보다 앞이다. "삼성전자 2027년 주가 오를까?"를 F1으로 분류하면
    '기간 밖 거부'로 집계되지만, 그러면 코퍼스를 2027년까지 늘리면 답하게 될 질문으로
    읽힌다. 실제로는 코퍼스를 아무리 늘려도 답하지 않는다. 같은 이유로 F3가 F4보다 앞이다.
    "내년 실적 보고 사도 될까?"를 F4로 분류하면 실제 요구인 투자 의견이 집계에서 사라진다.

    고르기 전의 범주 목록은 matched_categories에 남는다. 우선순위 적용으로 원 범주가
    사라지면 네 범주의 실제 탐지율을 분리해 셀 수 없다.
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

    f2 = _first_match(question, _F2_PATTERNS)
    f1 = _first_out_of_range_period(question, coverage)

    # 우선순위 순서대로 둔다. 아래 판정 반복과 순서가 같아야 "골라진 범주가 목록의 앞에
    # 있다"가 유지되고, 순서가 어긋나면 집계하는 쪽이 목록의 첫 값을 판정으로 오해한다.
    hits: tuple[tuple[str, str, tuple[str, str] | None], ...] = (
        (CATEGORY_INVESTMENT_ADVICE, REASON_INVESTMENT_ADVICE, f3),
        (CATEGORY_FORWARD_LOOKING, REASON_FORWARD_LOOKING_PREDICTION, f4),
        (CATEGORY_EXTERNAL_INFORMATION, REASON_EXTERNAL_INFORMATION, f2),
        (CATEGORY_OUT_OF_CORPUS_PERIOD, REASON_OUT_OF_CORPUS_PERIOD, f1),
    )

    matched_categories = tuple(category for category, _, hit in hits if hit is not None)

    for category, reason, hit in hits:
        if hit is None:
            continue

        label, expression = hit

        return PolicyDecision(
            allowed=False,
            category=category,
            reason=reason,
            detail=_DETAIL_BY_CATEGORY[category].format(
                label=label,
                expression=expression,
                coverage=f"{coverage.last_year}년 {coverage.last_month}월",
            ),
            matched_categories=matched_categories,
            matched_expressions=(expression,),
        )

    return PolicyDecision(allowed=True)
