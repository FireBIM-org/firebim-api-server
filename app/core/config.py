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
    model_validator,
)
from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",")]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


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
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"

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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

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

    CHECKS_SOLVER: str = "standard"


settings = Settings()
