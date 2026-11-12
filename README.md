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
for humans and blackbox monitoring, not for probes. The checks run concurrently and each gives up after 2.5 s, so
all three endpoints answer within about 2.5 s even when a dependency hangs.

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
Fetches from such sources are also bounded: a response larger than `ONTOLOGY_SYNC_MAX_FETCH_BYTES` (50 MiB) is
abandoned, and `ONTOLOGY_SYNC_TIMEOUT_SECONDS` is the deadline for each whole fetch, redirects included.

## Authentication, limits and hardening

`/graphs`, `/rules` and `/checks` require a bearer token (except their `/health` probes), like `/ifc-models`,
`/ids-files`, `/rulebuilder` and the write endpoints of `/ontologies` and `/lexicon`. `/graphs` only lists and
samples the IFC models of the current user. `GET /livez`, `/readyz`, `/health` and `/build-info` are public.

| Setting | Default | Effect |
| --- | --- | --- |
| `MAX_UPLOAD_BYTES` | `524288000` (500 MiB) | Max multipart upload to `POST /ifc-models` / `POST /ids-files`, and max `POST /ifc-models/{id}/data-payload` body → 413 `PAYLOAD_TOO_LARGE` |
| `MAX_JSON_BODY_BYTES` | `52428800` (50 MiB) | Max size of any other request body, whatever its `Content-Type` → 413 `PAYLOAD_TOO_LARGE` |
| `RATE_LIMIT_ENABLED` | `true` | In-memory, per-process rate limiting |
| `RATE_LIMIT_DEFAULT` | `300/minute` | Per user (verified token `sub`), all routes |
| `RATE_LIMIT_EXPENSIVE` | `20/minute` | Per user, in addition, on uploads, data payloads, check runs (except `run-diagnostic`), publish, sync and conversions |
| `RATE_LIMIT_ANON` | `60/minute` | Per client IP for requests without a token or with a token that does not verify |
| `TRUSTED_PROXY_CIDRS` | `10.42.0.0/16,10.43.0.0/16,127.0.0.1/32` | Peers whose `X-Forwarded-For` is honoured |
| `ENABLE_API_DOCS` | `true` | Serve `/docs`, `/redoc` and `/openapi.json` |
| `ONTOLOGY_SYNC_ALLOWED_HOSTS` | empty | Optional host allowlist for user-supplied sync sources |
| `ONTOLOGY_SYNC_MAX_FETCH_BYTES` | `52428800` (50 MiB) | Largest response accepted from a user-supplied sync source |
| `CHECK_CACHE_MAX_ENTRIES` | `32` | Graphs kept in the in-process check execution cache (LRU) |
| `CELERY_TASK_TIME_LIMIT` / `CELERY_TASK_SOFT_TIME_LIMIT` | `1680` / `1500` | Worker task time limits in seconds; keep the hard limit below RabbitMQ's `consumer_timeout` (default 30 min) |
| `STORAGE_HARD_LIMIT_BYTES`, `STORAGE_USER_QUOTA_BYTES`, `STORAGE_WARN_RATIO` | `1 TB`, `0`, `0.8` | Bucket storage caps (see below) |
| `STORAGE_RECONCILE_ON_STARTUP` | `true` | Reconcile the storage counters once at startup if they never were |
| `ADMIN_GROUP`, `ADMIN_EMAILS` | `firebim-admins`, empty | Who counts as an admin |

Rate-limited responses are 429 `{"detail": ..., "code": "RATE_LIMITED"}` with `Retry-After`,
`X-RateLimit-Limit` and `X-RateLimit-Remaining`. The rate limiter checks a bearer token's signature, issuer and
expiry against Authentik's JWKS (cached) and keys the limits on its `sub`, so refreshing a token does not reset the
budget; a request whose token does not verify is limited per client IP like an anonymous one, even on public routes.

Celery tasks are acknowledged when they finish (one message per worker at a time). A task whose worker process dies
is failed rather than requeued, and the IFC tasks refuse a redelivered message (the whole worker died while running
it), so an IFC that crashes the worker cannot loop through the queue. Because of the late acknowledgement, a task
that runs longer than RabbitMQ's `consumer_timeout` (30 min by default) would be redelivered while still running:
keep `CELERY_TASK_TIME_LIMIT` below it, or raise `consumer_timeout` in the broker configuration first. Unexpected errors return a generic
500 `{"detail": "Internal server error", "request_id": ...}`; the exception is logged with the same request id.

### Storage cap and usage accounting

Every write to the bucket (IFC/IDS uploads, graphs, user-scoped JSON documents, ontology manifests and sync jobs,
worker results) first reserves its bytes in the `storage_usage` tables with an atomic conditional update
(`app/storage/quota.py`); deletes give them back. A write that would take the bucket past
`STORAGE_HARD_LIMIT_BYTES` is refused before anything reaches S3 with 507
`{"detail": ..., "code": "STORAGE_LIMIT_EXCEEDED"}`, or `USER_QUOTA_EXCEEDED` when `STORAGE_USER_QUOTA_BYTES` is set
and the user's attributed objects would exceed it. If the accounting database is unreachable the write is refused
with 503 `STORAGE_ACCOUNTING_UNAVAILABLE` (the cap fails closed). Graphs, ontology manifests and `checks/*` results
are global; uploads, worker results of a model, user-scoped JSON and sync jobs count towards their owner.

New code that writes to the bucket must go through `quota.account_write(...)` (or `delete_object_accounted` /
`record_delete` for deletes). Uploads that bypass the API, such as presigned PUT URLs, must call
`quota.reserve_upload(key, size, user_id=...)` *before* the URL is issued (sign it with the same `ContentLength`) and
`reservation.release()` when the upload is abandoned.

The counters are recomputed from a bucket listing by `python -m app.storage.reconcile`, which prints the resulting
storage summary as JSON (exit 0; 1 on failure; 2 when another reconcile holds the lock). Deployments run it as a
CronJob; the API also runs it once in the background at startup while the counters have never been reconciled.
Writes made while the bucket is being listed are carried over (counted once, or twice if the listing also saw them),
and a reservation released after a reconcile is not subtracted again. Uploads already in flight when a reconcile
starts that finish after the listing passed their key are missing until the next reconcile, so the cap can be
exceeded by at most the bytes in flight at that moment.

The storage tables are created by `create_all` at startup. The Alembic revision `d9553d63ba7d` that also creates them
is chained on `1e9e75ea63ae`; another branch adding revisions on the same parent must be re-parented (or merged with
a merge revision) so there is a single head. Deployed databases have no `alembic_version` table, so stamp a baseline
before ever running `alembic upgrade` there.

## Telemetry and logs

The API (`firebim-api`) and the Celery worker (`firebim-worker`) export OpenTelemetry traces and metrics over
OTLP/HTTP (`app/core/telemetry.py`). Telemetry is off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set, so tests and local
runs export nothing. When it is on, FastAPI requests (except `/livez`, `/readyz`, `/health` and `/metrics`),
SQLAlchemy, botocore (S3), httpx, requests and Celery are traced. A task's trace continues the request that queued it,
and server spans carry the request's `X-Request-ID` as `firebim.request_id`. The worker starts telemetry in each pool
process (`worker_process_init`).

| Variable | Example / default | Effect |
| --- | --- | --- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset (in the clusters `http://alloy.monitoring.svc.cluster.local:4318`) | OTLP/HTTP collector; unset = telemetry off |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | Only `http/protobuf` is supported |
| `OTEL_SERVICE_NAME` | `firebim-api` / `firebim-worker` | Overrides the built-in service name (also the `service` field of JSON logs) |
| `OTEL_RESOURCE_ATTRIBUTES` | `deployment.environment=staging,k8s.cluster.name=firebim-staging` | Extra resource attributes; `deployment.environment` defaults to `ENVIRONMENT`, `service.version` to `GIT_COMMIT` |
| `OTEL_TRACES_SAMPLER` / `OTEL_TRACES_SAMPLER_ARG` | `parentbased_traceidratio` / `0.2` | Trace sampling (SDK default: every trace) |
| `OTEL_SDK_DISABLED` | `false` | `true` turns telemetry off even with an endpoint |
| `OTEL_PYTHON_FASTAPI_EXCLUDED_URLS` | unset | More URL regexes not to trace, added to the probe paths |
| `LOG_FORMAT` | `json` in staging/production, `text` otherwise | `json`: one JSON object per line on stdout |

Metrics are exported every 30 s. Besides the standard HTTP server metrics (`http.server.request.duration`, stable
semantic conventions) the API reports `firebim.ratelimit.rejected{route_class}`, `firebim.storage.rejected{reason}`
and the gauges `firebim.storage.used`, `.limit`, `.objects`, `firebim.storage.prefix.used{prefix}` and
`firebim.users{state=total|active_7d}` (`app/core/metrics.py`). The gauges read the storage accounting tables, cached
for 60 s, so an export queries the database at most once a minute. The worker reports
`firebim.celery.tasks{task,state}` and the histogram `firebim.celery.task.duration{task,state}` (seconds). A task
whose pool process is killed (hard time limit, out of memory) is not counted.

JSON log lines have `timestamp`, `level`, `logger`, `message`, `trace_id`, `span_id` and `service`, plus `request_id`
during a request and any `extra=` fields; `log_event()` fields become top-level keys. Uvicorn's and Celery's own logs
use the same format.

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
