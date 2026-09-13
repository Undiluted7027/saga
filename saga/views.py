"""Fixed projections over one evidence card."""

from __future__ import annotations

from typing import Any, Literal

from .diagnostics import group_diagnostics

ViewName = Literal["full", "return", "mutation", "failure", "boundary"]

VIEW_CLAIMS: dict[ViewName, set[str] | None] = {
    "full": None,
    "return": {"return_dependency"},
    "mutation": {"attempted_write", "known_effect"},
    "failure": {"rejected_input", "explicit_exception"},
    "boundary": set(),
}

VIEW_DIAGNOSTIC_ANALYSES: dict[ViewName, set[str] | None] = {
    "full": None,
    "return": {"inspection", "returns"},
    "mutation": {"inspection", "effects"},
    "failure": {"inspection", "guards", "exceptions"},
    "boundary": {"inspection"},
}


def _hidden_diagnostic_summary(group_count: int) -> dict[str, Any]:
    """Build the shared pointer from a focused view back to the full card."""
    noun = "group" if group_count == 1 else "groups"
    return {
        "group_count": group_count,
        "message": f"{group_count} diagnostic {noun} hidden; open the full card to inspect them.",
    }


VIEW_LABELS: dict[ViewName, str] = {
    "full": "Full evidence card",
    "return": "Return dependencies",
    "mutation": "Mutations and effects",
    "failure": "Failures",
    "boundary": "Analysis boundaries",
}

EMPTY_MESSAGES: dict[ViewName, str] = {
    "full": "Saga produced no claims or boundaries for this function.",
    "return": (
        "Saga found no supported return-dependency evidence. This does not establish "
        "that the function cannot return or that its return is independent of other values."
    ),
    "mutation": (
        "Saga found no supported mutation or known-effect evidence. This does not establish "
        "that the function is pure or cannot change state."
    ),
    "failure": (
        "Saga found no supported rejected-input or explicit-exception evidence. This does not "
        "establish that the function cannot fail or raise an exception."
    ),
    "boundary": (
        "Saga recorded no analysis boundaries for this function. This does not establish "
        "complete analysis. Check the full card for diagnostics."
    ),
}


def focus_card(card: dict[str, Any], view: ViewName) -> dict[str, Any]:
    """Filter a card without copying or changing any retained evidence record."""
    if view == "full":
        return card
    allowed = VIEW_CLAIMS[view]
    assert allowed is not None
    claims = [claim for claim in card["claims"] if claim["kind"] in allowed]
    if view == "mutation":
        # Local construction is still evidence, but it should not bury writes to
        # arguments, globals, or objects whose ownership Saga cannot establish.
        claims.sort(
            key=lambda claim: claim["kind"] == "attempted_write"
            and claim["statement"].get("write_scope") == "local_container"
        )
    if view == "boundary":
        boundaries = list(card["boundaries"])
    else:
        related_ids = {
            boundary_id
            for claim in claims
            for boundary_id in claim["boundary_ids"]
        }
        boundaries = [
            boundary
            for boundary in card["boundaries"]
            if boundary["id"] in related_ids
            or (view == "mutation" and "effects" in boundary.get("concerns", []))
        ]
    relevant_analyses = VIEW_DIAGNOSTIC_ANALYSES[view]
    assert relevant_analyses is not None
    diagnostics = [
        diagnostic
        for diagnostic in card["diagnostics"]
        if relevant_analyses & set(diagnostic.get("analyses", ["inspection"]))
    ]
    hidden = [diagnostic for diagnostic in card["diagnostics"] if diagnostic not in diagnostics]
    focused = {
        **card,
        "view": view,
        "claims": claims,
        "boundaries": boundaries,
        "diagnostics": diagnostics,
    }
    if hidden:
        focused["hidden_diagnostics"] = _hidden_diagnostic_summary(
            len(group_diagnostics({"diagnostics": hidden}))
        )
    else:
        focused.pop("hidden_diagnostics", None)
    return focused


def view_is_empty(card: dict[str, Any]) -> bool:
    """Return whether the selected view has no supported evidence to render."""
    view: ViewName = card.get("view", "full")
    if view == "boundary":
        return not card["boundaries"]
    if view == "mutation":
        return not card["claims"] and not card["boundaries"]
    return not card["claims"]
