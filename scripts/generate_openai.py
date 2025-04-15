import json
import sys
import os

# Add app to path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(root_dir)

app_dir = os.path.join(root_dir, "app")

# Change working directory to app directory
os.chdir(app_dir)

# Import the FastAPI app instance
from app.main import app

print("Generating OpenAPI schema...")
openapi_schema = app.openapi()

# Write the OpenAPI JSON schema to a file called "openapi.json"
os.chdir(root_dir)  # Change back to the root directory
with open("openapi.json", "w") as f:
    json.dump(openapi_schema, f, indent=2)

print("OpenAPI schema generated and saved to openapi.json")
