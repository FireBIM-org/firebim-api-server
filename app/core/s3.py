import boto3
from botocore.config import Config

from app.core.config import settings

# Shared client settings: fail fast on unreachable endpoints, allow slow large transfers, retry transient errors.
S3_CLIENT_CONFIG = Config(
    connect_timeout=5,
    read_timeout=60,
    retries={"max_attempts": 5, "mode": "standard"},
)


def create_s3_client(
    *,
    endpoint_url: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    region_name: str | None = None,
):
    """Build a boto3 S3 client from settings; any argument overrides the matching setting."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or settings.S3_ENDPOINT_URL,
        aws_access_key_id=access_key_id or settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=secret_access_key or settings.S3_SECRET_ACCESS_KEY.get_secret_value(),
        region_name=region_name or settings.S3_REGION,
        config=S3_CLIENT_CONFIG,
    )


# S3 Configuration
s3_client = create_s3_client()

BUCKET_NAME = settings.S3_BUCKET_NAME
