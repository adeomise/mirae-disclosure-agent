"""Actual-corpus HTTP E2E coverage for representative question Q15."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.common.enums import (
    AnswerabilityStatus,
    EventLinkResolutionStatus,
)
from disclosure_agent.common.schemas import AnswerResponse

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"
Q15 = "레인보우로보틱스 사옥·제조시설 신축 투자, 그동안 계획이 몇 번 바뀌었어?"

client = TestClient(app)


def require_actual_corpus() -> None:
    """Skip unless the actual disclosure corpus is explicitly configured."""

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


@pytest.mark.corpus
def test_q15_runs_through_actual_corpus_http_api() -> None:
    require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": Q15})

    assert response.status_code == 200
    actual = AnswerResponse.model_validate(response.json())
    assert actual.status is AnswerabilityStatus.ANSWERABLE
    assert actual.reason is None
    assert len(actual.event_histories) == 1

    history = actual.event_histories[0]
    assert history.chain_complete is True
    assert len(actual.event_links) == 3
    assert len(history.changes) == 4
    assert all(
        link.resolution_status is EventLinkResolutionStatus.RESOLVED
        for link in actual.event_links
    )
    assert len(actual.evidence) >= 2

    response_evidence_ids = {item.evidence_id for item in actual.evidence}
    for change in history.changes:
        assert change.before_evidence_id is not None
        assert change.after_evidence_id is not None
        assert change.before_evidence_id in response_evidence_ids
        assert change.after_evidence_id in response_evidence_ids
