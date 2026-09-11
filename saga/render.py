"""Render an EvidenceCard without changing its meaning."""

from __future__ import annotations

import json
from typing import Any


def terminal(card: dict[str, Any]) -> str:
    """Render the structured card for concise terminal inspection."""
    target = card["target"]
    lines = [f"{target['qualified_name']} {target['status']}", f"  {target['path']}:{target['source_span']['start_line'] if target['source_span'] else '?'}", f"  {target['signature'] or '(signature unavailable)'}", f"  Claims: {len(card['claims'])}"]
    for claim in card["claims"]:
        statement = claim["statement"]
        label = statement.get("type", claim["kind"])
        lines.append(f"  Claim [{label}; {claim['evidence']['evidence_class']}]: {statement['text']}")
        if "condition" in statement:
            lines.append(f"    Condition: {json.dumps(statement['condition'], sort_keys=True)}")
        if "dependencies" in statement:
            for dependency in statement["dependencies"]:
                span = dependency["source_span"]
                lines.append(f"    Dependency [{dependency['kind']}] at {span['path']}:{span['start_line']}")
        if claim["evidence"]["evidence_class"] == "observed":
            detail = claim["evidence"].get("detail", {})
            lines.append(f"    Support: {detail.get('support', 0)} distinct inputs")
            if detail.get("supporting_tests"):
                lines.append(f"    Supporting tests: {', '.join(detail['supporting_tests'])}")
            if detail.get("input_domain"):
                lines.append(f"    Input domain: {json.dumps(detail['input_domain'], sort_keys=True)}")
            if detail.get("return_domain"):
                lines.append(f"    Return domain: {json.dumps(detail['return_domain'], sort_keys=True)}")
            if detail.get("raised_executions"):
                lines.append(f"    Raised executions: {detail['raised_executions']}")
        for span in claim["source_spans"]:
            lines.append(f"    Source: {span['path']}:{span['start_line']}")
        for assumption in claim["assumptions"]:
            lines.append(f"    Assumption: {assumption['text']}")
    lines.append(f"  Boundaries: {len(card['boundaries'])}")
    for boundary in card["boundaries"]:
        span = boundary["source_span"]
        lines.append(f"  Boundary [{boundary['kind']}] {boundary['target']['text']} at {span['path']}:{span['start_line']}: {boundary['reason']}")
    for diagnostic in card["diagnostics"]:
        lines.append(f"  Diagnostic [{diagnostic['kind']}]: {diagnostic['message']}")
    return "\n".join(lines)
