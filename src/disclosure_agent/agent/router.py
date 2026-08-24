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

공통 출력 Contract:
- Pattern이 무엇이든 answer_question은 AgentExecutionResult 하나를 돌려준다. Pattern마다
  결과 모양이 다르면 받는 쪽이 Pattern을 알아야 하고, Pattern이 늘 때마다 호출부가 바뀐다.
- Pattern 내부 표현으로 옮기는 변환은 agent.normalization이 맡고, Router는 그 결과를
  가공하지 않는다.
- QueryPlan은 이 Contract에 싣지 않는다. 실행 결과가 아니라 질문 해석 결과이기 때문이다.

입력:
- QueryPlan, corpus 루트, manifest 경로

출력:
- AgentExecutionResult. 라우팅 단계에서 막힌 경우도 같은 타입으로 답한다.

TODO:
- Pattern C(temporal_compare) 등록
- 복합 질문의 처리 순서 정의
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from disclosure_agent.agent.answer_builder import (
    STAGE_FILING_DISCOVERY,
    STAGE_QUERY_PLAN,
    STAGE_ROUTING,
)
from disclosure_agent.agent.handlers.event_history import handle_event_history
from disclosure_agent.agent.handlers.single_fact import handle_single_fact
from disclosure_agent.agent.normalization import blocked_execution
from disclosure_agent.agent.query_analyzer import (
    KNOWN_INTENTS,
    QueryPlan,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.common.enums import Intent
from disclosure_agent.common.schemas import AgentExecutionResult
from disclosure_agent.retrieval.metadata_filter import (
    FilingNotFoundError,
    list_company_names,
    load_manifest,
)

REASON_UNIMPLEMENTED_INTENT = "unimplemented_intent"
REASON_UNKNOWN_INTENT = "unknown_intent"

# intent -> handler. 등록된 것만 실행한다.
#
# key는 Contract v0.1의 Intent를 쓴다. StrEnum이라 문자열 key와 같은 항목이 되므로,
# QueryPlan.intent가 문자열이어도 조회된다.
Handler = Callable[..., AgentExecutionResult]

HANDLERS: dict[str, Handler] = {
    Intent.SINGLE_FACT: handle_single_fact,
    Intent.EVENT_HISTORY: handle_event_history,
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


def _blocked_route(plan: QueryPlan) -> AgentExecutionResult:
    """
    handler가 없는 intent에 대한 blocked 결과를 만든다.

    어휘에 있는데 아직 구현되지 않은 경우와, 어휘 자체에 없는 경우를 구분한다.
    전자는 구현 대기이고 후자는 QueryPlan을 만든 쪽의 문제다.

    두 경우 모두 답변 가능성을 판정하지 않는다. 코퍼스를 보지 않았으므로 답이 있는지 없는지
    알지 못한다.
    """
    supported = ", ".join(supported_intents())

    if plan.intent in KNOWN_INTENTS:
        return blocked_execution(
            STAGE_ROUTING,
            REASON_UNIMPLEMENTED_INTENT,
            (
                f"intent '{plan.intent}'는 설계에는 있으나 아직 구현된 handler가 없습니다. "
                f"현재 실행 가능한 intent: {supported}"
            ),
            warnings=plan.warnings,
        )

    return blocked_execution(
        STAGE_ROUTING,
        REASON_UNKNOWN_INTENT,
        (
            f"알 수 없는 intent입니다: '{plan.intent}'. "
            f"현재 실행 가능한 intent: {supported}"
        ),
        warnings=plan.warnings,
    )


def dispatch(
    plan: QueryPlan,
    *,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> AgentExecutionResult:
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
) -> AgentExecutionResult:
    """
    질문 문자열 하나를 처음부터 끝까지 처리한다.

    질문 -> QueryPlan -> handler 순서로, 각 단계의 실패를 해당 stage의 실행 결과로 남긴다.
    Pattern에 관계없이 AgentExecutionResult 하나를 돌려준다.

    인식할 기업명은 manifest에서 읽어 넘긴다. query_analyzer가 기업명을 추측하지 않게 하기
    위해서이며, 목록을 만들지 못하면 질문 해석 이전에 막힌다.

    예상하지 못한 예외는 여기서 삼키지 않는다. 실행 실패(ExecutionStatus.FAILED)의 경계는
    API/Runtime 통합에서 정할 사항이며, 넓은 except로 앞질러 정하면 원인이 사라진다.
    """
    corpus_root = Path(corpus_root)

    resolved_manifest = (
        Path(manifest_path) if manifest_path else corpus_root / "manifest.jsonl"
    )

    try:
        known_companies = list_company_names(load_manifest(resolved_manifest))

    except FilingNotFoundError as error:
        return blocked_execution(STAGE_FILING_DISCOVERY, error.reason, error.detail)

    try:
        plan = build_query_plan(question, known_companies=known_companies)

    except UnsupportedQuestionError as error:
        return blocked_execution(STAGE_QUERY_PLAN, error.reason, error.detail)

    return dispatch(plan, corpus_root=corpus_root, manifest_path=resolved_manifest)
