"""Service boundary between the HTTP layer and the Agent runtime."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from disclosure_agent.agent.answer_builder import build_answer_response
from disclosure_agent.agent.router import answer_question
from disclosure_agent.common.enums import ExecutionStatus
from disclosure_agent.common.schemas import AgentExecutionResult, AnswerResponse, AskRequest

BLOCKED_PUBLIC_MESSAGE = "요청을 처리하는 데 필요한 조건을 충족하지 못했습니다."
FAILED_PUBLIC_MESSAGE = "요청 처리 중 오류가 발생했습니다."


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


def agent_execution_error_response(
    execution_status: Literal[ExecutionStatus.BLOCKED, ExecutionStatus.FAILED],
    *,
    stage: str,
    reason: str,
) -> AgentExecutionErrorResponse:
    """Build a public error without copying the internal diagnostic detail."""
    message = (
        BLOCKED_PUBLIC_MESSAGE
        if execution_status is ExecutionStatus.BLOCKED
        else FAILED_PUBLIC_MESSAGE
    )
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

    def answer(self, request: AskRequest) -> AnswerResponse:
        result = self.answer_question_fn(
            request.question,
            corpus_root=self.corpus_root,
            manifest_path=self.manifest_path,
        )

        if result.execution_status is ExecutionStatus.COMPLETED:
            return build_answer_response(result)

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
