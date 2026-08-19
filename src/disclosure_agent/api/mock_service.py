"""Development routing for the first API vertical slices."""

from pathlib import Path

from disclosure_agent.agent.router import (
    Q01VerticalSliceRouter,
    Q02VerticalSliceRouter,
    Q13VerticalSliceRouter,
)
from disclosure_agent.common.enums import AnswerabilityStatus
from disclosure_agent.common.schemas import AnswerResponse, AskRequest

DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


class DevelopmentFixtureAskService:
    """Use real processing for the Q01, Q02, and Q13 vertical slices."""

    def __init__(
        self,
        q01_router: Q01VerticalSliceRouter,
        q02_router: Q02VerticalSliceRouter,
        q13_router: Q13VerticalSliceRouter,
        fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    ) -> None:
        self.q01_router = q01_router
        self.q02_router = q02_router
        self.q13_router = q13_router
        self.fixture_dir = fixture_dir

    def answer(self, request: AskRequest) -> AnswerResponse:
        """Route a development question to one supported fixture response."""
        q13_response = self.q13_router.answer(request)
        if q13_response is not None:
            return q13_response
        q02_response = self.q02_router.answer(request)
        if q02_response is not None:
            return q02_response
        q01_response = self.q01_router.answer(request)
        if q01_response is not None:
            return q01_response

        return AnswerResponse(
            answer="Unsupported development fixture question.",
            status=AnswerabilityStatus.UNANSWERABLE,
        )
