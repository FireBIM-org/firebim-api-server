from dataclasses import dataclass, field
from typing import Any


@dataclass
class fbRuleElement:
    """
    Atomic rule element.

    node_type:
      - decision: SHACL-backed decision node
      - start: entry/orchestration node
      - end: terminal/output node
    """

    id: str

    target_class: str | None = None
    property_path: str | None = None
    operator: str | None = None
    value: Any | None = None

    true_path: str | None = None
    false_path: str | None = None
    unknown_path: str | None = None

    label: str | None = None

    node_type: str = "decision"
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class fbRule:
    id: str
    entry_element: str
    elements: dict[str, fbRuleElement] = field(default_factory=dict)

    def add_element(self, element: fbRuleElement):
        self.elements[element.id] = element

    def get_element(self, element_id: str) -> fbRuleElement:
        return self.elements[element_id]
