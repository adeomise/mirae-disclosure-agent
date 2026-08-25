"""Integration checks for the lightweight disclosure UI."""

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from disclosure_agent.api.main import app

client = TestClient(app)


def test_root_serves_disclosure_ui() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "요즘 어떤 기업이 궁금한가요?" in response.text
    assert "필요한 숫자와 근거까지 한눈에 찾아드려요." in response.text
    assert "샘플 결과 보기" in response.text
    assert '<form class="search-form" id="question-form">' in response.text
    assert '<button type="submit">질문하기</button>' in response.text
    assert 'id="results" aria-labelledby="results-title" hidden' in response.text
    assert 'class="result-card"' in response.text
    assert 'data-mode="none" hidden' in response.text


def test_ui_static_assets_are_available() -> None:
    stylesheet = client.get("/static/styles.css")
    javascript = client.get("/static/app.js")

    assert stylesheet.status_code == 200
    assert "--primary: #55d6d2" in stylesheet.text
    assert javascript.status_code == 200
    assert 'fetch("/api/ask"' in javascript.text


def test_ui_api_and_sample_state_transitions() -> None:
    state_test = Path(__file__).with_name("test_api_ui_state.js")

    result = subprocess.run(
        ["node", "--test", str(state_test)],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
