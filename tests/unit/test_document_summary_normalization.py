"""Pattern B(문서 요약) 정규화의 source_documents 배선 테스트.

무엇을 고정하는가:
- Pattern A와 같은 규약으로, 실제 선택된 FilingRef에서 문서 metadata를 옮기는가.
- Evidence.doc_id와 source_documents[0].doc_id가 정확히 연결되는가.
- 막힌 결과에는 source_documents도 비어 있는가.

값 변환(SectionContentItem -> DocumentSummary/Evidence)은 periodic.adapter가 이미
검증하고 있으므로 여기서 다시 보지 않는다. 이 파일이 보는 것은 그 결과에 문서 metadata가
따라붙는지 하나뿐이다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from disclosure_agent.agent.handlers.document_summary import DocumentSummaryResult
from disclosure_agent.agent.normalization import normalize_document_summary_result
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.common.enums import SectionScope
from disclosure_agent.periodic.service import collect_scope_content
from disclosure_agent.retrieval.metadata_filter import FilingRef

CORPUS_ROOT = Path("corpus")


def _filing() -> FilingRef:
    return FilingRef(
        doc_id="periodic_SYNTHETIC0001",
        corp_name="테스트반도체",
        corp_code="00000001",
        stock_code="000001",
        report_name="사업보고서",
        receipt_no="SYNTHETIC0001",
        receipt_date="20260310",
        document_class="periodic",
        doc_group="periodic",
        document_subtype="annual",
        base_year=2025,
        base_month=12,
        is_correction=False,
        directory=CORPUS_ROOT / "SYNTHETIC0001",
        main_document=CORPUS_ROOT / "SYNTHETIC0001" / "SYNTHETIC0001.xml",
    )


def _plan() -> QueryPlan:
    return QueryPlan(
        question="테스트반도체 설비투자 알려줘",
        companies=("테스트반도체",),
        years=(2025,),
    )


def test_document_summary_success_includes_the_selected_filing_as_a_source_document(
    summary_document: dict[str, Any],
) -> None:
    filing = _filing()
    plan = _plan()
    selection, items = collect_scope_content(
        summary_document, section_scope=SectionScope.FACILITY_INVESTMENT
    )

    result = normalize_document_summary_result(
        DocumentSummaryResult(
            plan=plan,
            filing=filing,
            document=summary_document,
            selection=selection,
            items=tuple(items),
        )
    )

    assert len(result.source_documents) == 1

    document = result.source_documents[0]

    assert {evidence.doc_id for evidence in result.evidence} == {document.doc_id}
    assert document.doc_id == filing.doc_id
    assert document.company == plan.company
    assert document.report_name == filing.report_name
    assert document.fiscal_year == filing.base_year
    assert document.document_subtype == filing.document_subtype
    assert document.base_month == filing.base_month
    assert document.receipt_no == filing.receipt_no


def test_blocked_document_summary_result_has_no_source_documents() -> None:
    from disclosure_agent.agent.answer_builder import (
        REASON_NO_MATCHING_FILING,
        STAGE_FILING_DISCOVERY,
        BlockedInfo,
    )

    result = normalize_document_summary_result(
        DocumentSummaryResult(
            plan=_plan(),
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=REASON_NO_MATCHING_FILING,
                detail="합성 사유",
            ),
        )
    )

    assert result.source_documents == []
