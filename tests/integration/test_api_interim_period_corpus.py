"""Actual-corpus HTTP regression for interim (cumulative) period answers.

무엇을 고정하는가:
    분기보고서·반기보고서의 연결 매출액 질문이 실제 코퍼스와 HTTP 경계를 지나 200으로
    돌아오고, 그 문장이 값의 누적 구간을 말한다. 사업보고서의 연간 답은 그대로다.

    이 자리는 500이었다. 기간 파이프라인은 ANSWERABLE까지 정상으로 끝나는데
    (test_periodic_ytd_corpus.py가 그 구간을 고정한다) 문장을 만드는 자리가 연간 외 기간을
    거절해, 답이 있는 요청이 서버 오류로 나갔다.

수치를 고정하지 않는 이유:
    실제 매출액은 공시 원문의 값이고, 이 저장소에 적지 않는다(AGENTS.md). 값이 옮겨졌는지는
    "문장에 값이 있다"까지만 보고, 어느 셀에서 왔는지는 test_periodic_ytd_corpus.py가 원문
    대조로 고정한다.

    대신 네 기간이 서로 다른 답으로 돌아온다는 것을 본다. 누적 값이 연간 값과 같은 문장으로
    수렴하면 그것이 이 회귀의 실패 모양이며, 값 없이도 그 사실은 드러난다.

두 경로를 함께 보는 이유:
    답변 문장은 결정론 경로 또는 HCX 경로에서 나온다. 어느 쪽으로 답하든 누적 구간이 문장에
    있어야 하므로, 기본 DI(환경에 따라 HCX가 붙을 수 있다)로 한 번, 결정론 경로를 고정해
    문장을 그대로 대조하는 것으로 한 번 본다.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.api.service import AgentAskService
from disclosure_agent.common.enums import AnswerabilityStatus, MetricConcept
from disclosure_agent.common.schemas import AnswerResponse

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"

COMPANY = "삼성전자"

# 질문 원문과 그 답이 말해야 하는 기간 구절.
#
# 사업보고서는 연간 값이라 붙일 누적 구간이 없다(period 없음). 나머지 셋은 각각 1~3월,
# 1~6월, 1~9월 누적이며, 그 구간이 문장에서 빠지면 한 해 전체의 실적으로 읽힌다.
INTERIM_CASES = [
    ("삼성전자 2025년 1분기보고서 연결 매출액 알려줘", "2025년 1분기 누적", "3M YTD"),
    ("삼성전자 2025년 반기보고서 연결 매출액 알려줘", "2025년 반기 누적", "6M YTD"),
    ("삼성전자 2025년 3분기보고서 연결 매출액 알려줘", "2025년 3분기 누적", "9M YTD"),
]

ANNUAL_QUESTION = "삼성전자 2025년 사업보고서 연결 매출액 알려줘"

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


def _corpus_paths() -> tuple[Path, Path | None]:
    corpus_root = Path(os.environ[CORPUS_ROOT_ENV]).expanduser()
    raw_manifest = os.environ.get(CORPUS_MANIFEST_ENV)

    return corpus_root, Path(raw_manifest).expanduser() if raw_manifest else None


def _deterministic_service() -> AgentAskService:
    """실제 코퍼스를 읽되 문장은 결정론 경로가 만든다.

    verbalize_fn을 None으로 고정하면 default_verbalize를 지나지 않으므로, 로컬 .env에
    HyperCLOVA 자격 증명이 있든 없든 같은 문장이 나온다. 문자열을 그대로 대조하려면 문장을
    만드는 경로가 하나여야 한다.
    """
    corpus_root, manifest_path = _corpus_paths()

    return AgentAskService(
        corpus_root=corpus_root,
        manifest_path=manifest_path,
        verbalize_fn=lambda _payload: None,
    )


def _ask(question: str) -> AnswerResponse:
    response = client.post("/api/ask", json={"question": question})

    assert response.status_code == 200, response.text

    return AnswerResponse.model_validate(response.json())


@pytest.fixture
def deterministic_path():
    """결정론 경로를 고정한다. 되돌리기까지가 이 fixture의 일이다."""
    _require_actual_corpus()

    app.dependency_overrides[get_ask_service] = _deterministic_service

    yield

    app.dependency_overrides.pop(get_ask_service, None)


@pytest.mark.corpus
@pytest.mark.parametrize(
    "question,period_phrase,fiscal_period",
    INTERIM_CASES,
    ids=["q1", "h1", "q3"],
)
def test_interim_question_is_answerable_through_the_actual_corpus_http_api(
    question: str, period_phrase: str, fiscal_period: str
) -> None:
    """500이 아니라 200이고, 그 답이 누적 값의 답이다."""
    _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = _ask(question)

    assert response.status is AnswerabilityStatus.ANSWERABLE
    assert response.reason is None

    [fact] = response.facts

    assert fact.company == COMPANY
    assert fact.metric is MetricConcept.REVENUE
    assert fact.fiscal_year == 2025
    assert fact.fiscal_period == fiscal_period


@pytest.mark.corpus
@pytest.mark.parametrize(
    "question,period_phrase,fiscal_period",
    INTERIM_CASES,
    ids=["q1", "h1", "q3"],
)
def test_the_interim_answer_states_its_cumulative_span(
    deterministic_path, question: str, period_phrase: str, fiscal_period: str
) -> None:
    """이 회귀의 본체. 누적 값이 한 해 전체의 값으로 적히지 않는다."""
    answer = _ask(question).answer

    assert period_phrase in answer
    assert answer.startswith(f"{COMPANY}의 {period_phrase} 연결 기준 매출액은 ")
    assert f"{COMPANY}의 2025년 연결" not in answer


@pytest.mark.corpus
def test_the_annual_answer_keeps_its_wording(deterministic_path) -> None:
    """사업보고서의 답은 이 변경 이전과 같다. 연간 값에는 붙일 누적 구간이 없다."""
    answer = _ask(ANNUAL_QUESTION).answer

    assert answer.startswith(f"{COMPANY}의 2025년 연결 기준 매출액은 ")
    assert "누적" not in answer


@pytest.mark.corpus
def test_the_four_periods_do_not_collapse_into_one_answer(deterministic_path) -> None:
    """
    네 기간이 서로 다른 답으로 돌아온다.

    수치를 적지 않고도 확인되는 것이 이것이다. 누적 값 셋 중 하나라도 연간 답과 같은 문장이
    되면 그 기간은 문장에서 사라진 것이고, 그것이 이 회귀가 막는 실패다.
    """
    answers = [_ask(question).answer for question, _, _ in INTERIM_CASES]

    answers.append(_ask(ANNUAL_QUESTION).answer)

    assert len(set(answers)) == len(answers)
