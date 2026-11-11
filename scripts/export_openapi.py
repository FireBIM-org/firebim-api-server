"""Export the browser contract without starting services or the application lifespan."""

import argparse
import json
from pathlib import Path

from app.main import app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
