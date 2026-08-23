"""FastAPI application entry point."""

from fastapi import FastAPI

from disclosure_agent.api.routes import router

app = FastAPI(title="Disclosure Agent", version="0.1.0")
app.include_router(router)
