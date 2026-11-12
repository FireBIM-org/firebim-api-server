from celery import Celery

from app.core.config import settings

# Celery configuration
celery_app = Celery("firebim_tasks", include=["app.ontologies.sync_task"])
celery_app.conf.broker_url = settings.CELERY_BROKER_URL
celery_app.conf.result_backend = settings.CELERY_RESULT_BACKEND
celery_app.conf.update(
    # A runaway task is interrupted (soft limit, catchable) and then killed (hard limit).
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    # Acknowledge after the task finished, and requeue it if the worker dies (e.g. OOM-killed) mid-task.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Long tasks: do not reserve work another worker could pick up.
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)
