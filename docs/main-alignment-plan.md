# Main Alignment Plan

This branch is being rebuilt from `origin/main` and should keep `main` as the source of truth for infrastructure, CI, and deployment-oriented files.

## Files and folders to keep exactly as `main`

- `.github/`
- `blueprints/`
- `docker/`
- `k8s/manifests/`
- `.dockerignore`
- `.env.example`
- `.gitattributes`
- `.gitignore`
- `.python-version`
- `Makefile`
- `README.md`
- `docker-compose.yml`
- `pyproject.toml`
- `uv.lock`

## Feature payload to carry from `feature/rules-core-refresh`

These files implement the rules, graphs, and checks functionality and should be transplanted onto `main`:

- `app/api/checks.py`
- `app/api/graphs.py`
- `app/api/rules.py`
- `app/checks/**`
- `app/graphs_and_checks/**`
- `app/rules/**`
- `app/ontology/**`
- `app/schemas/checks.py`
- `app/schemas/graphs.py`
- `app/schemas/rules.py`
- `app/storage/building_repository.py`
- `app/storage/fuseki_client.py`

## Keep as `main` for now

- `app/api/graph.py`

This file is intentionally kept as `main` unless a later rules/graphs/checks integration requires a focused change there.

## Main files that may need minimal, explicit follow-up changes

These files should start from `main`, and only receive narrow changes if required by the transplanted feature set:

- `app/main.py`
- `app/core/config.py`
- `app/models/models.py`
- `scripts/generate_openai.py`
- `openapi.json`
- `scripts/`
- `tests/`

If any of these need changes, the reason should be tied directly to rules, graphs, or checks functionality.

## Candidate additions that likely still make sense

- `scripts/seed_local_stack.py`
- `tests/run_all_test.py`
- `tests/run_all_complex_combo_integration_tests.py`
- rules/graphs/checks integration tests and required cleanup helpers
- fixture files required by the active integration flows

## Potential bloat to review before final merge

- `tests/fixtures/building_*_rvt_inspired.ttl`
  These do not appear to be referenced by the current active integration tests.
- `docker/worker/requirements.txt`
  Likely unnecessary if `main`'s worker build remains uv-based.
- `app/rules/results.py`
  Appears unused.
- `app/rules/validators.py`
  May contain dead or underused helpers.
- `tests/test_rule_execution.py`
  Still mostly parked/skipped and may not belong in the first-pass carry set.

## Working rule for this alignment

When in doubt:

1. keep `main`
2. add only what is necessary for rules, graphs, and checks
3. document every exception rather than silently drifting infrastructure files away from `main`
