from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def _set_default_env(name: str, value: str) -> None:
    os.environ.setdefault(name, value)


def _bootstrap_schema_generation_env() -> None:
    # Minimal settings required for importing app.main without real local secrets.
    _set_default_env("POSTGRES_HOST", "localhost")
    _set_default_env("POSTGRES_PORT", "5432")
    _set_default_env("POSTGRES_USER", "postgres")
    _set_default_env("POSTGRES_PASSWORD", "postgres")
    _set_default_env("POSTGRES_DB", "firebim")
    _set_default_env("S3_ENDPOINT_URL", "http://localhost:9000")
    _set_default_env("S3_BUCKET_NAME", "firebim")
    _set_default_env("S3_ACCESS_KEY_ID", "minioadmin")
    _set_default_env("S3_SECRET_ACCESS_KEY", "minioadmin")
    _set_default_env("S3_REGION", "us-east-1")
    _set_default_env("CELERY_BROKER_URL", "redis://localhost:6379/0")
    _set_default_env("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
    _set_default_env("FUSEKI_HOST", "http://localhost:3030")
    _set_default_env("FUSEKI_APP_USER", "admin")
    _set_default_env("FUSEKI_APP_PASSWORD", "admin")


def main() -> int:
    sys.path.append(str(ROOT_DIR))
    os.chdir(ROOT_DIR)
    _bootstrap_schema_generation_env()

    from app.main import app

    print("Generating OpenAPI schema...")
    openapi_schema = app.openapi()

    with open(ROOT_DIR / "openapi.json", "w", encoding="utf-8") as f:
        json.dump(openapi_schema, f, indent=2)

    print("OpenAPI schema generated and saved to openapi.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
