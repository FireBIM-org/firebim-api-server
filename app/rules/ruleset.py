# firebim/rules/ruleset.py

class RuleSet:

    def __init__(self, rule_id):
        self.id = rule_id
        self.elements = {}
        self.entry = None

    def add(self, element):
        if element.id in self.elements:
            raise ValueError(f"Duplicate rule id: {element.id}")
        self.elements[element.id] = element
        return self

    def start(self, element_id):
        self.entry = element_id
        return self

    def get_element(self, element_id):
        return self.elements[element_id]

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