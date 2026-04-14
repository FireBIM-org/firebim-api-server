from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
DEFAULT_SEQUENCE = [
    ROOT / "test_graphs_api_integration.py",
    ROOT / "test_rules_api_integration.py",
    ROOT / "test_rule_assembly_integration.py",
    ROOT / "test_checks_api_integration.py",
]
CLEANUP_SCRIPT = ROOT / "cleanup_generated_rulebook.py"


def _should_cleanup_after_run(argv: list[str]) -> bool:
    return "--cleanup-generated" in argv


def main() -> int:
    cleanup_enabled = _should_cleanup_after_run(sys.argv[1:])
    api_base = os.getenv("FIREBIM_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    integration_env = os.environ.copy()
    integration_env["FIREBIM_RUN_INTEGRATION"] = "1"
    print("FireBIM integration test runner")
    print(f"API base URL: {api_base}")
    print(f"Cleanup after run: {'enabled' if cleanup_enabled else 'disabled'}")
    print("Execution order:")
    for path in DEFAULT_SEQUENCE:
        print(f" - {path.name}")

    exit_code = 0
    try:
        for test_file in DEFAULT_SEQUENCE:
            if not test_file.exists():
                print(f"\nMissing expected test file: {test_file}")
                print("Adjust the filenames or place the files in the same tests directory before running.")
                return 1

            print(f"\nRunning {test_file.name} ...")
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_file), "-q"],
                cwd=str(ROOT),
                env=integration_env,
            )
            if result.returncode != 0:
                print(f"\nStopped after failure in {test_file.name}")
                exit_code = result.returncode
                break

        if exit_code == 0:
            print("\nAll integration test modules passed.")
        return exit_code
    finally:
        if cleanup_enabled and exit_code == 0:
            print("\nCleaning generated Fuseki rule data ...")
            cleanup_result = subprocess.run(
                [sys.executable, str(CLEANUP_SCRIPT)],
                cwd=str(ROOT),
                env=os.environ.copy(),
            )
            if cleanup_result.returncode != 0:
                print("Generated Fuseki rule cleanup reported failures.")


if __name__ == "__main__":
    raise SystemExit(main())
