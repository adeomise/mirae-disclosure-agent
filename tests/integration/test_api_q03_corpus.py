"""Actual-corpus HTTP regression for representative Q03."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.common.enums import AnswerabilityStatus, MetricConcept
from disclosure_agent.common.schemas import AnswerResponse

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"
Q03 = (
    "삼성물산이 2023년 1월에 낸 대량보유 보고서 기준으로, "
    "이번 보고 시점의 삼성전자 보유주식 수와 보유비율이 얼마야?"
)
EXPECTED_DOC_ID = "holding_20230120000563"
EXPECTED = {
    MetricConcept.HOLDING_SHARES: (
        "1237964708",
        "shares",
        "evidence_holding_20230120000563_table_0016_r3_c4_ths_stk_cnt",
    ),
    MetricConcept.HOLDING_RATIO: (
        "20.74",
        "percent",
        "evidence_holding_20230120000563_table_0016_r3_c5_ths_stk_rt",
    ),
}

client = TestClient(app)


def _require_actual_corpus() -> None:
    raw_root = os.environ.get(CORPUS_ROOT_ENV)
    if not raw_root:
        pytest.skip(f"{CORPUS_ROOT_ENV} is not set")
    root = Path(raw_root).expanduser()
    if not root.is_dir():
        pytest.skip(f"corpus root is not a directory: {root}")
    manifest = Path(os.environ.get(CORPUS_MANIFEST_ENV, root / "manifest.jsonl"))
    if not manifest.is_file():
        pytest.skip(f"corpus manifest does not exist: {manifest}")


@pytest.mark.corpus
def test_q03_runs_through_actual_corpus_http_api() -> None:
    _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": Q03})
    assert response.status_code == 200
    actual = AnswerResponse.model_validate(response.json())

    assert actual.status is AnswerabilityStatus.ANSWERABLE
    assert actual.reason is None
    assert len(actual.facts) == 2
    assert len(actual.evidence) == 2

    by_metric = {fact.metric: fact for fact in actual.facts}
    assert set(by_metric) == set(EXPECTED)
    available = {item.evidence_id for item in actual.evidence}
    assert {item.doc_id for item in actual.evidence} == {EXPECTED_DOC_ID}

    for metric, (value, unit, evidence_id) in EXPECTED.items():
        fact = by_metric[metric]
        assert fact.company == "삼성전자"
        assert fact.normalized_value == value
        assert fact.normalized_unit == unit
        assert fact.evidence_ids == [evidence_id]
        assert set(fact.evidence_ids) <= available

    assert "1,237,964,708주" in actual.answer
    assert "20.74%" in actual.answer
