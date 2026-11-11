"""Measure the production child runtime; never publishes or enables admission."""

import argparse
import json
import shutil
from pathlib import Path

from app.ingestion.execution import ProcessingError
from app.ingestion.jobs import CONVERTER_SHA256, require_supported_revision
from app.ingestion.pipeline import execution_fingerprint
from app.ingestion.policy import LIMITS
from app.ingestion.runtime import IsolatedRuntime, directory_size, file_digest
from app.schemas.translation_revision import TranslationPublishedRevision


def qualify(binary, revision, inputs, output_dir):
    require_supported_revision(revision)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    runtime = IsolatedRuntime(binary)
    measurements = []
    for number, source in enumerate(inputs):
        source = Path(source)
        directory = output_dir / str(number)
        directory.mkdir(mode=0o700)
        shutil.copyfile(source, directory / "source.ifc")
        (directory / "revision.json").write_text(revision.model_dump_json(), encoding="utf-8")
        (directory / "model-id").write_text("qualification-model", encoding="utf-8")
        before = directory_size(directory)
        measurement = {
            "source": source.name,
            "input_bytes": source.stat().st_size,
            "sha256": file_digest(source),
            "status": "rejected",
            "destination_bytes": None,
            "provenance_bytes": None,
        }
        stage = "conversion"
        try:
            measurement[stage] = runtime.convert(directory, lambda: None)
            after_conversion = directory_size(directory)
            measurement.update(
                conversion_growth_bytes=after_conversion - before,
                output_growth_ratio=(after_conversion - before) / source.stat().st_size,
            )
            stage = "mapping"
            measurement[stage] = runtime.map(directory, lambda: None)
            measurement["summary"] = json.loads((directory / "mapping-report.json").read_text(encoding="utf-8"))
            stage = "validation"
            measurement[stage] = runtime.validate(directory, lambda: None)
            destination = json.loads((directory / "validation-report.json").read_text(encoding="utf-8"))
            measurement["destination"] = destination
            if destination.get("valid") is True:
                measurement.update(
                    destination_bytes=(directory / "destination.ttl").stat().st_size,
                    provenance_bytes=(directory / "provenance.ttl").stat().st_size,
                    status="passed",
                )
            measurement["final_disk_bytes"] = directory_size(directory)
        except ProcessingError as exc:
            measurement.update(
                status="rejected",
                error={"code": exc.code, "message": str(exc), "stage": stage},
                failure_metrics=getattr(exc, "metrics", {}),
            )
        measurements.append(measurement)
    report = {
        "qualification_version": "neo-qualification/2",
        "execution_fingerprint": execution_fingerprint(),
        "converter_sha256": CONVERTER_SHA256,
        "limits": LIMITS.configuration(),
        "measurements": measurements,
        "revision_digest": revision.content_digest,
        "all_passed": all(item["status"] == "passed" for item in measurements),
        "admission_enabled": False,
        "capacity_claim": "Only these inputs measured; not a general size guarantee",
    }
    (output_dir / "measurements.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--revision", type=Path, required=True, help="Retained published revision JSON, not a draft")
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="New private output directory")
    args = parser.parse_args()
    revision = TranslationPublishedRevision.model_validate_json(args.revision.read_text(encoding="utf-8"))
    report = qualify(args.binary, revision, args.input, args.output_dir)
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
