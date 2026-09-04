"""Actual-corpus HTTP E2E coverage for representative question Q06.

여기서 문서 식별자를 적지 않는다:
    접수번호를 적으면 이 테스트가 그 문서 하나에만 통하고, 코퍼스가 바뀌면 왜 깨졌는지
    알기 어려워진다. 무엇보다 그것은 구현이 내용으로 문서를 골랐는지 확인하지 못한다.
    그래서 확인하는 것은 "질문에 적힌 주제가 고른 공시의 본문에 실제로 있는가"다
    (AGENTS.md의 실제 접수번호 비커밋 규칙과도 같은 방향이다).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.common.enums import (
    AnswerabilityStatus,
    EventType,
)
from disclosure_agent.common.schemas import AnswerResponse

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"

Q06 = "LG유플러스 파주 AIDC 투자 공시 내용 요약해줘."
Q06_ORIGINAL_STATE = "LG유플러스 파주 AIDC 투자 원공시 당시 내용 요약해줘."

Q06_COMPANY = "LG유플러스"
Q06_SUBJECT_TOKENS = ("파주", "AIDC")

REQUIRED_SUMMARY_FIELDS = (
    "investment_target",
    "investment_amount",
    "investment_purpose",
    "investment_start_date",
    "investment_end_date",
)

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


def ask(question: str) -> AnswerResponse:
    require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": question})

    assert response.status_code == 200

    return AnswerResponse.model_validate(response.json())


@pytest.mark.corpus
def test_q06_runs_through_actual_corpus_http_api() -> None:
    """기간을 밝히지 않은 Q06이 실제 코퍼스에서 끝까지 답해진다."""
    actual = ask(Q06)

    assert actual.status is AnswerabilityStatus.ANSWERABLE
    assert actual.reason is None
    assert len(actual.resolved_event_states) == 1
    assert len(actual.events) == 1

    state = actual.resolved_event_states[0]

    assert state.company == Q06_COMPANY
    assert state.event_type is EventType.FACILITY_INVESTMENT


@pytest.mark.corpus
def test_q06_surfaces_every_required_summary_field_with_evidence() -> None:
    """필수 요약 항목이 모두 값과 근거를 갖는다."""
    actual = ask(Q06)

    state = actual.resolved_event_states[0]
    evidence_ids = {item.evidence_id for item in actual.evidence}

    assert actual.evidence

    for field_name in REQUIRED_SUMMARY_FIELDS:
        resolved = state.fields[field_name]

        assert resolved.value is not None
        assert str(resolved.value).strip()
        assert resolved.evidence_ids
        assert set(resolved.evidence_ids) <= evidence_ids


@pytest.mark.corpus
def test_q06_selects_the_event_named_by_the_question() -> None:
    """고른 공시의 본문이 질문의 주제어를 담고 있다.

    선택이 질문 내용에서 왔다는 것을 결과 쪽에서 확인한다. 접수번호로 골랐다면 이 대조는
    성립하지 않아도 통과할 수 있다.
    """
    actual = ask(Q06)

    state = actual.resolved_event_states[0]

    subject_text = " ".join(
        str(state.fields[field_name].value or "")
        for field_name in ("investment_target", "investment_purpose")
    )

    for token in Q06_SUBJECT_TOKENS:
        assert token in subject_text


@pytest.mark.corpus
def test_q06_reports_the_amount_in_the_answer_sentence() -> None:
    """확정한 값이 문장에도 실린다. 구조화 결과에만 있으면 문장만 읽는 쪽은 답을 못 받는다."""
    actual = ask(Q06)

    state = actual.resolved_event_states[0]
    amount = state.fields["investment_amount"].value

    assert state.fields["investment_amount"].unit == "KRW"
    assert str(int(float(str(amount)))) in actual.answer
    assert str(state.fields["investment_start_date"].value) in actual.answer
    assert str(state.fields["investment_end_date"].value) in actual.answer


@pytest.mark.corpus
def test_q06_original_state_wording_runs_through_the_same_event_chain() -> None:
    """원공시 표현도 같은 이벤트를 가리킨다.

    이 코퍼스의 해당 체인은 원공시 1건뿐이라 두 시점의 값이 같다. 그것이 정상이며, 확인할
    것은 두 질문이 서로 다른 이벤트로 갈라지지 않는다는 사실이다. 두 시점이 실제로 갈리는
    경우는 tests/unit/test_q06_event_summary_handler.py가 고정한다.
    """
    latest = ask(Q06)
    original = ask(Q06_ORIGINAL_STATE)

    assert original.status is AnswerabilityStatus.ANSWERABLE
    assert original.reason is None
    assert len(original.resolved_event_states) == 1

    assert original.resolved_event_states[0].event_id == latest.resolved_event_states[0].event_id
    assert original.resolved_event_states[0].as_of == latest.resolved_event_states[0].as_of


@pytest.mark.corpus
def test_q06_does_not_disturb_the_yearless_periodic_summary_policy() -> None:
    """연도를 밝히지 않은 정기공시 요약은 계속 실행 전에 막힌다.

    Q06의 완화가 Pattern B로 새지 않는지를 실제 실행 경로에서 확인한다.
    """
    require_actual_corpus()

    response = client.post(
        "/api/ask",
        json={"question": "삼성바이오로직스 사업보고서에서 설비 투자 관련 내용 정리해줘."},
    )

    assert response.status_code == 422
    assert response.json()["error"]["reason"] == "missing_year"
