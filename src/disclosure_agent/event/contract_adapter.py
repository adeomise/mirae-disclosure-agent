"""이벤트 체인 결과를 Contract v0.1 타입으로 옮기는 변환 계층.

역할:
- linker/diff가 만든 내부 결과(LinkResult, FilingChain, 자체 FieldChange)를
  common.schemas의 RawReference / EventLink / FieldChange / EventHistory로 바꾼다.
- 그 과정에서 Contract에 실을 자리가 없는 정보를 조용히 버리지 않고 기록한다.

왜 별도 계층인가:
    linker/diff는 실제 코퍼스 체인에서 이미 검증된 판정 로직이다. Contract 타입에 맞추려고
    그 로직을 고치면 검증된 동작이 흔들린다. 변환만 따로 두면 Contract가 개정돼도 이 파일
    하나만 바뀐다.

conversion_gaps에 대하여:
    Contract v0.1은 원문 필드명, ambiguous 후보 목록, 변경의 출처 문서를 담을 자리가 없다.
    이들을 그냥 버리면 "정보가 없다"와 "형식에 못 담았다"가 구분되지 않는다.

    그래서 변환 결과를 Contract 객체 하나가 아니라 (객체, conversion_gaps) 쌍으로 돌려준다.
    gap에는 그 항목을 되살리는 데 필요한 값을 전부 담아, Contract가 보완되면 다시 연결할 수
    있게 한다.

    gap은 adapter 내부 결과이며 Contract 객체에 섞어 넣지 않는다. 섞으면 Contract를 쓰는 쪽이
    규격 밖 필드에 의존하게 된다.

임시 호환 gap과 영구 한계의 구분:
    planned_contract_field가 있는 gap은 다음 Contract 개정에서 사라질 임시 호환 항목이다.
    영구 정책으로 굳히면 안 된다.

입력:
- event.linker의 LinkResult / FilingChain
- event.parser의 ParsedDocument dict

출력:
- Contract v0.1 객체와 conversion_gaps

TODO:
- RawReference.related_disclosures (본문 '관련공시' 필드 구조화)
- Contract 개정 후 임시 gap 제거
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from disclosure_agent.common.enums import (
    EventLinkRelation,
    EventLinkResolutionStatus,
    EventType,
)
from disclosure_agent.common.schemas import (
    EventHistory,
    EventLink,
    FieldChange,
    RawReference,
)
from disclosure_agent.event.diff import FieldChange as InternalFieldChange
from disclosure_agent.event.linker import (
    LINK_STATUS_AMBIGUOUS_LINK,
    LINK_STATUS_INCOMPLETE_CHAIN,
    LINK_STATUS_LINKED,
    LINK_STATUS_MISSING_LINK_FIELD,
    FilingChain,
    FilingLike,
    LinkResult,
)

# 이 adapter가 맞추는 Contract 버전. 개정되면 아래 임시 gap들을 다시 본다.
TARGET_CONTRACT_VERSION = "0.1"

# --------------------------------------------------------------------------
# 손실 유형
# --------------------------------------------------------------------------

# Contract v0.1에 자리가 없어 못 싣는 것. 다음 개정에서 흡수될 예정이다.
GAP_RAW_FIELD_NAME = "raw_field_name"
GAP_AMBIGUOUS_CANDIDATES = "ambiguous_candidates"
GAP_CHANGE_SOURCE_DOCUMENT = "change_source_document"

# 팀 결정이 있어야 변환할 수 있는 것. Contract 구조는 이미 있다.
GAP_PENDING_RESOLUTION_STATUS = "pending_resolution_status"
GAP_PENDING_EVENT_ID = "pending_event_id"

# 위 어느 쪽도 아닌 것.
GAP_UNMAPPED_FIELD_NAME = "unmapped_field_name"
GAP_UNRESOLVED_LINKS = "unresolved_links"

# 다음 Contract 개정에서 각 gap을 흡수할 예정인 필드.
# 이 표에 있는 gap은 임시 호환 항목이며 영구 정책이 아니다.
PLANNED_CONTRACT_FIELDS: dict[str, str] = {
    GAP_RAW_FIELD_NAME: "FieldChange.raw_field",
    GAP_AMBIGUOUS_CANDIDATES: "EventLink.candidate_doc_ids",
    GAP_CHANGE_SOURCE_DOCUMENT: "FieldChange.before_evidence_id / after_evidence_id",
}


@dataclass(frozen=True)
class ContractGap:
    """Contract 객체에 실을 수 없어 따로 남긴 정보."""

    kind: str
    subject: str
    dropped: dict[str, Any] = field(default_factory=dict)
    detail: str = ""
    pending_decision: bool = False

    @property
    def planned_contract_field(self) -> str | None:
        """다음 Contract 개정에서 이 정보를 담을 예정인 필드. 없으면 None."""
        return PLANNED_CONTRACT_FIELDS.get(self.kind)

    @property
    def is_temporary(self) -> bool:
        """
        Contract 개정만 되면 사라질 항목인가.

        True인 gap을 근거로 영구 정책을 만들면 안 된다.
        """
        return self.planned_contract_field is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "dropped": dict(self.dropped),
            "detail": self.detail,
            "pending_decision": self.pending_decision,
            "planned_contract_field": self.planned_contract_field,
            "is_temporary": self.is_temporary,
            "contract_version": TARGET_CONTRACT_VERSION,
        }


@dataclass(frozen=True)
class EventLinkConversion:
    """EventLink 변환 결과. 변환할 수 없으면 link가 None이다."""

    link: EventLink | None = None
    conversion_gaps: tuple[ContractGap, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class EventHistoryConversion:
    """EventHistory 변환 결과."""

    history: EventHistory | None = None
    links: tuple[EventLink, ...] = field(default_factory=tuple)
    conversion_gaps: tuple[ContractGap, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------
# 상태 대응
# --------------------------------------------------------------------------

# 그대로 옮길 수 있는 것만 둔다.
#
# incomplete_chain과 missing_link_field는 넣지 않았다. Contract는 not_found와 out_of_corpus를
# 나누는데, 그 둘을 가르려면 코퍼스 수집 기간 경계를 알아야 하고 그 정보가 어디서 오는지 아직
# 정의돼 있지 않다. adapter가 임의로 한쪽을 고르면 '찾지 못했다'와 '보유 범위 밖이다'가 섞인다.
RESOLUTION_STATUS_BY_LINK_STATUS: dict[str, EventLinkResolutionStatus] = {
    LINK_STATUS_LINKED: EventLinkResolutionStatus.RESOLVED,
    LINK_STATUS_AMBIGUOUS_LINK: EventLinkResolutionStatus.AMBIGUOUS,
}

UNDECIDED_LINK_STATUSES = (LINK_STATUS_INCOMPLETE_CHAIN, LINK_STATUS_MISSING_LINK_FIELD)


def _doc_id(filing: FilingLike) -> str:
    """
    Contract는 문서를 doc_id로 가리킨다.

    FilingRef는 doc_id를 가지고 있다. 없는 객체를 위해 접수번호로 물러서지만 그 경우에도
    식별자를 지어내지는 않는다.
    """
    return getattr(filing, "doc_id", "") or filing.receipt_no


def build_event_id(event_type: EventType, root_doc_id: str) -> str:
    """
    체인 하나를 가리키는 결정론적 event_id.

    event_type과 루트 원공시 doc_id로만 만든다. 같은 체인이면 언제 만들어도 같은 값이 나오고,
    다른 계약의 체인과 겹치지 않는다.
    """
    return f"event_{event_type.value}_{root_doc_id}"


def build_link_id(source_doc_id: str, relation: EventLinkRelation) -> str:
    """
    링크 하나를 가리키는 결정론적 link_id.

    Contract가 link_id를 필수로 요구하지만 발급 규칙은 정해진 바 없다. 한 문서가 같은 관계의
    링크를 둘 이상 갖지 않는다는 전제에서 만든 임시 규칙이며 팀 확인이 필요하다.
    """
    return f"link_{source_doc_id}_{relation.value}"


# --------------------------------------------------------------------------
# RawReference
# --------------------------------------------------------------------------


def to_raw_reference(document: Mapping[str, Any] | None) -> RawReference:
    """
    파싱된 문서에서 연결 근거가 된 원문 표기를 옮긴다.

    날짜를 정규화하지 않는다. Contract의 RawReference는 '연결하기 전에 원문에서 보존한 참조'
    이므로 원문이 쓴 표기('2024-08-30')를 그대로 남긴다. 정규화된 형태는 linker가 비교에만
    쓰고 여기 담지 않는다.
    """
    if not document:
        return RawReference()

    correction = document.get("correction") or {}

    return RawReference(
        document_title=document.get("title"),
        original_filing_date=correction.get("original_filing_date"),
        correction_filing_date=correction.get("correction_date"),
        correction_reason=correction.get("reason"),
        # 본문 '관련공시' 필드는 아직 구조화하지 않았다. 빈 목록은 '없음'이 아니라 '미구현'이다.
        related_disclosures=[],
    )


# --------------------------------------------------------------------------
# EventLink
# --------------------------------------------------------------------------


def to_event_link(
    result: LinkResult,
    *,
    correction: FilingLike,
    document: Mapping[str, Any] | None = None,
    relation: EventLinkRelation = EventLinkRelation.CORRECTION_OF,
    undecided_status: EventLinkResolutionStatus | None = None,
) -> EventLinkConversion:
    """
    연결 판정 하나를 Contract EventLink로 옮긴다.

    undecided_status:
        incomplete_chain / missing_link_field를 어느 Contract 상태로 보낼지 정한 호출자만
        넘긴다. 넘기지 않으면 변환하지 않고(link=None) 결정 대기로 남긴다.
        adapter가 not_found와 out_of_corpus 중 하나를 임의로 고르지 않는다.
    """
    source_doc_id = _doc_id(correction)

    gaps: list[ContractGap] = []

    status = RESOLUTION_STATUS_BY_LINK_STATUS.get(result.status)

    if status is None:
        if undecided_status is None:
            return EventLinkConversion(
                link=None,
                conversion_gaps=(
                    ContractGap(
                        kind=GAP_PENDING_RESOLUTION_STATUS,
                        subject=source_doc_id,
                        dropped={
                            "link_status": result.status,
                            "original_filing_date": result.original_filing_date,
                            "detail": result.detail,
                        },
                        detail=(
                            f"'{result.status}'를 Contract 상태로 보낼지 정해지지 않았습니다. "
                            "not_found와 out_of_corpus를 가르려면 코퍼스 수집 기간 경계가 "
                            "필요합니다. 결정한 뒤 undecided_status로 넘기십시오."
                        ),
                        pending_decision=True,
                    ),
                ),
            )

        status = undecided_status

    if result.status == LINK_STATUS_AMBIGUOUS_LINK and result.candidates:
        gaps.append(
            ContractGap(
                kind=GAP_AMBIGUOUS_CANDIDATES,
                subject=source_doc_id,
                dropped={
                    "candidate_doc_ids": [
                        _doc_id(candidate) for candidate in result.candidates
                    ],
                    "candidate_receipt_nos": [
                        candidate.receipt_no for candidate in result.candidates
                    ],
                },
                detail=(
                    f"후보 {len(result.candidates)}건을 EventLink에 담을 자리가 없습니다. "
                    "후보를 병기하지 못하면 사용자가 어느 문서들 사이에서 모호한지 알 수 "
                    "없습니다."
                ),
            )
        )

    link = EventLink(
        link_id=build_link_id(source_doc_id, relation),
        source_doc_id=source_doc_id,
        relation=relation,
        raw_reference=to_raw_reference(document),
        resolved_doc_id=_doc_id(result.original) if result.original else None,
        resolution_status=status,
    )

    return EventLinkConversion(link=link, conversion_gaps=tuple(gaps))


# --------------------------------------------------------------------------
# FieldChange
# --------------------------------------------------------------------------


def to_field_change(
    change: InternalFieldChange,
    *,
    source_doc_id: str,
) -> tuple[FieldChange | None, tuple[ContractGap, ...]]:
    """
    변경 하나를 Contract FieldChange로 옮긴다.

    field_name에는 대응표가 확정한 canonical 이름만 넣는다. 매핑하지 못한 원문 라벨을 억지로
    넣지 않는다. 넣으면 canonical 이름과 한글 원문 라벨이 한 필드에 섞여, 받는 쪽이 어느 것이
    확정된 개념인지 구분할 수 없다.

    매핑하지 못한 변경은 변환에서 빼고 원문 값을 전부 gap에 남긴다. Contract가 보완되거나
    대응표가 넓어지면 그 기록만으로 되살릴 수 있다.
    """
    if change.field is None:
        return None, (
            ContractGap(
                kind=GAP_UNMAPPED_FIELD_NAME,
                subject=change.raw_field,
                dropped={
                    "raw_field": change.raw_field,
                    "before": change.before,
                    "after": change.after,
                    "source_doc_id": source_doc_id,
                },
                detail=(
                    f"'{change.raw_field}'에 대응하는 canonical 필드 이름이 없어 "
                    "Contract FieldChange로 옮기지 않았습니다. 원문 라벨을 field_name에 "
                    "넣으면 확정된 개념과 섞입니다. event.diff의 대응표를 넓히면 해소됩니다."
                ),
            ),
        )

    converted = FieldChange(
        field_name=change.field,
        before=change.before,
        after=change.after,
        # diff는 값을 해석하지 않으므로 단위를 알지 못한다. 손실이 아니라 미보유다.
        unit=None,
    )

    gaps = (
        ContractGap(
            kind=GAP_RAW_FIELD_NAME,
            subject=change.field,
            dropped={"raw_field": change.raw_field},
            detail=(
                f"원문 라벨 '{change.raw_field}'을 담을 자리가 없습니다. "
                "원문 대조가 끊깁니다."
            ),
        ),
        ContractGap(
            kind=GAP_CHANGE_SOURCE_DOCUMENT,
            subject=change.field,
            dropped={"source_doc_id": source_doc_id},
            detail=(
                "이 변경이 어느 문서에서 나왔는지 담을 자리가 없습니다. "
                "EventHistory.changes가 평탄한 목록이라 문서별 귀속이 사라집니다."
            ),
        ),
    )

    return converted, gaps


# --------------------------------------------------------------------------
# EventHistory
# --------------------------------------------------------------------------


def to_event_history(
    chain: FilingChain,
    *,
    documents: Mapping[str, Mapping[str, Any]] | None = None,
    filings: Sequence[FilingLike] | None = None,
    event_type: EventType = EventType.SUPPLY_CONTRACT,
    event_id: str | None = None,
    root_is_ambiguous: bool = False,
    relation: EventLinkRelation = EventLinkRelation.CORRECTION_OF,
    undecided_status: EventLinkResolutionStatus | None = None,
) -> EventHistoryConversion:
    """
    체인 하나를 Contract EventHistory로 옮긴다.

    event_id는 event_type과 루트 원공시 doc_id로 결정론적으로 만든다. 다만 루트를 확정하지
    못하면(체인이 비었거나 root_is_ambiguous가 True) canonical event_id를 만들지 않는다.
    근거 없이 만든 event_id는 서로 다른 계약의 이력을 같은 event로 묶어 버린다.

    연결 판정은 EventLink로 함께 돌려준다. EventHistory에는 링크를 담을 자리가 없다.

    filings에는 체인을 만들 때 쓴 후보 전체를 넘긴다. 미해결 정정은 체인에 들어가지 못해
    doc_id를 알 방법이 없는데, 후보 목록이 있으면 그 문서의 doc_id를 정확히 찾을 수 있다.
    """
    documents = documents or {}

    gaps: list[ContractGap] = []

    links: list[EventLink] = []

    root_doc_id = _resolve_root_doc_id(chain)

    if root_doc_id is None or root_is_ambiguous:
        return EventHistoryConversion(
            conversion_gaps=(
                ContractGap(
                    kind=GAP_PENDING_EVENT_ID,
                    subject=chain.root_receipt_no,
                    dropped={
                        "root_receipt_no": chain.root_receipt_no,
                        "entry_receipt_nos": [
                            entry.filing.receipt_no for entry in chain.entries
                        ],
                        "root_is_ambiguous": root_is_ambiguous,
                    },
                    detail=(
                        "루트 원공시를 확정하지 못해 canonical event_id를 만들지 않았습니다. "
                        "근거 없이 만든 event_id는 다른 계약의 이력을 같은 event로 묶습니다."
                    ),
                    pending_decision=True,
                ),
            ),
        )

    document_ids: list[str] = []

    changes: list[FieldChange] = []

    for entry in chain.entries:
        entry_doc_id = _doc_id(entry.filing)

        document_ids.append(entry_doc_id)

        if entry.link is not None:
            conversion = to_event_link(
                entry.link,
                correction=entry.filing,
                document=documents.get(entry.filing.receipt_no),
                relation=relation,
                undecided_status=undecided_status,
            )

            if conversion.link is not None:
                links.append(conversion.link)

            gaps.extend(conversion.conversion_gaps)

        for change in entry.changes:
            converted, change_gaps = to_field_change(change, source_doc_id=entry_doc_id)

            if converted is not None:
                changes.append(converted)

            gaps.extend(change_gaps)

    for unresolved in chain.unresolved_links:
        conversion = to_event_link(
            unresolved,
            correction=_find_filing(chain, unresolved.correction_receipt_no, filings),
            document=documents.get(unresolved.correction_receipt_no),
            relation=relation,
            undecided_status=undecided_status,
        )

        if conversion.link is not None:
            links.append(conversion.link)

        gaps.extend(conversion.conversion_gaps)

    if chain.unresolved_links:
        gaps.append(
            ContractGap(
                kind=GAP_UNRESOLVED_LINKS,
                subject=root_doc_id,
                dropped={
                    "unresolved_correction_receipt_nos": [
                        link.correction_receipt_no for link in chain.unresolved_links
                    ],
                },
                detail=(
                    f"이 체인에 속할 수 있었으나 연결하지 못한 정정 "
                    f"{len(chain.unresolved_links)}건을 EventHistory에 담을 자리가 없습니다. "
                    "chain_complete=False가 사실만 전할 뿐 무엇이 미해결인지는 남지 않습니다."
                ),
            )
        )

    history = EventHistory(
        event_id=event_id or build_event_id(event_type, root_doc_id),
        document_ids=document_ids,
        changes=changes,
        chain_complete=chain.is_complete,
    )

    return EventHistoryConversion(
        history=history,
        links=tuple(links),
        conversion_gaps=tuple(gaps),
    )


def _resolve_root_doc_id(chain: FilingChain) -> str | None:
    """체인의 루트 doc_id. 루트가 후보 안에 없으면 None."""
    for entry in chain.entries:
        if entry.filing.receipt_no == chain.root_receipt_no:
            return _doc_id(entry.filing)

    return None


def _find_filing(
    chain: FilingChain,
    receipt_no: str,
    filings: Sequence[FilingLike] | None = None,
) -> FilingLike:
    """
    접수번호에 해당하는 공시를 찾는다.

    미해결 정정은 체인에 들어가지 못하므로 후보 목록(filings)에서 먼저 찾는다. 어디에서도
    찾지 못하면 접수번호만 아는 최소 객체를 돌려준다. 그 경우 doc_id를 지어내지 않는다.
    """
    for filing in filings or ():
        if filing.receipt_no == receipt_no:
            return filing

    for entry in chain.entries:
        if entry.filing.receipt_no == receipt_no:
            return entry.filing

    for link in chain.unresolved_links:
        for candidate in link.candidates:
            if candidate.receipt_no == receipt_no:
                return candidate

    return _MinimalFiling(receipt_no=receipt_no)


@dataclass(frozen=True)
class _MinimalFiling:
    """접수번호만 아는 공시. 체인 밖 문서를 가리킬 때 쓴다."""

    receipt_no: str
    receipt_date: str | None = None
    doc_id: str = ""
    corp_name: str = ""
    document_subtype: str = ""
    is_correction: bool = True


# --------------------------------------------------------------------------
# 조회 도우미
# --------------------------------------------------------------------------


def temporary_gaps(gaps: tuple[ContractGap, ...]) -> tuple[ContractGap, ...]:
    """다음 Contract 개정에서 사라질 gap만 고른다."""
    return tuple(gap for gap in gaps if gap.is_temporary)


def pending_decision_gaps(gaps: tuple[ContractGap, ...]) -> tuple[ContractGap, ...]:
    """팀 결정이 있어야 진행되는 gap만 고른다."""
    return tuple(gap for gap in gaps if gap.pending_decision)


def gap_kinds(gaps: tuple[ContractGap, ...]) -> tuple[str, ...]:
    """등장한 gap 종류를 중복 없이 돌려준다."""
    kinds: dict[str, None] = {}

    for gap in gaps:
        kinds.setdefault(gap.kind, None)

    return tuple(kinds)
