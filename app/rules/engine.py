# firebim/rules/engine.py


class RuleEngine:
    """In-memory walker over fbRule / fbRuleElement (tests and tooling; the check runtimes are in app.checks).

    `evaluator(element, data_graph)` returns "True" | "False" | "Unknown" for decision elements.
    `derive` elements are evaluated with the expression language; inputs are read through `read_value(target, path)`
    when given (else from `data_graph` treated as a dict {path: value}) and results are written to `self.derived`.
    """

    def __init__(self, rule_set, evaluator, *, read_value=None, target=None):
        self.rule_set = rule_set
        self.evaluator = evaluator  # function or class
        self.read_value = read_value
        self.target = target
        self.derived: dict[str, object] = {}

    def _run_derive(self, element, data_graph):
        from app.rules.expressions import ExpressionUnknown, evaluate, parse_expression
        from app.rules.paths import expand_curie

        prefixes = element.config.get("prefixes") if isinstance(element.config, dict) else None

        def resolve(path):
            key = expand_curie(path, prefixes)
            if key in self.derived:
                return self.derived[key]
            if self.read_value is not None:
                return self.read_value(self.target, key)
            if isinstance(data_graph, dict) and key in data_graph:
                return data_graph[key]
            raise ExpressionUnknown("missing_required_input", f"no value for {path}")

        try:
            value = evaluate(parse_expression(str(element.expression)), resolve)
        except ExpressionUnknown:
            return "Unknown"
        self.derived[expand_curie(str(element.property_path), prefixes)] = value
        return "True"

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

            if element.node_type == "derive":
                result = self._run_derive(element, data_graph)
            elif element.node_type != "decision":
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
