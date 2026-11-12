"""Telemetry, JSON logs and task metrics for the Celery worker (imported by app/workers/celery_worker.py).

- Telemetry starts in each pool process (``worker_process_init``), after the fork, so exporter threads and HTTP
  connections are never shared between processes. Pools that do not fork (``--pool=solo``/``threads``) start it
  once in ``worker_init``. Celery's own instrumentation continues the trace the API started when it queued the task.
- ``LOG_FORMAT=json`` replaces the handlers Celery installs on the root and task loggers with the JSON handler.
- ``firebim.celery.tasks`` / ``firebim.celery.task.duration`` are recorded from ``task_prerun``/``task_postrun``;
  ``task_failure`` marks the run as failed.
"""

from __future__ import annotations

import logging
from time import monotonic

from celery.signals import (
    after_setup_logger,
    after_setup_task_logger,
    task_failure,
    task_postrun,
    task_prerun,
    worker_init,
    worker_process_init,
    worker_process_shutdown,
)

from app.core import metrics
from app.core.config import settings
from app.core.log_config import json_handler
from app.core.telemetry import WORKER_SERVICE_NAME, init_telemetry, service_name_for, shutdown_telemetry

# task_id -> monotonic start time / failed flag, for tasks running in this process.
_started: dict[str, float] = {}
_failed: set[str] = set()


def _use_json_handler(target: logging.Logger, loglevel) -> None:
    if settings.log_format != "json":
        return
    for handler in list(target.handlers):
        target.removeHandler(handler)
    target.addHandler(json_handler(service_name_for(WORKER_SERVICE_NAME)))
    if loglevel:
        target.setLevel(loglevel)


@after_setup_logger.connect(weak=False)
def _json_root_logger(logger=None, loglevel=None, **_):
    if logger is not None:
        _use_json_handler(logger, loglevel)


@after_setup_task_logger.connect(weak=False)
def _json_task_logger(logger=None, loglevel=None, **_):
    if logger is not None:
        _use_json_handler(logger, loglevel)


def _is_prefork(worker) -> bool:
    return "prefork" in str(getattr(worker, "pool_cls", "prefork")).lower()


@worker_init.connect(weak=False)
def _init_telemetry_without_fork(sender=None, **_):
    if not _is_prefork(sender):
        init_telemetry(WORKER_SERVICE_NAME)


@worker_process_init.connect(weak=False)
def _init_telemetry_in_pool_process(**_):
    init_telemetry(WORKER_SERVICE_NAME)


@worker_process_shutdown.connect(weak=False)
def _flush_telemetry(**_):
    shutdown_telemetry()


def _task_name(task, sender) -> str:
    return str(getattr(task, "name", None) or getattr(sender, "name", None) or "unknown")


@task_prerun.connect(weak=False)
def _task_started(task_id=None, task=None, **_):
    if task_id:
        _started[task_id] = monotonic()


@task_failure.connect(weak=False)
def _task_failed(sender=None, task_id=None, **_):
    if not task_id:
        return
    if task_id in _started:
        _failed.add(task_id)  # task_postrun follows and records it
    else:
        # Reported without a run in this process: a pool process that died (hard time limit, OOM) is reported by the
        # prefork parent, which has no telemetry (it only starts after the fork), so such tasks are not counted.
        metrics.record_celery_task(_task_name(None, sender), "failure", None)


@task_postrun.connect(weak=False)
def _task_finished(sender=None, task_id=None, task=None, state=None, **_):
    started = _started.pop(task_id, None) if task_id else None
    failed = task_id in _failed
    _failed.discard(task_id)
    final_state = (state or ("FAILURE" if failed else "SUCCESS")).lower()
    duration = monotonic() - started if started is not None else None
    metrics.record_celery_task(_task_name(task, sender), final_state, duration)


__all__ = ["WORKER_SERVICE_NAME"]
