"""calculator tool implementation."""
from __future__ import annotations

import ast
import math
import operator
from typing import Any

from app.tools.registry import register_tool

_SAFE_OPERATORS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_FUNCTIONS: dict[str, Any] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "sqrt": math.sqrt,
    "log": math.log,
    "exp": math.exp,
    "ceil": math.ceil,
    "floor": math.floor,
    "pow": math.pow,
    "pi": math.pi,
    "e": math.e,
}


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value)}")

    if isinstance(node, ast.Name):
        if node.id in _SAFE_FUNCTIONS and isinstance(_SAFE_FUNCTIONS[node.id], (int, float)):
            return _SAFE_FUNCTIONS[node.id]
        raise ValueError(f"Unknown variable or constant: '{node.id}'")

    if isinstance(node, ast.UnaryOp):
        op = _SAFE_OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op)}")
        return op(_eval_node(node.operand))

    if isinstance(node, ast.BinOp):
        op = _SAFE_OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported binary operator: {type(node.op)}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return op(left, right)

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are allowed.")
        func_name = node.func.id
        if func_name not in _SAFE_FUNCTIONS:
            raise ValueError(f"Unsupported or unsafe function: '{func_name}'")
        func = _SAFE_FUNCTIONS[func_name]
        args = [_eval_node(arg) for arg in node.args]
        return func(*args)

    raise ValueError(f"Unsupported AST node type: {type(node).__name__}")


@register_tool(
    name="calculator",
    description="Safely evaluate a mathematical expression.",
    source="local",
    cost_credits=0,
    timeout_s=5.0,
    tags=["utility", "math"],
)
async def calculator(expression: str) -> float:
    """Evaluate a mathematical expression string safely using AST parsing."""
    if not expression or not expression.strip():
        raise ValueError("Expression cannot be empty.")
    try:
        parsed = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as err:
        raise ValueError(f"Syntax error in expression '{expression}': {err}") from err

    result = _eval_node(parsed)
    return float(result)
