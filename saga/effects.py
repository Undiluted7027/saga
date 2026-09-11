"""Direct write and modeled-effect analysis for Slice 3."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from .guards import BUILTIN_EXCEPTIONS
from .inspect import _diagnostic, _span
from .presentation import source_expression

EFFECT_REGISTRY: dict[str, dict[str, str]] = {
    "pathlib.Path.write_text": {
        "kind": "filesystem_write",
        "description": "write text to the filesystem",
    },
}
PATH_CONSTRUCTORS = {"pathlib.Path"}
ROUTINE_BUILTINS = {"all", "any", "bool", "dict", "enumerate", "float", "int", "isinstance", "len", "list", "max", "min", "range", "set", "sorted", "str", "sum", "tuple", "zip"}
ROUTINE_METHODS = {"get", "items", "keys", "values"}


@dataclass
class EffectResult:
    """Collect effects, boundaries, and diagnostics without deduplicating source occurrences."""

    claims: list[dict[str, Any]]
    boundaries: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]


def _claim(path: str, node: ast.AST, statement: dict[str, Any], text: str) -> dict[str, Any]:
    """Build a derived effect claim tied to one call or write location."""
    return {
        "id": f"effect-{node.lineno}-{node.col_offset}-{statement['type']}",
        "kind": statement["type"],
        "statement": {"text": text, **statement},
        "evidence": {"method": "syntactic_write" if statement["type"] == "attempted_write" else "effect_registry", "evidence_class": "derived", "detail": {}},
        "source_spans": [_span(path, node).as_dict()],
        "assumptions": [],
        "boundary_ids": [],
    }


def _boundary(path: str, node: ast.AST, kind: str, target: str, reason: str, category: str = "important") -> dict[str, Any]:
    """Build a source-linked boundary for behavior the effect model cannot inspect."""
    return {
        "id": f"effect-boundary-{node.lineno}-{node.col_offset}-{kind}",
        "kind": kind,
        "target": {"text": target},
        "reason": reason,
        "category": category,
        "source_span": _span(path, node).as_dict(),
    }


def _call_category(node: ast.Call) -> str:
    """Classify common unresolved routines as low-signal without calling them safe."""
    if isinstance(node.func, ast.Name) and node.func.id in ROUTINE_BUILTINS:
        return "routine"
    if isinstance(node.func, ast.Attribute) and node.func.attr in ROUTINE_METHODS:
        return "routine"
    return "important"


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Resolve only explicit imports needed by the small effect registry."""
    aliases: dict[str, str] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for item in statement.names:
                aliases[item.asname or item.name.split(".")[0]] = item.name
        elif isinstance(statement, ast.ImportFrom) and statement.module == "pathlib":
            for item in statement.names:
                if item.name == "Path":
                    aliases[item.asname or item.name] = "pathlib.Path"
    return aliases


def _resolve(node: ast.AST, aliases: dict[str, str]) -> str | None:
    """Resolve a call expression to a registry name when syntax proves the alias."""
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Call):
            constructor = _resolve(node.value.func, aliases)
            if constructor in PATH_CONSTRUCTORS:
                return constructor + "." + node.attr
        base = _resolve(node.value, aliases)
        return f"{base}.{node.attr}" if base else None
    return None


def _target(target: ast.AST) -> dict[str, Any] | None:
    """Represent an assignable target without pretending to know its runtime type."""
    if isinstance(target, ast.Name):
        return {"kind": "name", "name": target.id}
    if isinstance(target, ast.Attribute):
        return {"kind": "attribute", "base": {"kind": "source", "text": ast.unparse(target.value)}, "name": target.attr}
    if isinstance(target, ast.Subscript):
        return {"kind": "subscript", "value": {"kind": "source", "text": ast.unparse(target.value)}, "index": {"kind": "source", "text": ast.unparse(target.slice)}}
    if isinstance(target, (ast.Tuple, ast.List)):
        items = [_target(item) for item in target.elts]
        if all(item is not None for item in items):
            return {"kind": "unpacking", "items": items}
    return None


class _EffectScanner(ast.NodeVisitor):
    """Walk one target body and record direct writes, known calls, and opaque calls."""

    def __init__(self, path: str, tree: ast.Module, globals_: set[str]) -> None:
        self.path = path
        self.aliases = _aliases(tree)
        self.globals = globals_
        self.result = EffectResult([], [], [])

    def _add_write(self, target: ast.AST) -> None:
        """Record an external write target and assignment-hook uncertainty when relevant."""
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._add_write(item)
            return
        structured = _target(target)
        if structured is None:
            self.result.diagnostics.append(_diagnostic("unsupported_semantics", "This assignment target is outside the Slice 3 write model.", _span(self.path, target)))
            return
        if isinstance(target, ast.Name) and target.id not in self.globals:
            return
        target_text = source_expression(target)
        claim = _claim(self.path, target, {"type": "attempted_write", "target": structured, "source_text": target_text}, f"Attempts to write to {target_text}.")
        if isinstance(target, (ast.Attribute, ast.Subscript)):
            boundary = _boundary(self.path, target, "assignment_hooks", ast.unparse(target), "The assignment may invoke a descriptor, __setattr__, or __setitem__ implementation.")
            self.result.boundaries.append(boundary)
            claim["boundary_ids"].append(boundary["id"])
        self.result.claims.append(claim)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Inspect assignment targets and then scan the assigned value for calls."""
        for target in node.targets:
            self._add_write(target)
            self.visit(target.value if isinstance(target, ast.Attribute) else target.slice if isinstance(target, ast.Subscript) else ast.Constant(value=None))
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Inspect an annotated assignment and its value or annotation expressions."""
        self._add_write(node.target)
        if isinstance(node.target, ast.Attribute):
            self.visit(node.target.value)
        elif isinstance(node.target, ast.Subscript):
            self.visit(node.target.value)
            self.visit(node.target.slice)
        if node.annotation:
            self.visit(node.annotation)
        if node.value:
            self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        """Treat augmented assignment as a write and scan its read and value sides."""
        self._add_write(node.target)
        if isinstance(node.target, ast.Attribute):
            self.visit(node.target.value)
        elif isinstance(node.target, ast.Subscript):
            self.visit(node.target.value)
            self.visit(node.target.slice)
        self.visit(node.value)

    def visit_For(self, node: ast.For) -> None:
        """Inspect loop expressions and any externally visible loop target."""
        self._add_write(node.target)
        self.visit(node.iter)
        for statement in node.body:
            self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)

    def visit_Global(self, node: ast.Global) -> None:
        """Record names declared global for later assignment classification."""
        self.globals.update(node.names)

    def visit_Call(self, node: ast.Call) -> None:
        """Classify a registry-backed call or expose it as an unresolved boundary."""
        canonical = _resolve(node.func, self.aliases)
        modeled_exception = isinstance(node.func, ast.Name) and node.func.id in BUILTIN_EXCEPTIONS
        if canonical in EFFECT_REGISTRY:
            effect = EFFECT_REGISTRY[canonical]
            self.result.claims.append(_claim(self.path, node, {"type": "known_effect", "effect": {"kind": effect["kind"], "callee": canonical}, "source_text": source_expression(node)}, f"May {effect['description']} through {canonical}(...)."))
        elif not modeled_exception:
            boundary = _boundary(self.path, node, "unresolved_call", ast.unparse(node.func) + "(...)", "The callee is not in the effect registry and may affect behavior.", _call_category(node))
            self.result.boundaries.append(boundary)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_With(self, node: ast.With) -> None:
        """Report context-manager semantics that the POC does not model."""
        self.result.diagnostics.append(_diagnostic("unsupported_semantics", "Context-manager effects are outside the Slice 3 model.", _span(self.path, node)))

    visit_AsyncWith = visit_With

    def visit_Try(self, node: ast.Try) -> None:
        """Report exception-handler semantics that could hide or add effects."""
        self.result.diagnostics.append(_diagnostic("unsupported_semantics", "Try/except effect control flow is outside the Slice 3 model.", _span(self.path, node)))

    def visit_While(self, node: ast.While) -> None:
        """Report while-loop semantics because only for loops are in the supported subset."""
        self.result.diagnostics.append(_diagnostic("unsupported_semantics", "While-loop analysis is outside the Slice 3 model.", _span(self.path, node)))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Do not attribute nested-function effects to the selected target."""
        self.result.diagnostics.append(_diagnostic("unsupported_semantics", "Nested function behavior is outside the Slice 3 model.", _span(self.path, node)))

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Report lambda semantics instead of silently traversing dynamic code."""
        self.result.diagnostics.append(_diagnostic("unsupported_semantics", "Lambda expressions are outside the Slice 3 model.", _span(self.path, node)))

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        """Report assignment expressions because their write semantics are not modeled here."""
        self.result.diagnostics.append(_diagnostic("unsupported_semantics", "Assignment expressions are outside the Slice 3 write model.", _span(self.path, node)))


def analyze_effects(path: str, tree: ast.Module, node: ast.FunctionDef) -> EffectResult:
    """Analyze direct effects in one supported function without entering nested definitions."""
    globals_: set[str] = set()
    for statement in node.body:
        if isinstance(statement, ast.Global):
            globals_.update(statement.names)
    scanner = _EffectScanner(path, tree, globals_)
    for statement in node.body:
        scanner.visit(statement)
    return scanner.result
