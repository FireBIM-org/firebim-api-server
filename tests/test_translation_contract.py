import pytest
from pydantic import ValidationError

from app.schemas.translation import (
    TranslationContract,
    TranslationDraftMapping,
    TranslationGenerationRequest,
    TranslationOntologyPin,
    TranslationProfileCreateRequest,
    TranslationProfileSummary,
    TranslationProfileUpdateRequest,
    TranslationSource,
    TranslationStoredProfile,
)


def test_minimal_rule_free_draft_defaults():
    request = TranslationProfileCreateRequest(kind="ifc_translation", name="  IFC draft  ")

    assert request.name == "IFC draft"
    assert request.description is None
    assert request.contract.model_dump() == {
        "kind": "ifc_translation",
        "contract_version": "1",
        "source": {
            "schema": "IFC4",
            "adapter": "native_ifc",
            "extraction_contract": "native-ifc-record/1",
        },
        "destination_release": "2026.09.04",
        "destination_modules": [],
        "mappings": [],
        "optional_rule_ids": [],
    }


def test_mutable_defaults_are_independent():
    first = TranslationProfileCreateRequest(kind="ifc_translation", name="First")
    second = TranslationProfileCreateRequest(kind="ifc_translation", name="Second")
    first.contract.mappings.append(TranslationDraftMapping(id="wall"))
    first.contract.optional_rule_ids.append("rule-1")
    first.contract.destination_modules.append(TranslationOntologyPin(family="fbo", country="dk"))

    assert second.contract == TranslationContract()
    first_mapping = TranslationDraftMapping(id="first")
    second_mapping = TranslationDraftMapping(id="second")
    first_mapping.definition["unsupported"] = True
    assert second_mapping.definition == {}
    first_pin = TranslationOntologyPin(family="fbo", country="dk")
    second_pin = TranslationOntologyPin(family="fbo", country="dk")
    first_pin.dependencies.append("fbo/global")
    assert second_pin.dependencies == []


def test_incomplete_pin_and_unsupported_mapping_round_trip():
    contract = TranslationContract.model_validate(
        {
            "destination_modules": [{"family": "fbo", "country": "dk", "dependencies": ["fbo/global"]}],
            "mappings": [
                {
                    "id": "mapping-1",
                    "seed_provenance": {"source": "suggestion", "nodes": [1, None, {"verified": False}]},
                    "definition": {"future_operator": {"arguments": ["unresolved", 3, True, None]}},
                }
            ],
            "optional_rule_ids": ["rule-1"],
        }
    )

    assert TranslationContract.model_validate_json(contract.model_dump_json()) == contract
    pin = contract.destination_modules[0]
    assert pin.version == "2026.09.04"
    assert pin.ontology_iri is None
    assert pin.version_iri is None
    assert pin.sha256 is None
    assert pin.artifact_ref is None
    assert pin.dependencies == ["fbo/global"]
    mapping = contract.mappings[0]
    assert mapping.enabled is False
    assert mapping.review_state == "unresolved"
    assert mapping.definition == {"future_operator": {"arguments": ["unresolved", 3, True, None]}}


def test_pin_metadata_is_retained_without_claiming_verification():
    payload = {
        "family": "fbo",
        "country": "dk",
        "version": "2026.09.04",
        "ontology_iri": "https://example.org/ontology",
        "version_iri": "https://example.org/ontology/2026.09.04",
        "sha256": "unverified-draft-value",
        "artifact_ref": "s3://draft-bucket/module.ttl",
        "dependencies": ["fbo/global"],
    }

    assert TranslationOntologyPin.model_validate(payload).model_dump() == payload


@pytest.mark.parametrize("rating", ["EI60", "REI 120", "  EI 30 / unclassified  ", "unknown", 60, None])
def test_mapping_preserves_raw_fire_rating_without_inference(rating):
    definition = {"label": "Fire rating EI60", "source": {"FireRating": rating}}
    mapping = TranslationDraftMapping(id="fire-rating", definition=definition)

    restored = TranslationDraftMapping.model_validate_json(mapping.model_dump_json())
    assert restored.definition == definition
    assert "kind" not in restored.model_dump()
    assert restored.seed_provenance is None


@pytest.mark.parametrize("review_state", ["suggestion", "accepted", "rejected", "unresolved"])
def test_mapping_review_states(review_state):
    mapping = TranslationDraftMapping(id="mapping-1", enabled=True, review_state=review_state)

    assert mapping.enabled is True
    assert mapping.review_state == review_state


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "rule_profile"},
        {"contract_version": "2"},
        {"source": {"schema": "IFC2X3"}},
        {"source": {"adapter": "ifc_owl"}},
        {"source": {"extraction_contract": "native-ifc-record/2"}},
        {"destination_release": "latest"},
        {"destination_release": "2026.04.23"},
    ],
)
def test_contract_rejects_unsupported_envelope_semantics(payload):
    with pytest.raises(ValidationError):
        TranslationContract.model_validate(payload)


@pytest.mark.parametrize("name", ["", " ", "\t\n", "a" * 201])
@pytest.mark.parametrize("model", [TranslationProfileCreateRequest, TranslationProfileUpdateRequest])
def test_profile_names_must_be_nonblank_and_bounded(model, name):
    payload = {"name": name}
    if model is TranslationProfileCreateRequest:
        payload["kind"] = "ifc_translation"
    else:
        payload["expected_generation"] = 1

    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize("mapping_id", ["", " ", "\t\n"])
def test_mapping_ids_must_be_nonblank(mapping_id):
    with pytest.raises(ValidationError):
        TranslationDraftMapping(id=mapping_id)


def test_create_requires_explicit_translation_kind():
    with pytest.raises(ValidationError):
        TranslationProfileCreateRequest(name="Draft")
    with pytest.raises(ValidationError):
        TranslationProfileCreateRequest(kind="legacy", name="Draft")


def test_update_distinguishes_omitted_fields_and_explicit_description_clear():
    omitted = TranslationProfileUpdateRequest(expected_generation=1)
    cleared = TranslationProfileUpdateRequest(expected_generation=1, description=None)
    renamed = TranslationProfileUpdateRequest(expected_generation=1, name="  Renamed draft  ")

    assert omitted.model_fields_set == {"expected_generation"}
    assert cleared.model_fields_set == {"expected_generation", "description"}
    assert cleared.model_dump(exclude_unset=True) == {"expected_generation": 1, "description": None}
    assert renamed.name == "Renamed draft"


def test_update_accepts_contract_without_rule_prerequisites():
    request = TranslationProfileUpdateRequest(expected_generation=2, contract={"mappings": [{"id": "unfinished"}]})

    assert request.contract.mappings == [TranslationDraftMapping(id="unfinished")]


@pytest.mark.parametrize("model", [TranslationGenerationRequest, TranslationProfileUpdateRequest])
def test_generation_requests_require_expected_generation(model):
    with pytest.raises(ValidationError):
        model.model_validate({})


@pytest.mark.parametrize("model", [TranslationGenerationRequest, TranslationProfileUpdateRequest])
@pytest.mark.parametrize("generation", [0, -1, 1.5, None])
def test_generation_requests_reject_invalid_generations(model, generation):
    with pytest.raises(ValidationError):
        model(expected_generation=generation)


def test_stored_profile_and_lightweight_summary():
    stored = TranslationStoredProfile(
        id="profile-1",
        name="Draft",
        contract=TranslationContract(),
        created_at="2026-10-23T11:26:00Z",
        updated_at="2026-10-23T11:26:00Z",
    )

    assert stored.kind == "ifc_translation"
    assert stored.status == "draft"
    assert stored.generation == 1
    assert stored.description is None
    assert stored.revision_refs == []
    summary = TranslationProfileSummary.model_validate(
        stored.model_dump(exclude={"contract", "created_at", "revision_refs"})
    )
    assert set(summary.model_dump()) == {"id", "name", "description", "kind", "status", "generation", "updated_at"}
    assert TranslationStoredProfile.model_validate({**stored.model_dump(), "status": "archived"}).status == "archived"


@pytest.mark.parametrize("model", [TranslationStoredProfile, TranslationProfileSummary])
@pytest.mark.parametrize("overrides", [{"generation": 0}, {"generation": -1}, {"status": "active"}, {"kind": "legacy"}])
def test_profile_records_reject_invalid_generation_status_or_kind(model, overrides):
    payload = {"id": "profile-1", "name": "Draft", "updated_at": "now", **overrides}
    if model is TranslationStoredProfile:
        payload.update(contract={}, created_at="now")

    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (TranslationSource, {}),
        (TranslationOntologyPin, {"family": "fbo", "country": "dk"}),
        (TranslationDraftMapping, {"id": "mapping-1"}),
        (TranslationContract, {}),
        (TranslationProfileCreateRequest, {"kind": "ifc_translation", "name": "Draft"}),
        (TranslationProfileUpdateRequest, {"expected_generation": 1}),
        (TranslationGenerationRequest, {"expected_generation": 1}),
        (
            TranslationStoredProfile,
            {"id": "p", "name": "Draft", "contract": {}, "created_at": "now", "updated_at": "now"},
        ),
        (TranslationProfileSummary, {"id": "p", "name": "Draft", "updated_at": "now"}),
    ],
)
def test_all_models_forbid_unknown_fields(model, payload):
    with pytest.raises(ValidationError) as exc:
        model.model_validate({**payload, "misspelled_semantics": True})

    assert any(error["type"] == "extra_forbidden" for error in exc.value.errors())


@pytest.mark.parametrize("field", ["definition", "seed_provenance"])
@pytest.mark.parametrize("value", [{"not_json": object()}, ["not a dictionary"]])
def test_mapping_payloads_require_json_dictionaries(field, value):
    with pytest.raises(ValidationError):
        TranslationDraftMapping.model_validate({"id": "mapping-1", field: value})


def test_nested_unknown_fields_are_not_silently_discarded():
    with pytest.raises(ValidationError):
        TranslationContract.model_validate({"source": {"scheam": "IFC4"}})
    with pytest.raises(ValidationError):
        TranslationContract.model_validate({"mappings": [{"id": "mapping-1", "review_sate": "accepted"}]})


def test_contract_json_schema_can_be_generated():
    schema = TranslationContract.model_json_schema()

    assert schema["additionalProperties"] is False
    assert schema["$defs"]["TranslationSource"]["properties"]["schema"]["const"] == "IFC4"
