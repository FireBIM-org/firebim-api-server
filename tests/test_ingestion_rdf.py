from dataclasses import asdict

import pytest
from rdflib import RDF, XSD, Graph, Literal, Namespace, URIRef

from app.ingestion.execution import ProcessingError
from app.ingestion.neo_revision import evaluate_neo_revision
from app.schemas.translation_revision import TranslationOntologyArtifact
from tests import test_neo_revision
from tests.test_neo_ifcowl_adapter import EX, mapping, read_target, source_graph

published_revision = test_neo_revision.published_revision
PROV = Namespace("urn:firebim:ingestion:")


def semantic_revision(revision):
    import hashlib

    mappings = {
        "wall": mapping(match="subtypes"),
        "external": mapping(
            read_target(),
            match="subtypes",
            condition={
                "kind": "equals",
                "selector": {"kind": "attribute", "name": "Name", "datatype": str(XSD.string)},
                "values": ["Inherited"],
            },
        ),
        "length": mapping(
            read_target("Length", datatype="double", pset="Qto_WallBaseQuantities", kind="quantity", unit="ifc_length"),
            match="subtypes",
        ),
        "contains": mapping(
            {"kind": "object_property", "iri": EX + "contains", "object": "related", "cardinality": "many"},
            ifc_class="IfcRelContainedInSpatialStructure",
            subject="relating",
        ),
        "group": mapping(
            {
                "kind": "class",
                "iri": EX + "Compartment",
                "grouping": {
                    "kind": "property",
                    "pset": "CPset_FireBIM",
                    "name": "Compartment",
                    "datatype": str(XSD.string),
                },
            },
            ifc_class="IfcSpace",
        ),
    }
    ttl = """@prefix d: <https://example.org/destination/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
d:Wall a owl:Class . d:Compartment a owl:Class .
d:IsExternal a owl:DatatypeProperty ; rdfs:range xsd:boolean .
d:Length a owl:DatatypeProperty ; rdfs:range xsd:double .
d:contains a owl:ObjectProperty .
"""
    revision = revision.model_copy(deep=True)
    pin = revision.content.ontology_artifacts[0].pin.model_copy(
        update={
            "sha256": hashlib.sha256(ttl.encode()).hexdigest(),
        }
    )
    revision.content.ontology_artifacts = [TranslationOntologyArtifact(pin=pin, ttl=ttl)]
    revision.content.contract.destination_modules = [pin]
    revision.content.compiled_mappings = mappings
    revision.content_digest = revision.content.digest()
    revision.revision_id = "ifctr_" + revision.content_digest
    return revision


def test_semantic_fixture_serializes_expected_assertions_units_relationships_and_private_groups(published_revision):
    from app.ingestion.rdf import serialize_destination

    revision = semantic_revision(published_revision)
    _, evaluation = evaluate_neo_revision(revision, source_graph(), "m")
    serialized = serialize_destination(revision, asdict(evaluation), "m")
    graph = Graph().parse(data=serialized.destination, format="turtle")
    provenance = Graph().parse(data=serialized.provenance, format="turtle")
    wall = URIRef("urn:firebim:model:m:entity:0000000000000000000010")
    override = URIRef("urn:firebim:model:m:entity:0000000000000000000011")
    assert (wall, RDF.type, URIRef(EX + "Wall")) in graph
    assert (wall, URIRef(EX + "IsExternal"), Literal(True, datatype=XSD.boolean)) in graph
    assert float(graph.value(wall, URIRef(EX + "Length"))) == 1.2
    assert float(graph.value(override, URIRef(EX + "Length"))) == 1.5
    assert graph.value(wall, URIRef(EX + "Length")).datatype == XSD.double
    assert (URIRef("urn:firebim:model:m:entity:0000000000000000000006"), URIRef(EX + "contains"), wall) in graph
    group = next(graph.subjects(RDF.type, URIRef(EX + "Compartment")))
    assert set(provenance.objects(group, PROV.member)) == {
        URIRef("urn:firebim:model:m:entity:0000000000000000000007"),
        URIRef("urn:firebim:model:m:entity:0000000000000000000008"),
    }
    assert not list(graph.triples((group, PROV.member, None)))
    statement = next(
        s for s in provenance.subjects(RDF.subject, wall) if provenance.value(s, RDF.predicate) == URIRef(EX + "Length")
    )
    assert float(provenance.value(statement, PROV.sourceValue)) == 1200
    assert str(provenance.value(statement, PROV.unitSymbol)) == "mm"
    assert str(provenance.value(statement, PROV.dimension)) == "length"
    assert float(provenance.value(statement, PROV.scaleToSI)) == 0.001
    assert str(provenance.value(statement, PROV.canonicalUnit)) == "m"
    assert serialized.destination == serialize_destination(revision, asdict(evaluation), "m").destination


def test_rule_comparisons_use_physical_metres_not_source_millimetres(published_revision):
    from app.checks.evaluator_direct import DirectAtomicEvaluator
    from app.checks.result_models import DecisionNode, EvalContext, GraphRef
    from app.ingestion.rdf import serialize_destination

    revision = semantic_revision(published_revision)
    _, evaluation = evaluate_neo_revision(revision, source_graph(), "m")
    serialized = serialize_destination(revision, asdict(evaluation), "m")
    graph_ref = GraphRef(graph_id="qualified", graph_uri="urn:test:qualified", ttl=serialized.destination.decode())
    node = DecisionNode(
        node_id="physical-length", target_class=EX + "Wall", property_path=EX + "Length", operator="<=", value=1.3
    )
    outcomes = DirectAtomicEvaluator().evaluate_many(
        graph_ref=graph_ref,
        node=node,
        targets=[
            "urn:firebim:model:m:entity:0000000000000000000010",
            "urn:firebim:model:m:entity:0000000000000000000011",
        ],
        ctx=EvalContext(rule_id="length-rule", graph_id=graph_ref.graph_id, graph_uri=graph_ref.graph_uri),
    )
    assert [result.status for result in outcomes.values()] == ["True", "False"]


@pytest.mark.parametrize("corruption", ["datatype", "unit", "dimension", "iri", "foreign", "mapping", "single"])
def test_semantically_invalid_assertions_are_rejected(published_revision, corruption):
    from app.ingestion.rdf import serialize_destination

    revision = semantic_revision(published_revision)
    _, result = evaluate_neo_revision(revision, source_graph(), "m")
    evaluation = asdict(result)
    evaluation["assertions"] = list(evaluation["assertions"])
    length = next(a for a in evaluation["assertions"] if a["predicate"] == EX + "Length")
    if corruption == "datatype":
        length["object"] = "1200"
    elif corruption == "unit":
        length["unit"] = None
    elif corruption == "dimension":
        length["unit"]["dimension"] = "area"
    elif corruption == "iri":
        length["predicate"] = "https://example.org/destination/Length> <urn:evil>"
    elif corruption == "foreign":
        length["subject"] = "urn:firebim:model:other:entity:one"
    elif corruption == "mapping":
        length["predicate"] = EX + "NotInRetainedOntology"
    else:
        evaluation["assertions"].append({**length, "object": 2000.0})
    with pytest.raises(ProcessingError) as error:
        serialize_destination(revision, evaluation, "m")
    assert error.value.code == "semantic_rejection"


def test_retained_ontology_disjoint_domain_contradiction_is_rejected(published_revision):
    import hashlib

    from app.ingestion.rdf import serialize_destination

    revision = semantic_revision(published_revision)
    artifact = revision.content.ontology_artifacts[0]
    artifact.ttl += "\nd:Impossible a owl:Class ; owl:disjointWith d:Wall . d:Length rdfs:domain d:Impossible .\n"
    artifact.pin.sha256 = hashlib.sha256(artifact.ttl.encode()).hexdigest()
    revision.content_digest = revision.content.digest()
    revision.revision_id = "ifctr_" + revision.content_digest
    _, result = evaluate_neo_revision(revision, source_graph(), "m")
    with pytest.raises(ProcessingError, match="disjointness"):
        serialize_destination(revision, asdict(result), "m")


def test_square_millimetres_are_normalized_to_square_metres(published_revision):
    from app.ingestion.rdf import serialize_destination

    revision = semantic_revision(published_revision)
    revision.content.compiled_mappings["length"] = mapping(
        read_target(
            "Length",
            datatype="double",
            kind="quantity",
            pset="Qto_WallBaseQuantities",
            unit="ifc_area",
        ),
        match="subtypes",
    )
    revision.content_digest = revision.content.digest()
    revision.revision_id = "ifctr_" + revision.content_digest
    evaluation = {
        "diagnostics": [],
        "memberships": [],
        "assertions": [
            {
                "subject": "urn:firebim:model:m:entity:wall",
                "predicate": EX + "Length",
                "object": 2500000.0,
                "datatype": str(XSD.double),
                "unit": {"dimension": "area", "symbol": "mm2", "scale_to_si": 0.000001},
                "evidence": [
                    {
                        "mapping_id": "length",
                        "source_id": "wall",
                        "related_source_id": None,
                        "selector": "quantity:Qto_WallBaseQuantities.Length",
                    }
                ],
            }
        ],
    }
    serialized = serialize_destination(revision, evaluation, "m")
    graph = Graph().parse(data=serialized.destination, format="turtle")
    assert float(graph.value(URIRef("urn:firebim:model:m:entity:wall"), URIRef(EX + "Length"))) == 2.5
    provenance = Graph().parse(data=serialized.provenance, format="turtle")
    assert list(provenance.objects(None, PROV.canonicalUnit)) == [Literal("m2")]


@pytest.mark.parametrize("budget", ["triples", "file_bytes"])
def test_destination_and_provenance_resource_budgets_fail_closed(published_revision, monkeypatch, budget):
    from dataclasses import replace

    from app.ingestion import rdf

    revision = semantic_revision(published_revision)
    _, evaluation = evaluate_neo_revision(revision, source_graph(), "m")
    monkeypatch.setattr(rdf, "LIMITS", replace(rdf.LIMITS, **{budget: 1}))
    with pytest.raises(ProcessingError) as error:
        rdf.serialize_destination(revision, asdict(evaluation), "m")
    assert error.value.code == "destination_limit"
