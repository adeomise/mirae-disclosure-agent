$ErrorActionPreference = "Stop"

uv run uvicorn disclosure_agent.api.main:app --host 0.0.0.0 --port 8000
