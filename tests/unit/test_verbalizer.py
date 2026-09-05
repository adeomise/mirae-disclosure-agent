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

from disclosure_agent.agent.answer_builder import (
    OPERATION_DELTA,
    OPERATION_DELTA_PCT,
    OPERATION_DIFF_PP,
    UNIT_PERCENTAGE_POINT,
    build_answer_response,
)
from disclosure_agent.agent.derived_metrics import OPERATION_RATIO, UNIT_PERCENT
from disclosure_agent.agent.grounded_payload import GroundedPayload, build_grounded_payload
from disclosure_agent.agent.hyperclova import (
    HyperClovaResponseError,
    HyperClovaUnavailableError,
)
from disclosure_agent.agent.verbalizer import (
    MAX_ANSWER_LENGTH,
    SYSTEM_PROMPT,
    AnswerRejectedError,
    _facts_consumed_by_calculations,
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
    CalculationResult,
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


def fact_result(
    *,
    scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
) -> AgentExecutionResult:
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

    return result


def fact_payload(
    *,
    scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
) -> GroundedPayload:
    return build_grounded_payload(fact_result(scope=scope), question=QUESTION)


def history_result(*, partial: bool = False) -> AgentExecutionResult:
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

    return result


HISTORY_QUESTION = "그 공급계약 나중에 바뀐 내용 있어?"


def history_payload(*, partial: bool = False) -> GroundedPayload:
    return build_grounded_payload(history_result(partial=partial), question=HISTORY_QUESTION)


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
# 수치 표기 보존 — 검증기가 보는 것은 문자열이다
#
# 검증기가 보는 것은 수의 크기가 아니라 문자열이다. _canonical_number가 지우는 것은 천단위
# 쉼표뿐이고 자릿수는 그대로 두므로, 같은 수를 다르게 적은 문장은 payload 밖의 수치가 된다.
#
# 모델은 그 표기를 정규화한다. HCX-007이 실제로 그렇게 했다. payload가 증감액을
# 17767427980.0000원으로 싣던 동안 세 번의 실측 호출이 모두 그것을 17,767,427,980원으로 다시
# 적었고, 그 응답은 ungrounded_number로 거절됐다.
#
# 그 불일치는 표기 쪽에서 없앴다. 증감액은 이제 결정론 문장과 payload가 같은 규칙으로 적는다
# (answer_builder._grouped_amount). 아래는 그 표기에 대한 검증기의 판정을 고정한다.
# --------------------------------------------------------------------------


def temporal_delta_result() -> AgentExecutionResult:
    """Q07 모양. 원천 Fact 2건과 그 둘에서 나온 증감액·증감률.

    값은 실제 코퍼스가 만든 것과 같은 표기를 쓴다. 원천 Fact는 공시 원문 표기라 쉼표가 있고,
    증감액은 계산 계층이 만든 문자열이라 쉼표가 없고 소수 자릿수가 남는다. 한 payload 안에
    두 표기가 함께 있다는 것이 이 고정의 핵심이다.
    """

    def revenue(fact_id: str, *, year: int, raw: str, normalized: int) -> Fact:
        return Fact(
            fact_id=fact_id,
            company="한미반도체",
            metric=MetricConcept.REVENUE,
            fiscal_year=year,
            fiscal_period="FY",
            accounting_scope=FactAccountingScope.CONSOLIDATED,
            raw_value=raw,
            raw_unit="원",
            normalized_value=normalized,
            normalized_unit="KRW",
            evidence_ids=[f"ev_{year}"],
        )

    def inputs() -> list[ResultInputReference]:
        return [
            ResultInputReference(input_type=ResultInputType.FACT, input_id="fact_rev_2024"),
            ResultInputReference(input_type=ResultInputType.FACT, input_id="fact_rev_2025"),
        ]

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[
            revenue("fact_rev_2024", year=2024, raw="558,917,191,547", normalized=558917191547),
            revenue("fact_rev_2025", year=2025, raw="576,684,619,527", normalized=576684619527),
        ],
        calculations=[
            CalculationResult(
                calculation_id="calc_delta",
                operation=OPERATION_DELTA,
                inputs=inputs(),
                value="17767427980.0000",
                unit="KRW",
                evidence_ids=["ev_2024", "ev_2025"],
            ),
            CalculationResult(
                calculation_id="calc_delta_pct",
                operation=OPERATION_DELTA_PCT,
                inputs=inputs(),
                value="3.1789",
                unit=UNIT_PERCENT,
                evidence_ids=["ev_2024", "ev_2025"],
            ),
        ],
        evidence=[
            Evidence(
                evidence_id="ev_2024", doc_id="doc_2024", location_type=EvidenceLocationType.TABLE
            ),
            Evidence(
                evidence_id="ev_2025", doc_id="doc_2025", location_type=EvidenceLocationType.TABLE
            ),
        ],
    )


def temporal_delta_payload() -> GroundedPayload:
    return build_grounded_payload(
        temporal_delta_result(), question="한미반도체 매출이 작년보다 얼마나 늘었어?"
    )


def temporal_delta_reply(answer: str) -> str:
    body = {
        "answer": answer,
        "used_fact_ids": ["fact_rev_2024", "fact_rev_2025"],
        "used_calculation_ids": ["calc_delta", "calc_delta_pct"],
        "used_evidence_ids": ["ev_2024", "ev_2025"],
    }

    return json.dumps(body, ensure_ascii=False)


def test_q07_answer_copying_the_payload_strings_passes() -> None:
    """
    payload의 문자열을 그대로 쓴 Q07 문장은 통과한다.

    수치를 손으로 적지 않고 payload에서 읽어 쓴다. 적어 두면 payload 표기가 바뀌는 날 이
    고정이 함께 틀리고, 그때 통과하는 문장이 실제로 통과할 문장인지 알 수 없다.
    """
    payload = temporal_delta_payload()

    previous, current = payload.facts

    amount, rate = payload.calculations

    answer = (
        f"한미반도체의 연결 기준 매출액은 {previous.fiscal_year}년 {previous.display_value}, "
        f"{current.fiscal_year}년 {current.display_value}입니다. "
        f"증감액은 {amount.display_value}, 증감률은 {rate.display_value}입니다."
    )

    # 실제로 그 문자열이 payload에 실린 표기인지 함께 본다.
    assert amount.display_value == "17,767,427,980원"
    assert rate.display_value == "3.1789%"

    validate_answer(parse_model_answer(temporal_delta_reply(answer)), payload)


def test_deterministic_q07_sentence_passes_the_validator() -> None:
    """결정론 문장이 자기 검증기에 걸리면 대조군이 사라진다."""
    result = temporal_delta_result()

    payload = build_grounded_payload(
        result, question="한미반도체 매출이 작년보다 얼마나 늘었어?"
    )

    sentence = build_answer_response(result).answer

    validate_answer(parse_model_answer(temporal_delta_reply(sentence)), payload)


def q07_answer_with_delta(delta: str) -> str:
    """증감액 자리만 갈아 끼운 Q07 문장. 나머지 수치는 payload에서 읽는다."""
    payload = temporal_delta_payload()

    previous, current = payload.facts

    rate = payload.calculations[1]

    return (
        f"한미반도체의 연결 기준 매출액은 {previous.fiscal_year}년 {previous.display_value}, "
        f"{current.fiscal_year}년 {current.display_value}입니다. "
        f"증감액은 {delta}, 증감률은 {rate.display_value}입니다."
    )


@pytest.mark.parametrize(
    "delta",
    [
        # 표기 규칙이 생기기 전 payload가 싣던 문자열. 이제는 근거 밖의 수치다.
        "17767427980.0000원",
        # 반올림·단위 환산. 규칙이 바뀌어도 이쪽이 통과해서는 안 된다.
        "약 177억원",
        "17,767,427,981원",
    ],
    ids=["pre-grouping", "abbreviated", "off-by-one"],
)
def test_q07_answer_not_matching_the_payload_delta_is_rejected(delta: str) -> None:
    """
    payload의 문자열과 다른 증감액은 거절된다.

    표기 규칙을 바꾼 것은 payload가 무엇을 싣는가이지 검증기가 무엇을 통과시키는가가 아니다.
    규칙 이전의 문자열(17767427980.0000원)이 이제 거절되는 것이 그 증거다. 검증기는 그때와
    같은 일을 하고 있고, 대조할 문자열만 달라졌다.
    """
    payload = temporal_delta_payload()

    with pytest.raises(AnswerRejectedError) as caught:
        validate_answer(
            parse_model_answer(temporal_delta_reply(q07_answer_with_delta(delta))), payload
        )

    assert caught.value.code == "ungrounded_number"


def test_q07_answer_using_the_grouped_delta_passes() -> None:
    """대조군. payload가 싣는 표기를 그대로 쓴 문장은 통과한다."""
    payload = temporal_delta_payload()

    amount = payload.calculations[0]

    assert amount.display_value == "17,767,427,980원"

    validate_answer(
        parse_model_answer(temporal_delta_reply(q07_answer_with_delta(amount.display_value))),
        payload,
    )


# --------------------------------------------------------------------------
# 답에 실리는 값과 보조 값
#
# payload에 실린 Fact가 모두 답의 일부인 것은 아니다. 파생값을 만든 원천 Fact는 그 파생값이
# 무엇에서 나왔는지를 붙들어 두려고 함께 실린다. 그것까지 문장에 요구하면 결정론 경로가 자기
# 검증기에 걸린다. Q08과 Q11이 실제로 걸렸다.
#
# 여기서 고정하는 것은 두 가지다. 완결성의 하한이 참조 위상에서 정해진다는 것과, 그 완화가
# 근거 밖의 수치를 열지 않는다는 것이다. 뒤쪽이 특히 중요하다. 하한을 낮춘 것이 상한을 함께
# 낮추면 검증이 약해진 것이고, 그 사실은 통과한 문장만 봐서는 드러나지 않는다.
# --------------------------------------------------------------------------


def margin_ratio(
    calculation_id: str,
    *,
    numerator: str,
    denominator: str,
    value: str,
) -> CalculationResult:
    return CalculationResult(
        calculation_id=calculation_id,
        operation=OPERATION_RATIO,
        inputs=[
            ResultInputReference(input_type=ResultInputType.FACT, input_id=numerator),
            ResultInputReference(input_type=ResultInputType.FACT, input_id=denominator),
        ],
        value=value,
        unit=UNIT_PERCENT,
        evidence_ids=[f"ev_{numerator}", f"ev_{denominator}"],
    )


def periodic_fact(
    fact_id: str,
    *,
    company: str,
    metric: MetricConcept,
    year: int,
    raw: str,
    normalized: int,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        company=company,
        metric=metric,
        fiscal_year=year,
        fiscal_period="FY",
        accounting_scope=FactAccountingScope.CONSOLIDATED,
        raw_value=raw,
        raw_unit="원",
        normalized_value=normalized,
        normalized_unit="KRW",
        evidence_ids=[f"ev_{fact_id}"],
    )


def source(evidence_id: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id, doc_id="doc_a", location_type=EvidenceLocationType.TABLE
    )


Q08_QUESTION = "한미반도체 영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?"

Q11_QUESTION = "A사랑 B사 중에 2025년 영업이익률이 더 높은 곳은 어디야?"


def derived_delta_result() -> AgentExecutionResult:
    """Q08 모양. 원천 Fact 4건, 기간별 비율 2건, 두 비율의 차이 1건."""
    facts = [
        periodic_fact(
            "f_oi_2024",
            company="한미반도체",
            metric=MetricConcept.OPERATING_INCOME,
            year=2024,
            raw="100",
            normalized=100,
        ),
        periodic_fact(
            "f_rev_2024",
            company="한미반도체",
            metric=MetricConcept.REVENUE,
            year=2024,
            raw="1,000",
            normalized=1000,
        ),
        periodic_fact(
            "f_oi_2025",
            company="한미반도체",
            metric=MetricConcept.OPERATING_INCOME,
            year=2025,
            raw="90",
            normalized=90,
        ),
        periodic_fact(
            "f_rev_2025",
            company="한미반도체",
            metric=MetricConcept.REVENUE,
            year=2025,
            raw="1,000",
            normalized=1000,
        ),
    ]

    change = CalculationResult(
        calculation_id="calc_margin_diff",
        operation=OPERATION_DIFF_PP,
        inputs=[
            ResultInputReference(
                input_type=ResultInputType.CALCULATION, input_id="calc_margin_2024"
            ),
            ResultInputReference(
                input_type=ResultInputType.CALCULATION, input_id="calc_margin_2025"
            ),
        ],
        value="-1.0000",
        unit=UNIT_PERCENTAGE_POINT,
        evidence_ids=["ev_f_oi_2024", "ev_f_rev_2024", "ev_f_oi_2025", "ev_f_rev_2025"],
    )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=facts,
        calculations=[
            margin_ratio(
                "calc_margin_2024",
                numerator="f_oi_2024",
                denominator="f_rev_2024",
                value="10.0000",
            ),
            margin_ratio(
                "calc_margin_2025",
                numerator="f_oi_2025",
                denominator="f_rev_2025",
                value="9.0000",
            ),
            change,
        ],
        evidence=[source(f"ev_{fact.fact_id}") for fact in facts],
    )


def margin_comparison_result() -> AgentExecutionResult:
    """Q11 모양. 두 기업의 원천 Fact 4건, 기업별 비율 2건, 그 둘의 우열 1건."""
    facts = [
        periodic_fact(
            "f_a_oi",
            company="A사",
            metric=MetricConcept.OPERATING_INCOME,
            year=2025,
            raw="200",
            normalized=200,
        ),
        periodic_fact(
            "f_a_rev",
            company="A사",
            metric=MetricConcept.REVENUE,
            year=2025,
            raw="1,000",
            normalized=1000,
        ),
        periodic_fact(
            "f_b_oi",
            company="B사",
            metric=MetricConcept.OPERATING_INCOME,
            year=2025,
            raw="100",
            normalized=100,
        ),
        periodic_fact(
            "f_b_rev",
            company="B사",
            metric=MetricConcept.REVENUE,
            year=2025,
            raw="1,000",
            normalized=1000,
        ),
    ]

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=facts,
        calculations=[
            margin_ratio(
                "calc_margin_a", numerator="f_a_oi", denominator="f_a_rev", value="20.0000"
            ),
            margin_ratio(
                "calc_margin_b", numerator="f_b_oi", denominator="f_b_rev", value="10.0000"
            ),
        ],
        comparisons=[
            ComparisonResult(
                comparison_id="cmp_margin",
                left=ResultInputReference(
                    input_type=ResultInputType.CALCULATION, input_id="calc_margin_a"
                ),
                right=ResultInputReference(
                    input_type=ResultInputType.CALCULATION, input_id="calc_margin_b"
                ),
                relation=ComparisonRelation.LEFT_GREATER,
            )
        ],
        evidence=[source(f"ev_{fact.fact_id}") for fact in facts],
    )


def reply_declaring(payload: GroundedPayload, answer: str) -> str:
    """payload가 싣고 있는 id를 그대로 신고하는 응답."""
    return json.dumps(
        {
            "answer": answer,
            "used_fact_ids": [item.fact_id for item in payload.facts],
            "used_calculation_ids": [item.calculation_id for item in payload.calculations],
            "used_evidence_ids": [item.evidence_id for item in payload.evidence],
        },
        ensure_ascii=False,
    )


DETERMINISTIC_SHAPES: dict[str, tuple[object, str]] = {
    "Q01 single fact": (fact_result, QUESTION),
    "Q07 temporal delta": (temporal_delta_result, "한미반도체 매출이 작년보다 얼마나 늘었어?"),
    "Q08 derived delta": (derived_delta_result, Q08_QUESTION),
    "Q10 fact compare": (
        lambda: comparison_result(ComparisonRelation.LEFT_GREATER),
        "A사와 B사 중 어디가 더 커?",
    ),
    "Q11 margin compare": (margin_comparison_result, Q11_QUESTION),
    "event history": (history_result, HISTORY_QUESTION),
}


@pytest.mark.parametrize("shape", sorted(DETERMINISTIC_SHAPES))
def test_deterministic_answer_validates_against_its_own_payload(shape: str) -> None:
    """
    결정론 문장이 자기 payload의 검증기를 통과한다.

    이 자리가 없어서 Q08과 Q11이 오래 어긋나 있었다. 두 문항의 결정론 문장은 원천 Fact의 값을
    적지 않는데 검증기는 그것을 요구했고, 그 사실은 외부 모델을 켜기 전까지 드러나지 않았다.
    모양이 늘 때 같은 어긋남이 다시 생기지 않도록 모양별로 한 번씩 지나가게 둔다.
    """
    build, question = DETERMINISTIC_SHAPES[shape]

    result = build()  # type: ignore[operator]

    payload = build_grounded_payload(result, question=question)

    sentence = build_answer_response(result).answer

    validate_answer(parse_model_answer(reply_declaring(payload, sentence)), payload)


def test_calculation_input_facts_are_supporting() -> None:
    """파생값을 만든 원천 Fact는 그 파생값이 대신 답한다."""
    result = derived_delta_result()

    payload = build_grounded_payload(result, question=Q08_QUESTION)

    # 네 Fact 모두 비율의 입력이다.
    assert {item.fact_id for item in payload.facts} == {
        "f_oi_2024",
        "f_rev_2024",
        "f_oi_2025",
        "f_rev_2025",
    }

    answer = (
        "한미반도체의 연결 기준 영업이익률은 2024년 10.0000%, 2025년 9.0000%이며, "
        "두 연도의 차이는 -1.0000%p입니다."
    )

    validate_answer(parse_model_answer(reply_declaring(payload, answer)), payload)


def test_supporting_fact_may_be_stated_verbatim() -> None:
    """보조로 내려간 값도 화이트리스트에 남는다. 적어도 되고, 적으면 그대로여야 한다."""
    result = derived_delta_result()

    payload = build_grounded_payload(result, question=Q08_QUESTION)

    answer = (
        "한미반도체의 연결 기준 영업이익률은 2024년 10.0000%, 2025년 9.0000%이며, "
        "두 연도의 차이는 -1.0000%p입니다. 2025년 영업이익은 90원입니다."
    )

    validate_answer(parse_model_answer(reply_declaring(payload, answer)), payload)


def test_supporting_fact_stated_with_an_altered_value_is_rejected() -> None:
    """
    하한을 낮춘 것이 상한을 함께 낮추지 않았는지 본다.

    보조 값은 '적지 않아도 된다'이지 '아무렇게나 적어도 된다'가 아니다. _allowed_numbers는
    그대로이므로 자릿수를 바꿔 적으면 근거 밖의 수치가 된다.
    """
    result = derived_delta_result()

    payload = build_grounded_payload(result, question=Q08_QUESTION)

    answer = (
        "한미반도체의 연결 기준 영업이익률은 2024년 10.0000%, 2025년 9.0000%이며, "
        "두 연도의 차이는 -1.0000%p입니다. 2025년 영업이익은 91원입니다."
    )

    with pytest.raises(AnswerRejectedError) as caught:
        validate_answer(parse_model_answer(reply_declaring(payload, answer)), payload)

    assert caught.value.code == "ungrounded_number"


def test_every_calculation_stays_required_at_any_depth() -> None:
    """다른 계산의 입력으로 쓰인 계산도 빠지면 안 된다. 대변할 값이 없다."""
    result = derived_delta_result()

    payload = build_grounded_payload(result, question=Q08_QUESTION)

    # 2024년 비율을 뺀 문장. 나머지는 그대로다.
    answer = (
        "한미반도체의 연결 기준 영업이익률은 2025년 9.0000%이며, "
        "두 연도의 차이는 -1.0000%p입니다."
    )

    with pytest.raises(AnswerRejectedError) as caught:
        validate_answer(parse_model_answer(reply_declaring(payload, answer)), payload)

    assert caught.value.code == "value_dropped"


@pytest.mark.parametrize("dropped", ["delta", "delta_pct"])
def test_q07_calculations_stay_required(dropped: str) -> None:
    """증감액과 증감률은 어느 쪽도 보조가 아니다."""
    payload = temporal_delta_payload()

    amount, rate = payload.calculations

    kept = rate.display_value if dropped == "delta" else amount.display_value

    answer = f"한미반도체의 매출액 변화는 {kept}입니다."

    with pytest.raises(AnswerRejectedError) as caught:
        validate_answer(parse_model_answer(temporal_delta_reply(answer)), payload)

    assert caught.value.code == "value_dropped"


def test_q07_answer_may_omit_the_source_facts() -> None:
    """
    변화만 물은 질문에서 원천 Fact는 보조다.

    실측에서 HCX-007이 실제로 이 모양의 문장을 돌려주었고, 규칙이 '전부'이던 동안에는
    value_dropped로 거절됐다.
    """
    payload = temporal_delta_payload()

    amount, rate = payload.calculations

    answer = (
        f"한미반도체의 매출액은 작년보다 {amount.display_value} 늘었고, "
        f"증감률은 {rate.display_value}입니다."
    )

    validate_answer(parse_model_answer(temporal_delta_reply(answer)), payload)


def test_comparison_operands_are_not_supporting() -> None:
    """
    우열 판정은 값을 대신 말해 주지 않는다.

    left_id/right_id를 보조 집합에 넣으면 Q10에서 값을 하나도 적지 않은 판정 문장이 통과한다.
    """
    result = comparison_result(ComparisonRelation.LEFT_GREATER)

    payload = build_grounded_payload(result, question="A사와 B사 중 어디가 더 커?")

    assert payload.comparisons

    with pytest.raises(AnswerRejectedError) as caught:
        validate_answer(
            parse_model_answer(
                reply_declaring(payload, "A사 쪽이 B사보다 더 큽니다.")
            ),
            payload,
        )

    assert caught.value.code == "value_dropped"


def test_payload_without_calculations_exempts_no_fact() -> None:
    """계산이 없으면 보조도 없다. Q01과 Q10의 완결성 기준은 이 변경 전과 같다."""
    for payload in (fact_payload(), build_grounded_payload(
        comparison_result(ComparisonRelation.LEFT_GREATER), question="A사와 B사 중 어디가 더 커?"
    )):
        assert not payload.calculations

        assert _facts_consumed_by_calculations(payload) == frozenset()


def test_calculation_ids_never_become_supporting_facts() -> None:
    """
    input_ids에는 계산 id도 섞인다(Q08의 변화량). 그것이 보조 집합에 들어가면 안 된다.

    들어가면 그 id를 가진 계산이 문장에서 빠져도 통과하게 된다. 종류를 문자열 모양으로 짐작하지
    않고 payload의 Fact id 집합과 교집합을 취하는 이유다.
    """
    payload = build_grounded_payload(derived_delta_result(), question=Q08_QUESTION)

    supporting = _facts_consumed_by_calculations(payload)

    known_facts = {item.fact_id for item in payload.facts}

    assert supporting <= known_facts

    assert not supporting & {item.calculation_id for item in payload.calculations}


# --------------------------------------------------------------------------
# 기간과 값의 짝
#
# 수치 검증은 값의 집합만 본다. 두 해의 값을 맞바꿔 적어도 쓰인 수치는 모두 payload 안에 있고
# 빠진 값도 없으므로, 짝을 보지 않으면 걸리는 자리가 없다. 아래는 그 짝이 문장에 남아 있는지를
# 고정한다.
# --------------------------------------------------------------------------


def q08_payload_and_ids() -> tuple[GroundedPayload, AgentExecutionResult]:
    result = derived_delta_result()

    return build_grounded_payload(result, question=Q08_QUESTION), result


def q08_verdict(answer: str) -> str | None:
    """Q08 payload에 대해 문장을 검증하고 거절 코드를 돌려준다. 통과면 None."""
    payload, _ = q08_payload_and_ids()

    try:
        validate_answer(parse_model_answer(reply_declaring(payload, answer)), payload)
    except AnswerRejectedError as error:
        return error.code

    return None


def test_q08_deterministic_sentence_keeps_each_year_with_its_value() -> None:
    """대조군. 결정론 문장은 연도 바로 뒤에 그 해의 값을 적는다."""
    payload, result = q08_payload_and_ids()

    sentence = build_answer_response(result).answer

    # 이 고정이 무엇을 보고 있는지 문장으로 남긴다. payload가 정한 짝이 아래 그대로 나온다.
    bound = {item.fiscal_year: item.display_value for item in payload.calculations}

    assert bound == {2024: "10.0000%", 2025: "9.0000%", None: "-1.0000%p"}

    assert "2024년 10.0000%" in sentence
    assert "2025년 9.0000%" in sentence

    assert q08_verdict(sentence) is None


def test_q08_answer_swapping_the_two_years_is_rejected() -> None:
    """
    맞바꾼 문장이 이 규칙 전에는 통과했다.

    쓰인 수치는 10.0000%·9.0000%·-1.0000%p로 payload와 같고 빠진 값도 없다. 틀렸다는 사실은
    값이 아니라 값과 해의 짝에만 남으며, 그것을 보는 자리가 여기 말고는 없다.
    """
    assert (
        q08_verdict(
            "한미반도체의 연결 기준 영업이익률은 2024년 9.0000%, 2025년 10.0000%이며, "
            "두 연도의 차이는 -1.0000%p입니다."
        )
        == "period_contradiction"
    )


def test_q08_answer_naming_both_years_before_both_values_is_rejected() -> None:
    """
    두 값이 다 있어도 어느 해의 것인지 갈리지 않으면 통과시키지 않는다.

    "2024년과 2025년에 각각 A와 B"는 읽는 사람이 순서를 맞춰 읽어야 짝이 선다. 그 순간 짝을
    세우는 것은 시스템이 아니라 사용자이고, 확정된 (연도, 값)은 답변 어디에도 남지 않는다.
    """
    assert (
        q08_verdict(
            "한미반도체의 연결 기준 영업이익률은 2024년과 2025년에 각각 10.0000%와 "
            "9.0000%였습니다. 두 연도의 차이는 -1.0000%p입니다."
        )
        == "unverifiable_period"
    )


def test_q08_answer_dropping_the_years_is_rejected() -> None:
    """연도를 떼면 값만 남는다. 그 문장은 어느 해의 값인지 말하지 않는다."""
    assert (
        q08_verdict(
            "한미반도체의 연결 기준 영업이익률은 10.0000%, 9.0000%이며, "
            "두 연도의 차이는 -1.0000%p입니다."
        )
        == "unverifiable_period"
    )


def test_q08_answer_without_a_comma_between_the_periods_passes() -> None:
    """
    구절을 쉼표로 나누지 않아도 짝이 서면 통과한다.

    앞 값의 소수점이 경계가 되어 앞 구절의 연도가 다음 값의 범위로 넘어오지 않는다. 결정론
    문장의 어순만 통과시키는 규칙이 아니다.
    """
    assert (
        q08_verdict(
            "한미반도체의 연결 기준 영업이익률은 2024년 10.0000%이며 2025년 9.0000%입니다. "
            "두 연도의 차이는 -1.0000%p입니다."
        )
        is None
    )


def test_q08_value_appearing_inside_another_value_does_not_break_the_binding() -> None:
    """
    같은 문자열이 다른 값 안에 들어 있어도 짝이 서면 통과한다.

    비율이 1.0000%이고 차이가 -1.0000%p이면 차이를 적은 자리에서도 비율의 문자열이 잡힌다.
    그 자리는 연도를 부르지 않으므로 짝을 세우지도 무너뜨리지도 않는다. 여기서는 그 자리가
    제대로 묶인 자리보다 문장 앞에 오게 두어, 먼저 잡힌 자리 하나로 판정하지 않는지 본다.
    """
    result = derived_delta_result()

    result.calculations[0].value = "1.0000"

    payload = build_grounded_payload(result, question=Q08_QUESTION)

    assert payload.calculations[0].display_value == "1.0000%"
    assert payload.calculations[2].display_value == "-1.0000%p"

    answer = (
        "한미반도체의 연결 기준 영업이익률은 두 연도의 차이가 -1.0000%p이며, "
        "2024년 1.0000%, 2025년 9.0000%입니다."
    )

    validate_answer(parse_model_answer(reply_declaring(payload, answer)), payload)


def test_q11_same_year_comparison_is_not_bound_by_period() -> None:
    """
    같은 해의 두 기업을 견준 결과에는 연도 짝을 요구하지 않는다.

    그 문장에서 값을 가르는 축은 연도가 아니라 기업명이다. 두 비율이 같은 해에 걸려 있으므로
    연도로는 어느 쪽도 특정되지 않으며, 그 축은 _require_preserved_relation이 이미 본다.
    """
    result = margin_comparison_result()

    payload = build_grounded_payload(result, question=Q11_QUESTION)

    # 규칙이 켜지지 않는 이유를 payload에서 확인한다. 연도가 하나뿐이다.
    assert {item.fiscal_year for item in payload.calculations} == {2025}

    validate_answer(
        parse_model_answer(reply_declaring(payload, build_answer_response(result).answer)),
        payload,
    )


def test_q07_between_period_values_are_not_bound_to_a_year() -> None:
    """
    두 기간 사이의 값은 회계연도 자리가 비어 있어 짝을 요구받지 않는다.

    증감액을 한쪽 해에 묶으면 그것이 그 해의 값으로 읽힌다. Q07의 두 Calculation이 모두 그
    자리이며, 원천 값은 Fact가 들고 있다.
    """
    result = temporal_delta_result()

    payload = build_grounded_payload(
        result, question="한미반도체 매출이 작년보다 얼마나 늘었어?"
    )

    assert {item.fiscal_year for item in payload.calculations} == {None}

    validate_answer(
        parse_model_answer(reply_declaring(payload, build_answer_response(result).answer)),
        payload,
    )


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


# --------------------------------------------------------------------------
# PR #81 blocker 3 - 동등 판정이 문장에 남는다
#
# 우열 표현이 없다는 것만으로는 동등 판정을 옮겼다고 볼 수 없다. 값만 나란히 적은 문장은
# 어느 쪽이 큰지 묻는 질문에 답하지 않은 것이고, 견주는 일이 사용자에게 넘어간다.
# --------------------------------------------------------------------------


EQUAL_VALUES_SENTENCE = "A사의 2025년 연결 기준 매출액은 100원, B사는 100원입니다."


def equal_payload() -> GroundedPayload:
    return comparison_payload(ComparisonRelation.EQUAL, left_value=100, right_value=100)


def test_equal_answer_without_an_equality_claim_is_rejected() -> None:
    """리뷰가 지적한 모양이다. 수치는 맞지만 확정된 판정이 문장 어디에도 없다."""
    assert comparison_code(EQUAL_VALUES_SENTENCE, equal_payload()) == "relation_dropped"


@pytest.mark.parametrize(
    "claim",
    [
        "두 값은 같습니다.",
        "두 값이 같습니다.",
        "두 수치는 동일합니다.",
        "두 금액은 같습니다.",
        "양쪽 값은 같습니다.",
        "둘의 수치는 동일합니다.",
    ],
)
def test_equal_answer_stating_equality_passes(claim: str) -> None:
    """수량사와 값 명사가 붙어 있는 꼴만 인정한다."""
    answer = f"{EQUAL_VALUES_SENTENCE} {claim}"

    client = ScriptedClient(comparison_reply(answer))

    assert verbalize(equal_payload(), client=client) == answer


@pytest.mark.parametrize(
    "claim",
    [
        "두 기업은 동일 업종입니다.",
        "두 회사는 동일합니다.",
        "두 회사의 연결 기준에는 차이가 없습니다.",
        "차이가 없습니다.",
    ],
)
def test_equality_of_something_other_than_the_values_is_not_an_equality_claim(
    claim: str,
) -> None:
    """
    동등의 주어가 값이 아니면 판정을 옮긴 문장이 아니다.

    업종·회계기준·기업 자체가 같다는 말은 견준 수치에 대해 아무 말도 하지 않는다. 이런
    문장을 동등 진술로 세면 판정이 빠진 답변이 통과하고, 값을 견주는 일은 사용자에게 남는다.
    """
    answer = f"{EQUAL_VALUES_SENTENCE} {claim}"

    assert comparison_code(answer, equal_payload()) == "relation_dropped"


@pytest.mark.parametrize(
    "claim",
    [
        "두 값은 같은 기준으로 계산되었습니다.",
        "두 금액은 동일한 단위로 표시되었습니다.",
        "두 수치는 같은 방식으로 산출되었습니다.",
    ],
)
def test_attributive_form_is_not_an_equality_predicate(claim: str) -> None:
    """
    '같은'·'동일한'은 뒤에 오는 명사를 꾸미는 자리다.

    계산 기준이나 표시 단위가 같다는 말이지 값이 같다는 말이 아니다. 어간까지만 보면 이런
    문장이 동등 진술로 세어지고, 판정이 빠진 답변이 통과한다.
    """
    answer = f"{EQUAL_VALUES_SENTENCE} {claim}"

    assert comparison_code(answer, equal_payload()) == "relation_dropped"


def test_ranking_answer_appending_an_equality_claim_is_rejected() -> None:
    """우열을 말하고 나서 두 값이 같다고 덧붙이면 판정이 두 개가 된다."""
    payload = comparison_payload(ComparisonRelation.LEFT_GREATER)

    answer = f"{VALUES_SENTENCE} A사 쪽이 더 큽니다. 두 값은 같습니다."

    assert comparison_code(answer, payload) == "relation_contradiction"


def test_equality_claim_is_rejected_even_when_the_ranking_is_correct() -> None:
    """
    우열 진술이 판정과 맞아도 통과시키지 않는다.

    한 답변에 서로 반대인 두 진술이 있으면 사용자가 어느 쪽을 믿을지 고르게 되고, 그 선택은
    확정된 결과와 무관하다.
    """
    payload = comparison_payload(ComparisonRelation.RIGHT_GREATER, left_value=90, right_value=100)

    answer = f"{SWAPPED_VALUES_SENTENCE} B사 쪽이 더 큽니다. 두 값은 동일합니다."

    assert comparison_code(answer, payload) == "relation_contradiction"


def test_listing_phrase_does_not_count_as_an_equality_claim() -> None:
    """
    '다음과 같습니다'는 동등 진술이 아니다.

    '같습니다'만으로 판정하면 이런 문장이 동등 진술로 읽히고, 우열 판정이 실린 정상 답변이
    근거 없이 거절된다.
    """
    payload = comparison_payload(ComparisonRelation.LEFT_GREATER)

    answer = f"두 회사의 매출액은 다음과 같습니다. {VALUES_SENTENCE} A사 쪽이 더 큽니다."

    client = ScriptedClient(comparison_reply(answer))

    assert verbalize(payload, client=client) == answer


def test_equal_answer_with_a_ranking_claim_is_still_rejected() -> None:
    """동등 진술을 요구하게 됐어도 우열 진술 금지가 사라지지 않는다."""
    answer = f"{EQUAL_VALUES_SENTENCE} 두 값은 같습니다. 다만 A사가 더 큽니다."

    assert comparison_code(answer, equal_payload()) == "relation_contradiction"
