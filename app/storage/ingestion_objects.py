"""Private staging and server-only write-once input snapshots."""

import base64
import hashlib
import re

from botocore.exceptions import BotoCoreError, ClientError

from app.ingestion.jobs import AdmissionError


def _mask_step(text: str) -> str:
    chunks = []
    cursor = 0
    marker = re.compile(r"/\*|'|\*/")
    while match := marker.search(text, cursor):
        start = match.start()
        chunks.append(text[cursor:start])
        if match[0] == "/*":
            end = text.find("*/", start + 2)
            if end == -1:
                raise AdmissionError(422, "invalid_ifc", "Unterminated STEP comment")
            end += 2
        elif match[0] == "'":
            end = start + 1
            while True:
                end = text.find("'", end)
                if end == -1:
                    raise AdmissionError(422, "invalid_ifc", "Unterminated STEP string")
                end += 1
                if end == len(text) or text[end] != "'":
                    break
                end += 1
        else:
            raise AdmissionError(422, "invalid_ifc", "Unexpected STEP comment terminator")
        chunks.append(" " * (end - start))
        cursor = end
    chunks.append(text[cursor:])
    return "".join(chunks)


def validate_ifc4(data: bytes) -> None:
    if any(byte < 32 and byte not in (9, 10, 13) for byte in data):
        raise AdmissionError(422, "invalid_ifc", "Expected an IFC STEP text file")
    text = data.decode("latin-1")
    # Tokenize quoted strings and comments before recognizing structure; neither can spoof a header/footer.
    pattern = re.compile(r"/\*.*?\*/|'(?:[^']|'')*'", re.DOTALL)
    masked = _mask_step(text)
    header = re.match(
        r"\s*ISO-10303-21\s*;\s*HEADER\s*;(.*?)\bENDSEC\s*;\s*DATA\s*;", masked, flags=re.DOTALL | re.IGNORECASE
    )
    if (
        not header
        or header.end() > 65536
        or not re.search(
            r";\s*ENDSEC\s*;\s*END-ISO-10303-21\s*;\s*\Z",
            masked,
            flags=re.IGNORECASE,
        )
        or not re.search(r"#[1-9][0-9]*\s*=\s*IFC[A-Z0-9_]+\s*\(", masked[header.end() :], re.IGNORECASE)
    ):
        raise AdmissionError(422, "invalid_ifc", "Expected a complete IFC STEP envelope and entity data")
    # Masking preserves offsets, so a quoted ENDSEC or semicolon cannot terminate the header.
    schemas = []
    start = 0
    for separator in re.finditer(";", masked[: header.end()]):
        statement = text[start : separator.start()]
        statement = pattern.sub(lambda match: " " if match[0].startswith("/*") else match[0], statement).strip()
        start = separator.end()
        if re.match(r"FILE_SCHEMA\s*\(", statement, re.IGNORECASE):
            schemas.append(statement)
    if len(schemas) != 1 or not re.fullmatch(r"FILE_SCHEMA\s*\(\s*\(\s*'IFC4'\s*\)\s*\)", schemas[0], re.IGNORECASE):
        raise AdmissionError(422, "unsupported_ifc_schema", "Only IFC4 STEP uploads are supported")


class IngestionObjects:
    @classmethod
    def for_worker(cls):
        import boto3
        from botocore.config import Config

        from app.core.config import settings
        from app.ingestion.policy import STORAGE_TIMEOUTS

        client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY.get_secret_value(),
            region_name=settings.S3_REGION,
            config=Config(
                connect_timeout=STORAGE_TIMEOUTS["connect_timeout"],
                read_timeout=STORAGE_TIMEOUTS["read_timeout"],
                retries={"total_max_attempts": STORAGE_TIMEOUTS["total_max_attempts"], "mode": "standard"},
            ),
        )
        return cls(client, settings.S3_BUCKET_NAME)

    def __init__(self, client, bucket: str, *, signing_client=None):
        self.client = client
        self.bucket = bucket
        self.signing_client = signing_client if signing_client is not None else client

    def upload_url(self, key: str, size: int, ttl: int) -> str:
        try:
            return self.signing_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    "ContentType": "application/octet-stream",
                    "ContentLength": size,
                },
                ExpiresIn=ttl,
                HttpMethod="PUT",
            )
        except (BotoCoreError, ClientError) as exc:
            raise AdmissionError(503, "storage_unavailable", "Upload storage is unavailable") from exc

    def read_verified(self, key: str, size: int, sha256: str) -> bytes:
        body = None
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response["Body"]
            if response.get("ContentLength", size) != size:
                raise AdmissionError(422, "input_size_mismatch", "Stored upload size does not match the declared bytes")
            chunks = []
            remaining = size + 1
            while remaining:
                chunk = body.read(min(remaining, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) != size:
                raise AdmissionError(422, "input_size_mismatch", "Actual upload bytes do not match the declared size")
            if hashlib.sha256(data).hexdigest() != sha256:
                raise AdmissionError(
                    422,
                    "input_hash_mismatch",
                    "Actual upload bytes do not match the declared SHA-256",
                )
            return data
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code")) in {"NoSuchKey", "404", "NotFound"}:
                raise AdmissionError(409, "input_missing", "The upload has not arrived in private storage") from exc
            raise AdmissionError(503, "storage_unavailable", "Private input storage is unavailable") from exc
        except BotoCoreError as exc:
            raise AdmissionError(503, "storage_unavailable", "Private input storage is unavailable") from exc
        finally:
            if body is not None:
                body.close()

    def finalize(self, staging_key: str, raw_key: str, size: int, sha256: str) -> None:
        data = self.read_verified(staging_key, size, sha256)
        validate_ifc4(data)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=raw_key,
                Body=data,
                ContentType="application/octet-stream",
                IfNoneMatch="*",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(sha256)).decode(),
                Metadata={"sha256": sha256},
            )
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code")) not in {"PreconditionFailed", "412"}:
                raise AdmissionError(503, "storage_unavailable", "Could not finalize immutable input") from exc
        except BotoCoreError as exc:
            raise AdmissionError(503, "storage_unavailable", "Could not finalize immutable input") from exc
        # Also verify a pre-existing snapshot after an interrupted admission, never overwrite it.
        self.read_verified(raw_key, size, sha256)

    def write_private(self, key: str, data: bytes) -> dict:
        digest = hashlib.sha256(data).hexdigest()
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType="application/octet-stream",
                IfNoneMatch="*",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode(),
                Metadata={"sha256": digest},
            )
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code")) not in {"PreconditionFailed", "412"}:
                raise AdmissionError(
                    503,
                    "storage_unavailable",
                    "Could not retain private processing artifact",
                ) from exc
        except BotoCoreError as exc:
            raise AdmissionError(503, "storage_unavailable", "Could not retain private processing artifact") from exc
        self.read_verified(key, len(data), digest)
        return {"key": key, "bytes": len(data), "sha256": digest}

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code")) in {"NoSuchKey", "404", "NotFound"}:
                return False
            raise AdmissionError(503, "storage_unavailable", "Could not verify graph identity availability") from exc
        except BotoCoreError as exc:
            raise AdmissionError(503, "storage_unavailable", "Could not verify graph identity availability") from exc
