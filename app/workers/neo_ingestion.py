"""The sole processing task for revision-bound Neo ingestion."""

from app.ingestion.policy import TASK_HARD_SECONDS, TASK_SOFT_SECONDS
from app.workers.celery_app import celery_app


@celery_app.task(
    name="neo_ingestion",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
    soft_time_limit=TASK_SOFT_SECONDS,
    time_limit=TASK_HARD_SECONDS,
)
def neo_ingestion(job_id: str, generation: int):
    from sqlmodel import Session

    from app.core.config import settings
    from app.ingestion.execution import ExecutionStore
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.runtime import IsolatedRuntime
    from app.models.database import engine
    from app.storage.ingestion_objects import IngestionObjects

    pipeline = IngestionPipeline(
        ExecutionStore(lambda: Session(engine)),
        IngestionObjects.for_worker(),
        IsolatedRuntime(settings.NEO_CONVERTER_PATH),
        settings.NEO_ATTEMPT_ROOT,
    )
    return pipeline.run(job_id, generation)
