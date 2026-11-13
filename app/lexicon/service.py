from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Any

from rdflib import RDF, RDFS, Graph, Literal, URIRef
from rdflib.namespace import OWL, XSD

from app.graphs_and_checks.publisher import fetch_rule_ttl
from app.lexicon.query_runtime import (
    LexiconMergedResolutionDataset,
    LexiconOntologyBundle,
    LexiconOntologyContext,
    LexiconQueryRuntime,
)
from app.rules.composer import check_ruleset_completeness
from app.rules.decompiler import decompile_ruleset_ttl
from app.rules.expressions import ExpressionError, parse_expression, referenced_paths
from app.rules.paths import expand_curie, expand_property_path, is_ref_value, ref_path, split_property_path
from app.schemas.lexicon import (
    LexiconBranchReference,
    LexiconCatalogCompareRequest,
    LexiconCatalogCompareResponse,
    LexiconCatalogRelatedTermsResponse,
    LexiconCatalogResolveRequest,
    LexiconCatalogResolveResponse,
    LexiconCatalogSimilarityMatrixCell,
    LexiconCatalogSimilarityMatrixRequest,
    LexiconCatalogSimilarityMatrixResponse,
    LexiconCatalogSimilarityRequest,
    LexiconCatalogSimilarityResponse,
    LexiconCatalogTermDetail,
    LexiconCatalogTermResponse,
    LexiconCatalogTermsResponse,
    LexiconCatalogTermSummary,
    LexiconConformityCheckResponse,
    LexiconConformityIssue,
    LexiconDerivedProperty,
    LexiconEntityInput,
    LexiconExecutionProfile,
    LexiconExecutionProfileNode,
    LexiconExecutionTarget,
    LexiconGraphEvidenceSummaryRequest,
    LexiconGraphEvidenceSummaryResponse,
    LexiconHandshake,
    LexiconMappedTermReference,
    LexiconMapping,
    LexiconMappingEvaluateRequest,
    LexiconMappingEvaluateResponse,
    LexiconMappingEvaluationClauseResult,
    LexiconMappingValidationRequest,
    LexiconMappingValidationResponse,
    LexiconNeededProperty,
    LexiconNextNeededResponse,
    LexiconProfileGraphAssociation,
    LexiconProfileGraphCoverageResponse,
    LexiconProfileMappingCandidate,
    LexiconProfileMappingCandidatesResponse,
    LexiconProfileRulesCoverageResponse,
    LexiconProfileSummaryResponse,
    LexiconPropertyRequirement,
    LexiconReconstructionResponse,
    LexiconRuleCoverageItem,
    LexiconRuleOntologySimilarityRequest,
    LexiconRuleOntologySimilarityResponse,
    LexiconRuleOntologySimilarityRuleResult,
    LexiconRuleOntologySimilarityStats,
    LexiconRuleOntologySimilaritySummary,
    LexiconRuleSimilarityMatch,
    LexiconRuleSimilaritySourceTerm,
    LexiconRuleSimilarityTermResult,
    LexiconRuleTermValidationFieldResult,
    LexiconRuleTermValidationIssue,
    LexiconRuleTermValidationMatch,
    LexiconRuleTermValidationNodeResult,
    LexiconRuleTermValidationRequest,
    LexiconRuleTermValidationResponse,
    LexiconRuleTermValidationStats,
    LexiconSelectedOntology,
    LexiconSimilarityComponentScore,
    LexiconSourceEntityPattern,
    LexiconSourcePattern,
    LexiconSourcePatternClause,
    LexiconSourceTermCondition,
    LexiconTargetAssertion,
)
from app.schemas.rules import RuleDraft
from app.storage.building_repository import BuildingRepository
from app.storage.graph_store import GraphStoreNotFoundError
from app.storage.ontology_repository import OntologyRepository

LEXICON_GRAPH_BASE_URI = "https://firebim.org/graphs/lexicon/"
ONTOLOGY_META_HAS_PROPERTY_URI = "https://firebim.org/ontology-meta#hasProperty"
ONTOLOGY_META_BELONGS_TO_CLASS_URI = "https://firebim.org/ontology-meta#belongsToClass"
ONTOLOGY_META_GROUP_OF_PROPERTIES_URI = "https://firebim.org/ontology-meta#groupOfProperties"
ONTOLOGY_META_UNIT_URI = "https://firebim.org/ontology-meta#unit"
ONTOLOGY_META_RELATED_PROPERTY_URI = "https://firebim.org/ontology-meta#relatedProperty"


@dataclass(frozen=True)
class LexiconOntologyCacheEntry:
    bundle: LexiconOntologyBundle
    created_at: float


def _aggregates_by_path(tree: Any) -> dict[str, str]:
    """`{path: aggregate}` for every `agg($path)` in a parsed expression."""
    found: dict[str, str] = {}

    def walk(node: Any) -> None:
        if node.kind == "agg" and node.children and node.children[0].kind == "ref":
            found[str(node.children[0].value)] = str(node.value)
        for child in node.children:
            walk(child)

    walk(tree)
    return found


class LexiconService:
    CACHE_MAX_ENTRIES = 16
    CACHE_TTL_SECONDS = 300.0

    def __init__(
        self,
        ontology_repo: OntologyRepository | None = None,
        building_repo: BuildingRepository | None = None,
    ) -> None:
        self.ontology_repo = ontology_repo or OntologyRepository()
        self.building_repo = building_repo or BuildingRepository()
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
            for term in self._unique_catalog_terms(bundle.catalog)
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
                property_set_term_ids=term["property_set_term_ids"],
                belongs_to_class_term_ids=term["belongs_to_class_term_ids"],
                related_property_term_ids=term["related_property_term_ids"],
                unit=term["unit"],
                is_property_set=term["is_property_set"],
                is_predefined_type=term["is_predefined_type"],
            ),
        )

    def resolve_catalog_terms(self, payload: LexiconCatalogResolveRequest) -> LexiconCatalogResolveResponse:
        runtime = self._build_query_runtime(payload.selected_ontologies)
        return runtime.resolve_terms(terms=payload.terms, limit_per_term=payload.limit_per_term)

    def compare_catalog_terms(self, payload: LexiconCatalogCompareRequest) -> LexiconCatalogCompareResponse:
        left_term = self._get_catalog_term_detail_from_reference(payload.left)
        right_term = self._get_catalog_term_detail_from_reference(payload.right)
        runtime = self._build_query_runtime(
            [
                LexiconSelectedOntology(
                    ontology_family=payload.left.ontology_family,
                    country=payload.left.country,
                    version=payload.left.version,
                ),
                LexiconSelectedOntology(
                    ontology_family=payload.right.ontology_family,
                    country=payload.right.country,
                    version=payload.right.version,
                ),
            ]
        )
        same_kind = left_term.kind == right_term.kind
        notes: list[str] = []
        if same_kind and left_term.kind == "class":
            notes.append("Class-to-class mapping is structurally allowed.")
        elif same_kind and left_term.kind == "property":
            notes.append("Property-to-property mapping is structurally allowed.")
        else:
            notes.append("Mixed class/property mappings are not structurally allowed.")
        if left_term.is_property_set or right_term.is_property_set:
            notes.append("Property sets are modeled as classes and may need special frontend treatment.")
        if left_term.is_predefined_type or right_term.is_predefined_type:
            notes.append("Predefined types still count as classes for structural validation.")
        if (
            same_kind
            and left_term.kind == "class"
            and runtime.dataset.evaluator.shared_hierarchy(
                left_term.uri,
                right_term.uri,
            )
        ):
            notes.append("The concepts share a class hierarchy in the merged ontology resolution context.")

        return LexiconCatalogCompareResponse(
            left_term=self._summary_from_catalog_term(left_term.model_dump()),
            right_term=self._summary_from_catalog_term(right_term.model_dump()),
            left_kind=left_term.kind,
            right_kind=right_term.kind,
            same_kind=same_kind,
            compatible_for_mapping=same_kind,
            left_is_property_set=left_term.is_property_set,
            right_is_property_set=right_term.is_property_set,
            left_parent_term_ids=left_term.parent_term_ids,
            right_parent_term_ids=right_term.parent_term_ids,
            notes=notes,
        )

    def score_catalog_similarity(
        self,
        payload: LexiconCatalogSimilarityRequest,
    ) -> LexiconCatalogSimilarityResponse:
        left_term = self._get_catalog_term_detail_from_reference(payload.left)
        right_term = self._get_catalog_term_detail_from_reference(payload.right)
        runtime = self._build_query_runtime(
            [
                LexiconSelectedOntology(
                    ontology_family=payload.left.ontology_family,
                    country=payload.left.country,
                    version=payload.left.version,
                ),
                LexiconSelectedOntology(
                    ontology_family=payload.right.ontology_family,
                    country=payload.right.country,
                    version=payload.right.version,
                ),
            ]
        )
        components = self._similarity_components(left_term, right_term, runtime.dataset)
        score = round(sum(item.score for item in components) / len(components), 3) if components else 0.0
        notes = [item.note for item in components if item.note]
        return LexiconCatalogSimilarityResponse(
            left_term=self._summary_from_catalog_term(left_term.model_dump()),
            right_term=self._summary_from_catalog_term(right_term.model_dump()),
            score=score,
            components=components,
            notes=notes,
        )

    def score_catalog_similarity_matrix(
        self,
        payload: LexiconCatalogSimilarityMatrixRequest,
    ) -> LexiconCatalogSimilarityMatrixResponse:
        cells: list[LexiconCatalogSimilarityMatrixCell] = []
        for left in payload.left_terms:
            for right in payload.right_terms:
                similarity = self.score_catalog_similarity(LexiconCatalogSimilarityRequest(left=left, right=right))
                cells.append(
                    LexiconCatalogSimilarityMatrixCell(
                        left_term_id=similarity.left_term.term_id,
                        right_term_id=similarity.right_term.term_id,
                        score=similarity.score,
                    )
                )
        return LexiconCatalogSimilarityMatrixResponse(
            left_count=len(payload.left_terms),
            right_count=len(payload.right_terms),
            cells=cells,
        )

    def score_rule_ontology_similarity(
        self,
        payload: LexiconRuleOntologySimilarityRequest,
    ) -> LexiconRuleOntologySimilarityResponse:
        normalized_rules = self._load_rules(
            rule_id=payload.rule_id,
            rule_ids=payload.rule_ids,
            rule=payload.rule,
            rules=payload.rules,
        )
        selected_ontologies = self._resolve_similarity_selected_ontologies(payload.selected_ontologies)
        bundles = [
            self._resolve_ontology_bundle(
                ontology_family=selection.ontology_family,
                country=selection.country,
                version=selection.version,
            )
            for selection in selected_ontologies
        ]
        dataset = LexiconQueryRuntime.from_bundles(bundles).dataset

        results: list[LexiconRuleOntologySimilarityRuleResult] = []
        rule_ontology_summaries: list[LexiconRuleOntologySimilaritySummary] = []
        total_target_classes = 0
        total_property_paths = 0
        total_matches = 0

        for normalized_rule in normalized_rules:
            target_terms = self._collect_rule_term_occurrences(normalized_rule, field_name="target_class")
            property_terms = self._collect_rule_term_occurrences(normalized_rule, field_name="property_path")
            target_class_entries = [
                self._score_rule_term_against_ontologies(
                    term_kind="class",
                    term_value=term_value,
                    node_ids=node_ids,
                    bundles=bundles,
                    dataset=dataset,
                    limit_per_term=payload.limit_per_term,
                    compact_terms=payload.compact_terms,
                )
                for term_value, node_ids in target_terms
            ]
            property_path_entries = [
                self._score_rule_term_against_ontologies(
                    term_kind="property",
                    term_value=term_value,
                    node_ids=node_ids,
                    bundles=bundles,
                    dataset=dataset,
                    limit_per_term=payload.limit_per_term,
                    compact_terms=payload.compact_terms,
                )
                for term_value, node_ids in property_terms
            ]
            target_classes = [item[0] for item in target_class_entries]
            property_paths = [item[0] for item in property_path_entries]
            results.append(
                LexiconRuleOntologySimilarityRuleResult(
                    rule_id=normalized_rule["id"],
                    target_classes=target_classes,
                    property_paths=property_paths,
                )
            )

            total_target_classes += len(target_classes)
            total_property_paths += len(property_paths)
            total_matches += sum(item.match_count for item in target_classes)
            total_matches += sum(item.match_count for item in property_paths)
            rule_ontology_summaries.extend(
                self._build_rule_ontology_summaries(
                    rule_id=normalized_rule["id"],
                    target_class_entries=target_class_entries,
                    property_path_entries=property_path_entries,
                    selections=selected_ontologies,
                )
            )

        return LexiconRuleOntologySimilarityResponse(
            rule_ids=[item["id"] for item in normalized_rules],
            selected_ontologies=selected_ontologies,
            limit_per_term=payload.limit_per_term,
            compact_terms=payload.compact_terms,
            results=results,
            rule_ontology_summaries=rule_ontology_summaries,
            stats=LexiconRuleOntologySimilarityStats(
                rule_count=len(normalized_rules),
                ontology_count=len(selected_ontologies),
                total_target_classes=total_target_classes,
                total_property_paths=total_property_paths,
                total_source_terms=total_target_classes + total_property_paths,
                total_matches=total_matches,
                rule_ontology_pairs=len(normalized_rules) * len(selected_ontologies),
            ),
        )

    def validate_rule_terms(
        self,
        payload: LexiconRuleTermValidationRequest,
    ) -> LexiconRuleTermValidationResponse:
        """Perform exact, kind-aware validation of rule terms in selected ontology catalogs."""
        normalized_rules = self._load_rules(
            rule_id=payload.rule_id,
            rule_ids=payload.rule_ids,
            rule=payload.rule,
            rules=payload.rules,
        )
        selected_ontologies = self._resolve_similarity_selected_ontologies(payload.selected_ontologies)
        bundles = [
            self._resolve_ontology_bundle(
                ontology_family=selection.ontology_family,
                country=selection.country,
                version=selection.version,
            )
            for selection in selected_ontologies
        ]
        runtime = LexiconQueryRuntime.from_bundles(bundles)

        nodes: list[LexiconRuleTermValidationNodeResult] = []
        issues: list[LexiconRuleTermValidationIssue] = []
        validated_field_count = 0

        selected_names = ", ".join(
            f"{sel.ontology_family}/{sel.country} {sel.version}".strip() for sel in selected_ontologies
        )

        def check_term(
            *, rule_id: str, node_id: str, field: str, expected_kind: str, value: str, context: str | None
        ) -> tuple[list[LexiconRuleTermValidationMatch], LexiconRuleTermValidationIssue | None]:
            resolved = runtime.resolve_term_matches(value)
            expected_uris = sorted(resolved[f"{expected_kind}_matches"])
            opposite_kind = "property" if expected_kind == "class" else "class"
            opposite_uris = sorted(resolved[f"{opposite_kind}_matches"])
            matches = self._rule_term_validation_matches(runtime, expected_uris)
            if matches:
                return matches, None
            code = "wrong_term_kind" if opposite_uris else "unknown_term"
            where = f" (step of {context})" if context and context != value else ""
            if code == "wrong_term_kind":
                message = (
                    f"'{value}'{where} is an ontology {opposite_kind}, not an ontology {expected_kind}. "
                    f"Pick a {expected_kind} instead."
                )
            else:
                message = (
                    f"'{value}'{where} is not a {expected_kind} in the selected ontologies ({selected_names}). "
                    "Pick a term from the suggestions, tick the ontology that defines it on the right, "
                    "or accept the term if it is intentional."
                )
            return matches, LexiconRuleTermValidationIssue(
                rule_id=rule_id,
                node_id=node_id,
                field=field,  # type: ignore[arg-type]
                expected_kind=expected_kind,  # type: ignore[arg-type]
                value=value,
                code=code,  # type: ignore[arg-type]
                message=message,
                context=context,
            )

        def path_steps(value: str) -> list[str]:
            return [step.lstrip("^") for step in split_property_path(value) if step.lstrip("^")]

        for normalized_rule in normalized_rules:
            rule_id = str(normalized_rule.get("id") or "")
            for element in normalized_rule.get("elements", []):
                element_type = element.get("element_type") or element.get("node_type")
                if element_type not in ("decision", "derive"):
                    continue

                node_id = str(element.get("id") or "")
                config = element.get("config") if isinstance(element.get("config"), dict) else {}
                accepted = {str(t) for t in (config.get("accepted_terms") or []) if t}
                prefixes = config.get("prefixes") if isinstance(config.get("prefixes"), dict) else None
                field_results: list[LexiconRuleTermValidationFieldResult] = []

                # (field, expected kind, whole value, the terms to check)
                checks: list[tuple[str, str, str, list[str]]] = []
                target_class = str(element.get("target_class") or "").strip()
                if target_class:
                    checks.append(("target_class", "class", target_class, [target_class]))
                property_path = str(element.get("property_path") or "").strip()
                if property_path:
                    checks.append(("property_path", "property", property_path, path_steps(property_path)))
                value = element.get("value")
                if is_ref_value(value):
                    ref = str(ref_path(value) or "").strip()
                    if ref:
                        checks.append(("value", "property", ref, path_steps(ref)))
                expression = str(element.get("expression") or "").strip()
                if element_type == "derive" and expression:
                    try:
                        refs = referenced_paths(parse_expression(expression))
                    except Exception:
                        refs = []
                    steps: list[str] = []
                    for ref in refs:
                        steps.extend(path_steps(ref))
                    if steps:
                        checks.append(("expression", "property", expression, steps))

                for field, expected_kind, whole, terms in checks:
                    validated_field_count += 1
                    matches: list[LexiconRuleTermValidationMatch] = []
                    issue = None
                    for term in terms:
                        term_iri = expand_curie(term, prefixes)
                        if term_iri in accepted or term in accepted:
                            continue
                        found, term_issue = check_term(
                            rule_id=rule_id,
                            node_id=node_id,
                            field=field,
                            expected_kind=expected_kind,
                            value=term,
                            context=whole,
                        )
                        matches.extend(found)
                        if term_issue is not None:
                            issues.append(term_issue)
                            if issue is None:
                                issue = term_issue
                    field_results.append(
                        LexiconRuleTermValidationFieldResult(
                            field=field,  # type: ignore[arg-type]
                            expected_kind=expected_kind,  # type: ignore[arg-type]
                            value=whole,
                            valid=issue is None,
                            matches=matches,
                            issue=issue,
                        )
                    )

                if field_results:
                    nodes.append(
                        LexiconRuleTermValidationNodeResult(
                            rule_id=rule_id,
                            node_id=node_id,
                            label=element.get("label"),
                            valid=all(field.valid for field in field_results),
                            fields=field_results,
                        )
                    )

        invalid_node_count = sum(not node.valid for node in nodes)
        return LexiconRuleTermValidationResponse(
            valid=not issues,
            rule_ids=[str(rule.get("id") or "") for rule in normalized_rules],
            selected_ontologies=selected_ontologies,
            nodes=nodes,
            issues=issues,
            stats=LexiconRuleTermValidationStats(
                rule_count=len(normalized_rules),
                ontology_count=len(selected_ontologies),
                validated_node_count=len(nodes),
                validated_field_count=validated_field_count,
                invalid_node_count=invalid_node_count,
                issue_count=len(issues),
            ),
        )

    @staticmethod
    def _rule_term_validation_matches(
        runtime: LexiconQueryRuntime,
        matched_uris: list[str],
    ) -> list[LexiconRuleTermValidationMatch]:
        matches: list[LexiconRuleTermValidationMatch] = []
        seen: set[tuple[str, str, str, str]] = set()
        for matched_uri in matched_uris:
            term = runtime.dataset.catalog.get(matched_uri)
            if term is None:
                continue
            for ontology_family, country, version in runtime.dataset.provenance_by_uri.get(matched_uri, []):
                key = (ontology_family, country, version, str(term["uri"]))
                if key in seen:
                    continue
                seen.add(key)
                matches.append(
                    LexiconRuleTermValidationMatch(
                        ontology_family=ontology_family,
                        country=country,
                        version=version,
                        term_id=str(term["term_id"]),
                        uri=str(term["uri"]),
                    )
                )
        return matches

    def summarize_profile(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
        selected_ontologies: list[LexiconSelectedOntology] | None = None,
        canonical_target_ontology: LexiconSelectedOntology | None = None,
        rule_id: str | None = None,
        rule_ids: list[str] | None = None,
        rule: RuleDraft | None = None,
        rules: list[RuleDraft] | None = None,
    ) -> LexiconProfileSummaryResponse:
        ontology, profile, _rule_profiles, resolved_rule_ids = self.derive_profile(
            ontology_family=ontology_family,
            country=country,
            version=version,
            selected_ontologies=selected_ontologies,
            canonical_target_ontology=canonical_target_ontology,
            rule_id=rule_id,
            rule_ids=rule_ids,
            rule=rule,
            rules=rules,
        )
        resolved = self.resolve_selected_ontologies(
            ontology_family=ontology_family,
            country=country,
            version=version,
            selected_ontologies=selected_ontologies,
            canonical_target_ontology=canonical_target_ontology,
        )
        property_requirement_count = sum(len(target.property_requirements) for target in profile.targets)
        return LexiconProfileSummaryResponse(
            rule_id=profile.rule_id,
            rule_ids=resolved_rule_ids,
            ontology_family=ontology.ontology_family,
            country=ontology.country,
            version=ontology.version,
            selected_ontologies=resolved["selected_ontologies"],
            canonical_target_ontology=resolved["canonical_target_ontology"],
            entry_node_id=profile.entry_node_id,
            node_count=profile.node_count,
            target_count=len(profile.targets),
            property_requirement_count=property_requirement_count,
            complete=profile.complete,
            open=profile.open,
            broken=profile.broken,
            publish_ready=profile.complete and not profile.open and not profile.broken,
            target_classes=profile.target_classes,
            property_paths=profile.property_paths,
            warnings=profile.warnings,
        )

    def list_related_catalog_terms(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
        term_id: str,
        relation: str,
    ) -> LexiconCatalogRelatedTermsResponse:
        bundle = self._resolve_ontology_bundle(
            ontology_family=ontology_family,
            country=country,
            version=version,
        )
        normalized_uri = self._normalize_term_uri(term_id, bundle.ontology.canonical_country_uri)
        term = bundle.catalog.get(normalized_uri)
        if term is None:
            raise GraphStoreNotFoundError(f"Lexicon term not found: {term_id}")

        relation_key_map = {
            "properties": "property_term_ids",
            "property_sets": "property_set_term_ids",
            "classes": "belongs_to_class_term_ids",
            "related_properties": "related_property_term_ids",
            "children": "child_term_ids",
            "parents": "parent_term_ids",
        }
        relation_key = relation_key_map.get(relation)
        if relation_key is None:
            raise ValueError(f"Unsupported catalog relation: {relation}")

        related_terms = [
            self._summary_from_catalog_term(
                bundle.catalog[self._normalize_term_uri(related_term_id, bundle.ontology.canonical_country_uri)]
            )
            for related_term_id in term.get(relation_key, [])
            if self._normalize_term_uri(related_term_id, bundle.ontology.canonical_country_uri) in bundle.catalog
        ]
        return LexiconCatalogRelatedTermsResponse(
            ontology_family=bundle.ontology.ontology_family,
            country=bundle.ontology.country,
            version=bundle.ontology.version,
            canonical_family_uri=bundle.ontology.canonical_family_uri,
            canonical_country_uri=bundle.ontology.canonical_country_uri,
            count=len(related_terms),
            terms=related_terms,
        )

    def summarize_graph_evidence(
        self,
        payload: LexiconGraphEvidenceSummaryRequest,
    ) -> LexiconGraphEvidenceSummaryResponse:
        graph_uri, ttl = self.building_repo.fetch_graph(payload.graph_id)
        data_graph = Graph()
        data_graph.parse(data=ttl, format="turtle")
        runtime = self._build_query_runtime(payload.selected_ontologies)
        return runtime.summarize_graph_evidence(
            graph_id=payload.graph_id,
            graph_uri=graph_uri,
            data_graph=data_graph,
        )

    def compute_profile_graph_coverage(
        self,
        *,
        profile_id: str,
        graph_id: str,
        selected_ontologies: list[LexiconSelectedOntology],
        expected_profile: LexiconExecutionProfile,
    ) -> LexiconProfileGraphCoverageResponse:
        """Follow every requirement's path on the stored graph, the way the rule engine will.

        The selected ontologies' schema subset is merged and materialised first, so instances of sub-classes count as
        the target class and inverse / sub-property declarations (fbo-dk:partOf ⇐ fbo:hasSubitem …) resolve."""
        from app.checks.derived import resolve_path_values
        from app.checks.rdfs_light import materialize, schema_subset

        graph_uri, ttl = self.building_repo.fetch_graph(graph_id)
        data_graph = Graph()
        data_graph.parse(data=ttl, format="turtle")
        merged_schemas: list[str] = []
        for selection in selected_ontologies:
            bundle = self._resolve_ontology_bundle(
                ontology_family=selection.ontology_family,
                country=selection.country,
                version=selection.version,
            )
            for triple in schema_subset(bundle.graph.serialize(format="turtle")):
                data_graph.add(triple)
            merged_schemas.append(
                f"{bundle.ontology.ontology_family}/{bundle.ontology.country}/{bundle.ontology.version}"
            )
        materialize(data_graph)

        target_instances: dict[str, int] = {}
        covered_target_classes: list[str] = []
        missing_target_classes: list[str] = []
        covered_properties: list[LexiconNeededProperty] = []
        partial_properties: list[LexiconNeededProperty] = []
        missing_properties: list[LexiconNeededProperty] = []
        matched_by_subject: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: {"classes": set(), "properties": set()}
        )

        for target in expected_profile.targets:
            instances = sorted({str(sub) for sub in data_graph.subjects(RDF.type, URIRef(target.target_class))})
            target_instances[target.target_class] = len(instances)
            (covered_target_classes if instances else missing_target_classes).append(target.target_class)
            for instance in instances:
                matched_by_subject[instance]["classes"].add(target.target_class)
            for requirement in target.property_requirements:
                segments = list(requirement.steps) or expand_property_path(requirement.property_path)
                with_value = 0
                for instance in instances:
                    if resolve_path_values(data_graph, instance, segments):
                        with_value += 1
                        matched_by_subject[instance]["properties"].add(requirement.property_path)
                needed = LexiconNeededProperty(
                    target_class=target.target_class,
                    property_path=requirement.property_path,
                    node_ids=requirement.node_ids,
                    via=requirement.via,
                    instances=len(instances),
                    with_value=with_value,
                )
                if instances and with_value == len(instances):
                    covered_properties.append(needed)
                elif with_value:
                    partial_properties.append(needed)
                else:
                    missing_properties.append(needed)

        associations = [
            LexiconProfileGraphAssociation(
                source_object_ref=subject,
                matched_target_classes=sorted(match["classes"]),
                matched_properties=sorted(match["properties"]),
            )
            for subject, match in sorted(matched_by_subject.items())
        ]
        return LexiconProfileGraphCoverageResponse(
            profile_id=profile_id,
            graph_id=graph_id,
            graph_uri=graph_uri,
            complete=not missing_target_classes and not missing_properties and not partial_properties,
            merged_schemas=merged_schemas,
            target_instances=target_instances,
            covered_target_classes=sorted(covered_target_classes),
            missing_target_classes=sorted(missing_target_classes),
            covered_properties=covered_properties,
            partial_properties=partial_properties,
            missing_properties=missing_properties,
            associations=associations,
        )

    def compute_profile_rules_coverage(
        self,
        *,
        profile_id: str,
        base_profile: LexiconExecutionProfile,
        ontology_family: str,
        country: str,
        version: str,
        selected_ontologies: list[LexiconSelectedOntology],
        canonical_target_ontology: LexiconSelectedOntology | None,
        rule_id: str | None = None,
        rule_ids: list[str] | None = None,
        rule: RuleDraft | None = None,
        rules: list[RuleDraft] | None = None,
        default_rule_ids: list[str] | None = None,
        stored_rule_profiles: list[LexiconExecutionProfile] | None = None,
    ) -> LexiconProfileRulesCoverageResponse:
        effective_rule_ids = list(rule_ids or [])
        if not any([rule_id, effective_rule_ids, rule, rules]) and stored_rule_profiles:
            rule_profiles = stored_rule_profiles
            resolved_rule_ids = [item.rule_id for item in stored_rule_profiles]
        else:
            if not any([rule_id, effective_rule_ids, rule, rules]):
                effective_rule_ids = list(default_rule_ids or [])
            _ontology, aggregate_profile, rule_profiles, resolved_rule_ids = self.derive_profile(
                ontology_family=ontology_family,
                country=country,
                version=version,
                selected_ontologies=selected_ontologies,
                canonical_target_ontology=canonical_target_ontology,
                rule_id=rule_id,
                rule_ids=effective_rule_ids,
                rule=rule,
                rules=rules,
            )
            _ = aggregate_profile
        base_targets = {target.target_class for target in base_profile.targets}
        base_properties = {
            (target.target_class, requirement.property_path)
            for target in base_profile.targets
            for requirement in target.property_requirements
        }
        fully_supported_rule_ids: list[str] = []
        partially_supported_rules: list[LexiconRuleCoverageItem] = []
        unsupported_rule_ids: list[str] = []

        for rule_profile in rule_profiles:
            missing_target_classes = sorted(
                {target.target_class for target in rule_profile.targets if target.target_class not in base_targets}
            )
            missing_properties = [
                LexiconNeededProperty(
                    target_class=target.target_class,
                    property_path=requirement.property_path,
                    node_ids=requirement.node_ids,
                )
                for target in rule_profile.targets
                for requirement in target.property_requirements
                if (target.target_class, requirement.property_path) not in base_properties
            ]
            supported = not missing_target_classes or not missing_properties
            complete = not missing_target_classes and not missing_properties
            if complete:
                fully_supported_rule_ids.append(rule_profile.rule_id)
            elif supported:
                partially_supported_rules.append(
                    LexiconRuleCoverageItem(
                        rule_id=rule_profile.rule_id,
                        supported=True,
                        complete=False,
                        missing_target_classes=missing_target_classes,
                        missing_properties=missing_properties,
                    )
                )
            else:
                unsupported_rule_ids.append(rule_profile.rule_id)

        return LexiconProfileRulesCoverageResponse(
            profile_id=profile_id,
            checked_rule_ids=resolved_rule_ids,
            fully_supported_rule_ids=fully_supported_rule_ids,
            partially_supported_rules=partially_supported_rules,
            unsupported_rule_ids=unsupported_rule_ids,
        )

    def find_profile_mapping_candidates(
        self,
        *,
        profile_id: str,
        profile: LexiconExecutionProfile,
        mappings: list[LexiconMapping],
    ) -> LexiconProfileMappingCandidatesResponse:
        relevant_terms = set(profile.target_classes) | set(profile.property_paths)
        candidates: list[LexiconProfileMappingCandidate] = []
        for mapping in mappings:
            matched_terms: set[str] = set()
            if mapping.source_term and mapping.source_term.term_id in relevant_terms:
                matched_terms.add(mapping.source_term.term_id)
            if mapping.target_term and mapping.target_term.term_id in relevant_terms:
                matched_terms.add(mapping.target_term.term_id)
            for assertion in mapping.target_assertions:
                if assertion.term_id in relevant_terms:
                    matched_terms.add(assertion.term_id)
            if mapping.source_pattern is not None:
                for condition in self._iter_source_pattern_conditions(mapping.source_pattern):
                    if condition.term_id in relevant_terms:
                        matched_terms.add(condition.term_id)
            if not matched_terms:
                continue
            relevance = "direct" if len(matched_terms) >= 2 else "partial"
            candidates.append(
                LexiconProfileMappingCandidate(
                    mapping_id=mapping.id,
                    name=mapping.name,
                    mapping_kind=mapping.mapping_kind,
                    relevance=relevance,
                    matched_terms=sorted(matched_terms),
                )
            )
        candidates.sort(key=lambda item: (item.relevance != "direct", item.name.lower(), item.mapping_id))
        return LexiconProfileMappingCandidatesResponse(
            profile_id=profile_id,
            count=len(candidates),
            mappings=candidates,
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

    def evaluate_mapping(
        self,
        *,
        payload: LexiconMappingEvaluateRequest,
        mapping: LexiconMappingValidationRequest,
    ) -> LexiconMappingEvaluateResponse:
        graph_uri, ttl = self.building_repo.fetch_graph(payload.graph_id)
        data_graph = Graph()
        data_graph.parse(data=ttl, format="turtle")
        runtime = self._build_query_runtime(self._selected_ontologies_for_mapping(mapping))
        subject_types, subject_predicates = runtime.build_subject_indexes(data_graph)

        clause_evaluation: LexiconMappingEvaluationClauseResult | None = None
        matched_source_objects: list[str] = []
        missing_conditions: list[str] = []
        warnings: list[str] = []

        if mapping.mapping_kind == "class_to_class" and mapping.source_term is not None:
            source_uri = self._resolve_reference_uri(mapping.source_term)
            matched_subjects = runtime.dataset.evaluator.subjects_for_target_class(subject_types, source_uri)
            clause_evaluation = LexiconMappingEvaluationClauseResult(
                clause_type="source_term",
                matched=bool(matched_subjects),
                matched_source_objects=matched_subjects,
                missing_conditions=[] if matched_subjects else [f"Missing class: {mapping.source_term.term_id}"],
            )
        elif mapping.mapping_kind == "property_to_property" and mapping.source_term is not None:
            source_uri = self._resolve_reference_uri(mapping.source_term)
            matched_subjects = sorted(
                subject for subject, predicates in subject_predicates.items() if source_uri in predicates
            )
            clause_evaluation = LexiconMappingEvaluationClauseResult(
                clause_type="source_term",
                matched=bool(matched_subjects),
                matched_source_objects=matched_subjects,
                missing_conditions=[] if matched_subjects else [f"Missing property: {mapping.source_term.term_id}"],
            )
        elif mapping.mapping_kind == "semantic_assertion" and mapping.source_pattern is not None:
            clause_evaluation = runtime.evaluate_source_pattern(
                pattern=mapping.source_pattern,
                subject_types=subject_types,
                subject_predicates=subject_predicates,
            )
            warnings.append("Semantic assertion evaluation checks source-side presence only.")
        else:
            raise ValueError(f"Unsupported mapping payload for evaluation: {mapping.mapping_kind}")

        matched_source_objects = clause_evaluation.matched_source_objects if clause_evaluation is not None else []
        missing_conditions = clause_evaluation.missing_conditions if clause_evaluation is not None else []
        return LexiconMappingEvaluateResponse(
            graph_id=payload.graph_id,
            graph_uri=graph_uri,
            mapping_kind=mapping.mapping_kind,
            matched=bool(clause_evaluation and clause_evaluation.matched),
            matched_source_objects=matched_source_objects,
            missing_conditions=missing_conditions,
            clause_evaluation=clause_evaluation,
            warnings=warnings,
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

    def resolve_selected_ontologies(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
        selected_ontologies: list[LexiconSelectedOntology] | None = None,
        canonical_target_ontology: LexiconSelectedOntology | None = None,
    ) -> dict[str, Any]:
        requested = list(selected_ontologies or [])
        if not requested:
            requested = [
                LexiconSelectedOntology(
                    ontology_family=ontology_family,
                    country=country,
                    version=version,
                )
            ]
        if canonical_target_ontology is None:
            canonical_target_ontology = LexiconSelectedOntology(
                ontology_family=ontology_family,
                country=country,
                version=version,
            )

        requested.append(canonical_target_ontology)
        resolved: list[LexiconSelectedOntology] = []
        seen: set[tuple[str, str, str]] = set()
        for selection in requested:
            metadata = self.ontology_repo.fetch_version_metadata(
                selection.ontology_family,
                selection.country,
                selection.version,
            )
            key = (selection.ontology_family, selection.country, metadata.version)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(
                LexiconSelectedOntology(
                    ontology_family=selection.ontology_family,
                    country=selection.country,
                    version=metadata.version,
                    canonical_family_uri=self.ontology_repo.canonical_family_uri(selection.ontology_family),
                    canonical_country_uri=self.ontology_repo.canonical_country_uri(
                        selection.ontology_family, selection.country
                    ),
                )
            )

        resolved_canonical = next(
            item
            for item in resolved
            if item.ontology_family == canonical_target_ontology.ontology_family
            and item.country == canonical_target_ontology.country
        )
        return {
            "selected_ontologies": resolved,
            "canonical_target_ontology": resolved_canonical,
        }

    def derive_profile(
        self,
        *,
        ontology_family: str,
        country: str,
        version: str,
        selected_ontologies: list[LexiconSelectedOntology] | None = None,
        canonical_target_ontology: LexiconSelectedOntology | None = None,
        rule_id: str | None = None,
        rule_ids: list[str] | None = None,
        rule: RuleDraft | None = None,
        rules: list[RuleDraft] | None = None,
    ) -> tuple[LexiconOntologyContext, LexiconExecutionProfile, list[LexiconExecutionProfile], list[str]]:
        resolved_selection = self.resolve_selected_ontologies(
            ontology_family=ontology_family,
            country=country,
            version=version,
            selected_ontologies=selected_ontologies,
            canonical_target_ontology=canonical_target_ontology,
        )
        selected_bundles = [
            self._resolve_ontology_bundle(
                ontology_family=selection.ontology_family,
                country=selection.country,
                version=selection.version,
            )
            for selection in resolved_selection["selected_ontologies"]
        ]
        runtime = LexiconQueryRuntime.from_bundles(selected_bundles)
        canonical_selection = resolved_selection["canonical_target_ontology"]
        bundle = self._resolve_ontology_bundle(
            ontology_family=canonical_selection.ontology_family,
            country=canonical_selection.country,
            version=canonical_selection.version,
        )
        normalized_rules = self._load_rules(rule_id=rule_id, rule_ids=rule_ids, rule=rule, rules=rules)
        rule_profiles = [self._derive_profile_from_rule_dict(item, runtime) for item in normalized_rules]
        aggregate_profile = self._aggregate_rule_profiles(rule_profiles)
        return bundle.ontology, aggregate_profile, rule_profiles, [item["id"] for item in normalized_rules]

    def _derive_profile_from_rule_dict(
        self,
        normalized_rule: dict,
        runtime: LexiconQueryRuntime,
    ) -> LexiconExecutionProfile:
        completeness = check_ruleset_completeness(self._to_composer_rule(normalized_rule))

        property_requirements_by_target: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        node_models: list[LexiconExecutionProfileNode] = []
        ontology_warnings: list[str] = []

        derived_by: dict[str, list[str]] = defaultdict(list)  # written property -> derive node ids
        derived_meta: dict[str, dict[str, Any]] = {}
        for item in normalized_rule["elements"]:
            if item.get("element_type") == "derive" and item.get("property_path"):
                written = str(item["property_path"])
                derived_by[written].append(item["id"])
                meta = derived_meta.setdefault(
                    written, {"target_class": item.get("target_class"), "expressions": [], "read_by": []}
                )
                if item.get("expression") is not None:
                    meta["expressions"].append(str(item["expression"]))

        def check_steps(node_id: str, path: str, *, role: str) -> list[str]:
            """Validate every step of a (sequence) path against the selected ontologies; returns expanded steps."""
            steps = expand_property_path(path)
            for step in steps:
                term = step[1:] if step.startswith("^") else step
                matches = self._resolve_profile_term_matches(term, runtime)
                if not matches["property_matches"] and matches["class_matches"]:
                    ontology_warnings.append(
                        f"Rule node '{node_id}' uses class URI as {role}: {matches['normalized_uri']}"
                    )
                elif not matches["property_matches"]:
                    ontology_warnings.append(
                        f"Rule node '{node_id}' uses {role} not declared as property in selected ontologies: "
                        f"{matches['normalized_uri']}"
                    )
                elif len(matches["property_matches"]) > 1:
                    ontology_warnings.append(
                        f"Rule node '{node_id}' {role} matches multiple selected ontologies: "
                        f"{', '.join(sorted(matches['property_matches']))}"
                    )
            return steps

        def require(
            target_class: str, path: str, node_id: str, *, operator: str | None, value: Any, aggregate: str | None
        ) -> None:
            steps = expand_property_path(path)
            if len(steps) == 1 and steps[0] in derived_by:
                derived_meta[steps[0]]["read_by"].append(node_id)  # produced by this rule, not a fact of the graph
                return
            bucket = property_requirements_by_target[target_class].setdefault(
                path,
                {"node_ids": [], "operators": [], "aggregates": [], "sample_values": [], "steps": steps},
            )
            bucket["node_ids"].append(node_id)
            if operator:
                bucket["operators"].append(str(operator))
            if aggregate:
                bucket["aggregates"].append(aggregate)
            if is_ref_value(value):
                bucket["sample_values"].append(f"$ref {ref_path(value)}")
                referenced = ref_path(value) or ""
                if expand_property_path(referenced)[:1] and expand_property_path(referenced)[0] in derived_by:
                    derived_meta[expand_property_path(referenced)[0]]["read_by"].append(node_id)
                else:
                    require(target_class, referenced, node_id, operator=None, value=None, aggregate=None)
            elif value is not None:
                bucket["sample_values"].append(str(value))

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
            element_type = item.get("element_type")
            if target_class:
                target_matches = self._resolve_profile_term_matches(target_class, runtime)
                if not target_matches["class_matches"] and target_matches["property_matches"]:
                    ontology_warnings.append(
                        "Rule node "
                        f"'{item['id']}' uses property URI as target_class: {target_matches['normalized_uri']}"
                    )
                elif not target_matches["class_matches"]:
                    ontology_warnings.append(
                        "Rule node "
                        f"'{item['id']}' uses target_class not declared as class in selected ontologies: "
                        f"{target_matches['normalized_uri']}"
                    )
                elif len(target_matches["class_matches"]) > 1:
                    ontology_warnings.append(
                        "Rule node "
                        f"'{item['id']}' target_class matches multiple selected ontologies: "
                        f"{', '.join(sorted(target_matches['class_matches']))}"
                    )
            if property_path:
                check_steps(item["id"], str(property_path), role="property_path")
            if element_type == "derive":
                # the written property is an output; the paths in the expression are the inputs
                expression = item.get("expression")
                if expression is not None and target_class:
                    try:
                        tree = parse_expression(str(expression))
                    except ExpressionError as exc:
                        ontology_warnings.append(f"Rule node '{item['id']}' has an invalid expression: {exc}")
                    else:
                        aggregates_by_path = _aggregates_by_path(tree)
                        for path in referenced_paths(tree):
                            check_steps(item["id"], path, role="expression")
                            require(
                                target_class,
                                path,
                                item["id"],
                                operator=None,
                                value=None,
                                aggregate=aggregates_by_path.get(path),
                            )
                continue
            if target_class and property_path:
                require(
                    target_class,
                    str(property_path),
                    item["id"],
                    operator=item.get("operator"),
                    value=item.get("value"),
                    aggregate=None,
                )

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
                            property=(requirement["steps"] or [None])[-1],
                            via="/".join(requirement["steps"][:-1]) or None,
                            steps=list(requirement["steps"]),
                            node_ids=sorted(set(requirement["node_ids"])),
                            operators=sorted(set(requirement["operators"])),
                            aggregates=sorted(set(requirement["aggregates"])),
                            sample_values=sorted(set(requirement["sample_values"])),
                        )
                        for property_path, requirement in sorted(requirements.items())
                    ],
                    relation_requirements=sorted(
                        {
                            "/".join(requirement["steps"][:-1])
                            for requirement in requirements.values()
                            if len(requirement["steps"]) > 1
                        }
                    ),
                )
            )
        required_paths = sorted(
            {path for requirements in property_requirements_by_target.values() for path in requirements}
        )
        relation_paths = sorted({rel for target in targets for rel in target.relation_requirements})
        derived_properties = [
            LexiconDerivedProperty(
                property_path=written,
                target_class=meta.get("target_class"),
                node_ids=sorted(set(derived_by[written])),
                expressions=list(dict.fromkeys(meta["expressions"])),
                read_by_node_ids=sorted(set(meta["read_by"])),
            )
            for written, meta in sorted(derived_meta.items())
        ]

        profile = LexiconExecutionProfile(
            rule_id=normalized_rule["id"],
            rule_ids=[normalized_rule["id"]],
            entry_node_id=normalized_rule.get("entry"),
            node_count=len(normalized_rule["elements"]),
            complete=bool(completeness["complete"]),
            open=bool(completeness["open"]),
            broken=bool(completeness["broken"]),
            target_classes=sorted(
                {str(item["target_class"]) for item in normalized_rule["elements"] if item.get("target_class")}
            ),
            property_paths=required_paths,
            relation_paths=relation_paths,
            targets=targets,
            derived_properties=derived_properties,
            nodes=node_models,
            warnings=[str(error) for error in completeness.get("errors", [])]
            + [
                f"Open branch from {branch.get('node_id')}:{branch['branch']}"
                for branch in completeness.get("open_branches", [])
            ]
            + sorted(set(ontology_warnings)),
        )
        return profile

    def _aggregate_rule_profiles(
        self,
        rule_profiles: list[LexiconExecutionProfile],
    ) -> LexiconExecutionProfile:
        if not rule_profiles:
            raise ValueError("At least one rule profile is required")
        if len(rule_profiles) == 1:
            return rule_profiles[0]

        aggregated_targets: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "node_ids": set(),
                "property_requirements": defaultdict(
                    lambda: {
                        "node_ids": set(),
                        "operators": set(),
                        "aggregates": set(),
                        "sample_values": set(),
                        "steps": [],
                    }
                ),
            }
        )
        aggregated_nodes: list[LexiconExecutionProfileNode] = []
        warnings: list[str] = []

        for profile in rule_profiles:
            node_prefix = f"{profile.rule_id}::"
            for node in profile.nodes:
                prefixed_node_id = f"{node_prefix}{node.node_id}"
                branches = [
                    LexiconBranchReference(
                        branch=branch.branch,
                        target_node_id=(f"{node_prefix}{branch.target_node_id}" if branch.target_node_id else None),
                    )
                    for branch in node.branches
                ]
                aggregated_nodes.append(
                    LexiconExecutionProfileNode(
                        node_id=prefixed_node_id,
                        element_type=node.element_type,
                        label=node.label,
                        target_class=node.target_class,
                        property_path=node.property_path,
                        operator=node.operator,
                        value=node.value,
                        branches=branches,
                    )
                )
            for target in profile.targets:
                target_bucket = aggregated_targets[target.target_class]
                target_bucket["node_ids"].update(f"{node_prefix}{node_id}" for node_id in target.node_ids)
                for requirement in target.property_requirements:
                    req_bucket = target_bucket["property_requirements"][requirement.property_path]
                    req_bucket["node_ids"].update(f"{node_prefix}{node_id}" for node_id in requirement.node_ids)
                    req_bucket["operators"].update(requirement.operators)
                    req_bucket["aggregates"].update(requirement.aggregates)
                    req_bucket["sample_values"].update(requirement.sample_values)
                    req_bucket["steps"] = list(requirement.steps)
            warnings.extend(profile.warnings)

        return LexiconExecutionProfile(
            rule_id="multi-rule-profile",
            rule_ids=[profile.rule_id for profile in rule_profiles],
            entry_node_id=None,
            node_count=sum(profile.node_count for profile in rule_profiles),
            complete=all(profile.complete for profile in rule_profiles),
            open=any(profile.open for profile in rule_profiles),
            broken=any(profile.broken for profile in rule_profiles),
            target_classes=sorted({item for profile in rule_profiles for item in profile.target_classes}),
            property_paths=sorted({item for profile in rule_profiles for item in profile.property_paths}),
            relation_paths=sorted({item for profile in rule_profiles for item in profile.relation_paths}),
            targets=[
                LexiconExecutionTarget(
                    target_class=target_class,
                    node_ids=sorted(bucket["node_ids"]),
                    property_requirements=[
                        LexiconPropertyRequirement(
                            property_path=property_path,
                            property=(requirement["steps"] or [None])[-1],
                            via="/".join(requirement["steps"][:-1]) or None,
                            steps=list(requirement["steps"]),
                            node_ids=sorted(requirement["node_ids"]),
                            operators=sorted(requirement["operators"]),
                            aggregates=sorted(requirement["aggregates"]),
                            sample_values=sorted(requirement["sample_values"]),
                        )
                        for property_path, requirement in sorted(bucket["property_requirements"].items())
                    ],
                    relation_requirements=sorted(
                        {
                            "/".join(r["steps"][:-1])
                            for r in bucket["property_requirements"].values()
                            if len(r["steps"]) > 1
                        }
                    ),
                )
                for target_class, bucket in sorted(aggregated_targets.items())
            ],
            derived_properties=[
                LexiconDerivedProperty(
                    property_path=item.property_path,
                    target_class=item.target_class,
                    node_ids=[f"{profile.rule_id}::{node_id}" for node_id in item.node_ids],
                    expressions=item.expressions,
                    read_by_node_ids=[f"{profile.rule_id}::{node_id}" for node_id in item.read_by_node_ids],
                )
                for profile in rule_profiles
                for item in profile.derived_properties
            ],
            nodes=aggregated_nodes,
            warnings=list(dict.fromkeys(warnings)),
        )

    def _resolve_profile_term_matches(
        self,
        value: str,
        runtime: LexiconQueryRuntime,
    ) -> dict[str, Any]:
        return runtime.resolve_term_matches(value)

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
        expected_profile: LexiconExecutionProfile | None = None,
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
        missing_profile_target_classes: list[str] = []
        missing_profile_properties: list[LexiconNeededProperty] = []
        issues: list[LexiconConformityIssue] = []

        typed_subjects = {str(subject) for subject, _, _ in data_graph.triples((None, RDF.type, None))}
        subject_types: dict[str, set[str]] = defaultdict(set)
        subject_predicates: dict[str, set[str]] = defaultdict(set)
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
            subject_types[str(subject)].add(text)
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
            subject_predicates[str(subject)].add(predicate_text)
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

        if expected_profile is not None:
            missing_profile_target_classes, missing_profile_properties = self._profile_gaps(
                expected_profile=expected_profile,
                subject_types=subject_types,
                subject_predicates=subject_predicates,
                canonical_country_uri=bundle.ontology.canonical_country_uri,
            )

        structure_ok = not missing_type_subjects
        semantic_ok = not unknown_classes and not unknown_properties
        profile_alignment_ok = not missing_profile_target_classes and not missing_profile_properties
        execution_ready = structure_ok and semantic_ok and profile_alignment_ok and len(data_graph) > 0
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
            profile_alignment_ok=profile_alignment_ok,
            missing_profile_target_classes=missing_profile_target_classes,
            missing_profile_properties=missing_profile_properties,
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
        catalog = self._build_catalog(graph)
        catalog = self._add_catalog_term_aliases(catalog, ontology=ontology)
        declared_classes = self._add_term_uri_aliases(declared_classes, ontology=ontology)
        declared_properties = self._add_term_uri_aliases(declared_properties, ontology=ontology)
        bundle = LexiconOntologyBundle(
            ontology=ontology,
            graph=graph,
            catalog=catalog,
            declared_classes=declared_classes,
            declared_properties=declared_properties,
        )
        self._store_cached_bundle(cache_key, bundle)
        return bundle

    @staticmethod
    def _load_rules(
        *,
        rule_id: str | None,
        rule_ids: list[str] | None,
        rule: RuleDraft | None,
        rules: list[RuleDraft] | None,
    ) -> list[dict]:
        if rule_id:
            _graph_uri, ttl = fetch_rule_ttl(rule_id)
            decompiled = decompile_ruleset_ttl(ttl)
            return [LexiconService._normalize_rule_dict(decompiled)]
        if rule_ids:
            return [
                LexiconService._normalize_rule_dict(decompile_ruleset_ttl(fetch_rule_ttl(current_rule_id)[1]))
                for current_rule_id in rule_ids
            ]
        if rule is not None:
            return [LexiconService._normalize_rule_dict(rule.as_internal_dict())]
        if rules:
            return [LexiconService._normalize_rule_dict(item.as_internal_dict()) for item in rules]
        raise ValueError("rule payload is required when no rule identifiers are provided")

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
    def _unique_catalog_terms(catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        unique_terms: list[dict[str, Any]] = []
        seen_uris: set[str] = set()
        for term in catalog.values():
            uri = str(term["uri"])
            if uri in seen_uris:
                continue
            seen_uris.add(uri)
            unique_terms.append(term)
        return unique_terms

    @staticmethod
    def _add_catalog_term_aliases(
        catalog: dict[str, dict[str, Any]],
        *,
        ontology: LexiconOntologyContext,
    ) -> dict[str, dict[str, Any]]:
        expanded = dict(catalog)
        for uri, term in list(catalog.items()):
            for alias in LexiconService._term_uri_aliases(uri, ontology=ontology):
                expanded.setdefault(alias, term)
        return expanded

    @staticmethod
    def _add_term_uri_aliases(
        values: set[str],
        *,
        ontology: LexiconOntologyContext,
    ) -> set[str]:
        expanded = set(values)
        for uri in list(values):
            expanded.update(LexiconService._term_uri_aliases(uri, ontology=ontology))
        return expanded

    @staticmethod
    def _term_uri_aliases(
        uri: str,
        *,
        ontology: LexiconOntologyContext,
    ) -> set[str]:
        aliases: set[str] = set()
        if "#" not in uri:
            return aliases
        local_name = uri.split("#", 1)[1]
        if ontology.version_iri and uri.startswith(ontology.version_iri):
            aliases.add(f"{ontology.canonical_country_uri}{local_name}")
        if ontology.ontology_uri and ontology.version_iri and uri.startswith(ontology.ontology_uri):
            aliases.add(f"{ontology.version_iri}{local_name}")
        return aliases

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

    def _get_catalog_term_detail_from_reference(
        self,
        reference: LexiconMappedTermReference,
    ) -> LexiconCatalogTermDetail:
        response = self.get_catalog_term(
            ontology_family=reference.ontology_family,
            country=reference.country,
            version=reference.version,
            term_id=reference.uri or reference.term_id,
        )
        return response.term

    def _resolve_similarity_selected_ontologies(
        self,
        selections: list[LexiconSelectedOntology],
    ) -> list[LexiconSelectedOntology]:
        resolved: list[LexiconSelectedOntology] = []
        seen: set[tuple[str, str, str]] = set()
        for selection in selections:
            ontology = self.resolve_ontology(
                ontology_family=selection.ontology_family,
                country=selection.country,
                version=selection.version,
            )
            key = (ontology.ontology_family, ontology.country, ontology.version)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(
                LexiconSelectedOntology(
                    ontology_family=ontology.ontology_family,
                    country=ontology.country,
                    version=ontology.version,
                    canonical_family_uri=ontology.canonical_family_uri,
                    canonical_country_uri=ontology.canonical_country_uri,
                )
            )
        return resolved

    @staticmethod
    def _collect_rule_term_occurrences(
        normalized_rule: dict,
        *,
        field_name: str,
    ) -> list[tuple[str, list[str]]]:
        occurrences: dict[str, set[str]] = defaultdict(set)
        for element in normalized_rule.get("elements", []):
            value = element.get(field_name)
            if not value:
                continue
            occurrences[str(value)].add(str(element.get("id") or ""))
        return [(term_value, sorted(node_ids)) for term_value, node_ids in sorted(occurrences.items())]

    def _score_rule_term_against_ontologies(
        self,
        *,
        term_kind: str,
        term_value: str,
        node_ids: list[str],
        bundles: list[LexiconOntologyBundle],
        dataset: LexiconMergedResolutionDataset,
        limit_per_term: int,
        compact_terms: bool,
    ) -> tuple[LexiconRuleSimilarityTermResult, dict[tuple[str, str, str], float]]:
        source_term = self._build_rule_source_term(
            term_kind=term_kind,
            term_value=term_value,
            node_ids=node_ids,
            compact_terms=compact_terms,
        )
        source_detail = self._resolve_rule_source_term_detail(
            term_kind=term_kind,
            term_value=term_value,
            bundles=bundles,
        )
        candidates: list[LexiconRuleSimilarityMatch] = []
        top_scores_by_ontology: dict[tuple[str, str, str], float] = {}

        for bundle in bundles:
            for term in self._unique_catalog_terms(bundle.catalog):
                if term["kind"] != term_kind:
                    continue
                candidate_detail = self._catalog_term_detail_from_dict(term)
                components = self._similarity_components(source_detail, candidate_detail, dataset)
                score = round(sum(item.score for item in components) / len(components), 3) if components else 0.0
                ontology_key = (
                    bundle.ontology.ontology_family,
                    bundle.ontology.country,
                    bundle.ontology.version,
                )
                if score > top_scores_by_ontology.get(ontology_key, -1.0):
                    top_scores_by_ontology[ontology_key] = score
                candidates.append(
                    LexiconRuleSimilarityMatch(
                        ontology_family=bundle.ontology.ontology_family,
                        country=bundle.ontology.country,
                        version=bundle.ontology.version,
                        term_id=candidate_detail.term_id,
                        uri=candidate_detail.uri,
                        kind=candidate_detail.kind,
                        label=candidate_detail.label,
                        value=self._term_display_value(candidate_detail.uri, compact_terms=compact_terms),
                        full_iri=candidate_detail.uri,
                        namespace=self._term_namespace(candidate_detail.uri),
                        local_name=self._term_local_name(candidate_detail.uri),
                        score=score,
                        notes=[item.note for item in components if item.note],
                    )
                )

        candidates.sort(
            key=lambda item: (
                -item.score,
                item.ontology_family,
                item.country,
                item.version,
                item.term_id.lower(),
            )
        )
        matches: list[LexiconRuleSimilarityMatch] = []
        seen_match_keys: set[tuple[str, str, str, str]] = set()

        for bundle in bundles:
            ontology_key = (
                bundle.ontology.ontology_family,
                bundle.ontology.country,
                bundle.ontology.version,
            )
            best_match = next(
                (
                    item
                    for item in candidates
                    if (
                        item.ontology_family,
                        item.country,
                        item.version,
                    )
                    == ontology_key
                ),
                None,
            )
            if best_match is None:
                continue
            match_key = (
                best_match.ontology_family,
                best_match.country,
                best_match.version,
                best_match.uri,
            )
            if match_key in seen_match_keys:
                continue
            seen_match_keys.add(match_key)
            matches.append(best_match)
            if len(matches) >= limit_per_term:
                break

        if len(matches) < limit_per_term:
            for candidate in candidates:
                match_key = (
                    candidate.ontology_family,
                    candidate.country,
                    candidate.version,
                    candidate.uri,
                )
                if match_key in seen_match_keys:
                    continue
                seen_match_keys.add(match_key)
                matches.append(candidate)
                if len(matches) >= limit_per_term:
                    break
        return (
            LexiconRuleSimilarityTermResult(
                source_term=source_term,
                match_count=len(matches),
                matches=matches,
            ),
            top_scores_by_ontology,
        )

    def _build_rule_ontology_summaries(
        self,
        *,
        rule_id: str,
        target_class_entries: list[tuple[LexiconRuleSimilarityTermResult, dict[tuple[str, str, str], float]]],
        property_path_entries: list[tuple[LexiconRuleSimilarityTermResult, dict[tuple[str, str, str], float]]],
        selections: list[LexiconSelectedOntology],
    ) -> list[LexiconRuleOntologySimilaritySummary]:
        summaries: list[LexiconRuleOntologySimilaritySummary] = []
        for selection in selections:
            scores: list[float] = []
            matched_target_classes = 0
            matched_property_paths = 0
            ontology_key = (selection.ontology_family, selection.country, selection.version)
            for _term_result, top_scores in target_class_entries:
                if ontology_key in top_scores:
                    matched_target_classes += 1
                    scores.append(top_scores[ontology_key])
            for _term_result, top_scores in property_path_entries:
                if ontology_key in top_scores:
                    matched_property_paths += 1
                    scores.append(top_scores[ontology_key])
            summaries.append(
                LexiconRuleOntologySimilaritySummary(
                    rule_id=rule_id,
                    ontology_family=selection.ontology_family,
                    country=selection.country,
                    version=selection.version,
                    source_term_count=len(target_class_entries) + len(property_path_entries),
                    matched_source_terms=matched_target_classes + matched_property_paths,
                    matched_target_classes=matched_target_classes,
                    matched_property_paths=matched_property_paths,
                    best_score=max(scores) if scores else 0.0,
                    average_top_score=round(sum(scores) / len(scores), 3) if scores else 0.0,
                )
            )
        return summaries

    def _build_query_runtime(
        self,
        selections: list[LexiconSelectedOntology],
    ) -> LexiconQueryRuntime:
        if not selections:
            raise ValueError("At least one selected ontology is required to build a lexicon query runtime.")
        bundles = [
            self._resolve_ontology_bundle(
                ontology_family=selection.ontology_family,
                country=selection.country,
                version=selection.version,
            )
            for selection in selections
        ]
        return LexiconQueryRuntime.from_bundles(bundles)

    def _selected_ontologies_for_mapping(
        self,
        mapping: LexiconMappingValidationRequest,
    ) -> list[LexiconSelectedOntology]:
        selected: list[LexiconSelectedOntology] = []
        seen: set[tuple[str, str, str]] = set()

        def add_reference(
            ontology_family: str | None,
            country: str | None,
            version: str | None,
        ) -> None:
            if not ontology_family or not country or not version:
                return
            key = (ontology_family, country, version)
            if key in seen:
                return
            seen.add(key)
            selected.append(
                LexiconSelectedOntology(
                    ontology_family=ontology_family,
                    country=country,
                    version=version,
                )
            )

        if mapping.source_term is not None:
            add_reference(mapping.source_term.ontology_family, mapping.source_term.country, mapping.source_term.version)
        if mapping.target_term is not None:
            add_reference(mapping.target_term.ontology_family, mapping.target_term.country, mapping.target_term.version)
        if mapping.source_pattern is not None:
            for condition in self._iter_source_pattern_conditions(mapping.source_pattern):
                add_reference(condition.ontology_family, condition.country, condition.version)
        for assertion in mapping.target_assertions:
            add_reference(assertion.ontology_family, assertion.country, assertion.version)
        return selected

    def _resolve_rule_source_term_detail(
        self,
        *,
        term_kind: str,
        term_value: str,
        bundles: list[LexiconOntologyBundle],
    ) -> LexiconCatalogTermDetail:
        if term_value.startswith("http://") or term_value.startswith("https://"):
            for bundle in bundles:
                term = bundle.catalog.get(term_value)
                if term and term["kind"] == term_kind:
                    return self._catalog_term_detail_from_dict(term)
        else:
            for bundle in bundles:
                for term in bundle.catalog.values():
                    if term["kind"] == term_kind and term["term_id"] == term_value:
                        return self._catalog_term_detail_from_dict(term)

        return LexiconCatalogTermDetail(
            term_id=self._term_local_name(term_value),
            uri=term_value,
            kind=term_kind,
            label=self._term_local_name(term_value),
            definition=None,
            description=None,
            parent_term_ids=[],
            child_term_ids=[],
            property_term_ids=[],
            property_set_term_ids=[],
            belongs_to_class_term_ids=[],
            related_property_term_ids=[],
            unit=None,
            is_property_set=False,
            is_predefined_type=False,
        )

    @staticmethod
    def _catalog_term_detail_from_dict(term: dict[str, Any]) -> LexiconCatalogTermDetail:
        return LexiconCatalogTermDetail(
            term_id=term["term_id"],
            uri=term["uri"],
            kind=term["kind"],
            label=term["label"],
            definition=term["definition"],
            description=term["description"],
            parent_term_ids=term["parent_term_ids"],
            child_term_ids=term["child_term_ids"],
            property_term_ids=term["property_term_ids"],
            property_set_term_ids=term["property_set_term_ids"],
            belongs_to_class_term_ids=term["belongs_to_class_term_ids"],
            related_property_term_ids=term["related_property_term_ids"],
            unit=term["unit"],
            is_property_set=term["is_property_set"],
            is_predefined_type=term["is_predefined_type"],
        )

    def _build_rule_source_term(
        self,
        *,
        term_kind: str,
        term_value: str,
        node_ids: list[str],
        compact_terms: bool,
    ) -> LexiconRuleSimilaritySourceTerm:
        return LexiconRuleSimilaritySourceTerm(
            term_kind=term_kind,
            value=self._term_display_value(term_value, compact_terms=compact_terms),
            full_iri=term_value,
            namespace=self._term_namespace(term_value),
            local_name=self._term_local_name(term_value),
            node_ids=node_ids,
        )

    def _similarity_components(
        self,
        left_term: LexiconCatalogTermDetail,
        right_term: LexiconCatalogTermDetail,
        dataset: LexiconMergedResolutionDataset | None = None,
    ) -> list[LexiconSimilarityComponentScore]:
        left_label = (left_term.label or left_term.term_id).strip().lower()
        right_label = (right_term.label or right_term.term_id).strip().lower()
        left_tokens = self._tokenize_similarity_text(
            " ".join(
                filter(
                    None,
                    [
                        left_term.term_id,
                        left_term.label,
                        left_term.definition,
                        left_term.description,
                    ],
                )
            )
        )
        right_tokens = self._tokenize_similarity_text(
            " ".join(
                filter(
                    None,
                    [
                        right_term.term_id,
                        right_term.label,
                        right_term.definition,
                        right_term.description,
                    ],
                )
            )
        )
        shared_tokens = left_tokens & right_tokens
        union_tokens = left_tokens | right_tokens
        token_score = round(len(shared_tokens) / len(union_tokens), 3) if union_tokens else 0.0
        hierarchy_overlap = set(left_term.parent_term_ids) & set(right_term.parent_term_ids)
        hierarchy_score = 1.0 if hierarchy_overlap else 0.0
        if dataset is not None and left_term.kind == "class" and right_term.kind == "class":
            hierarchy_score = max(
                hierarchy_score,
                1.0 if dataset.evaluator.shared_hierarchy(left_term.uri, right_term.uri) else 0.0,
            )
        property_set_overlap = set(left_term.property_set_term_ids) & set(right_term.property_set_term_ids)
        property_set_score = 1.0 if property_set_overlap else 0.0
        class_overlap = set(left_term.belongs_to_class_term_ids) & set(right_term.belongs_to_class_term_ids)
        class_context_score = 1.0 if class_overlap else 0.0
        unit_score = 1.0 if left_term.unit and right_term.unit and left_term.unit == right_term.unit else 0.0
        components = [
            LexiconSimilarityComponentScore(
                name="kind_match",
                score=1.0 if left_term.kind == right_term.kind else 0.0,
                note=(
                    "Both concepts are the same kind." if left_term.kind == right_term.kind else "Concept kinds differ."
                ),
            ),
            LexiconSimilarityComponentScore(
                name="label_similarity",
                score=1.0 if left_label == right_label else token_score,
                note=(
                    "Labels or concept tokens overlap strongly."
                    if token_score > 0.0 or left_label == right_label
                    else None
                ),
            ),
            LexiconSimilarityComponentScore(
                name="hierarchy_similarity",
                score=hierarchy_score,
                note="The concepts share parent hierarchy." if hierarchy_overlap else None,
            ),
            LexiconSimilarityComponentScore(
                name="property_set_similarity",
                score=property_set_score,
                note="The concepts participate in the same property set." if property_set_overlap else None,
            ),
            LexiconSimilarityComponentScore(
                name="class_context_similarity",
                score=class_context_score,
                note="The properties belong to the same classes." if class_overlap else None,
            ),
            LexiconSimilarityComponentScore(
                name="unit_similarity",
                score=unit_score,
                note="The concepts share the same unit." if unit_score else None,
            ),
        ]
        return components

    @staticmethod
    def _tokenize_similarity_text(value: str) -> set[str]:
        normalized = "".join(char.lower() if char.isalnum() else " " for char in value)
        return {token for token in normalized.split() if len(token) > 1}

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

    def _iter_source_pattern_conditions(
        self,
        pattern: LexiconSourcePattern,
    ) -> list[LexiconSourceTermCondition]:
        conditions: list[LexiconSourceTermCondition] = []
        clauses = pattern.all_of if pattern.all_of else pattern.any_of
        for clause in clauses:
            conditions.extend(self._iter_source_clause_conditions(clause))
        return conditions

    def _iter_source_clause_conditions(
        self,
        clause: LexiconSourcePatternClause,
    ) -> list[LexiconSourceTermCondition]:
        if clause.term is not None:
            return [clause.term]
        if clause.entity is not None:
            return list(clause.entity.all_of if clause.entity.all_of else clause.entity.any_of)

        conditions: list[LexiconSourceTermCondition] = []
        for item in clause.all_of if clause.all_of else clause.any_of:
            conditions.extend(self._iter_source_clause_conditions(item))
        return conditions

    def _validate_source_clause(self, clause: LexiconSourcePatternClause) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        if clause.term is not None:
            condition_errors, condition_warnings = self._validate_source_term_condition(clause.term)
            errors.extend(condition_errors)
            warnings.extend(condition_warnings)
        elif clause.entity is not None:
            entity_errors, entity_warnings = self._validate_source_entity_pattern(clause.entity)
            errors.extend(entity_errors)
            warnings.extend(entity_warnings)
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

    def _validate_source_entity_pattern(self, entity: LexiconSourceEntityPattern) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        conditions = entity.all_of if entity.all_of else entity.any_of
        for condition in conditions:
            condition_errors, condition_warnings = self._validate_source_term_condition(condition)
            errors.extend(condition_errors)
            warnings.extend(condition_warnings)
        warnings.append(
            "Entity-scoped source pattern keeps the matched terms on the same source object ('with' semantics)."
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

    @staticmethod
    def _profile_gaps(
        *,
        expected_profile: LexiconExecutionProfile,
        subject_types: dict[str, set[str]],
        subject_predicates: dict[str, set[str]],
        canonical_country_uri: str,
    ) -> tuple[list[str], list[LexiconNeededProperty]]:
        missing_target_classes: list[str] = []
        missing_properties: list[LexiconNeededProperty] = []

        for target in expected_profile.targets:
            normalized_target_class = (
                target.target_class
                if target.target_class.startswith("http://") or target.target_class.startswith("https://")
                else f"{canonical_country_uri}{target.target_class}"
            )
            subjects_for_target = [
                subject for subject, types in subject_types.items() if normalized_target_class in types
            ]
            if not subjects_for_target:
                missing_target_classes.append(target.target_class)
                for requirement in target.property_requirements:
                    missing_properties.append(
                        LexiconNeededProperty(
                            target_class=target.target_class,
                            property_path=requirement.property_path,
                            node_ids=requirement.node_ids,
                        )
                    )
                continue

            for requirement in target.property_requirements:
                normalized_property_path = (
                    requirement.property_path
                    if requirement.property_path.startswith("http://")
                    or requirement.property_path.startswith("https://")
                    else f"{canonical_country_uri}{requirement.property_path}"
                )
                if not any(
                    normalized_property_path in subject_predicates.get(subject, set())
                    for subject in subjects_for_target
                ):
                    missing_properties.append(
                        LexiconNeededProperty(
                            target_class=target.target_class,
                            property_path=requirement.property_path,
                            node_ids=requirement.node_ids,
                        )
                    )

        return sorted(set(missing_target_classes)), missing_properties

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
        # Terms the ontology relates its own terms to are of that kind by definition, even when it does not type
        # them itself — typically the core `fbo:` vocabulary a national ontology extends (`fbo-dk:partOf
        # owl:inverseOf fbo:hasSubitem`, `fbo-dk:X rdfs:subPropertyOf fbo:X`, `… rdfs:subClassOf fbo:Building`).
        for predicate in (RDFS.subPropertyOf, OWL.inverseOf, OWL.equivalentProperty):
            declared_properties |= {str(obj) for obj in graph.objects(None, predicate) if isinstance(obj, URIRef)}
        for predicate in (RDFS.subClassOf, OWL.equivalentClass):
            declared_classes |= {str(obj) for obj in graph.objects(None, predicate) if isinstance(obj, URIRef)}
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

        property_class_map: dict[str, set[str]] = defaultdict(set)
        belongs_to_class_predicate = URIRef(ONTOLOGY_META_BELONGS_TO_CLASS_URI)
        for subject, _, obj in graph.triples((None, belongs_to_class_predicate, None)):
            property_class_map[str(subject)].add(str(obj))

        is_property_set_by_uri: dict[str, bool] = {}
        group_of_properties_predicate = URIRef(ONTOLOGY_META_GROUP_OF_PROPERTIES_URI)
        for uri in declared_classes:
            value = graph.value(URIRef(uri), group_of_properties_predicate)
            is_property_set_by_uri[uri] = bool(isinstance(value, Literal) and str(value).strip().lower() == "true")

        property_set_membership_map: dict[str, set[str]] = defaultdict(set)
        for class_uri, property_uris in class_property_map.items():
            if not is_property_set_by_uri.get(class_uri, False):
                continue
            for property_uri in property_uris:
                property_set_membership_map[property_uri].add(class_uri)

        related_property_map: dict[str, set[str]] = defaultdict(set)
        related_property_predicate = URIRef(ONTOLOGY_META_RELATED_PROPERTY_URI)
        for subject, _, obj in graph.triples((None, related_property_predicate, None)):
            related_property_map[str(subject)].add(str(obj))

        unit_predicate = URIRef(ONTOLOGY_META_UNIT_URI)

        term_catalog: dict[str, dict[str, Any]] = {}
        definition_predicate = URIRef("http://www.w3.org/2004/02/skos/core#definition")
        for uri in sorted(declared_classes | declared_properties):
            subject = URIRef(uri)
            kind = "class" if uri in declared_classes else "property"
            label_value = graph.value(subject, RDFS.label)
            definition_value = graph.value(subject, definition_predicate)
            description_value = graph.value(subject, RDFS.comment)
            unit_value = graph.value(subject, unit_predicate)
            property_set_term_ids = sorted(
                {
                    LexiconService._term_id_from_uri(property_set_uri)
                    for property_set_uri in (
                        property_set_membership_map.get(uri, set())
                        if kind == "property"
                        else {
                            property_set_uri
                            for property_uri in class_property_map.get(uri, set())
                            for property_set_uri in property_set_membership_map.get(property_uri, set())
                        }
                    )
                }
            )
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
                "property_set_term_ids": property_set_term_ids,
                "belongs_to_class_term_ids": sorted(
                    LexiconService._term_id_from_uri(class_uri) for class_uri in property_class_map.get(uri, set())
                ),
                "related_property_term_ids": sorted(
                    LexiconService._term_id_from_uri(prop) for prop in related_property_map.get(uri, set())
                ),
                "unit": str(unit_value) if unit_value is not None else None,
                "is_property_set": is_property_set_by_uri.get(uri, False),
                "is_predefined_type": "predefined type"
                in (str(description_value).lower() if description_value else ""),
            }
        return term_catalog

    @staticmethod
    def _summary_from_catalog_term(term: dict[str, Any]) -> LexiconCatalogTermSummary:
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

    @staticmethod
    def _term_id_from_uri(uri: str) -> str:
        if "#" in uri:
            return uri.rsplit("#", 1)[1]
        return uri.rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _term_namespace(value: str) -> str:
        if "#" in value:
            return value.rsplit("#", 1)[0]
        if "/" in value:
            return value.rsplit("/", 1)[0]
        return ""

    @staticmethod
    def _term_local_name(value: str) -> str:
        if "#" in value:
            return value.rsplit("#", 1)[1]
        if "/" in value:
            return value.rstrip("/").rsplit("/", 1)[-1]
        return value

    @classmethod
    def _term_display_value(cls, value: str, *, compact_terms: bool) -> str:
        return cls._term_local_name(value) if compact_terms else value

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
