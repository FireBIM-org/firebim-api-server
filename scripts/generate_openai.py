import json
import os
import sys

# Add app to path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(root_dir)

app_dir = os.path.join(root_dir, "app")

# Change working directory to app directory
os.chdir(app_dir)

# Provide dummy values for secrets that are normally injected at runtime
# (e.g. from /var/run/secrets in Kubernetes) so that Settings() validation
# passes during schema generation.
os.environ.setdefault("DB-PASSWORD", "dummy")
os.environ.setdefault("S3-SECRET-KEY", "dummy")

from app.main import app  # noqa: E402  # Import the FastAPI app instance (path setup required first)

print("Generating OpenAPI schema...")
openapi_schema = app.openapi()

# Write the OpenAPI JSON schema to a file called "openapi.json"
os.chdir(root_dir)  # Change back to the root directory
with open("openapi.json", "w") as f:
    json.dump(openapi_schema, f, indent=2)

print("OpenAPI schema generated and saved to openapi.json")
