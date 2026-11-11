"""Transactional outbox delivery service, NOT a processing-stage Celery task."""

import argparse
import logging
import time
from datetime import timedelta

from sqlalchemy import and_, or_
from sqlmodel import select

from app.models.models import IfcModel, IngestionJob, IngestionOutbox

logger = logging.getLogger(__name__)
REDELIVERY_DELAYS = (120, 240, 480, 900)


def _redelivery_due(now):
    deadlines = [
        and_(
            IngestionOutbox.dispatch_attempts <= 1,
            IngestionOutbox.delivered_at <= now - timedelta(seconds=REDELIVERY_DELAYS[0]),
        )
    ]
    deadlines.extend(
        and_(IngestionOutbox.dispatch_attempts == count, IngestionOutbox.delivered_at <= now - timedelta(seconds=delay))
        for count, delay in enumerate(REDELIVERY_DELAYS[1:-1], start=2)
    )
    deadlines.append(
        and_(
            IngestionOutbox.dispatch_attempts >= len(REDELIVERY_DELAYS),
            IngestionOutbox.delivered_at <= now - timedelta(seconds=REDELIVERY_DELAYS[-1]),
        )
    )
    return or_(*deadlines)


def dispatch_pending(store, broker, limit=50):
    delivered = 0
    for _ in range(limit):
        with store.sessions() as db:
            now = store.now(db)
            event = db.exec(
                select(IngestionOutbox)
                .join(IngestionJob)
                .join(IfcModel)
                .where(
                    IngestionJob.status.in_(["queued", "retry_wait"]),
                    IngestionJob.dispatch_generation == IngestionOutbox.generation,
                    IngestionJob.cancellation_requested.is_(False),
                    IfcModel.deleted_at.is_(None),
                    IngestionOutbox.available_at <= now,
                    or_(IngestionOutbox.delivered_at.is_(None), _redelivery_due(now)),
                )
                .order_by(IngestionOutbox.created_at)
                .limit(1)
                .with_for_update(of=IngestionOutbox, skip_locked=True)
            ).first()
            if event is None:
                break
            event.dispatch_attempts += 1
            try:
                if event.task_name != "neo_ingestion":
                    raise ValueError("Unsupported outbox task type")
                # A crash after confirmation but before commit may resend. Claims
                # and fencing, not task IDs or the Celery result backend, deduplicate.
                broker.send_task(
                    "neo_ingestion",
                    kwargs={"job_id": event.job_id, "generation": event.generation},
                    task_id=f"neo-{event.job_id}-{event.generation}",
                    delivery_mode=2,
                    retry=False,
                    ignore_result=True,
                    queue="neo_ingestion",
                    timeout=5,
                    confirm_timeout=5,
                )
                event.delivered_at, event.last_error = store.now(db), None
                delivered += 1
            except Exception as exc:
                event.delivered_at, event.last_error = None, type(exc).__name__
                event.available_at = now + timedelta(seconds=min(60, 2 ** min(event.dispatch_attempts, 6)))
            db.add(event)
            db.commit()
    return delivered


def main():
    from sqlmodel import Session

    from app.ingestion.execution import ExecutionStore
    from app.models.database import engine
    from app.workers.celery_app import celery_app

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    store = ExecutionStore(lambda: Session(engine))
    while True:
        try:
            store.recover_expired()
            dispatch_pending(store, celery_app)
        except Exception:
            logger.exception("Ingestion outbox iteration failed")
            if args.once:
                raise
        if args.once:
            return
        time.sleep(1)


if __name__ == "__main__":
    main()
