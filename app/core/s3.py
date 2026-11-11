import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import settings

# S3 Configuration
s3_client = boto3.client(
    "s3",
    endpoint_url=settings.S3_ENDPOINT_URL,
    aws_access_key_id=settings.S3_ACCESS_KEY_ID,
    aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY.get_secret_value(),
    region_name=settings.S3_REGION,
)

# Sign the browser's actual host; rewriting an internally signed URL invalidates SigV4.
s3_upload_client = boto3.client(
    "s3",
    endpoint_url=str(settings.S3_PUBLIC_ENDPOINT_URL or settings.S3_ENDPOINT_URL),
    aws_access_key_id=settings.S3_ACCESS_KEY_ID,
    aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY.get_secret_value(),
    region_name=settings.S3_REGION,
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)

BUCKET_NAME = settings.S3_BUCKET_NAME


def configure_browser_upload_cors(client, bucket: str, origins: list[str]) -> None:
    if not origins:
        return
    try:
        rules = client.get_bucket_cors(Bucket=bucket).get("CORSRules", [])
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchCORSConfiguration":
            raise
        rules = []
    rules = [rule for rule in rules if rule.get("ID") != "firebim-direct-upload"]
    rules.append(
        {
            "ID": "firebim-direct-upload",
            "AllowedOrigins": origins,
            "AllowedMethods": ["PUT"],
            "AllowedHeaders": ["content-type"],
            "MaxAgeSeconds": 600,
        }
    )
    client.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": rules})
