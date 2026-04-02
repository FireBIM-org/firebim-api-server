# firebim/rules/control_nodes.py

from .models import fbRuleElement


# ----------------------------------
# 1. Explain missing (Unknown)
# ----------------------------------

def explain_unknown(rule_id="explain_unknown"):

    return fbRuleElement(
        id=rule_id,
        node_type="end",
        operator="explain",
        config={}
    )


# ----------------------------------
# 2. Default outcome
# ----------------------------------

def default_outcome(rule_id, value):

    return fbRuleElement(
        id=rule_id,
        node_type="end",
        operator="default",
        config={"value": value}
    )


# ----------------------------------
# 3. Assume rule (override)
# ----------------------------------

def assume(rule_id, value):

    return fbRuleElement(
        id=rule_id,
        node_type="control",
        operator="assume",
        config={"value": value}
    )


# ----------------------------------
# 4. Skip rule
# ----------------------------------

def skip(rule_id, next_rule):

    return fbRuleElement(
        id=rule_id,
        node_type="control",
        operator="skip",
        config={"next": next_rule}
    )