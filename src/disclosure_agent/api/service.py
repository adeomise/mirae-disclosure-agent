"""Service boundary between the HTTP layer and the Agent runtime.

답변 문장이 만들어지는 경로는 둘이다.

    결정론      agent.answer_builder.build_answer_response
    HyperCLOVA  agent.grounded_payload -> agent.verbalizer

결정론 응답을 항상 먼저 만든다. 그것이 정본이고, HCX 경로는 그 응답의 answer 문자열 하나만
바꾼다. 순서를 이렇게 두는 이유가 두 가지다.

    1. 결과 모양의 불변식을 한 번만 판정한다. build_answer_response가 이미 판정 상태와 사유의
       정합성, 거부 응답의 근거 유무, 체인 완결 여부를 확인하고 어긋나면 멈춘다. 그 판정을
       통과한 결과만 HCX로 간다. 같은 규칙을 이 파일에 다시 적으면 두 벌이 되고, 그때부터
       어느 쪽이 정본인지 알 수 없게 된다.
    2. 구조화 결과가 경로에 따라 달라질 수 없다. 응답을 다시 조립하지 않고 answer만 갈아
       끼우므로(model_copy), 나중에 AnswerResponse에 carrier가 늘어도 두 경로가 자동으로
       같은 것을 싣는다. 손으로 필드를 옮겨 적으면 새 carrier가 HCX 경로에서만 빠진다.

거부(UNANSWERABLE)·미해결(UNRESOLVED)과 실행이 끝나지 못한 결과(BLOCKED/FAILED)는 외부
모델을 거치지 않는다. 그 문장들은 사유별로 이미 확정돼 있고(answer_builder의
UNAVAILABLE_MESSAGE_BY_REASON), 표현을 바꿀 여지를 두면 "보유하고 있지 않습니다"가 "찾지
못했습니다"로 흔들린다. 두 문장은 사용자가 할 수 있는 다음 행동이 다르다.

외부 모델이 답을 주지 못하면(비활성·장애·검증 실패) 결정론 응답을 그대로 돌려준다. 세 경우의
대응이 같은 이유는 셋 다 "표현을 바꾸지 않는다"이기 때문이며, 그때의 응답은 연동을 붙이기
전과 완전히 같다.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from disclosure_agent.agent.answer_builder import build_answer_response
from disclosure_agent.agent.grounded_payload import (
    VERBALIZABLE_STATUSES,
    GroundedPayload,
    PayloadNotGroundableError,
    build_grounded_payload,
)
from disclosure_agent.agent.hyperclova import ChatCompletionClient, build_chat_client
from disclosure_agent.agent.router import answer_question
from disclosure_agent.agent.verbalizer import verbalize
from disclosure_agent.common.config import get_hyperclova_settings
from disclosure_agent.common.enums import ExecutionStatus
from disclosure_agent.common.schemas import AgentExecutionResult, AnswerResponse, AskRequest

logger = logging.getLogger(__name__)

BLOCKED_PUBLIC_MESSAGE = "요청을 처리하는 데 필요한 조건을 충족하지 못했습니다."
FAILED_PUBLIC_MESSAGE = "요청 처리 중 오류가 발생했습니다."

# reason == "missing_year"(query_analyzer._require_years -> router가 옮기는 query_plan
# 단계 진단) 전용 안내문. 다음 행동(연도를 채워 다시 물어봄)을 구체적으로 알려준다.
#
# BLOCKED_PUBLIC_MESSAGE 자체를 이 문장으로 바꾸지 않는다. 등록되지 않은 다른 BLOCKED
# reason(예: ambiguous_period)은 여전히 generic 문장을 받아야 하고, 그 사유들의 안내문은
# 이후 단계(다른 팀 작업)에서 각자 정해진다.
MISSING_YEAR_PUBLIC_MESSAGE = (
    "조회 연도를 입력해 주세요. 예: 2025년 한화오션 공급계약 내용을 알려줘."
)

# reason == "unknown_company"(query_analyzer의 company resolution 실패) 전용 안내문.
#
# 어떤 이름이 왜 풀리지 않았는지는 여기서 판단하지 않는다(예: 계약 상대방 이름과 공시
# 기업명의 구분). Team3은 company resolver를 보정하지 않고, 사용자에게 "정확한 공시
# 기업명"을 다시 입력해 달라고만 안내한다.
UNKNOWN_COMPANY_PUBLIC_MESSAGE = (
    "조회할 기업을 확인하지 못했습니다. 공시 기업명을 정확히 입력해 주세요."
)

# reason == "unsupported_metric"(query_analyzer의 metric 판정 실패) 전용 안내문.
#
# 지원 지표 목록을 여기서 나열하지 않는다 — 그 목록은 query_vocabulary 쪽 값이고, 여기서
# 하드코딩하면 지표가 늘거나 줄 때마다 이 파일도 같이 고쳐야 한다. 질문이 실제로 어떤
# 의도(투자계획 등)였는지도 재분류하지 않는다.
UNSUPPORTED_METRIC_PUBLIC_MESSAGE = (
    "요청하신 항목을 현재 지원하는 공시 지표로 확인하지 못했습니다. "
    "조회할 항목을 조금 더 구체적으로 입력해 주세요."
)

REASON_MISSING_YEAR = "missing_year"
REASON_UNKNOWN_COMPANY = "unknown_company"
REASON_UNSUPPORTED_METRIC = "unsupported_metric"

# ExecutionIssue.stage/reason은 둘 다 순수 str이다(schemas.ExecutionIssue). Enum이 아니므로
# 여기서도 str 그대로 비교한다. 값은 answer_builder.STAGE_QUERY_PLAN/STAGE_EXTRACTION과
# 같은 문자열이지만, 그 모듈을 이 경계에서 새로 import하지 않는다 — service.py는 이미
# 만들어진 공개 문자열(stage/reason)만 보고, Agent 내부 모듈의 상수 정의에 묶이지 않는다.
STAGE_QUERY_PLAN = "query_plan"
STAGE_EXTRACTION = "extraction"

# BLOCKED public message는 reason 하나만으로 고를 수 없다. unsupported_metric은 두 자리에서
# 난다:
#
#   query_plan / unsupported_metric  - query_analyzer가 질문에서 지표를 못 읽음.
#                                       사용자가 질문을 고치면 풀릴 수 있다.
#   extraction / unsupported_metric  - periodic.extractor가 문서에서 그 지표를 못 뽑음
#                                       (ExtractionBlockedError -> period_retrieval.py).
#                                       질문은 이미 지표를 정확히 짚었을 수 있고, 여기서
#                                       막힌 것은 내부 처리 실패에 가깝다. 질문 수정 안내를
#                                       그대로 주면 사용자가 이미 맞는 표현을 다시 써 보다가
#                                       똑같이 막힌다.
#
# 그래서 key를 (stage, reason)으로 둔다. 여기 없는 조합은 BLOCKED_PUBLIC_MESSAGE를 쓴다.
_BLOCKED_PUBLIC_MESSAGE_BY_STAGE_AND_REASON: dict[tuple[str, str], str] = {
    (STAGE_QUERY_PLAN, REASON_MISSING_YEAR): MISSING_YEAR_PUBLIC_MESSAGE,
    (STAGE_QUERY_PLAN, REASON_UNKNOWN_COMPANY): UNKNOWN_COMPANY_PUBLIC_MESSAGE,
    (STAGE_QUERY_PLAN, REASON_UNSUPPORTED_METRIC): UNSUPPORTED_METRIC_PUBLIC_MESSAGE,
}


class PublicExecutionError(BaseModel):
    """Public-safe projection of an internal execution diagnostic."""

    stage: str
    reason: str
    message: str


class AgentExecutionErrorResponse(BaseModel):
    """Public error envelope for an Agent execution that did not complete."""

    execution_status: Literal[ExecutionStatus.BLOCKED, ExecutionStatus.FAILED]
    error: PublicExecutionError


class RequestValidationErrorItem(BaseModel):
    """FastAPI request-validation item documented alongside Agent BLOCKED errors."""

    type: str
    loc: list[str | int]
    msg: str
    input: Any | None = None
    ctx: dict[str, Any] | None = None


class RequestValidationErrorResponse(BaseModel):
    """FastAPI's request-validation 422 payload, distinct from Agent execution errors."""

    detail: list[RequestValidationErrorItem]


class AgentExecutionHTTPError(RuntimeError):
    """Carry a non-completed Agent result to the HTTP route without rebuilding it."""

    def __init__(self, response: AgentExecutionErrorResponse) -> None:
        super().__init__(response.execution_status.value)
        self.response = response

    @property
    def status_code(self) -> int:
        if self.response.execution_status is ExecutionStatus.BLOCKED:
            return 422
        return 500


class MissingExecutionIssueError(RuntimeError):
    """A BLOCKED or FAILED result omitted its required public diagnostic."""


AnswerQuestion = Callable[..., AgentExecutionResult]

# 근거 payload 하나를 문장으로 옮긴다. 옮기지 못하면 None.
#
# 예외를 던지지 않는 자리로 둔다. 외부 모델의 실패는 이 시스템의 실패가 아니고, 그 경우의
# 동작은 이미 정해져 있다(결정론 응답 사용). 실패를 예외로 올리면 호출부가 그 구분을 다시
# 하게 되고, 한 번 빠뜨리면 모델 장애가 사용자에게 500으로 나간다.
Verbalize = Callable[[GroundedPayload], str | None]


@lru_cache(maxsize=1)
def _cached_chat_client() -> ChatCompletionClient | None:
    """프로세스 수명 동안 한 번만 만드는 클라이언트. 없으면 None(비활성)."""
    return build_chat_client(get_hyperclova_settings())


def default_verbalize(payload: GroundedPayload) -> str | None:
    """
    설정에 따라 외부 모델을 부른다. 비활성이면 부르지 않고 None.

    연동 활성 여부를 아는 곳을 여기 하나로 둔다. AgentAskService가 설정을 직접 읽으면 요청
    경로가 config에 묶이고, 테스트가 환경 변수를 건드려야 한다. 테스트와 회귀 실행은
    verbalize_fn을 갈아 끼워 이 자리를 지나가지 않는다.
    """
    client = _cached_chat_client()

    if client is None:
        return None

    return verbalize(payload, client=client)


def agent_execution_error_response(
    execution_status: Literal[ExecutionStatus.BLOCKED, ExecutionStatus.FAILED],
    *,
    stage: str,
    reason: str,
) -> AgentExecutionErrorResponse:
    """Build a public error without copying the internal diagnostic detail."""
    if execution_status is ExecutionStatus.BLOCKED:
        message = _BLOCKED_PUBLIC_MESSAGE_BY_STAGE_AND_REASON.get(
            (stage, reason), BLOCKED_PUBLIC_MESSAGE
        )
    else:
        message = FAILED_PUBLIC_MESSAGE
    return AgentExecutionErrorResponse(
        execution_status=execution_status,
        error=PublicExecutionError(stage=stage, reason=reason, message=message),
    )


def unexpected_failure_response() -> AgentExecutionErrorResponse:
    """Return the fixed public envelope for an unexpected runtime failure."""
    return agent_execution_error_response(
        ExecutionStatus.FAILED,
        stage="runtime",
        reason="unexpected_failure",
    )


class AskService(Protocol):
    """Return an Agent result normalized to the common response contract."""

    def answer(self, request: AskRequest) -> AnswerResponse:
        """Answer one disclosure question."""
        ...


@dataclass(frozen=True)
class AgentAskService:
    """Connect the existing Agent runtime and Answer Builder to the HTTP boundary."""

    corpus_root: Path
    manifest_path: Path | None = None
    answer_question_fn: AnswerQuestion = answer_question
    verbalize_fn: Verbalize = default_verbalize

    def answer(self, request: AskRequest) -> AnswerResponse:
        response, _ = self.answer_with_execution_result(request)
        return response

    def answer_with_execution_result(
        self, request: AskRequest
    ) -> tuple[AnswerResponse, AgentExecutionResult]:
        """
        `answer`와 완전히 같은 실행 경로를 한 번만 태우되, 원본 실행 결과도 함께 돌려준다.

        AnswerResponse에는 자리가 없는 진단(calculations/comparisons/document_summaries 등)이
        필요한 소비자(예: organizer adapter의 process trace)를 위한 것이다. `answer_question_fn`을
        두 번 부르지 않는다 — 같은 질문을 다시 실행하면 파이프라인이 두 벌이 되는 것과 같고,
        비결정적 경로(HyperCLOVA)가 있다면 두 호출의 결과가 갈릴 수도 있다.

        예외 계약은 `answer`와 동일하다. BLOCKED/FAILED는 원본 결과를 담을 자리가 없으므로
        (실행이 끝나지 못했다) 이전과 같이 AgentExecutionHTTPError로 올라간다.
        """
        result = self.answer_question_fn(
            request.question,
            corpus_root=self.corpus_root,
            manifest_path=self.manifest_path,
        )

        if result.execution_status is ExecutionStatus.COMPLETED:
            return self._completed_response(request.question, result), result

        issue = result.execution_issue
        if issue is None:
            raise MissingExecutionIssueError(
                f"{result.execution_status.value} Agent result has no execution_issue"
            )

        raise AgentExecutionHTTPError(
            agent_execution_error_response(
                result.execution_status,
                stage=issue.stage,
                reason=issue.reason,
            )
        )

    def _completed_response(
        self,
        question: str,
        result: AgentExecutionResult,
    ) -> AnswerResponse:
        """
        정상적으로 끝난 결과를 공개 응답으로 옮긴다.

        결정론 응답을 먼저 만든다. 그 과정에서 결과 모양이 표현할 수 없는 것이면 예외가
        올라가고, 그 정지는 연동 전과 같은 자리에 남는다. 연동을 붙였다고 해서 표현 방법이
        정해지지 않은 결과가 조용히 답해지지는 않는다.

        바뀌는 것은 answer 문자열 하나뿐이다. 구조화 결과는 어느 경로로 답하든 같다.
        """
        deterministic = build_answer_response(result)

        verbalized = self._verbalized_answer(question, result)

        if verbalized is None:
            return deterministic

        return deterministic.model_copy(update={"answer": verbalized})

    def _verbalized_answer(
        self,
        question: str,
        result: AgentExecutionResult,
    ) -> str | None:
        """
        외부 모델이 만든 문장. 이 경로를 지나지 않는 결과는 None.

        판정 상태를 먼저 본다. 거부·미해결은 문장이 사유별로 이미 확정돼 있어 넘기지 않는다.
        build_grounded_payload도 같은 판정을 하지만, 넘기지 않을 결과의 payload를 만들어
        두고 버리지 않도록 여기서 먼저 끊는다.

        어떤 예외도 밖으로 내보내지 않는다. 이 자리의 실패는 답변 실패가 아니라 '표현을
        바꾸지 않음'이며, 결정론 응답이 이미 만들어져 있다. 여기서 예외가 올라가면 결정론
        경로만으로 답할 수 있었던 요청이 연동을 켰다는 이유만으로 500이 된다.
        """
        if result.answerability_status not in VERBALIZABLE_STATUSES:
            return None

        try:
            payload = build_grounded_payload(result, question=question)

        except PayloadNotGroundableError as error:
            # 사유 종류만 남긴다. payload에는 확보한 공시 수치가 들어 있다.
            logger.info("grounded payload unavailable: %s", type(error).__name__)

            return None

        try:
            return self.verbalize_fn(payload)

        except Exception as error:  # noqa: BLE001 - 연동 실패가 답변 실패가 되지 않게 한다
            # verbalize는 스스로 실패를 None으로 옮기지만, 그 계약을 여기서 다시 믿지 않는다.
            # 주입된 구현이나 예상 못 한 오류가 이 경계를 넘으면 정상 요청이 500이 된다.
            logger.warning("verbalization failed: %s", type(error).__name__)

            return None
