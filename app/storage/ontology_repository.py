from __future__ import annotations

import json
from datetime import date
from hashlib import sha256

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.schemas.ontologies import (
    OntologyDeleteResponse,
    OntologyManifest,
    OntologyPublishRequest,
    OntologyPublishResponse,
    OntologySummaryResponse,
    OntologyVersionListItem,
    OntologyVersionListResponse,
    OntologyVersionManifestEntry,
    OntologyVersionResponse,
)


class OntologyRepositoryError(Exception):
    pass


class OntologyRepositoryNotFoundError(OntologyRepositoryError):
    pass


class OntologyRepository:
    ROOT_PREFIX = "ontologies"
    DISTRIBUTION_ROOT = "/ontologies"
    CANONICAL_ROOT = "https://firebim.org/ontology"

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
        self.client = s3_client or boto3.client(
            "s3",
            endpoint_url=endpoint_url or settings.S3_ENDPOINT_URL,
            aws_access_key_id=access_key_id or settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=(secret_access_key or settings.S3_SECRET_ACCESS_KEY.get_secret_value()),
            region_name=region_name or settings.S3_REGION,
        )

    def _country_prefix(self, ontology_family: str, country: str) -> str:
        family = self._normalize_path_segment(ontology_family, "ontology_family")
        code = self._normalize_path_segment(country, "country")
        return f"{self.ROOT_PREFIX}/{family}/{code}"

    def _manifest_key(self, ontology_family: str, country: str) -> str:
        return f"{self._country_prefix(ontology_family, country)}/manifest.json"

    def _default_content_key(self, ontology_family: str, country: str, version: str) -> str:
        family = self._normalize_path_segment(ontology_family, "ontology_family")
        code = self._normalize_path_segment(country, "country")
        cleaned_version = self._normalize_version(version)
        return f"{self.ROOT_PREFIX}/{family}/{code}/{cleaned_version}/ontology.ttl"

    @staticmethod
    def _normalize_path_segment(value: str, name: str) -> str:
        normalized = str(value).strip().strip("/").lower()
        if not normalized:
            raise ValueError(f"{name} must not be empty")
        return normalized

    @staticmethod
    def _normalize_version(value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("version must not be empty")
        parts = normalized.split(".")
        if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
            raise ValueError("version must follow the release id format YYYY.MM")
        if not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError("version must follow the release id format YYYY.MM")
        return normalized

    @classmethod
    def canonical_family_uri(cls, ontology_family: str) -> str:
        family = cls._normalize_path_segment(ontology_family, "ontology_family")
        return f"{cls.CANONICAL_ROOT}/{family}#"

    @classmethod
    def canonical_country_uri(cls, ontology_family: str, country: str) -> str:
        family = cls._normalize_path_segment(ontology_family, "ontology_family")
        code = cls._normalize_path_segment(country, "country")
        return f"{cls.CANONICAL_ROOT}/{family}/{code}#"

    @staticmethod
    def _parse_issued_at(raw: str) -> tuple[date, str]:
        return date.fromisoformat(raw), raw

    def _read_json_object(self, key: str) -> dict:
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise OntologyRepositoryNotFoundError(f"Ontology manifest not found: {key}") from exc
            raise OntologyRepositoryError(f"Ontology manifest fetch failed: {exc}") from exc
        except BotoCoreError as exc:
            raise OntologyRepositoryError(f"Ontology manifest fetch failed: {exc}") from exc

        try:
            payload = response["Body"].read().decode("utf-8")
            return json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OntologyRepositoryError(f"Ontology manifest is invalid JSON: {exc}") from exc

    def _load_manifest(self, ontology_family: str, country: str) -> OntologyManifest:
        key = self._manifest_key(ontology_family, country)
        data = self._read_json_object(key)
        if "ontology_family" not in data:
            data["ontology_family"] = self._normalize_path_segment(ontology_family, "ontology_family")
        if "country" not in data:
            data["country"] = self._normalize_path_segment(country, "country")
        try:
            return OntologyManifest.model_validate(data)
        except Exception as exc:
            raise OntologyRepositoryError(f"Ontology manifest is invalid: {exc}") from exc

    def _load_manifest_or_empty(self, ontology_family: str, country: str) -> OntologyManifest:
        try:
            return self._load_manifest(ontology_family, country)
        except OntologyRepositoryNotFoundError:
            return OntologyManifest(
                ontology_family=self._normalize_path_segment(ontology_family, "ontology_family"),
                country=self._normalize_path_segment(country, "country"),
                versions=[],
            )

    def _write_manifest(self, manifest: OntologyManifest) -> None:
        key = self._manifest_key(manifest.ontology_family, manifest.country)
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=manifest.model_dump_json(indent=2).encode("utf-8"),
                ContentType="application/json",
            )
        except (BotoCoreError, ClientError) as exc:
            raise OntologyRepositoryError(f"Ontology manifest write failed: {exc}") from exc

    def health(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
            self.client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=f"{self.ROOT_PREFIX}/",
                MaxKeys=1,
            )
            return True
        except (BotoCoreError, ClientError):
            return False

    def _build_item(
        self,
        manifest: OntologyManifest,
        entry: OntologyVersionManifestEntry,
        *,
        latest_version: str | None,
        stable_version: str | None,
        default_version: str | None,
    ) -> OntologyVersionListItem:
        family = manifest.ontology_family
        country = manifest.country
        url = f"{self.DISTRIBUTION_ROOT}/{family}/{country}/versions/{entry.version}"
        content_url = f"{url}/content"
        return OntologyVersionListItem(
            version=entry.version,
            status=entry.status,
            issued_at=entry.issued_at,
            is_latest=entry.version == latest_version,
            is_stable=entry.version == stable_version,
            is_default=entry.version == default_version,
            url=url,
            content_url=content_url,
            content_type=entry.content_type,
            size_bytes=entry.size_bytes,
            checksum_sha256=entry.checksum_sha256,
        )

    def list_versions(self, ontology_family: str, country: str) -> OntologyVersionListResponse:
        manifest = self._load_manifest(ontology_family, country)
        entries = list(manifest.versions)
        entries.sort(key=lambda item: (self._parse_issued_at(item.issued_at), item.version), reverse=True)
        latest_version = entries[0].version if entries else None
        stable_entries = [item for item in entries if item.status.lower() == "stable"]
        stable_version = stable_entries[0].version if stable_entries else None
        default_version = manifest.default_version or stable_version or latest_version

        return OntologyVersionListResponse(
            ontology_family=manifest.ontology_family,
            country=manifest.country,
            versions=[
                self._build_item(
                    manifest,
                    entry,
                    latest_version=latest_version,
                    stable_version=stable_version,
                    default_version=default_version,
                )
                for entry in entries
            ],
        )

    def get_summary(self, ontology_family: str, country: str) -> OntologySummaryResponse:
        listing = self.list_versions(ontology_family, country)
        latest = next((item.version for item in listing.versions if item.is_latest), None)
        stable = next((item.version for item in listing.versions if item.is_stable), None)
        default = next((item.version for item in listing.versions if item.is_default), None)
        family = listing.ontology_family
        code = listing.country
        return OntologySummaryResponse(
            ontology_family=family,
            country=code,
            canonical_family_uri=self.canonical_family_uri(family),
            canonical_country_uri=self.canonical_country_uri(family, code),
            latest_version=latest,
            latest_stable_version=stable,
            default_version=default,
            available_versions_url=f"{self.DISTRIBUTION_ROOT}/{family}/{code}/versions",
            latest_url=f"{self.DISTRIBUTION_ROOT}/{family}/{code}/versions/latest",
            stable_url=f"{self.DISTRIBUTION_ROOT}/{family}/{code}/versions/stable",
        )

    def get_version(self, ontology_family: str, country: str, version: str) -> OntologyVersionResponse:
        version = self._normalize_version(version)
        listing = self.list_versions(ontology_family, country)
        item = next((entry for entry in listing.versions if entry.version == version), None)
        if item is None:
            raise OntologyRepositoryNotFoundError(
                f"Ontology version not found: {listing.ontology_family}/{listing.country}/{version}"
            )
        return OntologyVersionResponse(
            ontology_family=listing.ontology_family,
            country=listing.country,
            canonical_family_uri=self.canonical_family_uri(listing.ontology_family),
            canonical_country_uri=self.canonical_country_uri(listing.ontology_family, listing.country),
            version=item.version,
            status=item.status,
            issued_at=item.issued_at,
            is_latest=item.is_latest,
            is_stable=item.is_stable,
            is_default=item.is_default,
            url=item.url,
            content_url=item.content_url,
            content_type=item.content_type,
            size_bytes=item.size_bytes,
            checksum_sha256=item.checksum_sha256,
        )

    def get_latest_version(self, ontology_family: str, country: str) -> OntologyVersionResponse:
        listing = self.list_versions(ontology_family, country)
        item = next((entry for entry in listing.versions if entry.is_latest), None)
        if item is None:
            raise OntologyRepositoryNotFoundError(
                f"No ontology versions found for: {listing.ontology_family}/{listing.country}"
            )
        return self.get_version(listing.ontology_family, listing.country, item.version)

    def get_latest_stable_version(self, ontology_family: str, country: str) -> OntologyVersionResponse:
        listing = self.list_versions(ontology_family, country)
        item = next((entry for entry in listing.versions if entry.is_stable), None)
        if item is None:
            raise OntologyRepositoryNotFoundError(
                f"No stable ontology version found for: {listing.ontology_family}/{listing.country}"
            )
        return self.get_version(listing.ontology_family, listing.country, item.version)

    def get_version_content(self, ontology_family: str, country: str, version: str) -> tuple[bytes, str]:
        version = self._normalize_version(version)
        manifest = self._load_manifest(ontology_family, country)
        entry = next((item for item in manifest.versions if item.version == version), None)
        if entry is None:
            raise OntologyRepositoryNotFoundError(
                f"Ontology version not found: {manifest.ontology_family}/{manifest.country}/{version}"
            )

        key = entry.object_key or self._default_content_key(manifest.ontology_family, manifest.country, entry.version)
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise OntologyRepositoryNotFoundError(
                    f"Ontology content not found: {manifest.ontology_family}/{manifest.country}/{version}"
                ) from exc
            raise OntologyRepositoryError(f"Ontology content fetch failed: {exc}") from exc
        except BotoCoreError as exc:
            raise OntologyRepositoryError(f"Ontology content fetch failed: {exc}") from exc

        return response["Body"].read(), entry.content_type

    def publish_version(self, payload: OntologyPublishRequest) -> OntologyPublishResponse:
        family = self._normalize_path_segment(payload.ontology_family, "ontology_family")
        country = self._normalize_path_segment(payload.country, "country")
        version = self._normalize_version(payload.version)
        self._parse_issued_at(payload.issued_at)

        manifest = self._load_manifest_or_empty(family, country)
        content_key = self._default_content_key(family, country, version)
        content_bytes = payload.ttl.encode("utf-8")
        checksum = sha256(content_bytes).hexdigest()

        created = True
        existing_entry = next((item for item in manifest.versions if item.version == version), None)
        if existing_entry is not None:
            created = False
            manifest.versions = [item for item in manifest.versions if item.version != version]

        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=content_key,
                Body=content_bytes,
                ContentType=payload.content_type,
            )
        except (BotoCoreError, ClientError) as exc:
            raise OntologyRepositoryError(f"Ontology content publish failed: {exc}") from exc

        manifest.versions.append(
            OntologyVersionManifestEntry(
                version=version,
                status=payload.status,
                issued_at=payload.issued_at,
                object_key=content_key,
                content_type=payload.content_type,
                size_bytes=len(content_bytes),
                checksum_sha256=checksum,
            )
        )

        if (
            payload.default_version
            or manifest.default_version == version
            or (manifest.default_version is None and payload.status == "stable")
        ):
            manifest.default_version = version

        self._write_manifest(manifest)
        response = self.get_version(family, country, version)
        return OntologyPublishResponse(**response.model_dump(), created=created, manifest_updated=True)

    def delete_version(self, ontology_family: str, country: str, version: str) -> OntologyDeleteResponse:
        family = self._normalize_path_segment(ontology_family, "ontology_family")
        code = self._normalize_path_segment(country, "country")
        release_id = self._normalize_version(version)
        manifest = self._load_manifest(family, code)
        entry = next((item for item in manifest.versions if item.version == release_id), None)
        if entry is None:
            raise OntologyRepositoryNotFoundError(f"Ontology version not found: {family}/{code}/{release_id}")

        content_key = entry.object_key or self._default_content_key(family, code, release_id)
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=content_key)
        except (BotoCoreError, ClientError) as exc:
            raise OntologyRepositoryError(f"Ontology content delete failed: {exc}") from exc

        manifest.versions = [item for item in manifest.versions if item.version != release_id]
        if manifest.default_version == release_id:
            stable_versions = [item for item in manifest.versions if item.status == "stable"]
            if stable_versions:
                stable_versions.sort(
                    key=lambda item: (self._parse_issued_at(item.issued_at), item.version),
                    reverse=True,
                )
                manifest.default_version = stable_versions[0].version
            else:
                manifest.default_version = manifest.versions[0].version if manifest.versions else None

        self._write_manifest(manifest)
        return OntologyDeleteResponse(
            ontology_family=family,
            country=code,
            version=release_id,
            deleted=True,
            deleted_content=True,
            manifest_updated=True,
            remaining_versions=len(manifest.versions),
        )
