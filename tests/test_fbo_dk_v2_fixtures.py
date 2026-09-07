from __future__ import annotations

import re
from pathlib import Path

from rdflib import RDF, XSD, Graph, Literal, Namespace, URIRef

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "app" / "storage" / "demo_fixtures"
FBO = Namespace("https://firebim.org/ontologies/fbo#")
DK = Namespace("https://firebim.org/ontologies/fbo/dk#")
OFFICE = Namespace("https://firebim.org/test-data/dk/three-storey-office-v2/")
CASES = Namespace("https://firebim.org/test-data/dk/rules-v2-aggregate-cases/")


def _load(name: str) -> Graph:
    return Graph().parse(FIXTURE_DIR / name, format="turtle")


def test_office_v2_has_one_downward_containment_hierarchy() -> None:
    graph = _load("synthetic-three-storey-office-fbo-dk-v2.ttl")
    units = set(graph.subjects(RDF.type, DK.BuildingFunctionalFireUnit))
    compartments = set(graph.subjects(RDF.type, DK.FireCompartment))
    rooms = set(graph.subjects(RDF.type, DK.Room))

    assert len(units) == 2
    assert len(compartments) == 6
    assert len(rooms) == 25
    assert not list(graph.triples((None, FBO.FireSafetyUnit, None)))
    assert not list(graph.triples((None, DK.FireSafetyUnit, None)))

    for unit in units:
        assert (OFFICE["office-building"], FBO.hasSubitem, unit) in graph
    for compartment in compartments:
        assert sum((unit, FBO.hasSubitem, compartment) in graph for unit in units) == 1
    for room in rooms:
        assert sum((compartment, FBO.hasSubitem, room) in graph for compartment in compartments) == 1


def test_office_v2_has_rule_target_classes_and_countable_elements() -> None:
    graph = _load("synthetic-three-storey-office-fbo-dk-v2.ttl")
    compartments = set(graph.subjects(RDF.type, DK.FireCompartment))
    doors = set(graph.subjects(RDF.type, DK.Door))
    walls = set(graph.subjects(RDF.type, DK.Wall))

    assert len(doors) == 18
    assert len(walls) == 6
    assert {value for value in graph.objects(None, DK.IsFireExit)} == {Literal(True), Literal(False)}
    assert {value for value in graph.objects(None, DK.IsExternal)} == {Literal(True), Literal(False)}

    for compartment in compartments:
        children = set(graph.objects(compartment, FBO.hasSubitem))
        assert children & doors
        assert children & walls


def test_office_v2_has_unit_facts_clean_values_and_deliberate_mismatch() -> None:
    graph = _load("synthetic-three-storey-office-fbo-dk-v2.ttl")
    units = set(graph.subjects(RDF.type, DK.BuildingFunctionalFireUnit))
    required = {
        DK.UseCategory,
        DK.PeopleSelfRescueAbility,
        DK.PeopleKnowledgeEscaperoutes,
        DK.CompartmentSleeping,
        DK.BuildingFireCompartmentDaytimeoccupancy,
        DK.NumberOfBuildingStoreys,
        DK.HasAutomaticFireSprinklersystem,
        DK.HasAutomaticFireAlarmsystem,
        DK.HasAutomaticWarningsystem,
        DK.HasAutomaticFireVentilationsystem,
        DK.ComparableFireRiskConditions,
        DK.AreaBuildingFunctionalFireUnit,
    }
    for unit in units:
        assert all(next(graph.objects(unit, predicate), None) is not None for predicate in required)
        areas = list(graph.objects(unit, DK.AreaBuildingFunctionalFireUnit))
        assert len(areas) == 1
        assert isinstance(areas[0], Literal)
        assert areas[0].datatype == XSD.decimal

    office = OFFICE["bffu-office"]
    assert (office, DK.UseCategory, Literal("AK2")) in graph
    assert (office, DK.PeopleKnowledgeEscaperoutes, Literal(True)) in graph

    for value in graph.objects(None, DK.RiskClass):
        assert re.fullmatch(r"RK[1-4]", str(value))
    for value in graph.objects(None, DK.NumberOccupantsSpaceCompartment):
        assert value.datatype == XSD.integer

    national_core_pairs = {
        DK.AreaFireCompartment: FBO.AreaFireCompartment,
        DK.AreaFloorRoom: FBO.AreaFloorRoom,
        DK.BuildingFireCompartmentDaytimeoccupancy: FBO.BuildingFireCompartmentDaytimeoccupancy,
        DK.DistanceActualTravel: FBO.DistanceActualTravel,
        DK.FireloadDensity: FBO.FireLoadDensity,
        DK.FireResistance: FBO.FireResistance,
        DK.HeightBuilding: FBO.HeightBuilding,
        DK.TimeWalking: FBO.TimeWalking,
    }
    for national, core in national_core_pairs.items():
        for subject in graph.subjects(national, None):
            assert not list(graph.objects(subject, core))

    for value in graph.objects(None, DK.AreaFireCompartment):
        assert value.datatype == XSD.decimal
    for wall in graph.subjects(RDF.type, DK.Wall):
        assert set(graph.objects(wall, DK.FireResistance)) == {Literal("EI60")}


def test_office_v2_has_relational_adjacency_via_contained_walls() -> None:
    graph = _load("synthetic-three-storey-office-fbo-dk-v2.ttl")
    adjacencies = set(graph.subjects(RDF.type, DK.Adjacency))
    walls = set(graph.subjects(RDF.type, DK.Wall))
    compartments = set(graph.subjects(RDF.type, DK.FireCompartment))

    assert len(adjacencies) == 3
    for adjacency in adjacencies:
        source = graph.value(adjacency, DK.adjacencyFrom)
        target = graph.value(adjacency, DK.adjacencyTo)
        via = graph.value(adjacency, DK.adjacencyVia)
        assert source in compartments
        assert target in compartments
        assert via in walls
        assert any((compartment, FBO.hasSubitem, via) in graph for compartment in compartments)


def test_aggregate_cases_cover_max_ambiguity_and_size_branches() -> None:
    graph = _load("synthetic-rules-v2-aggregate-cases-fbo-dk.ttl")
    mixed_unit = CASES["unit-mixed-risk"]
    multivalue_unit = CASES["unit-multivalue-category"]
    one_storey = CASES["compartment-one-storey-no-sprinkler"]
    multi_storey = CASES["compartment-multi-storey-no-sprinkler"]

    mixed_compartments = set(graph.objects(mixed_unit, FBO.hasSubitem))
    assert {str(graph.value(item, DK.RiskClass)) for item in mixed_compartments} == {
        "RK1",
        "RK2",
        "RK4",
    }
    assert set(graph.objects(multivalue_unit, DK.UseCategory)) == {Literal("AK2"), Literal("AK3")}

    assert (one_storey, DK.NumberOfBuildingStoreys, Literal(1, datatype=XSD.integer)) in graph
    assert (one_storey, DK.HasAutomaticFireSprinklersystem, Literal(False)) in graph
    assert (one_storey, DK.AreaFireCompartment, Literal("1900.0", datatype=XSD.decimal)) in graph
    assert (multi_storey, DK.NumberOfBuildingStoreys, Literal(2, datatype=XSD.integer)) in graph
    assert (multi_storey, DK.HasAutomaticFireSprinklersystem, Literal(False)) in graph
    assert (multi_storey, DK.AreaFireCompartment, Literal("1200.0", datatype=XSD.decimal)) in graph
    assert len(set(graph.subjects(RDF.type, DK.Adjacency))) == 3


def test_v2_graphs_use_only_fbo_family_vocabulary() -> None:
    allowed_predicate_namespaces = {
        str(RDF),
        "http://www.w3.org/2000/01/rdf-schema#",
        str(FBO),
        str(DK),
    }
    for name in (
        "synthetic-three-storey-office-fbo-dk-v2.ttl",
        "synthetic-rules-v2-aggregate-cases-fbo-dk.ttl",
    ):
        graph = _load(name)
        for predicate in graph.predicates():
            assert any(str(predicate).startswith(namespace) for namespace in allowed_predicate_namespaces)
        for value in graph.objects(None, DK.AreaBuildingFunctionalFireUnit):
            assert isinstance(value, Literal)
        for subject in graph.subjects(RDF.type, DK.BuildingFunctionalFireUnit):
            assert isinstance(subject, URIRef)
