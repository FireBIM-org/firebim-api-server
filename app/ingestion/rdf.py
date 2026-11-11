"""Destination assertions in SI; source units and grouping remain private provenance."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction
from time import monotonic
from urllib.parse import quote

from rdflib import OWL, RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef

from app.ingestion.errors import ProcessingError
from app.ingestion.neo_revision import verify_neo_revision
from app.ingestion.policy import LIMITS
from app.lexicon.ifc_mapping_units import Unit
from app.lexicon.ifc_mapping_validation import validate_executable_mapping
from app.lexicon.ifc_mapping_values import NUMERIC_DATATYPES, valid_literal

PROVENANCE = Namespace("urn:firebim:ingestion:")
RDF_CONTRACT = "neo-destination-rdf/1"


def reject(message):
    raise ProcessingError("semantic_rejection", message)


def iri(value):
    if (
        not isinstance(value, str)
        or not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
        or any(char.isspace() or ord(char) < 32 or char in '<>"{}|^`\\' for char in value)
    ):
        reject("Destination identities must be exact absolute IRIs")
    return URIRef(value)


def _numeric_lexical(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        reject("Measurements require finite numeric values")
    return str(value)


def _measurement(value, datatype, unit):
    if datatype not in NUMERIC_DATATYPES:
        reject("Only numeric datatypes may carry measurement units")
    number = Fraction(_numeric_lexical(value)) * Fraction(str(unit.scale_to_si))
    if datatype == str(XSD.integer):
        if number.denominator != 1:
            reject("SI normalization cannot losslessly represent this integer measurement")
        lexical = str(number.numerator)
    else:
        with localcontext() as context:
            context.prec = len(str(abs(number.numerator))) + len(str(number.denominator)) + 10
            lexical = format(Decimal(number.numerator) / Decimal(number.denominator), "f")
        if datatype in {str(XSD.float), str(XSD.double)}:
            numeric = float(lexical)
            if (
                not math.isfinite(numeric)
                or (number and numeric == 0)
                or datatype == str(XSD.float)
                and (abs(numeric) > 3.4028234663852886e38 or number and abs(numeric) < 1.401298464324817e-45)
            ):
                reject("SI measurement is outside its destination datatype range")
    return Literal(lexical, datatype=iri(datatype), normalize=False)


def _turtle(graph, guard):
    if len(graph) > LIMITS.triples:
        raise ProcessingError("destination_limit", "Destination or provenance exceeds the triple budget")
    # N-Triples is a Turtle subset; full IRIs and sorted statements give stable bytes.
    lines = []
    size = 0
    for index, (subject, predicate, obj) in enumerate(graph):
        if index % 128 == 0:
            guard()
        line = f"{subject.n3()} {predicate.n3()} {obj.n3()} .\n".encode()
        size += len(line)
        if size > LIMITS.file_bytes:
            raise ProcessingError("destination_limit", "Destination or provenance exceeds the byte budget")
        lines.append(line)
    return b"".join(sorted(lines))


@dataclass(frozen=True)
class SerializedDestination:
    destination: bytes
    provenance: bytes
    validation: dict


@dataclass(frozen=True)
class RetainedOntologyContext:
    pin: object
    graph: Graph
    ttl: str


def serialize_destination(revision, evaluation, model_id, *, heartbeat=lambda: None):
    """Validate against exact retained declarations, without compiling or library reads.

    Numeric rule properties use SI (metres/square metres). Original typed values,
    symbols, dimensions and scales are reified separately, never mixed into checks.
    Group membership is evidence, not an invented destination ontology assertion.
    """
    verify_neo_revision(revision)
    started = monotonic()

    def guard():
        if monotonic() - started > 30:
            raise ProcessingError("validation_timeout", "Destination validation exceeded its time budget")
        heartbeat()

    modules = []
    ontology = Graph()
    for artifact in revision.content.ontology_artifacts:
        guard()
        if len(artifact.ttl.encode()) > LIMITS.file_bytes:
            raise ProcessingError("destination_limit", "Retained ontology exceeds the byte budget")
        graph = Graph().parse(data=artifact.ttl, format="turtle")
        if len(graph) + len(ontology) > LIMITS.triples:
            raise ProcessingError("destination_limit", "Retained ontology exceeds the triple budget")
        modules.append(RetainedOntologyContext(artifact.pin, graph, artifact.ttl))
        ontology += graph
    mappings = revision.content.compiled_mappings
    for mapping_id, mapping in mappings.items():
        guard()
        _, diagnostics = validate_executable_mapping(mapping.model_dump(mode="json"), modules, mapping_id, "mapping")
        if diagnostics:
            reject("Retained destination contract is invalid: " + diagnostics[0].code)
    if (
        not isinstance(evaluation, dict)
        or set(evaluation) != {"assertions", "diagnostics", "memberships"}
        or any(item.get("severity", "error") == "error" for item in evaluation["diagnostics"])
    ):
        reject("Evaluation has errors or an invalid result contract")
    if len(evaluation["assertions"]) + len(evaluation["memberships"]) > LIMITS.triples:
        raise ProcessingError("destination_limit", "Evaluation exceeds the assertion budget")
    graph, provenance = Graph(), Graph()
    prefix = f"urn:firebim:model:{quote(model_id, safe='')}:"
    cardinalities = set()
    grouped = set()

    def identity(value, *, entity=False):
        node = iri(value)
        if not value.startswith(prefix + "entity:") and (entity or not value.startswith(prefix + "group:")):
            reject("Assertion identity is outside the admitted model")
        return node

    for index, assertion in enumerate(evaluation["assertions"]):
        if index % 128 == 0:
            guard()
        if set(assertion) != {"subject", "predicate", "object", "datatype", "evidence", "unit"}:
            reject("Malformed destination assertion")
        subject = identity(
            assertion["subject"], entity=assertion["datatype"] is not None or assertion["predicate"] != str(RDF.type)
        )
        predicate = iri(assertion["predicate"])
        datatype, value = assertion["datatype"], assertion["object"]
        evidence = assertion["evidence"]
        if not evidence:
            reject("Destination assertion has no mapping evidence")
        targets = []
        for item in evidence:
            mapping = mappings.get(item.get("mapping_id"))
            if mapping is None:
                reject("Assertion references a mapping outside the retained revision")
            targets.append(mapping.target)
        unit = None
        if assertion["unit"] is not None:
            try:
                unit = Unit(**assertion["unit"])
            except (TypeError, ValueError) as exc:
                reject("Malformed measurement unit: " + str(exc))
        if datatype is not None:
            if not valid_literal(value, datatype):
                reject("Destination literal does not match its declared datatype")
            obj = Literal(value, datatype=iri(datatype), normalize=False)
            if unit is not None:
                obj = _measurement(value, datatype, unit)
            for target in targets:
                if target.kind != "datatype_property" or target.iri != str(predicate) or target.datatype != datatype:
                    reject("Literal assertion disagrees with its retained mapping")
                selector = getattr(target.value, "selector", None)
                dimension = getattr(selector, "unit", None)
                if (dimension in {"ifc_length", "ifc_area"}) != (unit is not None) or (
                    unit is not None and unit.dimension != dimension.removeprefix("ifc_")
                ):
                    reject("Measurement units disagree with the retained mapping")
        else:
            if unit is not None:
                reject("Resource assertions cannot carry units")
            obj = iri(value)
            for target in targets:
                if predicate == RDF.type:
                    if target.kind != "class" or target.iri != value:
                        reject("Class assertion disagrees with its retained mapping")
                    is_group = str(subject).startswith(prefix + "group:")
                    if is_group != (target.grouping is not None):
                        reject("Grouping identity disagrees with the retained mapping")
                    if is_group:
                        grouped.add(subject)
                elif target.kind != "object_property" or target.iri != str(predicate):
                    reject("Relationship assertion disagrees with its retained mapping")
                else:
                    identity(value, entity=True)
        if any(getattr(target, "cardinality", None) == "single" for target in targets):
            cardinalities.add((subject, predicate))
        graph.add((subject, predicate, obj))
        node = URIRef(
            "urn:firebim:assertion:"
            + hashlib.sha256(
                json.dumps(assertion, sort_keys=True, allow_nan=False).encode(),
            ).hexdigest()
        )
        for triple in (
            (node, RDF.type, RDF.Statement),
            (node, RDF.subject, subject),
            (node, RDF.predicate, predicate),
            (node, RDF.object, obj),
            (node, PROVENANCE.evidence, Literal(json.dumps(evidence, sort_keys=True), datatype=RDF.JSON)),
        ):
            provenance.add(triple)
        if unit is not None:
            for predicate_, object_ in (
                (PROVENANCE.sourceValue, Literal(value, datatype=iri(datatype), normalize=False)),
                (PROVENANCE.unitSymbol, Literal(unit.symbol)),
                (PROVENANCE.dimension, Literal(unit.dimension)),
                (PROVENANCE.scaleToSI, Literal(str(unit.scale_to_si), datatype=XSD.decimal)),
                (PROVENANCE.canonicalUnit, Literal("m" if unit.dimension == "length" else "m2")),
            ):
                provenance.add((node, predicate_, object_))
        if len(provenance) > LIMITS.triples:
            raise ProcessingError("destination_limit", "Provenance exceeds the triple budget")
    for membership in evaluation["memberships"]:
        guard()
        group, member = identity(membership["group"]), identity(membership["member"], entity=True)
        if group not in grouped or not membership["evidence"]:
            reject("Group membership has no mapped group or evidence")
        for item in membership["evidence"]:
            mapping = mappings.get(item.get("mapping_id"))
            if mapping is None or mapping.target.kind != "class" or mapping.target.grouping is None:
                reject("Group membership disagrees with retained mappings")
        provenance.add((group, PROVENANCE.member, member))
    _validate_graph(graph, ontology, cardinalities, guard)
    guard()
    destination, evidence = _turtle(graph, guard), _turtle(provenance, guard)
    guard()
    return SerializedDestination(
        destination,
        evidence,
        {
            "validation_version": RDF_CONTRACT,
            "valid": True,
            "triples": len(graph),
            "provenance_triples": len(provenance),
            "memberships": len(evaluation["memberships"]),
            "measurement_policy": "SI-scalars-with-private-source-units/1",
            "wall_seconds": monotonic() - started,
        },
    )


def _validate_graph(graph, ontology, cardinalities, guard):
    for subject, predicate in graph.subject_predicates():
        if (predicate, RDF.type, OWL.FunctionalProperty) in ontology:
            cardinalities.add((subject, predicate))
    for subject, predicate in cardinalities:
        if len(set(graph.objects(subject, predicate))) > 1:
            reject("Destination violates a single-valued property")
    # Domain/range entail types rather than require explicit assertions (open world).
    types = {}
    for subject, predicate, obj in graph:
        if predicate == RDF.type:
            types.setdefault(subject, set()).add(obj)
        for domain in ontology.objects(predicate, RDFS.domain):
            types.setdefault(subject, set()).add(domain)
        if isinstance(obj, URIRef) and predicate != RDF.type:
            for range_ in ontology.objects(predicate, RDFS.range):
                types.setdefault(obj, set()).add(range_)
    ancestors = {}
    for index, classes in enumerate(types.values()):
        if index % 128 == 0:
            guard()
        expanded = set()
        for class_ in classes:
            if class_ not in ancestors:
                ancestors[class_] = {class_, *ontology.transitive_objects(class_, RDFS.subClassOf)}
            expanded.update(ancestors[class_])
        if OWL.Nothing in expanded or any(
            other in expanded for class_ in expanded for other in ontology.objects(class_, OWL.disjointWith)
        ):
            reject("Destination types contradict retained ontology disjointness")
