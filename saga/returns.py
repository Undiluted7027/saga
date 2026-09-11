"""Conservative intraprocedural return-dependency slicing for Slice 4."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from .inspect import Span, _diagnostic, _span


@dataclass
class CFGNode:
    """A statement node in the small control-flow graph used by the slice."""

    node_id: int
    statement: ast.stmt
    reads: set[str]
    definitions: set[str]
    controls: list[int] = field(default_factory=list)
    successors: set[int] = field(default_factory=set)
    predecessors: set[int] = field(default_factory=set)


@dataclass
class ReturnResult:
    """Hold return claims and diagnostics produced by the intraprocedural model."""

    claims: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]


def _read_names(node: ast.AST | None) -> set[str]:
    """Collect lexically read names without inferring their runtime values."""
    if node is None:
        return set()
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)}


def _definition_names(node: ast.AST) -> set[str]:
    """Collect local names written by one supported statement."""
    targets: list[ast.AST] = []
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        targets = [node.target]
    names: set[str] = set()
    for target in targets:
        names.update(item.id for item in ast.walk(target) if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)))
    return names


def _node_reads(node: ast.stmt) -> set[str]:
    """Collect reads while excluding assignment target names."""
    reads = _read_names(node)
    if isinstance(node, ast.Assign):
        for target in node.targets:
            reads -= {item.id for item in ast.walk(target) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)}
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor)):
        target = node.target
        if isinstance(node, ast.AugAssign):
            reads.add(next(iter(target.id for target in ast.walk(target) if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Load)), "")) if isinstance(target, ast.Name) else None
        reads -= {item.id for item in ast.walk(target) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)}
    return {item for item in reads if item}


def _unsupported_diagnostics(path: str, statement: ast.stmt) -> list[dict[str, Any]]:
    """Report control or expression forms that the return model cannot represent."""
    unsupported = (ast.While, ast.AsyncFor, ast.With, ast.AsyncWith, ast.Try, ast.Match, ast.Break, ast.Continue, ast.Lambda, ast.NamedExpr)
    diagnostics: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for item in ast.walk(statement):
        if isinstance(item, unsupported):
            key = (type(item).__name__, item.lineno, item.col_offset)
            if key not in seen:
                diagnostics.append(_diagnostic("unsupported_semantics", f"{type(item).__name__} semantics are outside the Slice 4 return model.", _span(path, item)))
                seen.add(key)
    return diagnostics


class _CFGBuilder:
    """Build statement nodes and conservative branch and loop edges."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.nodes: dict[int, CFGNode] = {}
        self.next_id = 0
        self.diagnostics: list[dict[str, Any]] = []
        self.gates_next: dict[int, bool] = {}

    def node(self, statement: ast.stmt, controls: list[int]) -> int:
        """Create one CFG node with lexical reads, definitions, and controllers."""
        node_id = self.next_id
        self.next_id += 1
        self.nodes[node_id] = CFGNode(node_id, statement, _node_reads(statement), _definition_names(statement), list(controls))
        self.diagnostics.extend(_unsupported_diagnostics(self.path, statement))
        return node_id

    def edge(self, source: int, target: int) -> None:
        """Add a directed control-flow edge."""
        self.nodes[source].successors.add(target)
        self.nodes[target].predecessors.add(source)

    def block(self, statements: list[ast.stmt], controls: list[int]) -> tuple[int | None, set[int]]:
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
            if isinstance(statement, ast.Assert) or self.gates_next.get(entry, False):
                active_controls.append(entry)
        return first, exits

    def statement(self, statement: ast.stmt, controls: list[int]) -> tuple[int, set[int]]:
        """Build one statement, including branch and loop edges."""
        current = self.node(statement, controls)
        if isinstance(statement, ast.If):
            body_first, body_exits = self.block(statement.body, [*controls, current])
            else_first, else_exits = self.block(statement.orelse, [*controls, current])
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
            self.gates_next[current] = body_terminates != else_terminates
            return current, body_exits | else_exits
        if isinstance(statement, ast.For):
            body_first, body_exits = self.block(statement.body, [*controls, current])
            else_first, else_exits = self.block(statement.orelse, [*controls, current])
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
        if isinstance(statement, (ast.Return, ast.Raise)):
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


def _claim(path: str, return_node: CFGNode, included: set[int], builder: _CFGBuilder, incoming: dict[int, dict[str, set[int]]], boundaries: list[dict[str, Any]], entry_spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Build one source-linked may-affect claim for a single return statement."""
    ordered = sorted(included, key=lambda item: (builder.nodes[item].statement.lineno, builder.nodes[item].statement.col_offset))
    source_spans = [_span(path, builder.nodes[item].statement).as_dict() for item in ordered]
    dependencies: list[dict[str, Any]] = []
    for item in ordered:
        node = builder.nodes[item]
        span = _span(path, node.statement).as_dict()
        if item == return_node.node_id:
            kind = "return"
        elif node.definitions:
            kind = "definition"
        elif isinstance(node.statement, (ast.If, ast.For, ast.Assert)):
            kind = "control_predicate"
        else:
            kind = "statement"
        dependencies.append({"kind": kind, "names": sorted(node.definitions), "source_span": span})
    boundary_ids = []
    for boundary in boundaries:
        if any(_contains(span, boundary["source_span"]) for span in source_spans):
            boundary_ids.append(boundary["id"])
    return {
        "id": f"return-{return_node.statement.lineno}-{return_node.statement.col_offset}",
        "kind": "return_dependency",
        "statement": {"text": "Return may depend on the included statements.", "type": "return_dependency", "dependencies": dependencies},
        "evidence": {"method": "intraprocedural_may_affect", "evidence_class": "derived", "detail": {"return_source_span": _span(path, return_node.statement).as_dict()}},
        "source_spans": source_spans,
        "assumptions": [{"text": "The slice is intraprocedural and conservative; included statements may not affect every execution."}],
        "boundary_ids": boundary_ids,
    }


def analyze_returns(path: str, node: ast.FunctionDef, boundaries: list[dict[str, Any]]) -> ReturnResult:
    """Compute a conservative may-affect slice for every return in a function."""
    builder = _CFGBuilder(path)
    first, _ = builder.block(node.body, [])
    if first is None:
        return ReturnResult([], builder.diagnostics)
    incoming = _dataflow(builder)
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
        needed: set[int] = set()
        while worklist:
            current = worklist.pop()
            current_node = builder.nodes[current]
            for control in current_node.controls:
                if control not in included:
                    included.add(control)
                    worklist.append(control)
            for name in current_node.reads:
                if name in needed:
                    continue
                needed.add(name)
                for definition in incoming[current].get(name, set()):
                    if definition not in included:
                        included.add(definition)
                        worklist.append(definition)
        claims.append(_claim(path, return_node, included, builder, incoming, boundaries, entry_spans))
    return ReturnResult(claims, builder.diagnostics)
