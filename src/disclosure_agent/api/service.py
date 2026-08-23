"""Service contract between the HTTP layer and the Agent runtime."""

from typing import Protocol

from disclosure_agent.common.schemas import AnswerResponse, AskRequest


class AskService(Protocol):
    """Return an Agent result normalized to the common response contract."""

    def answer(self, request: AskRequest) -> AnswerResponse:
        """Answer one disclosure question."""
        ...
