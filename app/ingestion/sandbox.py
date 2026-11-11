"""Credential-free child entry point. No Celery tasks or storage access here."""

import hashlib
import json
import os
import resource
import signal
import sys
from dataclasses import asdict
from pathlib import Path
from time import monotonic


def write_json(path, value):
    Path(path).write_text(json.dumps(value, allow_nan=False, separators=(",", ":")), encoding="utf-8")


def map_revision(limits):
    from rdflib import Graph

    from app.ingestion.display import build_display_index, encode_display_index
    from app.ingestion.neo_ifcowl_adapter import NeoAdapterError
    from app.ingestion.neo_revision import evaluate_neo_revision
    from app.ingestion.policy import MODULES, PRODUCERS
    from app.schemas.translation_revision import TranslationPublishedRevision

    started = monotonic()
    log = Path("converter.log").read_text(encoding="utf-8")
    for module in ("parse", *MODULES):
        if f"module {module}: success" not in log:
            raise ValueError(f"Required module did not complete: {module}")
    counts = {}
    source = None
    for producer in PRODUCERS:
        graph = Graph().parse(f"source_{producer}.ttl", format="turtle")
        if len(graph) > limits["triples"] or (producer == "ifcowl" and not graph):
            raise ValueError(f"Invalid producer triple count: {producer}")
        counts[producer] = len(graph)
        if producer == "ifcowl":
            source = graph
    revision = TranslationPublishedRevision.model_validate_json(Path("revision.json").read_text(encoding="utf-8"))
    try:
        adapted, result = evaluate_neo_revision(revision, source, Path("model-id").read_text(encoding="utf-8"))
    except NeoAdapterError as exc:
        write_json(
            "mapping-report.json",
            {
                "report_version": "neo-mapping/1",
                "diagnostics": [
                    {"code": exc.code, "message": str(exc), "source_iri": exc.source_iri, "severity": "error"},
                ],
            },
        )
        return
    write_json("evaluation.json", asdict(result))
    display_index = build_display_index(
        adapted,
        [asdict(item) for item in result.assertions],
        Path("model-id").read_text(encoding="utf-8"),
        revision.content_digest,
    )
    Path("display-index.json").write_bytes(encode_display_index(display_index))
    write_json(
        "evidence.json",
        {
            "evidence_version": "neo-evidence/1",
            "owned_values": [asdict(item) for item in adapted.evidence],
            "identities": {key: asdict(item) for key, item in adapted.identities.items()},
            "model_units": adapted.model_units,
        },
    )
    write_json(
        "mapping-report.json",
        {
            "report_version": "neo-mapping/1",
            "diagnostics": [asdict(item) for item in result.diagnostics],
            "records": len(adapted.records),
            "assertions": len(result.assertions),
            "memberships": len(result.memberships),
            "producer_triples": counts,
            "mapping_seconds": monotonic() - started,
        },
    )


def validate_destination():
    from app.ingestion.display import bind_display_index, encode_display_index
    from app.ingestion.errors import ProcessingError
    from app.ingestion.rdf import serialize_destination
    from app.schemas.translation_revision import TranslationPublishedRevision

    revision = TranslationPublishedRevision.model_validate_json(Path("revision.json").read_text(encoding="utf-8"))
    evaluation = Path("evaluation.json").read_bytes()
    model_id = Path("model-id").read_text(encoding="utf-8")
    try:
        serialized = serialize_destination(revision, json.loads(evaluation), model_id)
    except ProcessingError as exc:
        write_json(
            "validation-report.json",
            {
                "valid": False,
                "diagnostics": [
                    {"code": exc.code, "message": str(exc), "severity": "error"},
                ],
            },
        )
        return
    Path("destination.ttl").write_bytes(serialized.destination)
    Path("provenance.ttl").write_bytes(serialized.provenance)
    validation = {
        **serialized.validation,
        "revision_digest": revision.content_digest,
        "model_id": model_id,
        "evaluation_sha256": hashlib.sha256(evaluation).hexdigest(),
        "diagnostics": [],
        "destination_sha256": hashlib.sha256(serialized.destination).hexdigest(),
        "provenance_sha256": hashlib.sha256(serialized.provenance).hexdigest(),
    }
    display_index = json.loads(Path("display-index.json").read_text(encoding="utf-8"))
    display_index, validation = bind_display_index(display_index, validation)
    Path("display-index.json").write_bytes(encode_display_index(display_index))
    write_json("validation-report.json", validation)


def main():
    # Invoked with -I: only the server-owned application directory is added.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    mode, configuration = sys.argv[1:3]
    limits = json.loads(configuration)
    import ctypes

    parent = int(os.environ["NEO_SUPERVISOR_PID"])
    if os.getppid() != parent or ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise RuntimeError("Parent-death isolation is unavailable")
    if os.getppid() != parent:
        raise RuntimeError("Attempt supervisor exited")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    for kind, value in (
        (resource.RLIMIT_AS, limits["memory_bytes"]),
        (resource.RLIMIT_CPU, limits["cpu_seconds"]),
        (resource.RLIMIT_FSIZE, limits["file_bytes"]),
        (resource.RLIMIT_NOFILE, 64),
    ):
        resource.setrlimit(kind, (value, value))
    os.umask(0o077)
    if mode == "convert":
        from app.ingestion.policy import converter_arguments

        command = converter_arguments(sys.argv[3])
        os.execv(command[0], command)
    elif mode == "map":
        try:
            map_revision(limits)
        except Exception as exc:
            # Full source details stay in private logs, not public task results.
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
            write_json(
                "mapping-report.json",
                {
                    "report_version": "neo-mapping/1",
                    "diagnostics": [
                        {
                            "code": "invalid_producer_artifact",
                            "message": "Required producer artifacts could not be validated",
                            "severity": "error",
                        },
                    ],
                },
            )
            return 1
    elif mode == "validate":
        validate_destination()
    else:
        raise ValueError("Unknown sandbox operation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
