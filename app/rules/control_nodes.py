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
        config={},
    )


# ----------------------------------
# 2. Default outcome
# ----------------------------------


def default_outcome(rule_id, value):
    return fbRuleElement(
        id=rule_id,
        node_type="end",
        operator="default",
        config={"value": value},
    )


# ----------------------------------
# 3. Assume rule (override)
# ----------------------------------


def assume(rule_id, value):
    raise NotImplementedError(
        "assume() is not yet supported by the current validator/compiler/runtime. "
        "Use a supported start/end/decision node until advanced control-node behavior is implemented."
    )


# ----------------------------------
# 4. Skip rule
# ----------------------------------


def skip(rule_id, next_rule):
    raise NotImplementedError(
        "skip() is not yet supported by the current validator/compiler/runtime. "
        "Use explicit rule links instead until advanced control-node behavior is implemented."
    )
