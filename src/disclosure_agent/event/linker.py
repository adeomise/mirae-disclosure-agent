"""원본·정정공시 연결 모듈.

역할:
- 명시적 문서 메타데이터를 이용해 정정공시와 대상 원본을 연결한다.

예정 입력:
- Event 및 DocumentMetadata 목록

예정 출력:
- 원본 참조가 설정된 Event 목록

TODO:
- DART 문서의 정정 연결 필드 확인
- 불확실한 연결을 거부하는 정책 구현
- 실제 접수번호를 추정하지 않는 검증 추가
"""

from typing import Any

from disclosure_agent.common.enums import (
    EventLinkRelation,
    EventLinkResolutionStatus,
)
from disclosure_agent.common.schemas import EventLink, RawReference


def link_supply_contract_correction(
    correction_document: dict[str, Any],
    original_documents: list[dict[str, Any]],
) -> EventLink:
    """Resolve a correction only from explicit, jointly matching references."""
    metadata = correction_document.get("metadata") or {}
    correction = correction_document.get("correction") or {}
    source_doc_id = str(metadata.get("doc_id") or "")
    original_filing_date = _clean(correction.get("original_filing_date"))
    related_disclosures = metadata.get("related_disclosures") or []
    raw_reference = RawReference(
        document_title=_clean(metadata.get("report_name"))
        or _clean(correction_document.get("title")),
        original_filing_date=original_filing_date or None,
        correction_filing_date=_clean(correction.get("correction_date")) or None,
        correction_reason=_clean(correction.get("reason")) or None,
        related_disclosures=[
            {str(key): str(value) for key, value in item.items()}
            for item in related_disclosures
            if isinstance(item, dict)
        ],
    )

    candidates = [
        document
        for document in original_documents
        if _is_same_referenced_event(
            correction_document,
            document,
            original_filing_date,
        )
    ]
    if len(candidates) == 1:
        resolved_doc_id = str(candidates[0]["metadata"]["doc_id"])
        status = EventLinkResolutionStatus.RESOLVED
    elif len(candidates) > 1:
        resolved_doc_id = None
        status = EventLinkResolutionStatus.AMBIGUOUS
    else:
        resolved_doc_id = None
        status = (
            EventLinkResolutionStatus.OUT_OF_CORPUS
            if original_filing_date
            else EventLinkResolutionStatus.NOT_FOUND
        )

    return EventLink(
        link_id=f"link_{source_doc_id}_correction_of",
        source_doc_id=source_doc_id,
        relation=EventLinkRelation.CORRECTION_OF,
        raw_reference=raw_reference,
        resolved_doc_id=resolved_doc_id,
        candidate_doc_ids=(
            sorted(str(candidate["metadata"]["doc_id"]) for candidate in candidates)
            if status is EventLinkResolutionStatus.AMBIGUOUS
            else []
        ),
        resolution_status=status,
    )


def has_missing_related_disclosures(
    correction_document: dict[str, Any],
    documents: list[dict[str, Any]],
) -> bool:
    """Return true only for explicit related-disclosure references absent from corpus."""
    related = (correction_document.get("metadata") or {}).get(
        "related_disclosures"
    ) or []
    for reference in related:
        if not isinstance(reference, dict):
            continue
        filing_date = _clean(reference.get("filing_date"))
        document_title = _compact(_clean(reference.get("document_title")))
        if not filing_date and not document_title:
            continue
        if not any(
            _reference_matches_document(filing_date, document_title, document)
            for document in documents
        ):
            return True
    return False


def _is_same_referenced_event(
    correction_document: dict[str, Any],
    original_document: dict[str, Any],
    original_filing_date: str,
) -> bool:
    correction_metadata = correction_document.get("metadata") or {}
    original_metadata = original_document.get("metadata") or {}
    if not original_filing_date or original_metadata.get("is_correction") is not False:
        return False
    if _compact(_clean(correction_metadata.get("corp_name"))) != _compact(
        _clean(original_metadata.get("corp_name"))
    ):
        return False
    if _clean(original_metadata.get("receipt_date")) != original_filing_date:
        return False
    if not (_is_supply_contract(correction_document) and _is_supply_contract(original_document)):
        return False

    correction_counterparty = _row_value(correction_document, "계약상대", "계약상대방")
    original_counterparty = _row_value(original_document, "계약상대", "계약상대방")
    return bool(
        correction_counterparty
        and original_counterparty
        and _compact(correction_counterparty) == _compact(original_counterparty)
    )


def _reference_matches_document(
    filing_date: str,
    document_title: str,
    document: dict[str, Any],
) -> bool:
    metadata = document.get("metadata") or {}
    date_matches = not filing_date or _clean(metadata.get("receipt_date")) == filing_date
    title = _compact(
        _clean(metadata.get("report_name")) or _clean(document.get("title"))
    )
    title_matches = not document_title or title == document_title
    return date_matches and title_matches


def _is_supply_contract(document: dict[str, Any]) -> bool:
    metadata = document.get("metadata") or {}
    title = _compact(
        _clean(metadata.get("report_name")) or _clean(document.get("title"))
    )
    return "단일판매" in title and "공급계약" in title


def _row_value(document: dict[str, Any], *field_names: str) -> str:
    names = {_compact(name) for name in field_names}
    for row in document.get("rows") or []:
        if _compact(_clean(row.get("field"))) in names:
            return _clean(row.get("value"))
    return ""


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _compact(value: str) -> str:
    return "".join(value.split())
