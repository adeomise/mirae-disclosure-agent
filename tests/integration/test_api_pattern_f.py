"""HTTP regressions for representative Pattern F questions."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app

client = TestClient(app)


@pytest.mark.parametrize(
    ("question", "expected_reason"),
    [
        (
            "삼성전자 2026년 상반기 실적 좀 알려줘.",
            "out_of_data_range",
        ),
        (
            "이번 수주 공시 나오고 나서 시장 반응은 어땠어?",
            "out_of_scope_data",
        ),
        (
            "지금 한화오션 주식 사도 될까?",
            "out_of_scope_policy",
        ),
    ],
    ids=["q16-out-of-data-range", "q17-out-of-scope-data", "q18-policy"],
)
def test_pattern_f_rejects_before_corpus_access(
    question: str,
    expected_reason: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DISCLOSURE_CORPUS_ROOT", str(tmp_path / "unused-corpus"))
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": question})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "UNANSWERABLE"
    assert payload["reason"] == expected_reason
    assert payload["facts"] == []
    assert payload["evidence"] == []
    assert "execution_status" not in payload
    assert "error" not in payload
