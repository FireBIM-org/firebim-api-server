FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy workspace definition and lock file
COPY pyproject.toml uv.lock ./
COPY packages/common/pyproject.toml packages/common/pyproject.toml
COPY packages/api/pyproject.toml packages/api/pyproject.toml
COPY packages/worker/pyproject.toml packages/worker/pyproject.toml

# Install dependencies for common + worker
RUN uv sync --frozen --no-dev --package firebim-worker

# Copy package source code
COPY packages/common/src/ packages/common/src/
COPY packages/worker/src/ packages/worker/src/

# Re-install with source so workspace packages are linked
RUN uv sync --frozen --no-dev --package firebim-worker

# Add a non-root user to run the app
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 appuser

ENV PATH="/app/.venv/bin:$PATH"

USER appuser

CMD ["celery", "-A", "firebim_worker.tasks", "worker", "--loglevel=info"]
