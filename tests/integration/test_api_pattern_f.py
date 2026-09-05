"""HTTP regressions for representative Pattern F questions."""

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

client = TestClient(app)
Q17 = "이번 수주 공시 나오고 나서 시장 반응은 어땠어?"


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


@pytest.mark.corpus
def test_q17_runs_through_actual_corpus_http_api() -> None:
    _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": Q17})

    assert response.status_code == 200
    actual = AnswerResponse.model_validate(response.json())
    assert actual.status is AnswerabilityStatus.UNANSWERABLE
    assert actual.reason == "out_of_scope_data"
    assert actual.facts == []
    assert actual.events == []
    assert actual.event_links == []
    assert actual.event_histories == []
    assert actual.resolved_event_states == []
    assert actual.evidence == []


# 실측 코퍼스의 /api/ask에서 매출 Fact를 붙여 ANSWERABLE로 돌아온 F3 우회 질문.
#
# 기업명·연도·지표가 살아 있어 사실 조회로 라우팅됐고, 투자 판단 요구('매수 결정해도',
# '투자해도 괜찮아', '사는 게 나아', '사야 해')는 어디에도 남지 않았다. Gate가 코퍼스를
# 읽기 전에 판정하므로, 없는 코퍼스 경로에서 Q18과 같은 응답이 나와야 한다.
@pytest.mark.parametrize(
    "question",
    [
        "한화오션 2025년 매출 보고 매수 결정해도 될까?",
        "삼성전자 2024년 매출 기준으로 투자해도 괜찮아?",
        "HD현대중공업이랑 삼성중공업 2025년 영업이익률 비교해서 어느 쪽 주식을 사는 게 나아?",
        "한화오션 2025년 매출 보고 사야 해?",
        # 같은 요구의 '~는 게' 어형. 어미 한 벌만 보던 규칙이 통과시키던 형태다.
        "삼성전자 2025년 매출 보고 비중을 늘리는 게 나아?",
        "한화오션 2025년 매출 보고 지금 들어가는 게 나아?",
    ],
    ids=[
        "buy-decision",
        "invest-okay",
        "compare-then-pick",
        "should-buy",
        "increase-weight",
        "enter-now",
    ],
)
def test_disguised_investment_advice_is_refused_before_corpus_access(
    question: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DISCLOSURE_CORPUS_ROOT", str(tmp_path / "unused-corpus"))
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": question})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "UNANSWERABLE"
    assert payload["reason"] == "out_of_scope_policy"
    assert payload["facts"] == []
    assert payload["evidence"] == []
    assert "execution_status" not in payload
    assert "error" not in payload


# 위 우회 질문에서 판단 요구만 뗀 사실 조회. 넓힌 F3 어휘가 사실 경로를 막지 않는지를
# 실제 코퍼스로 본다.
@pytest.mark.corpus
def test_plain_revenue_question_still_reaches_the_fact_path() -> None:
    _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": "한화오션 2025년 매출 알려줘"})

    assert response.status_code == 200
    actual = AnswerResponse.model_validate(response.json())
    assert actual.status is AnswerabilityStatus.ANSWERABLE
    assert actual.reason is None
    assert len(actual.facts) == 1

    fact = actual.facts[0]
    assert fact.metric is MetricConcept.REVENUE
    assert fact.fiscal_year == 2025
    assert actual.evidence


# Q16 실측: 위의 test_pattern_f_rejects_before_corpus_access(id=q16-out-of-data-range)는
# 존재하지 않는 corpus_root로 Gate가 retrieval 이전에 판정됨을 고정한다. 아래는 같은 질문을
# 실제 코퍼스로 물어, 실제 코퍼스가 있어도 판정이 달라지지 않는다는 것까지 고정한다 — F1
# Gate는 코퍼스를 읽지 않고 판정하므로(safety.py 모듈 docstring), 두 경로가 같은 결과를
# 내는 것 자체가 "코퍼스 접근 이전"이라는 설계 불변식의 증거다.
@pytest.mark.corpus
def test_q16_out_of_data_range_holds_against_the_actual_corpus() -> None:
    _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post(
        "/api/ask", json={"question": "삼성전자 2026년 상반기 실적 좀 알려줘."}
    )

    assert response.status_code == 200
    actual = AnswerResponse.model_validate(response.json())
    assert actual.status is AnswerabilityStatus.UNANSWERABLE
    assert actual.reason.value == "out_of_data_range"
    assert actual.facts == []
    assert actual.evidence == []
    assert actual.events == []
    assert actual.event_links == []
    assert actual.event_histories == []
    assert actual.resolved_event_states == []
    payload = response.json()
    assert "execution_status" not in payload
    assert "error" not in payload


@pytest.mark.corpus
def test_plain_company_comparison_still_reaches_the_comparison_path() -> None:
    _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post(
        "/api/ask",
        json={"question": "HD현대중공업이랑 삼성중공업 2025년 영업이익률 비교해줘"},
    )

    assert response.status_code == 200
    actual = AnswerResponse.model_validate(response.json())
    assert actual.status is AnswerabilityStatus.ANSWERABLE
    assert actual.reason is None
    assert {(fact.company, fact.metric, fact.fiscal_year) for fact in actual.facts} == {
        ("에이치디현대중공업(주)", MetricConcept.REVENUE, 2025),
        ("에이치디현대중공업(주)", MetricConcept.OPERATING_INCOME, 2025),
        ("삼성중공업(주)", MetricConcept.REVENUE, 2025),
        ("삼성중공업(주)", MetricConcept.OPERATING_INCOME, 2025),
    }
    assert actual.evidence
