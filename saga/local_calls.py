"""Conservative resolution for one-hop calls within a Python module."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Literal

from .inspect import _select_concrete_function, _span

ResolutionStatus = Literal[
    "resolved",
    "recursive",
    "hop_limit",
    "unsupported",
    "ambiguous",
    "parameter",
    "shadowed",
    "external",
]


@dataclass(frozen=True)
class LocalCallResolution:
    """Describe what Saga established about one call target."""

    call: ast.Call
    status: ResolutionStatus
    invoked_as: str
    callee: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    via_alias: bool = False


class _FunctionScope(ast.NodeVisitor):
    """Collect calls and bindings without entering nested scopes."""

    def __init__(self) -> None:
        self.calls: list[ast.Call] = []
        self.bindings: dict[str, list[ast.AST]] = {}
        self.comprehension_shadows: list[set[str]] = []
        self.shadowed_calls: set[int] = set()

    def bind(self, name: str, node: ast.AST) -> None:
        """Record one binding in the selected function scope."""
        self.bindings.setdefault(name, []).append(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(node.id, node)

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node)
        if (
            isinstance(node.func, ast.Name)
            and any(node.func.id in names for names in self.comprehension_shadows)
        ):
            self.shadowed_calls.add(id(node))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.bind(node.name, node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bind(node.name, node)

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            self.bind(item.asname or item.name.split(".")[0], node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for item in node.names:
            self.bind(item.asname or item.name, node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.bind(node.name, node)
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        """Collect calls while respecting names bound inside the comprehension."""
        shadowed: set[str] = set()
        self.comprehension_shadows.append(shadowed)
        for generator in node.generators:
            self.visit(generator.iter)
            shadowed.update(
                item.id
                for item in ast.walk(generator.target)
                if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)
            )
            for condition in generator.ifs:
                self.visit(condition)
        self.visit(node.elt)
        self.comprehension_shadows.pop()

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node: ast.DictComp) -> None:
        shadowed: set[str] = set()
        self.comprehension_shadows.append(shadowed)
        for generator in node.generators:
            self.visit(generator.iter)
            shadowed.update(
                item.id
                for item in ast.walk(generator.target)
                if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)
            )
            for condition in generator.ifs:
                self.visit(condition)
        self.visit(node.key)
        self.visit(node.value)
        self.comprehension_shadows.pop()

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.bind(node.name, node)
        if node.pattern:
            self.visit(node.pattern)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.bind(node.name, node)


class _ModuleScope(_FunctionScope):
    """Collect bindings across module control flow without entering child scopes."""

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)


def _module_bindings(tree: ast.Module) -> dict[str, list[ast.AST]]:
    """Collect source-level module bindings used to reject ambiguous names."""
    scope = _ModuleScope()
    for statement in tree.body:
        scope.visit(statement)
    return scope.bindings


def _unique_function(
    tree: ast.Module,
    bindings: dict[str, list[ast.AST]],
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Resolve one function, excluding proven typing overload declarations."""
    candidates = bindings.get(name, [])
    if not all(
        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        for item in candidates
    ):
        return None
    return _select_concrete_function(tree, candidates)


def _module_aliases(
    tree: ast.Module,
    bindings: dict[str, list[ast.AST]],
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Resolve one-level module aliases with no competing source binding."""
    aliases: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(statement.value, ast.Name):
            continue
        if len(bindings.get(target.id, [])) != 1:
            continue
        callee = _unique_function(tree, bindings, statement.value.id)
        if callee is not None and callee.lineno < statement.lineno:
            aliases[target.id] = callee
    return aliases


def _local_aliases(
    tree: ast.Module,
    node: ast.FunctionDef,
    scope: _FunctionScope,
    module_bindings: dict[str, list[ast.AST]],
) -> dict[str, tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.Assign]]:
    """Resolve straight-line aliases assigned exactly once in the caller."""
    aliases: dict[str, tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.Assign]] = {}
    for statement in node.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(statement.value, ast.Name):
            continue
        if len(scope.bindings.get(target.id, [])) != 1 or statement.value.id in scope.bindings:
            continue
        callee = _unique_function(tree, module_bindings, statement.value.id)
        if callee is not None:
            aliases[target.id] = (callee, statement)
    return aliases


def _supported_callee(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Match the target subset accepted by the current function inspector."""
    if isinstance(node, ast.AsyncFunctionDef):
        return False
    return not any(isinstance(item, (ast.Yield, ast.YieldFrom)) for item in ast.walk(node))


def resolve_local_calls(
    tree: ast.Module,
    caller: ast.FunctionDef,
    ancestry: tuple[str, ...],
    depth: int,
) -> list[LocalCallResolution]:
    """Classify calls in one function without following any callee."""
    module_bindings = _module_bindings(tree)
    module_aliases = _module_aliases(tree, module_bindings)
    scope = _FunctionScope()
    for argument in [*caller.args.posonlyargs, *caller.args.args, *caller.args.kwonlyargs]:
        scope.bind(argument.arg, argument)
    if caller.args.vararg:
        scope.bind(caller.args.vararg.arg, caller.args.vararg)
    if caller.args.kwarg:
        scope.bind(caller.args.kwarg.arg, caller.args.kwarg)
    for statement in caller.body:
        scope.visit(statement)
    local_aliases = _local_aliases(tree, caller, scope, module_bindings)
    parameter_names = {
        argument.arg
        for argument in [
            *caller.args.posonlyargs,
            *caller.args.args,
            *caller.args.kwonlyargs,
        ]
    }
    if caller.args.vararg:
        parameter_names.add(caller.args.vararg.arg)
    if caller.args.kwarg:
        parameter_names.add(caller.args.kwarg.arg)

    resolutions: list[LocalCallResolution] = []
    for call in scope.calls:
        invoked_as = ast.unparse(call.func)
        if id(call) in scope.shadowed_calls:
            resolutions.append(LocalCallResolution(call, "shadowed", invoked_as))
            continue
        if not isinstance(call.func, ast.Name):
            resolutions.append(LocalCallResolution(call, "external", invoked_as))
            continue
        name = call.func.id
        callee: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        via_alias = False
        if name in scope.bindings:
            rebound_before_call = any(
                not isinstance(binding, ast.arg)
                and (binding.lineno, binding.col_offset)
                <= (call.lineno, call.col_offset)
                for binding in scope.bindings[name]
            )
            if name in parameter_names and not rebound_before_call:
                resolutions.append(LocalCallResolution(call, "parameter", invoked_as))
                continue
            alias = local_aliases.get(name)
            if alias is None or alias[1].lineno >= call.lineno:
                resolutions.append(LocalCallResolution(call, "shadowed", invoked_as))
                continue
            callee = alias[0]
            via_alias = True
        else:
            callee = _unique_function(tree, module_bindings, name)
            if callee is None and name in module_aliases:
                callee = module_aliases[name]
                via_alias = True
            elif callee is None and len(module_bindings.get(name, [])) > 1:
                resolutions.append(LocalCallResolution(call, "ambiguous", invoked_as))
                continue
            elif callee is None:
                resolutions.append(LocalCallResolution(call, "external", invoked_as))
                continue

        if callee.name in ancestry:
            status: ResolutionStatus = "recursive"
        elif depth >= 1:
            status = "hop_limit"
        elif not _supported_callee(callee):
            status = "unsupported"
        else:
            status = "resolved"
        resolutions.append(LocalCallResolution(call, status, invoked_as, callee, via_alias))
    return resolutions


def call_record(path: str, caller: ast.FunctionDef, resolution: LocalCallResolution) -> dict[str, object]:
    """Serialize the source chain and simple argument bindings for one call."""
    assert resolution.callee is not None
    positional = [*resolution.callee.args.posonlyargs, *resolution.callee.args.args]
    bindings = [
        {
            "parameter": parameter.arg,
            "argument": ast.unparse(argument),
            "source_span": _span(path, argument).as_dict(),
        }
        for parameter, argument in zip(positional, resolution.call.args)
        if not isinstance(argument, ast.Starred)
    ]
    parameters = {item.arg for item in [*positional, *resolution.callee.args.kwonlyargs]}
    for keyword in resolution.call.keywords:
        if keyword.arg in parameters:
            bindings.append({
                "parameter": keyword.arg,
                "argument": ast.unparse(keyword.value),
                "source_span": _span(path, keyword.value).as_dict(),
            })
    return {
        "caller": caller.name,
        "callee": resolution.callee.name,
        "invoked_as": resolution.invoked_as,
        "via_alias": resolution.via_alias,
        "call_site": _span(path, resolution.call).as_dict(),
        "callee_span": _span(path, resolution.callee).as_dict(),
        "argument_bindings": bindings,
    }
