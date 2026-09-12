"""Safe arithmetic evaluation for the calculator tool.

Expressions are parsed with :mod:`ast` and only numeric operators, a small
function table, and a couple of constants are evaluated — never ``eval`` — so
untrusted input can never reach Python internals.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

_BIN_OPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_FUNCTIONS: dict[str, Any] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
}

_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}

_MAX_EXPONENT = 1000


class CalculatorError(ValueError):
    """Raised when an expression is malformed or uses unsupported syntax."""


def calculate(expression: str) -> str:
    """Evaluate ``expression`` and return the formatted result."""
    expr = (expression or "").strip()
    if not expr:
        raise CalculatorError("empty expression")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"invalid expression: {exc.msg}") from None
    return _format(_eval(tree))


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError(f"unsupported literal: {node.value!r}")
        return node.value
    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise CalculatorError(f"unknown name: {node.id}")
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise CalculatorError(f"unsupported operator: {type(node.op).__name__}")
        return op(_eval(node.operand))
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise CalculatorError(f"unsupported operator: {type(node.op).__name__}")
        left = _eval(node.left)
        right = _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise CalculatorError("exponent too large")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise CalculatorError("division by zero")
        return op(left, right)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise CalculatorError("unsupported function call")
        if node.keywords:
            raise CalculatorError("keyword arguments are not supported")
        args = [_eval(arg) for arg in node.args]
        try:
            return _FUNCTIONS[node.func.id](*args)
        except (ValueError, TypeError) as exc:
            raise CalculatorError(str(exc)) from None
    raise CalculatorError(f"unsupported expression: {type(node).__name__}")


def _format(value: float) -> str:
    if isinstance(value, float):
        if math.isinf(value) or math.isnan(value):
            return str(value)
        if value.is_integer():
            return str(int(value))
        return f"{value:.12g}"
    return str(value)
