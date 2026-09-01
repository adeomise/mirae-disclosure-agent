"""Q12 모양(기업 간 집계 비교)이 실수로 열리지 않는지 검증한다.

무엇을 막는가:
    Q12는 "2024년에 LG이노텍하고 레인보우로보틱스가 새로 발표한 시설투자, 회사별로 다
    합치면 어디가 더 커?"다. 대상 연도의 거래소공시를 기업별로 모아 최신 정정값을 반영한 뒤
    합산해야 답할 수 있으며(agent_patterns.md Pattern D 집계 경로), 그 경로는 아직 없다.

    이 질문은 query_analyzer에서 `intent=cross_entity_compare`로 분류되면서
    **metric도 derived_metric도 비어 있는 상태**로 계획에 담긴다. Pattern D handler가
    Router에 등록되는 순간, 이 계획은 Q10·Q11과 같은 입구로 들어온다. 입구에서 걸러 내지
    않으면 두 가지 중 하나가 일어난다.

    - 지표를 읽지 못한 계획에 기본값(예: 매출)을 채워 엉뚱한 지표로 답한다.
    - 정기공시 경로로 흘려보내 "새로 발표한 시설투자"와 무관한 값을 근거까지 갖춰
      COMPLETED/ANSWERABLE로 돌려준다.

    둘 다 형식은 맞고 질문에는 답하지 않은 결과이며, 받는 쪽이 틀렸다는 것을 알 수 없다.
    GUARD-MULTI-COMPANY가 막았던 것과 같은 종류의 실패다.

왜 정지 단계를 고정하지 않는가:
    Pattern D handler가 등록되기 전에는 Router가 `routing`에서 막았고, 지금은 handler
    입구(`pattern_entry`)가 막는다. Pattern C에서 이미 한 번 일어난 이동이다
    (GUARD-MULTI-PERIOD). 거래소공시 집계 경로가 붙으면 그 자리는 또 옮겨갈 수 있다. 정지
    단계를 고정하면 자리를 옮기는 PR에서 이 테스트가 함께 깨지고, 그 순간 안전 성질까지
    같이 지워질 수 있다.

    그래서 여기서 고정하는 것은 '어디서 막느냐'가 아니라 **'값을 하나도 만들지 않는다'**다.
    이 성질은 막는 자리가 옮겨져도 그대로여야 한다.

언제 이 파일을 고쳐야 하는가:
    Q12의 집계 경로가 실제로 구현되면 이 파일은 유효하지 않다. 그때는 조용히 지우지 말고
    Q12의 최종 목표 모양(Calculation 2 · Comparison 1)을 검증하는 테스트로 대체한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from disclosure_agent.agent.query_analyzer import (
    INTENT_CROSS_ENTITY_COMPARE,
    QueryPlan,
    build_query_plan,
)
from disclosure_agent.agent.router import answer_question
from disclosure_agent.common.enums import AnswerabilityStatus
from disclosure_agent.common.schemas import AgentExecutionResult

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"

DEFAULT_CORPUS_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "3.공시" / "3.공시" / "corpus"
)

# evaluation/questions.jsonl의 Q12 원문.
Q12 = (
    "2024년에 LG이노텍하고 레인보우로보틱스가 새로 발표한 시설투자, "
    "회사별로 다 합치면 어디가 더 커?"
)

# 같은 모양의 합성 질문. 합성 manifest에 있는 두 기업을 쓴다.
SYNTHETIC_Q12 = (
    "2025년에 테스트반도체하고 테스트전자가 새로 발표한 시설투자, "
    "회사별로 다 합치면 어디가 더 커?"
)

# 대조군. 같은 Pattern D 경로이지만 지표를 읽어 낸 계획이다.
SYNTHETIC_Q10 = "2025년 테스트반도체랑 테스트전자 매출 비교해줘"

SYNTHETIC_COMPANIES = ("테스트반도체(주)", "테스트전자")

REAL_COMPANIES = ("LG이노텍", "레인보우로보틱스", "HD현대중공업", "삼성중공업")

ANSWERED = {
    AnswerabilityStatus.ANSWERABLE,
    AnswerabilityStatus.PARTIALLY_ANSWERABLE,
}


def _resolve_corpus_root() -> Path | None:
    raw = os.environ.get(CORPUS_ROOT_ENV)

    candidate = Path(raw) if raw else DEFAULT_CORPUS_ROOT

    if candidate.is_dir() and (candidate / "manifest.jsonl").is_file():
        return candidate

    return None


def _assert_no_value_was_produced(result: AgentExecutionResult) -> None:
    """
    값이 하나도 만들어지지 않았고, 멈춘 이유가 결과에 남아 있어야 한다.

    정지 단계는 보지 않는다. Router에서 막든 handler 입구에서 막든 이 성질은 같다.
    """
    assert result.facts == []
    assert result.evidence == []
    assert result.calculations == []
    assert result.comparisons == []
    assert result.events == []
    assert result.event_histories == []

    assert result.answerability_status not in ANSWERED

    # 조용히 빈 결과를 돌려주지 않는다. 실행이 막혔거나(execution_issue), 답할 수 없다고
    # 판정했거나(answerability_reason) 둘 중 하나가 남아 있어야 원인을 되짚을 수 있다.
    assert result.execution_issue is not None or result.answerability_reason is not None


# --------------------------------------------------------------------------
# 1. 계획 — Q12는 지표 없이 Pattern D 입구에 도착한다
#
# 계획 단계의 성질이므로 Router에 handler가 등록돼도 달라지지 않는다.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "known_companies"),
    [(Q12, REAL_COMPANIES), (SYNTHETIC_Q12, SYNTHETIC_COMPANIES)],
)
def test_q12_shaped_plan_reaches_pattern_d_without_a_metric(
    question: str, known_companies: tuple[str, ...]
) -> None:
    """
    Pattern D 입구가 확인해야 할 신호가 바로 이것이다.

    기업은 둘인데 metric도 derived_metric도 비어 있다. 이 상태에서 지표를 추측하면
    질문과 다른 값을 답하게 된다.
    """
    plan: QueryPlan = build_query_plan(question, known_companies=known_companies)

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert len(plan.companies) == 2
    assert plan.metric is None
    assert plan.derived_metric is None


def test_metric_bearing_pattern_d_plan_is_told_apart_from_the_q12_shape() -> None:
    """대조군. 같은 경로라도 Q10 모양은 지표를 읽어 낸다."""
    plan = build_query_plan(SYNTHETIC_Q10, known_companies=SYNTHETIC_COMPANIES)

    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.metric is not None
    assert plan.derived_metric is None


# --------------------------------------------------------------------------
# 2. 실행 — Q12 모양은 값을 하나도 만들지 않는다
# --------------------------------------------------------------------------


def test_q12_shaped_question_produces_no_value(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    Fact 하나라도 남으면 지표를 추측했거나 다른 경로로 흘려보냈다는 뜻이다.

    ComparisonResult가 남으면 집계 없이 우열을 판정했다는 뜻이다. 둘 다 이 가드가 막으려는
    동작이며, Pattern D handler가 등록된 뒤에도 같아야 한다.
    """
    result = answer_question(
        SYNTHETIC_Q12,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    _assert_no_value_was_produced(result)


# --------------------------------------------------------------------------
# 3. 실측 코퍼스 — 대표 질문 Q12 원문
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_q12_is_not_answered_in_the_actual_corpus() -> None:
    """대표 질문 원문 그대로 실행해도 값이 만들어지지 않아야 한다."""
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    _assert_no_value_was_produced(answer_question(Q12, corpus_root=corpus_root))
