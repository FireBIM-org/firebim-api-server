from rdflib import Graph, Namespace, RDF, Literal, BNode, URIRef
from rdflib.namespace import SH

from .models import fbRuleElement


class SHACLBuilder:

    """
    Converts fbRuleElement objects into SHACL NodeShapes
    using SPARQL rules that produce True / False / Unknown.
    """

    def __init__(self, base_uri="http://example.org/firebim/"):

        self.graph = Graph()

        self.EX = Namespace(base_uri)
        self.FB = Namespace(base_uri + "rule/")

        self.graph.bind("sh", SH)
        self.graph.bind("ex", self.EX)
        self.graph.bind("fb", self.FB)

    # --------------------------------------------------
    # simple comparison
    # --------------------------------------------------

    def _build_comparison(self, element):

        op = element.operator
        value = element.value

        if op in ["<=", ">=", "<", ">"]:
            return f"?h {op} {value}"

        if op == "==":

            if isinstance(value, (list, tuple)):

                formatted = []

                for v in value:

                    if isinstance(v, URIRef):
                        formatted.append(f"<{v}>")

                    elif isinstance(v, str):
                        formatted.append(f'"{v}"')

                    else:
                        formatted.append(str(v))

                values = ", ".join(formatted)

                return f"?h IN ({values})"

            else:

                if isinstance(value, URIRef):
                    return f"?h = <{value}>"

                if isinstance(value, str):
                    return f'?h = "{value}"'

                return f"?h = {value}"

        if op == "between":

            lower, upper = value
            return f"(?h >= {lower} && ?h < {upper})"

        raise ValueError(f"Unsupported operator {op}")

    # --------------------------------------------------
    # grouped rules (OR / AND)
    # --------------------------------------------------

    def _build_group(self, element):

        logic = element.value["logic"]
        conditions = element.value["conditions"]

        optionals = []
        expressions = []
        vars = []

        for i, cond in enumerate(conditions):

            var = f"?h{i}"
            vars.append(var)

            optionals.append(
                f"OPTIONAL {{ ?this <{cond.property_uri}> {var} }}"
            )

            if cond.operator in ["<=", ">=", "<", ">"]:
                expressions.append(f"{var} {cond.operator} {cond.value}")

            elif cond.operator == "==":

                if isinstance(cond.value, list):

                    values = ", ".join(f'"{v}"' for v in cond.value)
                    expressions.append(f"{var} IN ({values})")

                else:

                    expressions.append(f'{var} = "{cond.value}"')

        if logic == "OR":
            expr = " || ".join(expressions)
        else:
            expr = " && ".join(expressions)

        # detect if ALL inputs are missing
        bound_check = " && ".join([f"!BOUND({v})" for v in vars])

        optional_block = "\n".join(optionals)

        return optional_block, expr, bound_check


    # --------------------------------------------------

    def add_rule_element(self, element: fbRuleElement):
        
        if element.node_type != "decision":
            return  # skip SHACL generation

        shape_uri = self.EX[element.id]

        self.graph.add((shape_uri, RDF.type, SH.NodeShape))
        self.graph.add((shape_uri, SH.targetClass, self.EX[element.target_class]))

        if element.label:
            self.graph.add((shape_uri, self.EX.label, Literal(element.label)))

        # ----------------------------------------------
        # grouped rule
        # ----------------------------------------------

        if element.operator == "group":

            optionals, expr, bound_check = self._build_group(element)

            sparql = f"""
            CONSTRUCT {{
                ?this fb:ruleResult ?result .
            }}
            WHERE {{

                {optionals}

                BIND(
                    IF(
                        ({bound_check}),
                        fb:Unknown,

                        IF(
                            ({expr}),
                            fb:True,
                            fb:False
                        )
                    ) AS ?result
                )

            }}
            """

        # ----------------------------------------------
        # simple rule
        # ----------------------------------------------

        else:

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

        # ----------------------------------------------

        if element.true_path:
            self.graph.add((shape_uri, self.FB.truePath, self.EX[element.true_path]))

        if element.false_path:
            self.graph.add((shape_uri, self.FB.falsePath, self.EX[element.false_path]))

        if element.unknown_path:
            self.graph.add((shape_uri, self.FB.unknownPath, self.EX[element.unknown_path]))

    # --------------------------------------------------

    def add_rule_set(self, rule_set):
        
        ruleset_uri = self.EX[rule_set.id]
        entry_uri = self.EX[rule_set.entry]

        # define RuleSet as a resource
        self.graph.add((ruleset_uri, RDF.type, self.EX.RuleSet))

        # link entry point
        self.graph.add((ruleset_uri, self.EX.entryPoint, entry_uri))

        # add all rule elements
        for element in rule_set.elements.values():
            self.add_rule_element(element)


    def serialize(self, path):
        self.graph.serialize(path, format="turtle")

    def get_graph(self):
        return self.graph