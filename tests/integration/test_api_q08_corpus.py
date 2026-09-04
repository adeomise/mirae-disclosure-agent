"""Actual-corpus HTTP regression for the representative Q08 question.

무엇을 고정하는가:
    질문 문자열 하나가 실제 코퍼스와 HTTP 경계를 지나 Q08 모양의 답으로 돌아온다.
    구조화 계층의 개수·참조는 tests/integration/test_pattern_c.py가 이미 고정하고 있으며,
    여기서 더하는 것은 "실제 공시로도 그 경로가 끝까지 간다"이다.

    이 파일이 생긴 이유는 그 마지막 구간이 실제로 끊겨 있었기 때문이다. Agent는 COMPLETED /
    ANSWERABLE에 Fact 4건과 Calculation 3건을 실어 돌려주는데, Answer Builder에 그 모양의
    문장이 없어 다중 Fact 가드에서 멈췄고 API가 500(unexpected_failure)을 냈다. 구조화 계층만
    보는 테스트로는 그 정지가 드러나지 않는다.

연도를 고정하는 이유:
    Q07과 다른 자리다. Q07의 "작년"은 코퍼스 내용에 따라 옮겨지는 해가 달라지지만, Q08은
    질문이 2024·2025를 직접 적는다. 여기서 고정하는 것은 코퍼스의 현재 내용이 아니라 질문이
    가리킨 기간이다.

수치를 고정하지 않는 이유:
    영업이익률과 변화량의 실제 값은 실측 공시에서 나온다. 실제 원문 데이터를 코드에 적지
    않는다(AGENTS.md). 그래서 값 대신 "두 비율과 변화량이 문장에 실렸다"는 것을 단위 표기의
    개수로 본다.

Calculation을 검사하지 않는 이유:
    AnswerResponse에 calculations 자리가 없다(common.schemas.AnswerResponse). HTTP 경계에서
    파생 수치가 드러나는 곳은 문장뿐이므로, 여기서는 그 수치들이 문장에 실렸다는 것까지만
    본다. Calculation 3건의 연산·참조·근거는 구조화 계층에서 test_pattern_c.py가 고정한다.
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
Q08 = "한미반도체 영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?"

# 질문이 직접 적은 두 회계연도.
EXPECTED_FISCAL_YEARS = {2024, 2025}

# 영업이익률은 원문에 없는 파생지표이고, 두 해분을 만들려면 분자·분모를 해마다 확보해야 한다
# (agent.derived_metrics의 operating_margin = operating_income / revenue).
EXPECTED_FACT_COUNT = 4

# 두 기간의 비율 2건과 그 변화량 1건. 앞의 둘은 %, 마지막은 %p로 적힌다.
EXPECTED_PERCENT_SIGN_COUNT = 3

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
def q08_response() -> AnswerResponse:
    _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    return _ask(Q08)


@pytest.mark.corpus
def test_q08_is_answerable_through_the_actual_corpus_http_api(
    q08_response: AnswerResponse,
) -> None:
    """이 단언이 회귀의 자리다. 고치기 전에는 같은 요청이 500으로 끝났다."""
    assert q08_response.status is AnswerabilityStatus.ANSWERABLE
    assert q08_response.reason is None


@pytest.mark.corpus
def test_q08_carries_both_source_metrics_for_both_years(
    q08_response: AnswerResponse,
) -> None:
    """
    영업이익률은 공시에 적혀 있지 않다. 응답에 실리는 것은 그 비율을 만든 원천 Fact다.

    해마다 분자와 분모가 모두 있어야 한다. 한쪽이 빠진 해가 있으면 그 해의 비율은 만들어질 수
    없고, 그런데도 답이 나왔다면 어딘가에서 값을 지어낸 것이다.
    """
    assert len(q08_response.facts) == EXPECTED_FACT_COUNT

    by_year: dict[int | None, set[MetricConcept]] = {}

    for fact in q08_response.facts:
        by_year.setdefault(fact.fiscal_year, set()).add(fact.metric)

    assert set(by_year) == EXPECTED_FISCAL_YEARS

    for metrics in by_year.values():
        assert metrics == {MetricConcept.REVENUE, MetricConcept.OPERATING_INCOME}


@pytest.mark.corpus
def test_q08_facts_share_one_company(q08_response: AnswerResponse) -> None:
    """시계열 비교는 같은 기업의 두 기간을 견준다."""
    assert len({fact.company for fact in q08_response.facts}) == 1


@pytest.mark.corpus
def test_q08_facts_share_one_accounting_scope(q08_response: AnswerResponse) -> None:
    """연결과 별도를 섞어도 그럴듯한 비율이 나온다. guard①·guard②가 막은 것이 여기까지 온다."""
    assert len({fact.accounting_scope for fact in q08_response.facts}) == 1


@pytest.mark.corpus
def test_q08_fact_evidence_references_resolve(q08_response: AnswerResponse) -> None:
    """근거가 끊긴 참조가 응답에 실리지 않는다."""
    assert q08_response.evidence

    available = {item.evidence_id for item in q08_response.evidence}

    for fact in q08_response.facts:
        assert fact.evidence_ids
        assert set(fact.evidence_ids) <= available


@pytest.mark.corpus
def test_q08_carries_no_other_pattern_payload(q08_response: AnswerResponse) -> None:
    """Q08은 사건도 이력도 만들지 않는다."""
    assert q08_response.events == []
    assert q08_response.event_links == []
    assert q08_response.event_histories == []


@pytest.mark.corpus
def test_q08_answer_states_both_years_both_margins_and_the_change(
    q08_response: AnswerResponse,
) -> None:
    """
    문장이 비어 있지 않고, 질문이 요구한 세 수치가 모두 실린다.

    값 자체는 고정하지 않는다(모듈 docstring). 대신 단위 표기로 센다. 비율 2건은 %로, 변화량은
    %p로 적히므로 %는 세 번 나온다. 한 기간의 비율만 적히거나 변화량이 빠지면 그 수가 달라진다.
    """
    answer = q08_response.answer

    assert answer.strip()

    for fiscal_year in EXPECTED_FISCAL_YEARS:
        assert f"{fiscal_year}년" in answer

    assert "영업이익률" in answer
    assert "두 연도의 차이" in answer
    assert "%p" in answer
    assert answer.count("%") == EXPECTED_PERCENT_SIGN_COUNT


@pytest.mark.corpus
def test_q08_change_reaches_http_in_percentage_points(
    q08_response: AnswerResponse,
) -> None:
    """
    HTTP 경계에서도 차이의 단위 축이 비율과 갈려 있다.

    AnswerResponse에 calculations 자리가 없어(모듈 docstring) 단위가 드러나는 곳은 문장의
    마지막 구절뿐이다. 여기가 %로 되돌아가면 "영업이익률이 그만큼 % 달라졌다"는 다른 사실이
    공개 응답으로 나간다. 값은 고정하지 않고 그 자리의 표기만 본다.
    """
    assert q08_response.answer.endswith("%p입니다.")


@pytest.mark.corpus
def test_q08_answer_does_not_explain_why_the_margin_changed(
    q08_response: AnswerResponse,
) -> None:
    """
    변화의 원인은 확보한 결과에 없다.

    수치 네 건과 계산 세 건 어디에도 원인이 담겨 있지 않으므로, 원인을 적으면 근거 없는 서술이
    답변에 실린다.
    """
    for invented in ("때문", "영향", "덕분", "원인"):
        assert invented not in q08_response.answer


@pytest.mark.corpus
def test_q08_answer_is_deterministic(q08_response: AnswerResponse) -> None:
    """같은 질문을 두 번 물으면 같은 문장이 나온다."""
    assert _ask(Q08).answer == q08_response.answer
