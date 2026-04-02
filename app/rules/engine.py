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
                    return {
                        "status": "Unknown",
                        "rule_id": previous.id,
                        "target": previous.target_class,
                        "condition": {
                            "property": previous.property_path,
                            "operator": previous.operator,
                            "value": previous.value
                        }
                    }

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