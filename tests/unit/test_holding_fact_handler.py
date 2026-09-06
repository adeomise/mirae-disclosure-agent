"""Focused Q03 handler outcome regressions."""

from pathlib import Path

import pytest

from disclosure_agent.agent.handlers.holding_fact import handle_holding_fact
from disclosure_agent.agent.query_analyzer import INTENT_HOLDING_FACT, QueryPlan
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ExecutionStatus,
    MetricConcept,
)
from disclosure_agent.holding.extractor import HoldingExtractionResult


def _plan() -> QueryPlan:
    return QueryPlan(
        question="synthetic holding question",
        companies=("테스트발행회사",),
        years=(2023,),
        month=1,
        intent=INTENT_HOLDING_FACT,
        document_class="holding",
        document_subtype="대량보유상황보고서",
        accounting_scope="not_applicable",
        metric=None,
        requested_fields=(MetricConcept.HOLDING_SHARES, MetricConcept.HOLDING_RATIO),
        filer="테스트보고자",
    )


def _record(index: int) -> dict[str, str]:
    return {
        "corp_name": "테스트발행회사",
        "flr_nm": "테스트보고자",
        "doc_group": "holding",
        "doc_subtype": "대량보유상황보고서",
        "rcept_dt": "20230120",
        "rcept_no": f"synthetic-{index}",
    }


@pytest.mark.parametrize(
    ("records", "status", "reason", "detail_reason"),
    [
        (
            [],
            AnswerabilityStatus.UNANSWERABLE,
            AnswerabilityReason.NOT_FOUND,
            "no_matching_filing",
        ),
        (
            [_record(1), _record(2)],
            AnswerabilityStatus.UNRESOLVED,
            AnswerabilityReason.AMBIGUOUS_MATCH,
            "ambiguous_filing",
        ),
    ],
)
def test_filing_selection_is_completed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    records: list[dict[str, str]],
    status: AnswerabilityStatus,
    reason: AnswerabilityReason,
    detail_reason: str,
) -> None:
    monkeypatch.setattr(
        "disclosure_agent.agent.handlers.holding_fact.load_manifest", lambda _: records
    )
    monkeypatch.setattr(
        "disclosure_agent.agent.handlers.holding_fact.filter_filings", lambda rows, **_: rows
    )

    result = handle_holding_fact(_plan(), corpus_root=tmp_path)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is status
    assert result.answerability_reason is reason
    assert result.answerability_detail is not None
    assert result.answerability_detail.reason == detail_reason
    assert result.execution_issue is None


def test_shares_present_ratio_missing_preserves_supported_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    record = _record(1)
    monkeypatch.setattr(
        "disclosure_agent.agent.handlers.holding_fact.load_manifest", lambda _: [record]
    )
    monkeypatch.setattr(
        "disclosure_agent.agent.handlers.holding_fact.filter_filings", lambda rows, **_: rows
    )
    xml = tmp_path / "holding.xml"
    xml.write_text(
        '<DOCUMENT><BODY><TABLE><TR><TE ACODE="THS_STK_CNT">'
        "1,000</TE></TR></TABLE></BODY></DOCUMENT>",
        encoding="utf-8",
    )
    filing = type(
        "Filing",
        (),
        {"main_document": xml, "doc_id": "holding_SYNTHETIC", "corp_name": "테스트발행회사"},
    )()
    monkeypatch.setattr(
        "disclosure_agent.agent.handlers.holding_fact.resolve_filing_ref",
        lambda *_args, **_kwargs: filing,
    )

    result = handle_holding_fact(_plan(), corpus_root=tmp_path)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.PARTIAL_SOURCE_SUPPORT
    assert [fact.metric for fact in result.facts] == [MetricConcept.HOLDING_SHARES]
    assert result.facts[0].normalized_value == "1000"
    assert len(result.evidence) == 1
    assert result.facts[0].evidence_ids == [result.evidence[0].evidence_id]
    assert all(fact.metric is not MetricConcept.HOLDING_RATIO for fact in result.facts)


@pytest.mark.parametrize(
    "requested",
    [
        (MetricConcept.HOLDING_RATIO,),
        (MetricConcept.HOLDING_SHARES, MetricConcept.HOLDING_RATIO),
    ],
)
def test_missing_all_requested_metrics_is_not_partial(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    requested: tuple[MetricConcept, ...],
) -> None:
    plan = _plan()
    plan = QueryPlan(**{**plan.__dict__, "requested_fields": requested})
    record = _record(1)
    monkeypatch.setattr(
        "disclosure_agent.agent.handlers.holding_fact.load_manifest", lambda _: [record]
    )
    monkeypatch.setattr(
        "disclosure_agent.agent.handlers.holding_fact.filter_filings",
        lambda rows, **_: rows,
    )
    filing = type(
        "Filing",
        (),
        {
            "main_document": tmp_path / "unused.xml",
            "doc_id": "holding_SYNTHETIC",
            "corp_name": "테스트발행회사",
        },
    )()
    monkeypatch.setattr(
        "disclosure_agent.agent.handlers.holding_fact.resolve_filing_ref",
        lambda *_args, **_kwargs: filing,
    )
    monkeypatch.setattr(
        "disclosure_agent.agent.handlers.holding_fact.parse_holding_xml",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "disclosure_agent.agent.handlers.holding_fact.extract_holding_facts",
        lambda *_args, **_kwargs: HoldingExtractionResult(
            facts=(), evidence=(), missing_metrics=requested
        ),
    )

    result = handle_holding_fact(plan, corpus_root=tmp_path)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.INSUFFICIENT_EVIDENCE
    assert result.facts == []
    assert result.evidence == []
