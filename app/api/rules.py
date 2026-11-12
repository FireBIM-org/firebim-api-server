import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.auth.jwt import get_current_active_user
from app.graphs_and_checks.publisher import fetch_rule_ttl
from app.rules.composer import (
    check_ruleset_completeness,
    connect_ruleset_nodes,
    disconnect_ruleset_node_branch,
    force_end_ruleset,
)
from app.rules.decompiler import decompile_rule_element_ttl, decompile_ruleset_ttl
from app.rules.dsl import RuleElementBuilder
from app.rules.element_validator import validate_rule_element_payload
from app.rules.lint import lint_rule
from app.rules.models import fbRuleElement
from app.rules.paths import is_ref_value, ref_path, split_property_path
from app.rules.publisher import (
    default_rule_element_graph_uri,
    default_rule_graph_uri,
    delete_graph_ttl,
    display_title,
    fetch_graph_ttl,
    forget_graph,
    list_rule_element_entries,
    list_rule_graphs,
    publish_graph_ttl,
    remember_rule_element_graph,
)
from app.rules.ruleset import Rule
from app.rules.shacl_builder import SHACLBuilder
from app.schemas.rules import (
    RuleAssembleCompileResponse,
    RuleAssembleRequest,
    RuleAssembleResponse,
    RuleCompileRequest,
    RuleCompileResponse,
    RuleCompletenessRequest,
    RuleCompletenessResponse,
    RuleConnectRequest,
    RuleConnectResponse,
    RuleDecompileRequest,
    RuleDecompileResponse,
    RuleDeleteRequest,
    RuleDeleteResponse,
    RuleDisconnectRequest,
    RuleDisconnectResponse,
    RuleDraft,
    RuleElementDeleteRequest,
    RuleElementDeleteResponse,
    RuleElementDeriveRequest,
    RuleElementDeriveResponse,
    RuleElementFetchRequest,
    RuleElementFetchResponse,
    RuleElementJson,
    RuleElementListItem,
    RuleElementListResponse,
    RuleElementPublishRequest,
    RuleElementPublishResponse,
    RuleFetchRequest,
    RuleFetchResponse,
    RuleForceEndRequest,
    RuleForceEndResponse,
    RuleListItem,
    RuleListResponse,
    RulePublishRequest,
    RulePublishResponse,
    RuleTermDescriptor,
    RuleTermLocalNameGroup,
    RuleTermPairDescriptor,
    RuleTermsAggregateRequest,
    RuleTermsAggregateResponse,
    RuleTermsResponse,
    RuleTermStats,
    RuleValidateRequest,
    RuleValidateResponse,
)
from app.storage.graph_store import GraphStoreNotFoundError

logger = logging.getLogger(__name__)
# Every endpoint requires an authenticated user, except the probe on `public_router` (included before `router`
# so `/rules/health` is not captured by `/rules/{rule_id}`).
router = APIRouter(prefix="/rules", tags=["rules"], dependencies=[Depends(get_current_active_user)])
public_router = APIRouter(prefix="/rules", tags=["rules"])


class RuleTtlResponse(BaseModel):
    graph_uri: str
    ttl: str
    content_type: str = "text/turtle"


def _publish_error_payload(error_code: str, message: str, exc: Exception):
    logger.exception(message, exc_info=exc)
    return {
        "success": False,
        "error": error_code,
        "message": message,
        "fallback": True,
    }


def _rule_element_to_json(element: dict, graph_uri: str | None = None) -> RuleElementJson:
    element_type = element.get("element_type") or element.get("node_type") or "decision"
    return RuleElementJson(
        id=element.get("id"),
        element_type=element_type,
        target_class=element.get("target_class"),
        property_path=element.get("property_path"),
        operator=element.get("operator"),
        value=element.get("value"),
        expression=element.get("expression"),
        true_path=element.get("true_path"),
        false_path=element.get("false_path"),
        unknown_path=element.get("unknown_path"),
        label=element.get("label"),
        config=element.get("config") or {},
        graph_uri=graph_uri,
    )


def _normalize_rule_dict(rule: dict | None) -> dict | None:
    if rule is None:
        return None
    rule = dict(rule)
    elements = rule.get("elements") or rule.get("nodes") or []
    normalized = {
        "id": rule.get("id"),
        "entry": rule.get("entry"),
        "elements": [],
        "title": rule.get("title"),
        "description": rule.get("description"),
        "source": rule.get("source"),
    }
    for raw in elements:
        item = dict(raw)
        if "element_type" not in item and "node_type" in item:
            item["element_type"] = item["node_type"]
        normalized["elements"].append(item)
    return normalized


def _rule_to_composer_dict(rule: dict) -> dict:
    nodes = []
    for raw in rule.get("elements", []):
        item = dict(raw)
        if "node_type" not in item:
            item["node_type"] = item.get("element_type", "decision")
        nodes.append(item)
    return {
        "id": rule.get("id"),
        "entry": rule.get("entry"),
        "nodes": nodes,
        "title": rule.get("title"),
        "description": rule.get("description"),
        "source": rule.get("source"),
    }


def _build_rule(rule_payload) -> Rule:
    rule = Rule(rule_payload.id)

    for raw_element in rule_payload.elements:
        node_data = raw_element.as_internal_dict()
        validate_rule_element_payload(node_data)

        element = fbRuleElement(
            id=raw_element.id,
            target_class=raw_element.target_class,
            property_path=raw_element.property_path,
            operator=raw_element.operator,
            value=raw_element.value,
            expression=raw_element.expression,
            true_path=raw_element.true_path,
            false_path=raw_element.false_path,
            unknown_path=raw_element.unknown_path,
            label=raw_element.label,
            node_type=raw_element.element_type,
            config=raw_element.config,
        )
        rule.add(element)

    rule.start(rule_payload.entry)
    rule.validate()
    rule.title = _rule_title(rule_payload)
    rule.description = rule_payload.description
    rule.source = rule_payload.source
    return rule


def _rule_title(rule_payload) -> str | None:
    """Explicit title, else the start element's `config.rule_title`, else its label without 'Start:'."""
    if rule_payload.title and rule_payload.title.strip():
        return rule_payload.title.strip()
    for element in rule_payload.elements:
        if element.id == rule_payload.entry:
            config = element.config or {}
            if config.get("rule_title"):
                return str(config["rule_title"]).strip()
            return display_title(None, element.label)
    return None


def _lint_rule_payload(rule_payload) -> list[str]:
    """Whole-rule checks (cycles, derive ordering, degenerate comparisons). Raises on errors, returns warnings."""
    rule_dict = {
        "id": rule_payload.id,
        "entry": rule_payload.entry,
        "elements": [element.as_internal_dict() for element in rule_payload.elements],
    }
    errors, warnings = lint_rule(rule_dict)
    if errors:
        raise ValueError("; ".join(errors))
    return warnings


def _rule_label(rule_payload) -> str | None:
    for element in rule_payload.elements:
        if element.id == rule_payload.entry:
            return element.label
    return None


def _resolve_rule_graph_uri(rule_id: str) -> str:
    return default_rule_graph_uri(rule_id)


def _resolve_rule_element_graph_uri(element_id: str) -> str:
    return default_rule_element_graph_uri(element_id)


def _fetch_rule_element_json_by_graph_uri(graph_uri: str) -> RuleElementJson:
    ttl = fetch_graph_ttl(dataset="rulebook", graph_uri=graph_uri)
    element = decompile_rule_element_ttl(ttl)
    return _rule_element_to_json(element, graph_uri=graph_uri)


def _fetch_rule_by_id(rule_id: str) -> RuleFetchResponse:
    graph_uri = _resolve_rule_graph_uri(rule_id)
    ttl = fetch_graph_ttl(dataset="rulebook", graph_uri=graph_uri)
    rule = None
    try:
        rule = _normalize_rule_dict(decompile_ruleset_ttl(ttl))
    except Exception:
        logger.exception("Rule fetch succeeded but SHACL decompile failed")
        rule = None
    return RuleFetchResponse(graph_uri=graph_uri, ttl=ttl, rule=rule)


def _fetch_rule_element_by_id(element_id: str) -> RuleElementFetchResponse:
    graph_uri = _resolve_rule_element_graph_uri(element_id)
    ttl = fetch_graph_ttl(dataset="rulebook", graph_uri=graph_uri)
    rule_element = None
    try:
        rule_element = decompile_rule_element_ttl(ttl)
    except Exception:
        logger.exception("Rule element fetch succeeded but SHACL decompile failed")
        rule_element = None
    return RuleElementFetchResponse(graph_uri=graph_uri, ttl=ttl, rule_element=rule_element)


def _split_term_parts(term: str) -> tuple[str, str]:
    if "#" in term:
        namespace, local_name = term.rsplit("#", 1)
        return namespace, local_name
    if "/" in term:
        namespace, local_name = term.rsplit("/", 1)
        return namespace, local_name
    return "", term


def _term_descriptor(term: str, *, compact_terms: bool) -> RuleTermDescriptor:
    namespace, local_name = _split_term_parts(term)
    return RuleTermDescriptor(
        value=local_name if compact_terms else term,
        full_iri=term,
        namespace=namespace,
        local_name=local_name,
    )


def _local_name_groups(terms: list[str]) -> list[RuleTermLocalNameGroup]:
    grouped: dict[str, set[str]] = defaultdict(set)
    namespaces_by_name: dict[str, set[str]] = defaultdict(set)
    for term in terms:
        namespace, local_name = _split_term_parts(term)
        grouped[local_name].add(term)
        if namespace:
            namespaces_by_name[local_name].add(namespace)

    return [
        RuleTermLocalNameGroup(
            local_name=local_name,
            count=len(full_iris),
            full_iris=sorted(full_iris),
            namespaces=sorted(namespaces_by_name.get(local_name, set())),
        )
        for local_name, full_iris in sorted(grouped.items())
    ]


def _collect_rule_term_values(rule: dict) -> dict:
    elements = rule.get("elements", [])
    target_classes_raw = [str(item["target_class"]) for item in elements if item.get("target_class")]
    # sequence paths and $ref values contribute every segment they touch
    property_paths_raw = [
        segment
        for item in elements
        if item.get("property_path")
        for segment in split_property_path(str(item["property_path"]))
    ] + [
        segment
        for item in elements
        if is_ref_value(item.get("value"))
        for segment in split_property_path(ref_path(item.get("value")))
    ]
    pair_keys = {
        (str(item["target_class"]), str(item["property_path"]))
        for item in elements
        if item.get("target_class") and item.get("property_path")
    }
    return {
        "elements": elements,
        "target_classes_raw": target_classes_raw,
        "property_paths_raw": property_paths_raw,
        "pair_keys": pair_keys,
        "has_target_classes": bool(target_classes_raw),
        "has_property_paths": bool(property_paths_raw),
        "has_both": bool(pair_keys),
    }


def _build_rule_term_stats(
    *,
    rule_count: int,
    rules_with_target_classes: int,
    rules_with_property_paths: int,
    rules_with_both: int,
    elements: list[dict],
    unique_target_classes: list[str],
    unique_property_paths: list[str],
    pair_keys: list[tuple[str, str]],
    target_class_groups: list[RuleTermLocalNameGroup],
    property_path_groups: list[RuleTermLocalNameGroup],
) -> RuleTermStats:
    unique_target_class_namespaces = len({_split_term_parts(term)[0] for term in unique_target_classes if term})
    unique_target_class_local_names = len({_split_term_parts(term)[1] for term in unique_target_classes if term})
    unique_property_path_namespaces = len({_split_term_parts(term)[0] for term in unique_property_paths if term})
    unique_property_path_local_names = len({_split_term_parts(term)[1] for term in unique_property_paths if term})
    return RuleTermStats(
        rule_count=rule_count,
        element_count=len(elements),
        rules_with_target_classes=rules_with_target_classes,
        rules_with_property_paths=rules_with_property_paths,
        rules_with_both=rules_with_both,
        elements_with_target_class=sum(1 for item in elements if item.get("target_class")),
        elements_with_property_path=sum(1 for item in elements if item.get("property_path")),
        elements_with_both=sum(1 for item in elements if item.get("target_class") and item.get("property_path")),
        unique_target_classes=len(unique_target_classes),
        unique_target_class_namespaces=unique_target_class_namespaces,
        unique_target_class_local_names=unique_target_class_local_names,
        unique_property_paths=len(unique_property_paths),
        unique_property_path_namespaces=unique_property_path_namespaces,
        unique_property_path_local_names=unique_property_path_local_names,
        unique_target_property_pairs=len(pair_keys),
        target_class_local_name_collisions=sum(1 for group in target_class_groups if group.count > 1),
        property_path_local_name_collisions=sum(1 for group in property_path_groups if group.count > 1),
    )


def _build_terms_payload(
    *,
    compact_terms: bool,
    elements: list[dict],
    unique_target_classes: list[str],
    unique_property_paths: list[str],
    pair_keys: list[tuple[str, str]],
) -> dict:
    target_class_groups = _local_name_groups(unique_target_classes)
    property_path_groups = _local_name_groups(unique_property_paths)
    return {
        "target_classes": [_term_descriptor(term, compact_terms=compact_terms) for term in unique_target_classes],
        "property_paths": [_term_descriptor(term, compact_terms=compact_terms) for term in unique_property_paths],
        "target_property_pairs": [
            RuleTermPairDescriptor(
                value=(
                    f"{_term_descriptor(target_class, compact_terms=compact_terms).value}"
                    f" :: {_term_descriptor(property_path, compact_terms=compact_terms).value}"
                ),
                full_pair=f"{target_class}::{property_path}",
                compact_pair=f"{_split_term_parts(target_class)[1]}::{_split_term_parts(property_path)[1]}",
                target_class=_term_descriptor(target_class, compact_terms=compact_terms),
                property_path=_term_descriptor(property_path, compact_terms=compact_terms),
            )
            for target_class, property_path in pair_keys
        ],
        "target_class_local_name_groups": target_class_groups,
        "property_path_local_name_groups": property_path_groups,
        "target_class_groups": target_class_groups,
        "property_path_groups": property_path_groups,
    }


def _analyze_rule_terms(rule: dict, *, compact_terms: bool) -> RuleTermsResponse:
    collected = _collect_rule_term_values(rule)
    unique_target_classes = sorted(set(collected["target_classes_raw"]))
    unique_property_paths = sorted(set(collected["property_paths_raw"]))
    pair_keys = sorted(collected["pair_keys"])
    payload = _build_terms_payload(
        compact_terms=compact_terms,
        elements=collected["elements"],
        unique_target_classes=unique_target_classes,
        unique_property_paths=unique_property_paths,
        pair_keys=pair_keys,
    )

    return RuleTermsResponse(
        rule_id=str(rule.get("id") or ""),
        compact_terms=compact_terms,
        target_classes=payload["target_classes"],
        property_paths=payload["property_paths"],
        target_property_pairs=payload["target_property_pairs"],
        target_class_local_name_groups=payload["target_class_local_name_groups"],
        property_path_local_name_groups=payload["property_path_local_name_groups"],
        stats=_build_rule_term_stats(
            rule_count=1,
            rules_with_target_classes=1 if collected["has_target_classes"] else 0,
            rules_with_property_paths=1 if collected["has_property_paths"] else 0,
            rules_with_both=1 if collected["has_both"] else 0,
            elements=collected["elements"],
            unique_target_classes=unique_target_classes,
            unique_property_paths=unique_property_paths,
            pair_keys=pair_keys,
            target_class_groups=payload["target_class_groups"],
            property_path_groups=payload["property_path_groups"],
        ),
    )


def _apply_links(rule: dict, links: list) -> dict:
    if not links:
        return rule
    ruleset = _rule_to_composer_dict(rule)
    for link in links:
        result = connect_ruleset_nodes(
            ruleset=ruleset,
            source_id=link.source_id,
            branch=link.branch,
            target_id=link.target_id,
        )
        ruleset = result["ruleset"]
    return _normalize_rule_dict(ruleset) or rule


def _assemble_rule(payload: RuleAssembleRequest) -> tuple[dict, dict, list[str]]:
    elements = []
    errors: list[str] = []

    seen_ids = set()
    for element_id in payload.element_ids:
        if element_id in seen_ids:
            continue
        seen_ids.add(element_id)

        try:
            response = _fetch_rule_element_by_id(element_id)
            element = response.rule_element
            if not element:
                errors.append(f"Rule element '{element_id}' could not be decompiled")
                continue
            elements.append(_rule_element_to_json(element).model_dump(exclude_none=True))
        except Exception as exc:
            errors.append(f"Rule element '{element_id}' could not be fetched: {exc}")

    rule = {
        "id": payload.rule_id,
        "entry": payload.entry,
        "elements": elements,
    }
    rule = _apply_links(rule, payload.links)

    completeness = check_ruleset_completeness(_rule_to_composer_dict(rule))

    if payload.validate:
        try:
            _build_rule(RuleDraft(**rule))
        except Exception as exc:
            errors.append(str(exc))

    return rule, completeness, errors


@public_router.get("/health")
def rules_health():
    test_rule_element = (
        RuleElementBuilder("health_check_rule_element")
        .target("Test")
        .when("FBO.roomArea")
        .gt(1)
        .true("ok")
        .false("fail")
        .unknown("missing")
        .build()
    )
    return {
        "status": "ok",
        "rule_element_id": test_rule_element.id,
        "target": test_rule_element.target_class,
        "operator": test_rule_element.operator,
    }


@router.post("/validate", response_model=RuleValidateResponse)
def validate_rule(payload: RuleValidateRequest):
    try:
        rule = _build_rule(payload.rule)
        warnings = _lint_rule_payload(payload.rule)
        count = len(rule.elements)
        return RuleValidateResponse(
            valid=True,
            rule_id=rule.id,
            entry=rule.entry,
            element_count=count,
            errors=[],
            warnings=warnings,
        )
    except ValueError as exc:
        count = len(payload.rule.elements)
        return RuleValidateResponse(
            valid=False,
            rule_id=payload.rule.id,
            entry=payload.rule.entry,
            element_count=count,
            errors=[str(exc)],
            success=False,
            error="validation_error",
            message=str(exc),
            fallback=False,
        )
    except Exception as exc:
        count = len(payload.rule.elements)
        error_bits = _publish_error_payload("rule_validation_error", "Unexpected error while validating rule", exc)
        return RuleValidateResponse(
            valid=False,
            rule_id=payload.rule.id,
            entry=payload.rule.entry,
            element_count=count,
            errors=[error_bits["message"]],
            **error_bits,
        )


@router.post("/compile", response_model=RuleCompileResponse)
def compile_rule(payload: RuleCompileRequest):
    try:
        rule = _build_rule(payload.rule)
        warnings = _lint_rule_payload(payload.rule)
        builder = SHACLBuilder()
        builder.add_rule_set(rule)
        ttl = builder.get_graph().serialize(format="turtle")
        count = len(rule.elements)
        return RuleCompileResponse(
            rule_id=rule.id,
            element_count=count,
            ttl=str(ttl),
            warnings=warnings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        count = len(payload.rule.elements)
        error_bits = _publish_error_payload("rule_compile_error", "Unexpected error while compiling rule", exc)
        return RuleCompileResponse(
            rule_id=payload.rule.id,
            element_count=count,
            ttl="",
            **error_bits,
        )


@router.post("/decompile", response_model=RuleDecompileResponse)
def decompile_rule(payload: RuleDecompileRequest):
    try:
        rule = _normalize_rule_dict(decompile_ruleset_ttl(payload.ttl))
        return RuleDecompileResponse(rule=rule)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        error_bits = _publish_error_payload(
            "rule_decompile_error", "Unexpected error while decompiling SHACL rule", exc
        )
        return RuleDecompileResponse(rule=None, **error_bits)


@router.post("/publish", response_model=RulePublishResponse)
def publish_rule(payload: RulePublishRequest):
    graph_uri = _resolve_rule_graph_uri(payload.rule.id)

    try:
        rule = _build_rule(payload.rule)
        _lint_rule_payload(payload.rule)
        builder = SHACLBuilder()
        builder.add_rule_set(rule)
        ttl = builder.get_graph().serialize(format="turtle")
        publish_graph_ttl(ttl_text=str(ttl), dataset="rulebook", graph_uri=graph_uri)

        count = len(rule.elements)
        label = _rule_label(payload.rule)

        return RulePublishResponse(
            published=True,
            rule_id=rule.id,
            graph_uri=graph_uri,
            entry=rule.entry,
            element_count=count,
            label=label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        count = len(payload.rule.elements)
        error_bits = _publish_error_payload(
            "storage_error", "Temporary backend storage issue while publishing rule", exc
        )
        return RulePublishResponse(
            published=False,
            rule_id=payload.rule.id,
            graph_uri=graph_uri,
            entry=payload.rule.entry,
            element_count=count,
            label=_rule_label(payload.rule),
            **error_bits,
        )


@router.get("/list", response_model=RuleListResponse)
def list_rules():
    try:
        items = [RuleListItem(**item) for item in list_rule_graphs()]
        return RuleListResponse(count=len(items), rules=items)
    except Exception as exc:
        error_bits = _publish_error_payload("storage_error", "Rule list unavailable, returning fallback response", exc)
        return RuleListResponse(count=0, rules=[], warning="Graph storage temporarily unavailable", **error_bits)


@router.post("/fetch", response_model=RuleFetchResponse)
def fetch_rule(payload: RuleFetchRequest):
    try:
        return _fetch_rule_by_id(payload.rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GraphStoreNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Rule not found: {payload.rule_id}") from exc
    except Exception as exc:
        graph_uri = _resolve_rule_graph_uri(payload.rule_id)
        error_bits = _publish_error_payload("storage_error", "Temporary backend storage issue while fetching rule", exc)
        return RuleFetchResponse(graph_uri=graph_uri, ttl="", rule=None, **error_bits)


@router.post("/delete", response_model=RuleDeleteResponse)
def delete_rule(payload: RuleDeleteRequest):
    graph_uri = _resolve_rule_graph_uri(payload.rule_id)

    try:
        delete_graph_ttl(dataset="rulebook", graph_uri=graph_uri)
        return RuleDeleteResponse(deleted=True, graph_uri=graph_uri)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        error_bits = _publish_error_payload("storage_error", "Temporary backend storage issue while deleting rule", exc)
        return RuleDeleteResponse(deleted=False, graph_uri=graph_uri, **error_bits)


@router.post("/assemble", response_model=RuleAssembleResponse)
def assemble_rule(payload: RuleAssembleRequest):
    try:
        rule, completeness, errors = _assemble_rule(payload)
        return RuleAssembleResponse(
            rule=rule,
            element_count=len(rule.get("elements", [])),
            complete=completeness["complete"],
            open=completeness["open"],
            broken=completeness["broken"],
            entry_ok=completeness["entry_ok"],
            missing_entry=completeness["missing_entry"],
            missing_nodes=completeness["missing_nodes"],
            open_branches=completeness["open_branches"],
            errors=completeness["errors"] + errors,
            success=not errors,
            error=None if not errors else "assembly_error",
            message=None if not errors else "Rule assembled with one or more issues",
            fallback=False,
        )
    except Exception as exc:
        error_bits = _publish_error_payload("assembly_error", "Unexpected error while assembling rule", exc)
        return RuleAssembleResponse(rule=None, **error_bits)


@router.post("/assemble/compile", response_model=RuleAssembleCompileResponse)
def assemble_and_compile_rule(payload: RuleAssembleRequest):
    try:
        rule, completeness, errors = _assemble_rule(payload)
        draft = RuleDraft(**rule)
        compiled = _build_rule(draft)
        builder = SHACLBuilder()
        builder.add_rule_set(compiled)
        ttl = builder.get_graph().serialize(format="turtle")
        all_errors = completeness["errors"] + errors
        return RuleAssembleCompileResponse(
            rule=rule,
            rule_id=rule["id"],
            element_count=len(rule.get("elements", [])),
            ttl=str(ttl),
            complete=completeness["complete"],
            open=completeness["open"],
            broken=completeness["broken"],
            errors=all_errors,
            success=not all_errors,
            error=None if not all_errors else "assembly_validation_warning",
            message=None if not all_errors else "Rule compiled but one or more issues were detected",
            fallback=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        error_bits = _publish_error_payload(
            "assembly_compile_error", "Unexpected error while assembling and compiling rule", exc
        )
        return RuleAssembleCompileResponse(
            rule=None,
            rule_id=payload.rule_id,
            element_count=0,
            ttl="",
            **error_bits,
        )


@router.post("/rule-elements/derive", response_model=RuleElementDeriveResponse)
def derive_rule_element(payload: RuleElementDeriveRequest):
    try:
        source_graph_uri = _resolve_rule_element_graph_uri(payload.source_id)
        derived = _fetch_rule_element_json_by_graph_uri(source_graph_uri).model_dump()
        derived["id"] = payload.new_id
        derived["graph_uri"] = None

        if not payload.preserve_paths:
            derived["true_path"] = None
            derived["false_path"] = None
            derived["unknown_path"] = None

        for key, value in payload.overrides.items():
            if key in derived:
                derived[key] = value

        return RuleElementDeriveResponse(rule_element=RuleElementJson(**derived))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error while deriving rule element", exc_info=exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/rule-elements", response_model=RuleElementPublishResponse)
def publish_rule_element(payload: RuleElementPublishRequest):
    graph_uri = _resolve_rule_element_graph_uri(payload.id)
    node_data = payload.as_internal_dict()

    try:
        validate_rule_element_payload(node_data)
        element = fbRuleElement(
            id=payload.id,
            target_class=payload.target_class,
            property_path=payload.property_path,
            operator=payload.operator,
            value=payload.value,
            expression=payload.expression,
            true_path=payload.true_path,
            false_path=payload.false_path,
            unknown_path=payload.unknown_path,
            label=payload.label,
            node_type=payload.element_type,
            config=payload.config,
        )

        builder = SHACLBuilder()
        builder.add_rule_element(element)
        ttl = builder.get_graph().serialize(format="turtle")
        publish_graph_ttl(ttl_text=str(ttl), dataset="rulebook", graph_uri=graph_uri)
        remember_rule_element_graph(
            graph_uri=graph_uri,
            element_id=element.id,
            element_type=payload.element_type,
            label=payload.label,
        )

        return RuleElementPublishResponse(
            published=True,
            element_id=element.id,
            graph_uri=graph_uri,
            element_type=payload.element_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        error_bits = _publish_error_payload(
            "storage_error", "Temporary backend storage issue while publishing rule element", exc
        )
        return RuleElementPublishResponse(
            published=False,
            element_id=payload.id,
            graph_uri=graph_uri,
            element_type=payload.element_type,
            **error_bits,
        )


@router.post("/rule-elements/fetch", response_model=RuleElementFetchResponse)
def fetch_rule_element(payload: RuleElementFetchRequest):
    try:
        return _fetch_rule_element_by_id(payload.element_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GraphStoreNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Rule element not found: {payload.element_id}") from exc
    except Exception as exc:
        graph_uri = _resolve_rule_element_graph_uri(payload.element_id)
        error_bits = _publish_error_payload(
            "storage_error", "Temporary backend storage issue while fetching rule element", exc
        )
        return RuleElementFetchResponse(graph_uri=graph_uri, ttl="", rule_element=None, **error_bits)


@router.post("/rule-elements/delete", response_model=RuleElementDeleteResponse)
def delete_rule_element(payload: RuleElementDeleteRequest):
    graph_uri = _resolve_rule_element_graph_uri(payload.element_id)

    try:
        delete_graph_ttl(dataset="rulebook", graph_uri=graph_uri)
        forget_graph(graph_uri)
        return RuleElementDeleteResponse(deleted=True, graph_uri=graph_uri)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        error_bits = _publish_error_payload(
            "storage_error", "Temporary backend storage issue while deleting rule element", exc
        )
        return RuleElementDeleteResponse(deleted=False, graph_uri=graph_uri, **error_bits)


@router.get("/rule-elements", response_model=RuleElementListResponse)
def list_rule_elements():
    try:
        entries = [RuleElementListItem(**item) for item in list_rule_element_entries()]
        graph_uris = [item.graph_uri for item in entries]
        return RuleElementListResponse(count=len(entries), graph_uris=graph_uris, rule_elements=entries)
    except Exception as exc:
        error_bits = _publish_error_payload(
            "storage_error", "Rule element list unavailable, returning fallback response", exc
        )
        return RuleElementListResponse(
            count=0,
            graph_uris=[],
            rule_elements=[],
            warning="Graph storage temporarily unavailable",
            **error_bits,
        )


@router.post("/connect", response_model=RuleConnectResponse)
def connect_rules(payload: RuleConnectRequest):
    try:
        result = connect_ruleset_nodes(
            ruleset=_rule_to_composer_dict(payload.rule.as_internal_dict()),
            source_id=payload.source_id,
            branch=payload.branch,
            target_id=payload.target_id,
        )
        rule = _normalize_rule_dict(result["ruleset"])
        return RuleConnectResponse(
            updated=result["updated"],
            overwritten=result["overwritten"],
            previous_target_id=result["previous_target_id"],
            rule=rule,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/disconnect", response_model=RuleDisconnectResponse)
def disconnect_rules(payload: RuleDisconnectRequest):
    try:
        result = disconnect_ruleset_node_branch(
            ruleset=_rule_to_composer_dict(payload.rule.as_internal_dict()),
            source_id=payload.source_id,
            branch=payload.branch,
        )
        rule = _normalize_rule_dict(result["ruleset"])
        return RuleDisconnectResponse(
            updated=result["updated"],
            previous_target_id=result["previous_target_id"],
            rule=rule,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/check-completeness", response_model=RuleCompletenessResponse)
def check_completeness(payload: RuleCompletenessRequest):
    try:
        result = check_ruleset_completeness(ruleset=_rule_to_composer_dict(payload.rule.as_internal_dict()))
        return RuleCompletenessResponse(
            complete=result["complete"],
            open=result["open"],
            broken=result["broken"],
            entry_ok=result["entry_ok"],
            missing_entry=result["missing_entry"],
            missing_nodes=result["missing_nodes"],
            open_branches=result["open_branches"],
            errors=result["errors"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/force-end", response_model=RuleForceEndResponse)
def force_end(payload: RuleForceEndRequest):
    try:
        result = force_end_ruleset(
            ruleset=_rule_to_composer_dict(payload.rule.as_internal_dict()),
            mode=payload.mode,
            node_type=payload.node_type,
            label_prefix=payload.label_prefix,
        )
        rule = _normalize_rule_dict(result["ruleset"])
        return RuleForceEndResponse(
            updated=result["updated"],
            filled_count=result["filled_count"],
            created_node_ids=result["created_node_ids"],
            rule=rule,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/rule-elements/{element_id}", response_model=RuleElementFetchResponse)
def get_rule_element(element_id: str):
    try:
        return _fetch_rule_element_by_id(element_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GraphStoreNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Rule element not found: {element_id}") from exc
    except Exception as exc:
        graph_uri = _resolve_rule_element_graph_uri(element_id)
        error_bits = _publish_error_payload(
            "storage_error", "Temporary backend storage issue while fetching rule element", exc
        )
        return RuleElementFetchResponse(graph_uri=graph_uri, ttl="", rule_element=None, **error_bits)


@router.get("/{rule_id}", response_model=RuleFetchResponse)
def get_rule(rule_id: str):
    try:
        return _fetch_rule_by_id(rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GraphStoreNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}") from exc
    except Exception as exc:
        graph_uri = _resolve_rule_graph_uri(rule_id)
        error_bits = _publish_error_payload("storage_error", "Temporary backend storage issue while fetching rule", exc)
        return RuleFetchResponse(graph_uri=graph_uri, ttl="", rule=None, **error_bits)


@router.get("/{rule_id}/terms", response_model=RuleTermsResponse)
def get_rule_terms(
    rule_id: str,
    compact_terms: bool = Query(
        default=False,
        description="When true, the primary `value` fields use the local name after # or the final path segment.",
    ),
):
    try:
        response = _fetch_rule_by_id(rule_id)
        if response.rule is None:
            raise HTTPException(status_code=500, detail=f"Rule '{rule_id}' could not be decompiled")
        return _analyze_rule_terms(response.rule, compact_terms=compact_terms)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GraphStoreNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}") from exc
    except Exception as exc:
        error_bits = _publish_error_payload("rule_terms_error", "Unexpected error while extracting rule terms", exc)
        return RuleTermsResponse(
            rule_id=rule_id,
            compact_terms=compact_terms,
            target_classes=[],
            property_paths=[],
            target_property_pairs=[],
            target_class_local_name_groups=[],
            property_path_local_name_groups=[],
            stats=RuleTermStats(
                rule_count=0,
                element_count=0,
                rules_with_target_classes=0,
                rules_with_property_paths=0,
                rules_with_both=0,
                elements_with_target_class=0,
                elements_with_property_path=0,
                elements_with_both=0,
                unique_target_classes=0,
                unique_target_class_namespaces=0,
                unique_target_class_local_names=0,
                unique_property_paths=0,
                unique_property_path_namespaces=0,
                unique_property_path_local_names=0,
                unique_target_property_pairs=0,
                target_class_local_name_collisions=0,
                property_path_local_name_collisions=0,
            ),
            **error_bits,
        )


@router.post("/terms/aggregate", response_model=RuleTermsAggregateResponse)
def aggregate_rule_terms(payload: RuleTermsAggregateRequest):
    if len(payload.rule_ids) < 2:
        raise HTTPException(status_code=400, detail="At least two rule_ids are required")

    try:
        all_elements: list[dict] = []
        all_target_classes: list[str] = []
        all_property_paths: list[str] = []
        all_pair_keys: set[tuple[str, str]] = set()
        rules_with_target_classes = 0
        rules_with_property_paths = 0
        rules_with_both = 0

        for rule_id in payload.rule_ids:
            response = _fetch_rule_by_id(rule_id)
            if response.rule is None:
                raise HTTPException(status_code=500, detail=f"Rule '{rule_id}' could not be decompiled")
            collected = _collect_rule_term_values(response.rule)
            all_elements.extend(collected["elements"])
            all_target_classes.extend(collected["target_classes_raw"])
            all_property_paths.extend(collected["property_paths_raw"])
            all_pair_keys.update(collected["pair_keys"])
            rules_with_target_classes += 1 if collected["has_target_classes"] else 0
            rules_with_property_paths += 1 if collected["has_property_paths"] else 0
            rules_with_both += 1 if collected["has_both"] else 0

        unique_target_classes = sorted(set(all_target_classes))
        unique_property_paths = sorted(set(all_property_paths))
        pair_keys = sorted(all_pair_keys)
        terms_payload = _build_terms_payload(
            compact_terms=payload.compact_terms,
            elements=all_elements,
            unique_target_classes=unique_target_classes,
            unique_property_paths=unique_property_paths,
            pair_keys=pair_keys,
        )

        return RuleTermsAggregateResponse(
            rule_ids=payload.rule_ids,
            compact_terms=payload.compact_terms,
            target_classes=terms_payload["target_classes"],
            property_paths=terms_payload["property_paths"],
            target_property_pairs=terms_payload["target_property_pairs"],
            target_class_local_name_groups=terms_payload["target_class_local_name_groups"],
            property_path_local_name_groups=terms_payload["property_path_local_name_groups"],
            stats=_build_rule_term_stats(
                rule_count=len(payload.rule_ids),
                rules_with_target_classes=rules_with_target_classes,
                rules_with_property_paths=rules_with_property_paths,
                rules_with_both=rules_with_both,
                elements=all_elements,
                unique_target_classes=unique_target_classes,
                unique_property_paths=unique_property_paths,
                pair_keys=pair_keys,
                target_class_groups=terms_payload["target_class_groups"],
                property_path_groups=terms_payload["property_path_groups"],
            ),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GraphStoreNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        error_bits = _publish_error_payload(
            "rule_terms_aggregate_error",
            "Unexpected error while aggregating rule terms",
            exc,
        )
        return RuleTermsAggregateResponse(
            rule_ids=payload.rule_ids,
            compact_terms=payload.compact_terms,
            target_classes=[],
            property_paths=[],
            target_property_pairs=[],
            target_class_local_name_groups=[],
            property_path_local_name_groups=[],
            stats=RuleTermStats(
                rule_count=len(payload.rule_ids),
                element_count=0,
                rules_with_target_classes=0,
                rules_with_property_paths=0,
                rules_with_both=0,
                elements_with_target_class=0,
                elements_with_property_path=0,
                elements_with_both=0,
                unique_target_classes=0,
                unique_target_class_namespaces=0,
                unique_target_class_local_names=0,
                unique_property_paths=0,
                unique_property_path_namespaces=0,
                unique_property_path_local_names=0,
                unique_target_property_pairs=0,
                target_class_local_name_collisions=0,
                property_path_local_name_collisions=0,
            ),
            **error_bits,
        )


@router.get(
    "/{rule_id}/ttl",
    response_class=PlainTextResponse,
    responses={
        200: {
            "content": {
                "text/turtle": {
                    "example": """PREFIX ex: <http://example.org/firebim/>
PREFIX fb: <http://example.org/firebim/rule/>
PREFIX sh: <http://www.w3.org/ns/shacl#>

ex:room_area_rule a ex:RuleSet ;
    ex:entryPoint ex:check_room_area .
"""
                }
            },
            "description": "Raw Turtle for the rule graph",
        }
    },
)
def get_rule_ttl(rule_id: str):
    try:
        _graph_uri, ttl = fetch_rule_ttl(rule_id)
        return PlainTextResponse(
            content=ttl,
            media_type="text/turtle; charset=utf-8",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch raw TTL for rule '{rule_id}': {exc}",
        ) from exc


@router.get("/{rule_id}/ttl.json", response_model=RuleTtlResponse)
def get_rule_ttl_json(rule_id: str):
    try:
        graph_uri, ttl = fetch_rule_ttl(rule_id)
        return RuleTtlResponse(
            graph_uri=graph_uri,
            ttl=ttl,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch TTL JSON for rule '{rule_id}': {exc}",
        ) from exc
