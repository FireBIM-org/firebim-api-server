"""Parallel, bounded pytest files with persistent failing names and error logs (Linux).

Run from the API root with its venv Python, passing explicit test files. This module
also serves as the subprocess pytest plugin, so no optional pytest plugins are needed.
"""

import argparse
import json
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--per-test-timeout", type=int, default=15)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    def timeout(signum, frame):
        raise TimeoutError(f"Test exceeded {seconds}s: {item.nodeid}")

    seconds = item.config.getoption("--per-test-timeout")
    previous = signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, choices=range(5, 21), default=15)
    parser.add_argument("--file-timeout", type=int, default=120)
    parser.add_argument("--log-dir", type=Path, default=Path(".test-results/bounded"))
    args = parser.parse_args()
    if args.workers < 1 or any(not path.is_file() for path in args.paths):
        parser.error("Use positive workers and explicit existing test files")
    root = Path.cwd().resolve()
    if not args.log_dir.resolve().is_relative_to(root):
        parser.error("Log directory must be inside the project")
    args.log_dir = args.log_dir / f"run-{time.time_ns()}"
    args.log_dir.mkdir(parents=True, exist_ok=True)

    def run(entry):
        index, path = entry
        name = f"{index:03d}-{path.stem}"
        xml = args.log_dir / f"{name}.xml"
        started = time.monotonic()
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    str(path),
                    "-q",
                    "-p",
                    "scripts.run_bounded_tests",
                    f"--per-test-timeout={args.timeout}",
                    f"--junitxml={xml}",
                ],
                capture_output=True,
                text=True,
                timeout=args.file_timeout,
            )
            output, code = result.stdout + result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or b"").decode(errors="replace") + "\nFile exceeded its timeout"
            code = 124
        (args.log_dir / f"{name}.log").write_text(output)
        failures = []
        if xml.exists():
            for case in ET.parse(xml).iter("testcase"):
                for error in (*case.findall("failure"), *case.findall("error")):
                    failures.append(
                        {
                            "name": f"{case.get('classname')}::{case.get('name')}",
                            "error": error.text or error.get("message"),
                        }
                    )
        if code and not failures:
            failures.append({"name": str(path), "error": output})
        elapsed = round(time.monotonic() - started, 2)
        print(f"{'FAIL' if code else 'PASS'} {path} ({elapsed}s)", flush=True)
        return {"path": str(path), "exit_code": code, "seconds": elapsed, "failures": failures}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, enumerate(args.paths)))
    (args.log_dir / "summary.json").write_text(json.dumps(results, indent=2))
    failures = [failure for result in results for failure in result["failures"]]
    (args.log_dir / "failures.json").write_text(json.dumps(failures, indent=2))
    print(f"{len(results)} files; {len(failures)} failures. Logs: {args.log_dir}")
    return int(any(result["exit_code"] for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
