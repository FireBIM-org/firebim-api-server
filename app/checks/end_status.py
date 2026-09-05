"""Shared mapping from an end element's `end_kind` to a check status.

Used by CheckRuntime (and the horizontal/vertical runtimes that inherit from it) and by the blind SHACL runner so
that all algorithms agree on what an end element means.

  pass / passed / true / success                     -> True
  fail / failed / false / error                      -> False
  unknown / missing_data / missing-data              -> Unknown
  not_applicable / not-applicable / n/a / na         -> True   (the rule does not apply: nothing is violated)
  terminal / output / result / classification        -> True   (a defined outcome was reached, e.g. "unit is AK2";
       the outcome itself lives in config.result / output_text)
Anything else -> None (caller falls back to text heuristics / incoming status).
"""

from __future__ import annotations

_TRUE = {"pass", "passed", "true", "success", "ok"}
_FALSE = {"fail", "failed", "false", "error", "not_ok", "not-ok"}
_UNKNOWN = {"unknown", "missing_data", "missing-data", "missing"}
_VACUOUS = {"not_applicable", "not-applicable", "n/a", "na", "terminal", "output", "result", "classification"}


def status_from_end_kind(end_kind: str | None) -> str | None:
    kind = str(end_kind or "").strip().lower()
    if not kind:
        return None
    if kind in _TRUE or kind in _VACUOUS:
        return "True"
    if kind in _FALSE:
        return "False"
    if kind in _UNKNOWN:
        return "Unknown"
    return None
