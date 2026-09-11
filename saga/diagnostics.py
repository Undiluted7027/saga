"""Presentation-only grouping for raw analysis diagnostics."""

from __future__ import annotations

from typing import Any


def _span_key(span: dict[str, Any] | None) -> tuple[Any, ...] | None:
    """Build a stable identity for an optional source span."""
    if span is None:
        return None
    return (
        span["path"],
        span["start_line"],
        span["start_column"],
        span["end_line"],
        span["end_column"],
    )


def _occurrence_key(diagnostic: dict[str, Any]) -> tuple[Any, ...]:
    """Identify one displayed source/call-chain occurrence."""
    chain = tuple(
        _span_key(link["call_site"])
        for link in diagnostic.get("call_chain", [])
    )
    return (_span_key(diagnostic.get("source_span")), chain)


def group_diagnostics(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Group equal diagnostics while preserving raw reports on the card."""
    groups: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    occurrence_keys: dict[tuple[str, str], set[tuple[Any, ...]]] = {}
    for diagnostic in card.get("diagnostics", []):
        key = (diagnostic["kind"], diagnostic["message"])
        group = by_key.get(key)
        if group is None:
            group = {
                "kind": diagnostic["kind"],
                "message": diagnostic["message"],
                "report_count": 0,
                "occurrences": [],
            }
            by_key[key] = group
            occurrence_keys[key] = set()
            groups.append(group)
        group["report_count"] += 1
        occurrence_key = _occurrence_key(diagnostic)
        if occurrence_key not in occurrence_keys[key]:
            occurrence_keys[key].add(occurrence_key)
            group["occurrences"].append({
                "source_span": diagnostic.get("source_span"),
                "call_chain": diagnostic.get("call_chain", []),
            })
    for group in groups:
        group["site_count"] = len(group["occurrences"])
    return groups
