from celery import Celery

from app.core.config import settings

# Celery configuration
celery_app = Celery("firebim_tasks", include=["app.ontologies.sync_task"])
celery_app.conf.broker_url = settings.CELERY_BROKER_URL
celery_app.conf.result_backend = settings.CELERY_RESULT_BACKEND
