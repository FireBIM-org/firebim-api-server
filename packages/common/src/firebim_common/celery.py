from celery import Celery

from firebim_common.config import settings

celery_app = Celery("firebim_tasks")
celery_app.conf.broker_url = settings.CELERY_BROKER_URL
celery_app.conf.result_backend = settings.CELERY_RESULT_BACKEND
