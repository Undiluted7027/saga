"""Render an EvidenceCard without changing its meaning."""

from __future__ import annotations

from typing import Any

from .boundaries import group_boundaries
from .diagnostics import group_diagnostics
from .local_evidence import partition_local_call_evidence
from .return_evidence import return_path_presentation
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
    indent: str = "  ",
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
        f"{indent}{prefix} [{group['boundary_class']} · {group['kind']}] "
        f"{group['target']} — {group['count']} {site_label}{related}{limited_by}: {group['reason']}"
    )
    if group["count"] > 1 and not show_sites:
        lines.append(f"{indent}  Re-run with --show-boundary-sites to list every source location.")
        return
    for occurrence in group["occurrences"]:
        span = occurrence["source_span"]
        lines.append(
            f"{indent}  [{occurrence['boundary_id']}] {span['path']}:{span['start_line']}"
        )
        _local_call_chain(lines, occurrence["call_chain"])


def _dependency_facts(dependency: dict[str, Any]) -> str:
    """Render only facts already recorded on one dependency entry."""
    verb = "may change" if dependency["kind"] == "weak_definition" else "defines"
    facts = []
    if dependency.get("names"):
        facts.append(f"{verb} {', '.join(dependency['names'])}")
    if dependency.get("reads"):
        facts.append(f"reads {', '.join(dependency['reads'])}")
    if dependency.get("calls"):
        facts.append(
            "calls " + ", ".join(call["text"] for call in dependency["calls"])
        )
    return "; ".join(facts) or dependency["kind"].replace("_", " ")


def _return_path_lines(
    lines: list[str],
    claim: dict[str, Any],
    path_number: int,
    show_return_sites: bool,
    indent: str,
) -> bool:
    """Render a focused return-path heading and compact large dependency sets."""
    view = return_path_presentation(claim)
    span = view["return_span"]
    location = f"{span['path']}:{span['start_line']}" if span else "unknown location"
    lines.append(
        f"{indent}Return path {path_number}: {view['return_expression']} at {location}"
    )
    if view["path_conditions"]:
        conditions = " and ".join(
            f"({item.get('text', item.get('source_text', '?'))})"
            for item in view["path_conditions"]
        )
        lines.append(f"{indent}  When: {conditions}")
    if not view["compact"]:
        return False

    lines.append(f"{indent}  Dependency sites: {view['site_count']} in {len(view['groups'])} groups")
    for group in view["groups"]:
        site_label = "site" if group["count"] == 1 else "sites"
        lines.append(
            f"{indent}    {group['kind'].replace('_', ' ')}: "
            f"{group['count']} {site_label}"
        )
        if group["names"]:
            lines.append(f"{indent}      Names: {', '.join(group['names'])}")
        if group["reads"]:
            lines.append(f"{indent}      Reads: {', '.join(group['reads'])}")
        if group["calls"]:
            lines.append(f"{indent}      Calls: {', '.join(group['calls'])}")
        if show_return_sites:
            for dependency in group["entries"]:
                source = dependency["source_span"]
                lines.append(
                    f"{indent}      {source['path']}:{source['start_line']} — "
                    f"{_dependency_facts(dependency)}"
                )
    if not show_return_sites:
        lines.append(
            f"{indent}  Dependency sites are collapsed; re-run with "
            "--show-return-sites to expand them."
        )
    else:
        dependency_spans = {
            (
                item["source_span"]["path"],
                item["source_span"]["start_line"],
                item["source_span"]["start_column"],
                item["source_span"]["end_line"],
                item["source_span"]["end_column"],
            )
            for item in view["dependencies"]
        }
        for source in view["source_spans"]:
            key = (
                source["path"], source["start_line"], source["start_column"],
                source["end_line"], source["end_column"],
            )
            if key not in dependency_spans:
                lines.append(
                    f"{indent}      Additional claim source: "
                    f"{source['path']}:{source['start_line']}"
                )
    return True


def _claim_lines(
    lines: list[str],
    claim: dict[str, Any],
    indent: str = "  ",
    return_path_number: int | None = None,
    show_return_sites: bool = False,
) -> None:
    """Render one claim without changing or summarizing its evidence."""
    detail_indent = indent + "  "
    statement = claim["statement"]
    compact_return = False
    if return_path_number is not None and claim["kind"] == "return_dependency":
        compact_return = _return_path_lines(
            lines, claim, return_path_number, show_return_sites, indent
        )
    label = statement.get("type", claim["kind"])
    lines.append(
        f"{indent}Claim [{label}; {claim['evidence']['evidence_class']}]: "
        f"{statement['text']}"
    )
    if statement.get("source_text"):
        lines.append(f"{detail_indent}Source syntax: {statement['source_text']}")
    scope = statement.get("scope")
    if scope and scope["kind"] == "callee":
        names = f" ({', '.join(scope['names'])})" if scope["names"] else ""
        lines.append(f"{detail_indent}Callee scope: {scope['function']}{names}")
    if statement.get("condition_source_text"):
        lines.append(
            f"{detail_indent}Condition syntax: {statement['condition_source_text']}"
        )
    for handler_span in statement.get("handler_spans", []):
        lines.append(
            f"{detail_indent}Handler checked: "
            f"{handler_span['path']}:{handler_span['start_line']}"
        )
    if statement.get("inputs"):
        lines.append(
            f"{detail_indent}May use inputs: {', '.join(statement['inputs'])}"
        )
    if statement.get("definitions"):
        lines.append(
            f"{detail_indent}May use local values: "
            f"{', '.join(statement['definitions'])}"
        )
    if statement.get("weak_definitions"):
        lines.append(
            f"{detail_indent}May be changed through attribute, subscript, or method access: "
            f"{', '.join(statement['weak_definitions'])}"
        )
    if statement.get("calls"):
        lines.append(
            f"{detail_indent}May use calls: "
            f"{', '.join(call['text'] for call in statement['calls'])}"
        )
    for dependency in statement.get("local_call_dependencies", []):
        caller_inputs = (
            f"; caller inputs: {', '.join(dependency['caller_inputs'])}"
            if dependency["caller_inputs"]
            else ""
        )
        lines.append(
            f"{detail_indent}Callee binding: "
            f"{dependency['callee_scope']}.{dependency['callee_parameter']} = "
            f"{dependency['caller_argument']} "
            f"({dependency['binding_origin']}{caller_inputs})"
        )
        _local_call_chain(lines, dependency["call_chain"])
    lines.append(f"{detail_indent}Method: {claim['evidence']['method']}")
    if claim["boundary_ids"]:
        lines.append(f"{detail_indent}Limited by: {', '.join(claim['boundary_ids'])}")
    _local_call_chain(lines, claim.get("call_chain", []))
    if claim["evidence"]["evidence_class"] == "observed":
        detail = claim["evidence"].get("detail", {})
        lines.append(f"{detail_indent}Support: {detail.get('support', 0)} distinct inputs")
        if detail.get("supporting_tests"):
            lines.append(
                f"{detail_indent}Supporting tests: "
                f"{', '.join(detail['supporting_tests'])}"
            )
        if detail.get("input_domain"):
            domains = ", ".join(
                f"{name}: {_compact_domain(domain)}"
                for name, domain in detail["input_domain"].items()
            )
            lines.append(f"{detail_indent}Input domain: {domains}")
        if detail.get("return_domain"):
            lines.append(
                f"{detail_indent}Return domain: "
                f"{_compact_domain(detail['return_domain'])}"
            )
        elif detail.get("domain"):
            lines.append(
                f"{detail_indent}Observed domain: {_compact_domain(detail['domain'])}"
            )
        if detail.get("raised_executions"):
            lines.append(
                f"{detail_indent}Raised executions: {detail['raised_executions']}"
            )
        if detail.get("environment"):
            environment = ", ".join(
                f"{name}: {value}" for name, value in detail["environment"].items()
            )
            lines.append(f"{detail_indent}Environment: {environment}")
    if not compact_return:
        for span in claim["source_spans"]:
            lines.append(f"{detail_indent}Source: {span['path']}:{span['start_line']}")
    for assumption in claim["assumptions"]:
        lines.append(f"{detail_indent}Assumption: {assumption['text']}")


def _diagnostic_group(
    lines: list[str],
    group: dict[str, Any],
    show_sites: bool = False,
    indent: str = "  ",
) -> None:
    """Render one diagnostic group and optionally expand its source sites."""
    reports = "report" if group["report_count"] == 1 else "reports"
    sites = "site" if group["site_count"] == 1 else "sites"
    lines.append(
        f"{indent}Diagnostic [{group['kind']} · {', '.join(group['analyses'])}] — "
        f"{group['report_count']} {reports} at {group['site_count']} {sites}: "
        f"{group['message']}"
    )
    if group["report_count"] > 1 and not show_sites:
        lines.append(
            f"{indent}  Re-run with --show-diagnostic-sites to list source locations."
        )
        return
    for occurrence in group["occurrences"]:
        span = occurrence["source_span"]
        if span:
            lines.append(f"{indent}  Source: {span['path']}:{span['start_line']}")
        _local_call_chain(lines, occurrence["call_chain"])


def terminal(
    card: dict[str, Any],
    show_routine_boundaries: bool = False,
    show_boundary_sites: bool = False,
    show_diagnostic_sites: bool = False,
    show_local_call_evidence: bool = False,
    show_return_sites: bool = False,
) -> str:
    """Render the structured card for concise terminal inspection."""
    target = card["target"]
    view = card.get("view", "full")
    partition = partition_local_call_evidence(card) if view != "full" else None
    propagated_claims = (
        sum(len(group["claims"]) for group in partition["groups"])
        if partition
        else 0
    )
    claim_count = f"{len(card['claims'])}"
    if partition and partition["groups"]:
        claim_count += (
            f" ({len(partition['direct']['claims'])} direct; "
            f"{propagated_claims} inside local calls)"
        )
    lines = [
        f"{target['qualified_name']} {target['status']}",
        f"  {target['path']}:"
        f"{target['source_span']['start_line'] if target['source_span'] else '?'}",
        f"  {target['signature'] or '(signature unavailable)'}",
        f"  Claims: {claim_count}",
    ]
    if view != "full":
        lines.insert(3, f"  View: {VIEW_LABELS[view]}")
        lines.insert(4, "  Full card: omit --view.")
        if view_is_empty(card):
            lines.insert(5, f"  {EMPTY_MESSAGES[view]}")
    rendered_card = (
        {
            **card,
            "claims": partition["direct"]["claims"],
            "boundaries": partition["direct"]["boundaries"],
            "diagnostics": partition["direct"]["diagnostics"],
        }
        if partition
        else card
    )
    return_path_number = 0
    for claim in rendered_card["claims"]:
        if view == "return" and claim["kind"] == "return_dependency":
            return_path_number += 1
        _claim_lines(
            lines,
            claim,
            return_path_number=return_path_number or None,
            show_return_sites=show_return_sites,
        )
    groups = group_boundaries({**rendered_card, "claims": card["claims"]})
    important_groups = [group for group in groups if group["category"] != "routine"]
    routine_groups = [group for group in groups if group["category"] == "routine"]
    group_label = "group" if len(groups) == 1 else "groups"
    site_label = "site" if len(rendered_card["boundaries"]) == 1 else "sites"
    boundary_label = "Direct boundaries" if partition and partition["groups"] else "Boundaries"
    lines.append(
        f"  {boundary_label}: {len(rendered_card['boundaries'])} {site_label} in "
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
    diagnostic_groups = group_diagnostics(rendered_card)
    if diagnostic_groups:
        diagnostic_label = (
            "Direct diagnostics" if partition and partition["groups"] else "Diagnostics"
        )
        lines.append(
            f"  {diagnostic_label}: {len(rendered_card['diagnostics'])} reports in "
            f"{len(diagnostic_groups)} groups"
        )
    for group in diagnostic_groups:
        _diagnostic_group(
            lines,
            group,
            show_sites=show_diagnostic_sites,
        )
    if partition and partition["groups"]:
        noun = "group" if len(partition["groups"]) == 1 else "groups"
        lines.append(f"  Local-call evidence: {len(partition['groups'])} {noun}")
        for local_group in partition["groups"]:
            local_card = {
                **card,
                "claims": card["claims"],
                "boundaries": local_group["boundaries"],
                "diagnostics": local_group["diagnostics"],
            }
            boundary_groups = group_boundaries(local_card)
            diagnostic_groups = group_diagnostics(local_card)
            call_site = local_group["call_site"]
            lines.append(
                f"  Local call {local_group['invoked_as']}(...) at "
                f"{call_site['path']}:{call_site['start_line']} — "
                f"{len(local_group['claims'])} claims, "
                f"{len(boundary_groups)} boundary groups, "
                f"{len(diagnostic_groups)} diagnostic groups"
            )
            if local_group["argument_bindings"]:
                bindings = ", ".join(
                    f"{item['parameter']} = {item['argument']}"
                    for item in local_group["argument_bindings"]
                )
                lines.append(f"    Arguments: {bindings}")
            if not show_local_call_evidence:
                lines.append(
                    "    Evidence is collapsed; re-run with "
                    "--show-local-call-evidence to expand it."
                )
                continue
            for claim in local_group["claims"]:
                if view == "return" and claim["kind"] == "return_dependency":
                    return_path_number += 1
                _claim_lines(
                    lines,
                    claim,
                    indent="    ",
                    return_path_number=return_path_number or None,
                    show_return_sites=show_return_sites,
                )
            for group in boundary_groups:
                _boundary_group(
                    lines,
                    group,
                    show_sites=True,
                    indent="    ",
                )
            for group in diagnostic_groups:
                _diagnostic_group(
                    lines,
                    group,
                    show_sites=True,
                    indent="    ",
                )
    if card.get("hidden_diagnostics"):
        lines.append(f"  Hidden diagnostics: {card['hidden_diagnostics']['message']}")
    return "\n".join(lines)
