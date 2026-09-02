"""Q03 major-shareholding fact handler."""

from __future__ import annotations

from pathlib import Path

from disclosure_agent.agent.normalization import blocked_execution
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.common.enums import AnswerabilityStatus, ExecutionStatus
from disclosure_agent.common.schemas import AgentExecutionResult
from disclosure_agent.holding.extractor import HoldingExtractionError, extract_holding_facts
from disclosure_agent.holding.parser import parse_holding_xml
from disclosure_agent.retrieval.metadata_filter import (
    FilingNotFoundError,
    filter_filings,
    load_manifest,
    normalize_company_name,
    resolve_filing_ref,
)


def handle_holding_fact(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> AgentExecutionResult:
    """Select one issuer/filer/month filing and extract the two allowlisted values."""
    root = Path(corpus_root)
    manifest = Path(manifest_path) if manifest_path else root / "manifest.jsonl"
    try:
        candidates = filter_filings(
            load_manifest(manifest),
            company=plan.company,
            document_class=plan.document_class,
            document_subtype=plan.document_subtype,
            year=plan.year,
        )
        candidates = [
            record
            for record in candidates
            if normalize_company_name(str(record.get("flr_nm", "")))
            == normalize_company_name(plan.filer or "")
            and (plan.month is None or str(record.get("rcept_dt", ""))[4:6] == f"{plan.month:02d}")
        ]
        if len(candidates) != 1:
            reason = "no_matching_filing" if not candidates else "ambiguous_candidates"
            return blocked_execution(
                "filing_discovery",
                reason,
                f"지분공시 후보를 1건으로 확정하지 못했습니다: count={len(candidates)}",
            )
        filing = resolve_filing_ref(candidates[0], corpus_root=root)
        document = parse_holding_xml(filing.main_document, doc_id=filing.doc_id)
        extracted = extract_holding_facts(document, target_company=filing.corp_name)
    except FilingNotFoundError as error:
        return blocked_execution("filing_discovery", error.reason, error.detail)
    except (HoldingExtractionError, OSError, ValueError) as error:
        return blocked_execution("extraction", "holding_extraction_failed", str(error))

    if extracted.missing_metrics:
        return blocked_execution(
            "extraction",
            "no_metric_acode",
            f"지분공시 필수 지표가 없습니다: {[item.value for item in extracted.missing_metrics]}",
        )
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=list(extracted.facts),
        evidence=list(extracted.evidence),
    )
