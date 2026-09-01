"""Actual-corpus HTTP regressions for representative Pattern D questions."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.common.enums import AnswerabilityStatus
from disclosure_agent.common.schemas import AnswerResponse

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"

Q10 = "2025년 HD현대중공업이랑 삼성중공업 매출 비교해줘."
Q11 = "HD현대중공업이랑 삼성중공업 중에 2025년 영업이익률이 더 높은 곳은 어디야?"

client = TestClient(app)


def _require_actual_corpus() -> None:
    raw_corpus_root = os.environ.get(CORPUS_ROOT_ENV)
    if not raw_corpus_root:
        pytest.skip(f"{CORPUS_ROOT_ENV} is not set")

    corpus_root = Path(raw_corpus_root).expanduser()
    if not corpus_root.is_dir():
        pytest.skip(f"corpus root is not a directory: {corpus_root}")

    raw_manifest = os.environ.get(CORPUS_MANIFEST_ENV)
    manifest_path = (
        Path(raw_manifest).expanduser()
        if raw_manifest is not None
        else corpus_root / "manifest.jsonl"
    )
    if not manifest_path.is_file():
        pytest.skip(f"corpus manifest does not exist: {manifest_path}")


def _assert_answerable_with_resolved_fact_evidence(
    response_payload: object,
    *,
    expected_fact_count: int,
) -> None:
    actual = AnswerResponse.model_validate(response_payload)

    assert actual.status is AnswerabilityStatus.ANSWERABLE
    assert actual.reason is None
    assert len(actual.facts) == expected_fact_count
    assert actual.evidence

    response_evidence_ids = {item.evidence_id for item in actual.evidence}
    for fact in actual.facts:
        assert fact.evidence_ids
        assert set(fact.evidence_ids) <= response_evidence_ids


@pytest.mark.corpus
@pytest.mark.parametrize(
    ("question", "expected_fact_count"),
    [
        pytest.param(Q10, 2, id="q10-revenue-comparison"),
        pytest.param(Q11, 4, id="q11-operating-margin-comparison"),
    ],
)
def test_pattern_d_questions_run_through_actual_corpus_http_api(
    question: str,
    expected_fact_count: int,
) -> None:
    _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": question})

    assert response.status_code == 200
    _assert_answerable_with_resolved_fact_evidence(
        response.json(),
        expected_fact_count=expected_fact_count,
    )
