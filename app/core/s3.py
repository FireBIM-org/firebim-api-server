import boto3

from app.core.config import settings

# S3 Configuration
s3_client = boto3.client(
    "s3",
    endpoint_url=settings.S3_ENDPOINT_URL,
    aws_access_key_id=settings.S3_ACCESS_KEY_ID,
    aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY.get_secret_value(),
    region_name=settings.S3_REGION,
)

BUCKET_NAME = settings.S3_BUCKET_NAME
