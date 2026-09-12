"""Presentation partitions for evidence propagated from module-local calls."""

from __future__ import annotations

from typing import Any


def _span_key(span: dict[str, Any]) -> tuple[Any, ...]:
    """Return a stable key for one source span."""
    return (
        span["path"],
        span["start_line"],
        span["start_column"],
        span["end_line"],
        span["end_column"],
    )


def partition_local_call_evidence(card: dict[str, Any]) -> dict[str, Any]:
    """Separate direct evidence from evidence grouped by its first local call."""
    direct = {"claims": [], "boundaries": [], "diagnostics": []}
    groups: list[dict[str, Any]] = []
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}

    for collection in ("claims", "boundaries", "diagnostics"):
        for item in card.get(collection, []):
            chain = item.get("call_chain", [])
            if not chain:
                direct[collection].append(item)
                continue
            link = chain[0]
            key = (
                link["caller"],
                link["callee"],
                link.get("invoked_as", link["callee"]),
                _span_key(link["call_site"]),
            )
            group = by_key.get(key)
            if group is None:
                group = {
                    "caller": link["caller"],
                    "callee": link["callee"],
                    "invoked_as": link.get("invoked_as", link["callee"]),
                    "call_site": link["call_site"],
                    "callee_span": link["callee_span"],
                    "argument_bindings": link.get("argument_bindings", []),
                    "claims": [],
                    "boundaries": [],
                    "diagnostics": [],
                }
                by_key[key] = group
                groups.append(group)
            group[collection].append(item)

    groups.sort(
        key=lambda group: (
            *_span_key(group["call_site"]),
            group["caller"],
            group["callee"],
        )
    )
    return {"direct": direct, "groups": groups}
