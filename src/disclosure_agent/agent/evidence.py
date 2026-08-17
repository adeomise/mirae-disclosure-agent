"""답변 근거 검증 모듈.

역할:
- 답변의 주장과 CalculationResult를 원문 문서, Section, Chunk에 연결한다.

현재 구현 범위:
- 추출된 단일 Fact 하나에 대해 문서/섹션/표/셀 수준의 provenance를 조립한다.
- 근거가 비면 답변을 만들 수 없도록 오류로 알린다.

입력:
- ParsedDocument, FinancialFact, FilingRef

출력:
- Evidence

TODO:
- 인용 문자열과 원문 일치 검증 추가
- 계산 결과에 대한 다중 근거 연결
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from disclosure_agent.periodic.extractor import FinancialFact
from disclosure_agent.retrieval.metadata_filter import FilingRef


class MissingEvidenceError(RuntimeError):
    """근거를 조립할 수 없는 경우."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)

        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Evidence:
    """단일 수치 주장에 대한 원문 근거."""

    evidence_id: str
    doc_id: str
    receipt_no: str
    report_name: str
    corp_name: str
    source_path: str
    section_id: str | None
    section_path: tuple[str, ...]
    table_id: str
    row_index: int
    column_index: int
    label_text: str
    quote: str
    acode: str
    acontext: str
    unit_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_evidence(
    fact: FinancialFact,
    *,
    filing: FilingRef,
    document: dict[str, Any] | None = None,
) -> Evidence:
    """
    Fact 하나에 대한 Evidence를 만든다.

    evidence_id는 문서와 셀 위치로부터 결정론적으로 만들어 재현 가능하게 한다.
    """
    metadata = (document or {}).get("metadata", {})

    doc_id = metadata.get("doc_id") or filing.doc_id

    if not doc_id:
        raise MissingEvidenceError(
            reason="missing_doc_id",
            detail="근거 문서 식별자가 없습니다.",
        )

    location = fact.location

    evidence_id = (
        f"evidence_{doc_id}_{location.table_id}"
        f"_r{location.row_index}c{location.column_index}"
    )

    source_path = metadata.get("source_path") or str(filing.main_document)

    return Evidence(
        evidence_id=evidence_id,
        doc_id=doc_id,
        receipt_no=metadata.get("receipt_no") or filing.receipt_no,
        report_name=metadata.get("report_name") or filing.report_name,
        corp_name=metadata.get("corp_name") or filing.corp_name,
        source_path=source_path,
        section_id=location.section_id,
        section_path=location.section_path,
        table_id=location.table_id,
        row_index=location.row_index,
        column_index=location.column_index,
        label_text=location.label_text,
        quote=fact.raw_value,
        acode=fact.acode,
        acontext=fact.acontext,
        unit_text=fact.unit_text,
    )
