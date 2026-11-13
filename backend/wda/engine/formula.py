"""Formeln für abgeleitete Spalten, z. B. ``gesamt - mit_lizenz`` oder ``round(a / b, 2)``.

Ausgewertet wird über einen ``ast``-Baum mit fester Positivliste; ``eval`` kommt nicht vor.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping
from dataclasses import dataclass

MAX_LENGTH = 500
MAX_NODES = 200
_FUNCTIONS = {"round": (1, 2), "max": (1, 8), "min": (1, 8)}
Number = int | float


class QueryError(Exception):
    """Die Abfrage-Beschreibung ist fehlerhaft; die Meldung ist für die Oberfläche bestimmt."""


@dataclass(frozen=True)
class Formula:
    text: str
    names: frozenset[str]
    _tree: ast.expr

    def evaluate(self, values: Mapping[str, Number | None]) -> Number | None:
        result = _eval(self._tree, values)
        if result is None or isinstance(result, bool):
            return None
        if isinstance(result, float):
            if not math.isfinite(result):
                return None
            if result.is_integer() and abs(result) < 2**53:
                return int(result)
        return result


def compile_formula(text: str) -> Formula:
    if not isinstance(text, str) or not text.strip():
        raise QueryError("Die Formel ist leer.")
    if len(text) > MAX_LENGTH:
        raise QueryError(f"Die Formel ist länger als {MAX_LENGTH} Zeichen.")
    try:
        tree = ast.parse(text.strip(), mode="eval").body
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        raise QueryError(f"Die Formel '{text}' ist nicht lesbar.") from None
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise QueryError("Die Formel ist zu umfangreich.")
    names: set[str] = set()
    _check(tree, names, text)
    return Formula(text, frozenset(names), tree)


def _check(node: ast.AST, names: set[str], text: str) -> None:
    bad = QueryError(
        f"Die Formel '{text}' enthält etwas Unerlaubtes. Erlaubt sind Spaltennamen, Zahlen, "
        "+ - * /, Klammern sowie round, max und min.")
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, ast.Add | ast.Sub | ast.Mult | ast.Div):
            raise bad
        _check(node.left, names, text)
        _check(node.right, names, text)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, ast.USub | ast.UAdd):
            raise bad
        _check(node.operand, names, text)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise bad
    elif isinstance(node, ast.Name):
        if node.id in _FUNCTIONS:
            raise bad
        names.add(node.id)
    elif isinstance(node, ast.Call):
        if (not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS
                or node.keywords):
            raise bad
        low, high = _FUNCTIONS[node.func.id]
        if not low <= len(node.args) <= high:
            raise QueryError(
                f"Die Funktion {node.func.id} in '{text}' hat die falsche Zahl an Argumenten.")
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                raise bad
            _check(arg, names, text)
    else:
        raise bad


def _eval(node: ast.expr, values: Mapping[str, Number | None]) -> Number | None:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return values.get(node.id)
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, values)
        if operand is None:
            return None
        return -operand if isinstance(node.op, ast.USub) else operand
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, values), _eval(node.right, values)
        if left is None or right is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                return None
            return left / right
        except OverflowError:
            return None
    assert isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    args = [_eval(arg, values) for arg in node.args]
    if any(arg is None for arg in args):
        return None
    if node.func.id == "round":
        digits = args[1] if len(args) == 2 else 0
        if not isinstance(digits, int) or abs(digits) > 12:
            return None
        return _round_half_up(args[0], digits)
    return max(args) if node.func.id == "max" else min(args)


def _round_half_up(value: Number, digits: int) -> Number:
    """Kaufmännisch runden (0,5 immer weg von null), nicht „Banker's Rounding"."""
    factor = 10.0 ** digits
    scaled = abs(value) * factor
    if not math.isfinite(scaled):
        return value
    rounded = math.floor(scaled + 0.5) / factor
    result = math.copysign(rounded, value)
    return int(result) if digits <= 0 else result
