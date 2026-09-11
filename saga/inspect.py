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


def _diagnostic(kind: str, message: str, source_span: Span | None = None) -> dict[str, Any]:
    """Build a diagnostic and attach a source span when one is available."""
    result: dict[str, Any] = {"kind": kind, "message": message}
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
    if len(matches) > 1:
        card["diagnostics"].append(_diagnostic("ambiguous_target", f"Selector '{qualified_name}' matches {len(matches)} module-level functions."))
        return card

    node = matches[0]
    target_span = _span(file_path, node)
    card["target"].update({"signature": _signature(node), "source_span": target_span.as_dict()})
    if isinstance(node, ast.AsyncFunctionDef):
        card["diagnostics"].append(_diagnostic("unsupported_target", "Async functions are outside the Slice 1 scope.", target_span))
    elif node.decorator_list:
        card["diagnostics"].append(_diagnostic("unsupported_target", "Decorated target functions are outside the Slice 1 scope.", target_span))
    elif _is_generator(node):
        card["diagnostics"].append(_diagnostic("unsupported_target", "Generator functions are outside the Slice 1 scope.", target_span))
    else:
        card["target"]["status"] = "supported"
        from .guards import analyze_guards

        guard_claims, guard_boundaries, guard_diagnostics = analyze_guards(file_path, node)
        card["claims"].extend(guard_claims)
        card["boundaries"].extend(guard_boundaries)
        card["diagnostics"].extend(guard_diagnostics)
    return card
