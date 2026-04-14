from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from tests.generated_cleanup import cleanup_generated_rulebook


def main() -> int:
    summary = cleanup_generated_rulebook(verbose=True)
    if summary["failed_rules"] or summary["failed_rule_elements"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
