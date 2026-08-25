"""HTTP routes for disclosure questions."""

from typing import Annotated

from fastapi import APIRouter, Depends

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.service import AskService
from disclosure_agent.common.schemas import AnswerResponse, AskRequest

router = APIRouter(prefix="/api")


@router.post("/ask", response_model=AnswerResponse)
def ask_disclosure_question(
    request: AskRequest,
    service: Annotated[AskService, Depends(get_ask_service)],
) -> AnswerResponse:
    """Return the contract-normalized result from the injected Agent service."""
    return service.answer(request)
