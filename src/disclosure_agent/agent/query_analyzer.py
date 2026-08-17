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
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

# 이번 슬라이스가 다루는 고정 값. common/enums.py는 팀 합의 전이므로 여기서 국소 정의한다.
DOCUMENT_CLASS_PERIODIC = "periodic"
DOCUMENT_SUBTYPE_ANNUAL = "annual"

SCOPE_CONSOLIDATED = "consolidated"
SCOPE_SEPARATE = "separate"

METRIC_REVENUE = "revenue"

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

# 법인격 표기는 회사명 비교 시 제거한다.
LEGAL_SUFFIX_PATTERN = re.compile(
    r"\((?:주|유|재|사|합)\)|주식회사|유한회사|유한책임회사|합자회사|합명회사"
)

NON_NAME_PATTERN = re.compile(r"[\s ·ㆍ・,\.\-_/]+")


class UnsupportedQuestionError(ValueError):
    """이번 슬라이스가 결정론적으로 처리할 수 없는 질문."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)

        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class QueryPlan:
    """Q01 계열 단일 수치 조회를 위한 결정론적 실행 계획."""

    question: str
    company: str
    year: int
    document_class: str = DOCUMENT_CLASS_PERIODIC
    document_subtype: str = DOCUMENT_SUBTYPE_ANNUAL
    accounting_scope: str = SCOPE_CONSOLIDATED
    metric: str = METRIC_REVENUE
    # accounting_scope가 질문에 명시됐는지 기록한다. 기본값으로 채운 경우를 구분하기 위함이다.
    accounting_scope_explicit: bool = True
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_company_name(name: str) -> str:
    """
    회사명을 비교 가능한 형태로 정규화한다.

    - 유니코드 정규화(NFC)로 자모 분리/결합 표기를 통일한다.
    - '(주)', '주식회사' 같은 법인격 표기를 제거한다.
    - 공백과 구분 기호를 제거하고 소문자로 맞춘다.

    예:
        "한미반도체(주)"   -> "한미반도체"
        "한미반도체 주식회사" -> "한미반도체"
    """
    if not name:
        return ""

    text = unicodedata.normalize("NFC", name)

    text = LEGAL_SUFFIX_PATTERN.sub("", text)

    text = NON_NAME_PATTERN.sub("", text)

    return text.casefold()


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

    _reject_out_of_scope_period(question)

    if not companies:
        raise UnsupportedQuestionError(
            reason="missing_company_universe",
            detail="인식할 기업명 목록이 주어지지 않았습니다. manifest에서 읽어 전달해야 합니다.",
        )

    company = _extract_company(question, companies)

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
        document_class=DOCUMENT_CLASS_PERIODIC,
        document_subtype=DOCUMENT_SUBTYPE_ANNUAL,
        accounting_scope=accounting_scope,
        metric=metric,
        accounting_scope_explicit=scope_explicit,
        warnings=tuple(warnings),
    )
