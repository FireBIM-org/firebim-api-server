"""Bounded, revision-bound IFC display metadata for authorized graph previews."""

import hashlib
import json
from urllib.parse import quote

from app.ingestion.policy import LIMITS
from app.lexicon.ifc_mapping_evaluator import _identity

DISPLAY_INDEX_VERSION = "neo-display-index/1"
NAME_ATTRIBUTES = ("Name", "LongName", "Tag")
MAX_NAME_CHARS = 256
MAX_DESCRIPTION_CHARS = 1024
ENTRY_FIELDS = frozenset({"ifc_class", "global_id", "name", "description", "label_source"})


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8",
    )


def encode_display_index(index):
    data = _json_bytes(index)
    if len(data) > LIMITS.file_bytes:
        raise ValueError("Display index exceeds the artifact byte limit")
    return data


def _text(value, limit):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def build_display_index(source, assertions, model_id, revision_digest):
    """Keep selected source display fields for mapped source entities only."""
    if not isinstance(model_id, str) or not model_id or not isinstance(revision_digest, str) or not revision_digest:
        raise ValueError("Display index requires model and revision bindings")
    asserted = set()
    for assertion in assertions:
        subject = assertion.get("subject")
        if isinstance(subject, str):
            asserted.add(subject)
        obj = assertion.get("object")
        if assertion.get("datatype") is None and isinstance(obj, str):
            asserted.add(obj)

    entities = {}
    for record in source.records:
        source_id = record["id"]
        iri = _identity(model_id, source_id)
        if iri not in asserted:
            continue
        attributes = record.get("attributes", {})
        name, label_source = None, None
        for attribute in NAME_ATTRIBUTES:
            name = _text(attributes.get(attribute), MAX_NAME_CHARS)
            if name is not None:
                label_source = attribute
                break
        identity = source.identities[source_id]
        ifc_class = record.get("ifc_class")
        if not isinstance(ifc_class, str) or not ifc_class:
            raise ValueError("Display index source entity has no IFC class")
        entities[iri] = {
            "ifc_class": ifc_class[:MAX_NAME_CHARS],
            "global_id": _text(identity.guid, MAX_NAME_CHARS),
            "name": name,
            "description": _text(attributes.get("Description"), MAX_DESCRIPTION_CHARS),
            "label_source": label_source,
        }
    index = {
        "index_version": DISPLAY_INDEX_VERSION,
        "model_id": model_id,
        "revision_digest": revision_digest,
        "entities": dict(sorted(entities.items())),
    }
    encode_display_index(index)
    return index


def validation_binding_sha256(report):
    core = {key: value for key, value in report.items() if key != "display_index_sha256"}
    return hashlib.sha256(_json_bytes(core)).hexdigest()


def bind_display_index(index, report):
    """Bind the index to validation facts, then bind those facts to its final digest."""
    if (
        not report.get("valid")
        or report.get("model_id") != index.get("model_id")
        or report.get("revision_digest") != index.get("revision_digest")
    ):
        raise ValueError("Display index and validation report bindings differ")
    bound = {**index, "validation_binding_sha256": validation_binding_sha256(report)}
    data = encode_display_index(bound)
    validation = {**report, "display_index_sha256": hashlib.sha256(data).hexdigest()}
    return bound, validation


def validate_display_index(index, report, model_id, revision_digest):
    """Fail closed when an advertised display artifact or its validation binding is invalid."""
    if (
        not isinstance(index, dict)
        or index.get("index_version") != DISPLAY_INDEX_VERSION
        or index.get("model_id") != model_id
        or index.get("revision_digest") != revision_digest
        or set(index) != {"index_version", "model_id", "revision_digest", "entities", "validation_binding_sha256"}
        or not isinstance(report, dict)
        or not report.get("valid")
        or report.get("model_id") != model_id
        or report.get("revision_digest") != revision_digest
    ):
        raise ValueError("Display index is not bound to this published revision")
    entities = index.get("entities")
    if not isinstance(entities, dict):
        raise ValueError("Display index entities are malformed")
    for iri, entry in entities.items():
        if (
            not isinstance(iri, str)
            or not iri.startswith(f"urn:firebim:model:{quote(model_id, safe='')}:entity:")
            or not isinstance(entry, dict)
            or set(entry) != ENTRY_FIELDS
            or not isinstance(entry["ifc_class"], str)
            or not entry["ifc_class"]
            or entry["global_id"] is not None
            and not isinstance(entry["global_id"], str)
            or entry["name"] is not None
            and not isinstance(entry["name"], str)
            or entry["description"] is not None
            and not isinstance(entry["description"], str)
            or entry["label_source"] not in {*NAME_ATTRIBUTES, None}
        ):
            raise ValueError("Display index entry is malformed")
        if (
            len(entry["ifc_class"]) > MAX_NAME_CHARS
            or entry["global_id"] is not None
            and len(entry["global_id"]) > MAX_NAME_CHARS
            or entry["name"] is not None
            and len(entry["name"]) > MAX_NAME_CHARS
            or entry["description"] is not None
            and len(entry["description"]) > MAX_DESCRIPTION_CHARS
        ):
            raise ValueError("Display index entry exceeds its text limit")
        if (entry["name"] is None) != (entry["label_source"] is None):
            raise ValueError("Display index name provenance is inconsistent")
    data = encode_display_index(index)
    if report.get("display_index_sha256") != hashlib.sha256(data).hexdigest():
        raise ValueError("Display index digest does not match its validation report")
    if index.get("validation_binding_sha256") != validation_binding_sha256(report):
        raise ValueError("Display index validation binding does not match")
