FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONPATH=/ \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install production dependencies including worker-specific packages
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group worker --no-install-project

# Copy application code
COPY app/ /app/app/

# Add a non-root user to run the app
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 appuser

ENV PATH="/app/.venv/bin:$PATH"

# Switch to non-root user
USER appuser

# Command to run the Celery worker
CMD ["celery", "-A", "app.workers.celery_worker", "worker", "--loglevel=info"]
