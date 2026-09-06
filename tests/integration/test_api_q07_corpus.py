"""Actual-corpus HTTP regression for the representative Q07 question.

무엇을 고정하는가:
    질문 문자열 하나가 실제 코퍼스와 HTTP 경계를 지나 Q07 모양의 답으로 돌아온다.
    구조화 계층의 개수·참조는 tests/integration/test_pattern_c.py 5절이 이미 고정하고 있으며,
    여기서 더하는 것은 "실제 공시로도 그 경로가 끝까지 간다"이다.

연도를 고정하지 않는 이유:
    "작년"은 절대연도가 아니다. 질의 해석은 시각을 읽지 않고 해당 기업의 가장 최근 연간
    공시를 기준 연도로 삼으며(query_analyzer의 상대 기간 해석, ANCHOR_SOURCE_LATEST_ANNUAL_FILING),
    "작년"은 그 기준 연도와 그 직전 해를 가리킨다. 따라서 옮겨지는 두 해는 코퍼스가 무엇을
    담고 있느냐에 따라 달라진다.

    여기에 2024·2025를 적어 두면 코퍼스에 2026년 사업보고서가 들어오는 날 이 테스트가
    깨진다. 그때 깨지는 것은 안전 불변식이 아니라 코퍼스의 현재 내용이다. 그래서 두 해가
    무엇인지 대신 두 해가 연속한 두 회계연도라는 관계를 고정한다.
    기준 연도 규칙 자체는 tests/unit/test_relative_period_anchor.py가 맡는다.

수치를 고정하지 않는 이유:
    증감액과 증감률의 실제 값은 코퍼스를 읽어야 알 수 있고, 이 테스트를 작성한 환경에는
    실측 코퍼스가 없었다. 읽지 않은 값을 적으면 그것은 확인한 값이 아니라 지어낸 값이다.
    값을 pin하려면 먼저 실측 코퍼스에서 읽어야 한다.

Calculation을 검사하지 않는 이유:
    AnswerResponse에 calculations 자리가 없다(common.schemas.AnswerResponse). HTTP 경계에서
    증감 수치가 드러나는 곳은 문장뿐이므로, 여기서는 두 수치가 문장에 실렸다는 것까지만 본다.
    Calculation 2건의 연산·참조·근거는 구조화 계층에서 test_pattern_c.py가 고정한다.
"""

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

# 대표 질문 원문. evaluation/questions.jsonl의 표기와 같아야 한다.
Q07 = "한미반도체 매출이 작년보다 얼마나 늘었어?"

EXPECTED_FACT_COUNT = 2

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


def _ask(question: str) -> AnswerResponse:
    response = client.post("/api/ask", json={"question": question})

    assert response.status_code == 200

    return AnswerResponse.model_validate(response.json())


@pytest.fixture
def q07_response() -> AnswerResponse:
    _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    return _ask(Q07)


@pytest.mark.corpus
def test_q07_is_answerable_through_the_actual_corpus_http_api(
    q07_response: AnswerResponse,
) -> None:
    assert q07_response.status is AnswerabilityStatus.ANSWERABLE
    assert q07_response.reason is None


@pytest.mark.corpus
def test_q07_carries_two_revenue_facts_for_one_company(
    q07_response: AnswerResponse,
) -> None:
    assert len(q07_response.facts) == EXPECTED_FACT_COUNT

    assert {fact.metric for fact in q07_response.facts} == {MetricConcept.REVENUE}
    assert len({fact.company for fact in q07_response.facts}) == 1


@pytest.mark.corpus
def test_q07_facts_cover_two_consecutive_fiscal_years(
    q07_response: AnswerResponse,
) -> None:
    """
    "작년"이 옮겨진 결과다. 어느 해인지는 코퍼스가 정하고, 두 해가 이웃한다는 것은 표현이
    정한다. 한 해만 남거나 두 해가 떨어져 있으면 질문이 가리킨 기간이 아니다.
    """
    years = sorted(fact.fiscal_year for fact in q07_response.facts)

    assert None not in years
    assert len(set(years)) == EXPECTED_FACT_COUNT
    assert years[1] == years[0] + 1


@pytest.mark.corpus
def test_q07_facts_share_one_accounting_scope(q07_response: AnswerResponse) -> None:
    """연결과 별도를 섞어도 그럴듯한 증감이 나온다. guard②가 막은 것이 여기까지 온다."""
    assert len({fact.accounting_scope for fact in q07_response.facts}) == 1


@pytest.mark.corpus
def test_q07_fact_evidence_references_resolve(q07_response: AnswerResponse) -> None:
    """근거가 끊긴 참조가 응답에 실리지 않는다."""
    assert q07_response.evidence

    available = {item.evidence_id for item in q07_response.evidence}

    for fact in q07_response.facts:
        assert fact.evidence_ids
        assert set(fact.evidence_ids) <= available


@pytest.mark.corpus
def test_q07_carries_no_other_pattern_payload(q07_response: AnswerResponse) -> None:
    """
    Q07은 사건도 이력도 만들지 않는다.

    ComparisonResult도 만들지 않지만 AnswerResponse에 그 자리가 없어 여기서는 볼 수 없다.
    우열 판정을 만들지 않는다는 것은 test_pattern_c.py가 고정한다.
    """
    assert q07_response.events == []
    assert q07_response.event_links == []
    assert q07_response.event_histories == []


@pytest.mark.corpus
def test_q07_answer_states_both_periods_and_both_changes(
    q07_response: AnswerResponse,
) -> None:
    """
    문장이 비어 있지 않고, 질문이 요구한 수치 두 건이 모두 실린다.

    값 자체는 고정하지 않는다. 실측 코퍼스에서 읽지 않은 수치를 적으면 확인한 값이 아니다.
    """
    answer = q07_response.answer

    assert answer.strip()

    for fact in q07_response.facts:
        assert str(fact.fiscal_year) in answer

    assert "증감액" in answer
    assert "증감률" in answer


@pytest.mark.corpus
def test_q07_answer_is_deterministic(q07_response: AnswerResponse) -> None:
    """같은 질문을 두 번 물으면 같은 문장이 나온다."""
    assert _ask(Q07).answer == q07_response.answer
