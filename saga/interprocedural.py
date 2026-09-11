"""One-hop composition of evidence from module-local functions."""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .effects import analyze_effects
from .exceptions import analyze_exceptions, filter_call_exception
from .guards import analyze_guards
from .inspect import _span
from .local_calls import LocalCallResolution, call_record, resolve_local_calls
from .returns import analyze_returns


@dataclass
class FunctionEvidence:
    """Hold the evidence produced for one function and its allowed callees."""

    claims: list[dict[str, Any]]
    boundaries: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]


def _span_key(span: dict[str, Any]) -> tuple[Any, ...]:
    """Build a stable key for matching call and boundary locations."""
    return (
        span["path"],
        span["start_line"],
        span["start_column"],
        span["end_line"],
        span["end_column"],
    )


def _deduplicate_boundaries(boundaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate analyzer reports for the same kind and source span."""
    result = []
    seen: set[tuple[Any, ...]] = set()
    for boundary in boundaries:
        chain = tuple(_span_key(item["call_site"]) for item in boundary.get("call_chain", []))
        key = (boundary["kind"], _span_key(boundary["source_span"]), chain)
        if key not in seen:
            seen.add(key)
            result.append(boundary)
        else:
            retained = next(
                item
                for item in result
                if (
                    item["kind"],
                    _span_key(item["source_span"]),
                    tuple(
                        _span_key(link["call_site"])
                        for link in item.get("call_chain", [])
                    ),
                ) == key
            )
            concerns = sorted({
                *retained.get("concerns", []),
                *boundary.get("concerns", []),
            })
            if concerns:
                retained["concerns"] = concerns
            if (
                "effects" in boundary.get("concerns", [])
                and "cannot determine whether this call mutates state"
                not in retained["reason"]
            ):
                retained["reason"] += (
                    " Saga cannot determine whether this call mutates state or "
                    "causes an external effect."
                )
    return result


def _stopping_boundary(
    path: str,
    caller: ast.FunctionDef,
    resolution: LocalCallResolution,
) -> dict[str, Any]:
    """Explain why a module-local call was not followed."""
    reasons = {
        "recursive": "Following this call would recurse into a function already in the local call chain.",
        "hop_limit": "The call is module-local, but it is beyond Saga's one-hop analysis limit.",
        "unsupported": "The module-local callee uses a function form that Saga does not support.",
        "ambiguous": "The call name has more than one module-level binding, so Saga cannot choose a callee.",
        "shadowed": "The call name is bound in the caller, so Saga cannot treat it as the module-level function.",
    }
    kinds = {
        "recursive": "local_call_limit",
        "hop_limit": "local_call_limit",
        "unsupported": "unsupported_local_callee",
        "ambiguous": "ambiguous_local_callee",
        "shadowed": "unresolved_call",
    }
    boundary = {
        "id": f"local-boundary-{resolution.call.lineno}-{resolution.call.col_offset}-{resolution.status}",
        "kind": kinds[resolution.status],
        "target": {"text": f"{resolution.invoked_as}(...)"},
        "reason": reasons[resolution.status],
        "category": "important",
        "source_span": _span(path, resolution.call).as_dict(),
    }
    if resolution.callee is not None:
        boundary["call_chain"] = [call_record(path, caller, resolution)]
    return boundary


def _rewrite_call_boundaries(
    path: str,
    caller: ast.FunctionDef,
    boundaries: list[dict[str, Any]],
    resolutions: list[LocalCallResolution],
) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
    """Remove resolved-call opacity and replace stopped calls with precise boundaries."""
    by_span = {_span_key(_span(path, item.call).as_dict()): item for item in resolutions}
    result = []
    boundary_ids: dict[str, str | None] = {}
    replacements: dict[tuple[Any, ...], str] = {}
    for boundary in boundaries:
        key = _span_key(boundary["source_span"])
        resolution = by_span.get(key)
        if boundary["kind"] != "unresolved_call" or resolution is None or resolution.status == "external":
            result.append(boundary)
            boundary_ids[boundary["id"]] = boundary["id"]
            continue
        if resolution.status == "resolved":
            boundary_ids[boundary["id"]] = None
            continue
        if key not in replacements:
            replacement = _stopping_boundary(path, caller, resolution)
            if boundary.get("concerns"):
                replacement["concerns"] = list(boundary["concerns"])
                if "effects" in boundary["concerns"]:
                    replacement["reason"] += (
                        " Saga cannot determine whether this call mutates state or "
                        "causes an external effect."
                    )
            result.append(replacement)
            replacements[key] = replacement["id"]
        elif boundary.get("concerns"):
            replacement = next(
                item for item in result if item["id"] == replacements[key]
            )
            replacement["concerns"] = sorted({
                *replacement.get("concerns", []),
                *boundary["concerns"],
            })
            if (
                "effects" in boundary["concerns"]
                and "cannot determine whether this call mutates state"
                not in replacement["reason"]
            ):
                replacement["reason"] += (
                    " Saga cannot determine whether this call mutates state or "
                    "causes an external effect."
                )
        boundary_ids[boundary["id"]] = replacements[key]
    for resolution in resolutions:
        if resolution.status in {"resolved", "external"}:
            continue
        key = _span_key(_span(path, resolution.call).as_dict())
        if key not in replacements:
            replacement = _stopping_boundary(path, caller, resolution)
            result.append(replacement)
            replacements[key] = replacement["id"]
    return _deduplicate_boundaries(result), boundary_ids


def _remap_claim_boundaries(claims: list[dict[str, Any]], boundary_ids: dict[str, str | None]) -> None:
    """Keep claim limits aligned with rewritten call boundaries."""
    for claim in claims:
        claim["boundary_ids"] = [
            boundary_ids.get(item, item)
            for item in claim["boundary_ids"]
            if boundary_ids.get(item, item) is not None
        ]


def _assigned_names_containing_call(
    caller: ast.FunctionDef,
    call: ast.Call,
) -> set[str]:
    """Find names assigned by a statement that lexically contains this call."""
    names: set[str] = set()
    for item in ast.walk(caller):
        if not isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        if not any(child is call for child in ast.walk(item)):
            continue
        targets = item.targets if isinstance(item, ast.Assign) else [item.target]
        for target in targets:
            names.update(
                child.id
                for child in ast.walk(target)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
            )
    return names


def _call_is_in_return(
    path: str,
    caller: ast.FunctionDef,
    call: ast.Call,
    return_claims: list[dict[str, Any]],
) -> bool:
    """Return whether a call may supply a value used by one caller return."""
    call_span = _span_key(_span(path, call).as_dict())
    if any(
        _span_key(item["source_span"]) == call_span
        for claim in return_claims
        for item in claim["statement"].get("calls", [])
    ):
        return True

    # Unsupported compound statements are currently represented as one coarse
    # CFG node. Preserve a conservative connection when a nested call assigns a
    # name that the supported part of the return slice reads or defines.
    assigned = _assigned_names_containing_call(caller, call)
    return bool(assigned) and any(
        assigned
        & {
            name
            for dependency in claim["statement"].get("dependencies", [])
            for name in [*dependency.get("reads", []), *dependency.get("names", [])]
        }
        for claim in return_claims
    )


def _caller_parameters(node: ast.FunctionDef) -> set[str]:
    """Return every parameter name available at the caller boundary."""
    parameters = {
        item.arg
        for item in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    }
    if node.args.vararg:
        parameters.add(node.args.vararg.arg)
    if node.args.kwarg:
        parameters.add(node.args.kwarg.arg)
    return parameters


def _caller_inputs_for_argument(
    caller: ast.FunctionDef,
    call: ast.Call,
    argument: ast.AST,
    caller_parameters: set[str],
) -> list[str]:
    """Trace a call argument through direct inputs and enclosing loop/control inputs."""
    inputs = {
        item.id
        for item in ast.walk(argument)
        if isinstance(item, ast.Name)
        and isinstance(item.ctx, ast.Load)
        and item.id in caller_parameters
    }
    argument_names = {
        item.id
        for item in ast.walk(argument)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
    }
    for container in ast.walk(caller):
        if not any(item is call for item in ast.walk(container)):
            continue
        control: ast.AST | None = None
        if isinstance(container, (ast.For, ast.AsyncFor)):
            target_names = {
                item.id
                for item in ast.walk(container.target)
                if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)
            }
            if argument_names & target_names:
                control = container.iter
        elif isinstance(container, (ast.If, ast.While)):
            control = container.test
        if control is not None:
            inputs.update(
                item.id
                for item in ast.walk(control)
                if isinstance(item, ast.Name)
                and isinstance(item.ctx, ast.Load)
                and item.id in caller_parameters
            )
    return sorted(inputs)


def _argument_bindings(
    resolution: LocalCallResolution,
) -> tuple[dict[str, tuple[ast.AST, str]], str | None]:
    """Bind a supported call to callee parameters or explain why that is unsafe."""
    assert resolution.callee is not None
    call = resolution.call
    callee = resolution.callee
    if any(isinstance(argument, ast.Starred) for argument in call.args):
        return {}, "Saga cannot map starred positional arguments to callee parameters."
    if any(keyword.arg is None for keyword in call.keywords):
        return {}, "Saga cannot map expanded keyword arguments to callee parameters."

    positional = [*callee.args.posonlyargs, *callee.args.args]
    if len(call.args) > len(positional):
        return {}, "Saga cannot map extra positional arguments through a variadic parameter."

    bindings: dict[str, tuple[ast.AST, str]] = {}
    for parameter, argument in zip(positional, call.args):
        bindings[parameter.arg] = (argument, "argument")

    positional_only = {item.arg for item in callee.args.posonlyargs}
    keyword_parameters = {
        item.arg for item in [*callee.args.args, *callee.args.kwonlyargs]
    }
    for keyword in call.keywords:
        assert keyword.arg is not None
        if keyword.arg in positional_only:
            return {}, f"Saga cannot bind positional-only parameter '{keyword.arg}' by keyword."
        if keyword.arg not in keyword_parameters:
            return {}, f"Saga cannot map keyword '{keyword.arg}' to a declared callee parameter."
        if keyword.arg in bindings:
            return {}, f"Saga found more than one argument for callee parameter '{keyword.arg}'."
        bindings[keyword.arg] = (keyword.value, "argument")

    defaults_start = len(positional) - len(callee.args.defaults)
    for index, parameter in enumerate(positional):
        if parameter.arg not in bindings and index >= defaults_start:
            bindings[parameter.arg] = (
                callee.args.defaults[index - defaults_start],
                "default",
            )
    for parameter, default in zip(callee.args.kwonlyargs, callee.args.kw_defaults):
        if parameter.arg not in bindings and default is not None:
            bindings[parameter.arg] = (default, "default")
    return bindings, None


def _binding_boundary(
    path: str,
    caller: ast.FunctionDef,
    resolution: LocalCallResolution,
    reason: str,
) -> dict[str, Any]:
    """Record an argument mapping that Saga refused to guess."""
    return {
        "id": f"local-binding-{resolution.call.lineno}-{resolution.call.col_offset}",
        "kind": "unsupported_semantics",
        "target": {"text": f"{resolution.invoked_as}(...) argument binding"},
        "reason": reason,
        "category": "important",
        "source_span": _span(path, resolution.call).as_dict(),
        "call_chain": [call_record(path, caller, resolution)],
    }


def _compose_return_dependencies(
    path: str,
    caller: ast.FunctionDef,
    resolution: LocalCallResolution,
    caller_returns: list[dict[str, Any]],
    callee_returns: list[dict[str, Any]],
    boundary_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Carry supported callee inputs into each caller return that uses the call."""
    relevant = [
        claim
        for claim in caller_returns
        if _call_is_in_return(path, caller, resolution.call, [claim])
    ]
    if not relevant or not callee_returns:
        return []

    required_inputs = {
        name
        for claim in callee_returns
        for name in claim["statement"].get("inputs", [])
    }
    if not required_inputs:
        return []

    bindings, refusal = _argument_bindings(resolution)
    if refusal:
        boundary = _binding_boundary(path, caller, resolution, refusal)
        for claim in relevant:
            if boundary["id"] not in claim["boundary_ids"]:
                claim["boundary_ids"].append(boundary["id"])
        return [boundary]

    assert resolution.callee is not None
    caller_parameters = _caller_parameters(caller)
    callee_vararg = resolution.callee.args.vararg
    callee_kwarg = resolution.callee.args.kwarg
    unsupported_parameters = {
        item.arg for item in (callee_vararg, callee_kwarg) if item is not None
    }
    unresolved = sorted(required_inputs & unsupported_parameters)
    if unresolved:
        reason = (
            "Saga cannot map return-relevant variadic callee parameter(s): "
            + ", ".join(unresolved)
            + "."
        )
        boundary = _binding_boundary(path, caller, resolution, reason)
        for claim in relevant:
            if boundary["id"] not in claim["boundary_ids"]:
                claim["boundary_ids"].append(boundary["id"])
        return [boundary]

    call_link = call_record(path, caller, resolution)
    callee_return_spans = [
        claim["evidence"]["detail"]["return_source_span"]
        for claim in callee_returns
    ]
    limiting_boundaries = sorted({
        boundary_map[boundary_id]
        for claim in callee_returns
        for boundary_id in claim["boundary_ids"]
        if boundary_id in boundary_map
    })
    composed: list[dict[str, Any]] = []
    for parameter in sorted(required_inputs):
        binding = bindings.get(parameter)
        if binding is None:
            reason = f"Saga could not bind return-relevant callee parameter '{parameter}'."
            boundary = _binding_boundary(path, caller, resolution, reason)
            for claim in relevant:
                if boundary["id"] not in claim["boundary_ids"]:
                    claim["boundary_ids"].append(boundary["id"])
            return [boundary]
        argument, origin = binding
        caller_inputs = (
            _caller_inputs_for_argument(
                caller,
                resolution.call,
                argument,
                caller_parameters,
            )
            if origin == "argument"
            else []
        )
        composed.append({
            "callee_parameter": parameter,
            "caller_argument": ast.unparse(argument),
            "binding_origin": origin,
            "caller_inputs": caller_inputs,
            "argument_span": _span(path, argument).as_dict(),
            "callee_return_spans": callee_return_spans,
            "boundary_ids": limiting_boundaries,
            "call_chain": [call_link],
        })

    for claim in relevant:
        statement = claim["statement"]
        existing = statement.setdefault("local_call_dependencies", [])
        existing_keys = {
            (
                item["callee_parameter"],
                _span_key(item["call_chain"][0]["call_site"]),
            )
            for item in existing
        }
        for item in composed:
            key = (
                item["callee_parameter"],
                _span_key(item["call_chain"][0]["call_site"]),
            )
            if key not in existing_keys:
                existing.append(item)
                existing_keys.add(key)
        statement["inputs"] = sorted({
            *statement.get("inputs", []),
            *(name for item in composed for name in item["caller_inputs"]),
        })
        claim["boundary_ids"] = sorted({
            *claim["boundary_ids"],
            *limiting_boundaries,
        })
        call_detail = {
            "text": f"{resolution.invoked_as}(...)",
            "source_span": call_link["call_site"],
        }
        if not any(
            _span_key(item["source_span"]) == _span_key(call_detail["source_span"])
            for item in statement.setdefault("calls", [])
        ):
            statement["calls"].append(call_detail)
        dependency_detail = {
            "kind": "local_call_return",
            "names": sorted(_assigned_names_containing_call(caller, resolution.call)),
            "reads": sorted({
                name for item in composed for name in item["caller_inputs"]
            }),
            "calls": [call_detail],
            "source_span": call_link["call_site"],
        }
        if not any(
            item.get("kind") == "local_call_return"
            and _span_key(item["source_span"]) == _span_key(call_link["call_site"])
            for item in statement.setdefault("dependencies", [])
        ):
            statement["dependencies"].append(dependency_detail)
        claim["source_spans"] = _unique_spans([
            *claim["source_spans"],
            call_link["call_site"],
            call_link["callee_span"],
            *(span for item in composed for span in item["callee_return_spans"]),
        ])
        assumption = {
            "text": "Return dependencies from this module-local call use one-hop argument binding."
        }
        if assumption not in claim["assumptions"]:
            claim["assumptions"].append(assumption)
    return []


def _propagated_text(invoked_as: str, claim: dict[str, Any]) -> str:
    """Put a callee fact in caller context without changing the fact itself."""
    label = f"{invoked_as}(...)"
    text = claim["statement"]["text"]
    if text.startswith("Returns "):
        return f"{label} returns {text[len('Returns '):]}"
    if text.startswith("Raises "):
        return f"{label} may raise {text[len('Raises '):]}"
    if text.startswith("Re-raises "):
        return f"{label} may re-raise {text[len('Re-raises '):]}"
    if text.startswith("Attempts to "):
        return f"{label} may attempt to {text[len('Attempts to '):]}"
    if text.startswith("May "):
        return f"{label} may {text[len('May '):]}"
    return f"Through {label}: {text}"


def _unique_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep source spans in order while removing exact duplicates."""
    result = []
    seen: set[tuple[Any, ...]] = set()
    for span in spans:
        key = _span_key(span)
        if key not in seen:
            seen.add(key)
            result.append(span)
    return result


def _propagate_boundary(
    path: str,
    caller: ast.FunctionDef,
    resolution: LocalCallResolution,
    boundary: dict[str, Any],
) -> dict[str, Any]:
    """Copy a callee boundary into the caller card with its call chain."""
    result = deepcopy(boundary)
    result["id"] = f"local-{resolution.call.lineno}-{resolution.call.col_offset}-{boundary['id']}"
    result["call_chain"] = [call_record(path, caller, resolution), *boundary.get("call_chain", [])]
    return result


def _propagate_claim(
    path: str,
    caller: ast.FunctionDef,
    resolution: LocalCallResolution,
    claim: dict[str, Any],
    boundary_ids: dict[str, str],
) -> dict[str, Any]:
    """Copy one supported callee claim into the caller card."""
    assert resolution.callee is not None
    result = deepcopy(claim)
    result["id"] = f"local-{resolution.call.lineno}-{resolution.call.col_offset}-{claim['id']}"
    result["statement"]["text"] = _propagated_text(resolution.invoked_as, claim)
    chain = call_record(path, caller, resolution)
    result["call_chain"] = [chain, *claim.get("call_chain", [])]
    result["source_spans"] = _unique_spans([
        chain["call_site"],
        chain["callee_span"],
        *claim["source_spans"],
    ])
    result["boundary_ids"] = [boundary_ids[item] for item in claim["boundary_ids"] if item in boundary_ids]
    original_method = claim["evidence"]["method"]
    result["evidence"]["method"] = "one_hop_local_call"
    result["evidence"]["detail"] = {**result["evidence"].get("detail", {}), "callee_method": original_method}
    result["assumptions"].append({
        "text": "The module-level function binding is assumed not to be replaced at runtime."
    })
    if resolution.via_alias:
        result["assumptions"].append({
            "text": "The callee was resolved through a single unambiguous source-level alias."
        })
    return result


def _propagate_diagnostic(
    path: str,
    caller: ast.FunctionDef,
    resolution: LocalCallResolution,
    diagnostic: dict[str, Any],
) -> dict[str, Any]:
    """Keep a callee diagnostic tied to the call that exposed it."""
    result = deepcopy(diagnostic)
    result["message"] = f"In module-local callee {resolution.callee.name}: {diagnostic['message']}"
    result["call_chain"] = [call_record(path, caller, resolution), *diagnostic.get("call_chain", [])]
    return result


def _analyze_direct(
    path: str,
    tree: ast.Module,
    node: ast.FunctionDef,
    ancestry: tuple[str, ...],
    depth: int,
) -> tuple[FunctionEvidence, list[LocalCallResolution]]:
    """Run existing analyses and classify local calls at one depth."""
    guard_claims, guard_boundaries, guard_diagnostics = analyze_guards(path, node)
    exceptions = analyze_exceptions(path, tree, node)
    effects = analyze_effects(path, tree, node)
    resolutions = resolve_local_calls(tree, node, ancestry, depth)
    # Guard analysis owns rejected-input claims. Exception flow owns all
    # explicit-exception claims, including the raises used by entry guards.
    direct_claims = [
        *(claim for claim in guard_claims if claim["kind"] != "explicit_exception"),
        *exceptions.claims,
        *effects.claims,
    ]
    boundaries, boundary_ids = _rewrite_call_boundaries(
        path,
        node,
        [*guard_boundaries, *exceptions.boundaries, *effects.boundaries],
        resolutions,
    )
    _remap_claim_boundaries(direct_claims, boundary_ids)
    returns = analyze_returns(path, node, boundaries)
    evidence = FunctionEvidence(
        [*direct_claims, *returns.claims],
        boundaries,
        [*guard_diagnostics, *exceptions.diagnostics, *effects.diagnostics, *returns.diagnostics],
    )
    return evidence, resolutions


def analyze_one_hop(path: str, tree: ast.Module, node: ast.FunctionDef) -> FunctionEvidence:
    """Analyze a selected function and propagate facts from one local callee hop."""
    evidence, resolutions = _analyze_direct(path, tree, node, (node.name,), 0)
    caller_returns = [claim for claim in evidence.claims if claim["kind"] == "return_dependency"]
    propagated_keys: set[tuple[Any, ...]] = set()
    callee_cache: dict[str, FunctionEvidence] = {}
    for resolution in resolutions:
        if resolution.status != "resolved" or resolution.callee is None:
            continue
        if resolution.callee.name not in callee_cache:
            callee_cache[resolution.callee.name], _ = _analyze_direct(
                path,
                tree,
                resolution.callee,
                (node.name, resolution.callee.name),
                1,
            )
        callee_evidence = callee_cache[resolution.callee.name]
        boundary_map: dict[str, str] = {}
        for boundary in callee_evidence.boundaries:
            propagated = _propagate_boundary(path, node, resolution, boundary)
            boundary_map[boundary["id"]] = propagated["id"]
            key = (propagated["id"], _span_key(propagated["source_span"]))
            if key not in propagated_keys:
                propagated_keys.add(key)
                evidence.boundaries.append(propagated)
        binding_boundaries = _compose_return_dependencies(
            path,
            node,
            resolution,
            caller_returns,
            [
                claim
                for claim in callee_evidence.claims
                if claim["kind"] == "return_dependency"
            ],
            boundary_map,
        )
        evidence.boundaries.extend(binding_boundaries)
        for claim in callee_evidence.claims:
            if claim["kind"] == "return_dependency" and not _call_is_in_return(path, node, resolution.call, caller_returns):
                continue
            if claim["kind"] not in {"return_dependency", "attempted_write", "known_effect", "explicit_exception"}:
                continue
            exception_boundaries: list[dict[str, Any]] = []
            handler_spans: list[dict[str, Any]] = []
            if claim["kind"] == "explicit_exception":
                keep, exception_boundaries, handler_spans = filter_call_exception(
                    path, tree, node, resolution.call, claim
                )
                if not keep:
                    continue
                for boundary in exception_boundaries:
                    propagated_boundary = _propagate_boundary(path, node, resolution, boundary)
                    boundary_map[boundary["id"]] = propagated_boundary["id"]
                    evidence.boundaries.append(propagated_boundary)
            propagated = _propagate_claim(path, node, resolution, claim, boundary_map)
            if handler_spans:
                propagated["statement"]["handler_spans"] = handler_spans
                propagated["source_spans"] = _unique_spans([
                    *propagated["source_spans"],
                    *handler_spans,
                ])
            if exception_boundaries:
                propagated["boundary_ids"].extend(
                    boundary_map[item["id"]] for item in exception_boundaries
                )
            key = (propagated["id"], propagated["kind"])
            if key not in propagated_keys:
                propagated_keys.add(key)
                evidence.claims.append(propagated)
        evidence.diagnostics.extend(
            _propagate_diagnostic(path, node, resolution, item)
            for item in callee_evidence.diagnostics
        )
    evidence.boundaries = _deduplicate_boundaries(evidence.boundaries)
    return evidence
