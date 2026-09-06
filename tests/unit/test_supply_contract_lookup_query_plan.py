"""유형만 밝힌 거래소공시 조회의 질의 해석 테스트.

무엇을 고정하는가:
    "삼성전자 2024년 공급계약 알려줘"는 이력 표현도 계약 필드 표기도 쓰지 않는다. 그래서
    _looks_like_event_history에도 _looks_like_contract_fact에도 걸리지 않고 Pattern A의 지표
    판정까지 흘러 내려가 unsupported_metric으로 막혔다. 질문은 지표를 묻지 않았으므로 그
    사유는 실제로 일어난 일과 달랐다.

    이 파일은 그 질문이 거래소공시 경로로 가는 것과, 가는 과정에서 다른 질문이 함께 끌려오지
    않는 것을 같은 무게로 고정한다. 어휘와 경로를 넓히는 변경의 위험은 '열리지 않는다'가
    아니라 '엉뚱한 것까지 열린다'이기 때문이다. 그래서 아래 절반이 음성 대조군이다.

    "삼성전자 2024년 Ford 관련 공급계약 알려줘"도 같은 자리다. 여기서 Ford는 대상 기업이
    아니라 계약 상대방이며, 계획의 counterparty에 담긴다. 그 값이 조회 조건이 아니라
    fail-close 신호라는 성질은 예전과 같다(agent.query_analyzer의
    _extract_event_counterparty).

무엇을 고정하지 않는가:
    문서를 몇 건 찾는지, 그중 하나로 좁혀지는지는 코퍼스가 정한다. 이 파일은 질의 해석까지만
    본다. 실제 후보 개수에 따른 판정은 tests/integration/test_supply_contract_lookup_corpus.py가
    실측 코퍼스로 고정한다.

    코퍼스 전체를 상대방만으로 뒤지는 조회(root 기업 없는 질문)는 이번 범위 밖이다. 그
    경계도 아래에 대조군으로 박아 둔다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.query_analyzer import (
    EVENT_SUBTYPE_FACILITY_INVESTMENT,
    EVENT_SUBTYPE_SUPPLY_CONTRACT,
    INTENT_CONTRACT_FACT,
    INTENT_CROSS_ENTITY_COMPARE,
    INTENT_EVENT_HISTORY,
    INTENT_SINGLE_FACT,
    INTENT_TEMPORAL_COMPARE,
    SUPPORTED_CONTRACT_FIELDS,
    QueryPlan,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.common.enums import MetricConcept, QueryAccountingScope
from disclosure_agent.retrieval.metadata_filter import DOCUMENT_CLASS_EVENT

KNOWN_COMPANIES = ("삼성전자", "LG에너지솔루션", "한화오션", "레인보우로보틱스")

# 이번 변경이 열어야 하는 두 질문.
TARGET_PLAIN = "삼성전자 2024년 공급계약 알려줘"
TARGET_WITH_COUNTERPARTY = "삼성전자 2024년 Ford 관련 공급계약 알려줘"

# evaluation/questions.jsonl의 Q14 원문. 기존 상대방 표기(랑 + 맺)가 그대로 읽혀야 한다.
Q14 = "LG에너지솔루션이 포드랑 맺었던 배터리 공급계약, 지금 어떻게 됐어?"

# Q02 계열. 계약 필드를 명시한 질문은 예전대로 contract_fact여야 한다.
Q02_SHAPED = "한화오션이 2025년 9월 Yang Ming과 체결한 계약의 계약금액과 계약기간은?"


def _plan(question: str) -> QueryPlan:
    return build_query_plan(question, KNOWN_COMPANIES)


def _blocked_reason(question: str) -> str:
    with pytest.raises(UnsupportedQuestionError) as raised:
        _plan(question)

    return raised.value.reason


# --------------------------------------------------------------------------
# 1. 열려야 하는 두 질문
# --------------------------------------------------------------------------


def test_type_only_supply_contract_question_reaches_the_event_path() -> None:
    """
    유형만 밝힌 공급계약 질문이 거래소공시 계획이 되는지 전부 본다.

    intent를 single_fact로 두지 않는 이유:
        Router는 intent 하나만 보고 handler를 고른다. single_fact로 담기면 이 계획이 정기공시
        Pattern A handler로 들어가고, 거래소공시를 물은 질문에 회계 지표 경로가 실행된다.
        실제로는 그 전에 _extract_metric이 막았고, 막힌 사유가 unsupported_metric이었다.
    """
    plan = _plan(TARGET_PLAIN)

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.document_class == DOCUMENT_CLASS_EVENT
    assert plan.document_subtype == EVENT_SUBTYPE_SUPPLY_CONTRACT
    assert plan.companies == ("삼성전자",)
    assert plan.years == (2024,)
    # 거래소공시에는 연결·별도 구분이 없다.
    assert plan.accounting_scope == QueryAccountingScope.NOT_APPLICABLE.value
    # 지표를 묻지 않은 질문이다. 계획이 지표를 만들어 넣지 않는다.
    assert plan.metric is None
    # 계약 필드를 묻지 않은 질문이다. 기본 필드를 골라 채우지 않는다.
    assert plan.requested_fields == ()
    # 상대방을 적지 않았다. 추정하지 않는다.
    assert plan.counterparty is None


def test_type_only_question_no_longer_stops_on_the_metric_vocabulary() -> None:
    """
    막힌 사유가 사라졌는지를 사유 이름으로 직접 고정한다.

    위 테스트가 intent를 보는 것과 따로 두는 이유:
        intent가 맞아도 다른 가드가 같은 질문을 unsupported_metric으로 막으면 이 변경은
        목적을 이루지 못한다. '어디로 갔는가'와 '무엇으로도 막히지 않았는가'는 서로 다른
        사실이므로 각각 고정한다.
    """
    # 예외가 나지 않는 것 자체가 검증이다. 나면 여기서 그대로 실패한다.
    _plan(TARGET_PLAIN)


def test_counterparty_relation_form_is_read_without_becoming_the_root_company() -> None:
    """
    'Ford 관련'이 상대방으로 읽히고, 대상 기업 자리는 건드리지 않는지 본다.

    두 가지를 한자리에서 보는 이유:
        이 질문의 위험은 둘이며 방향이 반대다. 하나는 Ford를 못 읽는 것이고, 다른 하나는
        Ford를 대상 기업으로 읽어 삼성전자를 밀어내는 것이다. 뒤쪽이 일어나면 질문이 물은
        기업의 공시가 아닌 곳을 뒤지게 되므로, 읽었다는 사실만으로는 검증이 되지 않는다.
    """
    plan = _plan(TARGET_WITH_COUNTERPARTY)

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.document_subtype == EVENT_SUBTYPE_SUPPLY_CONTRACT
    # Ford는 이 코퍼스의 corp_name이 아니다. 대상 기업은 삼성전자 하나뿐이어야 한다.
    assert plan.companies == ("삼성전자",)
    assert plan.counterparty == "Ford"
    assert plan.years == (2024,)


def test_counterparty_relation_form_reads_the_postposition_variant() -> None:
    """'Ford와 관련된'도 같은 이름을 읽는다. 조사는 선택이다."""
    plan = _plan("삼성전자 2024년 Ford와 관련된 공급계약 알려줘")

    assert plan.counterparty == "Ford"
    assert plan.companies == ("삼성전자",)


def test_multi_word_latin_counterparty_is_read_whole() -> None:
    """
    공백이 들어간 라틴 표기를 잘라 읽지 않는다.

    잘라 읽으면 'Ford'만 담기고, 질문이 적은 이름과 계획이 담은 이름이 달라진다. 이 값은
    조회 조건이 아니지만 진단 문자열에 그대로 나가므로, 사용자가 쓴 이름과 같아야 한다.
    """
    plan = _plan("삼성전자 2024년 Ford Motor Company 관련 공급계약 알려줘")

    assert plan.counterparty == "Ford Motor Company"


# --------------------------------------------------------------------------
# 2. 음성 대조군 — 기존 경로가 끌려오지 않는다
# --------------------------------------------------------------------------


def test_contract_field_question_stays_on_the_q02_path() -> None:
    """
    계약 필드를 명시한 질문은 예전대로 contract_fact다.

    이 대조군이 필요한 이유:
        새 경로의 요청 표기('알려')는 Q02 질문에도 들어 있다. 판정 순서를 잘못 두면 Q02가
        먼저 잡혀 requested_fields가 계획에서 사라지고, 질문이 요구한 값을 답하지 못한다.
    """
    plan = _plan(Q02_SHAPED)

    assert plan.intent == INTENT_CONTRACT_FACT
    assert set(plan.requested_fields) == SUPPORTED_CONTRACT_FIELDS
    assert plan.counterparty == "Yang Ming"


def test_history_question_stays_on_pattern_e_with_the_existing_counterparty_form() -> None:
    """
    Q14의 '랑 + 맺' 표기가 그대로 읽히는지 본다.

    상대방 표현을 하나 더한 변경이므로, 기존 표현이 새 표현에 가려지지 않는 것을 함께
    고정한다.
    """
    plan = _plan(Q14)

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.document_subtype == EVENT_SUBTYPE_SUPPLY_CONTRACT
    assert plan.counterparty == "포드"
    # Q14는 공시 시점을 밝히지 않는다. 연도를 만들어 채우지 않는다.
    assert plan.years == ()


@pytest.mark.parametrize(
    "question",
    [
        "삼성전자 2024년 공급계약 금액이 어떻게 바뀌었어?",
        "삼성전자가 2024년에 낸 공급계약 공시, 그 뒤에 정정된 부분이 있어?",
    ],
)
def test_history_expressions_still_take_precedence(question: str) -> None:
    """이력 표현이 있는 질문은 예전과 같은 판정에 먼저 걸린다."""
    plan = _plan(question)

    assert plan.intent == INTENT_EVENT_HISTORY


def test_metric_question_that_mentions_a_disclosure_type_keeps_its_metric() -> None:
    """
    지표를 물은 질문은 유형 표기가 함께 있어도 지표 경로에 남는다.

    이것이 새 판정의 가장 큰 위험이다. '공급계약'과 '알려줘'만으로 경로를 열면
    "공급계약 관련 매출 알려줘"가 거래소공시 조회로 끌려가고, 근거까지 갖춰 답하던 매출
    질문이 답을 잃는다. 그래서 지표를 하나라도 읽으면 이 경로를 열지 않는다.
    """
    plan = _plan("삼성전자 2024년 공급계약 관련 매출 알려줘")

    assert plan.intent == INTENT_SINGLE_FACT
    assert plan.metric == MetricConcept.REVENUE
    assert plan.document_class != DOCUMENT_CLASS_EVENT


def test_derived_metric_question_that_mentions_a_disclosure_type_is_left_alone() -> None:
    """
    파생지표를 물은 질문도 같다.

    _extract_metric은 파생지표를 읽지 못해 이 질문을 unsupported_metric으로 막는다. 그
    판정은 이 변경의 대상이 아니므로, 새 경로가 가로채 사유를 바꾸지 않는 것까지 고정한다.
    """
    assert _blocked_reason("삼성전자 2024년 공급계약 영업이익률 알려줘") == "unsupported_metric"


def test_question_without_a_request_expression_is_not_rerouted() -> None:
    """
    유형 표기만으로는 경로를 열지 않는다.

    유형 낱말은 지표 질문 안에도 우연히 들어온다. 요청 표기를 함께 요구하지 않으면 그런
    질문이 전부 거래소공시 조회로 끌려간다.
    """
    plan = _plan("삼성전자 2024년 공급계약 매출 얼마야")

    assert plan.intent == INTENT_SINGLE_FACT
    assert plan.metric == MetricConcept.REVENUE


def test_hangul_word_before_the_relation_marker_is_not_read_as_a_counterparty() -> None:
    """
    '공급계약 관련'의 '공급계약'이 상대방으로 읽히지 않는지 본다.

    '관련' 앞자리에는 이 모듈이 이미 다른 규칙으로 읽고 있는 한글 낱말이 그대로 온다.
    한글까지 열면 유형을 가리킨 말이 상대방이 되고, 답할 수 있던 질문이
    unverified_counterparty로 막힌다. 그래서 '관련' 형태는 라틴 표기만 읽는다.
    """
    plan = _plan("LG에너지솔루션 2024년 공급계약 관련 알려줘")

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.document_subtype == EVENT_SUBTYPE_SUPPLY_CONTRACT
    # 유형을 가리킨 낱말이 상대방 자리에 담기지 않는다.
    assert plan.counterparty is None


def test_relation_form_is_not_added_to_the_document_selecting_grammar() -> None:
    """
    Q02의 상대방 표기는 넓히지 않는다.

    그 경로는 읽은 이름으로 후보 문서를 좁힌다. '관련'처럼 당사자를 보증하지 않는 말로
    문서를 고르면 틀린 계약의 금액이 형식만 맞은 채 답이 되고, 받는 쪽에서 드러나지 않는다.
    이쪽 계획은 상대방 없이 만들어지고, 막는 자리는 handler의 missing_counterparty다.

    '계약금액이랑 계약기간'으로 적는 이유:
        '계약금액과 계약기간'으로 적으면 COUNTERPARTY_PATTERN이 '계약금액'을 상대방으로
        읽는다. 거기서 '과'는 두 필드를 잇는 접속 조사인데 그 패턴의 조사 목록에 있고
        뒤따르는 '계약'도 목록에 있어서다. 이 변경 이전부터 그랬고 여기서 고치지 않는다.
        이 테스트가 보려는 것은 '관련' 형태가 그 패턴에 들어가지 않았다는 사실 하나이므로,
        그 사실만 남도록 다른 표기를 쓴다. EVENT_COUNTERPARTY_PATTERN이 같은 이유로 조사
        목록에서 '이랑'을 다루는 방식을 주석에 적어 두고 있다.
    """
    plan = _plan("한화오션 2025년 Ford 관련 공급계약 계약금액이랑 계약기간 알려줘")

    assert plan.intent == INTENT_CONTRACT_FACT
    assert plan.counterparty is None


def test_two_disclosure_types_are_still_refused_without_choosing_one() -> None:
    """유형이 둘이면 새 경로도 하나를 고르지 않는다."""
    assert (
        _blocked_reason("삼성전자 2024년 공급계약이랑 시설투자 알려줘")
        == "ambiguous_event_subtype"
    )


def test_facility_investment_lookup_opens_on_the_same_rule() -> None:
    """
    유형 판정은 공급계약에만 걸려 있지 않다.

    새 경로가 한 유형만 아는 규칙이 되면, 같은 모양의 시설투자 질문이 그대로 막힌 채 남고
    왜 한쪽만 열렸는지가 코드에 남지 않는다. EVENT_SUBTYPE_RULES 하나가 정하는 것을 고정한다.
    """
    plan = _plan("레인보우로보틱스 2024년 시설투자 알려줘")

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.document_subtype == EVENT_SUBTYPE_FACILITY_INVESTMENT


def test_multi_period_question_still_belongs_to_the_period_axis() -> None:
    """기간이 둘인 질문은 기간 축이 먼저다. 새 경로가 앞지르지 않는다."""
    plan = _plan("삼성전자 2023년이랑 2024년 공급계약 알려줘")

    assert plan.intent == INTENT_TEMPORAL_COMPARE


def test_multi_company_question_still_belongs_to_the_entity_axis() -> None:
    """기업이 둘인 질문은 기업 축이 먼저다."""
    plan = _plan("삼성전자랑 LG에너지솔루션 2024년 공급계약 알려줘")

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE


# --------------------------------------------------------------------------
# 3. 이번 범위 밖의 경계
# --------------------------------------------------------------------------


def test_root_company_is_still_required() -> None:
    """
    상대방만 적은 질문은 그대로 막힌다.

    "2024년 Ford 관련 공급계약 알려줘"는 코퍼스 전체를 상대방으로 뒤지는 조회이며, 그
    경로는 어느 계층에도 없다(retrieval.metadata_filter의 조회 진입점이 전부 company를
    필수로 받는다). 이 변경이 그 경계를 옮기지 않는다는 것을 여기서 고정한다.

    사유를 함께 보는 이유:
        막히는 것만 확인하면 나중에 다른 이유로 막혀도 이 테스트가 통과한다. 지금 막는 것은
        기업을 하나도 읽지 못했다는 사실이며, 그 사실이 사유에 남아야 한다.
    """
    assert _blocked_reason("2024년 Ford 관련 공급계약 알려줘") == "unknown_company"
