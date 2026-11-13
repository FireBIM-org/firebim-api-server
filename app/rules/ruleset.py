class Rule:
    """
    Composite rule made of reusable rule elements.

    Public terminology:
      - Rule = composite graph with entry + connected rule elements
      - Rule element = atomic start/decision/end unit
    """

    def __init__(self, rule_id: str):
        self.id = rule_id
        self.elements = {}
        self.entry = None
        # human-facing metadata (the Miro frame title, a description, where it came from)
        self.title: str | None = None
        self.description: str | None = None
        self.source: dict | None = None

    def add(self, element):
        if element.id in self.elements:
            raise ValueError(f"Duplicate rule element id: {element.id}")
        self.elements[element.id] = element
        return self

    def start(self, element_id: str):
        self.entry = element_id
        return self

    def get_element(self, element_id: str):
        return self.elements[element_id]

    def validate(self):
        if not self.entry:
            raise ValueError("Rule must define an entry element")

        if self.entry not in self.elements:
            raise ValueError(f"Entry element '{self.entry}' not found")

        for element in self.elements.values():
            for path in [element.true_path, element.false_path, element.unknown_path]:
                if path and path not in self.elements:
                    raise ValueError(f"{element.id} points to unknown rule element '{path}'")

        return True
