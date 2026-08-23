"""FastAPI dependency providers."""

import os
from functools import lru_cache
from pathlib import Path

from disclosure_agent.agent.router import (
    Q01VerticalSliceRouter,
    Q02VerticalSliceRouter,
    Q13VerticalSliceRouter,
)
from disclosure_agent.api.mock_service import DevelopmentFixtureAskService
from disclosure_agent.event.service import EventQ02Service, EventQ13Service
from disclosure_agent.periodic.service import PeriodicQ01Service

DEFAULT_PERIODIC_CORPUS_DIR = Path(__file__).resolve().parents[3] / "data" / "raw"
DEFAULT_EVENT_CORPUS_DIR = DEFAULT_PERIODIC_CORPUS_DIR / "event"


@lru_cache
def get_development_fixture_ask_service() -> DevelopmentFixtureAskService:
    """Provide the implemented Q01/Q02/Q13 vertical slices."""
    periodic_corpus_dir = Path(
        os.getenv("DISCLOSURE_PERIODIC_DATA_DIR", str(DEFAULT_PERIODIC_CORPUS_DIR))
    )
    event_corpus_dir = Path(
        os.getenv("DISCLOSURE_EVENT_DATA_DIR", str(DEFAULT_EVENT_CORPUS_DIR))
    )
    q01_router = Q01VerticalSliceRouter(PeriodicQ01Service(periodic_corpus_dir))
    q02_router = Q02VerticalSliceRouter(EventQ02Service(event_corpus_dir))
    q13_router = Q13VerticalSliceRouter(EventQ13Service(event_corpus_dir))
    return DevelopmentFixtureAskService(
        q01_router=q01_router,
        q02_router=q02_router,
        q13_router=q13_router,
    )
