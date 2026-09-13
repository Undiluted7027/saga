"""Slice 1 function inspection using Python's standard AST."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION


@dataclass(frozen=True)
class Span:
    """A half-open source location used by the serialized evidence schema."""

    path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int

    def as_dict(self) -> dict[str, Any]:
        """Serialize this source location using the schema's field names."""
        return {
            "path": self.path,
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
        }


def _span(path: str, node: ast.AST) -> Span:
    """Convert an AST node's location into a schema source span."""
    return Span(
        path,
        node.lineno,
        node.col_offset,
        getattr(node, "end_lineno", node.lineno),
        getattr(node, "end_col_offset", node.col_offset),
    )


def _diagnostic(
    kind: str,
    message: str,
    source_span: Span | None = None,
    analysis: str = "inspection",
) -> dict[str, Any]:
    """Build a diagnostic and attach a source span when one is available."""
    result: dict[str, Any] = {
        "kind": kind,
        "message": message,
        "analyses": [analysis],
    }
    if source_span:
        result["source_span"] = source_span.as_dict()
    return result


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Render the selected function's arguments without performing type inference."""
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults_start = len(positional) - len(args.defaults)
    rendered: list[str] = []
    for index, arg in enumerate(positional):
        value = ast.unparse(arg)
        if index >= defaults_start:
            value += "=" + ast.unparse(args.defaults[index - defaults_start])
        rendered.append(value)
        if args.posonlyargs and index == len(args.posonlyargs) - 1:
            rendered.append("/")
        if args.vararg and index == len(positional) - 1:
            rendered.append("*" + ast.unparse(args.vararg))
    if args.vararg and len(positional) == 0:
        rendered.append("*" + ast.unparse(args.vararg))
    elif not args.vararg and args.kwonlyargs:
        rendered.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        value = ast.unparse(arg)
        if default is not None:
            value += "=" + ast.unparse(default)
        rendered.append(value)
    if args.kwarg:
        rendered.append("**" + ast.unparse(args.kwarg))
    return f"{node.name}({', '.join(rendered)})"


def _is_generator(node: ast.FunctionDef) -> bool:
    """Return whether a function body contains a yield expression."""
    return any(isinstance(item, (ast.Yield, ast.YieldFrom)) for item in ast.walk(node))


def _overload_decorators(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Return imported names that can syntactically identify typing overloads."""
    names: set[str] = set()
    modules: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module in {
            "typing",
            "typing_extensions",
        }:
            names.update(
                item.asname or item.name
                for item in statement.names
                if item.name == "overload"
            )
        elif isinstance(statement, ast.Import):
            modules.update(
                item.asname or item.name
                for item in statement.names
                if item.name in {"typing", "typing_extensions"}
            )
    return names, modules


def _is_overload_declaration(tree: ast.Module, node: ast.AST) -> bool:
    """Recognize only overload decorators proven to come from typing modules."""
    names, modules = _overload_decorators(tree)
    for decorator in getattr(node, "decorator_list", []):
        if isinstance(decorator, ast.Name) and decorator.id in names:
            return True
        if (
            isinstance(decorator, ast.Attribute)
            and decorator.attr == "overload"
            and isinstance(decorator.value, ast.Name)
            and decorator.value.id in modules
        ):
            return True
    return False


def _select_concrete_function(
    tree: ast.Module,
    matches: list[ast.FunctionDef | ast.AsyncFunctionDef],
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Select one implementation after excluding proven overload declarations."""
    concrete = [node for node in matches if not _is_overload_declaration(tree, node)]
    return concrete[0] if len(concrete) == 1 else None


def _decorator_boundary(path: str, decorator: ast.expr) -> dict[str, Any]:
    """Limit body-derived claims when a decorator may replace the callable."""
    source_text = ast.unparse(decorator)
    return {
        "id": f"inspection-boundary-{decorator.lineno}-{decorator.col_offset}-decorator",
        "kind": "unsupported_semantics",
        "target": {"text": "@" + source_text},
        "reason": (
            "Saga analyzes the function body, but this decorator may replace or wrap "
            "the callable and change its returns, failures, or effects at runtime."
        ),
        "category": "important",
        "source_span": _span(path, decorator).as_dict(),
    }


def _base_card(path: str, qualified_name: str) -> dict[str, Any]:
    """Create an empty, schema-shaped card for a target under inspection."""
    return {
        "schema_version": SCHEMA_VERSION,
        "target": {
            "path": path,
            "qualified_name": qualified_name,
            "name": qualified_name.rsplit(".", 1)[-1],
            "signature": "",
            "status": "unsupported",
            "source_span": None,
        },
        "claims": [],
        "boundaries": [],
        "diagnostics": [],
    }


def inspect_function(file_path: str, qualified_name: str) -> dict[str, Any]:
    """Return one schema-shaped card for a module-level function selector."""
    card = _base_card(file_path, qualified_name)
    source = Path(file_path)
    if not source.exists():
        card["diagnostics"].append(_diagnostic("missing_file", f"Python file does not exist: {file_path}"))
        return card
    if not source.is_file():
        card["diagnostics"].append(_diagnostic("invalid_file", f"Target path is not a file: {file_path}"))
        return card
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=file_path)
    except (OSError, UnicodeError) as exc:
        card["diagnostics"].append(_diagnostic("file_error", f"Could not read {file_path}: {exc}"))
        return card
    except SyntaxError as exc:
        card["diagnostics"].append(_diagnostic("parsing", f"Could not parse {file_path}: {exc.msg}", Span(file_path, exc.lineno or 1, exc.offset or 0, exc.lineno or 1, exc.offset or 0)))
        return card

    if "." in qualified_name:
        card["diagnostics"].append(_diagnostic("unsupported_target", f"Only module-level functions are supported; '{qualified_name}' is qualified."))
        return card
    matches = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == qualified_name]
    if not matches:
        card["diagnostics"].append(_diagnostic("target_not_found", f"No module-level function named '{qualified_name}' was found in {file_path}."))
        return card
    node = _select_concrete_function(tree, matches)
    if node is None:
        card["diagnostics"].append(_diagnostic("ambiguous_target", f"Selector '{qualified_name}' matches {len(matches)} module-level functions."))
        return card

    target_span = _span(file_path, node)
    card["target"].update({"signature": _signature(node), "source_span": target_span.as_dict()})
    if isinstance(node, ast.AsyncFunctionDef):
        card["diagnostics"].append(_diagnostic("unsupported_target", "Async functions are outside the Slice 1 scope.", target_span))
    elif _is_generator(node):
        card["diagnostics"].append(_diagnostic("unsupported_target", "Generator functions are outside the Slice 1 scope.", target_span))
    else:
        card["target"]["status"] = "supported"
        from .interprocedural import analyze_one_hop

        evidence = analyze_one_hop(file_path, tree, node)
        card["claims"].extend(evidence.claims)
        card["boundaries"].extend(evidence.boundaries)
        card["diagnostics"].extend(evidence.diagnostics)
    return card
