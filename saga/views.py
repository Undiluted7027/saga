"""Fixed projections over one evidence card."""

from __future__ import annotations

from typing import Any, Literal

ViewName = Literal["full", "return", "mutation", "failure", "boundary"]

VIEW_CLAIMS: dict[ViewName, set[str] | None] = {
    "full": None,
    "return": {"return_dependency"},
    "mutation": {"attempted_write", "known_effect"},
    "failure": {"rejected_input", "explicit_exception"},
    "boundary": set(),
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
        ]
    return {
        **card,
        "view": view,
        "claims": claims,
        "boundaries": boundaries,
        "diagnostics": list(card["diagnostics"]),
    }


def view_is_empty(card: dict[str, Any]) -> bool:
    """Return whether the selected view has no supported evidence to render."""
    view: ViewName = card.get("view", "full")
    if view == "boundary":
        return not card["boundaries"]
    return not card["claims"]
