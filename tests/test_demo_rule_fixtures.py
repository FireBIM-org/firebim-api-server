from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("POSTGRES_DB", "firebim")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("S3_BUCKET_NAME", "firebim")
os.environ.setdefault("S3_ACCESS_KEY_ID", "minioadmin")
os.environ.setdefault("S3_SECRET_ACCESS_KEY", "minioadmin")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

import pytest

from app.rules.decompiler import decompile_ruleset_ttl
from app.storage import demo_content_bootstrap as bootstrap_module

IFC = "https://firebim.org/ontologies/ifc/global/2026.04#"
FBO_DK = "https://firebim.org/ontologies/fbo/dk/2026.04#"
RIR = "https://firebim.org/ontologies/rir/global/2026.04#"

EXPECTED_RULE_TARGETS = {
    "ifc-only-rule-compliant": {IFC},
    "fbo-only-rule-compliant": {FBO_DK},
    "ifc-fbo-rule-compliant": {IFC, FBO_DK},
    "ifc-rir-fbo-rule-compliant": {IFC, RIR, FBO_DK},
}


@pytest.mark.parametrize(
    ("builder_name", "rule_id"),
    [
        ("_ifc_only_rule_payload", "ifc-only-rule-compliant"),
        ("_fbo_only_rule_payload", "fbo-only-rule-compliant"),
        ("_ifc_fbo_rule_payload", "ifc-fbo-rule-compliant"),
        ("_ifc_rir_fbo_rule_payload", "ifc-rir-fbo-rule-compliant"),
    ],
)
def test_demo_rule_uses_expected_ontology_targets(monkeypatch: pytest.MonkeyPatch, builder_name: str, rule_id: str):
    payload = getattr(bootstrap_module, builder_name)()
    captured: dict[str, str] = {}

    def fake_publish(*, ttl_text: str, dataset: str, graph_uri: str) -> None:
        captured["ttl"] = ttl_text
        captured["dataset"] = dataset
        captured["graph_uri"] = graph_uri

    monkeypatch.setattr(bootstrap_module, "publish_graph_ttl", fake_publish)

    graph_uri, element_count = bootstrap_module._publish_rule_payload(payload)
    assert graph_uri == captured["graph_uri"]
    assert captured["dataset"] == "rulebook"
    assert element_count == len(payload["elements"])

    decompiled = decompile_ruleset_ttl(captured["ttl"])
    assert decompiled["id"] == rule_id
    assert decompiled["entry"] == payload["entry"]

    used_prefixes = set()
    for node in decompiled["nodes"]:
        target_class = node.get("target_class")
        property_path = node.get("property_path")
        if target_class:
            for prefix in (IFC, FBO_DK, RIR):
                if str(target_class).startswith(prefix):
                    used_prefixes.add(prefix)
        if property_path:
            for prefix in (IFC, FBO_DK, RIR):
                if str(property_path).startswith(prefix):
                    used_prefixes.add(prefix)

    assert used_prefixes == EXPECTED_RULE_TARGETS[rule_id]
