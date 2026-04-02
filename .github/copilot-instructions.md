# Firebim-api-server — Copilot Instructions

## Purpose
This is the main repo for firebim-api-server. It contains the backend and worker applications.
The frontend application is in a separate repo (firebim-webapp) to allow for independent development and deployment.
Firebim business logic is implemented in a separate repo (firebim-core) and used as a dependency in both backend and worker.

## Tech Stack
- **Python**: 3.12 with UV workspace
- **Backend**: FastAPI
- **Worker**: Celery
- **Database**: PostgreSQL
- **Queue**: RabbitMQ 4.x
- **Storage**: S3-compatible
- **Auth**: Authentik (OIDC/OAuth2)
- **CI/CD**: GitHub Actions

## Conventions
- All Python code uses type hints
- Backend uses async/await throughout
- Worker uses synchronous code (Celery limitation)
- Shared models live in app/models — never duplicate
- Environment variables for all configuration — never hardcode secrets
- All Docker images are multi-stage builds
- UV for Python dependency management (not pip/poetry)

## Testing
- Backend: pytest with httpx AsyncClient, pytest-asyncio
- Worker: pytest with mocked DB/S3/RabbitMQ
- Helm: helm lint + helm template

## Pre-commit (Python)
Before committing any Python code changes, always run:
```bash
uv run ruff format .
uv run ruff check --fix .
```

## Key Patterns
- JWT authentication via Authentik OIDC
- OpenTelemetry tracing across backend and worker
