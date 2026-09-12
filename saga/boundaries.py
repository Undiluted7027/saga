"""Presentation-only grouping for raw analysis boundaries."""

from __future__ import annotations

import ast
from typing import Any

LOCAL_KINDS = {
    "local_call_limit",
    "unsupported_local_callee",
    "ambiguous_local_callee",
}
DYNAMIC_KINDS = {"dynamic_dispatch", "assignment_hooks"}
EXCEPTION_KINDS = {"exception_dispatch", "exception_matching"}
ROUTINE_BUILTINS = {
    "abs", "all", "any", "bool", "dict", "enumerate", "float", "int",
    "isinstance", "len", "list", "max", "min", "range", "round", "set",
    "sorted", "str", "sum", "tuple", "zip",
}
ROUTINE_METHODS = {"get", "items", "keys", "values"}


def call_category(node: ast.Call) -> str:
    """Classify familiar unresolved routines without claiming their semantics."""
    if isinstance(node.func, ast.Name) and node.func.id in ROUTINE_BUILTINS:
        return "routine"
    if isinstance(node.func, ast.Attribute) and node.func.attr in ROUTINE_METHODS:
        return "routine"
    return "important"


def boundary_class(boundary: dict[str, Any]) -> str:
    """Give a boundary a stable developer-facing class without changing it."""
    if boundary.get("category") == "routine":
        return "routine_call"
    if boundary["kind"] in LOCAL_KINDS:
        return "module_local"
    if boundary["kind"] in DYNAMIC_KINDS:
        return "dynamic_behavior"
    if boundary["kind"] in EXCEPTION_KINDS:
        return "exception_flow"
    if boundary["kind"] == "unresolved_call":
        return "external_or_unresolved_call"
    return "unsupported_behavior"


def group_boundaries(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Group equal stop reasons while retaining every raw boundary occurrence."""
    claims_by_boundary: dict[str, set[str]] = {}
    for claim in card.get("claims", []):
        for boundary_id in claim.get("boundary_ids", []):
            claims_by_boundary.setdefault(boundary_id, set()).add(claim["kind"])

    groups: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for boundary in card.get("boundaries", []):
        key = (
            boundary["kind"],
            boundary["target"]["text"],
            boundary["reason"],
        )
        group = by_key.get(key)
        if group is None:
            group = {
                "kind": boundary["kind"],
                "target": boundary["target"]["text"],
                "reason": boundary["reason"],
                "category": boundary.get("category", "important"),
                "boundary_class": boundary_class(boundary),
                "boundary_ids": [],
                "limiting_boundary_ids": [],
                "claim_kinds": [],
                "occurrences": [],
            }
            by_key[key] = group
            groups.append(group)
        if boundary.get("category") != "routine":
            group["category"] = "important"
            group["boundary_class"] = boundary_class(
                {**boundary, "category": "important"}
            )
        group["boundary_ids"].append(boundary["id"])
        group["limiting_boundary_ids"] = sorted({
            *group["limiting_boundary_ids"],
            *boundary.get("boundary_ids", []),
        })
        group["claim_kinds"] = sorted(
            {
                *group["claim_kinds"],
                *claims_by_boundary.get(boundary["id"], set()),
            }
        )
        group["occurrences"].append(
            {
                "boundary_id": boundary["id"],
                "source_span": boundary["source_span"],
                "call_chain": boundary.get("call_chain", []),
                "boundary_ids": boundary.get("boundary_ids", []),
            }
        )
    for group in groups:
        group["count"] = len(group["occurrences"])
    return groups
