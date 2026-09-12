"""Conservative entry-guard and explicit-failure analysis for Slice 2."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from .boundaries import call_category
from .inspect import Span, _diagnostic, _span
from .presentation import describe_condition, source_expression


BUILTIN_EXCEPTIONS = {
    "ArithmeticError", "AssertionError", "AttributeError", "EOFError", "Exception",
    "FileExistsError", "FileNotFoundError", "ImportError", "IndexError", "KeyError",
    "LookupError", "MemoryError", "NameError", "OSError", "OverflowError", "RuntimeError",
    "StopIteration", "SyntaxError", "SystemError", "TypeError", "UnboundLocalError",
    "UnicodeError", "ValueError", "ZeroDivisionError",
}


@dataclass
class ExpressionResult:
    """Hold a structural expression plus its assumptions and analysis boundaries."""

    expression: dict[str, Any] | None
    assumptions: list[dict[str, str]] = field(default_factory=list)
    boundaries: list[dict[str, Any]] = field(default_factory=list)
    unsupported: bool = False


def _boundary(
    path: str,
    node: ast.AST,
    kind: str,
    target: str,
    reason: str,
    category: str = "important",
) -> dict[str, Any]:
    """Build a boundary for dynamic behavior encountered in a guard expression."""
    return {
        "id": f"guard-boundary-{node.lineno}-{node.col_offset}-{kind}",
        "kind": kind,
        "target": {"text": target},
        "reason": reason,
        "category": category,
        "source_span": _span(path, node).as_dict(),
    }


def _operator_name(node: ast.operator | ast.unaryop | ast.boolop | ast.cmpop) -> str:
    """Return the source-level name for a supported AST operator."""
    names = {
        ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//", ast.Mod: "%",
        ast.Pow: "**", ast.MatMult: "@", ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^",
        ast.LShift: "<<", ast.RShift: ">>", ast.And: "and", ast.Or: "or", ast.Not: "not",
        ast.UAdd: "+", ast.USub: "-", ast.Invert: "~", ast.Eq: "==", ast.NotEq: "!=",
        ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Is: "is", ast.IsNot: "is not",
        ast.In: "in", ast.NotIn: "not in",
    }
    return names[type(node)]


def _expression(path: str, node: ast.AST, parameters: set[str]) -> ExpressionResult:
    """Serialize one guard expression without evaluating Python semantics."""
    if isinstance(node, ast.Name):
        if node.id not in parameters:
            return ExpressionResult(None, unsupported=True)
        return ExpressionResult({"kind": "name", "name": node.id})
    if isinstance(node, ast.Constant):
        value = node.value if isinstance(node.value, (str, int, float, bool)) or node.value is None else repr(node.value)
        return ExpressionResult({"kind": "constant", "value": value})
    if isinstance(node, ast.Attribute):
        base = _expression(path, node.value, parameters)
        if base.unsupported:
            return base
        boundary = _boundary(path, node, "dynamic_dispatch", ast.unparse(node), "Attribute lookup may invoke __getattribute__ or a descriptor.")
        base.boundaries.append(boundary)
        base.expression = {"kind": "attribute", "base": base.expression, "name": node.attr}
        return base
    if isinstance(node, ast.Subscript):
        value = _expression(path, node.value, parameters)
        index = _expression(path, node.slice, parameters)
        if value.unsupported or index.unsupported:
            return ExpressionResult(None, unsupported=True)
        result = ExpressionResult({"kind": "subscript", "value": value.expression, "index": index.expression}, value.assumptions + index.assumptions, value.boundaries + index.boundaries)
        result.boundaries.append(_boundary(path, node, "dynamic_dispatch", ast.unparse(node), "Subscript access may invoke __getitem__ or a custom mapping implementation."))
        return result
    if isinstance(node, ast.Call):
        arguments: list[dict[str, Any]] = []
        result = ExpressionResult({"kind": "call", "callee": ast.unparse(node.func), "arguments": arguments})
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            child = _expression(path, argument, parameters)
            if child.unsupported:
                return ExpressionResult(None, unsupported=True)
            arguments.append(child.expression)
            result.assumptions.extend(child.assumptions)
            result.boundaries.extend(child.boundaries)
        result.boundaries.append(_boundary(path, node, "unresolved_call", ast.unparse(node), "The call may return any value or raise an exception.", call_category(node)))
        return result
    if isinstance(node, ast.BoolOp):
        values: list[dict[str, Any]] = []
        result = ExpressionResult({"kind": "boolean", "operator": _operator_name(node.op), "values": values})
        for value in node.values:
            child = _expression(path, value, parameters)
            if child.unsupported:
                return ExpressionResult(None, unsupported=True)
            values.append(child.expression)
            result.assumptions.extend(child.assumptions)
            result.boundaries.extend(child.boundaries)
        return result
    if isinstance(node, ast.UnaryOp):
        operand = _expression(path, node.operand, parameters)
        if operand.unsupported:
            return operand
        if not isinstance(node.op, ast.Not):
            operand.assumptions.append({"text": f"Unary operator {_operator_name(node.op)} follows the modeled Python semantics."})
        operand.expression = {"kind": "unary", "operator": _operator_name(node.op), "operand": operand.expression}
        return operand
    if isinstance(node, ast.BinOp):
        left = _expression(path, node.left, parameters)
        right = _expression(path, node.right, parameters)
        if left.unsupported or right.unsupported:
            return ExpressionResult(None, unsupported=True)
        result = ExpressionResult({"kind": "binary", "operator": _operator_name(node.op), "left": left.expression, "right": right.expression}, left.assumptions + right.assumptions, left.boundaries + right.boundaries)
        result.assumptions.append({"text": f"Binary operator {_operator_name(node.op)} follows the modeled Python semantics."})
        return result
    if isinstance(node, ast.Compare):
        left = _expression(path, node.left, parameters)
        comparators = [_expression(path, item, parameters) for item in node.comparators]
        if left.unsupported or any(item.unsupported for item in comparators):
            return ExpressionResult(None, unsupported=True)
        result = ExpressionResult({"kind": "comparison", "operators": [_operator_name(item) for item in node.ops], "left": left.expression, "comparators": [item.expression for item in comparators]}, left.assumptions, left.boundaries)
        for item in comparators:
            result.assumptions.extend(item.assumptions)
            result.boundaries.extend(item.boundaries)
        result.assumptions.append({"text": "Comparison operators follow the modeled Python semantics; overloaded comparisons are not resolved."})
        return result
    return ExpressionResult(None, unsupported=True)


def _exception(path: str, node: ast.Raise) -> tuple[dict[str, Any] | None, str | None]:
    """Serialize a modeled builtin exception from a raise statement."""
    value = node.exc
    if isinstance(value, ast.Name) and value.id in BUILTIN_EXCEPTIONS:
        return {"kind": "builtin_exception", "name": value.id}, None
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in BUILTIN_EXCEPTIONS:
        return {"kind": "builtin_exception", "name": value.func.id}, None
    return None, "Only modeled builtin exception construction is supported in Slice 2."


def _claim(claim_id: str, kind: str, text: str, statement: dict[str, Any], spans: list[Span], assumptions: list[dict[str, str]], boundary_ids: list[str]) -> dict[str, Any]:
    """Create a derived claim with shared source, assumption, and boundary fields."""
    return {
        "id": claim_id,
        "kind": kind,
        "statement": {"text": text, **statement},
        "evidence": {"method": statement["type"], "evidence_class": "derived", "detail": {}},
        "source_spans": [item.as_dict() for item in spans],
        "assumptions": assumptions,
        "boundary_ids": boundary_ids,
    }


def analyze_guards(path: str, node: ast.FunctionDef) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract the contiguous entry guard prefix from a supported function."""
    parameters = {argument.arg for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
    if node.args.vararg:
        parameters.add(node.args.vararg.arg)
    if node.args.kwarg:
        parameters.add(node.args.kwarg.arg)
    claims: list[dict[str, Any]] = []
    boundaries: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for statement in node.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
            continue
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.Assert):
            condition = _expression(path, statement.test, parameters)
            if condition.unsupported:
                diagnostics.append(_diagnostic("unsupported_semantics", "The assertion condition contains a read or expression outside the Slice 2 model.", _span(path, statement.test), "guards"))
                continue
            boundaries.extend(condition.boundaries)
            boundary_ids = [item["id"] for item in condition.boundaries]
            assumptions = [{"text": "The assertion depends on __debug__ being true; Python may remove it under optimization."}, *condition.assumptions]
            description = describe_condition(statement.test)
            claims.append(_claim(f"assertion-{statement.lineno}", "rejected_input", f"Requires {description}.", {"type": "assertion", "condition": condition.expression, "source_text": source_expression(statement.test)}, [_span(path, statement.test), _span(path, statement)], assumptions, boundary_ids))
            continue
        if isinstance(statement, ast.If) and not statement.orelse and len(statement.body) == 1 and isinstance(statement.body[0], ast.Raise):
            raised = statement.body[0]
            exception, error = _exception(path, raised)
            condition = _expression(path, statement.test, parameters)
            if error:
                diagnostics.append(_diagnostic("unsupported_semantics", error, _span(path, raised), "guards"))
                continue
            if condition.unsupported:
                diagnostics.append(_diagnostic("unsupported_semantics", "The guard condition contains a read or expression outside the Slice 2 model.", _span(path, statement.test), "guards"))
                continue
            boundaries.extend(condition.boundaries)
            boundary_ids = [item["id"] for item in condition.boundaries]
            assumptions = condition.assumptions
            exit_statement = {"kind": "raise", "exception": exception}
            description = describe_condition(statement.test)
            condition_source_text = source_expression(statement.test)
            claims.append(_claim(f"guard-{statement.lineno}", "rejected_input", f"Rejects input when {description}.", {"type": "entry_guard", "condition": condition.expression, "source_text": condition_source_text, "exit": exit_statement}, [_span(path, statement.test), _span(path, raised)], assumptions, boundary_ids))
            claims.append(_claim(f"exception-{statement.lineno}", "explicit_exception", f"Raises {exception['name']} when {description}.", {"type": "explicit_exception", "condition": condition.expression, "condition_source_text": condition_source_text, "source_text": source_expression(raised), "exception": exception}, [_span(path, statement.test), _span(path, raised)], assumptions, boundary_ids))
            continue
        if isinstance(statement, ast.Raise):
            exception, error = _exception(path, statement)
            if error:
                diagnostics.append(_diagnostic("unsupported_semantics", error, _span(path, statement), "guards"))
                continue
            claims.append(_claim(f"exception-{statement.lineno}", "explicit_exception", f"Raises {exception['name']}.", {"type": "explicit_exception", "condition": None, "source_text": source_expression(statement), "exception": exception}, [_span(path, statement)], [], []))
            continue
        break
    return claims, boundaries, diagnostics
