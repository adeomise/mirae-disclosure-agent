"""공급계약 단건 사실 조회(Q02) 질의 해석 테스트.

검증 대상:
- 대표 Q02 질문이 계약 조회 계획으로 해석되는가
- 질문이 요구한 필드만 requested_fields에 담기는가
- 계약 상대방을 질문에 적힌 대로만 읽고 추정하지 않는가
- requested_fields가 metric·derived_metric과 배타인가
- 기존 Pattern A·C·E가 이 경로에 끌려오지 않는가

문서 선택과 값 확보는 여기서 보지 않는다. 이 slice가 정하는 것은 질문 해석까지이며,
후보 대조는 agent.event_retrieval이, 값 변환은 event.adapter가 각자 고정하고 있다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.derived_metrics import DerivedMetric
from disclosure_agent.agent.query_analyzer import (
    DOCUMENT_CLASS_PERIODIC,
    EVENT_SUBTYPE_SUPPLY_CONTRACT,
    INTENT_CONTRACT_FACT,
    INTENT_EVENT_HISTORY,
    INTENT_SINGLE_FACT,
    INTENT_TEMPORAL_COMPARE,
    QueryPlan,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.common.enums import MetricConcept, QueryAccountingScope
from disclosure_agent.retrieval.metadata_filter import DOCUMENT_CLASS_EVENT

KNOWN_COMPANIES = ("한화오션",)

Q02 = "한화오션이 2025년 9월 Yang Ming과 체결한 계약의 계약금액과 계약기간은?"

CONTRACT_PERIOD_FIELDS = (
    MetricConcept.CONTRACT_START_DATE,
    MetricConcept.CONTRACT_END_DATE,
)


def _plan(question: str) -> QueryPlan:
    return build_query_plan(question, KNOWN_COMPANIES)


# --------------------------------------------------------------------------
# 1. 대표 Q02
# --------------------------------------------------------------------------


def test_representative_question_becomes_a_contract_fact_plan() -> None:
    """
    대표 Q02가 요구하는 값을 하나도 잃지 않고 계획에 담는지 전부 본다.

    intent를 single_fact로 두지 않는 이유:
        Router는 intent 하나만 보고 handler를 고른다. single_fact로 담으면 이 계획이 정기공시
        Pattern A handler로 들어가 계약 질문에 회계 지표 경로가 실행된다.
    """
    plan = _plan(Q02)

    assert plan.intent == INTENT_CONTRACT_FACT
    assert plan.companies == ("한화오션",)
    assert plan.years == (2025,)
    assert plan.month == 9
    assert plan.document_class == DOCUMENT_CLASS_EVENT
    assert plan.document_subtype == EVENT_SUBTYPE_SUPPLY_CONTRACT
    assert plan.counterparty == "Yang Ming"

    assert plan.requested_fields == (
        MetricConcept.CONTRACT_AMOUNT,
        MetricConcept.CONTRACT_START_DATE,
        MetricConcept.CONTRACT_END_DATE,
    )

    # 요구한 값의 정본은 requested_fields 하나다.
    assert plan.metric is None
    assert plan.derived_metric is None

    # 거래소공시에는 연결·별도 구분이 없다.
    assert plan.accounting_scope == QueryAccountingScope.NOT_APPLICABLE.value


def test_spaced_wording_reads_the_same_fields() -> None:
    """
    "계약 금액과 기간"처럼 앞의 '계약'을 한 번만 쓰는 표현이 실제로 온다.

    같은 질문을 다르게 쓴 두 표현이 서로 다른 계획을 만들면 그 차이가 답의 차이로 새어 나간다.
    """
    spaced = "한화오션이 2025년 9월 Yang Ming과 체결한 계약의 계약 금액과 기간은?"

    assert _plan(spaced).requested_fields == _plan(Q02).requested_fields
    assert _plan(spaced).counterparty == "Yang Ming"


# --------------------------------------------------------------------------
# 2. 요구한 필드만 담는다
# --------------------------------------------------------------------------


def test_amount_only_question_requests_one_field() -> None:
    """묻지 않은 값을 담으면 받는 쪽이 무엇이 답이고 무엇이 부수 정보인지 구분할 수 없다."""
    plan = _plan("한화오션의 2025년 9월 계약금액은?")

    assert plan.requested_fields == (MetricConcept.CONTRACT_AMOUNT,)


def test_period_question_requests_both_dates() -> None:
    """
    기간은 시작일과 종료일이 함께 있어야 서술된다.

    한쪽만 담으면 "2025-09-16부터"까지만 답하고 언제 끝나는지를 물었다는 사실이 사라진다.
    """
    plan = _plan("한화오션의 2025년 9월 계약기간은?")

    assert plan.requested_fields == CONTRACT_PERIOD_FIELDS


# --------------------------------------------------------------------------
# 3. 계약 상대방
# --------------------------------------------------------------------------


def test_counterparty_is_absent_when_the_question_does_not_name_one(
) -> None:
    """
    없으면 추정하지 않는다.

    상대방을 지어내면 그 이름으로 문서를 고르게 되고, 틀린 상대방으로 고른 계약은 형식이 맞아
    틀렸다는 사실이 드러나지 않는다. 후보가 여럿일 때 막는 일은 retrieval 계층이 한다.
    """
    plan = _plan("한화오션의 2025년 9월 계약금액은?")

    assert plan.counterparty is None


def test_target_company_is_not_read_as_counterparty() -> None:
    """주체를 상대방 자리에 담으면 그 이름으로 후보를 좁혀 아무 문서도 찾지 못한다."""
    plan = _plan("2025년 9월 한화오션과 체결한 계약의 계약금액은?")

    assert plan.companies == ("한화오션",)
    assert plan.counterparty is None


def test_korean_counterparty_is_read_without_the_particle() -> None:
    """조사까지 상대방 이름에 담으면 코퍼스의 어떤 이름과도 맞지 않는다."""
    plan = _plan("한화오션이 2025년 9월 테스트해운과 체결한 계약의 계약금액은?")

    assert plan.counterparty == "테스트해운"


# --------------------------------------------------------------------------
# 4. 계획 불변식
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "conflict",
    [
        {"metric": "revenue"},
        {"derived_metric": DerivedMetric.OPERATING_MARGIN},
    ],
)
def test_requested_fields_cannot_coexist_with_a_metric(conflict: dict) -> None:
    """
    셋이 공존하면 계획을 읽는 쪽이 무엇을 답할지 스스로 골라야 하고, 무엇을 골랐는지는
    계획 어디에도 남지 않는다.
    """
    with pytest.raises(ValueError):
        QueryPlan(
            question="q",
            companies=("한화오션",),
            years=(2025,),
            intent=INTENT_CONTRACT_FACT,
            requested_fields=(MetricConcept.CONTRACT_AMOUNT,),
            **conflict,
        )


def test_single_metric_concept_is_rejected() -> None:
    """
    MetricConcept은 StrEnum이라 낱개로 넘기면 글자 단위로 순회된다.

    조용히 통과시키면 필드 하나가 글자 수만큼의 필드로 늘어난다.
    """
    with pytest.raises(TypeError):
        QueryPlan(
            question="q",
            companies=("한화오션",),
            years=(2025,),
            intent=INTENT_CONTRACT_FACT,
            metric=None,
            requested_fields=MetricConcept.CONTRACT_AMOUNT,  # type: ignore[arg-type]
        )


def test_year_is_required_for_a_contract_question() -> None:
    """
    한 기업이 해마다 여러 건의 공급계약을 공시한다.

    시점이 없으면 어느 계약인지 정할 수 없다. 월만 적힌 질문도 마찬가지다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        _plan("한화오션의 9월 계약금액은?")

    assert error.value.reason == "missing_year"


def test_two_event_subtypes_are_refused() -> None:
    """시설투자 공시에도 금액과 기간이 적힌다. 한 유형을 골라 담지 않는다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        _plan("한화오션이 2025년 공장 증설 시설투자로 체결한 계약의 계약금액은?")

    assert error.value.reason == "ambiguous_event_subtype"


# --------------------------------------------------------------------------
# 5. 기존 Pattern을 가로채지 않는다
# --------------------------------------------------------------------------


def test_pattern_a_single_metric_is_unchanged() -> None:
    """'계약'이 없는 질문은 이 경로에 도달하지 않는다."""
    plan = _plan("한화오션의 2025년 매출액은?")

    assert plan.intent == INTENT_SINGLE_FACT
    assert plan.document_class == DOCUMENT_CLASS_PERIODIC
    assert plan.metric == "revenue"
    assert plan.requested_fields == ()
    assert plan.counterparty is None


def test_pattern_c_derived_metric_is_unchanged() -> None:
    """파생 개념은 여전히 derived_metric에 담긴다. 새 자리로 옮겨 가지 않는다."""
    plan = _plan("한화오션의 2024년과 2025년 영업이익률은?")

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.derived_metric is DerivedMetric.OPERATING_MARGIN
    assert plan.metric is None
    assert plan.requested_fields == ()


def test_multi_year_contract_question_stays_on_the_period_axis() -> None:
    """
    기간이 둘인 계약 질문은 기간 축이 먼저다.

    계약 판정을 앞에 두면 두 기간을 물은 질문이 단일 계약 조회로 축소되고, 나머지 기간이
    사라진 흔적이 어디에도 남지 않는다.
    """
    plan = _plan("한화오션의 2024년과 2025년 계약금액은?")

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.years == (2024, 2025)
    assert plan.requested_fields == ()


def test_pattern_e_history_question_is_unchanged() -> None:
    """
    이력 표현이 있으면 계약 필드 표기가 함께 있어도 Pattern E다.

    앞에 두면 실제로 답하고 있던 정정 이력 질문을 가로챈다.
    """
    plan = _plan("한화오션의 2025년 9월 공급계약 계약금액이 어떻게 바뀌었어?")

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.requested_fields == ()
    assert plan.metric is None
