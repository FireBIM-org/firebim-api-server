FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ /app/app

# Add a non-root user to run the app
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 appuser

# Create directory for temporary files
RUN mkdir -p /tmp

# Set environment variables
ENV PYTHONPATH=/
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Switch to non-root user
USER appuser

# Expose the port that FastAPI will run on
EXPOSE 8000

# Command to run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
