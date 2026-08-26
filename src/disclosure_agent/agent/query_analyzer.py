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
- Pattern C 구현 시 상대기간 표현("작년")을 해석한다. 지금은 절대연도만 읽으므로 상대기간
  질문은 missing_year로 막힌다. 답을 축소하지는 않으므로 이번 범위에서 다루지 않는다.
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

YEAR_PATTERN = re.compile(r"(19|20)\d{2}")

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

    periods[] 같은 나머지 다중 값 표현으로 넓히는 작업은 팀원 3의 Data Contract가 확인된
    뒤에 한다. 지금 확인되지 않은 어휘로 구조를 바꾸면 되돌릴 비용만 커진다.
    """

    question: str
    companies: tuple[str, ...]
    year: int
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

        if not self.companies:
            raise ValueError("companies는 최소 한 곳의 기업을 담아야 합니다.")

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
        # company는 담지 않는다. companies에서 파생되는 값을 함께 실으면 읽는 쪽이
        # 둘 중 무엇이 정본인지 알 수 없게 된다.
        return asdict(self)


def _extract_years(question: str) -> tuple[int, ...]:
    """
    질문에 쓰인 절대연도를 전부 찾는다. 없으면 빈 튜플이며 추측하지 않는다.

    첫 매치 하나만 돌려주지 않는다. 두 기간을 묻는 질문에서 하나만 고르면 나머지 기간이
    사라진 채로 답이 조립되고, 사라졌다는 사실이 warnings에도 결과에도 남지 않는다.
    _extract_companies가 기업에 대해 하는 일과 같다.

    같은 연도가 문장에 여러 번 나오는 것은 두 기간이 아니다("2024년 매출과 2024년 영업이익").
    그래서 값으로 중복을 제거한다. 돌아오는 순서는 질문에 나타난 순서다.

    상대기간 표현("작년")은 읽지 않는다. 해석하려면 기준 시점을 정해야 하고 그것은 이번
    범위 밖이다. 읽지 못한 결과는 missing_year이므로 답이 축소되지는 않는다.
    """
    seen: dict[int, None] = {}

    for match in YEAR_PATTERN.finditer(question):
        seen.setdefault(int(match.group(0)), None)

    return tuple(seen)


def _extract_year(question: str) -> int:
    """
    질문이 가리키는 연도 하나. 단일 기간 Pattern(A·E)이 쓴다.

    연도가 둘 이상인 질문은 build_query_plan이 이 함수에 닿기 전에 temporal_compare로
    갈라내므로, 여기까지 온 질문의 연도는 하나다.
    """
    years = _extract_years(question)

    if not years:
        raise UnsupportedQuestionError(
            reason="missing_year",
            detail="질문에서 대상 연도를 찾을 수 없습니다.",
        )

    return years[0]


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
        year=_extract_year(question),
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

    year에 첫 연도를 담는 것은 QueryPlan이 아직 스칼라 year를 요구하기 때문이며, 이것은
    다중 기간의 정본 표현이 아니다. 정본 표현(periods[])은 Data Contract 확인 뒤에 정한다
    (위 QueryPlan docstring 참조). 그래서 확보한 연도 전부는 warnings에 남긴다. 계획 구조에
    담을 자리가 없더라도 어느 연도를 읽었는지가 결과에서 사라지면 안 된다.
    """
    accounting_scope, scope_explicit = _extract_accounting_scope(question)

    listed = ", ".join(str(year) for year in years)

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        year=years[0],
        intent=INTENT_TEMPORAL_COMPARE,
        accounting_scope=accounting_scope,
        metric=_find_metric(question),
        accounting_scope_explicit=scope_explicit,
        warnings=(
            f"질문이 {len(years)}개 기간({listed})을 함께 묻고 있어 시계열 비교로 "
            "해석했습니다.",
        ),
    )


def _build_event_history_plan(question: str, companies: tuple[str, ...]) -> QueryPlan:
    """
    Q13 계열 정정 이력 조회 계획을 만든다.

    chain_policy는 full_chain으로 고정한다. 이력을 묻는 질문에서 정정본을 빼면 답할 것이
    남지 않는다. 다른 정책이 필요한 질문 형태는 이번 범위 밖이다.
    """
    year = _extract_year(question)

    return QueryPlan(
        question=question.strip(),
        companies=companies,
        year=year,
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
    if len(years) > 1:
        return _build_temporal_compare_plan(question, companies, years)

    year = _extract_year(question)

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
        year=year,
        intent=INTENT_SINGLE_FACT,
        document_class=DOCUMENT_CLASS_PERIODIC,
        document_subtype=DOCUMENT_SUBTYPE_ANNUAL,
        accounting_scope=accounting_scope,
        metric=metric,
        accounting_scope_explicit=scope_explicit,
        warnings=tuple(warnings),
    )
