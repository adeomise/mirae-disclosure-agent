"""HTTP routes for disclosure questions."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.service import (
    AgentExecutionErrorResponse,
    AgentExecutionHTTPError,
    AskService,
)
from disclosure_agent.common.schemas import AnswerResponse, AskRequest

router = APIRouter(prefix="/api")


@router.post(
    "/ask",
    response_model=AnswerResponse,
    responses={
        422: {"model": AgentExecutionErrorResponse},
        500: {"model": AgentExecutionErrorResponse},
    },
)
def ask_disclosure_question(
    request: AskRequest,
    service: Annotated[AskService, Depends(get_ask_service)],
) -> AnswerResponse | JSONResponse:
    """Return the contract-normalized result from the injected Agent service."""
    try:
        return service.answer(request)
    except AgentExecutionHTTPError as error:
        return JSONResponse(
            status_code=error.status_code,
            content=error.response.model_dump(mode="json"),
        )
