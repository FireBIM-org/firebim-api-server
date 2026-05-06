from __future__ import annotations

import os
from pathlib import Path

import boto3
import httpx

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

API_BASE_URL = os.getenv("FIREBIM_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
MINIO_ENDPOINT = os.getenv("FIREBIM_MINIO_ENDPOINT", "http://127.0.0.1:9000").rstrip("/")
MINIO_REGION = os.getenv("FIREBIM_MINIO_REGION", "us-east-1")
MINIO_BUCKET = os.getenv("FIREBIM_MINIO_BUCKET", "firebim")
MINIO_ACCESS_KEY = os.getenv("FIREBIM_MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("FIREBIM_MINIO_SECRET_KEY", "minioadmin")

BUILDING_FIXTURES = {
    "ifc-inspired-small": "building_small_ifc_inspired.ttl",
    "ifc-inspired-medium": "building_medium_ifc_inspired.ttl",
    "ifc-inspired-large": "building_large_ifc_inspired.ttl",
    "ifc-inspired-very-large": "building_very_large_ifc_inspired.ttl",
}


def ensure_bucket() -> None:
    client = boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name=MINIO_REGION,
    )
    existing = {item["Name"] for item in client.list_buckets().get("Buckets", [])}
    if MINIO_BUCKET in existing:
        print(f"[ok] MinIO bucket exists: {MINIO_BUCKET}")
        return

    create_kwargs = {"Bucket": MINIO_BUCKET}
    if MINIO_REGION != "us-east-1":
        create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": MINIO_REGION}
    client.create_bucket(**create_kwargs)
    print(f"[created] MinIO bucket: {MINIO_BUCKET}")


def publish_building_fixtures() -> None:
    with httpx.Client(timeout=120) as client:
        for graph_id, fixture_name in BUILDING_FIXTURES.items():
            ttl = (FIXTURES / fixture_name).read_text(encoding="utf-8")
            response = client.post(
                f"{API_BASE_URL}/graphs/publish",
                json={"graph_id": graph_id, "ttl": ttl},
            )
            response.raise_for_status()
            body = response.json()
            status = "updated" if body.get("overwritten") else "published"
            print(f"[{status}] graph fixture: {graph_id}")


def main() -> int:
    ensure_bucket()
    publish_building_fixtures()
    print("[done] local stack seed completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
