"""FastAPI dependency providers."""

import os
from pathlib import Path

from disclosure_agent.api.service import AgentAskService, AskService

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
MANIFEST_PATH_ENV = "DISCLOSURE_CORPUS_MANIFEST"
DATA_ROOT_ENV = "DATA_ROOT"
DEFAULT_DATA_ROOT = "./data"


def get_ask_service() -> AskService:
    """Provide the concrete Agent service using the established corpus environment."""
    corpus_root = Path(
        os.environ.get(CORPUS_ROOT_ENV)
        or os.environ.get(DATA_ROOT_ENV, DEFAULT_DATA_ROOT)
    ).expanduser()
    manifest_value = os.environ.get(MANIFEST_PATH_ENV)
    manifest_path = Path(manifest_value).expanduser() if manifest_value else None
    return AgentAskService(
        corpus_root=corpus_root,
        manifest_path=manifest_path,
    )
