from types import SimpleNamespace

import pytest
from rdflib import Graph

from app.ingestion.neo_ifcowl_adapter import NeoAdapterError
from app.ingestion.neo_revision import evaluate_neo_revision
from app.lexicon.translation_publication import prepare_translation_revision
from app.storage.translation_profile_repository import TranslationProfileRepository
from tests.test_neo_ifcowl_adapter import FIXTURES
from tests.test_translation_executable_mappings import FBO, Releases
from tests.test_translation_profile_repository import ConditionalS3Client
from tests.test_translation_revisions import create_draft


@pytest.fixture
def published_revision():
    repository = TranslationProfileRepository(bucket_name="test", s3_client=ConditionalS3Client())
    user = SimpleNamespace(id="alice")
    draft = create_draft(repository, user)
    committed = repository.commit_revision(prepare_translation_revision(draft, Releases()), user)
    return repository.get_revision(draft.id, committed.revision_id, user)


def test_published_snapshot_executes_against_neo_source_without_mutating_or_recompiling(published_revision):
    before = published_revision.model_dump_json()
    source = Graph().parse(FIXTURES / "semantic_ifcowl.ttl", format="turtle")
    adapted, result = evaluate_neo_revision(published_revision, source, "model")
    assert not result.diagnostics
    assert {(item.subject, item.object) for item in result.assertions} == {
        ("urn:firebim:model:model:entity:0000000000000000000010", FBO + "Wall"),
        ("urn:firebim:model:model:entity:0000000000000000000011", FBO + "Wall"),
        ("urn:firebim:model:model:entity:0000000000000000000012", FBO + "Wall"),
        ("urn:firebim:model:model:entity:0000000000000000000013", FBO + "Wall"),
        ("urn:firebim:model:model:entity:0000000000000000000014", FBO + "Wall"),
    }
    assert not adapted.evidence
    assert published_revision.model_dump_json() == before


@pytest.mark.parametrize(
    ("corruption", "code"),
    [
        ("snapshot", "invalid_revision_digest"),
        ("revision_id", "invalid_revision_digest"),
        ("catalogue", "unsupported_source_contract"),
        ("ontology", "invalid_ontology_digest"),
        ("pins", "invalid_ontology_retention"),
        ("mappings", "invalid_revision_contract"),
    ],
)
def test_incompatible_or_corrupted_revisions_fail_before_source_execution(published_revision, corruption, code):
    if corruption == "snapshot":
        published_revision.content.name = "tampered"
    elif corruption == "revision_id":
        published_revision.revision_id = "ifctr_wrong"
    else:
        if corruption == "catalogue":
            published_revision.content.source_schema_provenance["sha256"] = "different-catalogue"
        elif corruption == "ontology":
            published_revision.content.ontology_artifacts[0].ttl += "\n"
        elif corruption == "pins":
            published_revision.content.contract.destination_modules.clear()
        elif corruption == "mappings":
            published_revision.content.compiled_mappings.clear()
        published_revision.content_digest = published_revision.content.digest()
        published_revision.revision_id = "ifctr_" + published_revision.content_digest
    with pytest.raises(NeoAdapterError) as caught:
        evaluate_neo_revision(published_revision, Graph(), "model")
    assert caught.value.code == code
