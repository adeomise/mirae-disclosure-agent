"""모델 응답 검증 테스트.

여기서 고정하는 것:
    - 검증을 통과하지 못한 응답은 예외가 아니라 None이 된다(결정론 문장으로 돌아간다).
    - 거절한 응답을 다시 묻지 않는다. 다시 물으면 검증이 표본 추출이 된다.
    - payload에 없는 수치·기업·회계기준이 문장에 들어오면 막는다.
    - 확보한 값이 문장에서 빠져도 막는다.
    - 응답 본문은 로그에 남지 않는다.

payload는 build_grounded_payload로 만든다. 손으로 조립하면 실제 Contract에서 나올 수 없는
모양을 검증하게 된다.
"""

from __future__ import annotations

import json
import logging

import pytest

from disclosure_agent.agent.grounded_payload import GroundedPayload, build_grounded_payload
from disclosure_agent.agent.hyperclova import (
    HyperClovaResponseError,
    HyperClovaUnavailableError,
)
from disclosure_agent.agent.verbalizer import (
    MAX_ANSWER_LENGTH,
    SYSTEM_PROMPT,
    AnswerRejectedError,
    parse_model_answer,
    validate_answer,
    verbalize,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    EventHistory,
    Evidence,
    EvidenceProvenance,
    Fact,
    FieldChange,
)

QUESTION = "한미반도체 2025년 연결 기준 매출액이 얼마야?"

RAW_TEXT = "576,684,619,527"

VALUE = f"{RAW_TEXT}원"

GOOD_ANSWER = f"한미반도체의 2025년 연결 기준 매출액은 {VALUE}입니다."


# --------------------------------------------------------------------------
# 고정물
# --------------------------------------------------------------------------


def evidence() -> Evidence:
    return Evidence(
        evidence_id="ev_revenue",
        doc_id="doc_a",
        location_type=EvidenceLocationType.TABLE,
        table_name="연결 손익계산서",
        raw_text=RAW_TEXT,
        provenance=EvidenceProvenance(table_id="T-1", acode="revenue"),
    )


def fact_payload(
    *,
    scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
) -> GroundedPayload:
    fact = Fact(
        fact_id="fact_revenue",
        company="한미반도체",
        metric=MetricConcept.REVENUE,
        fiscal_year=2025,
        fiscal_period="FY",
        accounting_scope=scope,
        raw_value=RAW_TEXT,
        raw_unit="원",
        normalized_value=576684619527,
        normalized_unit="KRW",
        evidence_ids=["ev_revenue"],
    )

    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[fact],
        evidence=[evidence()],
    )

    return build_grounded_payload(result, question=QUESTION)


def history_payload(*, partial: bool = False) -> GroundedPayload:
    history = EventHistory(
        event_id="evt_1",
        document_ids=["doc_a", "doc_b"],
        changes=[FieldChange(field_name="contract_amount", before=100.0, after=120.0)],
        chain_complete=not partial,
    )

    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=(
            AnswerabilityStatus.PARTIALLY_ANSWERABLE if partial else AnswerabilityStatus.ANSWERABLE
        ),
        answerability_reason=AnswerabilityReason.INCOMPLETE_CHAIN if partial else None,
        event_histories=[history],
        evidence=[evidence()],
    )

    return build_grounded_payload(result, question="그 공급계약 나중에 바뀐 내용 있어?")


def model_reply(answer: str, **declared: list[str]) -> str:
    body = {
        "answer": answer,
        "used_fact_ids": declared.get("used_fact_ids", ["fact_revenue"]),
        "used_calculation_ids": declared.get("used_calculation_ids", []),
        "used_evidence_ids": declared.get("used_evidence_ids", ["ev_revenue"]),
    }

    return json.dumps(body, ensure_ascii=False)


class ScriptedClient:
    """미리 정한 결과를 돌려주고 호출을 기록한다. 망을 타지 않는다."""

    def __init__(self, *outcomes: str | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, str]] = []

    def complete(self, *, system_prompt: str, user_message: str) -> str:
        self.calls.append({"system_prompt": system_prompt, "user_message": user_message})

        outcome = self.outcomes.pop(0)

        if isinstance(outcome, Exception):
            raise outcome

        return outcome


def rejected(answer: str, payload: GroundedPayload | None = None) -> None:
    """이 답변이 거절되는지 확인한다. 거절은 None이며 재요청이 없다."""
    client = ScriptedClient(model_reply(answer), model_reply(GOOD_ANSWER))

    assert verbalize(payload or fact_payload(), client=client) is None
    assert len(client.calls) == 1, "거절한 응답을 다시 묻지 않는다"


# --------------------------------------------------------------------------
# 정상 경로
# --------------------------------------------------------------------------


def test_grounded_answer_reaches_the_caller() -> None:
    client = ScriptedClient(model_reply(GOOD_ANSWER))

    assert verbalize(fact_payload(), client=client) == GOOD_ANSWER


def test_request_carries_the_instructions_and_the_payload_only() -> None:
    """모델이 받는 것은 지시문과 근거 payload뿐이다."""
    client = ScriptedClient(model_reply(GOOD_ANSWER))

    payload = fact_payload()

    verbalize(payload, client=client)

    [call] = client.calls

    assert call["system_prompt"] == SYSTEM_PROMPT
    assert call["user_message"] == payload.to_json()


def test_raw_evidence_text_never_reaches_the_model() -> None:
    """payload 계층의 최소 전송 정책이 실제 요청에서도 지켜지는지 본다."""
    client = ScriptedClient(model_reply(GOOD_ANSWER))

    verbalize(fact_payload(), client=client)

    sent = client.calls[0]["user_message"]

    assert "연결 손익계산서" not in sent
    assert "T-1" not in sent
    assert "ev_revenue" in sent


def test_code_fenced_response_is_still_read() -> None:
    """형식 지시를 반쯤 따른 응답이 흔하다. 펜스만 벗기고 내용은 그대로 검증한다."""
    fenced = f"```json\n{model_reply(GOOD_ANSWER)}\n```"

    assert verbalize(fact_payload(), client=ScriptedClient(fenced)) == GOOD_ANSWER


def test_event_history_answer_stating_the_document_count_passes() -> None:
    answer = "관련 공시는 2건이며, 확인된 변경 항목은 1건입니다."

    client = ScriptedClient(model_reply(answer, used_fact_ids=[], used_evidence_ids=[]))

    assert verbalize(history_payload(), client=client) == answer


# --------------------------------------------------------------------------
# 모델 쪽 실패
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        HyperClovaUnavailableError("retryable status 503"),
        HyperClovaResponseError("no assistant content found in the response body"),
    ],
)
def test_client_failure_falls_back_without_raising(error: Exception) -> None:
    """외부 모델의 실패는 이 시스템의 실패가 아니다. 그때의 동작은 표현을 바꾸지 않는 것이다."""
    assert verbalize(fact_payload(), client=ScriptedClient(error)) is None


# --------------------------------------------------------------------------
# 형식
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "그냥 문장으로 답했습니다",
        "[]",
        '"answer"',
        '{"answer": ""}',
        '{"answer": "   "}',
        '{"answer": null}',
        '{"used_fact_ids": ["fact_revenue"]}',
    ],
)
def test_malformed_or_empty_response_is_rejected(raw: str) -> None:
    """형식을 지키지 못한 응답은 내용도 지시를 따랐다고 볼 근거가 없다."""
    with pytest.raises(AnswerRejectedError):
        parse_model_answer(raw)

    assert verbalize(fact_payload(), client=ScriptedClient(raw)) is None


@pytest.mark.parametrize(
    "declared",
    ['{"answer": "a", "used_fact_ids": "fact_revenue"}', '{"answer": "a", "used_fact_ids": [1]}'],
)
def test_malformed_id_list_is_rejected(declared: str) -> None:
    with pytest.raises(AnswerRejectedError, match="malformed_id_list"):
        parse_model_answer(declared)


def test_declared_id_outside_the_payload_is_rejected() -> None:
    """없는 evidence_id를 만들어 신고하면 근거가 있다는 주장만 남는다."""
    reply = model_reply(GOOD_ANSWER, used_evidence_ids=["ev_invented"])

    client = ScriptedClient(reply)

    assert verbalize(fact_payload(), client=client) is None


# --------------------------------------------------------------------------
# 근거 밖으로 나간 문장
# --------------------------------------------------------------------------


def test_invented_number_is_rejected() -> None:
    """모델이 만든 숫자는 공시에 없는 값이고, 형식상 정상인 문장에 실려 나간다."""
    rejected(f"한미반도체의 2025년 연결 기준 매출액은 {VALUE}이며 영업이익은 1,234원입니다.")


def test_rounded_number_is_rejected() -> None:
    """반올림도 새 숫자다. 자릿수가 달라지면 답변에 실린 수치로 검산할 수 없다."""
    rejected("한미반도체의 2025년 연결 기준 매출액은 약 5,766억원입니다.")


def test_switched_accounting_scope_is_rejected() -> None:
    """Q01의 주요 실패 모드가 연결·별도 혼동이다."""
    rejected(f"한미반도체의 2025년 별도 기준 매출액은 {VALUE}입니다.")


def test_scope_added_to_an_unconfirmed_fact_is_rejected() -> None:
    """확정하지 못한 기준을 모델이 채워 넣지 못한다."""
    payload = fact_payload(scope=FactAccountingScope.UNKNOWN)

    rejected(f"한미반도체의 2025년 연결 기준 매출액은 {VALUE}입니다.", payload)


def test_dropped_value_is_rejected() -> None:
    """빠뜨리는 쪽은 틀린 숫자를 쓰는 것보다 잡기 어렵다. 형식은 완전한 답변이다."""
    rejected("한미반도체의 2025년 연결 기준 매출액을 확인했습니다.")


def test_dropped_company_is_rejected() -> None:
    rejected(f"2025년 연결 기준 매출액은 {VALUE}입니다.")


def test_identifier_leak_is_rejected() -> None:
    rejected(f"한미반도체의 2025년 연결 기준 매출액은 {VALUE}입니다. (근거: ev_revenue)")


def test_internal_english_vocabulary_is_rejected() -> None:
    rejected(f"한미반도체의 2025년 연결 기준 revenue는 {VALUE}입니다.")


def test_advisory_wording_is_rejected() -> None:
    """사실 조회로 통과한 질문의 답변에 투자 의견이 덧붙지 않는지 보는 두 번째 자리다."""
    rejected(f"한미반도체의 2025년 연결 기준 매출액은 {VALUE}입니다. 지금 매수 추천합니다.")


def test_overlong_answer_is_rejected() -> None:
    padded = GOOD_ANSWER + "그리고 " * MAX_ANSWER_LENGTH

    rejected(padded)


def test_dropped_limitation_is_rejected() -> None:
    """한계 진술이 빠지면 부분 답변이 완전한 답변으로 둔갑한다."""
    payload = history_payload(partial=True)

    client = ScriptedClient(
        model_reply("관련 공시는 2건입니다.", used_fact_ids=[], used_evidence_ids=[])
    )

    assert verbalize(payload, client=client) is None


def test_answer_keeping_the_limitation_passes() -> None:
    payload = history_payload(partial=True)

    assert payload.limitation is not None

    answer = f"관련 공시는 2건입니다. {payload.limitation}"

    client = ScriptedClient(model_reply(answer, used_fact_ids=[], used_evidence_ids=[]))

    assert verbalize(payload, client=client) == answer


def test_dropped_document_count_is_rejected() -> None:
    payload = history_payload()

    client = ScriptedClient(
        model_reply("변경된 내용이 있습니다.", used_fact_ids=[], used_evidence_ids=[])
    )

    assert verbalize(payload, client=client) is None


# --------------------------------------------------------------------------
# 로깅 정책
# --------------------------------------------------------------------------


def test_rejection_logging_never_carries_the_answer_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """응답 본문에는 확보한 공시 수치가 들어 있다. 남기는 것은 어떤 규칙에 걸렸는가뿐이다."""
    leaking = f"한미반도체의 2025년 연결 기준 매출액은 {VALUE}이며 영업이익은 999,999원입니다."

    with caplog.at_level(logging.DEBUG):
        assert verbalize(fact_payload(), client=ScriptedClient(model_reply(leaking))) is None

    logged = "\n".join(record.getMessage() for record in caplog.records)

    assert "999,999" not in logged
    assert RAW_TEXT not in logged
    assert "ungrounded_number" in logged


def test_validate_answer_names_the_rule_it_broke() -> None:
    """code는 로그에 남는 유일한 단서다. 규칙마다 갈려 있어야 한다."""
    answer = parse_model_answer(model_reply("한미반도체는 2025년 연결 기준입니다."))

    with pytest.raises(AnswerRejectedError) as raised:
        validate_answer(answer, fact_payload())

    assert raised.value.code == "value_dropped"
