"""Q03 major-shareholding fact handler."""

from __future__ import annotations

from pathlib import Path

from disclosure_agent.agent.normalization import blocked_execution
from disclosure_agent.agent.query_analyzer import QueryPlan
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ExecutionStatus,
    MetricConcept,
)
from disclosure_agent.common.schemas import AgentExecutionResult, AnswerabilityDetail
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
        if not candidates:
            return _completed_without_filing(
                status=AnswerabilityStatus.UNANSWERABLE,
                canonical_reason=AnswerabilityReason.NOT_FOUND,
                reason="no_matching_filing",
                count=0,
            )
        if len(candidates) > 1:
            return _completed_without_filing(
                status=AnswerabilityStatus.UNRESOLVED,
                canonical_reason=AnswerabilityReason.AMBIGUOUS_MATCH,
                reason="ambiguous_filing",
                count=len(candidates),
            )
        filing = resolve_filing_ref(candidates[0], corpus_root=root)
        document = parse_holding_xml(filing.main_document, doc_id=filing.doc_id)
        extracted = extract_holding_facts(document, target_company=filing.corp_name)
    except FilingNotFoundError as error:
        return blocked_execution("filing_discovery", error.reason, error.detail)
    except (HoldingExtractionError, OSError, ValueError) as error:
        return blocked_execution("extraction", "holding_extraction_failed", str(error))

    requested = set(
        plan.requested_fields
        or (MetricConcept.HOLDING_SHARES, MetricConcept.HOLDING_RATIO)
    )
    facts = [fact for fact in extracted.facts if fact.metric in requested]
    evidence_ids = {evidence_id for fact in facts for evidence_id in fact.evidence_ids}
    evidence = [item for item in extracted.evidence if item.evidence_id in evidence_ids]
    missing = requested - {fact.metric for fact in facts}

    if len(requested) > 1 and facts and missing:
        return AgentExecutionResult(
            execution_status=ExecutionStatus.COMPLETED,
            answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
            answerability_reason=AnswerabilityReason.PARTIAL_SOURCE_SUPPORT,
            answerability_detail=AnswerabilityDetail(
                stage="extraction",
                reason="partial_source_support",
                detail=(
                    "지분공시 요청 지표 일부의 근거가 없습니다: "
                    f"{sorted(item.value for item in missing)}"
                ),
            ),
            facts=facts,
            evidence=evidence,
        )
    if missing:
        return AgentExecutionResult(
            execution_status=ExecutionStatus.COMPLETED,
            answerability_status=AnswerabilityStatus.UNANSWERABLE,
            answerability_reason=AnswerabilityReason.INSUFFICIENT_EVIDENCE,
            answerability_detail=AnswerabilityDetail(
                stage="extraction",
                reason="no_metric_acode",
                detail=(
                    "지분공시 요청 지표가 없습니다: "
                    f"{sorted(item.value for item in missing)}"
                ),
            ),
        )
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=facts,
        evidence=evidence,
    )


def _completed_without_filing(
    *,
    status: AnswerabilityStatus,
    canonical_reason: AnswerabilityReason,
    reason: str,
    count: int,
) -> AgentExecutionResult:
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=status,
        answerability_reason=canonical_reason,
        answerability_detail=AnswerabilityDetail(
            stage="filing_discovery",
            reason=reason,
            detail=f"지분공시 후보를 1건으로 확정하지 못했습니다: count={count}",
        ),
    )
