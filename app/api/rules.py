from fastapi import APIRouter, HTTPException

from app.rules.dsl import Rule
from app.rules.models import fbRuleElement
from app.rules.ruleset import RuleSet
from app.rules.shacl_builder import SHACLBuilder
from app.rules.element_validator import validate_rule_element_payload
from app.rules.decompiler import decompile_ruleset_ttl, decompile_rule_element_ttl
from app.rules.composer import (
    connect_ruleset_nodes,
    disconnect_ruleset_node_branch,
    check_ruleset_completeness,
    force_end_ruleset,
)
from app.rules.publisher import (
    publish_ttl_to_fuseki,
    fetch_ttl_from_fuseki,
    delete_graph_from_fuseki,
    default_ruleset_graph_uri,
    default_rule_element_graph_uri,
    list_rule_element_graphs,
)
from app.schemas.rules import (
    RuleSetValidateRequest,
    RuleSetValidateResponse,
    RuleSetCompileResponse,
    RuleSetDecompileRequest,
    RuleSetDecompileResponse,
    RuleSetPublishRequest,
    RuleSetPublishResponse,
    RuleSetFetchRequest,
    RuleSetFetchResponse,
    RuleSetDeleteRequest,
    RuleSetDeleteResponse,
    RuleElementPublishRequest,
    RuleElementPublishResponse,
    RuleElementFetchRequest,
    RuleElementFetchResponse,
    RuleElementDeleteRequest,
    RuleElementDeleteResponse,
    RuleElementListResponse,
    RuleSetDraft,
    RuleConnectRequest,
    RuleConnectResponse,
    RuleDisconnectRequest,
    RuleDisconnectResponse,
    RuleCompletenessRequest,
    RuleCompletenessResponse,
    RuleForceEndRequest,
    RuleForceEndResponse,
    RuleElementJson,
    RuleElementJsonResponse,
    RuleElementDeriveRequest,
    RuleElementDeriveResponse,
)



router = APIRouter(prefix="/rules", tags=["rules"])


def _rule_element_to_json(element: dict, graph_uri: str | None = None) -> RuleElementJson:
    return RuleElementJson(
        id=element.get("id"),
        node_type=element.get("node_type") or "decision",
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


def _fetch_rule_element_json_by_graph_uri(graph_uri: str) -> RuleElementJson:
    ttl = fetch_ttl_from_fuseki(
        dataset="rulebook",
        graph_uri=graph_uri,
    )
    element = decompile_rule_element_ttl(ttl)
    return _rule_element_to_json(element, graph_uri=graph_uri)

@router.get("/health")
def rules_health():
    """
    Simple health check that proves:
    - app.rules is importable
    - Rule DSL works
    """

    # Create a tiny rule (no external dependencies)
    test_rule = (
        Rule("health_check_rule") #Name and ID of the rule
        .target("Test") #Target class for the rule, this is ontology-specific and can be any URI  
        .when("FBO.roomArea") #Property to evaluate, this is also ontology-specific and can be any URI - ps dont use quotes! - this is just an examplei
        .gt(1) #Operator and value to evaluate against, in this case "greater than 1"
        .true("ok") #Path to set if the rule evaluates to true, this is application-specific and can be any string
        .false("fail") #Path to set if the rule evaluates to false, this is application-specific and can be any string
        .unknown("missing") #Path to set if the rule evaluates to unknown, this is application-specific and can be any string
        .build()
    )

    return {
        "status": "ok",
        "rule_id": test_rule.id,
        "target": test_rule.target_class,
        "operator": test_rule.operator,
    }

@router.post("/validate", response_model=RuleSetValidateResponse)
def validate_ruleset(payload: RuleSetValidateRequest):
    try:
        ruleset = RuleSet(payload.id)

        for node in payload.nodes:
            element = fbRuleElement(
                id=node.id,
                target_class=node.target_class,
                property_path=node.property_path,
                operator=node.operator,
                value=node.value,
                true_path=node.true_path,
                false_path=node.false_path,
                unknown_path=node.unknown_path,
                label=node.label,
                node_type=node.node_type,
                config=node.config,
            )
            ruleset.add(element)

        ruleset.start(payload.entry)
        ruleset.validate()

        return RuleSetValidateResponse(
            valid=True,
            ruleset_id=ruleset.id,
            entry=ruleset.entry,
            node_count=len(ruleset.elements),
            errors=[],
        )

    except ValueError as e:
        return RuleSetValidateResponse(
            valid=False,
            ruleset_id=payload.id,
            entry=payload.entry,
            node_count=len(payload.nodes),
            errors=[str(e)],
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/compile", response_model=RuleSetCompileResponse)
def compile_ruleset(payload: RuleSetValidateRequest):
    try:
        ruleset = RuleSet(payload.id)

        for node in payload.nodes:
            element = fbRuleElement(
                id=node.id,
                target_class=node.target_class,
                property_path=node.property_path,
                operator=node.operator,
                value=node.value,
                true_path=node.true_path,
                false_path=node.false_path,
                unknown_path=node.unknown_path,
                label=node.label,
                node_type=node.node_type,
                config=node.config,
            )
            ruleset.add(element)

        ruleset.start(payload.entry)
        ruleset.validate()

        builder = SHACLBuilder()
        builder.add_rule_set(ruleset)

        ttl = builder.get_graph().serialize(format="turtle")

        return RuleSetCompileResponse(
            ruleset_id=ruleset.id,
            node_count=len(ruleset.elements),
            ttl=str(ttl),
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/decompile", response_model=RuleSetDecompileResponse)
def decompile_ruleset(payload: RuleSetDecompileRequest):
    try:
        ruleset = decompile_ruleset_ttl(payload.ttl)
        return RuleSetDecompileResponse(ruleset=ruleset)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/publish", response_model=RuleSetPublishResponse)
def publish_ruleset(payload: RuleSetPublishRequest):
    try:
        ruleset = RuleSet(payload.id)

        for node in payload.nodes:
            element = fbRuleElement(
                id=node.id,
                target_class=node.target_class,
                property_path=node.property_path,
                operator=node.operator,
                value=node.value,
                true_path=node.true_path,
                false_path=node.false_path,
                unknown_path=node.unknown_path,
                label=node.label,
                node_type=node.node_type,
                config=node.config,
            )
            ruleset.add(element)

        ruleset.start(payload.entry)
        ruleset.validate()

        builder = SHACLBuilder()
        builder.add_rule_set(ruleset)

        ttl = builder.get_graph().serialize(format="turtle")
        graph_uri = payload.graph_uri or default_ruleset_graph_uri(ruleset.id)

        publish_ttl_to_fuseki(
            ttl_text=str(ttl),
            dataset="rulebook",
            graph_uri=graph_uri,
        )

        return RuleSetPublishResponse(
            published=True,
            ruleset_id=ruleset.id,
            graph_uri=graph_uri,
            node_count=len(ruleset.elements),
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/fetch", response_model=RuleSetFetchResponse)
def fetch_ruleset(payload: RuleSetFetchRequest):
    try:
        ttl = fetch_ttl_from_fuseki(
            dataset="rulebook",
            graph_uri=payload.graph_uri,
        )

        ruleset = None
        try:
            ruleset = decompile_ruleset_ttl(ttl)
        except Exception:
            # keep fetch usable even if decompile fails
            ruleset = None

        return RuleSetFetchResponse(
            graph_uri=payload.graph_uri,
            ttl=ttl,
            ruleset=ruleset,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/delete", response_model=RuleSetDeleteResponse)
def delete_ruleset(payload: RuleSetDeleteRequest):
    try:
        delete_graph_from_fuseki(
            dataset="rulebook",
            graph_uri=payload.graph_uri,
        )

        return RuleSetDeleteResponse(
            deleted=True,
            graph_uri=payload.graph_uri,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/rule-elements/{element_id}/json", response_model=RuleElementJsonResponse)
def get_rule_element_json(element_id: str):
    try:
        graph_uri = default_rule_element_graph_uri(element_id)
        element = _fetch_rule_element_json_by_graph_uri(graph_uri)
        return RuleElementJsonResponse(rule_element=element)

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/rule-elements/derive", response_model=RuleElementDeriveResponse)
def derive_rule_element(payload: RuleElementDeriveRequest):
    try:
        source_graph_uri = payload.source_graph_uri or default_rule_element_graph_uri(payload.source_id)
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

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/rule-elements", response_model=RuleElementPublishResponse)
def publish_rule_element(payload: RuleElementPublishRequest):
    node_data = payload.model_dump()
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
            node_type=payload.node_type,
            config=payload.config,
        )

        builder = SHACLBuilder()
        builder.add_rule_element(element)

        ttl = builder.get_graph().serialize(format="turtle")
        graph_uri = payload.graph_uri or default_rule_element_graph_uri(element.id)

        publish_ttl_to_fuseki(
            ttl_text=str(ttl),
            dataset="rulebook",
            graph_uri=graph_uri,
        )

        return RuleElementPublishResponse(
            published=True,
            element_id=element.id,
            graph_uri=graph_uri,
            node_type=element.node_type,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/rule-elements/fetch", response_model=RuleElementFetchResponse)
def fetch_rule_element(payload: RuleElementFetchRequest):
    try:
        ttl = fetch_ttl_from_fuseki(
            dataset="rulebook",
            graph_uri=payload.graph_uri,
        )

        rule_element = None
        try:
            rule_element = decompile_rule_element_ttl(ttl)
        except Exception:
            rule_element = None

        return RuleElementFetchResponse(
            graph_uri=payload.graph_uri,
            ttl=ttl,
            rule_element=rule_element,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/rule-elements/delete", response_model=RuleElementDeleteResponse)
def delete_rule_element(payload: RuleElementDeleteRequest):
    try:
        delete_graph_from_fuseki(
            dataset="rulebook",
            graph_uri=payload.graph_uri,
        )

        return RuleElementDeleteResponse(
            deleted=True,
            graph_uri=payload.graph_uri,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/rule-elements", response_model=RuleElementListResponse)
def list_rule_elements():
    try:
        graph_uris = list_rule_element_graphs(dataset="rulebook")

        return RuleElementListResponse(
            count=len(graph_uris),
            graph_uris=graph_uris,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/connect", response_model=RuleConnectResponse)
def connect_rules(payload: RuleConnectRequest):
    try:
        result = connect_ruleset_nodes(
            ruleset=payload.ruleset.model_dump(),
            source_id=payload.source_id,
            branch=payload.branch,
            target_id=payload.target_id,
        )

        return RuleConnectResponse(
            updated=result["updated"],
            overwritten=result["overwritten"],
            previous_target_id=result["previous_target_id"],
            ruleset=result["ruleset"],
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/disconnect", response_model=RuleDisconnectResponse)
def disconnect_rules(payload: RuleDisconnectRequest):
    try:
        result = disconnect_ruleset_node_branch(
            ruleset=payload.ruleset.model_dump(),
            source_id=payload.source_id,
            branch=payload.branch,
        )

        return RuleDisconnectResponse(
            updated=result["updated"],
            previous_target_id=result["previous_target_id"],
            ruleset=result["ruleset"],
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/check-completeness", response_model=RuleCompletenessResponse)
def check_completeness(payload: RuleCompletenessRequest):
    try:
        result = check_ruleset_completeness(
            ruleset=payload.ruleset.model_dump(),
        )

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

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/force-end", response_model=RuleForceEndResponse)
def force_end(payload: RuleForceEndRequest):
    try:
        result = force_end_ruleset(
            ruleset=payload.ruleset.model_dump(),
            mode=payload.mode,
            node_type=payload.node_type,
            label_prefix=payload.label_prefix,
        )

        return RuleForceEndResponse(
            updated=result["updated"],
            filled_count=result["filled_count"],
            created_node_ids=result["created_node_ids"],
            ruleset=result["ruleset"],
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/rule-elements/{element_id}/json")
def get_rule_element_json(element_id: str):
    try:
        graph_uri = default_rule_element_graph_uri(element_id)
        ttl = fetch_ttl_from_fuseki(
            dataset="rulebook",
            graph_uri=graph_uri,
        )
        rule_element = decompile_rule_element_ttl(ttl)
        return rule_element

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))