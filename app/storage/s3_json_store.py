from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TypeVar
from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel

from app.core.config import settings
from app.core.s3 import create_s3_client
from app.models.models import User
from app.storage import quota

ModelT = TypeVar("ModelT", bound=BaseModel)


class S3JsonStoreError(Exception):
    pass


class S3JsonStoreNotFoundError(S3JsonStoreError):
    pass


class UserScopedS3JsonStore:
    def __init__(
        self,
        *,
        bucket_name: str | None = None,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region_name: str | None = None,
        s3_client=None,
    ) -> None:
        self.bucket_name = bucket_name or settings.S3_BUCKET_NAME
        self.client = s3_client or create_s3_client(
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region_name=region_name,
        )

    @staticmethod
    def _user_id(user: User | SimpleNamespace) -> str:
        return str(user.id).strip()

    @classmethod
    def _user_prefix(cls, user: User | SimpleNamespace) -> str:
        return quote(cls._user_id(user), safe="")

    @staticmethod
    def _normalize_document_id(document_id: str) -> str:
        normalized = str(document_id).strip()
        if not normalized:
            raise ValueError("document_id must not be empty")
        return quote(normalized, safe="")

    def document_prefix(self, root_prefix: str, user: User | SimpleNamespace) -> str:
        return f"{root_prefix.rstrip('/')}/{self._user_prefix(user)}/"

    def document_key(self, root_prefix: str, user: User | SimpleNamespace, document_id: str) -> str:
        return f"{self.document_prefix(root_prefix, user)}{self._normalize_document_id(document_id)}.json"

    def read_text(
        self,
        root_prefix: str,
        user: User | SimpleNamespace,
        document_id: str,
        *,
        not_found_message: str,
        fetch_error_message: str,
    ) -> str:
        key = self.document_key(root_prefix, user, document_id)
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise S3JsonStoreNotFoundError(not_found_message) from exc
            raise S3JsonStoreError(f"{fetch_error_message}: {exc}") from exc
        except BotoCoreError as exc:
            raise S3JsonStoreError(f"{fetch_error_message}: {exc}") from exc

        return response["Body"].read().decode("utf-8")

    def read_model(
        self,
        root_prefix: str,
        user: User | SimpleNamespace,
        document_id: str,
        *,
        model_type: type[ModelT],
        not_found_message: str,
        fetch_error_message: str,
        payload_error_message: str,
    ) -> ModelT:
        payload = self.read_text(
            root_prefix,
            user,
            document_id,
            not_found_message=not_found_message,
            fetch_error_message=fetch_error_message,
        )
        try:
            return model_type.model_validate_json(payload)
        except Exception as exc:
            raise S3JsonStoreError(f"{payload_error_message}: {exc}") from exc

    def write_json_text(
        self,
        root_prefix: str,
        user: User | SimpleNamespace,
        document_id: str,
        payload: str,
        *,
        write_error_message: str,
        content_type: str = "application/json",
    ) -> None:
        key = self.document_key(root_prefix, user, document_id)
        body = payload.encode("utf-8")
        try:
            with quota.account_write(self.client, self.bucket_name, key, len(body), user_id=self._user_id(user)):
                self.client.put_object(
                    Bucket=self.bucket_name,
                    Key=key,
                    Body=body,
                    ContentType=content_type,
                )
        except (BotoCoreError, ClientError) as exc:
            raise S3JsonStoreError(f"{write_error_message}: {exc}") from exc

    def write_model(
        self,
        root_prefix: str,
        user: User | SimpleNamespace,
        document_id: str,
        payload: BaseModel,
        *,
        write_error_message: str,
    ) -> None:
        self.write_json_text(
            root_prefix,
            user,
            document_id,
            payload.model_dump_json(indent=2),
            write_error_message=write_error_message,
        )

    def write_dict(
        self,
        root_prefix: str,
        user: User | SimpleNamespace,
        document_id: str,
        payload: dict,
        *,
        write_error_message: str,
    ) -> None:
        self.write_json_text(
            root_prefix,
            user,
            document_id,
            json.dumps(payload, indent=2),
            write_error_message=write_error_message,
        )

    def list_document_ids(self, root_prefix: str, user: User | SimpleNamespace) -> list[str]:
        document_ids: list[str] = []
        continuation_token: str | None = None
        prefix = self.document_prefix(root_prefix, user)

        while True:
            kwargs = {
                "Bucket": self.bucket_name,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token

            try:
                response = self.client.list_objects_v2(**kwargs)
            except (BotoCoreError, ClientError) as exc:
                raise S3JsonStoreError(f"JSON document list failed: {exc}") from exc

            for obj in response.get("Contents", []):
                document_ids.append(obj["Key"].rsplit("/", 1)[-1].removesuffix(".json"))

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

        return document_ids

    def delete_document(
        self,
        root_prefix: str,
        user: User | SimpleNamespace,
        document_id: str,
        *,
        not_found_message: str,
        delete_error_message: str,
    ) -> None:
        key = self.document_key(root_prefix, user, document_id)
        try:
            head = self.client.head_object(Bucket=self.bucket_name, Key=key)
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise S3JsonStoreNotFoundError(not_found_message) from exc
            raise S3JsonStoreError(f"{delete_error_message}: {exc}") from exc
        except BotoCoreError as exc:
            raise S3JsonStoreError(f"{delete_error_message}: {exc}") from exc
        size = head.get("ContentLength") if isinstance(head, dict) else None
        quota.record_delete(key, size if isinstance(size, int) else 0, user_id=self._user_id(user))
