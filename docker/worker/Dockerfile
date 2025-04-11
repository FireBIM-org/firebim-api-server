FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir ifcopenshell

# # Install ifcopenshell
# RUN apt-get update && \
#     apt-get install -y --no-install-recommends \
#     wget unzip && \
#     wget https://s3.amazonaws.com/ifcopenshell-builds/ifcopenshell-python-$(python3 -c 'import platform; print(platform.python_version()[:3])')-v0.7.0-9f2317d-linux64.zip -O ifcopenshell.zip && \
#     unzip ifcopenshell.zip -d /tmp && \
#     cp /tmp/ifcopenshell*.whl . && \
#     pip install *.whl && \
#     rm -rf /tmp/ifcopenshell* *.whl && \
#     apt-get remove -y wget unzip && \
#     apt-get autoremove -y && \
#     apt-get clean && \
#     rm -rf /var/lib/apt/lists/*

# Copy application code
COPY app/workers /app/

# Create directory for temporary files
RUN mkdir -p /tmp

# Set environment variables
ENV PYTHONPATH=/
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Expose the port that the worker will run on
EXPOSE 8000

# Command to run the worker service
CMD ["uvicorn", "worker:app", "--host", "0.0.0.0", "--port", "8000"]