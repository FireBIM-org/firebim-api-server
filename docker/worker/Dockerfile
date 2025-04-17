FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
COPY docker/worker/requirements.txt requirements-worker.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -r requirements-worker.txt

# Create directory for temporary files
RUN mkdir -p /tmp

# Set environment variables
ENV PYTHONPATH=/
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Command to run the Celery worker
CMD ["celery", "-A", "app.workers.celery_worker", "worker", "--loglevel=info"]