from dataclasses import dataclass, field
from typing import Dict, Optional, Any


@dataclass
class fbRuleElement:

    id: str

    # decision rule fields
    target_class: Optional[str] = None
    property_path: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[Any] = None

    true_path: Optional[str] = None
    false_path: Optional[str] = None
    unknown_path: Optional[str] = None

    label: Optional[str] = None
    node_type: str = "decision"
    config: Optional[Dict] = field(default_factory=dict)

@dataclass
class fbRule:
    """
    Composite fire safety rule consisting of multiple rule elements.
    """

    id: str
    entry_element: str

    elements: Dict[str, fbRuleElement] = field(default_factory=dict)

    def add_element(self, element: fbRuleElement):
        self.elements[element.id] = element

    def get_element(self, element_id: str) -> fbRuleElement:
        return self.elements[element_id]