import logging

from fastapi import APIRouter, HTTPException

from app.rules.composer import (
    check_ruleset_completeness,
    connect_ruleset_nodes,
    disconnect_ruleset_node_branch,
    force_end_ruleset,
)
from app.rules.decompiler import decompile_rule_element_ttl, decompile_ruleset_ttl
from app.rules.dsl import RuleElementBuilder
from app.rules.element_validator import validate_rule_element_payload
from app.rules.models import fbRuleElement
from app.rules.publisher import (
    default_rule_element_graph_uri,
    default_rule_graph_uri,
    delete_graph_from_fuseki,
    fetch_ttl_from_fuseki,
    forget_graph,
    list_rule_element_entries,
    list_rule_graphs,
    publish_ttl_to_fuseki,
    remember_rule_element_graph,
    remember_rule_graph,
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
    RuleValidateRequest,
    RuleValidateResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/rules", tags=["rules"])


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
    return rule


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
    ttl = fetch_ttl_from_fuseki(dataset="rulebook", graph_uri=graph_uri)
    element = decompile_rule_element_ttl(ttl)
    return _rule_element_to_json(element, graph_uri=graph_uri)


def _fetch_rule_by_id(rule_id: str) -> RuleFetchResponse:
    graph_uri = _resolve_rule_graph_uri(rule_id)
    ttl = fetch_ttl_from_fuseki(dataset="rulebook", graph_uri=graph_uri)
    rule = None
    try:
        rule = _normalize_rule_dict(decompile_ruleset_ttl(ttl))
        if rule is not None:
            remember_rule_graph(
                graph_uri=graph_uri,
                rule_id=rule.get("id"),
                entry=rule.get("entry"),
                label=None,
                element_count=len(rule.get("elements", [])),
            )
    except Exception:
        logger.exception("Rule fetch succeeded but SHACL decompile failed")
        rule = None
    return RuleFetchResponse(graph_uri=graph_uri, ttl=ttl, rule=rule)


def _fetch_rule_element_by_id(element_id: str) -> RuleElementFetchResponse:
    graph_uri = _resolve_rule_element_graph_uri(element_id)
    ttl = fetch_ttl_from_fuseki(dataset="rulebook", graph_uri=graph_uri)
    rule_element = None
    try:
        rule_element = decompile_rule_element_ttl(ttl)
        if rule_element is not None:
            element_type = rule_element.get("element_type") or rule_element.get("node_type") or "decision"
            remember_rule_element_graph(
                graph_uri=graph_uri,
                element_id=rule_element.get("id"),
                element_type=element_type,
                label=rule_element.get("label"),
            )
    except Exception:
        logger.exception("Rule element fetch succeeded but SHACL decompile failed")
        rule_element = None
    return RuleElementFetchResponse(graph_uri=graph_uri, ttl=ttl, rule_element=rule_element)


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


@router.get("/health")
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
        count = len(rule.elements)
        return RuleValidateResponse(
            valid=True,
            rule_id=rule.id,
            entry=rule.entry,
            element_count=count,
            errors=[],
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
        builder = SHACLBuilder()
        builder.add_rule_set(rule)
        ttl = builder.get_graph().serialize(format="turtle")
        count = len(rule.elements)
        return RuleCompileResponse(
            rule_id=rule.id,
            element_count=count,
            ttl=str(ttl),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
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
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        error_bits = _publish_error_payload("rule_decompile_error", "Unexpected error while decompiling SHACL rule", exc)
        return RuleDecompileResponse(rule=None, **error_bits)


@router.post("/publish", response_model=RulePublishResponse)
def publish_rule(payload: RulePublishRequest):
    graph_uri = _resolve_rule_graph_uri(payload.rule.id)

    try:
        rule = _build_rule(payload.rule)
        builder = SHACLBuilder()
        builder.add_rule_set(rule)
        ttl = builder.get_graph().serialize(format="turtle")
        publish_ttl_to_fuseki(ttl_text=str(ttl), dataset="rulebook", graph_uri=graph_uri)

        count = len(rule.elements)
        label = _rule_label(payload.rule)
        remember_rule_graph(
            graph_uri=graph_uri,
            rule_id=rule.id,
            entry=rule.entry,
            label=label,
            element_count=count,
        )

        return RulePublishResponse(
            published=True,
            rule_id=rule.id,
            graph_uri=graph_uri,
            entry=rule.entry,
            element_count=count,
            label=label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        count = len(payload.rule.elements)
        error_bits = _publish_error_payload("fuseki_error", "Temporary backend storage issue while publishing rule", exc)
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
        error_bits = _publish_error_payload("fuseki_error", "Rule list unavailable, returning fallback response", exc)
        return RuleListResponse(count=0, rules=[], warning="Fuseki temporarily unavailable", **error_bits)


@router.post("/fetch", response_model=RuleFetchResponse)
def fetch_rule(payload: RuleFetchRequest):
    try:
        return _fetch_rule_by_id(payload.rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        graph_uri = _resolve_rule_graph_uri(payload.rule_id)
        error_bits = _publish_error_payload("fuseki_error", "Temporary backend storage issue while fetching rule", exc)
        return RuleFetchResponse(graph_uri=graph_uri, ttl="", rule=None, **error_bits)


@router.post("/delete", response_model=RuleDeleteResponse)
def delete_rule(payload: RuleDeleteRequest):
    graph_uri = _resolve_rule_graph_uri(payload.rule_id)

    try:
        delete_graph_from_fuseki(dataset="rulebook", graph_uri=graph_uri)
        forget_graph(graph_uri)
        return RuleDeleteResponse(deleted=True, graph_uri=graph_uri)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        error_bits = _publish_error_payload("fuseki_error", "Temporary backend storage issue while deleting rule", exc)
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
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        error_bits = _publish_error_payload("assembly_compile_error", "Unexpected error while assembling and compiling rule", exc)
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
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error while deriving rule element", exc_info=exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/rule-elements", response_model=RuleElementPublishResponse)
def publish_rule_element(payload: RuleElementPublishRequest):
    graph_uri = _resolve_rule_element_graph_uri(payload.id)
    node_data = payload.as_internal_dict()
    validate_rule_element_payload(node_data)

    try:
        element = fbRuleElement(
            id=payload.id,
            target_class=payload.target_class,
            property_path=payload.property_path,
            operator=payload.operator,
            value=payload.value,
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
        publish_ttl_to_fuseki(ttl_text=str(ttl), dataset="rulebook", graph_uri=graph_uri)

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
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        error_bits = _publish_error_payload("fuseki_error", "Temporary backend storage issue while publishing rule element", exc)
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
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        graph_uri = _resolve_rule_element_graph_uri(payload.element_id)
        error_bits = _publish_error_payload("fuseki_error", "Temporary backend storage issue while fetching rule element", exc)
        return RuleElementFetchResponse(graph_uri=graph_uri, ttl="", rule_element=None, **error_bits)


@router.post("/rule-elements/delete", response_model=RuleElementDeleteResponse)
def delete_rule_element(payload: RuleElementDeleteRequest):
    graph_uri = _resolve_rule_element_graph_uri(payload.element_id)

    try:
        delete_graph_from_fuseki(dataset="rulebook", graph_uri=graph_uri)
        forget_graph(graph_uri)
        return RuleElementDeleteResponse(deleted=True, graph_uri=graph_uri)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        error_bits = _publish_error_payload("fuseki_error", "Temporary backend storage issue while deleting rule element", exc)
        return RuleElementDeleteResponse(deleted=False, graph_uri=graph_uri, **error_bits)


@router.get("/rule-elements", response_model=RuleElementListResponse)
def list_rule_elements():
    try:
        entries = [RuleElementListItem(**item) for item in list_rule_element_entries()]
        graph_uris = [item.graph_uri for item in entries]
        return RuleElementListResponse(count=len(entries), graph_uris=graph_uris, rule_elements=entries)
    except Exception as exc:
        error_bits = _publish_error_payload("fuseki_error", "Rule element list unavailable, returning fallback response", exc)
        return RuleElementListResponse(
            count=0,
            graph_uris=[],
            rule_elements=[],
            warning="Fuseki temporarily unavailable",
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
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/rule-elements/{element_id}", response_model=RuleElementFetchResponse)
def get_rule_element(element_id: str):
    try:
        return _fetch_rule_element_by_id(element_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        graph_uri = _resolve_rule_element_graph_uri(element_id)
        error_bits = _publish_error_payload("fuseki_error", "Temporary backend storage issue while fetching rule element", exc)
        return RuleElementFetchResponse(graph_uri=graph_uri, ttl="", rule_element=None, **error_bits)


@router.get("/{rule_id}", response_model=RuleFetchResponse)
def get_rule(rule_id: str):
    try:
        return _fetch_rule_by_id(rule_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        graph_uri = _resolve_rule_graph_uri(rule_id)
        error_bits = _publish_error_payload("fuseki_error", "Temporary backend storage issue while fetching rule", exc)
        return RuleFetchResponse(graph_uri=graph_uri, ttl="", rule=None, **error_bits)
