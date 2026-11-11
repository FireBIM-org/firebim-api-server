"""Explicit pilot decisions; never infer approval from a label or arbitrary prose."""

from rdflib import URIRef
from rdflib.namespace import OWL, RDF, RDFS

FBO = "https://firebim.org/ontologies/fbo#"
DK = "https://firebim.org/ontologies/fbo/dk#"
IFC = "https://firebim.org/ontologies/fbo/ifc#"

EXCLUDED_DECLARATIONS = {
    IFC + "EscapeRoute": "Route-identifying conditions are deferred until specified in FBO (D11).",
    IFC + "FireCompartment2a": (
        "The custom ElementType compartment mapping is excluded (D12); ObjectType is not substituted."
    ),
    IFC + "FireCompartment2c": "The ambiguous free-text compartment source is excluded (D14).",
    IFC + "FireResistance": "Fire-resistance semantics are deferred; no REI value is parsed.",
}

# Class/property delimiters are already part of the bounded module convention.
# Values remain exact literals; in particular, no general comma-splitting of values.
APPROVED_SOURCES = {
    "AreaFireCompartment1": (
        "IfcSpatialZone.FIRESAFETY",
        "Qto_SpatialZoneOtherBaseQuantities.NetFloorArea, CPset_FireBIM.CompartmentArea",
        "Area",
        (),
    ),
    "AreaFireCompartment2a": ("IfcSpace", "Qto_SpaceBaseQuantities.NetFloorArea", "Area", ()),
    "HeightBuilding": ("IfcBuilding", "CPset_FireBIM.HeightBuilding", "Length", ()),
    "WidthFreeDoors": ("IfcDoor", "Qto_DoorBaseQuantities.Width", "Length", ()),
    "OccupantsCompartment": (
        "IfcSpace, IfcSpatialZone.FIRESAFETY, IfcZone",
        "Pset_SpaceOccupancyRequirements.OccupancyNumber",
        "Integer",
        (),
    ),
    "TeachingZone": ("IfcSpace", "Pset_SpaceOccupancyRequirements.OccupancyType", "String", ("EDUCATION, TEACHING",)),
    "FireCompartment2b": ("IfcSpace", "CPset_FireBIM.Compartment", "String", ("<name of compartment>",)),
    "AutomaticFireExtinguishSystem": (
        "IfcSpace, IfcSpatialZone.FIRESAFETY, IfcZone",
        "CPset_FireBIM.AutomaticFireExtinguishSystem",
        "Boolean",
        (),
    ),
}
APPROVED_TARGETS = {
    "AreaFireCompartment1": {FBO + "AreaFireCompartment"},
    "AreaFireCompartment2a": {FBO + "AreaFireCompartment", DK + "AreaFireCompartment"},
    "HeightBuilding": {FBO + "HeightBuilding"},
    "WidthFreeDoors": {FBO + "WidthFreeDoors", DK + "WidthFreeDoors"},
    "OccupantsCompartment": {FBO + "NumberOccupants", DK + "NumberOccupantsSpaceCompartment"},
    "TeachingZone": {FBO + "TeachingZone"},
    "FireCompartment2b": {FBO + "FireCompartment"},
    "AutomaticFireExtinguishSystem": {FBO + "AutomaticFireExtinguishSystem"},
}


def approved_rule(module, declaration_iri, target_iri, source):
    if (module.pin.family, module.pin.country, module.pin.version) != ("fbo", "ifc", "2026.09.04"):
        return None
    for name, (classes, properties, datatype, values) in APPROVED_SOURCES.items():
        if declaration_iri != IFC + name or target_iri not in APPROVED_TARGETS[name]:
            continue

        def tokens(items):
            return sorted(part.strip() for item in items for part in item.split(","))

        if (
            tokens(source["class_predefined_types"]) == tokens([classes])
            and tokens(source["pset_properties"]) == tokens([properties])
            and source["data_types"] == [datatype]
            and source["values"] == list(values)
        ):
            return name
    return None


def final_targets(rule, target_iri, modules):
    """Resolve only approved final-module choices, never module-list order/local names."""
    if rule not in {"AreaFireCompartment1", "AreaFireCompartment2a", "WidthFreeDoors", "OccupantsCompartment"}:
        return []
    if not target_iri.startswith(FBO):
        return []
    base = URIRef(target_iri)
    candidates = set()
    for module in modules:
        if module.pin.family != "fbo" or module.pin.country in {"core", "ifc"}:
            continue
        for subject in module.graph.subjects(RDF.type, OWL.DatatypeProperty):
            if (subject, RDFS.subPropertyOf, base) in module.graph:
                candidates.add(str(subject))
            if (
                rule == "OccupantsCompartment"
                and (subject, URIRef(FBO + "hasIFCMapping"), URIRef(IFC + rule)) in module.graph
            ):
                candidates.add(str(subject))
    return sorted(candidates)
