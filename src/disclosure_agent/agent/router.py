"""Agent 요청 라우팅 모듈.

역할:
- 구조화된 질의를 정기공시, 이벤트공시, 검색, 계산 처리 경로에 배정한다.

현재 구현 범위:
- QueryPlan.intent 하나만 보고 handler를 고른다. 문서군이나 질문 문자열을 다시 들여다보지
  않는다. 라우팅 근거를 한 곳에 모아 두어야 어떤 질문이 어디로 갔는지 재현할 수 있다.
- 등록된 intent는 single_fact(Pattern A), document_summary(Pattern B),
  event_history(Pattern E), temporal_compare(Pattern C), cross_entity_compare(Pattern D)
  다섯이다.
- 등록되지 않은 intent는 조용히 다른 경로로 넘기지 않고 blocked 결과로 답한다.

지원하지 않는 요청에 대한 메모:
- 가장 가까운 handler로 대신 처리하지 않는다. Pattern이 다르면 검증 조건과 필요한 Evidence
  건수가 다르므로, 대신 처리한 결과는 형식만 맞고 근거가 틀린 답이 된다.
- 그래서 "왜 처리할 수 없는지"를 두 가지로 나눠 답한다. 어휘에는 있으나 아직 구현되지 않은
  intent와, 어휘에 없는 intent는 후속 작업이 서로 다르다.
- 한 intent 안에서 일부 모양만 지원하는 경우는 Router가 판정하지 않는다. intent만 보는 것이
  이 모듈의 규약이고, 계획의 모양은 그 Pattern이 안다. temporal_compare와
  cross_entity_compare가 그 경우이며, Pattern C·D가 다루지 못하는 계획은 handler 입구에서
  pattern_entry로 막힌다. 막는 자리가 옮겨졌을 뿐 축소해서 답하지 않는 것은 같다.
  document_summary도 같다. Pattern B는 정기공시 단건 요약만 실행하고, 거래소공시 요약과
  기간을 밝히지 않은 요약은 handler 입구에서 막힌다. 거래소공시 단건 요약(Q06)은
  event_summary라는 별도 intent와 별도 handler를 갖는다. 대상 문서를 고르는 규칙이
  회계연도가 아니라 이벤트 체인이어서, 같은 handler에 담으면 Pattern B의 입구 가드를
  넓혀야 하기 때문이다.

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
from disclosure_agent.agent.handlers.contract_fact import handle_contract_fact
from disclosure_agent.agent.handlers.cross_entity_compare import (
    handle_cross_entity_compare,
)
from disclosure_agent.agent.handlers.document_summary import handle_document_summary
from disclosure_agent.agent.handlers.event_history import handle_event_history
from disclosure_agent.agent.handlers.event_summary import handle_event_summary
from disclosure_agent.agent.handlers.holding_fact import handle_holding_fact
from disclosure_agent.agent.handlers.single_fact import handle_single_fact
from disclosure_agent.agent.handlers.temporal_compare import handle_temporal_compare
from disclosure_agent.agent.normalization import (
    answerability_reason_for_policy_category,
    blocked_execution,
    refused_execution,
    stopped_execution,
)
from disclosure_agent.agent.query_analyzer import (
    INTENT_CONTRACT_FACT,
    INTENT_CROSS_ENTITY_COMPARE,
    INTENT_DOCUMENT_SUMMARY,
    INTENT_EVENT_SUMMARY,
    INTENT_HOLDING_FACT,
    INTENT_TEMPORAL_COMPARE,
    KNOWN_INTENTS,
    QueryPlan,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.agent.safety import STAGE_ANSWERABILITY_GATE, screen_question
from disclosure_agent.common.enums import Intent
from disclosure_agent.common.schemas import AgentExecutionResult
from disclosure_agent.retrieval.metadata_filter import (
    FilingNotFoundError,
    latest_annual_fiscal_year_by_company,
    list_company_names,
    list_company_universe,
    load_manifest,
)

REASON_UNIMPLEMENTED_INTENT = "unimplemented_intent"
REASON_UNKNOWN_INTENT = "unknown_intent"

# intent -> handler. 등록된 것만 실행한다.
#
# key는 Contract v0.1의 Intent를 쓴다. StrEnum이라 문자열 key와 같은 항목이 되므로,
# QueryPlan.intent가 문자열이어도 조회된다.
Handler = Callable[..., AgentExecutionResult]

# document_summary, temporal_compare, cross_entity_compare는 Contract v0.1의 Intent에 아직
# 없어 query_analyzer의 문자열 상수를 쓴다. Router가 dispatch하는 것은
# agent.query_analyzer.QueryPlan이고 그 intent는 문자열이므로, 이 key를 등록하기 위해 공유
# Contract의 enum을 넓힐 이유가 없다.
HANDLERS: dict[str, Handler] = {
    Intent.SINGLE_FACT: handle_single_fact,
    Intent.EVENT_HISTORY: handle_event_history,
    INTENT_DOCUMENT_SUMMARY: handle_document_summary,
    # event_summary도 Contract v0.1의 Intent에 아직 없어 query_analyzer의 문자열 상수를 쓴다.
    # 거래소공시 단건 요약(Q06)이며, 정기공시 요약(document_summary)과 handler를 나눈 이유는
    # query_analyzer의 INTENT_EVENT_SUMMARY 주석에 있다.
    INTENT_EVENT_SUMMARY: handle_event_summary,
    INTENT_TEMPORAL_COMPARE: handle_temporal_compare,
    INTENT_CROSS_ENTITY_COMPARE: handle_cross_entity_compare,
    # contract_fact도 Contract v0.1의 Intent에 아직 없어 query_analyzer의 문자열 상수를 쓴다.
    INTENT_CONTRACT_FACT: handle_contract_fact,
    INTENT_HOLDING_FACT: handle_holding_fact,
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

    맨 앞에 안전 정책 판정이 있다. manifest를 읽기도 전이며, 이 순서가 규약이다. Gate가
    거부한 질문에는 manifest 조회·문서 파싱·값 추출이 한 번도 일어나지 않으므로 Fact도
    Evidence도 만들어지지 않는다. "만들었다가 마지막에 지웠다"와는 다르다.
    """
    decision = screen_question(question)

    if not decision.allowed:
        # 판정 근거는 answerability_detail에 남는다. execution_issue는 채우지 않는다.
        # 막힌 것이 아니라 답하지 않기로 한 것이기 때문이다.
        #
        # canonical reason은 범주가 정한다. safety.py는 Contract 타입을 만들지 않는 순수
        # 함수로 두고, 범주를 Contract 어휘로 옮기는 일은 normalization의 대응표가 맡는다.
        # 여기서 값을 고르지 않으므로, F1·F2 판정이 붙어도 이 자리는 그대로다.
        #
        # 대응표에 없는 범주면 예외로 멈춘다. OUT_OF_SCOPE_POLICY로 덮으면 새 범주가 조용히
        # 정책 거부로 집계된다.
        return refused_execution(
            STAGE_ANSWERABILITY_GATE,
            decision.reason or "",
            decision.detail,
            answerability_reason=answerability_reason_for_policy_category(decision.category),
        )

    corpus_root = Path(corpus_root)

    resolved_manifest = (
        Path(manifest_path) if manifest_path else corpus_root / "manifest.jsonl"
    )

    try:
        manifest_records = load_manifest(resolved_manifest)

    except FilingNotFoundError as error:
        return blocked_execution(STAGE_FILING_DISCOVERY, error.reason, error.detail)

    known_companies = list_company_names(manifest_records)

    # 상장명까지 담은 목록을 함께 넘긴다. '현대차'·'삼성화재'·'KT'처럼 사용자가 흔히 쓰는
    # 표기는 manifest가 이미 알고 있고, 그것을 코드에 따로 적으면 코퍼스가 바뀔 때 두 벌이
    # 어긋난다. 무엇으로 인식했든 계획에 담기는 것은 canonical corp_name 하나다.
    #
    # manifest를 다시 읽지 않는다. 같은 records에서 두 목록을 함께 유도한다. 이유는 아래
    # fiscal_year_anchors와 같다.
    company_universe = list_company_universe(manifest_records)

    # 지분공시의 보고자는 corp_name(발행회사) 목록에 없을 수 있다. 지분공시
    # 질문에서만 manifest의 flr_nm을 인식 후보에 더한다.
    if "대량보유" in question:
        known_companies = tuple(
            dict.fromkeys(
                (
                    *known_companies,
                    *(
                        str(row.get("flr_nm"))
                        for row in manifest_records
                        if row.get("flr_nm")
                    ),
                )
            )
        )

    # 상대 기간("작년")을 옮길 기준 연도도 같은 manifest에서 유도해 넘긴다.
    #
    # query_analyzer가 직접 읽지 않는 이유는 known_companies와 같다. 질의 해석 계층은 코퍼스를
    # 열지 않으며, 그 덕분에 manifest 없이도 전부 시험할 수 있다.
    #
    # manifest를 두 번 읽지 않는다. 두 번 읽으면 그 사이에 파일이 바뀌었을 때 기업 목록과 기준
    # 연도가 서로 다른 코퍼스를 가리키게 되고, 그 어긋남은 결과만 봐서는 드러나지 않는다.
    fiscal_year_anchors = latest_annual_fiscal_year_by_company(manifest_records)

    try:
        plan = build_query_plan(
            question,
            known_companies=known_companies,
            fiscal_year_anchors=fiscal_year_anchors,
            company_universe=company_universe,
        )

    except UnsupportedQuestionError as error:
        # 정지 사유를 여기서 해석하지 않는다. 어떤 사유가 실행 중단이고 어떤 사유가 정상적으로
        # 끝난 답변 가능성 판정인지는 agent.normalization의 ANSWERABILITY_BY_STOP 하나가
        # 정하며, 대응표에 없는 사유는 예전과 같이 BLOCKED로 나간다.
        #
        # blocked_execution을 쓰지 않는 이유:
        #     그쪽은 무조건 BLOCKED를 만든다. 계획 단계의 정지 중에도 '후보를 다 보고 내린
        #     판정'이 섞여 있고(ambiguous_company_match), 그것까지 실행 중단으로 보고하면
        #     되묻기로 풀리는 질문이 파이프라인 고장과 같은 모양으로 나간다.
        return stopped_execution(STAGE_QUERY_PLAN, error.reason, error.detail)

    return dispatch(plan, corpus_root=corpus_root, manifest_path=resolved_manifest)
