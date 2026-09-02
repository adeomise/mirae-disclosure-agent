"""거래소공시 후보를 1건으로 확정하는 선택 helper.

역할:
- 기업·기간·계약 상대방을 받아 공급계약 공시 후보를 모으고, 그 중 하나로 확정하거나
  확정하지 못했음을 밝힌다.

왜 별도 모듈인가:
    agent.period_retrieval이 정기공시에 대해 하는 일과 같은 자리다. handler 안에 두면
    이후 다른 Pattern이 같은 선택 규칙을 쓸 때 그 handler에 의존하게 된다.

    나누는 또 한 가지 이유는 검증이다. 이 모듈은 QueryPlan을 모르고 primitive만 받으므로,
    질문 해석을 거치지 않고 문서 선택 규칙만 따로 시험할 수 있다.

QueryPlan을 받지 않는다:
    질문 해석 결과를 검색 조건으로 옮기는 일은 handler가 한다. 특히 계약 상대방은 handler가
    읽어 넘긴다. 여기서 질문 문자열을 다시 읽으면 같은 질문에 대한 해석이 두 벌이 되고,
    갈라진 뒤에는 어느 쪽이 정본인지 알 수 없다.

문서를 1건으로 확정하는 방법(C3 Document Disambiguation):
    메타데이터만으로는 좁혀지지 않는다. 같은 기업이 같은 달에 낸 공급계약 공시가 실제로 여러
    건 있다. 그래서 후보를 전부 열어 본문의 계약 상대방을 읽고, 질문이 지목한 상대방과
    일치하는 문서만 남긴다.

    남은 것이 1건이 아니면 고르지 않는다. 0건이면 그 계약이 후보 안에 없는 것이고, 2건
    이상이면 질문이 어느 계약을 가리키는지 정할 근거가 없다. 어느 쪽이든 하나를 고르면 다른
    계약의 금액을 그 계약의 금액이라고 답하게 된다.

    접수번호가 크다거나 공시일이 늦다는 이유로 고르지 않는다. 정렬은 순서를 고정할 뿐이고
    우선순위가 아니다.

정정공시를 후보로 세지 않는 이유:
    이 helper가 찾는 것은 계약을 처음 알린 원공시다. 정정공시는 같은 계약의 다른 판본이므로
    독립 후보로 세면 원공시 하나짜리 질문이 후보 2건으로 보여 ambiguous가 된다.
    handlers.event_history가 원공시 후보를 모을 때 쓰는 기준과 같다(include_corrections=False).

    판본 정책(D5)이 정해지면 정정본을 어떻게 반영할지는 그때 정한다. 지금 한쪽을 고르면
    그 결정이 코드에 숨은 채로 굳는다.

이 slice의 범위:
- 문서 선택까지만 한다. Fact 변환(event.adapter.adapt_contract_facts) 호출, QueryPlan 연결,
  Answer Builder의 BlockedInfo 변환은 아직 하지 않는다.

TODO:
- 계약 상대방 외의 본문 조건(계약명 등)으로 좁히는 경로
- 판본 정책(D5) 확정 후 원공시/정정공시 우선순위
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from disclosure_agent.agent.query_analyzer import EVENT_SUBTYPE_SUPPLY_CONTRACT
from disclosure_agent.event.extractor import extract_event
from disclosure_agent.event.parser import parse_exchange_document
from disclosure_agent.retrieval.metadata_filter import (
    FilingRef,
    list_event_filings,
    normalize_company_name,
)

__all__ = [
    "COUNTERPARTY_ALIAS_GROUPS",
    "ContractCandidate",
    "ContractCandidateReadError",
    "ContractSelection",
    "ContractSelectionStatus",
    "counterparty_key",
    "filing_date_window",
    "select_by_counterparty",
    "select_contract_filing",
]

# 본문에서 계약 상대방을 담는 canonical field. event.extractor의 어휘다.
COUNTERPARTY_FIELD = "counterparty"

# 같은 상대방을 가리키는 표기 묶음.
#
# 왜 필요한가:
#     질문과 공시 본문이 같은 회사를 다른 문자 체계로 적는 경우가 있다. 대표 Q02가 그렇다.
#     질문은 "Yang Ming"으로 적고 본문은 "양밍해운"으로 적는다. normalize_company_name은
#     법인격 표기와 공백·구분 기호만 걷어내므로 두 표기는 끝내 만나지 않고, 실제로 있는
#     계약이 '없다'로 판정된다.
#
# 음차 규칙을 만들지 않는 이유:
#     "Yang Ming -> 양밍"을 규칙으로 세우면 그 규칙이 적어 두지 않은 다른 이름에도
#     적용된다. 어떤 상호가 한글로 어떻게 읽히는지는 규칙이 정하는 것이 아니라 그 회사가
#     정하는 것이고, 규칙이 만들어 낸 이름으로 문서를 고르면 틀린 상대방의 계약이 형식만
#     맞은 채 답이 된다. 그 틀림은 답을 받는 쪽에서 드러나지 않는다.
#
#     그래서 표기를 하나씩 적는다. 여기 적히지 않은 이름은 지금까지와 똑같이 정규화한 전체
#     문자열이 같아야만 일치한다. 늘어나는 것은 이 표뿐이고 판정 규칙은 그대로다.
#
# 부분 일치·유사도를 여전히 쓰지 않는다:
#     별칭을 거친 뒤에도 비교는 전체 문자열이 같은지 하나다. "테스트해운"이
#     "테스트해운로지스"에 걸리지 않는 성질은 그대로다(select_by_counterparty 참조).
#
# 각 묶음의 첫 표기가 canonical이다. 비교 키는 거기서 유도하며 손으로 적지 않는다. 두 벌을
# 적어 두면 표기를 고칠 때 한쪽만 바뀔 수 있다.
COUNTERPARTY_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    # 대표 Q02의 상대방. 공시 본문은 한글 상호로, 질문은 라틴 표기로 적는다.
    ("양밍해운", "Yang Ming"),
)

# 정규화한 표기 -> canonical 비교 키.
_COUNTERPARTY_ALIASES: dict[str, str] = {
    normalize_company_name(spelling): normalize_company_name(group[0])
    for group in COUNTERPARTY_ALIAS_GROUPS
    for spelling in group
}


def counterparty_key(name: str) -> str:
    """
    계약 상대방을 비교할 때 쓰는 키.

    normalize_company_name을 거친 뒤 별칭 표를 한 번 찾아본다. 표에 없으면 정규화한 이름
    그대로이므로, 별칭을 등록하지 않은 상대방의 판정은 이 함수가 생기기 전과 같다.

    질문 쪽과 문서 쪽 모두 이 함수를 지나야 한다. 한쪽만 지나면 등록한 별칭이 방향에 따라
    걸리기도 하고 걸리지 않기도 한다.

    빈 이름은 빈 키다. 호출부가 "전부 일치"로 읽지 않도록 select_by_counterparty가 따로
    막는다.
    """
    normalized = normalize_company_name(name)

    if not normalized:
        return ""

    return _COUNTERPARTY_ALIASES.get(normalized, normalized)


def filing_date_window(year: int, month: int | None) -> tuple[str, str]:
    """
    질문이 지정한 기간을 공시일 범위로 바꾼다.

    월이 없으면 그 해 전체를 본다. 월이 있으면 그 달의 실제 마지막 날까지만 본다.
    말일을 30이나 31로 고정하면 2월이 없는 날짜를 가리키거나 3월 1일을 끌어들인다.

    거래소공시 조회는 회계연도가 아니라 공시일로 판정한다. manifest의 거래소공시 레코드에는
    base_year가 비어 있다.

    handlers.event_history에 같은 규칙의 함수가 있다. 그쪽은 QueryPlan을 받는 handler
    경로에 묶여 있어 여기서 가져다 쓰면 retrieval이 handler에 의존하게 된다. 두 벌로 둔
    것은 의도이며, 공용 자리로 합치는 것은 Q02 handler를 붙이는 slice에서 정한다.
    """
    if month is None:
        return f"{year}0101", f"{year}1231"

    last_day = calendar.monthrange(year, month)[1]

    return f"{year}{month:02d}01", f"{year}{month:02d}{last_day:02d}"


class ContractSelectionStatus(StrEnum):
    """후보를 1건으로 확정했는지에 대한 판정."""

    SELECTED = "selected"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


class ContractCandidateReadError(RuntimeError):
    """후보 문서를 열거나 읽지 못한 경우.

    조용히 건너뛰지 않는 이유:
        읽지 못한 후보를 빼고 세면 후보가 2건인 상황이 1건으로 보인다. 그러면 확정할 수 없는
        질문에 대해 다른 계약의 값을 답하게 된다. 못 읽었으면 못 읽었다고 멈춘다.
    """

    def __init__(self, doc_id: str, error: Exception) -> None:
        super().__init__(f"후보 공시를 읽지 못했습니다: {doc_id}: {error}")

        self.doc_id = doc_id
        self.error = error


@dataclass(frozen=True)
class ContractCandidate:
    """후보 문서 하나와 그 문서에서 읽은 이벤트."""

    filing: FilingRef
    event: dict[str, Any]

    @property
    def doc_id(self) -> str:
        return self.filing.doc_id or self.filing.receipt_no


@dataclass(frozen=True)
class ContractSelection:
    """후보 선택 결과.

    확정하지 못한 경우에도 후보 목록을 남긴다. 어느 후보들 사이에서 막혔는지가 결과에
    남아야 호출부가 "그런 공시가 없다"와 "어느 것인지 정할 수 없다"를 구분해 답할 수 있다.

    candidate_doc_ids는 상대방으로 좁히기 전의 후보 전체다. NOT_FOUND일 때 이 값이 비어
    있으면 기간·유형 조건에 맞는 공시 자체가 없었던 것이고, 비어 있지 않으면 공시는 있었으나
    그 상대방의 계약이 없었던 것이다.
    """

    status: ContractSelectionStatus
    selected: ContractCandidate | None = None
    candidate_doc_ids: tuple[str, ...] = ()
    matched_doc_ids: tuple[str, ...] = ()
    detail: str = ""

    @property
    def is_selected(self) -> bool:
        return self.status is ContractSelectionStatus.SELECTED and self.selected is not None


def select_by_counterparty(
    candidates: Sequence[ContractCandidate],
    counterparty: str,
) -> tuple[ContractCandidate, ...]:
    """
    계약 상대방이 일치하는 후보만 남긴다.

    비교는 counterparty_key를 거친다. 회사명 정규화(normalize_company_name)에 더해 등록된
    표기 별칭을 canonical 키로 모은다. 법인격 표기·공백·구분 기호 차이나 문자 체계 차이
    때문에 같은 상대방이 다른 이름으로 읽히면, 실제로는 있는 계약이 '없다'로 판정된다.

    별칭을 거친 뒤에도 비교는 전체 문자열이 같은지 하나다. 부분 일치를 쓰지 않는다.
    "선주"가 "북미 지역 선주"와 "유럽 지역 선주"에 모두 걸리는 식으로 후보가 늘어나면,
    좁히려고 넣은 조건이 오히려 잘못된 문서를 후보에 들인다.

    입력 순서를 그대로 보존한다. 순서를 바꾸면 호출부가 앞에서부터 하나를 고르고 싶어진다.
    """
    target = counterparty_key(counterparty)

    if not target:
        return ()

    matched: list[ContractCandidate] = []

    for candidate in candidates:
        fields = candidate.event.get("fields") or {}

        found = fields.get(COUNTERPARTY_FIELD)

        if not found:
            continue

        if counterparty_key(str(found.get("normalized_value") or "")) == target:
            matched.append(candidate)

    return tuple(matched)


def _read_event(filing: FilingRef) -> dict[str, Any]:
    """후보 문서 한 건을 열어 canonical 이벤트로 읽는다."""
    document = parse_exchange_document(filing.main_document)

    return extract_event(document)


def _read_candidates(filings: Sequence[FilingRef]) -> tuple[ContractCandidate, ...]:
    """후보 문서를 전부 열어 이벤트를 읽는다. 하나라도 못 읽으면 멈춘다."""
    candidates: list[ContractCandidate] = []

    for filing in filings:
        try:
            event = _read_event(filing)

        except (OSError, ValueError) as error:
            raise ContractCandidateReadError(
                filing.doc_id or filing.receipt_no,
                error,
            ) from error

        candidates.append(ContractCandidate(filing=filing, event=event))

    return tuple(candidates)


def select_contract_filing(
    *,
    company: str,
    year: int,
    month: int | None,
    counterparty: str,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
    document_subtype: str = EVENT_SUBTYPE_SUPPLY_CONTRACT,
) -> ContractSelection:
    """
    공급계약 원공시 후보를 모아 계약 상대방으로 1건까지 좁힌다.

    확정하지 못해도 예외를 올리지 않고 status로 표현한다. 어느 단계에서 왜 막혔는지 호출자가
    그대로 볼 수 있게 하기 위해서다. 다만 후보 문서를 읽지 못한 경우는 예외를 올린다.
    그것은 "값이 없다"가 아니라 "판정할 수 없다"이며, 조용히 후보에서 빼면 확정이 아닌 것이
    확정으로 보인다.

    counterparty를 필수로 받는 이유:
        상대방 없이는 같은 달의 여러 계약 중 어느 것인지 정할 근거가 없다. 비어 있으면 첫
        번째 후보를 고르고 싶어지는데, 그것이 정확히 이 함수가 막으려는 선택이다.
    """
    if not counterparty:
        raise ValueError(
            "counterparty가 비어 있습니다. 상대방 없이 문서를 확정하지 않습니다."
        )

    corpus_root = Path(corpus_root)

    manifest_path = (
        Path(manifest_path) if manifest_path else corpus_root / "manifest.jsonl"
    )

    window = filing_date_window(year, month)

    # 정정본은 원공시가 아니다. 같은 계약의 다른 판본을 독립 후보로 세지 않는다.
    filings = list_event_filings(
        manifest_path=manifest_path,
        corpus_root=corpus_root,
        company=company,
        document_subtype=document_subtype,
        include_corrections=False,
        date_range=window,
    )

    if not filings:
        return ContractSelection(
            status=ContractSelectionStatus.NOT_FOUND,
            detail=(
                f"{window[0]}~{window[1]} 사이에 제출된 {company}의 {document_subtype} "
                "원공시가 없습니다."
            ),
        )

    candidates = _read_candidates(filings)

    candidate_doc_ids = tuple(candidate.doc_id for candidate in candidates)

    matched = select_by_counterparty(candidates, counterparty)

    matched_doc_ids = tuple(candidate.doc_id for candidate in matched)

    if not matched:
        return ContractSelection(
            status=ContractSelectionStatus.NOT_FOUND,
            candidate_doc_ids=candidate_doc_ids,
            detail=(
                f"후보 {len(candidates)}건 중 계약 상대방이 '{counterparty}'인 공시가 "
                "없습니다. 다른 상대방의 계약으로 대신하지 않습니다."
            ),
        )

    if len(matched) > 1:
        return ContractSelection(
            status=ContractSelectionStatus.AMBIGUOUS,
            candidate_doc_ids=candidate_doc_ids,
            matched_doc_ids=matched_doc_ids,
            detail=(
                f"계약 상대방이 '{counterparty}'인 공시가 {len(matched)}건이라 어느 "
                f"계약인지 특정할 수 없습니다: {', '.join(matched_doc_ids)}."
            ),
        )

    return ContractSelection(
        status=ContractSelectionStatus.SELECTED,
        selected=matched[0],
        candidate_doc_ids=candidate_doc_ids,
        matched_doc_ids=matched_doc_ids,
    )
