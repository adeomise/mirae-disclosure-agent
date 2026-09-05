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
- 자기참조 원공시 제출일을 적은 정정의 대상 복구. 지금은 그런 정정을 미해결로 남겨
  chain_complete=False로 만들 뿐(_might_belong_to_chain), 대상을 되찾지는 않는다.
  실측 코퍼스에 자기참조 정정이 있고(엘에스일렉트릭 20240813800252 등), 정정 시점에 이미
  원공시가 여럿이었던 경우에는 어느 체인에도 남지 않는다.
- 루트에 매이지 않는 미해결 표현. FilingChain은 루트 단위여서 '이 그룹 어딘가에 붙지 못한
  정정이 있다'를 담을 자리가 없다.
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

    연결하지 못한 정정 중 이 체인에 속할 수 있었던 것은 unresolved_links에 남긴다. 조용히
    빼면 이력이 완결된 것처럼 보인다. 무엇을 남기는지는 _might_belong_to_chain이 정한다.

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

    roots = tuple(filing for filing in filings if not filing.is_correction)

    unresolved = tuple(
        links[correction.receipt_no]
        for correction in sorted(pending, key=_sort_key)
        if _might_belong_to_chain(
            links[correction.receipt_no],
            chain_members,
            correction=correction,
            roots=roots,
        )
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


def _temporally_plausible_roots(
    roots: Sequence[FilingLike],
    correction: FilingLike,
) -> list[FilingLike]:
    """
    이 정정이 정정했을 수 있는 원공시만 남긴다.

    후보 집합은 (기업, 공시 세부유형)으로 이미 좁혀져 있다. 거기서 더 좁히는 기준은 시간
    하나다. 정정보다 나중에 제출된 원공시는 정정 시점에 존재하지 않았으므로 그 정정의
    대상일 수 없다.

    전체 집합의 원공시 개수를 세면 안 되는 이유:
        나중에 나온 원공시까지 세면 "이 정정이 어느 이력의 것인지 모른다"는 판정이 실제보다
        쉽게 나온다. 정정 시점에 이력이 하나뿐이었는데도 그 뒤에 다른 계약이 하나 더 공시됐다는
        이유로 미해결 정정이 어느 체인에도 남지 않고 사라진다. 그러면 원래 붙었어야 할 체인이
        완결로 답해지며, 이 모듈이 막으려는 바로 그 오류가 다시 난다.

    같은 날 제출을 가능으로 보는 이유:
        receipt_date는 날짜이지 시각이 아니다. 그리고 find_original_candidates가 원공시를
        찾는 조건 자체가 '같은 날짜'다. 즉 같은 날 낸 정정은 예외가 아니라 정상 연결 경로이며,
        실측 코퍼스에서 정상 연결된 정정 253건 중 6건이 원공시와 같은 날 제출됐다.
        같은 날을 빼면 실재하는 원공시가 후보에서 빠져 가짜 판정이 나온다.

    날짜를 모르면 빼지 않는다. 모르는 것을 근거로 지우지 않는다.
    """
    correction_date = normalize_filing_date(correction.receipt_date)

    if correction_date is None:
        return list(roots)

    plausible: list[FilingLike] = []

    for root in roots:
        root_date = normalize_filing_date(root.receipt_date)

        if root_date is None or root_date <= correction_date:
            plausible.append(root)

    return plausible


def _link_field_is_uninformative(
    result: LinkResult,
    correction: FilingLike,
) -> bool:
    """
    이 정정의 연결 정보가 대상을 가릴 근거를 전혀 주지 못했는가.

    두 경우다.

        missing_link_field   원문이 원공시 제출일을 밝히지 않았거나 파서가 읽지 못했다.
        자기참조 날짜         읽어낸 원공시 제출일이 이 정정 자신의 접수일과 같다. 정정은
                             자기 자신을 정정할 수 없으므로 그 값은 대상을 가리키지 못한다.
                             실측 코퍼스에 실재한다(예: 엘에스일렉트릭 20240813800252가
                             original_filing_date로 자기 접수일 2024-08-13을 적었다).

    날짜가 자기참조가 아닌 incomplete_chain은 여기 들어오지 않는다. 그때 날짜는 대상이 언제
    제출됐는지를 실제로 말해 주고, 그 날짜에 해당하는 후보가 없다는 것은 대상이 후보 집합
    밖이라는 근거가 된다. 근거가 있는 배제와 근거가 없는 배제를 같은 값으로 묶지 않는다.
    """
    if result.status == LINK_STATUS_MISSING_LINK_FIELD:
        return True

    if result.status != LINK_STATUS_INCOMPLETE_CHAIN:
        return False

    correction_date = normalize_filing_date(correction.receipt_date)

    if correction_date is None:
        return False

    return normalize_filing_date(result.original_filing_date) == correction_date


def _might_belong_to_chain(
    result: LinkResult,
    chain_members: Mapping[str, FilingLike],
    *,
    correction: FilingLike,
    roots: Sequence[FilingLike],
) -> bool:
    """
    연결에 실패한 정정이 이 체인에 속할 수 있었는지 본다.

    두 갈래로 남긴다.

    1. 후보 중 하나가 체인 구성원인 경우(주로 ambiguous_link). 대상 후보 안에 이 체인의
       문서가 들어 있으므로 이 체인의 미해결 항목이다.

    2. 연결 정보 자체가 무의미하고(_link_field_is_uninformative), 이 정정이 정정했을 수
       있는 원공시(_temporally_plausible_roots)가 이 체인의 루트 하나뿐인 경우.

    2번을 남기는 이유:
        후보가 비어 있다는 것을 '이 체인과 무관하다'는 증거로 쓰던 자리다. 그것은 대상 날짜를
        읽어냈을 때만 성립하는 추론이다. 날짜를 못 읽었거나 읽은 값이 자기 자신을 가리키면
        어느 체인 것인지 가릴 근거가 하나도 없고, 근거 없이 무관하다고 정하면 그 정정은
        결과에서 사라진다. 그러면 실제로는 뒤에 변경이 있었는데 chain_complete=True가 되어
        "이후 변경된 내용은 확인되지 않았습니다"가 나간다. 형식이 온전한 채로 틀린 답이다.

        어디에 붙는지 모를 때는 붙이지 못했다는 사실을 남긴다. 여기서 EventLink를 지어내지
        않는다. 대상을 되찾는 일은 별도 과제다.

    개수를 시간으로 세는 이유:
        후보 집합 전체의 원공시를 세면, 정정보다 나중에 나온 원공시가 경쟁 후보로 잡힌다.
        정정 시점에는 이력이 하나뿐이었는데 그 뒤에 다른 계약이 공시됐다는 이유만으로 판정이
        '모른다'로 바뀌고, 그 정정은 원래 붙었어야 할 체인에서도 사라진다. 존재하지도 않던
        문서를 근거로 아는 것을 모른다고 하는 셈이다.

    plausible 루트가 여럿이면 2번을 적용하지 않는 이유:
        같은 기업의 같은 유형 공시라도 계약·투자마다 이력이 따로 있다. 정정 시점에 이미
        원공시가 여럿이었다면 이 정정이 그중 어느 이력의 것인지 정할 수 없다. 그 상태로
        선택된 루트에 귀속시키면 무관한 정정 하나가 멀쩡한 체인 전부를 미완결로 만든다.
        실측 코퍼스에서 그런 정정 하나 앞에 원공시가 16건까지 있다. 하나를 고르지 않고,
        전부에 붙이지도 않는다.

        그래서 plausible 루트가 여럿인 경우 이 정정은 어느 체인에도 남지 않는다. 알려진
        한계다. FilingChain은 루트에 매인 구조라 '그룹 어딘가에 미해결이 있다'를 담을 자리가
        없다. 그 사실은 호출자가 후보 전체에 link_correction을 돌려 따로 드러내야 한다.

    선택된 루트가 정정보다 나중에 나온 경우:
        그 루트는 plausible 집합에 들어가지 않으므로 이 체인은 오염되지 않는다. 루트가 나오기
        전에 제출된 문서는 그 루트를 정정한 것일 수 없다.
    """
    if any(candidate.receipt_no in chain_members for candidate in result.candidates):
        return True

    if not _link_field_is_uninformative(result, correction):
        return False

    plausible = _temporally_plausible_roots(roots, correction)

    if len(plausible) != 1:
        return False

    return plausible[0].receipt_no in chain_members
