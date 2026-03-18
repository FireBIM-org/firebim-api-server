from .models import fbRuleElement
from rdflib import URIRef

class Condition:

    def __init__(self, property_uri, operator, value):

        if isinstance(property_uri, URIRef):
            property_uri = str(property_uri)

        self.property_uri = property_uri
        self.operator = operator
        self.value = value




class Property:

    """
    Helper so we can write:
    FBO.FireLoadDensityModified.gt(20000)
    """

    def __init__(self, uri):

        self.uri = uri

    def gt(self, value):
        return Condition(self.uri, ">", value)

    def ge(self, value):
        return Condition(self.uri, ">=", value)

    def lt(self, value):
        return Condition(self.uri, "<", value)

    def le(self, value):
        return Condition(self.uri, "<=", value)

    def eq(self, *values):

        if len(values) == 1:
            value = values[0]
        else:
            value = list(values)

        return Condition(self.uri, "==", value)


class Rule:
    """
    Helper DSL to construct fbRuleElement objects.
    """

    def __init__(self, rule_id):

        self.rule_id = rule_id
        self.target_class = "Building" #default
        self.property_path = None
        self.operator = None
        self.value = None

        # grouped rule
        self.conditions = None
        self.logic = None

        self.true_path = None
        self.false_path = None
        self.unknown_path = None

        self.label = None

    # --------------------------------------------------
    # Original simple rule DSL
    # --------------------------------------------------
    def target(self, class_uri):

        if hasattr(class_uri, "uri"):
            self.target_class = str(class_uri.uri)
        else:
            self.target_class = str(class_uri)

        return self


    def when(self, property_name):

        self.property_path = property_name
        return self

    def le(self, value):
        self.operator = "<="
        self.value = value
        return self

    def ge(self, value):
        self.operator = ">="
        self.value = value
        return self

    def gt(self, value):
        self.operator = ">"
        self.value = value
        return self

    def lt(self, value):
        self.operator = "<"
        self.value = value
        return self

    def eq(self, *values):

        if len(values) == 1:
            self.value = values[0]
        else:
            self.value = list(values)

        self.operator = "=="
        return self

    def between(self, lower, upper):

        self.operator = "between"
        self.value = (lower, upper)
        return self

    # --------------------------------------------------
    # New grouped rule DSL
    # --------------------------------------------------

    def any(self, *conditions):

        self.conditions = conditions
        self.logic = "OR"

        self.operator = "group"

        return self

    def all(self, *conditions):

        self.conditions = conditions
        self.logic = "AND"

        self.operator = "group"

        return self

    # --------------------------------------------------
    # rule graph paths
    # --------------------------------------------------

    def true(self, rule_id):

        self.true_path = rule_id
        return self

    def false(self, rule_id):

        self.false_path = rule_id
        return self

    def unknown(self, rule_id):

        self.unknown_path = rule_id
        return self

    # --------------------------------------------------

    def name(self, label):

        self.label = label
        return self

    # --------------------------------------------------

    def build(self):

        if self.operator == "group":

            value = {
                "logic": self.logic,
                "conditions": self.conditions
            }

            property_path = None

        else:

            value = self.value
            property_path = self.property_path

        return fbRuleElement(
            id=self.rule_id,
            target_class=self.target_class,
            property_path=property_path,
            operator=self.operator,
            value=value,
            true_path=self.true_path,
            false_path=self.false_path,
            unknown_path=self.unknown_path,
            label=self.label
        )
    
class RuleSet:

    def __init__(self, rule_id):
        self.id = rule_id
        self.elements = {}
        self.entry = None

    def add(self, element):
        self.elements[element.id] = element
        return self

    def start(self, element_id):
        self.entry = element_id
        return self

    def validate(self):
        if not self.entry:
            raise ValueError("RuleSet must define a start element")

        if self.entry not in self.elements:
            raise ValueError(f"Start element '{self.entry}' not found")

        for el in self.elements.values():
            for path in [el.true_path, el.false_path, el.unknown_path]:
                if path and path not in self.elements:
                    raise ValueError(
                        f"{el.id} points to unknown rule '{path}'"
                    )

        return True