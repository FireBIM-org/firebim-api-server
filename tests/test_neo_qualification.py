import json

from scripts.qualify_neo_runtime import qualify
from tests import test_ingestion_admission, test_ingestion_pipeline
from tests.test_ingestion_workflow import publish_workload

admission_api = test_ingestion_admission.admission_api
captured_converter = test_ingestion_pipeline.captured_converter


def test_qualification_retains_a_semantic_rejection_without_claiming_capacity(
    admission_api,
    captured_converter,
    test_user,
    tmp_path,
    monkeypatch,
):
    _, service, _, base = admission_api
    revision = publish_workload(service.profiles, base, test_user, conflict=True)
    monkeypatch.setattr("scripts.qualify_neo_runtime.IsolatedRuntime", lambda binary: captured_converter)
    report = qualify("captured", revision, ["tests/fixtures/neo/semantic.ifc"], tmp_path / "qualification")
    assert report["all_passed"] is False and report["admission_enabled"] is False
    measured = report["measurements"][0]
    assert measured["status"] == "rejected" and measured["destination_bytes"] is None
    assert measured["destination"]["valid"] is False
    assert measured["summary"]["diagnostics"][0]["code"] == "cardinality_conflict"
    assert json.loads((tmp_path / "qualification/measurements.json").read_text()) == report
