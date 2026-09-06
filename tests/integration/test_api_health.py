"""Integration checks for the deployment liveness probe."""

from typing import NoReturn

from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app

client = TestClient(app)


def test_health_returns_200_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def _fail_if_called() -> NoReturn:
    raise AssertionError("GET /health must not resolve get_ask_service")


def test_health_does_not_depend_on_the_ask_service() -> None:
    """
    Liveness must not enter the Agent/service dependency graph at all.

    get_ask_service reads DISCLOSURE_CORPUS_ROOT/DISCLOSURE_CORPUS_MANIFEST at
    call time (api/dependencies.py), so calling it is itself a corpus/Agent
    concern, not a liveness one. Asserting it is absent from
    dependency_overrides (as this test used to) only shows nothing *else*
    substituted it — it does not prove the route never resolves it, since an
    unoverridden dependency still runs. Overriding it with a callable that
    raises turns any accidental resolution into a hard failure instead.

    The override is removed in `finally` so a failure here cannot leak into
    other tests in this process (matches the clear_dependency_overrides
    convention in tests/integration/test_api_ask.py).
    """
    app.dependency_overrides[get_ask_service] = _fail_if_called
    try:
        response = client.get("/health")
    finally:
        del app.dependency_overrides[get_ask_service]

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
