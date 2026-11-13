# FireBIM API server - Copilot Instructions

## Purpose
This is the main repository for `firebim-api-server`.
It contains the backend API and worker applications.
The frontend application lives in a separate repository (`firebim-webapp`) to allow independent development and deployment.
FireBIM business logic is implemented in separate repositories and consumed here as dependencies.

## Tech stack
- **Python**: 3.12 with UV workspace
- **Backend**: FastAPI
- **Worker**: Celery
- **Database**: PostgreSQL
- **Queue**: RabbitMQ 4.x
- **Storage**: S3-compatible object storage
- **Auth**: Authentik (OIDC/OAuth2)
- **CI/CD**: GitHub Actions

## Conventions
- All Python code uses type hints.
- Backend code should be explicit about sync vs async boundaries.
- Worker code is synchronous unless a specific async integration is required.
- Shared models live in `app/models`; do not duplicate them.
- Environment variables are the source of configuration; do not hardcode secrets.
- Docker images are multi-stage builds.
- Use UV for dependency management, not `pip` or Poetry.

## Local development reality
- Docker Compose in this repository is for local development only.
- Local mode and production mode are intentionally not identical.
- A container being `Up` does **not** mean the API is ready.
- The API performs synchronous startup work in local development:
  - database initialization
  - S3 bucket checks
  - ontology bootstrap into S3-backed graph storage
- A real cold start can therefore take around 1-2 minutes.
- During this time, `localhost:8000` may refuse or close connections even though the container is running.

## Local readiness checks
Prefer these checks in this order:

1. `docker compose ps`
2. `docker compose logs --tail=200 api`
3. Wait for `Application startup complete.`
4. Then verify:
   - `http://localhost:8000/health`
   - `http://localhost:8000/docs`

Do not assume a startup failure too early if the API is still on:
- `INFO: Waiting for application startup.`

## Testing
- Backend: pytest
- Worker: pytest with mocked DB, S3, and RabbitMQ where possible
- Helm: `helm lint` and `helm template`

## Preferred verification flow
Before committing Python changes, prefer:

```bash
uv run ruff format .
uv run ruff check --fix .
uv run pytest
make openapi
```

For Linux-like verification in Docker, prefer:

```bash
docker run --rm -v ${PWD}:/app -w /app ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
  sh -lc "uv sync --dev && uv run ruff check . && uv run pytest -q"
```

If formatting should be included:

```bash
docker run --rm -v ${PWD}:/app -w /app ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
  sh -lc "uv sync --dev && uv run ruff format --check . && uv run ruff check . && uv run pytest -q"
```

## Key patterns
- JWT authentication via Authentik OIDC
- OpenTelemetry tracing across backend and worker
- S3-backed graph and document storage
- Startup bootstrapping of ontology resources into local storage

## Guidance for contributors and agents
- Do not treat local Docker Compose readiness as instantaneous.
- When debugging local startup, check logs before changing architecture.
- Avoid changing the distinction between local and production setup unless explicitly requested.
- If local startup is slow, first verify whether startup is still progressing before “fixing” it. In this branch, startup can legitimately be slow because ontology profiling and bootstrap work includes heavier resources such as IFC and similar large ontology sets.
- Keep S3-backed document structures stable and predictable to support clean backups and migrations.
