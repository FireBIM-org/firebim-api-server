"""File upload/download helpers: filename hygiene, size checks and streaming to and from S3."""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from collections.abc import Iterator
from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.core import metrics
from app.core.config import settings
from app.core.errors import payload_too_large
from app.storage import quota

logger = logging.getLogger(__name__)

DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_MAX_FILENAME_CHARS = 200
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def display_filename(filename: str | None, *, fallback: str = "upload") -> str:
    """The client's filename without any directory part or control characters (for the DB and the UI)."""
    name = _CONTROL_CHARS.sub("", str(filename or ""))
    name = re.split(r"[\\/]", name)[-1].strip()
    if name in {"", ".", ".."}:
        return fallback
    if len(name) > _MAX_FILENAME_CHARS:
        stem, ext = os.path.splitext(name)
        name = stem[: _MAX_FILENAME_CHARS - len(ext)] + ext
    return name


def safe_storage_filename(filename: str | None, *, fallback: str = "upload") -> str:
    """An ASCII ``[A-Za-z0-9._-]`` filename that is safe in an S3 key and a quoted header value."""
    name = unicodedata.normalize("NFKD", display_filename(filename, fallback=fallback))
    name = name.encode("ascii", "ignore").decode("ascii")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = _UNSAFE_KEY_CHARS.sub("_", stem).strip("._-") or fallback
    ext = _UNSAFE_KEY_CHARS.sub("", ext)[:16]
    suffix = f".{ext}" if ext else ""
    return stem[: _MAX_FILENAME_CHARS - len(suffix)] + suffix


def content_disposition(filename: str | None, *, disposition: str = "attachment") -> str:
    """RFC 6266 ``Content-Disposition`` with an ASCII fallback and the UTF-8 name in ``filename*``."""
    name = display_filename(filename, fallback="download")
    ascii_name = safe_storage_filename(name, fallback="download")
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


def upload_size(file: UploadFile) -> int:
    if file.size is not None:
        return file.size
    position = file.file.tell()
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(position)
    return size


def ensure_upload_size(file: UploadFile, *, max_bytes: int | None = None) -> int:
    """Return the upload's size, or raise 413 PAYLOAD_TOO_LARGE above ``max_bytes`` (default MAX_UPLOAD_BYTES)."""
    limit = settings.MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    size = upload_size(file)
    if size > limit:
        metrics.record_storage_rejected("payload_too_large")
        raise payload_too_large(f"File exceeds the upload limit of {limit} bytes")
    return size


async def upload_file_to_s3(
    s3_client,
    *,
    bucket: str,
    key: str,
    file: UploadFile,
    user_id: str | None,
    content_type: str = "application/octet-stream",
) -> None:
    """Stream an already spooled upload to a new S3 key (multipart for large files) without loading it into memory.

    The upload's exact size is reserved against the storage cap first (507 STORAGE_LIMIT_EXCEEDED /
    USER_QUOTA_EXCEEDED, 503 when accounting is down) and released again if the upload fails."""
    reservation = await run_in_threadpool(quota.reserve_upload, key, upload_size(file), user_id=user_id)
    try:
        file.file.seek(0)
        await run_in_threadpool(
            s3_client.upload_fileobj,
            file.file,
            bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
    except BaseException:
        await run_in_threadpool(reservation.release)
        raise


async def s3_download_response(
    s3_client, *, bucket: str, key: str, filename: str | None, media_type: str
) -> StreamingResponse:
    """Stream an S3 object to the client; nothing is written to local disk."""
    try:
        obj = await run_in_threadpool(s3_client.get_object, Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404", "NotFound"}:
            raise HTTPException(status_code=404, detail="File not found in storage") from exc
        logger.exception("Could not read %s from storage", key)
        raise HTTPException(status_code=502, detail="Could not read file from storage") from exc
    except BotoCoreError as exc:
        logger.exception("Could not read %s from storage", key)
        raise HTTPException(status_code=502, detail="Could not read file from storage") from exc

    body = obj["Body"]

    def iter_body() -> Iterator[bytes]:
        try:
            yield from body.iter_chunks(chunk_size=DOWNLOAD_CHUNK_BYTES)
        finally:
            body.close()

    headers = {"Content-Disposition": content_disposition(filename)}
    content_length = obj.get("ContentLength")
    if isinstance(content_length, int):
        headers["Content-Length"] = str(content_length)
    return StreamingResponse(iter_body(), media_type=media_type, headers=headers)


__all__ = [
    "content_disposition",
    "display_filename",
    "ensure_upload_size",
    "s3_download_response",
    "safe_storage_filename",
    "upload_file_to_s3",
    "upload_size",
]
