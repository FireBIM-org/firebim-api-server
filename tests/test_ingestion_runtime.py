import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app.ingestion.execution import LeaseLost, ProcessingError
from app.ingestion.policy import LIMITS, MODULES, PRODUCERS
from app.ingestion.runtime import IsolatedRuntime, run_isolated
from tests import test_neo_revision

published_revision = test_neo_revision.published_revision


def test_mapping_executes_retained_revision_in_bounded_child(tmp_path, published_revision):
    fixture = Path("tests/fixtures/neo/semantic_ifcowl.ttl")
    for producer in PRODUCERS:
        shutil.copyfile(fixture, tmp_path / f"source_{producer}.ttl")
    (tmp_path / "converter.log").write_text("\n".join(f"module {module}: success" for module in ("parse", *MODULES)))
    (tmp_path / "revision.json").write_text(published_revision.model_dump_json())
    (tmp_path / "model-id").write_text("test-model")
    metrics = IsolatedRuntime("unused").map(tmp_path, lambda: None)
    summary = json.loads((tmp_path / "mapping-report.json").read_text())
    evaluation = json.loads((tmp_path / "evaluation.json").read_text())
    assert summary["assertions"] == 5 and not summary["diagnostics"]
    assert len(evaluation["assertions"]) == 5
    assert metrics["peak_rss_bytes"] > 0 and metrics["cpu_seconds"] > 0
    assert metrics["peak_disk_bytes"] > fixture.stat().st_size


def test_valid_empty_auxiliary_exports_do_not_reject_ifcowl_mapping(tmp_path, published_revision):
    for producer in PRODUCERS:
        (tmp_path / f"source_{producer}.ttl").write_text("")
    shutil.copyfile("tests/fixtures/neo/semantic_ifcowl.ttl", tmp_path / "source_ifcowl.ttl")
    (tmp_path / "converter.log").write_text("\n".join(f"module {module}: success" for module in ("parse", *MODULES)))
    (tmp_path / "revision.json").write_text(published_revision.model_dump_json())
    (tmp_path / "model-id").write_text("test")
    IsolatedRuntime("unused").map(tmp_path, lambda: None)
    summary = json.loads((tmp_path / "mapping-report.json").read_text())
    assert summary["assertions"] == 5 and not summary["diagnostics"]
    assert summary["producer_triples"] == {"bot": 0, "beo": 0, "bsdd": 0, "ifcowl": 381}


def test_runner_timeout_and_cancellation_stop_child(tmp_path):
    with pytest.raises(ProcessingError, match="wall-clock") as caught:
        run_isolated(
            [sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, seconds=0.1, heartbeat=lambda: None
        )
    assert caught.value.code == "stage_timeout"
    assert caught.value.metrics["exit_code"] == -9
    assert caught.value.metrics["cpu_seconds"] >= 0

    def cancelled():
        raise LeaseLost()

    with pytest.raises(LeaseLost):
        run_isolated([sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, seconds=1, heartbeat=cancelled)


def test_runner_rejects_aggregate_disk_growth(tmp_path):
    with pytest.raises(ProcessingError) as caught:
        run_isolated(
            [sys.executable, "-c", "open('large', 'wb').write(b'x' * 4096)"],
            tmp_path,
            seconds=1,
            heartbeat=lambda: None,
            limits=replace(LIMITS, disk_bytes=1024),
        )
    assert caught.value.code == "disk_limit"


def test_children_receive_bound_supervisor_but_no_host_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-be-inherited")
    monkeypatch.setenv("POSTGRES_PASSWORD", "must-not-be-inherited")
    metrics = run_isolated(
        [
            sys.executable,
            "-I",
            "-c",
            "import os; assert int(os.environ['NEO_SUPERVISOR_PID']) == os.getppid(); "
            "assert 'AWS_SECRET_ACCESS_KEY' not in os.environ; assert 'POSTGRES_PASSWORD' not in os.environ",
        ],
        tmp_path,
        seconds=1,
        heartbeat=lambda: None,
    )
    assert metrics["exit_code"] == 0


def test_converter_must_match_pinned_digest(tmp_path):
    binary = tmp_path / "fake-converter"
    binary.write_bytes(b"not the pinned binary")
    with pytest.raises(ProcessingError) as caught:
        IsolatedRuntime(binary).convert(tmp_path, lambda: None)
    assert caught.value.code == "converter_digest_mismatch"


@pytest.mark.parametrize("limit", ["memory", "triples", "malformed", "cpu"])
def test_mapping_child_enforces_resource_and_artifact_limits(tmp_path, published_revision, limit):
    (tmp_path / "converter.log").write_text("\n".join(f"module {module}: success" for module in ("parse", *MODULES)))
    (tmp_path / "revision.json").write_text(published_revision.model_dump_json())
    (tmp_path / "model-id").write_text("test")
    limits = LIMITS
    fixture = Path("tests/fixtures/neo/semantic_ifcowl.ttl").read_text()
    if limit == "memory":
        limits = replace(limits, memory_bytes=16 * 1024 * 1024)
    elif limit == "triples":
        limits = replace(limits, triples=1)
    elif limit == "malformed":
        fixture = "this is not Turtle"
    else:
        fixture = "\n".join(f"<urn:s{i}> <urn:p> <urn:o> ." for i in range(60000))
        limits = replace(limits, cpu_seconds=1)
    for producer in PRODUCERS:
        (tmp_path / f"source_{producer}.ttl").write_text(fixture)
    with pytest.raises(ProcessingError) as caught:
        IsolatedRuntime("unused", limits=limits).map(tmp_path, lambda: None)
    assert caught.value.code == "stage_process_failed"
    if limit in {"triples", "malformed"}:
        summary = json.loads((tmp_path / "mapping-report.json").read_text())
        assert summary["diagnostics"][0]["code"] == "invalid_producer_artifact"
