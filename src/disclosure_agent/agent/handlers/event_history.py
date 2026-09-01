"""Pattern E — Event History handler.

역할:
- 거래소공시 하나와 그 정정 이력을 모아 무엇이 언제 바뀌었는지 구조화한다.

현재 구현 범위:
- 대표 질문 Q13 계열(공급계약 1건의 정정 추적)과 Q15 계열(신규시설투자 1건의 정정 추적)을
  다룬다. 두 유형은 같은 파이프라인을 쓰고 EventType만 다르다.

    QueryPlan
      -> 원공시 후보 조회        (retrieval.metadata_filter, 질문이 지정한 기간)
      -> 체인 후보 조회          (retrieval.metadata_filter, 기간 제한 없음)
      -> ParsedDocument         (event.parser)
      -> 연결 판정 / 체인 구성    (event.linker)
      -> 필드 변경 정리          (event.diff)
      -> Contract 타입 변환       (event.contract_adapter)
      -> EventHistoryResult
      -> AgentExecutionResult    (agent.normalization)

기간을 두 번 나눠 조회하는 이유:
    질문의 기간("2024년 8월")은 **원공시를 지목하는 조건**이지 체인의 범위가 아니다. 그 창으로
    체인까지 자르면 이후 연도의 정정이 통째로 빠져 "바뀐 것이 없다"는 잘못된 답이 나온다.
    원공시는 창 안에서 찾고, 체인은 창 밖까지 본다.

기간을 밝히지 않은 질문:
    Q15처럼 공시 시점을 말하지 않는 이력 질문이 있다. 그때 계획의 years는 비어 있고
    (agent.query_analyzer.EventSubtypeRule.requires_filing_anchor), 원공시 조회에 창을 걸지
    않는다. 창이 없어도 기업과 공시 유형이 후보를 좁히며, 그래도 원공시가 여럿이면 아래
    ambiguous_root가 막는다. 창이 없다는 이유로 후보 하나를 고르지 않는다.

자연어 답변을 만들지 않는 이유:
    이 handler는 구조화된 결과까지만 만든다. Contract의 AnswerResponse는 answer를 필수 문자열로
    요구하지만, 빈 문자열이나 기계 문장으로 형식만 맞추면 "답을 만들었다"와 "아직 못 만들었다"가
    구분되지 않는다. 문장 생성은 이후 answer-builder 단계의 책임이다.

입력:
- QueryPlan, corpus 루트, manifest 경로

출력:
- AgentExecutionResult. 실패해도 예외를 올리지 않는다.
  Pattern 내부 표현(EventHistoryResult)이 필요하면 build_event_history_result를 직접 쓴다.

TODO:
- 후속·해지 방향 연결(본문 '관련공시')로 Q14 확장
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from disclosure_agent.agent.answer_builder import (
    REASON_AMBIGUOUS_ROOT,
    REASON_NO_MATCHING_FILING,
    REASON_NO_ORIGINAL_FILING,
    REASON_PARSE_FAILED,
    REASON_UNSUPPORTED_EVENT_TYPE,
    STAGE_CONTRACT_ADAPTATION,
    STAGE_FILING_DISCOVERY,
    STAGE_PARSING,
    STAGE_PATTERN_ENTRY,
    BlockedInfo,
)
from disclosure_agent.agent.normalization import normalize_event_history_result
from disclosure_agent.agent.query_analyzer import (
    EVENT_SUBTYPE_FACILITY_INVESTMENT,
    EVENT_SUBTYPE_SUPPLY_CONTRACT,
    QueryPlan,
)
from disclosure_agent.common.enums import (
    AnswerabilityStatus,
    EventType,
    EvidenceLocationType,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    EventHistory,
    EventLink,
    Evidence,
)
from disclosure_agent.event.contract_adapter import (
    ContractGap,
    build_change_evidence_id,
    to_event_history,
)
from disclosure_agent.event.linker import FilingChain, FilingLike, build_filing_chain
from disclosure_agent.event.parser import parse_exchange_document
from disclosure_agent.retrieval.metadata_filter import FilingNotFoundError, list_event_filings

# 정지 사유와 BlockedInfo는 answer_builder가 어휘를 들고 있다. Pattern마다 같은 사유를 다른
# 문자열로 쓰면 정규화 대응표와 대조할 수 없다.

# 질문이 지목한 공시 세부유형(manifest의 doc_subtype)을 이벤트 유형으로 옮긴다.
#
# 상수 하나로 고정하지 않는 이유:
#     예전에는 EVENT_TYPE = SUPPLY_CONTRACT 하나였다. 그 상태로 신규시설투자 계획이 들어오면
#     '투자금액'·'투자기간 종료일' 같은 정정 항목이 공급계약 어휘로 조회되어 한 건도 이름이
#     붙지 않는다(event.correction_fields의 대응표는 유형별로 나뉘어 있다). 그러면 변경이
#     전부 field=None으로 남아 "바뀐 것이 없다"에 가까운 결과가 되고, 그것이 틀린 답이다.
#
# 모르는 유형은 상수로 대신하지 않는다. resolve_event_type이 None을 돌려주고 호출부가 막는다.
EVENT_TYPE_BY_DOCUMENT_SUBTYPE: dict[str, EventType] = {
    EVENT_SUBTYPE_SUPPLY_CONTRACT: EventType.SUPPLY_CONTRACT,
    EVENT_SUBTYPE_FACILITY_INVESTMENT: EventType.FACILITY_INVESTMENT,
}

# 같은 문서의 같은 canonical field는 현재 행 단위 Evidence ID를 공유하게 된다. 서로 다른
# 원문 행을 하나의 근거처럼 보이게 만들 수 있으므로 Contract 변환 전에 실행을 막는다.
REASON_EVENT_EVIDENCE_ID_COLLISION = "event_evidence_id_collision"


def resolve_event_type(document_subtype: str) -> EventType | None:
    """계획의 공시 세부유형에 대응하는 이벤트 유형. 모르는 유형이면 None."""
    return EVENT_TYPE_BY_DOCUMENT_SUBTYPE.get(document_subtype)


@dataclass(frozen=True)
class EventHistoryResult:
    """
    Pattern E의 구조화 결과.

    Contract의 AnswerResponse가 아니다. 자연어 answer를 만들지 않고, 이후 answer-builder가
    이 결과를 받아 최종 응답을 조립한다.

    status가 None이면 답변 가능성을 판정할 단계에 이르지 못한 것이다(blocked 참조).
    데이터가 없어서 답할 수 없는 것과 파이프라인이 막힌 것은 다르다.
    """

    plan: QueryPlan
    status: AnswerabilityStatus | None = None
    history: EventHistory | None = None
    links: tuple[EventLink, ...] = field(default_factory=tuple)
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)
    conversion_gaps: tuple[ContractGap, ...] = field(default_factory=tuple)
    root_candidate_doc_ids: tuple[str, ...] = field(default_factory=tuple)
    blocked: BlockedInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.plan.intent,
            "status": self.status.value if self.status else None,
            "query": self.plan.to_dict(),
            "event_history": self.history.model_dump() if self.history else None,
            "event_links": [link.model_dump() for link in self.links],
            "evidence": [item.model_dump() for item in self.evidence],
            "conversion_gaps": [gap.to_dict() for gap in self.conversion_gaps],
            "root_candidate_doc_ids": list(self.root_candidate_doc_ids),
            "blocked": self.blocked.to_dict() if self.blocked else None,
        }


def filing_date_window(year: int, month: int | None) -> tuple[str, str]:
    """
    질문이 지정한 기간을 공시일 범위로 바꾼다.

    월이 없으면 그 해 전체를 본다. 월이 있으면 그 달의 실제 마지막 날까지만 본다.
    """
    if month is None:
        return f"{year}0101", f"{year}1231"

    last_day = calendar.monthrange(year, month)[1]

    return f"{year}{month:02d}01", f"{year}{month:02d}{last_day:02d}"


def root_filing_window(plan: QueryPlan) -> tuple[str, str] | None:
    """
    원공시 후보를 좁힐 공시일 범위. 질문이 시점을 밝히지 않았으면 None.

    None은 '기간 조건 없음'이며 '아무 문서나 좋다'가 아니다. 기업과 공시 유형 조건은 그대로
    걸리고, 그러고도 원공시가 여럿이면 호출부가 ambiguous_root로 막는다.

    plan.year를 바로 읽지 않는 이유는 그 통로가 단일 기간을 전제하기 때문이다. 연도가 없는
    계획에서 그대로 부르면 ValueError가 난다. 여기서 먼저 판정해 예외 대신 None으로 가른다.
    """
    if not plan.years:
        return None

    return filing_date_window(plan.year, plan.month)


def build_event_evidence(chain: FilingChain) -> tuple[Evidence, ...]:
    """
    체인의 각 문서와 변경 필드에 대한 근거를 만든다.

    문서 단위 근거를 따로 두는 이유는 원공시가 변경 항목을 갖지 않기 때문이다. 변경 근거만
    만들면 이력의 출발점이 근거 없이 남는다.

    거래소공시는 Section 구조가 없으므로 location_type은 event_field를 쓴다.
    변경 행의 raw_text에는 원문 필드 표기와 before/after 값을 함께 남긴다. 셋은 parser가
    보존한 같은 정정 행에서 왔으며, 별도의 셀 위치를 추론하지 않는다.
    """
    evidence: list[Evidence] = []

    for entry in chain.entries:
        doc_id = _doc_id(entry.filing)

        evidence.append(
            Evidence(
                evidence_id=f"evidence_{doc_id}_document",
                doc_id=doc_id,
                location_type=EvidenceLocationType.EVENT_FIELD,
                raw_text=entry.filing.report_name
                if hasattr(entry.filing, "report_name")
                else None,
            )
        )

        for change in entry.changes:
            if change.field is None:
                # 이름을 붙이지 못한 변경은 Contract FieldChange로 옮기지 않았다.
                # 근거도 만들지 않는다. 무엇을 가리키는지 확정하지 못했기 때문이다.
                continue

            evidence.append(
                Evidence(
                    evidence_id=build_change_evidence_id(doc_id, change.field),
                    doc_id=doc_id,
                    location_type=EvidenceLocationType.EVENT_FIELD,
                    field_name=change.field,
                    raw_text=(
                        f"raw_field: {change.raw_field}\n"
                        f"before: {change.before}\n"
                        f"after: {change.after}"
                    ),
                )
            )

    return tuple(evidence)


def build_event_history_result(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> EventHistoryResult:
    """
    정정 이력 조회 계획 하나를 구조화 결과까지 처리한다.

    실패해도 예외를 올리지 않는다. 어느 단계에서 왜 막혔는지 호출자가 그대로 보게 한다.
    """
    corpus_root = Path(corpus_root)

    manifest_path = Path(manifest_path) if manifest_path else corpus_root / "manifest.jsonl"

    retrieval_kwargs = {
        "manifest_path": manifest_path,
        "corpus_root": corpus_root,
        "company": plan.company,
        "document_subtype": plan.document_subtype,
    }

    event_type = resolve_event_type(plan.document_subtype)

    if event_type is None:
        # 유형을 모르면 정정 항목의 이름을 붙일 수 없다. 가까운 유형으로 대신 처리하면
        # 변경이 전부 이름 없이 남고, 그 결과는 "바뀐 것이 없다"와 구분되지 않는다.
        return EventHistoryResult(
            plan=plan,
            blocked=BlockedInfo(
                stage=STAGE_PATTERN_ENTRY,
                reason=REASON_UNSUPPORTED_EVENT_TYPE,
                detail=(
                    f"'{plan.document_subtype}'에 대응하는 이벤트 유형이 없습니다. "
                    f"현재 지원하는 유형: "
                    f"{', '.join(sorted(EVENT_TYPE_BY_DOCUMENT_SUBTYPE))}."
                ),
            ),
        )

    root_window = root_filing_window(plan)

    try:
        # 원공시 후보: 질문이 지정한 기간 안에서만 찾는다. 정정본은 원공시가 아니다.
        # 질문이 기간을 밝히지 않았으면(root_window is None) 기간 조건 없이 찾는다.
        root_candidates = list_event_filings(
            **retrieval_kwargs,
            include_corrections=False,
            date_range=root_window,
        )

        # 체인 후보: 기간 제한 없이 본다. 정정은 이후 연도에 나온다.
        chain_candidates = list_event_filings(
            **retrieval_kwargs,
            include_corrections=True,
        )

    except FilingNotFoundError as error:
        return EventHistoryResult(
            plan=plan,
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=error.reason,
                detail=error.detail,
            ),
        )

    if not chain_candidates:
        return EventHistoryResult(
            plan=plan,
            status=AnswerabilityStatus.UNANSWERABLE,
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=REASON_NO_MATCHING_FILING,
                detail=(
                    f"{plan.company}의 {plan.document_subtype} 공시를 코퍼스에서 찾지 "
                    "못했습니다."
                ),
            ),
        )

    if not root_candidates:
        detail = (
            f"{plan.company}의 {plan.document_subtype} 원공시가 없습니다. "
            "정정본만으로는 이력의 출발점을 정하지 않습니다."
            if root_window is None
            else (
                f"{root_window[0]}~{root_window[1]} 사이에 제출된 원공시가 없습니다. "
                "다른 기간의 공시로 대신하지 않습니다."
            )
        )

        return EventHistoryResult(
            plan=plan,
            status=AnswerabilityStatus.UNANSWERABLE,
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=REASON_NO_ORIGINAL_FILING,
                detail=detail,
            ),
        )

    if len(root_candidates) > 1:
        # 어느 계약의 이력인지 정하지 못한다. 하나를 골라 답하면 다른 계약의 이력을
        # 그 계약의 이력이라고 말하게 된다.
        return EventHistoryResult(
            plan=plan,
            status=AnswerabilityStatus.UNRESOLVED,
            root_candidate_doc_ids=tuple(_doc_id(filing) for filing in root_candidates),
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=REASON_AMBIGUOUS_ROOT,
                detail=(
                    f"조건에 맞는 원공시가 {len(root_candidates)}건이라 어느 건의 이력인지 "
                    "특정할 수 없습니다. 본문 대조로 문서를 확정해야 합니다."
                ),
            ),
        )

    root = root_candidates[0]

    try:
        documents = {
            filing.receipt_no: parse_exchange_document(filing.main_document)
            for filing in chain_candidates
        }

    except (OSError, ValueError) as error:
        return EventHistoryResult(
            plan=plan,
            blocked=BlockedInfo(
                stage=STAGE_PARSING,
                reason=REASON_PARSE_FAILED,
                detail=str(error),
            ),
        )

    chain = build_filing_chain(
        root_receipt_no=root.receipt_no,
        filings=chain_candidates,
        documents=documents,
        event_type=event_type,
    )

    collision_key = find_event_evidence_id_collision(chain)

    if collision_key is not None:
        source_doc_id, canonical_field = collision_key
        return EventHistoryResult(
            plan=plan,
            root_candidate_doc_ids=(_doc_id(root),),
            blocked=BlockedInfo(
                stage=STAGE_CONTRACT_ADAPTATION,
                reason=REASON_EVENT_EVIDENCE_ID_COLLISION,
                detail=(
                    "서로 다른 정정 행이 같은 행 단위 Evidence ID를 만들 수 있어 "
                    "EventHistory 변환을 중단했습니다: "
                    f"source_doc_id={source_doc_id!r}, "
                    f"canonical_field={canonical_field!r}."
                ),
            ),
        )

    conversion = to_event_history(
        chain,
        documents=documents,
        filings=chain_candidates,
        event_type=event_type,
    )

    if conversion.history is None:
        # 루트를 확정하지 못해 canonical event_id를 만들지 않은 경우다.
        return EventHistoryResult(
            plan=plan,
            status=AnswerabilityStatus.UNRESOLVED,
            links=conversion.links,
            conversion_gaps=conversion.conversion_gaps,
            root_candidate_doc_ids=(_doc_id(root),),
        )

    return EventHistoryResult(
        plan=plan,
        status=_judge_answerability(chain),
        history=conversion.history,
        links=conversion.links,
        evidence=build_event_evidence(chain),
        conversion_gaps=conversion.conversion_gaps,
        root_candidate_doc_ids=(_doc_id(root),),
    )


def _judge_answerability(chain: FilingChain) -> AnswerabilityStatus:
    """
    확보한 체인으로 어디까지 답할 수 있는지 판정한다.

    체인이 완결됐으면 답할 수 있다. 완결되지 않았어도 확보한 구간 자체는 원문이 밝힌 연결만
    따라간 것이므로 신뢰할 수 있다. 그 경우 확보분을 답하고 미해결분을 밝힌다.
    """
    if chain.is_complete:
        return AnswerabilityStatus.ANSWERABLE

    return AnswerabilityStatus.PARTIALLY_ANSWERABLE


def find_event_evidence_id_collision(chain: FilingChain) -> tuple[str, str] | None:
    """첫 중복 (source_doc_id, canonical_field) 키를 결정론적으로 찾는다."""
    seen: set[tuple[str, str]] = set()

    for entry in chain.entries:
        source_doc_id = _doc_id(entry.filing)

        for change in entry.changes:
            if change.field is None:
                continue

            key = (source_doc_id, change.field)

            if key in seen:
                return key

            seen.add(key)

    return None


def _doc_id(filing: FilingLike) -> str:
    return getattr(filing, "doc_id", "") or filing.receipt_no


def handle_event_history(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> AgentExecutionResult:
    """
    Router가 부르는 진입점.

    공통 Contract인 AgentExecutionResult를 돌려준다. Pattern 내부 표현이 필요한 호출자는
    build_event_history_result를 직접 쓴다.
    """
    return normalize_event_history_result(
        build_event_history_result(
            plan,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )
    )
