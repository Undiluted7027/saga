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
        for assumption in claim["assumptions"]:
            lines.append(f"    Assumption: {assumption['text']}")
    for diagnostic in card["diagnostics"]:
        lines.append(f"  Diagnostic [{diagnostic['kind']}]: {diagnostic['message']}")
    return "\n".join(lines)
