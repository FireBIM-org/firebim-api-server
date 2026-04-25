from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from rdflib import RDF, Graph

from app.lexicon.custom_eval import LexiconCustomEvaluator
from app.schemas.lexicon import (
    LexiconCatalogResolvedMatch,
    LexiconCatalogResolvedTerm,
    LexiconCatalogResolveResponse,
    LexiconCatalogTermSummary,
    LexiconGraphEvidenceSubjectSummary,
    LexiconGraphEvidenceSummaryResponse,
    LexiconMappingEvaluationClauseResult,
    LexiconSourceEntityPattern,
    LexiconSourcePattern,
    LexiconSourcePatternClause,
    LexiconSourceTermCondition,
)


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
class LexiconMergedResolutionDataset:
    bundles: tuple[LexiconOntologyBundle, ...]
    merged_graph: Graph
    catalog: dict[str, dict[str, Any]]
    declared_classes: set[str]
    declared_properties: set[str]
    provenance_by_uri: dict[str, list[tuple[str, str, str]]]
    evaluator: LexiconCustomEvaluator


class LexiconQueryRuntime:
    def __init__(self, dataset: LexiconMergedResolutionDataset) -> None:
        self.dataset = dataset

    @classmethod
    def from_bundles(cls, bundles: list[LexiconOntologyBundle]) -> LexiconQueryRuntime:
        merged_graph = Graph()
        merged_catalog: dict[str, dict[str, Any]] = {}
        declared_classes: set[str] = set()
        declared_properties: set[str] = set()
        provenance_by_uri: dict[str, list[tuple[str, str, str]]] = defaultdict(list)

        for bundle in bundles:
            merged_graph += bundle.graph
            declared_classes.update(bundle.declared_classes)
            declared_properties.update(bundle.declared_properties)
            for uri, term in bundle.catalog.items():
                merged_catalog.setdefault(uri, term)
                provenance_by_uri[uri].append(
                    (
                        bundle.ontology.ontology_family,
                        bundle.ontology.country,
                        bundle.ontology.version,
                    )
                )

        return cls(
            LexiconMergedResolutionDataset(
                bundles=tuple(bundles),
                merged_graph=merged_graph,
                catalog=merged_catalog,
                declared_classes=declared_classes,
                declared_properties=declared_properties,
                provenance_by_uri=dict(provenance_by_uri),
                evaluator=LexiconCustomEvaluator(merged_graph),
            )
        )

    def catalog_lookup_by_uri(self) -> dict[str, dict[str, Any]]:
        return self.dataset.catalog

    def resolve_term_matches(self, value: str) -> dict[str, Any]:
        if value.startswith("http://") or value.startswith("https://"):
            candidate_uris = [value]
            normalized_uri = value
        else:
            candidate_uris = [f"{bundle.ontology.canonical_country_uri}{value}" for bundle in self.dataset.bundles]
            normalized_uri = candidate_uris[0]

        class_matches = {
            candidate_uri for candidate_uri in candidate_uris if candidate_uri in self.dataset.declared_classes
        }
        property_matches = {
            candidate_uri for candidate_uri in candidate_uris if candidate_uri in self.dataset.declared_properties
        }
        return {
            "normalized_uri": normalized_uri,
            "class_matches": class_matches,
            "property_matches": property_matches,
        }

    def resolve_terms(self, *, terms: list[str], limit_per_term: int) -> LexiconCatalogResolveResponse:
        results: list[LexiconCatalogResolvedTerm] = []
        for input_term in terms:
            matches: list[LexiconCatalogResolvedMatch] = []
            seen_uris: set[str] = set()
            normalized_input = input_term.strip()
            lowered_input = normalized_input.lower()
            direct_uri = (
                normalized_input
                if normalized_input.startswith("http://") or normalized_input.startswith("https://")
                else None
            )

            scored_matches: list[tuple[int, str, dict[str, Any], tuple[str, str, str]]] = []
            for uri, term in self.dataset.catalog.items():
                match_type: str | None = None
                score = 0
                if direct_uri and uri == direct_uri:
                    match_type = "exact_uri"
                    score = 100
                elif term["term_id"].lower() == lowered_input:
                    match_type = "exact_term_id"
                    score = 95
                elif (term["label"] or "").lower() == lowered_input:
                    match_type = "exact_label"
                    score = 90
                elif lowered_input in term["term_id"].lower():
                    match_type = "term_id_contains"
                    score = 75
                elif lowered_input in (term["label"] or "").lower():
                    match_type = "label_contains"
                    score = 70
                elif lowered_input in (term["definition"] or "").lower():
                    match_type = "definition_contains"
                    score = 60

                if match_type is None:
                    continue
                provenance = self.dataset.provenance_by_uri.get(uri, [("", "", "")])[0]
                scored_matches.append((score, match_type, term, provenance))

            scored_matches.sort(
                key=lambda item: (
                    -item[0],
                    (item[2]["label"] or item[2]["term_id"]).lower(),
                    item[2]["term_id"].lower(),
                )
            )

            for score, match_type, term, provenance in scored_matches:
                if term["uri"] in seen_uris:
                    continue
                seen_uris.add(term["uri"])
                family, country, version = provenance
                matches.append(
                    LexiconCatalogResolvedMatch(
                        input_term=normalized_input,
                        ontology_family=family,
                        country=country,
                        version=version,
                        canonical_country_uri=self._canonical_country_uri_for_uri(term["uri"]),
                        match_type=match_type,
                        score=score,
                        term=self.summary_from_catalog_term(term),
                    )
                )
                if len(matches) >= limit_per_term:
                    break

            results.append(
                LexiconCatalogResolvedTerm(
                    input_term=normalized_input,
                    count=len(matches),
                    matches=matches,
                )
            )

        return LexiconCatalogResolveResponse(count=len(results), results=results)

    def summarize_graph_evidence(
        self,
        *,
        graph_id: str,
        graph_uri: str,
        data_graph: Graph,
    ) -> LexiconGraphEvidenceSummaryResponse:
        subject_types, subject_predicates = self.build_subject_indexes(data_graph)
        class_term_ids: set[str] = set()
        property_term_ids: set[str] = set()
        property_set_term_ids: set[str] = set()
        subjects: list[LexiconGraphEvidenceSubjectSummary] = []

        for subject in sorted({str(item) for item in data_graph.subjects()}):
            matched_classes = self._matched_classes_for_subject(subject, subject_types)
            matched_properties = self._matched_properties_for_subject(subject, subject_predicates)
            matched_property_sets = self._matched_property_sets_for_subject(
                subject,
                subject_types=subject_types,
                subject_predicates=subject_predicates,
            )
            class_term_ids.update(matched_classes)
            property_term_ids.update(matched_properties)
            property_set_term_ids.update(matched_property_sets)
            subjects.append(
                LexiconGraphEvidenceSubjectSummary(
                    source_object_ref=subject,
                    class_term_ids=matched_classes,
                    property_term_ids=matched_properties,
                    property_set_term_ids=matched_property_sets,
                )
            )

        return LexiconGraphEvidenceSummaryResponse(
            graph_id=graph_id,
            graph_uri=graph_uri,
            triple_count=len(data_graph),
            object_count=len(subjects),
            class_term_ids=sorted(class_term_ids),
            property_term_ids=sorted(property_term_ids),
            property_set_term_ids=sorted(property_set_term_ids),
            subjects=subjects,
        )

    def build_subject_indexes(self, data_graph: Graph) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        subject_types: dict[str, set[str]] = defaultdict(set)
        subject_predicates: dict[str, set[str]] = defaultdict(set)
        for subject, _, object_value in data_graph.triples((None, RDF.type, None)):
            subject_types[str(subject)].add(str(object_value))
        for subject, predicate, _ in data_graph:
            if predicate != RDF.type:
                subject_predicates[str(subject)].add(str(predicate))
        return subject_types, subject_predicates

    def evaluate_source_pattern(
        self,
        *,
        pattern: LexiconSourcePattern,
        subject_types: dict[str, set[str]],
        subject_predicates: dict[str, set[str]],
    ) -> LexiconMappingEvaluationClauseResult:
        clauses = pattern.all_of if pattern.all_of else pattern.any_of
        clause_type = "all_of" if pattern.all_of else "any_of"
        child_results = [
            self.evaluate_source_clause(
                clause=clause,
                subject_types=subject_types,
                subject_predicates=subject_predicates,
            )
            for clause in clauses
        ]
        return self.combine_clause_results(clause_type=clause_type, children=child_results)

    def evaluate_source_clause(
        self,
        *,
        clause: LexiconSourcePatternClause,
        subject_types: dict[str, set[str]],
        subject_predicates: dict[str, set[str]],
    ) -> LexiconMappingEvaluationClauseResult:
        if clause.term is not None:
            return self.evaluate_term_condition(
                condition=clause.term,
                subject_types=subject_types,
                subject_predicates=subject_predicates,
            )
        if clause.entity is not None:
            return self.evaluate_entity_pattern(
                entity=clause.entity,
                subject_types=subject_types,
                subject_predicates=subject_predicates,
            )

        child_results = [
            self.evaluate_source_clause(
                clause=item,
                subject_types=subject_types,
                subject_predicates=subject_predicates,
            )
            for item in (clause.all_of if clause.all_of else clause.any_of)
        ]
        return self.combine_clause_results(
            clause_type="all_of" if clause.all_of else "any_of",
            children=child_results,
        )

    def evaluate_term_condition(
        self,
        *,
        condition: LexiconSourceTermCondition,
        subject_types: dict[str, set[str]],
        subject_predicates: dict[str, set[str]],
    ) -> LexiconMappingEvaluationClauseResult:
        uri = self.resolve_term_uri_from_condition(condition)
        if condition.term_kind == "class":
            matched_subjects = self.dataset.evaluator.subjects_for_target_class(subject_types, uri)
            missing = [] if matched_subjects else [f"Missing class: {condition.term_id}"]
        else:
            matched_subjects = sorted(
                subject for subject, predicates in subject_predicates.items() if uri in predicates
            )
            missing = [] if matched_subjects else [f"Missing property: {condition.term_id}"]
        return LexiconMappingEvaluationClauseResult(
            clause_type="term",
            matched=bool(matched_subjects),
            matched_source_objects=matched_subjects,
            missing_conditions=missing,
        )

    def evaluate_entity_pattern(
        self,
        *,
        entity: LexiconSourceEntityPattern,
        subject_types: dict[str, set[str]],
        subject_predicates: dict[str, set[str]],
    ) -> LexiconMappingEvaluationClauseResult:
        conditions = entity.all_of if entity.all_of else entity.any_of
        child_results = [
            self.evaluate_term_condition(
                condition=condition,
                subject_types=subject_types,
                subject_predicates=subject_predicates,
            )
            for condition in conditions
        ]
        clause_type = "entity_all_of" if entity.all_of else "entity_any_of"
        combined = self.combine_clause_results(clause_type=clause_type, children=child_results)
        combined.missing_conditions.append("Entity-scoped terms must be found on the same source object.")
        if combined.matched:
            combined.missing_conditions = [
                item
                for item in combined.missing_conditions
                if item != "Entity-scoped terms must be found on the same source object."
            ]
        return combined

    @staticmethod
    def combine_clause_results(
        *,
        clause_type: str,
        children: list[LexiconMappingEvaluationClauseResult],
    ) -> LexiconMappingEvaluationClauseResult:
        if clause_type in {"all_of", "entity_all_of"}:
            matched = all(child.matched for child in children)
            if clause_type == "entity_all_of":
                subject_sets = [set(child.matched_source_objects) for child in children if child.matched_source_objects]
                matched_subjects = sorted(set.intersection(*subject_sets)) if subject_sets else []
                matched = matched and bool(matched_subjects)
            else:
                matched_subjects = sorted({subject for child in children for subject in child.matched_source_objects})
            missing_conditions = [item for child in children for item in child.missing_conditions]
        else:
            matched = any(child.matched for child in children)
            matched_subjects = sorted({subject for child in children for subject in child.matched_source_objects})
            missing_conditions = [] if matched else [item for child in children for item in child.missing_conditions]

        return LexiconMappingEvaluationClauseResult(
            clause_type=clause_type,
            matched=matched,
            matched_source_objects=matched_subjects,
            missing_conditions=missing_conditions,
            children=children,
        )

    @staticmethod
    def summary_from_catalog_term(term: dict[str, Any]) -> LexiconCatalogTermSummary:
        return LexiconCatalogTermSummary(
            term_id=term["term_id"],
            uri=term["uri"],
            kind=term["kind"],
            label=term["label"],
            definition=term["definition"],
            description=term["description"],
            parent_term_ids=term["parent_term_ids"],
            is_predefined_type=term["is_predefined_type"],
        )

    def resolve_term_uri_from_condition(self, condition: LexiconSourceTermCondition) -> str:
        if condition.uri:
            return condition.uri
        for bundle in self.dataset.bundles:
            if (
                bundle.ontology.ontology_family == condition.ontology_family
                and bundle.ontology.country == condition.country
                and bundle.ontology.version == condition.version
            ):
                return f"{bundle.ontology.canonical_country_uri}{condition.term_id}"
        first_bundle = self.dataset.bundles[0]
        return f"{first_bundle.ontology.canonical_country_uri}{condition.term_id}"

    def _matched_classes_for_subject(
        self,
        subject: str,
        subject_types: dict[str, set[str]],
    ) -> list[str]:
        matched_terms = {
            term["term_id"]
            for uri, term in self.dataset.catalog.items()
            if term["kind"] == "class"
            and any(
                self.dataset.evaluator.is_same_or_subclass(candidate_uri, uri)
                for candidate_uri in subject_types.get(subject, set())
            )
        }
        return sorted(matched_terms)

    def _matched_properties_for_subject(
        self,
        subject: str,
        subject_predicates: dict[str, set[str]],
    ) -> list[str]:
        return sorted(
            {
                self.dataset.catalog[uri]["term_id"]
                for uri in subject_predicates.get(subject, set())
                if uri in self.dataset.catalog and self.dataset.catalog[uri]["kind"] == "property"
            }
        )

    def _matched_property_sets_for_subject(
        self,
        subject: str,
        *,
        subject_types: dict[str, set[str]],
        subject_predicates: dict[str, set[str]],
    ) -> list[str]:
        return sorted(
            {
                property_set_id
                for uri in sorted(subject_types.get(subject, set()) | subject_predicates.get(subject, set()))
                if uri in self.dataset.catalog
                for property_set_id in self.dataset.catalog[uri]["property_set_term_ids"]
            }
        )

    def _canonical_country_uri_for_uri(self, uri: str) -> str:
        for bundle in self.dataset.bundles:
            if uri.startswith(bundle.ontology.canonical_country_uri):
                return bundle.ontology.canonical_country_uri
        return self.dataset.bundles[0].ontology.canonical_country_uri
