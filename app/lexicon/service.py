from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from rdflib import RDF, RDFS, Graph, Literal, URIRef
from rdflib.namespace import OWL, XSD

from app.graphs_and_checks.publisher import fetch_rule_ttl
from app.rules.composer import check_ruleset_completeness
from app.rules.decompiler import decompile_ruleset_ttl
from app.schemas.lexicon import (
    LexiconBranchReference,
    LexiconConformityCheckResponse,
    LexiconConformityIssue,
    LexiconEntityInput,
    LexiconExecutionProfile,
    LexiconExecutionProfileNode,
    LexiconExecutionTarget,
    LexiconPropertyRequirement,
    LexiconReconstructionResponse,
)
from app.schemas.rules import RuleDraft
from app.storage.ontology_repository import OntologyRepository

LEXICON_GRAPH_BASE_URI = "https://firebim.org/graphs/lexicon/"


@dataclass(frozen=True)
class LexiconOntologyContext:
    ontology_family: str
    country: str
    version: str
    canonical_family_uri: str
    canonical_country_uri: str
    ontology_uri: str | None
    version_iri: str | None
    ontology_ttl: str


class LexiconService:
    def __init__(self, ontology_repo: OntologyRepository | None = None) -> None:
        self.ontology_repo = ontology_repo or OntologyRepository()

    def health(self) -> bool:
        return self.ontology_repo.health()

    def derive_profile(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
        rule_id: str | None = None,
        rule: RuleDraft | None = None,
    ) -> tuple[LexiconOntologyContext, LexiconExecutionProfile]:
        ontology = self.resolve_ontology(
            ontology_family=ontology_family,
            country=country,
            version=version,
        )
        ontology_graph = Graph()
        ontology_graph.parse(data=ontology.ontology_ttl, format="turtle")
        declared_classes, declared_properties = self._collect_declared_terms(ontology_graph)
        normalized_rule = self._load_rule(rule_id=rule_id, rule=rule)
        completeness = check_ruleset_completeness(self._to_composer_rule(normalized_rule))

        property_requirements_by_target: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        node_models: list[LexiconExecutionProfileNode] = []
        ontology_warnings: list[str] = []

        for item in normalized_rule["elements"]:
            branches = [
                LexiconBranchReference(branch=branch_name, target_node_id=item.get(target_field))
                for branch_name, target_field in (
                    ("true", "true_path"),
                    ("false", "false_path"),
                    ("unknown", "unknown_path"),
                )
                if item.get(target_field)
            ]
            node_models.append(
                LexiconExecutionProfileNode(
                    node_id=item["id"],
                    element_type=item.get("element_type"),
                    label=item.get("label"),
                    target_class=item.get("target_class"),
                    property_path=item.get("property_path"),
                    operator=item.get("operator"),
                    value=item.get("value"),
                    branches=branches,
                )
            )

            target_class = item.get("target_class")
            property_path = item.get("property_path")
            if target_class:
                target_uri = self._normalize_term_uri(target_class, ontology.canonical_country_uri)
                if target_uri in declared_properties:
                    ontology_warnings.append(
                        f"Rule node '{item['id']}' uses property URI as target_class: {target_uri}"
                    )
                elif target_uri not in declared_classes:
                    ontology_warnings.append(
                        f"Rule node '{item['id']}' uses target_class not declared as class in ontology: {target_uri}"
                    )
            if property_path:
                property_uri = self._normalize_term_uri(property_path, ontology.canonical_country_uri)
                if property_uri in declared_classes:
                    ontology_warnings.append(
                        f"Rule node '{item['id']}' uses class URI as property_path: {property_uri}"
                    )
                elif property_uri not in declared_properties:
                    ontology_warnings.append(
                        f"Rule node '{item['id']}' uses property_path not declared as property in ontology: {property_uri}"
                    )
            if target_class and property_path:
                bucket = property_requirements_by_target[target_class].setdefault(
                    property_path,
                    {
                        "node_ids": [],
                        "operators": [],
                        "sample_values": [],
                    },
                )
                bucket["node_ids"].append(item["id"])
                if item.get("operator"):
                    bucket["operators"].append(str(item["operator"]))
                if item.get("value") is not None:
                    bucket["sample_values"].append(str(item["value"]))

        targets: list[LexiconExecutionTarget] = []
        for target_class, requirements in sorted(property_requirements_by_target.items()):
            target_node_ids = sorted(
                {node_id for requirement in requirements.values() for node_id in requirement["node_ids"]}
            )
            targets.append(
                LexiconExecutionTarget(
                    target_class=target_class,
                    node_ids=target_node_ids,
                    property_requirements=[
                        LexiconPropertyRequirement(
                            property_path=property_path,
                            node_ids=sorted(set(requirement["node_ids"])),
                            operators=sorted(set(requirement["operators"])),
                            sample_values=sorted(set(requirement["sample_values"])),
                        )
                        for property_path, requirement in sorted(requirements.items())
                    ],
                    relation_requirements=[],
                )
            )

        profile = LexiconExecutionProfile(
            rule_id=normalized_rule["id"],
            entry_node_id=normalized_rule.get("entry"),
            node_count=len(normalized_rule["elements"]),
            complete=bool(completeness["complete"]),
            open=bool(completeness["open"]),
            broken=bool(completeness["broken"]),
            target_classes=sorted(
                {str(item["target_class"]) for item in normalized_rule["elements"] if item.get("target_class")}
            ),
            property_paths=sorted(
                {str(item["property_path"]) for item in normalized_rule["elements"] if item.get("property_path")}
            ),
            relation_paths=[],
            targets=targets,
            nodes=node_models,
            warnings=[str(error) for error in completeness.get("errors", [])]
            + [
                f"Open branch from {branch.get('node_id')}:{branch['branch']}"
                for branch in completeness.get("open_branches", [])
            ]
            + sorted(set(ontology_warnings)),
        )
        return ontology, profile

    def reconstruct_graph(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
        graph_id: str,
        instance_base_uri: str | None,
        entities: list[LexiconEntityInput],
    ) -> LexiconReconstructionResponse:
        ontology = self.resolve_ontology(
            ontology_family=ontology_family,
            country=country,
            version=version,
        )
        base_instance_uri = (
            instance_base_uri.rstrip("/") + "/"
            if instance_base_uri
            else f"https://firebim.org/instances/lexicon/{country}/{ontology.version}/"
        )
        graph = Graph()

        for entity in entities:
            subject = URIRef(f"{base_instance_uri}{entity.entity_id}")
            class_uri = URIRef(self._normalize_term_uri(entity.class_uri, ontology.canonical_country_uri))
            graph.add((subject, RDF.type, class_uri))
            if entity.label:
                graph.add((subject, RDFS.label, Literal(entity.label)))

            for prop in entity.properties:
                predicate = URIRef(self._normalize_term_uri(prop.property_uri, ontology.canonical_country_uri))
                graph.add((subject, predicate, self._to_literal(prop.value, prop.datatype_uri, prop.language)))

            for relation in entity.relations:
                predicate = URIRef(self._normalize_term_uri(relation.predicate_uri, ontology.canonical_country_uri))
                target = URIRef(f"{base_instance_uri}{relation.target_entity_id}")
                graph.add((subject, predicate, target))

        ttl = graph.serialize(format="turtle")
        if isinstance(ttl, bytes):
            ttl = ttl.decode("utf-8")

        return LexiconReconstructionResponse(
            ontology_family=ontology_family,
            country=country,
            version=ontology.version,
            graph_id=graph_id,
            graph_uri=f"{LEXICON_GRAPH_BASE_URI}{graph_id}",
            canonical_family_uri=ontology.canonical_family_uri,
            canonical_country_uri=ontology.canonical_country_uri,
            ontology_uri=ontology.ontology_uri,
            version_iri=ontology.version_iri,
            triple_count=len(graph),
            ttl=str(ttl),
        )

    def check_conformity(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
        ttl: str,
    ) -> LexiconConformityCheckResponse:
        ontology = self.resolve_ontology(
            ontology_family=ontology_family,
            country=country,
            version=version,
        )
        data_graph = Graph()
        data_graph.parse(data=ttl, format="turtle")
        ontology_graph = Graph()
        ontology_graph.parse(data=ontology.ontology_ttl, format="turtle")

        declared_classes = {str(subject) for subject in ontology_graph.subjects(RDF.type, OWL.Class)} | {
            str(subject) for subject in ontology_graph.subjects(RDF.type, RDFS.Class)
        }
        declared_properties = (
            {str(subject) for subject in ontology_graph.subjects(RDF.type, RDF.Property)}
            | {str(subject) for subject in ontology_graph.subjects(RDF.type, OWL.ObjectProperty)}
            | {str(subject) for subject in ontology_graph.subjects(RDF.type, OWL.DatatypeProperty)}
        )

        unknown_classes: set[str] = set()
        unknown_properties: set[str] = set()
        missing_type_subjects: set[str] = set()
        issues: list[LexiconConformityIssue] = []

        typed_subjects = {str(subject) for subject, _, _ in data_graph.triples((None, RDF.type, None))}
        for subject in {str(item) for item in data_graph.subjects()}:
            if subject not in typed_subjects:
                missing_type_subjects.add(subject)
                issues.append(
                    LexiconConformityIssue(
                        code="missing_type",
                        message="Subject has no rdf:type assertion",
                        subject=subject,
                    )
                )

        for subject, _, object_value in data_graph.triples((None, RDF.type, None)):
            text = str(object_value)
            if text.startswith(str(RDF)) or text.startswith(str(RDFS)) or text.startswith(str(OWL)):
                continue
            if text not in declared_classes:
                unknown_classes.add(text)
                issues.append(
                    LexiconConformityIssue(
                        code="unknown_class",
                        message="Class is not declared in the selected ontology",
                        subject=str(subject),
                        object=text,
                    )
                )

        for subject, predicate, object_value in data_graph:
            if predicate == RDF.type:
                continue
            predicate_text = str(predicate)
            if (
                predicate_text.startswith(str(RDF))
                or predicate_text.startswith(str(RDFS))
                or predicate_text.startswith(str(OWL))
            ):
                continue
            if predicate_text not in declared_properties:
                unknown_properties.add(predicate_text)
                issues.append(
                    LexiconConformityIssue(
                        code="unknown_property",
                        message="Property is not declared in the selected ontology",
                        subject=str(subject),
                        predicate=predicate_text,
                        object=str(object_value),
                    )
                )

        structure_ok = not missing_type_subjects
        semantic_ok = not unknown_classes and not unknown_properties
        execution_ready = structure_ok and semantic_ok and len(data_graph) > 0
        return LexiconConformityCheckResponse(
            ontology_family=ontology_family,
            country=country,
            version=ontology.version,
            canonical_country_uri=ontology.canonical_country_uri,
            ontology_uri=ontology.ontology_uri,
            version_iri=ontology.version_iri,
            triple_count=len(data_graph),
            structure_ok=structure_ok,
            semantic_ok=semantic_ok,
            execution_ready=execution_ready,
            conforms=structure_ok and semantic_ok,
            unknown_classes=sorted(unknown_classes),
            unknown_properties=sorted(unknown_properties),
            missing_type_subjects=sorted(missing_type_subjects),
            issues=issues,
        )

    def resolve_ontology(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
    ) -> LexiconOntologyContext:
        fetched = self.ontology_repo.fetch_version(ontology_family, country, version)
        return LexiconOntologyContext(
            ontology_family=ontology_family,
            country=country,
            version=fetched.version.version,
            canonical_family_uri=fetched.canonical_family_uri,
            canonical_country_uri=fetched.canonical_country_uri,
            ontology_uri=fetched.version.ontology_uri,
            version_iri=fetched.version.version_iri,
            ontology_ttl=fetched.ttl,
        )

    @staticmethod
    def _load_rule(*, rule_id: str | None, rule: RuleDraft | None) -> dict:
        if rule_id:
            _graph_uri, ttl = fetch_rule_ttl(rule_id)
            decompiled = decompile_ruleset_ttl(ttl)
            return LexiconService._normalize_rule_dict(decompiled)
        if rule is None:
            raise ValueError("rule payload is required when rule_id is not provided")
        return LexiconService._normalize_rule_dict(rule.as_internal_dict())

    @staticmethod
    def _normalize_rule_dict(rule: dict) -> dict:
        raw_elements = rule.get("elements") or rule.get("nodes") or []
        elements = []
        for raw in raw_elements:
            item = dict(raw)
            item.setdefault("element_type", item.get("node_type"))
            elements.append(item)
        return {
            "id": rule.get("id"),
            "entry": rule.get("entry"),
            "elements": elements,
        }

    @staticmethod
    def _to_composer_rule(rule: dict) -> dict:
        return {
            "id": rule.get("id"),
            "entry": rule.get("entry"),
            "nodes": [
                {
                    **item,
                    "node_type": item.get("element_type", item.get("node_type")),
                }
                for item in rule.get("elements", [])
            ],
        }

    @staticmethod
    def _normalize_term_uri(value: str, canonical_country_uri: str) -> str:
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return f"{canonical_country_uri}{value}"

    @staticmethod
    def _collect_declared_terms(graph: Graph) -> tuple[set[str], set[str]]:
        declared_classes = {str(subject) for subject in graph.subjects(RDF.type, OWL.Class)} | {
            str(subject) for subject in graph.subjects(RDF.type, RDFS.Class)
        }
        declared_properties = (
            {str(subject) for subject in graph.subjects(RDF.type, RDF.Property)}
            | {str(subject) for subject in graph.subjects(RDF.type, OWL.ObjectProperty)}
            | {str(subject) for subject in graph.subjects(RDF.type, OWL.DatatypeProperty)}
        )
        return declared_classes, declared_properties

    @staticmethod
    def _to_literal(value: Any, datatype_uri: str | None, language: str | None) -> Literal:
        if language:
            return Literal(value, lang=language)
        if datatype_uri:
            return Literal(value, datatype=URIRef(datatype_uri))
        if isinstance(value, bool):
            return Literal(value, datatype=XSD.boolean)
        if isinstance(value, int):
            return Literal(value, datatype=XSD.integer)
        if isinstance(value, float):
            return Literal(value, datatype=XSD.decimal)
        return Literal(value)
