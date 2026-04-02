from rdflib import URIRef

from .models import fbRuleElement
from .ruleset import Rule as CompositeRule


class Condition:
    def __init__(self, property_uri, operator, value):
        if isinstance(property_uri, URIRef):
            property_uri = str(property_uri)

        self.property_uri = property_uri
        self.operator = operator
        self.value = value


class Property:
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
        value = values[0] if len(values) == 1 else list(values)
        return Condition(self.uri, "==", value)


class RuleElementBuilder:
    """
    Builder DSL for one atomic rule element.
    """

    def __init__(self, element_id):
        self.element_id = element_id
        self.target_class = "Building"
        self.property_path = None
        self.operator = None
        self.value = None

        self.conditions = None
        self.logic = None

        self.true_path = None
        self.false_path = None
        self.unknown_path = None
        self.label = None

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

    def true(self, element_id):
        self.true_path = element_id
        return self

    def false(self, element_id):
        self.false_path = element_id
        return self

    def unknown(self, element_id):
        self.unknown_path = element_id
        return self

    def name(self, label):
        self.label = label
        return self

    def build(self):
        if self.operator == "group":
            value = {
                "logic": self.logic,
                "conditions": self.conditions,
            }
            property_path = None
        else:
            value = self.value
            property_path = self.property_path

        return fbRuleElement(
            id=self.element_id,
            target_class=self.target_class,
            property_path=property_path,
            operator=self.operator,
            value=value,
            true_path=self.true_path,
            false_path=self.false_path,
            unknown_path=self.unknown_path,
            label=self.label,
        )


RuleSet = CompositeRule
