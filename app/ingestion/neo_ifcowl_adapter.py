"""Bounded adapter for the pinned Neo full-ifcOWL IFC4 source, not arbitrary RDF."""

import math
import re
from dataclasses import dataclass, field

from rdflib import OWL, RDF, Graph, Literal, Namespace, URIRef

from app.lexicon.ifc4_schema import attribute_datatype, is_entity, is_subtype
from app.schemas.ifc_mapping import ExecutableMapping, source_selectors

IFC = Namespace("https://standards.buildingsmart.org/IFC/DEV/IFC4/ADD2/OWL#")
EXPRESS = Namespace("https://w3id.org/express#")
ADAPTER_VERSION = "neo-ifcowl-record/1"


class NeoAdapterError(ValueError):
    def __init__(self, code: str, message: str, source_iri=None):
        self.code = code
        self.source_iri = str(source_iri) if source_iri is not None else None
        super().__init__(message)


@dataclass(frozen=True)
class SourceIdentity:
    iri: str
    step_id: int
    guid: str | None


@dataclass(frozen=True)
class SourceValueEvidence:
    source_id: str
    container: str
    pset: str
    name: str
    pset_iri: str
    value_iri: str
    relationship_iri: str
    type_iri: str | None


@dataclass
class NativeSource:
    records: list[dict]
    identities: dict[str, SourceIdentity]
    model_units: dict = field(default_factory=dict)
    evidence: tuple[SourceValueEvidence, ...] = ()


def _one(graph: Graph, subject, predicate, *, required=False):
    values = list(graph.objects(subject, predicate))
    if len(values) > 1 or required and not values:
        raise NeoAdapterError("invalid_source_cardinality", f"Expected one {predicate}", subject)
    return values[0] if values else None


def _string(graph: Graph, node):
    if node is None:
        raise NeoAdapterError("missing_source_name", "Expected a named source value")
    value = _one(graph, node, EXPRESS.hasString, required=True)
    if not isinstance(value, Literal) or not isinstance(value.toPython(), str):
        raise NeoAdapterError("invalid_source_literal", "Expected an EXPRESS string", node)
    return str(value)


def _scalar(graph: Graph, node):
    if node is None:
        return None
    values = [
        (predicate, value)
        for predicate, value in graph.predicate_objects(node)
        if str(predicate).startswith(str(EXPRESS))
    ]
    types = {EXPRESS.hasString: str, EXPRESS.hasBoolean: bool, EXPRESS.hasInteger: int, EXPRESS.hasDouble: float}
    if len(values) != 1 or values[0][0] not in types:
        raise NeoAdapterError("unsupported_source_value", "Expected a supported EXPRESS scalar", node)
    predicate, literal = values[0]
    value = literal.toPython() if isinstance(literal, Literal) else None
    if type(value) is not types[predicate] or isinstance(value, float) and not math.isfinite(value):
        raise NeoAdapterError("invalid_source_literal", "Malformed typed scalar", node)
    return None if isinstance(value, str) and not value.strip() else value


def _unit(graph: Graph, node, dimension: str):
    if (node, RDF.type, IFC.IfcSIUnit) not in graph:
        raise NeoAdapterError("unsupported_source_unit", "Only explicit SI length/area units are supported", node)
    expected = (IFC.LENGTHUNIT, IFC.METRE) if dimension == "length" else (IFC.AREAUNIT, IFC.SQUARE_METRE)
    if (_one(graph, node, IFC.unitType_IfcNamedUnit), _one(graph, node, IFC.name_IfcSIUnit)) != expected:
        raise NeoAdapterError("invalid_source_unit", "Unit dimension or name does not match the value", node)
    prefix = _one(graph, node, IFC.prefix_IfcSIUnit)
    prefixes = {
        None: ("", 1),
        IFC.MILLI: ("m", 0.001),
        IFC.CENTI: ("c", 0.01),
        IFC.DECI: ("d", 0.1),
        IFC.KILO: ("k", 1000),
        IFC.MICRO: ("u", 0.000001),
    }
    if prefix not in prefixes:
        raise NeoAdapterError("unsupported_source_unit", "Unsupported SI prefix", node)
    symbol, scale = prefixes[prefix]
    return {
        "symbol": symbol + ("m" if dimension == "length" else "m2"),
        "dimension": dimension,
        "scale_to_si": scale if dimension == "length" else scale**2,
    }


def _model_units(graph: Graph):
    projects = list(graph.subjects(RDF.type, IFC.IfcProject))
    if len(projects) != 1:
        raise NeoAdapterError("invalid_source_project", "Exactly one IFC project is required")
    assignment = _one(graph, projects[0], IFC.unitsInContext_IfcContext)
    if assignment is None:
        return {}
    if (assignment, RDF.type, IFC.IfcUnitAssignment) not in graph:
        raise NeoAdapterError("invalid_source_unit", "Missing unit assignment", assignment)
    units = {}
    for node in graph.objects(assignment, IFC.units_IfcUnitAssignment):
        kind = _one(graph, node, IFC.unitType_IfcNamedUnit, required=True)
        dimension = {IFC.LENGTHUNIT: "length", IFC.AREAUNIT: "area"}.get(kind)
        if dimension is not None:
            if dimension in units:
                raise NeoAdapterError("ambiguous_source_unit", "Repeated model unit dimension", assignment)
            units[dimension] = _unit(graph, node, dimension)
    return units


def _property_value(graph: Graph, prop):
    node = _one(graph, prop, IFC.nominalValue_IfcPropertySingleValue)
    value = _scalar(graph, node)
    unit = _one(graph, prop, IFC.unit_IfcPropertySingleValue)
    if unit is not None and value is not None:
        classes = set(graph.objects(node, RDF.type)) if node is not None else set()
        if classes <= {IFC.IfcLengthMeasure, IFC.IfcPositiveLengthMeasure, IFC.IfcNonNegativeLengthMeasure} and classes:
            dimension = "length"
        elif classes == {IFC.IfcAreaMeasure}:
            dimension = "area"
        else:
            raise NeoAdapterError("unsupported_source_unit", "Explicit units require a length/area measure", prop)
        value = {"value": value, "unit": _unit(graph, unit, dimension)}
    return value


def mapping_reads(mapping: ExecutableMapping) -> list:
    """The same selected source reads drive runtime eligibility and graph adaptation."""
    reads = []
    if mapping.target.kind == "datatype_property" and mapping.target.value.kind == "read":
        reads.extend([mapping.target.value.selector, *mapping.target.value.fallback])
    if mapping.target.kind == "class" and mapping.target.grouping is not None:
        reads.append(mapping.target.grouping)
    pending = [mapping.condition] if mapping.condition is not None else []
    while pending:
        condition = pending.pop()
        if condition.kind in {"all_of", "any_of"}:
            pending.extend(condition.conditions)
        else:
            reads.append(condition.selector)
    return reads


def _mapping_requirements(mappings: dict[str, ExecutableMapping], records: dict):
    requirements = {subject: set() for subject in records}
    for mapping in mappings.values():
        reads = mapping_reads(mapping)
        for subject, record in records.items():
            if any(
                (
                    record["ifc_class"] == selector.ifc_class
                    if selector.match == "exact"
                    else is_subtype(record["ifc_class"], selector.ifc_class)
                )
                and (selector.predefined_type is None or record.get("predefined_type") == selector.predefined_type)
                for selector in source_selectors(mapping.source)
            ):
                requirements[subject].update((read.kind, read.pset, read.name) for read in reads)
    return requirements


def _properties(graph: Graph, entities: dict, records: dict, requirements=None):
    evidence = []

    def attach(owner, pset, *, origin, relationship, source_type=None):
        if requirements is not None and not any(
            kind in {"property", "quantity"} for kind, _, _ in requirements.get(owner, ())
        ):
            return
        if owner not in records or pset not in records:
            raise NeoAdapterError("dangling_source_reference", "Missing property owner or set", relationship)
        pset_name = records[pset]["attributes"].get("Name")
        if not pset_name:
            raise NeoAdapterError("missing_source_name", "Property sets require a name", pset)
        quantity = entities[pset] == "IfcElementQuantity"
        if not quantity and entities[pset] != "IfcPropertySet":
            raise NeoAdapterError("unsupported_property_set", "Expected a property or quantity set", pset)
        kind = "quantity" if quantity else "property"
        if requirements is not None and not any(
            wanted_kind == kind and wanted_pset == pset_name for wanted_kind, wanted_pset, _ in requirements[owner]
        ):
            return
        container = ("type_" if origin == "type" else "") + ("quantities" if quantity else "properties")
        destination = records[owner].setdefault(container, {}).setdefault(pset_name, {})
        predicate = IFC.quantities_IfcElementQuantity if quantity else IFC.hasProperties_IfcPropertySet
        for prop in sorted(graph.objects(pset, predicate), key=str):
            name_predicate = IFC.name_IfcPhysicalQuantity if quantity else IFC.name_IfcProperty
            name = _string(graph, _one(graph, prop, name_predicate, required=True))
            if requirements is not None and (kind, pset_name, name) not in requirements[owner]:
                continue
            if quantity:
                if entities.get(prop) not in {"IfcQuantityLength", "IfcQuantityArea"}:
                    raise NeoAdapterError(
                        "unsupported_quantity_form",
                        "Only length/area quantities are supported",
                        prop,
                    )
                dimension = "length" if entities[prop] == "IfcQuantityLength" else "area"
                value = _scalar(graph, _one(graph, prop, IFC[f"{dimension}Value_{entities[prop]}"], required=True))
                unit = _one(graph, prop, IFC.unit_IfcPhysicalSimpleQuantity)
                if unit is not None:
                    value = {"value": value, "unit": _unit(graph, unit, dimension)}
            else:
                if entities.get(prop) != "IfcPropertySingleValue":
                    raise NeoAdapterError(
                        "unsupported_property_form",
                        "Only single-value properties are supported",
                        prop,
                    )
                value = _property_value(graph, prop)
            if name in destination:
                previous = destination[name]
                values = (previous if isinstance(previous, list) else [previous]) + [value]
                if None in values and any(item is not None for item in values):
                    raise NeoAdapterError(
                        "conflicting_source_null",
                        "Null and non-null values have the same owned key",
                        prop,
                    )
                destination[name] = values
            else:
                destination[name] = value
            evidence.append(
                SourceValueEvidence(
                    records[owner]["id"],
                    container,
                    pset_name,
                    name,
                    str(pset),
                    str(prop),
                    str(relationship),
                    str(source_type) if source_type is not None else None,
                )
            )

    type_owners = set()
    for relationship, ifc_class in sorted(entities.items(), key=lambda item: str(item[0])):
        if ifc_class == "IfcRelDefinesByProperties":
            pset = _one(graph, relationship, IFC.relatingPropertyDefinition_IfcRelDefinesByProperties, required=True)
            for owner in sorted(graph.objects(relationship, IFC.relatedObjects_IfcRelDefinesByProperties), key=str):
                attach(owner, pset, origin="occurrence", relationship=relationship)
        elif ifc_class == "IfcRelDefinesByType":
            source_type = _one(graph, relationship, IFC.relatingType_IfcRelDefinesByType, required=True)
            if source_type not in entities or not is_subtype(entities[source_type], "IfcTypeObject"):
                raise NeoAdapterError("invalid_source_type", "Expected an IFC type object", relationship)
            for owner in sorted(graph.objects(relationship, IFC.relatedObjects_IfcRelDefinesByType), key=str):
                if owner in type_owners:
                    raise NeoAdapterError("ambiguous_source_type", "Multiple type assignments", owner)
                type_owners.add(owner)
                for pset in sorted(graph.objects(source_type, IFC.hasPropertySets_IfcTypeObject), key=str):
                    attach(owner, pset, origin="type", relationship=relationship, source_type=source_type)
    return tuple(
        sorted(
            evidence,
            key=lambda item: (
                item.source_id,
                item.container,
                item.pset_iri,
                item.value_iri,
                item.relationship_iri,
            ),
        )
    )


def _relationships(graph: Graph, entities: dict, records: dict):
    predicates = {
        "IfcRelAggregates": (IFC.relatingObject_IfcRelAggregates, IFC.relatedObjects_IfcRelAggregates),
        "IfcRelContainedInSpatialStructure": (
            IFC.relatingStructure_IfcRelContainedInSpatialStructure,
            IFC.relatedElements_IfcRelContainedInSpatialStructure,
        ),
    }
    for subject, ifc_class in entities.items():
        if ifc_class not in predicates:
            continue
        relating_predicate, related_predicate = predicates[ifc_class]
        relating = _one(graph, subject, relating_predicate, required=True)
        related = sorted(graph.objects(subject, related_predicate), key=str)
        if not related or any(endpoint not in records for endpoint in [relating, *related]):
            raise NeoAdapterError("dangling_relationship_endpoint", "Missing explicit relationship endpoint", subject)
        records[subject]["relating"] = records[relating]["id"]
        records[subject]["related"] = sorted(records[endpoint]["id"] for endpoint in related)


def adapt_ifcowl(graph: Graph, *, mappings: dict[str, ExecutableMapping] | None = None) -> NativeSource:
    """Adapt a separately retained full-ifcOWL graph; never fetch owl:imports."""
    imports = set(graph.objects(None, OWL.imports))
    if imports != {URIRef(str(IFC))}:
        raise NeoAdapterError("unsupported_source_schema", "Expected Neo full-ifcOWL IFC4 output")
    entities = {}
    for subject, class_iri in graph.subject_objects(RDF.type):
        if str(class_iri).startswith(str(IFC)) and is_entity(str(class_iri)[len(str(IFC)) :]):
            if subject in entities:
                raise NeoAdapterError("ambiguous_source_class", "Multiple exact IFC classes", subject)
            entities[subject] = str(class_iri)[len(str(IFC)) :]
    if not entities:
        raise NeoAdapterError("empty_source", "Full-ifcOWL output contains no IFC entities")
    records = []
    identities = {}
    step_ids = set()
    for subject, ifc_class in sorted(entities.items(), key=lambda item: str(item[0])):
        match = re.fullmatch(rf".+/{ifc_class}_([1-9][0-9]*)", str(subject))
        if not isinstance(subject, URIRef) or not match:
            raise NeoAdapterError("invalid_source_identity", "Expected a Neo entity IRI", subject)
        step_id = int(match[1])
        guid_node = _one(graph, subject, IFC.globalId_IfcRoot)
        guid = _string(graph, guid_node) if guid_node is not None else None
        if is_subtype(ifc_class, "IfcRoot") and (guid is None or not re.fullmatch(r"[0-3][0-9A-Za-z_$]{21}", guid)):
            raise NeoAdapterError("invalid_source_guid", "IFC roots require a valid GUID", subject)
        identifier = guid if guid is not None else f"step:{step_id}"
        if identifier in identities or step_id in step_ids:
            raise NeoAdapterError("duplicate_source_identity", "Repeated GUID or STEP ID", subject)
        step_ids.add(step_id)
        identities[identifier] = SourceIdentity(str(subject), step_id, guid)
        attributes = {}
        for predicate, node in graph.predicate_objects(subject):
            if not str(predicate).startswith(str(IFC)):
                continue
            local = str(predicate)[len(str(IFC)) :]
            if "_Ifc" not in local:
                continue
            raw_name, declaring = local.split("_", 1)
            name = raw_name[0].upper() + raw_name[1:]
            measurement = any(
                (node, RDF.type, kind) in graph
                for kind in (
                    IFC.IfcLengthMeasure,
                    IFC.IfcPositiveLengthMeasure,
                    IFC.IfcNonNegativeLengthMeasure,
                    IFC.IfcAreaMeasure,
                )
            )
            if is_subtype(ifc_class, declaring) and (attribute_datatype(ifc_class, name) or measurement):
                if name in attributes:
                    raise NeoAdapterError("invalid_source_cardinality", "Repeated scalar attribute", subject)
                attributes[name] = _scalar(graph, node)
        predefined = [
            value
            for predicate, value in graph.predicate_objects(subject)
            if str(predicate).startswith(str(IFC) + "predefinedType_")
        ]
        if len(predefined) > 1 or any(not str(value).startswith(str(IFC)) for value in predefined):
            raise NeoAdapterError("invalid_predefined_type", "Invalid predefined type", subject)
        record = {"id": identifier, "ifc_class": ifc_class, "attributes": attributes}
        if predefined:
            record["predefined_type"] = str(predefined[0])[len(str(IFC)) :]
        records.append(record)
    indexed = {URIRef(identities[record["id"]].iri): record for record in records}
    requirements = _mapping_requirements(mappings, indexed) if mappings is not None else None
    evidence = _properties(graph, entities, indexed, requirements)
    _relationships(graph, entities, indexed)
    return NativeSource(records, identities, _model_units(graph), evidence)
