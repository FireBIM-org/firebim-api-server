"""Generate FBO-DK v2 demo graphs used to exercise the rule runners.

The realistic graph is adapted from the original 25-room synthetic office.  The
second graph is intentionally small and isolates aggregate/ambiguity branches.
Neither graph represents a real building or a regulatory design reference.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "app" / "storage" / "demo_fixtures"

FBO = Namespace("https://firebim.org/ontologies/fbo#")
DK = Namespace("https://firebim.org/ontologies/fbo/dk#")
OFFICE = Namespace("https://firebim.org/test-data/dk/three-storey-office-v2/")
CASES = Namespace("https://firebim.org/test-data/dk/rules-v2-aggregate-cases/")

UNIT_FACTS = {
    DK.PeopleSelfRescueAbility: True,
    DK.CompartmentSleeping: False,
    DK.BuildingFireCompartmentDaytimeoccupancy: True,
    DK.NumberOfBuildingStoreys: 3,
    DK.HasAutomaticFireSprinklersystem: True,
    DK.HasAutomaticFireAlarmsystem: True,
    DK.HasAutomaticWarningsystem: True,
    DK.ComparableFireRiskConditions: True,
}


def _bind(graph: Graph, ex: Namespace) -> None:
    graph.bind("ex", ex)
    graph.bind("fbo", FBO)
    graph.bind("fbo-dk", DK)
    graph.bind("rdfs", RDFS)
    graph.bind("xsd", XSD)


def _replace(graph: Graph, subject: URIRef, predicate: URIRef, value: object) -> None:
    graph.remove((subject, predicate, None))
    graph.add((subject, predicate, Literal(value)))


def _add_unit_facts(
    graph: Graph,
    unit: URIRef,
    *,
    use_category: str | Iterable[str],
    area: Decimal,
    knows_routes: bool,
    ventilation: bool,
    storeys: int = 3,
    sprinklered: bool = True,
) -> None:
    graph.add((unit, RDF.type, DK.BuildingFunctionalFireUnit))
    categories = [use_category] if isinstance(use_category, str) else use_category
    for category in categories:
        graph.add((unit, DK.UseCategory, Literal(category)))
    for predicate, value in UNIT_FACTS.items():
        graph.add((unit, predicate, Literal(value)))
    _replace(graph, unit, DK.NumberOfBuildingStoreys, Literal(storeys, datatype=XSD.integer))
    _replace(graph, unit, DK.HasAutomaticFireSprinklersystem, sprinklered)
    graph.add((unit, DK.PeopleKnowledgeEscaperoutes, Literal(knows_routes)))
    graph.add((unit, DK.HasAutomaticFireVentilationsystem, Literal(ventilation)))
    graph.add((unit, DK.AreaBuildingFunctionalFireUnit, Literal(area, datatype=XSD.decimal)))


def _remove_duplicate_core_facts(graph: Graph) -> None:
    """Prefer national facts when both national and matching core facts exist."""

    ontology = Graph().parse(ROOT / "app" / "lexicon" / "resources" / "fbo-dk.ttl", format="turtle")
    mappings: set[tuple[URIRef, URIRef]] = set()
    for national, _, core in ontology.triples((None, RDFS.subPropertyOf, None)):
        if str(national).startswith(str(DK)) and str(core).startswith(str(FBO)):
            mappings.add((national, core))

    # New/proposed terms are not all present in the bundled older ontology yet.
    for national in set(graph.predicates()):
        if str(national).startswith(str(DK)):
            mappings.add((national, FBO[str(national).removeprefix(str(DK))]))
    mappings.add((DK.FireloadDensity, FBO.FireLoadDensity))

    # The graph specification explicitly keeps these two system relations in core.
    for subject in set(graph.subjects()):
        for local_name in ("ActiveFireProtection", "PassiveFireProtection"):
            if any(graph.objects(subject, FBO[local_name])):
                graph.remove((subject, DK[local_name], None))

    for national, core in mappings:
        for subject in set(graph.subjects(national, None)):
            graph.remove((subject, core, None))

    # A national class is enough when the graph currently states both equivalents.
    for subject, national_class in set(graph.subject_objects(RDF.type)):
        if str(national_class).startswith(str(DK)):
            graph.remove((subject, RDF.type, FBO[str(national_class).removeprefix(str(DK))]))


def generate_office() -> Graph:
    source = FIXTURE_DIR / "synthetic-three-storey-office-fbo-dk.ttl"
    graph = Graph().parse(source, format="turtle")
    _bind(graph, OFFICE)

    # Move the graph into a v2 namespace while retaining stable local identifiers.
    old_prefix = "https://firebim.org/test-data/dk/three-storey-office/"
    replacements: list[tuple[tuple[object, object, object], tuple[object, object, object]]] = []
    for subject, predicate, obj in graph:
        new_subject = OFFICE[str(subject).removeprefix(old_prefix)] if str(subject).startswith(old_prefix) else subject
        new_object = OFFICE[str(obj).removeprefix(old_prefix)] if str(obj).startswith(old_prefix) else obj
        if new_subject != subject or new_object != obj:
            replacements.append(((subject, predicate, obj), (new_subject, predicate, new_object)))
    for old, new in replacements:
        graph.remove(old)
        graph.add(new)

    building = OFFICE["office-building"]
    compartments = {subject for subject in graph.subjects(RDF.type, FBO.FireCompartment)} | {
        subject for subject in graph.subjects(RDF.type, DK.FireCompartment)
    }
    rooms = set(graph.subjects(DK.AreaFloorRoom, None))

    # Capture old containment before removing the obsolete property links.
    parents_by_room: dict[URIRef, set[URIRef]] = {room: set() for room in rooms}
    for room in rooms:
        for predicate in (FBO.FireSafetyUnit, DK.FireSafetyUnit):
            parents_by_room[room].update(parent for parent in graph.objects(room, predicate) if parent in compartments)
        parents_by_room[room].update(
            parent for parent in graph.subjects(FBO.hasSubitem, room) if parent in compartments
        )

    graph.remove((None, FBO.FireSafetyUnit, None))
    graph.remove((None, DK.FireSafetyUnit, None))

    # Rooms have exactly one compartment container in the rule-facing hierarchy.
    for room in rooms:
        graph.remove((None, FBO.hasSubitem, room))
        candidates = sorted(parents_by_room[room], key=str)
        if not candidates:
            raise ValueError(f"Room has no compartment in the source graph: {room}")
        graph.add((candidates[0], FBO.hasSubitem, room))
        graph.add((room, RDF.type, DK.Room))

    office_members = [OFFICE[name] for name in ("fc-f-east", "fc-f-west", "fc-g-west")]
    assembly_members = [OFFICE[name] for name in ("fc-g-east", "fc-s-canteen", "fc-s-office")]
    units = {
        OFFICE["bffu-office"]: office_members,
        OFFICE["bffu-assembly"]: assembly_members,
    }
    for compartment in compartments:
        graph.remove((building, FBO.hasSubitem, compartment))
    for unit, members in units.items():
        graph.add((building, FBO.hasSubitem, unit))
        for compartment in members:
            graph.add((unit, FBO.hasSubitem, compartment))

    _add_unit_facts(
        graph,
        OFFICE["bffu-office"],
        use_category="AK2",
        area=Decimal("477.0"),
        knows_routes=True,
        ventilation=False,
    )
    graph.add((OFFICE["bffu-office"], RDFS.label, Literal("Office section (declared AK2)", lang="en")))
    _add_unit_facts(
        graph,
        OFFICE["bffu-assembly"],
        use_category="AK3",
        area=Decimal("501.0"),
        knows_routes=False,
        ventilation=True,
    )
    graph.add((OFFICE["bffu-assembly"], RDFS.label, Literal("Assembly section (AK3)", lang="en")))

    # Structural/value hygiene on compartments.
    for compartment in compartments:
        graph.add((compartment, RDF.type, DK.FireCompartment))
        _replace(graph, compartment, DK.RiskClass, "RK2")
        _replace(graph, compartment, DK.NumberOfBuildingStoreys, Literal(1, datatype=XSD.integer))
        for predicate in (
            DK.HasAutomaticFireSprinklersystem,
            DK.HasAutomaticFireAlarmsystem,
            DK.HasAutomaticWarningsystem,
        ):
            _replace(graph, compartment, predicate, True)
        _replace(graph, compartment, DK.HasAutomaticFireVentilationsystem, False)

    for subject in set(graph.subjects(DK.NumberOccupantsSpaceCompartment, None)):
        values = list(graph.objects(subject, DK.NumberOccupantsSpaceCompartment))
        graph.remove((subject, DK.NumberOccupantsSpaceCompartment, None))
        for value in values:
            graph.add(
                (
                    subject,
                    DK.NumberOccupantsSpaceCompartment,
                    Literal(int(Decimal(str(value))), datatype=XSD.integer),
                )
            )

    for subject in set(graph.subjects(DK.AreaFireCompartment, None)):
        value = next(graph.objects(subject, DK.AreaFireCompartment))
        _replace(graph, subject, DK.AreaFireCompartment, Literal(Decimal(str(value)), datatype=XSD.decimal))

    _replace(graph, building, DK.RiskClass, "RK2")
    graph.add((building, DK.DistanceToPropertyLine, Literal(Decimal("8.0"), datatype=XSD.decimal)))
    graph.add((building, DK.DistanceToOtherBuildings, Literal(Decimal("14.0"), datatype=XSD.decimal)))

    # Doors and walls are counted by traversal rather than declared counts.
    for index, compartment in enumerate(sorted(compartments, key=str), start=1):
        local_name = str(compartment).rsplit("/", 1)[-1]
        exit_doors = [OFFICE[f"door-exit-{index:02d}-a"], OFFICE[f"door-exit-{index:02d}-b"]]
        internal_door = OFFICE[f"door-internal-{index:02d}"]
        for door in exit_doors:
            graph.add((door, RDF.type, DK.Door))
            graph.add((door, DK.IsFireExit, Literal(True)))
            graph.add((compartment, FBO.hasSubitem, door))
        graph.add((internal_door, RDF.type, DK.Door))
        graph.add((internal_door, DK.IsFireExit, Literal(False)))
        graph.add((compartment, FBO.hasSubitem, internal_door))

        walls = sorted(graph.objects(compartment, DK.SeparationFire), key=str)
        wall = walls[0] if walls else OFFICE[f"wall-{local_name}"]
        external = index in {1, 4, 6}
        graph.add((wall, RDF.type, DK.Wall))
        _replace(graph, wall, DK.IsExternal, external)
        _replace(graph, wall, DK.FireResistance, "EI60")
        _replace(graph, wall, DK.SurfaceCladdingRainscreen, external)
        graph.add((compartment, FBO.hasSubitem, wall))

    adjacency_specs = (
        ("adjacency-ground", "fc-g-west", "fc-g-east", "wall-g-west"),
        ("adjacency-first", "fc-f-west", "fc-f-east", "wall-f-west"),
        ("adjacency-second", "fc-s-office", "fc-s-canteen", "wall-s-office"),
    )
    for adjacency_name, from_name, to_name, wall_name in adjacency_specs:
        adjacency = OFFICE[adjacency_name]
        graph.add((adjacency, RDF.type, DK.Adjacency))
        graph.add((adjacency, DK.adjacencyFrom, OFFICE[from_name]))
        graph.add((adjacency, DK.adjacencyTo, OFFICE[to_name]))
        graph.add((adjacency, DK.adjacencyVia, OFFICE[wall_name]))

    # Dataset provenance and the deliberately inconsistent office classification.
    dataset = OFFICE["dataset"]
    graph.remove((dataset, None, None))
    graph.add((dataset, RDFS.label, Literal("Denmark: three-storey office v2 synthetic graph", lang="en")))
    graph.add(
        (dataset, FBO.hasDescription, Literal("Synthetic graph; it does not represent a real building.", lang="en"))
    )
    graph.add(
        (
            dataset,
            FBO.hasRemarks,
            Literal(
                "For software testing only. The office unit is deliberately declared AK2 "
                "although its facts derive AK1.",
                lang="en",
            ),
        )
    )

    # AreaBuildingFunctionalFireUnit must never be a link.
    for subject, value in list(graph.subject_objects(DK.AreaBuildingFunctionalFireUnit)):
        if not isinstance(value, Literal):
            graph.remove((subject, DK.AreaBuildingFunctionalFireUnit, value))

    _remove_duplicate_core_facts(graph)
    return graph


def _add_compartment(
    graph: Graph,
    unit: URIRef,
    compartment: URIRef,
    *,
    area: Decimal,
    storeys: int,
    sprinklered: bool,
    risk_class: str,
    use_categories: Iterable[str] = ("AK2",),
) -> None:
    graph.add((unit, FBO.hasSubitem, compartment))
    graph.add((compartment, RDF.type, DK.FireCompartment))
    graph.add((compartment, DK.AreaFireCompartment, Literal(area, datatype=XSD.decimal)))
    graph.add((compartment, DK.NumberOfBuildingStoreys, Literal(storeys, datatype=XSD.integer)))
    graph.add((compartment, DK.HasAutomaticFireSprinklersystem, Literal(sprinklered)))
    graph.add((compartment, DK.RiskClass, Literal(risk_class)))
    for category in use_categories:
        graph.add((compartment, DK.UseCategory, Literal(category)))


def generate_aggregate_cases() -> Graph:
    graph = Graph()
    _bind(graph, CASES)
    dataset = CASES["dataset"]
    building = CASES["building"]
    mixed_unit = CASES["unit-mixed-risk"]
    multivalue_unit = CASES["unit-multivalue-category"]

    graph.add((dataset, RDFS.label, Literal("Denmark: rules-v2 aggregate cases synthetic graph", lang="en")))
    graph.add(
        (dataset, FBO.hasDescription, Literal("Synthetic rule-runner branch coverage; not a real building.", lang="en"))
    )
    graph.add((building, RDF.type, FBO.Building))
    graph.add((building, FBO.hasSubitem, mixed_unit))
    graph.add((building, FBO.hasSubitem, multivalue_unit))

    _add_unit_facts(
        graph,
        mixed_unit,
        use_category="AK2",
        area=Decimal("4000.0"),
        knows_routes=True,
        ventilation=False,
        sprinklered=False,
    )
    _add_unit_facts(
        graph,
        multivalue_unit,
        use_category=("AK2", "AK3"),
        area=Decimal("450.0"),
        knows_routes=False,
        ventilation=False,
        storeys=1,
        sprinklered=False,
    )

    one_storey = CASES["compartment-one-storey-no-sprinkler"]
    multi_storey = CASES["compartment-multi-storey-no-sprinkler"]
    high_risk = CASES["compartment-high-risk"]
    multivalue_compartment = CASES["compartment-multivalue-category"]
    _add_compartment(
        graph,
        mixed_unit,
        one_storey,
        area=Decimal("1900.0"),
        storeys=1,
        sprinklered=False,
        risk_class="RK1",
    )
    _add_compartment(
        graph,
        mixed_unit,
        multi_storey,
        area=Decimal("1200.0"),
        storeys=2,
        sprinklered=False,
        risk_class="RK2",
    )
    _add_compartment(
        graph,
        mixed_unit,
        high_risk,
        area=Decimal("800.0"),
        storeys=1,
        sprinklered=True,
        risk_class="RK4",
    )
    _add_compartment(
        graph,
        multivalue_unit,
        multivalue_compartment,
        area=Decimal("450.0"),
        storeys=1,
        sprinklered=False,
        risk_class="RK2",
        use_categories=("AK2", "AK3"),
    )

    for index, compartment in enumerate((one_storey, multi_storey, high_risk, multivalue_compartment), start=1):
        room = CASES[f"room-{index}"]
        wall = CASES[f"wall-{index}"]
        door = CASES[f"door-{index}"]
        graph.add((compartment, FBO.hasSubitem, room))
        graph.add((compartment, FBO.hasSubitem, wall))
        graph.add((compartment, FBO.hasSubitem, door))
        graph.add((room, RDF.type, DK.Room))
        graph.add((room, DK.AreaFloorRoom, Literal(Decimal("100.0"), datatype=XSD.decimal)))
        graph.add((wall, RDF.type, DK.Wall))
        graph.add((wall, DK.IsExternal, Literal(index in {1, 4})))
        graph.add((wall, DK.FireResistance, Literal("EI60")))
        graph.add((door, RDF.type, DK.Door))
        graph.add((door, DK.IsFireExit, Literal(index != 3)))

    for index, (source, target, wall) in enumerate(
        (
            (one_storey, multi_storey, CASES["wall-1"]),
            (multi_storey, high_risk, CASES["wall-2"]),
            (high_risk, multivalue_compartment, CASES["wall-3"]),
        ),
        start=1,
    ):
        adjacency = CASES[f"adjacency-{index}"]
        graph.add((adjacency, RDF.type, DK.Adjacency))
        graph.add((adjacency, DK.adjacencyFrom, source))
        graph.add((adjacency, DK.adjacencyTo, target))
        graph.add((adjacency, DK.adjacencyVia, wall))

    return graph


def main() -> None:
    outputs = {
        FIXTURE_DIR / "synthetic-three-storey-office-fbo-dk-v2.ttl": generate_office(),
        FIXTURE_DIR / "synthetic-rules-v2-aggregate-cases-fbo-dk.ttl": generate_aggregate_cases(),
    }
    for path, graph in outputs.items():
        ttl = graph.serialize(format="turtle")
        path.write_text(f"{ttl.rstrip()}\n", encoding="utf-8")
        print(f"{path.name}: {len(graph)} triples")


if __name__ == "__main__":
    main()
