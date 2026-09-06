"""Actual-corpus HTTP E2E coverage for representative question Q14.

무엇을 고정하는가:
    Q14는 답이 나가지 않는 질문이다. 다만 답이 나가지 않는 방식이 두 가지로 갈리고, 그
    차이가 이 파일의 내용이다.

        BLOCKED   / query_plan / missing_year   -> HTTP 422
        COMPLETED / UNRESOLVED / AMBIGUOUS_MATCH -> HTTP 200

    예전에는 앞쪽이었다. 질의 해석 계층이 공급계약 이력 질문에 연도를 요구했고, 연도가 없는
    Q14는 실행에 들어가지도 못했다. 422는 "요청이 조건을 못 갖췄다"는 뜻이므로, 받는 쪽은
    질문을 고치면 답이 나온다고 읽는다. 실제로 일어난 일은 그것이 아니다. 질문은 온전했고,
    그 기업의 공급계약 원공시가 여러 건이라 어느 계약인지 좁히지 못한 것이다.

    지금은 뒤쪽이다. 실행을 끝까지 하고 나서 답을 확정하지 못했다는 판정이 200으로 나가며,
    무엇 때문에 확정하지 못했는지가 reason에 남는다.

무엇을 검증하지 않는가:
    루트를 좁히는 능력을 검증하지 않는다. 계약상대방 기반 문서 확정(C3)은 아직 없고, 이
    질문이 답에 이르는 것은 그것이 들어온 뒤의 일이다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.common.enums import AnswerabilityReason, AnswerabilityStatus
from disclosure_agent.common.schemas import AnswerResponse

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"

# evaluation/questions.jsonl의 Q14 원문.
Q14 = "LG에너지솔루션이 포드랑 맺었던 배터리 공급계약, 지금 어떻게 됐어?"

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
def test_q14_returns_200_unresolved_through_actual_corpus_http_api() -> None:
    require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": Q14})

    # 422가 아니다. 실행이 끝나지 못한 것이 아니라 끝나고 나서 답을 확정하지 못했다.
    assert response.status_code == 200

    actual = AnswerResponse.model_validate(response.json())

    assert actual.status is AnswerabilityStatus.UNRESOLVED
    assert actual.reason is AnswerabilityReason.AMBIGUOUS_MATCH

    # 루트를 하나 지어내지 않는다. 후속 체인이 한쪽에만 있다는 이유로 루트를 역으로 고르면
    # 다른 계약의 이력을 그 계약의 이력이라고 말하게 되고, 그 오답은 형식상 완전해 보인다.
    assert actual.event_histories == []
    assert actual.evidence == []
