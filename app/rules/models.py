from dataclasses import dataclass, field
from typing import Dict, Optional, Any


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

    target_class: Optional[str] = None
    property_path: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[Any] = None

    true_path: Optional[str] = None
    false_path: Optional[str] = None
    unknown_path: Optional[str] = None

    label: Optional[str] = None

    node_type: str = "decision"
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class fbRule:
    id: str
    entry_element: str
    elements: Dict[str, fbRuleElement] = field(default_factory=dict)

    def add_element(self, element: fbRuleElement):
        self.elements[element.id] = element

    def get_element(self, element_id: str) -> fbRuleElement:
        return self.elements[element_id]
