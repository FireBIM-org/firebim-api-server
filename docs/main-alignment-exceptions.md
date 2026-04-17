# Main Alignment Exceptions

This branch is being rebuilt on top of `origin/main` with the goal of keeping the
current `main` layout and infrastructure files intact while carrying only the
Rules, Graphs, and Checks feature surface.

## Main-Owned Files Kept As-Is

These paths should stay identical to `main` unless a later, concrete integration
problem proves otherwise:

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

## Main Files That Need Narrow Changes

### `app/main.py`

Why changed:
- `main` only registers the legacy auth and graph routers.
- The carried Rules, Graphs, and Checks modules remain unreachable unless their
  routers are mounted.

Scope of change:
- Add `checks`, `graphs`, and `rules` routers only.

### `app/core/config.py`

Why changed:
- The carried Fuseki-backed Rules/Graphs/Checks code expects:
  - `FUSEKI_APP_USER`
  - `FUSEKI_APP_PASSWORD`
  - `CHECKS_SOLVER`
- `main` currently exposes only `FUSEKI_HOST`.

Scope of change:
- Add the missing settings with compatibility aliases for older variable names.
- Do not alter Authentik or other `main` configuration behavior.

### `scripts/generate_openai.py`

Why changed:
- With the expanded app surface, importing `app.main` requires more settings than
  `main`'s schema generation script currently provides.
- The script now needs a minimal local bootstrap environment to generate
  `openapi.json` reliably without real secrets.

Scope of change:
- Provide import-only defaults.
- Keep output behavior the same: generate `openapi.json` at repo root.

### `pyproject.toml`

Why change is likely needed:
- The carried integration tests import `requests` at module import time.
- `main`'s dev dependency group does not currently include `requests`.
- `main` still targets Ruff `py313` even though the documented/runtime floor is `3.12`.
- `main` still pins `python-multipart>=0.0.22`, which previously failed the
  Trivy security scan on this feature work.

Expected narrow changes:
- add `requests` to dev dependencies
- align Ruff target to `py312`
- bump `python-multipart` to a patched version

### `uv.lock`

Why change is needed:
- Any justified `pyproject.toml` changes above must be reflected in the lockfile.

## Files Added Beyond Main

These are intentionally added because they directly support Rules, Graphs, and
Checks development or validation.

### `scripts/seed_local_stack.py`

Purpose:
- Seed MinIO and Fuseki with the local graph fixtures needed by the feature APIs
  and integration tests.


Purpose:
- Clean generated rule and rule-element data after integration/sanity runs.

### `tests/run_all_test.py`
### `tests/run_all_complex_combo_integration_tests.py`

Purpose:
- Provide the two validated sanity runners for this feature surface.

### Rules/Graphs/Checks Test Modules

Purpose:
- Exercise the carried feature APIs and runtime behavior.

### `openapi.json`

Purpose:
- Regenerated artifact reflecting the merged `main` shell plus the carried
  Rules/Graphs/Checks routes.

## Intentionally Not Carried

### RVT-Inspired Fixture Files

Not currently carried:
- `tests/fixtures/building_small_rvt_inspired.ttl`
- `tests/fixtures/building_medium_rvt_inspired.ttl`
- `tests/fixtures/building_large_rvt_inspired.ttl`

Why:
- The current sanity runners, seed script, and active integration modules only
  reference the IFC-inspired fixture set.

## Suspected Bloat / Follow-Up Cleanup Candidates

These should be reviewed before final merge if we want to trim dead weight:

- `app/rules/results.py`
  Appears to be unused.
- `app/rules/validators.py`
  May contain dead or underused validation helpers.
- `tests/test_rule_execution.py`
  Still intentionally skipped and not part of the main sanity flow.
- local tooling directories such as `.ruff-check-venv/` and `.uv-lock-venv/`
  Should remain untracked and out of the PR.
