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

from disclosure_agent.agent.evidence import Evidence
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.agent.safety import (
    REASON_FORWARD_LOOKING_PREDICTION,
    REASON_INVESTMENT_ADVICE,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    AnswerResponse,
    EventHistory,
    Fact,
)
from disclosure_agent.periodic.extractor import FinancialFact
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

# periodic.extractor가 올리는 ExtractionBlockedError.reason 중 agent가 다시 해석하는 것.
REASON_NO_METRIC_ACODE = "no_metric_acode"
REASON_NO_MATCHING_CONTEXT = "no_matching_context"
REASON_AMBIGUOUS_CANDIDATES = "ambiguous_candidates"

# Pattern E가 자체 판정으로 만드는 사유.
REASON_NO_ORIGINAL_FILING = "no_original_filing"
REASON_AMBIGUOUS_ROOT = "ambiguous_root"

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
}

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

# 판정은 났는데 canonical 사유가 비어 있는 경우의 문장.
#
# 현재 producer는 UNANSWERABLE이면 사유를 채우고, Pattern E의 루트 미확정만 사유 없이
# UNRESOLVED로 남긴다(execution_result_mapping.md §3). 없는 사유를 골라 채우지 않고,
# 사유를 단정하지 않는 문장을 쓴다.
UNANSWERABLE_WITHOUT_REASON_MESSAGE = "확보한 근거로는 답변할 수 없습니다."
UNRESOLVED_WITHOUT_REASON_MESSAGE = "대상을 하나로 확정하지 못해 답변을 확정할 수 없습니다."

# 일부만 답할 수 있을 때 확보분 뒤에 붙는 한계 문장.
LIMITATION_MESSAGE_BY_REASON: dict[AnswerabilityReason, str] = {
    AnswerabilityReason.INCOMPLETE_CHAIN: (
        "다만 이력의 일부를 보유 공시 안에서 확정하지 못해 전체 이력은 확인하지 못했습니다."
    ),
}

LIMITATION_WITHOUT_REASON_MESSAGE = "다만 요청하신 내용의 일부는 확인하지 못했습니다."


def build_answer_response(result: AgentExecutionResult) -> AnswerResponse:
    """
    정상적으로 끝난 실행 결과 하나를 공개 응답 Contract로 옮긴다.

    답변 가능성 판정(status/reason)은 그대로 싣는다. 두 필드의 어휘가 실행 Contract와 같아서
    새로 고를 것이 없고, 새로 고르면 평가가 대조하는 canonical 값이 두 벌이 된다.

    구조화 결과는 AnswerResponse에 자리가 있는 것만 손실 없이 옮긴다. calculations와
    warnings는 현재 AnswerResponse에 자리가 없어 실리지 않으며, 이번 범위에서 Contract를
    고치지 않는다.

    Evidence를 실행 결과에서 지우지 않는다. 거부 응답에 Evidence를 붙이지 않는 원칙
    (evidence_and_answerability.md 원칙 7)은 producer가 이미 지킨다. 여기서 다시 걸러내면
    같은 판정이 두 곳에 생기고, 정상 응답의 provenance까지 함께 지울 위험이 남는다.

    Raises:
        UnsupportedExecutionStatusError: execution_status가 COMPLETED가 아닌 경우.
        MissingAnswerabilityStatusError: COMPLETED인데 판정이 비어 있는 경우.
        MissingAnswerSupportError: 답할 수 있다고 판정됐는데 근거가 없는 경우.
        UnsupportedResultShapeError: 표현 방법이 정해지지 않은 결과 모양인 경우.
    """
    if result.execution_status is not ExecutionStatus.COMPLETED:
        raise UnsupportedExecutionStatusError(result.execution_status)

    status = result.answerability_status

    if status is None:
        raise MissingAnswerabilityStatusError

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

    무엇을 확보했는지에 따라 Pattern이 갈린다. 둘 다 있거나 둘 다 없는 모양은 현재 producer가
    만들지 않으므로 표현 방법을 정하지 않았고, 조용히 한쪽을 고르지 않는다.
    """
    if result.facts and result.event_histories:
        raise UnsupportedResultShapeError(
            f"Fact {len(result.facts)}건과 EventHistory {len(result.event_histories)}건이 "
            "함께 있습니다. 두 결과를 한 문장으로 잇는 형태가 정해지지 않았습니다."
        )

    if result.facts:
        return _join(
            _single_fact_sentence(result.facts),
            _limitation(result, partial=partial),
        )

    if result.event_histories:
        return _join(
            _event_history_sentence(result.event_histories, partial=partial),
            _limitation(result, partial=partial),
        )

    if result.events:
        raise UnsupportedResultShapeError(
            f"Event {len(result.events)}건만 있는 결과의 문장 형태가 정해지지 않았습니다. "
            "현재 Pattern A는 Fact를, Pattern E는 EventHistory를 만듭니다."
        )

    raise MissingAnswerSupportError(
        "답할 수 있다고 판정됐지만 facts/events/event_histories가 모두 비어 있습니다. "
        "근거 없이 문장을 만들지 않습니다."
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

    원문 표기(raw_value/raw_unit)를 먼저 쓴다. 정규화 값은 배수를 곱해 만든 파생값이므로,
    원문에 적힌 표기가 있으면 그것이 공시 대조에 맞다. 값을 여기서 다시 계산하지 않는다.
    """
    raw_value = (fact.raw_value or "").strip()

    if raw_value:
        return f"{raw_value}{(fact.raw_unit or '').strip()}"

    normalized_value = (
        "" if fact.normalized_value is None else str(fact.normalized_value).strip()
    )

    if normalized_value:
        return f"{normalized_value}{(fact.normalized_unit or '').strip()}"

    raise MissingAnswerSupportError(
        f"Fact {fact.fact_id!r}에 raw_value도 normalized_value도 없습니다. "
        "값 없이 문장을 만들지 않습니다."
    )


def _event_history_sentence(histories: list[EventHistory], *, partial: bool) -> str:
    """
    EventHistory 1건을 문장으로 옮긴다.

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

    if partial:
        # 체인을 다 보지 못했다. "변경이 없었다"는 확정 진술을 붙이지 않는다.
        sentence = f"관련 공시 {document_count}건까지 확인했습니다."

    elif document_count == 1:
        return "관련 공시 1건을 확인했으며, 이후 변경된 내용은 확인되지 않았습니다."

    else:
        sentence = f"관련 공시 {document_count}건으로 이어지는 이력을 확인했습니다."

    change_count = len(history.changes)

    if change_count:
        return f"{sentence} 확인된 변경 항목은 {change_count}건입니다."

    if partial:
        return sentence

    return f"{sentence} 확인된 변경 항목은 없습니다."


def _limitation(result: AgentExecutionResult, *, partial: bool) -> str:
    """일부만 답한 경우 무엇이 남았는지 밝히는 문장."""
    if not partial:
        return ""

    if result.answerability_reason is None:
        return LIMITATION_WITHOUT_REASON_MESSAGE

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
    reason = result.answerability_reason

    if reason is None:
        return UNANSWERABLE_WITHOUT_REASON_MESSAGE

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
