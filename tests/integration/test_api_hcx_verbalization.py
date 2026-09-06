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

from datetime import date
from pathlib import Path

import pytest

from disclosure_agent.agent.answer_builder import UnsupportedResultShapeError
from disclosure_agent.agent.grounded_payload import GroundedPayload
from disclosure_agent.api.service import AgentAskService, default_verbalize
from disclosure_agent.common.config import HyperClovaSettings
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    EventType,
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
    EventHistory,
    Evidence,
    Fact,
    FieldChange,
    ResolvedEventState,
    ResolvedFieldValue,
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
    """두 경로 모두 답하는 문서 요약 결과(Pattern B)."""
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


def event_summary_result() -> AgentExecutionResult:
    """
    결정론 경로는 답하고 payload 계층은 거부하는 결과(Q06 이벤트 단건 요약).

    답은 ResolvedEventState의 필드 값들이고 payload에는 그것을 담을 자리가 없다. 두 경로의
    지원 범위가 갈리는 자리라, groundable 하지 않은 결과의 fallback을 시험할 대조군이 된다.
    """
    state = ResolvedEventState(
        event_id="evt_facility",
        event_type=EventType.FACILITY_INVESTMENT,
        company="한미반도체",
        as_of=date(2025, 4, 29),
        fields={
            "investment_target": ResolvedFieldValue(
                value="파주 AIDC 신규 구축",
                source_doc_id="doc_a",
                evidence_ids=["ev_revenue"],
            )
        },
    )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        resolved_event_states=[state],
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
# 누적 기간
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fiscal_period,period_label",
    [
        ("3M YTD", "1분기 누적"),
        ("6M YTD", "반기 누적"),
        ("9M YTD", "3분기 누적"),
    ],
)
def test_a_cumulative_fact_reaches_the_model_with_its_span(
    fiscal_period: str, period_label: str
) -> None:
    """분기·반기 값은 payload로 나가되 기간을 달고 나간다.

    이전에는 아예 나가지 않았다(연간 외 기간은 넘기지 않음). 그 규칙이 막던 것은 '연도만 실린
    분기 값'이고, 기간을 함께 실으면 같은 실패가 생기지 않는다.
    """
    result = answerable_result()
    result.facts[0].fiscal_period = fiscal_period

    verbalizer = RecordingVerbalize(None)

    ask(result, verbalizer)

    [payload] = verbalizer.payloads

    assert payload.facts[0].fiscal_year == 2025
    assert payload.facts[0].period_label == period_label
    assert period_label in payload.to_json()


def test_a_cumulative_result_falls_back_to_a_sentence_that_states_the_span() -> None:
    """모델이 답하지 못해도 사용자가 받는 문장은 기간을 말한다."""
    result = answerable_result()
    result.facts[0].fiscal_period = "6M YTD"

    response = ask(result, RecordingVerbalize(None))

    assert response.status is AnswerabilityStatus.ANSWERABLE
    assert "2025년 반기 누적" in response.answer


def test_a_cumulative_fact_outside_the_single_lookup_shape_never_reaches_the_model() -> None:
    """
    누적 기간이 열린 것은 단일 Fact 조회뿐이고, 두 경로가 같은 자리에서 멈춘다.

    한쪽만 열려 있으면 결정론 경로가 답하지 못하는 결과가 연동을 켰다는 이유만으로 답해진다.
    payload 계층의 규칙이 그것을 금지한다(모듈 docstring).
    """
    result = answerable_result()

    second = result.facts[0].model_copy(
        update={"fact_id": "fact_revenue_rival", "company": "경쟁사"}
    )

    result.facts.append(second)

    for fact in result.facts:
        fact.fiscal_period = "6M YTD"

    verbalizer = RecordingVerbalize(HCX_ANSWER)

    with pytest.raises(UnsupportedResultShapeError, match="누적 기간"):
        ask(result, verbalizer)

    assert verbalizer.payloads == []


# --------------------------------------------------------------------------
# c. payload로 옮길 수 없는 결과
# --------------------------------------------------------------------------


def test_not_groundable_result_never_reaches_the_model() -> None:
    """이벤트 단건 요약은 결정론 경로가 답한다. 상태를 뺀 나머지만 모델에 보내지 않는다."""
    result = event_summary_result()

    verbalizer = RecordingVerbalize(HCX_ANSWER)

    response = ask(result, verbalizer)

    assert verbalizer.payloads == []
    assert response == deterministic(result)
    assert "파주 AIDC 신규 구축" in response.answer


def test_document_summary_reaches_the_model_with_its_items() -> None:
    """Pattern B는 이제 표현 단계까지 간다. 요약 항목이 payload에 그대로 실린다.

    실리지 않던 동안 "무슨 사업을 하느냐"의 답은 범위 이름과 건수뿐이었다.
    """
    result = summary_result()

    verbalizer = RecordingVerbalize(HCX_ANSWER)

    ask(result, verbalizer)

    [payload] = verbalizer.payloads

    [summary] = payload.document_summaries

    assert [item.content for item in summary.items] == ["설비 투자 개요"]


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
# 새로 열린 모양의 fallback
#
# 문서 요약(Pattern B)과 정정 이력(Pattern E)은 이제 표현 단계까지 간다. 그 두 모양에서도
# 연동이 답을 주지 못했을 때의 응답이 연동을 붙이기 전과 같아야 한다. 열어 놓은 경로가
# 늘어난 만큼 실패했을 때 무너지는 자리도 늘기 때문이다.
# --------------------------------------------------------------------------


def history_result() -> AgentExecutionResult:
    """정정 이력 결과. 변경 항목과 그 전후 값을 담는다."""
    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        event_histories=[
            EventHistory(
                event_id="evt_contract",
                document_ids=["doc_a", "doc_b"],
                changes=[
                    FieldChange(
                        field_name="contract_end_date",
                        before="2029-02-28",
                        after="2029-09-30",
                    )
                ],
                chain_complete=True,
            )
        ],
        evidence=[evidence()],
    )


@pytest.mark.parametrize(
    "outcome",
    [
        None,
        RuntimeError("transport exploded"),
        ValueError("malformed response body"),
    ],
    ids=["inactive_or_rejected", "transport_error", "malformed_response"],
)
@pytest.mark.parametrize(
    "build",
    [summary_result, history_result],
    ids=["document_summary", "event_history"],
)
def test_newly_opened_shapes_fall_back_to_the_deterministic_answer(
    build: object,
    outcome: object,
) -> None:
    """실패의 종류가 무엇이든 응답은 결정론 경로의 것과 완전히 같다."""
    result = build()  # type: ignore[operator]

    response = ask(result, RecordingVerbalize(outcome))  # type: ignore[arg-type]

    control = deterministic(result)

    assert response == control
    assert response.status is AnswerabilityStatus.ANSWERABLE

    for carrier in STRUCTURED_CARRIERS:
        assert getattr(response, carrier) == getattr(control, carrier), carrier


@pytest.mark.parametrize(
    "build",
    [summary_result, history_result],
    ids=["document_summary", "event_history"],
)
def test_newly_opened_shapes_replace_only_the_sentence(build: object) -> None:
    """채택된 경우에도 바뀌는 것은 answer 하나뿐이다."""
    result = build()  # type: ignore[operator]

    control = deterministic(result)

    response = ask(result, RecordingVerbalize(HCX_ANSWER))

    assert response.answer == HCX_ANSWER
    assert response.model_dump(exclude={"answer"}) == control.model_dump(exclude={"answer"})


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
