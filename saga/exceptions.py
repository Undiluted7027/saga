"""Conservative analysis of explicit exceptions that may leave a function."""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field
from typing import Any

from .guards import BUILTIN_EXCEPTIONS
from .inspect import _diagnostic, _span
from .presentation import describe_condition, source_expression


@dataclass
class ExceptionFact:
    """An explicit raise plus the information needed for handler matching."""

    claim: dict[str, Any]
    names: tuple[str, ...] | None


@dataclass
class ExceptionResult:
    """Escaping exception claims and limits encountered while deriving them."""

    claims: list[dict[str, Any]] = field(default_factory=list)
    boundaries: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


def _builtin_exception(name: str) -> type[BaseException] | None:
    value = getattr(builtins, name, None)
    return value if isinstance(value, type) and issubclass(value, BaseException) else None


class _BoundNameCollector(ast.NodeVisitor):
    """Collect bindings in one scope without entering child scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(item.asname or item.name.split(".")[0] for item in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(item.asname or item.name for item in node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        for statement in node.body:
            self.visit(statement)

    def visit_Global(self, node: ast.Global) -> None:
        # A global declaration makes builtin lookup depend on mutable module state.
        self.names.update(node.names)

    visit_Nonlocal = visit_Global


def _builtin_names(tree: ast.Module, node: ast.FunctionDef) -> set[str]:
    """Return exception names not visibly shadowed in the module or function."""
    module_bindings = _BoundNameCollector()
    for statement in tree.body:
        module_bindings.visit(statement)
    local_bindings = _BoundNameCollector()
    for statement in node.body:
        local_bindings.visit(statement)
    local_bindings.names.update(
        argument.arg
        for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    )
    if node.args.vararg:
        local_bindings.names.add(node.args.vararg.arg)
    if node.args.kwarg:
        local_bindings.names.add(node.args.kwarg.arg)
    return BUILTIN_EXCEPTIONS - module_bindings.names - local_bindings.names


def _raised_exception(node: ast.Raise, builtin_names: set[str]) -> tuple[dict[str, Any], tuple[str, ...] | None]:
    """Describe only what the syntax establishes about the raised value."""
    value = node.exc
    if isinstance(value, ast.Name) and value.id in builtin_names:
        return {"kind": "builtin_exception", "name": value.id}, (value.id,)
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in builtin_names:
        return {"kind": "builtin_exception", "name": value.func.id}, (value.func.id,)
    if value is None:
        return {"kind": "caught_exception", "name": None}, None
    return {"kind": "unknown_exception", "expression": source_expression(value)}, None


def _handler_types(handler: ast.ExceptHandler, builtin_names: set[str]) -> tuple[str, ...] | None:
    """Return statically modeled handler types; None means matching is unknown."""
    if handler.type is None:
        return ("*",)
    values = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    names: list[str] = []
    for value in values:
        if not isinstance(value, ast.Name) or value.id not in builtin_names:
            return None
        names.append(value.id)
    return tuple(names)


def _matches(raised: tuple[str, ...], handled: tuple[str, ...]) -> bool:
    """Use Python's builtin exception hierarchy for a known match."""
    for raised_name in raised:
        raised_type = _builtin_exception(raised_name)
        if raised_type is None:
            return False
        for handled_name in handled:
            handled_type = _builtin_exception(handled_name)
            if handled_type is not None and issubclass(raised_type, handled_type):
                return True
    return False


def _boundary(path: str, raise_span: dict[str, Any], handler: ast.ExceptHandler) -> dict[str, Any]:
    """Expose an exception match Saga cannot decide safely."""
    return {
        "id": (
            f"exception-boundary-{raise_span['start_line']}-{raise_span['start_column']}"
            f"-{handler.lineno}-{handler.col_offset}"
        ),
        "kind": "exception_matching",
        "target": {"text": source_expression(handler.type) if handler.type else "bare except"},
        "reason": "Saga cannot determine whether this handler catches the explicitly raised value.",
        "category": "important",
        "source_span": _span(path, handler).as_dict(),
    }


def _claim(
    path: str,
    node: ast.Raise,
    exception: dict[str, Any],
    conditions: tuple[dict[str, Any], ...],
    handler: ast.ExceptHandler | None,
    handler_types: tuple[str, ...] | None,
) -> dict[str, Any]:
    """Build an explicit-exception claim without upgrading syntactic evidence."""
    if node.exc is None:
        if handler is None:
            text = "A bare raise may escape with an unresolved exception type."
        elif handler_types == ("Exception",):
            text = "Re-raises an exception of unknown concrete type matched by Exception."
            exception = {"kind": "caught_exception", "handler_types": ["Exception"]}
        elif handler_types and handler_types != ("*",):
            text = f"Re-raises an exception matched by {' or '.join(handler_types)}."
            exception = {"kind": "caught_exception", "handler_types": list(handler_types)}
        else:
            text = "Re-raises a caught exception of unknown type."
    elif exception["kind"] == "builtin_exception":
        text = f"Raises {exception['name']}."
    else:
        text = f"An exception may escape from raising {exception['expression']}; its type is unresolved."
    if conditions:
        text = text[:-1] + " when " + " and ".join(item["text"] for item in conditions) + "."
    spans = [item["source_span"] for item in conditions]
    spans.append(_span(path, node).as_dict())
    if handler is not None:
        spans.insert(0, _span(path, handler).as_dict())
    return {
        "id": f"exception-{node.lineno}-{node.col_offset}",
        "kind": "explicit_exception",
        "statement": {
            "text": text,
            "type": "explicit_exception",
            "source_text": source_expression(node),
            "condition": None,
            "condition_source_text": " and ".join(item["source_text"] for item in conditions),
            "path_conditions": list(conditions),
            "exception": exception,
            "handler_spans": [_span(path, handler).as_dict()] if handler is not None else [],
        },
        "evidence": {"method": "explicit_raise_flow", "evidence_class": "derived", "detail": {}},
        "source_spans": spans,
        "assumptions": (
            [{"text": f"{exception['name']} resolves to the Python builtin exception class."}]
            if exception.get("kind") == "builtin_exception"
            else []
        ),
        "boundary_ids": [],
    }


def _assertion_claim(
    path: str,
    node: ast.Assert,
    conditions: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Describe the conditional AssertionError established by an assert statement."""
    failure = _condition(path, node.test, False)
    all_conditions = (*conditions, failure)
    requirement = describe_condition(node.test)
    text = f"May raise AssertionError unless {requirement}."
    if conditions:
        text = (
            "When "
            + " and ".join(item["text"] for item in conditions)
            + f", may raise AssertionError unless {requirement}."
        )
    return {
        "id": f"exception-{node.lineno}-{node.col_offset}-assertion",
        "kind": "explicit_exception",
        "statement": {
            "text": text,
            "type": "explicit_exception",
            "source_text": source_expression(node),
            "condition": None,
            "condition_source_text": " and ".join(
                item["source_text"] for item in all_conditions
            ),
            "path_conditions": list(all_conditions),
            "exception": {
                "kind": "builtin_exception",
                "name": "AssertionError",
            },
            "handler_spans": [],
        },
        "evidence": {
            "method": "assert_statement",
            "evidence_class": "derived",
            "detail": {},
        },
        "source_spans": [
            *(item["source_span"] for item in all_conditions),
            _span(path, node).as_dict(),
        ],
        "assumptions": [
            {
                "text": (
                    "The assertion depends on __debug__ being true; Python may remove "
                    "it under optimization."
                )
            },
            {
                "text": (
                    "The assertion condition and optional message must finish "
                    "evaluation; Saga does not infer exceptions raised by them."
                )
            },
        ],
        "boundary_ids": [],
    }


def _condition(path: str, node: ast.AST, truth: bool) -> dict[str, Any]:
    description = describe_condition(node)
    source = source_expression(node)
    return {
        "text": description if truth else f"not ({description})",
        "source_text": source if truth else f"not ({source})",
        "source_span": _span(path, node).as_dict(),
    }


def _suite_always_exits(statements: list[ast.stmt]) -> bool:
    """Recognize simple suites after which later statements are unreachable."""
    for statement in statements:
        if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
            return True
        if (
            isinstance(statement, ast.If)
            and statement.orelse
            and _suite_always_exits(statement.body)
            and _suite_always_exits(statement.orelse)
        ):
            return True
        if (
            isinstance(statement, ast.Try)
            and statement.finalbody
            and _suite_always_exits(statement.finalbody)
        ):
            return True
        if isinstance(statement, ast.Try):
            normal_path_exits = _suite_always_exits(statement.body) or (
                bool(statement.orelse) and _suite_always_exits(statement.orelse)
            )
            if normal_path_exits and all(
                _suite_always_exits(handler.body) for handler in statement.handlers
            ):
                return True
    return False


class _ExceptionAnalyzer:
    """Walk statement suites while applying local try/except handling."""

    def __init__(self, path: str, builtin_names: set[str]) -> None:
        self.path = path
        self.builtin_names = builtin_names
        self.boundaries: list[dict[str, Any]] = []
        self.diagnostics: list[dict[str, Any]] = []

    def suite(
        self,
        statements: list[ast.stmt],
        conditions: tuple[dict[str, Any], ...] = (),
        caught_by: ast.ExceptHandler | None = None,
    ) -> list[ExceptionFact]:
        facts: list[ExceptionFact] = []
        for statement in statements:
            facts.extend(self.statement(statement, conditions, caught_by))
            if _suite_always_exits([statement]):
                break
        return facts

    def statement(
        self,
        node: ast.stmt,
        conditions: tuple[dict[str, Any], ...],
        caught_by: ast.ExceptHandler | None,
    ) -> list[ExceptionFact]:
        if isinstance(node, ast.Raise):
            exception, names = _raised_exception(node, self.builtin_names)
            caught_names = _handler_types(caught_by, self.builtin_names) if caught_by is not None else None
            if node.exc is None and caught_by is None:
                self.diagnostics.append(
                    _diagnostic(
                        "unsupported_semantics",
                        "A bare raise outside a statically visible exception handler cannot be described as a re-raise.",
                        _span(self.path, node),
                        "exceptions",
                    )
                )
            elif node.exc is None and caught_names != ("*",):
                names = caught_names
            claim = _claim(self.path, node, exception, conditions, caught_by, caught_names)
            if exception["kind"] == "unknown_exception":
                boundary = {
                    "id": f"exception-boundary-{node.lineno}-{node.col_offset}-dispatch",
                    "kind": "exception_dispatch",
                    "target": {"text": exception["expression"]},
                    "reason": (
                        "The raised expression does not resolve to an unshadowed "
                        "modeled builtin exception."
                    ),
                    "category": "important",
                    "source_span": _span(self.path, node).as_dict(),
                }
                self.boundaries.append(boundary)
                claim["boundary_ids"].append(boundary["id"])
            return [ExceptionFact(claim, names)]
        if isinstance(node, ast.Assert):
            return [
                ExceptionFact(
                    _assertion_claim(self.path, node, conditions),
                    ("AssertionError",),
                )
            ]
        if isinstance(node, ast.If):
            return [
                *self.suite(node.body, (*conditions, _condition(self.path, node.test, True)), caught_by),
                *self.suite(node.orelse, (*conditions, _condition(self.path, node.test, False)), caught_by),
            ]
        if isinstance(node, (ast.For, ast.While)):
            return [
                *self.suite(node.body, conditions, caught_by),
                *self.suite(node.orelse, conditions, caught_by),
            ]
        if isinstance(node, ast.Try):
            protected = self.apply_handlers(self.suite(node.body, conditions, caught_by), node.handlers)
            handler_facts = [
                fact
                for handler in node.handlers
                for fact in self.suite(handler.body, conditions, handler)
            ]
            # We keep exceptions from both the protected suite and finally. Proving
            # that finally always replaces an active exception needs full CFG flow.
            final_facts = self.suite(node.finalbody, conditions, caught_by)
            if node.finalbody and _suite_always_exits(node.finalbody):
                return final_facts
            return [
                *protected,
                *handler_facts,
                *self.suite(node.orelse, conditions, caught_by),
                *final_facts,
            ]
        if isinstance(node, (ast.With, ast.AsyncWith)):
            facts = self.suite(node.body, conditions, caught_by)
            for fact in facts:
                span = fact.claim["source_spans"][-1]
                boundary = {
                    "id": (
                        f"exception-boundary-{span['start_line']}-{span['start_column']}"
                        f"-{node.lineno}-{node.col_offset}-context-manager"
                    ),
                    "kind": "exception_matching",
                    "target": {"text": ", ".join(source_expression(item.context_expr) for item in node.items)},
                    "reason": "A context manager may suppress this explicitly raised exception.",
                    "category": "important",
                    "source_span": _span(self.path, node).as_dict(),
                }
                self.boundaries.append(boundary)
                fact.claim["boundary_ids"].append(boundary["id"])
                fact.claim["source_spans"].append(boundary["source_span"])
            return facts
        if isinstance(node, ast.TryStar):
            self.diagnostics.append(
                _diagnostic(
                    "unsupported_semantics",
                    "Exception-group matching through except* is outside the explicit exception model.",
                    _span(self.path, node),
                    "exceptions",
                )
            )
            return [
                *self.suite(node.body, conditions, caught_by),
                *(fact for handler in node.handlers for fact in self.suite(handler.body, conditions, handler)),
                *self.suite(node.orelse, conditions, caught_by),
                *self.suite(node.finalbody, conditions, caught_by),
            ]
        if isinstance(node, ast.Match):
            return [fact for case in node.cases for fact in self.suite(case.body, conditions, caught_by)]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return []
        return []

    def apply_handlers(
        self,
        facts: list[ExceptionFact],
        handlers: list[ast.ExceptHandler],
    ) -> list[ExceptionFact]:
        escaping: list[ExceptionFact] = []
        for fact in facts:
            handled = False
            checked_spans: list[dict[str, Any]] = []
            for index, handler in enumerate(handlers):
                handler_types = _handler_types(handler, self.builtin_names)
                if handler_types == ("*",):
                    handled = True
                    break
                if fact.names is None or handler_types is None:
                    later_types = [
                        _handler_types(item, self.builtin_names)
                        for item in handlers[index + 1 :]
                    ]
                    if ("*",) in later_types or (
                        fact.names is not None
                        and any(
                            item is not None and _matches(fact.names, item)
                            for item in later_types
                        )
                    ):
                        handled = True
                        break
                    boundary = _boundary(self.path, fact.claim["source_spans"][-1], handler)
                    self.boundaries.append(boundary)
                    fact.claim["boundary_ids"].append(boundary["id"])
                    fact.claim["source_spans"].append(_span(self.path, handler).as_dict())
                    break
                if _matches(fact.names, handler_types):
                    handled = True
                    break
                checked_spans.append(_span(self.path, handler).as_dict())
            if not handled:
                fact.claim["statement"]["handler_spans"].extend(checked_spans)
                fact.claim["source_spans"].extend(checked_spans)
                escaping.append(fact)
        return escaping


def analyze_exceptions(path: str, tree: ast.Module, node: ast.FunctionDef) -> ExceptionResult:
    """Report explicit exceptions that may escape the selected function."""
    analyzer = _ExceptionAnalyzer(path, _builtin_names(tree, node))
    facts = analyzer.suite(node.body)
    referenced_boundaries = {
        boundary_id for fact in facts for boundary_id in fact.claim["boundary_ids"]
    }
    return ExceptionResult(
        claims=[fact.claim for fact in facts],
        boundaries=[item for item in analyzer.boundaries if item["id"] in referenced_boundaries],
        diagnostics=analyzer.diagnostics,
    )


def _contains(root: ast.AST, target: ast.AST) -> bool:
    """Check identity membership without confusing equal-looking call sites."""
    return any(item is target for item in ast.walk(root))


def _call_handler_groups(node: ast.FunctionDef, call: ast.Call) -> list[list[ast.ExceptHandler]]:
    """Return enclosing handlers from the innermost protected suite outward."""
    groups: list[list[ast.ExceptHandler]] = []
    candidates = [item for item in ast.walk(node) if isinstance(item, ast.Try)]
    protected = [item for item in candidates if any(_contains(statement, call) for statement in item.body)]
    protected.sort(
        key=lambda item: (
            item.end_lineno - item.lineno,
            item.end_col_offset - item.col_offset,
        )
    )
    groups.extend(item.handlers for item in protected)
    return groups


def _call_context_managers(node: ast.FunctionDef, call: ast.Call) -> list[ast.With | ast.AsyncWith]:
    """Return context managers whose suites contain this exact call site."""
    candidates = [
        item for item in ast.walk(node) if isinstance(item, (ast.With, ast.AsyncWith))
    ]
    return [item for item in candidates if any(_contains(statement, call) for statement in item.body)]


def filter_call_exception(
    path: str,
    tree: ast.Module,
    caller: ast.FunctionDef,
    call: ast.Call,
    claim: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]]]:
    """Decide whether a known callee exception escapes local caller handlers.

    Returns whether to keep the propagated claim, any matching boundaries, and
    handler spans that explain the decision or its uncertainty.
    """
    exception = claim["statement"].get("exception", {})
    if exception.get("kind") == "builtin_exception":
        names: tuple[str, ...] | None = (exception["name"],)
    elif exception.get("kind") == "caught_exception" and exception.get("handler_types"):
        names = tuple(exception["handler_types"])
    else:
        names = None
    boundaries: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    call_span = _span(path, call).as_dict()
    builtin_names = _builtin_names(tree, caller)
    for manager in _call_context_managers(caller, call):
        boundary = {
            "id": (
                f"exception-boundary-{call.lineno}-{call.col_offset}"
                f"-{manager.lineno}-{manager.col_offset}-context-manager"
            ),
            "kind": "exception_matching",
            "target": {"text": ", ".join(source_expression(item.context_expr) for item in manager.items)},
            "reason": "A context manager may suppress the exception propagated from the local callee.",
            "category": "important",
            "source_span": _span(path, manager).as_dict(),
        }
        boundaries.append(boundary)
        spans.append(boundary["source_span"])
    for handlers in _call_handler_groups(caller, call):
        for index, handler in enumerate(handlers):
            handler_types = _handler_types(handler, builtin_names)
            if handler_types == ("*",):
                return False, boundaries, [_span(path, handler).as_dict()]
            if names is None or handler_types is None:
                later_types = [
                    _handler_types(item, builtin_names)
                    for item in handlers[index + 1 :]
                ]
                if ("*",) in later_types or (
                    names is not None
                    and any(
                        item is not None and _matches(names, item)
                        for item in later_types
                    )
                ):
                    return False, boundaries, [
                        _span(path, item).as_dict() for item in handlers[index:]
                    ]
                boundary = _boundary(path, call_span, handler)
                boundary["reason"] = (
                    "Saga cannot determine whether this handler catches the exception "
                    "propagated from the local callee."
                )
                boundaries.append(boundary)
                spans.append(_span(path, handler).as_dict())
                return True, boundaries, spans
            if _matches(names, handler_types):
                return False, boundaries, [_span(path, handler).as_dict()]
            spans.append(_span(path, handler).as_dict())
    return True, boundaries, spans
