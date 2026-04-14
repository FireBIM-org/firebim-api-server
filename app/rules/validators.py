from .models import fbRule


def validate_paths(rule: fbRule) -> None:
    """
    Ensures that all rule element paths reference valid elements in the rule.
    """

    for element in rule.elements.values():
        for path in [element.true_path, element.false_path, element.unknown_path]:
            if path and path not in rule.elements:
                raise ValueError(f"RuleElement '{element.id}' references unknown element '{path}'")
