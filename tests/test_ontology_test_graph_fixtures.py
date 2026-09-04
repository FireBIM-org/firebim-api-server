from __future__ import annotations

from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FBO = Namespace("https://ontology.firebim.be/ontology/fbo#")
FBO_DK = Namespace("https://ontology.firebim.be/ontology/fbo-DK#")
REALISTIC_EX = Namespace("https://firebim.org/test-data/three-storey-office/")
EDGE_EX = Namespace("https://firebim.org/test-data/unrealistic-edge-case/")

REALISTIC_FIXTURE = "building_three_storey_office_fbo.ttl"
EDGE_FIXTURE = "building_unrealistic_edge_case_fbo.ttl"


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
