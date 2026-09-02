"""HCX 표현 경로가 서비스 경계에 붙은 뒤의 응답 계약 테스트.

여기서 고정하는 것:
    - 외부 모델은 answer 문자열 하나만 바꾼다. 구조화 결과는 어느 경로로 답하든 같다.
    - 외부 모델이 답을 주지 못하면 응답이 연동 전과 완전히 같다.
    - 연동 실패가 정상 요청을 BLOCKED/FAILED로 바꾸지 않는다.
    - 거부·미해결 결과는 외부 모델을 아예 거치지 않는다.

망을 타지 않는다. verbalize_fn을 갈아 끼워 default_verbalize(설정을 읽고 클라이언트를 만드는
자리)를 지나가지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from disclosure_agent.agent.grounded_payload import GroundedPayload
from disclosure_agent.api.service import AgentAskService, default_verbalize
from disclosure_agent.common.config import HyperClovaSettings
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
    SectionScope,
    SummaryItemType,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    AnswerResponse,
    AskRequest,
    DocumentSummary,
    Evidence,
    Fact,
    SummaryItem,
)

QUESTION = "한미반도체 2025년 연결 기준 매출액이 얼마야?"

HCX_ANSWER = "한미반도체의 2025년 연결 기준 매출액은 576,684,619,527원으로 확인됩니다."

# 응답에서 answer를 뺀 나머지 전부. 경로가 달라져도 이 필드들은 같아야 한다.
STRUCTURED_CARRIERS = tuple(name for name in AnswerResponse.model_fields if name != "answer")


class RecordingVerbalize:
    """호출을 기록하고 미리 정한 결과를 돌려준다."""

    def __init__(self, outcome: str | None | Exception = None) -> None:
        self.outcome = outcome
        self.payloads: list[GroundedPayload] = []

    def __call__(self, payload: GroundedPayload) -> str | None:
        self.payloads.append(payload)

        if isinstance(self.outcome, Exception):
            raise self.outcome

        return self.outcome


def evidence() -> Evidence:
    return Evidence(
        evidence_id="ev_revenue",
        doc_id="doc_a",
        location_type=EvidenceLocationType.TABLE,
        raw_text="576,684,619,527",
    )


def answerable_result() -> AgentExecutionResult:
    fact = Fact(
        fact_id="fact_revenue",
        company="한미반도체",
        metric=MetricConcept.REVENUE,
        fiscal_year=2025,
        fiscal_period="FY",
        accounting_scope=FactAccountingScope.CONSOLIDATED,
        raw_value="576,684,619,527",
        raw_unit="원",
        normalized_value=576684619527,
        normalized_unit="KRW",
        evidence_ids=["ev_revenue"],
    )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[fact],
        evidence=[evidence()],
    )


def unanswerable_result() -> AgentExecutionResult:
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=AnswerabilityReason.NOT_FOUND,
    )


def summary_result() -> AgentExecutionResult:
    """
    결정론 경로는 답하고 payload 계층은 거부하는 결과(Pattern B).

    두 경로의 지원 범위가 갈리는 유일한 자리라, groundable 하지 않은 결과의 fallback을
    시험할 대조군이 된다.
    """
    summary = DocumentSummary(
        summary_id="sum_1",
        doc_id="doc_a",
        company="한미반도체",
        report_name="사업보고서",
        section_scope=SectionScope.FACILITY_INVESTMENT,
        section_path=["II. 사업의 내용", "4. 설비 투자"],
        items=[
            SummaryItem(
                item_id="item_1",
                item_type=SummaryItemType.PARAGRAPH,
                label="설비 투자 개요",
                evidence_ids=["ev_revenue"],
            )
        ],
    )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        document_summaries=[summary],
        evidence=[evidence()],
    )


def service(result: AgentExecutionResult, verbalize_fn: RecordingVerbalize) -> AgentAskService:
    def fake_answer_question(question: str, **_: object) -> AgentExecutionResult:
        assert question

        return result

    return AgentAskService(
        corpus_root=Path("unused-synthetic-corpus"),
        answer_question_fn=fake_answer_question,
        verbalize_fn=verbalize_fn,
    )


def ask(result: AgentExecutionResult, verbalize_fn: RecordingVerbalize) -> AnswerResponse:
    return service(result, verbalize_fn).answer(AskRequest(question=QUESTION))


def deterministic(result: AgentExecutionResult) -> AnswerResponse:
    """연동이 없을 때의 응답. 대조군이다."""
    return ask(result, RecordingVerbalize(None))


# --------------------------------------------------------------------------
# a. 채택
# --------------------------------------------------------------------------


def test_accepted_answer_replaces_only_the_sentence() -> None:
    verbalizer = RecordingVerbalize(HCX_ANSWER)

    response = ask(answerable_result(), verbalizer)

    assert response.answer == HCX_ANSWER
    assert len(verbalizer.payloads) == 1


def test_question_reaches_the_payload() -> None:
    """모델이 무엇을 물었는지 알아야 확보한 값 중 무엇을 앞세울지 정할 수 있다."""
    verbalizer = RecordingVerbalize(HCX_ANSWER)

    ask(answerable_result(), verbalizer)

    assert verbalizer.payloads[0].question == QUESTION


# --------------------------------------------------------------------------
# f. 구조화 결과 동일성
# --------------------------------------------------------------------------


def test_structured_carriers_are_identical_before_and_after_replacement() -> None:
    """
    바뀌는 것은 answer 하나뿐이다.

    필드 목록을 손으로 적지 않고 AnswerResponse에서 만든다. carrier가 늘었을 때 이 검사가
    따라가지 못하면, 새 carrier만 조용히 두 경로에서 갈린다.
    """
    result = answerable_result()

    control = deterministic(result)

    verbalized = ask(result, RecordingVerbalize(HCX_ANSWER))

    assert verbalized.answer != control.answer

    for carrier in STRUCTURED_CARRIERS:
        assert getattr(verbalized, carrier) == getattr(control, carrier), carrier

    assert verbalized.model_dump(exclude={"answer"}) == control.model_dump(exclude={"answer"})


def test_execution_status_stays_completed_on_every_path() -> None:
    """연동 실패가 정상 요청을 BLOCKED/FAILED로 바꾸지 않는다."""
    result = answerable_result()

    for outcome in (HCX_ANSWER, None, RuntimeError("boom")):
        response = ask(result, RecordingVerbalize(outcome))

        assert response.status is AnswerabilityStatus.ANSWERABLE
        assert response.facts == result.facts


# --------------------------------------------------------------------------
# b/d. 모델이 답을 주지 못한 경우
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome",
    [None, RuntimeError("transport exploded")],
    ids=["unavailable_or_rejected", "unexpected_error"],
)
def test_missing_verbalization_returns_the_deterministic_response(outcome: object) -> None:
    """
    비활성·장애·검증 실패의 대응이 모두 같다: 표현을 바꾸지 않는다.

    예상 못 한 예외도 같은 자리로 모은다. 여기서 예외가 올라가면 결정론 경로만으로 답할 수
    있었던 요청이 연동을 켰다는 이유만으로 500이 된다.
    """
    result = answerable_result()

    response = ask(result, RecordingVerbalize(outcome))  # type: ignore[arg-type]

    assert response == deterministic(result)


# --------------------------------------------------------------------------
# c. payload로 옮길 수 없는 결과
# --------------------------------------------------------------------------


def test_not_groundable_result_never_reaches_the_model() -> None:
    """Pattern B는 결정론 경로가 답한다. 요약을 뺀 나머지만 모델에 보내지 않는다."""
    result = summary_result()

    verbalizer = RecordingVerbalize(HCX_ANSWER)

    response = ask(result, verbalizer)

    assert verbalizer.payloads == []
    assert response == deterministic(result)
    assert "설비 투자" in response.answer


# --------------------------------------------------------------------------
# e. 표현 대상이 아닌 판정
# --------------------------------------------------------------------------


def test_refusal_answer_is_not_sent_to_the_model() -> None:
    """거부 문장은 사유별로 이미 확정돼 있다. 표현을 바꿀 여지를 두지 않는다."""
    result = unanswerable_result()

    verbalizer = RecordingVerbalize(HCX_ANSWER)

    response = ask(result, verbalizer)

    assert verbalizer.payloads == []
    assert response.status is AnswerabilityStatus.UNANSWERABLE
    assert response.reason is AnswerabilityReason.NOT_FOUND
    assert response.answer == deterministic(result).answer


# --------------------------------------------------------------------------
# 기본 seam
# --------------------------------------------------------------------------


def test_default_verbalize_makes_no_call_when_the_integration_is_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """자격 증명이 없는 것은 오류가 아니라 결정론 문장을 그대로 쓴다는 뜻이다."""
    from disclosure_agent.api import service as service_module

    monkeypatch.setattr(service_module, "_cached_chat_client", lambda: None)

    payload = object()

    assert default_verbalize(payload) is None  # type: ignore[arg-type]


def test_inactive_settings_produce_no_client() -> None:
    """default_verbalize가 기대는 판정이 설정 계층에 있는지 확인한다."""
    empty = HyperClovaSettings(_env_file=None, api_key="", api_url="", model="")

    assert not empty.is_active
