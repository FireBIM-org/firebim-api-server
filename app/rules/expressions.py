"""A deliberately small expression language for `derive` rule elements.

    expr    := term (('+'|'-') term)*
    term    := unary (('*'|'/') unary)*
    unary   := '-' unary | primary
    primary := NUMBER | STRING | true | false | null | $path | NAME '(' args ')' | '(' expr ')' | '[' args ']'

`$path` references a property path (see app.rules.paths) evaluated on the target being processed.
No comparisons, no conditionals, no loops: branching is the job of decision elements.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

MAX_EXPRESSION_LENGTH = 2000
MAX_DEPTH = 64

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<ref>\$(?:<[^>\s]+>|[^\s,()\[\]+*]+(?:/[^\s,()\[\]+*]+)*))
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>[-+*/(),\[\]])
    """,
    re.VERBOSE,
)


class ExpressionError(ValueError):
    """Static error: the expression does not parse or uses an unknown function."""


class ExpressionUnknown(Exception):
    """Runtime: an input is missing/ambiguous or the arithmetic is undefined -> outcome Unknown."""

    def __init__(self, reason_code: str, message: str, **evidence: Any) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message
        self.evidence = evidence


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    pos: int


@dataclass(frozen=True)
class Node:
    kind: str  # num | str | bool | null | ref | list | call | neg | bin
    value: Any = None
    children: tuple[Node, ...] = ()


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise ExpressionError(f"Unexpected character {text[pos]!r} at position {pos}")
        kind = match.lastgroup or ""
        if kind != "ws":
            tokens.append(Token(kind, match.group(kind), pos))
        pos = match.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.i = 0
        self.depth = 0

    def peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, kind: str | None = None, text: str | None = None) -> Token:
        tok = self.peek()
        if tok is None:
            raise ExpressionError("Unexpected end of expression")
        if kind and tok.kind != kind or text and tok.text != text:
            raise ExpressionError(f"Unexpected token {tok.text!r} at position {tok.pos}")
        self.i += 1
        return tok

    def parse(self) -> Node:
        node = self.expr()
        if self.peek() is not None:
            tok = self.peek()
            raise ExpressionError(f"Unexpected token {tok.text!r} at position {tok.pos}")  # type: ignore[union-attr]
        return node

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExpressionError("Expression is nested too deeply")

    def _leave(self) -> None:
        self.depth -= 1

    def expr(self) -> Node:
        self._enter()
        node = self.term()
        while (tok := self.peek()) and tok.kind == "op" and tok.text in "+-":
            self.take()
            node = Node("bin", tok.text, (node, self.term()))
        self._leave()
        return node

    def term(self) -> Node:
        node = self.unary()
        while (tok := self.peek()) and tok.kind == "op" and tok.text in "*/":
            self.take()
            node = Node("bin", tok.text, (node, self.unary()))
        return node

    def unary(self) -> Node:
        tok = self.peek()
        if tok and tok.kind == "op" and tok.text == "-":
            self.take()
            return Node("neg", None, (self.unary(),))
        return self.primary()

    def args(self, closer: str) -> tuple[Node, ...]:
        items: list[Node] = []
        if (tok := self.peek()) and tok.kind == "op" and tok.text == closer:
            self.take()
            return ()
        while True:
            items.append(self.expr())
            tok = self.take("op")
            if tok.text == closer:
                return tuple(items)
            if tok.text != ",":
                raise ExpressionError(f"Expected ',' or '{closer}' at position {tok.pos}")

    def primary(self) -> Node:
        tok = self.take()
        if tok.kind == "number":
            text = tok.text
            return Node("num", float(text) if any(c in text for c in ".eE") else int(text))
        if tok.kind == "string":
            return Node("str", bytes(tok.text[1:-1], "utf-8").decode("unicode_escape"))
        if tok.kind == "ref":
            path = tok.text[1:]
            if path.startswith("<") and path.endswith(">"):
                path = path[1:-1]
            return Node("ref", path)
        if tok.kind == "name":
            lowered = tok.text.lower()
            if lowered in ("true", "false"):
                return Node("bool", lowered == "true")
            if lowered == "null":
                return Node("null")
            if lowered == "pi":
                return Node("num", math.pi)
            nxt = self.peek()
            if not (nxt and nxt.kind == "op" and nxt.text == "("):
                raise ExpressionError(
                    f"Unknown identifier {tok.text!r} at position {tok.pos} (did you mean ${tok.text}?)"
                )
            self.take()
            if lowered not in FUNCTIONS:
                raise ExpressionError(f"Unknown function {tok.text!r}")
            arguments = self.args(")")
            arity = FUNCTIONS[lowered][0]
            if arity is not None and len(arguments) != arity:
                raise ExpressionError(f"Function {lowered}() takes {arity} argument(s), got {len(arguments)}")
            if arity is None and lowered != "lookup" and len(arguments) == 0:
                raise ExpressionError(f"Function {lowered}() needs at least one argument")
            return Node("call", lowered, arguments)
        if tok.kind == "op" and tok.text == "(":
            node = self.expr()
            self.take("op", ")")
            return node
        if tok.kind == "op" and tok.text == "[":
            return Node("list", None, self.args("]"))
        raise ExpressionError(f"Unexpected token {tok.text!r} at position {tok.pos}")


def _num(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ExpressionUnknown("expression_error", f"{what} is not a number: {value!r}", operand=repr(value))
    return value


def _lookup(args: list[Any]) -> Any:
    if len(args) != 2 or not isinstance(args[1], list):
        raise ExpressionError("lookup(x, [[lo, hi, value], ...]) takes a value and a table")
    x = _num(args[0], "lookup key")
    for row in args[1]:
        if not isinstance(row, list) or len(row) != 3:
            raise ExpressionUnknown("expression_error", "lookup table rows must be [lo, hi, value]")
        lo, hi, value = row
        if (lo is None or _num(lo, "lookup lower bound") <= x) and (hi is None or x < _num(hi, "lookup upper bound")):
            return value
    raise ExpressionUnknown("lookup_miss", f"no lookup row matches {x}", key=x)


FUNCTIONS: dict[str, tuple[int | None, Callable[[list[Any]], Any]]] = {
    "min": (None, lambda a: min(_num(v, "min argument") for v in a)),
    "max": (None, lambda a: max(_num(v, "max argument") for v in a)),
    "abs": (1, lambda a: abs(_num(a[0], "abs argument"))),
    "sqrt": (1, lambda a: math.sqrt(_num(a[0], "sqrt argument"))),
    "floor": (1, lambda a: math.floor(_num(a[0], "floor argument"))),
    "ceil": (1, lambda a: math.ceil(_num(a[0], "ceil argument"))),
    "round": (None, lambda a: round(_num(a[0], "round argument"), int(_num(a[1], "digits")) if len(a) > 1 else 0)),
    "pow": (2, lambda a: math.pow(_num(a[0], "pow base"), _num(a[1], "pow exponent"))),
    "sin": (1, lambda a: math.sin(_num(a[0], "sin argument"))),
    "cos": (1, lambda a: math.cos(_num(a[0], "cos argument"))),
    "tan": (1, lambda a: math.tan(_num(a[0], "tan argument"))),
    "sind": (1, lambda a: math.sin(math.radians(_num(a[0], "sind argument")))),
    "cosd": (1, lambda a: math.cos(math.radians(_num(a[0], "cosd argument")))),
    "tand": (1, lambda a: math.tan(math.radians(_num(a[0], "tand argument")))),
    "lookup": (None, _lookup),
}


def parse_expression(text: str) -> Node:
    if text is None:
        raise ExpressionError("Expression is missing")
    source = str(text).strip()
    if not source:
        raise ExpressionError("Expression is empty")
    if len(source) > MAX_EXPRESSION_LENGTH:
        raise ExpressionError(f"Expression is longer than {MAX_EXPRESSION_LENGTH} characters")
    return _Parser(tokenize(source)).parse()


def referenced_paths(node: Node) -> list[str]:
    found: list[str] = []

    def walk(n: Node) -> None:
        if n.kind == "ref" and n.value not in found:
            found.append(n.value)
        for child in n.children:
            walk(child)

    walk(node)
    return found


def validate_expression(text: str) -> list[str]:
    """Parse and return the referenced paths; raises ExpressionError on static problems."""
    return referenced_paths(parse_expression(text))


def evaluate(node: Node, resolve: Callable[[str], Any]) -> Any:
    """Evaluate an AST. `resolve(path)` must return exactly one python value or raise ExpressionUnknown."""
    kind = node.kind
    if kind in ("num", "str", "bool"):
        return node.value
    if kind == "null":
        return None
    if kind == "ref":
        return resolve(node.value)
    if kind == "list":
        return [evaluate(c, resolve) for c in node.children]
    if kind == "neg":
        return -_num(evaluate(node.children[0], resolve), "negation operand")
    if kind == "call":
        args = [evaluate(c, resolve) for c in node.children]
        try:
            return FUNCTIONS[node.value][1](args)
        except (ValueError, OverflowError, ZeroDivisionError) as exc:
            raise ExpressionUnknown(
                "expression_error", f"{node.value}() failed: {exc}", arguments=[repr(a) for a in args]
            ) from exc
    if kind == "bin":
        left = evaluate(node.children[0], resolve)
        right = evaluate(node.children[1], resolve)
        op = node.value
        if op == "+" and isinstance(left, str) and isinstance(right, str):
            return left + right
        lhs, rhs = _num(left, "left operand"), _num(right, "right operand")
        if op == "+":
            return lhs + rhs
        if op == "-":
            return lhs - rhs
        if op == "*":
            return lhs * rhs
        if rhs == 0:
            raise ExpressionUnknown("expression_error", "division by zero", left=lhs)
        return lhs / rhs
    raise ExpressionError(f"Unknown node kind {kind}")


def describe(node: Node) -> str:
    """Render an AST back to source (used for evidence)."""
    kind = node.kind
    if kind == "num":
        return repr(node.value) if isinstance(node.value, float) else str(node.value)
    if kind == "str":
        return '"' + str(node.value).replace('"', '\\"') + '"'
    if kind == "bool":
        return "true" if node.value else "false"
    if kind == "null":
        return "null"
    if kind == "ref":
        return "$" + str(node.value)
    if kind == "list":
        return "[" + ", ".join(describe(c) for c in node.children) + "]"
    if kind == "neg":
        return "-" + describe(node.children[0])
    if kind == "call":
        return f"{node.value}(" + ", ".join(describe(c) for c in node.children) + ")"
    return "(" + describe(node.children[0]) + f" {node.value} " + describe(node.children[1]) + ")"
