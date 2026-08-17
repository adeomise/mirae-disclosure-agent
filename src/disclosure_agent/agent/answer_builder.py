"""최종 답변 조립 모듈.

역할:
- 검색 결과, Fact, 계산 결과, Evidence를 하나의 응답 구조로 묶는다.

현재 구현 범위:
- 자연어 문장을 만들지 않는다. 구조화된 결과 dict만 조립한다.
- 자연어 생성은 이후 LLM 단계의 책임이며 이번 슬라이스 범위 밖이다.

입력:
- QueryPlan, FilingRef, FinancialFact, Evidence

출력:
- 직렬화 가능한 결과 dict

TODO:
- 계산 결과와 다중 Fact 응답 형태 확장
- 경고 정책 정리
"""

from __future__ import annotations

from typing import Any

from disclosure_agent.agent.evidence import Evidence
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.periodic.extractor import FinancialFact
from disclosure_agent.retrieval.metadata_filter import FilingRef

STATUS_OK = "ok"
STATUS_BLOCKED = "blocked"


def _filing_summary(filing: FilingRef | None) -> dict[str, Any] | None:
    if filing is None:
        return None

    return {
        "doc_id": filing.doc_id,
        "corp_name": filing.corp_name,
        "corp_code": filing.corp_code,
        "stock_code": filing.stock_code,
        "report_name": filing.report_name,
        "receipt_no": filing.receipt_no,
        "receipt_date": filing.receipt_date,
        "document_class": filing.document_class,
        "document_subtype": filing.document_subtype,
        "base_year": filing.base_year,
        "base_month": filing.base_month,
        "is_correction": filing.is_correction,
        "source_path": str(filing.main_document),
    }


def build_structured_result(
    plan: QueryPlan,
    *,
    filing: FilingRef,
    fact: FinancialFact,
    evidence: Evidence,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """성공한 단일 수치 조회 결과를 조립한다."""
    combined_warnings = list(plan.warnings) + list(warnings or [])

    return {
        "status": STATUS_OK,
        "query": plan.to_dict(),
        "filing": _filing_summary(filing),
        "fact": fact.to_dict(),
        "evidence": [evidence.to_dict()],
        "calculations": [],
        "warnings": combined_warnings,
    }


def build_blocked_result(
    plan: QueryPlan | None,
    *,
    stage: str,
    reason: str,
    detail: str,
    filing: FilingRef | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """
    파이프라인이 진행되지 못한 경우의 결과를 조립한다.

    값을 추측해 채우지 않고 어느 단계에서 왜 막혔는지만 남긴다.
    """
    combined_warnings = list(plan.warnings) if plan else []

    combined_warnings += list(warnings or [])

    return {
        "status": STATUS_BLOCKED,
        "query": plan.to_dict() if plan else None,
        "filing": _filing_summary(filing),
        "fact": None,
        "evidence": [],
        "calculations": [],
        "blocked": {
            "stage": stage,
            "reason": reason,
            "detail": detail,
        },
        "warnings": combined_warnings,
    }
