"""Deterministic wording for evidence-card claims."""

from __future__ import annotations

import ast


_COMPARISONS: dict[type[ast.cmpop], str] = {
    ast.Eq: "equals",
    ast.NotEq: "does not equal",
    ast.Lt: "is less than",
    ast.LtE: "is less than or equal to",
    ast.Gt: "is greater than",
    ast.GtE: "is greater than or equal to",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.In: "is in",
    ast.NotIn: "is not in",
}

_NEGATED_COMPARISONS: dict[type[ast.cmpop], str] = {
    ast.Eq: "does not equal",
    ast.NotEq: "equals",
    ast.Lt: "is greater than or equal to",
    ast.LtE: "is greater than",
    ast.Gt: "is less than or equal to",
    ast.GtE: "is less than",
    ast.Is: "is not",
    ast.IsNot: "is",
    ast.In: "is not in",
    ast.NotIn: "is in",
}


def source_expression(node: ast.AST | None) -> str:
    """Return stable source-shaped text for an expression in the evidence."""
    return "None" if node is None else ast.unparse(node)


def condition_is_compound(node: ast.AST) -> bool:
    """Return whether a condition description has a top-level boolean join."""
    while isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        node = node.operand
    return isinstance(node, ast.BoolOp)


def describe_condition(node: ast.AST, expected: bool = True) -> str:
    """Describe the truth value required from one supported condition."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return describe_condition(node.operand, not expected)

    if isinstance(node, ast.BoolOp):
        # De Morgan's law keeps false branch descriptions exact without a vague
        # "condition failed" label.
        if isinstance(node.op, ast.And):
            joiner = " and " if expected else " or "
        else:
            joiner = " or " if expected else " and "
        parts = [describe_condition(value, expected) for value in node.values]
        rendered = (
            f"({part})" if condition_is_compound(value) else part
            for value, part in zip(node.values, parts)
        )
        return joiner.join(rendered)

    if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
        operators = _COMPARISONS if expected else _NEGATED_COMPARISONS
        operator = operators[type(node.ops[0])]
        return f"{source_expression(node.left)} {operator} {source_expression(node.comparators[0])}"

    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        holds = node.value is expected
        return "the condition always holds" if holds else "the condition never holds"

    expression = source_expression(node)
    return f"{expression} is {'truthy' if expected else 'falsy'}"
