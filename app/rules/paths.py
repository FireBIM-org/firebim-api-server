"""Property paths and `$ref` values for rule elements.

A property path is one IRI, or a *sequence* of IRIs joined with "/":

    https://firebim.org/ontologies/fbo#from/https://firebim.org/ontologies/fbo/dk#UseCategory
    fbo:from/fbo-dk:UseCategory

Because IRIs themselves contain "/", the sequence separator is only a "/" that is immediately followed by
the start of a new IRI (``http://``, ``https://``, ``urn:``) or a CURIE (``prefix:local``).

A segment may be prefixed with ``^`` to follow the property *backwards* (SHACL ``sh:inversePath``):

    ^fbo:FireSafetyUnit/fbo-dk:PeopleKnowledgeEscaperoutes   # from a compartment up to whatever lists it, then down

A `$ref` value is ``{"$ref": "<path>"}`` and means "compare with this path evaluated on the same target".
"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_PREFIXES: dict[str, str] = {
    "fbo": "https://firebim.org/ontologies/fbo#",
    "fbo-dk": "https://firebim.org/ontologies/fbo/dk#",
    "fbo-be": "https://firebim.org/ontologies/fbo/be#",
    "fbo-lt": "https://firebim.org/ontologies/fbo/lt#",
    "fbo-nl": "https://firebim.org/ontologies/fbo/nl#",
    "fbo-pt": "https://firebim.org/ontologies/fbo/pt#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

_SEGMENT_SPLIT = re.compile(r"/(?=\^?(?:https?://|urn:|[A-Za-z][A-Za-z0-9_.\-]*:(?!//)))")
INVERSE = "^"
_CURIE = re.compile(r"^([A-Za-z][A-Za-z0-9_.\-]*):([^/:#][^/]*)$")
REF_KEY = "$ref"


def split_property_path(path: str | None) -> list[str]:
    """Split a (possibly sequence) property path into IRI/CURIE segments."""
    if path is None:
        return []
    text = str(path).strip()
    if not text:
        return []
    # allow an explicit spaced form "a / b" as well
    text = re.sub(r"\s*/\s*(?=\^?(?:https?://|urn:|[A-Za-z][A-Za-z0-9_.\-]*:(?!//)))", "/", text)
    return [seg for seg in _SEGMENT_SPLIT.split(text) if seg]


def join_property_path(segments: list[str]) -> str:
    return "/".join(segments)


def is_sequence_path(path: str | None) -> bool:
    return len(split_property_path(path)) > 1


def is_inverse_segment(segment: str) -> bool:
    return str(segment).startswith(INVERSE)


def segment_iri(segment: str) -> str:
    """The IRI of a (possibly inverse) expanded segment."""
    return segment[1:] if is_inverse_segment(segment) else segment


def expand_curie(term: str, prefixes: dict[str, str] | None = None) -> str:
    """fbo-dk:UseCategory -> full IRI; full IRIs are returned unchanged. A leading ``^`` (inverse) is kept."""
    text = str(term).strip()
    if text.startswith(INVERSE):
        return INVERSE + expand_curie(text[1:], prefixes)
    if text.startswith(("http://", "https://", "urn:")):
        return text
    match = _CURIE.match(text)
    if not match:
        return text
    prefix, local = match.groups()
    table = {**DEFAULT_PREFIXES, **(prefixes or {})}
    base = table.get(prefix)
    if not base:
        return text
    return base + local


def expand_property_path(path: str | None, prefixes: dict[str, str] | None = None) -> list[str]:
    return [expand_curie(seg, prefixes) for seg in split_property_path(path)]


def is_ref_value(value: Any) -> bool:
    return isinstance(value, dict) and REF_KEY in value and len(value) == 1


def ref_path(value: Any) -> str | None:
    if not is_ref_value(value):
        return None
    path = value.get(REF_KEY)
    return str(path).strip() if path is not None else None


def validate_path_syntax(path: str | None, *, field: str = "property_path") -> None:
    segments = split_property_path(path)
    if not segments:
        raise ValueError(f"{field} is empty")
    for seg in segments:
        if seg == INVERSE:
            raise ValueError(f"{field} has an inverse marker without a property: {path!r}")
        if not seg or seg.startswith("/") or seg.endswith("/") and not seg.startswith(("http", "urn:")):
            raise ValueError(f"{field} has an empty segment: {path!r}")
        if any(ch.isspace() for ch in seg):
            raise ValueError(f"{field} segment contains whitespace: {seg!r}")
