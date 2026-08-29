"""Service boundary between the HTTP layer and the Agent runtime."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel

from disclosure_agent.agent.answer_builder import build_answer_response
from disclosure_agent.agent.router import answer_question
from disclosure_agent.common.enums import ExecutionStatus
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    AnswerResponse,
    AskRequest,
    ExecutionIssue,
)


class AgentExecutionErrorResponse(BaseModel):
    """Public error envelope for an Agent execution that did not complete."""

    execution_status: Literal[ExecutionStatus.BLOCKED, ExecutionStatus.FAILED]
    error: ExecutionIssue


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
            AgentExecutionErrorResponse(
                execution_status=result.execution_status,
                error=issue,
            )
        )
