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
- Pattern C 구현 시 상대기간 표현("작년")을 연도로 옮긴다. 지금은 탐지만 하고 해석하지
  않는다. 절대연도와 함께 쓰이면 기간이 둘이므로 temporal_compare로 막고, 상대기간만 있는
  질문은 missing_year로 막힌다. 어느 쪽도 답을 축소하지 않는다.
"""

from __future__ import annotations

import re
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
from disclosure_agent.common.enums import ChainPolicy, Intent, QueryAccountingScope
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
    "DOCUMENT_CLASS_PERIODIC",
    "DOCUMENT_SUBTYPE_ANNUAL",
    "INTENT_CROSS_ENTITY_COMPARE",
    "INTENT_DOCUMENT_SUMMARY",
    "INTENT_EVENT_HISTORY",
    "INTENT_SINGLE_FACT",
    "INTENT_TEMPORAL_COMPARE",
    "INTENT_UNANSWERABLE_CHECK",
    "KNOWN_INTENTS",
    "METRIC_REVENUE",
    "SCOPE_CONSOLIDATED",
    "SCOPE_SEPARATE",
    "QueryPlan",
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

INTENT_DOCUMENT_SUMMARY = "document_summary"
INTENT_TEMPORAL_COMPARE = "temporal_compare"
INTENT_CROSS_ENTITY_COMPARE = "cross_entity_compare"
INTENT_UNANSWERABLE_CHECK = "unanswerable_check"

KNOWN_INTENTS: tuple[str, ...] = (
    INTENT_SINGLE_FACT,
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

# 기준 시점이 있어야 연도로 옮길 수 있는 기간 표현.
#
# 이번 범위는 이 표현을 해석하지 않는다. 탐지만 한다. 절대연도와 함께 쓰이면 질문이 가리키는
# 기간이 둘이므로, 하나로 줄여 답하지 않도록 시계열 비교로 분류하기 위해서다.
#
# 과거 방향만 둔다. "내년"·"올해"도 상대 기간이지만 이번 구멍(절대연도 + 과거 상대기간이
# single_fact로 축소되는 경로)의 원인이 아니고, 미래 방향은 Gate(F4)가 query_analyzer보다
# 앞에서 판정한다. 확인되지 않은 표현을 미리 넣으면 답할 수 있는 질문까지 막는다.
RELATIVE_PERIOD_TOKENS = ("작년", "전년", "지난해")

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


class UnsupportedQuestionError(ValueError):
    """이번 슬라이스가 결정론적으로 처리할 수 없는 질문."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)

        self.reason = reason
        self.detail = detail


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
    # 질문이 월까지 지정한 경우. Q13처럼 원공시를 월 단위로 지목할 때 쓴다.
    month: int | None = None
    # 정정·후속 문서를 어디까지 볼 것인가. 값은 Contract v0.1의 ChainPolicy를 따른다.
    chain_policy: ChainPolicy | None = None
    # accounting_scope가 질문에 명시됐는지 기록한다. 기본값으로 채운 경우를 구분하기 위함이다.
    accounting_scope_explicit: bool = True
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


def _has_relative_period(question: str) -> bool:
    """
    기준 시점이 있어야 연도로 옮길 수 있는 기간 표현이 있는가.

    해석하지 않고 있는지만 본다. 이번 범위는 "작년"을 2024로 바꾸지 않는다. 그 변환은 기준
    시점(as_of) 정책을 먼저 정해야 하고, 정하지 않은 채 오늘 날짜로 대신하면 같은 질문이
    실행 시점에 따라 다른 해를 가리킨다.

    탐지만으로 충분한 이유는, 이 판정이 답을 만들기 위한 것이 아니라 답을 만들지 않기 위한
    것이기 때문이다. 절대연도와 함께 쓰이면 질문이 가리키는 기간이 둘이라는 사실만 확정되면
    되고, 그 둘이 정확히 몇 년인지는 Pattern C를 구현할 때 정한다.
    """
    return any(token in question for token in RELATIVE_PERIOD_TOKENS)


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

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        years=_require_years(_extract_years(question)),
        intent=INTENT_CROSS_ENTITY_COMPARE,
        accounting_scope=accounting_scope,
        metric=metric,
        derived_metric=derived_metric,
        accounting_scope_explicit=scope_explicit,
    )


def _build_temporal_compare_plan(
    question: str,
    companies: tuple[str, ...],
    years: tuple[int, ...],
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
    """
    accounting_scope, scope_explicit = _extract_accounting_scope(question)
    metric, derived_metric = _resolve_requested_metrics(question)

    listed = ", ".join(str(year) for year in years)

    # 상대기간이 섞인 질문은 확보한 절대연도 개수가 곧 기간 개수가 아니다. 개수를 세어 적으면
    # "1개 기간"이라고 말하면서 시계열 비교로 분류한 결과가 되어 진단이 스스로 모순된다.
    if _has_relative_period(question):
        detail = f"질문이 절대연도({listed})와 상대 기간 표현을 함께 쓰고 있어"
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
        accounting_scope_explicit=scope_explicit,
        warnings=(f"{detail} 시계열 비교로 해석했습니다.",),
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
) -> QueryPlan:
    """
    질문 문자열에서 Q01 계열 QueryPlan을 만든다.

    known_companies에 준 목록 안에서만 기업을 인식한다.
    목록이 없으면 기업명을 추측하지 않고 거절한다.
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
    # 절대연도가 하나도 없는 질문(Q07 "매출이 작년보다 얼마나 늘었어?")은 여기서 걸지 않는다.
    # 아래 _require_years가 missing_year로 막으므로 이미 fail-closed이고, 그 질문에 답하려면
    # 상대기간 해석이 필요한데 그것은 Pattern C 구현 범위다.
    if len(years) > 1 or (years and _has_relative_period(question)):
        return _build_temporal_compare_plan(question, companies, years)

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
