import json

from rdflib import RDF, BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import SH

from .models import fbRuleElement
from .paths import expand_curie, expand_property_path, is_ref_value, ref_path


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

    @staticmethod
    def _sparql_path(segments: list[str]) -> str:
        return "/".join(f"^<{seg[1:]}>" if seg.startswith("^") else f"<{seg}>" for seg in segments)

    def _build_comparison(self, element):
        op = element.operator
        value = element.value

        if is_ref_value(value):
            if op == "between":
                raise ValueError("'between' cannot be combined with a $ref value")
            sparql_op = "=" if op == "==" else op
            return f"?h {sparql_op} ?v"

        if op == "exists":
            return "BOUND(?h)"

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

            next_rule = element.config.get("next_rule")
            if next_rule:
                self.graph.add((node_uri, self.EX.nextRule, Literal(str(next_rule))))
                if end_kind is None:
                    end_kind = "rule"
            if end_kind is not None:
                self.graph.add((node_uri, self.EX.endKind, Literal(str(end_kind))))
            if output_text is not None:
                self.graph.add((node_uri, self.EX.outputText, Literal(str(output_text))))

    def add_rule_element(self, element: fbRuleElement):
        if element.node_type in {"start", "end"}:
            self._add_metadata_node(element)
            return

        if element.node_type == "derive":
            self._add_derive_node(element)
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

        prefixes = element.config.get("prefixes") if isinstance(element.config, dict) else None
        segments = expand_property_path(element.property_path, prefixes)
        property_path = self._sparql_path(segments)
        comparison = self._build_comparison(element)
        ref_clause = ""
        unknown_test = "false" if element.operator == "exists" else "!BOUND(?h)"
        if is_ref_value(element.value):
            ref_segments = expand_property_path(ref_path(element.value), prefixes)
            ref_clause = f"OPTIONAL {{ ?this {self._sparql_path(ref_segments)} ?v }}"
            unknown_test = "!BOUND(?h) || !BOUND(?v)"

        # `?this` must be bound for every instance of the target class, also when the property is absent —
        # otherwise a missing value yields no result at all instead of Unknown / False (`exists`).
        target_iri = (
            f"<{element.target_class}>"
            if element.target_class and str(element.target_class).startswith("http")
            else f"<{self.EX[element.target_class]}>"
        )
        sparql = f"""
        CONSTRUCT {{
            ?this fb:ruleResult ?result .
        }}
        WHERE {{
            ?this a {target_iri} .
            OPTIONAL {{ ?this {property_path} ?h }}
            {ref_clause}

            BIND(
                IF({unknown_test}, fb:Unknown,
                    IF({comparison}, fb:True, fb:False)
                ) AS ?result
            )

        }}
        """
        # explicit, lossless copies of the condition (the CONSTRUCT is for SPARQL engines)
        self.graph.add((shape_uri, self.EX.propertyPath, Literal("/".join(segments))))
        self.graph.add((shape_uri, self.EX.operator, Literal(str(element.operator))))
        self.graph.add((shape_uri, self.EX.valueJson, Literal(json.dumps(element.value, ensure_ascii=False))))

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

    def _add_derive_node(self, element: fbRuleElement):
        node_uri = self.EX[element.id]
        prefixes = element.config.get("prefixes") if isinstance(element.config, dict) else None
        self.graph.add((node_uri, RDF.type, self.EX.RuleElement))
        self.graph.add((node_uri, self.EX.nodeType, Literal("derive")))
        if element.label is not None:
            self.graph.add((node_uri, self.EX.label, Literal(element.label)))
        if element.target_class and str(element.target_class).startswith("http"):
            self.graph.add((node_uri, SH.targetClass, URIRef(element.target_class)))
        elif element.target_class:
            self.graph.add((node_uri, SH.targetClass, URIRef(expand_curie(element.target_class, prefixes))))
        written = expand_curie(str(element.property_path), prefixes)
        self.graph.add((node_uri, self.EX.propertyPath, Literal(written)))
        self.graph.add((node_uri, self.EX.expression, Literal(str(element.expression))))
        if prefixes:
            self.graph.add((node_uri, self.EX.prefixesJson, Literal(json.dumps(prefixes))))
        if element.true_path:
            self.graph.add((node_uri, self.FB.truePath, self.EX[element.true_path]))
        if element.unknown_path:
            self.graph.add((node_uri, self.FB.unknownPath, self.EX[element.unknown_path]))

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
