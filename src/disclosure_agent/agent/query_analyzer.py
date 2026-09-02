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

- 여러 기업을 함께 묻는 질문은 기업 하나로 줄여서 답하지 않는다. cross_entity_compare로
  분류해 Router가 routing 단계에서 막게 한다. Pattern D handler는 아직 없다.

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
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
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
from disclosure_agent.agent.derived_metrics import DerivedMetric
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
    DOCUMENT_CLASS_PERIODIC,
    normalize_company_name,
)

__all__ = [
    "ANCHOR_SOURCE_LATEST_ANNUAL_FILING",
    "DOCUMENT_CLASS_PERIODIC",
    "DOCUMENT_SUBTYPE_ANNUAL",
    "INTENT_CONTRACT_FACT",
    "INTENT_CROSS_ENTITY_COMPARE",
    "INTENT_DOCUMENT_SUMMARY",
    "INTENT_EVENT_HISTORY",
    "INTENT_SINGLE_FACT",
    "INTENT_TEMPORAL_COMPARE",
    "INTENT_UNANSWERABLE_CHECK",
    "KNOWN_INTENTS",
    "METRIC_REVENUE",
    "PREVIOUS_YEAR_EXPRESSIONS",
    "PREVIOUS_YEAR_OFFSET",
    "SCOPE_CONSOLIDATED",
    "SCOPE_SEPARATE",
    "SECTION_SCOPE_TOKENS",
    "SUMMARY_REQUEST_TOKENS",
    "SUPPORTED_CONTRACT_FIELDS",
    "QueryPlan",
    "RelativePeriod",
    "UnsupportedQuestionError",
    "build_query_plan",
    "normalize_company_name",
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

INTENT_DOCUMENT_SUMMARY = "document_summary"
INTENT_TEMPORAL_COMPARE = "temporal_compare"
INTENT_CROSS_ENTITY_COMPARE = "cross_entity_compare"
INTENT_UNANSWERABLE_CHECK = "unanswerable_check"

KNOWN_INTENTS: tuple[str, ...] = (
    INTENT_SINGLE_FACT,
    INTENT_CONTRACT_FACT,
    INTENT_DOCUMENT_SUMMARY,
    INTENT_TEMPORAL_COMPARE,
    INTENT_CROSS_ENTITY_COMPARE,
    INTENT_EVENT_HISTORY,
    INTENT_UNANSWERABLE_CHECK,
)

# 인식 후보 기업명은 호출자가 넘긴다. service는 manifest에서 읽어 전달한다.
# 특정 기업명을 코드에 넣어 두지 않는다. 그것도 결국 추측이고, 목록에 없는 기업을
# 조용히 인식하거나 못 하는 차이를 만든다.
DEFAULT_KNOWN_COMPANIES: tuple[str, ...] = ()

# 절대연도. '년'이 붙은 표기만 연도로 읽는다.
#
# 네 자리 숫자를 그대로 연도로 보면 금액이 연도가 된다. "2024년 매출이 2025억원 맞아?"의
# 2025는 기간이 아닌데, 그것을 두 번째 연도로 세면 답할 수 있는 질문이 시계열 비교로
# 오분류되어 막힌다. 막는 쪽으로 틀리는 것도 틀리는 것이다.
#
# '년'을 요구해도 기존 입력을 깨지 않는다. 대표 질문 18문항과 테스트·회귀 케이스의 질문
# 문자열은 전부 'YYYY년' 표기이며, '년' 없는 네 자리 숫자는 보고서명("사업보고서 (2025.12)")
# 이나 공시일처럼 질문이 아닌 자리에만 나온다.
#
# 앞자리 숫자를 배제해 더 긴 숫자의 일부를 연도로 읽지 않는다. 뒤에 오는 조사는 보지 않는다
# ("2024년이랑", "2024년과", "2024년에").
YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?=\s*년)")

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

    requires_filing_anchor
        원공시를 지목하려면 질문이 공시 시점(연도)을 밝혀야 하는가.

        공급계약은 한 기업이 해마다 여러 건을 공시하므로 시점이 없으면 어느 계약인지 정할 수
        없다. 그래서 True다. 이 값이 Q14("포드랑 맺었던 배터리 공급계약")를 계속 막는다.
        상대방으로 계약을 특정하려면 본문 대조가 필요하고 그것은 이번 범위 밖이다.

        신규시설투자는 원공시가 유일하지 않을 수 있지만, 유일하지 않을 때 handler가
        ambiguous_root로 막는다(agent.handlers.event_history). 시점 없이도 후보를 좁혀 볼 수
        있고 좁히지 못하면 답하지 않으므로 False로 둔다. 값을 고르는 자리가 생기지 않는다.
    """

    subtype: str
    tokens: tuple[str, ...] = ()
    target_tokens: tuple[str, ...] = ()
    action_tokens: tuple[str, ...] = ()
    requires_filing_anchor: bool = True

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
        requires_filing_anchor=True,
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
        requires_filing_anchor=False,
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
# 거래소공시 단건 사실 조회(Q02)의 어휘
# --------------------------------------------------------------------------

# 계약 사실 조회(Q02)가 요구하는 문서 필드 표기.
#
# 값이 MetricConcept인 이유:
#     event.adapter의 CONTRACT_FIELD_BY_METRIC이 같은 어휘로 문서 필드를 찾는다. 여기서
#     문자열을 따로 쓰면 같은 개념에 두 벌의 이름이 생기고, 한쪽이 바뀔 때 조용히 끊어진다.
#
# 맨 낱말("금액", "기간")을 함께 두는 이유:
#     "계약 금액과 기간은?"처럼 앞의 '계약'을 한 번만 쓰는 표현이 실제로 온다. 다만 이
#     낱말들만으로는 판정하지 않는다. _looks_like_contract_fact가 질문에 '계약'이 있는지를
#     먼저 보므로, 매출 질문의 '금액'이 이 경로로 끌려오지 않는다.
CONTRACT_AMOUNT_TOKENS = ("계약금액", "계약 금액", "금액")
CONTRACT_PERIOD_TOKENS = ("계약기간", "계약 기간", "기간")

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
    document_subtype: str = DOCUMENT_SUBTYPE_ANNUAL
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

        if not self.years and not self._allows_absent_years():
            raise ValueError(
                "years는 최소 한 개의 연도를 담아야 합니다: "
                f"intent={self.intent!r}, chain_policy={self.chain_policy!r}."
            )

        # 단일 문서 요약 계획은 범위 없이 만들 수 없다.
        #
        # 요약 범위는 이 Pattern의 답 그 자체다. 범위가 비면 이 계획을 실행하는 쪽이 어디를
        # 요약할지 골라야 하고, 무엇을 골랐는지는 계획에도 결과에도 남지 않는다.
        if self.intent == INTENT_DOCUMENT_SUMMARY and self.section_scope is None:
            raise ValueError(
                "단일 문서 요약 계획에는 section_scope가 있어야 합니다: "
                f"question={self.question!r}."
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

        event_history + full_chain 하나만이다. 이 조합에서 연도는 비교 대상 기간이 아니라
        원공시를 지목하는 공시일 기준점이고, 기준점 없이도 기업과 공시 유형만으로 체인을
        찾아볼 수 있다. 찾은 원공시가 여럿이면 handler가 ambiguous_root로 막으므로 값을
        고르는 자리가 생기지 않는다.

        intent별로 나누는 이유:
            single_fact·temporal_compare·cross_entity_compare에서 연도는 답의 일부다. 그쪽에서
            연도가 비면 어느 해를 답했는지가 결과 어디에도 남지 않는다. 전역으로 느슨하게 하면
            Q07("매출이 작년보다 얼마나 늘었어?")처럼 지금 missing_year로 막히는 질문들이
            연도 없이 흘러 들어간다.

        어떤 공시 유형이 기준점 없이 열려도 되는가는 여기서 정하지 않는다.
        EventSubtypeRule.requires_filing_anchor가 정하며, 이 계획을 만드는
        _build_event_history_plan이 그 값을 보고 _require_years를 부를지 말지 고른다.
        두 곳에 같은 규칙을 두면 어긋날 수 있어 한쪽만 정본으로 둔다.
        """
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
    _extract_companies가 기업에 대해 하는 일과 같다.

    같은 연도가 문장에 여러 번 나오는 것은 두 기간이 아니다("2024년 매출과 2024년 영업이익").
    그래서 값으로 중복을 제거한다. 돌아오는 순서는 질문에 나타난 순서다.

    상대기간 표현("작년")은 여기서 읽지 않는다. 해석하려면 기준 시점을 정해야 하고 그것은
    이번 범위 밖이다. 탐지는 _has_relative_period가 따로 맡는다.

    '년'이 붙지 않은 네 자리 숫자는 연도로 보지 않는다(YEAR_PATTERN 주석 참조).
    """
    seen: dict[int, None] = {}

    for match in YEAR_PATTERN.finditer(question):
        seen.setdefault(int(match.group(0)), None)

    return tuple(seen)


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


def _find_unclaimed_span(
    haystack: str,
    needle: str,
    claimed: list[tuple[int, int]],
) -> tuple[int, int] | None:
    """
    이미 다른 회사명이 차지한 구간과 겹치지 않는 첫 출현 위치를 찾는다. 없으면 None.

    이 검사가 다중 기업 판정의 false positive를 막는 자리다. 단순 포함 검사만 하면
    'LG생활건강'을 물은 질문에서 'LG'가 또 매치되어 한 기업이 두 기업으로 세어진다.
    """
    start = haystack.find(needle)

    while start != -1:
        end = start + len(needle)

        overlaps = any(
            claim_start < end and start < claim_end for claim_start, claim_end in claimed
        )

        if not overlaps:
            return start, end

        start = haystack.find(needle, start + 1)

    return None


def _extract_companies(question: str, known_companies: tuple[str, ...]) -> tuple[str, ...]:
    """
    질문 안에서 알려진 회사명을 전부 찾는다.

    회사명을 추측하지 않고 주어진 목록과의 매칭만 사용한다.

    첫 매치 하나만 돌려주지 않는다. 두 기업을 묻는 질문에서 하나만 고르면 나머지 기업이
    사라진 채로 답이 조립되고, 사라졌다는 사실이 warnings에도 결과에도 남지 않는다.

    긴 이름부터 배정하고 이미 배정된 구간은 다시 쓰지 않는다. 이름이 서로를 포함하는 경우
    (예: 한미약품 / 한미)에 한 기업이 둘로 세어지지 않게 하기 위함이다. 조사나 구분자는
    보지 않는다. 실제로 목록에 있는 회사명이 서로 다른 자리에서 매치될 때만 여러 기업이다.

    돌아오는 순서는 질문에 나타난 순서다.
    """
    normalized_question = normalize_company_name(question)

    # 정규화한 이름의 길이로 정렬한다. 비교에 쓰는 것이 정규화한 문자열이므로, 원본 표기
    # 길이로 정렬하면 '(주)' 같은 표기 차이 때문에 포함 관계가 어긋나게 풀린다.
    ordered = sorted(
        known_companies,
        key=lambda name: len(normalize_company_name(name)),
        reverse=True,
    )

    claimed: list[tuple[int, int]] = []
    seen: set[str] = set()
    matches: list[tuple[int, str]] = []

    for company in ordered:
        normalized_company = normalize_company_name(company)

        # 같은 기업이 표기만 달리해 목록에 여러 번 들어 있을 수 있다. 그것은 두 기업이 아니다.
        if not normalized_company or normalized_company in seen:
            continue

        span = _find_unclaimed_span(normalized_question, normalized_company, claimed)

        if span is None:
            continue

        claimed.append(span)
        seen.add(normalized_company)
        matches.append((span[0], company))

    if not matches:
        raise UnsupportedQuestionError(
            reason="unknown_company",
            detail="질문에서 알려진 기업명을 찾을 수 없습니다.",
        )

    matches.sort()

    return tuple(company for _, company in matches)


def _extract_accounting_scope(question: str) -> tuple[str, bool]:
    lowered = question.casefold()

    if any(token in lowered for token in CONSOLIDATED_TOKENS):
        return SCOPE_CONSOLIDATED, True

    if any(token in lowered for token in SEPARATE_TOKENS):
        return SCOPE_SEPARATE, True

    # 명시가 없으면 연결 기준을 기본값으로 쓰되 명시 여부를 남긴다.
    return SCOPE_CONSOLIDATED, False


def _find_metric(question: str) -> str | None:
    """질문에서 지표를 읽는다. 없으면 None이며 추측하지 않는다."""
    if any(token in question for token in REVENUE_TOKENS):
        return METRIC_REVENUE

    return None


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
    """
    found = _find_derived_metric(question)

    if found is None:
        return _find_metric(question), None

    derived, token = found
    source = _find_metric(question.replace(token, " "))

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
    채로 계획이 만들어지고, 사라졌다는 사실이 어디에도 남지 않는다. _extract_companies가
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

    세부유형 문자열이 아니라 규칙을 돌려주는 이유는, 호출부가 유형 이름뿐 아니라 그 유형이
    공시 시점을 요구하는지(requires_filing_anchor)까지 알아야 하기 때문이다. 두 값을 각각
    다시 조회하면 같은 질문을 두 번 해석하게 되고, 어휘가 바뀔 때 한쪽만 고쳐질 수 있다.
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

    이력 표현을 함께 요구하는 것이 Q12를 막는 두 번째 가드다. Q12("...새로 발표한 시설투자,
    회사별로 다 합치면 어디가 더 커?")는 유형 표현을 가지고 있지만 이력 표현이 없어 여기서
    걸린다. 첫 번째 가드는 build_query_plan의 다중 기업 판정이며, 그쪽이 먼저 실행된다.

    유형이 둘 이상 맞는 질문도 여기서는 통과시킨다. 막는 자리는 계획을 만드는
    _extract_event_subtype_rule이며, 그래야 ambiguous_event_subtype으로 막힌다. 여기서
    걸러 내면 그 질문이 단일 사실 경로로 흘러 '지표를 모르겠다'로 막히고, 실제 원인인
    유형 모호성이 결과에서 사라진다.
    """
    return bool(_find_event_subtype_rules(question)) and any(
        token in question for token in HISTORY_TOKENS
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


def _find_contract_fields(question: str) -> tuple[MetricConcept, ...]:
    """
    질문이 요구한 계약 문서 필드를 읽는다. 없으면 빈 튜플이며 추측하지 않는다.

    계약기간이 두 필드가 되는 이유:
        기간은 시작일과 종료일이 함께 있어야 서술된다. 한쪽만 담으면 "2025-09-16부터"까지만
        답하고 언제 끝나는지를 물었다는 사실이 결과 어디에도 남지 않는다.

    돌아오는 차례는 이 함수가 고정한다. 질문에 적힌 차례를 따르지 않는 이유는, 같은 질문을
    다르게 쓴 두 표현이 서로 다른 계획을 만들면 그 차이가 답의 차이로 새어 나가기 때문이다.
    """
    fields: list[MetricConcept] = []

    if any(token in question for token in CONTRACT_AMOUNT_TOKENS):
        fields.append(MetricConcept.CONTRACT_AMOUNT)

    if any(token in question for token in CONTRACT_PERIOD_TOKENS):
        fields.append(MetricConcept.CONTRACT_START_DATE)
        fields.append(MetricConcept.CONTRACT_END_DATE)

    return tuple(fields)


def _extract_counterparty(question: str, company: str) -> str | None:
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

    if normalize_company_name(found) == normalize_company_name(company):
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
) -> QueryPlan:
    """
    Q02 계열 공급계약 단건 사실 조회 계획을 만든다.

    세부유형을 공급계약으로 고정하는 이유:
        계약금액·계약기간은 공급계약 공시의 어휘다. 다만 질문이 다른 유형을 함께 가리키면
        고르지 않고 막는다. 시설투자 공시에도 금액과 기간이 적히므로, 한쪽을 골라 담으면
        다른 유형을 물었다는 사실이 계획에서 사라진다.

    연도를 요구하는 이유:
        한 기업이 해마다 여러 건의 공급계약을 공시한다. 시점이 없으면 어느 계약인지 정할 수
        없고, 상대방만으로 좁히는 경로는 아직 없다. EventSubtypeRule.requires_filing_anchor가
        공급계약에 대해 True인 것과 같은 판단이다.

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
        counterparty=_extract_counterparty(question, companies[0]),
        month=_extract_month(question),
    )


def _build_cross_entity_compare_plan(
    question: str,
    companies: tuple[str, ...],
) -> QueryPlan:
    """
    여러 기업을 함께 묻는 질문의 계획을 만든다.

    Pattern D handler는 아직 없다. 그런데도 계획을 만들어 두는 이유는, Router가 routing
    단계에서 '설계에는 있으나 아직 구현되지 않았다'를 구체적인 이유로 답할 수 있어야 하기
    때문이다. intent를 붙이지 않고 single_fact로 흘려보내면 기업 하나만 남은 답이 Fact와
    Evidence까지 갖춘 채로 ANSWERABLE로 돌아간다. 미지원보다 그쪽이 위험하다.

    지표와 회계기준은 느슨하게 읽는다. 이 계획을 실행할 handler가 없으므로 여기서 거절하면
    막힌 이유가 'Pattern D 미구현'이 아니라 '지표를 모르겠다'로 바뀌어, 무엇을 구현하면
    풀리는 질문인지가 결과에서 사라진다.

    한 가지만 예외다. 원천 지표와 파생지표를 함께 물은 질문은 여기서 막는다. 그것은 지표를
    모르는 상태가 아니라 한쪽을 골라야만 답이 되는 상태이고, 골라 담으면 나머지를 물었다는
    사실이 사라진다(_resolve_requested_metrics 참조).

    Q11("영업이익률이 더 높은 곳은 어디야?")의 파생 개념은 Pattern C와 같은 대응표에서 읽는다.
    비교 축이 기업인지 기간인지는 "영업이익률이 무엇에서 나오는가"와 무관하다.
    """
    accounting_scope, scope_explicit = _extract_accounting_scope(question)
    metric, derived_metric = _resolve_requested_metrics(question)

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
        accounting_scope=accounting_scope,
        metric=metric,
        derived_metric=derived_metric,
        accounting_scope_explicit=scope_explicit,
        relative_period=(
            RelativePeriod(
                expression=relative_expression,
                offset=PREVIOUS_YEAR_OFFSET,
            )
            if relative_expression is not None
            else None
        ),
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


def _build_event_history_plan(question: str, companies: tuple[str, ...]) -> QueryPlan:
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
        Q15("레인보우로보틱스 사옥·제조시설 신축 투자, 그동안 계획이 몇 번 바뀌었어?")처럼
        공시 시점을 밝히지 않는 이력 질문이 있다. 그때 연도를 요구할지는 공시 유형이 정한다
        (EventSubtypeRule.requires_filing_anchor).

        요구하는 유형(공급계약)에서는 예전과 똑같이 missing_year로 막는다. 한 기업이 해마다
        여러 건을 공시하므로 시점이 없으면 어느 계약인지 정할 수 없고, 상대방 이름으로
        좁히려면 본문 대조가 필요하다. Q14가 이 자리에서 계속 막힌다.

        요구하지 않는 유형(신규시설투자)에서는 years를 비운 계획을 만든다. handler가 공시일
        창 없이 원공시 후보를 찾고, 후보가 여럿이면 ambiguous_root로 막는다. 계획도 handler도
        후보 하나를 고르지 않는다.
    """
    rule = _extract_event_subtype_rule(question)

    years = _extract_years(question)

    month = _extract_month(question)

    # 월만 있고 연도가 없으면 공시일 창을 만들 수 없다. 그대로 두면 질문이 밝힌 '4월'이
    # 계획에는 담기지만 조회에는 쓰이지 않아, 사용자가 좁힌 범위가 조용히 사라진다.
    # 유형이 기준점을 요구하지 않더라도 이 경우는 연도를 요구한다.
    if rule.requires_filing_anchor or month is not None:
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


def build_query_plan(
    question: str,
    known_companies: tuple[str, ...] | list[str] | None = None,
    *,
    fiscal_year_anchors: Mapping[str, int] | None = None,
) -> QueryPlan:
    """
    질문 문자열에서 Q01 계열 QueryPlan을 만든다.

    known_companies에 준 목록 안에서만 기업을 인식한다.
    목록이 없으면 기업명을 추측하지 않고 거절한다.

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

    company_universe = (
        tuple(known_companies) if known_companies else DEFAULT_KNOWN_COMPANIES
    )

    if not company_universe:
        raise UnsupportedQuestionError(
            reason="missing_company_universe",
            detail="인식할 기업명 목록이 주어지지 않았습니다. manifest에서 읽어 전달해야 합니다.",
        )

    companies = _extract_companies(question, company_universe)

    # 의도와 무관한 기간 규칙은 의도 분기보다 먼저 본다.
    #
    # 의도별 builder 안으로 들여보내면 Pattern이 늘 때마다 같은 검사를 다시 붙여야 하고,
    # 한 번 빠뜨린 Pattern만 조용히 검사를 건너뛴다. 지금은 Pattern D handler가 없어
    # routing에서 막히지만, handler가 생기는 순간 그 경로가 기간 규칙을 우회하게 된다.
    _reject_out_of_scope_period(question)

    # 지원하지 않는 상대 기간 표현도 의도 분기보다 먼저 본다. 같은 자리에 두는 이유가 같다.
    #
    # 이 검사가 여기 있어야 "재작년"이 아래 지원 패턴에 걸리지 않은 채로 흘러 내려가
    # missing_year가 되는 일이 없다. 질문에는 기간이 분명히 적혀 있으므로 '연도를 못 읽었다'는
    # 사유는 실제로 일어난 일과 다르다.
    _reject_unsupported_relative_period(question)

    # 기업이 둘 이상이면 Pattern D다. 이 판정을 나머지 의도 분기보다 먼저 한다.
    #
    # 뒤로 미루면 단일 기업 흐름(Pattern A·E)이 먼저 기업 하나를 골라 버린다. 그러면 두
    # 기업을 물은 질문에 한 기업의 Fact만 근거까지 갖춰 COMPLETED/ANSWERABLE로 돌아가고,
    # 나머지 기업이 사라진 흔적이 어디에도 남지 않는다.
    if len(companies) > 1:
        return _build_cross_entity_compare_plan(question, companies)

    # 남은 의도를 가른다. 이후 단계는 의도마다 요구하는 것이 다르다.
    if _looks_like_event_history(question):
        return _build_event_history_plan(question, companies)

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
        return _build_contract_fact_plan(question, companies, years)

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
