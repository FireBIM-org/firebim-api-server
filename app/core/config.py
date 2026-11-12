import ipaddress
import secrets
import warnings
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AnyUrl,
    BeforeValidator,
    Field,
    HttpUrl,
    PostgresDsn,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.rate_limit import parse_rate_limit


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",")]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


def _split_csv(value: str, *, lower: bool = False) -> list[str]:
    items = [item.strip() for item in (value or "").split(",") if item.strip()]
    return [item.lower() for item in items] if lower else items


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
        secrets_dir="/var/run/secrets",
    )
    # API_V1_STR: str = "/api/v1"
    SECRET_KEY: SecretStr = Field(default=secrets.token_urlsafe(32))
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    # FRONTEND_HOST: str = "http://localhost:5173"
    ENVIRONMENT: Literal["local", "staging", "production", "test"] = "local"

    BACKEND_CORS_ORIGINS: Annotated[list[AnyUrl] | str, BeforeValidator(parse_cors)] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return (
            [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS]
            + [
                # self.FRONTEND_HOST
            ]
        )

    # PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    POSTGRES_HOST: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: SecretStr = Field()
    # POSTGRES_PASSWORD_FILE: str = ""
    POSTGRES_DB: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> PostgresDsn:
        return MultiHostUrl.build(
            scheme="postgresql",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD.get_secret_value(),
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
        )

    # Authentik OIDC
    AUTHENTIK_ISSUER_URL: str = "https://auth.firebim.org/application/o/firebim/"
    # Optional: internal URL for JWKS fetching when running inside Docker.
    # If not set, falls back to AUTHENTIK_ISSUER_URL.
    AUTHENTIK_INTERNAL_URL: str = ""

    # Public ontology registry (firebim.org) used by POST /ontologies/sync.
    # Any URL serving an index in the same format may be used as source_url per request.
    ONTOLOGY_REGISTRY_URL: str = "https://firebim.org/documentation/ontologies/index.json"
    ONTOLOGY_SYNC_TIMEOUT_SECONDS: float = 120.0

    # Local development only: (re)publish the bundled demo graphs and demo rules at startup.
    # Set to false to keep a hand-curated workspace — nothing is seeded and nothing you deleted comes back.
    SEED_DEMO_CONTENT: bool = True

    # S3
    S3_ENDPOINT_URL: str
    S3_BUCKET_NAME: str
    S3_ACCESS_KEY_ID: str
    S3_SECRET_ACCESS_KEY: SecretStr = Field()
    # S3_SECRET_ACCESS_KEY_FILE: str
    S3_REGION: str

    # Worker
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str

    # SMTP_TLS: bool = True
    # SMTP_SSL: bool = False
    # SMTP_PORT: int = 587
    # SMTP_HOST: str | None = None
    # SMTP_USER: str | None = None
    # SMTP_PASSWORD: str | None = None
    # EMAILS_FROM_EMAIL: EmailStr | None = None
    # EMAILS_FROM_NAME: EmailStr | None = None

    # EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    # EMAIL_TEST_USER: EmailStr = "test@example.com"
    # FIRST_SUPERUSER: EmailStr
    # FIRST_SUPERUSER_PASSWORD: str

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", for security, please change it, at least for deployments.'
            )
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        self._check_default_secret("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD)
        # self._check_default_secret(
        #     "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        # )

        return self

    @model_validator(mode="after")
    def _check_celery_time_limits(self) -> Self:
        if self.CELERY_TASK_SOFT_TIME_LIMIT >= self.CELERY_TASK_TIME_LIMIT:
            raise ValueError("CELERY_TASK_SOFT_TIME_LIMIT must be lower than CELERY_TASK_TIME_LIMIT")
        return self

    CHECKS_SOLVER: str = "standard"
    # Upper bound on distinct graphs/rules kept in the in-process check execution cache (LRU).
    CHECK_CACHE_MAX_ENTRIES: int = Field(default=32, ge=1)

    # Celery task limits (seconds). The soft limit raises SoftTimeLimitExceeded inside the task first. Tasks are
    # acknowledged late, so the hard limit must stay below RabbitMQ's consumer_timeout (default 1800 s).
    CELERY_TASK_TIME_LIMIT: int = Field(default=1680, ge=1)
    CELERY_TASK_SOFT_TIME_LIMIT: int = Field(default=1500, ge=1)

    # Optional comma-separated allowlist of hosts a user-supplied ontology sync source_url may point at.
    # Empty = any public host (private, loopback, link-local and other internal addresses are always refused).
    ONTOLOGY_SYNC_ALLOWED_HOSTS: str = ""
    # Largest response accepted from a user-supplied sync source (index or TTL file); ONTOLOGY_SYNC_TIMEOUT_SECONDS is
    # the deadline for each whole fetch from such a source, redirects included.
    ONTOLOGY_SYNC_MAX_FETCH_BYTES: int = Field(default=52_428_800, ge=1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ontology_sync_allowed_hosts(self) -> list[str]:
        return _split_csv(self.ONTOLOGY_SYNC_ALLOWED_HOSTS, lower=True)

    # Storage limits. Writes that would exceed the hard limit are rejected with 507; 0 disables a limit.
    STORAGE_HARD_LIMIT_BYTES: int = Field(default=1_000_000_000_000, ge=0)
    STORAGE_USER_QUOTA_BYTES: int = Field(default=0, ge=0)
    STORAGE_WARN_RATIO: float = Field(default=0.8, ge=0, le=1)
    # Recompute the storage counters from a bucket listing in the background at startup, once, if they have never
    # been reconciled (``python -m app.storage.reconcile`` does the same on demand).
    STORAGE_RECONCILE_ON_STARTUP: bool = True

    # Request body limits (413 when exceeded). MAX_UPLOAD_BYTES applies to multipart file uploads,
    # MAX_JSON_BODY_BYTES to every other request body.
    MAX_UPLOAD_BYTES: int = Field(default=524_288_000, ge=1)
    MAX_JSON_BODY_BYTES: int = Field(default=52_428_800, ge=1)

    # Rate limiting (in-memory, per process). Limits are "<count>/<second|minute|hour|day>".
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT: str = "300/minute"
    RATE_LIMIT_EXPENSIVE: str = "20/minute"
    RATE_LIMIT_ANON: str = "60/minute"
    # X-Forwarded-For is only honoured when the direct peer is inside one of these networks.
    TRUSTED_PROXY_CIDRS: str = "10.42.0.0/16,10.43.0.0/16,127.0.0.1/32"

    @field_validator("RATE_LIMIT_DEFAULT", "RATE_LIMIT_EXPENSIVE", "RATE_LIMIT_ANON")
    @classmethod
    def _validate_rate_limit(cls, value: str) -> str:
        parse_rate_limit(value)
        return value.strip()

    @field_validator("TRUSTED_PROXY_CIDRS")
    @classmethod
    def _validate_trusted_proxy_cidrs(cls, value: str) -> str:
        for cidr in _split_csv(value):
            ipaddress.ip_network(cidr, strict=False)
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def trusted_proxy_cidrs(self) -> list[str]:
        return _split_csv(self.TRUSTED_PROXY_CIDRS)

    # Serve /docs, /redoc and /openapi.json.
    ENABLE_API_DOCS: bool = True

    # Log output of the API and the worker: one JSON object per line (with trace_id/span_id) or plain text.
    # Unset = json in staging/production, text otherwise.
    LOG_FORMAT: Literal["json", "text"] | None = None

    @field_validator("LOG_FORMAT", mode="before")
    @classmethod
    def _normalize_log_format(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def log_format(self) -> Literal["json", "text"]:
        if self.LOG_FORMAT is not None:
            return self.LOG_FORMAT
        return "json" if self.ENVIRONMENT in ("staging", "production") else "text"

    # Admin access: members of this Authentik group (JWT `groups` claim), or the listed verified emails.
    ADMIN_GROUP: str = "firebim-admins"
    ADMIN_EMAILS: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def admin_emails(self) -> list[str]:
        return _split_csv(self.ADMIN_EMAILS, lower=True)


settings = Settings()
