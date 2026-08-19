"""Pattern Router 단위 테스트.

검증 대상:
- intent 하나만 보고 handler를 고르는가
- 등록되지 않은 intent를 조용히 다른 경로로 넘기지 않는가

여기서는 실제 문서를 읽지 않는다. 라우팅 결정만 본다.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from disclosure_agent.agent import router
from disclosure_agent.agent.answer_builder import STATUS_BLOCKED, STATUS_OK
from disclosure_agent.agent.handlers.event_history import handle_event_history
from disclosure_agent.agent.handlers.single_fact import handle_single_fact
from disclosure_agent.agent.query_analyzer import (
    INTENT_CROSS_ENTITY_COMPARE,
    INTENT_DOCUMENT_SUMMARY,
    INTENT_EVENT_HISTORY,
    INTENT_SINGLE_FACT,
    INTENT_TEMPORAL_COMPARE,
    INTENT_UNANSWERABLE_CHECK,
    QueryPlan,
    build_query_plan,
)

SYNTHETIC_QUESTION = "테스트반도체 2025년 연결 기준 매출액이 얼마야?"

SYNTHETIC_COMPANIES = ("테스트반도체",)


@pytest.fixture
def single_fact_plan() -> QueryPlan:
    return build_query_plan(SYNTHETIC_QUESTION, known_companies=SYNTHETIC_COMPANIES)


@pytest.fixture
def isolated_registry():
    """
    HANDLERS는 모듈 전역이므로 테스트가 등록한 handler가 다른 테스트로 새지 않게 되돌린다.
    """
    original = dict(router.HANDLERS)

    yield router.HANDLERS

    router.HANDLERS.clear()
    router.HANDLERS.update(original)


# --------------------------------------------------------------------------
# 1. 등록된 intent
# --------------------------------------------------------------------------


def test_build_query_plan_marks_intent_as_single_fact(single_fact_plan: QueryPlan) -> None:
    """이번 슬라이스가 만드는 계획은 모두 single_fact다."""
    assert single_fact_plan.intent == INTENT_SINGLE_FACT


def test_single_fact_intent_resolves_to_pattern_a_handler() -> None:
    assert router.resolve_handler(INTENT_SINGLE_FACT) is handle_single_fact


def test_event_history_intent_resolves_to_pattern_e_handler() -> None:
    assert router.resolve_handler(INTENT_EVENT_HISTORY) is handle_event_history


def test_supported_intents_reports_only_registered_handlers() -> None:
    """Contract v0.1의 Intent 2개가 등록돼 있다."""
    assert router.supported_intents() == (INTENT_EVENT_HISTORY, INTENT_SINGLE_FACT)


def test_dispatch_passes_plan_and_context_to_handler(
    single_fact_plan: QueryPlan,
    isolated_registry: dict[str, Any],
) -> None:
    """Router는 결과를 가공하지 않고 handler에 그대로 넘긴다."""
    received: dict[str, Any] = {}

    def fake_handler(plan: QueryPlan, **context: Any) -> dict[str, Any]:
        received["plan"] = plan
        received["context"] = context

        return {"status": STATUS_OK}

    isolated_registry[INTENT_SINGLE_FACT] = fake_handler

    result = router.dispatch(
        single_fact_plan,
        corpus_root="corpus_root_placeholder",
        manifest_path="manifest_path_placeholder",
    )

    assert result == {"status": STATUS_OK}
    assert received["plan"] is single_fact_plan
    assert received["context"] == {
        "corpus_root": "corpus_root_placeholder",
        "manifest_path": "manifest_path_placeholder",
    }


# --------------------------------------------------------------------------
# 2. 등록되지 않은 intent
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intent",
    [
        INTENT_DOCUMENT_SUMMARY,
        INTENT_TEMPORAL_COMPARE,
        INTENT_CROSS_ENTITY_COMPARE,
        INTENT_UNANSWERABLE_CHECK,
    ],
)
def test_known_but_unimplemented_intent_is_blocked(
    single_fact_plan: QueryPlan,
    intent: str,
) -> None:
    """설계 어휘에는 있으나 handler가 없는 intent는 구현 대기로 구분해 막는다."""
    plan = dataclasses.replace(single_fact_plan, intent=intent)

    result = router.dispatch(plan, corpus_root="unused", manifest_path="unused")

    assert result["status"] == STATUS_BLOCKED
    assert result["blocked"]["stage"] == "routing"
    assert result["blocked"]["reason"] == router.REASON_UNIMPLEMENTED_INTENT
    assert intent in result["blocked"]["detail"]


def test_unknown_intent_is_blocked_with_distinct_reason(single_fact_plan: QueryPlan) -> None:
    """어휘에 없는 intent는 구현 대기와 원인이 다르므로 사유를 나눈다."""
    plan = dataclasses.replace(single_fact_plan, intent="not_a_designed_intent")

    result = router.dispatch(plan, corpus_root="unused", manifest_path="unused")

    assert result["status"] == STATUS_BLOCKED
    assert result["blocked"]["stage"] == "routing"
    assert result["blocked"]["reason"] == router.REASON_UNKNOWN_INTENT


def test_blocked_route_does_not_invent_a_fact(single_fact_plan: QueryPlan) -> None:
    """가장 가까운 handler로 대신 처리하지 않는다."""
    plan = dataclasses.replace(single_fact_plan, intent=INTENT_TEMPORAL_COMPARE)

    result = router.dispatch(plan, corpus_root="unused", manifest_path="unused")

    assert result["fact"] is None
    assert result["evidence"] == []
    assert result["calculations"] == []


def test_blocked_route_preserves_the_plan(single_fact_plan: QueryPlan) -> None:
    """어떤 계획이 거절됐는지 결과에서 확인할 수 있어야 한다."""
    plan = dataclasses.replace(single_fact_plan, intent=INTENT_CROSS_ENTITY_COMPARE)

    result = router.dispatch(plan, corpus_root="unused", manifest_path="unused")

    assert result["query"]["intent"] == INTENT_CROSS_ENTITY_COMPARE
    assert result["query"]["question"] == plan.question


def test_blocked_detail_lists_currently_supported_intents(single_fact_plan: QueryPlan) -> None:
    plan = dataclasses.replace(single_fact_plan, intent=INTENT_CROSS_ENTITY_COMPARE)

    result = router.dispatch(plan, corpus_root="unused", manifest_path="unused")

    assert INTENT_SINGLE_FACT in result["blocked"]["detail"]
    assert INTENT_EVENT_HISTORY in result["blocked"]["detail"]


# --------------------------------------------------------------------------
# 3. Pattern 추가 경로
# --------------------------------------------------------------------------


def test_register_handler_makes_a_new_intent_routable(
    single_fact_plan: QueryPlan,
    isolated_registry: dict[str, Any],
) -> None:
    """Pattern C/E는 handler 등록만으로 붙을 수 있어야 한다."""
    plan = dataclasses.replace(single_fact_plan, intent=INTENT_TEMPORAL_COMPARE)

    before = router.dispatch(plan, corpus_root="unused", manifest_path="unused")

    assert before["status"] == STATUS_BLOCKED

    router.register_handler(
        INTENT_TEMPORAL_COMPARE,
        lambda plan, **context: {"status": STATUS_OK, "routed_intent": plan.intent},
    )

    after = router.dispatch(plan, corpus_root="unused", manifest_path="unused")

    assert after["status"] == STATUS_OK
    assert after["routed_intent"] == INTENT_TEMPORAL_COMPARE


def test_registry_is_restored_between_tests() -> None:
    """isolated_registry가 실제로 되돌리는지 확인한다."""
    assert router.supported_intents() == (INTENT_EVENT_HISTORY, INTENT_SINGLE_FACT)
