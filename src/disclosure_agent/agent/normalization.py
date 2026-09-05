"""Pattern 실행 결과를 공통 실행 Contract로 옮기는 정규화 계층.

역할:
- Pattern A/B/C/D/E가 각자의 형태로 만든 내부 결과를 common.schemas.AgentExecutionResult
  하나로 옮긴다.
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
    이 모듈은 FAILED를 만들지 않는다. Pattern A/B/C/D/E는 예상한 실패를 전부 BlockedInfo로
    돌려주고, 예상하지 못한 예외는 삼키지 않고 호출자에게 올린다. 그 예외를 FAILED로 바꿀지는
    API/Runtime 경계의 결정이며 여기서 앞질러 정하지 않는다.

입력:
- Pattern A의 SingleFactResult, Pattern B의 DocumentSummaryResult,
  Pattern C의 TemporalCompareResult, Pattern D의 CrossEntityCompareResult,
  Pattern E의 EventHistoryResult

출력:
- AgentExecutionResult

세 진단 필드의 역할 구분:
    answerability_reason  평가·정책이 쓰는 canonical 사유. Contract enum 값이다.
    answerability_detail  그 판정을 내린 Agent 내부 진단 원문(stage/reason/detail).
    conversion_gaps       Contract에 실을 자리가 없어 손실되는 정보 전용.

    판정 근거는 손실이 아니라 진단이므로 conversion_gaps에 넣지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from disclosure_agent.agent.answer_builder import (
    REASON_AMBIGUOUS_CANDIDATES,
    REASON_AMBIGUOUS_FILING,
    REASON_AMBIGUOUS_ROOT,
    REASON_INCOMPARABLE_CALCULATION_INPUTS,
    REASON_INCOMPARABLE_ENTITIES,
    REASON_INCOMPARABLE_NARRATIVE_SECTIONS,
    REASON_INCOMPARABLE_PERIODS,
    REASON_NO_MATCHING_CONTEXT,
    REASON_NO_MATCHING_FILING,
    REASON_NO_METRIC_ACODE,
    REASON_NO_ORIGINAL_FILING,
    REASON_UNCONVERTIBLE_FACT,
    REASON_UNVERIFIED_COUNTERPARTY,
    STAGE_COMPARABILITY,
    STAGE_CONTRACT_ADAPTATION,
    STAGE_EXTRACTION,
    STAGE_FILING_DISCOVERY,
    STAGE_QUERY_PLAN,
    STAGE_SECTION_NAVIGATION,
    BlockedInfo,
)
from disclosure_agent.agent.query_analyzer import AMBIGUOUS_COMPANY_REASON
from disclosure_agent.agent.safety import (
    CATEGORY_EXTERNAL_INFORMATION,
    CATEGORY_FORWARD_LOOKING,
    CATEGORY_INVESTMENT_ADVICE,
    CATEGORY_OUT_OF_CORPUS_PERIOD,
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
    Fact,
)
from disclosure_agent.periodic.adapter import adapt_financial_fact, adapt_section_summary
from disclosure_agent.periodic.sections import (
    REASON_AMBIGUOUS_SECTION,
    REASON_EMPTY_SECTION,
    REASON_NO_MATCHING_SECTION,
)

if TYPE_CHECKING:  # pragma: no cover - 순환 import를 만들지 않기 위한 타입 전용 import
    from disclosure_agent.agent.handlers.contract_fact import ContractFactResult
    from disclosure_agent.agent.handlers.cross_entity_compare import (
        CrossEntityCompareResult,
    )
    from disclosure_agent.agent.handlers.document_summary import DocumentSummaryResult
    from disclosure_agent.agent.handlers.event_history import EventHistoryResult
    from disclosure_agent.agent.handlers.event_summary import EventSummaryResult
    from disclosure_agent.agent.handlers.single_fact import SingleFactResult
    from disclosure_agent.agent.handlers.temporal_compare import TemporalCompareResult

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

# 확정된 요약 범위·항목을 공통 Contract 타입으로 옮기지 못한 경우.
#
# answer_builder의 REASON_UNCONVERTIBLE_FACT와 나눈다. 그쪽은 Fact 변환이고 이쪽은
# DocumentSummary 변환이며, 무엇을 옮기지 못했는지가 후속 조치를 가른다.
REASON_UNCONVERTIBLE_SUMMARY = "unconvertible_summary"


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
#     ambiguous_company_match
#         인식 후보 목록 전체와 대조한 결과다. 위 둘과 같은 자리이며, 코퍼스를 여는 단계가
#         아니라 질의 해석 단계에서 났을 뿐이다. 아래 설명 참조.
#
# 반대로 목록에 넣지 않은 것들:
#     unknown_company / missing_year / unsupported_period 등 나머지 query_plan 사유
#         질문을 계획으로 옮기지 못한 것이다. 코퍼스에 답이 있는지에 대한 판정이 아니므로
#         대응표에 넣지 않으며, 그대로 BLOCKED다.
#     parse_failed / manifest_missing / path_unresolved / main_document_missing
#         문서를 열지 못했다. 답이 없는지조차 알지 못한다.
#     missing_acontext / missing_table_unit / untrusted_table_unit / unparsable_value
#         값은 문서 안에 있는데 파이프라인이 안전하게 읽어내지 못했다. 각 사유의 detail이
#         "Parser가 ...를 제공해야 합니다"라고 밝히는 대로 시스템 쪽 한계다. 이를
#         UNANSWERABLE로 보내면 코퍼스에 답이 없다는 뜻이 되어 사실과 달라진다.
#     unsupported_metric / unsupported_accounting_scope / unimplemented_intent 등
#         아직 구현하지 않은 요청이다. 코퍼스에 대한 판정이 아니다.
#     unsupported_section_scope
#         질문이 지정한 범위를 Section 경로로 옮기는 규칙이 아직 없다. 문서를 열기는 했지만
#         어디를 볼지 정하지 못한 것이고, 코퍼스에 그 내용이 없다는 판정이 아니다. 위
#         unsupported_* 계열과 같은 자리다.
#     unsupported_comparison_* / unsupported_derived_operation
#         Pattern C와 Pattern D가 계획 입구에서 막은 것이다. 위와 같은 이유로 코퍼스를 보기
#         전이다.
#     unusable_calculation_input
#         값은 확보했지만 그 값으로 산술이 성립하지 않는다(분모 0, 파싱 불가). 근거가 없는
#         것이 아니므로 INSUFFICIENT_EVIDENCE가 아니고, 코퍼스에 답이 없다는 판정도 아니다.
#         대응하는 Contract 값이 아직 없어 실행 중단으로 둔다.
ANSWERABILITY_BY_STOP: dict[tuple[str, str], Answerability] = {
    # 질문이 가리킨 기업 표현에 맞는 후보가 여럿이라 그중 하나를 고르지 않았다.
    #
    # 질의 해석 단계의 정지 사유 중 이 하나만 대응표에 두는 이유:
    #     이 판정은 인식 후보 목록 전체와 대조한 뒤에 나온다. manifest가 아는 표기를 모두 보고
    #     "여덟 곳이 걸렸고 그중 하나를 고를 근거가 없다"까지 확정했으므로, 판정에 이르지 못하고
    #     멈춘 것이 아니다. 문서 목록을 모두 보고 내리는 ambiguous_root·ambiguous_filing와 같은
    #     자리이며, 코퍼스를 여느냐 인식 후보 목록을 보느냐만 다르다.
    #
    #     BLOCKED로 두면 사용자에게 "실행이 멈췄다"로 나간다. 실제로 일어난 일은 답할 수 있는지
    #     판정을 끝냈고 대상을 좁히지 못했다는 것이고, 그 차이는 되묻기를 안내할지 말지를 가른다.
    #
    # 후보를 하나 고르지 않는다. 어느 후보들 사이에서 모호했는지는 _resolve_companies가 적어 둔
    # answerability_detail 원문에 그대로 남으며, 이 자리에서 그것을 요약하거나 좁히지 않는다.
    # Pattern A의 ambiguous_candidates가 같은 방식이다(아래 주석 참조).
    (STAGE_QUERY_PLAN, AMBIGUOUS_COMPANY_REASON): Answerability(
        AnswerabilityStatus.UNRESOLVED,
        AnswerabilityReason.AMBIGUOUS_MATCH,
    ),
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
    # 요약 대상 공시를 하나로 좁히지 못했다. 문서 목록을 모두 보고 내린 대상 선택 판정이므로
    # ambiguous_root와 같은 계열이며, 연결 판정이 아니라 AMBIGUOUS_MATCH다(아래 주석 참조).
    (STAGE_FILING_DISCOVERY, REASON_AMBIGUOUS_FILING): Answerability(
        AnswerabilityStatus.UNRESOLVED,
        AnswerabilityReason.AMBIGUOUS_MATCH,
    ),
    # 질문이 계약 상대방을 지목했는데 그 이름을 대조할 수 없어 원공시 1건을 확정하지 못했다.
    # 후보 개수만 다를 뿐 대상 문서를 고르지 못한 것은 위 둘과 같으므로 AMBIGUOUS_MATCH다.
    # 새 공개 사유를 만들지 않는다(answer_builder의 REASON_UNVERIFIED_COUNTERPARTY 참조).
    (STAGE_FILING_DISCOVERY, REASON_UNVERIFIED_COUNTERPARTY): Answerability(
        AnswerabilityStatus.UNRESOLVED,
        AnswerabilityReason.AMBIGUOUS_MATCH,
    ),
    # Section 탐색(C4)은 문서를 끝까지 읽고 내리는 판정이다. 문서는 열렸고 그 안에 요청한
    # 범위가 없었거나 하나로 좁혀지지 않았다.
    #
    # no_matching_section이 NOT_FOUND인 이유:
    #     evidence_and_answerability.md의 판정 흐름에서 [5] Section·Fact 탐색 단계의 실패가
    #     NOT_FOUND다. 문서는 있는데 찾지 못한 것이므로 검색 품질 문제일 수 있고, 그래서
    #     "해당 기간 데이터가 없다"는 확정 표현(OUT_OF_DATA_RANGE)과 구분한다.
    #
    # empty_section을 INSUFFICIENT_EVIDENCE로 두는 이유:
    #     범위는 찾았는데 그 안에 근거로 쓸 것이 없다. 값을 얻지 못한 것이 아니라 근거가
    #     비어 있는 상태이며, 근거 없이 요약을 만들지 않는다는 원칙이 답변을 막는다
    #     (evidence_and_answerability.md 원칙 1).
    (STAGE_SECTION_NAVIGATION, REASON_NO_MATCHING_SECTION): Answerability(
        AnswerabilityStatus.UNANSWERABLE,
        AnswerabilityReason.NOT_FOUND,
    ),
    (STAGE_SECTION_NAVIGATION, REASON_AMBIGUOUS_SECTION): Answerability(
        AnswerabilityStatus.UNRESOLVED,
        AnswerabilityReason.AMBIGUOUS_MATCH,
    ),
    (STAGE_SECTION_NAVIGATION, REASON_EMPTY_SECTION): Answerability(
        AnswerabilityStatus.UNANSWERABLE,
        AnswerabilityReason.INSUFFICIENT_EVIDENCE,
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
    # 비교 가능성 판정(C9)은 코퍼스를 끝까지 읽고 값을 확보한 뒤에 내린다. 실행은 끝났고,
    # 확보한 값으로는 답할 수 없다고 판정한 것이다.
    #
    # 세 사유가 같은 값으로 모이는 것은 의도된 것이다. 셋 다 "이 값들은 함께 쓸 수 없다"이며,
    # 계산 입력이 어긋난 것인지(guard①) 두 기간이 어긋난 것인지(Pattern C guard②) 두 기업이
    # 어긋난 것인지(Pattern D guard②)는 answerability_detail.reason이 구분한다
    # (pattern_f_safety.md §5.1의 F3·F4와 같은 구조).
    # 거래소공시 계약 조회(Q02)의 정지 사유.
    #
    # 문자열을 그대로 참조하는 것은 위 no_matching_label_fallback과 같은 방식이다. 사유를
    # 정의하는 쪽(agent.handlers.contract_fact)이 이 모듈을 import하므로, 상수를 가져오면
    # 순환이 된다.
    #
    # 후보 공시는 있었는데 그 상대방의 계약이 없었다. 코퍼스를 끝까지 보고 내린 판정이므로
    # 실행은 끝났고, 없다는 것이 답이다. 다른 상대방의 계약으로 대신하지 않는다.
    (STAGE_FILING_DISCOVERY, "no_matching_counterparty"): Answerability(
        AnswerabilityStatus.UNANSWERABLE,
        AnswerabilityReason.NOT_FOUND,
    ),
    # 문서는 1건으로 확정했는데 요구한 필드가 그 문서에 없다. 값이 없다는 것을 문서를 열어
    # 확인했으므로 INSUFFICIENT_EVIDENCE이며, 위 정기공시의 no_metric_acode와 같은 자리다.
    (STAGE_EXTRACTION, "missing_contract_field"): Answerability(
        AnswerabilityStatus.UNANSWERABLE,
        AnswerabilityReason.INSUFFICIENT_EVIDENCE,
    ),
    (STAGE_COMPARABILITY, REASON_INCOMPARABLE_CALCULATION_INPUTS): Answerability(
        AnswerabilityStatus.UNANSWERABLE,
        AnswerabilityReason.NOT_COMPARABLE,
    ),
    (STAGE_COMPARABILITY, REASON_INCOMPARABLE_PERIODS): Answerability(
        AnswerabilityStatus.UNANSWERABLE,
        AnswerabilityReason.NOT_COMPARABLE,
    ),
    (STAGE_COMPARABILITY, REASON_INCOMPARABLE_ENTITIES): Answerability(
        AnswerabilityStatus.UNANSWERABLE,
        AnswerabilityReason.NOT_COMPARABLE,
    ),
    # 서술 비교(Q09)가 두 연도의 Section을 모두 확보한 뒤 내린 판정이다. 위 셋과 같은 자리에
    # 두는 이유도 같다. 코퍼스를 끝까지 읽고 내린 판정이므로 실행은 끝났고, 확보한 것으로는
    # 답할 수 없다는 판정이다.
    #
    # UNANSWERABLE이지 UNRESOLVED가 아니다. 문서도 Section도 하나로 확정했으므로 대상이
    # 모호한 상태가 아니다. 짝지을 수 없다는 판정 자체는 끝났다.
    #
    # PARTIALLY_ANSWERABLE로 낮추지 않는다. 확보한 서술을 일부 답으로 내보내면 견주지 못한
    # 결과가 견준 결과처럼 읽힌다.
    (STAGE_COMPARABILITY, REASON_INCOMPARABLE_NARRATIVE_SECTIONS): Answerability(
        AnswerabilityStatus.UNANSWERABLE,
        AnswerabilityReason.NOT_COMPARABLE,
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


class UnmappedPolicyCategoryError(RuntimeError):
    """정책 범주에 대응하는 canonical reason이 대응표에 없는 경우."""

    def __init__(self, category: str | None) -> None:
        super().__init__(
            f"정책 범주 {category!r}에 대응하는 AnswerabilityReason이 없습니다. "
            f"ANSWERABILITY_REASON_BY_POLICY_CATEGORY에 추가하십시오."
        )

        self.category = category


# 정책 범주 하나가 어떤 canonical reason으로 나가는지.
#
# ANSWERABILITY_BY_STOP과 같은 자리에 같은 모양으로 둔다. 판정이 일어나는 곳(safety.py,
# 각 handler)과 그것을 Contract 어휘로 옮기는 곳을 나누는 것이 이 모듈의 역할이고,
# 정책 판정이라고 예외를 둘 이유가 없다.
#
# F3와 F4가 같은 값으로 모이는 것은 의도된 것이다. 둘 다 "정책상 답하지 않는다"이며,
# 무엇을 막았는지는 answerability_detail.reason이 구분한다
# (pattern_f_safety.md §5.1).
#
# F1·F2는 판정 규칙이 아직 없어 지금은 도달하지 않는 줄이다. 그래도 비워 두지 않는 이유는,
# 규칙을 붙이는 쪽이 canonical reason을 즉석에서 고르게 두면 F1·F2가 F3·F4와 같은 값으로
# 새기 때문이다. 세 판정이 한 값으로 묶이면 평가에서 구분되지 않는다.
ANSWERABILITY_REASON_BY_POLICY_CATEGORY: dict[str, AnswerabilityReason] = {
    CATEGORY_OUT_OF_CORPUS_PERIOD: AnswerabilityReason.OUT_OF_DATA_RANGE,
    CATEGORY_EXTERNAL_INFORMATION: AnswerabilityReason.OUT_OF_SCOPE_DATA,
    CATEGORY_INVESTMENT_ADVICE: AnswerabilityReason.OUT_OF_SCOPE_POLICY,
    CATEGORY_FORWARD_LOOKING: AnswerabilityReason.OUT_OF_SCOPE_POLICY,
}


def answerability_reason_for_policy_category(category: str | None) -> AnswerabilityReason:
    """
    정책 범주를 canonical reason으로 옮긴다.

    대응표에 없으면 예외로 멈춘다. OUT_OF_SCOPE_POLICY로 기본값을 두지 않는 이유:
        지금 대응표에 없는 범주는 F1과 F2뿐이고, 둘 다 정책 범위 밖이 아니다. 기본값을
        두면 새 범주가 조용히 정책 거부로 집계되고, 그 사실이 어디에도 드러나지 않는다.
        판정을 붙이는 쪽이 대응표를 먼저 고치게 하는 편이 낫다.

    범주가 없는 경우(None)도 같이 막는다. PolicyDecision이 거부인데 category가 비어 있다면
    safety.py 쪽 결함이며, 그것을 canonical reason으로 덮으면 원인이 사라진다.
    """
    reason = ANSWERABILITY_REASON_BY_POLICY_CATEGORY.get(category) if category else None

    if reason is None:
        raise UnmappedPolicyCategoryError(category)

    return reason


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


def refused_execution(
    stage: str,
    reason: str,
    detail: str,
    *,
    answerability_reason: AnswerabilityReason,
    warnings: Sequence[str] = (),
) -> AgentExecutionResult:
    """
    정책 판정으로 답하지 않기로 한 실행 결과를 만든다.

    blocked_execution과 나누는 이유:
        blocked는 "판정에 이르지 못하고 멈췄다"이고, 이쪽은 "판정을 끝냈고 답하지 않기로
        했다"이다. Gate는 질문만 보고 결론을 냈으므로 실행은 정상적으로 끝났다. 그래서
        COMPLETED + UNANSWERABLE이며, BLOCKED가 아니다
        (docs/agent_design/pattern_f_safety.md §4).

    canonical reason을 인자로 받는 이유:
        PR #29에서 OUT_OF_DATA_RANGE / OUT_OF_SCOPE_DATA / OUT_OF_SCOPE_POLICY가
        AnswerabilityReason에 추가되어(F-D1 해소) Gate 판정에 대응하는 값이 생겼다.
        어떤 값인지는 범주가 정한다. F3·F4는 정책 범위 밖이므로 OUT_OF_SCOPE_POLICY이고,
        F1은 보유 기간 밖(OUT_OF_DATA_RANGE), F2는 데이터군 밖(OUT_OF_SCOPE_DATA)이다.
        여기서 하나로 고정하면 세 판정이 한 값으로 묶여 평가에서 구분되지 않는다.

        비워 두는 선택지는 남기지 않는다. 대응 값이 있는데 비우면 소비자가 "Contract에
        값이 없다"로 읽는다. 인자를 필수 키워드로 두어, 새 범주를 붙이는 쪽이 어떤
        canonical reason에 해당하는지 먼저 정하게 한다.

    canonical reason은 서로 다른 판정을 한 값으로 묶으므로, 어느 규칙에 걸렸는지는
    answerability_detail에 원문 그대로 남는다(F3의 investment_advice와 F4의
    forward_looking_prediction은 둘 다 OUT_OF_SCOPE_POLICY다).

    facts와 evidence는 채우지 않는다. 거부 응답에 근거를 붙이면 부분 답변으로 읽힌다
    (evidence_and_answerability.md 원칙 7).
    """
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=answerability_reason,
        answerability_detail=AnswerabilityDetail(stage=stage, reason=reason, detail=detail),
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
    facts: Sequence[Fact] = (),
    event_links: Sequence[EventLink] = (),
    evidence: Sequence[Evidence] = (),
    conversion_gaps: Sequence[ConversionGap] = (),
) -> AgentExecutionResult:
    """
    파이프라인이 멈춘 지점 하나를 실행 결과로 옮긴다.

    대응표에 있으면 정상적으로 끝난 실행의 답변 가능성 판정으로 보고, 없으면 실행 중단으로
    본다.

    facts를 받는 이유:
        멈추기 전에 확보한 것을 지우지 않는다. 비교 가능성 판정(Pattern C)은 값을 다 확보한
        뒤에 내리므로, Fact를 지우면 무엇이 견줄 수 없다고 판정됐는지 결과에서 사라진다.
        Pattern E가 같은 이유로 evidence와 event_links를 남기는 것과 같은 자리다.

        정책 거부(F1~F4)와 섞이지 않는다. 그쪽은 refused_execution이 따로 만들고 근거를
        붙이지 않는다(evidence_and_answerability.md 원칙 7).

    calculations는 받지 않는다. 멈춘 결과에 파생값을 실으면 받는 쪽이 그것으로 막힌 계산을
    이어서 할 수 있고, 그러면 guard가 막은 일이 결과를 받는 쪽에서 그대로 일어난다.
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
            facts=list(facts),
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
        facts=list(facts),
        event_links=list(event_links),
        evidence=list(evidence),
        warnings=list(warnings),
        conversion_gaps=list(conversion_gaps),
    )


def stopped_execution(
    stage: str,
    reason: str,
    detail: str,
    *,
    warnings: Sequence[str] = (),
) -> AgentExecutionResult:
    """
    아직 Pattern 결과가 없는 단계에서 멈춘 지점 하나를 실행 결과로 옮긴다.

    _stopped_execution과 같은 대응표를 쓰며, BlockedInfo를 만들 것이 없는 호출자용 입구다.
    질의 해석 단계(query_plan)가 그 자리다. 그 단계의 정지 사유는 UnsupportedQuestionError의
    reason 문자열로만 오고, Pattern이 돌기 전이라 함께 실을 facts·evidence도 없다.

    blocked_execution과 나누는 이유:
        blocked_execution은 무조건 BLOCKED를 만든다. 호출자가 그것을 쓰면 "이 정지는 실행
        중단인가 답변 가능성 판정인가"를 호출자가 정하게 되고, 그 판단이 대응표 밖에 하나 더
        생긴다. 여기로 보내면 판단하는 곳은 ANSWERABILITY_BY_STOP 하나로 남는다.

        대응표에 없는 사유는 그대로 BLOCKED다. 그래서 이 함수로 바꿔도 매핑을 더하기 전에는
        기존 동작이 한 줄도 달라지지 않으며, 반대로 모든 정지 사유가 한꺼번에 답변 가능성
        판정으로 새지도 않는다.
    """
    return _stopped_execution(
        BlockedInfo(stage=stage, reason=reason, detail=detail),
        warnings=warnings,
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
        fact, evidence = adapt_financial_fact(
            result.fact,
            result.document or {},
            canonical_company=result.plan.company,
        )

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
# Pattern B
# --------------------------------------------------------------------------


def normalize_document_summary_result(
    result: DocumentSummaryResult,
) -> AgentExecutionResult:
    """
    Pattern B의 구조화 결과를 공통 실행 Contract로 옮긴다.

    SectionContentItem -> DocumentSummary/Evidence 변환은 periodic.adapter가 한다. Pattern A의
    FinancialFact 변환과 같은 자리이며, 같은 변환을 여기서 다시 구현하면 두 벌이 된다.

    facts를 만들지 않는다:
        Pattern B의 최소 Fact 요건이 0이라는 것은 확정 사항이다
        (evidence_and_answerability.md 유형별 최소 요건). 요약 항목과 그 항목마다의 Evidence가
        결과이고, 값을 함께 실으면 요약 안에 Pattern A가 고르는 값과 다른 수치가 섞인다.

    calculations도 comparisons도 만들지 않는다:
        원문에 적힌 비율은 인용하고 재계산하지 않는다는 것이 Pattern B의 출력 요구사항이다
        (agent_patterns.md Pattern B).

    성공 경로에 answerability_reason을 싣지 않는다. ANSWERABLE은 사유를 갖지 않는다.
    """
    warnings = [*result.plan.warnings, *result.warnings]

    if result.blocked is not None:
        return _stopped_execution(result.blocked, warnings=warnings)

    if result.selection is None or not result.items:
        # 막히지도 않았는데 확보한 것이 없다. 값을 지어내지 않고 멈춘 지점을 남긴다.
        return blocked_execution(
            STAGE_CONTRACT_ADAPTATION,
            REASON_UNCONVERTIBLE_SUMMARY,
            "요약 범위나 항목이 비어 있는데 막힌 사유도 없습니다.",
            warnings=warnings,
        )

    try:
        summary, evidence = adapt_section_summary(
            result.document or {},
            canonical_company=result.plan.company,
            section_scope=result.selection.scope,
            selection=result.selection,
            items=list(result.items),
        )

    except ValueError as error:
        # 범위와 내용은 확정했지만 공통 타입으로 옮기지 못했다. 추측해 채우지 않는다.
        return blocked_execution(
            STAGE_CONTRACT_ADAPTATION,
            REASON_UNCONVERTIBLE_SUMMARY,
            str(error),
            warnings=warnings,
        )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        document_summaries=[summary],
        evidence=list(evidence),
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Q02 계약 단건 사실 조회
# --------------------------------------------------------------------------


def normalize_contract_fact_result(result: ContractFactResult) -> AgentExecutionResult:
    """
    Q02의 구조화 결과를 공통 실행 Contract로 옮긴다.

    Fact/Evidence 변환을 여기서 하지 않는다. event.adapter가 이미 Contract 타입으로 만들어
    두었고, 같은 변환을 다시 구현하면 두 벌이 되어 그때부터 어느 쪽이 정본인지 알 수 없다.
    Pattern A가 여기서 adapt_financial_fact를 부르는 것은 그쪽 변환이 문서 metadata를 더
    요구하기 때문이며, 규칙이 달라서가 아니다.

    막힌 결과에 facts를 싣지 않는다:
        이 Pattern은 요구한 값을 전부 확보하거나 하나도 확보하지 못한다(adapt_contract_facts).
        중간 상태가 없으므로 남길 부분 결과도 없다. 후보 목록만 gap으로 남긴다.
    """
    warnings = list(result.plan.warnings)

    if result.blocked is not None:
        return _stopped_execution(
            result.blocked,
            warnings=warnings,
            conversion_gaps=_contract_candidate_gaps(result),
        )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=list(result.facts),
        evidence=list(result.evidence),
        warnings=warnings,
    )


def _contract_candidate_gaps(result: ContractFactResult) -> list[ConversionGap]:
    """
    좁히지 못한 후보 목록을 남긴다.

    AgentExecutionResult에는 후보 문서를 담을 자리가 없다. 그냥 버리면 어느 후보들 사이에서
    막혔는지가 결과에서 사라지고, 받는 쪽은 "왜 못 골랐는지"를 다시 조사해야 한다.
    Pattern E가 root_candidate_doc_ids를 같은 방식으로 남긴다.
    """
    if not result.candidate_doc_ids:
        return []

    return [
        ConversionGap(
            kind="contract_candidate_doc_ids",
            subject=", ".join(result.candidate_doc_ids),
            detail=(
                "좁히지 못한 계약 공시 후보입니다. Contract에 후보 목록을 담을 자리가 "
                "없어 여기에 남깁니다."
            ),
        )
    ]


# --------------------------------------------------------------------------
# Pattern C
# --------------------------------------------------------------------------


def normalize_temporal_compare_result(result: TemporalCompareResult) -> AgentExecutionResult:
    """
    Pattern C의 구조화 결과를 공통 실행 Contract로 옮긴다.

    Fact/Evidence/CalculationResult는 이미 Contract 타입이므로 다시 만들지 않고 그대로 싣는다.
    확보는 agent.period_retrieval이, 계산은 agent.calculator가 끝냈고, 여기서 다시 변환하면
    같은 판정이 두 곳에 생긴다.

    성공 경로에 answerability_reason을 싣지 않는다. ANSWERABLE은 사유를 갖지 않으며, 그
    규칙은 answer_builder의 ALLOWED_REASONS_BY_STATUS와 evaluation.runner의 GoldAnswer 검증이
    이미 고정하고 있다.

    ComparisonResult를 만들지 않는다. Q08은 변화량 자체를 묻고 최종 우열 판정을 요구하지
    않는다(evidence_and_answerability.md Calculation cardinality).
    """
    warnings = list(result.plan.warnings)

    if result.blocked is not None:
        return _stopped_execution(
            result.blocked,
            warnings=warnings,
            facts=result.facts,
            evidence=result.evidence,
        )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=list(result.facts),
        evidence=list(result.evidence),
        calculations=list(result.calculations),
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Pattern D
# --------------------------------------------------------------------------


def normalize_cross_entity_compare_result(
    result: CrossEntityCompareResult,
) -> AgentExecutionResult:
    """
    Pattern D의 구조화 결과를 공통 실행 Contract로 옮긴다.

    Fact/Evidence/CalculationResult/ComparisonResult는 이미 Contract 타입이므로 다시 만들지
    않고 그대로 싣는다. Pattern C와 같은 자리이며, 여기서 다시 변환하면 확보·계산 계층이
    이미 내린 판정이 두 곳에 생긴다.

    Pattern C와 다른 것은 comparisons 하나다. Q10·Q11은 최종 우열 판정을 요구하므로
    ComparisonResult가 결과에 실린다(evidence_and_answerability.md Calculation cardinality).
    Q08이 변화량 자체를 묻고 우열을 요구하지 않아 Pattern C가 comparisons를 비워 두는 것과
    대비된다.

    멈춘 결과에서 갈리는 세 갈래:
        pattern_entry에서 막힌 경우
            계획 모양이 이번 범위 밖인 경우다(unsupported_comparison_* /
            unsupported_derived_operation). 코퍼스를 열기 전이므로 ANSWERABILITY_BY_STOP에
            없고, BLOCKED로 나간다. 답변 가능성을 판정하지 않는 것이 규약이다. 코퍼스를
            보지 않았으므로 답이 있는지 없는지 알지 못한다.

        확보 단계에서 막힌 경우
            filing_discovery/no_matching_filing이나 extraction/* 계열이다. 대응표가 이미
            이 사유들을 COMPLETED + UNANSWERABLE로 옮기고 있고, Pattern D라고 다르게 볼
            이유가 없다. 새 대응을 만들지 않고 같은 표를 쓴다.

        비교 가능성(C9)에서 막힌 경우
            comparability/incomparable_* 계열이며 COMPLETED + UNANSWERABLE + NOT_COMPARABLE로
            나간다. 값을 다 확보한 뒤에 내린 판정이므로 실행은 끝났다.

            이때 facts와 evidence는 남고 calculations와 comparisons는 남지 않는다. 무엇이
            견줄 수 없다고 판정됐는지 보려면 그 값이 있어야 하지만, 기업별 파생값이나 우열
            판정이 함께 남으면 받는 쪽이 guard가 막은 비교를 그대로 이어서 할 수 있다.
            그 규칙은 _stopped_execution이 인자로 강제한다. calculations도 comparisons도
            받지 않으므로 이 자리에서 실수로 실을 수 없다.

            새 사유를 만들 때는 ANSWERABILITY_BY_STOP에 함께 등록해야 한다. 등록하지 않으면
            비교 가능성 판정이 BLOCKED로 나가 "판정에 이르지 못하고 멈췄다"가 되고, 실제로
            일어난 일과 달라진다.

    성공 경로에 answerability_reason을 싣지 않는다. ANSWERABLE은 사유를 갖지 않으며, 그
    규칙은 answer_builder의 ALLOWED_REASONS_BY_STATUS와 evaluation.runner의 GoldAnswer 검증이
    이미 고정하고 있다.
    """
    warnings = list(result.plan.warnings)

    if result.blocked is not None:
        return _stopped_execution(
            result.blocked,
            warnings=warnings,
            facts=result.facts,
            evidence=result.evidence,
            conversion_gaps=result.conversion_gaps,
        )

    if result.answerability_status is not None:
        return AgentExecutionResult(
            execution_status=ExecutionStatus.COMPLETED,
            answerability_status=result.answerability_status,
            answerability_reason=result.answerability_reason,
            answerability_detail=(
                _answerability_detail(result.answerability_detail)
                if result.answerability_detail is not None
                else None
            ),
            facts=list(result.facts),
            evidence=list(result.evidence),
            warnings=warnings,
            conversion_gaps=list(result.conversion_gaps),
        )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=list(result.facts),
        evidence=list(result.evidence),
        calculations=list(result.calculations),
        comparisons=list(result.comparisons),
        warnings=warnings,
        conversion_gaps=list(result.conversion_gaps),
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
        operations=list(result.plan.operations),
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


# --------------------------------------------------------------------------
# Q06 거래소공시 단건 요약
# --------------------------------------------------------------------------


def normalize_event_summary_result(result: EventSummaryResult) -> AgentExecutionResult:
    """
    Q06의 구조화 결과를 공통 실행 Contract로 옮긴다.

    ResolvedEventState / EventHistory / EventLink / Evidence는 이미 Contract 타입이므로
    다시 만들지 않고 그대로 싣는다. Pattern E를 옮길 때와 같은 규약이다.

    판정을 여기서 새로 내리지 않는다:
        status와 reason은 handler가 정한다. 필수 요약 항목이 무엇인지, 그중 무엇이 유보이고
        무엇이 미확보인지는 그쪽이 알고 이쪽은 모른다. 여기서 다시 판정하면 같은 규칙이 두
        벌이 된다.

        예외는 정지 지점이다. 어느 정지가 '실행은 끝났고 답이 없다'이고 어느 정지가 '판정에
        이르지 못했다'인지는 ANSWERABILITY_BY_STOP 하나가 정하며, 그 대응표가 이 모듈의
        것이다.

    상태가 없는 결과를 그대로 싣는다:
        최신 유효 상태를 요구했는데 체인을 확정하지 못하면 handler가 상태 없이
        PARTIALLY_ANSWERABLE + INCOMPLETE_CHAIN을 돌려준다. 그때 답의 근거는 EventHistory와
        읽은 문서의 필드 근거이며, 원공시 값을 최신값 자리에 채워 넣지 않는다. 여기서 상태를
        만들어 메우면 handler가 막은 일이 이 계층에서 그대로 일어난다.
    """
    warnings = [*result.plan.warnings, *result.warnings]

    gaps = [to_conversion_gap(gap) for gap in result.conversion_gaps]

    if result.blocked is not None:
        if result.state is None and result.history is None:
            gaps.extend(_root_candidate_gaps(result))

        return _stopped_execution(
            result.blocked,
            warnings=warnings,
            event_links=result.links,
            evidence=result.evidence,
            conversion_gaps=gaps,
        )

    if result.state is None and result.history is None:
        # 루트를 확정하지 못해 event_id를 만들지 않은 경우다. 실행은 끝났고 판정만 미해결로
        # 남는다(Pattern E의 같은 경로와 동일하다).
        gaps.extend(_root_candidate_gaps(result))

        return AgentExecutionResult(
            execution_status=ExecutionStatus.COMPLETED,
            answerability_status=result.status,
            answerability_reason=result.reason,
            event_links=list(result.links),
            evidence=list(result.evidence),
            warnings=warnings,
            conversion_gaps=gaps,
        )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=result.status,
        answerability_reason=result.reason,
        events=[result.event] if result.event is not None else [],
        event_links=list(result.links),
        event_histories=[result.history] if result.history is not None else [],
        resolved_event_states=[result.state] if result.state is not None else [],
        evidence=list(result.evidence),
        warnings=warnings,
        conversion_gaps=gaps,
    )


def _root_candidate_gaps(
    result: EventHistoryResult | EventSummaryResult,
) -> list[ConversionGap]:
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
