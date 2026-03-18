from fastapi import APIRouter, HTTPException

from app.rules.dsl import Rule
from app.rules.models import fbRuleElement
from app.rules.ruleset import RuleSet
from app.rules.shacl_builder import SHACLBuilder
from app.schemas.rules import (
    RuleSetValidateRequest,
    RuleSetValidateResponse,
    RuleSetCompileResponse,
)


router = APIRouter(prefix="/rules", tags=["rules"])


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