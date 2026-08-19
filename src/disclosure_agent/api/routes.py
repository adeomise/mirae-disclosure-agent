"""HTTP routes for the Disclosure Agent API."""

from typing import Annotated

from fastapi import APIRouter, Depends

from disclosure_agent.api.dependencies import get_development_fixture_ask_service
from disclosure_agent.api.mock_service import DevelopmentFixtureAskService
from disclosure_agent.common.schemas import AnswerResponse, AskRequest

router = APIRouter(prefix="/api")


@router.post("/ask", response_model=AnswerResponse)
def ask_disclosure_question(
    request: AskRequest,
    service: Annotated[
        DevelopmentFixtureAskService,
        Depends(get_development_fixture_ask_service),
    ],
) -> AnswerResponse:
    """Return a fixture-backed answer for a supported development question."""
    return service.answer(request)
