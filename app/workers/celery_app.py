from celery import Celery

from app.core.config import settings

# Celery configuration
celery_app = Celery("firebim_tasks", include=["app.ontologies.sync_task", "app.workers.neo_ingestion"])
celery_app.conf.broker_url = settings.CELERY_BROKER_URL
celery_app.conf.result_backend = settings.CELERY_RESULT_BACKEND
celery_app.conf.broker_transport_options = {"confirm_publish": True}
celery_app.conf.broker_connection_timeout = 5
celery_app.conf.task_routes = {"neo_ingestion": {"queue": "neo_ingestion"}}
