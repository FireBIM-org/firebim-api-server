from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))


def main() -> int:
    cleanup_generated_rulebook = import_module(
        "tests.generated_cleanup"
    ).cleanup_generated_rulebook
    summary = cleanup_generated_rulebook(verbose=True)
    if summary["failed_rules"] or summary["failed_rule_elements"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
