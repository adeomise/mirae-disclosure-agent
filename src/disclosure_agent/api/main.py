"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from disclosure_agent.api.routes import router

app = FastAPI(title="Disclosure Agent", version="0.1.0")
app.include_router(router)

UI_DIR = Path(__file__).parent / "ui"
app.mount("/static", StaticFiles(directory=UI_DIR), name="static")


@app.get("/", include_in_schema=False)
def disclosure_ui() -> FileResponse:
    """Serve the lightweight disclosure question interface."""
    return FileResponse(UI_DIR / "index.html")


@app.get("/health")
def health_check() -> dict[str, str]:
    """Liveness probe: the process can accept and complete a request.

    Deliberately does not call the Agent, touch the corpus, or reach
    HyperCLOVA X. Those are answerability concerns, not liveness. A
    deployment healthcheck must return quickly and independently of
    corpus/HyperCLOVA availability so it never reports the process itself
    as down for reasons unrelated to process health.
    """
    return {"status": "ok"}
