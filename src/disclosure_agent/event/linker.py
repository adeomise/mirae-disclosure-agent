"""원본·정정공시 연결 모듈.

역할:
- 명시적 문서 메타데이터를 이용해 정정공시와 대상 원본을 연결한다.

현재 구현 범위:
- 정정공시 본문의 '정정관련 공시서류제출일'을 읽어 원공시 후보를 찾는다.
- 후보가 정확히 1건일 때만 연결한다.
- 원공시 1건과 그에 연결된 정정공시들을 공시일 시간순으로 잇는다.

연결 정보에 대한 메모:
    원문은 정정 대상을 접수번호로 지목하지 않는다. 날짜와 서류 종류만 준다. 따라서 연결은
    (기업, 원공시 제출일, 세부유형)으로 후보를 좁히는 방식이 된다. 실제 코퍼스에는 같은
    기업이 같은 날 같은 유형의 공시를 여러 건 낸 경우가 있어 후보가 1건으로 좁혀지지 않을
    수 있다.

    그때 접수번호가 작은 것, 먼저 나온 것 같은 규칙으로 고르지 않는다. 그렇게 고르면 다른
    계약의 이력을 그 계약의 이력이라고 답하게 되고, 틀렸다는 사실이 결과에 드러나지 않는다.

후보를 직접 조회하지 않는 이유:
    이 모듈은 manifest를 읽지 않고 후보를 인자로 받는다. 연결 판단과 코퍼스 접근을 섞으면
    코퍼스 없이는 연결 규칙을 시험할 수 없고, 이 모듈이 검색 계층에 의존하게 된다.
    후보를 모으는 일은 retrieval.metadata_filter의 책임이다.

입력:
- 정정공시의 FilingRef 형태 객체와 파싱된 문서
- 원공시 후보 목록

출력:
- LinkResult, FilingChain

TODO:
- 후속·해지 공시 방향의 연결(본문 '관련공시' 필드)
- 세부유형이 바뀌는 정정의 처리
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from disclosure_agent.common.dates import normalize_filing_date
from disclosure_agent.event.diff import FieldChange, diff_correction

# 연결 판정 결과.
#
# 값 이름은 docs/agent_design/evidence_and_answerability.md의 사유 코드 표기를 소문자로 옮긴
# 것이다. 응답 구조에 그대로 실을지는 팀원 3의 Contract 확정 사항이므로 여기서는 모듈 내부
# 판정값으로만 쓴다.
LINK_STATUS_LINKED = "linked"
LINK_STATUS_INCOMPLETE_CHAIN = "incomplete_chain"
LINK_STATUS_AMBIGUOUS_LINK = "ambiguous_link"

# 정정 블록에서 원공시 제출일을 읽지 못한 경우.
#
# 후보가 0건인 것(incomplete_chain)과 다르다. 전자는 원공시가 코퍼스 밖이라는 뜻이고
# 이쪽은 원문이 연결 정보를 주지 않았거나 파서가 읽지 못했다는 뜻이다. 후속 조치가 다르므로
# 같은 값으로 묶지 않는다.
LINK_STATUS_MISSING_LINK_FIELD = "missing_link_field"


class FilingLike(Protocol):
    """
    연결에 필요한 공시 식별 정보.

    retrieval.metadata_filter.FilingRef가 이 형태를 만족한다. 타입으로 직접 참조하지 않는
    것은 event가 retrieval에 의존하지 않게 하기 위해서다.
    """

    receipt_no: str
    receipt_date: str | None
    corp_name: str
    document_subtype: str
    is_correction: bool


@dataclass(frozen=True)
class LinkResult:
    """정정공시 1건에 대한 연결 판정."""

    status: str
    correction_receipt_no: str
    original_filing_date: str | None
    original: FilingLike | None = None
    candidates: tuple[FilingLike, ...] = field(default_factory=tuple)
    detail: str = ""

    @property
    def is_linked(self) -> bool:
        return self.status == LINK_STATUS_LINKED

    def to_dict(self) -> dict[str, Any]:
        """직렬화 가능한 요약. 후보는 접수번호로만 남긴다."""
        return {
            "status": self.status,
            "correction_receipt_no": self.correction_receipt_no,
            "original_filing_date": self.original_filing_date,
            "original_receipt_no": self.original.receipt_no if self.original else None,
            "candidate_receipt_nos": [
                candidate.receipt_no for candidate in self.candidates
            ],
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ChainEntry:
    """체인에 속한 공시 1건."""

    filing: FilingLike
    is_correction: bool
    link: LinkResult | None = None
    changes: tuple[FieldChange, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_no": self.filing.receipt_no,
            "receipt_date": normalize_filing_date(self.filing.receipt_date),
            "is_correction": self.is_correction,
            "link": self.link.to_dict() if self.link else None,
            "changes": [change.to_dict() for change in self.changes],
        }


@dataclass(frozen=True)
class FilingChain:
    """원공시 1건과 그에 연결된 정정공시들."""

    root_receipt_no: str
    entries: tuple[ChainEntry, ...] = field(default_factory=tuple)
    unresolved_links: tuple[LinkResult, ...] = field(default_factory=tuple)

    @property
    def is_complete(self) -> bool:
        """
        체인을 완결된 것으로 볼 수 있는가.

        루트가 후보 안에 있고, 이 체인에 속할 수 있었으나 연결하지 못한 정정이 없어야 한다.
        완결되지 않았다면 결과에 부분 이력임을 밝혀야 한다.
        """
        return bool(self.entries) and not self.unresolved_links

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_receipt_no": self.root_receipt_no,
            "is_complete": self.is_complete,
            "entries": [entry.to_dict() for entry in self.entries],
            "unresolved_links": [link.to_dict() for link in self.unresolved_links],
        }


def extract_original_filing_date(document: Mapping[str, Any] | None) -> str | None:
    """
    정정 블록에서 원공시 제출일을 읽어 YYYYMMDD로 맞춘다.

    정정 문서가 아니거나 값을 해석할 수 없으면 None을 돌려준다. 정정일자로 대신하지 않는다.
    정정일자는 이 문서가 제출된 날이지 대상 문서가 제출된 날이 아니다.
    """
    if not document:
        return None

    correction = document.get("correction")

    if not correction:
        return None

    return normalize_filing_date(correction.get("original_filing_date"))


def _sort_key(filing: FilingLike) -> tuple[str, str]:
    return (normalize_filing_date(filing.receipt_date) or "", str(filing.receipt_no))


def find_original_candidates(
    candidates: Iterable[FilingLike],
    *,
    corp_name: str,
    original_filing_date: str,
    document_subtype: str | None = None,
    exclude_receipt_no: str | None = None,
) -> list[FilingLike]:
    """
    (기업, 원공시 제출일, 세부유형)으로 원공시 후보를 좁힌다.

    정정본을 후보에서 빼지 않는다. 정정이 직전 정정을 가리키는 다단 체인이 실제로 존재하며,
    정정본을 제외하면 그런 체인이 끊어진다.

    자기 자신은 뺀다. 같은 날 제출된 정정이 자기를 가리키는 것으로 오인될 수 있다.
    """
    target_date = normalize_filing_date(original_filing_date)

    if target_date is None:
        return []

    matched: list[FilingLike] = []

    for candidate in candidates:
        if exclude_receipt_no and candidate.receipt_no == exclude_receipt_no:
            continue

        if candidate.corp_name != corp_name:
            continue

        if normalize_filing_date(candidate.receipt_date) != target_date:
            continue

        if document_subtype is not None and candidate.document_subtype != document_subtype:
            continue

        matched.append(candidate)

    return sorted(matched, key=_sort_key)


def link_correction(
    correction: FilingLike,
    *,
    document: Mapping[str, Any] | None,
    candidates: Iterable[FilingLike],
    match_document_subtype: bool = True,
) -> LinkResult:
    """
    정정공시 1건을 원공시에 연결한다.

    판정:
        원공시 제출일을 못 읽음 -> missing_link_field
        후보 0건               -> incomplete_chain   (원공시가 코퍼스 밖)
        후보 1건               -> linked
        후보 2건 이상          -> ambiguous_link     (본문 대조 필요)

    후보가 여럿일 때 접수번호나 문서 순서로 고르지 않는다.
    """
    original_filing_date = extract_original_filing_date(document)

    if original_filing_date is None:
        return LinkResult(
            status=LINK_STATUS_MISSING_LINK_FIELD,
            correction_receipt_no=correction.receipt_no,
            original_filing_date=None,
            detail=(
                "정정공시 본문에서 원공시 제출일을 읽지 못했습니다. "
                "정정일자로 대신하지 않습니다."
            ),
        )

    matched = find_original_candidates(
        candidates,
        corp_name=correction.corp_name,
        original_filing_date=original_filing_date,
        document_subtype=correction.document_subtype if match_document_subtype else None,
        exclude_receipt_no=correction.receipt_no,
    )

    if not matched:
        return LinkResult(
            status=LINK_STATUS_INCOMPLETE_CHAIN,
            correction_receipt_no=correction.receipt_no,
            original_filing_date=original_filing_date,
            detail=(
                f"{original_filing_date}에 제출된 원공시를 후보에서 찾지 못했습니다. "
                "코퍼스 수집 범위 밖일 수 있습니다."
            ),
        )

    if len(matched) > 1:
        return LinkResult(
            status=LINK_STATUS_AMBIGUOUS_LINK,
            correction_receipt_no=correction.receipt_no,
            original_filing_date=original_filing_date,
            candidates=tuple(matched),
            detail=(
                f"{original_filing_date}에 제출된 같은 유형의 공시가 {len(matched)}건이라 "
                "정정 대상을 1건으로 특정할 수 없습니다. 본문 대조가 필요합니다."
            ),
        )

    return LinkResult(
        status=LINK_STATUS_LINKED,
        correction_receipt_no=correction.receipt_no,
        original_filing_date=original_filing_date,
        original=matched[0],
        candidates=(matched[0],),
    )


def build_filing_chain(
    *,
    root_receipt_no: str,
    filings: Sequence[FilingLike],
    documents: Mapping[str, Mapping[str, Any]],
    event_type: str,
    match_document_subtype: bool = True,
) -> FilingChain:
    """
    원공시 1건에서 출발해 연결된 정정공시를 시간순으로 잇는다.

    정정이 직전 정정을 가리키는 다단 체인을 따라가기 위해, 더 붙는 문서가 없을 때까지
    반복해서 확장한다.

    연결하지 못한 정정 중 이 체인에 속할 수 있었던 것(후보에 체인 구성원이 들어 있는 경우)은
    unresolved_links에 남긴다. 조용히 빼면 이력이 완결된 것처럼 보인다.

    반대로 incomplete_chain과 missing_link_field는 어느 체인 것인지 알 수 없어 여기에
    귀속시키지 않는다. 대상이 후보 집합 밖이거나 원문이 대상을 밝히지 않은 경우다.
    그런 정정이 있다는 사실은 호출자가 후보 전체에 link_correction을 돌려 따로 드러내야 한다.

    입력 순서에 의존하지 않는다. 결과는 공시일과 접수번호 순으로 정렬한다.
    """
    by_receipt_no = {filing.receipt_no: filing for filing in filings}

    root = by_receipt_no.get(root_receipt_no)

    if root is None:
        return FilingChain(root_receipt_no=root_receipt_no)

    chain_members: dict[str, FilingLike] = {root_receipt_no: root}

    links: dict[str, LinkResult] = {}

    pending = [
        filing
        for filing in filings
        if filing.is_correction and filing.receipt_no != root_receipt_no
    ]

    # 다단 체인을 따라간다. 한 바퀴에 아무것도 붙지 않으면 멈춘다.
    while True:
        joined = False

        for correction in list(pending):
            result = link_correction(
                correction,
                document=documents.get(correction.receipt_no),
                candidates=filings,
                match_document_subtype=match_document_subtype,
            )

            links[correction.receipt_no] = result

            if result.is_linked and result.original.receipt_no in chain_members:
                chain_members[correction.receipt_no] = correction

                pending.remove(correction)

                joined = True

        if not joined:
            break

    unresolved = tuple(
        links[correction.receipt_no]
        for correction in sorted(pending, key=_sort_key)
        if _might_belong_to_chain(links[correction.receipt_no], chain_members)
    )

    entries = tuple(
        ChainEntry(
            filing=filing,
            is_correction=filing.is_correction,
            link=links.get(filing.receipt_no),
            changes=diff_correction(
                documents.get(filing.receipt_no),
                event_type=event_type,
            )
            if filing.is_correction
            else (),
        )
        for filing in sorted(chain_members.values(), key=_sort_key)
    )

    return FilingChain(
        root_receipt_no=root_receipt_no,
        entries=entries,
        unresolved_links=unresolved,
    )


def _might_belong_to_chain(
    result: LinkResult,
    chain_members: Mapping[str, FilingLike],
) -> bool:
    """
    연결에 실패한 정정이 이 체인에 속할 수 있었는지 본다.

    후보 중 하나라도 체인 구성원이면 이 체인의 미해결 항목으로 본다. 후보가 아예 없는
    경우(incomplete_chain)는 대상이 후보 집합 밖이므로 이 체인과 무관하다고 판단한다.
    """
    return any(
        candidate.receipt_no in chain_members for candidate in result.candidates
    )
