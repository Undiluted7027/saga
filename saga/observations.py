"""Fixed-template evaluation of pytest observations."""

from __future__ import annotations

import json
from typing import Any

from .trace import contains_unusable_value


def _number(value: Any) -> bool:
    """Return whether a value is a finite numeric observation, excluding booleans."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fingerprint(value: Any) -> str:
    """Build a stable input identity from the serialized value."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _domain(values: list[Any]) -> dict[str, Any]:
    """Summarize a bounded observed domain without claiming an unobserved range."""
    unique = list(dict.fromkeys(_fingerprint(value) for value in values))
    if values and all(_number(value) for value in values):
        return {"kind": "numeric", "min": min(values), "max": max(values), "distinct": len(unique)}
    examples = []
    seen: set[str] = set()
    for value in values:
        fingerprint = _fingerprint(value)
        if fingerprint not in seen:
            seen.add(fingerprint)
            examples.append(value)
    return {"kind": "observed_values", "distinct": len(unique), "examples": examples[:4]}


def evaluate_observations(card: dict[str, Any], trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate the fixed non-negative numeric-return template over a trace."""
    successful = [item for item in trace.get("executions", []) if item.get("outcome") == "return" and _number(item.get("return"))]
    if not successful or any(contains_unusable_value(item.get("input", {})) for item in successful):
        return []
    if any(item["return"] < 0 for item in successful):
        return []
    distinct: dict[str, dict[str, Any]] = {}
    for item in successful:
        distinct.setdefault(_fingerprint(item["input"]), item)
    returns = [item["return"] for item in distinct.values()]
    if len(distinct) < 2 or len(set(returns)) < 2:
        return []
    input_names = sorted({name for item in distinct.values() for name in item["input"]})
    input_domain = {name: _domain([item["input"].get(name) for item in distinct.values()]) for name in input_names}
    tests = sorted({item["test_id"] for item in successful})
    return [{
        "id": "observation-numeric-return-nonnegative",
        "kind": "test_observation",
        "statement": {"text": f"Observed non-negative numeric returns across {len(distinct)} distinct inputs.", "type": "test_observation", "template": "numeric_return_non_negative"},
        "evidence": {
            "method": "pytest_boundary_trace",
            "evidence_class": "observed",
            "detail": {
                "template": "numeric_return_non_negative",
                "support": len(distinct),
                "supporting_tests": tests,
                "input_domain": input_domain,
                "return_domain": _domain(returns),
                "raised_executions": sum(item.get("outcome") == "raise" for item in trace.get("executions", [])),
            },
        },
        "source_spans": [card["target"]["source_span"]],
        "assumptions": [{"text": "This is observed only for the recorded test inputs and environment; it is not a proof for other executions."}],
        "boundary_ids": [],
    }]
