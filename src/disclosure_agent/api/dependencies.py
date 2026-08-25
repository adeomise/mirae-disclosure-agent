"""FastAPI dependency providers."""

from fastapi import HTTPException, status

from disclosure_agent.api.service import AskService


def get_ask_service() -> AskService:
    """Provide the Agent service after its public result contract is normalized."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The Agent does not yet expose the common AnswerResponse contract.",
    )
