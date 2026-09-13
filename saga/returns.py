"""Conservative intraprocedural return-dependency slicing for Slice 4."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from .guards import BUILTIN_EXCEPTIONS
from .inspect import Span, _diagnostic, _span
from .presentation import condition_is_compound, describe_condition, source_expression


@dataclass
class CFGNode:
    """A statement node in the small control-flow graph used by the slice."""

    node_id: int
    statement: ast.stmt
    reads: set[str]
    definitions: set[str]
    weak_definitions: set[str] = field(default_factory=set)
    controls: list[tuple[int, bool | None]] = field(default_factory=list)
    successors: set[int] = field(default_factory=set)
    predecessors: set[int] = field(default_factory=set)


@dataclass
class ReturnResult:
    """Hold return claims, boundaries, and diagnostics produced by the model."""

    claims: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    boundaries: list[dict[str, Any]] = field(default_factory=list)


_DEFERRED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _walk_current_scope(node: ast.AST):
    """Yield node and its descendants, stopping at any nested def/class/lambda body.

    A nested function, lambda, or class body does not execute along with the
    statement that contains it, so a read or write inside one must not be
    attributed to the enclosing statement or control-flow node. Calling this
    directly on a deferred-scope node yields only that node.
    """
    yield node
    if isinstance(node, _DEFERRED_SCOPES):
        return
    for child in ast.iter_child_nodes(node):
        yield from _walk_current_scope(child)


def _read_names(node: ast.AST | None) -> set[str]:
    """Collect lexically read names without inferring their runtime values."""
    if node is None:
        return set()
    return {item.id for item in _walk_current_scope(node) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)}


def _definition_names(node: ast.AST) -> set[str]:
    """Collect local names written by one supported statement."""
    targets: list[ast.AST] = []
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        targets = [node.target]
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        targets = [item.optional_vars for item in node.items if item.optional_vars]
    names: set[str] = set()
    for target in targets:
        names.update(item.id for item in ast.walk(target) if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)))
    return names


_OPAQUE_WEAK_CONTAINERS = (ast.TryStar, ast.Match)


def _write_root(node: ast.AST) -> str | None:
    """Return the root name of an attribute/subscript access chain."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _write_root(node.value)
    if isinstance(node, ast.Subscript):
        return _write_root(node.value)
    return None


def _mutating_call_roots(node: ast.AST) -> set[str]:
    """Collect receiver root names from attribute-style calls in the current scope.

    Saga does not model which methods mutate their receiver, so any call shaped
    like ``name.method(...)`` is treated as a possible weak write to ``name``.
    """
    roots: set[str] = set()
    for call in _walk_current_scope(node):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
            root = _write_root(call.func.value)
            if root is not None:
                roots.add(root)
    return roots


def _assignment_write_roots(node: ast.AST) -> set[str]:
    """Collect root names of attribute/subscript assignment targets in one statement."""
    if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return set()
    targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
    roots: set[str] = set()
    for target in targets:
        for item in ast.walk(target):
            if isinstance(item, (ast.Attribute, ast.Subscript)):
                root = _write_root(item)
                if root is not None:
                    roots.add(root)
    return roots


def _weak_definition_names(node: ast.stmt) -> set[str]:
    """Collect root names weakly written by attribute/subscript writes or mutating calls.

    A weak write does not prove that a name's value changed, so it must not
    replace prior reaching definitions the way a plain name assignment does.
    """
    if isinstance(node, _OPAQUE_WEAK_CONTAINERS):
        # This construct is represented as one coarse CFG node, so every
        # nested write in the current scope is folded into a weak definition
        # of this node instead of disappearing from the model entirely.
        names = _mutating_call_roots(node)
        for item in _walk_current_scope(node):
            if isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = list(item.targets) if isinstance(item, ast.Assign) else [item.target]
                for target in targets:
                    names.update(
                        child.id
                        for child in ast.walk(target)
                        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
                    )
                names.update(_assignment_write_roots(item))
            elif isinstance(item, (ast.For, ast.AsyncFor)):
                names.update(
                    child.id
                    for child in ast.walk(item.target)
                    if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
                )
            elif isinstance(item, ast.MatchAs) and item.name:
                names.add(item.name)
        return names
    names = _mutating_call_roots(node) | _assignment_write_roots(node)
    if isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            if item.optional_vars is not None:
                root = _write_root(item.optional_vars)
                if root is not None and not isinstance(item.optional_vars, ast.Name):
                    names.add(root)
    return names


def _node_reads(node: ast.stmt) -> set[str]:
    """Collect reads while excluding assignment target names."""
    if isinstance(node, (ast.If, ast.Assert)):
        return _read_names(node.test)
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return _read_names(node.iter)
    if isinstance(node, ast.While):
        return _read_names(node.test)
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return set().union(*(_read_names(item.context_expr) for item in node.items))
    if isinstance(node, ast.Try):
        return set()
    reads = _read_names(node)
    if isinstance(node, ast.Assign):
        for target in node.targets:
            reads -= {item.id for item in ast.walk(target) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)}
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor)):
        target = node.target
        reads -= {item.id for item in ast.walk(target) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)}
        if isinstance(node, ast.AugAssign) and isinstance(target, ast.Name):
            # Augmented assignment reads the old value before writing the new one.
            reads.add(target.id)
    return {item for item in reads if item}


def _flatten_assignment_targets(target: ast.AST) -> list[ast.AST]:
    """Split a tuple/list assignment target into its independent sub-targets."""
    if isinstance(target, (ast.Tuple, ast.List)):
        flattened: list[ast.AST] = []
        for element in target.elts:
            flattened.extend(_flatten_assignment_targets(element))
        return flattened
    return [target]


def _resolve_unresolved_targets(assignment: ast.Assign | ast.AnnAssign | ast.AugAssign) -> list[ast.AST]:
    """Find one assignment's targets Saga cannot root to a plain local name."""
    unresolved: list[ast.AST] = []
    outer_targets = list(assignment.targets) if isinstance(assignment, ast.Assign) else [assignment.target]
    for outer in outer_targets:
        for target in _flatten_assignment_targets(outer):
            if isinstance(target, (ast.Attribute, ast.Subscript)) and _write_root(target) is None:
                unresolved.append(target)
    return unresolved


def _unresolved_write_targets(node: ast.stmt) -> list[ast.AST]:
    """Find assignment targets Saga cannot root to a plain local name.

    Saga does not perform alias analysis, so a target reached only through a
    call, comparison, or other dynamic expression cannot be connected to a
    return dependency at all. Reporting nothing here would look like proof of
    independence rather than an analysis limit. A method call on a freshly
    constructed value (``Path(x).write_text(...)``) is not a write to any
    existing local name, so call receivers are deliberately not checked here.
    A coarse control-flow node (with/try/while/match) is one CFG node for its
    whole body, so nested assignments are checked in the current scope too.
    """
    if isinstance(node, _OPAQUE_WEAK_CONTAINERS):
        return [
            target
            for item in _walk_current_scope(node)
            if isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign))
            for target in _resolve_unresolved_targets(item)
        ]
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return _resolve_unresolved_targets(node)
    return []


def _unsupported_diagnostics(path: str, statement: ast.stmt) -> list[dict[str, Any]]:
    """Report control or expression forms that the return model cannot represent."""
    unsupported = (ast.While, ast.AsyncFor, ast.With, ast.AsyncWith, ast.Try, ast.TryStar, ast.Match, ast.Break, ast.Continue, ast.Lambda, ast.NamedExpr)
    diagnostics: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for item in ast.walk(statement):
        if isinstance(item, unsupported):
            key = (type(item).__name__, item.lineno, item.col_offset)
            if key not in seen:
                if isinstance(item, (ast.With, ast.AsyncWith)):
                    message = (
                        "Saga traverses this context-manager body, but does not model "
                        "__enter__, __exit__, or exception suppression."
                    )
                elif isinstance(item, ast.Try):
                    message = (
                        "Saga traverses this try statement, but does not model exact "
                        "exception transfer or a finally block overriding a return."
                    )
                elif isinstance(item, ast.While):
                    message = (
                        "Saga traverses this while statement, but does not determine "
                        "iteration counts or exact break and else behavior."
                    )
                elif isinstance(item, ast.Continue):
                    message = (
                        "Saga uses this continue to constrain later returns in the same "
                        "loop, but does not model exact loop transfer behavior."
                    )
                else:
                    message = f"{type(item).__name__} semantics are outside the Slice 4 return model."
                diagnostics.append(_diagnostic("unsupported_semantics", message, _span(path, item), "returns"))
                seen.add(key)
    return diagnostics


def _unresolved_write_boundary(path: str, target: ast.AST) -> dict[str, Any]:
    """Record a write Saga cannot root to a name as a boundary, not silence.

    Saga does not perform alias analysis, so it cannot rule out that this
    write changes a value some return depends on. The boundary is attached to
    every return in the function rather than only the ones whose current
    slice happens to reach it, since a claim of independence would need the
    alias analysis this slice deliberately does not do.
    """
    return {
        "id": f"return-boundary-{target.lineno}-{target.col_offset}-unresolved_write",
        "kind": "unsupported_semantics",
        "target": {"text": source_expression(target)},
        "reason": "Saga cannot determine which name this write may change, so it cannot rule out an effect on a return value.",
        "category": "important",
        "source_span": _span(path, target).as_dict(),
    }


Fallthrough = bool | ast.expr


def _and_fallthrough(left: Fallthrough, right: Fallthrough) -> Fallthrough:
    """Combine two requirements for reaching the next statement."""
    if left is False or right is False:
        return False
    if left is True:
        return right
    if right is True:
        return left
    return ast.BoolOp(op=ast.And(), values=[left, right])


def _or_fallthrough(left: Fallthrough, right: Fallthrough) -> Fallthrough:
    """Combine alternative ways to reach the next statement."""
    if left is True or right is True:
        return True
    if left is False:
        return right
    if right is False:
        return left
    return ast.BoolOp(op=ast.Or(), values=[left, right])


def _not_fallthrough(value: ast.expr) -> ast.expr:
    """Negate one source condition without evaluating it."""
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not):
        return value.operand
    return ast.UnaryOp(op=ast.Not(), operand=value)


def _suite_fallthrough(statements: list[ast.stmt]) -> Fallthrough:
    """Describe when a small statement suite reaches its lexical successor."""
    result: Fallthrough = True
    for statement in statements:
        result = _and_fallthrough(result, _statement_fallthrough(statement))
        if result is False:
            break
    return result


def _statement_fallthrough(statement: ast.stmt) -> Fallthrough:
    """Model only lexical exits needed to preserve post-guard conditions."""
    if isinstance(statement, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return False
    if not isinstance(statement, ast.If):
        return True
    body = _suite_fallthrough(statement.body)
    alternative = _suite_fallthrough(statement.orelse) if statement.orelse else True
    when_true = _and_fallthrough(statement.test, body)
    when_false = _and_fallthrough(_not_fallthrough(statement.test), alternative)
    result = _or_fallthrough(when_true, when_false)
    if isinstance(result, ast.expr):
        ast.copy_location(result, statement.test)
        ast.fix_missing_locations(result)
    return result


class _CFGBuilder:
    """Build statement nodes and conservative branch and loop edges."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.nodes: dict[int, CFGNode] = {}
        self.next_id = 0
        self.diagnostics: list[dict[str, Any]] = []
        # Paired with the node that discovered them, so a return only gets
        # limited by a write that can actually execute before it.
        self.write_boundaries: list[tuple[int, dict[str, Any]]] = []
        self.gates_next: dict[int, bool] = {}
        self.gate_conditions: dict[int, ast.expr] = {}

    def node(self, statement: ast.stmt, controls: list[tuple[int, bool | None]]) -> int:
        """Create one CFG node with lexical reads, definitions, and controllers."""
        node_id = self.next_id
        self.next_id += 1
        strong = _definition_names(statement)
        weak = _weak_definition_names(statement) - strong
        self.nodes[node_id] = CFGNode(node_id, statement, _node_reads(statement), strong, weak, list(controls))
        self.diagnostics.extend(_unsupported_diagnostics(self.path, statement))
        for target in _unresolved_write_targets(statement):
            self.write_boundaries.append((node_id, _unresolved_write_boundary(self.path, target)))
        return node_id

    def edge(self, source: int, target: int) -> None:
        """Add a directed control-flow edge."""
        self.nodes[source].successors.add(target)
        self.nodes[target].predecessors.add(source)

    def ancestors(self, node_id: int) -> set[int]:
        """Return CFG nodes that may execute before this node."""
        seen: set[int] = set()
        stack = list(self.nodes[node_id].predecessors)
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self.nodes[current].predecessors)
        return seen

    def block(self, statements: list[ast.stmt], controls: list[tuple[int, bool | None]]) -> tuple[int | None, set[int]]:
        """Build a block and return its entry node and fall-through exits."""
        first: int | None = None
        exits: set[int] = set()
        active_controls = list(controls)
        for statement in statements:
            entry, statement_exits = self.statement(statement, active_controls)
            if first is None:
                first = entry
            for previous in exits:
                self.edge(previous, entry)
            exits = statement_exits
            if isinstance(statement, ast.Assert):
                active_controls.append((entry, True))
            elif entry in self.gates_next:
                active_controls.append((entry, self.gates_next[entry]))
            elif isinstance(statement, ast.If):
                fallthrough = _statement_fallthrough(statement)
                if isinstance(fallthrough, ast.expr):
                    self.gate_conditions[entry] = fallthrough
                    active_controls.append((entry, True))
        return first, exits

    def statement(self, statement: ast.stmt, controls: list[tuple[int, bool | None]]) -> tuple[int, set[int]]:
        """Build one statement, including branch and loop edges."""
        current = self.node(statement, controls)
        if isinstance(statement, ast.If):
            body_first, body_exits = self.block(statement.body, [*controls, (current, True)])
            else_first, else_exits = self.block(statement.orelse, [*controls, (current, False)])
            body_terminates = body_first is not None and not body_exits
            else_terminates = else_first is not None and not else_exits
            if body_first is not None:
                self.edge(current, body_first)
            else:
                body_exits = {current}
            if else_first is not None:
                self.edge(current, else_first)
            else:
                else_exits = {current}
            if body_terminates != else_terminates:
                self.gates_next[current] = else_terminates
            return current, body_exits | else_exits
        if isinstance(statement, ast.For):
            body_first, body_exits = self.block(statement.body, [*controls, (current, None)])
            else_first, else_exits = self.block(statement.orelse, [*controls, (current, None)])
            if body_first is not None:
                self.edge(current, body_first)
                for exit_node in body_exits:
                    self.edge(exit_node, current)
            if else_first is not None:
                self.edge(current, else_first)
                for exit_node in body_exits:
                    self.edge(exit_node, else_first)
                return current, else_exits
            return current, {current}
        if isinstance(statement, ast.While):
            body_first, body_exits = self.block(
                statement.body,
                [*controls, (current, None)],
            )
            else_first, else_exits = self.block(
                statement.orelse,
                [*controls, (current, None)],
            )
            if body_first is not None:
                self.edge(current, body_first)
                for exit_node in body_exits:
                    self.edge(exit_node, current)
            if else_first is not None:
                self.edge(current, else_first)
                for exit_node in body_exits:
                    self.edge(exit_node, else_first)
                return current, else_exits
            return current, {current}
        if isinstance(statement, ast.Try):
            before_region = set(self.nodes)
            body_first, body_exits = self.block(
                statement.body,
                [*controls, (current, None)],
            )
            if body_first is not None:
                self.edge(current, body_first)
            else:
                body_exits = {current}

            if statement.orelse:
                else_first, else_exits = self.block(
                    statement.orelse,
                    [*controls, (current, None)],
                )
                if else_first is not None:
                    for exit_node in body_exits:
                        self.edge(exit_node, else_first)
                    normal_exits = else_exits
                else:
                    normal_exits = body_exits
            else:
                normal_exits = body_exits

            handler_exits: set[int] = set()
            for handler in statement.handlers:
                handler_first, exits = self.block(
                    handler.body,
                    [*controls, (current, None)],
                )
                if handler_first is not None:
                    self.edge(current, handler_first)
                handler_exits.update(exits)
            region_nodes = set(self.nodes) - before_region
            exits = normal_exits | handler_exits

            if statement.finalbody:
                before_final = set(self.nodes)
                final_first, final_exits = self.block(
                    statement.finalbody,
                    [*controls, (current, None)],
                )
                if final_first is not None:
                    # A finally suite can run after normal flow, a handler, a
                    # return, or a raise. These edges intentionally
                    # over-approximate which definitions reach it.
                    for source in {current, *region_nodes, *exits}:
                        if source != final_first:
                            self.edge(source, final_first)
                    return current, final_exits
                if set(self.nodes) != before_final:
                    return current, set()
            return current, exits
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            body_first, body_exits = self.block(
                statement.body,
                [*controls, (current, None)],
            )
            if body_first is not None:
                self.edge(current, body_first)
                return current, body_exits
            return current, {current}
        if isinstance(statement, (ast.Return, ast.Raise, ast.Continue)):
            return current, set()
        return current, {current}


def _dataflow(builder: _CFGBuilder) -> dict[int, dict[str, set[int]]]:
    """Compute reaching definitions to a fixed point over the CFG."""
    incoming: dict[int, dict[str, set[int]]] = {node_id: {} for node_id in builder.nodes}
    outgoing: dict[int, dict[str, set[int]]] = {node_id: {} for node_id in builder.nodes}
    changed = True
    while changed:
        changed = False
        for node_id, node in builder.nodes.items():
            merged: dict[str, set[int]] = {}
            for predecessor in node.predecessors:
                for name, definitions in outgoing[predecessor].items():
                    merged.setdefault(name, set()).update(definitions)
            transferred = {name: set(definitions) for name, definitions in merged.items()}
            for name in node.definitions:
                transferred[name] = {node_id}
            for name in node.weak_definitions:
                # A weak write may not execute, so it adds a possible source
                # without discarding the definitions that reached this point.
                transferred.setdefault(name, set()).add(node_id)
            if incoming[node_id] != merged or outgoing[node_id] != transferred:
                incoming[node_id] = merged
                outgoing[node_id] = transferred
                changed = True
    return incoming


def _contains(outer: dict[str, Any], inner: dict[str, Any]) -> bool:
    """Return whether one source span contains another source span."""
    if outer["path"] != inner["path"]:
        return False
    start = (outer["start_line"], outer["start_column"])
    end = (outer["end_line"], outer["end_column"])
    inner_start = (inner["start_line"], inner["start_column"])
    inner_end = (inner["end_line"], inner["end_column"])
    return start <= inner_start and inner_end <= end


def _call_details(path: str, node: ast.AST) -> list[dict[str, Any]]:
    """Describe calls in a statement without pretending to resolve their bodies."""
    calls = []
    roots: list[ast.AST]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        roots = [item.context_expr for item in node.items]
    elif isinstance(node, ast.Try):
        roots = []
    elif isinstance(node, ast.While):
        roots = [node.test]
    else:
        roots = [node]
    for root in roots:
        for call in ast.walk(root):
            if not isinstance(call, ast.Call):
                continue
            if isinstance(call.func, ast.Name) and call.func.id in BUILTIN_EXCEPTIONS:
                continue
            calls.append({"text": f"{ast.unparse(call.func)}(...)", "source_span": _span(path, call).as_dict()})
    return calls


def _call_dependency_names(call: ast.Call) -> set[str]:
    """Return receiver and argument names an opaque call could affect.

    The callee name itself is deliberately excluded. Without call semantics,
    Saga can only connect the boundary to a return through values supplied to
    the call or through the receiver of an attribute call.
    """
    names: set[str] = set()
    if isinstance(call.func, ast.Attribute):
        receiver = _write_root(call.func.value)
        if receiver is not None:
            names.add(receiver)
    for argument in call.args:
        names.update(_read_names(argument))
    for keyword in call.keywords:
        names.update(_read_names(keyword.value))
    return names


def _opaque_call_boundary_ids(
    path: str,
    return_node: CFGNode,
    included: set[int],
    builder: _CFGBuilder,
    boundaries: list[dict[str, Any]],
) -> set[str]:
    """Find unresolved calls that may affect the selected return slice."""
    slice_names: set[str] = set()
    for node_id in included:
        node = builder.nodes[node_id]
        slice_names.update(node.reads)
        slice_names.update(node.definitions)
        slice_names.update(node.weak_definitions)
    if not slice_names:
        return set()

    unresolved_by_span: dict[tuple[tuple[str, Any], ...], list[str]] = {}
    for boundary in boundaries:
        if boundary["kind"] != "unresolved_call":
            continue
        key = tuple(sorted(boundary["source_span"].items()))
        unresolved_by_span.setdefault(key, []).append(boundary["id"])

    relevant: set[str] = set()
    reachable = builder.ancestors(return_node.node_id) | {return_node.node_id}
    calls: dict[tuple[tuple[str, Any], ...], tuple[ast.Call, set[int]]] = {}
    for node_id in reachable:
        for item in _walk_current_scope(builder.nodes[node_id].statement):
            if isinstance(item, ast.Call):
                key = tuple(sorted(_span(path, item).as_dict().items()))
                call, owners = calls.setdefault(key, (item, set()))
                owners.add(node_id)
    for key, (call, owners) in calls.items():
        if key not in unresolved_by_span:
            continue
        # Nested statements also lie inside their coarse parent span. The node
        # with the latest source start is the narrowest CFG owner of the call.
        owner = max(
            owners,
            key=lambda node_id: (
                builder.nodes[node_id].statement.lineno,
                builder.nodes[node_id].statement.col_offset,
            ),
        )
        result_is_in_slice = owner in included
        shares_slice_value = bool(_call_dependency_names(call) & slice_names)
        if result_is_in_slice or shares_slice_value:
            relevant.update(unresolved_by_span[key])
    return relevant


def _claim(path: str, return_node: CFGNode, included: set[int], builder: _CFGBuilder, boundaries: list[dict[str, Any]], parameter_names: set[str]) -> dict[str, Any]:
    """Build one source-linked may-affect claim for a single return statement."""
    ordered = sorted(included, key=lambda item: (builder.nodes[item].statement.lineno, builder.nodes[item].statement.col_offset))
    source_spans = [_span(path, builder.nodes[item].statement).as_dict() for item in ordered]
    dependencies: list[dict[str, Any]] = []
    reads = set().union(*(builder.nodes[item].reads for item in ordered))
    definitions = set().union(*(builder.nodes[item].definitions for item in ordered))
    weak_definitions = set().union(*(builder.nodes[item].weak_definitions for item in ordered))
    calls = [call for item in ordered for call in _call_details(path, builder.nodes[item].statement)]
    call_keys: set[tuple[str, tuple[tuple[str, Any], ...]]] = set()
    unique_calls = []
    for call in calls:
        key = (call["text"], tuple(sorted(call["source_span"].items())))
        if key not in call_keys:
            call_keys.add(key)
            unique_calls.append(call)
    for item in ordered:
        node = builder.nodes[item]
        span = _span(path, node.statement).as_dict()
        if item == return_node.node_id:
            kind = "return"
        elif node.definitions:
            kind = "definition"
        elif node.weak_definitions:
            kind = "weak_definition"
        elif isinstance(node.statement, (ast.If, ast.For, ast.Assert)):
            kind = "control_predicate"
        else:
            kind = "statement"
        names = sorted(node.definitions | node.weak_definitions)
        dependencies.append({"kind": kind, "names": names, "reads": sorted(node.reads), "calls": _call_details(path, node.statement), "source_span": span})
    boundary_ids = []
    for boundary in boundaries:
        if boundary["kind"] == "unresolved_call":
            # Opaque calls are attached below using exact AST ownership and
            # slice-name overlap. A coarse statement span alone is not enough.
            continue
        if any(_contains(span, boundary["source_span"]) for span in source_spans):
            boundary_ids.append(boundary["id"])
    inputs = sorted(reads & parameter_names)
    path_conditions = []
    compound_conditions: list[bool] = []
    path_assumptions: list[dict[str, str]] = []
    seen_conditions: set[tuple[int, bool]] = set()
    for control_id, expected in return_node.controls:
        control = builder.nodes[control_id].statement
        if expected is None or not isinstance(control, (ast.If, ast.Assert)):
            continue
        key = (control_id, expected)
        if key in seen_conditions:
            continue
        seen_conditions.add(key)
        test = builder.gate_conditions.get(control_id, control.test)
        path_conditions.append({
            "text": describe_condition(test, expected),
            "expected": expected,
            "source_text": source_expression(test),
            "source_span": _span(path, test).as_dict(),
        })
        compound_conditions.append(condition_is_compound(test))
        if isinstance(control, ast.Assert):
            path_assumptions.append({"text": "This return path assumes __debug__ is true; Python may remove the assertion under optimization."})
        if any(isinstance(item, ast.Compare) for item in ast.walk(test)):
            path_assumptions.append({"text": "Comparison path wording follows the modeled Python semantics; overloaded comparisons are not resolved."})
    returned = source_expression(return_node.statement.value)
    rendered_conditions = [
        f"({item['text']})" if len(path_conditions) > 1 and compound else item["text"]
        for item, compound in zip(path_conditions, compound_conditions)
    ]
    path_suffix = " when " + " and ".join(rendered_conditions) if rendered_conditions else ""
    summary = f"Returns {returned}{path_suffix}."
    assumptions = [{"text": "The slice is intraprocedural and conservative; included statements may not affect every execution."}, *path_assumptions]
    if weak_definitions:
        assumptions.append({
            "text": (
                "Names changed only through an attribute, subscript, or method call are "
                "included as a conservative over-approximation; Saga does not prove the "
                "write executed or that it changed the value."
            )
        })
    return {
        "id": f"return-{return_node.statement.lineno}-{return_node.statement.col_offset}",
        "kind": "return_dependency",
        "statement": {
            "text": summary,
            "type": "return_dependency",
            "source_text": returned,
            "return_expression": returned,
            "path_conditions": path_conditions,
            "inputs": inputs,
            "definitions": sorted(definitions),
            "weak_definitions": sorted(weak_definitions),
            "calls": unique_calls,
            "dependencies": dependencies,
        },
        "evidence": {"method": "intraprocedural_may_affect", "evidence_class": "derived", "detail": {"return_source_span": _span(path, return_node.statement).as_dict()}},
        "source_spans": source_spans,
        "assumptions": assumptions,
        "boundary_ids": boundary_ids,
    }


def analyze_returns(path: str, node: ast.FunctionDef, boundaries: list[dict[str, Any]]) -> ReturnResult:
    """Compute a conservative may-affect slice for every return in a function."""
    builder = _CFGBuilder(path)
    first, _ = builder.block(node.body, [])
    if first is None:
        return ReturnResult([], builder.diagnostics)
    incoming = _dataflow(builder)
    parameter_names = {argument.arg for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
    if node.args.vararg:
        parameter_names.add(node.args.vararg.arg)
    if node.args.kwarg:
        parameter_names.add(node.args.kwarg.arg)
    entry_spans: list[dict[str, Any]] = []
    for statement in node.body:
        if isinstance(statement, ast.Assert) or (isinstance(statement, ast.If) and not statement.orelse and len(statement.body) == 1 and isinstance(statement.body[0], ast.Raise)):
            entry_spans.append(_span(path, statement).as_dict())
            continue
        if isinstance(statement, (ast.Expr, ast.Pass)):
            continue
        break
    claims: list[dict[str, Any]] = []
    for return_node in [item for item in builder.nodes.values() if isinstance(item.statement, ast.Return)]:
        included: set[int] = {return_node.node_id}
        worklist = [return_node.node_id]
        for item_id, item in builder.nodes.items():
            if any(_contains(entry_span, _span(path, item.statement).as_dict()) for entry_span in entry_spans):
                included.add(item_id)
                worklist.append(item_id)
        visited_reads: set[tuple[int, str]] = set()
        while worklist:
            current = worklist.pop()
            current_node = builder.nodes[current]
            for control, _ in current_node.controls:
                if control not in included:
                    included.add(control)
                    worklist.append(control)
            for name in current_node.reads:
                read = (current, name)
                if read in visited_reads:
                    continue
                visited_reads.add(read)
                for definition in incoming[current].get(name, set()):
                    if definition not in included:
                        included.add(definition)
                        worklist.append(definition)
        claim = _claim(path, return_node, included, builder, boundaries, parameter_names)
        opaque_call_ids = _opaque_call_boundary_ids(path, return_node, included, builder, boundaries)
        if opaque_call_ids:
            claim["boundary_ids"] = sorted({*claim["boundary_ids"], *opaque_call_ids})
        if builder.write_boundaries:
            reachable = builder.ancestors(return_node.node_id)
            relevant_ids = {
                boundary["id"]
                for write_node_id, boundary in builder.write_boundaries
                if write_node_id in reachable
            }
            if relevant_ids:
                claim["boundary_ids"] = sorted({*claim["boundary_ids"], *relevant_ids})
        claims.append(claim)
    return ReturnResult(claims, builder.diagnostics, [boundary for _, boundary in builder.write_boundaries])
