"""공급계약 공시 내용 조회 질문의 질의 해석 테스트.

무엇을 고정하는가:
    "삼성전자 2025년 공급계약 알려줘"는 공급계약 공시를 이름으로 지목하고 그 내용을 묻지만,
    이력 표현도 지원하는 계약 필드 표기도 쓰지 않는다. 이 구현에는 그 질문을 실행할 carrier가
    없다.

    이 파일은 그런 질문이 **답하지 않는 쪽으로** 끝나는 것과, 끝내는 사유가 실제로 일어난 일과
    맞는 것을 고정한다. 그리고 그 거절이 이미 답하던 질문들을 함께 끌어가지 않는 것을 같은
    무게로 고정한다.

왜 Pattern E로 보내지 않는가(#141 리뷰 blocker 2):
    실측으로 확인했다. "삼성전자 2025년 공급계약 알려줘"는 원공시가 1건으로 확정되어
    COMPLETED/ANSWERABLE로 끝나지만 돌아오는 문장은

        "관련 공시 2건으로 이어지는 이력을 확인했습니다. 확인된 변경 항목은 1건입니다.
         계약상대 변경 전 …, 변경 후 …"

    이다. 계약금액도 계약기간도 계약명도 없다. Fact 0건, Event 0건, ResolvedEventState 0건이며
    EventHistory와 EventLink만 나온다. 질문은 계약이 무엇인지 물었고 답은 그 계약의 어디가
    정정되었는지를 말한다. 형식이 맞고 근거까지 갖춘 답이라 받는 쪽에서 드러나지 않으므로,
    답하지 않는 쪽이 맞다.

왜 요청 항목을 진단에 싣는가(#141 리뷰 blocker 1):
    "…공급계약 상대방 알려줘"처럼 이 구현이 읽지 못하는 항목을 지목한 질문이 있다. 그 항목을
    없는 셈 치고 유형·기간만으로 계획을 만들면 사용자가 물은 것이 계획에도 결과에도 남지
    않는다. 얼개를 걷어내고 남은 낱말을 거절 detail에 그대로 싣는다.

무엇을 고정하지 않는가:
    HTTP 경계와 실측 코퍼스 동작은 tests/integration/test_supply_contract_lookup_corpus.py가
    본다. 이 파일은 질의 해석까지다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.query_analyzer import (
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
from disclosure_agent.common.enums import MetricConcept
from disclosure_agent.retrieval.metadata_filter import DOCUMENT_CLASS_EVENT

KNOWN_COMPANIES = ("삼성전자", "LG에너지솔루션", "한화오션", "레인보우로보틱스")

REASON_UNSUPPORTED_CONTRACT_FIELDS = "unsupported_contract_fields"

# #141 리뷰가 지목한 다섯 질문. 1번은 유형만 밝힌 조회이고, 나머지 넷은 이 구현이 읽지 못하는
# 항목을 명시적으로 지목한 요청이다. 다섯 모두 generic event lookup으로 흡수되면 안 된다.
TYPE_ONLY_REQUEST = "삼성전자 2025년 공급계약 알려줘"

ATTRIBUTE_REQUESTS = (
    ("삼성전자 2025년 공급계약 상대방 알려줘", "상대방"),
    ("삼성전자 2025년 공급계약 계약명 알려줘", "계약명"),
    ("삼성전자 2025년 공급계약 공급지역 알려줘", "공급지역"),
    ("삼성전자 2025년 공급계약 주요 내용 알려줘", "내용"),
)

ATTRIBUTE_IDS = ["counterparty", "contract-name", "supply-region", "main-content"]

# evaluation/questions.jsonl의 Q14 원문. 기존 상대방 표기(랑 + 맺)가 그대로 읽혀야 한다.
Q14 = "LG에너지솔루션이 포드랑 맺었던 배터리 공급계약, 지금 어떻게 됐어?"

# Q02 계열. 계약 필드를 명시한 질문은 예전대로 contract_fact여야 한다.
Q02_SHAPED = "한화오션이 2025년 9월 Yang Ming과 체결한 계약의 계약금액과 계약기간은?"


def _plan(question: str) -> QueryPlan:
    return build_query_plan(question, KNOWN_COMPANIES)


def _refusal(question: str) -> UnsupportedQuestionError:
    with pytest.raises(UnsupportedQuestionError) as raised:
        _plan(question)

    return raised.value


# --------------------------------------------------------------------------
# 1. blocker 2 — 유형만 밝힌 조회는 실행하지 않는다
# --------------------------------------------------------------------------


def test_type_only_supply_contract_request_is_refused() -> None:
    """
    유형만 밝힌 공급계약 조회가 계획을 만들지 않고 거절되는지 본다.

    계획을 만들지 않는 것이 핵심이다. 계획이 만들어지면 Router가 그것을 실행하고, 어느
    handler로 가든 질문이 물은 계약 내용이 아닌 것을 답하게 된다.
    """
    error = _refusal(TYPE_ONLY_REQUEST)

    assert error.reason == REASON_UNSUPPORTED_CONTRACT_FIELDS


def test_type_only_request_never_produces_an_event_history_plan() -> None:
    """
    Pattern E로 흡수되지 않는지를 계획의 유무로 직접 고정한다.

    거절 사유만 보면 나중에 이 질문이 다시 event_history 계획을 만들어도 그 계획이 다른
    자리에서 막히는 한 위 테스트가 통과한다. 계획 자체가 만들어지지 않는다는 사실을 따로
    박아 둔다.
    """
    plans: list[QueryPlan] = []

    with pytest.raises(UnsupportedQuestionError):
        plans.append(_plan(TYPE_ONLY_REQUEST))

    assert plans == []


def test_type_only_request_does_not_stop_on_the_metric_vocabulary() -> None:
    """
    막힌 사유가 unsupported_metric이 아닌지 본다.

    질문은 지표를 묻지 않았다. 지표 어휘로 막으면 무엇을 구현하면 풀리는 질문인지가 결과에서
    사라진다. #141 리뷰가 명시적으로 요구한 조건이다.
    """
    assert _refusal(TYPE_ONLY_REQUEST).reason != "unsupported_metric"


def test_type_only_refusal_says_which_combination_is_answerable() -> None:
    """
    거절 detail이 답할 수 있는 조합을 알려 주는지 본다.

    사유만으로는 질문을 어떻게 고쳐야 답을 받을 수 있는지 알 수 없다. 지원하는 필드 이름이
    detail에 있어야 한다.
    """
    detail = _refusal(TYPE_ONLY_REQUEST).detail

    for field in SUPPORTED_CONTRACT_FIELDS:
        assert field.value in detail


# --------------------------------------------------------------------------
# 2. blocker 1 — 읽지 못한 요청 항목을 조용히 버리지 않는다
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("question", "residue"), ATTRIBUTE_REQUESTS, ids=ATTRIBUTE_IDS)
def test_attribute_requests_are_not_absorbed_into_generic_lookup(
    question: str,
    residue: str,
) -> None:
    """
    이 구현이 읽지 못하는 항목을 지목한 질문이 generic lookup으로 흡수되지 않는지 본다.

    #141 리뷰의 최소 요구다. 네 질문 모두 계획이 만들어지지 않아야 하며, 특히
    INTENT_EVENT_HISTORY로 들어가 requested_fields=()인 채 실행되면 안 된다. 그렇게 되면
    사용자가 물은 항목이 계획에도 결과에도 남지 않는다.
    """
    assert _refusal(question).reason == REASON_UNSUPPORTED_CONTRACT_FIELDS


@pytest.mark.parametrize(("question", "residue"), ATTRIBUTE_REQUESTS, ids=ATTRIBUTE_IDS)
def test_unreadable_request_item_is_named_in_the_refusal(
    question: str,
    residue: str,
) -> None:
    """
    읽지 못한 항목이 진단에 그대로 남는지 본다.

    이것이 '조용히 버리지 않는다'의 실질이다. 사유만 돌려주면 사용자는 무엇이 문제인지 알 수
    없고, 같은 질문을 조금 바꿔 다시 물어도 같은 벽에 부딪힌다.

    낱말을 변형하지 않으므로 질문에 적힌 표기가 그대로 나온다.
    """
    assert residue in _refusal(question).detail


def test_type_only_request_names_no_residue() -> None:
    """
    얼개만으로 이뤄진 질문에는 읽지 못한 항목이 없다.

    residue 판정이 아무 낱말이나 남기는 것이 아니라는 대조군이다. 이것이 없으면 위 네
    테스트는 '항상 무언가 남는다'로도 통과한다.
    """
    detail = _refusal(TYPE_ONLY_REQUEST).detail

    assert "요구한 항목" not in detail
    assert "어떤 값을 묻는지" in detail


def test_recognized_counterparty_is_scaffolding_not_residue() -> None:
    """
    상대방으로 읽은 이름과 그 '관련'은 얼개이므로 읽지 못한 항목이 아니다.

    걷어내지 않으면 이해한 표현이 이해하지 못한 것으로 보고된다.
    """
    detail = _refusal("삼성전자 2025년 Ford 관련 공급계약 알려줘").detail

    assert "Ford" not in detail
    assert "관련" not in detail


# --------------------------------------------------------------------------
# 3. 상대방 '관련' 표기는 이력 질문에서 계속 의미가 있다
# --------------------------------------------------------------------------


def test_relation_form_still_reads_a_counterparty_in_history_questions() -> None:
    """
    '관련' 표기가 남아 있어야 하는 자리를 고정한다.

    조회 경로는 사라졌지만 이력 질문은 그대로 Pattern E로 간다. 거기서 이 표기를 읽지 못하면
    질문이 건 상대방 조건이 무시된 채 원공시 1건의 이력이 답으로 나간다. 이름을 읽어야
    handler가 unverified_counterparty로 막을 수 있다.
    """
    plan = _plan("삼성전자 2025년 Ford 관련 공급계약 뭐가 바뀌었어?")

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.document_subtype == EVENT_SUBTYPE_SUPPLY_CONTRACT
    # Ford는 이 코퍼스의 corp_name이 아니다. 대상 기업 자리는 건드리지 않는다.
    assert plan.companies == ("삼성전자",)
    assert plan.counterparty == "Ford"


def test_relation_form_reads_the_postposition_variant_in_history_questions() -> None:
    """'Ford와 관련된'도 같은 이름을 읽는다. 조사는 선택이다."""
    plan = _plan("삼성전자 2025년 Ford와 관련된 공급계약 뭐가 바뀌었어?")

    assert plan.counterparty == "Ford"


def test_multi_word_latin_counterparty_is_read_whole() -> None:
    """
    공백이 들어간 라틴 표기를 잘라 읽지 않는다.

    잘라 읽으면 'Ford'만 담기고, 질문이 적은 이름과 계획이 담은 이름이 달라진다.
    """
    plan = _plan("삼성전자 2025년 Ford Motor Company 관련 공급계약 뭐가 바뀌었어?")

    assert plan.counterparty == "Ford Motor Company"


def test_hangul_word_before_the_relation_marker_is_not_read_as_a_counterparty() -> None:
    """
    '공급계약 관련'의 '공급계약'이 상대방으로 읽히지 않는지 본다.

    '관련' 앞자리에는 이 모듈이 이미 다른 규칙으로 읽고 있는 한글 낱말이 그대로 온다.
    한글까지 열면 유형을 가리킨 말이 상대방이 되고, 답할 수 있던 이력 질문이
    unverified_counterparty로 막힌다. 그래서 '관련' 형태는 라틴 표기만 읽는다.
    """
    plan = _plan("LG에너지솔루션 2024년 공급계약 관련 뭐가 바뀌었어?")

    assert plan.intent == INTENT_EVENT_HISTORY
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
    """
    plan = _plan("한화오션 2025년 Ford 관련 공급계약 계약금액이랑 계약기간 알려줘")

    assert plan.intent == INTENT_CONTRACT_FACT
    assert plan.counterparty is None


# --------------------------------------------------------------------------
# 4. 음성 대조군 — 이미 답하던 질문을 끌어가지 않는다
# --------------------------------------------------------------------------


def test_contract_field_question_stays_on_the_q02_path() -> None:
    """
    계약 필드를 명시한 질문은 예전대로 contract_fact다.

    새 거절의 요청 표기('알려')는 Q02 질문에도 들어 있다. 판정 순서를 잘못 두면 Q02가 먼저
    걸려 거절되고, 답하던 질문이 답을 잃는다.
    """
    plan = _plan(Q02_SHAPED)

    assert plan.intent == INTENT_CONTRACT_FACT
    assert set(plan.requested_fields) == SUPPORTED_CONTRACT_FIELDS
    assert plan.counterparty == "Yang Ming"


def test_history_question_stays_on_pattern_e_with_the_existing_counterparty_form() -> None:
    """Q14의 '랑 + 맺' 표기가 그대로 읽히는지 본다."""
    plan = _plan(Q14)

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.document_class == DOCUMENT_CLASS_EVENT
    assert plan.document_subtype == EVENT_SUBTYPE_SUPPLY_CONTRACT
    assert plan.counterparty == "포드"
    # Q14는 공시 시점을 밝히지 않는다. 연도를 만들어 채우지 않는다.
    assert plan.years == ()


@pytest.mark.parametrize(
    "question",
    [
        "삼성전자 2025년 공급계약 금액이 어떻게 바뀌었어?",
        "삼성전자가 2025년에 낸 공급계약 공시, 그 뒤에 정정된 부분이 있어?",
        "삼성전자 2025년 공급계약 변경 내용 알려줘",
    ],
    ids=["changed", "corrected", "change-content"],
)
def test_history_expressions_still_take_precedence(question: str) -> None:
    """
    이력 표현이 있는 질문은 예전과 같은 판정에 먼저 걸린다.

    세 번째 질문이 경계다. '내용 알려줘'가 붙어 있어 새 거절의 조건과 겹치지만 '변경'이
    있으므로 이력 질문이며, 그 판정이 훨씬 앞에서 먼저 끝난다.
    """
    plan = _plan(question)

    assert plan.intent == INTENT_EVENT_HISTORY


def test_metric_question_that_mentions_a_disclosure_type_keeps_its_metric() -> None:
    """
    지표를 물은 질문은 유형 표기가 함께 있어도 지표 경로에 남는다.

    이것이 새 거절의 가장 큰 위험이다. '공급계약'과 '알려줘'만으로 막으면
    "공급계약 관련 매출 알려줘"가 거절되고, 근거까지 갖춰 답하던 매출 질문이 답을 잃는다.
    """
    plan = _plan("삼성전자 2024년 공급계약 관련 매출 알려줘")

    assert plan.intent == INTENT_SINGLE_FACT
    assert plan.metric == MetricConcept.REVENUE
    assert plan.document_class != DOCUMENT_CLASS_EVENT


def test_question_without_a_request_expression_is_not_refused_here() -> None:
    """
    유형 표기만으로는 이 거절을 열지 않는다.

    유형 낱말은 지표 질문 안에도 우연히 들어온다. 요청 표기를 함께 요구하지 않으면 그런
    질문이 전부 계약 필드 사유로 막힌다.
    """
    plan = _plan("삼성전자 2024년 공급계약 매출 얼마야")

    assert plan.intent == INTENT_SINGLE_FACT
    assert plan.metric == MetricConcept.REVENUE


def test_two_disclosure_types_are_refused_as_ambiguous_not_as_contract_fields() -> None:
    """
    유형이 둘이면 유형 모호성이 먼저다.

    계약 필드 사유로 막으면 두 유형을 물었다는 사실이 결과에서 사라지고, 사용자는 유형을
    하나로 좁히면 풀린다는 것을 알 수 없다.
    """
    assert (
        _refusal("삼성전자 2024년 공급계약이랑 시설투자 알려줘").reason
        == "ambiguous_event_subtype"
    )


def test_facility_investment_lookup_is_not_opened_by_this_change() -> None:
    """
    시설투자만 지목한 조회 질문은 이 PR이 건드리지 않는다.

    거절 사유가 계약 필드 어휘로 말하므로 그 유형에는 맞지 않는다. 그 유형에 맞는 사유를
    정하는 것은 이 PR의 범위 밖이며, 여기서는 범위 밖이라는 사실을 값으로 고정한다.
    이 사유가 언젠가 바뀐다면 그것은 의도된 변경이어야 한다.
    """
    assert (
        _refusal("레인보우로보틱스 2024년 시설투자 알려줘").reason == "unsupported_metric"
    )


def test_multi_period_question_still_belongs_to_the_period_axis() -> None:
    """기간이 둘인 질문은 기간 축이 먼저다. 새 거절이 앞지르지 않는다."""
    plan = _plan("삼성전자 2023년이랑 2024년 공급계약 알려줘")

    assert plan.intent == INTENT_TEMPORAL_COMPARE


def test_multi_company_question_still_belongs_to_the_entity_axis() -> None:
    """기업이 둘인 질문은 기업 축이 먼저다."""
    plan = _plan("삼성전자랑 LG에너지솔루션 2024년 공급계약 알려줘")

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE


# --------------------------------------------------------------------------
# 5. 이번 범위 밖의 경계
# --------------------------------------------------------------------------


def test_root_company_is_still_required() -> None:
    """
    상대방만 적은 질문은 기업을 읽지 못한 사유로 막힌다.

    "2025년 Ford 관련 공급계약 알려줘"는 코퍼스 전체를 상대방으로 뒤지는 조회이며, 그 경로는
    어느 계층에도 없다. 기업 판정이 계약 필드 판정보다 훨씬 앞이므로 사유도 그쪽이다.
    """
    assert _refusal("2025년 Ford 관련 공급계약 알려줘").reason == "unknown_company"
