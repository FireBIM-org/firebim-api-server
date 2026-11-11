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

FROM builder AS neo-builder

# Pinned geometry-free producer. No floating latest URL or unchecked executable.
RUN test "$(uname -m)" = x86_64 \
    && mkdir -p /opt/ifc2lbd-neo \
    && curl --fail --location --retry 3 --connect-timeout 10 --max-time 120 \
       https://github.com/ISBE-TUe/IFC2LBD-Neo/releases/download/v0.7.4/ifc2lbd-neo-cli-linux \
       --output /opt/ifc2lbd-neo/ifc2lbd-neo \
    && printf '%s  %s\n' f773019184ddd3ae6441e97f9bbf5085f4f460b349aac8df5bdc37e4742a4879 \
       /opt/ifc2lbd-neo/ifc2lbd-neo | sha256sum --check --strict \
    && chmod 0555 /opt/ifc2lbd-neo/ifc2lbd-neo

# Stage 2: Runtime
FROM python:3.12-slim-bookworm AS runtime

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
    adduser --system --uid 1001 appuser && \
    mkdir -p /app/ingestion-attempts && chown 1001:1001 /app/ingestion-attempts

ENV PATH="/app/.venv/bin:$PATH"
ENV NEO_ATTEMPT_ROOT="/app/ingestion-attempts"

ARG GIT_BRANCH=unknown
ARG GIT_COMMIT=unknown
ARG BUILD_TIME=unknown
ENV GIT_BRANCH=$GIT_BRANCH \
    GIT_COMMIT=$GIT_COMMIT \
    BUILD_TIME=$BUILD_TIME

# Switch to non-root user
USER appuser

# Command to run the Celery worker
CMD ["celery", "-A", "app.workers.celery_worker", "worker", "--loglevel=info", "--concurrency=1"]

# Opt-in target: legacy workers remain buildable without this Linux x86_64 binary.
FROM python:3.12-slim-trixie AS neo-runtime
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=runtime /app /app
COPY --from=neo-builder /opt/ifc2lbd-neo /opt/ifc2lbd-neo
RUN /opt/ifc2lbd-neo/ifc2lbd-neo --help > /dev/null \
    && addgroup --system --gid 1001 appgroup \
    && adduser --system --uid 1001 appuser
ENV PATH="/app/.venv/bin:$PATH"
ENV NEO_ATTEMPT_ROOT="/app/ingestion-attempts"
USER appuser
CMD ["celery", "-A", "app.workers.celery_app", "worker", "-Q", "neo_ingestion", "--loglevel=info", "--concurrency=1", "--prefetch-multiplier=1"]

FROM runtime AS legacy-runtime
