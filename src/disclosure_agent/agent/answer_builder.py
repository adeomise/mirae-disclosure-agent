"""최종 답변 조립 모듈.

역할:
- Pattern 내부 결과를 진단용 dict로 조립한다(내부 표현).
- 공통 실행 Contract(AgentExecutionResult)를 공개 응답 Contract(AnswerResponse)로 옮긴다.

두 가지를 한 모듈에 두는 이유:
- 둘 다 "확정된 결과를 받는 쪽이 읽을 형태로 옮기는" 일이고, 값을 새로 만들지 않는다.
- 정지 단계·사유 어휘(STAGE_*/REASON_*)를 이미 이 모듈이 들고 있다. 그 어휘를 사용자 문장으로
  옮기는 대응표가 같은 파일에 있어야 어느 정지 사유가 어떤 문장이 되는지 대조할 수 있다.
- docs/agent_design/pattern_f_safety.md §8이 자연어 생성의 자리를 이 모듈로 지목한다.

공개 경계:
    AgentExecutionResult -> build_answer_response -> AnswerResponse

    검색·파싱·계산·연결·정책 판정은 하지 않는다. 이미 확정된 결과를 표현만 한다.
    HTTP 상태 결정과 예외 변환은 API 계층의 몫이다.

입력:
- QueryPlan, FilingRef, FinancialFact, Evidence (내부 조립)
- AgentExecutionResult (공개 변환)

출력:
- 직렬화 가능한 결과 dict (내부 조립)
- AnswerResponse (공개 변환)

TODO:
- 계산 결과와 다중 Fact 응답 형태 확장
- 경고 정책 정리
- Pattern E 변경 전/후 값까지 담는 narrative (P1)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from disclosure_agent.agent.derived_metrics import (
    UNIT_PERCENT,
    DerivedMetric,
    get_derived_metric_spec,
)
from disclosure_agent.agent.evidence import Evidence
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.agent.safety import (
    REASON_FORWARD_LOOKING_PREDICTION,
    REASON_INVESTMENT_ADVICE,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ComparisonRelation,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
    SectionScope,
    SummaryItemType,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    AnswerResponse,
    CalculationResult,
    DocumentSummary,
    EventHistory,
    Fact,
    ResultInputReference,
)
from disclosure_agent.periodic.extractor import BASE_CURRENCY_UNIT, FinancialFact
from disclosure_agent.retrieval.metadata_filter import FilingRef

STATUS_OK = "ok"
STATUS_BLOCKED = "blocked"

# 파이프라인이 막힐 수 있는 지점. blocked 결과의 stage 값으로 쓴다.
#
# 이전에는 periodic/service.py가 들고 있었지만, 막힌 단계를 결과에 적는 일은 결과를 조립하는
# 쪽의 어휘다. Pattern이 늘어도 같은 목록을 공유해야 어느 단계에서 막혔는지 비교할 수 있다.
STAGE_ROUTING = "routing"
STAGE_QUERY_PLAN = "query_plan"
STAGE_FILING_DISCOVERY = "filing_discovery"
STAGE_PARSING = "parsing"
STAGE_EXTRACTION = "extraction"
STAGE_EVIDENCE = "evidence"

# 질문이 지정한 범위를 문서 안의 Section 하나로 옮기는 단계(C4 Section Navigation).
#
# extraction과 나누는 이유:
#     extraction은 확정된 위치에서 값을 읽지 못한 것이고, 이 단계는 읽을 위치 자체를 정하지
#     못한 것이다. 후속 작업이 다르다. 앞쪽은 추출 규칙을, 뒤쪽은 Section 대응 규칙을 고쳐야
#     한다. 같은 사유로 뭉개면 결과만 보고 어느 쪽인지 알 수 없다.
STAGE_SECTION_NAVIGATION = "section_navigation"

# handler가 계획을 받아 들고 "이 모양은 내가 실행할 수 없다"고 판정한 단계.
#
# routing과 나누는 이유:
#     routing/unimplemented_intent는 "그 intent를 맡을 handler가 없다"이다. handler가 등록된
#     뒤에는 그 진술이 거짓이 된다. Pattern C는 등록되어 있고, 다만 계획의 모양이 이번
#     구현 범위 밖일 뿐이다. 같은 사유로 뭉개면 "Pattern C를 구현하면 풀린다"와 "Pattern C의
#     어느 부분을 더 구현해야 풀린다"가 구분되지 않는다.
#
# 코퍼스를 열기 전이므로 답변 가능성을 판정하지 않는다. 그 성질은 routing과 같다.
STAGE_PATTERN_ENTRY = "pattern_entry"

# 두 값을 함께 써도 되는지 판정하는 단계(C9 Comparability Validation).
#
# 계산 입력 검증(guard①)과 비교 가능성 검증(guard②)이 모두 여기에 적힌다. 둘은 적용 시점이
# 다를 뿐 같은 판정이며(agent_patterns.md), 무엇이 어긋났는지는 reason이 나눈다.
STAGE_COMPARABILITY = "comparability"

# 내부 결과를 공통 Contract 타입으로 옮기는 단계.
#
# 다른 stage와 달리 원문을 더 읽지 않는다. 확정된 값을 옮기지 못한 경우이므로 값을 추측해
# 채우지 않고 여기서 막혔다고 남긴다.
STAGE_CONTRACT_ADAPTATION = "contract_adaptation"

# 파이프라인이 멈춘 사유. stage와 짝을 이뤄 하나의 정지 지점을 가리킨다.
#
# 사유 문자열 자체는 그 판정을 내리는 계층이 만든다(retrieval, periodic.extractor, event).
# 여기 모아 두는 것은 agent 계층이 그 값을 다시 해석해야 하기 때문이다. 어느 사유를 "실행
# 중단"으로 보고 어느 사유를 "실행은 끝났고 답을 못 찾음"으로 볼지는 agent.normalization의
# 대응표가 정하며, 그 표의 key가 흩어진 문자열 리터럴이면 대조할 수 없다.
REASON_NO_MATCHING_FILING = "no_matching_filing"
REASON_PARSE_FAILED = "parse_failed"

# 조건에 맞는 공시가 여럿이라 1건으로 확정하지 못한 경우.
#
# Pattern E의 ambiguous_root와 나눈다. 그쪽은 이력 체인의 뿌리를 고르지 못한 것이고, 이쪽은
# 요약·조회 대상 문서 자체를 고르지 못한 것이다. canonical 사유는 둘 다 AMBIGUOUS_MATCH로
# 모이므로, 어느 판정이었는지는 이 값으로만 구분된다.
REASON_AMBIGUOUS_FILING = "ambiguous_filing"

# periodic.extractor가 올리는 ExtractionBlockedError.reason 중 agent가 다시 해석하는 것.
REASON_NO_METRIC_ACODE = "no_metric_acode"
REASON_NO_MATCHING_CONTEXT = "no_matching_context"
REASON_AMBIGUOUS_CANDIDATES = "ambiguous_candidates"

# Pattern E가 자체 판정으로 만드는 사유.
REASON_NO_ORIGINAL_FILING = "no_original_filing"
REASON_AMBIGUOUS_ROOT = "ambiguous_root"

# Pattern E가 계획 입구에서 만드는 사유.
#
# 계획이 지목한 공시 세부유형에 대응하는 이벤트 유형이 없다. 위의 두 사유와 달리 코퍼스를
# 보기 전이므로 답변 가능성을 판정하지 않는다. 가까운 유형으로 대신 처리하면 정정 항목이
# 다른 유형의 어휘로 조회되어 변경이 전부 이름 없이 남고, 그 결과는 "바뀐 것이 없다"와
# 구분되지 않는다.
REASON_UNSUPPORTED_EVENT_TYPE = "unsupported_event_type"

# Pattern C가 계획 입구에서 만드는 사유.
#
# 넷으로 나눈 이유는 후속 작업이 서로 다르기 때문이다. 비교 대상이 하나가 아닌 것과, 기간이
# 둘이 아닌 것과, 파생 개념이 아닌 것과, 대응표의 연산을 아직 만들 수 없는 것은 각각 다른
# 구현을 요구한다. 하나로 묶으면 결과만 보고 무엇을 구현하면 풀리는지 알 수 없다.
REASON_UNSUPPORTED_COMPARISON_SUBJECT = "unsupported_comparison_subject"
REASON_UNSUPPORTED_COMPARISON_PERIOD = "unsupported_comparison_period"
REASON_UNSUPPORTED_COMPARISON_METRIC = "unsupported_comparison_metric"
REASON_UNSUPPORTED_DERIVED_OPERATION = "unsupported_derived_operation"

# agent.calculator가 계산을 거부한 사유.
#
# 앞의 셋과 마지막을 나누는 이유는 성질이 다르기 때문이다. 앞쪽은 "값은 읽었지만 함께 쓸 수
# 없다"는 판정이고, 뒤쪽은 "그 값으로는 산술 자체가 성립하지 않는다"이다. 전자만 답변 가능성
# 판정(NOT_COMPARABLE)으로 나가고, 후자는 대응표에 없어 실행 중단으로 남는다.
#
# 함께 쓸 수 없다는 판정을 다시 셋으로 나누는 이유는 어긋난 축이 다르기 때문이다.
#     incomparable_calculation_inputs  한 파생값의 분자와 분모가 어긋났다(guard①).
#     incomparable_periods             같은 기업의 두 기간이 어긋났다(guard②, Pattern C).
#     incomparable_entities            같은 기간의 두 기업이 어긋났다(guard②, Pattern D).
# canonical 사유는 셋 다 NOT_COMPARABLE 하나로 모이므로, 어느 축이었는지는 이 값으로만
# 구분된다. 하나로 묶으면 결과만 보고 무엇을 고쳐야 하는지 알 수 없다.
REASON_INCOMPARABLE_CALCULATION_INPUTS = "incomparable_calculation_inputs"
REASON_INCOMPARABLE_PERIODS = "incomparable_periods"
REASON_INCOMPARABLE_ENTITIES = "incomparable_entities"
REASON_UNUSABLE_CALCULATION_INPUT = "unusable_calculation_input"

# Section 탐색 단계(STAGE_SECTION_NAVIGATION)의 사유는 여기 적지 않는다.
#
# 그 판정을 내리는 곳은 periodic.sections이고, 값도 그 모듈이 정본으로 가지고 있다
# (REASON_UNSUPPORTED_SECTION_SCOPE / REASON_NO_MATCHING_SECTION / REASON_AMBIGUOUS_SECTION /
# REASON_EMPTY_SECTION). 같은 문자열을 여기에 한 벌 더 적으면 한쪽만 고쳤을 때
# agent.normalization의 대응표가 조용히 빗나가고, 그때 정지 사유는 "실행 중단"으로 떨어진다.
# 위쪽 사유들이 여기 모여 있는 이유는 그 값을 만드는 계층이 여럿이라 대조할 자리가 필요해서인데,
# Section 탐색 사유는 만드는 계층이 하나뿐이라 그 자리가 필요하지 않다.

# 확정된 내부 값을 공통 Contract 타입으로 옮기지 못한 경우.
REASON_UNCONVERTIBLE_FACT = "unconvertible_fact"


@dataclass(frozen=True)
class BlockedInfo:
    """
    파이프라인이 진행되지 못한 지점.

    "여기서 멈췄다"만 말한다. 그것이 실행 실패인지 정상 실행 뒤의 답변 불가 판정인지는
    여기서 정하지 않는다. 그 해석은 agent.normalization의 대응표가 맡는다.
    """

    stage: str
    reason: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _filing_summary(filing: FilingRef | None) -> dict[str, Any] | None:
    if filing is None:
        return None

    return {
        "doc_id": filing.doc_id,
        "corp_name": filing.corp_name,
        "corp_code": filing.corp_code,
        "stock_code": filing.stock_code,
        "report_name": filing.report_name,
        "receipt_no": filing.receipt_no,
        "receipt_date": filing.receipt_date,
        "document_class": filing.document_class,
        # manifest 원 표기도 함께 남긴다. 설계 어휘와 manifest 표기가 다른 문서군이 있어
        # 어느 레코드에서 왔는지 확인할 수 있어야 한다.
        "doc_group": filing.doc_group,
        "document_subtype": filing.document_subtype,
        "base_year": filing.base_year,
        "base_month": filing.base_month,
        "is_correction": filing.is_correction,
        "source_path": str(filing.main_document),
    }


def build_structured_result(
    plan: QueryPlan,
    *,
    filing: FilingRef,
    fact: FinancialFact,
    evidence: Evidence,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """성공한 단일 수치 조회 결과를 조립한다."""
    combined_warnings = list(plan.warnings) + list(warnings or [])

    return {
        "status": STATUS_OK,
        "query": plan.to_dict(),
        "filing": _filing_summary(filing),
        "fact": fact.to_dict(),
        "evidence": [evidence.to_dict()],
        "calculations": [],
        "warnings": combined_warnings,
    }


def build_blocked_result(
    plan: QueryPlan | None,
    *,
    stage: str,
    reason: str,
    detail: str,
    filing: FilingRef | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """
    파이프라인이 진행되지 못한 경우의 결과를 조립한다.

    값을 추측해 채우지 않고 어느 단계에서 왜 막혔는지만 남긴다.
    """
    combined_warnings = list(plan.warnings) if plan else []

    combined_warnings += list(warnings or [])

    return {
        "status": STATUS_BLOCKED,
        "query": plan.to_dict() if plan else None,
        "filing": _filing_summary(filing),
        "fact": None,
        "evidence": [],
        "calculations": [],
        "blocked": {
            "stage": stage,
            "reason": reason,
            "detail": detail,
        },
        "warnings": combined_warnings,
    }


# ==========================================================================
# 공개 응답 경계 — AgentExecutionResult -> AnswerResponse
#
# 이 아래는 값을 새로 만들지 않는다. 확정된 실행 결과를 사용자가 읽을 형태로 옮기기만 한다.
# 검색·파싱·계산·연결·정책 판정은 위쪽 계층에서 이미 끝났다.
#
# 문장을 대응표로 두는 이유:
#     사유 하나가 어떤 문장이 되는지 한곳에서 대조할 수 있어야 한다. 문자열이 분기문 안에
#     흩어지면 "이 사유에는 문장이 없다"가 조용한 fallback으로 나타나고, 서로 다른 판정이
#     같은 문장으로 뭉개져도 테스트가 잡지 못한다.
# ==========================================================================


class AnswerBuildError(RuntimeError):
    """공개 응답으로 옮길 수 없는 실행 결과를 받은 경우.

    normalization.UnmappedPolicyCategoryError와 같은 자리의 예외다. 값을 추측해 채우거나
    그럴듯한 기본 문장으로 덮지 않고, 대응을 정하는 쪽이 먼저 고치게 한다.
    """


class UnsupportedExecutionStatusError(AnswerBuildError):
    """COMPLETED가 아닌 실행 결과를 받은 경우.

    BLOCKED/FAILED는 답변 가능성 판정 자체가 없다. AnswerResponse.status는 필수이므로
    억지로 채우면 "코퍼스에 답이 없다"거나 "정책상 거부했다"는 없던 진술이 생긴다.
    두 상태를 어떻게 사용자에게 전할지는 AskService/API 계층이 정한다
    (docs/agent_design/pattern_f_safety.md §8 원칙 5).
    """

    def __init__(self, execution_status: ExecutionStatus) -> None:
        super().__init__(
            f"execution_status가 {execution_status.value}입니다. "
            "Answer Builder는 COMPLETED 결과만 옮깁니다. "
            "BLOCKED/FAILED 처리는 AskService/API 계층의 몫입니다."
        )

        self.execution_status = execution_status


class MissingAnswerabilityStatusError(AnswerBuildError):
    """COMPLETED인데 답변 가능성 판정이 비어 있는 경우.

    AgentExecutionResult Contract 위반이다. 판정이 없는데 status를 고르면 그 선택이
    판정으로 둔갑한다.
    """

    def __init__(self) -> None:
        super().__init__(
            "COMPLETED 결과에 answerability_status가 없습니다. 판정을 대신 고르지 않습니다."
        )


class MissingAnswerSupportError(AnswerBuildError):
    """답할 수 있다고 판정됐는데 그 근거가 결과 안에 없는 경우.

    Pattern A의 최소 불변식은 ANSWERABLE + facts >= 1이다. 근거 없이 문장을 만들면
    형식만 맞고 내용이 없는 답이 된다.
    """


class UnsupportedResultShapeError(AnswerBuildError):
    """표현 방법이 정해지지 않은 결과 모양을 받은 경우.

    첫 항목만 골라 답하거나 영문 enum 값을 그대로 문장에 넣지 않는다. 새 Pattern을 붙이는
    쪽이 문장 형태를 먼저 정하게 한다. Router가 미등록 intent를 가장 가까운 handler로
    대신 처리하지 않는 것과 같은 이유다.
    """


# --------------------------------------------------------------------------
# 표기 어휘
#
# Contract enum을 사용자 문장에 쓰는 한글 표기로 옮긴다. 새 값이 늘면 대응표를 먼저 고치게
# 한다. 영문 enum 값으로 조용히 되돌아가면 한국어 문장 안에 revenue가 섞여도 테스트가
# 잡지 못한다.
# --------------------------------------------------------------------------

# 단일 Fact 문장이 지원하는 metric.
#
# MetricConcept 전체를 채우지 않는다. _single_fact_sentence의 문장은
#
#     {company}의 {fiscal_year}년 {accounting_scope} 기준 {metric}은 {value}입니다.
#
# 즉 재무제표 축(회계연도 × 연결/별도)을 전제한 손익 지표 템플릿이다. 계약금액·보유
# 주식수 같은 거래소공시·지분공시 지표는 이 축을 갖지 않고, 대신 "어느 계약인지",
# "어느 보유 내역인지"라는 식별자를 요구한다.
#
# 그런 지표에 표기만 미리 붙여 두면 fiscal_year와 scope가 생략된 채
# "{회사}의 계약금액은 X원입니다"처럼 형식은 자연스럽고 대상이 특정되지 않은 문장이
# 나간다. Router가 미등록 intent를 가장 가까운 handler로 대신 처리하지 않는 것과 같은
# 실패 모드다.
#
# 그래서 표기를 비워 두고 _metric_label에서 멈춘다. 해당 Pattern을 붙이는 쪽이 표기가
# 아니라 문장 템플릿부터 정하게 하기 위해서다.
METRIC_LABELS: dict[MetricConcept, str] = {
    MetricConcept.REVENUE: "매출액",
    MetricConcept.OPERATING_INCOME: "영업이익",
}

# 계약 조회(Q02) 문장이 쓸 거래소공시 필드 표기.
#
# METRIC_LABELS와 합치지 않는다. 저쪽은 재무제표 축(회계연도·연결기준)을 전제한 단일 Fact
# 템플릿이 쓰는 표기이고, 계약금액과 계약기간에는 그 축이 없다. 한 표에 담으면 계약 지표가
# 그 템플릿으로도 흘러 들어가 "2025년 연결 기준 계약금액" 같은 문장이 만들어진다.
# DERIVED_METRIC_LABELS를 따로 둔 것과 같은 자리다.
# 표기가 문장에 그대로 쓰이는 것은 계약금액뿐이다. 시작일·종료일은 "계약기간은 A부터
# B까지"로 묶여 나가므로 낱개 표기가 문장에 나타나지 않는다. 그래도 표에 두는 이유는 이 표가
# "어떤 지표가 거래소공시 계약 필드인가"를 정하는 자리이기도 하기 때문이다
# (_is_contract_fact_bundle).
CONTRACT_METRIC_LABELS: dict[MetricConcept, str] = {
    MetricConcept.CONTRACT_AMOUNT: "계약금액",
    MetricConcept.CONTRACT_START_DATE: "계약 시작일",
    MetricConcept.CONTRACT_END_DATE: "계약 종료일",
}

ACCOUNTING_SCOPE_LABELS: dict[FactAccountingScope, str] = {
    FactAccountingScope.CONSOLIDATED: "연결",
    FactAccountingScope.SEPARATE: "별도",
}

# 문장에서 기준 표기를 생략하는 scope.
#
# UNKNOWN은 "확인하지 못했다"이고 NOT_APPLICABLE은 "해당 없음"이다. 둘 다 연결/별도 중
# 하나로 단정할 수 없으므로 '... 기준' 구절 자체를 넣지 않는다. 임의로 연결로 적으면
# Q01의 주요 실패 모드(연결·별도 혼동)를 문장 단계에서 다시 만든다.
SCOPE_OMITTED_IN_ANSWER: frozenset[FactAccountingScope] = frozenset(
    {FactAccountingScope.UNKNOWN, FactAccountingScope.NOT_APPLICABLE}
)

# 연도만 적어도 되는 fiscal_period 표기.
#
# 현재 periodic.adapter는 fiscal_period를 채우지 않고(None), Pattern A는 사업보고서의
# 연간 값만 다룬다. 분기·반기 값이 들어오는데 "2025년 매출액"이라고 적으면 기간이 틀린
# 답이 되므로, 표기가 정해지지 않은 값은 여기서 막는다.
FULL_YEAR_FISCAL_PERIODS: frozenset[str] = frozenset({"FY"})


# 우열 판정 문장이 쓸 파생 개념 표기.
#
# 정의(분자·분모·연산)는 agent.derived_metrics가 정본이고 여기에는 한국어 표기만 둔다. 정의를
# 여기에 다시 적으면 같은 개념이 두 벌이 되고 한쪽만 바뀌는 날이 온다. METRIC_LABELS가 원천
# 지표에 대해 하는 일과 같은 자리이며, 표기가 없는 개념에서 멈추는 것도 같다.
#
# CalculationResult에는 어떤 파생 개념이었는지가 남지 않는다. 남는 것은 operation과 입력
# 참조뿐이라 그 둘로 대응표를 되짚어 개념을 찾는다(_derived_metric_label).
# 요약 범위의 한글 표기.
#
# SectionScope enum 값을 그대로 문장에 넣지 않는다. 한국어 문장에 facility_investment가
# 섞이고, 그 상태로도 테스트가 통과한다. 표기가 없는 값은 문장으로 옮기지 않고 막는다.
SECTION_SCOPE_LABELS: dict[SectionScope, str] = {
    SectionScope.FINANCIAL_STATEMENT: "재무제표",
    SectionScope.BUSINESS_OVERVIEW: "사업 개요",
    SectionScope.PRODUCTS: "주요 제품 및 서비스",
    SectionScope.FACILITY_INVESTMENT: "설비 투자",
}

# 요약 항목 종류의 한글 표기. 같은 이유로 대응표를 둔다.
SUMMARY_ITEM_TYPE_LABELS: dict[SummaryItemType, str] = {
    SummaryItemType.PARAGRAPH: "본문",
    SummaryItemType.TABLE: "표",
}

DERIVED_METRIC_LABELS: dict[DerivedMetric, str] = {
    DerivedMetric.OPERATING_MARGIN: "영업이익률",
}

# 계산 결과 단위의 한국어 표기.
#
# CalculationResult.unit은 계산 계층의 어휘("percent")다. 그대로 문장에 넣으면 한국어 문장에
# 영문 식별자가 섞인다(METRIC_LABELS 주석과 같은 실패 모드).
CALCULATION_UNIT_LABELS: dict[str, str] = {
    UNIT_PERCENT: "%",
}

# Q07이 만드는 두 Calculation의 연산 이름.
#
# agent.calculator에서 가져오지 않는다. 그쪽이 이 모듈의 정지 사유 상수를 이미 가져가고 있어
# 반대 방향을 열면 순환 import가 된다.
#
# 같은 문자열이 두 곳에 있지만 어긋나도 조용히 틀리지 않는다. 이름이 갈리면
# _is_temporal_delta_shape이 그 모양을 알아보지 못하고, 결과는 아래 단일 Fact 경로로 내려가
# Fact 2건에서 멈춘다. 틀린 문장이 나가는 쪽이 아니라 문장을 만들지 않는 쪽으로 실패한다.
# 두 이름이 붙어 있는지는 tests/unit/test_answer_builder.py가 함께 고정한다.
OPERATION_DELTA = "delta"
OPERATION_DELTA_PCT = "delta_pct"

# Fact 정규화 단위의 한국어 표기.
#
# 아래 표와 짝을 이루지만 합치지 않는다. 열쇠의 출처가 다르기 때문이다:
#     이 표          추출 계층이 Fact에 적는 어휘. periodic.extractor가 정한다.
#     위 표          agent가 계산 결과에 적는 어휘. agent.derived_metrics가 정한다.
#
# 합치면 한 계층이 단위를 늘릴 때 다른 계층의 문장이 함께 바뀐다. 실제로 두 어휘에는 같은
# 문자열이 이미 있다. "percent"는 이쪽에서 원문에 적힌 비율이고 저쪽에서 agent가 만든 비율이며,
# 한 표에 담으면 그 구분이 사라진다.
#
# 원문 단위(raw_unit)는 이 표를 지나지 않는다. 그쪽은 공시에 적힌 표기("백만원")를 그대로 쓰며,
# 사용자가 원문과 대조하는 값이므로 옮기면 대조가 불가능해진다. 옮기는 것은 정규화 단위뿐이다.
#
# 통화 표기를 두 개 싣는 이유:
#     같은 재무 Fact인데 만든 쪽에 따라 값이 다르다.
#         "KRW"  periodic.extractor.normalize_value_and_unit. Contract 수준 Fact producer이며
#                event 계층과 Contract 예제도 같은 값을 쓴다.
#         "원"   periodic.extractor.BASE_CURRENCY_UNIT. periodic.adapter.adapt_financial_fact가
#                FinancialFact.base_unit을 그대로 실어 만드는 값이며, 현재 Pattern D가 지나는
#                경로다(agent.period_retrieval.collect_period_facts).
#
#     어느 쪽을 정본으로 둘지는 추출 계층이 정할 일이다. 표현 계층은 그 결정을 기다리는 동안
#     양쪽을 모두 옮길 수 있어야 하며, 한쪽만 받으면 producer가 바뀌는 날 사용자 문장에 "KRW"가
#     그대로 실린다. 여기서 Fact의 값을 고쳐 어휘를 하나로 맞추지 않는다. 그것은 추출 계층의
#     결정을 표현 계층이 대신 내리는 일이다.
#
# "shares"·"percent"는 싣지 않는다. normalize_value_and_unit이 만들지만, 그 단위를 갖는 지표는
# METRIC_LABELS에 표기가 없어 문장이 그 앞에서 먼저 멈춘다. 도달하지 않는 표기를 미리 열면
# 문장 템플릿을 정하지 않은 지표가 조용히 통과한다.
FACT_NORMALIZED_UNIT_LABELS: dict[str, str] = {
    "KRW": "원",
    BASE_CURRENCY_UNIT: "원",
}

# 우열 관계 문장.
#
# ComparisonResult.relation이 정본이다. Builder는 두 값을 다시 견주지 않고 관계 이름을 문장으로
# 옮기기만 한다(agent_patterns.md "Agent computes and judges. Builder presents.").
#
# 질문별 분기가 없다. Q10과 Q11은 견준 대상이 Fact인지 Calculation인지만 다르고, 그 차이는 대상
# 표기를 만드는 자리에서 끝난다. 여기에 질문을 알아보는 분기를 두면 새 비교 질문마다 문장이 한
# 벌씩 늘고, 관계 어휘가 어느 표를 정본으로 삼는지 알 수 없게 된다.
#
# equal에 이름을 넣지 않는다. 어느 쪽도 크지 않은 판정이라 한쪽을 가리키면 없는 우열이 생긴다.
COMPARISON_RELATION_TEMPLATES: dict[ComparisonRelation, str] = {
    ComparisonRelation.LEFT_GREATER: "{left} 쪽이 더 큽니다.",
    ComparisonRelation.RIGHT_GREATER: "{right} 쪽이 더 큽니다.",
    ComparisonRelation.EQUAL: "두 값은 같습니다.",
}


# --------------------------------------------------------------------------
# 사유별 문장
#
# 원칙은 docs/agent_design/pattern_f_safety.md §8을 따른다.
#     - NOT_FOUND는 "찾지 못했습니다"(시스템 한계 가능성을 남김)
#     - 기간 밖은 "보유하고 있지 않습니다"(확정)
#     - 거부 사유를 대체 답변으로 바꾸지 않는다(다른 연도 값을 제시하지 않는다)
#     - 투자 판단 질문에 지표를 제시하지 않는다
# --------------------------------------------------------------------------

UNAVAILABLE_MESSAGE_BY_REASON: dict[AnswerabilityReason, str] = {
    # 문서는 봤지만 그 안에서 찾지 못했다. 검색 품질 문제일 수 있어 확정하지 않는다.
    AnswerabilityReason.NOT_FOUND: "요청하신 정보를 공시에서 찾지 못했습니다.",
    AnswerabilityReason.INSUFFICIENT_EVIDENCE: (
        "요청하신 항목의 근거를 원문에서 확인하지 못해 답변하지 않습니다."
    ),
    # 값은 확보했지만 함께 쓸 수 없다고 판정한 결과다(normalization의 STAGE_COMPARABILITY 행).
    # "찾지 못했다"와 다르다. 계산 입력이 어긋난 것인지 기간이 어긋난 것인지는
    # answerability_detail.reason이 구분하며, 문장은 그 구분을 담지 않는다. detail로 문장을
    # 갈라 쓰면 사용자 문구가 진단 어휘를 따라가고 같은 뜻의 문장이 여러 벌이 된다.
    AnswerabilityReason.NOT_COMPARABLE: (
        "확보한 값들의 기준, 단위 또는 개념이 서로 달라 직접 비교할 수 없습니다."
    ),
    # 문서 목록이라는 완전한 집합과 대조한 결과다. 확정 표현을 쓴다.
    AnswerabilityReason.OUT_OF_DATA_RANGE: (
        "요청하신 기간의 공시 데이터를 보유하고 있지 않습니다."
    ),
    AnswerabilityReason.OUT_OF_SCOPE_DATA: (
        "요청하신 종류의 데이터는 보유한 공시 문서 범위에 없습니다."
    ),
    # 아래 값은 F1~F4가 공유한다. 무엇을 막았는지는 answerability_detail.reason이 구분하며
    # POLICY_REFUSAL_MESSAGE_BY_DETAIL_REASON이 그 구분을 문장으로 옮긴다.
    AnswerabilityReason.OUT_OF_SCOPE_POLICY: (
        "정책상 답변 범위 밖인 요청이라 답변하지 않습니다."
    ),
    # 후보가 여러 건이라 하나로 좁히지 못했다. "정보가 없다"와 다르다.
    AnswerabilityReason.AMBIGUOUS_MATCH: (
        "조건에 맞는 후보가 여러 건이어서 하나로 확정할 수 없습니다. "
        "질문을 더 좁혀 주시면 다시 확인할 수 있습니다."
    ),
    AnswerabilityReason.AMBIGUOUS_LINK: (
        "정정 대상 문서를 1건으로 확정할 수 없어 이력을 확정하지 못했습니다."
    ),
    AnswerabilityReason.INCOMPLETE_CHAIN: (
        "정정 이력의 일부를 확정하지 못해 답변을 확정할 수 없습니다."
    ),
    # 공시는 찾았고 항목도 있는데 값만 원문에서 공개되지 않았다. "찾지 못했다"(NOT_FOUND)와
    # 다르므로 질문을 좁히라고 안내하지 않는다. 좁혀도 원문에 값이 없다.
    #
    # 유보된 값을 0·미공개·없음으로 바꾸지 않는다. 그렇게 쓰면 원문에 없는 값을 만든 것이 되고,
    # 뒤에 정정공시로 값이 공개됐을 때 앞선 답변이 틀린 사실을 말한 것이 된다.
    AnswerabilityReason.WITHHELD_IN_SOURCE: (
        "공시는 확인했지만 질문에 필요한 정보가 원문에서 공개되지 않았습니다."
    ),
}

# Contract에는 있지만 사용자 문구가 아직 확정되지 않아 Answer Builder가 지원하지 않는 사유.
# 문구가 확정되면 UNAVAILABLE_MESSAGE_BY_REASON으로 옮기고 이 집합에서 제거한다.
#
# 현재는 비어 있다. 마지막 항목이었던 NOT_COMPARABLE의 문구가 확정돼 대응표로 옮겨졌다.
# 그래도 집합 자체는 남긴다. 이 자리는 "Contract에 사유가 늘었는데 문구가 아직 없다"를 담는
# 곳이고, ALLOWED_REASONS_BY_STATUS의 뺄셈과 대응표 완전성 검사가 그 자리를 전제로 서 있다.
# 지웠다가 다음 사유가 생길 때 다시 만들면, 그 사이에 문구 없는 사유가 UNANSWERABLE로 조용히
# 통과해 영문 enum 값이 그대로 응답에 실릴 수 있다.
UNMAPPED_ANSWERABILITY_REASONS: frozenset[AnswerabilityReason] = frozenset()

# 정책 거부 문장. answerability_detail.reason이 고른다.
#
# safety.py가 만든 detail 원문을 그대로 노출하지 않는다. 그 문자열에는 어떤 규칙과 어떤
# 표현에 걸렸는지가 진단용으로 들어 있어 탐지 규칙이 그대로 드러난다. 문장의 뜻은 같게
# 두고 진단 부분만 뺀다.
POLICY_REFUSAL_MESSAGE_BY_DETAIL_REASON: dict[str, str] = {
    REASON_INVESTMENT_ADVICE: (
        "투자 판단·추천 요청은 답변 범위 밖입니다. 공시에 기재된 사실 조회는 계속 지원합니다."
    ),
    REASON_FORWARD_LOOKING_PREDICTION: (
        "아직 공시되지 않은 결과에 대한 예측 요청은 답변 범위 밖입니다. "
        "공시에 기재된 확정 사실·일정 조회는 계속 지원합니다."
    ),
}

# 사유 없이 UNRESOLVED만 남은 결과의 문장.
#
# Pattern E가 루트를 확정하지 못한 경로다(execution_result_mapping.md §3). 없는 사유를 골라
# 채우지 않고, 사유를 단정하지 않는 문장을 쓴다.
#
# UNANSWERABLE에는 같은 문장을 두지 않는다. 사유 없는 UNANSWERABLE은 producer가 만들지
# 않으므로 _require_compatible_reason이 먼저 막는다.
UNRESOLVED_WITHOUT_REASON_MESSAGE = "대상을 하나로 확정하지 못해 답변을 확정할 수 없습니다."

# 일부만 답할 수 있을 때 확보분 뒤에 붙는 한계 문장.
#
# UNAVAILABLE_MESSAGE_BY_REASON과 뜻은 같고 자리가 다르다. 이쪽은 확보분 문장 뒤에 붙는
# 종속절이라 "다만"으로 시작해야 하고, 저쪽은 답변 전체가 되는 독립 문장이다. 한 문자열을
# 두 자리에 함께 쓰면 둘 중 한쪽의 문장이 어그러진다. INCOMPLETE_CHAIN이 이미 같은 사유를
# 두 표현으로 나눠 들고 있고, 여기서도 그 방식을 따른다.
LIMITATION_MESSAGE_BY_REASON: dict[AnswerabilityReason, str] = {
    AnswerabilityReason.INCOMPLETE_CHAIN: (
        "다만 이력의 일부를 보유 공시 안에서 확정하지 못해 전체 이력은 확인하지 못했습니다."
    ),
    AnswerabilityReason.WITHHELD_IN_SOURCE: (
        "다만 질문에 필요한 일부 항목은 원문에서 공개되지 않아 확인하지 못했습니다."
    ),
}

LIMITATION_WITHOUT_REASON_MESSAGE = "다만 요청하신 내용의 일부는 확인하지 못했습니다."


# --------------------------------------------------------------------------
# 결과 정합성
#
# Contract는 pydantic 모델이라 서로 모순되는 조합도 만들어진다. UNRESOLVED + NOT_FOUND는
# 스키마상 유효하지만 "후보를 좁히지 못했다"와 "찾지 못했다"를 동시에 말한다. 그런 입력을
# 문장으로 옮기면 두 판정 중 하나가 조용히 사라진다.
#
# 여기서 걸러내지 않고 문장만 고르면, 잘못된 조합이 형식상 정상인 AnswerResponse로 나가
# 어느 단계에서 어긋났는지 되짚을 수 없게 된다. 값을 고쳐 맞추지 않고 멈춘다.
# --------------------------------------------------------------------------

# 판정 상태별로 실릴 수 있는 canonical 사유.
#
# 각 줄의 근거:
#     ANSWERABLE            사유를 싣지 않는다. evaluation.runner의 GoldAnswer 검증이
#                           "ANSWERABLE cases must not define a failure reason"으로
#                           같은 규칙을 이미 고정하고 있다.
#     PARTIALLY_ANSWERABLE  INCOMPLETE_CHAIN / WITHHELD_IN_SOURCE. 앞은
#                           execution_result_mapping.md §3의 체인 미완결 행이 producer다.
#                           뒤는 open_decisions.md D9가 정한 "필요한 field 일부만 유보"
#                           경로이며, 체인 완결 여부와 독립된 축이다.
#     UNRESOLVED            AMBIGUOUS_MATCH / AMBIGUOUS_LINK. 둘 다 "후보를 하나로 좁히지
#                           못했다"이며, normalization의 ANSWERABILITY_BY_STOP이 만드는
#                           것은 AMBIGUOUS_MATCH다. AMBIGUOUS_LINK는
#                           evidence_and_answerability.md가 "정정 대상 문서를 1건으로
#                           확정할 수 없음"으로 정의한 같은 계열이라 함께 허용한다.
#     UNANSWERABLE          위 사유들 중 다른 상태에만 속하는 것을 뺀 나머지. 그 사유들이
#                           UNANSWERABLE로 실리면 "답이 없다"와 "확정하지 못했다"가 한
#                           지표로 묶인다(normalization의 AMBIGUOUS_MATCH 주석 참조).
#                           WITHHELD_IN_SOURCE는 빼지 않는다. 핵심 field 전체가 유보되면
#                           확보분이 없어 UNANSWERABLE이고, 일부만 유보되면
#                           PARTIALLY_ANSWERABLE이다(D9). 두 상태에 모두 실릴 수 있는
#                           유일한 사유이며, 어느 쪽인지는 유보 범위가 정한다.
_UNRESOLVED_REASONS: frozenset[AnswerabilityReason] = frozenset(
    {AnswerabilityReason.AMBIGUOUS_MATCH, AnswerabilityReason.AMBIGUOUS_LINK}
)

# PARTIALLY_ANSWERABLE에만 실리는 사유. UNANSWERABLE 집합을 만들 때 뺀다.
#
# 이것은 "PARTIALLY_ANSWERABLE에서 허용되는 사유"가 아니라 "PARTIALLY_ANSWERABLE에서만
# 허용되는 사유"다. 허용 집합은 아래에서 WITHHELD_IN_SOURCE를 더해 만든다. 두 집합이 다른
# 이유는 WITHHELD_IN_SOURCE가 두 상태에 걸쳐 있기 때문이고, 이 파일에서 배타성과 허용이
# 갈리는 유일한 자리다.
#
# 그래서 WITHHELD_IN_SOURCE를 여기 넣지 않는 것이 요점이다. 넣으면 "핵심 field 전체가 유보"
# (D9 case B)가 UNANSWERABLE로 나가지 못하고 Builder에서 멈춘다.
_PARTIAL_ONLY_REASONS: frozenset[AnswerabilityReason] = frozenset(
    {AnswerabilityReason.INCOMPLETE_CHAIN}
)

ALLOWED_REASONS_BY_STATUS: dict[AnswerabilityStatus, frozenset[AnswerabilityReason]] = {
    AnswerabilityStatus.ANSWERABLE: frozenset(),
    AnswerabilityStatus.PARTIALLY_ANSWERABLE: _PARTIAL_ONLY_REASONS
    | {AnswerabilityReason.WITHHELD_IN_SOURCE},
    AnswerabilityStatus.UNRESOLVED: _UNRESOLVED_REASONS,
    AnswerabilityStatus.UNANSWERABLE: frozenset(AnswerabilityReason)
    - _UNRESOLVED_REASONS
    - _PARTIAL_ONLY_REASONS
    - UNMAPPED_ANSWERABILITY_REASONS,
}

# 사유를 비운 채로 나올 수 있는 판정 상태.
#
# "언젠가 생길 수 있다"가 아니라 지금 producer가 실제로 그렇게 만드는 것만 넣는다. 현재
# canonical shape를 고정해 두고, 새 capability가 붙을 때 이 집합을 명시적으로 넓히는 편이
# 낫다. 미리 열어 두면 사유를 채우지 않은 새 경로가 조용히 통과한다.
#
#     ANSWERABLE   사유를 싣지 않는 것이 정상이다. normalize_single_fact_result의 성공
#                  경로와 normalize_event_history_result의 체인 완결 경로가 모두 그렇다.
#     UNRESOLVED   normalize_event_history_result에서 history도 blocked도 없는 경로가
#                  사유 없이 UNRESOLVED만 남긴다(execution_result_mapping.md §3의
#                  "conversion.history is None ... answerability_reason 없음" 행).
#
# 빠진 두 상태의 근거:
#     PARTIALLY_ANSWERABLE  구조적으로 불가능하다. handlers.event_history._judge_answerability와
#                           normalization._chain_reason이 같은 boolean(FilingChain.is_complete,
#                           event.contract_adapter가 EventHistory.chain_complete에 그대로
#                           옮긴 값)에서 갈리므로, PARTIALLY라면 INCOMPLETE_CHAIN이 반드시
#                           함께 나온다.
#     UNANSWERABLE          producer가 없다. refused_execution은 answerability_reason을
#                           필수 키워드로 받고, _stopped_execution은 ANSWERABILITY_BY_STOP의
#                           모든 항목이 사유를 채우고 있다.
STATUSES_ALLOWING_NO_REASON: frozenset[AnswerabilityStatus] = frozenset(
    {AnswerabilityStatus.ANSWERABLE, AnswerabilityStatus.UNRESOLVED}
)

# 근거를 붙이면 안 되는 거부 사유.
#
# pattern_f_safety.md §3 "F1~F4는 facts와 evidence가 비어 있어야 합니다"와
# evidence_and_answerability.md 원칙 7이 근거다. 거부 응답에 원문 근거가 붙으면 부분 답변으로
# 읽히고, 투자 판단 질문에 지표를 제시한 것과 같아진다(§8 원칙 4).
#
# events / event_links / event_histories는 넣지 않았다. 위 두 문서가 명시한 것은 facts와
# evidence뿐이고, 나머지까지 막을 근거를 문서에서 찾지 못했다. 실제로 Pattern E의
# no_matching_filing 경로는 UNANSWERABLE 결과에 event_links를 실어 보낼 수 있는 구조다
# (normalization._stopped_execution). 추측으로 범위를 넓히면 그 경로가 함께 막힌다.
REFUSAL_REASONS_WITHOUT_SUPPORTING_PAYLOAD: frozenset[AnswerabilityReason] = frozenset(
    {
        AnswerabilityReason.OUT_OF_DATA_RANGE,
        AnswerabilityReason.OUT_OF_SCOPE_DATA,
        AnswerabilityReason.OUT_OF_SCOPE_POLICY,
    }
)

# EventHistory를 실은 결과에서 판정 상태가 요구하는 chain_complete 값.
#
# handlers.event_history._judge_answerability가 chain.is_complete 하나로 두 상태를 가르고,
# normalization._chain_reason이 같은 값으로 INCOMPLETE_CHAIN을 붙인다. 그래서 상태와
# chain_complete는 서로를 결정한다. 어긋나면 둘 중 하나가 틀린 것이고, 문장은 그 틀린 쪽을
# 따라간다.
CHAIN_COMPLETE_BY_STATUS: dict[AnswerabilityStatus, bool] = {
    AnswerabilityStatus.ANSWERABLE: True,
    AnswerabilityStatus.PARTIALLY_ANSWERABLE: False,
}


def build_answer_response(result: AgentExecutionResult) -> AnswerResponse:
    """
    정상적으로 끝난 실행 결과 하나를 공개 응답 Contract로 옮긴다.

    답변 가능성 판정(status/reason)은 그대로 싣는다. 두 필드의 어휘가 실행 Contract와 같아서
    새로 고를 것이 없고, 새로 고르면 평가가 대조하는 canonical 값이 두 벌이 된다.

    구조화 결과는 AnswerResponse에 자리가 있는 것만 손실 없이 옮긴다. calculations와
    comparisons, warnings는 현재 AnswerResponse에 자리가 없어 실리지 않으며, 이번 범위에서
    Contract를 고치지 않는다. 우열 판정은 자리가 생기기 전까지 문장으로만 나가고, 그 문장은
    실행 결과에 적힌 relation을 그대로 옮긴 것이다(_comparison_sentence).

    Evidence를 실행 결과에서 지우지 않는다. 거부 응답에 Evidence를 붙이지 않는 원칙
    (evidence_and_answerability.md 원칙 7)은 producer가 이미 지킨다. 여기서 다시 걸러내면
    같은 판정이 두 곳에 생기고, 정상 응답의 provenance까지 함께 지울 위험이 남는다.

    Raises:
        UnsupportedExecutionStatusError: execution_status가 COMPLETED가 아닌 경우.
        MissingAnswerabilityStatusError: COMPLETED인데 판정이 비어 있는 경우.
        MissingAnswerSupportError: 답할 수 있다고 판정됐는데 근거가 없는 경우.
        UnsupportedResultShapeError: 서로 모순되는 조합이거나 표현 방법이 정해지지 않은
            결과 모양인 경우.
    """
    if result.execution_status is not ExecutionStatus.COMPLETED:
        raise UnsupportedExecutionStatusError(result.execution_status)

    status = result.answerability_status

    if status is None:
        raise MissingAnswerabilityStatusError

    _require_compatible_reason(status, result.answerability_reason)
    _require_empty_refusal_payload(result, status)
    _require_consistent_chain(result, status)

    return AnswerResponse(
        answer=_build_answer(result, status),
        status=status,
        reason=result.answerability_reason,
        facts=list(result.facts),
        events=list(result.events),
        event_links=list(result.event_links),
        event_histories=list(result.event_histories),
        evidence=list(result.evidence),
    )


def _require_compatible_reason(
    status: AnswerabilityStatus,
    reason: AnswerabilityReason | None,
) -> None:
    """
    판정 상태와 canonical 사유가 같은 것을 말하는지 확인한다.

    UNRESOLVED + NOT_FOUND처럼 서로 다른 판정을 동시에 담은 결과는 문장으로 옮기지 않는다.
    사유 쪽 문장을 고르면 "후보를 좁히지 못했다"가 사라지고, 상태 쪽 문장을 고르면 사유가
    사라진다. 어느 쪽을 골라도 판정 하나가 조용히 없어진다.

    사유가 비어 있는 경우도 상태별로 나눈다. 현재 producer가 사유 없이 만들지 않는 상태는
    허용하지 않는다(STATUSES_ALLOWING_NO_REASON 참조).
    """
    allowed = ALLOWED_REASONS_BY_STATUS.get(status)

    if allowed is None:
        raise UnsupportedResultShapeError(
            f"허용 사유가 정해지지 않은 answerability_status입니다: {status!r}. "
            "ALLOWED_REASONS_BY_STATUS에 추가하십시오."
        )

    if reason is None:
        if status in STATUSES_ALLOWING_NO_REASON:
            return

        permitted = ", ".join(sorted(item.value for item in allowed))

        raise UnsupportedResultShapeError(
            f"{status.value} 결과에는 canonical 사유가 있어야 합니다. "
            f"현재 producer가 만드는 사유: {permitted}. "
            "사유를 비우면 무엇 때문에 답하지 못했는지가 응답에서 사라집니다."
        )

    if reason in allowed:
        return

    permitted = ", ".join(sorted(item.value for item in allowed)) or "없음(사유를 싣지 않음)"

    raise UnsupportedResultShapeError(
        f"{status.value}에 실릴 수 없는 answerability_reason입니다: {reason.value!r}. "
        f"허용되는 사유: {permitted}. "
        "두 판정이 어긋난 결과이므로 문장으로 옮기지 않습니다."
    )


def _require_empty_refusal_payload(
    result: AgentExecutionResult,
    status: AnswerabilityStatus,
) -> None:
    """
    거부 응답에 근거가 붙어 있지 않은지 확인한다.

    걸러내지 않고 멈춘다. 여기서 facts/evidence를 비워 응답을 만들면 producer가 원칙을
    어긴 사실이 사라지고, 같은 판정이 두 곳에 생긴다. 원칙을 지키는 자리는 producer이고
    이곳은 그것을 확인하는 자리다.
    """
    if status is not AnswerabilityStatus.UNANSWERABLE:
        return

    reason = result.answerability_reason

    if reason not in REFUSAL_REASONS_WITHOUT_SUPPORTING_PAYLOAD:
        return

    if not result.facts and not result.evidence:
        return

    raise UnsupportedResultShapeError(
        f"{reason.value} 거부 결과에 facts {len(result.facts)}건, "
        f"evidence {len(result.evidence)}건이 붙어 있습니다. "
        "거부 응답에 근거를 붙이면 부분 답변으로 읽힙니다"
        "(evidence_and_answerability.md 원칙 7). 지우지 않고 여기서 멈춥니다."
    )


def _require_consistent_chain(
    result: AgentExecutionResult,
    status: AnswerabilityStatus,
) -> None:
    """
    EventHistory를 실은 결과의 판정과 chain_complete가 서로 맞는지 확인한다.

    체인 완결 여부가 판정을 정하므로 둘은 같은 사실의 두 표현이다. 어긋난 결과로 문장을
    만들면 미완결 체인을 완결로 말하거나 그 반대가 된다.

    EventHistory가 없는 결과는 보지 않는다. 이 불변식의 근거는 Pattern E의
    _judge_answerability 하나이며, Fact 기반 PARTIALLY 같은 다른 조합은 아직 producer도
    문서도 없어 여기서 판정하지 않는다.

    WITHHELD_IN_SOURCE도 보지 않는다. 체인 완결과 원문 유보는 서로 독립된 축이라
    (open_decisions.md D9) 체인이 완결된 채로 일부 field만 유보된 결과가 성립한다. 그런
    결과에 chain_complete=False를 요구하면, 완결된 체인을 미완결로 적어야 판정을 통과하게
    된다.
    """
    if not result.event_histories:
        return

    if result.answerability_reason is AnswerabilityReason.WITHHELD_IN_SOURCE:
        return

    expected = CHAIN_COMPLETE_BY_STATUS.get(status)

    if expected is None:
        return

    mismatched = [
        history for history in result.event_histories if history.chain_complete is not expected
    ]

    if mismatched:
        raise UnsupportedResultShapeError(
            f"{status.value} 결과인데 chain_complete가 {not expected}인 EventHistory가 "
            f"{len(mismatched)}건 있습니다. 체인 완결 여부와 판정이 어긋났습니다"
            "(execution_result_mapping.md §3)."
        )

    # PARTIALLY_ANSWERABLE의 사유가 무엇인지는 여기서 보지 않는다.
    # ALLOWED_REASONS_BY_STATUS가 그 상태에 INCOMPLETE_CHAIN과 WITHHELD_IN_SOURCE만
    # 허용하고 STATUSES_ALLOWING_NO_REASON이 사유를 비우는 것도 막으므로, 위의 유보 예외를
    # 지나 이 지점에 도달한 결과는 INCOMPLETE_CHAIN을 달고 있다.


def _build_answer(result: AgentExecutionResult, status: AnswerabilityStatus) -> str:
    """판정 상태별로 문장을 만든다. 네 상태를 하나로 뭉개지 않는다."""
    if status is AnswerabilityStatus.ANSWERABLE:
        return _supported_answer(result, partial=False)

    if status is AnswerabilityStatus.PARTIALLY_ANSWERABLE:
        return _supported_answer(result, partial=True)

    if status is AnswerabilityStatus.UNANSWERABLE:
        return _unavailable_answer(result)

    if status is AnswerabilityStatus.UNRESOLVED:
        return _unresolved_answer(result)

    raise UnsupportedResultShapeError(
        f"문장 형태가 정해지지 않은 answerability_status입니다: {status!r}."
    )


# --------------------------------------------------------------------------
# 답할 수 있다고 판정된 경우
# --------------------------------------------------------------------------


def _supported_answer(result: AgentExecutionResult, *, partial: bool) -> str:
    """
    확보한 구조화 결과를 문장으로 옮긴다.

    무엇을 확보했는지에 따라 Pattern이 갈린다. 표현 방법을 정하지 않은 조합은 현재 producer가
    만들지 않는 모양이고, 조용히 한쪽을 고르지 않는다.

    우열 판정을 가장 먼저 본다:
        ComparisonResult가 실린 결과는 "어느 쪽이 큰가"를 물은 질문의 답이다(Pattern D). 그
        결과에는 견준 값이 Fact로 함께 실리므로(Q10은 2건, Q11은 4건), Fact를 먼저 보면 값
        하나를 말하는 단일 Fact 문장이 여러 건 앞에서 멈춘다. 그 정지가 API에서 500이 된다.

        순서만 바꾸고 단일 Fact 경로는 그대로 둔다. 우열 판정이 없는 결과(Pattern A)는 이 분기를
        지나가지 않으므로, 값 하나를 물은 질문의 문장은 달라지지 않는다.

        판정이 있는데도 Fact 문장을 고르면 질문이 요구한 판정이 사라진 채 값 하나만 답하게 되고,
        형식은 정상인 응답이 나간다. Router가 미등록 intent를 가장 가까운 handler로 대신
        처리하지 않는 것과 같은 이유로, 여기서도 대신 고르지 않는다.
    """
    if result.document_summaries and (
        result.facts or result.comparisons or result.event_histories
    ):
        raise UnsupportedResultShapeError(
            f"DocumentSummary {len(result.document_summaries)}건이 Fact "
            f"{len(result.facts)}건 / ComparisonResult {len(result.comparisons)}건 / "
            f"EventHistory {len(result.event_histories)}건과 함께 있습니다. "
            "단일 문서 요약은 Fact도 계산도 만들지 않으므로 이 조합을 만드는 producer가 "
            "없습니다(evidence_and_answerability.md 유형별 최소 요건)."
        )

    if result.document_summaries:
        return _join(
            _document_summary_sentence(result.document_summaries),
            _limitation(result, partial=partial),
        )

    if result.comparisons and result.event_histories:
        raise UnsupportedResultShapeError(
            f"ComparisonResult {len(result.comparisons)}건과 "
            f"EventHistory {len(result.event_histories)}건이 함께 있습니다. "
            "두 결과를 한 문장으로 잇는 형태가 정해지지 않았습니다."
        )

    if result.comparisons:
        return _join(
            _comparison_sentence(result),
            _limitation(result, partial=partial),
        )

    if result.facts and result.event_histories:
        raise UnsupportedResultShapeError(
            f"Fact {len(result.facts)}건과 EventHistory {len(result.event_histories)}건이 "
            "함께 있습니다. 두 결과를 한 문장으로 잇는 형태가 정해지지 않았습니다."
        )

    # 시계열 증감 결과를 단일 Fact 경로보다 먼저 본다.
    #
    # Q07은 두 기간의 원천 Fact 2건과 그 둘에서 나온 Calculation 2건을 함께 싣는다. 아래 단일
    # Fact 경로는 여러 건에서 멈추도록 만들어졌고 그 가드는 그대로 두어야 하므로, 여기서 먼저
    # 가르지 않으면 답할 수 있는 결과가 그 가드에 걸린다.
    #
    # 가드를 넓히지 않고 이 모양만 연다. _is_temporal_delta_shape은 Fact 2건과 Calculation
    # 2건의 연산 이름, 그리고 두 Calculation이 그 Fact 2건만 참조한다는 것까지 본다. 조건 하나라도
    # 어긋나면 아래로 내려가 멈추므로, 임의의 다중 Fact 결과가 이 경로로 답이 되지 않는다.
    if result.facts and _is_temporal_delta_shape(result):
        return _join(
            _temporal_delta_sentence(result),
            _limitation(result, partial=partial),
        )

    # 계약 조회 결과를 단일 Fact 경로보다 먼저 본다.
    #
    # Q02는 한 계약을 서술하는 여러 값을 함께 확보한다. 아래 단일 Fact 경로는 여러 건에서
    # 멈추도록 만들어졌고 그 가드는 그대로 두어야 한다. 여기서 먼저 가르지 않으면 계약
    # 질문이 그 가드에 걸려 답이 사라진다.
    if result.facts and _is_contract_fact_bundle(result.facts):
        return _join(
            _contract_fact_sentence(result.facts),
            _limitation(result, partial=partial),
        )

    if result.facts:
        return _join(
            _single_fact_sentence(result.facts),
            _limitation(result, partial=partial),
        )

    if result.event_histories:
        # partial은 _limitation에만 넘긴다. 완결 여부는 _event_history_sentence가
        # history.chain_complete에서 직접 읽는다.
        return _join(
            _event_history_sentence(result.event_histories),
            _limitation(result, partial=partial),
        )

    if result.events:
        raise UnsupportedResultShapeError(
            f"Event {len(result.events)}건만 있는 결과의 문장 형태가 정해지지 않았습니다. "
            "현재 Pattern A는 Fact를, Pattern E는 EventHistory를 만듭니다."
        )

    raise MissingAnswerSupportError(
        "답할 수 있다고 판정됐지만 facts/events/event_histories/document_summaries가 모두 "
        "비어 있습니다. 근거 없이 문장을 만들지 않습니다."
    )


def _document_summary_sentence(summaries: list[DocumentSummary]) -> str:
    """
    DocumentSummary 1건을 문장으로 옮긴다.

    여러 건이면 막는다. Pattern B는 문서 1건의 지정 범위를 정리하는 Pattern이고, 여러 문서를
    한 문장으로 잇는 형태는 정해지지 않았다. 첫 건만 고르면 나머지 문서를 요약했다는 사실이
    사라진다(_single_fact_sentence와 같은 이유).

    구조화 결과에 적힌 것만 말한다:
        문서와 범위는 DocumentSummary가, 항목 수는 items가 정한다. 요약문을 여기서 새로
        쓰지 않는다. 이 모듈은 확정된 결과를 표현만 하며, 원문에서 새 서술을 만드는 일은
        이 경계 밖이다.

    항목 이름을 나열하지 않는다:
        앞의 몇 건만 적으면 그 몇 건이 나머지보다 중요하다는 뜻이 된다. 항목 순서는 원문
        배치일 뿐 중요도가 아니므로 그 주장은 결과가 보장하지 않는다. Q04의 해당 Section이
        실제로 그렇다. 앞쪽에 원재료 관련 본문이 오고 투자 내용은 뒤에 있어, 앞 몇 건을
        내세우면 질문이 묻지 않은 쪽이 답의 머리에 온다.

        순서를 다시 정해 고르지도 않는다. 그러려면 어떤 항목이 질문에 더 가까운지 판정하는
        규칙이 필요하고, 그것은 확정된 결과를 표현만 하는 이 모듈의 일이 아니다. 그래서 어느
        범위를 얼마나 확보했는지만 말한다. 항목 이름은 DocumentSummary.items에, 원문 발췌는
        Evidence에 그대로 남아 있으므로 나열하지 않아도 사라지는 것이 없다.

    범위와 내용을 한 문장으로 붙이지 않는다:
        "설비 투자 관련 내용 15건"이라고 적으면 15건 전부가 설비 투자 내용이라는 뜻이 된다.
        확보 단위는 Section이고 그 Section에는 요청 범위 밖의 내용도 함께 들어 있다
        (periodic.sections의 TODO). 그래서 "어느 범위를 골랐는가"와 "그 범위에 무엇이 얼마나
        있는가"를 나눠 적는다.
    """
    if len(summaries) > 1:
        raise UnsupportedResultShapeError(
            f"DocumentSummary가 {len(summaries)}건입니다. 단일 문서 요약은 문서 1건을 "
            "대상으로 하며, 여러 문서를 한 문장으로 잇는 형태는 정해지지 않았습니다."
        )

    summary = summaries[0]

    scope_label = SECTION_SCOPE_LABELS.get(summary.section_scope)

    if scope_label is None:
        raise UnsupportedResultShapeError(
            f"표기가 정해지지 않은 section_scope입니다: {summary.section_scope.value!r}. "
            "SECTION_SCOPE_LABELS에 추가하십시오."
        )

    counts = _summary_item_counts(summary)

    location = " > ".join(summary.section_path) if summary.section_path else summary.report_name

    selected = (
        f"{scope_label} 범위에 해당하는 부분은 "
        f"{summary.company} {summary.report_name}의 '{location}'입니다."
    )

    collected = f"이 범위의 내용은 {len(summary.items)}건({counts})입니다."

    return _join(selected, collected)


def _summary_item_counts(summary: DocumentSummary) -> str:
    """요약 항목을 종류별로 세어 '본문 9건, 표 6건' 형태로 적는다."""
    parts: list[str] = []

    for item_type, label in SUMMARY_ITEM_TYPE_LABELS.items():
        count = sum(1 for item in summary.items if item.item_type is item_type)

        if count:
            parts.append(f"{label} {count}건")

    unknown = sorted(
        {
            item.item_type.value
            for item in summary.items
            if item.item_type not in SUMMARY_ITEM_TYPE_LABELS
        }
    )

    if unknown:
        raise UnsupportedResultShapeError(
            f"표기가 정해지지 않은 요약 항목 종류입니다: {unknown}. "
            "SUMMARY_ITEM_TYPE_LABELS에 추가하십시오."
        )

    return ", ".join(parts)


def _is_temporal_delta_shape(result: AgentExecutionResult) -> bool:
    """
    같은 기업 두 기간의 증감 결과인가(Q07, Pattern C의 원천 지표 모양).

    좁게 본다. 개수만 맞는 결과를 통과시키면 다른 Pattern이 우연히 Fact 2건과 Calculation
    2건을 실었을 때 그것이 증감 문장으로 나간다. 그래서 네 가지를 함께 본다.

        Fact 2건 / Calculation 2건
        두 Calculation의 연산이 증감액과 증감률이고 그 순서다
        두 Calculation의 입력 참조가 순서까지 같다
        그 참조가 결과에 실린 Fact 2건이고 둘 다 Fact 참조다

    셋째 조건을 집합이 아니라 순서로 보는 이유:
        아래 문장 조립은 이전·이후를 증감액의 inputs에서 읽고, 증감률은 값을 그대로 옮긴다.
        두 Calculation이 같은 Fact 2건을 쓰되 순서가 서로 반대이면, 집합으로는 같으므로
        통과하지만 증감액은 A->B의 값이고 증감률은 B->A의 값이 된다. 그 둘을 한 문장에 적으면
        부호가 서로 어긋난 수치가 나란히 실리고, 받는 쪽은 어느 쪽이 맞는지 알 수 없다.

        순서까지 같은지 보면 그 결과는 문장이 되지 않고 아래 단일 Fact 경로로 내려가 멈춘다.
        조용히 틀린 문장이 나가는 쪽이 아니라 문장을 만들지 않는 쪽으로 실패한다.

    Fact 목록의 순서는 보지 않는다. 확보 순서일 뿐 시간 순서라는 보장이 없고, 시간 순서는
    증감액의 inputs가 정한다(_temporal_delta_sentence).

    ComparisonResult는 보지 않는다. 이 함수에 닿기 전에 우열 판정 분기가 먼저 갈라지므로
    여기까지 온 결과에는 판정이 없다.
    """
    if len(result.facts) != 2 or len(result.calculations) != 2:
        return False

    if tuple(item.operation for item in result.calculations) != (
        OPERATION_DELTA,
        OPERATION_DELTA_PCT,
    ):
        return False

    amount, rate = result.calculations

    ordered = [(item.input_type, item.input_id) for item in amount.inputs]

    if ordered != [(item.input_type, item.input_id) for item in rate.inputs]:
        return False

    if [input_type for input_type, _ in ordered] != [
        ResultInputType.FACT,
        ResultInputType.FACT,
    ]:
        return False

    # 같은 Fact를 두 번 가리키는 참조는 여기서 걸린다. 그 경우 아래 집합의 크기가 1이 되어
    # Fact 2건의 식별자 집합과 같아지지 않는다.
    return {input_id for _, input_id in ordered} == {
        fact.fact_id for fact in result.facts
    }


def _temporal_delta_sentence(result: AgentExecutionResult) -> str:
    """
    두 기간의 값과 그 증감을 문장으로 옮긴다(Q07).

    값을 만들지 않는다. 두 기간의 값은 Fact에서, 증감액과 증감률은 Calculation에서 그대로
    읽는다. 여기서 빼거나 나누면 응답에 실린 수치와 문장의 수치가 어긋날 수 있고, 그 어긋남은
    검산하는 쪽에서만 드러난다.

    이전·이후를 Fact 목록의 순서에서 읽지 않는다. 순서는 확보 요청 순서일 뿐 시간 순서라는
    보장이 없다. 증감액 Calculation의 inputs가 previous, current 순서라는 것이 계산 계층의
    계약이므로(calculator.delta) 그 참조를 따라 읽는다.

    늘었다/줄었다를 적지 않는다. 방향은 증감액의 부호에 이미 담겨 있고, 그 부호를 말로 옮기는
    것은 결과가 담고 있지 않은 판정을 문장이 새로 만드는 일이다. 우열 판정은 ComparisonResult가
    있는 결과에서만 한다(agent_patterns.md "Agent computes and judges. Builder presents.").
    """
    amount, rate = result.calculations

    previous = _referenced_fact(result, amount.inputs[0].input_id)

    current = _referenced_fact(result, amount.inputs[1].input_id)

    sources = [previous, current]

    company = _single_source_attribute(amount, sources, attribute="company")

    metric = _single_source_attribute(amount, sources, attribute="metric")

    scope = _single_source_attribute(amount, sources, attribute="accounting_scope")

    label = _metric_label(metric)

    return _join(
        f"{company}의 {_scope_phrase(scope)}{label}{_topic_particle(label)} "
        f"{_period_phrase(previous)}{_fact_value(previous)}, "
        f"{_period_phrase(current)}{_fact_value(current)}입니다.",
        f"증감액은 {_delta_amount(amount)}, 증감률은 {_calculation_value(rate)}입니다.",
    )


def _delta_amount(calculation: CalculationResult) -> str:
    """
    증감액 표기.

    _calculation_value를 쓰지 않는다. 그쪽이 보는 것은 파생지표의 결과 단위 어휘(percent)이고,
    증감액의 단위는 원천 Fact의 정규화 단위를 그대로 물려받은 값이다
    (calculator._shared_normalized_unit). 두 어휘를 한 대응표에 합치면 "percent"와 "KRW"가
    같은 자리에 놓이고, 어느 쪽 어휘로 적힌 단위인지 받는 쪽이 알 수 없게 된다.

    표기가 없으면 멈춘다. 정규화 단위를 그대로 문장에 적으면 한국어 문장에 추출 계층의 어휘가
    실린다(_normalized_value_with_unit과 같은 자리).
    """
    label = (
        FACT_NORMALIZED_UNIT_LABELS.get(calculation.unit)
        if calculation.unit is not None
        else None
    )

    if calculation.unit is not None and label is None:
        raise UnsupportedResultShapeError(
            f"표기가 정해지지 않은 증감액 단위입니다: {calculation.unit!r}. "
            "FACT_NORMALIZED_UNIT_LABELS에 추가하십시오."
        )

    rendered = _value_with_unit(calculation.value, label)

    if rendered is None:
        raise MissingAnswerSupportError(
            f"Calculation {calculation.calculation_id!r}에 값과 단위가 모두 갖춰진 표현이 "
            "없습니다. 값만 있거나 단위만 있는 상태로는 문장을 만들지 않습니다."
        )

    return rendered


def _is_contract_fact_bundle(facts: list[Fact]) -> bool:
    """거래소공시 계약 필드만으로 이루어진 결과인가.

    전부일 때만 참이다. 계약 필드와 재무 지표가 섞인 결과는 이 문장 형태가 정해지지 않았고,
    한쪽을 골라 적으면 나머지를 물었다는 사실이 사라진다. 그런 결과는 아래 단일 Fact 경로로
    내려가 거기서 멈춘다.
    """
    return all(fact.metric in CONTRACT_METRIC_LABELS for fact in facts)


def _contract_fact_sentence(facts: list[Fact]) -> str:
    """
    한 계약에서 확보한 값들을 문장 하나로 옮긴다.

    값을 만들지 않는다. 확보한 Fact에 적힌 것만 옮기며, 없는 필드는 문장에 나타나지 않는다.
    요청했는데 없는 필드는 여기까지 오지 않는다. adapt_contract_facts가 전부 아니면 하나도
    만들지 않으므로, 이 자리의 Fact 묶음은 이미 요청한 값을 전부 담고 있다.

    시작일과 종료일을 기간 하나로 적는다:
        "계약 시작일은 A입니다. 계약 종료일은 B입니다"로 나누어 적으면 두 값이 같은 계약의
        양 끝이라는 관계가 문장에서 사라진다. 한쪽만 있는 결과는 아래에서 막는다. 없는 쪽을
        추측해 기간을 만들지 않는다.

    회계연도와 연결/별도 구절을 붙이지 않는다:
        계약금액과 계약기간은 회계기간의 값이 아니고 거래소공시에는 연결·별도 구분이 없다.
        Fact도 그렇게 담겨 있으므로(fiscal_year=None, accounting_scope=not_applicable)
        여기서 덧붙일 것이 없다.
    """
    companies = {fact.company for fact in facts}

    if len(companies) > 1:
        raise UnsupportedResultShapeError(
            f"계약 문장에 기업이 {len(companies)}곳 실려 있습니다: "
            f"{', '.join(sorted(companies))}. 한 계약을 서술하는 문장이므로 기업은 한 곳이어야 "
            "하며, 한 곳을 골라 적지 않습니다."
        )

    by_metric = {fact.metric: fact for fact in facts}

    if len(by_metric) != len(facts):
        raise UnsupportedResultShapeError(
            "같은 계약 필드가 여러 건 실려 있습니다. 어느 값이 정본인지 정해지지 않아 "
            "하나를 골라 적지 않습니다."
        )

    start = by_metric.get(MetricConcept.CONTRACT_START_DATE)
    end = by_metric.get(MetricConcept.CONTRACT_END_DATE)

    # 계약기간이 없으면 어느 계약인지 문장이 밝히지 못한다.
    #
    # 한 기업은 한 해에 여러 건의 공급계약을 공시한다. "{회사}의 계약금액은 X원입니다"는
    # 형식은 자연스럽지만 어느 계약인지 특정되지 않은 문장이고, 읽는 쪽은 그 기업의 유일한
    # 계약으로 읽는다. 계약기간은 Fact에 이미 실려 있는 값이면서 그 계약을 다른 계약과
    # 구분해 준다.
    #
    # 상대방으로 밝히는 편이 더 낫지만 그 값은 여기까지 오지 않는다. 계약 상대방은
    # QueryPlan에 있고 AgentExecutionResult에는 자리가 없다. 자리를 만드는 것은 공유
    # Contract 개정이므로 이 범위에서 하지 않는다.
    if start is None or end is None:
        listed = ", ".join(sorted(metric.value for metric in by_metric))

        raise UnsupportedResultShapeError(
            f"계약기간 없이 계약 값을 문장으로 옮기지 않습니다: {listed}. "
            "어느 계약인지 문장에서 특정되지 않아, 그 기업의 유일한 계약으로 읽히는 답이 "
            "나갑니다. 계약을 식별하는 표현을 먼저 정하십시오."
        )

    clauses: list[str] = []

    amount = by_metric.get(MetricConcept.CONTRACT_AMOUNT)

    if amount is not None:
        label = CONTRACT_METRIC_LABELS[MetricConcept.CONTRACT_AMOUNT]

        clauses.append(f"{label}{_topic_particle(label)} {_fact_value(amount)}")

    clauses.append(
        f"계약기간은 {_contract_date_value(start)}부터 {_contract_date_value(end)}까지"
    )

    return f"{facts[0].company}의 {', '.join(clauses)}입니다."


def _contract_date_value(fact: Fact) -> str:
    """
    계약 일자 Fact의 값.

    단위를 요구하지 않는 것이 _fact_value와 다른 점이다. 저쪽은 수량을 적으므로 단위가 없으면
    자릿수를 알 수 없어 멈춘다. 날짜는 수량이 아니라 그 자체로 완결된 표기이며, 단위를
    요구하면 정상적인 Fact가 답하지 못하고 멈춘다.

    원문 표기를 먼저 쓴다. 공시 대조에 맞는 것은 문서에 적힌 표기다.
    """
    for candidate in (fact.raw_value, fact.normalized_value):
        if candidate is None:
            continue

        rendered = str(candidate).strip()

        if rendered:
            return rendered

    raise MissingAnswerSupportError(
        f"Fact {fact.fact_id!r}에 적을 일자 값이 없습니다. "
        "값이 비어 있는 상태로는 문장을 만들지 않습니다."
    )


def _single_fact_sentence(facts: list[Fact]) -> str:
    """
    Fact 1건을 문장으로 옮긴다.

    여러 건이면 막는다. 첫 건만 고르면 여러 값을 물은 질문에 한 값만 답하고도 완전한 답으로
    읽힌다. 다중 Fact를 다루는 Pattern(B/C/D)을 붙이는 쪽이 문장 형태를 먼저 정한다.
    """
    if len(facts) > 1:
        raise UnsupportedResultShapeError(
            f"Fact가 {len(facts)}건입니다. 이번 구현은 단일 Fact 질문만 문장으로 옮깁니다. "
            "첫 건만 골라 답하지 않습니다."
        )

    fact = facts[0]
    metric_label = _metric_label(fact.metric)
    value = _fact_value(fact)

    return (
        f"{fact.company}의 {_period_phrase(fact)}"
        f"{_scope_phrase(fact.accounting_scope)}"
        f"{metric_label}{_topic_particle(metric_label)} {value}입니다."
    )


def _metric_label(metric: MetricConcept) -> str:
    """
    단일 Fact 문장에 쓸 지표 표기.

    표기가 없으면 generic label을 만들어 문장을 완성하지 않고 멈춘다. 지원하지 않는 지표에
    표기만 붙이면 재무제표 축을 전제한 템플릿이 그 지표에도 적용돼, 대상이 특정되지 않은
    문장이 나간다(METRIC_LABELS 주석 참조).
    """
    label = METRIC_LABELS.get(metric)

    if label is None:
        raise UnsupportedResultShapeError(
            f"단일 Fact 문장이 지원하지 않는 metric입니다: {metric.value!r}. "
            f"현재 지원하는 지표: {', '.join(sorted(item.value for item in METRIC_LABELS))}. "
            "표기만 추가하지 말고 이 지표에 맞는 문장 템플릿을 먼저 정하십시오."
        )

    return label


def _period_phrase(fact: Fact) -> str:
    """
    회계기간 구절. 확정된 값만 적는다.

    fiscal_period가 연간 표기가 아니면 막는다. 분기 값을 연간처럼 적으면 기간이 틀린 답이 된다.
    """
    if fact.fiscal_period is not None and fact.fiscal_period not in FULL_YEAR_FISCAL_PERIODS:
        raise UnsupportedResultShapeError(
            f"표기가 정해지지 않은 fiscal_period입니다: {fact.fiscal_period!r}. "
            "연간 외 기간의 문장 형태를 먼저 정하십시오."
        )

    if fact.fiscal_year is None:
        return ""

    return f"{fact.fiscal_year}년 "


def _scope_phrase(scope: FactAccountingScope) -> str:
    """연결/별도 기준 구절. 확정하지 못한 scope는 구절 자체를 넣지 않는다."""
    if scope in SCOPE_OMITTED_IN_ANSWER:
        return ""

    label = ACCOUNTING_SCOPE_LABELS.get(scope)

    if label is None:
        raise UnsupportedResultShapeError(
            f"표기가 정해지지 않은 accounting_scope입니다: {scope.value!r}. "
            "ACCOUNTING_SCOPE_LABELS에 추가하거나 생략 대상으로 정하십시오."
        )

    return f"{label} 기준 "


def _fact_value(fact: Fact) -> str:
    """
    문장에 적을 값.

    값과 단위는 짝으로 다룬다. 한쪽만 있으면 그 표현을 쓰지 않는다:
        값만 쓰면 "매출액은 1,234입니다"처럼 단위 없는 금액이 되어 자릿수를 알 수 없다.
        단위만 있는 쪽은 애초에 적을 값이 없다.

    원문 짝(raw_value/raw_unit)을 먼저 쓴다. 정규화 값은 배수를 곱해 만든 파생값이므로,
    원문에 적힌 표기가 있으면 그것이 공시 대조에 맞다. 값을 여기서 다시 계산하지 않는다.

    짝이 하나도 완전하지 않으면 멈춘다. 남은 한쪽을 섞어 쓰면(원문 값 + 정규화 단위) 배수가
    맞지 않는 수치가 나간다.
    """
    raw = _value_with_unit(fact.raw_value, fact.raw_unit)

    if raw is not None:
        return raw

    # 원문 짝이 완전할 때는 정규화 쪽을 보지 않는다. 정규화 단위의 표기가 정해지지 않았다는
    # 사실이 원문으로 답할 수 있는 Fact까지 멈추게 하면, 쓰지도 않을 값 때문에 답이 사라진다.
    normalized = _normalized_value_with_unit(fact)

    if normalized is not None:
        return normalized

    raise MissingAnswerSupportError(
        f"Fact {fact.fact_id!r}에 값과 단위가 모두 갖춰진 표현이 없습니다. "
        "값만 있거나 단위만 있는 상태로는 문장을 만들지 않습니다."
    )


def _normalized_value_with_unit(fact: Fact) -> str | None:
    """
    Fact의 정규화 표현. 단위는 사용자 표기로 옮겨서 적는다.

    정규화 단위는 추출 계층이 붙인 canonical 어휘이고 사용자 문장의 말이 아니다. 그대로 적으면
    한국어 문장에 "2000000000KRW"가 실린다. 영문 enum 값을 문장에 넣지 않는 것과 같은 자리이며,
    다른 점은 열쇠가 Contract enum이 아니라 추출 계층의 문자열이라는 것뿐이다.

    값을 바꾸지 않는다. Fact.normalized_unit은 그대로 두고 문장에 적을 표기만 고른다. 여기서
    Fact를 고쳐 쓰면 응답의 구조화 결과와 문장이 서로 다른 단위를 말하게 된다.

    두 경우를 나눈다:
        정규화 표현이 없음(None)      Fact가 정규화되지 않았다. 원문으로 답할 수 있으면 답한다.
        표기가 정해지지 않음(예외)     추출 계층이 새 단위를 만들었는데 표기를 정하지 않았다.
                                      generic 표기를 만들지 않고 멈춘다.
    """
    if fact.normalized_unit is None:
        return None

    label = FACT_NORMALIZED_UNIT_LABELS.get(fact.normalized_unit)

    if label is None:
        raise UnsupportedResultShapeError(
            f"표기가 정해지지 않은 정규화 단위입니다: {fact.normalized_unit!r}. "
            "FACT_NORMALIZED_UNIT_LABELS에 추가하십시오. 표기를 정하지 않은 채로 적으면 "
            "사용자 문장에 추출 계층의 어휘가 그대로 실립니다."
        )

    return _value_with_unit(fact.normalized_value, label)


def _value_with_unit(value: float | str | None, unit: str | None) -> str | None:
    """값과 단위가 모두 있을 때만 붙여서 돌려준다. 하나라도 없으면 None."""
    if value is None or unit is None:
        return None

    rendered_value = str(value).strip()
    rendered_unit = unit.strip()

    if not rendered_value or not rendered_unit:
        return None

    return f"{rendered_value}{rendered_unit}"


# --------------------------------------------------------------------------
# 우열 판정을 실은 결과
#
# Pattern D가 만드는 모양이다. Agent가 이미 확정한 관계를 읽어 옮기기만 하며, 값을 다시 견주지도
# 다시 계산하지도 않는다(agent_patterns.md "Agent와 Answer Builder의 책임 경계").
#
# 그 경계를 지키는 방법은 "계산하지 않기로 한다"가 아니라 "계산할 것이 남아 있지 않게 읽는다"에
# 가깝다. relation은 ComparisonResult에서, 수치는 Fact와 CalculationResult에서 그대로 온다.
# 이 구획에 비교 연산자도 산술 연산자도 두지 않는 것이 그 확인 방법이다.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _ComparisonOperand:
    """
    견준 대상 한쪽의 표기.

        name     관계 문장이 가리키는 이름. 현재는 기업이다.
        subject  값 문장의 주어("<기업>의 2025년 연결 기준 매출액").
        value    그 주어의 값. 결과에 적힌 것을 옮긴 문자열이며 여기서 만들지 않는다.

    Fact와 CalculationResult를 하나의 모양으로 좁힌다. 견준 대상이 원천 값인지 파생값인지는 표기를
    만드는 자리에서 끝나고, 관계 문장은 그 차이를 알 필요가 없다. 알게 두면 Q10과 Q11이 서로 다른
    관계 문장을 갖게 되고 관계 어휘가 두 벌이 된다.
    """

    name: str
    subject: str
    value: str


def _comparison_sentence(result: AgentExecutionResult) -> str:
    """
    우열 판정 1건을 문장으로 옮긴다.

    관계를 다시 판정하지 않는다. ComparisonResult.relation이 정본이며, 이 함수가 값을 견주면 같은
    판정이 두 곳에 생긴다. 두 판정이 갈리는 날 결과에 적힌 것과 다른 문장이 나가고, 그 어긋남은
    응답 어디에도 남지 않는다. 값이 관계와 맞는지도 확인하지 않는다. 확인하려면 여기서 값을 견줘야
    하고, 그 순간 판정이 두 곳에 생기는 것은 같다.

    값도 다시 만들지 않는다. Q11의 비율을 Fact에서 다시 계산하지 않는 것이 특히 그렇다. 다시
    계산하면 반올림 자릿수가 계산 계층과 달라질 수 있고, 그러면 답변에 실린 수치와 우열 판정의
    근거가 된 수치가 서로 다른 값이 된다(calculator.compare_calculations).
    """
    if len(result.comparisons) > 1:
        raise UnsupportedResultShapeError(
            f"ComparisonResult가 {len(result.comparisons)}건입니다. "
            "이번 구현은 1건만 문장으로 옮깁니다. 첫 건만 골라 답하지 않습니다."
        )

    comparison = result.comparisons[0]

    if comparison.left.input_type is not comparison.right.input_type:
        # 원천 값과 파생값을 견준 판정이다. 한 문장에서 두 대상을 같은 축으로 말할 방법이 정해지지
        # 않았고, 한쪽 표기에 맞추면 다른 쪽이 무엇인지가 문장에서 사라진다.
        raise UnsupportedResultShapeError(
            f"우열 판정 {comparison.comparison_id!r}이 서로 다른 종류를 견줍니다: "
            f"{comparison.left.input_type.value!r} vs {comparison.right.input_type.value!r}. "
            "원천 값과 파생값을 한 문장으로 견주는 형태가 정해지지 않았습니다."
        )

    if comparison.left == comparison.right:
        # 같은 대상을 두 번 가리킨 판정이다. 개수만 맞고 견준 것이 없으며, 그대로 문장을 만들면
        # 한 값이 자기보다 크다는 진술이 나간다.
        raise UnsupportedResultShapeError(
            f"우열 판정 {comparison.comparison_id!r}의 left와 right가 같은 대상"
            f"({comparison.left.input_id!r})을 가리킵니다. 견준 것이 없는 판정입니다."
        )

    left = _comparison_operand(result, comparison.left)
    right = _comparison_operand(result, comparison.right)

    return _join(
        f"{left.subject}{_topic_particle(left.subject)} {left.value}, "
        f"{right.subject}{_topic_particle(right.subject)} {right.value}입니다.",
        _relation_clause(comparison.relation, left=left, right=right),
    )


def _relation_clause(
    relation: ComparisonRelation,
    *,
    left: _ComparisonOperand,
    right: _ComparisonOperand,
) -> str:
    """
    관계 이름을 문장으로 옮긴다. 두 값을 보지 않는다.

    값을 인자로 받지 않는 것이 이 함수의 계약이다. 받으면 "값이 관계와 맞는지" 같은 검사가 언제든
    들어올 수 있고, 그 검사는 곧 두 번째 판정이다.
    """
    template = COMPARISON_RELATION_TEMPLATES.get(relation)

    if template is None:
        raise UnsupportedResultShapeError(
            f"문장이 정해지지 않은 ComparisonRelation입니다: {relation.value!r}. "
            "COMPARISON_RELATION_TEMPLATES에 추가하십시오."
        )

    return template.format(left=left.name, right=right.name)


def _comparison_operand(
    result: AgentExecutionResult,
    reference: ResultInputReference,
) -> _ComparisonOperand:
    """
    참조가 가리키는 대상을 결과 안에서 찾아 표기를 만든다.

    참조를 따라가기만 하고 대상을 새로 만들지 않는다. 결과에 없는 참조를 만나면 비슷한 값을 골라
    채우지 않는다. 그 순간 답변이 가리키는 값과 판정이 참조한 값이 달라진다.
    """
    if reference.input_type is ResultInputType.FACT:
        return _fact_operand(_referenced_fact(result, reference.input_id))

    if reference.input_type is ResultInputType.CALCULATION:
        return _calculation_operand(result, _referenced_calculation(result, reference.input_id))

    raise UnsupportedResultShapeError(
        f"표기 방법이 정해지지 않은 비교 대상 종류입니다: {reference.input_type!r}."
    )


def _referenced_fact(result: AgentExecutionResult, fact_id: str) -> Fact:
    """결과에 실린 Fact 중 참조가 가리키는 것. 없으면 멈춘다."""
    for fact in result.facts:
        if fact.fact_id == fact_id:
            return fact

    raise MissingAnswerSupportError(
        f"참조가 가리키는 Fact {fact_id!r}가 결과에 없습니다. "
        "근거가 끊긴 참조로 문장을 만들지 않습니다."
    )


def _referenced_calculation(
    result: AgentExecutionResult,
    calculation_id: str,
) -> CalculationResult:
    """결과에 실린 CalculationResult 중 참조가 가리키는 것. 없으면 멈춘다."""
    for calculation in result.calculations:
        if calculation.calculation_id == calculation_id:
            return calculation

    raise MissingAnswerSupportError(
        f"참조가 가리키는 Calculation {calculation_id!r}가 결과에 없습니다. "
        "근거가 끊긴 참조로 문장을 만들지 않습니다."
    )


def _fact_operand(fact: Fact) -> _ComparisonOperand:
    """
    원천 Fact 한 건의 표기(Q10 계열).

    원문 표기와 정규화 값을 함께 적는다. 우열은 정규화 값에서 판정되고(calculator.compare_facts),
    사용자가 공시와 대조하는 것은 원문 표기다. 한쪽만 적으면 둘 중 하나가 불가능해진다
    (evidence_and_answerability.md Q10 "원문 단위 값과 정규화 값을 병기").

    단위 표기가 다른 두 기업이 그 규칙의 이유다. 원문만 적으면 900,000천원이 1,000백만원보다 커
    보이고, 그때 판정 문장이 값과 어긋나 읽힌다. 정규화 값만 적으면 어느 쪽도 공시에 적힌 그대로가
    아니어서 대조할 수 없다.

    두 표기가 같은 문자열이면 한 번만 적는다. 같은 값을 괄호로 한 번 더 적어 읽는 사람이 다른 값이
    있다고 오해할 이유가 없다.

    정규화 단위는 사용자 표기로 옮겨 적는다(_normalized_value_with_unit). 원문 표기는 공시에
    적힌 그대로 두고, 옮기는 것은 정규화 쪽뿐이다.

    지표·기간·기준 표기는 단일 Fact 문장과 같은 대응표를 쓴다. 같은 재무제표 축(회계연도 ×
    연결/별도)을 전제한 문장이라 표기가 갈릴 이유가 없고, 갈리면 같은 Fact가 질문에 따라 다른
    이름으로 불린다.
    """
    metric_label = _metric_label(fact.metric)

    subject = (
        f"{fact.company}의 {_period_phrase(fact)}"
        f"{_scope_phrase(fact.accounting_scope)}{metric_label}"
    )

    normalized = _normalized_value_with_unit(fact)

    if normalized is None:
        raise MissingAnswerSupportError(
            f"Fact {fact.fact_id!r}에 정규화 값과 단위가 갖춰져 있지 않습니다. "
            "우열 판정은 정규화 값에서 나오므로, 그 값 없이 판정 문장을 만들지 않습니다."
        )

    raw = _value_with_unit(fact.raw_value, fact.raw_unit)

    value = normalized if raw is None or raw == normalized else f"{raw}(정규화 {normalized})"

    return _ComparisonOperand(name=fact.company, subject=subject, value=value)


def _calculation_operand(
    result: AgentExecutionResult,
    calculation: CalculationResult,
) -> _ComparisonOperand:
    """
    파생값 한 건의 표기(Q11 계열).

    원천 Fact를 읽되 값을 다시 만들지 않는다. 참조를 따라가는 이유는 대상을 부르기 위해서다.
    파생값이 어느 기업의 어느 기간·기준 값인지는 CalculationResult에 없고 입력 참조를 따라가야만
    나온다. 참조를 따라가 이름을 읽는 것과 그 값으로 비율을 다시 만드는 것은 다른 일이다. 여기서
    Fact에서 읽는 것은 company·fiscal_year·accounting_scope·metric뿐이고, 수치는 계산 계층이
    확정한 calculation.value를 그대로 쓴다.

    입력이 하나의 기업·기간·기준으로 모이지 않으면 멈춘다. 그런 파생값은 "누구의 무엇"인지를 한
    구절로 부를 수 없고, 한쪽을 골라 적으면 나머지가 문장에서 사라진다. 정상 경로에서는 guard①과
    guard②가 같은 것을 이미 확인하므로 여기서 새로 걸릴 일은 없다.
    """
    sources: list[Fact] = []

    for item in calculation.inputs:
        if item.input_type is not ResultInputType.FACT:
            # 파생값을 다시 입력으로 쓴 계산이다. 개념 이름을 대응표에서 되짚는 방법이 원천 지표
            # 조합을 전제하므로, 여러 단계를 거친 값에는 붙일 이름이 없다.
            raise UnsupportedResultShapeError(
                f"Calculation {calculation.calculation_id!r}이 {item.input_id!r}을 입력으로 "
                "씁니다. 원천 Fact가 아닌 입력을 가진 파생값의 표기가 정해지지 않았습니다."
            )

        sources.append(_referenced_fact(result, item.input_id))

    company = _single_source_attribute(calculation, sources, attribute="company")

    scope = _single_source_attribute(calculation, sources, attribute="accounting_scope")

    _single_source_attribute(calculation, sources, attribute="fiscal_year")

    subject = (
        f"{company}의 {_period_phrase(sources[0])}"
        f"{_scope_phrase(scope)}{_derived_metric_label(calculation, sources)}"
    )

    return _ComparisonOperand(
        name=company,
        subject=subject,
        value=_calculation_value(calculation),
    )


def _single_source_attribute(
    calculation: CalculationResult,
    sources: list[Fact],
    *,
    attribute: str,
) -> Any:
    """
    파생값의 입력 Fact들이 한 값으로 모이는 속성을 읽는다. 갈리면 멈춘다.

    첫 입력의 값을 그냥 쓰지 않는다. 입력이 갈려 있는데 첫 건을 고르면 그 파생값이 실제로
    무엇에서 나왔는지가 문장에서 사라지고, 사라진 사실은 결과 어디에도 남지 않는다.
    """
    values = [getattr(fact, attribute) for fact in sources]

    if len(set(values)) != 1:
        raise UnsupportedResultShapeError(
            f"Calculation {calculation.calculation_id!r}의 입력 {attribute}가 한 값으로 모이지 "
            f"않습니다: {values}. 어느 것의 값인지 정해지지 않아 표기를 만들지 않습니다."
        )

    return values[0]


def _derived_metric_label(calculation: CalculationResult, sources: list[Fact]) -> str:
    """
    파생값의 개념 표기.

    CalculationResult에는 어떤 파생 개념이었는지가 남지 않는다. 연산 이름과 입력 지표의 순서로
    대응표(agent.derived_metrics)를 되짚어 개념을 찾는다. 그 대응표가 정의의 정본이므로
    분자·분모 조합을 이 파일에 다시 적지 않는다.

    찾지 못하면 "비율" 같은 generic 표기를 만들지 않고 멈춘다. 정의를 모르는 값에 이름을 붙이면
    다른 개념의 수치가 영업이익률로 읽힐 수 있고, 그것이 틀렸다는 사실은 문장 어디에도 남지 않는다.
    """
    source_metrics = tuple(fact.metric for fact in sources)

    for metric in DerivedMetric:
        spec = get_derived_metric_spec(metric)

        if spec.operation != calculation.operation:
            continue

        if (spec.numerator, spec.denominator) != source_metrics:
            continue

        label = DERIVED_METRIC_LABELS.get(metric)

        if label is None:
            raise UnsupportedResultShapeError(
                f"표기가 정해지지 않은 파생지표입니다: {metric.value!r}. "
                "DERIVED_METRIC_LABELS에 추가하십시오."
            )

        return label

    listed = ", ".join(item.value for item in source_metrics) or "없음"

    raise UnsupportedResultShapeError(
        f"대응표에 없는 파생값입니다: operation={calculation.operation!r}, 입력 지표={listed}. "
        "이름을 확인할 수 없는 값에 표기를 붙이지 않습니다."
    )


def _calculation_value(calculation: CalculationResult) -> str:
    """
    문장에 적을 파생값. 계산 계층이 확정한 값을 그대로 옮긴다.

    반올림하지도 다시 계산하지도 않는다. 저장된 값이 우열 판정의 근거이므로
    (calculator.compare_calculations), 문장에 다른 자릿수를 적으면 답변에 실린 두 수치로
    검산했을 때 관계가 뒤집혀 보일 수 있다.

    값과 단위는 Fact와 같이 짝으로 다룬다. 단위 없는 비율은 백분율인지 배수인지 알 수 없다.
    """
    label = CALCULATION_UNIT_LABELS.get(calculation.unit) if calculation.unit else None

    if calculation.unit is not None and label is None:
        raise UnsupportedResultShapeError(
            f"표기가 정해지지 않은 계산 결과 단위입니다: {calculation.unit!r}. "
            "CALCULATION_UNIT_LABELS에 추가하십시오."
        )

    rendered = _value_with_unit(calculation.value, label)

    if rendered is None:
        raise MissingAnswerSupportError(
            f"Calculation {calculation.calculation_id!r}에 값과 단위가 모두 갖춰진 표현이 "
            "없습니다. 값만 있거나 단위만 있는 상태로는 문장을 만들지 않습니다."
        )

    return rendered


def _event_history_sentence(histories: list[EventHistory]) -> str:
    """
    EventHistory 1건을 문장으로 옮긴다.

    완결 여부는 history.chain_complete만 본다. 판정 상태(PARTIALLY_ANSWERABLE)는 보지
    않는다:
        둘은 다른 축이다. 체인 완결은 "이력을 끝까지 봤는가"이고, 판정의 부분성은
        "질문에 다 답했는가"다. WITHHELD_IN_SOURCE가 그 둘을 갈라 놓는다. 체인은 끝까지
        확보했는데 그 안의 일부 field만 원문에서 유보된 결과가 성립하고(open_decisions.md
        D9), 그것이 PARTIALLY_ANSWERABLE + chain_complete=True다.

        상태로 완결 여부를 정하면 그 결과가 "관련 공시 N건까지 확인했습니다"로 나간다.
        끝까지 확인한 이력을 중간에서 멈춘 것처럼 말하는 문장이며, 같은 응답에 실려 나가는
        chain_complete=True와도 어긋난다. 무엇이 남았는지는 _limitation이 사유로 말한다.

    구조화 결과에 적힌 것만 말한다. 공시 건수는 document_ids에서, 변경 건수는 changes에서
    센다.

    변경 항목의 '이름'은 문장에 넣지 않는다:
        FieldChange.field_name은 canonical 영문 식별자(contract_amount 등)이고, 한국어 문장에
        그대로 넣으면 응답에 내부 식별자가 섞인다. 한글 원문 라벨은 event.contract_adapter가
        raw_field_name gap으로 보내므로 현재 production에서 FieldChange.raw_field는 항상
        비어 있다.

        여기서 영문 이름을 한글로 다시 옮기는 대응표를 만들면 event.extractor가 이미 들고
        있는 라벨 어휘가 두 벌이 되고, 어느 쪽이 정본인지 알 수 없게 된다. 이름과 변경
        전/후 값까지 담는 narrative는 P1이며, Contract에 원문 라벨을 실을 자리가 생긴 뒤에
        붙이는 것이 맞다. 그때까지 구조화 결과(event_histories)에는 전부 실려 나간다.
    """
    if len(histories) > 1:
        raise UnsupportedResultShapeError(
            f"EventHistory가 {len(histories)}건입니다. 이번 구현은 1건만 문장으로 옮깁니다."
        )

    history = histories[0]
    document_count = len(history.document_ids)

    if document_count == 0:
        raise MissingAnswerSupportError(
            f"EventHistory {history.event_id!r}에 document_ids가 없습니다. "
            "확인한 공시가 없는 이력을 문장으로 만들지 않습니다."
        )

    incomplete = not history.chain_complete

    if incomplete:
        # 체인을 다 보지 못했다. "변경이 없었다"는 확정 진술을 붙이지 않는다.
        sentence = f"관련 공시 {document_count}건까지 확인했습니다."

    elif document_count == 1:
        return "관련 공시 1건을 확인했으며, 이후 변경된 내용은 확인되지 않았습니다."

    else:
        sentence = f"관련 공시 {document_count}건으로 이어지는 이력을 확인했습니다."

    change_count = len(history.changes)

    if change_count:
        return f"{sentence} 확인된 변경 항목은 {change_count}건입니다."

    if incomplete:
        return sentence

    return f"{sentence} 확인된 변경 항목은 없습니다."


def _limitation(result: AgentExecutionResult, *, partial: bool) -> str:
    """
    일부만 답한 경우 무엇이 남았는지 밝히는 문장.

    사유가 비어 있는 경우는 보지 않는다. _require_compatible_reason이
    PARTIALLY_ANSWERABLE에 사유를 요구하므로 여기 도달한 결과는 사유를 달고 있다.
    """
    if not partial:
        return ""

    return LIMITATION_MESSAGE_BY_REASON.get(
        result.answerability_reason,
        LIMITATION_WITHOUT_REASON_MESSAGE,
    )


# --------------------------------------------------------------------------
# 답할 수 없다고 판정된 경우
# --------------------------------------------------------------------------


def _unavailable_answer(result: AgentExecutionResult) -> str:
    """
    답변 불가 판정을 사유별 문장으로 옮긴다.

    사유를 하나의 문장으로 뭉개지 않는다. 사유가 다르면 사용자가 할 수 있는 다음 행동이
    다르고, 평가도 두 실패를 따로 센다.
    """
    # 사유가 비어 있는 UNANSWERABLE은 _require_compatible_reason이 이미 막았다.
    reason = result.answerability_reason

    if reason is AnswerabilityReason.OUT_OF_SCOPE_POLICY:
        return _policy_refusal_answer(result)

    return _message_for_reason(reason)


def _policy_refusal_answer(result: AgentExecutionResult) -> str:
    """
    정책 거부 문장.

    OUT_OF_SCOPE_POLICY 하나에 F1~F4가 모인다(normalization의 대응표). 어느 판정이었는지는
    answerability_detail.reason만 구분하므로 그 값으로 문장을 고른다. 값이 없거나 대응표에
    없는 값이면 사유를 단정하지 않는 기본 문장을 쓴다.
    """
    detail = result.answerability_detail

    if detail is None:
        return UNAVAILABLE_MESSAGE_BY_REASON[AnswerabilityReason.OUT_OF_SCOPE_POLICY]

    return POLICY_REFUSAL_MESSAGE_BY_DETAIL_REASON.get(
        detail.reason,
        UNAVAILABLE_MESSAGE_BY_REASON[AnswerabilityReason.OUT_OF_SCOPE_POLICY],
    )


def _unresolved_answer(result: AgentExecutionResult) -> str:
    """
    확정하지 못한 판정을 문장으로 옮긴다.

    "정보가 없습니다"로 만들지 않는다. UNRESOLVED는 후보를 찾았는데 하나로 좁히지 못한
    것이고, NOT_FOUND는 찾지 못한 것이다. 두 문장이 같으면 사용자가 질문을 좁혀야 한다는
    사실이 사라진다.
    """
    reason = result.answerability_reason

    if reason is None:
        return UNRESOLVED_WITHOUT_REASON_MESSAGE

    return _message_for_reason(reason)


def _message_for_reason(reason: AnswerabilityReason) -> str:
    message = UNAVAILABLE_MESSAGE_BY_REASON.get(reason)

    if message is None:
        raise UnsupportedResultShapeError(
            f"문장이 정해지지 않은 answerability_reason입니다: {reason.value!r}. "
            "UNAVAILABLE_MESSAGE_BY_REASON에 추가하십시오."
        )

    return message


# --------------------------------------------------------------------------
# 문장 조립 보조
# --------------------------------------------------------------------------


def _join(*sentences: str) -> str:
    """비어 있지 않은 문장만 한 칸 띄어 잇는다."""
    return " ".join(sentence for sentence in sentences if sentence)


def _topic_particle(word: str) -> str:
    """
    앞말에 붙일 주제 조사(은/는).

    받침이 있으면 '은', 없으면 '는'이다. 한글 음절은 (코드포인트 - 0xAC00) % 28로 종성
    유무를 알 수 있다. 한글이 아니면 '는'을 쓴다.
    """
    if not word:
        return "는"

    last = word[-1]

    if "가" <= last <= "힣":
        return "는" if (ord(last) - 0xAC00) % 28 == 0 else "은"

    return "는"
