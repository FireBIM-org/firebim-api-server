from __future__ import annotations

import re
from typing import Any

from rdflib import URIRef


def _coerce_token(token: str) -> Any:
    token = token.strip()
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    if re.fullmatch(r"-?\d+\.\d+", token):
        return float(token)
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    if token.startswith("<") and token.endswith(">"):
        return URIRef(token[1:-1])
    return token


def parse_construct_rule(sparql_text: str) -> tuple[str | None, str | None, Any]:
    prop_match = re.search(
        r"OPTIONAL\s*\{\s*\?this\s*<([^>]+)>\s*\?h\s*\}",
        sparql_text,
        re.DOTALL,
    )
    in_match = re.search(
        r"IF\s*\(\s*\?h\s+IN\s*\(\s*(.+?)\s*\)\s*,\s*fb:True",
        sparql_text,
        re.DOTALL,
    )
    between_match = re.search(
        r"IF\s*\(\s*\(\s*\?h\s*>=\s*(.+?)\s*&&\s*\?h\s*<\=?\s*(.+?)\s*\)\s*,\s*fb:True",
        sparql_text,
        re.DOTALL,
    )
    cmp_match = re.search(
        r"IF\s*\(\s*\?h\s*(<=|>=|=|<|>)\s*(.+?)\s*,\s*fb:True",
        sparql_text,
        re.DOTALL,
    )

    property_path = prop_match.group(1) if prop_match else None
    operator = None
    value: Any = None

    if in_match:
        operator = "=="
        raw_list = in_match.group(1).strip()
        tokens = re.findall(r'"[^"]*"|<[^>]+>|-?\d+\.\d+|-?\d+|[^,\s]+', raw_list)
        value = [_coerce_token(t) for t in tokens]
    elif between_match:
        operator = "between"
        low = _coerce_token(between_match.group(1).strip().rstrip(") "))
        high = _coerce_token(between_match.group(2).strip().rstrip(") "))
        value = [low, high]
    elif cmp_match:
        operator = cmp_match.group(1)
        raw_value = cmp_match.group(2).strip().rstrip(") ").strip()
        value = _coerce_token(raw_value)
        if operator == "=":
            operator = "=="

    return property_path, operator, value
