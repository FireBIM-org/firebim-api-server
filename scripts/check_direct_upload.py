"""Exercise signed browser transfer against real S3 using a disposable private bucket.

Run from the API root with storage settings exported:
PYTHONPATH=. .venv/bin/python scripts/check_direct_upload.py
Requires bucket create/delete and CORS permissions; never modifies the configured bucket.
"""

import argparse
import hashlib
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests

from app.core.config import settings
from app.core.s3 import configure_browser_upload_cors, s3_client, s3_upload_client
from app.storage.ingestion_objects import IngestionObjects


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://localhost:3000", help="Browser origin to verify")
    args = parser.parse_args()
    bucket = f"firebim-upload-smoke-{uuid.uuid4().hex}"
    origin = args.origin
    staging, raw = "ingestion/staging/fixture.ifc", "ingestion/raw/fixture.ifc"
    data = (Path(__file__).parents[1] / "tests/fixtures/neo/semantic.ifc").read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    options = (
        {}
        if settings.S3_REGION == "us-east-1"
        else {
            "CreateBucketConfiguration": {"LocationConstraint": settings.S3_REGION},
        }
    )
    s3_client.create_bucket(Bucket=bucket, **options)
    try:
        configure_browser_upload_cors(s3_client, bucket, [origin])
        objects = IngestionObjects(s3_client, bucket, signing_client=s3_upload_client)
        url = objects.upload_url(staging, len(data), 60)
        preflight_headers = {
            "Origin": origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        }
        preflight = requests.options(url, headers=preflight_headers, timeout=10)
        assert preflight.ok, f"Preflight rejected: {preflight.status_code}"
        assert preflight.headers.get("Access-Control-Allow-Origin") == origin, "Origin not allowed"
        assert "PUT" in preflight.headers.get("Access-Control-Allow-Methods", ""), "PUT not allowed"
        assert "content-type" in preflight.headers.get("Access-Control-Allow-Headers", "").lower()

        denied = requests.options(url, headers={**preflight_headers, "Origin": "https://untrusted.example"}, timeout=10)
        assert denied.headers.get("Access-Control-Allow-Origin") not in {"*", "https://untrusted.example"}

        headers = {"Origin": origin, "Content-Type": "application/octet-stream"}
        uploaded = requests.put(url, data=data, headers=headers, timeout=10)
        assert uploaded.ok, f"Signed PUT rejected: {uploaded.status_code}"
        assert uploaded.headers.get("Access-Control-Allow-Origin") == origin
        objects.finalize(staging, raw, len(data), digest)

        # The browser can reuse its staging URL but cannot alter the admitted snapshot.
        overwritten = requests.put(url, data=b"x" * len(data), headers=headers, timeout=10)
        assert overwritten.ok, f"Staging replay rejected: {overwritten.status_code}"
        assert objects.read_verified(raw, len(data), digest) == data

        changed_host = requests.put(url, data=data, headers={**headers, "Host": "rewritten.example"}, timeout=10)
        assert changed_host.status_code == 403, "Changed Host must invalidate the signature"
        unsigned = urlsplit(url)._replace(query="").geturl()
        anonymous = requests.get(unsigned, timeout=10)
        assert anonymous.status_code in {403, 404}, "Staging data must not be publicly readable"
        print("PASS: preflight, direct signed PUT, private readback, immutable snapshot and Host fencing")
    finally:
        for key in (staging, raw):
            s3_client.delete_object(Bucket=bucket, Key=key)
        s3_client.delete_bucket(Bucket=bucket)


if __name__ == "__main__":
    main()
