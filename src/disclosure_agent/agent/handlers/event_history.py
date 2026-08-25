"""Pattern E — Event History handler.

역할:
- 거래소공시 하나와 그 정정 이력을 모아 무엇이 언제 바뀌었는지 구조화한다.

현재 구현 범위:
- 대표 질문 Q13 계열(공급계약 1건의 정정 추적)만 다룬다.

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
- 필드별 변경 궤적으로 Q15 확장
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
    STAGE_FILING_DISCOVERY,
    STAGE_PARSING,
    BlockedInfo,
)
from disclosure_agent.agent.normalization import normalize_event_history_result
from disclosure_agent.agent.query_analyzer import QueryPlan
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
from disclosure_agent.event.contract_adapter import ContractGap, to_event_history
from disclosure_agent.event.linker import FilingChain, FilingLike, build_filing_chain
from disclosure_agent.event.parser import parse_exchange_document
from disclosure_agent.retrieval.metadata_filter import FilingNotFoundError, list_event_filings

# 정지 사유와 BlockedInfo는 answer_builder가 어휘를 들고 있다. Pattern마다 같은 사유를 다른
# 문자열로 쓰면 정규화 대응표와 대조할 수 없다.

# 이번 슬라이스가 다루는 이벤트 유형.
EVENT_TYPE = EventType.SUPPLY_CONTRACT


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


def build_event_evidence(chain: FilingChain) -> tuple[Evidence, ...]:
    """
    체인의 각 문서와 변경 필드에 대한 근거를 만든다.

    문서 단위 근거를 따로 두는 이유는 원공시가 변경 항목을 갖지 않기 때문이다. 변경 근거만
    만들면 이력의 출발점이 근거 없이 남는다.

    거래소공시는 Section 구조가 없으므로 location_type은 event_field를 쓴다.
    raw_text에는 원문 필드 표기를 그대로 남긴다. Contract FieldChange가 원문 라벨을 담지
    못하는 것과 달리 Evidence는 담을 수 있어, 원문 대조 경로가 여기서는 끊기지 않는다.
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
                    evidence_id=f"evidence_{doc_id}_{change.field}",
                    doc_id=doc_id,
                    location_type=EvidenceLocationType.EVENT_FIELD,
                    field_name=change.field,
                    raw_text=change.raw_field,
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

    try:
        # 원공시 후보: 질문이 지정한 기간 안에서만 찾는다. 정정본은 원공시가 아니다.
        root_candidates = list_event_filings(
            **retrieval_kwargs,
            include_corrections=False,
            date_range=filing_date_window(plan.year, plan.month),
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
        window = filing_date_window(plan.year, plan.month)

        return EventHistoryResult(
            plan=plan,
            status=AnswerabilityStatus.UNANSWERABLE,
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=REASON_NO_ORIGINAL_FILING,
                detail=(
                    f"{window[0]}~{window[1]} 사이에 제출된 원공시가 없습니다. "
                    "다른 기간의 공시로 대신하지 않습니다."
                ),
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
                    f"조건에 맞는 원공시가 {len(root_candidates)}건이라 어느 계약의 이력인지 "
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
    )

    conversion = to_event_history(
        chain,
        documents=documents,
        filings=chain_candidates,
        event_type=EVENT_TYPE,
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
