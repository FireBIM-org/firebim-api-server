"""Durable admission contracts shared with the single ingestion worker."""

import hashlib
import json
from dataclasses import dataclass

from fastapi import HTTPException

from app.ingestion.neo_ifcowl_adapter import ADAPTER_VERSION, NeoAdapterError, mapping_reads
from app.ingestion.neo_revision import verify_neo_revision
from app.ingestion.policy import worker_contract
from app.lexicon.ifc4_schema import attribute_datatype
from app.schemas.ifc_mapping import source_selectors
from app.schemas.translation_revision import TranslationPublishedRevision

CONVERTER_RELEASE = "v0.7.4"
CONVERTER_COMMIT = "853f8ad16916a372dcde709f845ee0c7a6014ba8"
CONVERTER_SHA256 = "f773019184ddd3ae6441e97f9bbf5085f4f460b349aac8df5bdc37e4742a4879"


class AdmissionError(HTTPException):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(status_code=status, detail={"code": code, "message": message})


def content_digest(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class AdmissionRuntime:
    enabled: bool = False
    max_input_bytes: int = 0
    upload_ttl_seconds: int = 900

    def require_available(self):
        if not self.enabled or self.max_input_bytes <= 0:
            raise AdmissionError(503, "ingestion_unavailable", "Neo admission is not enabled and resource-qualified")

    def configuration(self) -> dict:
        return {
            "runtime_version": "neo-ingestion/1",
            "converter_release": CONVERTER_RELEASE,
            "converter_commit": CONVERTER_COMMIT,
            "converter_sha256": CONVERTER_SHA256,
            "adapter_version": ADAPTER_VERSION,
            "compiler_version": "ifc4-module-compiler/1",
            "source_schema": "IFC4",
            "max_input_bytes": self.max_input_bytes,
            "producer_mode": "full-ifcowl",
            "geometry": False,
            "worker": worker_contract(),
        }

    def fingerprint(self) -> str:
        return content_digest(self.configuration())


def require_supported_revision(revision: TranslationPublishedRevision) -> None:
    verify_neo_revision(revision)
    # The published language is wider than the adapter's proven source forms.
    for mapping in revision.content.compiled_mappings.values():
        for read in mapping_reads(mapping):
            if read.kind == "quantity" and read.unit not in {"ifc_length", "ifc_area"}:
                raise NeoAdapterError("unsupported_quantity_requirement", "Neo supports only length/area quantities")
            if read.kind == "attribute" and any(
                attribute_datatype(selector.ifc_class, read.name) is None
                for selector in source_selectors(mapping.source)
            ):
                raise NeoAdapterError("unsupported_attribute_requirement", "Neo requires a supported scalar attribute")
