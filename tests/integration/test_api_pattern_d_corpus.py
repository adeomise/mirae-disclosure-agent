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
Q12 = (
    "2024년에 LG이노텍하고 레인보우로보틱스가 새로 발표한 시설투자, "
    "회사별로 다 합치면 어디가 더 커?"
)

Q10_FACT_IDENTITIES = {
    ("에이치디현대중공업(주)", "revenue", 2025),
    ("삼성중공업(주)", "revenue", 2025),
}
Q11_FACT_IDENTITIES = {
    ("에이치디현대중공업(주)", "revenue", 2025),
    ("에이치디현대중공업(주)", "operating_income", 2025),
    ("삼성중공업(주)", "revenue", 2025),
    ("삼성중공업(주)", "operating_income", 2025),
}

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
    expected_fact_identities: set[tuple[str, str, int]],
) -> None:
    actual = AnswerResponse.model_validate(response_payload)

    assert actual.status is AnswerabilityStatus.ANSWERABLE
    assert actual.reason is None
    assert len(actual.facts) == expected_fact_count
    assert {
        (fact.company, fact.metric.value, fact.fiscal_year) for fact in actual.facts
    } == expected_fact_identities
    assert actual.evidence

    response_evidence_ids = {item.evidence_id for item in actual.evidence}
    for fact in actual.facts:
        assert fact.evidence_ids
        assert set(fact.evidence_ids) <= response_evidence_ids


@pytest.mark.corpus
@pytest.mark.parametrize(
    ("question", "expected_fact_count", "expected_fact_identities"),
    [
        pytest.param(Q10, 2, Q10_FACT_IDENTITIES, id="q10-revenue-comparison"),
        pytest.param(Q11, 4, Q11_FACT_IDENTITIES, id="q11-operating-margin-comparison"),
    ],
)
def test_pattern_d_questions_run_through_actual_corpus_http_api(
    question: str,
    expected_fact_count: int,
    expected_fact_identities: set[tuple[str, str, int]],
) -> None:
    _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": question})

    assert response.status_code == 200
    _assert_answerable_with_resolved_fact_evidence(
        response.json(),
        expected_fact_count=expected_fact_count,
        expected_fact_identities=expected_fact_identities,
    )


@pytest.mark.corpus
def test_exact_q12_runs_through_actual_corpus_http_api() -> None:
    _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": Q12})

    assert response.status_code == 200
    actual = AnswerResponse.model_validate(response.json())
    assert actual.status is AnswerabilityStatus.ANSWERABLE
    assert actual.reason is None
    assert len(actual.facts) == 3
    assert [fact.normalized_value for fact in actual.facts] == [
        "383000000000",
        "375900000000",
        "28533520000",
    ]
    evidence_by_id = {item.evidence_id: item for item in actual.evidence}
    assert all(
        fact.evidence_ids and set(fact.evidence_ids) <= evidence_by_id.keys()
        for fact in actual.facts
    )
    rainbow = next(fact for fact in actual.facts if fact.company == "레인보우로보틱스")
    assert rainbow.evidence_ids == [
        "evidence_exchange_20251205900454_investment_amount"
    ]
    assert evidence_by_id[rainbow.evidence_ids[0]].doc_id == "exchange_20251205900454"
    assert "LG이노텍의 시설투자 합계는 758900000000원" in actual.answer
    assert "레인보우로보틱스의 시설투자 합계는 28533520000원" in actual.answer
    assert "LG이노텍 쪽이 더 큽니다" in actual.answer
