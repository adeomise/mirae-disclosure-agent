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

MONTH_PATTERN = re.compile(r"(1[0-2]|0?[1-9])\s*월")

# 거래소공시 세부유형. Q13 계열이 다루는 것만 둔다.
# 값은 manifest의 doc_subtype 표기와 같아야 한다.
EVENT_SUBTYPE_SUPPLY_CONTRACT = "단일판매공급계약체결"

EVENT_SUBTYPE_TOKENS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("공급계약", "수주계약", "판매계약"), EVENT_SUBTYPE_SUPPLY_CONTRACT),
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
    metric: str | None = METRIC_REVENUE
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

        if not self.years:
            raise ValueError("years는 최소 한 개의 연도를 담아야 합니다.")

    @property
    def year(self) -> int:
        """
        단일 기간 질문의 대상 연도.

        **다중 기간 계획에서는 쓰지 않는다. years를 읽어야 한다.**

        정본이 아니다. years에서 파생되는 읽기 전용 값이며, 단일 기간 Pattern(A·E)의 기존
        호출부를 위해 남긴 호환 통로다. 값을 따로 저장하지 않으므로 years와 어긋날 수 없고,
        기간 정보를 읽어야 하는 새 코드는 이 통로가 아니라 years를 본다.

        company와 같은 자리이지만 한 가지가 다르다. company는 companies[0]을 그냥
        돌려주는 반면, 이 값은 기간이 둘 이상이면 돌려주지 않고 막는다.

        그 차이의 이유:
            여러 기업 중 하나를 고르는 일은 Router가 routing 단계에서 막으므로 company를
            읽는 handler에 애초에 도달하지 않는다. 기간도 지금은 같지만, Pattern C handler가
            생기는 순간 다중 기간 계획이 handler 안까지 들어온다. 그때 이 값이 첫 연도를
            돌려주면 "2024년 매출과 2025년 매출 비교해줘"에 2024년 값 하나만 답하는 결과가
            되고, 그것이 C-0에서 막은 silent-wrong 그 자체다. 한 줄 실수로 되살아나지 않도록
            통로 쪽에서 막는다.

        ValueError를 쓰고 AttributeError를 쓰지 않는 이유:
            AttributeError는 hasattr()와 getattr(plan, "year", 기본값)이 조용히 삼킨다.
            값을 고르지 않으려고 만든 가드가 그렇게 삼켜지면 가드가 없는 것과 같아진다.
            같은 dataclass의 __post_init__이 이미 잘못된 생성을 ValueError로 막고 있으므로
            어휘도 그쪽에 맞춘다.
        """
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


def _extract_event_subtype(question: str) -> str:
    """
    질문에서 거래소공시 세부유형을 찾는다.

    이번 슬라이스는 공급계약만 다룬다. 모르는 유형을 조용히 넘기지 않고 거절한다.
    """
    for tokens, subtype in EVENT_SUBTYPE_TOKENS:
        if any(token in question for token in tokens):
            return subtype

    raise UnsupportedQuestionError(
        reason="unsupported_event_subtype",
        detail="이번 범위에서 지원하는 거래소공시 유형(공급계약)을 질문에서 찾을 수 없습니다.",
    )


def _looks_like_event_history(question: str) -> bool:
    """
    변경 이력을 묻는 거래소공시 질문인가.

    유형 표현과 이력 표현이 함께 있어야 한다. 둘 중 하나만으로 판정하면 '공급계약 금액이
    얼마야'(단일 사실)까지 이력 조회로 끌려간다.
    """
    has_event_subtype = any(
        token in question for tokens, _ in EVENT_SUBTYPE_TOKENS for token in tokens
    )

    has_history = any(token in question for token in HISTORY_TOKENS)

    return has_event_subtype and has_history


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
    """
    accounting_scope, scope_explicit = _extract_accounting_scope(question)

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        years=_require_years(_extract_years(question)),
        intent=INTENT_CROSS_ENTITY_COMPARE,
        accounting_scope=accounting_scope,
        metric=_find_metric(question),
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
    풀리는 질문인지가 결과에서 사라진다. Q08("영업이익률이 2024년이랑 2025년에 어떻게
    달라졌어?")이 그 경우다.

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
        metric=_find_metric(question),
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
    """
    anchor, *_ = _require_years(_extract_years(question))

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        years=(anchor,),
        intent=INTENT_EVENT_HISTORY,
        document_class=DOCUMENT_CLASS_EVENT,
        document_subtype=_extract_event_subtype(question),
        # 거래소공시에는 연결·별도 구분이 없다.
        accounting_scope=QueryAccountingScope.NOT_APPLICABLE.value,
        metric=None,
        month=_extract_month(question),
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
