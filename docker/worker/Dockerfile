# Stage 1: Build
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ARG APT_CACHE_BUSTER
# Install curl for healthchecks
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --package firebim-api-server --group worker --no-dev

# Stage 2: Runtime
FROM python:3.14-slim-bookworm AS runtime

ARG APT_CACHE_BUSTER
# Patch OS-level vulnerabilities (CVE-2026-28390: libssl3/openssl)
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY app/ /app/app/

# Add a non-root user to run the app
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 appuser

ENV PATH="/app/.venv/bin:$PATH"

ARG GIT_BRANCH=unknown
ARG GIT_COMMIT=unknown
ARG BUILD_TIME=unknown
ENV GIT_BRANCH=$GIT_BRANCH \
    GIT_COMMIT=$GIT_COMMIT \
    BUILD_TIME=$BUILD_TIME

# Switch to non-root user
USER appuser

# Command to run the Celery worker
CMD ["celery", "-A", "app.workers.celery_worker", "worker", "--loglevel=info"]
