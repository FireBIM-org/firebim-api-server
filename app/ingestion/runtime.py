"""Bounded subprocesses within one ingestion task, never queued stage tasks."""

import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

from app.ingestion.execution import ProcessingError
from app.ingestion.jobs import CONVERTER_SHA256
from app.ingestion.policy import CHILD_ENVIRONMENT, LIMITS

SANDBOX = Path(__file__).with_name("sandbox.py").resolve()


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_binary(binary):
    try:
        binary = Path(binary).resolve(strict=True)
        digest = file_digest(binary)
    except OSError as exc:
        raise ProcessingError("converter_unavailable", "Pinned converter is not installed or readable") from exc
    if digest != CONVERTER_SHA256:
        raise ProcessingError("converter_digest_mismatch", "Expected the pinned converter executable")
    return binary


def directory_size(directory, limits=LIMITS):
    size, count = 0, 0
    for path in directory.iterdir():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ProcessingError("invalid_artifact", "Only regular attempt files are permitted")
        count += 1
        size += info.st_size
        if info.st_size > limits.file_bytes:
            raise ProcessingError("file_limit", "Attempt file exceeds the output limit")
    if size > limits.disk_bytes or count > limits.files:
        raise ProcessingError("disk_limit", "Attempt exceeds the disk/file budget")
    return size


def run_isolated(command, directory, *, seconds, heartbeat, limits=LIMITS, log_name="runner.log"):
    """Linux child limits are set by sandbox.py before parser/converter imports.

    Use wait4 rather than cumulative RUSAGE_CHILDREN so measurements belong to
    this process, not previous Celery jobs. Kill the whole session on any exit.
    """
    started = time.monotonic()
    peak_disk = directory_size(directory, limits)
    process = None
    usage = None
    failure = None
    with (directory / log_name).open("wb") as log:
        try:
            process = subprocess.Popen(
                command,
                cwd=directory,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
                env={**CHILD_ENVIRONMENT, "NEO_SUPERVISOR_PID": str(os.getpid())},
            )
            next_heartbeat = 0
            while True:
                pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                if pid:
                    process.returncode = os.waitstatus_to_exitcode(status)
                    break
                now = time.monotonic()
                if now - started > seconds:
                    raise ProcessingError("stage_timeout", "Processing stage exceeded its wall-clock limit")
                peak_disk = max(peak_disk, directory_size(directory, limits))
                if now >= next_heartbeat:
                    heartbeat()
                    next_heartbeat = now + 1
                time.sleep(0.05)
            heartbeat()
            peak_disk = max(peak_disk, directory_size(directory, limits))
            metrics = {
                "wall_seconds": time.monotonic() - started,
                "cpu_seconds": usage.ru_utime + usage.ru_stime,
                "peak_rss_bytes": usage.ru_maxrss * 1024,
                "peak_disk_bytes": peak_disk,
                "exit_code": process.returncode,
            }
            if process.returncode:
                raise ProcessingError("stage_process_failed", "Isolated processing failed or exceeded a resource limit")
            return metrics
        except ProcessingError as exc:
            failure = exc
            raise
        finally:
            if process is not None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                if process.returncode is None:
                    _, status, usage = os.wait4(process.pid, 0)
                    process.returncode = os.waitstatus_to_exitcode(status)
            if failure is not None:
                failure.metrics = {
                    "wall_seconds": time.monotonic() - started,
                    "peak_disk_bytes": peak_disk,
                    "exit_code": process.returncode if process is not None else None,
                }
                if usage is not None:
                    failure.metrics.update(
                        cpu_seconds=usage.ru_utime + usage.ru_stime, peak_rss_bytes=usage.ru_maxrss * 1024
                    )


class IsolatedRuntime:
    def __init__(self, binary, *, limits=LIMITS):
        self.binary = Path(binary)
        self.limits = limits

    def convert(self, directory, heartbeat):
        binary = verify_binary(self.binary)
        return run_isolated(
            [sys.executable, "-I", str(SANDBOX), "convert", json.dumps(self.limits.configuration()), str(binary)],
            directory,
            seconds=self.limits.conversion_seconds,
            heartbeat=heartbeat,
            limits=self.limits,
            log_name="converter.log",
        )

    def map(self, directory, heartbeat):
        return run_isolated(
            [sys.executable, "-I", str(SANDBOX), "map", json.dumps(self.limits.configuration())],
            directory,
            seconds=self.limits.mapping_seconds,
            heartbeat=heartbeat,
            limits=self.limits,
            log_name="mapping.log",
        )

    def validate(self, directory, heartbeat):
        return run_isolated(
            [sys.executable, "-I", str(SANDBOX), "validate", json.dumps(self.limits.configuration())],
            directory,
            seconds=self.limits.validation_seconds,
            heartbeat=heartbeat,
            limits=self.limits,
            log_name="validation.log",
        )
