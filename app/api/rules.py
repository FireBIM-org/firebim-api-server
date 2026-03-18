from fastapi import APIRouter

# 👇 this is the important part — proves your module loads
from app.rules.dsl import Rule


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