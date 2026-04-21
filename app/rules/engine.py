# firebim/rules/engine.py


class RuleEngine:
    def __init__(self, rule_set, evaluator):
        self.rule_set = rule_set
        self.evaluator = evaluator  # function or class

    def execute(self, start=None, data_graph=None):

        if start is None:
            start = self.rule_set.entry

        visited = set()
        current = start
        previous = None

        while True:
            if current in visited:
                raise ValueError(f"Cycle detected at '{current}'")

            visited.add(current)

            element = self.rule_set.get_element(current)

            # ----------------------------------
            # CONTROL / END NODES
            # ----------------------------------

            if element.node_type != "decision":
                if element.operator == "default":
                    return element.config["value"]

                if element.operator == "assume":
                    result = element.config["value"]

                elif element.operator == "skip":
                    current = element.config["next"]
                    continue

                elif element.operator == "explain":
                    context = previous or element
                    return {
                        "status": "Unknown",
                        "rule_id": context.id,
                        "target": context.target_class,
                        "condition": {
                            "property": context.property_path,
                            "operator": context.operator,
                            "value": context.value,
                        },
                    }
                elif element.node_type == "end":
                    terminal_value = element.config.get("terminal_value")
                    if terminal_value is not None:
                        return terminal_value
                    return element.config.get("value")
                else:
                    raise ValueError(f"Unsupported control node '{element.id}' with operator '{element.operator}'")

            else:
                result = self.evaluator(element, data_graph)

            # ----------------------------------

            previous = element

            if result == "True":
                next_rule = element.true_path
            elif result == "False":
                next_rule = element.false_path
            else:
                next_rule = element.unknown_path

            if not next_rule:
                return result

            current = next_rule
