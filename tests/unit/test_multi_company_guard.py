"""다중 기업 질문이 한 기업으로 축소되지 않는지 검증한다.

무엇을 막는가:
    "2025년 HD현대중공업이랑 삼성중공업 매출 비교해줘"처럼 두 기업을 묻는 질문이
    single_fact로 흘러가면, 한 기업의 Fact 1건이 Evidence까지 갖춘 채
    COMPLETED/ANSWERABLE로 돌아간다. 형식은 맞지만 질문에는 답하지 않은 결과이고,
    두 번째 기업이 사라졌다는 사실이 warnings에도 conversion_gaps에도 남지 않는다.

    지원하지 않는다고 답하는 것보다 이쪽이 위험하다. 받는 쪽이 틀렸다는 것을 알 수 없기
    때문이다. 그래서 '조용히 틀린 답'이 아니라 'routing 단계에서 막혔다'로 답하게 한다.

무엇을 검증하지 않는가:
    Pattern D(기업 간 비교)의 실제 동작은 검증하지 않는다. handler가 없기 때문이다.
    여기서 고정하는 것은 '축소해서 답하지 않는다'까지다.

false positive:
    조사나 구분자로 판정하지 않는다. 실제 목록에 있는 회사명이 서로 다른 자리에서
    매치될 때만 여러 기업이다. 이름이 서로를 포함하는 경우(LG생활건강 / LG)에 한 기업이
    둘로 세어지지 않아야 하며, 그 대조군을 아래 3절에 둔다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from disclosure_agent.agent.answer_builder import STAGE_ROUTING
from disclosure_agent.agent.query_analyzer import (
    INTENT_CROSS_ENTITY_COMPARE,
    INTENT_EVENT_HISTORY,
    INTENT_SINGLE_FACT,
    QueryPlan,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.agent.router import REASON_UNIMPLEMENTED_INTENT, answer_question
from disclosure_agent.common.enums import ExecutionStatus

# 실측 코퍼스에 실제로 있는 기업명. 대표 질문 Q10이 이 둘을 묻는다.
REAL_COMPANIES = ("HD현대중공업", "삼성중공업", "삼성전자", "SK하이닉스", "한미반도체")

Q10_QUESTION = "2025년 HD현대중공업이랑 삼성중공업 매출 비교해줘"

Q10_VARIANT_QUESTION = "삼성전자와 SK하이닉스 2024년 매출 비교해줘"


# --------------------------------------------------------------------------
# 1. 다중 기업 질문은 cross_entity_compare로 분류된다
# --------------------------------------------------------------------------


@pytest.mark.parametrize("question", [Q10_QUESTION, Q10_VARIANT_QUESTION])
def test_multi_company_question_is_classified_as_cross_entity_compare(
    question: str,
) -> None:
    """
    기존 어휘를 쓴다.

    cross_entity_compare는 이미 KNOWN_INTENTS에 있다. 새 intent를 만들면 Router가
    '어휘에 없는 intent'로 분류해 원인이 QueryPlan을 만든 쪽 문제로 바뀐다.
    """
    plan = build_query_plan(question, known_companies=REAL_COMPANIES)

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE


def test_multi_company_plan_keeps_every_matched_company() -> None:
    """두 기업을 물었으면 계획에도 두 기업이 남아야 한다. 순서는 질문에 나온 순서다."""
    plan = build_query_plan(Q10_QUESTION, known_companies=REAL_COMPANIES)

    assert plan.companies == ("HD현대중공업", "삼성중공업")


def test_multi_company_question_does_not_reach_a_single_fact_plan() -> None:
    """single_fact로 새어 나가면 기업 하나만 남은 답이 근거까지 갖춰 돌아간다."""
    plan = build_query_plan(Q10_VARIANT_QUESTION, known_companies=REAL_COMPANIES)

    assert plan.intent != INTENT_SINGLE_FACT
    assert plan.intent != INTENT_EVENT_HISTORY


def test_multi_company_event_history_question_is_also_guarded() -> None:
    """
    이력 질문에도 같은 구멍이 있다.

    다중 기업 판정을 의도 분기보다 먼저 하지 않으면, Pattern E가 먼저 기업 하나를 골라
    한 기업의 정정 이력만 답하게 된다.
    """
    plan = build_query_plan(
        "HD현대중공업이랑 삼성중공업이 2024년에 공시한 공급계약, 바뀐 내용 있어?",
        known_companies=REAL_COMPANIES,
    )

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE


# --------------------------------------------------------------------------
# 2. Router는 routing 단계에서 막는다
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected_companies"),
    [
        ("2025년 테스트반도체랑 테스트전자 매출 비교해줘", ("테스트반도체(주)", "테스트전자")),
        ("테스트전자와 테스트반도체 2025년 매출 비교해줘", ("테스트전자", "테스트반도체(주)")),
    ],
)
def test_multi_company_question_is_blocked_end_to_end(
    question: str,
    expected_companies: tuple[str, ...],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    질문 문자열부터 실행 결과까지 한 번에 본다.

    facts와 evidence가 비어 있어야 한다. 하나라도 남으면 '두 기업 중 하나를 골라
    답했다'는 뜻이고, 그것이 이 가드가 막으려는 동작이다.
    """
    plan = build_query_plan(question, known_companies=expected_companies)

    assert plan.companies == expected_companies

    result = answer_question(
        question,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.facts == []
    assert result.evidence == []

    # 답변 가능성은 판정하지 않는다. 코퍼스를 보지 않았으므로 답이 있는지 알지 못한다.
    assert result.answerability_status is None
    assert result.answerability_reason is None

    issue = result.execution_issue

    assert issue is not None
    assert issue.stage == STAGE_ROUTING
    assert issue.reason == REASON_UNIMPLEMENTED_INTENT


def test_blocked_multi_company_result_names_the_missing_capability(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """무엇을 구현하면 풀리는 질문인지가 결과에 남아야 후속 작업을 고를 수 있다."""
    result = answer_question(
        "2025년 테스트반도체랑 테스트전자 매출 비교해줘",
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_issue is not None
    assert INTENT_CROSS_ENTITY_COMPARE in result.execution_issue.detail


# --------------------------------------------------------------------------
# 3. false positive 대조군
#
# 여기가 무너지면 정상 질문까지 막힌다. 다중 기업 판정보다 이쪽이 회귀 비용이 크다.
# --------------------------------------------------------------------------


def test_company_name_containing_another_name_counts_once() -> None:
    """
    'LG생활건강'을 물었는데 'LG'가 또 매치되어 두 기업이 되면 안 된다.

    긴 이름이 차지한 구간을 짧은 이름이 다시 가져가지 못하게 하는 규칙을 고정한다.
    """
    plan = build_query_plan(
        "LG생활건강 2025년 연결 기준 매출액이 얼마야?",
        known_companies=("LG생활건강", "LG"),
    )

    assert plan.companies == ("LG생활건강",)
    assert plan.intent == INTENT_SINGLE_FACT


def test_shorter_name_sharing_a_prefix_does_not_split_one_company() -> None:
    """한미반도체를 물었는데 한미약품 목록 때문에 갈라지지 않는다."""
    plan = build_query_plan(
        "한미반도체 2025년 연결 기준 매출액이 얼마야?",
        known_companies=("한미반도체", "한미약품", "한미"),
    )

    assert plan.companies == ("한미반도체",)


def test_same_company_mentioned_twice_is_still_one_company() -> None:
    """같은 기업을 두 번 부른 것은 두 기업이 아니다."""
    plan = build_query_plan(
        "한미반도체 2025년 매출, 그러니까 한미반도체 연결 기준으로 얼마야?",
        known_companies=("한미반도체",),
    )

    assert plan.companies == ("한미반도체",)
    assert plan.intent == INTENT_SINGLE_FACT


def test_same_company_listed_under_different_notations_is_one_company() -> None:
    """
    목록에 표기만 다른 같은 기업이 들어 있어도 한 곳이다.

    manifest는 '(주)'를 붙인 표기와 붙이지 않은 표기를 함께 담을 수 있다. 정규화 후
    같은 이름이면 같은 기업으로 본다.
    """
    plan = build_query_plan(
        "한미반도체 2025년 연결 기준 매출액이 얼마야?",
        known_companies=("한미반도체", "한미반도체(주)", "한미반도체 주식회사"),
    )

    assert len(plan.companies) == 1


def test_conjunction_alone_does_not_make_a_question_multi_company() -> None:
    """
    조사만으로 판정하지 않는다.

    '~랑'이 있어도 목록에 있는 회사명이 하나면 한 기업이다. 문자열 split으로 판정하면
    이 질문이 막히고, 정상 질문을 막는 쪽이 회귀로서 더 비싸다.
    """
    plan = build_query_plan(
        "한미반도체랑 계약한 곳들 말고, 한미반도체 2025년 연결 매출액 알려줘",
        known_companies=REAL_COMPANIES,
    )

    assert plan.companies == ("한미반도체",)
    assert plan.intent == INTENT_SINGLE_FACT


def test_unknown_company_still_reports_unknown_company() -> None:
    """목록에 없는 기업은 여전히 추측하지 않는다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan("없는회사 2025년 연결 매출액", known_companies=REAL_COMPANIES)

    assert error.value.reason == "unknown_company"


# --------------------------------------------------------------------------
# 4. QueryPlan 표현
#
# 정본은 companies 하나다. company는 거기서 읽기만 한다.
# --------------------------------------------------------------------------


def test_company_property_reads_the_first_of_companies() -> None:
    """Pattern A·E의 기존 호출부가 그대로 동작해야 한다."""
    plan = build_query_plan(
        "한미반도체 2025년 연결 기준 매출액이 얼마야?",
        known_companies=("한미반도체",),
    )

    assert plan.company == "한미반도체"
    assert plan.company == plan.companies[0]


def test_company_cannot_be_assigned_independently() -> None:
    """
    company에 따로 값을 넣을 수 없어야 한다.

    쓸 수 있으면 companies와 어긋난 상태가 만들어지고, 어느 쪽이 정본인지 코드를 읽어야만
    알 수 있게 된다. 구조적으로 불가능해야 한다.
    """
    plan = build_query_plan(
        "한미반도체 2025년 연결 기준 매출액이 얼마야?",
        known_companies=("한미반도체",),
    )

    with pytest.raises(AttributeError):
        plan.company = "삼성전자"  # type: ignore[misc]


def test_serialized_plan_carries_companies_only() -> None:
    """파생 값을 함께 실으면 읽는 쪽이 무엇이 정본인지 알 수 없게 된다."""
    plan = build_query_plan(
        "한미반도체 2025년 연결 기준 매출액이 얼마야?",
        known_companies=("한미반도체",),
    )

    dumped = plan.to_dict()

    assert dumped["companies"] == ("한미반도체",)
    assert "company" not in dumped


def test_query_plan_rejects_a_bare_company_string() -> None:
    """
    문자열을 그대로 받으면 글자 단위로 순회되어 '한미반도체'가 다섯 기업이 된다.

    조용히 통과시키면 계획을 만든 쪽이 아니라 답을 받는 쪽에서 틀린다.
    """
    with pytest.raises(TypeError):
        QueryPlan(question="합성", companies="한미반도체", year=2025)  # type: ignore[arg-type]


def test_query_plan_rejects_an_empty_company_set() -> None:
    with pytest.raises(ValueError):
        QueryPlan(question="합성", companies=(), year=2025)
