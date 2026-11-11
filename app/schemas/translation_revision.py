from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.schemas.ifc_mapping import ExecutableMapping
from app.schemas.translation import (
    TranslationContract,
    TranslationOntologyPin,
    TranslationRevisionReference,
    TranslationValidationDiagnostic,
)


class TranslationOntologyArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pin: TranslationOntologyPin
    ttl: str


class TranslationRevisionContent(BaseModel):
    """Self-contained execution snapshot; no mutable library lookups are needed to read it."""

    model_config = ConfigDict(extra="forbid")

    snapshot_version: Literal["1"] = "1"
    profile_id: str
    draft_generation: int = Field(ge=1)
    name: str
    description: str | None = None
    source_contract: TranslationContract
    contract: TranslationContract
    compiled_mappings: dict[str, ExecutableMapping]
    ontology_artifacts: list[TranslationOntologyArtifact]
    diagnostics: list[TranslationValidationDiagnostic]
    compiler_version: Literal["ifc4-module-compiler/1"] = "ifc4-module-compiler/1"
    source_schema_provenance: dict[str, JsonValue]

    def digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TranslationPublishedRevision(BaseModel):
    """Validated immutable contract; publication does not attest to runtime availability."""

    model_config = ConfigDict(extra="forbid")

    revision_id: str
    published_at: str
    content_digest: str
    content: TranslationRevisionContent


class TranslationRevisionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    revisions: list[TranslationRevisionReference]
