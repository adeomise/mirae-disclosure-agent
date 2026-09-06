"""Q06 — 거래소공시 단건 요약 handler.

역할:
- 질문이 가리키는 이벤트공시 1건을 본문 주제어로 확정하고, 그 이벤트의 필드 상태를
  요청한 시점(최신 유효 / 원공시) 기준으로 돌려준다.

현재 구현 범위:
- 대표 질문 Q06 계열("<기업> <주제> 공시 내용 요약해줘").

    QueryPlan
      -> 후보 공시 조회            (retrieval.metadata_filter, 기업 + 지원 유형)
      -> ParsedDocument            (event.parser)
      -> canonical 이벤트          (event.extractor)
      -> 주제어 대조로 원공시 확정   (이 모듈)
      -> 체인 구성                 (event.linker)
      -> EventHistory / EventLink   (event.contract_adapter)
      -> ResolvedEventState        (event.latest_state)
      -> EventSummaryResult
      -> AgentExecutionResult      (agent.normalization)

이벤트 계층을 다시 만들지 않는다:
    파싱·연결·정정 해석·상태 확정은 event 패키지가 이미 하고 있고 실제 코퍼스에서 검증됐다.
    이 모듈이 하는 일은 질문 해석 결과를 그 계층의 입력으로 옮기고, 나온 결과를 공통
    Contract로 넘기는 것뿐이다. 여기에 판정 규칙을 다시 적으면 두 벌이 된다.

문서를 1건으로 확정하는 방법:
    메타데이터만으로는 좁혀지지 않는다. 거래소공시 manifest 레코드에는 회계연도가 없고,
    한 기업이 같은 유형의 공시를 여러 건 낼 수 있다. 그래서 후보를 전부 열어 본문의 주제
    필드를 읽고, 질문이 밝힌 주제어가 전부 걸리는 문서만 남긴다.

    접수번호나 doc_id로 고르지 않는다. 그렇게 고르면 그 질문 하나만 답하고 같은 모양의 다른
    질문은 답하지 못하며, 코퍼스가 바뀌면 조용히 다른 문서를 가리킨다. 좁힌 뒤에도 1건이
    아니면 고르지 않는다(agent.event_retrieval이 계약 상대방에 대해 하는 판정과 같다).

판본(시점) 선택:
    질문이 정한다. 계획의 chain_policy가 정본이며 이 모듈이 기본값을 고르지 않는다
    (docs/agent_design/open_decisions.md D5가 "chain_policy 필드가 이 정책을 표현하는
    자리"라고 적어 둔 곳).

        latest_valid    정정을 반영한 최신 유효 상태. 체인이 완결돼야만 만든다.
        original_only   원공시 시점 상태. 이후 정정을 아예 보지 않는다.

    체인을 끝까지 확정하지 못한 채로 최신 상태를 만들지 않는다. 만들면 미해결 정정 뒤에 있는
    낡은 값이 '지금 유효한 값'으로 나가고, 받는 쪽이 그것을 알 방법이 없다.

입력:
- QueryPlan, corpus 루트, manifest 경로

출력:
- AgentExecutionResult. 실패해도 예외를 올리지 않는다.
  Pattern 내부 표현(EventSummaryResult)이 필요한 호출자는 build_event_summary_result를
  직접 쓴다.

TODO:
- 공급계약(supply_contract)의 필수 요약 필드 확정. 지금은 유형 자체를 막는다.
- 후속·해지 관계가 붙었을 때의 시점 어휘(ChainPolicy 확장)
"""

from __future__ import annotations

import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from disclosure_agent.agent.answer_builder import (
    REASON_AMBIGUOUS_ROOT,
    REASON_NO_MATCHING_FILING,
    REASON_NO_ORIGINAL_FILING,
    REASON_PARSE_FAILED,
    STAGE_CONTRACT_ADAPTATION,
    STAGE_FILING_DISCOVERY,
    STAGE_PARSING,
    STAGE_PATTERN_ENTRY,
    BlockedInfo,
)
from disclosure_agent.agent.handlers.event_history import (
    EVENT_TYPE_BY_DOCUMENT_SUBTYPE,
    root_filing_window,
)
from disclosure_agent.agent.normalization import normalize_event_summary_result
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ChainPolicy,
    EventType,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    Event,
    EventHistory,
    EventLink,
    Evidence,
    ResolvedEventState,
)
from disclosure_agent.event.adapter import EventAdapterResult, adapt_extracted_event
from disclosure_agent.event.contract_adapter import ContractGap, to_event_history
from disclosure_agent.event.correction_fields import (
    classify_latest_state_blocking_correction,
    resolve_correction_field,
)
from disclosure_agent.event.exceptions import UnsupportedCompositeCorrectionError
from disclosure_agent.event.extractor import extract_event
from disclosure_agent.event.latest_state import (
    resolve_latest_event_state,
    resolve_original_event_state,
)
from disclosure_agent.event.linker import FilingChain, build_filing_chain
from disclosure_agent.event.parser import parse_exchange_document
from disclosure_agent.retrieval.metadata_filter import (
    FilingNotFoundError,
    FilingRef,
    list_event_filings,
)

__all__ = [
    "REASON_UNREPRESENTABLE_CORRECTION_CHANGE",
    "REASON_UNRESOLVABLE_EVENT_STATE",
    "REASON_UNSUPPORTED_COMPOSITE_CORRECTION",
    "REASON_UNSUPPORTED_EVENT_SUMMARY_TYPE",
    "REQUIRED_SUMMARY_FIELDS_BY_EVENT_TYPE",
    "SUBJECT_FIELDS_BY_EVENT_TYPE",
    "EventSummaryResult",
    "build_event_summary_result",
    "find_unrepresentable_correction_change",
    "handle_event_summary",
    "judge_summary_answerability",
    "select_root_by_subject",
]

# 이 handler가 만드는 정지 사유.
#
# 나머지 사유(no_matching_filing / no_original_filing / ambiguous_root / parse_failed /
# unsupported_event_type)는 answer_builder의 공용 어휘를 그대로 쓴다. 같은 상황에 새 이름을
# 붙이면 normalization의 대응표와 대조할 수 없다.
REASON_UNSUPPORTED_EVENT_SUMMARY_TYPE = "unsupported_event_summary_type"
REASON_UNSUPPORTED_COMPOSITE_CORRECTION = "unsupported_composite_correction"
REASON_UNREPRESENTABLE_CORRECTION_CHANGE = "unrepresentable_correction_change"
REASON_UNRESOLVABLE_EVENT_STATE = "unresolvable_event_state"

# 이벤트 유형별 필수 요약 필드.
#
# 이름은 event.extractor의 canonical field 어휘다(FACILITY_INVESTMENT_FIELDS의 값). 여기서
# 새 이름을 만들지 않으며, 두 어휘가 어긋나면 tests/unit/test_q06_event_summary_handler.py가
# 잡는다.
#
# 이 다섯으로 둔 이유:
#     Q06이 묻는 것은 "이 투자 공시가 무엇을, 얼마에, 왜, 언제부터 언제까지"다. 자기자본·
#     자기자본대비·이사회결의일·기타사항은 문서에 함께 있지만 그 물음의 답이 아니다. 필수
#     집합을 넓히면 질문이 묻지 않은 항목의 유보가 답변 가능성을 낮춘다.
#
#     반대로 좁히지도 않는다. 다섯 중 하나라도 빠지면 "무엇을 얼마에"가 성립하지 않는다.
#
# 공급계약은 담지 않는다. 그 유형의 필수 요약 집합을 정하지 않았고, 정하지 않은 것을 정한
# 척하면 아무 필드나 담은 요약이 형식만 맞은 채로 나간다. 유형 자체를 입구에서 막는다.
REQUIRED_SUMMARY_FIELDS_BY_EVENT_TYPE: dict[EventType, tuple[str, ...]] = {
    EventType.FACILITY_INVESTMENT: (
        "investment_target",
        "investment_amount",
        "investment_purpose",
        "investment_start_date",
        "investment_end_date",
    ),
}

# 주제어를 대조할 본문 필드.
#
# 이벤트가 '무엇에 대한 것인가'를 담는 필드만 둔다. 금액·날짜·자기자본은 주제가 아니며,
# 숫자에 주제어가 우연히 걸리면 다른 공시가 후보로 들어온다.
#
# notes(기타 투자판단과 관련한 중요사항)는 넣지 않는다. 그 필드는 다른 필드의 값을 그대로
# 다시 서술하는 긴 자유 문장이라, 넣으면 거의 모든 주제어가 걸려 좁히는 조건이 되지 않는다.
SUBJECT_FIELDS_BY_EVENT_TYPE: dict[EventType, tuple[str, ...]] = {
    EventType.FACILITY_INVESTMENT: (
        "investment_type",
        "investment_target",
        "investment_purpose",
    ),
    EventType.SUPPLY_CONTRACT: (
        "contract_type",
        "contract_name",
        "counterparty",
        "supply_region",
    ),
}


@dataclass(frozen=True)
class EventSummaryCandidate:
    """후보 공시 1건과 그 문서에서 읽은 것들."""

    filing: FilingRef
    document: dict[str, Any]
    extracted: dict[str, Any]

    @property
    def doc_id(self) -> str:
        return self.filing.doc_id or self.filing.receipt_no


@dataclass(frozen=True)
class EventSummaryResult:
    """
    Q06의 구조화 결과.

    공통 Contract가 아니다. 공통 타입으로 옮기는 일은 agent.normalization이 한다.

    status가 None이면 답변 가능성을 판정할 단계에 이르지 못한 것이다(blocked 참조).

    state가 없는데 history가 있는 경우가 있다. 최신 유효 상태를 요구했으나 체인을 끝까지
    확정하지 못한 경우이며, 그때 확보한 것은 '어디까지 이어지는 이력을 봤는가'뿐이다.
    원공시 값을 최신값 자리에 놓지 않는다.

    filings를 따로 들고 다니는 이유:
        event/state는 이미 공통 Contract 타입이지만 그 안에는 FilingRef가 없다.
        source_documents를 만들려면 evidence가 실제로 가리키는 문서(원공시일 수도, 필드를
        갱신한 정정공시일 수도 있다)의 FilingRef가 필요하다. 값은 evidence와 정확히 같은
        doc_id 집합만 담는다 — 체인의 다른 문서까지 넓히지 않는다.
    """

    plan: QueryPlan
    status: AnswerabilityStatus | None = None
    reason: AnswerabilityReason | None = None
    event: Event | None = None
    state: ResolvedEventState | None = None
    history: EventHistory | None = None
    links: tuple[EventLink, ...] = field(default_factory=tuple)
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)
    conversion_gaps: tuple[ContractGap, ...] = field(default_factory=tuple)
    root_candidate_doc_ids: tuple[str, ...] = field(default_factory=tuple)
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    withheld_fields: tuple[str, ...] = field(default_factory=tuple)
    filings: tuple[FilingRef, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    blocked: BlockedInfo | None = None


def _match_key(text: Any) -> str:
    """주제어 대조에 쓰는 키. 공백을 걷고 대소문자와 자모 표기를 맞춘다."""
    if text is None:
        return ""

    return "".join(unicodedata.normalize("NFC", str(text)).split()).casefold()


def subject_text(extracted: Mapping[str, Any], document: Mapping[str, Any]) -> str:
    """
    이벤트 하나에서 주제어를 대조할 본문 텍스트를 모은다.

    문서 제목을 함께 넣는 이유는 거래소공시의 제목이 공시 유형과 기업을 담고 있어, 질문이
    유형 이름으로 주제를 말한 경우("시설투자")를 본문 필드만으로는 받지 못하기 때문이다.

    유형별 주제 필드가 없으면 제목만 본다. 모르는 유형에 대해 모든 필드를 훑지 않는다.
    """
    raw_event_type = str(extracted.get("event_type") or "")

    try:
        event_type = EventType(raw_event_type)

    except ValueError:
        field_names: tuple[str, ...] = ()

    else:
        field_names = SUBJECT_FIELDS_BY_EVENT_TYPE.get(event_type, ())

    fields = extracted.get("fields") or {}

    parts = [str(document.get("title") or "")]

    for field_name in field_names:
        entry = fields.get(field_name) or {}

        # 원문 표기와 정규화 값을 모두 본다. 정규화가 값을 바꾸지 않는 문자열 필드에서는
        # 같은 값이지만, 한쪽만 보면 정규화가 걷어낸 표기로 물은 질문이 걸리지 않는다.
        parts.append(str(entry.get("raw_value") or ""))
        parts.append(str(entry.get("normalized_value") or ""))

    return " ".join(part for part in parts if part)


def matches_subject(
    candidate: EventSummaryCandidate,
    subject_tokens: Sequence[str],
) -> bool:
    """
    후보 문서가 질문의 주제어를 전부 담고 있는가.

    전부를 요구한다. 하나라도 걸리면 통과시키면 "파주 AIDC 투자"가 '투자' 하나로 그 기업의
    모든 투자 공시에 걸리고, 사용자가 좁힌 조건이 조용히 사라진다.

    부분 문자열 비교다. 형태소를 나누거나 유사도를 쓰지 않는다. 어떤 낱말이 어떤 낱말과
    같은 뜻인지는 규칙이 정할 수 있는 것이 아니며, 규칙이 만들어 낸 일치로 문서를 고르면
    틀린 문서의 내용이 형식만 맞은 채로 답이 된다.
    """
    haystack = _match_key(subject_text(candidate.extracted, candidate.document))

    if not haystack:
        return False

    return all(_match_key(token) in haystack for token in subject_tokens if _match_key(token))


def select_root_by_subject(
    candidates: Sequence[EventSummaryCandidate],
    subject_tokens: Sequence[str],
) -> tuple[EventSummaryCandidate, ...]:
    """
    주제어가 걸리는 원공시 후보만 남긴다. 입력 순서를 보존한다.

    정정본은 후보로 세지 않는다. 요약의 대상은 이벤트 하나이고 그 이벤트를 처음 알린 문서가
    체인의 출발점이다. 정정본을 독립 후보로 세면 같은 이벤트가 후보 여러 건으로 보여
    ambiguous가 된다(handlers.event_history가 원공시를 모을 때 쓰는 기준과 같다).
    """
    return tuple(
        candidate
        for candidate in candidates
        if not candidate.filing.is_correction and matches_subject(candidate, subject_tokens)
    )


def _within_window(filing: FilingRef, window: tuple[str, str] | None) -> bool:
    """질문이 공시 시점을 밝힌 경우 그 창 안의 공시인가. 밝히지 않았으면 전부 참."""
    if window is None:
        return True

    receipt_date = filing.receipt_date or ""

    return window[0] <= receipt_date <= window[1]


def build_event_summary_result(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> EventSummaryResult:
    """
    거래소공시 단건 요약 계획 하나를 구조화 결과까지 처리한다.

    실패해도 예외를 올리지 않는다. 어느 단계에서 왜 막혔는지 호출자가 그대로 보게 한다.
    """
    corpus_root = Path(corpus_root)

    manifest_path = Path(manifest_path) if manifest_path else corpus_root / "manifest.jsonl"

    try:
        filings = list_event_filings(
            manifest_path=manifest_path,
            corpus_root=corpus_root,
            company=plan.company,
            document_subtype=plan.document_subtype,
            include_corrections=True,
        )

    except FilingNotFoundError as error:
        return EventSummaryResult(
            plan=plan,
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=error.reason,
                detail=error.detail,
            ),
        )

    # 요약할 수 있는 유형만 후보로 둔다. 유형을 모르면 canonical 필드 이름을 붙일 수 없고,
    # 이름 없는 필드로 만든 요약은 무엇을 말한 것인지 확인할 수 없다.
    supported = [
        filing
        for filing in filings
        if filing.document_subtype in EVENT_TYPE_BY_DOCUMENT_SUBTYPE
    ]

    if not supported:
        return EventSummaryResult(
            plan=plan,
            status=AnswerabilityStatus.UNANSWERABLE,
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=REASON_NO_MATCHING_FILING,
                detail=(
                    f"{plan.company}의 요약 가능한 거래소공시를 코퍼스에서 찾지 못했습니다. "
                    f"현재 요약하는 유형: "
                    f"{', '.join(sorted(EVENT_TYPE_BY_DOCUMENT_SUBTYPE))}."
                ),
            ),
        )

    try:
        candidates = tuple(
            _read_candidate(filing) for filing in supported
        )

    except (OSError, ValueError) as error:
        # 읽지 못한 후보를 조용히 빼지 않는다. 빼고 세면 후보가 2건인 상황이 1건으로 보이고,
        # 확정할 수 없는 질문에 다른 공시의 내용을 답하게 된다.
        return EventSummaryResult(
            plan=plan,
            blocked=BlockedInfo(
                stage=STAGE_PARSING,
                reason=REASON_PARSE_FAILED,
                detail=str(error),
            ),
        )

    window = root_filing_window(plan)

    in_window = tuple(
        candidate for candidate in candidates if _within_window(candidate.filing, window)
    )

    matched = select_root_by_subject(in_window, plan.event_subject_tokens)

    if not matched:
        listed = ", ".join(plan.event_subject_tokens)

        return EventSummaryResult(
            plan=plan,
            status=AnswerabilityStatus.UNANSWERABLE,
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=REASON_NO_ORIGINAL_FILING,
                detail=(
                    f"후보 {len(in_window)}건 중 '{listed}'에 해당하는 {plan.company}의 "
                    "원공시가 없습니다. 다른 공시로 대신하지 않습니다."
                ),
            ),
        )

    if len(matched) > 1:
        listed = ", ".join(candidate.doc_id for candidate in matched)

        return EventSummaryResult(
            plan=plan,
            status=AnswerabilityStatus.UNRESOLVED,
            root_candidate_doc_ids=tuple(candidate.doc_id for candidate in matched),
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=REASON_AMBIGUOUS_ROOT,
                detail=(
                    f"질문의 주제어에 해당하는 원공시가 {len(matched)}건이라 어느 공시를 "
                    f"요약할지 특정할 수 없습니다: {listed}. 한 건을 골라 답하지 않습니다."
                ),
            ),
        )

    root = matched[0]

    event_type = EVENT_TYPE_BY_DOCUMENT_SUBTYPE[root.filing.document_subtype]

    required_fields = REQUIRED_SUMMARY_FIELDS_BY_EVENT_TYPE.get(event_type)

    if required_fields is None:
        supported_types = ", ".join(
            sorted(item.value for item in REQUIRED_SUMMARY_FIELDS_BY_EVENT_TYPE)
        )

        return EventSummaryResult(
            plan=plan,
            root_candidate_doc_ids=(root.doc_id,),
            blocked=BlockedInfo(
                stage=STAGE_PATTERN_ENTRY,
                reason=REASON_UNSUPPORTED_EVENT_SUMMARY_TYPE,
                detail=(
                    f"'{event_type.value}' 공시의 필수 요약 항목이 정해지지 않았습니다. "
                    f"현재 요약하는 유형: {supported_types}. 항목을 임의로 골라 요약하지 "
                    "않습니다."
                ),
            ),
        )

    return _summarize_selected_event(
        plan,
        root=root,
        candidates=candidates,
        event_type=event_type,
        required_fields=required_fields,
    )


def _read_candidate(filing: FilingRef) -> EventSummaryCandidate:
    """후보 공시 1건을 열어 canonical 이벤트까지 읽는다.

    회사명·보고서명·공시일을 manifest에서 넘긴다. 거래소공시 본문은 그 셋을 표로 담지 않아
    parser가 metadata를 비워 두며, 넘기지 않으면 Event.company가 빈 문자열이 되어
    event.adapter가 거절한다.
    """
    document = parse_exchange_document(
        filing.main_document,
        corp_name=filing.corp_name,
        report_name=filing.report_name,
        receipt_date=filing.receipt_date,
    )

    return EventSummaryCandidate(
        filing=filing,
        document=document,
        extracted=extract_event(document),
    )


def _summarize_selected_event(
    plan: QueryPlan,
    *,
    root: EventSummaryCandidate,
    candidates: Sequence[EventSummaryCandidate],
    event_type: EventType,
    required_fields: Sequence[str],
) -> EventSummaryResult:
    """확정한 원공시 1건의 체인을 구성하고 요청한 시점의 상태를 만든다."""
    # 체인 후보는 같은 세부유형만 본다. 정정은 원공시와 같은 유형으로 제출되며, 다른 유형을
    # 섞으면 linker가 유형 대조로 걸러 낼 후보를 굳이 넣는 것이 된다.
    chain_candidates = tuple(
        candidate
        for candidate in candidates
        if candidate.filing.document_subtype == root.filing.document_subtype
    )

    # evidence가 가리키는 doc_id로 실제 선택된 FilingRef를 되짚을 때 쓴다. manifest를 다시
    # 조회하지 않고 이미 연 chain_candidates 안에서만 찾는다.
    filing_by_doc_id = {candidate.doc_id: candidate.filing for candidate in chain_candidates}

    chain_filings = [candidate.filing for candidate in chain_candidates]

    parsed_by_receipt_no = {
        candidate.filing.receipt_no: candidate.document for candidate in chain_candidates
    }

    chain = build_filing_chain(
        root_receipt_no=root.filing.receipt_no,
        filings=chain_filings,
        documents=parsed_by_receipt_no,
        event_type=event_type.value,
    )

    conversion = to_event_history(
        chain,
        documents=parsed_by_receipt_no,
        filings=chain_filings,
        event_type=event_type,
    )

    if conversion.history is None:
        # 루트를 확정하지 못해 canonical event_id를 만들지 않았다. 근거 없이 event_id를
        # 지어내면 다른 공시의 내용이 이 이벤트의 내용으로 묶인다.
        return EventSummaryResult(
            plan=plan,
            status=AnswerabilityStatus.UNRESOLVED,
            links=conversion.links,
            conversion_gaps=conversion.conversion_gaps,
            root_candidate_doc_ids=(root.doc_id,),
        )

    try:
        adapted = {
            candidate.doc_id: adapt_extracted_event(candidate.extracted)
            for candidate in chain_candidates
        }

    except ValueError as error:
        return EventSummaryResult(
            plan=plan,
            root_candidate_doc_ids=(root.doc_id,),
            blocked=BlockedInfo(
                stage=STAGE_CONTRACT_ADAPTATION,
                reason=REASON_UNRESOLVABLE_EVENT_STATE,
                detail=str(error),
            ),
        )

    root_event = adapted[root.doc_id].event

    warnings = (
        f"공시 판본은 질문이 정한 기준을 따랐습니다: chain_policy={plan.chain_policy}.",
    )

    if plan.chain_policy is ChainPolicy.ORIGINAL_ONLY:
        state, blocked = _resolve_state(
            resolve_original_event_state,
            root_event=root_event,
            history=conversion.history,
            chain=chain,
            documents=adapted,
        )

    elif not chain.is_complete:
        # 최신 유효 상태를 요구했는데 체인을 끝까지 확정하지 못했다. 원공시 값으로 대신하지
        # 않는다. 그 값은 이미 정정됐을 수 있고, 정정됐는지 여부를 이 실행은 알지 못한다.
        return EventSummaryResult(
            plan=plan,
            status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
            reason=AnswerabilityReason.INCOMPLETE_CHAIN,
            event=root_event,
            history=conversion.history,
            links=conversion.links,
            evidence=_state_evidence_for_document(adapted, root.doc_id),
            conversion_gaps=conversion.conversion_gaps,
            root_candidate_doc_ids=(root.doc_id,),
            # 이 경로의 evidence는 root 문서 하나에서만 나온다(_state_evidence_for_document).
            filings=(root.filing,),
            warnings=warnings,
        )

    else:
        unrepresentable = find_unrepresentable_correction_change(
            chain,
            event_type=event_type,
            required_fields=required_fields,
        )

        if unrepresentable is not None:
            source_doc_id, raw_field = unrepresentable

            return EventSummaryResult(
                plan=plan,
                event=root_event,
                links=conversion.links,
                conversion_gaps=conversion.conversion_gaps,
                root_candidate_doc_ids=(root.doc_id,),
                warnings=warnings,
                blocked=BlockedInfo(
                    stage=STAGE_CONTRACT_ADAPTATION,
                    reason=REASON_UNREPRESENTABLE_CORRECTION_CHANGE,
                    detail=(
                        "정정이 밝힌 변경 항목의 이름을 확정하지 못해 최신 상태를 만들지 "
                        f"않았습니다: source_doc_id={source_doc_id!r}, "
                        f"raw_field={raw_field!r}. 그 항목이 요약 대상인지 알 수 없는 채로 "
                        "원공시 값을 최신값으로 내보내지 않습니다."
                    ),
                ),
            )

        state, blocked = _resolve_state(
            resolve_latest_event_state,
            root_event=root_event,
            history=conversion.history,
            chain=chain,
            documents=adapted,
        )

    if state is None:
        return EventSummaryResult(
            plan=plan,
            event=root_event,
            links=conversion.links,
            conversion_gaps=conversion.conversion_gaps,
            root_candidate_doc_ids=(root.doc_id,),
            warnings=warnings,
            blocked=blocked,
        )

    # 근거를 먼저 모은다. 판정이 그것을 보고 내려지기 때문이다.
    #
    # 이 튜플이 결과에 실리는 Evidence 전부이고(normalization이 그대로 옮긴다), 판정은 필수
    # 항목의 값이 그중 하나에 실제로 이어지는지를 본다. 판정을 먼저 하고 근거를 나중에
    # 실으면 두 계산이 서로 다른 것을 보게 되어, 근거 없는 값이 ANSWERABLE로 나갈 수 있다.
    evidence = _state_evidence(state, adapted)

    status, reason, missing, withheld = judge_summary_answerability(
        state,
        required_fields,
        available_evidence_ids={item.evidence_id for item in evidence},
    )

    # evidence가 실제로 가리키는 doc_id만 문서로 옮긴다. 값을 갱신한 정정공시가 있으면 그
    # 문서도 여기 포함된다 — root 하나로 좁히지 않는다. 순서는 evidence에 처음 나온 순서다.
    used_doc_ids = dict.fromkeys(item.doc_id for item in evidence)
    filings = tuple(
        filing_by_doc_id[doc_id] for doc_id in used_doc_ids if doc_id in filing_by_doc_id
    )

    return EventSummaryResult(
        plan=plan,
        status=status,
        reason=reason,
        event=root_event,
        state=state,
        links=conversion.links,
        evidence=evidence,
        conversion_gaps=conversion.conversion_gaps,
        root_candidate_doc_ids=(root.doc_id,),
        missing_fields=missing,
        withheld_fields=withheld,
        filings=filings,
        warnings=warnings,
    )


def find_unrepresentable_correction_change(
    chain: FilingChain,
    *,
    event_type: EventType,
    required_fields: Sequence[str],
) -> tuple[str, str] | None:
    """
    최신 상태를 만들기 전에, 이름을 붙이지 못한 정정 변경이 있는지 본다.

    왜 필요한가:
        event.diff는 EventHistory가 지원하는 canonical 필드(HISTORY_CANONICAL_FIELDS)에만
        이름을 붙인다. 그 집합은 Q06의 필수 요약 항목보다 좁아서, 투자대상·투자목적·투자
        시작일을 고친 정정이 field=None으로 남는다. resolve_latest_event_state는 이름 없는
        변경을 (조합형 정정이 아닌 한) 건너뛰므로, 그대로 두면 정정된 항목의 원공시 값이
        '최신 유효 상태'라는 이름으로 나간다. 그것이 정확히 이 구현이 막아야 하는 실패다.

        그 실패는 결과만 봐서는 드러나지 않는다. 값도 근거도 갖춰져 있고 상태도 ANSWERABLE
        이며, 다만 값이 낡았을 뿐이다.

    무엇을 통과시키는가:
        이름을 붙이지 못한 변경이라도, 그 원문 라벨이 공유 어휘(CORRECTION_FIELD_ALIASES)로
        해석되고 그 필드가 Q06의 필수 요약 항목이 아니면 지나간다. 질문이 묻지 않은 항목의
        정정은 이 요약의 값을 바꾸지 않는다.

        해석되지 않는 라벨은 통과시키지 않는다. 무엇이 바뀌었는지 모르는 상태에서 "요약과
        무관하다"고 말할 근거가 없다.

    공유 이벤트 계층이 이미 판정한 라벨은 여기서 막지 않는다:
        event.correction_fields의 LATEST_STATE_BLOCKING_CORRECTIONS는 "원문이 여러 항목을 한
        행에 묶어 두어 현재 필드 모델로 옮길 수 없다"를 유형별로 분류해 둔 정본이고,
        resolve_latest_event_state가 그 라벨을 만나면 UnsupportedCompositeCorrectionError를
        올린다. 그 판정을 이 handler가 앞질러 가로채면 두 가지를 잃는다.

            1. 분류(category=source_composite)가 결과에서 사라진다. "원문이 묶어 놓았다"와
               "이름을 못 붙였다"는 후속 조치가 다르다. 앞은 원문 구조를 표현할 모델이
               필요하고, 뒤는 대응표를 넓히면 풀린다.
            2. 공유 계층이 정책을 넓혀도 Q06만 옛 사유로 답한다. 정본이 두 곳이 된다.

        그래서 분류된 라벨은 건너뛰고 resolver가 올리는 예외에 맡긴다(_resolve_state가
        REASON_UNSUPPORTED_COMPOSITE_CORRECTION으로 옮긴다). 어느 쪽이든 최신 상태를 만들지
        않는 것은 같고, 달라지는 것은 어느 사유로 멈추는가뿐이다.

        여기에 그 라벨 목록을 다시 적지 않는다. 분류 함수를 부른다. 목록을 옮겨 적으면
        공유 계층이 항목을 추가할 때 이쪽만 뒤처진다.

    이미 이름이 붙은 변경(change.field)은 보지 않는다. 그쪽은 resolve_latest_event_state가
    해당 정정 문서의 값으로 상태를 실제로 갱신한다.

    첫 항목을 결정론적으로 돌려준다. 체인 순서가 같으면 같은 항목이 나온다. 한 체인에
    공유 계층이 분류한 변경과 이 함수만 아는 변경이 함께 있으면 뒤엣것을 돌려주며, 그때도
    실행은 멈춘다.
    """
    required = set(required_fields)

    for entry in chain.entries:
        if not entry.is_correction:
            continue

        source_doc_id = getattr(entry.filing, "doc_id", "") or entry.filing.receipt_no

        for change in entry.changes:
            if change.field is not None:
                continue

            if (
                classify_latest_state_blocking_correction(
                    change.raw_field,
                    event_type=event_type.value,
                )
                is not None
            ):
                continue

            canonical = resolve_correction_field(
                change.raw_field,
                event_type=event_type.value,
            )

            if canonical is not None and canonical not in required:
                continue

            return source_doc_id, change.raw_field

    return None


def _resolve_state(
    resolver: Any,
    **kwargs: Any,
) -> tuple[ResolvedEventState | None, BlockedInfo | None]:
    """
    상태 확정을 부르고 실패를 정지 지점으로 옮긴다.

    두 실패를 나눈다. 후속 조치가 다르기 때문이다.

        unsupported_composite_correction
            정정 원문이 여러 항목을 한 행에 묶어 놓아 현재 필드 모델로 안전하게 옮길 수
            없다. 어휘를 넓혀도 풀리지 않고, 원문 구조를 표현할 모델이 필요하다.

        unresolvable_event_state
            루트·체인·이력·문서가 서로 어긋난다. 파이프라인 쪽 문제다.

    둘 다 답변 가능성 판정이 아니다. 코퍼스에 답이 없다는 뜻이 아니라 확정하지 못하고 멈춘
    것이므로 BLOCKED로 남는다(agent.normalization의 ANSWERABILITY_BY_STOP 참조).
    """
    try:
        return resolver(**kwargs), None

    except UnsupportedCompositeCorrectionError as error:
        return None, BlockedInfo(
            stage=STAGE_CONTRACT_ADAPTATION,
            reason=REASON_UNSUPPORTED_COMPOSITE_CORRECTION,
            detail=(
                "여러 항목을 한 행에 묶은 정정이 있어 상태를 확정하지 못했습니다: "
                f"source_doc_id={error.source_doc_id!r}, category={error.category!r}. "
                "묶인 값을 항목별로 나눠 짐작하지 않습니다."
            ),
        )

    except ValueError as error:
        return None, BlockedInfo(
            stage=STAGE_CONTRACT_ADAPTATION,
            reason=REASON_UNRESOLVABLE_EVENT_STATE,
            detail=str(error),
        )


def judge_summary_answerability(
    state: ResolvedEventState,
    required_fields: Sequence[str],
    *,
    available_evidence_ids: Collection[str],
) -> tuple[
    AnswerabilityStatus,
    AnswerabilityReason | None,
    tuple[str, ...],
    tuple[str, ...],
]:
    """
    확정한 상태로 필수 요약 항목에 답할 수 있는지 판정한다.

    필수 항목만 본다:
        상태에는 문서에 있던 필드가 전부 담긴다. 그중에는 원문이 '-'로 비워 둔 항목도 있고
        (사외이사 불참 인원, 공시유보 사유), 그것들은 질문이 묻지 않은 것이다. 전부를 보면
        질문과 무관한 빈칸 하나가 답변 가능성을 낮춘다.

    빠진 것과 유보된 것을 나눈다:
        원문에 항목 자체가 없으면 근거를 확보하지 못한 것이고(PARTIAL_SOURCE_SUPPORT),
        항목은 있는데 값이 비어 있으면 원문이 공개하지 않은 것이다(WITHHELD_IN_SOURCE).
        둘은 사용자가 할 수 있는 다음 행동이 다르다. 유보는 질문을 좁혀도 검색을 개선해도
        값이 나오지 않는다(docs/agent_design/open_decisions.md D9).

    유보의 범위가 상태를 가른다:
        일부만 유보면 PARTIALLY_ANSWERABLE, 필수 항목 전체가 유보면 UNANSWERABLE이다.
        사유는 두 경우 모두 WITHHELD_IN_SOURCE이며, D9의 canonical semantics 표 A·B행이
        정확히 이 구분이다.

    둘이 섞이면 근거 미확보 쪽을 사유로 삼는다. 유보는 원문이 밝힌 사실이고 미확보는 이
    파이프라인이 확인하지 못한 것이라, 후자를 감추면 확인하지 못한 항목이 "원문이 공개하지
    않았다"로 잘못 보고된다.

    값만으로는 답한 것이 아니다:
        필수 항목이 '해결됐다'고 세려면 값과 함께 그 값이 어느 문서의 어느 행에서 왔는지가
        응답 안에 있어야 한다. ResolvedFieldValue.evidence_ids는 비어 있을 수 있고, 담겨
        있어도 그 식별자가 가리키는 Evidence가 이 응답에 실리지 않을 수 있다
        (_state_evidence는 source_doc_id로 찾지 못한 문서나 그 문서에 없는 식별자를
        건너뛴다). 값만 보고 세면 근거 없는 값이 ANSWERABLE로 나가고, 받는 쪽은 그 값을
        확인할 방법이 없는 채로 확인된 값과 구별하지 못한다.

        그래서 available_evidence_ids를 요구한다. 이 결과가 실제로 싣는 Evidence의 식별자
        집합이며, 호출자가 evidence를 만든 뒤에 넘긴다. 기본값을 두지 않는 이유는 기본값이
        곧 "근거를 보지 않는다"는 뜻이 되어 지금 막으려는 상태를 되살리기 때문이다.

    근거가 없는 값은 미확보로 센다:
        새 사유를 만들지 않는다. missing이 이미 "원문에서 근거를 확보하지 못했다"는 자리이고
        (PARTIAL_SOURCE_SUPPORT / INSUFFICIENT_EVIDENCE), 근거 없는 값은 정확히 그 상태다.
        유보(WITHHELD_IN_SOURCE)로 세지 않는다. 유보는 원문이 값을 공개하지 않았다는 원문
        쪽 사실이지만, 여기서 일어난 일은 이 파이프라인이 값을 근거에 잇지 못한 것이다.
    """
    available = set(available_evidence_ids)

    missing: list[str] = []
    withheld: list[str] = []
    resolved: list[str] = []

    for field_name in required_fields:
        resolved_field = state.fields.get(field_name)

        if resolved_field is None:
            missing.append(field_name)

        elif resolved_field.value is None or str(resolved_field.value).strip() == "":
            withheld.append(field_name)

        elif not resolved_field.evidence_ids or not available.issuperset(
            resolved_field.evidence_ids
        ):
            # 값은 있으나 그 값을 뒷받침할 근거가 응답에 없다. 값이 있다는 이유로 세지
            # 않는다. 가리키는 근거가 하나라도 응답에 없으면 통째로 미확보다. 남은
            # 식별자만으로 "일부는 확인됐다"고 말하려면 어느 근거가 어느 값을 뒷받침하는지
            # 필드 안에서 갈라야 하는데, ResolvedFieldValue에는 그 자리가 없다.
            missing.append(field_name)

        else:
            resolved.append(field_name)

    if not missing and not withheld:
        return AnswerabilityStatus.ANSWERABLE, None, (), ()

    if not resolved:
        reason = (
            AnswerabilityReason.WITHHELD_IN_SOURCE
            if not missing
            else AnswerabilityReason.INSUFFICIENT_EVIDENCE
        )

        return (
            AnswerabilityStatus.UNANSWERABLE,
            reason,
            tuple(missing),
            tuple(withheld),
        )

    reason = (
        AnswerabilityReason.PARTIAL_SOURCE_SUPPORT
        if missing
        else AnswerabilityReason.WITHHELD_IN_SOURCE
    )

    return (
        AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        reason,
        tuple(missing),
        tuple(withheld),
    )


def _state_evidence(
    state: ResolvedEventState,
    adapted: Mapping[str, EventAdapterResult],
) -> tuple[Evidence, ...]:
    """
    상태가 가리키는 필드 근거를 모은다.

    상태의 각 필드는 자기 값이 어느 문서의 어느 근거에서 왔는지 evidence_ids로 가리킨다.
    그 식별자가 가리키는 Evidence를 실어야 응답 안에서 값과 근거가 이어진다. 여기서 새
    Evidence를 만들지 않는다. 만들면 event.adapter가 만든 것과 두 벌이 된다.

    정정 행 근거(before/after)는 싣지 않는다. 이 결과가 말하는 것은 '지금 그 값이 무엇인가'
    이고, 그 값의 근거는 값을 담은 문서의 필드 행이다. 변경 이력의 근거는 Pattern E가
    싣는다.

    순서는 상태의 필드 순서다. 같은 근거가 두 필드에 걸릴 수 없으므로(_field_evidence_id가
    문서와 필드 이름으로 만든다) 중복 제거는 안전 장치다.
    """
    by_id: dict[str, Evidence] = {}

    for resolved_field in state.fields.values():
        result = adapted.get(resolved_field.source_doc_id)

        if result is None:
            continue

        wanted = set(resolved_field.evidence_ids)

        for item in result.evidence:
            if item.evidence_id in wanted:
                by_id.setdefault(item.evidence_id, item)

    return tuple(by_id.values())


def _state_evidence_for_document(
    adapted: Mapping[str, EventAdapterResult],
    doc_id: str,
) -> tuple[Evidence, ...]:
    """
    문서 1건의 필드 근거를 그대로 모은다.

    최신 상태를 확정하지 못한 경우에 쓴다. 값은 싣지 않지만 어느 공시를 읽었는지는 남겨야
    한다. 정정 행 근거는 여기서도 싣지 않는다(_state_evidence 참조).
    """
    result = adapted.get(doc_id)

    if result is None:
        return ()

    return tuple(item for item in result.evidence if item.field_name)


def handle_event_summary(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> AgentExecutionResult:
    """
    Router가 부르는 진입점.

    공통 Contract인 AgentExecutionResult를 돌려준다. Pattern 내부 표현이 필요한 호출자는
    build_event_summary_result를 직접 쓴다.
    """
    return normalize_event_summary_result(
        build_event_summary_result(
            plan,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )
    )
