"""Presentation helpers for large return-dependency claims."""

from __future__ import annotations

from typing import Any


RETURN_SITE_LIMIT = 8
RETURN_DEPENDENCY_KINDS = (
    "return",
    "definition",
    "weak_definition",
    "control_predicate",
    "statement",
)


def return_path_presentation(claim: dict[str, Any]) -> dict[str, Any]:
    """Group existing return dependencies without changing the claim."""
    dependencies = claim.get("statement", {}).get("dependencies", [])
    by_kind = {kind: [] for kind in RETURN_DEPENDENCY_KINDS}
    extra_kinds: list[str] = []
    for dependency in dependencies:
        kind = dependency.get("kind", "statement")
        if kind not in by_kind:
            by_kind[kind] = []
            extra_kinds.append(kind)
        by_kind[kind].append(dependency)

    groups = []
    for kind in (*RETURN_DEPENDENCY_KINDS, *extra_kinds):
        entries = by_kind[kind]
        if not entries:
            continue
        groups.append(
            {
                "kind": kind,
                "count": len(entries),
                "names": sorted(
                    {name for item in entries for name in item.get("names", [])}
                ),
                "reads": sorted(
                    {name for item in entries for name in item.get("reads", [])}
                ),
                "calls": sorted(
                    {
                        call["text"]
                        for item in entries
                        for call in item.get("calls", [])
                    }
                ),
                "entries": entries,
            }
        )

    return_span = claim.get("evidence", {}).get("detail", {}).get(
        "return_source_span"
    )
    if return_span is None:
        return_entry = next(
            (item for item in dependencies if item.get("kind") == "return"),
            None,
        )
        return_span = (
            return_entry.get("source_span")
            if return_entry
            else claim.get("source_spans", [None])[-1]
        )
    return {
        "compact": len(dependencies) > RETURN_SITE_LIMIT,
        "site_count": len(dependencies),
        "return_expression": claim.get("statement", {}).get(
            "return_expression",
            claim.get("statement", {}).get("source_text", "?"),
        ),
        "path_conditions": claim.get("statement", {}).get("path_conditions", []),
        "return_span": return_span,
        "groups": groups,
        "dependencies": dependencies,
        "source_spans": claim.get("source_spans", []),
    }
