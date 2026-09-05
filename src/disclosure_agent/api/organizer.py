"""미래에셋 AI Festival 주최측 평가 규격용 GET /answer adapter.

역할:
    기존 프로덕션 실행 경로(AgentAskService, /api/ask와 완전히 동일한 answer_question_fn 호출
    1회)를 그대로 재사용해, 그 결과를 주최측이 요구하는 5-field JSON(question_id, question,
    retrieved_context, think_trace, answer — 전부 string)으로 다시 포장한다.

이 모듈이 하지 않는 것:
    - retrieval/reasoning/event/evaluation 알고리즘을 새로 만들지 않는다. 아래 두 builder는
      이미 확정된 AnswerResponse/AgentExecutionResult를 문자열로 옮기기만 한다
      (agent.answer_builder가 AgentExecutionResult -> AnswerResponse에 대해 지키는 것과 같은
      경계 원칙).
    - 두 번째 agent pipeline을 만들지 않는다. AgentAskService.answer_with_execution_result가
      호출하는 answer_question_fn은 /api/ask와 동일한 자리에서 한 번만 실행된다.
    - hidden chain-of-thought를 노출하지 않는다. think_trace는 실행 결과에 이미 존재하는
      구조화 메타데이터(상태·사유·개수)만 요약하며, 자연어 추론 문장을 새로 만들지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.service import (
    AgentAskService,
    AgentExecutionErrorResponse,
    AgentExecutionHTTPError,
    AskService,
    unexpected_failure_response,
)
from disclosure_agent.common.enums import ExecutionStatus
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    AnswerResponse,
    AskRequest,
    Evidence,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# 근거 발췌 하나가 think_trace/retrieved_context에 실을 수 있는 최대 길이.
#
# "지나치게 긴 중복 context를 생성하지 말 것"이 주최측 계약의 명시 조건이다. Evidence.raw_text는
# 이미 근거 위치 하나로 좁혀진 발췌이지만, 표 전체가 옮겨진 경우를 대비해 상한을 둔다. 값을
# 자르되 지어내지 않는다 — 원문의 앞부분만 그대로 남기고 잘렸다는 표시만 붙인다.
_MAX_EXCERPT_CHARS = 240


class OrganizerAnswerResponse(BaseModel):
    """GET /answer가 돌려주는 주최측 평가 규격 응답. 5개 필드 모두 string이다."""

    question_id: str
    question: str
    retrieved_context: str
    think_trace: str
    answer: str


def _truncate(text: str, limit: int = _MAX_EXCERPT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _evidence_line(index: int, evidence: Evidence) -> str:
    """Evidence 한 건을 존재하는 필드만으로 한 줄로 직렬화한다.

    존재하지 않는 필드는 만들어 넣지 않는다(주최측 계약 "존재하지 않는 필드를 invent하지 말
    것"). 순서는 AnswerResponse.evidence의 원래 순서를 그대로 따른다 — 그 순서 자체가 이미
    producer가 정한 deterministic한 순서이므로 여기서 다시 정렬하지 않는다.
    """
    parts = [f"doc_id={evidence.doc_id}"]

    if evidence.section_path:
        parts.append(f"section={'/'.join(evidence.section_path)}")
    if evidence.table_name:
        parts.append(f"table={evidence.table_name}")
    if evidence.row_label:
        parts.append(f"row={evidence.row_label}")
    if evidence.column_label:
        parts.append(f"column={evidence.column_label}")
    if evidence.field_name:
        parts.append(f"field={evidence.field_name}")
    if evidence.raw_text:
        parts.append(f'text="{_truncate(evidence.raw_text)}"')

    return f"[{index}] " + " | ".join(parts)


def build_retrieved_context(evidence: list[Evidence]) -> str:
    """AnswerResponse.evidence를 deterministic한 문자열로 직렬화한다.

    코퍼스 전체가 아니라 이 답변이 실제로 실은 근거만 담는다(AnswerResponse.evidence 자체가
    이미 그렇게 좁혀져 있다 — build_answer_response는 이 목록을 새로 고르지 않고 실행 결과를
    그대로 옮긴다). 근거가 없는 결과(정책 거부 등)는 빈 문자열이다.
    """
    if not evidence:
        return ""

    return "\n".join(
        _evidence_line(index, item) for index, item in enumerate(evidence, start=1)
    )


def _unique_in_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def build_process_trace(
    answer_response: AnswerResponse,
    execution_result: AgentExecutionResult | None,
) -> str:
    """이미 확정된 실행 결과를 "safe process trace" 문자열로 요약한다.

    여기 실리는 값은 전부 기존 Contract(AnswerResponse/AgentExecutionResult)에 이미 존재하는
    구조화 필드의 개수·상태·사유뿐이다. 새 자연어 추론 문장을 만들지 않으므로 hidden
    chain-of-thought가 아니다.

    calculation_performed/comparison_performed/document_summary_count는 AnswerResponse에는
    자리가 없다(answer_builder의 build_answer_response 주석 참조: "calculations와 comparisons,
    warnings는 현재 AnswerResponse에 자리가 없어 실리지 않는다"). 그래서 원본
    AgentExecutionResult가 있을 때만 그 세 줄을 추가한다 — 없다고 해서 값을 추측해 채우지
    않는다.
    """
    reason = answer_response.reason.value if answer_response.reason else "none"
    lines = [
        "execution_status=COMPLETED",
        f"answerability_status={answer_response.status.value}",
        f"answerability_reason={reason}",
        f"evidence_count={len(answer_response.evidence)}",
        f"fact_count={len(answer_response.facts)}",
        f"event_count={len(answer_response.events)}",
        f"event_link_count={len(answer_response.event_links)}",
        f"event_history_count={len(answer_response.event_histories)}",
        f"resolved_event_state_count={len(answer_response.resolved_event_states)}",
    ]

    if execution_result is not None:
        lines.append(f"document_summary_count={len(execution_result.document_summaries)}")
        lines.append(
            "calculation_performed="
            + ("true" if execution_result.calculations else "false")
        )
        lines.append(
            "comparison_performed="
            + (
                "true"
                if execution_result.comparisons or execution_result.narrative_comparisons
                else "false"
            )
        )

    documents_used = _unique_in_order(item.doc_id for item in answer_response.evidence)
    if documents_used:
        lines.append(f"documents_used={','.join(documents_used)}")

    return "\n".join(lines)


def build_blocked_process_trace(error_response: AgentExecutionErrorResponse) -> str:
    """BLOCKED 결과의 safe trace.

    error_response.error.stage/reason은 이미 /api/ask의 422 응답 본문에 그대로 노출되는
    public-safe 값이다(agent_execution_error_response가 만드는 값). 여기서 다시 쓴다고 해서
    새로운 정보가 새지 않는다.
    """
    return "\n".join(
        [
            f"execution_status={error_response.execution_status.value}",
            f"stage={error_response.error.stage}",
            f"reason={error_response.error.reason}",
        ]
    )


@router.get(
    "/answer",
    response_model=OrganizerAnswerResponse,
    responses={500: {"model": AgentExecutionErrorResponse}},
)
def organizer_answer(
    question_id: str,
    question: str,
    service: Annotated[AskService, Depends(get_ask_service)],
) -> OrganizerAnswerResponse | JSONResponse:
    """주최측 평가 규격: GET /answer?question_id=...&question=... -> 5-field JSON.

    실행 경로는 /api/ask와 완전히 같다 — 같은 DI(get_ask_service), 같은 AgentAskService.
    이 함수가 추가하는 것은 표현뿐이다.

    Controlled fail-close(BLOCKED)는 200 + 5-field 스키마로 안전하게 표현한다. 스키마가
    string 5개를 고정 요구하므로, 예상 가능한 실행 중단이 422 오류 봉투로 나가면 evaluator가
    그 결과를 파싱하지 못한다.

    반면 실제 예상하지 못한 실패(FAILED, 혹은 핸들러 내부 예외)는 기존 5xx semantics를 그대로
    유지한다 — /api/ask가 이미 그렇게 하고 있고, 이 자리에서 5xx를 200으로 위장하면 실제 서버
    장애가 "정상적으로 답하지 못한 질문"처럼 조용히 감춰진다.
    """
    request = AskRequest(question=question)

    try:
        if isinstance(service, AgentAskService):
            answer_response, execution_result = service.answer_with_execution_result(request)
        else:
            # AgentAskService가 아닌 AskService 구현(예: 테스트 더블)이 주입된 경우.
            # /api/ask와 같은 DI를 그대로 쓰되, 원본 실행 결과가 없으니 그 정보에만
            # 의존하는 trace 항목은 생략한다(추측해 채우지 않는다).
            answer_response = service.answer(request)
            execution_result = None
    except AgentExecutionHTTPError as error:
        if error.response.execution_status is ExecutionStatus.BLOCKED:
            return OrganizerAnswerResponse(
                question_id=question_id,
                question=question,
                retrieved_context="",
                think_trace=build_blocked_process_trace(error.response),
                answer=error.response.error.message,
            )

        # FAILED: 기존 /api/ask와 같은 5xx 오류 봉투를 그대로 돌려준다.
        return JSONResponse(
            status_code=error.status_code,
            content=error.response.model_dump(mode="json"),
        )
    except Exception:
        logger.exception("Unexpected failure at the organizer Agent HTTP boundary")
        response = unexpected_failure_response()
        return JSONResponse(
            status_code=500,
            content=response.model_dump(mode="json"),
        )

    return OrganizerAnswerResponse(
        question_id=question_id,
        question=question,
        retrieved_context=build_retrieved_context(answer_response.evidence),
        think_trace=build_process_trace(answer_response, execution_result),
        answer=answer_response.answer,
    )
