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

from disclosure_agent.agent.answer_builder import build_answer_response
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
    ComparisonRelation,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    ComparisonResult,
    EventHistory,
    Evidence,
    EvidenceProvenance,
    Fact,
    FieldChange,
    ResultInputReference,
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


# --------------------------------------------------------------------------
# PR #81 blocker 1 - 우열 판정이 문장에서 뒤집히지 않는다
#
# 값과 기업명이 모두 맞고 방향만 반대인 문장은 기존 검증기를 전부 통과한다. relation을 보는
# 자리가 없으면 걸리는 곳이 없다.
# --------------------------------------------------------------------------


def comparison_result(
    relation: ComparisonRelation,
    *,
    left_value: int = 100,
    right_value: int = 90,
) -> AgentExecutionResult:
    def operand(fact_id: str, company: str, value: int, evidence_id: str) -> Fact:
        return Fact(
            fact_id=fact_id,
            company=company,
            metric=MetricConcept.REVENUE,
            fiscal_year=2025,
            fiscal_period="FY",
            accounting_scope=FactAccountingScope.CONSOLIDATED,
            raw_value=str(value),
            raw_unit="원",
            normalized_value=value,
            normalized_unit="KRW",
            evidence_ids=[evidence_id],
        )

    def reference(fact_id: str) -> ResultInputReference:
        return ResultInputReference(input_type=ResultInputType.FACT, input_id=fact_id)

    def source(evidence_id: str, doc_id: str) -> Evidence:
        return Evidence(
            evidence_id=evidence_id, doc_id=doc_id, location_type=EvidenceLocationType.TABLE
        )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[
            operand("fact_left", "A사", left_value, "ev_left"),
            operand("fact_right", "B사", right_value, "ev_right"),
        ],
        evidence=[source("ev_left", "doc_a"), source("ev_right", "doc_b")],
        comparisons=[
            ComparisonResult(
                comparison_id="cmp_1",
                left=reference("fact_left"),
                right=reference("fact_right"),
                relation=relation,
            )
        ],
    )


def comparison_payload(
    relation: ComparisonRelation,
    *,
    left_value: int = 100,
    right_value: int = 90,
) -> GroundedPayload:
    result = comparison_result(relation, left_value=left_value, right_value=right_value)

    return build_grounded_payload(result, question="A사와 B사 중 어디가 더 커?")


def comparison_reply(answer: str) -> str:
    return json.dumps(
        {
            "answer": answer,
            "used_fact_ids": ["fact_left", "fact_right"],
            "used_calculation_ids": [],
            "used_evidence_ids": ["ev_left", "ev_right"],
        },
        ensure_ascii=False,
    )


def comparison_code(answer: str, payload: GroundedPayload) -> str:
    """이 문장이 걸린 규칙의 이름. 어느 규칙에 걸렸는지까지 고정한다."""
    with pytest.raises(AnswerRejectedError) as raised:
        validate_answer(parse_model_answer(comparison_reply(answer)), payload)

    return raised.value.code


VALUES_SENTENCE = "A사의 2025년 연결 기준 매출액은 100원, B사는 90원입니다."

SWAPPED_VALUES_SENTENCE = "A사의 2025년 연결 기준 매출액은 90원, B사는 100원입니다."


def test_left_greater_answer_naming_the_right_side_is_rejected() -> None:
    """리뷰가 지적한 바로 그 모양이다. 수치는 맞고 방향만 반대다."""
    payload = comparison_payload(ComparisonRelation.LEFT_GREATER)

    assert (
        comparison_code(f"{VALUES_SENTENCE} B사가 더 큽니다.", payload) == "relation_contradiction"
    )


def test_right_greater_answer_naming_the_left_side_is_rejected() -> None:
    payload = comparison_payload(ComparisonRelation.RIGHT_GREATER, left_value=90, right_value=100)

    answer = f"{SWAPPED_VALUES_SENTENCE} A사가 더 큽니다."

    assert comparison_code(answer, payload) == "relation_contradiction"


def test_equal_answer_claiming_a_winner_is_rejected() -> None:
    payload = comparison_payload(ComparisonRelation.EQUAL, left_value=100, right_value=100)

    answer = "A사의 2025년 연결 기준 매출액은 100원, B사는 100원입니다. A사가 더 큽니다."

    assert comparison_code(answer, payload) == "relation_contradiction"


def test_left_greater_answer_naming_the_left_side_passes() -> None:
    payload = comparison_payload(ComparisonRelation.LEFT_GREATER)

    answer = f"{VALUES_SENTENCE} A사 쪽이 더 큽니다."

    assert verbalize(payload, client=ScriptedClient(comparison_reply(answer))) == answer


def test_right_greater_answer_naming_the_right_side_passes() -> None:
    payload = comparison_payload(ComparisonRelation.RIGHT_GREATER, left_value=90, right_value=100)

    answer = f"{SWAPPED_VALUES_SENTENCE} B사 쪽이 더 큽니다."

    assert verbalize(payload, client=ScriptedClient(comparison_reply(answer))) == answer


def test_equal_answer_without_a_ranking_claim_passes() -> None:
    payload = comparison_payload(ComparisonRelation.EQUAL, left_value=100, right_value=100)

    answer = "A사의 2025년 연결 기준 매출액은 100원, B사는 100원입니다. 두 값은 같습니다."

    assert verbalize(payload, client=ScriptedClient(comparison_reply(answer))) == answer


def test_answer_dropping_the_ranking_is_rejected() -> None:
    """질문이 요구한 판정이 사라진 답변이다. 확인하지 못한 것을 통과시키지 않는다."""
    payload = comparison_payload(ComparisonRelation.LEFT_GREATER)

    assert comparison_code(VALUES_SENTENCE, payload) == "relation_dropped"


def test_inverting_comparative_is_not_reinterpreted() -> None:
    """A가 B보다 크다는 꼴은 표현 앞에 진 쪽이 온다. 좌우를 다시 해석하지 않고 거절한다."""
    payload = comparison_payload(ComparisonRelation.LEFT_GREATER)

    answer = f"{VALUES_SENTENCE} A사가 B사보다 더 큽니다."

    assert comparison_code(answer, payload) == "unverifiable_relation"


@pytest.mark.parametrize("relation", list(ComparisonRelation))
def test_deterministic_comparison_sentence_passes_the_validator(
    relation: ComparisonRelation,
) -> None:
    """결정론 문장이 자기 검증기에 걸리면 대조군이 사라진다."""
    result = comparison_result(relation)

    payload = build_grounded_payload(result, question="A사와 B사 중 어디가 더 커?")

    sentence = build_answer_response(result).answer

    validate_answer(parse_model_answer(comparison_reply(sentence)), payload)


# --------------------------------------------------------------------------
# PR #81 blocker 2 - 이력의 셈과 완결 여부를 문장이 뒤집지 않는다
# --------------------------------------------------------------------------


def counted_history_result(
    *,
    documents: int = 2,
    changes: int = 1,
    chain_complete: bool = True,
) -> AgentExecutionResult:
    history = EventHistory(
        event_id="evt_1",
        document_ids=[f"doc_{index}" for index in range(documents)],
        changes=[
            FieldChange(field_name="contract_amount", before=100.0, after=120.0)
            for _ in range(changes)
        ],
        chain_complete=chain_complete,
    )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=(
            AnswerabilityStatus.ANSWERABLE
            if chain_complete
            else AnswerabilityStatus.PARTIALLY_ANSWERABLE
        ),
        answerability_reason=None if chain_complete else AnswerabilityReason.INCOMPLETE_CHAIN,
        event_histories=[history],
        evidence=[evidence()],
    )


def counted_history_payload(**shape: object) -> GroundedPayload:
    return build_grounded_payload(
        counted_history_result(**shape),  # type: ignore[arg-type]
        question="그 공급계약 나중에 바뀐 내용 있어?",
    )


def history_code(answer: str, payload: GroundedPayload) -> str:
    reply = model_reply(answer, used_fact_ids=[], used_evidence_ids=[])

    with pytest.raises(AnswerRejectedError) as raised:
        validate_answer(parse_model_answer(reply), payload)

    return raised.value.code


def test_denying_changes_that_exist_is_rejected() -> None:
    payload = counted_history_payload(changes=1)

    answer = "관련 공시 2건을 확인했으며, 변경된 내용은 없습니다."

    assert history_code(answer, payload) == "history_contradiction"


def test_claiming_changes_that_do_not_exist_is_rejected() -> None:
    """건수를 적지 않아도 변경이 있었다는 진술 자체가 결과와 어긋난다."""
    payload = counted_history_payload(changes=0)

    answer = "관련 공시 2건을 확인했으며, 계약 내용이 변경되었습니다."

    assert history_code(answer, payload) == "history_contradiction"


def test_claiming_a_change_count_that_does_not_exist_is_rejected() -> None:
    """
    건수를 적은 쪽은 수치 검증이 먼저 잡는다.

    change_count=0인 payload에 '1'이라는 수치가 없기 때문이다. 두 규칙 중 어느 쪽이 잡든
    결과는 같지만(결정론 문장으로 돌아간다), 어느 자리에서 걸리는지는 고정해 둔다.
    """
    payload = counted_history_payload(changes=0)

    answer = "관련 공시 2건을 확인했으며, 1건 변경되었습니다."

    assert history_code(answer, payload) == "ungrounded_number"


def test_claiming_a_complete_chain_when_it_is_not_is_rejected() -> None:
    payload = counted_history_payload(chain_complete=False)

    assert payload.limitation is not None

    answer = f"관련 공시 2건으로 전체 이력이 완결되었습니다. {payload.limitation}"

    assert history_code(answer, payload) == "chain_contradiction"


def test_claiming_an_incomplete_chain_when_it_is_complete_is_rejected() -> None:
    payload = counted_history_payload(chain_complete=True)

    answer = "관련 공시 2건을 확인했으나 이력이 불완전합니다. 확인된 변경 항목은 1건입니다."

    assert history_code(answer, payload) == "chain_contradiction"


def test_non_contradictory_history_wording_passes() -> None:
    """모든 항목을 적으라고 요구하지 않는다. 정면으로 어긋나는 진술만 막는다."""
    payload = counted_history_payload(documents=2, changes=1, chain_complete=True)

    answer = "관련 공시 2건으로 이어지는 이력을 확인했으며, 확인된 변경 항목은 1건입니다."

    client = ScriptedClient(model_reply(answer, used_fact_ids=[], used_evidence_ids=[]))

    assert verbalize(payload, client=client) == answer


def test_withheld_limitation_does_not_read_as_an_incomplete_chain() -> None:
    """
    체인은 완결됐는데 원문에서 일부 field가 유보된 결과다(D9).

    그 한계 문장은 확인하지 못했다는 말로 끝나고 답변에 반드시 남아야 한다. 완결 여부를 그
    표현만으로 판정하면 반드시 남아야 할 문장이 검증기에 걸린다.
    """
    result = counted_history_result(chain_complete=True)

    result.answerability_status = AnswerabilityStatus.PARTIALLY_ANSWERABLE
    result.answerability_reason = AnswerabilityReason.WITHHELD_IN_SOURCE

    payload = build_grounded_payload(result, question="그 공급계약 나중에 바뀐 내용 있어?")

    sentence = build_answer_response(result).answer

    validate_answer(
        parse_model_answer(model_reply(sentence, used_fact_ids=[], used_evidence_ids=[])),
        payload,
    )


@pytest.mark.parametrize(
    ("documents", "changes", "chain_complete"),
    [(2, 1, False), (2, 0, False), (1, 0, True), (3, 2, True), (3, 0, True)],
)
def test_deterministic_history_sentence_passes_the_validator(
    documents: int,
    changes: int,
    chain_complete: bool,
) -> None:
    """Pattern E의 다섯 가지 문장 모양이 모두 자기 검증기를 통과한다."""
    result = counted_history_result(
        documents=documents, changes=changes, chain_complete=chain_complete
    )

    payload = build_grounded_payload(result, question="그 공급계약 나중에 바뀐 내용 있어?")

    sentence = build_answer_response(result).answer

    validate_answer(
        parse_model_answer(model_reply(sentence, used_fact_ids=[], used_evidence_ids=[])),
        payload,
    )
