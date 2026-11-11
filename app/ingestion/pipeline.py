"""Ordinary-function orchestration owned by the one Neo ingestion task."""

import hashlib
import json
import platform
import re
import shutil
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import rdflib
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy.exc import OperationalError

from app.ingestion.execution import LeaseLost, ProcessingError
from app.ingestion.jobs import AdmissionError, AdmissionRuntime, content_digest, require_supported_revision
from app.ingestion.policy import LIMITS, MODULES, PRODUCERS, worker_contract
from app.ingestion.runtime import directory_size
from app.schemas.translation_revision import TranslationPublishedRevision
from app.storage.ingestion_objects import validate_ifc4

CONVERTED = ("converter.log", *(f"source_{producer}.ttl" for producer in PRODUCERS))
MAPPED = ("evaluation.json", "evidence.json", "mapping-report.json", "mapping.log")
VALIDATED = ("destination.ttl", "provenance.ttl", "validation-report.json", "validation.log", "display-index.json")
TRANSIENT_SQLSTATES = {"40001", "40P01", "55P03", "57P01", "57P02", "57P03"}


def _is_transient_operational_error(error):
    if error.connection_invalidated or isinstance(error.orig, (ConnectionError, TimeoutError)):
        return True
    # Driver-level connection failures (often no SQL state)
    if error.orig and "connection refused" in str(error.orig).lower():
        return True
    sqlstate = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
    if sqlstate and (sqlstate.startswith("08") or sqlstate.startswith("53") or sqlstate in TRANSIENT_SQLSTATES):
        return True
    sqlite_error = str(getattr(error.orig, "sqlite_errorname", ""))
    return sqlite_error.startswith(("SQLITE_BUSY", "SQLITE_LOCKED"))


def execution_fingerprint():
    return content_digest(
        {
            "contract": worker_contract(),
            "python": platform.python_version(),
            "rdflib": rdflib.__version__,
        }
    )


@dataclass(frozen=True)
class PreparedIngestion:
    revision: TranslationPublishedRevision
    evaluation: dict
    artifacts: dict
    report: dict
    execution_fingerprint: str
    objects: object


def publish_ingestion(store, claim, prepared):
    from app.ingestion.publication import publish_ingestion as publish

    return publish(store, claim, prepared)


class IngestionPipeline:
    def __init__(self, store, objects, runtime, attempt_root, *, publisher=publish_ingestion):
        self.store, self.objects, self.runtime = store, objects, runtime
        self.attempt_root = Path(attempt_root)
        self.publisher = publisher

    def _checkpoint(self, claim, artifacts, metrics):
        return {
            "checkpoint_version": "neo-checkpoint/1",
            "execution_fingerprint": claim.execution_fingerprint,
            "runtime_fingerprint": claim.job.runtime_fingerprint,
            "input_sha256": claim.job.raw_sha256,
            "revision_digest": claim.job.revision_digest,
            "artifacts": artifacts,
            "metrics": metrics,
        }

    def _retain(self, claim, directory, names):
        artifacts = {}
        prefix = f"ingestion/jobs/{quote(claim.job.id, safe='')}/attempts/{claim.number}-{claim.fencing_token}/"
        directory_size(directory)
        for name in names:
            self.store.heartbeat(claim)
            data = (directory / name).read_bytes()
            artifacts[name] = self.objects.write_private(prefix + hashlib.sha256(data).hexdigest() + "/" + name, data)
        return artifacts

    def _restore_conversion(self, claim, directory):
        checkpoint = claim.job.checkpoints.get("converted", {})
        expected = self._checkpoint(claim, checkpoint.get("artifacts", {}), checkpoint.get("metrics", {}))
        if checkpoint != expected or set(checkpoint.get("artifacts", {})) != set(CONVERTED):
            return None
        for name, reference in checkpoint["artifacts"].items():
            if not 0 <= reference["bytes"] <= LIMITS.file_bytes or not reference["key"].startswith(
                f"ingestion/jobs/{quote(claim.job.id, safe='')}/attempts/",
            ):
                raise ProcessingError(
                    "invalid_checkpoint",
                    "Conversion checkpoint is outside the job artifact contract",
                )
            self.store.heartbeat(claim)
            try:
                data = self.objects.read_verified(reference["key"], reference["bytes"], reference["sha256"])
            except AdmissionError as exc:
                if exc.detail["code"] in {"input_hash_mismatch", "input_size_mismatch", "input_missing"}:
                    raise ProcessingError(
                        "checkpoint_integrity_mismatch",
                        "Retained conversion checkpoint is missing or corrupt",
                    ) from exc
                raise
            (directory / name).write_bytes(data)
        directory_size(directory)
        return checkpoint

    def _prepare(self, claim, directory, report):
        job = claim.job
        config = AdmissionRuntime(max_input_bytes=job.runtime_config.get("max_input_bytes", 0)).configuration()
        if config != job.runtime_config or content_digest(config) != job.runtime_fingerprint:
            raise ProcessingError(
                "runtime_changed",
                "Admitted execution contract does not match this worker; reprocess explicitly",
            )
        if (
            job.raw_size <= 0
            or job.raw_size > config["max_input_bytes"]
            or job.raw_size > LIMITS.file_bytes
            or job.source_schema != "IFC4"
        ):
            raise ProcessingError("input_limit", "Retained input exceeds the admitted worker contract")
        revision = TranslationPublishedRevision.model_validate(job.revision_snapshot)
        require_supported_revision(revision)
        if (
            revision.content.profile_id != job.profile_id
            or revision.revision_id != job.revision_id
            or revision.content_digest != job.revision_digest
        ):
            raise ProcessingError("revision_binding_mismatch", "Retained revision does not match the admitted job")
        self.store.heartbeat(claim, "verifying_input")
        source = self.objects.read_verified(job.raw_key, job.raw_size, job.raw_sha256)
        validate_ifc4(source)
        (directory / "source.ifc").write_bytes(source)
        (directory / "revision.json").write_text(revision.model_dump_json(), encoding="utf-8")
        (directory / "model-id").write_text(job.model_id, encoding="utf-8")
        directory_size(directory)
        self.store.heartbeat(claim, "converting")
        conversion = self._restore_conversion(claim, directory)
        if conversion is None:
            metrics = self.runtime.convert(directory, lambda: self.store.heartbeat(claim))
            log = (directory / "converter.log").read_text(encoding="utf-8")
            if any(f"module {module}: success" not in log for module in ("parse", *MODULES)):
                raise ProcessingError("producer_incomplete", "A required producer did not report success")
            if any(not (directory / name).is_file() for name in CONVERTED):
                raise ProcessingError("producer_artifact_missing", "A required producer artifact is absent")
            artifacts = self._retain(claim, directory, CONVERTED)
            conversion = self._checkpoint(claim, artifacts, metrics)
            self.store.checkpoint(claim, "converted", conversion)
        report["conversion"] = conversion["metrics"]
        self.store.heartbeat(claim, "mapping")
        metrics = self.runtime.map(directory, lambda: self.store.heartbeat(claim))
        summary = json.loads((directory / "mapping-report.json").read_text(encoding="utf-8"))
        report["mapping"], report["summary"] = metrics, summary
        mapped_names = tuple(name for name in MAPPED if (directory / name).is_file())
        artifacts = {**conversion["artifacts"], **self._retain(claim, directory, mapped_names)}
        self.store.checkpoint(claim, "mapped", self._checkpoint(claim, artifacts, metrics))
        self.store.heartbeat(claim, "validating")
        if any(item.get("severity", "error") == "error" for item in summary["diagnostics"]):
            raise ProcessingError("mapping_rejected", "Mapping diagnostics rejected this input")
        evaluation = json.loads((directory / "evaluation.json").read_text(encoding="utf-8"))
        report["validation"] = self.runtime.validate(directory, lambda: self.store.heartbeat(claim))
        validation = json.loads((directory / "validation-report.json").read_text(encoding="utf-8"))
        report["destination"] = validation
        if not validation.get("valid"):
            report["diagnostics"] = validation["diagnostics"]
            raise ProcessingError(validation["diagnostics"][0]["code"], "Destination semantics rejected this input")
        artifacts.update(self._retain(claim, directory, VALIDATED))
        report["report_version"] = "neo-ingestion/2"
        report["execution_fingerprint"] = claim.execution_fingerprint
        report["runtime_fingerprint"] = job.runtime_fingerprint
        report["input_sha256"], report["revision_digest"] = job.raw_sha256, job.revision_digest
        report["diagnostics"] = summary["diagnostics"]
        (directory / "report.json").write_text(json.dumps(report, allow_nan=False), encoding="utf-8")
        artifacts.update(self._retain(claim, directory, ("report.json",)))
        self.store.checkpoint(claim, "validated", self._checkpoint(claim, artifacts, {}), report=report)
        return PreparedIngestion(revision, evaluation, artifacts, report, claim.execution_fingerprint, self.objects)

    def run(self, job_id, generation):
        claim = self.store.claim(job_id, generation, execution_fingerprint())
        if claim is None:
            return {"status": "ignored"}
        report = {"report_version": "neo-ingestion/2", "execution_fingerprint": claim.execution_fingerprint}
        try:
            self.attempt_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            for abandoned in self.attempt_root.iterdir():
                match = re.fullmatch(r"attempt-([0-9a-f-]{36})-([0-9]+)-.+", abandoned.name)
                if (
                    match
                    and not abandoned.is_symlink()
                    and abandoned.is_dir()
                    and self.store.attempt_inactive(match[1], int(match[2]))
                ):
                    shutil.rmtree(abandoned)
            with tempfile.TemporaryDirectory(
                prefix=f"attempt-{claim.job.id}-{claim.fencing_token}-", dir=self.attempt_root
            ) as directory:
                try:
                    prepared = self._prepare(claim, Path(directory), report)
                except LeaseLost:
                    raise
                except Exception:
                    # Retain bounded failure evidence too; a storage failure may
                    # prevent this, but must not replace the original diagnostic.
                    try:
                        (Path(directory) / "failure.log").write_text(traceback.format_exc(limit=20), encoding="utf-8")
                        names = tuple(
                            name
                            for name in (*CONVERTED, *MAPPED, *VALIDATED, "failure.log")
                            if (Path(directory) / name).is_file()
                        )
                        artifacts = self._retain(claim, Path(directory), names)
                        self.store.checkpoint(
                            claim, "failure_evidence", self._checkpoint(claim, artifacts, {}), advance=False
                        )
                    except (AdmissionError, OperationalError, ProcessingError):
                        pass
                    raise
                self.store.heartbeat(claim, "publishing")
                graph_id = self.publisher(self.store, claim, prepared)
                if not self.store.published(claim, graph_id):
                    raise ProcessingError("publication_contract", "Publisher did not commit an immutable graph")
                return {"status": "succeeded", "graph_id": graph_id}
        except LeaseLost:
            return {"status": "superseded"}
        except Exception as exc:
            if isinstance(exc, ProcessingError):
                error = exc
                if exc.metrics:
                    report["failed_stage_metrics"] = exc.metrics
            elif isinstance(exc, SoftTimeLimitExceeded):
                error = ProcessingError("attempt_timeout", "Ingestion attempt exceeded its time budget")
            elif isinstance(exc, AdmissionError):
                error = ProcessingError(exc.detail["code"], exc.detail["message"], transient=exc.status_code == 503)
            elif isinstance(exc, OperationalError):
                transient = _is_transient_operational_error(exc)
                error = ProcessingError(
                    "database_unavailable" if transient else "database_operation_failed",
                    "Temporary database operation failed" if transient else "Database operation failed",
                    transient=transient,
                )
            else:
                from app.ingestion.neo_ifcowl_adapter import NeoAdapterError

                report["exception_type"] = type(exc).__name__
                error = (
                    ProcessingError(exc.code, str(exc))
                    if isinstance(exc, NeoAdapterError)
                    else ProcessingError(
                        "processing_failed",
                        "Processing failed; consult the private attempt diagnostics",
                    )
                )
            try:
                self.store.fail(claim, error, report=report)
            except LeaseLost:
                return {"status": "superseded"}
            return {"status": "failed", "code": error.code}
