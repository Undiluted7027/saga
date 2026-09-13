"""Direct write and modeled-effect analysis for Slice 3."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from .boundaries import call_category
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


def _boundary(
    path: str,
    node: ast.AST,
    kind: str,
    target: str,
    reason: str,
    category: str = "important",
    concerns: list[str] | None = None,
) -> dict[str, Any]:
    """Build a source-linked boundary for behavior the effect model cannot inspect."""
    boundary = {
        "id": f"effect-boundary-{node.lineno}-{node.col_offset}-{kind}",
        "kind": kind,
        "target": {"text": target},
        "reason": reason,
        "category": category,
        "source_span": _span(path, node).as_dict(),
    }
    if concerns:
        boundary["concerns"] = concerns
    return boundary


def _root_name(node: ast.AST) -> str | None:
    """Return the source-level receiver root for a simple access chain."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _root_name(node.value)
    if isinstance(node, ast.Subscript):
        return _root_name(node.value)
    return None


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


_FRESH_CONTAINER_VALUES = (
    ast.Dict,
    ast.List,
    ast.Set,
    ast.DictComp,
    ast.ListComp,
    ast.SetComp,
)


class _FreshContainerCollector(ast.NodeVisitor):
    """Find names whose only local binding creates a builtin container."""

    def __init__(self) -> None:
        self.bindings: dict[str, list[bool]] = {}

    def _bind(self, target: ast.AST, fresh: bool = False) -> None:
        for item in ast.walk(target):
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store):
                self.bindings.setdefault(item.id, []).append(
                    fresh and item is target
                )

    def visit_Assign(self, node: ast.Assign) -> None:
        fresh = isinstance(node.value, _FRESH_CONTAINER_VALUES)
        for target in node.targets:
            self._bind(target, fresh)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._bind(
            node.target,
            node.value is not None and isinstance(node.value, _FRESH_CONTAINER_VALUES),
        )
        if node.value:
            self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._bind(node.target)
        self.visit(node.value)

    def visit_For(self, node: ast.For) -> None:
        self._bind(node.target)
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars:
                self._bind(item.optional_vars)
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._bind(node.target, isinstance(node.value, _FRESH_CONTAINER_VALUES))
        self.visit(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def fresh_names(self) -> set[str]:
        return {
            name
            for name, bindings in self.bindings.items()
            if bindings == [True]
        }


def _fresh_container_names(node: ast.FunctionDef) -> set[str]:
    """Return un-rebound local names initialized by container syntax."""
    collector = _FreshContainerCollector()
    for statement in node.body:
        collector.visit(statement)
    return collector.fresh_names()


class _EffectScanner(ast.NodeVisitor):
    """Walk one target body and record direct writes, known calls, and opaque calls."""

    def __init__(
        self,
        path: str,
        tree: ast.Module,
        globals_: set[str],
        parameters: set[str],
        fresh_containers: set[str],
    ) -> None:
        self.path = path
        self.aliases = _aliases(tree)
        self.globals = globals_
        self.parameters = parameters
        self.fresh_containers = fresh_containers
        self.effect_position_calls: set[int] = set()
        self.conditional_boundaries: list[dict[str, Any]] = []
        self.result = EffectResult([], [], [])

    def _conditional_boundary_ids(self) -> list[str]:
        """Return the active exception-flow limits from outermost to innermost."""
        return [boundary["id"] for boundary in self.conditional_boundaries]

    def _limit_claim(self, claim: dict[str, Any]) -> None:
        """Attach active exception-flow limits to an effect claim."""
        claim["boundary_ids"] = [
            *claim["boundary_ids"],
            *(
                boundary_id
                for boundary_id in self._conditional_boundary_ids()
                if boundary_id not in claim["boundary_ids"]
            ),
        ]

    def _limit_boundary(self, boundary: dict[str, Any]) -> None:
        """Attach active exception-flow limits to another boundary record."""
        boundary_ids = self._conditional_boundary_ids()
        if boundary_ids:
            boundary["boundary_ids"] = boundary_ids

    def _effect_relevant(self, node: ast.Call) -> bool:
        """Identify unresolved calls that can hide the answer to an effects question."""
        if call_category(node) == "routine":
            return False
        if id(node) in self.effect_position_calls:
            return True
        if isinstance(node.func, ast.Name):
            return node.func.id in self.parameters
        if isinstance(node.func, ast.Attribute):
            return _root_name(node.func.value) in self.parameters
        return False

    def _add_write(self, target: ast.AST) -> None:
        """Record an external write target and assignment-hook uncertainty when relevant."""
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._add_write(item)
            return
        structured = _target(target)
        if structured is None:
            self.result.diagnostics.append(_diagnostic("unsupported_semantics", "This assignment target is outside the Slice 3 write model.", _span(self.path, target), "effects"))
            return
        if isinstance(target, ast.Name) and target.id not in self.globals:
            return
        target_text = source_expression(target)
        local_container = (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id in self.fresh_containers
        )
        write_scope = "local_container" if local_container else "potentially_aliased"
        claim = _claim(
            self.path,
            target,
            {
                "type": "attempted_write",
                "target": structured,
                "source_text": target_text,
                "write_scope": write_scope,
            },
            (
                f"Writes to locally created container at {target_text}."
                if local_container
                else f"Attempts to write to {target_text}."
            ),
        )
        if isinstance(target, (ast.Attribute, ast.Subscript)) and not local_container:
            boundary = _boundary(self.path, target, "assignment_hooks", ast.unparse(target), "The assignment may invoke a descriptor, __setattr__, or __setitem__ implementation.")
            self._limit_boundary(boundary)
            self.result.boundaries.append(boundary)
            claim["boundary_ids"].append(boundary["id"])
        self._limit_claim(claim)
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
            claim = _claim(self.path, node, {"type": "known_effect", "effect": {"kind": effect["kind"], "callee": canonical}, "source_text": source_expression(node)}, f"May {effect['description']} through {canonical}(...).")
            self._limit_claim(claim)
            self.result.claims.append(claim)
        elif not modeled_exception:
            effect_relevant = self._effect_relevant(node)
            category = call_category(node)
            reason = (
                "Saga cannot determine whether this call mutates state or causes an external effect."
                if effect_relevant
                else "The callee is not in the effect registry and may affect behavior."
            )
            concerns = ["effects"] if effect_relevant else []
            if category != "routine":
                concerns.append("exceptions")
            boundary = _boundary(
                self.path,
                node,
                "unresolved_call",
                ast.unparse(node.func) + "(...)",
                reason,
                category,
                concerns or None,
            )
            self._limit_boundary(boundary)
            self.result.boundaries.append(boundary)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Expr(self, node: ast.Expr) -> None:
        """Treat calls made for discarded results as potentially effect-relevant."""
        calls = [item for item in ast.walk(node.value) if isinstance(item, ast.Call)]
        self.effect_position_calls.update(id(item) for item in calls)
        self.visit(node.value)

    def visit_With(self, node: ast.With) -> None:
        """Inspect a context-managed region while exposing entry/exit uncertainty."""
        self.result.diagnostics.append(_diagnostic("unsupported_semantics", "Context-manager effect control flow is outside the Slice 3 model.", _span(self.path, node), "effects"))
        boundary = _boundary(
            self.path,
            node,
            "unsupported_semantics",
            "with statement",
            (
                "Behavior in this context-managed region is conditional; Saga does "
                "not model __enter__, __exit__, or exception suppression."
            ),
            concerns=["effects"],
        )
        self._limit_boundary(boundary)
        self.result.boundaries.append(boundary)
        self.conditional_boundaries.append(boundary)
        try:
            for item in node.items:
                self.visit(item.context_expr)
                if item.optional_vars is not None:
                    self._add_write(item.optional_vars)
                    if isinstance(item.optional_vars, ast.Attribute):
                        self.visit(item.optional_vars.value)
                    elif isinstance(item.optional_vars, ast.Subscript):
                        self.visit(item.optional_vars.value)
                        self.visit(item.optional_vars.slice)
            for statement in node.body:
                self.visit(statement)
        finally:
            self.conditional_boundaries.pop()

    visit_AsyncWith = visit_With

    def visit_Try(self, node: ast.Try) -> None:
        """Inspect every try block while preserving exception-flow uncertainty."""
        self.result.diagnostics.append(_diagnostic("unsupported_semantics", "Try/except effect control flow is outside the Slice 3 model.", _span(self.path, node), "effects"))
        boundary = _boundary(
            self.path,
            node,
            "unsupported_semantics",
            "try statement",
            (
                "Behavior inside the protected body, handlers, else, and finally is conditional; "
                "Saga does not determine which blocks execute on a given call."
            ),
            concerns=["effects"],
        )
        self._limit_boundary(boundary)
        self.result.boundaries.append(boundary)
        self.conditional_boundaries.append(boundary)
        try:
            for statement in node.body:
                self.visit(statement)
            for handler in node.handlers:
                for statement in handler.body:
                    self.visit(statement)
            for statement in node.orelse:
                self.visit(statement)
            for statement in node.finalbody:
                self.visit(statement)
        finally:
            self.conditional_boundaries.pop()

    visit_TryStar = visit_Try

    def visit_While(self, node: ast.While) -> None:
        """Inspect every loop region without claiming which regions execute."""
        self.result.diagnostics.append(_diagnostic("unsupported_semantics", "While-loop analysis is outside the Slice 3 model.", _span(self.path, node), "effects"))
        boundary = _boundary(
            self.path,
            node,
            "unsupported_semantics",
            "while statement",
            (
                "Behavior in the loop test, body, and else branch may be conditional or repeated; "
                "Saga does not determine which regions execute or how often."
            ),
            concerns=["effects"],
        )
        self._limit_boundary(boundary)
        self.result.boundaries.append(boundary)
        self.conditional_boundaries.append(boundary)
        try:
            self.visit(node.test)
            for statement in node.body:
                self.visit(statement)
            for statement in node.orelse:
                self.visit(statement)
        finally:
            self.conditional_boundaries.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Do not attribute nested-function effects to the selected target."""
        self.result.diagnostics.append(_diagnostic("unsupported_semantics", "Nested function behavior is outside the Slice 3 model.", _span(self.path, node), "effects"))

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Report lambda semantics instead of silently traversing dynamic code."""
        self.result.diagnostics.append(_diagnostic("unsupported_semantics", "Lambda expressions are outside the Slice 3 model.", _span(self.path, node), "effects"))

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        """Report assignment expressions because their write semantics are not modeled here."""
        self.result.diagnostics.append(_diagnostic("unsupported_semantics", "Assignment expressions are outside the Slice 3 write model.", _span(self.path, node), "effects"))


def analyze_effects(path: str, tree: ast.Module, node: ast.FunctionDef) -> EffectResult:
    """Analyze direct effects in one supported function without entering nested definitions."""
    globals_: set[str] = set()
    for statement in node.body:
        if isinstance(statement, ast.Global):
            globals_.update(statement.names)
    parameters = {
        item.arg
        for item in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    }
    if node.args.vararg:
        parameters.add(node.args.vararg.arg)
    if node.args.kwarg:
        parameters.add(node.args.kwarg.arg)
    scanner = _EffectScanner(
        path,
        tree,
        globals_,
        parameters,
        _fresh_container_names(node) - globals_ - parameters,
    )
    for statement in node.body:
        scanner.visit(statement)
    return scanner.result
