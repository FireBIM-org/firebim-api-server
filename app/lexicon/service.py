from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Any

from rdflib import RDF, RDFS, Graph, Literal, URIRef
from rdflib.namespace import OWL, XSD

from app.graphs_and_checks.publisher import fetch_rule_ttl
from app.rules.composer import check_ruleset_completeness
from app.rules.decompiler import decompile_ruleset_ttl
from app.schemas.lexicon import (
    LexiconBranchReference,
    LexiconCatalogTermDetail,
    LexiconCatalogTermResponse,
    LexiconCatalogTermsResponse,
    LexiconCatalogTermSummary,
    LexiconConformityCheckResponse,
    LexiconConformityIssue,
    LexiconEntityInput,
    LexiconExecutionProfile,
    LexiconExecutionProfileNode,
    LexiconExecutionTarget,
    LexiconHandshake,
    LexiconMappedTermReference,
    LexiconMapping,
    LexiconMappingValidationRequest,
    LexiconMappingValidationResponse,
    LexiconNeededProperty,
    LexiconNextNeededResponse,
    LexiconPropertyRequirement,
    LexiconReconstructionResponse,
    LexiconSourcePattern,
    LexiconSourcePatternClause,
    LexiconSourceTermCondition,
    LexiconTargetAssertion,
)
from app.schemas.rules import RuleDraft
from app.storage.graph_store import GraphStoreNotFoundError
from app.storage.ontology_repository import OntologyRepository

LEXICON_GRAPH_BASE_URI = "https://firebim.org/graphs/lexicon/"
ONTOLOGY_META_HAS_PROPERTY_URI = "https://firebim.org/ontology-meta#hasProperty"


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


@dataclass(frozen=True)
class LexiconOntologyBundle:
    ontology: LexiconOntologyContext
    graph: Graph
    catalog: dict[str, dict[str, Any]]
    declared_classes: set[str]
    declared_properties: set[str]


@dataclass(frozen=True)
class LexiconOntologyCacheEntry:
    bundle: LexiconOntologyBundle
    created_at: float


class LexiconService:
    CACHE_MAX_ENTRIES = 16
    CACHE_TTL_SECONDS = 300.0

    def __init__(self, ontology_repo: OntologyRepository | None = None) -> None:
        self.ontology_repo = ontology_repo or OntologyRepository()
        self._ontology_cache: OrderedDict[tuple[str, str, str], LexiconOntologyCacheEntry] = OrderedDict()
        self._cache_lock = RLock()

    def health(self) -> bool:
        return self.ontology_repo.health()

    def list_catalog_terms(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
        kind: str | None = None,
        search: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        parent_term_id: str | None = None,
        property_of: str | None = None,
        is_predefined_type: bool | None = None,
    ) -> LexiconCatalogTermsResponse:
        bundle = self._resolve_ontology_bundle(
            ontology_family=ontology_family,
            country=country,
            version=version,
        )
        normalized_kind = kind.strip().lower() if kind else None
        normalized_search = search.strip().lower() if search else None
        normalized_parent_uri = (
            self._normalize_term_uri(parent_term_id, bundle.ontology.canonical_country_uri) if parent_term_id else None
        )
        normalized_property_of_uri = (
            self._normalize_term_uri(property_of, bundle.ontology.canonical_country_uri) if property_of else None
        )
        if limit < 1:
            raise ValueError("limit must be at least 1")

        filtered_terms = [
            LexiconCatalogTermSummary(
                term_id=term["term_id"],
                uri=term["uri"],
                kind=term["kind"],
                label=term["label"],
                definition=term["definition"],
                description=term["description"],
                parent_term_ids=term["parent_term_ids"],
                is_predefined_type=term["is_predefined_type"],
            )
            for term in bundle.catalog.values()
            if (normalized_kind is None or term["kind"] == normalized_kind)
            and (
                normalized_parent_uri is None
                or normalized_parent_uri
                in {
                    self._normalize_term_uri(parent_id, bundle.ontology.canonical_country_uri)
                    for parent_id in term["parent_term_ids"]
                }
            )
            and (
                normalized_property_of_uri is None
                or (
                    term["kind"] == "property"
                    and term["term_id"]
                    in bundle.catalog.get(normalized_property_of_uri, {}).get("property_term_ids", [])
                )
            )
            and (is_predefined_type is None or term["is_predefined_type"] is is_predefined_type)
            and (
                normalized_search is None
                or normalized_search in term["term_id"].lower()
                or normalized_search in (term["label"] or "").lower()
                or normalized_search in (term["definition"] or "").lower()
                or normalized_search in term["uri"].lower()
            )
        ]
        filtered_terms.sort(key=lambda item: ((item.label or item.term_id).lower(), item.term_id.lower()))

        start_index = 0
        if cursor:
            cursor_index = next((i for i, item in enumerate(filtered_terms) if item.term_id == cursor), None)
            if cursor_index is None:
                raise ValueError(f"Unknown cursor: {cursor}")
            start_index = cursor_index + 1

        terms = filtered_terms[start_index : start_index + limit]
        next_cursor = terms[-1].term_id if start_index + limit < len(filtered_terms) and terms else None

        return LexiconCatalogTermsResponse(
            ontology_family=bundle.ontology.ontology_family,
            country=bundle.ontology.country,
            version=bundle.ontology.version,
            canonical_family_uri=bundle.ontology.canonical_family_uri,
            canonical_country_uri=bundle.ontology.canonical_country_uri,
            count=len(terms),
            total_count=len(filtered_terms),
            next_cursor=next_cursor,
            terms=terms,
        )

    def get_catalog_term(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
        term_id: str,
    ) -> LexiconCatalogTermResponse:
        bundle = self._resolve_ontology_bundle(
            ontology_family=ontology_family,
            country=country,
            version=version,
        )
        normalized_uri = self._normalize_term_uri(term_id, bundle.ontology.canonical_country_uri)
        term = bundle.catalog.get(normalized_uri)
        if term is None:
            raise GraphStoreNotFoundError(f"Lexicon term not found: {term_id}")

        return LexiconCatalogTermResponse(
            ontology_family=bundle.ontology.ontology_family,
            country=bundle.ontology.country,
            version=bundle.ontology.version,
            canonical_family_uri=bundle.ontology.canonical_family_uri,
            canonical_country_uri=bundle.ontology.canonical_country_uri,
            term=LexiconCatalogTermDetail(
                term_id=term["term_id"],
                uri=term["uri"],
                kind=term["kind"],
                label=term["label"],
                definition=term["definition"],
                description=term["description"],
                parent_term_ids=term["parent_term_ids"],
                child_term_ids=term["child_term_ids"],
                property_term_ids=term["property_term_ids"],
                is_predefined_type=term["is_predefined_type"],
            ),
        )

    def validate_mapping(
        self,
        *,
        payload: LexiconMappingValidationRequest,
    ) -> LexiconMappingValidationResponse:
        errors: list[str] = []
        warnings: list[str] = []
        source_kind: str | None = None
        target_kind: str | None = None

        if payload.mapping_kind in {"class_to_class", "property_to_property"}:
            source_kind = self._resolve_mapping_term_kind(payload.source_term)
            target_kind = self._resolve_mapping_term_kind(payload.target_term)

            expected_kind = "class" if payload.mapping_kind == "class_to_class" else "property"
            if source_kind != expected_kind:
                errors.append(
                    f"Source term must be a {expected_kind} for mapping_kind={payload.mapping_kind}, "
                    f"but was {source_kind}."
                )
            if target_kind != expected_kind:
                errors.append(
                    f"Target term must be a {expected_kind} for mapping_kind={payload.mapping_kind}, "
                    f"but was {target_kind}."
                )

        elif payload.mapping_kind == "semantic_assertion":
            pattern_errors, pattern_warnings = self._validate_source_pattern(payload.source_pattern)
            target_errors, target_warnings = self._validate_target_assertions(payload.target_assertions)
            errors.extend(pattern_errors)
            errors.extend(target_errors)
            warnings.extend(pattern_warnings)
            warnings.extend(target_warnings)

        compatible = not errors
        if payload.mapping_kind == "class_to_class":
            warnings.append("Class-to-class mappings may connect a class to a subclass or predefined type.")
        if payload.mapping_kind == "semantic_assertion":
            warnings.append("Semantic assertions support many-are-one and many-can-be-one source patterns.")

        return LexiconMappingValidationResponse(
            valid=not errors,
            mapping_kind=payload.mapping_kind,
            source_kind=source_kind,
            target_kind=target_kind,
            compatible=compatible,
            warnings=warnings,
            errors=errors,
        )

    def compute_next_needed(self, handshake: LexiconHandshake) -> LexiconNextNeededResponse:
        covered_target_classes = {item.target_class for item in handshake.accepted_payload.targets}
        covered_properties = {
            (item.target_class, property_path)
            for item in handshake.accepted_payload.targets
            for property_path in item.property_paths
        }

        remaining_target_classes = sorted(
            {
                target.target_class
                for target in handshake.profile.targets
                if target.target_class not in covered_target_classes
            }
        )
        remaining_properties = [
            LexiconNeededProperty(
                target_class=target.target_class,
                property_path=requirement.property_path,
                node_ids=requirement.node_ids,
            )
            for target in handshake.profile.targets
            for requirement in target.property_requirements
            if (target.target_class, requirement.property_path) not in covered_properties
        ]

        return LexiconNextNeededResponse(
            handshake_id=handshake.id,
            complete=not remaining_target_classes and not remaining_properties,
            remaining_target_classes=remaining_target_classes,
            remaining_properties=remaining_properties,
        )

    def derive_profile(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
        rule_id: str | None = None,
        rule: RuleDraft | None = None,
    ) -> tuple[LexiconOntologyContext, LexiconExecutionProfile]:
        bundle = self._resolve_ontology_bundle(
            ontology_family=ontology_family,
            country=country,
            version=version,
        )
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
                target_uri = self._normalize_term_uri(target_class, bundle.ontology.canonical_country_uri)
                if target_uri in bundle.declared_properties:
                    ontology_warnings.append(
                        f"Rule node '{item['id']}' uses property URI as target_class: {target_uri}"
                    )
                elif target_uri not in bundle.declared_classes:
                    ontology_warnings.append(
                        f"Rule node '{item['id']}' uses target_class not declared as class in ontology: {target_uri}"
                    )
            if property_path:
                property_uri = self._normalize_term_uri(property_path, bundle.ontology.canonical_country_uri)
                if property_uri in bundle.declared_classes:
                    ontology_warnings.append(
                        f"Rule node '{item['id']}' uses class URI as property_path: {property_uri}"
                    )
                elif property_uri not in bundle.declared_properties:
                    ontology_warnings.append(
                        "Rule node "
                        f"'{item['id']}' uses property_path not declared as property in ontology: "
                        f"{property_uri}"
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
        return bundle.ontology, profile

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
            for additional_class_uri in entity.additional_class_uris:
                graph.add(
                    (
                        subject,
                        RDF.type,
                        URIRef(self._normalize_term_uri(additional_class_uri, ontology.canonical_country_uri)),
                    )
                )
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

    def reconstruct_graph_from_handshake(
        self,
        *,
        handshake: LexiconHandshake,
        mappings: list[LexiconMapping],
        graph_id: str,
        instance_base_uri: str | None,
    ) -> LexiconReconstructionResponse:
        entities = self._entities_from_handshake(handshake, mappings)
        return self.reconstruct_graph(
            ontology_family=handshake.ontology_family,
            country=handshake.country,
            version=handshake.version,
            graph_id=graph_id,
            instance_base_uri=instance_base_uri,
            entities=entities,
        )

    def check_conformity(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
        ttl: str,
    ) -> LexiconConformityCheckResponse:
        bundle = self._resolve_ontology_bundle(
            ontology_family=ontology_family,
            country=country,
            version=version,
        )
        data_graph = Graph()
        data_graph.parse(data=ttl, format="turtle")

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
            if text not in bundle.declared_classes:
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
            if predicate_text not in bundle.declared_properties:
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
            version=bundle.ontology.version,
            canonical_country_uri=bundle.ontology.canonical_country_uri,
            ontology_uri=bundle.ontology.ontology_uri,
            version_iri=bundle.ontology.version_iri,
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

    def _resolve_ontology_graph(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
    ) -> tuple[LexiconOntologyContext, Graph]:
        bundle = self._resolve_ontology_bundle(
            ontology_family=ontology_family,
            country=country,
            version=version,
        )
        return bundle.ontology, bundle.graph

    def _resolve_ontology_bundle(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
    ) -> LexiconOntologyBundle:
        cache_key = (ontology_family, country, version)
        cached = self._get_cached_bundle(cache_key)
        if cached is not None:
            return cached

        ontology = self.resolve_ontology(
            ontology_family=ontology_family,
            country=country,
            version=version,
        )
        graph = Graph()
        graph.parse(data=ontology.ontology_ttl, format="turtle")
        declared_classes, declared_properties = self._collect_declared_terms(graph)
        bundle = LexiconOntologyBundle(
            ontology=ontology,
            graph=graph,
            catalog=self._build_catalog(graph),
            declared_classes=declared_classes,
            declared_properties=declared_properties,
        )
        self._store_cached_bundle(cache_key, bundle)
        return bundle

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

    def _resolve_mapping_term_kind(self, term: LexiconMappedTermReference) -> str:
        bundle = self._resolve_ontology_bundle(
            ontology_family=term.ontology_family,
            country=term.country,
            version=term.version,
        )
        normalized_uri = term.uri or self._normalize_term_uri(term.term_id, bundle.ontology.canonical_country_uri)
        resolved = bundle.catalog.get(normalized_uri)
        if resolved is None:
            raise GraphStoreNotFoundError(f"Lexicon term not found: {term.term_id}")
        return str(resolved["kind"])

    def _entities_from_handshake(
        self,
        handshake: LexiconHandshake,
        mappings: list[LexiconMapping],
    ) -> list[LexiconEntityInput]:
        mapping_by_id = {mapping.id: mapping for mapping in mappings}
        global_mapping_ids = set(handshake.accepted_payload.mapping_ids)
        entities: list[LexiconEntityInput] = []

        for index, target in enumerate(handshake.accepted_payload.targets, start=1):
            primary_class_uri = self._normalize_term_uri(
                target.target_class,
                handshake.canonical_country_uri,
            )
            additional_class_uris: list[str] = []
            properties: list[dict[str, Any]] = []
            seen_property_values: set[tuple[str, str, str | None, str | None]] = set()
            applicable_mapping_ids = global_mapping_ids | set(target.mapping_ids)

            for mapping_id in applicable_mapping_ids:
                mapping = mapping_by_id.get(mapping_id)
                if mapping is None:
                    continue
                if mapping.mapping_kind == "class_to_class" and mapping.target_term is not None:
                    target_uri = self._resolve_reference_uri(mapping.target_term)
                    if target_uri != primary_class_uri and target_uri not in additional_class_uris:
                        additional_class_uris.append(target_uri)
                elif mapping.mapping_kind == "property_to_property" and mapping.target_term is not None:
                    evidence_value = self._first_evidence_value(target.evidence)
                    if evidence_value is not None:
                        property_uri = self._resolve_reference_uri(mapping.target_term)
                        property_key = (
                            property_uri,
                            str(evidence_value),
                            None,
                            None,
                        )
                        if property_key not in seen_property_values:
                            properties.append(
                                {
                                    "property_uri": property_uri,
                                    "value": evidence_value,
                                }
                            )
                            seen_property_values.add(property_key)
                elif mapping.mapping_kind == "semantic_assertion":
                    for assertion in mapping.target_assertions:
                        assertion_uri = self._resolve_assertion_uri(assertion)
                        if assertion.assertion_kind == "class":
                            if assertion_uri != primary_class_uri and assertion_uri not in additional_class_uris:
                                additional_class_uris.append(assertion_uri)
                        elif assertion.assertion_kind == "property_value":
                            property_key = (
                                assertion_uri,
                                str(assertion.value),
                                assertion.datatype_uri,
                                assertion.language,
                            )
                            if property_key not in seen_property_values:
                                properties.append(
                                    {
                                        "property_uri": assertion_uri,
                                        "value": assertion.value,
                                        "datatype_uri": assertion.datatype_uri,
                                        "language": assertion.language,
                                    }
                                )
                                seen_property_values.add(property_key)

            entity_id = self._entity_id_from_source_ref(target.source_object_ref, index)
            entities.append(
                LexiconEntityInput(
                    entity_id=entity_id,
                    class_uri=primary_class_uri,
                    additional_class_uris=additional_class_uris,
                    label=target.source_object_ref,
                    properties=properties,
                    relations=[],
                )
            )

        return entities

    def _validate_source_pattern(self, pattern: LexiconSourcePattern) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        clauses = pattern.all_of if pattern.all_of else pattern.any_of
        for clause in clauses:
            clause_errors, clause_warnings = self._validate_source_clause(clause)
            errors.extend(clause_errors)
            warnings.extend(clause_warnings)
        return errors, warnings

    def _validate_source_clause(self, clause: LexiconSourcePatternClause) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        if clause.term is not None:
            condition_errors, condition_warnings = self._validate_source_term_condition(clause.term)
            errors.extend(condition_errors)
            warnings.extend(condition_warnings)
        else:
            nested = clause.all_of if clause.all_of else clause.any_of
            for item in nested:
                nested_errors, nested_warnings = self._validate_source_clause(item)
                errors.extend(nested_errors)
                warnings.extend(nested_warnings)
        return errors, warnings

    def _validate_source_term_condition(self, condition: LexiconSourceTermCondition) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        actual_kind = self._resolve_condition_term_kind(condition)
        if actual_kind != condition.term_kind:
            errors.append(
                f"Source term {condition.term_id} was declared as {condition.term_kind} but resolves to {actual_kind}."
            )
        return errors, warnings

    def _validate_target_assertions(self, assertions: list[LexiconTargetAssertion]) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        for assertion in assertions:
            actual_kind = self._resolve_assertion_term_kind(assertion)
            expected_kind = "class" if assertion.assertion_kind == "class" else "property"
            if actual_kind != expected_kind:
                errors.append(
                    f"Target assertion {assertion.term_id} must resolve to {expected_kind}, but was {actual_kind}."
                )
            if assertion.assertion_kind == "property_value":
                warnings.append(
                    "Property_value assertion for "
                    f"{assertion.term_id} is structurally valid; "
                    "semantic meaning is frontend-defined."
                )
        return errors, warnings

    def _resolve_condition_term_kind(self, condition: LexiconSourceTermCondition) -> str:
        return self._resolve_term_kind(
            ontology_family=condition.ontology_family,
            country=condition.country,
            version=condition.version,
            term_id=condition.term_id,
            uri=condition.uri,
        )

    def _resolve_assertion_term_kind(self, assertion: LexiconTargetAssertion) -> str:
        return self._resolve_term_kind(
            ontology_family=assertion.ontology_family,
            country=assertion.country,
            version=assertion.version,
            term_id=assertion.term_id,
            uri=assertion.uri,
        )

    def _resolve_term_kind(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
        term_id: str,
        uri: str | None,
    ) -> str:
        bundle = self._resolve_ontology_bundle(
            ontology_family=ontology_family,
            country=country,
            version=version,
        )
        normalized_uri = uri or self._normalize_term_uri(term_id, bundle.ontology.canonical_country_uri)
        resolved = bundle.catalog.get(normalized_uri)
        if resolved is None:
            raise GraphStoreNotFoundError(f"Lexicon term not found: {term_id}")
        return str(resolved["kind"])

    def _resolve_reference_uri(self, reference: LexiconMappedTermReference) -> str:
        if reference.uri:
            return reference.uri
        ontology = self.resolve_ontology(
            ontology_family=reference.ontology_family,
            country=reference.country,
            version=reference.version,
        )
        return self._normalize_term_uri(reference.term_id, ontology.canonical_country_uri)

    def _resolve_assertion_uri(self, assertion: LexiconTargetAssertion) -> str:
        if assertion.uri:
            return assertion.uri
        ontology = self.resolve_ontology(
            ontology_family=assertion.ontology_family,
            country=assertion.country,
            version=assertion.version,
        )
        return self._normalize_term_uri(assertion.term_id, ontology.canonical_country_uri)

    @staticmethod
    def _first_evidence_value(evidence_items: list[Any]) -> Any | None:
        for item in evidence_items:
            if item.value is not None:
                return item.value
        return None

    @staticmethod
    def _entity_id_from_source_ref(source_ref: str | None, index: int) -> str:
        if not source_ref:
            return f"handshake-entity-{index}"
        sanitized = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in source_ref).strip("-")
        return sanitized or f"handshake-entity-{index}"

    def _get_cached_bundle(
        self,
        cache_key: tuple[str, str, str],
    ) -> LexiconOntologyBundle | None:
        with self._cache_lock:
            entry = self._ontology_cache.get(cache_key)
            if entry is None:
                return None
            if monotonic() - entry.created_at > self.CACHE_TTL_SECONDS:
                del self._ontology_cache[cache_key]
                return None
            self._ontology_cache.move_to_end(cache_key)
            return entry.bundle

    def _store_cached_bundle(
        self,
        cache_key: tuple[str, str, str],
        bundle: LexiconOntologyBundle,
    ) -> None:
        with self._cache_lock:
            self._ontology_cache[cache_key] = LexiconOntologyCacheEntry(
                bundle=bundle,
                created_at=monotonic(),
            )
            self._ontology_cache.move_to_end(cache_key)
            while len(self._ontology_cache) > self.CACHE_MAX_ENTRIES:
                self._ontology_cache.popitem(last=False)

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
    def _build_catalog(graph: Graph) -> dict[str, dict[str, Any]]:
        declared_classes, declared_properties = LexiconService._collect_declared_terms(graph)
        subclass_map: dict[str, set[str]] = defaultdict(set)
        for child, _, parent in graph.triples((None, RDFS.subClassOf, None)):
            subclass_map[str(parent)].add(str(child))

        class_property_map: dict[str, set[str]] = defaultdict(set)
        has_property_predicate = URIRef(ONTOLOGY_META_HAS_PROPERTY_URI)
        for subject, _, obj in graph.triples((None, has_property_predicate, None)):
            class_property_map[str(subject)].add(str(obj))

        term_catalog: dict[str, dict[str, Any]] = {}
        definition_predicate = URIRef("http://www.w3.org/2004/02/skos/core#definition")
        for uri in sorted(declared_classes | declared_properties):
            subject = URIRef(uri)
            kind = "class" if uri in declared_classes else "property"
            label_value = graph.value(subject, RDFS.label)
            definition_value = graph.value(subject, definition_predicate)
            description_value = graph.value(subject, RDFS.comment)
            parent_term_ids = sorted(
                {
                    LexiconService._term_id_from_uri(str(parent))
                    for parent in graph.objects(subject, RDFS.subClassOf)
                    if str(parent) in declared_classes
                }
            )
            term_catalog[uri] = {
                "term_id": LexiconService._term_id_from_uri(uri),
                "uri": uri,
                "kind": kind,
                "label": str(label_value) if label_value is not None else None,
                "definition": str(definition_value) if definition_value is not None else None,
                "description": str(description_value) if description_value is not None else None,
                "parent_term_ids": parent_term_ids,
                "child_term_ids": sorted(
                    LexiconService._term_id_from_uri(child) for child in subclass_map.get(uri, set())
                ),
                "property_term_ids": sorted(
                    LexiconService._term_id_from_uri(prop) for prop in class_property_map.get(uri, set())
                ),
                "is_predefined_type": "predefined type"
                in (str(description_value).lower() if description_value else ""),
            }
        return term_catalog

    @staticmethod
    def _term_id_from_uri(uri: str) -> str:
        if "#" in uri:
            return uri.rsplit("#", 1)[1]
        return uri.rstrip("/").rsplit("/", 1)[-1]

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
