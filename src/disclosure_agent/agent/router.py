"""Agent 요청 라우팅 모듈.

역할:
- 구조화된 질의를 정기공시, 이벤트공시, 검색, 계산 처리 경로에 배정한다.

현재 구현 범위:
- QueryPlan.intent 하나만 보고 handler를 고른다. 문서군이나 질문 문자열을 다시 들여다보지
  않는다. 라우팅 근거를 한 곳에 모아 두어야 어떤 질문이 어디로 갔는지 재현할 수 있다.
- 등록된 intent는 single_fact(Pattern A) 하나다.
- 등록되지 않은 intent는 조용히 다른 경로로 넘기지 않고 blocked 결과로 답한다.

지원하지 않는 요청에 대한 메모:
- 가장 가까운 handler로 대신 처리하지 않는다. Pattern이 다르면 검증 조건과 필요한 Evidence
  건수가 다르므로, 대신 처리한 결과는 형식만 맞고 근거가 틀린 답이 된다.
- 그래서 "왜 처리할 수 없는지"를 두 가지로 나눠 답한다. 어휘에는 있으나 아직 구현되지 않은
  intent와, 어휘에 없는 intent는 후속 작업이 서로 다르다.

Pattern 추가 방법:
- agent/handlers/에 모듈을 만들고 아래 HANDLERS에 한 줄 등록한다.
- 테스트에서 임시로 붙이려면 register_handler를 쓴다.

입력:
- QueryPlan, corpus 루트, manifest 경로

출력:
- 각 handler가 만든 결과 dict, 또는 라우팅 단계의 blocked 결과

TODO:
- Pattern C(temporal_compare), Pattern E(event_history) 등록
- 복합 질문의 처리 순서 정의
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from disclosure_agent.agent.answer_builder import (
    STAGE_FILING_DISCOVERY,
    STAGE_QUERY_PLAN,
    STAGE_ROUTING,
    build_blocked_result,
)
from disclosure_agent.agent.handlers.single_fact import handle_single_fact
from disclosure_agent.agent.query_analyzer import (
    INTENT_SINGLE_FACT,
    KNOWN_INTENTS,
    QueryPlan,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.retrieval.metadata_filter import (
    FilingNotFoundError,
    list_company_names,
    load_manifest,
)

REASON_UNIMPLEMENTED_INTENT = "unimplemented_intent"
REASON_UNKNOWN_INTENT = "unknown_intent"

# intent -> handler. 등록된 것만 실행한다.
Handler = Callable[..., dict[str, Any]]

HANDLERS: dict[str, Handler] = {
    INTENT_SINGLE_FACT: handle_single_fact,
}


def register_handler(intent: str, handler: Handler) -> None:
    """
    intent에 handler를 등록한다.

    Pattern을 추가할 때 HANDLERS를 직접 고치는 대신 쓸 수 있고, 테스트가 임시 handler를
    붙일 때도 쓴다.
    """
    HANDLERS[intent] = handler


def resolve_handler(intent: str) -> Handler | None:
    """intent에 등록된 handler. 없으면 None."""
    return HANDLERS.get(intent)


def supported_intents() -> tuple[str, ...]:
    """현재 실행 가능한 intent 목록."""
    return tuple(sorted(HANDLERS))


def _blocked_route(plan: QueryPlan) -> dict[str, Any]:
    """
    handler가 없는 intent에 대한 blocked 결과를 만든다.

    어휘에 있는데 아직 구현되지 않은 경우와, 어휘 자체에 없는 경우를 구분한다.
    전자는 구현 대기이고 후자는 QueryPlan을 만든 쪽의 문제다.
    """
    supported = ", ".join(supported_intents())

    if plan.intent in KNOWN_INTENTS:
        return build_blocked_result(
            plan,
            stage=STAGE_ROUTING,
            reason=REASON_UNIMPLEMENTED_INTENT,
            detail=(
                f"intent '{plan.intent}'는 설계에는 있으나 아직 구현된 handler가 없습니다. "
                f"현재 실행 가능한 intent: {supported}"
            ),
        )

    return build_blocked_result(
        plan,
        stage=STAGE_ROUTING,
        reason=REASON_UNKNOWN_INTENT,
        detail=(
            f"알 수 없는 intent입니다: '{plan.intent}'. "
            f"현재 실행 가능한 intent: {supported}"
        ),
    )


def dispatch(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    QueryPlan을 담당 handler로 넘긴다.

    handler가 없으면 대신 처리하지 않고 blocked 결과를 돌려준다.
    """
    handler = resolve_handler(plan.intent)

    if handler is None:
        return _blocked_route(plan)

    return handler(plan, corpus_root=corpus_root, manifest_path=manifest_path)


def answer_question(
    question: str,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    질문 문자열 하나를 처음부터 끝까지 처리한다.

    질문 -> QueryPlan -> handler 순서로, 각 단계의 실패를 해당 stage의 blocked 결과로 남긴다.

    인식할 기업명은 manifest에서 읽어 넘긴다. query_analyzer가 기업명을 추측하지 않게 하기
    위해서이며, 목록을 만들지 못하면 질문 해석 이전에 막힌다.
    """
    corpus_root = Path(corpus_root)

    resolved_manifest = (
        Path(manifest_path) if manifest_path else corpus_root / "manifest.jsonl"
    )

    try:
        known_companies = list_company_names(load_manifest(resolved_manifest))

    except FilingNotFoundError as error:
        return build_blocked_result(
            None,
            stage=STAGE_FILING_DISCOVERY,
            reason=error.reason,
            detail=error.detail,
        )

    try:
        plan = build_query_plan(question, known_companies=known_companies)

    except UnsupportedQuestionError as error:
        return build_blocked_result(
            None,
            stage=STAGE_QUERY_PLAN,
            reason=error.reason,
            detail=error.detail,
        )

    return dispatch(plan, corpus_root=corpus_root, manifest_path=resolved_manifest)
