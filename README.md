# FireBIM API server
[![Build & Push Image](https://github.com/FireBIM-org/firebim-api-server/actions/workflows/build-image.yml/badge.svg)](https://github.com/FireBIM-org/firebim-api-server/actions/workflows/build-image.yml)
[![CI](https://github.com/FireBIM-org/firebim-api-server/actions/workflows/ci.yml/badge.svg)](https://github.com/FireBIM-org/firebim-api-server/actions/workflows/ci.yml)

This repository is solely for API-specific code for deployment to a server.
FireBIM business logic does **not** belong in this repository.
This repository depends on other FireBIM repositories for the logic. `firebim-api-server` is intentionally thin on its own.

## Prerequisites

- **Python 3.12** or higher
- **UV** package manager ([installation guide](https://docs.astral.sh/uv/))
- **Docker**

## Getting started (using Docker Compose)

1. Clone the repository
2. Copy `.env.example` to `.env`
3. Start all services:

```bash
make up-build
```

4. Wait for the API to become ready before using the UI.

The API container can be `Up` before the FastAPI app is actually ready to serve requests.
On a real cold start, local startup can take around 1-2 minutes because the app:
- initializes the database
- checks or creates the S3 bucket
- bootstraps default ontologies into S3-backed graph storage

Good readiness checks are:

```bash
docker compose logs --tail=200 api
```

Wait until you see:

```text
Application startup complete.
```

Or use:

```bash
curl http://localhost:8000/readyz
curl http://localhost:8000/health
```

`GET /livez` (process is serving, no dependency checks) and `GET /readyz` (database and S3 reachable) are meant
for Kubernetes liveness and readiness probes. `GET /health` additionally checks Celery and Authentik and is meant
for humans and blackbox monitoring, not for probes.

5. Access the services:
- Backend API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- Authentik Admin: http://localhost:9080 (`akadmin` / `Password123!`, auto-initialized)
- RabbitMQ Management: http://localhost:15672 (`firebim` / `firebim`)
- MinIO Console: http://localhost:9001 (`minioadmin` / `minioadmin`)
- Mailpit (email UI): http://localhost:8025

## Local development notes

- The included Docker Compose setup is for local development only.
- Local mode and production mode are intentionally not the same.
- A slow cold start is often expected in this branch because startup includes heavier ontology profiling and bootstrap work, including IFC-scale resources. Check the API logs before assuming startup failed.
- `docker compose ps` only tells you that the container is running, not that the FastAPI app has completed startup.
- If `localhost:8000` closes the connection during startup, wait for the API log to print `Application startup complete.` and try again.

### Updating `openapi.json`

Do not edit `openapi.json` manually.

To update `openapi.json` run:

```bash
make openapi
```

Always run this before committing if making changes to the API.

### Lint and format

To lint and format all Python code run:

```bash
make format
```

Always run this before committing.

### Full local verification

For a Linux-like verification flow without leaving Docker, run:

```bash
docker run --rm -v ${PWD}:/app -w /app ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
  sh -lc "uv sync --dev && uv run ruff check . && uv run pytest -q"
```

If you want to include formatting in that check, run:

```bash
docker run --rm -v ${PWD}:/app -w /app ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
  sh -lc "uv sync --dev && uv run ruff format --check . && uv run ruff check . && uv run pytest -q"
```

## Syncing ontologies from a public registry

Ontology releases (FBO core, the national FBO modules, the FBO → IFC mapping, and the IFC/RIR reference
ontologies) are published on https://firebim.org/documentation/ontologies/ as versioned, credential-free
static files with a JSON index. This API can pull them into its own ontology store:

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /ontologies` | no | Discover every ontology family/country currently published in this API |
| `GET /ontologies/sync/source` | no | Default registry URL (`ONTOLOGY_REGISTRY_URL`) |
| `POST /ontologies/sync/preview` | yes | Fetch the index and report per version: `publish`, `update`, `current` or `skip` – publishes nothing |
| `POST /ontologies/sync` | yes | Publish every version that is missing locally (and, with `overwrite: true`, versions whose content or status changed) |

Request body (all fields optional):

```json
{
  "source_url": "https://firebim.org/documentation/ontologies/index.json",
  "families": ["fbo"],
  "countries": ["dk", "core"],
  "selections": ["fbo/dk/2026.09.04"],
  "include_unlisted": true,
  "latest_only": false,
  "overwrite": false,
  "dry_run": false
}
```

Synced versions land in the same place as manually published ones
(`GET /ontologies/{family}/{country}/versions`, newest first). Content integrity is verified against the
registry's `sha256` before publishing. To sync from another site, point `source_url` at any index that
follows the same format (see `app/ontologies/registry_sync.py` for the schema); relative `ttl_url`s are
resolved against the index's `base_url`.

A user-supplied `source_url` (anything other than `ONTOLOGY_REGISTRY_URL`) and every URL its index lists are
only fetched from public http(s) hosts: URLs with credentials, and hosts that resolve to private, loopback,
link-local or other internal addresses, are refused with 400, and each redirect hop is checked again. Set
`ONTOLOGY_SYNC_ALLOWED_HOSTS` (comma list, `.example.org` matches subdomains) to restrict the hosts further.

## Authentication, limits and hardening

`/graphs`, `/rules` and `/checks` require a bearer token (except their `/health` probes), like `/ifc-models`,
`/ids-files`, `/rulebuilder` and the write endpoints of `/ontologies` and `/lexicon`. `/graphs` only lists and
samples the IFC models of the current user. `GET /livez`, `/readyz`, `/health` and `/build-info` are public.

| Setting | Default | Effect |
| --- | --- | --- |
| `MAX_UPLOAD_BYTES` | `524288000` (500 MiB) | Max IFC/IDS file upload → 413 `PAYLOAD_TOO_LARGE` |
| `MAX_JSON_BODY_BYTES` | `52428800` (50 MiB) | Max size of any other request body → 413 `PAYLOAD_TOO_LARGE` |
| `RATE_LIMIT_ENABLED` | `true` | In-memory, per-process rate limiting |
| `RATE_LIMIT_DEFAULT` | `300/minute` | Per bearer token (SHA-256 of the token), all routes |
| `RATE_LIMIT_EXPENSIVE` | `20/minute` | Per bearer token, in addition, on uploads, check runs, publish, sync and conversions |
| `RATE_LIMIT_ANON` | `60/minute` | Per client IP for requests without a token |
| `TRUSTED_PROXY_CIDRS` | `10.42.0.0/16,10.43.0.0/16,127.0.0.1/32` | Peers whose `X-Forwarded-For` is honoured |
| `ENABLE_API_DOCS` | `true` | Serve `/docs`, `/redoc` and `/openapi.json` |
| `ONTOLOGY_SYNC_ALLOWED_HOSTS` | empty | Optional host allowlist for user-supplied sync sources |
| `CHECK_CACHE_MAX_ENTRIES` | `32` | Graphs kept in the in-process check execution cache (LRU) |
| `CELERY_TASK_TIME_LIMIT` / `CELERY_TASK_SOFT_TIME_LIMIT` | `3600` / `3300` | Worker task time limits in seconds |
| `STORAGE_HARD_LIMIT_BYTES`, `STORAGE_USER_QUOTA_BYTES`, `STORAGE_WARN_RATIO` | `1 TB`, `0`, `0.8` | Bucket storage caps |
| `ADMIN_GROUP`, `ADMIN_EMAILS` | `firebim-admins`, empty | Who counts as an admin |

Rate-limited responses are 429 `{"detail": ..., "code": "RATE_LIMITED"}` with `Retry-After`,
`X-RateLimit-Limit` and `X-RateLimit-Remaining`. Unexpected errors return a generic
500 `{"detail": "Internal server error", "request_id": ...}`; the exception is logged with the same request id.

## Validating RuleBuilder ontology terms

`POST /lexicon/catalog/rule-term-validation` validates the non-empty `target_class` and
`property_path` values in one or more rules against a caller-selected ontology set. Validation is
exact and kind-aware: target classes must resolve to declared classes, and property paths must
resolve to declared properties. The response includes a flattened issue list plus per-node and
per-field results, making it suitable for both live editor feedback and an imported-rule overview.

The endpoint accepts the same `rule`, `rules`, `rule_id`, and `rule_ids` alternatives as the rule
ontology similarity endpoint. Empty fields are left to the existing rule completeness validation.

## Deployment

This repository can be deployed for self-hosting, or you can use one of the official API endpoints.

The official deployment is available at:
- [api.firebim.org](https://api.firebim.org/docs)
- [api.staging.firebim.org](https://api.staging.firebim.org)

### Production

**Important:** Docker Compose files in this repository are only intended for local development.
Do not use the included compose files for production deployment.
