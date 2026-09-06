"""Integration checks for the lightweight disclosure UI.

The UI was redesigned after these tests were first written (status-badge/
evidence-panel layout replacing an older "sample data" preview concept). The
assertions below were re-aligned to the *current* index.html/app.js/
styles.css rather than weakened or deleted — see test_api_ui_state.js for the
matching rewrite of the JS-behavior suite.
"""

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from disclosure_agent.api.main import app

client = TestClient(app)


def test_root_serves_disclosure_ui() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "어떤 공시가 궁금하신가요?" in response.text
    assert "핵심 정보와 답변을 뒷받침하는 근거까지 함께 보여드려요." in response.text
    assert '<form class="search-form" id="question-form">' in response.text
    assert '<button type="submit">질문하기</button>' in response.text
    assert 'id="results"' in response.text
    assert 'aria-labelledby="results-title"' in response.text
    assert 'id="result-card"' in response.text
    assert 'class="result-card"' in response.text
    # 답변 상태 배지와 근거 패널이 실제로 마크업에 존재하는지 — 이 둘이 없으면
    # AnswerResponse.status/evidence를 표시할 자리 자체가 없다.
    assert 'id="status-badge"' in response.text
    assert 'id="evidence-panel"' in response.text


def test_ui_static_assets_are_available() -> None:
    stylesheet = client.get("/static/styles.css")
    javascript = client.get("/static/app.js")

    assert stylesheet.status_code == 200
    assert "text/css" in stylesheet.headers["content-type"]
    assert "--primary" in stylesheet.text

    assert javascript.status_code == 200
    assert 'fetch("/api/ask"' in javascript.text
    # organizer 평가용 GET /answer는 UI에서 절대 호출하지 않는다. "/answer"라는
    # 경로 리터럴이 없어야 하며, "#answer-summary"·"response.answer"처럼 "answer"를
    # 포함하는 다른 식별자와는 슬래시 유무로 구분한다.
    assert "/answer" not in javascript.text


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
