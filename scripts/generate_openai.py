import json
import os
import sys

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(root_dir, "packages", "common", "src"))
sys.path.insert(0, os.path.join(root_dir, "packages", "api", "src"))

from firebim_api.main import app  # noqa: E402

print("Generating OpenAPI schema...")
openapi_schema = app.openapi()

with open(os.path.join(root_dir, "openapi.json"), "w") as f:
    json.dump(openapi_schema, f, indent=2)

print("OpenAPI schema generated and saved to openapi.json")
