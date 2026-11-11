import hashlib
import json
from types import SimpleNamespace

import pytest

from app.ingestion.display import (
    bind_display_index,
    build_display_index,
    encode_display_index,
    validate_display_index,
)
from app.ingestion.neo_ifcowl_adapter import SourceIdentity
from app.lexicon.ifc_mapping_evaluator import _identity


def test_display_index_contains_only_asserted_entities_and_selected_ifc_fields():
    model_id = "model / one"
    source = SimpleNamespace(
        records=[
            {
                "id": "guid:one / two",
                "ifc_class": "IfcWall",
                "attributes": {
                    "Name": "  Outer wall  ",
                    "LongName": "Ignored long name",
                    "Tag": "W-1",
                    "Description": "West elevation",
                    "Sensitive": "must not be exposed",
                },
            },
            {"id": "guid:duplicate", "ifc_class": "IfcDoor", "attributes": {"Name": "Outer wall"}},
            {"id": "unnamed", "ifc_class": "IfcSlab", "attributes": {"Name": "  ", "Tag": "S-1"}},
            {"id": "unused", "ifc_class": "IfcWindow", "attributes": {"Name": "Not mapped"}},
        ],
        identities={
            "guid:one / two": SourceIdentity("source:one", 1, "guid-one"),
            "guid:duplicate": SourceIdentity("source:two", 2, "guid-two"),
            "unnamed": SourceIdentity("source:three", 3, None),
            "unused": SourceIdentity("source:four", 4, "guid-four"),
        },
    )
    assertions = [
        {"subject": _identity(model_id, "guid:one / two"), "object": "https://example.org/Wall"},
        {
            "subject": _identity(model_id, "guid:one / two"),
            "object": _identity(model_id, "unused"),
            "datatype": "http://www.w3.org/2001/XMLSchema#string",
        },
        {"subject": _identity(model_id, "guid:duplicate"), "object": "https://example.org/Door"},
        {"subject": _identity(model_id, "unnamed"), "object": "S-1"},
    ]

    index = build_display_index(source, assertions, model_id, "revision-digest")

    assert index["model_id"] == model_id
    assert index["revision_digest"] == "revision-digest"
    assert set(index["entities"]) == {
        _identity(model_id, "guid:one / two"),
        _identity(model_id, "guid:duplicate"),
        _identity(model_id, "unnamed"),
    }
    assert index["entities"][_identity(model_id, "guid:one / two")] == {
        "ifc_class": "IfcWall",
        "global_id": "guid-one",
        "name": "Outer wall",
        "description": "West elevation",
        "label_source": "Name",
    }
    assert index["entities"][_identity(model_id, "guid:duplicate")]["name"] == "Outer wall"
    assert index["entities"][_identity(model_id, "unnamed")]["name"] == "S-1"
    assert index["entities"][_identity(model_id, "unnamed")]["label_source"] == "Tag"
    assert "Sensitive" not in json.dumps(index)
    assert "unused" not in json.dumps(index)


def test_display_index_uses_deterministic_name_fallback_priority():
    source = SimpleNamespace(
        records=[
            {"id": "long", "ifc_class": "IfcWall", "attributes": {"LongName": "Long"}},
            {"id": "tag", "ifc_class": "IfcDoor", "attributes": {"Tag": "D-1"}},
            {"id": "missing", "ifc_class": "IfcSlab", "attributes": {"Name": None}},
        ],
        identities={
            key: SourceIdentity("source:" + key, position, None)
            for position, key in enumerate(("long", "tag", "missing"), start=1)
        },
    )
    model_id = "model"
    assertions = [{"subject": _identity(model_id, key)} for key in ("long", "tag", "missing")]

    entities = build_display_index(source, assertions, model_id, "revision")["entities"]

    assert entities[_identity(model_id, "long")]["name"] == "Long"
    assert entities[_identity(model_id, "long")]["label_source"] == "LongName"
    assert entities[_identity(model_id, "tag")]["name"] == "D-1"
    assert entities[_identity(model_id, "tag")]["label_source"] == "Tag"
    assert entities[_identity(model_id, "missing")]["name"] is None
    assert entities[_identity(model_id, "missing")]["label_source"] is None


def test_display_index_binds_revision_and_validation_report_without_circular_hashes():
    report = {
        "validation_version": "neo-destination-rdf/1",
        "valid": True,
        "model_id": "model",
        "revision_digest": "revision",
        "destination_sha256": "destination",
    }
    index = {"index_version": "neo-display-index/1", "model_id": "model", "revision_digest": "revision", "entities": {}}

    bound, validation = bind_display_index(index, report)

    assert validation["display_index_sha256"] == hashlib.sha256(encode_display_index(bound)).hexdigest()
    assert validate_display_index(bound, validation, "model", "revision") is None
    with pytest.raises(ValueError):
        validate_display_index(bound, {**validation, "display_index_sha256": "wrong"}, "model", "revision")
    with pytest.raises(ValueError):
        validate_display_index(bound, {**validation, "destination_sha256": "changed"}, "model", "revision")
    with pytest.raises(ValueError):
        validate_display_index(bound, validation, "other-model", "revision")
    with pytest.raises(ValueError):
        validate_display_index(bound, validation, "model", "other-revision")
