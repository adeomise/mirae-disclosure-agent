"""Q12 시설투자 이벤트 집계의 actual-corpus 회귀."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from disclosure_agent.agent.router import answer_question
from disclosure_agent.common.enums import (
    AnswerabilityStatus,
    ComparisonRelation,
    ExecutionStatus,
    MetricConcept,
)

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
Q12 = (
    "2024년에 LG이노텍하고 레인보우로보틱스가 새로 발표한 시설투자, "
    "회사별로 다 합치면 어디가 더 커?"
)

LG_ROOTS = ("exchange_20240220800842", "exchange_20241121800444")
LG_2025_ROOT = "exchange_20251127800903"
RAINBOW_ROOT = "exchange_20240430900455"
RAINBOW_AMOUNT_SOURCE = "exchange_20251205900454"


def _actual_corpus_root() -> Path:
    raw = os.environ.get(CORPUS_ROOT_ENV)
    if not raw:
        pytest.skip(f"{CORPUS_ROOT_ENV}가 설정된 경우에만 actual Q12를 검증합니다.")

    root = Path(raw)
    if not root.is_dir() or not (root / "manifest.jsonl").is_file():
        pytest.skip(f"actual corpus/manifest를 찾을 수 없습니다: {root}")

    return root


@pytest.mark.corpus
def test_q12_actual_latest_amount_aggregation_and_provenance() -> None:
    result = answer_question(Q12, corpus_root=_actual_corpus_root())

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None
    assert len(result.facts) == 3
    assert len(result.calculations) == 2
    assert len(result.comparisons) == 1

    assert all(fact.metric is MetricConcept.INVESTMENT_AMOUNT for fact in result.facts)
    fact_by_root = {
        root: next(fact for fact in result.facts if root in fact.fact_id)
        for root in (*LG_ROOTS, RAINBOW_ROOT)
    }
    assert [fact_by_root[root].normalized_value for root in LG_ROOTS] == [
        "383000000000",
        "375900000000",
    ]
    assert fact_by_root[RAINBOW_ROOT].normalized_value == "28533520000"
    assert LG_2025_ROOT not in " ".join(fact.fact_id for fact in result.facts)

    evidence_by_id = {item.evidence_id: item for item in result.evidence}
    for fact in result.facts:
        assert fact.evidence_ids
        assert set(fact.evidence_ids) <= evidence_by_id.keys()

    rainbow_fact = fact_by_root[RAINBOW_ROOT]
    assert rainbow_fact.evidence_ids == [
        f"evidence_{RAINBOW_AMOUNT_SOURCE}_investment_amount"
    ]
    assert evidence_by_id[rainbow_fact.evidence_ids[0]].doc_id == RAINBOW_AMOUNT_SOURCE

    assert [calculation.value for calculation in result.calculations] == [
        "758900000000",
        "28533520000",
    ]
    assert [calculation.unit for calculation in result.calculations] == ["KRW", "KRW"]
    assert [len(calculation.inputs) for calculation in result.calculations] == [2, 1]

    comparison = result.comparisons[0]
    assert comparison.left.input_id == result.calculations[0].calculation_id
    assert comparison.right.input_id == result.calculations[1].calculation_id
    assert comparison.relation is ComparisonRelation.LEFT_GREATER
