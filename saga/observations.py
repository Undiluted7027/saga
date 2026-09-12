"""Fixed-template evaluation of pytest observations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .trace import contains_unusable_value


@dataclass(frozen=True)
class ObservationResult:
    """Keep behavioral claims separate from the status of template evaluation."""

    claims: list[dict[str, Any]]
    status: dict[str, Any]


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


def _unusable_metadata(value: Any) -> tuple[set[str], set[str]]:
    """Collect only safe marker kinds and type names from an unusable value."""
    kinds: set[str] = set()
    types: set[str] = set()
    if isinstance(value, dict):
        kind = value.get("kind")
        if kind in {"unsupported", "redacted", "truncated", "non_finite"}:
            kinds.add(kind)
            if isinstance(value.get("type"), str):
                types.add(value["type"])
        for item in value.values():
            child_kinds, child_types = _unusable_metadata(item)
            kinds.update(child_kinds)
            types.update(child_types)
    elif isinstance(value, list):
        for item in value:
            child_kinds, child_types = _unusable_metadata(item)
            kinds.update(child_kinds)
            types.update(child_types)
    return kinds, types


def _input_evidence(
    returned: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Project returned executions onto parameters safe enough to fingerprint."""
    names = sorted({name for item in returned for name in item.get("input", {})})
    excluded: list[dict[str, Any]] = []
    excluded_names: set[str] = set()
    for name in names:
        values = [item.get("input", {}).get(name) for item in returned]
        if not any(contains_unusable_value(value) for value in values):
            continue
        kinds: set[str] = set()
        types: set[str] = set()
        for value in values:
            value_kinds, value_types = _unusable_metadata(value)
            kinds.update(value_kinds)
            types.update(value_types)
        excluded_names.add(name)
        excluded.append({
            "name": name,
            "serialization_kinds": sorted(kinds),
            "types": sorted(types),
        })

    projected = [
        {
            name: value
            for name, value in item.get("input", {}).items()
            if name not in excluded_names
        }
        for item in returned
    ]
    domain = {
        name: _domain([item.get("input", {}).get(name) for item in returned])
        for name in names
        if name not in excluded_names
    }
    for item in excluded:
        domain[item["name"]] = {
            "kind": "excluded",
            "serialization_kinds": item["serialization_kinds"],
            "types": item["types"],
        }
    return projected, excluded, domain


def _status(
    trace: dict[str, Any],
    reason: str,
    message: str,
    distinct_inputs: int = 0,
    distinct_outputs: int = 0,
    excluded_parameters: list[dict[str, Any]] | None = None,
    input_domain: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe one template-evaluation result without turning it into a claim."""
    executions = trace.get("executions", [])
    returned = [item for item in executions if item.get("outcome") == "return"]
    return {
        "state": "claim_produced" if reason == "supported" else "no_claim",
        "template": "numeric_return_non_negative",
        "reason": reason,
        "message": message,
        "execution_count": len(executions),
        "returned_executions": len(returned),
        "raised_executions": sum(
            item.get("outcome") == "raise" for item in executions
        ),
        "distinct_inputs": distinct_inputs,
        "distinct_outputs": distinct_outputs,
        "excluded_parameters": excluded_parameters or [],
        "input_domain": input_domain or {},
        "tests": sorted({
            item.get("test_id", "<unknown>") for item in executions
        }),
        "environment": trace.get("environment", {}),
    }


def evaluate_observations(
    card: dict[str, Any],
    trace: dict[str, Any],
) -> ObservationResult:
    """Evaluate the fixed non-negative numeric-return template over a trace."""
    executions = trace.get("executions", [])
    if not executions:
        return ObservationResult([], _status(
            trace,
            "no_recorded_executions",
            "Tests completed, but no execution of the selected function was recorded.",
        ))
    returned = [item for item in executions if item.get("outcome") == "return"]
    if not returned:
        return ObservationResult([], _status(
            trace,
            "no_successful_returns",
            "Tests reached the selected function, but every recorded execution raised.",
        ))
    projected_inputs, excluded_parameters, input_domain = _input_evidence(returned)
    if any(contains_unusable_value(item.get("return")) for item in returned):
        return ObservationResult([], _status(
            trace,
            "unusable_serialized_values",
            "Saga could not evaluate observations because a recorded return was redacted, truncated, non-finite, or unsupported.",
            excluded_parameters=excluded_parameters,
            input_domain=input_domain,
        ))
    if not all(_number(item.get("return")) for item in returned):
        return ObservationResult([], _status(
            trace,
            "unsupported_return_shape",
            "Tests recorded returns, but no current observation template supports their serialized shape.",
            excluded_parameters=excluded_parameters,
            input_domain=input_domain,
        ))
    if any(item["return"] < 0 for item in returned):
        return ObservationResult([], _status(
            trace,
            "template_contradicted",
            "At least one negative return contradicted the non-negative numeric template, so Saga produced no observation claim.",
            excluded_parameters=excluded_parameters,
            input_domain=input_domain,
        ))
    distinct: dict[str, dict[str, Any]] = {}
    for item, projected_input in zip(returned, projected_inputs, strict=True):
        distinct.setdefault(_fingerprint(projected_input), item)
    returns = [item["return"] for item in distinct.values()]
    if len(distinct) < 2:
        excluded_names = ", ".join(
            item["name"] for item in excluded_parameters
        )
        exclusion = (
            f" after excluding {excluded_names}"
            if excluded_names
            else ""
        )
        return ObservationResult([], _status(
            trace,
            "insufficient_distinct_inputs",
            f"Saga recorded fewer than two distinct usable inputs{exclusion}, so the template has too little support.",
            distinct_inputs=len(distinct),
            distinct_outputs=len(set(returns)),
            excluded_parameters=excluded_parameters,
            input_domain=input_domain,
        ))
    if len(set(returns)) < 2:
        return ObservationResult([], _status(
            trace,
            "insufficient_distinct_outputs",
            "Distinct inputs produced fewer than two distinct returns, so Saga did not emit the numeric observation claim.",
            distinct_inputs=len(distinct),
            distinct_outputs=len(set(returns)),
            excluded_parameters=excluded_parameters,
            input_domain=input_domain,
        ))
    tests = sorted({item["test_id"] for item in returned})
    claims = [{
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
                "raised_executions": sum(item.get("outcome") == "raise" for item in executions),
                "environment": trace.get("environment", {}),
            },
        },
        "source_spans": [card["target"]["source_span"]],
        "assumptions": [{"text": "This is observed only for the recorded test inputs and environment; it is not a proof for other executions."}],
        "boundary_ids": [],
    }]
    return ObservationResult(claims, _status(
        trace,
        "supported",
        "Saga produced an observed claim from the recorded executions.",
        distinct_inputs=len(distinct),
        distinct_outputs=len(set(returns)),
        excluded_parameters=excluded_parameters,
        input_domain=input_domain,
    ))
