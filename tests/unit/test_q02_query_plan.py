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

import json
from pathlib import Path

import pytest

from disclosure_agent.agent.answer_builder import STAGE_QUERY_PLAN
from disclosure_agent.agent.derived_metrics import DerivedMetric
from disclosure_agent.agent.query_analyzer import (
    DOCUMENT_CLASS_PERIODIC,
    EVENT_SUBTYPE_SUPPLY_CONTRACT,
    INTENT_CONTRACT_FACT,
    INTENT_EVENT_HISTORY,
    INTENT_SINGLE_FACT,
    INTENT_TEMPORAL_COMPARE,
    SUPPORTED_CONTRACT_FIELDS,
    QueryPlan,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.agent.router import answer_question
from disclosure_agent.common.enums import (
    ExecutionStatus,
    MetricConcept,
    QueryAccountingScope,
)
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
# 2. 지원하는 필드 조합만 계획이 된다
#
# 계획이 열어 주는 범위는 answer_builder가 문장으로 옮길 수 있는 범위를 넘지 않는다.
# 넘으면 ANSWERABLE로 판정된 결과가 문장을 만드는 자리에서 터지고, 그때는 막힌 이유가
# 결과에 남지 않은 채 HTTP 500이 된다.
# --------------------------------------------------------------------------


def test_amount_and_period_question_requests_the_supported_shape() -> None:
    """계약금액과 계약기간을 함께 물은 질문은 지금까지처럼 계획이 된다."""
    plan = _plan(Q02)

    assert set(plan.requested_fields) == SUPPORTED_CONTRACT_FIELDS
    assert plan.requested_fields == (
        MetricConcept.CONTRACT_AMOUNT,
        *CONTRACT_PERIOD_FIELDS,
    )


def test_amount_only_question_is_blocked_at_query_plan() -> None:
    """
    계약금액만 물은 질문은 계획 단계에서 막는다.

    예전에는 requested_fields에 계약금액 하나만 담아 계획을 만들었다. 그 계획은 handler까지
    통과한 뒤 answer_builder에서 터졌고, 결과가 이미 ANSWERABLE로 판정된 자리라 막힌 이유가
    남지 않았다. 지금은 여기서 막으므로 사유가 query_plan 단계에 남는다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        _plan("한화오션의 2025년 9월 계약금액은?")

    assert error.value.reason == "unsupported_contract_fields"


def test_period_only_question_is_blocked_at_query_plan() -> None:
    """
    계약기간만 물은 질문도 함께 막는다.

    이 형태는 answer_builder가 문장으로 옮길 수 있다. 그런데도 막는 이유는, 열어 두면
    planner가 여는 범위와 builder가 지원하는 범위가 두 벌로 갈리기 때문이다. 넓히는 쪽이
    늘 나중이어야 두 범위가 조용히 어긋나지 않는다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        _plan("한화오션의 2025년 9월 계약기간은?")

    assert error.value.reason == "unsupported_contract_fields"


def test_unsupported_field_detail_names_the_supported_shape() -> None:
    """무엇을 물어야 답할 수 있는지가 사유에 남지 않으면 질문을 고칠 수 없다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        _plan("한화오션의 2025년 9월 계약금액은?")

    detail = error.value.detail

    assert "계약금액과 계약기간을 함께" in detail

    for field in SUPPORTED_CONTRACT_FIELDS:
        assert field.value in detail


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
    plan = _plan("한화오션의 2025년 9월 계약금액이랑 계약기간은?")

    assert plan.counterparty is None


def test_target_company_is_not_read_as_counterparty() -> None:
    """주체를 상대방 자리에 담으면 그 이름으로 후보를 좁혀 아무 문서도 찾지 못한다."""
    plan = _plan("2025년 9월 한화오션과 체결한 계약의 계약금액과 계약기간은?")

    assert plan.companies == ("한화오션",)
    assert plan.counterparty is None


def test_korean_counterparty_is_read_without_the_particle() -> None:
    """조사까지 상대방 이름에 담으면 코퍼스의 어떤 이름과도 맞지 않는다."""
    plan = _plan("한화오션이 2025년 9월 테스트해운과 체결한 계약의 계약금액과 계약기간은?")

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


# --------------------------------------------------------------------------
# 6. 막힌 질문이 실행 경로로 새어 나가지 않는다
# --------------------------------------------------------------------------


def test_unsupported_shape_becomes_a_blocked_result_not_an_exception(
    tmp_path: Path,
) -> None:
    """
    지원하지 않는 필드 조합은 BLOCKED 결과가 되며 예외로 올라가지 않는다.

    이 자리를 보는 이유:
        query_analyzer가 막아도 그 거절이 예외로 호출자까지 올라가면 API에서 500이 된다.
        Router가 UnsupportedQuestionError를 query_plan 단계의 BLOCKED로 옮기는 것이 규약이고,
        이 회귀가 그 연결을 고정한다.

    코퍼스를 열지 않는다:
        막히는 자리가 질의 해석 단계라 문서를 한 번도 읽지 않는다. manifest는 기업 목록을
        읽는 데만 쓰이므로 레코드 한 줄이면 된다. 실제 코퍼스 위의 HTTP 회귀는
        tests/integration/test_api_q02_corpus.py가 따로 본다.
    """
    manifest_path = tmp_path / "manifest.jsonl"
    manifest_path.write_text(
        json.dumps({"corp_name": "한화오션"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    result = answer_question(
        "한화오션의 2025년 9월 계약금액은?",
        corpus_root=tmp_path,
        manifest_path=manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED

    assert result.execution_issue is not None
    assert result.execution_issue.stage == STAGE_QUERY_PLAN
    assert result.execution_issue.reason == "unsupported_contract_fields"

    # 답이 되지 않았으므로 값도 근거도 없다.
    assert result.facts == []
    assert result.evidence == []
