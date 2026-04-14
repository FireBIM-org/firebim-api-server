from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))


def main() -> int:
    purge_generated_rulebook_data = import_module("tests.generated_data_ops").purge_generated_rulebook_data
    summary = purge_generated_rulebook_data(verbose=True)
    if summary["failed_rules"] or summary["failed_rule_elements"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
