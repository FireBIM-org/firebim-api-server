from __future__ import annotations

from pathlib import Path

from app.rules.models import fbRuleElement
from app.rules.publisher import RULEBOOK_DATASET, default_rule_graph_uri, publish_graph_ttl
from app.rules.ruleset import Rule
from app.rules.shacl_builder import SHACLBuilder
from app.storage.building_repository import BuildingRepository

FIXTURE_DIR = Path(__file__).resolve().parent / "demo_fixtures"
IFC = "https://firebim.org/ontologies/ifc/global/2026.04#"
FBO_DK = "https://firebim.org/ontologies/fbo/dk/2026.04#"
RIR = "https://firebim.org/ontologies/rir/global/2026.04#"

GRAPH_FIXTURES: dict[str, str] = {
    "ifc-inspired-small": "building_small_ifc_inspired.ttl",
    "ifc-inspired-medium": "building_medium_ifc_inspired.ttl",
    "ifc-inspired-large": "building_large_ifc_inspired.ttl",
    "ifc-only-graph-compliant": "building_ifc_only_compliant.ttl",
    "fbo-only-graph-compliant": "building_fbo_only_compliant.ttl",
    "ifc-fbo-graph-compliant": "building_ifc_fbo_compliant.ttl",
    "ifc-rir-fbo-graph-compliant": "building_ifc_rir_fbo_compliant.ttl",
}
RULE_IDS = {
    "ifc_only": "ifc-only-rule-compliant",
    "fbo_only": "fbo-only-rule-compliant",
    "ifc_fbo": "ifc-fbo-rule-compliant",
    "ifc_rir_fbo": "ifc-rir-fbo-rule-compliant",
}


def _load_fixture_ttl(filename: str) -> str:
    return (FIXTURE_DIR / filename).read_text(encoding="utf-8")


def _end_node(node_id: str, text: str, *, end_kind: str = "output", label: str | None = None) -> dict[str, object]:
    return {
        "id": node_id,
        "element_type": "end",
        "label": label or text,
        "config": {
            "terminal_value": text,
            "end_kind": end_kind,
            "output_text": text,
        },
    }


def _publish_rule_payload(rule_payload: dict[str, object]) -> tuple[str, int]:
    rule = Rule(str(rule_payload["id"]))
    elements = list(rule_payload["elements"])
    for raw in elements:
        rule.add(
            fbRuleElement(
                id=str(raw["id"]),
                target_class=raw.get("target_class"),
                property_path=raw.get("property_path"),
                operator=raw.get("operator"),
                value=raw.get("value"),
                true_path=raw.get("true_path"),
                false_path=raw.get("false_path"),
                unknown_path=raw.get("unknown_path"),
                label=raw.get("label"),
                node_type=str(raw.get("element_type", "decision")),
                config=dict(raw.get("config") or {}),
            )
        )
    rule.start(str(rule_payload["entry"]))
    rule.validate()

    builder = SHACLBuilder()
    builder.add_rule_set(rule)
    ttl = builder.get_graph().serialize(format="turtle")
    graph_uri = default_rule_graph_uri(rule.id)
    publish_graph_ttl(ttl_text=str(ttl), dataset=RULEBOOK_DATASET, graph_uri=graph_uri)
    return graph_uri, len(elements)


def _ifc_only_rule_payload() -> dict[str, object]:
    rule_id = RULE_IDS["ifc_only"]
    decision_id = f"{rule_id}-decision"
    return {
        "id": rule_id,
        "entry": decision_id,
        "elements": [
            {
                "id": decision_id,
                "element_type": "decision",
                "target_class": f"{IFC}IfcWall",
                "property_path": f"{IFC}FireRating",
                "operator": "==",
                "value": "EI60",
                "true_path": f"{rule_id}-pass",
                "false_path": f"{rule_id}-fail",
                "unknown_path": f"{rule_id}-unknown",
                "label": "IfcWall fire rating equals EI60",
                "config": {},
            },
            _end_node(f"{rule_id}-pass", "ifc wall fire rating matched"),
            _end_node(f"{rule_id}-fail", "ifc wall fire rating did not match"),
            _end_node(f"{rule_id}-unknown", "ifc wall fire rating missing"),
        ],
    }


def _fbo_only_rule_payload() -> dict[str, object]:
    rule_id = RULE_IDS["fbo_only"]
    decision_id = f"{rule_id}-decision"
    return {
        "id": rule_id,
        "entry": decision_id,
        "elements": [
            {
                "id": decision_id,
                "element_type": "decision",
                "target_class": f"{FBO_DK}FireCompartment",
                "property_path": f"{FBO_DK}AreaFireCompartment",
                "operator": ">=",
                "value": 70,
                "true_path": f"{rule_id}-pass",
                "false_path": f"{rule_id}-fail",
                "unknown_path": f"{rule_id}-unknown",
                "label": "Fire compartment area is at least 70 square meters",
                "config": {},
            },
            _end_node(f"{rule_id}-pass", "fbo fire compartment area matched"),
            _end_node(f"{rule_id}-fail", "fbo fire compartment area did not match"),
            _end_node(f"{rule_id}-unknown", "fbo fire compartment area missing"),
        ],
    }


def _ifc_fbo_rule_payload() -> dict[str, object]:
    rule_id = RULE_IDS["ifc_fbo"]
    start_id = f"{rule_id}-start"
    ifc_id = f"{rule_id}-ifc-window"
    fbo_id = f"{rule_id}-fbo-compartment"
    return {
        "id": rule_id,
        "entry": start_id,
        "elements": [
            {
                "id": start_id,
                "element_type": "start",
                "true_path": ifc_id,
                "label": "Start IFC/FBO compliant demo rule",
                "config": {},
            },
            {
                "id": ifc_id,
                "element_type": "decision",
                "target_class": f"{IFC}IfcWindow",
                "property_path": f"{IFC}IsExternal",
                "operator": "==",
                "value": True,
                "true_path": fbo_id,
                "false_path": f"{rule_id}-fail",
                "unknown_path": f"{rule_id}-unknown",
                "label": "IfcWindow is external",
                "config": {},
            },
            {
                "id": fbo_id,
                "element_type": "decision",
                "target_class": f"{FBO_DK}FireCompartment",
                "property_path": f"{FBO_DK}AreaFireCompartment",
                "operator": ">=",
                "value": 70,
                "true_path": f"{rule_id}-pass",
                "false_path": f"{rule_id}-fail",
                "unknown_path": f"{rule_id}-unknown",
                "label": "Fire compartment area is at least 70 square meters",
                "config": {},
            },
            _end_node(f"{rule_id}-pass", "ifc and fbo checks matched"),
            _end_node(f"{rule_id}-fail", "ifc or fbo checks failed"),
            _end_node(f"{rule_id}-unknown", "ifc or fbo checks missing data"),
        ],
    }


def _ifc_rir_fbo_rule_payload() -> dict[str, object]:
    rule_id = RULE_IDS["ifc_rir_fbo"]
    start_id = f"{rule_id}-start"
    ifc_id = f"{rule_id}-ifc-building"
    rir_id = f"{rule_id}-rir-building"
    fbo_id = f"{rule_id}-fbo-compartment"
    return {
        "id": rule_id,
        "entry": start_id,
        "elements": [
            {
                "id": start_id,
                "element_type": "start",
                "true_path": ifc_id,
                "label": "Start IFC/RIR/FBO compliant demo rule",
                "config": {},
            },
            {
                "id": ifc_id,
                "element_type": "decision",
                "target_class": f"{IFC}IfcBuilding",
                "property_path": f"{IFC}BuildingID",
                "operator": "==",
                "value": "B-FULL-001",
                "true_path": rir_id,
                "false_path": f"{rule_id}-fail",
                "unknown_path": f"{rule_id}-unknown",
                "label": "IfcBuilding has expected BuildingID",
                "config": {},
            },
            {
                "id": rir_id,
                "element_type": "decision",
                "target_class": f"{RIR}cPset_BuildingSummary",
                "property_path": f"{RIR}HasElectricity",
                "operator": "==",
                "value": True,
                "true_path": fbo_id,
                "false_path": f"{rule_id}-fail",
                "unknown_path": f"{rule_id}-unknown",
                "label": "RIR building summary has electricity",
                "config": {},
            },
            {
                "id": fbo_id,
                "element_type": "decision",
                "target_class": f"{FBO_DK}FireCompartment",
                "property_path": f"{FBO_DK}AreaFireCompartment",
                "operator": ">=",
                "value": 70,
                "true_path": f"{rule_id}-pass",
                "false_path": f"{rule_id}-fail",
                "unknown_path": f"{rule_id}-unknown",
                "label": "FBO fire compartment area is at least 70 square meters",
                "config": {},
            },
            _end_node(f"{rule_id}-pass", "ifc, rir and fbo checks matched"),
            _end_node(f"{rule_id}-fail", "ifc, rir or fbo checks failed"),
            _end_node(f"{rule_id}-unknown", "ifc, rir or fbo checks missing data"),
        ],
    }


def bootstrap_default_demo_content(repo: BuildingRepository | None = None) -> list[dict[str, object]]:
    repository = repo or BuildingRepository()
    seeded: list[dict[str, object]] = []

    for graph_id, filename in GRAPH_FIXTURES.items():
        graph_uri, overwritten = repository.publish_graph(
            graph_id=graph_id,
            ttl=_load_fixture_ttl(filename),
        )
        seeded.append(
            {
                "kind": "graph",
                "graph_id": graph_id,
                "graph_uri": graph_uri,
                "fixture": filename,
                "overwritten": overwritten,
            }
        )

    for rule_payload in [
        _ifc_only_rule_payload(),
        _fbo_only_rule_payload(),
        _ifc_fbo_rule_payload(),
        _ifc_rir_fbo_rule_payload(),
    ]:
        graph_uri, element_count = _publish_rule_payload(rule_payload)
        seeded.append(
            {
                "kind": "rule",
                "rule_id": rule_payload["id"],
                "graph_uri": graph_uri,
                "entry": rule_payload["entry"],
                "element_count": element_count,
            }
        )

    return seeded
