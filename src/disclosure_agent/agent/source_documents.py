"""선택된 FilingRef를 document-level source metadata Contract로 옮기는 모듈.

역할:
- 실행 결과의 Evidence.doc_id가 가리키는 문서의 metadata(정식 명칭, 회계기간, 접수번호,
  공시일)를 AgentExecutionResult.source_documents로 옮긴다.

왜 별도 모듈인가:
    FilingRef(retrieval) -> SourceDocument(common) 변환은 Pattern A/B/Q02가 함께 쓴다. 각
    normalize_* 함수 안에 반복해 적으면 한쪽만 고쳐지는 날이 온다.

    agent 계층에 두는 이유는 이 변환이 retrieval과 common을 함께 아는 조합이기 때문이다.
    retrieval은 agent를 모르고(tests/unit/test_layering.py), periodic·event도 retrieval을
    모른다. 이 조합이 가능한 자리는 agent뿐이다.

Metadata source 원칙(사용자 설계 문서와 같다):
    manifest를 다시 검색해 채우지 않는다. 이미 선택된 FilingRef가 들고 있는 값만 그대로
    옮긴다. 질문·Fact.metric·section_path·doc_id 문자열에서 문서 종류나 날짜를 추측하지
    않는다.

무결성 규칙:
    같은 doc_id를 가리키는 FilingRef가 둘 이상이고 그 metadata가 서로 다르면 조용히 하나를
    고르지 않고 예외로 멈춘다. 순서는 호출자가 넘긴 FilingRef 순서(=실행 결과·Evidence의
    결정적 순서)를 그대로 따르며 여기서 다시 정렬하지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable

from disclosure_agent.common.dates import parse_filing_date
from disclosure_agent.common.schemas import SourceDocument
from disclosure_agent.retrieval.metadata_filter import FilingRef

__all__ = [
    "ConflictingSourceDocumentError",
    "build_source_document",
    "collect_source_documents",
]


class ConflictingSourceDocumentError(RuntimeError):
    """같은 doc_id를 가리키는 두 FilingRef의 metadata가 서로 다른 경우."""

    def __init__(self, doc_id: str, first: SourceDocument, second: SourceDocument) -> None:
        super().__init__(
            f"doc_id {doc_id!r}에 서로 다른 source document metadata가 있습니다: "
            f"{first!r} != {second!r}. 하나를 조용히 고르지 않습니다."
        )

        self.doc_id = doc_id
        self.first = first
        self.second = second


def build_source_document(
    filing: FilingRef,
    *,
    canonical_company: str | None = None,
) -> SourceDocument:
    """선택된 공시 1건의 metadata를 그대로 옮긴다. 값을 새로 만들지 않는다.

    canonical_company를 받는 이유:
        periodic.adapter._contract_company와 같은 자리다. Pattern A/B는 Fact.company·
        DocumentSummary.company에 manifest 원 표기(법인격 표기가 남아 있을 수 있다) 대신
        질의 해석이 확정한 canonical 회사명을 싣는다. source_documents.company를 여기서
        manifest 원 표기로 두면 같은 응답 안에서 같은 회사가 두 표기로 나뉘어 나간다.
        생략하면(Q02처럼 canonical 표기가 없는 경우) manifest 원 표기를 그대로 쓴다 —
        거래소공시 Fact.company도 같은 값을 쓴다(agent.handlers.contract_fact).

    document_subtype·receipt_no는 manifest에 빈 문자열로 남아 있을 수 있어(FilingRef의
    기본값이 ""다) 빈 문자열을 None으로 맞춘다. 값을 지어내는 것이 아니라 "없음"의 표기를
    Contract의 optional 규약(빈 문자열이 아니라 None)에 맞추는 것뿐이다.
    """
    return SourceDocument(
        doc_id=filing.doc_id,
        company=canonical_company or filing.corp_name,
        report_name=filing.report_name,
        fiscal_year=filing.base_year,
        document_subtype=filing.document_subtype or None,
        base_month=filing.base_month,
        filing_date=parse_filing_date(filing.receipt_date),
        receipt_no=filing.receipt_no or None,
    )


def collect_source_documents(
    filings: Iterable[FilingRef],
    *,
    canonical_company: str | None = None,
) -> list[SourceDocument]:
    """
    선택된 FilingRef들을 doc_id 기준 중복 없이 옮긴다.

    같은 문서를 가리키는 FilingRef가 여러 번 들어와도(예: 같은 문서에서 여러 Fact를
    확보한 경우) source document는 한 번만 담는다. 처음 등장한 자리에 남기고, 같은
    doc_id로 다시 나타난 FilingRef의 metadata가 다르면 멈춘다(ConflictingSourceDocumentError).

    canonical_company는 모든 filing에 똑같이 적용한다. 지금 이 함수를 쓰는 Pattern은
    회사 하나를 다루므로(단일 기업 조회) 이 인자도 하나만 받는다. 기업이 여럿인 Pattern이
    생기면 그때 호출자가 filing마다 다른 canonical_company로 build_source_document를 직접
    불러야 한다 — 여기서 미리 매핑을 받으면 지금 쓰이지 않는 모양을 위한 자리가 된다.

    정렬하지 않는다. 호출자가 넘기는 순서가 이미 실행 결과·Evidence의 결정적 순서다.
    """
    ordered: list[SourceDocument] = []
    by_doc_id: dict[str, SourceDocument] = {}

    for filing in filings:
        document = build_source_document(filing, canonical_company=canonical_company)

        existing = by_doc_id.get(document.doc_id)

        if existing is None:
            by_doc_id[document.doc_id] = document
            ordered.append(document)
            continue

        if existing != document:
            raise ConflictingSourceDocumentError(document.doc_id, existing, document)

    return ordered
