"""Render an EvidenceCard without changing its meaning."""

from __future__ import annotations

from typing import Any

from .boundaries import group_boundaries
from .diagnostics import group_diagnostics
from .views import EMPTY_MESSAGES, VIEW_LABELS, view_is_empty


def _compact_domain(domain: Any) -> str:
    """Render an observed domain without dumping serialized object internals."""
    if not isinstance(domain, dict):
        return str(domain)
    if domain.get("kind") == "numeric":
        return f"numeric {domain.get('min')}–{domain.get('max')} ({domain.get('distinct', 0)} distinct)"
    if domain.get("kind") == "observed_values":
        return f"{domain.get('distinct', 0)} observed values"
    if domain.get("kind") == "excluded":
        kinds = ", ".join(domain.get("serialization_kinds", [])) or "unusable"
        types = ", ".join(domain.get("types", []))
        return f"excluded ({kinds}{f'; types: {types}' if types else ''})"
    return str(domain.get("kind", "unknown"))


def _local_call_chain(lines: list[str], chain: list[dict[str, Any]]) -> None:
    """Render navigable locations for evidence propagated from a local callee."""
    for link in chain:
        call_site = link["call_site"]
        callee_span = link["callee_span"]
        lines.append(
            f"    Local call: {link['caller']} -> {link['callee']} at "
            f"{call_site['path']}:{call_site['start_line']}; callee at {callee_span['path']}:{callee_span['start_line']}"
        )
        if link["argument_bindings"]:
            bindings = ", ".join(f"{item['parameter']} = {item['argument']}" for item in link["argument_bindings"])
            lines.append(f"      Arguments: {bindings}")


def _boundary_group(
    lines: list[str],
    group: dict[str, Any],
    prefix: str = "Boundary",
    show_sites: bool = False,
) -> None:
    """Render one group and all of its source-preserving occurrences."""
    is_call = group["boundary_class"] in {
        "routine_call",
        "module_local",
        "external_or_unresolved_call",
    }
    site_label = "call site" if is_call else "source site"
    if group["count"] != 1:
        site_label += "s"
    related = (
        f"; limits {', '.join(group['claim_kinds'])}"
        if group["claim_kinds"]
        else ""
    )
    limited_by = (
        f"; limited by {', '.join(group['limiting_boundary_ids'])}"
        if group["limiting_boundary_ids"]
        else ""
    )
    lines.append(
        f"  {prefix} [{group['boundary_class']} · {group['kind']}] "
        f"{group['target']} — {group['count']} {site_label}{related}{limited_by}: {group['reason']}"
    )
    if group["count"] > 1 and not show_sites:
        lines.append("    Re-run with --show-boundary-sites to list every source location.")
        return
    for occurrence in group["occurrences"]:
        span = occurrence["source_span"]
        lines.append(
            f"    [{occurrence['boundary_id']}] {span['path']}:{span['start_line']}"
        )
        _local_call_chain(lines, occurrence["call_chain"])


def terminal(
    card: dict[str, Any],
    show_routine_boundaries: bool = False,
    show_boundary_sites: bool = False,
    show_diagnostic_sites: bool = False,
) -> str:
    """Render the structured card for concise terminal inspection."""
    target = card["target"]
    lines = [f"{target['qualified_name']} {target['status']}", f"  {target['path']}:{target['source_span']['start_line'] if target['source_span'] else '?'}", f"  {target['signature'] or '(signature unavailable)'}", f"  Claims: {len(card['claims'])}"]
    view = card.get("view", "full")
    if view != "full":
        lines.insert(3, f"  View: {VIEW_LABELS[view]}")
        lines.insert(4, "  Full card: omit --view.")
        if view_is_empty(card):
            lines.insert(5, f"  {EMPTY_MESSAGES[view]}")
    for claim in card["claims"]:
        statement = claim["statement"]
        label = statement.get("type", claim["kind"])
        lines.append(f"  Claim [{label}; {claim['evidence']['evidence_class']}]: {statement['text']}")
        if statement.get("source_text"):
            lines.append(f"    Source syntax: {statement['source_text']}")
        scope = statement.get("scope")
        if scope and scope["kind"] == "callee":
            names = f" ({', '.join(scope['names'])})" if scope["names"] else ""
            lines.append(f"    Callee scope: {scope['function']}{names}")
        if statement.get("condition_source_text"):
            lines.append(f"    Condition syntax: {statement['condition_source_text']}")
        for handler_span in statement.get("handler_spans", []):
            lines.append(f"    Handler checked: {handler_span['path']}:{handler_span['start_line']}")
        if statement.get("inputs"):
            lines.append(f"    May use inputs: {', '.join(statement['inputs'])}")
        if statement.get("definitions"):
            lines.append(f"    May use local values: {', '.join(statement['definitions'])}")
        if statement.get("weak_definitions"):
            lines.append(f"    May be changed through attribute, subscript, or method access: {', '.join(statement['weak_definitions'])}")
        if statement.get("calls"):
            lines.append(f"    May use calls: {', '.join(call['text'] for call in statement['calls'])}")
        for dependency in statement.get("local_call_dependencies", []):
            caller_inputs = (
                f"; caller inputs: {', '.join(dependency['caller_inputs'])}"
                if dependency["caller_inputs"]
                else ""
            )
            lines.append(
                "    Callee binding: "
                f"{dependency['callee_scope']}.{dependency['callee_parameter']} = {dependency['caller_argument']}"
                f" ({dependency['binding_origin']}{caller_inputs})"
            )
            _local_call_chain(lines, dependency["call_chain"])
        lines.append(f"    Method: {claim['evidence']['method']}")
        if claim["boundary_ids"]:
            lines.append(f"    Limited by: {', '.join(claim['boundary_ids'])}")
        _local_call_chain(lines, claim.get("call_chain", []))
        if claim["evidence"]["evidence_class"] == "observed":
            detail = claim["evidence"].get("detail", {})
            lines.append(f"    Support: {detail.get('support', 0)} distinct inputs")
            if detail.get("supporting_tests"):
                lines.append(f"    Supporting tests: {', '.join(detail['supporting_tests'])}")
            if detail.get("input_domain"):
                domains = ", ".join(f"{name}: {_compact_domain(domain)}" for name, domain in detail["input_domain"].items())
                lines.append(f"    Input domain: {domains}")
            if detail.get("return_domain"):
                lines.append(f"    Return domain: {_compact_domain(detail['return_domain'])}")
            elif detail.get("domain"):
                lines.append(f"    Observed domain: {_compact_domain(detail['domain'])}")
            if detail.get("raised_executions"):
                lines.append(f"    Raised executions: {detail['raised_executions']}")
            if detail.get("environment"):
                environment = ", ".join(
                    f"{name}: {value}"
                    for name, value in detail["environment"].items()
                )
                lines.append(f"    Environment: {environment}")
        for span in claim["source_spans"]:
            lines.append(f"    Source: {span['path']}:{span['start_line']}")
        for assumption in claim["assumptions"]:
            lines.append(f"    Assumption: {assumption['text']}")
    groups = group_boundaries(card)
    important_groups = [group for group in groups if group["category"] != "routine"]
    routine_groups = [group for group in groups if group["category"] == "routine"]
    group_label = "group" if len(groups) == 1 else "groups"
    site_label = "site" if len(card["boundaries"]) == 1 else "sites"
    lines.append(
        f"  Boundaries: {len(card['boundaries'])} {site_label} in "
        f"{len(groups)} {group_label}"
    )
    for group in important_groups:
        _boundary_group(lines, group, show_sites=show_boundary_sites)
    if routine_groups:
        routine_sites = sum(group["count"] for group in routine_groups)
        routine_group_label = "group" if len(routine_groups) == 1 else "groups"
        lines.append(
            f"  Routine unresolved calls: {routine_sites} sites in "
            f"{len(routine_groups)} {routine_group_label}"
        )
        if show_routine_boundaries:
            for group in routine_groups:
                _boundary_group(
                    lines,
                    group,
                    "Routine boundary",
                    show_sites=show_boundary_sites,
                )
        else:
            lines.append("    Re-run with --show-routine-boundaries to expand them.")
    observation_status = card.get("observation_status")
    if observation_status:
        lines.append(
            f"  Observation status [{observation_status['state']} · "
            f"{observation_status['reason']}]: {observation_status['message']}"
        )
        lines.append(
            "    Executions: "
            f"{observation_status['execution_count']} total, "
            f"{observation_status['returned_executions']} returned, "
            f"{observation_status['raised_executions']} raised"
        )
        lines.append(
            f"    Distinct usable inputs: {observation_status['distinct_inputs']}"
        )
        if observation_status.get("excluded_parameters"):
            excluded = []
            for parameter in observation_status["excluded_parameters"]:
                kinds = ", ".join(parameter["serialization_kinds"])
                types = ", ".join(parameter["types"])
                excluded.append(
                    f"{parameter['name']} ({kinds}{f'; types: {types}' if types else ''})"
                )
            lines.append("    Excluded parameters: " + ", ".join(excluded))
        if observation_status.get("input_domain"):
            domains = ", ".join(
                f"{name}: {_compact_domain(domain)}"
                for name, domain in observation_status["input_domain"].items()
            )
            lines.append(f"    Input domain: {domains}")
        if observation_status["tests"]:
            lines.append(
                "    Tests: " + ", ".join(observation_status["tests"])
            )
        if observation_status["environment"]:
            environment = ", ".join(
                f"{name}: {value}"
                for name, value in observation_status["environment"].items()
            )
            lines.append(f"    Environment: {environment}")
    diagnostic_groups = group_diagnostics(card)
    if diagnostic_groups:
        lines.append(
            f"  Diagnostics: {len(card['diagnostics'])} reports in "
            f"{len(diagnostic_groups)} groups"
        )
    for group in diagnostic_groups:
        reports = "report" if group["report_count"] == 1 else "reports"
        sites = "site" if group["site_count"] == 1 else "sites"
        lines.append(
            f"  Diagnostic [{group['kind']} · {', '.join(group['analyses'])}] — {group['report_count']} {reports} "
            f"at {group['site_count']} {sites}: {group['message']}"
        )
        if group["report_count"] > 1 and not show_diagnostic_sites:
            lines.append(
                "    Re-run with --show-diagnostic-sites to list source locations."
            )
            continue
        for occurrence in group["occurrences"]:
            span = occurrence["source_span"]
            if span:
                lines.append(f"    Source: {span['path']}:{span['start_line']}")
            _local_call_chain(lines, occurrence["call_chain"])
    if card.get("hidden_diagnostics"):
        lines.append(f"  Hidden diagnostics: {card['hidden_diagnostics']['message']}")
    return "\n".join(lines)
