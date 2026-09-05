"""사용자 질의 분석 계약 모듈.

역할:
- 질문에서 의도, 대상 기업, 기간, 문서 유형과 계산 요청 후보를 구조화한다.

현재 구현 범위:
- 대표 질문 Q01 계열("<기업> <연도>년 연결 기준 매출액이 얼마야?")의 단일 수치 조회만
  규칙 기반으로 처리한다. LLM을 사용하지 않으며 추정도 하지 않는다.
- 범위를 벗어난 질문은 조용히 넘기지 않고 UnsupportedQuestionError로 거절한다.

입력:
- 사용자 질문 문자열
- 검색 대상 기업명 목록. service가 manifest에서 읽어 전달한다. 목록이 없으면 기업명을
  추측하지 않고 거절한다.
- 기업별 상대 기간 기준 연도(anchor) 대응표. 같은 이유로 manifest에서 읽어 전달한다.
  주지 않으면 상대 기간 질문이 막히며, 오늘 연도로 대신하지 않는다.

출력:
- QueryPlan

- 여러 기업을 함께 묻는 질문은 기업 하나로 줄여서 답하지 않는다.
  cross_entity_compare로 분류하고 Pattern D handler가 지원 모양을 명시적으로 판정한다.

- 여러 기간을 함께 묻는 질문도 같은 이유로 기간 하나로 줄이지 않는다. temporal_compare로
  분류해 Router가 막게 한다. Pattern C handler는 아직 없다.

TODO:
- 계산형/비교형 질의 의도 추가
- 분기·반기 보고서 대상 기간 표현 확장
- Pattern D 구현 시 '비교 의도'와 '단순 다중 기업 언급'을 구분한다. 지금은 안전을 위해
  기업이 둘 이상 매치되면 비교 의도인지 보지 않고 cross_entity_compare로 분류한다.
  막는 쪽에서는 이 구분이 없어도 되지만, 실제로 답하기 시작하면 필요해진다.
- Pattern C 구현 시 같은 구분이 기간에도 필요하다. 지금은 서로 다른 절대연도가 둘 이상
  보이면 비교 의도인지 보지 않고 temporal_compare로 분류한다.
- 절대연도와 상대기간이 함께 쓰인 질문("2025년 매출이 전년보다")의 해석. 지금은 읽기만 하고
  옮기지 않는다. 그 경우 "전년"의 기준이 문장에 적힌 연도인지 기업의 최신 사업보고서 연도인지가
  D8에서 정해지지 않았고, 둘 중 하나를 골라 옮기면 고른 쪽이 조용히 답이 된다.
- 두 해 앞("재작년") 등 지원하지 않는 상대 기간 표현. 지금은 unsupported_relative_period로
  거절한다.

상대 기간 해석에 대해:
- 과거 방향 한 해("작년"·"전년"·"전년도"·"지난해")만 절대연도로 옮긴다. 기준 연도는 해당
  기업의 사업보고서가 코퍼스에 존재하는 가장 최신 회계연도이며, 실행 시각을 쓰지 않는다
  (docs/agent_design/open_decisions.md D8).
- 미래 방향("내년"·"다음 해")은 여기서 해석하지 않는다. Pattern F 소관이다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

# 어휘는 그 값을 해석해야 하는 계층이 정의하고, 질의 해석 계층은 가져다 쓴다.
#
# - 지표·회계기준은 ACODE와 ACONTEXT 축 멤버에 대응하므로 추출 계층이 안다.
# - 문서군은 manifest 표기(doc_group)와의 대응을 알아야 하므로 검색 계층이 안다.
#
# 기존 import 경로를 유지하기 위해 이 모듈에서도 같은 이름으로 노출한다.
#
# 파생 개념은 추출 계층의 어휘가 아니다. 대응하는 ACODE가 없고 문서에서 읽히지도 않으며,
# 원천 지표에서 계산으로 만들어진다. 그래서 agent.derived_metrics가 정의한다.
from disclosure_agent.agent.company_resolution import (
    CompanyResolution,
    CompanyResolver,
)
from disclosure_agent.agent.derived_metrics import DerivedMetric

# 같은 것을 다르게 쓴 표현을 정본 값으로 옮기는 규칙은 agent.query_vocabulary가 갖는다.
#
# 나눠 두는 이유:
#     이 모듈이 정하는 것은 "무엇을 요구하고 무엇을 막는가"이고, 그 앞에 "질문에 적힌 말이
#     무엇을 가리키는가"가 있다. 둘을 한 파일에 두면 어휘를 넓히는 변경이 판정 순서를 함께
#     건드리게 되고, 표현 하나를 받아들이는 변경과 지원 범위를 넓히는 변경이 구분되지 않는다.
from disclosure_agent.agent.query_vocabulary import (
    OPERATION_AGGREGATE,
    PRODUCT_TOKENS,
    YEAR_PATTERN,
    find_contract_fields,
    find_product_phrase_span,
    find_year_spans,
    find_years,
    matches_product_phrase,
    normalize_aggregate_request,
)
from disclosure_agent.common.enums import (
    ChainPolicy,
    Intent,
    MetricConcept,
    QueryAccountingScope,
    SectionScope,
)
from disclosure_agent.periodic.extractor import (
    METRIC_REVENUE,
    SCOPE_CONSOLIDATED,
    SCOPE_SEPARATE,
)
from disclosure_agent.retrieval.metadata_filter import (
    DOCUMENT_CLASS_EVENT,
    DOCUMENT_CLASS_HOLDING,
    DOCUMENT_CLASS_PERIODIC,
    normalize_company_name,
)

__all__ = [
    "AMBIGUOUS_COMPANY_REASON",
    "ANCHOR_SOURCE_LATEST_ANNUAL_FILING",
    "BUSINESS_DESCRIPTION_TOKENS",
    "BUSINESS_PROFILE_SECTION_SCOPES",
    "DOCUMENT_CLASS_PERIODIC",
    "DOCUMENT_SUBTYPE_ANNUAL",
    "INTENT_CONTRACT_FACT",
    "INTENT_CROSS_ENTITY_COMPARE",
    "INTENT_DOCUMENT_SUMMARY",
    "INTENT_EVENT_HISTORY",
    "INTENT_HOLDING_FACT",
    "INTENT_SINGLE_FACT",
    "INTENT_TEMPORAL_COMPARE",
    "INTENT_UNANSWERABLE_CHECK",
    "KNOWN_INTENTS",
    "METRIC_REVENUE",
    "PREVIOUS_YEAR_EXPRESSIONS",
    "PREVIOUS_YEAR_OFFSET",
    "PRODUCT_TOKENS",
    "SCOPE_CONSOLIDATED",
    "SCOPE_SEPARATE",
    "SECTION_SCOPE_TOKENS",
    "SUMMARY_REQUEST_TOKENS",
    "SUPPORTED_CONTRACT_FIELDS",
    "QueryPlan",
    "RelativePeriod",
    "UnsupportedQuestionError",
    "build_company_resolver",
    "build_query_plan",
    "normalize_company_name",
    "resolve_companies",
]

# 이번 슬라이스가 다루는 고정 값. common/enums.py는 팀 합의 전이므로 여기서 국소 정의한다.
# doc_subtype은 manifest 표기와 값이 같아 대응표가 필요 없다.
DOCUMENT_SUBTYPE_ANNUAL = "annual"

# 실행 Pattern을 가르는 질의 의도.
#
# single_fact와 event_history는 Contract v0.1의 Intent가 정의한다. 값이 어긋나지 않도록
# enum에서 가져온다. 나머지 넷은 docs/agent_design/query_plan.md에는 있으나 Contract v0.1에는
# 아직 없다. Router가 '지원하지 않는다'를 구체적인 이유로 답하기 위해 이름만 남겨 둔다.
INTENT_SINGLE_FACT = Intent.SINGLE_FACT.value
INTENT_EVENT_HISTORY = Intent.EVENT_HISTORY.value

# 거래소공시 단건 사실 조회(Q02). Contract v0.1의 Intent에는 아직 없다.
#
# single_fact를 재사용하지 않는 이유:
#     Router는 intent 하나만 보고 handler를 고른다. single_fact로 담으면 이 계획이 정기공시
#     Pattern A handler로 들어가 계약 질문에 회계 지표 경로가 실행된다. 그 답은 형식이 맞아
#     틀렸다는 사실이 드러나지 않는다. 별도 이름이면 handler가 등록될 때까지
#     unimplemented_intent로 막힌다.
INTENT_CONTRACT_FACT = "contract_fact"
INTENT_HOLDING_FACT = "holding_fact"

INTENT_DOCUMENT_SUMMARY = "document_summary"
INTENT_TEMPORAL_COMPARE = "temporal_compare"
INTENT_CROSS_ENTITY_COMPARE = "cross_entity_compare"
INTENT_UNANSWERABLE_CHECK = "unanswerable_check"

# 거래소공시 단건 요약(Q06). document_summary를 재사용하지 않는다.
#
#     document_summary는 "정기공시 문서 1건의 지정 Section을 정리한다"는 뜻으로 이미
#     Pattern B handler가 읽고 있다. Q06은 Section 구조가 아니라 번호가 매겨진 필드 구조를
#     다루고(representative_questions.md 유형 2), 대상 문서를 고르는 규칙도 회계연도가 아니라
#     이벤트 체인이다. 같은 intent에 담으면 Pattern B의 입구 가드를 Q06 때문에 넓혀야 하고,
#     그 순간 정기공시 요약의 회계연도 요건이 함께 느슨해진다.
#
#     이름을 따로 두면 Pattern B는 그대로 두고 Router가 새 handler로 보낸다. Q02가
#     single_fact를 재사용하지 않은 것과 같은 이유다.
INTENT_EVENT_SUMMARY = "event_summary"

# 거래소공시 단건 요약이 지원하는 판본 정책의 전부.
#
# 이 둘만인 이유:
#     Q06이 답하는 것은 이벤트 하나의 '어느 시점 상태'다. latest_valid는 정정을 반영한 최신
#     유효 상태, original_only는 원공시 시점 상태이며, handler는 이 두 갈래만 실제로 만든다
#     (handlers.event_summary._summarize_selected_event).
#
#     full_chain은 상태가 아니라 변경 이력 전체를 답으로 삼는 정책이고, 그 답을 만드는 것은
#     Pattern E(event_history)다. 그 정책이 실린 요약 계획을 그대로 실행하면 handler의
#     else 가지가 받아 최신 상태를 만든다. 질문이 요구하지 않은 시점의 값이 요구한 것처럼
#     나가며, 어느 정책으로 답했는지는 결과에 남지 않는다. None도 같다.
#
# 여기 한 곳에만 적는다. _allows_absent_years와 __post_init__이 모두 이 값을 읽으므로,
# 정책이 늘어날 때 두 곳이 어긋날 수 없다.
EVENT_SUMMARY_CHAIN_POLICIES: tuple[ChainPolicy, ...] = (
    ChainPolicy.LATEST_VALID,
    ChainPolicy.ORIGINAL_ONLY,
)

KNOWN_INTENTS: tuple[str, ...] = (
    INTENT_SINGLE_FACT,
    INTENT_CONTRACT_FACT,
    INTENT_HOLDING_FACT,
    INTENT_DOCUMENT_SUMMARY,
    INTENT_EVENT_SUMMARY,
    INTENT_TEMPORAL_COMPARE,
    INTENT_CROSS_ENTITY_COMPARE,
    INTENT_EVENT_HISTORY,
    INTENT_UNANSWERABLE_CHECK,
)

# 인식 후보 기업명은 호출자가 넘긴다. service는 manifest에서 읽어 전달한다.
# 특정 기업명을 코드에 넣어 두지 않는다. 그것도 결국 추측이고, 목록에 없는 기업을
# 조용히 인식하거나 못 하는 차이를 만든다.
DEFAULT_KNOWN_COMPANIES: tuple[str, ...] = ()

# 기업 표현을 하나로 확정하지 못했을 때의 거절 사유.
#
# unknown_company와 따로 두는 것이 규약이다. 둘은 실제로 일어난 일이 다르다.
#
#     unknown_company              질문이 가리킨 기업이 이 코퍼스에 없다.
#     ambiguous_company_match      질문이 가리킨 표현에 맞는 기업이 여럿이며, 그중 하나를
#                                  고르지 않았다.
#
# 한 사유로 묶으면 '삼성'을 물은 사용자에게 '그런 기업 없음'이라고 답하게 된다. 실제로는
# 여덟 곳이 있고 어느 곳인지만 정해지지 않았다.
#
# 값이 company_resolution.RESOLUTION_AMBIGUOUS_MATCH와 다른 것은 층이 다르기 때문이다.
# 그쪽은 해석 방법(method)의 이름이고, 이쪽은 Router가 결과에 싣는 거절 사유의 이름이다.
AMBIGUOUS_COMPANY_REASON = "ambiguous_company_match"

# 절대연도 어휘(YEAR_PATTERN, REPORT_CONTEXT_YEAR_PATTERN)는 agent.query_vocabulary가
# 갖는다. 위 import가 이 모듈의 이름으로 그대로 잇는다.
#
# 옮긴 이유:
#     '년'이 붙은 표기와 보고서 종류가 붙은 표기를 함께 읽어야 하고, 두 규칙이 한자리에
#     있어야 어느 쪽을 넓혔는지가 코드에 남는다.
#
# 이름을 이 모듈에서도 읽을 수 있게 두는 이유:
#     주제어에서 기간 표기를 걷어내는 자리(_event_summary_subject_tokens)가 '년'이 붙은
#     표기만 보면 되고, 그 자리는 연도를 세지 않는다.
#
# 기간을 세는 자리는 이 패턴을 직접 쓰지 않는다:
#     연도를 세는 것은 find_years이고, 자리까지 필요한 곳은 find_year_spans다. Pattern F
#     Gate(agent.safety)도 그 헬퍼를 쓴다. Gate가 자기 정규식을 따로 두던 동안 "2027년
#     사업보고서"와 "2027 사업보고서"가 갈렸고, 두 정규식을 대조하는 테스트는 대조 자체가
#     '년' 표기만 보고 있어 그것을 잡지 못했다.

# 기준 연도(anchor)에서 한 해 앞을 가리키는 상대 기간 표현.
#
# 과거 방향만 둔다. "내년"·"올해"는 여기 넣지 않는다. 미래 방향은 Pattern F 소관이며
# (F4는 safety Gate가 query_analyzer보다 앞에서, F1은 코퍼스 경계 판정이 맡는다), 이 모듈이
# 미래 연도를 만들어 내면 아직 존재할 수 없는 문서를 찾는 질의가 된다.
#
# 이 목록은 사람이 읽는 어휘이고, 실제 판정은 아래 _PREVIOUS_YEAR_PATTERN이 한다. 두 벌을
# 두는 이유는 부분문자열 검사로는 "재작년"과 "작년"을 가를 수 없기 때문이다.
PREVIOUS_YEAR_EXPRESSIONS = ("작년", "전년", "전년도", "지난해")

# 기준 연도에서 몇 해를 옮길 것인가. 이번 범위는 한 해 앞 하나뿐이다.
PREVIOUS_YEAR_OFFSET = -1

# 상대 기간 기준 연도의 출처.
#
# 값을 문자열로 남기는 이유는 결과를 읽는 쪽이 "몇 년인가"뿐 아니라 "무엇을 보고 정했는가"까지
# 알아야 하기 때문이다. 실행 시각으로 정한 값과 문서 존재로 정한 값은 재현성이 다르다.
ANCHOR_SOURCE_LATEST_ANNUAL_FILING = "latest_annual_filing"

# 지원하는 과거 상대 기간 표현.
#
# 부분문자열 검사를 쓰지 않는 이유가 이 자리에 있다. "재작년"은 "작년"을, "지지난해"는
# "지난해"를, "전전년도"는 "전년"을 각각 안에 담고 있다. `in`으로 보면 셋 다 걸리고, 걸린 것을
# 그대로 해석하면 두 해 앞을 물은 질문에 한 해 앞을 답하게 된다. 근거를 갖춘 채로 질문과 다른
# 기간을 답하는 형태이며, 개수나 형식으로는 드러나지 않는다.
#
# 그래서 앞 글자를 lookbehind로 배제한다. 한국어에는 정규식의 단어 경계(\b)로 가를 자리가
# 없으므로, 경계 역할을 할 앞 글자를 직접 적는다.
#
#   (?<!재)작년      재작년 배제
#   (?<!전)전년도?   전전년/전전년도 배제. "전년도"는 "전년"과 같은 뜻이라 함께 받는다
#   (?<!지)지난해    지지난해 배제
_PREVIOUS_YEAR_PATTERN = re.compile(r"(?<!재)작년|(?<!전)전년도?|(?<!지)지난해")

# 상대 기간이긴 하나 이번 범위가 해석하지 않는 표현.
#
# 위 패턴이 걸러 내는 것만으로는 부족하다. "전년 동기"는 (?<!전)전년에 그대로 걸리는데, 그것이
# 가리키는 것은 전년도 연간이 아니라 전년 같은 분기다. 해석하면 분기 값을 물은 질문에 연간 값을
# 답하게 된다.
#
# 여기 걸리면 해석하지 않고 거절한다. 두 해 앞("재작년")을 지원 범위로 넓히는 것보다, 지원하지
# 않는다고 말하는 쪽이 먼저다.
#
# "전기"는 넣지 않는다. 회계 용어로는 전년도를 뜻하지만 "전기차"·"전기전자"·"전기공사"가 모두
# 같은 두 글자로 시작하고, 그쪽이 이 코퍼스에서 훨씬 흔한 기재 사실이다. 낱말 하나로 막으면
# 답할 수 있는 조회 질문을 막는다. 해석되지 않으므로 지금도 fail-closed다.
_UNSUPPORTED_RELATIVE_PERIOD_PATTERN = re.compile(
    r"재작년|지지난해|전전년도?|(?:전년|작년|지난해)\s*동기"
)

# 옛 이름. 부분문자열 검사만 하던 시절의 어휘이며 정본이 아니다.
#
# 남겨 둔 이유는 이 값을 import해 "해석하지 않은 상대 기간이 있는가"를 판정하던 호출부가 있기
# 때문이다. 그 판정은 이제 QueryPlan.has_unresolved_relative_period가 답한다. 계획에 남은
# 구조를 보지 않고 질문 문자열을 다시 읽으면, 해석에 성공한 계획도 "작년"이라는 글자가 남아
# 있다는 이유로 막힌다.
RELATIVE_PERIOD_TOKENS = PREVIOUS_YEAR_EXPRESSIONS

# 분기/반기 표현이 보이면 이번 슬라이스 범위 밖이다.
QUARTER_TOKENS = ("분기", "반기", "상반기", "하반기", "q1", "q2", "q3", "q4")

CONSOLIDATED_TOKENS = ("연결",)
SEPARATE_TOKENS = ("별도", "개별")

REVENUE_TOKENS = ("매출액", "매출", "영업수익", "수익")

# 지표 어휘 뒤에 붙어도 같은 요청으로 읽는 조사·어미.
#
# 여기 적힌 어형만 어휘의 연장으로 본다. "매출액이"·"매출액은?"·"매출액인가요?"를
# "매출액"과 같은 요청으로 읽기 위한 목록이며, 그 이상을 열지 않는다.
#
# 목록에 없는 음절이 이어지면 다른 낱말로 보고 지표를 읽지 않는다. "매출얼마야"처럼 조사를
# 생략하고 붙여 쓴 표기도 여기서는 읽히지 않는다. 그것을 읽으려고 경계를 늘리면 "매출원가"·
# "매출채권"이 같은 문으로 함께 들어온다. 모르는 어형은 막는 쪽이 먼저다
# (_metric_token_pattern 참조).
METRIC_TOKEN_SUFFIXES: tuple[str, ...] = (
    # 격조사·보조사
    "이",
    "가",
    "은",
    "는",
    "을",
    "를",
    "의",
    "도",
    "만",
    "과",
    "와",
    "과는",
    "와는",
    "랑",
    "이랑",
    "하고",
    "에",
    "에는",
    "에서",
    "에서는",
    "로",
    "로는",
    "으로",
    "으로는",
    "부터",
    "까지",
    "보다",
    "처럼",
    "만큼",
    "밖에",
    "나",
    "이나",
    "든",
    "이든",
    "든지",
    "이든지",
    "조차",
    "마저",
    "뿐",
    "라도",
    "이라도",
    "란",
    "이란",
    "라는",
    "이라는",
    # 서술격 조사와 의문 어미
    "야",
    "이야",
    "이다",
    "임",
    "인가",
    "인가요",
    "인지",
    "이니",
    "이냐",
    "입니다",
    "이에요",
    "예요",
)

# 지표 어휘의 좌우 경계로 쓰는 문자 부류. 한글 음절이 붙어 있으면 다른 낱말로 본다.
_HANGUL_SYLLABLE_CLASS = "가-힣"
_HANGUL_SYLLABLE_PATTERN = re.compile(f"[{_HANGUL_SYLLABLE_CLASS}]")


@lru_cache(maxsize=8)
def _metric_token_pattern(
    tokens: tuple[str, ...],
    scope_prefixes: tuple[str, ...],
) -> re.Pattern[str]:
    """
    지표 어휘를 낱말 경계까지 맞춰 찾는 패턴을 만든다.

    부분문자열 검사를 쓰지 않는 이유:
        "매출원가"·"매출채권"·"매출총이익"·"금융수익"에는 모두 REVENUE_TOKENS의 표기가
        연속 문자열로 들어 있다. 담고만 있는지 보면 이 개념들이 전부 매출로 분류되고,
        그렇게 만들어진 계획은 실제로 매출 Fact를 근거까지 갖춘 채 돌려준다. 형식이 맞아
        틀렸다는 사실이 결과 어디에도 드러나지 않는다. 추출 계층은 라벨을 정확히 비교하므로
        이미 안전하고, 뚫려 있는 자리는 질의 해석 한 곳이다.

    경계 규약:
        왼쪽    앞 글자가 한글 음절이면 매치하지 않는다. "제품매출액"·"순매출액"·
                "기타영업수익"·"이자수익"이 여기서 걸린다.

                예외는 회계기준 어휘 하나가 붙어 있는 경우다. "연결매출액"·"별도매출액"·
                "개별매출액"은 이 모듈이 이미 회계기준으로 읽는 낱말(CONSOLIDATED_TOKENS,
                SEPARATE_TOKENS)에 지표 어휘가 붙은 형태이며, 같은 질문을 띄어 쓴
                "연결 매출액"은 처음부터 읽고 있었다. 붙여 썼다는 이유로 막으면 답할 수
                있는 질문이 표기 차이로 막힌다.

                허용 목록은 회계기준 어휘 그 자체다. 여기에 다른 접두어를 적지 않는다.
                "제품"·"순"처럼 뜻을 바꾸는 접두어를 함께 열면 이 함수가 막으려던 것이
                다시 들어온다. 접두어 자체도 왼쪽 경계를 요구하므로 "비연결매출액"은
                걸린다.

        오른쪽  뒤에 한글 음절이 이어지면 매치하지 않는다. 다만 METRIC_TOKEN_SUFFIXES에
                적힌 조사·어미 하나가 통째로 이어지고 그 뒤가 다시 경계이면 같은 낱말로
                본다. "매출채권"·"수익성"·"수익률"이 여기서 걸린다.

        모르는 어형은 열지 않는다. 경계를 넓히는 쪽이 언제나 나중이며, 지금 넓히면 위의
        개념들이 같은 문으로 함께 들어온다. "연결기준매출액"처럼 회계기준 어휘와 지표
        어휘 사이에 다른 낱말이 낀 표기도 여기서는 읽지 않는다. 띄어 쓰면 읽힌다.

    매치 범위:
        회계기준 접두어를 매치에 포함시킨다. 찾기만 할 때는 어느 쪽이든 같지만, 호출부가
        '경계로 읽히지 않은 지표 어휘가 남아 있는가'를 매치 구간으로 판정한다
        (_find_unsupported_revenue_occurrence). 접두어를 매치 밖에 두면 "연결매출액"의
        "매출액"이 구간 안에 들어오지 않아 지원하지 않는 표기로 잘못 걸린다.

    캐시 키에 대해:
        REVENUE_TOKENS는 테스트가 monkeypatch로 바꾼다. 모듈 수준에서 한 번 컴파일해 두면
        그 패턴은 바뀐 어휘를 보지 못하고, 어휘를 늘려도 판정이 그대로인 상태가 조용히
        만들어진다. 그래서 현재 어휘 자체를 인자로 받아 캐시 키에 넣는다. 회계기준 어휘도
        같은 이유로 인자다. 호출부는 호출 시점의 값을 넘긴다.
    """
    if not tokens:
        raise ValueError("지표 어휘가 비어 있으면 경계 패턴을 만들 수 없습니다.")

    # 긴 표기를 먼저 시도한다. "매출액"이 "매출"보다 앞에 놓여야 조사 판정이 "액"에
    # 걸려 넘어가지 않는다.
    alternation = "|".join(
        re.escape(token) for token in sorted(set(tokens), key=len, reverse=True)
    )
    suffixes = "|".join(
        re.escape(suffix)
        for suffix in sorted(set(METRIC_TOKEN_SUFFIXES), key=len, reverse=True)
    )
    prefixes = "|".join(
        re.escape(prefix)
        for prefix in sorted(set(scope_prefixes), key=len, reverse=True)
    )
    prefix_group = rf"(?:{prefixes})?" if prefixes else ""

    return re.compile(
        rf"(?<![{_HANGUL_SYLLABLE_CLASS}])"
        rf"{prefix_group}"
        rf"(?:{alternation})"
        rf"(?:{suffixes})?"
        rf"(?![{_HANGUL_SYLLABLE_CLASS}])"
    )


def _accounting_scope_prefixes() -> tuple[str, ...]:
    """
    지표 어휘 앞에 붙여 써도 되는 낱말. 회계기준 어휘가 정본이며 여기서 새로 적지 않는다.

    따로 적으면 회계기준 어휘가 늘 때 두 벌이 되고, 갈라진 뒤에는 "연결 매출액"은 읽는데
    "연결매출액"은 못 읽는 상태가 조용히 생긴다.
    """
    return (*CONSOLIDATED_TOKENS, *SEPARATE_TOKENS)


# 파생지표 표기. 여기 담기는 값은 계산으로 만들어지는 요청 개념이며 원천 지표가 아니다.
#
# 이번 범위는 영업이익률 하나다. 다른 비율 표현을 함께 넣지 않는 이유는, 대응표에 등록되지
# 않은 개념을 인식하면 계획에는 담기지만 만들 수 없는 값이 되기 때문이다. 인식 범위는
# agent.derived_metrics의 등록 범위를 넘지 않는다.
DERIVED_METRIC_TOKENS: tuple[tuple[str, DerivedMetric], ...] = (
    ("영업이익률", DerivedMetric.OPERATING_MARGIN),
)

MONTH_PATTERN = re.compile(r"(1[0-2]|0?[1-9])\s*월")

# 거래소공시 세부유형. 값은 manifest의 doc_subtype 표기와 같아야 한다.
EVENT_SUBTYPE_SUPPLY_CONTRACT = "단일판매공급계약체결"
EVENT_SUBTYPE_FACILITY_INVESTMENT = "신규시설투자등"

# Pattern D Q12의 결정적 후처리. Common Contract의 QueryPlan을 늘리지 않고
# agent 내부 실행 계획에만 남긴다. 두 신호가 모두 있어야 시설투자 집계 경로가
# 열리며, 하나라도 비면 유사한 이벤트 질문을 집계로 추측하지 않는다.
#
# OPERATION_AGGREGATE와 그 표현 어휘는 agent.query_vocabulary가 갖는다. "합치다"·"합쳐"·
# "합산"·"총합"·"전부 더해"가 모두 같은 요청이며, 그 대응을 이 판정 자리에 늘어놓으면
# 어휘가 늘 때마다 여기가 함께 늘어난다.
#
# OPERATION_COMPARE는 옮기지 않는다. 비교 표현에는 아직 정규화할 다른 표기가 없고, 옮겨
# 두면 어휘 모듈이 "정규화가 있는 개념"과 "이름만 있는 개념"을 함께 담게 된다.
OPERATION_COMPARE = "compare"

COMPARISON_REQUEST_TOKENS = ("더 커", "더 큰", "비교")


@dataclass(frozen=True)
class EventSubtypeRule:
    """
    거래소공시 세부유형 하나를 질문에서 알아보는 규칙.

    두 가지 인식 경로를 둔다.

    tokens
        하나만 나와도 유형이 확정되는 표현이다. '공급계약', '시설투자'처럼 공시 유형명이
        질문에 거의 그대로 쓰인 경우다.

    target_tokens + action_tokens
        사용자가 유형명 대신 무엇에 무엇을 했는지로 말한 경우다. Q15의 "사옥·제조시설 신축
        투자"에는 '신규시설투자'도 '시설투자'도 연속 문자열로 들어 있지 않아 tokens만으로는
        열리지 않는다.

        둘을 함께 요구하는 것이 규약이다. '투자' 한 낱말을 tokens에 넣으면
        투자판단관련주요경영사항, 투자자문, 투자활동 현금흐름 같은 다른 문서·다른 질문까지
        이 유형으로 끌려온다. 대상(사옥·제조시설)과 행위(신축·증설)가 함께 있어야 신규시설투자
        공시를 가리킨 것으로 본다.

    공시 시점(연도) 요구는 이 규칙이 정하지 않는다:
        예전에는 유형마다 requires_filing_anchor를 두어 공급계약 이력 질문에 연도를
        요구했다. 그 요구가 Q14("포드랑 맺었던 배터리 공급계약, 지금 어떻게 됐어?")를
        missing_year로 막았고, 그 사유는 실제로 일어난 일과 달랐다. 질문은 연도를 밝히지
        않았을 뿐이고, 실제로 답하지 못하는 까닭은 그 기업의 원공시 후보가 여럿이어서
        어느 계약인지 좁히지 못하는 것이다.

        후보가 여럿인지는 코퍼스를 열어야 알 수 있으므로 이 계층이 판정할 수 없다. 그래서
        연도 없는 이력 질문을 계획 단계에서 막지 않고, 후보를 하나로 좁히지 못하면 handler가
        ambiguous_root로 막는다(agent.handlers.event_history). 막는 자리를 후보 개수가
        보이는 곳으로 옮긴 것이며, 답하지 못하는 질문이 답하게 되는 것이 아니다.
        _reject_unsupported_relative_period·_looks_like_business_profile이 같은 이유로
        같은 선택을 했다.
    """

    subtype: str
    tokens: tuple[str, ...] = ()
    target_tokens: tuple[str, ...] = ()
    action_tokens: tuple[str, ...] = ()

    def matches(self, question: str) -> bool:
        if any(token in question for token in self.tokens):
            return True

        if not self.target_tokens or not self.action_tokens:
            return False

        return any(token in question for token in self.target_tokens) and any(
            token in question for token in self.action_tokens
        )


# 선언 순서는 우선순위가 아니다.
#
# 예전에는 앞의 규칙이 먼저 맞으면 그것으로 확정했다. 그 방식은 한 질문이 두 유형에 모두
# 맞을 때 뒤의 유형을 조용히 버린다. "2024년 공급계약 이후 공장 증설 투자 계획이 바뀌었어?"가
# 그 경우이며, 계획에는 공급계약만 남고 시설투자를 함께 물었다는 사실이 계획에도 결과에도
# 남지 않는다. companies·years를 하나로 줄이지 않는 것과 같은 종류의 축소다.
#
# 그래서 지금은 모든 규칙을 평가하고, 둘 이상 맞으면 고르지 않고 막는다
# (_extract_event_subtype_rule 참조). 순서가 결과에 영향을 주지 않으므로 규칙을 추가할 때
# 자리를 고민할 필요가 없다. 순서가 남기는 것은 진단 문자열에 유형이 나열되는 차례뿐이며,
# 그 차례가 고정돼 있어야 같은 질문이 늘 같은 detail을 낸다.
EVENT_SUBTYPE_RULES: tuple[EventSubtypeRule, ...] = (
    EventSubtypeRule(
        subtype=EVENT_SUBTYPE_SUPPLY_CONTRACT,
        tokens=("공급계약", "수주계약", "판매계약"),
    ),
    EventSubtypeRule(
        subtype=EVENT_SUBTYPE_FACILITY_INVESTMENT,
        tokens=("신규시설투자", "시설투자"),
        # 대상은 시설 자체를 가리키는 말만 둔다. '투자'가 붙은 다른 개념(투자판단·투자자문)과
        # 겹치는 낱말은 넣지 않는다.
        target_tokens=("제조시설", "생산시설", "사옥", "공장"),
        # 행위는 시설을 새로 짓거나 늘리는 말이다. '투자'를 여기 두는 것은 대상 표현과 함께
        # 있을 때만 읽히기 때문이다. 단독으로는 어떤 유형도 열지 못한다.
        action_tokens=("신축", "증설", "건설", "투자"),
    ),
)

# 변경 이력을 묻는 표현. 이게 있어야 event_history로 본다.
HISTORY_TOKENS = (
    "바뀐",
    "바뀌",
    "변경",
    "정정",
    "수정",
    "달라진",
    "달라졌",
    "이력",
    "어떻게 됐",
)


# --------------------------------------------------------------------------
# 단일 문서 요약(Pattern B)의 어휘
# --------------------------------------------------------------------------

# 요약을 요청하는 표현. 이게 있어야 document_summary로 본다.
#
# 값을 묻는 표현("알려줘", "얼마야")을 넣지 않는다. Q02("금액이랑 계약기간 알려줘")가
# 그 경우이며, 넣으면 단일 사실 조회 질문이 요약 경로로 끌려간다. 여기 담는 것은 사용자가
# 값 하나가 아니라 서술 범위를 정리해 달라고 말한 표현뿐이다.
#
# 문항 번호나 특정 기업명으로 가르지 않는다. 대표 질문 문자열을 그대로 알아보는 규칙은
# 그 문장 하나만 통과시키고 같은 모양의 다른 질문을 막는다.
SUMMARY_REQUEST_TOKENS = ("요약", "정리")

# 요약 대상이 정기공시임을 밝히는 표기.
#
# 사업보고서 하나만 둔다. 반기·분기보고서 표기는 QUARTER_TOKENS가 의도 분기보다 앞에서
# 거절하므로 여기까지 오지 않는다.
PERIODIC_REPORT_TOKENS = ("사업보고서",)

# 문서 내 범위(section_scope) 표기.
#
# 알아보는 것은 설비 투자와 사업 개요 두 범위다. 나머지 SectionScope 값은 어휘로만 있고
# 여기에 표기가 없다. 표기를 먼저 넣으면 Section 규칙이 없는 범위의 계획이 만들어지고, 그
# 계획은 handler에서 막힌다. 막히는 자리가 뒤로 밀릴 뿐 답하지 못하는 것은 같으므로, 규칙과
# 표기를 함께 늘린다. business_overview도 그 규칙(periodic.sections)과 같은 변경에서 늘렸다.
#
# 판정 전에 질문의 공백을 걷어낸다. "설비 투자"와 "설비투자"가 같은 범위를 가리키는데
# 표기만 다르며, 두 벌을 적어 두면 한쪽을 빠뜨렸을 때 조용히 막힌다.
#
# business_overview의 표기를 셋으로 둔 이유:
#     "사업 구조"는 Q09가 쓰는 표현이고, "사업 개요"·"사업의 개요"는 사업보고서가 그 내용을
#     담고 있는 Section의 제목이다. 셋 다 같은 범위를 가리키므로 함께 둔다.
#
#     '사업'만으로는 알아보지 않는다. 공백을 걷어내면 "사업보고서"가 그 조건을 만족해,
#     문서 종류를 밝힌 모든 질문이 이 범위를 가리킨 것으로 읽힌다.
SECTION_SCOPE_TOKENS: tuple[tuple[SectionScope, tuple[str, ...]], ...] = (
    (SectionScope.FACILITY_INVESTMENT, ("설비", "시설투자")),
    (SectionScope.BUSINESS_OVERVIEW, ("사업구조", "사업개요", "사업의개요")),
)

# --------------------------------------------------------------------------
# 기업 소개 요약(Q05)의 어휘
# --------------------------------------------------------------------------

# "무슨 사업을 하고 주력 제품이 뭐야?"처럼 사업 개요와 주요 제품을 함께 묻는 표현.
#
# 두 묶음을 모두 요구한다. 사업 개요만 묻거나 제품만 묻는 질문은 이 모양이 아니며, 그런
# 질문을 여기로 끌어오면 묻지 않은 범위까지 요약한 결과가 나간다. 한 묶음만으로 판정하면
# "주요 제품 매출이 얼마야?" 같은 값 조회 질문도 요약 경로로 끌려온다.
#
# 요약 요청 표현("정리해줘")도 보고서 종류("사업보고서")도 요구하지 않는다. 이 질문 모양은
# 문서를 지목하지 않고 기업을 소개해 달라고 말하며, 그 답이 사업보고서의 사업 개요·주요 제품
# Section에 있다는 것이 이 경로가 아는 서식 지식이다.
#
# 판정 전에 질문의 공백을 걷어낸다. SECTION_SCOPE_TOKENS와 같은 이유다.
#
# 사업 서술 표기는 조사까지 요구한다("무슨 사업을", "어떤 사업을", "어떤 사업인지"). '무슨
# 사업'만으로 보면 "무슨 사업부의 주요 제품이야?"처럼 사업부를 묻는 질문이 함께 걸리고, 그
# 질문은 기업 소개가 아니다. 조사를 붙이면 '사업부'는 걸리지 않는다.
#
# 제품 표현은 어휘 모듈이 갖는다(query_vocabulary.PRODUCT_PHRASE_PATTERNS). 같은 것을
# 묻는 어순이 여럿이고("주력 제품", "어떤 제품이 주력이야", "무슨 제품을 만들어"),
# 그것을 여기에 낱말로 늘어놓으면 어순 하나를 넓힐 때마다 이 자리가 함께 늘어난다.
# PRODUCT_TOKENS는 그 개념의 정본 표기이며 판정은 matches_product_phrase가 한다.
BUSINESS_DESCRIPTION_TOKENS = ("무슨사업을", "어떤사업을", "어떤사업인지")

# 기업 소개 요약이 답하지 않고도 남아 있을 수 있는 말.
#
# 왜 필요한가:
#     제품 표현이 질문의 머리라는 것과 제품 표현이 질문의 **전부**라는 것은 다른 진술이다.
#     앞엣것만 보면 "대표 제품이 뭐고 출시일도 알려줘"가 통과한다. '대표 제품이 뭐'까지는
#     이 경로가 답하는 질문이 맞고, 그 뒤에 붙은 '출시일도 알려줘'는 답하지 않는다. 그런데
#     결과는 사업 개요와 주요 제품을 실은 정상적인 요약이라, 물어본 출시일이 빠졌다는 것이
#     응답 어디에도 남지 않는다. 형식이 맞는 답이 나가므로 사용자도 알아채지 못한다.
#
# 어떻게 가르는가:
#     이 경로가 의미를 가져간 구간(제품 표현·사업 서술 표기)과 질문이 답과 무관하게 담을
#     수 있는 것(기업명·기간)을 걷어낸다. 그러고도 남는 것이 있으면 답하지 않을 요청이
#     남은 것이므로 경로를 열지 않는다.
#
# 왜 속성 낱말 목록이 아닌가:
#     막을 것을 적으면 목록에 없는 속성이 오는 날 같은 축소가 다시 일어나고, 그때 일어나는
#     일은 '조용히 요약했다'라서 구멍이 드러나지도 않는다. 그래서 남아도 되는 것을 적는다.
#     아래는 전부 기능 형태소(조사·어미·접속사·요청 표현)와 구두점이며, 내용을 가리키는
#     낱말은 하나도 없다. 그 낱말이 남으면 무엇을 가리키든 요청이 남은 것으로 본다.
#
# 닫힌 부류만 담는다:
#     조사와 어미는 한국어에서 더 늘지 않는 부류다. 반대로 '출시일'·'생산량'·'가격'이 속한
#     부류는 끝이 없다. 늘지 않는 쪽을 적어야 목록이 완결된다.
#
# 맨 '가'와 맨 '주'를 넣지 않는 이유:
#     둘을 넣으면 '주가'가 조사 둘로 읽혀 통째로 사라진다. 남아야 할 요청이 사라지는 쪽이
#     여기서는 더 나쁘다. 넣지 않으면 이 표기를 담은 질문은 요약 경로가 열리지 않고 기존
#     경로로 흘러간다. 그쪽이 안전한 방향이다.
BUSINESS_PROFILE_SCAFFOLDING: tuple[str, ...] = (
    # 요청 표현.
    "알려주세요",
    "알려줘",
    "알려",
    "설명해주세요",
    "설명해줘",
    "소개해주세요",
    "소개해줘",
    "말해주세요",
    "말해줘",
    "정리해주세요",
    "정리해줘",
    "보여줘",
    "주세요",
    "줄래",
    "줘",
    "해줘",
    # 종결·의문 어미.
    "인가요",
    "일까요",
    # '뭔가요'의 꼬리. 맨 '가'를 넣지 않고 이 표기만 받는다. 맨 '가'를 넣으면 '주가'가
    # 조사 둘로 읽혀 통째로 사라지지만, 두 글자로 묶으면 그 일이 생기지 않는다.
    "가요",
    "이에요",
    "예요",
    "이야",
    "인지",
    "뭔지",
    "나요",
    "는지",
    "은지",
    "어요",
    "아요",
    "해요",
    "요",
    "야",
    "지",
    "어",
    "아",
    "해",
    "니",
    "냐",
    "죠",
    # 접속.
    "그리고",
    "또한",
    "하는지",
    "하고",
    "하며",
    "이며",
    "이고",
    "이랑",
    "랑",
    "와",
    "과",
    "및",
    "또",
    "고",
    # 조사.
    "은",
    "는",
    "이",
    "을",
    "를",
    "도",
    "의",
    "에서",
    "에",
    "으로",
    "로",
    "만",
    "까지",
    "부터",
    # 기간 단위와 상대 기간. 절대연도 숫자는 find_year_spans가 따로 걷어낸다.
    "년",
    *PREVIOUS_YEAR_EXPRESSIONS,
    # 법인격 표기. 기업명을 걷어낸 자리에 남는다("한미반도체(주)는").
    "주식회사",
    "(주)",
    "㈜",
)

# 긴 표기부터 시도한다. 짧은 쪽이 먼저 걸리면 긴 표기의 앞부분만 지워진다("하고"보다 "고"가
# 먼저 걸리면 '하'가 남는다).
_BUSINESS_PROFILE_SCAFFOLDING_PATTERN = re.compile(
    "|".join(
        re.escape(token)
        for token in sorted(set(BUSINESS_PROFILE_SCAFFOLDING), key=len, reverse=True)
    )
    + r"|[\s?!.,·ㆍ、'\"“”‘’()\[\]{}<>《》「」]"
)

# 이 질문 모양이 요약하는 범위. 순서는 사업보고서 서식의 Section 순서이며, 결과의
# DocumentSummary도 이 순서로 실린다.
BUSINESS_PROFILE_SECTION_SCOPES: tuple[SectionScope, ...] = (
    SectionScope.BUSINESS_OVERVIEW,
    SectionScope.PRODUCTS,
)

# --------------------------------------------------------------------------
# 거래소공시 단건 요약(Q06)의 어휘
# --------------------------------------------------------------------------

# 요약 대상이 공시 문서 자체임을 밝히는 표기.
#
# 이 낱말 하나로 경로가 열리지는 않는다. 아래 세 조건이 모두 갖춰져야 한다
# (_looks_like_event_summary 참조). 공시라는 말은 질문 어디에나 붙을 수 있다.
EVENT_DISCLOSURE_TOKEN = "공시"

# 문서에 적힌 것을 달라는 표기. 값 하나를 묻는 질문과 가르는 자리다.
#
# "내용"을 요구하는 이유:
#     "공시 요약해줘"만으로 열면 "그 공시 금액 알려줘" 같은 값 조회까지 끌려온다. Q06은
#     문서에 적힌 항목들을 정리해 달라는 질문이고, 그 요청은 실제로 '내용'이라는 말로 온다.
#     대표 질문 셋(원문/원공시/처음 공시)이 모두 이 낱말을 쓴다.
EVENT_SUMMARY_CONTENT_TOKEN = "내용"

# 정리해 달라는 요청 표기.
#
# SUMMARY_REQUEST_TOKENS보다 넓다. Q06의 원공시 표현("처음 공시했을 때 내용 알려줘")은
# '요약'도 '정리'도 쓰지 않는다. 다만 넓힌 낱말은 위의 공시·내용 조건과 함께일 때만 읽히므로,
# "매출 알려줘" 같은 값 조회가 이 경로로 오지 않는다.
EVENT_SUMMARY_REQUEST_TOKENS = ("요약", "정리", "알려", "설명")

# 원공시 시점을 지목하는 표현.
#
# 공시라는 말과 붙어 있을 때만 읽는다. '처음'·'최초'만으로 판정하면 "처음 계약한 금액"처럼
# 시점이 아닌 다른 것을 가리키는 표현까지 원공시 요청으로 읽힌다.
#
# 이 표현이 없으면 최신 유효 상태다. 기본값을 코드가 고르는 것이 아니라, 질문이 시점을
# 밝히지 않았다는 사실이 "지금 유효한 내용"을 뜻한다(open_decisions.md D5의 chain_policy).
ORIGINAL_STATE_PATTERN = re.compile(r"(?:원|최초|처음|당초)(?:으로|로|에)?공시")

# 이벤트 주제어에서 걷어낼 낱말.
#
# 여기 담긴 것은 어느 공시를 가리키는지에 기여하지 않는 말이다. 남겨 두면 본문 대조에서
# 걸리지 않아 실제로 있는 공시가 '없다'로 판정된다.
#
# 기업명·기간·공시 표기·요청 표기는 여기 담지 않는다. 그것들은 각각 다른 규칙이 이미
# 걷어낸다(_event_summary_subject_tokens 참조).
EVENT_SUBJECT_STOP_TOKENS: frozenset[str] = frozenset(
    {
        "당시",
        "때",
        "관련",
        "건",
        "중",
        "그",
        "이",
        "저",
        "좀",
        "및",
        "한",
        "번",
        "한번",
    }
)

# 주제어에서 걷어낼 앞뒤 문장부호.
EVENT_SUBJECT_TRIM_CHARS = " \t\n.,!?·ㆍ、'\"“”‘’()[]{}<>《》「」"

# --------------------------------------------------------------------------
# 거래소공시 단건 사실 조회(Q02)의 어휘
# --------------------------------------------------------------------------

# 계약 문서 필드 어휘(CONTRACT_FIELD_VOCABULARY)는 agent.query_vocabulary가 갖는다.
#
# 옮긴 이유:
#     "계약 시작일"·"시작 날짜"처럼 같은 필드를 가리키는 표기가 여럿이고, 그것을 판정
#     자리마다 부분문자열로 늘어놓으면 표기가 늘 때 어느 자리를 함께 늘려야 하는지가
#     코드에 남지 않는다.

# 기업 소개 요약(Q05)이 요약 요청으로 받지 않는 값 조회 표기.
#
# "무슨 사업을 하고 주력 제품이 뭐고 2025년 매출은 얼마야?"처럼 사업 서술과 제품 표현이 있어도
# 값을 함께 묻는 질문은 순수한 문서 요약이 아니다. 그것을 요약으로 받으면 근거를 갖춘 채로
# 물은 값을 빼놓은 답이 나가고, 값을 물었다는 사실은 결과 어디에도 남지 않는다. 그래서 아래
# 표기가 하나라도 있으면 Q05 경로를 열지 않고, 질문은 이 경로가 생기기 전과 같은 경로(단일
# 사실 조회·계약 조회)로 흘러가 그쪽 규칙으로 답하거나 막힌다. 두 반을 함께 답하는 능력은
# 여기서 만들지 않는다.
#
# 표기는 이 모듈이 값 조회에 이미 쓰는 어휘를 그대로 잇는다. 지표 어휘가 늘면 여기도 함께
# 늘어야 하므로 이름을 다시 적지 않는다. 맨 뒤의 낱말들은 지표 이름 없이 값을 묻는 표현이다.
# 판정은 공백을 걷어낸 질문에 대해 하므로 "계약 금액"처럼 띄어 쓴 표기는 "금액"이 대신 받는다.
BUSINESS_PROFILE_EXCLUDED_TOKENS: tuple[str, ...] = (
    *REVENUE_TOKENS,
    *(token for token, _ in DERIVED_METRIC_TOKENS),
    # 계약 금액 조회. 판정이 공백을 걷어낸 질문에 대해 이뤄지므로 "계약 금액"·"계약금액"이
    # 모두 이 한 낱말에 걸린다.
    "금액",
    "영업이익",
    "얼마",
    "비중",
    "비율",
    "주식수",
    "지분",
)

# 이 구현이 실제로 답까지 옮길 수 있는 계약 필드 조합.
#
# 계획 단계에서 이 조합을 요구하는 이유:
#     answer_builder가 결정론적으로 문장을 만드는 형태는 대표 Q02 하나다. 계약금액만 담긴
#     계획이나 계약기간만 담긴 계획도 여기서는 만들 수 있었고 handler도 통과했지만, 문장을
#     만드는 자리에서 UnsupportedResultShapeError가 났다. 그 예외는 결과가 이미 ANSWERABLE로
#     판정된 뒤에 터지므로 막힌 이유가 결과에 남지 않고 HTTP 500이 된다.
#
#     같은 질문을 막을 것이라면 계획 단계에서 막는다. 그러면 Router가 query_plan 단계의
#     BLOCKED로 옮기고, 무엇을 지원하지 않는지가 detail에 남는다.
#
# 조합을 부분집합으로 두지 않는다:
#     계약기간만 담긴 결과는 answer_builder가 문장으로 옮길 수 있지만 여기서는 함께 막는다.
#     넓히려면 그 형태를 대표 질문으로 고정하고 회귀를 함께 두어야 하며, 지금 열어 두면
#     builder가 지원하는 범위와 planner가 여는 범위가 두 벌로 갈린다. 넓히는 쪽이 늘
#     나중이어야 조용히 어긋나지 않는다.
SUPPORTED_CONTRACT_FIELDS: frozenset[MetricConcept] = frozenset(
    (
        MetricConcept.CONTRACT_AMOUNT,
        MetricConcept.CONTRACT_START_DATE,
        MetricConcept.CONTRACT_END_DATE,
    )
)

# 계약 상대방을 가리키는 표현. "<상대방>과/와 체결한 계약" 형태만 읽는다.
#
# 라틴 문자 이름을 따로 두는 이유는 "Yang Ming"처럼 공백이 들어간 표기가 실제로 오기
# 때문이다. 한글 이름은 조사 앞까지만 읽는다.
#
# 넓게 잡지 않는다. 상대방을 추정해 넣으면 그 이름으로 문서를 고르게 되고, 틀린 상대방으로
# 고른 계약은 형식이 맞아 틀렸다는 사실이 드러나지 않는다.
COUNTERPARTY_PATTERN = re.compile(
    r"([A-Za-z][A-Za-z0-9&.\-]*(?:\s+[A-Za-z][A-Za-z0-9&.\-]*)*|[가-힣]{2,})"
    r"\s*(?:과|와)\s*(?:체결|계약|맺)"
)

# 이력 조회(Pattern E)에서 "질문이 계약 상대방을 지목했는가"를 읽는 표현.
#
# 위 COUNTERPARTY_PATTERN과 나눠 두는 이유:
#     두 패턴이 같은 개념을 읽지만 잘못 읽었을 때의 방향이 반대다.
#
#     Q02(단건 조회)는 읽은 이름으로 문서를 고른다. 잘못 읽으면 틀린 계약의 금액을 답하게
#     되므로 좁게 잡아야 한다.
#
#     Pattern E는 읽은 이름으로 문서를 고르지 않는다. 이름이 있다는 사실만으로 원공시 1건을
#     확정하지 않고 막는 데 쓴다(handlers.event_history의 unverified_counterparty). 잘못
#     읽으면 답할 수 있었던 질문이 막히고, 그것은 틀린 계약의 이력을 답하는 것보다 안전한
#     방향이다.
#
#     그래서 조사는 넓히고("포드랑 맺었던"이 Q14의 표기다) 뒤따르는 말은 좁힌다.
#
# 뒤따르는 말에서 '계약'을 뺀 이유:
#     넣으면 "계약금액이랑 계약기간은?"에서 '계약금액이'가 상대방으로 읽힌다. 거기서 '이랑'은
#     두 필드를 함께 묻는 접속 조사이지 상대를 가리키는 조사가 아니다. 체결·맺음을 뜻하는 말만
#     남기면 그 문장은 걸리지 않는다(tests/unit/test_q02_query_plan.py가 쓰는 실제 문장이다).
#
# 조사 나열은 긴 것이 앞이다. '이랑'을 뒤에 두면 '랑'이 먼저 맞아 이름 끝에 '이'가 남는다.
EVENT_COUNTERPARTY_PATTERN = re.compile(
    r"([A-Za-z][A-Za-z0-9&.\-]*(?:\s+[A-Za-z][A-Za-z0-9&.\-]*)*|[가-힣]{2,})"
    r"\s*(?:이랑|랑|과|와)\s*(?:체결|맺)"
)


class UnsupportedQuestionError(ValueError):
    """이번 슬라이스가 결정론적으로 처리할 수 없는 질문."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)

        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class RelativePeriod:
    """
    질문에 쓰인 상대 기간 표현과 그것을 절대연도로 옮긴 결과.

    QueryPlan이 이 값을 들고 다니는 이유:
        "이 계획에 해석하지 않은 상대 기간이 남아 있는가"를 계획만 보고 답할 수 있어야 한다.
        그 판정을 질문 문자열을 다시 읽어서 하면, 해석에 성공한 계획도 "작년"이라는 글자가
        남아 있다는 이유로 막힌다. 원래 그 판정은 답을 축소하지 않으려고 만든 것인데, 그렇게
        되면 답할 수 있는 질문을 막는 쪽으로 틀린다. 막는 쪽으로 틀리는 것도 틀리는 것이다.

    anchor_year를 None으로 둘 수 있게 한 이유:
        상대 기간을 읽었지만 옮기지 않은 계획이 실제로 있다. 절대연도와 상대 기간이 함께
        쓰인 질문("2025년 매출이 전년보다 얼마나 늘었어?")이 그 경우다. 그 질문에서 "전년"의
        기준이 문장에 적힌 2025인지 최신 사업보고서 연도인지는 이 구현이 정하지 않았고,
        정하지 않은 것을 정한 척하면 두 해석 중 하나가 조용히 답이 된다. 그래서 읽었다는
        사실만 남기고 연도는 비워 둔다.

    warnings에 담지 않는 이유:
        warnings는 사람이 읽는 진단 문자열이다. 기계가 상태를 판정해야 할 때 그 문자열을
        다시 파싱하면, 문구를 다듬는 일이 곧 동작 변경이 된다.
    """

    expression: str
    offset: int
    anchor_year: int | None = None
    anchor_source: str | None = None

    def __post_init__(self) -> None:
        if self.offset >= 0:
            # 미래 방향은 이 구현이 만들지 않는다. Pattern F 소관이며, 여기서 미래 연도를
            # 만들면 아직 존재할 수 없는 문서를 찾는 질의가 된다.
            raise ValueError(
                f"과거 방향 상대 기간만 표현합니다: offset={self.offset!r}"
            )

        if (self.anchor_year is None) != (self.anchor_source is None):
            # 연도만 있고 출처가 없으면 그 값이 무엇을 보고 정해졌는지 알 수 없다. 출처만
            # 있고 연도가 없으면 해석하지 않은 것을 해석한 것처럼 보인다.
            raise ValueError(
                "anchor_year와 anchor_source는 함께 있거나 함께 비어 있어야 합니다: "
                f"anchor_year={self.anchor_year!r}, anchor_source={self.anchor_source!r}"
            )

    @property
    def resolved(self) -> bool:
        """기준 연도가 정해졌는가."""
        return self.anchor_year is not None

    @property
    def resolved_years(self) -> tuple[int, ...]:
        """
        옮겨진 절대연도를 오래된 것부터 돌려준다. 옮기지 않았으면 빈 튜플.

        QueryPlan.years와 같은 값을 만들지만 정본은 years다. 이 값은 "무엇이 이 연도를
        만들었는가"를 되짚는 자리이며, 실행 경로가 읽는 곳이 아니다.
        """
        if self.anchor_year is None:
            return ()

        return (self.anchor_year + self.offset, self.anchor_year)


@dataclass(frozen=True)
class QueryPlan:
    """
    Q01 계열 단일 수치 조회를 위한 결정론적 실행 계획.

    intent는 Router가 handler를 고르는 유일한 기준이다. 기본값을 둔 이유는 기존 호출부와의
    호환 때문이며, 이 슬라이스가 만드는 단일 기업 계획은 모두 single_fact다.

    대상 기업은 companies 하나에만 담는다. 질문이 두 기업을 물었는데 계획이 한 곳만 담으면
    나머지 기업이 사라진 사실이 어디에도 남지 않고, 그 계획으로 만든 답은 형식만 맞고
    질문에는 답하지 않은 결과가 된다.

    대상 기간도 같은 이유로 years 하나에만 담는다. 질문이 두 기간을 물었는데 계획이 첫
    연도만 담으면 나머지 기간이 사라진 사실이 어디에도 남지 않는다.

    years의 위치:
        years는 이 dataclass, 즉 agent 내부 실행 계획이 절대연도를 담는 정본이다. 범위는
        agent 계층 안이며, 공유 Data Contract의 기간 표현을 대체하지 않는다.

        공유 Contract의 기간 정본은 common.schemas.QueryPlan.periods(list[Period])다. 그쪽은
        {kind, year, period_type, range}로 회계기간과 공시일을 구분하고 범위 표현까지 담는
        모델이며, 팀 확인을 거친 shared Data Contract의 일부다. 여기서 부정하거나 대신하는
        것이 아니다.

        두 계층을 이번 PR에서 연결하지 않는다. agent 내부 표현이 공유 경계를 넘어가야 할 때
        agent의 years -> common의 periods 변환을 명시적으로 하는 adapter를 두고, 그 자리에서
        kind와 period_type을 정한다. Pattern 내부 결과를 공통 Contract로 옮기는 일을
        agent.normalization이 맡는 것과 같은 구조다.

        지금 years만 두는 이유는 이 슬라이스가 요구하는 것이 절대연도를 잃지 않는 것뿐이기
        때문이지, 공유 Contract의 periods가 불필요해서가 아니다.
    """

    question: str
    companies: tuple[str, ...]
    years: tuple[int, ...]
    intent: str = INTENT_SINGLE_FACT
    document_class: str = DOCUMENT_CLASS_PERIODIC
    # 질문이 지목한 공시 세부유형. None은 '질문이 밝히지 않았다'이며 '아무거나 좋다'가 아니다.
    #
    # 거래소공시 단건 요약(Q06)에서 그 경우가 생긴다. "파주 AIDC 투자 공시"는 어느 공시
    # 유형인지 이름으로 말하지 않으므로, 유형을 추측해 채우면 그 추측이 후보를 잘못 좁힌다.
    # 대신 handler가 지원하는 유형 전체를 후보로 두고 본문 주제어로 좁히며, 좁혀지지 않으면
    # 답하지 않는다.
    document_subtype: str | None = DOCUMENT_SUBTYPE_ANNUAL
    accounting_scope: str = SCOPE_CONSOLIDATED
    # 거래소공시 질의에는 지표가 없다. 값이 아니라 이력을 묻기 때문이다.
    #
    # 이 자리에 담기는 것은 문서에서 추출되는 원천 지표다. 계산으로 만들어지는 요청 개념은
    # derived_metric에 담는다. 파생 개념을 이 자리에 담으면 그대로 추출 계층으로 넘어가고,
    # 계산으로 답할 수 있는 질문이 '지원하지 않는 지표'로 막힌다.
    metric: str | None = METRIC_REVENUE
    # 사용자가 요청한 파생 개념. metric과 서로 배타다.
    #
    # 이 값을 만들려면 어떤 원천 지표가 필요한지는 담지 않는다. agent.derived_metrics의
    # 대응표에서 전부 파생되므로, 계획에 함께 담으면 같은 질문에 대응표와 계획이 서로 다른
    # 답을 갖는 순간이 생긴다.
    derived_metric: DerivedMetric | None = None
    # 질문이 요구한 문서 필드. 거래소공시 단건 사실 조회(Q02)가 쓴다.
    #
    # metric을 튜플로 바꾸지 않고 자리를 따로 둔 이유:
    #     metric은 "이 계획이 답할 값 하나"라는 뜻으로 이미 여러 Pattern이 읽고 있다. 그것을
    #     복수로 바꾸면 읽는 쪽이 전부 "첫 값" 또는 "전부"를 다시 정해야 하고, 어느 쪽을
    #     골랐는지는 계획에 남지 않는다.
    #
    #     그리고 이 자리에 담기는 것은 회계 지표가 아니라 한 문서에서 함께 읽어야 하는 필드
    #     묶음이다. 계약금액·시작일·종료일은 서로 다른 지표가 아니라 한 계약을 서술하는 세
    #     값이며, 하나라도 빠지면 질문에 답한 것이 아니다.
    #
    # metric·derived_metric과 서로 배타다(__post_init__ 참조).
    requested_fields: tuple[MetricConcept, ...] = ()
    # 질문이 지목한 계약 상대방. 없으면 None이며 추정하지 않는다.
    #
    # companies에 담지 않는 이유:
    #     companies는 답의 주체가 되는 대상 기업이고 company property가 그 첫 값을 돌려준다.
    #     상대방을 그 자리에 담으면 두 기업을 물은 질문으로 보이거나 상대방이 주체가 된다.
    #
    # 이 값으로 문서를 고르는 일은 여기서 하지 않는다. 실제 후보 대조는 retrieval 계층이
    # 맡는다(agent.event_retrieval.select_contract_filing).
    counterparty: str | None = None
    # 지분공시의 보고자. companies에는 발행회사만 담는다.
    filer: str | None = None
    # 질문이 월까지 지정한 경우. Q13처럼 원공시를 월 단위로 지목할 때 쓴다.
    month: int | None = None
    # 정정·후속 문서를 어디까지 볼 것인가. 값은 Contract v0.1의 ChainPolicy를 따른다.
    chain_policy: ChainPolicy | None = None
    # 문서 안에서 어느 범위를 볼 것인가. 단일 문서 요약(Pattern B)에서 required다.
    #
    # 기본값을 두지 않는다. 범위를 읽지 못한 요약 질문에 '문서 전체'를 채워 넣으면
    # docs/agent_design/retrieval_strategy.md Pattern B가 금지한 문서 전체 요약이 조용히
    # 기본 동작이 된다. 읽지 못하면 계획을 만들지 않는다.
    section_scope: SectionScope | None = None
    # 한 문서에서 함께 요약해야 하는 범위 묶음. 기업 소개 요약(Q05)이 쓴다.
    #
    # section_scope를 튜플로 바꾸지 않은 이유:
    #     section_scope는 "이 계획이 요약할 범위 하나"라는 뜻으로 Pattern B(Q04)와
    #     Pattern C의 서술 비교(Q09)가 이미 읽고 있다. 그것을 복수로 바꾸면 읽는 쪽이 전부
    #     "첫 값" 또는 "전부"를 다시 정해야 한다. requested_fields가 metric 옆에 따로 있는
    #     것과 같은 구조다.
    #
    # section_scope와 서로 배타다(__post_init__ 참조). 단일 문서 요약 계획에서만 쓴다.
    section_scopes: tuple[SectionScope, ...] = ()
    # 질문이 어느 이벤트를 가리키는지 밝힌 주제어. 거래소공시 단건 요약(Q06)이 쓴다.
    #
    # 왜 계획에 담는가:
    #     Q06의 대상 문서는 회계연도로도 공시 세부유형으로도 좁혀지지 않는다. 한 기업이 같은
    #     유형의 공시를 여러 건 낼 수 있고, 질문이 그중 하나를 가리키는 근거는 본문에 적힌
    #     주제("파주 AIDC 투자")뿐이다. 그 주제를 계획에 담아야 handler가 질문 문자열을 다시
    #     읽지 않는다. counterparty가 Q02에서 같은 자리에 있는 것과 같은 구조다.
    #
    # 문서 식별자를 담지 않는 이유:
    #     doc_id나 접수번호를 계획에 넣으면 그 질문 하나만 답하고 같은 모양의 다른 질문은
    #     답하지 못한다. 여기 담기는 것은 사용자가 실제로 쓴 말이며, 어느 문서가 그 말에
    #     해당하는지는 코퍼스를 열어 본 handler가 정한다.
    event_subject_tokens: tuple[str, ...] = ()
    # accounting_scope가 질문에 명시됐는지 기록한다. 기본값으로 채운 경우를 구분하기 위함이다.
    accounting_scope_explicit: bool = True
    # 질문에 상대 기간 표현이 있었다면 그 표현과 해석 결과.
    #
    # 이 계획을 읽는 쪽이 "해석하지 않은 상대 기간이 남아 있는가"를 계획만 보고 답할 수 있게
    # 하는 자리다. 질문 문자열을 다시 읽는 판정을 대신한다
    # (has_unresolved_relative_period 참조).
    #
    # 공유 Data Contract에는 넣지 않는다. 이 값은 질의 해석 계층이 자기 판정을 기록해 두는
    # 실행 계획 상태이며, Pattern 결과로 나가지 않는다. 공유 Contract가 미해결 기간을
    # 표현해야 할 때가 오면 common.schemas.Period.kind가 그 자리를 이미 갖고 있다.
    relative_period: RelativePeriod | None = None
    # 원문 검색 후 수행할 결정적 처리. 현재 실행 경로에서는 Q12의
    # aggregate + compare만 명시하며, 다른 Pattern의 기존 계획은 빈 튜플을 유지한다.
    operations: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # 문자열을 그대로 받으면 글자 단위로 순회되어 '한미반도체'가 다섯 기업이 된다.
        # 조용히 통과시키면 이 계획을 만든 쪽이 아니라 답을 받는 쪽에서 틀린다.
        if isinstance(self.companies, str):
            raise TypeError(
                "companies는 기업명 문자열 하나가 아니라 튜플이어야 합니다: "
                f"{self.companies!r}"
            )

        if isinstance(self.years, int):
            raise TypeError(
                "years는 연도 하나가 아니라 튜플이어야 합니다: " f"{self.years!r}"
            )

        if not self.companies:
            raise ValueError("companies는 최소 한 곳의 기업을 담아야 합니다.")

        # 이벤트 단건 요약 계획은 지원하는 판본 정책만 담을 수 있다.
        #
        # handler는 original_only만 명시적으로 갈라 보고 나머지는 최신 유효 상태를 만드는
        # 가지로 떨어진다. 그래서 이 자리를 비워 두면 full_chain(또는 None)이 실린 계획이
        # 조용히 '최신'으로 실행되고, 질문이 요구하지 않은 시점의 값이 요구한 것처럼 나간다.
        # handler에서 되돌려 잡지 않고 계획이 만들어지는 순간 막는다. 정책 규칙의 정본은
        # EVENT_SUMMARY_CHAIN_POLICIES 하나이며, 이 검사와 _allows_absent_years가 함께
        # 그것을 읽는다.
        #
        # 연도 검사보다 앞에 둔다. 뒤에 두면 연도를 담은 계획에서만 이 사유가 나오고,
        # 연도 없는 full_chain 계획은 "years가 없다"로 막혀 무엇이 잘못됐는지 어긋나게
        # 보고된다. 어차피 _allows_absent_years도 같은 집합을 보므로 순서를 앞으로 옮기면
        # 그쪽은 이미 허용된 정책만 보게 된다.
        if (
            self.intent == INTENT_EVENT_SUMMARY
            and self.chain_policy not in EVENT_SUMMARY_CHAIN_POLICIES
        ):
            supported = ", ".join(policy.value for policy in EVENT_SUMMARY_CHAIN_POLICIES)

            raise ValueError(
                "이벤트 단건 요약 계획이 지원하지 않는 chain_policy입니다: "
                f"chain_policy={self.chain_policy!r}. 지원하는 값: {supported}. "
                "지원하지 않는 정책을 최신 유효 상태로 바꿔 읽지 않습니다."
            )

        if not self.years and not self._allows_absent_years():
            raise ValueError(
                "years는 최소 한 개의 연도를 담아야 합니다: "
                f"intent={self.intent!r}, chain_policy={self.chain_policy!r}."
            )

        # 단일 문서 요약 계획은 범위 없이 만들 수 없다.
        #
        # 요약 범위는 이 Pattern의 답 그 자체다. 범위가 비면 이 계획을 실행하는 쪽이 어디를
        # 요약할지 골라야 하고, 무엇을 골랐는지는 계획에도 결과에도 남지 않는다.
        if (
            self.intent == INTENT_DOCUMENT_SUMMARY
            and self.section_scope is None
            and not self.section_scopes
        ):
            raise ValueError(
                "단일 문서 요약 계획에는 section_scope가 있어야 합니다: "
                f"question={self.question!r}."
            )

        # 이벤트 단건 요약 계획은 주제어 없이 만들 수 없다.
        #
        # 주제어는 이 Pattern이 대상 문서를 고르는 유일한 근거다. 비어 있으면 실행하는 쪽이
        # 기업의 거래소공시 중 하나를 스스로 골라야 하고, 무엇을 골랐는지는 계획에도 결과에도
        # 남지 않는다. section_scope를 요구하는 규칙과 같은 자리다.
        if self.intent == INTENT_EVENT_SUMMARY and not self.event_subject_tokens:
            raise ValueError(
                "이벤트 단건 요약 계획에는 event_subject_tokens가 있어야 합니다: "
                f"question={self.question!r}."
            )

        # 문자열을 그대로 받으면 글자 단위로 순회된다. companies를 막는 것과 같다.
        if isinstance(self.event_subject_tokens, str):
            raise TypeError(
                "event_subject_tokens는 주제어 하나가 아니라 튜플이어야 합니다: "
                f"{self.event_subject_tokens!r}"
            )

        # 주제어는 이벤트 단건 요약에서만 뜻이 있다. 다른 의도에 실리면 그 handler는 이 값을
        # 읽지 않으므로, 질문이 좁힌 조건이 조용히 사라진 계획이 된다.
        if self.event_subject_tokens and self.intent != INTENT_EVENT_SUMMARY:
            raise ValueError(
                "event_subject_tokens는 이벤트 단건 요약 계획에서만 담을 수 있습니다: "
                f"intent={self.intent!r}."
            )

        # SectionScope은 StrEnum이라 낱개로 넘겨도 문자열로 순회된다. companies를 막는
        # 것과 같다.
        if isinstance(self.section_scopes, str):
            raise TypeError(
                "section_scopes는 SectionScope 하나가 아니라 튜플이어야 합니다: "
                f"{self.section_scopes!r}"
            )

        # 범위 하나와 범위 묶음을 함께 담지 않는다.
        #
        # 둘 다 담기면 이 계획을 읽는 쪽이 어느 쪽을 요약할지 스스로 골라야 하고, 무엇을
        # 골랐는지는 계획 어디에도 남지 않는다. metric/derived_metric 규칙과 같은 이유다.
        if self.section_scope is not None and self.section_scopes:
            raise ValueError(
                "section_scope와 section_scopes를 함께 담을 수 없습니다: "
                f"section_scope={self.section_scope!r}, "
                f"section_scopes={self.section_scopes!r}. 요약 범위의 정본은 하나여야 합니다."
            )

        # 범위 묶음은 단일 문서 요약에서만 뜻이 있다. 다른 의도에 실리면 그 handler는 이
        # 값을 읽지 않으므로, 요청한 범위가 조용히 사라진 계획이 된다.
        if self.section_scopes and self.intent != INTENT_DOCUMENT_SUMMARY:
            raise ValueError(
                "section_scopes는 단일 문서 요약 계획에서만 담을 수 있습니다: "
                f"intent={self.intent!r}."
            )

        if len(set(self.section_scopes)) != len(self.section_scopes):
            raise ValueError(
                f"section_scopes에 같은 범위가 거듭 담겨 있습니다: {self.section_scopes!r}."
            )

        # 원천 지표와 파생 개념을 동시에 담지 않는다.
        #
        # 둘 다 담기면 이 계획을 읽는 쪽이 무엇을 답해야 하는지 스스로 골라야 하고, 무엇을
        # 골랐는지는 계획 어디에도 남지 않는다. companies·years를 생성 시점에 막는 것과 같은
        # 이유로, 두 정본이 공존할 수 있는 상태 자체를 만들지 않는다.
        if self.metric is not None and self.derived_metric is not None:
            raise ValueError(
                "metric과 derived_metric을 함께 담을 수 없습니다: "
                f"metric={self.metric!r}, derived_metric={self.derived_metric!r}. "
                "원천 지표와 파생 개념 중 하나만 정본입니다."
            )

        # MetricConcept은 StrEnum이라 낱개로 넘겨도 문자열로 순회된다. 그대로 두면
        # CONTRACT_AMOUNT 하나가 글자 수만큼의 필드로 늘어난다. companies를 막는 것과 같다.
        if isinstance(self.requested_fields, str):
            raise TypeError(
                "requested_fields는 MetricConcept 하나가 아니라 튜플이어야 합니다: "
                f"{self.requested_fields!r}"
            )

        # requested_fields는 metric·derived_metric과 함께 담기지 않는다.
        #
        # 셋이 공존할 수 있으면 이 계획을 읽는 쪽이 무엇을 답해야 하는지 스스로 골라야 하고,
        # 무엇을 골랐는지는 계획 어디에도 남지 않는다. 위의 metric/derived_metric 규칙과
        # 같은 이유이며, 세 정본이 공존할 수 있는 상태 자체를 만들지 않는다.
        if self.requested_fields and (
            self.metric is not None or self.derived_metric is not None
        ):
            raise ValueError(
                "requested_fields는 metric·derived_metric과 함께 담을 수 없습니다: "
                f"requested_fields={self.requested_fields!r}, metric={self.metric!r}, "
                f"derived_metric={self.derived_metric!r}. "
                "질문이 요구한 값의 정본은 하나여야 합니다."
            )

    def _allows_absent_years(self) -> bool:
        """
        연도를 하나도 담지 않고 만들 수 있는 계획인가.

        거래소공시를 대상으로 하는 두 계획뿐이다. 둘 다 연도가 비교 대상 기간이 아니라
        원공시를 지목하는 공시일 기준점이고, 기준점 없이도 다른 조건이 후보를 좁힌다. 좁혀지지
        않으면 handler가 ambiguous_root로 막으므로 값을 고르는 자리가 생기지 않는다.

            event_history + full_chain
                기업과 공시 유형이 후보를 좁힌다.

            event_summary + latest_valid / original_only
                기업과 질문이 밝힌 이벤트 주제어가 후보를 좁힌다(event_subject_tokens).
                거래소공시 레코드에는 회계연도가 아예 없고(manifest의 base_year가 비어 있다)
                공시일만 있으므로, 연도를 요구하면 사용자가 알 수 없는 값을 요구하게 된다.

        정기공시 요약(Pattern B)은 여기 없다. 그쪽의 회계연도 요건은 그대로다. 기간을 밝히지
        않은 정기공시 요약에서 어떤 문서를 기본값으로 고를지는 여전히 미정이며
        (open_decisions.md D5-a), 이 완화가 그 결정을 대신하지 않는다.

        intent별로 나누는 이유:
            single_fact·temporal_compare·cross_entity_compare에서 연도는 답의 일부다. 그쪽에서
            연도가 비면 어느 해를 답했는지가 결과 어디에도 남지 않는다. 전역으로 느슨하게 하면
            Q07("매출이 작년보다 얼마나 늘었어?")처럼 지금 missing_year로 막히는 질문들이
            연도 없이 흘러 들어간다.

        공시 유형으로 나누지 않는다:
            예전에는 EventSubtypeRule.requires_filing_anchor가 유형마다 기준점을 요구할지
            정했고, 공급계약은 요구하는 쪽이었다. 그 요구를 없앴다. 기준점이 없을 때 답할 수
            있는지는 유형이 아니라 그 기업의 원공시 후보가 몇 건인지가 정하며, 그것은 코퍼스를
            열어야 알 수 있어 이 계층이 판정할 수 없다. 유형으로 미리 가르면 후보가 하나뿐인
            질문까지 막고, 막힌 사유도 실제와 달라진다(EventSubtypeRule 참조).
        """
        if self.intent == INTENT_EVENT_SUMMARY:
            # __post_init__이 이미 이 집합 밖의 정책을 막는다. 여기서 다시 목록을 적지 않고
            # 같은 정본을 읽어, 정책이 늘어날 때 두 곳이 어긋나지 않게 한다.
            return self.chain_policy in EVENT_SUMMARY_CHAIN_POLICIES

        return (
            self.intent == INTENT_EVENT_HISTORY
            and self.chain_policy == ChainPolicy.FULL_CHAIN
        )

    @property
    def year(self) -> int:
        """
        단일 기간 경로를 위한 호환 통로.

        **시계열 비교(Pattern C)에서는 쓰지 않는다. years를 읽어야 한다.**

        정본이 아니다. years에서 파생되는 읽기 전용 값이며, 단일 기간을 전제로 이미
        동작하고 있는 호출부(Pattern A·E)를 위해 남겼다. 값을 따로 저장하지 않으므로
        years와 어긋날 수 없고, 기간 정보를 읽어야 하는 새 코드는 이 통로가 아니라 years를
        본다.

        "기간이 하나면 쓸 수 있는 값"이 아니다:
            개수만으로 판정하지 않는다. intent가 temporal_compare면 years에 절대연도가
            하나뿐이어도 막는다. 그 계획에는 해석하지 않은 상대 기간 표현이 함께 있을 수
            있기 때문이다.

            "2025년 매출이 전년보다 얼마나 늘었어?"가 그 경우다. 질문이 가리키는 기간은
            둘인데 "전년"은 기준 시점 정책이 없어 연도로 옮기지 않았으므로 years에는 2025
            하나만 남는다. 여기서 이 통로가 2025를 돌려주면 두 기간을 물은 질문에 한 기간만
            답하게 되고, 그것이 C-0에서 막은 silent-wrong과 같은 결과다. 개수로만 판정하면
            이 경로가 그대로 열린다.

        company와 같은 자리이지만 한 가지가 다르다. company는 companies[0]을 그냥
        돌려주는 반면, 이 값은 조건을 만족하지 않으면 돌려주지 않고 막는다.

        그 차이의 이유:
            여러 기업 중 하나를 고르는 일은 Router가 routing 단계에서 막으므로 company를
            읽는 handler에 애초에 도달하지 않는다. 기간도 지금은 같지만, Pattern C handler가
            생기는 순간 시계열 비교 계획이 handler 안까지 들어온다. Pattern C는 이 통로가
            아니라 years를 읽어야 하며, 한 줄 실수로 옛 경로에 기대지 않도록 통로 쪽에서
            막는다.

        ValueError를 쓰고 AttributeError를 쓰지 않는 이유:
            AttributeError는 hasattr()와 getattr(plan, "year", 기본값)이 조용히 삼킨다.
            값을 고르지 않으려고 만든 가드가 그렇게 삼켜지면 가드가 없는 것과 같아진다.
            같은 dataclass의 __post_init__이 이미 잘못된 생성을 ValueError로 막고 있으므로
            어휘도 그쪽에 맞춘다.
        """
        if self.intent == INTENT_TEMPORAL_COMPARE:
            raise ValueError(
                f"시계열 비교 계획에서는 year를 읽을 수 없습니다: years={self.years!r}. "
                "절대연도가 하나만 구조화돼 있어도 해석하지 않은 상대 기간 표현이 함께 있을 "
                "수 있어, 이 값 하나로 답하면 질문이 물은 기간 중 일부만 답하게 됩니다. "
                "years를 읽어야 합니다."
            )

        if len(self.years) != 1:
            raise ValueError(
                f"기간이 {len(self.years)}개인 계획에서 year를 읽을 수 없습니다: "
                f"{self.years!r}. 단일 기간 계획에서만 쓰는 통로이며, 다중 기간은 years를 "
                "읽어야 합니다. 첫 연도만 골라 답하지 않습니다."
            )

        return self.years[0]

    @property
    def has_unresolved_relative_period(self) -> bool:
        """
        계획에 해석하지 않은 상대 기간이 남아 있는가.

        이 계획으로 답을 만들어도 되는지를 판정하는 쪽이 읽는다. 참이면 질문이 가리키는
        기간이 계획의 years만으로 결정되지 않으므로, years를 그대로 써서 답하면 질문이 물은
        기간 중 일부만 답하게 된다.

        질문 문자열을 보지 않는다. "작년"은 해석에 성공한 계획에도 그대로 남아 있으므로,
        문자열로 판정하면 옮기기 전과 옮긴 뒤를 가릴 수 없다.
        """
        return self.relative_period is not None and not self.relative_period.resolved

    @property
    def company(self) -> str:
        """
        대표 기업 하나.

        단일 기업 Pattern(A·E)의 기존 호출부를 위해 남긴 읽기 전용 통로다. 값을 따로
        저장하지 않고 companies에서 읽으므로 둘이 어긋날 수 없다.

        기업이 둘 이상인 질문은 Router가 routing 단계에서 막으므로, 이 값을 읽는 handler에는
        애초에 도달하지 않는다.
        """
        return self.companies[0]

    def to_dict(self) -> dict[str, Any]:
        # company와 year는 담지 않는다. companies·years에서 파생되는 값을 함께 실으면 읽는
        # 쪽이 둘 중 무엇이 정본인지 알 수 없게 된다. property는 asdict가 담지 않으므로
        # 따로 지울 것이 없다.
        return asdict(self)


def _extract_years(question: str) -> tuple[int, ...]:
    """
    질문에 쓰인 절대연도를 전부 찾는다. 없으면 빈 튜플이며 추측하지 않는다.

    첫 매치 하나만 돌려주지 않는다. 두 기간을 묻는 질문에서 하나만 고르면 나머지 기간이
    사라진 채로 답이 조립되고, 사라졌다는 사실이 warnings에도 결과에도 남지 않는다.
    _resolve_companies가 기업에 대해 하는 일과 같다.

    같은 연도가 문장에 여러 번 나오는 것은 두 기간이 아니다("2024년 매출과 2024년 영업이익").
    그래서 값으로 중복을 제거한다. 돌아오는 순서는 질문에 나타난 순서다.

    상대기간 표현("작년")은 여기서 읽지 않는다. 해석하려면 기준 시점을 정해야 하고 그것은
    이번 범위 밖이다. 탐지는 _has_relative_period가 따로 맡는다.

    무엇을 연도로 볼지는 agent.query_vocabulary.find_years가 정한다. 여기서 다시 세면 같은
    질문에 두 벌의 규칙이 생기고, 한쪽만 넓어지는 날이 온다. 지금 읽는 표기는 '년'이 붙은
    네 자리 숫자와, 보고서 종류가 바로 뒤에 붙은 네 자리 숫자("2025 사업보고서") 둘이다.
    그 밖의 네 자리 숫자("2025억원", "계약번호 2025-0001")는 연도로 보지 않는다.
    """
    return find_years(question)


def _match_previous_year_expression(question: str) -> str | None:
    """
    질문에 쓰인 과거 상대 기간 표현을 돌려준다. 없으면 None.

    있다는 사실만이 아니라 무엇에 걸렸는지를 돌려준다. 해석 결과를 사람이 되짚으려면
    "'작년'을 2024로 옮겼다"까지 남아야 하고, 참/거짓만으로는 그 문장을 만들 수 없다.

    지원하지 않는 표현은 여기서 보지 않는다. 그 판정은 _reject_unsupported_relative_period가
    질문 해석 앞에서 이미 끝내며, 순서가 규약이다. 여기서 함께 보면 "지원하지 않아서 걸리지
    않은 것"과 "아예 없어서 걸리지 않은 것"이 같은 None이 된다.
    """
    match = _PREVIOUS_YEAR_PATTERN.search(question)

    return match.group(0) if match else None


def _has_relative_period(question: str) -> bool:
    """
    해석 대상 상대 기간 표현이 있는가.

    판정 규칙은 _match_previous_year_expression 하나가 갖는다. 여기서 다시 적으면 같은
    질문에 두 벌의 어휘가 생기고, 한쪽만 넓어지는 날이 온다.
    """
    return _match_previous_year_expression(question) is not None


def _reject_unsupported_relative_period(question: str) -> None:
    """
    상대 기간이긴 하나 이번 범위가 해석하지 않는 표현을 거절한다.

    의도 분기보다 앞의 공통 경로에서 실행한다. _reject_out_of_scope_period와 같은 자리이며
    이유도 같다. 의도별 builder 안에서 부르면 Pattern을 추가할 때마다 같은 검사를 다시 붙여야
    하고, 한 번 빠뜨린 Pattern만 조용히 검사를 건너뛴다.

    거절하지 않고 넘기면 어떻게 되는가:
        "재작년보다 얼마나 늘었어?"는 지원 패턴에 걸리지 않으므로 상대 기간이 없는 질문이 되고,
        절대연도도 없으니 missing_year로 막힌다. 막히기는 하지만 "연도를 못 읽었다"는 사유가
        실제로 일어난 일과 다르다. 질문에는 기간이 분명히 적혀 있고, 이 구현이 그 표현을
        지원하지 않을 뿐이다. 무엇을 구현하면 풀리는 질문인지가 결과에 남아야 한다.
    """
    match = _UNSUPPORTED_RELATIVE_PERIOD_PATTERN.search(question)

    if match is None:
        return

    supported = ", ".join(f"'{item}'" for item in PREVIOUS_YEAR_EXPRESSIONS)

    raise UnsupportedQuestionError(
        reason="unsupported_relative_period",
        detail=(
            f"상대 기간 표현 '{match.group(0)}'에 대한 해석은 이번 범위에 포함되지 않습니다. "
            f"지원하는 표현: {supported}."
        ),
    )


def _resolve_anchor_year(
    company: str,
    fiscal_year_anchors: Mapping[str, int] | None,
) -> int:
    """
    기업 한 곳의 상대 기간 기준 연도를 읽는다. 읽지 못하면 거절한다.

    이 함수는 코퍼스를 열지 않는다. 호출자가 manifest에서 유도해 넘긴 대응표만 읽는다.
    query_analyzer가 known_companies를 인자로 받는 것과 같은 자리이며, 질의 해석 계층이
    검색 계층을 직접 부르지 않게 하려는 것이다(tests/unit/test_layering.py).

    없을 때 오늘 날짜로 대신하지 않는다. 그렇게 하면 같은 질문이 실행한 날에 따라 다른 해를
    가리키고, 평가에서 어제 맞던 답이 오늘 틀린다. 값이 없으면 막는다.

    앞 연도가 대응표의 어느 범위에 있는지도 보지 않는다. anchor - 1의 사업보고서가 코퍼스에
    없더라도 여기서 연도를 바꾸지 않으며, 그 판정은 문서를 실제로 찾아 본 뒤 filing_discovery가
    한다. 여기서 앞당기면 질문이 물은 기간과 다른 기간을 답하게 된다.
    """
    if not fiscal_year_anchors:
        raise UnsupportedQuestionError(
            reason="missing_fiscal_year_anchor",
            detail=(
                "상대 기간을 옮길 기준 연도를 알 수 없습니다. 기업별 최신 사업보고서 회계연도 "
                "대응표가 주어지지 않았습니다. manifest에서 읽어 전달해야 합니다."
            ),
        )

    anchor = fiscal_year_anchors.get(normalize_company_name(company))

    if anchor is None:
        raise UnsupportedQuestionError(
            reason="missing_fiscal_year_anchor",
            detail=(
                f"'{company}'의 사업보고서를 코퍼스에서 찾을 수 없어 상대 기간을 옮길 기준 "
                "연도를 정할 수 없습니다."
            ),
        )

    return anchor


def _require_years(years: tuple[int, ...]) -> tuple[int, ...]:
    """
    연도를 하나도 읽지 못한 질문을 거절한다.

    이미 뽑아 둔 값을 검사만 하고 다시 뽑지 않는다. 호출부마다 _extract_years를 또 부르면
    같은 질문에서 두 번 해석하게 되고, 나중에 해석 규칙이 바뀔 때 한쪽만 고쳐질 수 있다.

    거절 사유를 UnsupportedQuestionError로 두는 것은 기존 동작이다. Router가 이 예외를
    query_plan 단계의 BLOCKED로 옮긴다.
    """
    if not years:
        raise UnsupportedQuestionError(
            reason="missing_year",
            detail="질문에서 대상 연도를 찾을 수 없습니다.",
        )

    return years


def _resolve_companies(question: str, resolver: CompanyResolver) -> CompanyResolution:
    """
    질문 안에서 기업 표현을 전부 찾아 canonical 기업으로 옮긴다.

    회사명을 추측하지 않는다. 실제 규칙은 agent.company_resolution이 가지고 있고, 여기서는
    그 결과를 질의 해석 계층의 거절 어휘로 옮기기만 한다.

    막는 자리가 셋이며 순서가 규약이다.

        모호한 표현이 하나라도 있으면    먼저 막는다. '삼성이랑 현대차 비교해줘'에서 현대차만
                                        남겨 실행하면, 질문이 지목한 기업 하나가 사라진 채로
                                        답이 조립되고 사라졌다는 사실이 결과에 남지 않는다.
                                        일부만 실행하지 않는다.

        확정되지 않은 표현이 남으면      해석기가 기업 언급으로 읽었지만 옮기지 않은 표현이다.
                                        '레인이랑 현대차 2025년 매출 비교해줘'의 '레인'이 그
                                        자리이며, 후보가 하나뿐이어도 그 근거로는 확정하지
                                        않는다는 것이 해석기의 규약이다. 여기서 통과시키면
                                        현대자동차 단독 계획이 만들어져, 확정하지 않기로 한
                                        표현을 '없는 것'으로 처리한 셈이 된다.

        하나도 못 찾았으면              기존과 같은 unknown_company다. 모호성 판정이 먼저이므로
                                        '삼성'처럼 후보가 여럿인 표현이 '모르는 기업'으로
                                        잘못 보고되지 않는다.

    판정 근거는 전부 CompanyResolution의 구조화된 결과다. 질문 문자열을 여기서 다시 들여다보지
    않는다. 그렇게 하면 기업 인식 규칙이 해석기와 이 함수 두 곳에 생기고, 한쪽만 고쳐질 수 있다.

    확정되지 않은 표현의 사유를 unknown_company로 두는 이유:
        '레인'은 후보가 여럿이어서 못 고른 것이 아니다. 이 질문에서 어느 기업인지 정해지지
        않았다는 뜻이며, 그것은 ambiguous_company_match가 아니라 unknown_company 쪽이다.
        AMBIGUOUS_COMPANY_REASON으로 보고하면 후보 하나짜리 표현이 '후보가 여럿'으로 보고되고,
        사유를 읽는 쪽이 그 하나를 답으로 읽는 길이 열린다. 대신 detail에 어느 표현이
        걸렸는지를 적어, 기업을 하나도 못 찾은 경우와 결과에서 구분되게 둔다.

    돌아오는 순서는 질문에 나타난 순서다.
    """
    resolution = resolver.resolve_question(question)

    if resolution.ambiguous:
        mention = resolution.ambiguous[0]

        raise UnsupportedQuestionError(
            reason=AMBIGUOUS_COMPANY_REASON,
            detail=(
                f"'{mention.raw}'이(가) 가리키는 기업을 하나로 확정할 수 없습니다. "
                f"후보: {', '.join(mention.candidates)}."
            ),
        )

    if resolution.unresolved:
        mention = resolution.unresolved[0]

        raise UnsupportedQuestionError(
            reason="unknown_company",
            detail=f"'{mention.raw}'이(가) 가리키는 기업을 확정할 수 없습니다.",
        )

    if not resolution.companies:
        raise UnsupportedQuestionError(
            reason="unknown_company",
            detail="질문에서 알려진 기업명을 찾을 수 없습니다.",
        )

    return resolution


def _extract_accounting_scope(question: str) -> tuple[str, bool]:
    lowered = question.casefold()

    if any(token in lowered for token in CONSOLIDATED_TOKENS):
        return SCOPE_CONSOLIDATED, True

    if any(token in lowered for token in SEPARATE_TOKENS):
        return SCOPE_SEPARATE, True

    # 명시가 없으면 연결 기준을 기본값으로 쓰되 명시 여부를 남긴다.
    return SCOPE_CONSOLIDATED, False


def _hangul_run(question: str, start: int, end: int) -> str:
    """[start, end)를 품은 한글 음절 덩어리를 통째로 돌려준다. 진단 문자열용이다."""
    left = start
    while left > 0 and _HANGUL_SYLLABLE_PATTERN.match(question[left - 1]):
        left -= 1

    right = end
    while right < len(question) and _HANGUL_SYLLABLE_PATTERN.match(question[right]):
        right += 1

    return question[left:right]


def _revenue_token_spans(question: str) -> tuple[tuple[int, int], ...]:
    """
    경계로 읽힌 지표 어휘 구간.

    어휘와 회계기준 어휘는 호출 시점 값을 쓴다. 이 함수를 거치지 않고 패턴을 따로 만들면
    '무엇을 읽었는가'의 정의가 두 벌이 되고, 한쪽만 고친 날 두 판정이 갈린다.
    """
    pattern = _metric_token_pattern(tuple(REVENUE_TOKENS), _accounting_scope_prefixes())

    return tuple(match.span() for match in pattern.finditer(question))


def _find_unsupported_revenue_occurrence(
    question: str,
    spans: tuple[tuple[int, int], ...],
) -> str | None:
    """
    지표 어휘를 품고 있으나 경계로 읽히지 않은 표기를 찾는다. 없으면 None.

    spans는 _metric_token_pattern이 실제로 읽어 낸 구간이다. 어휘가 문자열로는 있는데
    어느 구간에도 들어가지 않으면, 그 자리는 지표 어휘를 품은 다른 개념이다
    ("매출원가"의 "매출", "매출채권"의 "매출", "금융수익"의 "수익").

    어휘 목록에서 신호를 만드는 이유:
        막을 표기를 따로 나열하지 않는다. 나열하면 목록에 없는 복합 개념이 그대로 통과하고,
        REVENUE_TOKENS가 늘 때 이 목록도 함께 늘려야 한다는 사실이 어디에도 남지 않는다.
        경계 판정이 이미 '읽은 자리'를 알고 있으므로, 읽지 못한 자리를 그 여집합으로 구한다.

    돌려주는 값은 그 자리를 품은 한글 덩어리다. 진단에만 쓰며 판정에는 쓰지 않는다.
    """
    for token in REVENUE_TOKENS:
        start = question.find(token)

        while start != -1:
            end = start + len(token)

            if not any(span_start <= start and end <= span_end for span_start, span_end in spans):
                return _hangul_run(question, start, end)

            start = question.find(token, start + 1)

    return None


def _find_metric(question: str) -> str | None:
    """
    질문에서 지표를 읽는다. 없으면 None이며 추측하지 않는다.

    낱말 경계까지 맞춰 찾는다. 담고만 있는지 보면 "매출원가"·"매출채권"·"매출총이익"·
    "금융수익"처럼 이번 범위가 답할 수 없는 개념이 전부 매출로 분류되고, 그렇게 나온
    계획은 엉뚱한 질문에 매출 Fact를 근거까지 갖춰 돌려준다. 경계 규약은
    _metric_token_pattern에 적었다.

    지원하는 표기와 지원하지 않는 표기가 함께 있으면 고르지 않는다:
        "매출원가와 매출 알려줘"에는 읽히는 어휘("매출")가 있다. 그것만 담아 계획을 만들면
        매출원가를 물었다는 사실이 계획에도 결과에도 남지 않은 채 매출 답이 나간다. 질문의
        일부를 조용히 버리는 것이며, _resolve_requested_metrics가 원천 지표와 파생지표를
        함께 물은 질문에서 한쪽을 고르지 않는 것과 같은 이유다.

        그래서 둘 다 있으면 거절한다. 사유는 새로 만들지 않고 기존 unsupported_metric을
        그대로 쓴다.

    읽히는 어휘가 하나도 없으면 거절하지 않고 None이다:
        "매출원가가 얼마야?"는 지표를 읽지 못한 질문이며, 그 상태를 어떻게 다룰지는 이미
        호출부가 정해 두었다(단일 사실 조회는 _extract_metric이 거절하고, 비교 질의는
        지표를 비운 계획을 만들어 Router가 막는다). 여기서 앞질러 예외로 바꾸면 그 분업이
        무너진다.

    어휘는 호출 시점에 읽어 넘긴다. 모듈 수준에서 패턴을 붙들고 있으면 REVENUE_TOKENS를
    바꿔도 판정이 따라오지 않는다.
    """
    spans = _revenue_token_spans(question)

    if not spans:
        return None

    unsupported = _find_unsupported_revenue_occurrence(question, spans)

    if unsupported is not None:
        raise UnsupportedQuestionError(
            reason="unsupported_metric",
            detail=(
                f"질문이 지원하는 지표(매출액)와 지원하지 않는 표기('{unsupported}')를 함께 "
                "묻고 있습니다. 한쪽만 골라 답하지 않습니다."
            ),
        )

    return METRIC_REVENUE


def _find_derived_metric(question: str) -> tuple[DerivedMetric, str] | None:
    """
    질문에서 파생 개념을 읽는다. 없으면 None이며 추측하지 않는다.

    찾은 표기를 함께 돌려준다. 원천 지표를 읽기 전에 그 표기를 질문에서 걷어내야 하기
    때문이다(_resolve_requested_metrics 참조).
    """
    for token, metric in DERIVED_METRIC_TOKENS:
        if token in question:
            return metric, token

    return None


def _resolve_requested_metrics(question: str) -> tuple[str | None, DerivedMetric | None]:
    """
    질문이 요청한 지표를 읽는다. 원천 지표와 파생 개념을 각각 돌려준다.

    파생 개념을 먼저 본다:
        원천 지표 표기가 파생지표 표기 안에 들어 있을 수 있다. "영업이익률"이 "영업이익"을
        품고 있는 것이 그 경우다. 원천 지표를 먼저 읽으면 영업이익률을 물은 질문이
        영업이익으로 분류되고, 그렇게 나온 답은 형식이 맞아 틀렸다는 사실이 드러나지 않는다.

        그래서 파생 개념을 먼저 찾고, 찾았으면 그 표기를 걷어낸 나머지에서 원천 지표를
        읽는다. 순서만 정하면 두 판정이 같은 글자를 각각 세므로 표기를 소비한다. 이번 범위의
        원천 어휘에는 "영업이익"이 없지만, 나중에 그것을 추가하는 작업이 이 판정을 조용히
        뒤집지 못하도록 지금 구조로 막아 둔다.

    둘 다 읽히면 고르지 않는다:
        "매출이랑 영업이익률"처럼 서로 다른 두 지표를 함께 물으면 한쪽만으로는 답이 되지
        않는다. 조용히 하나를 고르면 나머지를 물었다는 사실이 결과 어디에도 남지 않는다.

    걷어낸 나머지도 같은 잣대로 본다:
        나머지에 지원하지 않는 지표 표기만 남는 경우가 있다. "매출원가랑 영업이익률 어떻게
        달라졌어?"가 그것이다. _find_metric은 읽히는 어휘가 없으면 None을 돌려주므로(그것이
        그 함수의 계약이다) 여기서 더 보지 않으면 원천 지표 쪽 요청이 사라진 채 파생지표만
        담긴 계획이 만들어진다. 그 계획은 막히지 않고 실행돼 영업이익률만 답한다.

        물어본 것은 둘인데 답은 하나이고, 나머지 하나가 빠졌다는 사실은 계획에도 결과에도
        남지 않는다. 원천 지표와 파생지표를 함께 물었을 때 한쪽을 고르지 않는 것과 같은
        상황이므로 같은 사유로 거절한다.

        판정은 _find_unsupported_revenue_occurrence 하나가 한다. 막을 표기를 여기에 따로
        나열하지 않는다. 나열하면 목록에 없는 복합 개념이 그대로 통과하고, '지원하지 않는
        지표 표기'의 정의가 두 벌로 갈린다.
    """
    found = _find_derived_metric(question)

    if found is None:
        return _find_metric(question), None

    derived, token = found
    remainder = question.replace(token, " ")

    unsupported = _find_unsupported_revenue_occurrence(
        remainder,
        _revenue_token_spans(remainder),
    )

    if unsupported is not None:
        raise UnsupportedQuestionError(
            reason="unsupported_metric",
            detail=(
                f"질문이 파생지표({derived.value})와 지원하지 않는 표기"
                f"('{unsupported}')를 함께 묻고 있습니다. 한쪽만 골라 답하지 않습니다."
            ),
        )

    source = _find_metric(remainder)

    if source is not None:
        raise UnsupportedQuestionError(
            reason="unsupported_metric",
            detail=(
                f"질문이 원천 지표({source})와 파생지표({derived.value})를 함께 묻고 "
                "있습니다. 한쪽만 골라 답하지 않습니다."
            ),
        )

    return None, derived


def _extract_metric(question: str) -> str:
    metric = _find_metric(question)

    if metric is None:
        raise UnsupportedQuestionError(
            reason="unsupported_metric",
            detail="이번 범위에서 지원하는 지표(매출액)를 질문에서 찾을 수 없습니다.",
        )

    return metric


def _extract_month(question: str) -> int | None:
    """질문이 월을 지정했으면 읽는다. 없으면 None이며 추측하지 않는다."""
    match = MONTH_PATTERN.search(question)

    if not match:
        return None

    return int(match.group(1))


def _find_event_subtype_rules(question: str) -> tuple[EventSubtypeRule, ...]:
    """
    질문에 맞는 거래소공시 세부유형 규칙을 전부 찾는다. 없으면 빈 튜플.

    첫 매치에서 멈추지 않는다. 멈추면 두 유형을 함께 가리킨 질문에서 뒤의 유형이 사라진
    채로 계획이 만들어지고, 사라졌다는 사실이 어디에도 남지 않는다. _resolve_companies가
    기업에 대해, _extract_years가 기간에 대해 하는 일과 같다.

    돌아오는 순서는 EVENT_SUBTYPE_RULES의 선언 순서다. 우선순위가 아니라 진단 문자열의
    나열 차례를 고정하기 위한 것이다.
    """
    return tuple(rule for rule in EVENT_SUBTYPE_RULES if rule.matches(question))


def _extract_event_subtype_rule(question: str) -> EventSubtypeRule:
    """
    질문에서 거래소공시 세부유형 하나를 확정한다.

    확정하지 못하는 두 경우를 서로 다른 사유로 거절한다. 후속 작업이 다르기 때문이다.

        0건  unsupported_event_subtype
             이번 범위가 다루지 않는 유형이다. 어휘를 넓히면 풀린다.

        2건+ ambiguous_event_subtype
             질문이 두 유형을 함께 가리킨다. 어휘를 넓혀도 풀리지 않는다. 어느 쪽을 묻는지
             되묻거나, 두 유형의 이력을 함께 답하는 경로를 만들어야 풀린다.

    2건 이상에서 하나를 고르지 않는 이유:
        고르면 나머지 유형을 물었다는 사실이 계획에도 결과에도 남지 않는다. 그렇게 만든 답은
        형식이 맞고 근거까지 갖췄지만 질문의 절반에만 답한 것이며, 받는 쪽이 그것을 알 방법이
        없다. QueryPlan이 companies와 years를 하나로 줄이지 않는 것과 같은 이유다.

        선언 순서로 고르는 것은 근거가 아니다. 순서는 이 파일을 쓴 사람이 정한 것이지 질문이
        정한 것이 아니다.

    세부유형 문자열이 아니라 규칙을 돌려주는 이유는, 호출부가 유형 이름뿐 아니라 그 유형을
    무엇으로 알아봤는지까지 함께 다룰 수 있어야 하기 때문이다. 두 값을 각각 다시 조회하면
    같은 질문을 두 번 해석하게 되고, 어휘가 바뀔 때 한쪽만 고쳐질 수 있다.
    """
    matched = _find_event_subtype_rules(question)

    if len(matched) == 1:
        return matched[0]

    if not matched:
        raise UnsupportedQuestionError(
            reason="unsupported_event_subtype",
            detail=(
                "이번 범위에서 지원하는 거래소공시 유형(공급계약, 신규시설투자)을 질문에서 "
                "찾을 수 없습니다."
            ),
        )

    # 후보를 detail에 남긴다. 무엇과 무엇 사이에서 막혔는지가 없으면, 질문을 어떻게 고쳐야
    # 답할 수 있는지 알 수 없다. 나열 차례는 선언 순서라 같은 질문이면 늘 같은 문자열이다.
    listed = ", ".join(rule.subtype for rule in matched)

    raise UnsupportedQuestionError(
        reason="ambiguous_event_subtype",
        detail=(
            f"질문이 거래소공시 유형 {len(matched)}종({listed})을 함께 가리키고 있어 "
            "어느 유형의 이력인지 특정할 수 없습니다. 한 유형을 골라 답하지 않습니다."
        ),
    )


def _looks_like_event_history(question: str) -> bool:
    """
    변경 이력을 묻는 거래소공시 질문인가.

    유형 표현과 이력 표현이 함께 있어야 한다. 둘 중 하나만으로 판정하면 '공급계약 금액이
    얼마야'(단일 사실)까지 이력 조회로 끌려간다.

    Q12("...새로 발표한 시설투자, 회사별로 다 합치면...")는 유형 표현은
    있지만 이력 표현이 없다. 다중 기업 판정이 먼저 실행되어 Pattern D의
    집계 계획을 만들고, 이 함수는 Pattern E로 보내지 않는다.

    유형이 둘 이상 맞는 질문도 여기서는 통과시킨다. 막는 자리는 계획을 만드는
    _extract_event_subtype_rule이며, 그래야 ambiguous_event_subtype으로 막힌다. 여기서
    걸러 내면 그 질문이 단일 사실 경로로 흘러 '지표를 모르겠다'로 막히고, 실제 원인인
    유형 모호성이 결과에서 사라진다.
    """
    return bool(_find_event_subtype_rules(question)) and any(
        token in question for token in HISTORY_TOKENS
    )


def _looks_like_facility_investment_aggregate(question: str) -> bool:
    """Q12의 시설투자 집계 비교 의미가 모두 있는가.

    시설투자 유형, 집계, 비교 세 신호를 모두 요구한다. 어느 하나라도 비면
    metric이 없는 다른 다중 기업 질문이 Q12 실행 경로로 들어가지 않는다.
    여러 거래소공시 유형이 동시에 맞으면 한 유형을 고르지 않는다.
    """
    matched = _find_event_subtype_rules(question)

    return (
        len(matched) == 1
        and matched[0].subtype == EVENT_SUBTYPE_FACILITY_INVESTMENT
        and normalize_aggregate_request(question) == OPERATION_AGGREGATE
        and any(token in question for token in COMPARISON_REQUEST_TOKENS)
    )


def _business_profile_residual(
    question: str,
    companies: tuple[str, ...],
    subject_names: tuple[str, ...] = (),
) -> str:
    """기업 소개 요약이 의미를 가져가고 남은 말을 돌려준다. 빈 문자열이면 남은 요청이 없다.

    걷어내는 것은 넷이다.

        제품 표현      이 경로가 답하는 요청 그 자체(find_product_phrase_span)
        사업 서술 표기 같은 요청의 나머지 반(BUSINESS_DESCRIPTION_TOKENS)
        기업명         무엇을 묻는지가 아니라 누구에게 묻는지다
        기간           같은 이유다. 어느 문서를 볼지를 정할 뿐 요청을 늘리지 않는다

    그러고 남은 것에서 기능 형태소와 구두점을 지운다(BUSINESS_PROFILE_SCAFFOLDING). 그래도
    남는 글자가 있으면 이 경로가 답하지 않을 요청이 함께 온 것이다.

    자리 계산에 대해:
        공백을 걷어낸 문자열 하나를 두고 전부 처리한다. find_product_phrase_span이 돌려주는
        자리도 같은 기준이다. 원문과 걷어낸 문자열을 섞어 쓰면 자리가 어긋난다.

    canonical과 subject_names를 함께 지우는 이유:
        계획에 담기는 것은 canonical 하나지만, 질문에 적히는 것은 사용자가 쓴 표기다.
        "현대차는 무슨 사업을 하고 대표 제품이 뭐야?"에서 canonical은 '현대자동차'이고
        질문에는 '현대차'가 적혀 있다. canonical만 지우면 '현대차'가 남고, 남은 요청이
        있다고 세어져 정상 Q05가 막힌다. 별칭('삼바')도 같다.

        그 표기를 여기서 다시 찾지 않는다. 무엇이 기업을 가리켰는지는 이미
        CompanyResolution이 판정했고 surfaces에 담겨 있다(호출부가 subject_names로 넘긴다).
        여기서 별칭 규칙을 다시 두면 해석기와 두 벌이 되고, 한쪽만 늘어난 날 같은 질문이
        경로에 따라 갈린다. 이 모듈의 다른 자리(_names_the_subject·주제어 추출)가 같은
        값을 같은 이유로 받는다.

    긴 표기부터 지우는 이유:
        짧은 표기가 먼저 지워지면 긴 표기의 일부만 사라진다('기아'를 먼저 지우면
        '기아자동차'가 '자동차'로 남는다).
    """
    compacted = "".join(question.split())

    span = find_product_phrase_span(question)

    if span is not None:
        compacted = compacted[: span[0]] + compacted[span[1] :]

    for token in BUSINESS_DESCRIPTION_TOKENS:
        compacted = compacted.replace(token, "")

    # canonical 표기, 그 정규화 표기, 그리고 질문에 실제로 적힌 표기.
    subject_wording = {
        name
        for name in (
            *companies,
            *(normalize_company_name(company) for company in companies),
            *subject_names,
        )
        if name
    }

    for name in sorted(subject_wording, key=len, reverse=True):
        compacted = compacted.replace(name, "")

    # 절대연도의 숫자. 뒤에서부터 지워야 앞 구간의 자리가 어긋나지 않는다.
    for start, end, _ in reversed(find_year_spans(compacted)):
        compacted = compacted[:start] + compacted[end:]

    return _BUSINESS_PROFILE_SCAFFOLDING_PATTERN.sub("", compacted)


def _looks_like_business_profile(
    question: str,
    companies: tuple[str, ...] = (),
    subject_names: tuple[str, ...] = (),
) -> bool:
    """
    기업이 무슨 사업을 하고 주력 제품이 무엇인지 함께 묻는 질문인가(Q05).

    두 묶음(사업 서술 표현, 제품 표현)이 모두 있어야 참이다. 한쪽만으로 판정하지 않는
    이유는 BUSINESS_DESCRIPTION_TOKENS의 설명과 같다.

    값 조회 표기가 함께 있으면 거짓이다. 두 묶음이 있어도 값을 함께 묻는 질문은 순수한
    요약 요청이 아니며, 요약으로 받으면 물은 값이 조용히 빠진 답이 나간다
    (BUSINESS_PROFILE_EXCLUDED_TOKENS의 설명). 그런 질문은 이 경로가 생기기 전과 같은
    경로로 흘러간다.

    두 묶음이 있어도 **남은 요청이 있으면 거짓이다.** 값 조회 표기 검사는 이 경로가 아는
    지표 어휘만 보므로, 그 어휘에 없는 속성이 함께 오면 걸리지 않는다("대표 제품이 뭐고
    출시일도 알려줘"). 어휘를 늘리는 것으로는 끝나지 않으므로, 무엇이 남았는지를 본다
    (_business_profile_residual).

    기업명을 받는 이유:
        남은 말을 세려면 기업명이 남은 요청이 아니라는 것을 알아야 한다. 이 모듈은 기업명을
        추측하지 않으므로 호출부가 확정한 결과를 그대로 받는다. 기본값이 빈 튜플인 것은
        기업명을 찾지 못한 질문도 판정 자체는 할 수 있어야 하기 때문이며, 그 경우 기업명이
        남은 요청으로 세어져 경로가 열리지 않는다. 기업을 확정하지 못한 질문은 어차피 뒤에서
        막힌다.

    둘을 함께 받는 이유:
        companies는 canonical identity이고 subject_names는 질문에 실제로 적힌 표기다
        (CompanyResolution.surfaces). 계획에 담기는 것은 앞엣것뿐이지만, 질문에서 걷어내야
        하는 것은 뒤엣것이다. '현대차'로 물으면 canonical은 '현대자동차'이므로 canonical만
        지워서는 질문에 적힌 '현대차'가 남는다.

        CompanyResolution을 통째로 받지 않는다. 이 판정에 필요한 것은 두 목록뿐이고, 이
        모듈의 다른 자리(_looks_like_event_summary·_extract_counterparty)가 이미 같은 두
        값을 같은 이름으로 받는다. 같은 것을 같은 모양으로 받는 쪽이 읽기 쉽다.

    판정 순서에 대해:
        이력 판정보다 뒤, 일반 요약 판정보다 앞이다. 일반 요약 판정은 요약 요청 표현과
        보고서 종류를 요구하므로 이 질문을 알아보지 못하고, 그대로 두면 단일 사실 조회
        경로로 내려가 missing_year로 막힌다. 그 사유는 실제로 일어난 일(기업 소개를 물었다)과
        다르다.
    """
    compacted = "".join(question.split())

    if any(token in compacted for token in BUSINESS_PROFILE_EXCLUDED_TOKENS):
        return False

    if not any(token in compacted for token in BUSINESS_DESCRIPTION_TOKENS):
        return False

    if not matches_product_phrase(question):
        return False

    return _business_profile_residual(question, companies, subject_names) == ""


def _build_business_profile_plan(
    question: str,
    companies: tuple[str, ...],
    fiscal_year_anchors: Mapping[str, int] | None,
) -> QueryPlan:
    """
    Q05 계열 기업 소개 요약 계획을 만든다.

        "<기업>는 무슨 사업을 하고 주력 제품이 뭐야?"
        -> 정기공시 사업보고서 1건 + section_scopes=(business_overview, products)

    대상 기간:
        절대연도가 하나 있으면 그 해다. 없으면 코퍼스에 있는 그 기업의 최신 사업보고서
        회계연도(fiscal_year_anchors)로 옮긴다. 이 질문 모양은 기업의 현재 모습을 묻고,
        그 답은 코퍼스 기준 가장 최근 사업보고서에 있다. 실행 시각을 쓰지 않는 이유는
        _resolve_anchor_year와 같다.

        이 기본값은 이 질문 모양에만 적용한다. 일반 요약(_build_document_summary_plan)의
        기간 규칙은 그대로이며, 기간 없는 일반 요약은 여전히 missing_year로 막힌다. 기간
        미지정 질문 전반의 기본 문서 정책(open_decisions.md D5-a)을 여기서 정하지 않는다.

        상대 기간("작년")은 옮기지 않는다. 일반 요약과 같은 이유다. 절대연도가 둘이면 문서
        1건으로 좁혀지지 않으므로 막는다.

    metric을 담지 않는다:
        _build_document_summary_plan과 같은 이유다.
    """
    relative_expression = _match_previous_year_expression(question)

    if relative_expression is not None:
        raise UnsupportedQuestionError(
            reason="unsupported_summary_period",
            detail=(
                f"요약 질문의 상대 기간 표현('{relative_expression}')은 해석하지 않습니다. "
                "연도를 밝히거나 비워 두십시오. 비워 두면 코퍼스에 있는 최신 사업보고서를 "
                "기준으로 답합니다."
            ),
        )

    years = _extract_years(question)

    if len(years) > 1:
        listed = ", ".join(str(year) for year in years)

        raise UnsupportedQuestionError(
            reason="unsupported_summary_period",
            detail=(
                f"질문이 {len(years)}개 기간({listed})을 함께 가리키고 있습니다. "
                "기업 소개 요약은 사업보고서 1건을 대상으로 합니다. 한 기간을 골라 답하지 "
                "않습니다."
            ),
        )

    warnings: tuple[str, ...] = ()

    if not years:
        anchor_year = _resolve_anchor_year(companies[0], fiscal_year_anchors)

        years = (anchor_year,)
        warnings = (
            f"질문에 회계연도가 없어 코퍼스에 있는 '{companies[0]}'의 최신 사업보고서 "
            f"회계연도 FY{anchor_year}로 해석했습니다. 실행 시각이나 현재 연도를 쓰지 "
            "않았습니다.",
        )

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        years=years,
        intent=INTENT_DOCUMENT_SUMMARY,
        document_class=DOCUMENT_CLASS_PERIODIC,
        document_subtype=DOCUMENT_SUBTYPE_ANNUAL,
        accounting_scope=QueryAccountingScope.NOT_APPLICABLE.value,
        metric=None,
        section_scopes=BUSINESS_PROFILE_SECTION_SCOPES,
        warnings=warnings,
    )


def _looks_like_document_summary(question: str) -> bool:
    """
    문서 하나의 지정 범위를 정리해 달라는 질문인가.

    셋을 모두 만족해야 참이다.

        1. 요약 요청 표현      "정리해줘" / "요약해줘"
        2. 정기공시 보고서 표기 "사업보고서"
        3. 알아볼 수 있는 범위  section_scope로 옮겨지는 표현

    요약 표현 하나만으로 판정하지 않는 이유:
        "정리해줘"·"요약해줘"는 값 조회 질문에도 붙는다. "매출액 정리해줘"가 그 경우이며,
        표현 하나로 이 경로를 열면 Pattern A가 이미 답하던 질문이 요약 경로로 끌려온다.
        그 질문은 요약 대상 문서도 범위도 없으므로 결국 막히는데, 막힌 사유가 '요약 대상을
        확정할 수 없다'가 되어 실제로 일어난 일(답할 수 있었던 값 조회를 가로챘다)과 달라진다.
        같은 구멍이 Pattern C에도 있다("매출 비교해서 정리해줘").

        그래서 이 경로가 실제로 실행할 수 있는 조건을 전부 여기서 본다. 셋 중 하나라도 없으면
        요약 계획을 만들지 않고 나머지 판정으로 넘긴다. 넘겨받은 쪽이 답할 수 있으면 답하고,
        답할 수 없으면 그쪽의 사유로 막힌다. 어느 쪽이든 사유가 실제와 맞는다.

    막는 자리를 옮기지는 않는다:
        _build_document_summary_plan의 확인은 그대로 둔다. 그 builder는 계획을 직접 만드는
        호출자도 쓰므로, 여기서 걸렀다는 이유로 그쪽 가드를 지우면 검사 없는 입구가 생긴다.

    판정 순서에 대해:
        이 판정은 이력 판정보다 뒤다. "시설투자 변경 내용 정리해줘"처럼 두 표현이 함께 있는
        질문은 Pattern E가 답하던 것이고, 앞에 두면 그 질문을 가로챈다.
    """
    if not any(token in question for token in SUMMARY_REQUEST_TOKENS):
        return False

    if not any(token in question for token in PERIODIC_REPORT_TOKENS):
        return False

    return _extract_section_scope(question) is not None


def _extract_section_scope(question: str) -> SectionScope | None:
    """
    질문이 가리키는 문서 내 범위를 읽는다. 없으면 None이며 추측하지 않는다.

    공백을 걷어내고 본다. "설비 투자"와 "설비투자"는 같은 범위이고, 표기를 두 벌 적어 두면
    한쪽을 빠뜨렸을 때 조용히 막힌다.

    둘 이상 맞으면 고르지 않고 막는다. 한 질문이 두 범위를 함께 가리키면 하나를 골라 답한
    결과에서 나머지 범위를 물었다는 사실이 사라진다. 기업·기간·공시 유형에 대해 이미 같은
    규칙을 쓰고 있다.
    """
    compact = "".join(question.split())

    matched = [
        scope
        for scope, tokens in SECTION_SCOPE_TOKENS
        if any(token in compact for token in tokens)
    ]

    if not matched:
        return None

    if len(matched) > 1:
        listed = ", ".join(scope.value for scope in matched)

        raise UnsupportedQuestionError(
            reason="ambiguous_section_scope",
            detail=(
                f"질문이 문서 내 범위 {len(matched)}종({listed})을 함께 가리키고 있어 "
                "어느 범위를 요약할지 특정할 수 없습니다. 한 범위를 골라 답하지 않습니다."
            ),
        )

    return matched[0]


def _build_document_summary_plan(question: str, companies: tuple[str, ...]) -> QueryPlan:
    """
    Q04 계열 단일 문서 요약 계획을 만든다.

        "<기업> <연도>년 사업보고서에서 설비 투자 관련 내용 정리해줘."
        -> 정기공시 사업보고서 1건 + section_scope=facility_investment

    이 builder가 세 가지를 요구하고, 셋 중 하나라도 없으면 계획을 만들지 않는다.

    대상 문서 종류:
        정기공시 보고서 표기("사업보고서")를 요구한다. 표기가 없으면 요약 대상을 확정할 수
        없다. 거래소공시 단건 요약(Q06)이 그 경우이며, 원공시와 정정본 중 무엇을 요약할지는
        판본 선택 정책이 정한다. 그 정책은 open_decisions.md D5에서 아직 미정이므로 여기서
        한쪽을 골라 열지 않는다. 정기공시에도 정정본은 있지만, 그쪽은 이미 Pattern A가 쓰고
        있는 조회 규칙(정정본 제외)이 있어 이 PR이 새로 정하는 것이 없다.

    대상 기간:
        절대연도 하나를 요구한다. 기간을 밝히지 않은 요약 질문(Q05)에서 어떤 문서를 기본값으로
        고를지는 open_decisions.md D5-a에서 미정이다. 기본값을 채우면 그 선택이 곧 정책이
        되므로 missing_year로 막는다. 상대 기간("작년")도 옮기지 않는다. D8이 정한 기준은
        시계열 비교의 기준 연도이고, 요약 대상 문서를 고르는 기준까지 정하지 않았다.

    문서 내 범위:
        section_scope를 요구한다. 읽지 못하면 문서 전체 요약으로 대체하지 않고 막는다
        (docs/agent_design/retrieval_strategy.md Pattern B의 Fallback).

    metric을 담지 않는다:
        요약 질문은 값 하나를 묻지 않는다. 기본값(매출액)이 남아 있으면 이 계획을 실행하는
        쪽이 재무 수치를 함께 뽑을 수 있고, 그 값은 질문이 요청한 범위 밖에서 나온다.
    """
    if not any(token in question for token in PERIODIC_REPORT_TOKENS):
        listed = ", ".join(PERIODIC_REPORT_TOKENS)

        raise UnsupportedQuestionError(
            reason="unsupported_summary_target",
            detail=(
                "요약 대상 문서를 확정할 수 없습니다. 정기공시 요약은 보고서 종류"
                f"({listed})를 질문에서 밝혀야 합니다. 거래소공시 단건 요약은 원공시와 "
                "정정본 중 무엇을 기준으로 할지 정하는 판본 선택 정책이 필요하며, 그 정책은 "
                "아직 정해지지 않았습니다(docs/agent_design/open_decisions.md D5). "
                "대상 문서를 임의로 고르지 않습니다."
            ),
        )

    relative_expression = _match_previous_year_expression(question)

    if relative_expression is not None:
        raise UnsupportedQuestionError(
            reason="unsupported_summary_period",
            detail=(
                f"요약 질문의 상대 기간 표현('{relative_expression}')은 해석하지 않습니다. "
                "확정된 기준 연도 정책(D8)은 시계열 비교의 기준이며, 요약 대상 문서를 고르는 "
                "기준까지 정하지 않았습니다. 연도를 밝혀 주십시오."
            ),
        )

    years = _require_years(_extract_years(question))

    if len(years) > 1:
        listed = ", ".join(str(year) for year in years)

        raise UnsupportedQuestionError(
            reason="unsupported_summary_period",
            detail=(
                f"질문이 {len(years)}개 기간({listed})을 함께 가리키고 있습니다. "
                "단일 문서 요약은 문서 1건을 대상으로 합니다. 한 기간을 골라 답하지 않습니다."
            ),
        )

    section_scope = _extract_section_scope(question)

    if section_scope is None:
        raise UnsupportedQuestionError(
            reason="missing_section_scope",
            detail=(
                "요약할 문서 내 범위를 질문에서 읽지 못했습니다. 문서 전체 요약으로 "
                "대체하지 않습니다."
            ),
        )

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        years=years,
        intent=INTENT_DOCUMENT_SUMMARY,
        document_class=DOCUMENT_CLASS_PERIODIC,
        document_subtype=DOCUMENT_SUBTYPE_ANNUAL,
        # 요약 범위에 연결·별도 구분이 적용되지 않는다. 재무 수치를 고르지 않기 때문이다.
        accounting_scope=QueryAccountingScope.NOT_APPLICABLE.value,
        metric=None,
        section_scope=section_scope,
    )


def _event_summary_subject_tokens(
    question: str,
    companies: tuple[str, ...],
    subject_names: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """
    질문에서 어느 이벤트를 가리키는지 밝힌 주제어를 읽는다. 없으면 빈 튜플.

    남기는 것이 아니라 걷어내는 방식이다. 무엇이 주제어인지 알아보는 규칙을 만들면 그 규칙에
    없는 주제는 인식되지 않고, 인식되지 않은 주제는 '주제를 밝히지 않은 질문'으로 취급되어
    실제로 답할 수 있는 질문이 막힌다. 반대로 걷어내는 쪽은 어떤 낱말이 주제가 될 수 있는지
    정하지 않는다.

    걷어내는 것은 다섯이다.

        기업명          답의 주체이지 이벤트를 가르는 조건이 아니다. 기업은 companies가
                        이미 담고 있고, 후보 조회의 조건으로 따로 쓰인다.
        공시 표기       '공시'가 들어간 낱말('공시', '원공시', '공시했을')은 문서 종류를
                        말할 뿐 어느 문서인지 가리지 않는다.
        요청 표기       '요약해줘', '알려줘' 같은 요청 표현.
        시점 표기       '처음', '당초' 같은 판본 지시어와 연·월 표기. 판본은 chain_policy가,
                        기간은 years/month가 각각 따로 담는다.
        일반 조사어     EVENT_SUBJECT_STOP_TOKENS.

    남은 낱말은 그대로 둔다. 조사를 떼거나 어간을 뽑지 않는다. 그런 변형은 규칙이 만들어 낸
    말이고, 그 말로 문서를 고르면 사용자가 쓰지 않은 조건으로 고른 것이 된다. 변형하지 않은
    낱말이 본문에서 걸리지 않으면 그 질문은 답하지 않는 쪽으로 끝난다.

    순서는 질문에 나온 순서다.
    """
    # canonical 이름만으로 걷어내면 별칭·상장명으로 적은 기업명이 남는다. '현대차 공시 내용
    # 정리해줘'에서 companies는 '현대자동차'뿐이고, 질문에 적힌 '현대차'는 어느 canonical과도
    # 같지 않아 주제어로 남는다. 그러면 답의 주체가 이벤트를 가르는 조건이 되어, 그 낱말이
    # 본문에 없는 문서가 전부 탈락한다.
    normalized_companies = {
        normalize_company_name(name)
        for name in (*companies, *subject_names)
        if name
    }

    normalized_companies.discard("")

    tokens: list[str] = []

    for raw_token in question.split():
        token = raw_token.strip(EVENT_SUBJECT_TRIM_CHARS)

        if not token:
            continue

        if EVENT_DISCLOSURE_TOKEN in token:
            continue

        if EVENT_SUMMARY_CONTENT_TOKEN in token:
            continue

        if any(request in token for request in EVENT_SUMMARY_REQUEST_TOKENS):
            continue

        if token in EVENT_SUBJECT_STOP_TOKENS:
            continue

        if ORIGINAL_STATE_PATTERN.search(token) or token in ("처음", "최초", "당초", "원래"):
            continue

        if YEAR_PATTERN.search(token) or MONTH_PATTERN.fullmatch(token):
            continue

        normalized_token = normalize_company_name(token)

        if normalized_token and normalized_token in normalized_companies:
            continue

        # 기업명이 조사와 붙어 온 경우("LG유플러스가")도 주제어가 아니다. 정규화한 기업명으로
        # 시작하는지 본다. 포함 여부로 보면 기업명이 우연히 들어간 다른 낱말까지 걸린다.
        if normalized_token and any(
            normalized_token.startswith(company) for company in normalized_companies
        ):
            continue

        tokens.append(token)

    return tuple(tokens)


def _looks_like_event_summary(
    question: str,
    companies: tuple[str, ...],
    subject_names: tuple[str, ...] = (),
) -> bool:
    """
    거래소공시 1건의 내용을 정리해 달라는 질문인가.

    넷을 모두 만족해야 참이다.

        1. 정기공시 보고서 표기가 없다   "사업보고서"가 있으면 Pattern B의 질문이다.
        2. 공시 표기가 있다              요약 대상이 공시 문서 자체임을 질문이 밝혔다.
        3. 내용 요청 표기가 있다         "내용" + "요약/정리/알려/설명".
        4. 이벤트 주제어를 읽었다        어느 공시인지 가릴 근거가 질문에 있다.

    1번이 규약의 핵심이다. 이 판정이 정기공시 요약의 회계연도 요건을 건드리지 않는다는 것을
    여기서 보장한다. "사업보고서 내용 요약해줘"처럼 연도를 밝히지 않은 정기공시 요약은 이
    경로로 오지 않고, 예전과 같이 missing_year로 막힌다(_build_document_summary_plan).

    4번이 두 번째 규약이다. 주제어가 없으면 이 경로를 열지 않으므로, 연도를 밝히지 않은
    모든 요약 질문이 합법이 되지는 않는다. "공시 내용 요약해줘"는 어느 공시인지 가릴 근거가
    없고, 그 질문은 이 판정을 통과하지 못한 채 예전 경로로 흘러간다.

    판정 순서에 대해:
        이 판정은 이력 판정보다 뒤다. "공시 내용 어떻게 바뀌었는지 정리해줘"처럼 두 표현이
        함께 있는 질문은 Pattern E가 답하던 것이고, 앞에 두면 그 질문을 가로챈다.
    """
    if any(token in question for token in PERIODIC_REPORT_TOKENS):
        return False

    compact = "".join(question.split())

    if EVENT_DISCLOSURE_TOKEN not in compact:
        return False

    if EVENT_SUMMARY_CONTENT_TOKEN not in compact:
        return False

    if not any(token in compact for token in EVENT_SUMMARY_REQUEST_TOKENS):
        return False

    return bool(_event_summary_subject_tokens(question, companies, subject_names))


def _extract_event_summary_chain_policy(question: str) -> ChainPolicy:
    """
    질문이 요구한 판본을 읽는다.

    원공시 시점을 지목한 표현이 있으면 원공시 상태, 없으면 최신 유효 상태다. 두 값 모두
    Contract v0.1의 ChainPolicy 어휘이며, 새 어휘를 만들지 않는다
    (open_decisions.md D5가 "chain_policy 필드가 이 정책을 표현하는 자리"라고 적어 둔 곳).

    기본값을 코드가 고르는 것이 아니다. 시점을 밝히지 않은 질문이 묻는 것은 "지금 유효한
    내용"이며, 그것이 latest_valid다. 반대로 두면 정정이 반영된 공시에서 낡은 값이 기본
    답이 되고, 사용자는 그것이 낡았다는 사실을 알 수 없다.
    """
    compact = "".join(question.split())

    if ORIGINAL_STATE_PATTERN.search(compact):
        return ChainPolicy.ORIGINAL_ONLY

    return ChainPolicy.LATEST_VALID


def _build_event_summary_plan(
    question: str,
    companies: tuple[str, ...],
    subject_names: tuple[str, ...] = (),
) -> QueryPlan:
    """
    Q06 계열 거래소공시 단건 요약 계획을 만든다.

        "LG유플러스 파주 AIDC 투자 공시 내용 요약해줘."
        -> 거래소공시 + event_subject_tokens=('파주', 'AIDC', '투자') + latest_valid

    회계연도를 요구하지 않는다:
        거래소공시 manifest 레코드에는 base_year가 없다. 공시일만 있으므로 회계연도를
        요구하면 사용자가 알 수 없는 값을 요구하게 되고, 오늘 날짜에서 연도를 만들어 채우면
        같은 질문이 실행한 날에 따라 다른 문서를 가리킨다. 어느 쪽도 하지 않는다.

        대신 대상 문서는 기업과 이벤트 주제어가 좁힌다. 좁혀지지 않으면 handler가
        ambiguous_root로 막는다. 정기공시 요약(Pattern B)의 회계연도 요건은 그대로다.

    연도를 밝힌 질문에서는 그 연도를 버리지 않는다:
        "LG유플러스 2024년 파주 AIDC 투자 공시 내용 요약해줘"에서 2024를 버리면, 사용자가
        좁힌 범위가 조용히 사라진 채로 다른 해의 공시가 답이 된다. Pattern E와 같이 공시일
        기준점으로 담고 handler가 원공시 후보를 그 창으로 좁힌다.

    상대 기간 표현은 옮기지 않는다:
        확정된 기준 연도 정책(D8)은 시계열 비교의 기준이며 요약 대상 문서를 고르는 기준까지
        정하지 않았다. _build_document_summary_plan이 같은 이유로 같은 판정을 한다.

    공시 세부유형은 질문이 밝힌 경우에만 담는다:
        "시설투자 공시 내용 요약해줘"처럼 유형을 말한 질문에서는 그 유형으로 후보를 좁힌다.
        말하지 않았으면 None이고, handler가 지원하는 유형 전체를 후보로 둔다. 유형을 추측해
        채우면 그 추측이 후보를 잘못 좁혀 실제로 있는 공시가 '없다'로 판정된다.

        두 유형이 함께 맞으면 고르지 않고 막는다. _extract_event_subtype_rule이 이력 질문에
        대해 하는 판정과 같으며, 사유 이름도 같다.
    """
    relative_expression = _match_previous_year_expression(question)

    if relative_expression is not None:
        raise UnsupportedQuestionError(
            reason="unsupported_event_summary_period",
            detail=(
                f"공시 요약 질문의 상대 기간 표현('{relative_expression}')은 해석하지 "
                "않습니다. 확정된 기준 연도 정책(D8)은 시계열 비교의 기준이며, 요약 대상 "
                "공시를 고르는 기준까지 정하지 않았습니다."
            ),
        )

    years = _extract_years(question)

    if len(years) > 1:
        listed = ", ".join(str(year) for year in years)

        raise UnsupportedQuestionError(
            reason="unsupported_event_summary_period",
            detail=(
                f"질문이 {len(years)}개 기간({listed})을 함께 가리키고 있습니다. "
                "공시 단건 요약은 공시 1건을 대상으로 합니다. 한 기간을 골라 답하지 않습니다."
            ),
        )

    matched_rules = _find_event_subtype_rules(question)

    if len(matched_rules) > 1:
        listed = ", ".join(rule.subtype for rule in matched_rules)

        raise UnsupportedQuestionError(
            reason="ambiguous_event_subtype",
            detail=(
                f"질문이 거래소공시 유형 {len(matched_rules)}종({listed})을 함께 가리키고 "
                "있어 어느 유형의 공시를 요약할지 특정할 수 없습니다. "
                "한 유형을 골라 답하지 않습니다."
            ),
        )

    subject_tokens = _event_summary_subject_tokens(question, companies, subject_names)

    if not subject_tokens:
        raise UnsupportedQuestionError(
            reason="missing_event_subject",
            detail=(
                "어느 공시를 요약할지 가릴 주제어를 질문에서 읽지 못했습니다. "
                "기업의 거래소공시 중 하나를 임의로 고르지 않습니다."
            ),
        )

    month = _extract_month(question)

    # 월만 있고 연도가 없으면 공시일 창을 만들 수 없다. 질문이 밝힌 '4월'이 계획에는 담기지만
    # 조회에는 쓰이지 않는 상태를 만들지 않는다. _build_event_history_plan과 같은 규칙이다.
    if month is not None:
        years = _require_years(years)

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        years=years[:1],
        intent=INTENT_EVENT_SUMMARY,
        document_class=DOCUMENT_CLASS_EVENT,
        document_subtype=matched_rules[0].subtype if matched_rules else None,
        # 거래소공시에는 연결·별도 구분이 없다.
        accounting_scope=QueryAccountingScope.NOT_APPLICABLE.value,
        metric=None,
        month=month,
        chain_policy=_extract_event_summary_chain_policy(question),
        event_subject_tokens=subject_tokens,
    )


def _find_contract_fields(question: str) -> tuple[MetricConcept, ...]:
    """
    질문이 요구한 계약 문서 필드를 읽는다. 없으면 빈 튜플이며 추측하지 않는다.

    표기와 필드의 대응은 agent.query_vocabulary.find_contract_fields가 갖는다. "계약금액과
    계약기간", "계약 시작일과 종료일, 금액"은 같은 세 필드를 요구하며 같은 차례로 돌아온다.
    """
    return find_contract_fields(question)


def _names_the_subject(
    found: str,
    company: str,
    subject_names: tuple[str, ...],
) -> bool:
    """
    읽어 낸 이름이 질문의 대상 기업을 가리키는가.

    canonical 이름만 비교하지 않는다. 사용자가 상장명이나 별칭으로 적었으면 그 표기가
    canonical과 같지 않고, 그러면 대상 기업 자신이 계약 상대방 자리에 담긴다. 그 이름으로
    후보를 좁히면 아무 문서도 찾지 못한다.

    subject_names는 이 질문에서 기업을 가리킨 표현들이다(CompanyResolution.surfaces).
    질문에 실제로 적힌 표기만 들어 있으며, 여기서 새로 만들지 않는다.
    """
    normalized_found = normalize_company_name(found)

    if not normalized_found:
        return False

    return normalized_found in {
        normalize_company_name(name) for name in (company, *subject_names) if name
    }


def _extract_counterparty(
    question: str,
    company: str,
    subject_names: tuple[str, ...] = (),
) -> str | None:
    """
    질문이 지목한 계약 상대방. 없으면 None이며 추정하지 않는다.

    질문에 적힌 문자열을 그대로 쓴다. 정규화하거나 코퍼스의 이름으로 바꾸지 않는다. 그 대조는
    실제 후보 문서를 여는 retrieval 계층이 하며, 여기서 미리 바꾸면 같은 이름에 대한 판정이
    두 벌이 된다.

    대상 기업과 같은 이름이면 상대방으로 읽지 않는다. "한화오션과 체결한"처럼 쓰인 문장에서
    주체를 상대방 자리에 담으면, 그 이름으로 후보를 좁혀 아무 문서도 찾지 못한다.
    """
    match = COUNTERPARTY_PATTERN.search(question)

    if not match:
        return None

    found = match.group(1).strip()

    if not found:
        return None

    if _names_the_subject(found, company, subject_names):
        return None

    return found


def _extract_event_counterparty(
    question: str,
    company: str,
    subject_names: tuple[str, ...] = (),
) -> str | None:
    """
    이력 질문이 지목한 계약 상대방. 없으면 None이며 추정하지 않는다.

    _extract_counterparty와 규칙만 다르고 하는 일은 같다. 어느 표현을 읽는지는
    EVENT_COUNTERPARTY_PATTERN에 적어 두었다.

    이 값을 무엇에 쓰는지가 Q02와 다르다:
        Q02는 이 이름으로 후보 문서를 좁힌다. Pattern E는 좁히지 않는다. 상대방으로 문서를
        확정하는 능력(C3)이 아직 없기 때문이며, 여기서 이름을 담는 것은 그 능력을 흉내 내려는
        것이 아니라 **없다는 사실을 handler에 전달**하려는 것이다.

        handler는 이 값이 있고 원공시 후보가 1건일 때 그 1건을 답으로 삼지 않고 막는다
        (handlers.event_history의 unverified_counterparty). 질문이 상대방으로 계약을 지목했는데
        이 구현은 그 이름을 본문과 대조하지 못하므로, 후보가 하나라는 것만으로 "그 상대방의
        계약"이라고 말할 수 없다. 후보가 하나뿐인 것은 코퍼스가 그렇다는 뜻이지 질문이 가리킨
        계약이라는 뜻이 아니다.

        그래서 이 값은 계획에 담기지만 조회 조건으로는 쓰이지 않는다. 담고도 아무도 읽지 않는
        값이 아니라, 읽는 쪽이 정해져 있고 그 쪽이 하는 일이 '고르지 않기'다.

    대상 기업과 같은 이름이면 상대방으로 읽지 않는다. _extract_counterparty와 같은 이유다.
    """
    match = EVENT_COUNTERPARTY_PATTERN.search(question)

    if not match:
        return None

    found = match.group(1).strip()

    if not found:
        return None

    if _names_the_subject(found, company, subject_names):
        return None

    return found


def _looks_like_contract_fact(question: str) -> bool:
    """
    거래소공시 계약 1건의 값을 묻는 질문인가.

    '계약'과 요구 필드 표기가 함께 있어야 한다. 둘 중 하나만으로 판정하면 '계약 이력'까지
    이 경로로 끌려오거나, 매출 질문의 '금액'이 계약 질문이 된다.

    이력 표현은 여기서 보지 않는다. build_query_plan이 _looks_like_event_history를 먼저
    판정하므로, 정정·변경을 묻는 질문은 이 자리에 도달하지 않는다. 두 곳에서 같은 규칙을
    세면 어긋날 수 있어 순서 하나로 가른다.
    """
    return "계약" in question and bool(_find_contract_fields(question))


def _build_contract_fact_plan(
    question: str,
    companies: tuple[str, ...],
    years: tuple[int, ...],
    subject_names: tuple[str, ...] = (),
) -> QueryPlan:
    """
    Q02 계열 공급계약 단건 사실 조회 계획을 만든다.

    세부유형을 공급계약으로 고정하는 이유:
        계약금액·계약기간은 공급계약 공시의 어휘다. 다만 질문이 다른 유형을 함께 가리키면
        고르지 않고 막는다. 시설투자 공시에도 금액과 기간이 적히므로, 한쪽을 골라 담으면
        다른 유형을 물었다는 사실이 계획에서 사라진다.

    연도를 요구하는 이유:
        한 기업이 해마다 여러 건의 공급계약을 공시한다. 시점이 없으면 어느 계약인지 정할 수
        없고, 상대방만으로 좁히는 경로는 아직 없다.

        이력 조회(_build_event_history_plan)와 다른 자리다. 그쪽은 연도가 없어도 계획을
        만들고, 후보를 하나로 좁히지 못하면 handler가 ambiguous_root로 막는다. 이쪽에는
        그 자리가 없다. 계약 1건의 값을 묻는 질문이라 후보가 여럿일 때 무엇으로 막을지는
        handler의 missing_counterparty가 따로 정하고 있으며, 그 경로는 상대방을 읽은
        계획을 전제한다. 두 경로의 요구가 다른 것은 답의 모양이 다르기 때문이다.

    요구 필드를 SUPPORTED_CONTRACT_FIELDS로 고정하는 이유:
        이 구현이 답 문장까지 옮길 수 있는 형태가 그 하나다. 부분집합을 통과시키면 계획은
        만들어지고 handler도 지나가지만 answer_builder에서 터지며, 그때는 결과가 이미
        ANSWERABLE로 판정된 뒤라 막힌 이유가 남지 않는다. 막을 것이면 여기서 막는다.

    counterparty가 None이어도 계획을 만든다:
        여기서 거절하지 않는 것은 상대방을 읽지 못한 질문과 지원하지 않는 질문 형태를
        구분해 남기기 위해서다. 다만 그 계획이 답에 이르지는 않는다. handler가
        missing_counterparty로 막으며(agent.handlers.contract_fact), 후보가 하나뿐이라
        상대방 없이도 확정할 수 있는 경로는 아직 열려 있지 않다. 그 경로를 여는 것은 후보
        유일성 판정을 어느 계층이 내릴지 정하는 결정이고 이 범위 밖이다.
    """
    matched = _find_event_subtype_rules(question)

    other = tuple(
        rule.subtype for rule in matched if rule.subtype != EVENT_SUBTYPE_SUPPLY_CONTRACT
    )

    if other:
        listed = ", ".join((EVENT_SUBTYPE_SUPPLY_CONTRACT, *other))

        raise UnsupportedQuestionError(
            reason="ambiguous_event_subtype",
            detail=(
                f"질문이 여러 공시 유형을 함께 가리키고 있습니다: {listed}. "
                "어느 유형의 계약인지 특정할 수 없습니다. 한 유형을 골라 답하지 않습니다."
            ),
        )

    years = _require_years(years)

    # 요구 필드 판정은 대상 문서를 확정한 뒤에 본다.
    #
    # 유형이 모호하거나 연도가 없는 질문은 어느 계약을 묻는지부터 정해지지 않는다. 그
    # 상태에서 필드 조합을 먼저 나무라면, 유형과 연도를 밝혀도 여전히 막힌다는 사실이
    # 첫 응답에 드러나지 않는다.
    requested_fields = _find_contract_fields(question)

    if set(requested_fields) != SUPPORTED_CONTRACT_FIELDS:
        listed = ", ".join(sorted(field.value for field in requested_fields)) or "없음"
        supported = ", ".join(sorted(field.value for field in SUPPORTED_CONTRACT_FIELDS))

        raise UnsupportedQuestionError(
            reason="unsupported_contract_fields",
            detail=(
                f"이 구현이 답할 수 있는 계약 필드 조합이 아닙니다. 질문이 요구한 필드: "
                f"{listed}. 지원하는 조합: {supported}. 계약금액과 계약기간을 함께 물어야 "
                "합니다. 일부만 담아 답하지 않습니다."
            ),
        )

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        years=years[:1],
        intent=INTENT_CONTRACT_FACT,
        document_class=DOCUMENT_CLASS_EVENT,
        document_subtype=EVENT_SUBTYPE_SUPPLY_CONTRACT,
        # 거래소공시에는 연결·별도 구분이 없다.
        accounting_scope=QueryAccountingScope.NOT_APPLICABLE.value,
        metric=None,
        requested_fields=requested_fields,
        counterparty=_extract_counterparty(question, companies[0], subject_names),
        month=_extract_month(question),
    )


def _build_cross_entity_compare_plan(
    question: str,
    companies: tuple[str, ...],
) -> QueryPlan:
    """
    여러 기업을 함께 묻는 질문의 계획을 만든다.

    Pattern D handler가 계획의 주체·기간·지표·문서군을 입구에서 다시 확인한다.
    Q12는 시설투자·집계·비교 신호가 모두 있을 때만 event aggregate 계획을 만들고,
    기존 Q10·Q11은 periodic 계획을 유지한다.

    한 가지만 예외다. 원천 지표와 파생지표를 함께 물은 질문은 여기서 막는다. 그것은 지표를
    모르는 상태가 아니라 한쪽을 골라야만 답이 되는 상태이고, 골라 담으면 나머지를 물었다는
    사실이 사라진다(_resolve_requested_metrics 참조).

    Q11("영업이익률이 더 높은 곳은 어디야?")의 파생 개념은 Pattern C와 같은 대응표에서 읽는다.
    비교 축이 기업인지 기간인지는 "영업이익률이 무엇에서 나오는가"와 무관하다.
    """
    accounting_scope, scope_explicit = _extract_accounting_scope(question)
    metric, derived_metric = _resolve_requested_metrics(question)
    facility_aggregate = _looks_like_facility_investment_aggregate(question)

    # Q12에서 요청한 개념은 이벤트의 investment_amount이다. 시설투자 문구와
    # 집계·비교 의미가 모두 확정된 경우에만 채운며, 질문이 다른 정기공시
    # 지표까지 함께 물으면 한쪽을 고르지 않고 아래 입구 guard가 막게 둔다.
    q12_metric = (
        MetricConcept.INVESTMENT_AMOUNT
        if facility_aggregate and metric is None and derived_metric is None
        else metric
    )

    # 상대 기간 표현을 읽었다는 사실은 옮기지 않더라도 계획에 남긴다.
    #
    # 이 판정은 다중 기업 분기가 기간 분기보다 먼저 일어나기 때문에 여기에 있다. 남기지
    # 않으면 "HD현대중공업이랑 삼성중공업 2025년 매출이 전년보다 어때"의 계획에는 years=(2025,)
    # 만 남고, 상대 기간을 봤다는 기록이 어디에도 없다. 그러면 이 계획을 실행하는 쪽은 기간이
    # 하나라고 읽어 2025년만 견준 답을 돌려주고, 사라진 기간이 결과에 남지 않는다.
    #
    # 옮기지는 않는다. 기업이 둘이면 D8의 기준(해당 기업의 최신 사업보고서 회계연도)이 기업마다
    # 다를 수 있고, 문장 안의 절대연도와 둘 중 무엇을 기준으로 삼을지도 정해진 바 없다. 둘 중
    # 하나를 골라 옮기면 고른 쪽이 조용히 답이 된다. 그래서 anchor_year는 비워 두며, 그 계획은
    # has_unresolved_relative_period가 참이 되어 실행하는 쪽이 years만으로 답하지 않는다.
    relative_expression = _match_previous_year_expression(question)

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        years=_require_years(_extract_years(question)),
        intent=INTENT_CROSS_ENTITY_COMPARE,
        document_class=(DOCUMENT_CLASS_EVENT if facility_aggregate else DOCUMENT_CLASS_PERIODIC),
        document_subtype=(
            EVENT_SUBTYPE_FACILITY_INVESTMENT
            if facility_aggregate
            else DOCUMENT_SUBTYPE_ANNUAL
        ),
        accounting_scope=(
            QueryAccountingScope.NOT_APPLICABLE.value
            if facility_aggregate
            else accounting_scope
        ),
        metric=q12_metric,
        derived_metric=derived_metric,
        accounting_scope_explicit=(False if facility_aggregate else scope_explicit),
        relative_period=(
            RelativePeriod(
                expression=relative_expression,
                offset=PREVIOUS_YEAR_OFFSET,
            )
            if relative_expression is not None
            else None
        ),
        operations=(
            (OPERATION_AGGREGATE, OPERATION_COMPARE) if facility_aggregate else ()
        ),
        chain_policy=(ChainPolicy.FULL_CHAIN if facility_aggregate else None),
    )


def _build_temporal_compare_plan(
    question: str,
    companies: tuple[str, ...],
    years: tuple[int, ...],
    *,
    relative_period: RelativePeriod | None = None,
) -> QueryPlan:
    """
    여러 기간을 함께 묻는 질문의 계획을 만든다.

    Pattern C handler는 아직 없다. 그런데도 계획을 만들어 두는 이유는
    _build_cross_entity_compare_plan과 같다. intent를 붙이지 않고 single_fact로 흘려보내면
    첫 연도만 남은 답이 Fact와 Evidence까지 갖춘 채로 ANSWERABLE로 돌아간다. 미지원보다
    그쪽이 위험하다.

    지표와 회계기준은 느슨하게 읽는다. 이 계획을 실행할 handler가 없으므로 여기서 거절하면
    막힌 이유가 'Pattern C 미구현'이 아니라 '지표를 모르겠다'로 바뀌어, 무엇을 구현하면
    풀리는 질문인지가 결과에서 사라진다.

    한 가지만 예외다. 원천 지표와 파생지표를 함께 물은 질문은 여기서 막는다. 그것은 지표를
    모르는 상태가 아니라 한쪽을 골라야만 답이 되는 상태이고, 골라 담으면 나머지를 물었다는
    사실이 사라진다(_resolve_requested_metrics 참조).

    Q08("영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?")의 영업이익률은 원천 지표가
    아니라 파생 개념이므로 derived_metric에 담긴다. 그것을 만들려면 어떤 원천 지표가 필요한지는
    여기서 정하지 않는다. 계획은 무엇을 물었는지까지만 담고, 무엇을 확보하고 계산할지는 이
    계획을 실행하는 쪽이 대응표를 보고 정한다.

    확보한 절대연도는 years에 그대로 담는다. 첫 연도를 골라 담지 않으며, 그렇게 하면 두
    기간을 물은 질문에 한 연도만 남아 F6-02가 재현된다.

    warnings는 진단 용도다. 어느 연도를 왜 그렇게 읽었는지를 사람이 읽는 자리이며, 기간
    정보를 기계가 읽어야 할 때 참조하는 곳이 아니다. agent 안에서 그 값을 읽어야 하면
    years를 본다.

    상대기간이 섞인 질문에서는 years에 절대연도만 남는다("2025년 매출이 전년보다"는
    years=(2025,)). "전년"을 연도로 옮기려면 기준 시점 정책이 필요하고 그것은 Pattern C
    범위다. 미해결 상대기간을 표현해야 할 때가 오면 공유 Contract의 Period.kind가 그 자리를
    이미 갖고 있으므로, 그때 agent -> common 변환 경계에서 정한다.

    문서 내 범위를 함께 읽는 이유(Q09):
        시계열 비교에는 수치 경로와 서술 경로가 있다(agent_patterns.md Pattern C). 두 경로를
        가르는 것은 '지표를 읽지 못했다'가 아니라 '어느 서술 범위를 견주라고 했는가'이다.
        지표가 비어 있다는 것만으로 서술 비교라고 단정하면, 아직 어휘가 없는 원천 지표를 물은
        질문이 서술 경로로 끌려가 엉뚱한 Section을 견준 답이 나온다. 그래서 질문이 범위를
        밝힌 경우에만 이 값을 담고, 읽지 못하면 None으로 둔 채 실행하는 쪽의 판정에 맡긴다.

        범위를 읽는 일은 _extract_section_scope 하나가 한다. Pattern B와 같은 어휘를 쓰며,
        여기에 따로 대응표를 두면 같은 표현에 두 벌의 규칙이 생긴다.
    """
    accounting_scope, scope_explicit = _extract_accounting_scope(question)
    metric, derived_metric = _resolve_requested_metrics(question)
    section_scope = _extract_section_scope(question)

    listed = ", ".join(str(year) for year in years)

    # 상대기간이 섞인 질문은 확보한 절대연도 개수가 곧 기간 개수가 아니다. 개수를 세어 적으면
    # "1개 기간"이라고 말하면서 시계열 비교로 분류한 결과가 되어 진단이 스스로 모순된다.
    if relative_period is not None:
        detail = (
            f"질문이 절대연도({listed})와 상대 기간 표현"
            f"('{relative_period.expression}')을 함께 쓰고 있어"
        )
    else:
        detail = f"질문이 {len(years)}개 기간({listed})을 함께 묻고 있어"

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        years=years,
        intent=INTENT_TEMPORAL_COMPARE,
        accounting_scope=accounting_scope,
        metric=metric,
        derived_metric=derived_metric,
        section_scope=section_scope,
        accounting_scope_explicit=scope_explicit,
        relative_period=relative_period,
        warnings=(f"{detail} 시계열 비교로 해석했습니다.",),
    )


def _build_relative_period_plan(
    question: str,
    companies: tuple[str, ...],
    *,
    expression: str,
    fiscal_year_anchors: Mapping[str, int] | None,
) -> QueryPlan:
    """
    절대연도 없이 과거 상대 기간만 쓴 질문의 계획을 만든다. Q07이 이 경로다.

        "한미반도체 매출이 작년보다 얼마나 늘었어?"
        -> anchor = 한미반도체의 최신 사업보고서 회계연도
        -> years = (anchor - 1, anchor)

    기준 연도를 어디서 가져오는가 (docs/agent_design/open_decisions.md D8):
        해당 기업의 사업보고서가 코퍼스에 존재하는 가장 최신 회계연도다. 실행 시각을 쓰지
        않으므로 같은 코퍼스에서 같은 질문은 언제 실행해도 같은 연도를 가리킨다.

        요청 지표를 그 문서에서 실제로 읽을 수 있는지는 보지 않는다. 보기 시작하면 추출에
        실패한 해가 조용히 건너뛰어져 "2025년 매출을 못 읽었으니 2024년으로 견주자"가 되고,
        그 결과는 근거를 갖춘 채로 질문과 다른 기간을 답하는 형태다. anchor는 문서의 존재만으로
        정하고, 값이 없으면 뒤 단계가 NOT_FOUND로 답한다.

    years 순서는 오래된 것부터다. 질문에 적힌 순서를 그대로 쓰면 같은 변화에 부호가 뒤집힌
    값이 나오고, 뒤집힌 값도 그럴듯하다. 이 순서를 계획 쪽에서 고정해 두면 실행하는 쪽이
    매번 다시 정하지 않는다.

    intent를 temporal_compare로 두는 이유는 질문이 가리키는 기간이 둘이기 때문이다. 기존
    어휘를 쓰며 새 intent를 만들지 않는다. 절대연도 두 개를 적은 질문(Q08)과 이 질문은 계획이
    같은 모양이어야 하고, 실행하는 쪽이 둘을 구분할 이유가 없다.

    지표와 회계기준은 _build_temporal_compare_plan과 같은 규칙으로 읽는다. 두 경로가 만드는
    계획의 모양이 달라지면, 같은 Pattern을 실행하는 쪽이 어느 경로로 왔는지 알아야 한다.
    """
    anchor_year = _resolve_anchor_year(companies[0], fiscal_year_anchors)

    relative_period = RelativePeriod(
        expression=expression,
        offset=PREVIOUS_YEAR_OFFSET,
        anchor_year=anchor_year,
        anchor_source=ANCHOR_SOURCE_LATEST_ANNUAL_FILING,
    )

    years = relative_period.resolved_years

    accounting_scope, scope_explicit = _extract_accounting_scope(question)
    metric, derived_metric = _resolve_requested_metrics(question)

    older, newer = years

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        years=years,
        intent=INTENT_TEMPORAL_COMPARE,
        accounting_scope=accounting_scope,
        metric=metric,
        derived_metric=derived_metric,
        accounting_scope_explicit=scope_explicit,
        relative_period=relative_period,
        warnings=(
            f"질문의 상대 기간 표현('{expression}')에 기준 연도 FY{anchor_year}를 적용해 "
            f"FY{older} -> FY{newer} 시계열 비교로 해석했습니다. "
            f"기준 연도는 코퍼스에 있는 '{companies[0]}'의 최신 사업보고서 회계연도이며, "
            "실행 시각이나 현재 연도를 쓰지 않았습니다.",
        ),
    )


def _build_event_history_plan(
    question: str,
    companies: tuple[str, ...],
    subject_names: tuple[str, ...] = (),
) -> QueryPlan:
    """
    Q13 계열 정정 이력 조회 계획을 만든다.

    chain_policy는 full_chain으로 고정한다. 이력을 묻는 질문에서 정정본을 빼면 답할 것이
    남지 않는다. 다른 정책이 필요한 질문 형태는 이번 범위 밖이다.

    years에는 연도를 하나만 담는다. 질문에 연도가 둘 이상 보여도 마찬가지다.

    Pattern E의 연도는 비교 대상 기간이 아니라 원공시를 찾는 공시일 기준점이기 때문이다.
    handler는 이 값으로 원공시 후보만 좁히고(filing_date_window), 정정본은 기간 제한 없이
    문서에 적힌 연결을 따라간다. 그래서 "2024년 8월에 공시한 공급계약, 2025년에 바뀐 내용
    있어?"에서 2024는 기준점이고 2025는 그 기준점에서 출발한 체인이 이미 포함하는 서술이다.
    첫 연도를 고르는 것이 값을 버리는 일이 아니라 기준점을 고르는 일이다.

    다중 기간 질문을 여기로 들이지 않는 것은 build_query_plan의 판정 순서가 맡는다. 이력
    판정이 다중 기간 판정보다 앞이라 이 builder에 먼저 도달하며, 그 순서가 규약이다.

    연도가 없는 질문:
        Q14("LG에너지솔루션이 포드랑 맺었던 배터리 공급계약, 지금 어떻게 됐어?")와
        Q15("레인보우로보틱스 사옥·제조시설 신축 투자, 그동안 계획이 몇 번 바뀌었어?")처럼
        공시 시점을 밝히지 않는 이력 질문이 있다. 유형과 무관하게 years를 비운 계획을
        만든다.

        예전에는 유형이 정했다(EventSubtypeRule.requires_filing_anchor). 공급계약은 요구하는
        쪽이었고, 그래서 Q14가 이 자리에서 missing_year로 막혔다. 그 사유는 실제로 일어난
        일과 달랐다. 질문이 연도를 못 밝힌 것이 문제가 아니라, 그 기업의 공급계약 원공시가
        여러 건이라 어느 계약인지 좁히지 못하는 것이 문제다. 후보가 몇 건인지는 코퍼스를
        열어야 알 수 있어 이 계층이 판정할 수 없으므로, 막는 자리를 후보 개수가 보이는
        handler로 옮긴다.

        handler는 공시일 창 없이 원공시 후보를 찾는다(agent.handlers.event_history). 계획도
        handler도 후보 하나를 고르지 않으며, 무엇으로 막는지는 후보 개수와 이 계획이 담은
        counterparty가 함께 정한다.

            원공시가 여럿                    ambiguous_root. 상대방을 적었든 아니든 이 판정이
                                             먼저다. Q14가 이 경로로
                                             COMPLETED·UNRESOLVED·AMBIGUOUS_MATCH가 된다.

            원공시 1건 + counterparty 있음    unverified_counterparty. 아래 참조.

            원공시 1건 + counterparty 없음    그 1건의 이력을 답한다. 예전과 같다.

        상대방을 적은 질문에서 1건을 답으로 삼지 않는 이유:
            이 계획이 담는 counterparty는 조회 조건이 아니다(_extract_event_counterparty).
            상대방으로 문서를 확정하는 능력(C3)은 없으며 이 PR이 만들지도 않는다. 이름을 담는
            것은 그 능력을 흉내 내려는 것이 아니라, 질문이 확인할 수 없는 조건을 걸었다는
            사실을 handler에 넘기려는 것이다.

            그 조건을 무시하고 후보 1건을 답하면 "그 상대방과 맺은 계약의 이력"을 물었는데
            "이 기업의 공급계약 중 코퍼스에 하나뿐인 것의 이력"을 답하게 된다. 후보가 하나뿐인
            것은 코퍼스가 그렇다는 뜻이지 질문이 가리킨 계약이라는 뜻이 아니다.

            그래서 handler가 unverified_counterparty로 막는다. 공개 사유는 ambiguous_root와
            같은 AMBIGUOUS_MATCH이며, 대상 문서를 확정하지 못한 판정이라는 점이 같다. 내부
            사유를 나눈 것은 후보 개수가 1과 N으로 달라 같은 진단 문장을 쓸 수 없어서다.

        세 갈래 모두 tests/integration/test_pattern_e_q15.py에 대조군으로 고정해 두었다.

    월만 있고 연도가 없는 질문:
        연도 요구가 남아 있는 자리는 여기 하나다. 월은 공시일 창을 좁히는 조건인데 연도가
        없으면 창을 만들 수 없고, 그대로 두면 질문이 밝힌 '4월'이 계획에는 담기지만 조회에는
        쓰이지 않아 사용자가 좁힌 범위가 조용히 사라진다. 후보 개수와 무관하게 막는다.
    """
    rule = _extract_event_subtype_rule(question)

    years = _extract_years(question)

    month = _extract_month(question)

    if month is not None:
        years = _require_years(years)

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        # 연도가 여럿 보여도 첫 값 하나만 기준점으로 담는다(위 설명 참조).
        years=years[:1],
        intent=INTENT_EVENT_HISTORY,
        document_class=DOCUMENT_CLASS_EVENT,
        document_subtype=rule.subtype,
        # 거래소공시에는 연결·별도 구분이 없다.
        accounting_scope=QueryAccountingScope.NOT_APPLICABLE.value,
        metric=None,
        month=month,
        # 조회 조건이 아니라 handler에 넘기는 fail-close 신호다. 상대방으로 후보를 좁히지
        # 않으며, 이 값이 있으면 후보 1건을 답으로 확정하지 않는다
        # (_extract_event_counterparty 참조).
        counterparty=_extract_event_counterparty(question, companies[0], subject_names),
        chain_policy=ChainPolicy.FULL_CHAIN,
    )


def _reject_out_of_scope_period(question: str) -> None:
    """
    이번 범위 밖 기간 표현을 거절한다.

    질문에 쓰인 기간 표현만 보며 어떤 intent인지는 보지 않는다. 그래서 의도 분기보다 앞의
    공통 경로에서 실행한다. 의도별 builder 안에서 부르면 Pattern을 추가할 때마다 같은 검사를
    다시 붙여야 하고, 한 번 빠뜨리면 그 Pattern만 조용히 검사를 건너뛴다.

    연도 범위는 여기서 보지 않는다. 코퍼스 보유 기간 밖 연도(예: 2022, 2027)는 문서 조회
    단계에서 걸러져 `COMPLETED`/`UNANSWERABLE`로 답한다. 그 판정은 코퍼스를 실제로 본 뒤에야
    할 수 있으므로 질문 해석 단계에서 앞질러 정하지 않는다.
    """
    lowered = question.casefold()

    for token in QUARTER_TOKENS:
        if token in lowered:
            raise UnsupportedQuestionError(
                reason="unsupported_period",
                detail="분기·반기 보고서 조회는 이번 범위에 포함되지 않습니다.",
            )


def build_company_resolver(
    known_companies: Sequence[str] | None = None,
    *,
    company_universe: Sequence[Mapping[str, Any]] | None = None,
) -> CompanyResolver:
    """
    인식 후보 목록에서 기업명 해석기를 만든다.

    두 입력을 함께 받는 이유:
        company_universe는 manifest가 아는 표기 전부(`corp_name` + `listed_name`)이고,
        known_companies는 '이 질문에서 인식해도 되는 기업명'이다. 지분공시 질문은 보고자
        (`flr_nm`)를 인식해야 하는데 그 이름은 발행회사 universe에 없을 수 있다.

        universe를 주지 않으면 known_companies만으로 만든다. 그때는 상장명이 없으므로
        canonical 표기와 alias만으로 인식하며, 기존 호출자의 동작이 그대로다.
    """
    names = tuple(known_companies or ())

    if company_universe is None:
        return CompanyResolver.from_names(names)

    return CompanyResolver.from_universe(company_universe, extra_names=names)


def resolve_companies(
    question: str,
    known_companies: Sequence[str] | None = None,
    *,
    company_universe: Sequence[Mapping[str, Any]] | None = None,
) -> CompanyResolution:
    """
    질문 하나의 기업명 해석 결과를 그대로 돌려준다.

    build_query_plan이 쓰는 것과 같은 규칙이며, 계획을 만들지 않고 해석 근거만 본다.
    raw 표기 / 정규화 표기 / canonical 기업 / 해석 방법 / 후보가 모두 들어 있어, 어느 표현이
    어떤 경로로 어느 회사가 되었는지를 QueryPlan을 거치지 않고 확인할 수 있다.

    QueryPlan에 이 값을 싣지는 않는다. Contract를 넓히는 일이라 이번 범위 밖이다.
    """
    resolver = build_company_resolver(known_companies, company_universe=company_universe)

    return resolver.resolve_question(question)


def build_query_plan(
    question: str,
    known_companies: tuple[str, ...] | list[str] | None = None,
    *,
    fiscal_year_anchors: Mapping[str, int] | None = None,
    company_universe: Sequence[Mapping[str, Any]] | None = None,
) -> QueryPlan:
    """
    질문 문자열에서 Q01 계열 QueryPlan을 만든다.

    known_companies에 준 목록 안에서만 기업을 인식한다.
    목록이 없으면 기업명을 추측하지 않고 거절한다.

    company_universe를 함께 주면 manifest의 `listed_name`까지 인식 후보 표기로 쓴다.
    '현대차'·'삼성화재'·'KT'가 그 자리이며, 무엇으로 인식했든 계획에 담기는 것은 canonical
    `corp_name` 하나다. 주지 않으면 canonical 표기와 등록된 alias만으로 인식한다.

    fiscal_year_anchors는 상대 기간("작년")을 절대연도로 옮길 기준 연도 대응표다. 키는
    normalize_company_name을 거친 기업명이며, 값은 그 기업의 사업보고서가 코퍼스에 존재하는
    가장 최신 회계연도다. retrieval 계층의
    latest_annual_fiscal_year_by_company가 manifest에서 유도하고 호출자가 넘긴다.

    known_companies와 같은 방식으로 주입받는 이유는 같다. 질의 해석 계층이 코퍼스를 직접
    열지 않게 하려는 것이며(tests/unit/test_layering.py), 덕분에 이 모듈은 manifest 없이도
    전부 시험할 수 있다.

    주지 않으면 상대 기간 질문이 막힌다. 기본값으로 오늘 연도를 쓰지 않는다. 그렇게 하면 같은
    질문이 실행한 날에 따라 다른 해를 가리키고, 그 차이는 결과만 봐서는 드러나지 않는다.
    """
    if not question or not question.strip():
        raise UnsupportedQuestionError(
            reason="empty_question",
            detail="질문이 비어 있습니다.",
        )

    recognized_names = (
        tuple(known_companies) if known_companies else DEFAULT_KNOWN_COMPANIES
    )

    if not recognized_names and not company_universe:
        raise UnsupportedQuestionError(
            reason="missing_company_universe",
            detail="인식할 기업명 목록이 주어지지 않았습니다. manifest에서 읽어 전달해야 합니다.",
        )

    resolver = build_company_resolver(
        recognized_names,
        company_universe=company_universe,
    )

    resolution = _resolve_companies(question, resolver)

    companies = resolution.companies

    # 질문에 실제로 적힌 기업 표기. 계획에는 canonical만 담기지만, '이 낱말은 답의 주체이지
    # 조회 조건이 아니다'를 판정하는 자리들은 사용자가 쓴 표기를 알아야 한다.
    subject_names = resolution.surfaces

    # 의도와 무관한 기간 규칙은 의도 분기보다 먼저 본다.
    #
    # 의도별 builder 안으로 들여보내면 Pattern이 늘 때마다 같은 검사를 다시 붙여야 하고,
    # 한 번 빠뜨린 Pattern만 조용히 검사를 건너뛴다. 모든 Pattern이 Router에
    # 도달하기 전에 같은 기간 규칙을 통과하게 한다.
    _reject_out_of_scope_period(question)

    # 지원하지 않는 상대 기간 표현도 의도 분기보다 먼저 본다. 같은 자리에 두는 이유가 같다.
    #
    # 이 검사가 여기 있어야 "재작년"이 아래 지원 패턴에 걸리지 않은 채로 흘러 내려가
    # missing_year가 되는 일이 없다. 질문에는 기간이 분명히 적혀 있으므로 '연도를 못 읽었다'는
    # 사유는 실제로 일어난 일과 다르다.
    _reject_unsupported_relative_period(question)

    # 지분공시는 보고자와 발행회사 두 곳을 모두 지목한다. 비교 질문이
    # 아니므로 다중 기업 분기보다 먼저 구조를 확정한다.
    if "대량보유" in question:
        if len(companies) != 2:
            raise UnsupportedQuestionError(
                reason="ambiguous_holding_entities",
                detail="지분공시 질문에서 보고자와 발행회사를 하나씩 확정해야 합니다.",
            )
        normalized_question = normalize_company_name(question)
        # 자리는 canonical 이름이 아니라 질문에 실제로 적힌 표현에서 읽는다. canonical로 찾으면
        # 상장명·별칭으로 적은 질문에서 그 이름이 문장에 없어 find가 -1을 돌려주고, 보고자와
        # 발행회사의 순서 판정이 있지도 않은 자리를 기준으로 이뤄진다.
        filer_mention = resolution.first_mention(companies[0])
        issuer_mention = resolution.first_mention(companies[1])
        if filer_mention is None or issuer_mention is None:
            raise UnsupportedQuestionError(
                reason="ambiguous_holding_entities",
                detail="지분공시 질문에서 보고자와 발행회사를 하나씩 확정해야 합니다.",
            )
        filer_end = filer_mention.span[1]
        filing_verb = normalized_question.find("낸", filer_end)
        issuer_start = issuer_mention.span[0]
        if filing_verb < filer_end or issuer_start < filing_verb:
            raise UnsupportedQuestionError(
                reason="ambiguous_holding_entities",
                detail=(
                    "현재 지원하는 지분공시 질문 순서에서 보고자와 발행회사를 "
                    "확정할 수 없습니다."
                ),
            )
        years = _require_years(_extract_years(question))
        if len(years) != 1:
            raise UnsupportedQuestionError(
                reason="unsupported_holding_period_count",
                detail="지분공시 단건 조회는 한 개 연도만 지정해야 합니다.",
            )
        requested = (MetricConcept.HOLDING_SHARES, MetricConcept.HOLDING_RATIO)
        if not all(token in question for token in ("보유주식 수", "보유비율")):
            raise UnsupportedQuestionError(
                reason="unsupported_holding_fields",
                detail="현재는 보유주식 수와 보유비율을 함께 묻는 질문만 지원합니다.",
            )
        return QueryPlan(
            question=question.strip(),
            companies=(companies[1],),
            years=years,
            month=_extract_month(question),
            intent=INTENT_HOLDING_FACT,
            document_class=DOCUMENT_CLASS_HOLDING,
            document_subtype="대량보유상황보고서",
            accounting_scope="not_applicable",
            metric=None,
            requested_fields=requested,
            filer=companies[0],
        )

    # 기업이 둘 이상이면 Pattern D다. 이 판정을 나머지 의도 분기보다 먼저 한다.
    #
    # 뒤로 미루면 단일 기업 흐름(Pattern A·E)이 먼저 기업 하나를 골라 버린다. 그러면 두
    # 기업을 물은 질문에 한 기업의 Fact만 근거까지 갖춰 COMPLETED/ANSWERABLE로 돌아가고,
    # 나머지 기업이 사라진 흔적이 어디에도 남지 않는다.
    if len(companies) > 1:
        return _build_cross_entity_compare_plan(question, companies)

    # 남은 의도를 가른다. 이후 단계는 의도마다 요구하는 것이 다르다.
    if _looks_like_event_history(question):
        return _build_event_history_plan(question, companies, subject_names)

    # 거래소공시 단건 요약(Q06)은 이력 판정보다 뒤, 정기공시 요약 판정보다 앞이다.
    #
    #   이력 판정보다 뒤      "공시 내용 어떻게 바뀌었는지 정리해줘"는 Pattern E가 답하던
    #                        질문이다. 앞에 두면 그 질문을 가로챈다.
    #
    #   정기공시 요약보다 앞  순서가 결과를 바꾸지는 않는다. 두 판정은 서로 배타이며
    #                        (_looks_like_event_summary가 "사업보고서"가 있으면 거짓),
    #                        앞에 두는 것은 요약 판정 셋이 한자리에 모여 서로 다른 질문
    #                        모양을 본다는 것을 드러내기 위해서다.
    #
    #   기간 판정보다 앞      이 질문은 회계연도를 밝히지 않는다. 뒤에 두면 _require_years가
    #                        먼저 missing_year로 막고, 막힌 사유가 실제로 일어난 일과
    #                        달라진다.
    if _looks_like_event_summary(question, companies, subject_names):
        return _build_event_summary_plan(question, companies, subject_names)

    # 요약 요청은 이력 판정보다 뒤, 기간 판정보다 앞이다.
    #
    #   이력 판정보다 뒤   "시설투자 변경 내용 정리해줘"처럼 두 표현이 함께 있는 질문은
    #                      Pattern E가 답하던 것이다. 앞에 두면 그 질문을 가로챈다.
    #
    #   기간 판정보다 앞   요약 질문의 기간 규칙은 시계열 비교의 것과 다르다. 요약은 문서
    #                      1건을 대상으로 하므로 기간이 둘이면 답이 성립하지 않고, 상대 기간의
    #                      기준 연도 정책도 요약 대상 선택까지는 정해진 바 없다. 뒤에 두면
    #                      "2024년이랑 2025년 사업보고서 설비투자 정리해줘"가 시계열 비교로
    #                      분류되어 'Pattern C가 지원하지 않는 지표'로 막힌다. 실제로 일어난
    #                      일은 그것이 아니다.
    # 기업 소개 요약(Q05)은 일반 요약 판정보다 앞이다. 일반 요약 판정은 이 질문을
    # 알아보지 못하므로 순서가 결과를 바꾸지는 않지만, 앞에 두어야 두 판정이 서로 다른
    # 질문 모양을 본다는 것이 자리에서 드러난다.
    if _looks_like_business_profile(question, companies, subject_names):
        return _build_business_profile_plan(question, companies, fiscal_year_anchors)

    if _looks_like_document_summary(question):
        return _build_document_summary_plan(question, companies)

    years = _extract_years(question)

    # 서로 다른 절대연도가 둘 이상이면 Pattern C다.
    #
    # 위치가 규약이다. 세 경계 사이에 있어야 한다.
    #
    #   다중 기업 판정보다 뒤   기업 축과 기간 축이 함께 여럿이면 Pattern D가 먼저다.
    #                          Pattern D 판정이 이미 그 자리에 있고 순서를 바꿀 이유가 없다.
    #
    #   이력 판정보다 뒤       거래소공시 이력 질문은 원공시 연도와 정정 연도를 함께 적을 수
    #                          있다("2024년 8월에 공시한 계약, 2025년에 바뀐 내용 있어?").
    #                          앞에 두면 실제로 답하고 있던 Pattern E 질문을 가로챈다.
    #                          Q08은 이력 표현이 있어도 유형 토큰이 없어 여기까지 내려온다.
    #
    #   지표 판정보다 앞       여기서 거절하면 막힌 이유가 'Pattern C 미구현'이 아니라
    #                          '지표를 모르겠다'로 바뀐다. Pattern D가 같은 이유로 같은
    #                          순서를 택했다.
    #
    # 뒤로 미루면 단일 기간 흐름(Pattern A)이 먼저 연도 하나를 골라 버린다. 그러면 두 기간을
    # 물은 질문에 첫 연도의 Fact만 근거까지 갖춰 COMPLETED/ANSWERABLE로 돌아가고, 나머지
    # 기간이 사라진 흔적이 어디에도 남지 않는다.
    #
    # 절대연도 하나에 상대기간 표현이 붙은 질문도 같은 구멍이다("2025년 매출이 전년보다 얼마나
    # 늘었어?"). 절대연도 개수만 세면 하나이므로 이 가드를 통과해 single_fact로 축소된다.
    # 상대기간을 연도로 옮기지는 않는다. 기간이 둘이라는 사실만 확정하면 막기에 충분하다.
    #
    # 절대연도가 하나도 없고 과거 상대 기간만 쓴 질문(Q07 "매출이 작년보다 얼마나 늘었어?")은
    # 아래에서 따로 다룬다. 그쪽은 기준 연도를 붙여 절대연도 두 개를 만든다.
    relative_expression = _match_previous_year_expression(question)

    if len(years) > 1 or (years and relative_expression is not None):
        # 절대연도와 상대 기간이 함께 쓰인 질문은 옮기지 않는다.
        #
        # "2025년 매출이 전년보다 얼마나 늘었어?"에서 "전년"의 기준이 문장에 적힌 2025인지
        # 기업의 최신 사업보고서 연도인지는 D8이 정하지 않았다. D8이 정한 것은 기준을 밖에서
        # 가져와야 하는 경우 하나뿐이고, 여기는 문장 안에 후보가 이미 있다.
        #
        # 둘 중 하나를 골라 옮기면 고른 쪽이 조용히 답이 된다. 그래서 읽었다는 사실만 계획에
        # 남기고 연도는 비워 둔다. 이 계획은 has_unresolved_relative_period가 참이므로
        # 실행하는 쪽이 years만으로 답하지 않는다.
        return _build_temporal_compare_plan(
            question,
            companies,
            years,
            relative_period=(
                RelativePeriod(
                    expression=relative_expression,
                    offset=PREVIOUS_YEAR_OFFSET,
                )
                if relative_expression is not None
                else None
            ),
        )

    if relative_expression is not None:
        return _build_relative_period_plan(
            question,
            companies,
            expression=relative_expression,
            fiscal_year_anchors=fiscal_year_anchors,
        )

    # 계약 단건 사실 조회(Q02)를 가른다.
    #
    # 위치가 규약이다. 세 경계 사이에 있어야 한다.
    #
    #   이력 판정보다 뒤       '공급계약 금액이 어떻게 바뀌었어?'는 이력 질문이다. 앞에 두면
    #                          실제로 답하고 있던 Pattern E 질문을 가로챈다.
    #
    #   Pattern C·상대기간보다 뒤  기간이 둘인 계약 질문은 기간 축이 먼저다. 앞에 두면 두
    #                          기간을 물은 질문이 단일 계약 조회로 축소된다.
    #
    #   지표 판정보다 앞       계약금액·계약기간은 _extract_metric의 어휘(매출액)에 없다.
    #                          뒤에 두면 이 질문들이 전부 unsupported_metric으로 막히고,
    #                          막힌 이유가 '계약 조회 미지원'이 아니라 '지표를 모르겠다'로
    #                          바뀐다.
    if _looks_like_contract_fact(question):
        return _build_contract_fact_plan(question, companies, years, subject_names)

    _require_years(years)

    metric = _extract_metric(question)

    accounting_scope, scope_explicit = _extract_accounting_scope(question)

    warnings: list[str] = []

    if not scope_explicit:
        warnings.append(
            "질문에 연결/별도 기준이 명시되지 않아 연결 기준으로 해석했습니다."
        )

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        years=years,
        intent=INTENT_SINGLE_FACT,
        document_class=DOCUMENT_CLASS_PERIODIC,
        document_subtype=DOCUMENT_SUBTYPE_ANNUAL,
        accounting_scope=accounting_scope,
        metric=metric,
        accounting_scope_explicit=scope_explicit,
        warnings=tuple(warnings),
    )
