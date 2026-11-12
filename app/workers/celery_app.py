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
    # Acknowledge after the task finished, so no message sits prefetched and unacknowledged behind a long task.
    # With late acks RabbitMQ's consumer_timeout (default 30 min) applies to the running task: the hard time limit
    # must stay below it, or the broker closes the channel and redelivers a task that is still running.
    task_acks_late=True,
    # A task whose pool process dies (e.g. OOM-killed on a huge IFC) is failed and acknowledged, never requeued:
    # requeueing would turn an input that always kills the worker into a poison message that blocks the queue.
    # If the whole worker dies the broker still redelivers; the IFC tasks refuse redeliveries (celery_worker.py).
    task_reject_on_worker_lost=False,
    # Long tasks: do not reserve work another worker could pick up.
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)
