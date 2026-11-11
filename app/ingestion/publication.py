"""Private preparation first; a single fenced database commit is visibility."""

import hashlib
import json
from urllib.parse import quote

from sqlmodel import select

from app.ingestion.display import validate_display_index
from app.ingestion.execution import LeaseLost, ProcessingError
from app.ingestion.policy import LIMITS
from app.ingestion.rdf import RDF_CONTRACT
from app.models.models import IfcModel, PublishedGraph
from app.storage.building_repository import default_building_graph_uri


def graph_id_for_job(job_id):
    return "neo_" + hashlib.sha256(job_id.encode()).hexdigest()


def committed_publication(store, claim):
    """Resolve ambiguous commit acknowledgements without rewriting a successful job."""
    with store.sessions() as db:
        model, job = store._lock(db, claim.job.id)
        if (
            not model
            or model.deleted_at is not None
            or not job
            or job.cancellation_requested
            or job.fencing_token != claim.fencing_token
            or job.dispatch_generation != claim.job.dispatch_generation
        ):
            raise LeaseLost()
        if job.status != "succeeded":
            return None
        graph = db.get(PublishedGraph, job.result_graph_id)
        if (
            not graph
            or graph.id != graph_id_for_job(job.id)
            or graph.job_id != job.id
            or graph.model_id != model.id
            or graph.user_id != job.user_id
            or graph.fencing_token != claim.fencing_token
            or graph.status != "ready"
        ):
            raise ProcessingError("publication_integrity", "Committed graph registration is inconsistent")
        return graph.id


def _json(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def publish_ingestion(store, claim, prepared):
    previous = committed_publication(store, claim)
    if previous:
        return previous
    job = claim.job
    graph_id = graph_id_for_job(job.id)
    graph_uri = default_building_graph_uri(graph_id)
    store.heartbeat(claim, "validating_destination")
    if (
        prepared.execution_fingerprint != claim.execution_fingerprint
        or prepared.revision.revision_id != job.revision_id
        or prepared.revision.content_digest != job.revision_digest
        or prepared.revision.content.profile_id != job.profile_id
    ):
        raise ProcessingError("publication_binding", "Prepared result differs from the admitted execution")
    # Serialization/ontology validation ran in the credential-free limited child.
    # Verify its bytes and binding again before any database visibility change.
    for name in ("destination.ttl", "provenance.ttl", "validation-report.json", "display-index.json"):
        reference = prepared.artifacts[name]
        data = prepared.objects.read_verified(reference["key"], reference["bytes"], reference["sha256"])
        store.heartbeat(claim)
        if name == "validation-report.json":
            validation = json.loads(data)
        elif name == "display-index.json":
            display_index = json.loads(data)
    if (
        not validation.get("valid")
        or validation.get("validation_version") != RDF_CONTRACT
        or validation.get("revision_digest") != job.revision_digest
        or validation.get("model_id") != job.model_id
        or any(
            validation.get(name + "_sha256") != prepared.artifacts[name + "." + extension]["sha256"]
            for name, extension in (("evaluation", "json"), ("destination", "ttl"), ("provenance", "ttl"))
        )
    ):
        raise ProcessingError("publication_binding", "Validated destination does not match the admitted execution")
    try:
        validate_display_index(display_index, validation, job.model_id, job.revision_digest)
    except (TypeError, ValueError) as exc:
        raise ProcessingError(
            "publication_binding", "Validated display index does not match the admitted execution"
        ) from exc
    # Old legacy graphs cannot be shadowed. New legacy writes reject this namespace.
    if prepared.objects.exists(f"graphs/buildings/{quote(graph_uri, safe='')}.ttl"):
        raise ProcessingError("graph_identity_collision", "Graph identity is already used by a legacy graph")
    prefix = f"ingestion/jobs/{quote(job.id, safe='')}/attempts/{claim.number}-{claim.fencing_token}/publication/"
    artifacts = dict(prepared.artifacts)

    def retain(name, data):
        store.heartbeat(claim, "retaining_destination")
        if len(data) > LIMITS.file_bytes:
            raise ProcessingError("destination_limit", "Publication artifact exceeds the byte budget")
        # Content addressing also makes a pre-commit replay safe for timing reports.
        reference = prepared.objects.write_private(prefix + hashlib.sha256(data).hexdigest() + "/" + name, data)
        artifacts[name] = reference
        return reference

    destination = artifacts["destination.ttl"]
    provenance = artifacts["provenance.ttl"]
    retain("revision.json", prepared.revision.model_dump_json().encode())
    ontologies = []
    for index, artifact in enumerate(prepared.revision.content.ontology_artifacts):
        reference = retain(f"ontology-{index}.ttl", artifact.ttl.encode())
        ontologies.append({"pin": artifact.pin.model_dump(mode="json"), "artifact": reference})
    prepared.report["destination"] = validation
    report = retain("publication-report.json", _json(prepared.report))
    raw = {"key": job.raw_key, "bytes": job.raw_size, "sha256": job.raw_sha256}
    manifest = retain(
        "manifest.json",
        _json(
            {
                "manifest_version": "neo-publication/1",
                "rdf_contract": RDF_CONTRACT,
                "graph_id": graph_id,
                "graph_uri": graph_uri,
                "model_id": job.model_id,
                "job_id": job.id,
                "attempt": claim.number,
                "fencing_token": claim.fencing_token,
                "raw_input": raw,
                "profile_id": job.profile_id,
                "revision_id": job.revision_id,
                "revision_digest": job.revision_digest,
                "runtime_fingerprint": job.runtime_fingerprint,
                "execution_fingerprint": claim.execution_fingerprint,
                "ontology_artifacts": ontologies,
                "artifacts": dict(artifacts),
                "validation": validation,
            }
        ),
    )
    checkpoint = {
        "checkpoint_version": "neo-checkpoint/1",
        "execution_fingerprint": claim.execution_fingerprint,
        "runtime_fingerprint": job.runtime_fingerprint,
        "input_sha256": job.raw_sha256,
        "revision_digest": job.revision_digest,
        "artifacts": artifacts,
        "metrics": validation,
    }
    store.checkpoint(claim, "publication_ready", checkpoint, report=prepared.report)
    store.heartbeat(claim, "publishing")

    def register(db, model, locked_job):
        if model.ingestion_backend != "neo" or model.user_id != locked_job.user_id:
            raise ProcessingError("publication_binding", "Model ownership/backend differs from the job")
        if (
            db.get(PublishedGraph, graph_id)
            or db.exec(
                select(PublishedGraph).where(
                    PublishedGraph.job_id == locked_job.id,
                )
            ).first()
            or db.get(IfcModel, graph_id)
        ):
            raise ProcessingError("graph_identity_collision", "Graph identity is already registered")
        db.add(
            PublishedGraph(
                id=graph_id,
                graph_uri=graph_uri,
                user_id=locked_job.user_id,
                model_id=model.id,
                job_id=locked_job.id,
                profile_id=locked_job.profile_id,
                revision_id=locked_job.revision_id,
                revision_digest=locked_job.revision_digest,
                input_sha256=locked_job.raw_sha256,
                runtime_fingerprint=locked_job.runtime_fingerprint,
                execution_fingerprint=claim.execution_fingerprint,
                fencing_token=claim.fencing_token,
                attempt_number=claim.number,
                triple_count=validation["triples"],
                destination=destination,
                provenance=provenance,
                ontology_artifacts=ontologies,
                manifest=manifest,
                report=report,
                created_at=store.now(db),
            )
        )
        db.flush()
        return graph_id

    try:
        return store.publish(claim, register)
    except Exception:
        # A connection can die after COMMIT was accepted. Never mark that success failed.
        previous = committed_publication(store, claim)
        if previous:
            return previous
        raise
