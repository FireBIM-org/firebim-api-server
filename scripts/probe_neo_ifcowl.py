"""Offline conformance probe for trusted local fixtures, not an upload worker."""

import argparse
import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

from rdflib import Graph

from app.ingestion.neo_ifcowl_adapter import ADAPTER_VERSION, NeoAdapterError, adapt_ifcowl
from app.ingestion.neo_revision import evaluate_neo_revision
from app.schemas.translation_revision import TranslationPublishedRevision

RELEASE = "v0.7.4"
COMMIT = "853f8ad16916a372dcde709f845ee0c7a6014ba8"
BINARY_SHA256 = "f773019184ddd3ae6441e97f9bbf5085f4f460b349aac8df5bdc37e4742a4879"
BASE_URI = "https://example.org/neo-fixture/"
MODULES = (
    "neo-bot-producer",
    "neo-beo-producer",
    "neo-bsdd-producer",
    "neo-ifcowl-producer",
    "neo-turtle-serializer",
    "neo-file-export",
)
OPTIONS = (
    "neo-ifcowl-producer.mode=full",
    "neo-bsdd-producer.opm_level=l2",
    "neo-bsdd-producer.compact=false",
    "neo-bsdd-producer.dedup_properties=false",
    "neo-turtle-serializer.layout=separate",
    "neo-turtle-serializer.grouping=sorted",
)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--input", type=Path, default=Path("tests/fixtures/neo/semantic.ifc"))
    parser.add_argument("--revision", type=Path, help="Authorized exported revision; omit for strict extraction")
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory; never overwrite existing files")
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    if sha256(binary) != BINARY_SHA256:
        parser.error("Expected the pinned v0.7.4 Linux x86_64 release executable (SHA-256 mismatch)")
    source = args.input.resolve(strict=True)
    output = args.output_dir.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    shutil.copyfile(source, output / "source.ifc")
    command = [str(binary), "source.ifc", "--output", "source.ttl", "--base-uri", BASE_URI]
    for module in MODULES:
        command.extend(["--module", module])
    for option in OPTIONS:
        command.extend(["--module-opt", option])
    with (output / "converter.log").open("wb") as log:
        subprocess.run(
            command, cwd=output, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=120, check=True
        )
    log_text = (output / "converter.log").read_text(encoding="utf-8")
    for module in ("parse", *MODULES):
        if f"module {module}: success" not in log_text:
            raise ValueError(f"Required module did not report success: {module}")
    artifacts = {}
    for producer in ("bot", "beo", "bsdd", "ifcowl"):
        path = output / f"source_{producer}.ttl"
        graph = Graph().parse(path, format="turtle")
        if not graph:
            raise ValueError(f"Empty required producer output: {producer}")
        artifacts[producer] = {
            "file": path.name,
            "format": "turtle",
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
            "triples": len(graph),
        }
    report = {
        "report_version": "neo-conformance-probe/1",
        "release": RELEASE,
        "commit": COMMIT,
        "binary_sha256": sha256(binary),
        "input_sha256": sha256(output / "source.ifc"),
        "base_uri": BASE_URI,
        "modules": MODULES,
        "options": OPTIONS,
        "artifacts": artifacts,
        "adapter_version": ADAPTER_VERSION,
    }
    status = 0
    try:
        graph = Graph().parse(output / "source_ifcowl.ttl", format="turtle")
        if args.revision is not None:
            revision = TranslationPublishedRevision.model_validate_json(args.revision.read_text(encoding="utf-8"))
            adapted, result = evaluate_neo_revision(revision, graph, "neo-probe")
            report["revision"] = {
                "profile_id": revision.content.profile_id,
                "revision_id": revision.revision_id,
                "content_digest": revision.content_digest,
            }
            report["evaluation"] = {
                "assertions": len(result.assertions),
                "memberships": len(result.memberships),
                "diagnostics": [asdict(item) for item in result.diagnostics],
            }
            status = int(any(item.severity == "error" for item in result.diagnostics))
            (output / "evaluation.json").write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
        else:
            adapted = adapt_ifcowl(graph)
        (output / "evidence.json").write_text(
            json.dumps([asdict(item) for item in adapted.evidence], indent=2) + "\n", encoding="utf-8"
        )
        report["adapter"] = {
            "status": "passed",
            "records": len(adapted.records),
            "owned_values": len(adapted.evidence),
            "model_units": adapted.model_units,
        }
    except NeoAdapterError as error:
        report["adapter"] = {
            "status": "rejected",
            "code": error.code,
            "message": str(error),
            "source_iri": error.source_iri,
        }
        status = 1
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
