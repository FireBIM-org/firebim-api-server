from rdflib import RDF, BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import SH

from .models import fbRuleElement


class SHACLBuilder:
    """
    Converts fbRuleElement objects into RDF.

    decision -> SHACL NodeShape + SPARQL rule
    start/end -> lightweight RDF metadata
    """

    def __init__(self, base_uri="http://example.org/firebim/"):
        self.graph = Graph()

        self.EX = Namespace(base_uri)
        self.FB = Namespace(base_uri + "rule/")

        self.graph.bind("sh", SH)
        self.graph.bind("ex", self.EX)
        self.graph.bind("fb", self.FB)

    @staticmethod
    def _looks_like_absolute_uri(value) -> bool:
        if not isinstance(value, str):
            return False
        return value.startswith("http://") or value.startswith("https://")

    def _format_comparison_value(self, value):
        if isinstance(value, URIRef):
            return f"<{value}>"
        if self._looks_like_absolute_uri(value):
            return f"<{value}>"
        if isinstance(value, str):
            return f'"{value}"'
        return str(value)

    def _build_comparison(self, element):
        op = element.operator
        value = element.value

        if op in ["<=", ">=", "<", ">"]:
            return f"?h {op} {value}"

        if op == "==":
            if isinstance(value, (list, tuple)):
                formatted = [self._format_comparison_value(v) for v in value]
                values = ", ".join(formatted)
                return f"?h IN ({values})"
            else:
                return f"?h = {self._format_comparison_value(value)}"

        if op == "between":
            lower, upper = value
            return f"(?h >= {lower} && ?h < {upper})"

        raise ValueError(f"Unsupported operator {op}")

    def _add_metadata_node(self, element: fbRuleElement):
        node_uri = self.EX[element.id]
        self.graph.add((node_uri, RDF.type, self.EX.RuleElement))
        self.graph.add((node_uri, self.EX.nodeType, Literal(element.node_type)))

        if element.label is not None:
            self.graph.add((node_uri, self.EX.label, Literal(element.label)))

        if element.value is not None:
            self.graph.add((node_uri, self.EX.value, Literal(str(element.value))))

        if element.true_path:
            self.graph.add((node_uri, self.FB.truePath, self.EX[element.true_path]))
        if element.false_path:
            self.graph.add((node_uri, self.FB.falsePath, self.EX[element.false_path]))
        if element.unknown_path:
            self.graph.add((node_uri, self.FB.unknownPath, self.EX[element.unknown_path]))

        if isinstance(element.config, dict):
            terminal_value = element.config.get("terminal_value")
            end_kind = element.config.get("end_kind")
            output_text = element.config.get("output_text")

            if terminal_value is not None:
                terminal_text = str(terminal_value)
                normalized = terminal_text.strip().lower()
                self.graph.add((node_uri, self.EX.value, Literal(terminal_text)))
                if end_kind is None:
                    if normalized in {"pass", "passed", "true", "success"}:
                        end_kind = "passed"
                    elif normalized in {"fail", "failed", "false", "error"}:
                        end_kind = "failed"
                    elif normalized in {"unknown", "missing_data", "missing-data"}:
                        end_kind = "unknown"
                if output_text is None:
                    output_text = terminal_text

            if end_kind is not None:
                self.graph.add((node_uri, self.EX.endKind, Literal(str(end_kind))))
            if output_text is not None:
                self.graph.add((node_uri, self.EX.outputText, Literal(str(output_text))))

    def add_rule_element(self, element: fbRuleElement):
        if element.node_type in {"start", "end"}:
            self._add_metadata_node(element)
            return

        if element.node_type != "decision":
            raise ValueError(f"Unsupported node_type: {element.node_type}")

        shape_uri = self.EX[element.id]
        self.graph.add((shape_uri, RDF.type, SH.NodeShape))
        self.graph.add((shape_uri, self.EX.nodeType, Literal(element.node_type)))

        # target_class may be compact local name or full URI
        if element.target_class and str(element.target_class).startswith("http"):
            self.graph.add((shape_uri, SH.targetClass, URIRef(element.target_class)))
        else:
            self.graph.add((shape_uri, SH.targetClass, self.EX[element.target_class]))

        if element.label:
            self.graph.add((shape_uri, self.EX.label, Literal(element.label)))

        property_uri = element.property_path
        comparison = self._build_comparison(element)

        sparql = f"""
        CONSTRUCT {{
            ?this fb:ruleResult ?result .
        }}
        WHERE {{

            OPTIONAL {{ ?this <{property_uri}> ?h }}

            BIND(
                IF(!BOUND(?h), fb:Unknown,
                    IF({comparison}, fb:True, fb:False)
                ) AS ?result
            )

        }}
        """

        rule_node = BNode()
        self.graph.add((shape_uri, SH.rule, rule_node))
        self.graph.add((rule_node, RDF.type, SH.SPARQLRule))
        self.graph.add((rule_node, SH.construct, Literal(sparql)))

        if element.true_path:
            self.graph.add((shape_uri, self.FB.truePath, self.EX[element.true_path]))
        if element.false_path:
            self.graph.add((shape_uri, self.FB.falsePath, self.EX[element.false_path]))
        if element.unknown_path:
            self.graph.add((shape_uri, self.FB.unknownPath, self.EX[element.unknown_path]))

    def add_rule_set(self, rule_set):
        ruleset_uri = self.EX[rule_set.id]
        entry_id = getattr(rule_set, "entry", None) or getattr(rule_set, "entry_element", None)
        if not entry_id:
            raise ValueError("Rule set is missing an entry/entry_element identifier")
        entry_uri = self.EX[entry_id]

        self.graph.add((ruleset_uri, RDF.type, self.EX.RuleSet))
        self.graph.add((ruleset_uri, self.EX.entryPoint, entry_uri))

        for element in rule_set.elements.values():
            self.add_rule_element(element)

    def serialize(self, path):
        self.graph.serialize(path, format="turtle")

    def get_graph(self):
        return self.graph
