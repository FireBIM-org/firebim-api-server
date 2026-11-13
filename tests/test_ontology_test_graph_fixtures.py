from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FBO = Namespace("https://ontology.firebim.be/ontology/fbo#")
FBO_DK = Namespace("https://ontology.firebim.be/ontology/fbo-DK#")
REALISTIC_EX = Namespace("https://firebim.org/test-data/three-storey-office/")
EDGE_EX = Namespace("https://firebim.org/test-data/unrealistic-edge-case/")

REALISTIC_FIXTURE = "building_three_storey_office_fbo.ttl"
EDGE_FIXTURE = "building_unrealistic_edge_case_fbo.ttl"

PUBLIC_FBO = Namespace("https://firebim.org/ontologies/fbo#")
COUNTRIES = {
    "be": ("Belgium", 63),
    "dk": ("Denmark", 107),
    "lt": ("Lithuania", 62),
    "nl": ("Netherlands", 109),
    "pt": ("Portugal", 137),
}
SYNTHETIC_REALISTIC_FIXTURE = "synthetic-three-storey-office-fbo-{country}.ttl"
SYNTHETIC_EDGE_FIXTURE = "synthetic-unrealistic-edge-case-fbo-{country}.ttl"


def _parse(name: str) -> Graph:
    graph = Graph()
    graph.parse(FIXTURE_DIR / name, format="turtle")
    return graph


def _subjects_with(graph: Graph, predicate: URIRef) -> set[URIRef]:
    return {subject for subject in graph.subjects(predicate=predicate) if isinstance(subject, URIRef)}


def test_ontology_test_graph_fixtures_parse_and_only_assert_fbo_terms():
    allowed_standard_predicates = {RDF.type, RDFS.label, RDFS.comment}
    allowed_ontology_prefixes = (str(FBO), str(FBO_DK))

    for fixture_name in (REALISTIC_FIXTURE, EDGE_FIXTURE):
        graph = _parse(fixture_name)
        assert graph

        for subject, predicate, obj in graph:
            predicate_value = str(predicate)
            assert predicate in allowed_standard_predicates or predicate_value.startswith(allowed_ontology_prefixes)

            if predicate == RDF.type:
                assert isinstance(obj, URIRef)
                assert str(obj).startswith(allowed_ontology_prefixes)

            for term in (subject, predicate, obj):
                if isinstance(term, URIRef):
                    assert "fbo-ifc" not in str(term).lower()
                    assert "/ifc" not in str(term).lower()


def test_realistic_office_has_exact_requested_storeys_and_spaces():
    graph = _parse(REALISTIC_FIXTURE)

    storeys = set(graph.subjects(RDF.type, FBO.BuildingStorey))
    rooms = _subjects_with(graph, FBO_DK.AreaFloorRoom)

    assert len(set(graph.subjects(RDF.type, FBO.Building))) == 1
    assert storeys == {REALISTIC_EX["ground-storey"], REALISTIC_EX["first-storey"], REALISTIC_EX["second-storey"]}
    assert len(rooms) == 25

    expected_room_counts = {
        REALISTIC_EX["ground-storey"]: 8,
        REALISTIC_EX["first-storey"]: 8,
        REALISTIC_EX["second-storey"]: 9,
    }
    for storey, expected_count in expected_room_counts.items():
        storey_rooms = {room for room in graph.objects(storey, FBO.hasSubitem) if room in rooms}
        assert len(storey_rooms) == expected_count

    areas = {float(value) for room in rooms for value in graph.objects(room, FBO_DK.AreaFloorRoom)}
    assert len(areas) >= 15
    assert min(areas) > 0


def test_realistic_office_includes_required_room_functions_and_fire_safety_coverage():
    graph = _parse(REALISTIC_FIXTURE)
    rooms = _subjects_with(graph, FBO_DK.AreaFloorRoom)

    assert len(rooms & _subjects_with(graph, FBO_DK.RoomfunctionEscaperouteCorridor)) == 3
    assert len(rooms & _subjects_with(graph, FBO_DK.RoomfunctionEscapestaircase)) == 3
    assert len(rooms & _subjects_with(graph, FBO_DK.RoomfunctionCopyroom)) == 3
    assert len(set(graph.subjects(FBO_DK.Roomfunction, REALISTIC_EX["function-toilet"]))) == 3
    assert len(_subjects_with(graph, FBO_DK.RoomfunctionCanteen)) == 1
    assert len(_subjects_with(graph, FBO_DK.RoomfunctionKitchen)) == 1

    assert len(set(graph.subjects(RDF.type, FBO_DK.FireCompartment))) == 6
    assert len(set(graph.subjects(RDF.type, FBO_DK.BoundaryFireCompartment))) == 6
    assert len(set(graph.subjects(RDF.type, FBO_DK.DoorExit))) == 1
    assert len(set(graph.subjects(RDF.type, FBO_DK.AutomaticFireAlarmsystem))) == 1
    assert len(set(graph.subjects(RDF.type, FBO_DK.AutomaticFireSprinklersystem))) == 1


def test_unrealistic_graph_has_one_of_each_supported_building_object():
    graph = _parse(EDGE_FIXTURE)

    expected_singleton_classes = {
        FBO.Building,
        FBO.BuildingStorey,
        FBO.BuildingLayers,
        FBO.TravelDistance,
        FBO_DK.Site,
        FBO_DK.BuildingLayers,
        FBO_DK.FireCompartment,
        FBO_DK.BoundaryFireCompartment,
        FBO_DK.DoorExit,
        FBO_DK.DoorEscaperoute,
        FBO_DK.Exit,
        FBO_DK.AutomaticFireAlarmsystem,
        FBO_DK.AutomaticFireSprinklersystem,
        FBO_DK.AutomaticFireVentilationsystem,
        FBO_DK.AutomaticWarningsystem,
        FBO_DK.FireSafetyInstallationSmokeAlarmsystem,
        FBO_DK.HoseReel,
        FBO_DK.CeilingSuspended,
        FBO_DK.SurfaceCladding,
        FBO_DK.SurfaceCladdingRainscreen,
        FBO_DK.SurfaceExposed,
    }
    for class_uri in expected_singleton_classes:
        assert len(set(graph.subjects(RDF.type, class_uri))) == 1, class_uri

    assert (EDGE_EX.room, FBO_DK.AreaFloorRoom, None) in graph
    assert len(set(graph.subjects(RDF.type, FBO_DK.FireCompartment))) == 1
    assert len(set(graph.subjects(RDF.type, FBO_DK.BoundaryFireCompartment))) == 1


def test_unrealistic_room_covers_every_declared_room_function_variant():
    graph = _parse(EDGE_FIXTURE)
    room_predicates = set(graph.predicates(EDGE_EX.room))
    expected_room_function_predicates = {
        FBO_DK.Roomfunction,
        FBO_DK.RoomfunctionAssemblyrooms,
        FBO_DK.RoomfunctionCanteen,
        FBO_DK.RoomfunctionClassrooms,
        FBO_DK.RoomfunctionCopyroom,
        FBO_DK.RoomfunctionDormitory,
        FBO_DK.RoomfunctionEscaperouteCorridor,
        FBO_DK.RoomfunctionEscapestaircase,
        FBO_DK.RoomfunctionKitchen,
        FBO_DK.RoomfunctionKitchenette,
        FBO_DK.RoomfunctionOfficespace,
        FBO_DK.RoomfunctionProductionskitchen,
        FBO_DK.RoomfunctionResidentialunit,
        FBO_DK.RoomfunctionSafetyStaircase,
        FBO_DK.RoomfunctionSchoolkitchen,
        FBO_DK.RoomfunctionShop,
        FBO_DK.RoomfunctionStaffroom,
        FBO_DK.RoomfunctionStorage,
        FBO_DK.RoomfunctionStorageroom,
    }
    assert expected_room_function_predicates <= room_predicates

    numeric_literals = [
        value
        for _, _, value in graph
        if isinstance(value, Literal) and value.datatype and str(value.datatype).endswith(("#float", "#decimal"))
    ]
    assert any(float(value) < 0 for value in numeric_literals)


@pytest.mark.parametrize(
    ("country", "country_name", "_"),
    [(country, country_name, expected_terms) for country, (country_name, expected_terms) in COUNTRIES.items()],
)
def test_country_realistic_graphs_have_the_same_requested_topology(
    country: str,
    country_name: str,
    _: int,
):
    graph = _parse(SYNTHETIC_REALISTIC_FIXTURE.format(country=country))
    example = Namespace(f"https://firebim.org/test-data/{country}/three-storey-office/")
    country_namespace = f"https://firebim.org/ontologies/fbo/{country}#"

    rooms = _subjects_with(graph, PUBLIC_FBO.AreaFloorRoom)
    storeys = set(graph.subjects(RDF.type, PUBLIC_FBO.BuildingStorey))

    assert len(set(graph.subjects(RDF.type, PUBLIC_FBO.Building))) == 1
    assert storeys == {example["ground-storey"], example["first-storey"], example["second-storey"]}
    assert len(rooms) == 25
    assert len(_subjects_with(graph, PUBLIC_FBO.Roomfunction)) == 25

    expected_room_counts = {
        example["ground-storey"]: 8,
        example["first-storey"]: 8,
        example["second-storey"]: 9,
    }
    for storey, expected_count in expected_room_counts.items():
        assert len(set(graph.objects(storey, PUBLIC_FBO.hasSubitem)) & rooms) == expected_count

    descriptions = {str(value) for value in graph.objects(example.dataset, PUBLIC_FBO.hasDescription)}
    assert any(country_name in value and "does not represent a real building" in value for value in descriptions)
    assert any(
        str(predicate).startswith(country_namespace)
        or (predicate == RDF.type and isinstance(obj, URIRef) and str(obj).startswith(country_namespace))
        for _, predicate, obj in graph
    )


@pytest.mark.parametrize(
    ("country", "country_name", "expected_national_terms"),
    [(country, country_name, expected_terms) for country, (country_name, expected_terms) in COUNTRIES.items()],
)
def test_country_edge_graphs_cover_each_national_term_and_remain_single_object_examples(
    country: str,
    country_name: str,
    expected_national_terms: int,
):
    graph = _parse(SYNTHETIC_EDGE_FIXTURE.format(country=country))
    example = Namespace(f"https://firebim.org/test-data/{country}/unrealistic-edge-case/")
    country_namespace = f"https://firebim.org/ontologies/fbo/{country}#"

    assert len(set(graph.subjects(RDF.type, PUBLIC_FBO.Building))) == 1
    assert len(set(graph.subjects(RDF.type, PUBLIC_FBO.BuildingStorey))) == 1
    for object_name in ("room", "wall", "door", "exit"):
        assert (example.storey, PUBLIC_FBO.hasSubitem, example[object_name]) in graph
    assert (example.compartment, PUBLIC_FBO.hasSubitem, example.room) in graph

    national_terms = {predicate for _, predicate, _ in graph if str(predicate).startswith(country_namespace)}
    national_terms.update(
        obj
        for _, predicate, obj in graph
        if predicate == RDF.type and isinstance(obj, URIRef) and str(obj).startswith(country_namespace)
    )
    assert len(national_terms) == expected_national_terms

    descriptions = {str(value) for value in graph.objects(example.dataset, PUBLIC_FBO.hasDescription)}
    assert any(country_name in value and "does not represent a real building" in value for value in descriptions)

    for subject, predicate, obj in graph:
        for term in (subject, predicate, obj):
            if isinstance(term, URIRef):
                assert "ontology.firebim.be" not in str(term)
                assert "fbo-ifc" not in str(term).lower()
                assert "/ifc" not in str(term).lower()
