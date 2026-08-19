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

TODO:
- 계산형/비교형 질의 의도 추가
- 분기·반기 보고서 대상 기간 표현 확장
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
    호환 때문이며, 이 슬라이스가 만드는 계획은 모두 single_fact다.

    companies[]·periods[] 같은 다중 값 표현으로 넓히는 작업은 팀원 3의 Data Contract가
    확인된 뒤에 한다. 지금 확인되지 않은 어휘로 구조를 바꾸면 되돌릴 비용만 커진다.
    """

    question: str
    company: str
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_year(question: str) -> int:
    match = YEAR_PATTERN.search(question)

    if not match:
        raise UnsupportedQuestionError(
            reason="missing_year",
            detail="질문에서 대상 연도를 찾을 수 없습니다.",
        )

    return int(match.group(0))


def _extract_company(question: str, known_companies: tuple[str, ...]) -> str:
    """
    질문 안에서 알려진 회사명을 찾는다.

    회사명을 추측하지 않고 주어진 목록과의 매칭만 사용한다.
    긴 이름이 짧은 이름을 포함할 수 있으므로(예: 한미약품 / 한미) 긴 것부터 확인한다.
    """
    normalized_question = normalize_company_name(question)

    matches = []

    for company in sorted(known_companies, key=len, reverse=True):
        normalized_company = normalize_company_name(company)

        if normalized_company and normalized_company in normalized_question:
            matches.append(company)

    if not matches:
        raise UnsupportedQuestionError(
            reason="unknown_company",
            detail="질문에서 알려진 기업명을 찾을 수 없습니다.",
        )

    return matches[0]


def _extract_accounting_scope(question: str) -> tuple[str, bool]:
    lowered = question.casefold()

    if any(token in lowered for token in CONSOLIDATED_TOKENS):
        return SCOPE_CONSOLIDATED, True

    if any(token in lowered for token in SEPARATE_TOKENS):
        return SCOPE_SEPARATE, True

    # 명시가 없으면 연결 기준을 기본값으로 쓰되 명시 여부를 남긴다.
    return SCOPE_CONSOLIDATED, False


def _extract_metric(question: str) -> str:
    if any(token in question for token in REVENUE_TOKENS):
        return METRIC_REVENUE

    raise UnsupportedQuestionError(
        reason="unsupported_metric",
        detail="이번 범위에서 지원하는 지표(매출액)를 질문에서 찾을 수 없습니다.",
    )


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


def _build_event_history_plan(question: str, company: str) -> QueryPlan:
    """
    Q13 계열 정정 이력 조회 계획을 만든다.

    chain_policy는 full_chain으로 고정한다. 이력을 묻는 질문에서 정정본을 빼면 답할 것이
    남지 않는다. 다른 정책이 필요한 질문 형태는 이번 범위 밖이다.
    """
    year = _extract_year(question)

    return QueryPlan(
        question=question.strip(),
        company=company,
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

    companies = tuple(known_companies) if known_companies else DEFAULT_KNOWN_COMPANIES

    if not companies:
        raise UnsupportedQuestionError(
            reason="missing_company_universe",
            detail="인식할 기업명 목록이 주어지지 않았습니다. manifest에서 읽어 전달해야 합니다.",
        )

    company = _extract_company(question, companies)

    # 의도를 먼저 가른다. 이후 단계는 의도마다 요구하는 것이 다르다.
    if _looks_like_event_history(question):
        return _build_event_history_plan(question, company)

    _reject_out_of_scope_period(question)

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
        company=company,
        year=year,
        intent=INTENT_SINGLE_FACT,
        document_class=DOCUMENT_CLASS_PERIODIC,
        document_subtype=DOCUMENT_SUBTYPE_ANNUAL,
        accounting_scope=accounting_scope,
        metric=metric,
        accounting_scope_explicit=scope_explicit,
        warnings=tuple(warnings),
    )
