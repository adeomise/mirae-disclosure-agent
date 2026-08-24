"""Pattern 실행 결과를 공통 실행 Contract로 옮기는 정규화 계층.

역할:
- Pattern A/E가 각자의 형태로 만든 내부 결과를 common.schemas.AgentExecutionResult 하나로
  옮긴다.
- 그 과정에서 공통 Contract에 자리가 없는 정보를 조용히 버리지 않고 conversion_gaps에
  남긴다.

왜 별도 계층인가:
    Pattern 안의 판정 로직(공시 선택, 값 추출, 연결, 차이 정리)은 실제 코퍼스에서 검증된
    것이다. 공통 Contract에 맞추려고 그 로직을 고치면 검증된 동작이 흔들린다. 변환만 따로
    두면 Contract가 개정돼도 이 파일 하나만 바뀐다.

    같은 이유로 이 모듈은 문서를 읽지 않고 검색도 하지 않는다. 이미 확정된 결과를 옮기기만
    하는 순수 함수여서, 코퍼스 없이 대응표 전체를 시험할 수 있다.

execution_status와 answerability_status를 나누는 기준:
    두 값은 서로 다른 질문에 답한다.

        execution_status    실행이 정상적으로 끝났는가
        answerability_status 확보한 근거로 답할 수 있는가

    그래서 "막혔다(BlockedInfo)"는 사실 하나만으로 execution_status를 정하지 않는다.
    같은 blocked라도 의미가 갈린다.

        코퍼스를 끝까지 뒤졌고 해당 공시가 없다      -> 실행은 끝났다. 답이 없을 뿐이다.
        파서가 문서를 열지 못했다                    -> 판정에 이르지 못하고 멈춘 것이다.

    앞쪽은 COMPLETED + UNANSWERABLE/UNRESOLVED, 뒤쪽은 BLOCKED + ExecutionIssue다.
    어느 사유가 어느 쪽인지는 아래 ANSWERABILITY_BY_STOP 하나가 정한다.

ExecutionStatus.FAILED에 대하여:
    이 모듈은 FAILED를 만들지 않는다. Pattern A/E는 예상한 실패를 전부 BlockedInfo로
    돌려주고, 예상하지 못한 예외는 삼키지 않고 호출자에게 올린다. 그 예외를 FAILED로 바꿀지는
    API/Runtime 경계의 결정이며 여기서 앞질러 정하지 않는다.

입력:
- Pattern A의 SingleFactResult, Pattern E의 EventHistoryResult

출력:
- AgentExecutionResult

세 진단 필드의 역할 구분:
    answerability_reason  평가·정책이 쓰는 canonical 사유. Contract enum 값이다.
    answerability_detail  그 판정을 내린 Agent 내부 진단 원문(stage/reason/detail).
    conversion_gaps       Contract에 실을 자리가 없어 손실되는 정보 전용.

    판정 근거는 손실이 아니라 진단이므로 conversion_gaps에 넣지 않는다.

TODO:
- 계산 결과(CalculationResult) 연결
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from disclosure_agent.agent.answer_builder import (
    REASON_AMBIGUOUS_CANDIDATES,
    REASON_AMBIGUOUS_ROOT,
    REASON_NO_MATCHING_CONTEXT,
    REASON_NO_MATCHING_FILING,
    REASON_NO_METRIC_ACODE,
    REASON_NO_ORIGINAL_FILING,
    REASON_UNCONVERTIBLE_FACT,
    STAGE_CONTRACT_ADAPTATION,
    STAGE_EXTRACTION,
    STAGE_FILING_DISCOVERY,
    BlockedInfo,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ExecutionStatus,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    AnswerabilityDetail,
    ConversionGap,
    EventLink,
    Evidence,
    ExecutionIssue,
)
from disclosure_agent.periodic.adapter import adapt_financial_fact

if TYPE_CHECKING:  # pragma: no cover - 순환 import를 만들지 않기 위한 타입 전용 import
    from disclosure_agent.agent.handlers.event_history import EventHistoryResult
    from disclosure_agent.agent.handlers.single_fact import SingleFactResult

# 이 정규화가 맞추는 Contract 버전. conversion_gaps의 기본값과 같아야 한다.
TARGET_CONTRACT_VERSION = "0.1"

# --------------------------------------------------------------------------
# 손실 유형
#
# 판정 근거(stage/reason/detail)는 여기 없다. Contract에 answerability_detail이 생겨
# 실을 자리가 있으므로 손실이 아니다.
# --------------------------------------------------------------------------

# 질문이 가리키는 원공시 후보 목록.
#
# EventHistory를 만든 경우에는 event_id에 루트 doc_id가 들어가 후보를 되살릴 수 있지만,
# 루트를 확정하지 못해 EventHistory 자체가 없으면 담을 자리가 없다.
GAP_ROOT_CANDIDATE_DOC_IDS = "root_candidate_doc_ids"


@dataclass(frozen=True)
class Answerability:
    """정상적으로 끝난 실행이 내린 답변 가능성 판정."""

    status: AnswerabilityStatus
    reason: AnswerabilityReason | None = None


# (stage, reason) -> 실행은 끝났고 답변 가능성만 판정된 경우.
#
# 여기 없는 정지 사유는 전부 BLOCKED로 본다. 목록에 넣는 기준은 하나다.
# "코퍼스를 볼 만큼 보고 내린 판정인가, 아니면 그 판정에 이르지 못하고 멈춘 것인가."
#
# 아래 사유들은 앞쪽이다.
#     no_matching_filing / no_original_filing
#         manifest 전체와 대조한 결과다. 문서 목록은 완전한 집합이므로 "없다"를 확정할 수
#         있다.
#     no_metric_acode / no_matching_label_fallback / no_matching_context
#         문서를 끝까지 읽었고 요청한 지표·기간·기준에 해당하는 셀이 없었다. 값이 없는
#         것이지 파이프라인이 멈춘 것이 아니다.
#     ambiguous_candidates / ambiguous_root
#         후보를 모두 찾았고 그중 하나로 좁힐 근거가 없다고 판정했다. 판정 자체는 끝났다.
#
# 반대로 목록에 넣지 않은 것들:
#     parse_failed / manifest_missing / path_unresolved / main_document_missing
#         문서를 열지 못했다. 답이 없는지조차 알지 못한다.
#     missing_acontext / missing_table_unit / untrusted_table_unit / unparsable_value
#         값은 문서 안에 있는데 파이프라인이 안전하게 읽어내지 못했다. 각 사유의 detail이
#         "Parser가 ...를 제공해야 합니다"라고 밝히는 대로 시스템 쪽 한계다. 이를
#         UNANSWERABLE로 보내면 코퍼스에 답이 없다는 뜻이 되어 사실과 달라진다.
#     unsupported_metric / unsupported_accounting_scope / unimplemented_intent 등
#         아직 구현하지 않은 요청이다. 코퍼스에 대한 판정이 아니다.
ANSWERABILITY_BY_STOP: dict[tuple[str, str], Answerability] = {
    (STAGE_FILING_DISCOVERY, REASON_NO_MATCHING_FILING): Answerability(
        AnswerabilityStatus.UNANSWERABLE,
        AnswerabilityReason.NOT_FOUND,
    ),
    (STAGE_FILING_DISCOVERY, REASON_NO_ORIGINAL_FILING): Answerability(
        AnswerabilityStatus.UNANSWERABLE,
        AnswerabilityReason.NOT_FOUND,
    ),
    # 질문이 가리키는 원공시 후보를 하나로 좁히지 못했다. 연결 판정이 아니라 대상 선택
    # 판정이므로 AMBIGUOUS_LINK가 아니라 AMBIGUOUS_MATCH다. 아래 주석 참조.
    (STAGE_FILING_DISCOVERY, REASON_AMBIGUOUS_ROOT): Answerability(
        AnswerabilityStatus.UNRESOLVED,
        AnswerabilityReason.AMBIGUOUS_MATCH,
    ),
    (STAGE_EXTRACTION, REASON_NO_METRIC_ACODE): Answerability(
        AnswerabilityStatus.UNANSWERABLE,
        AnswerabilityReason.INSUFFICIENT_EVIDENCE,
    ),
    (STAGE_EXTRACTION, "no_matching_label_fallback"): Answerability(
        AnswerabilityStatus.UNANSWERABLE,
        AnswerabilityReason.INSUFFICIENT_EVIDENCE,
    ),
    (STAGE_EXTRACTION, REASON_NO_MATCHING_CONTEXT): Answerability(
        AnswerabilityStatus.UNANSWERABLE,
        AnswerabilityReason.INSUFFICIENT_EVIDENCE,
    ),
    (STAGE_EXTRACTION, REASON_AMBIGUOUS_CANDIDATES): Answerability(
        AnswerabilityStatus.UNRESOLVED,
        AnswerabilityReason.AMBIGUOUS_MATCH,
    ),
}

# AMBIGUOUS_MATCH와 AMBIGUOUS_LINK를 섞지 않는다:
#
#     AMBIGUOUS_LINK는 docs/agent_design/evidence_and_answerability.md에서 "정정 대상 문서를
#     1건으로 확정할 수 없음"으로 정의됐고, Contract에서도 EventLinkResolutionStatus.AMBIGUOUS와
#     같은 어휘를 쓴다. 즉 문서 사이의 '연결'이 모호하다는 뜻이며, 그 판정은 event.linker가
#     내리고 EventLink.resolution_status에 실려 나간다.
#
#     위 두 사유는 연결 판정이 아니다.
#         ambiguous_candidates  문서 하나 안에서 본표와 주석의 셀 후보를 좁히지 못했다.
#         ambiguous_root        질문이 어느 계약을 가리키는지 좁히지 못했다.
#     둘 다 "무엇을 대상으로 삼을지"를 정하지 못한 것이고, 그 결과에는 EventLink가 하나도
#     없다. AMBIGUOUS_LINK를 실으면 받는 쪽이 "정정 대상을 특정하지 못했다"는 문장을 만들어
#     형식만 맞고 근거가 틀린 답이 된다. 그래서 AMBIGUOUS_MATCH를 쓴다.
#
#     어느 후보들 사이에서 모호했는지는 canonical reason이 담지 못한다. Pattern A는
#     answerability_detail의 원문에, Pattern E는 root_candidate_doc_ids gap에 남는다.


class ContractGapLike(Protocol):
    """
    event.contract_adapter.ContractGap이 만족하는 구조.

    구조로만 요구해 두면 다른 변환 계층이 자기 gap 타입을 만들어도 같은 경로로 실을 수
    있다. 이 모듈이 event 패키지의 특정 dataclass에 묶이지 않는다.
    """

    kind: str
    subject: str
    dropped: dict[str, Any]
    detail: str
    pending_decision: bool

    @property
    def planned_contract_field(self) -> str | None: ...

    @property
    def is_temporary(self) -> bool: ...


def to_conversion_gap(gap: ContractGapLike) -> ConversionGap:
    """
    변환 계층이 만든 gap을 Contract의 ConversionGap으로 옮긴다.

    문자열로 합치지 않고 필드를 그대로 옮긴다. dropped에 담긴 원본 값이 이 gap의 존재
    이유이므로, 요약하면 남길 이유가 없어진다.
    """
    return ConversionGap(
        kind=gap.kind,
        subject=gap.subject,
        dropped=dict(gap.dropped),
        detail=gap.detail,
        pending_decision=gap.pending_decision,
        planned_contract_field=gap.planned_contract_field,
        is_temporary=gap.is_temporary,
        contract_version=TARGET_CONTRACT_VERSION,
    )


def blocked_execution(
    stage: str,
    reason: str,
    detail: str,
    *,
    warnings: Sequence[str] = (),
) -> AgentExecutionResult:
    """
    판정에 이르지 못하고 멈춘 실행 결과를 만든다.

    answerability_status를 비워 둔다. 답할 수 있는지 없는지를 확인하지 못했기 때문이다.
    UNANSWERABLE로 채우면 "코퍼스에 답이 없다"는 확정 진술이 되어 사실과 달라진다.
    """
    return AgentExecutionResult(
        execution_status=ExecutionStatus.BLOCKED,
        execution_issue=ExecutionIssue(stage=stage, reason=reason, detail=detail),
        warnings=list(warnings),
    )


def _answerability_detail(blocked: BlockedInfo) -> AnswerabilityDetail:
    """
    canonical reason 뒤에 있던 Agent 내부 진단을 그대로 옮긴다.

    canonical reason은 서로 다른 정지 사유를 한 값으로 묶는다. NOT_FOUND만 보면
    no_matching_filing인지 no_original_filing인지 알 수 없고, 후속 조치는 서로 다르다.
    요약하지 않고 원문을 남긴다.
    """
    return AnswerabilityDetail(
        stage=blocked.stage,
        reason=blocked.reason,
        detail=blocked.detail,
    )


def _stopped_execution(
    blocked: BlockedInfo,
    *,
    warnings: Sequence[str] = (),
    event_links: Sequence[EventLink] = (),
    evidence: Sequence[Evidence] = (),
    conversion_gaps: Sequence[ConversionGap] = (),
) -> AgentExecutionResult:
    """
    파이프라인이 멈춘 지점 하나를 실행 결과로 옮긴다.

    대응표에 있으면 정상적으로 끝난 실행의 답변 가능성 판정으로 보고, 없으면 실행 중단으로
    본다.
    """
    outcome = ANSWERABILITY_BY_STOP.get((blocked.stage, blocked.reason))

    if outcome is None:
        # 답변 가능성을 판정하지 못했으므로 answerability_detail도 만들지 않는다.
        # 멈춘 지점은 execution_issue가 담는다.
        return AgentExecutionResult(
            execution_status=ExecutionStatus.BLOCKED,
            execution_issue=ExecutionIssue(
                stage=blocked.stage,
                reason=blocked.reason,
                detail=blocked.detail,
            ),
            event_links=list(event_links),
            evidence=list(evidence),
            warnings=list(warnings),
            conversion_gaps=list(conversion_gaps),
        )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=outcome.status,
        answerability_reason=outcome.reason,
        answerability_detail=_answerability_detail(blocked),
        event_links=list(event_links),
        evidence=list(evidence),
        warnings=list(warnings),
        conversion_gaps=list(conversion_gaps),
    )


# --------------------------------------------------------------------------
# Pattern A
# --------------------------------------------------------------------------


def normalize_single_fact_result(result: SingleFactResult) -> AgentExecutionResult:
    """
    Pattern A의 구조화 결과를 공통 실행 Contract로 옮긴다.

    FinancialFact -> Fact/Evidence 변환은 periodic.adapter가 한다. 같은 변환을 여기서 다시
    구현하면 두 벌이 되고, 그때부터 어느 쪽이 정본인지 알 수 없게 된다.
    """
    warnings = [*result.plan.warnings, *result.warnings]

    if result.blocked is not None:
        return _stopped_execution(result.blocked, warnings=warnings)

    try:
        fact, evidence = adapt_financial_fact(result.fact, result.document or {})

    except ValueError as error:
        # 값은 확정했지만 공통 타입으로 옮기지 못했다. 추측해 채우지 않고 멈춘 지점을 남긴다.
        return blocked_execution(
            STAGE_CONTRACT_ADAPTATION,
            REASON_UNCONVERTIBLE_FACT,
            str(error),
            warnings=warnings,
        )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[fact],
        evidence=[evidence],
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Pattern E
# --------------------------------------------------------------------------


def normalize_event_history_result(result: EventHistoryResult) -> AgentExecutionResult:
    """
    Pattern E의 구조화 결과를 공통 실행 Contract로 옮긴다.

    EventHistory / EventLink / Evidence는 이미 Contract 타입이므로 다시 만들지 않고 그대로
    싣는다. 재변환하면 event.contract_adapter가 이미 판정한 것을 두 번 판정하게 된다.
    """
    warnings = list(result.plan.warnings)

    gaps = [to_conversion_gap(gap) for gap in result.conversion_gaps]

    if result.history is None:
        # 이력을 만들지 못했으면 루트 후보를 담을 자리가 없다. event_id가 없기 때문이다.
        gaps.extend(_root_candidate_gaps(result))

        if result.blocked is not None:
            return _stopped_execution(
                result.blocked,
                warnings=warnings,
                event_links=result.links,
                evidence=result.evidence,
                conversion_gaps=gaps,
            )

        # 멈춘 지점 없이 이력만 만들지 못한 경우다. 실행은 끝났고 판정만 미해결로 남는다.
        return AgentExecutionResult(
            execution_status=ExecutionStatus.COMPLETED,
            answerability_status=result.status,
            event_links=list(result.links),
            evidence=list(result.evidence),
            warnings=warnings,
            conversion_gaps=gaps,
        )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=result.status,
        answerability_reason=_chain_reason(result),
        event_histories=[result.history],
        event_links=list(result.links),
        evidence=list(result.evidence),
        warnings=warnings,
        conversion_gaps=gaps,
    )


def _chain_reason(result: EventHistoryResult) -> AnswerabilityReason | None:
    """
    체인이 완결되지 않은 사실을 기계가 읽을 수 있는 사유로 옮긴다.

    새로 판정하지 않는다. EventHistory.chain_complete를 그대로 옮길 뿐이며, 무엇을 연결하지
    못했는지는 EventLink.resolution_status와 conversion_gaps에 이미 남아 있다.
    """
    if result.history is None or result.history.chain_complete:
        return None

    return AnswerabilityReason.INCOMPLETE_CHAIN


def _root_candidate_gaps(result: EventHistoryResult) -> list[ConversionGap]:
    """루트 후보 목록을 담을 자리가 없을 때 그 목록을 남긴다."""
    if not result.root_candidate_doc_ids:
        return []

    return [
        ConversionGap(
            kind=GAP_ROOT_CANDIDATE_DOC_IDS,
            subject=result.plan.company,
            dropped={"root_candidate_doc_ids": list(result.root_candidate_doc_ids)},
            detail=(
                f"질문이 가리키는 원공시 후보 {len(result.root_candidate_doc_ids)}건을 담을 "
                "자리가 없습니다. EventHistory를 만든 경우에는 event_id에 루트 doc_id가 들어가 "
                "되살릴 수 있지만, 루트를 확정하지 못하면 EventHistory 자체가 없습니다."
            ),
            contract_version=TARGET_CONTRACT_VERSION,
        )
    ]
