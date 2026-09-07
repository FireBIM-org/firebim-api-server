"""Rule term validation against the selected ontologies: paths per step, derive elements, $ref, accepted terms."""

from __future__ import annotations

from types import SimpleNamespace

from app.lexicon import service as lexicon_service
from app.schemas.lexicon import LexiconRuleTermValidationRequest, LexiconSelectedOntology

DK = "https://firebim.org/ontologies/fbo/dk#"
FBO = "https://firebim.org/ontologies/fbo#"
KNOWN_CLASSES = {DK + "FireCompartment", DK + "BuildingFunctionalFireUnit"}
KNOWN_PROPERTIES = {
    DK + "partOf",
    DK + "FunctionType",
    DK + "AreaFireCompartment",
    FBO + "hasSubitem",
    DK + "IsFireExit",
}


class FakeRuntime:
    dataset = SimpleNamespace(
        catalog={u: {"uri": u, "term_id": u.rsplit("#", 1)[-1]} for u in KNOWN_CLASSES | KNOWN_PROPERTIES},
        provenance_by_uri={u: [("fbo", "dk", "2026.09.06")] for u in KNOWN_CLASSES | KNOWN_PROPERTIES},
    )

    def resolve_term_matches(self, value: str):
        return {
            "normalized_uri": value,
            "class_matches": {value} & KNOWN_CLASSES,
            "property_matches": {value} & KNOWN_PROPERTIES,
        }


def _service(monkeypatch):
    svc = lexicon_service.LexiconService()
    monkeypatch.setattr(svc, "_resolve_similarity_selected_ontologies", lambda selected: selected)
    monkeypatch.setattr(svc, "_resolve_ontology_bundle", lambda **kwargs: object())
    monkeypatch.setattr(
        lexicon_service.LexiconQueryRuntime, "from_bundles", staticmethod(lambda bundles: FakeRuntime())
    )
    return svc


def _request(elements):
    return LexiconRuleTermValidationRequest(
        selected_ontologies=[LexiconSelectedOntology(ontology_family="fbo", country="dk", version="2026.09.06")],
        rule={"id": "r", "entry": "r-start", "elements": [{"id": "r-start", "element_type": "start"}, *elements]},
    )


def test_paths_are_validated_per_step_and_derive_expressions_too(monkeypatch):
    svc = _service(monkeypatch)
    res = svc.validate_rule_terms(
        _request(
            [
                {
                    "id": "d1",
                    "element_type": "decision",
                    "target_class": DK + "FireCompartment",
                    "property_path": f"{DK}partOf/{DK}FunctionTyp",  # typo in the second step
                    "operator": "==",
                    "value": "Garage complexes",
                },
                {
                    "id": "x1",
                    "element_type": "derive",
                    "target_class": DK + "BuildingFunctionalFireUnit",
                    "property_path": DK + "NumberOfExits",  # not in the fake catalogue
                    "expression": f"sum(${FBO}hasSubitem/{FBO}hasSubitem/{DK}IsFireExit)",
                },
            ]
        )
    )
    by = {(i.node_id, i.field): i for i in res.issues}
    assert set(by) == {("d1", "property_path"), ("x1", "property_path")}
    issue = by[("d1", "property_path")]
    assert issue.value == DK + "FunctionTyp" and issue.context == f"{DK}partOf/{DK}FunctionTyp"
    assert "step of" in issue.message and "accept the term" in issue.message
    # the derive's expression paths were all known -> no expression issue, but the field was validated
    x1 = next(n for n in res.nodes if n.node_id == "x1")
    assert {f.field: f.valid for f in x1.fields} == {"target_class": True, "property_path": False, "expression": True}


def test_accepted_terms_suppress_the_issue(monkeypatch):
    svc = _service(monkeypatch)
    element = {
        "id": "d1",
        "element_type": "decision",
        "target_class": DK + "FireCompartment",
        "property_path": DK + "SomethingNew",
        "operator": "==",
        "value": True,
        "config": {"accepted_terms": [DK + "SomethingNew"]},
    }
    assert svc.validate_rule_terms(_request([element])).valid is True
    element["config"] = {}
    assert svc.validate_rule_terms(_request([element])).valid is False
