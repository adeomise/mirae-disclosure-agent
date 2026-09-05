FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

# Dependency layer first, separate from the source copy below, so that
# editing application code does not invalidate this layer and trigger a
# dependency reinstall on every rebuild. --frozen refuses to resolve new
# versions from pyproject.toml; the lockfile is the only source of truth.
# --no-dev excludes pytest/ruff — this image runs the server, not the tests.
# README.md must be copied here too: pyproject.toml declares
# `readme = "README.md"` (hatchling reads it while building the local
# project's metadata), so `uv sync` fails without it even though nothing
# else in this layer needs it.
#
# --no-install-project: at this point src/ has not been copied yet, so the
# local `disclosure_agent` package itself cannot be built/installed. This
# step only resolves and installs third-party dependencies from the
# lockfile, which is exactly what keeps this layer cacheable across source
# edits.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src

# Second sync, now that src/ exists: installs the local project itself
# (skipped above by --no-install-project) into the already-populated
# environment. Dependencies are unchanged from the layer above, so this is
# fast and does not re-resolve or re-download anything. The CMD below runs
# with --no-sync, so this is the only place project installation happens.
RUN uv sync --frozen --no-dev

# The corpus is runtime state, not build state: it is bind/volume-mounted at
# `docker run`, never baked into the image (see docs/deployment.md).
# Secrets (.env, HYPERCLOVA_*) are likewise runtime-injected and are
# excluded from the build context by .dockerignore, so there is nothing
# here for either to COPY.

EXPOSE 8000

# --no-sync: the environment was already synced in the layers above: skip
# uv's startup sync check so every container start (and every restart) does
# not re-resolve or reinstall dependencies.
CMD ["uv", "run", "--no-sync", "uvicorn", "disclosure_agent.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
