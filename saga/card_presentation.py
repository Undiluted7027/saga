"""Lossless, answer-first presentation helpers for focused evidence cards."""

from __future__ import annotations

from typing import Any


_ANSWER_LABELS = {
    "return_dependency": "return answer",
    "attempted_write": "writes and effects answer",
    "known_effect": "writes and effects answer",
    "rejected_input": "failure answer",
    "explicit_exception": "failure answer",
}


def readable_claim_summary(claim: dict[str, Any]) -> str:
    """Shorten analyzer-shaped write wording without changing its modality."""
    text = claim["statement"]["text"]
    if claim["kind"] != "attempted_write":
        return text
    if text.startswith("Attempts to write to "):
        return "May write to " + text.removeprefix("Attempts to write to ")
    return text.replace(" may attempt to write to ", " may write to ", 1)


def _call_site_key(claim: dict[str, Any]) -> tuple[Any, ...]:
    chain = claim.get("call_chain", [])
    if not chain:
        return ()
    link = chain[0]
    span = link["call_site"]
    return (
        link["caller"], link["callee"], link.get("invoked_as", link["callee"]),
        span["path"], span["start_line"], span["start_column"],
    )


def _claim_group_key(claim: dict[str, Any]) -> tuple[Any, ...]:
    statement = claim["statement"]
    kind = claim["kind"]
    call_site = _call_site_key(claim)
    if kind == "attempted_write":
        return (kind, statement.get("source_text", statement["text"]), call_site)
    if kind == "known_effect":
        effect = statement.get("effect", {})
        return (
            kind,
            effect.get("kind"),
            effect.get("callee"),
            statement.get("source_text"),
            call_site,
        )
    if kind in {"rejected_input", "explicit_exception"}:
        return (
            kind,
            statement["text"],
            statement.get("condition_source_text"),
            statement.get("source_text"),
            call_site,
        )
    return (kind, claim["id"])


def group_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group repeatable claim records while retaining every original object."""
    groups: list[dict[str, Any]] = []
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for claim in claims:
        key = _claim_group_key(claim)
        group = by_key.get(key)
        if group is None:
            group = {
                "key": repr(key),
                "kind": claim["kind"],
                "summary": readable_claim_summary(claim),
                "claims": [],
                "claim_ids": [],
                "source_spans": [],
                "boundary_ids": [],
            }
            by_key[key] = group
            groups.append(group)
        group["claims"].append(claim)
        group["claim_ids"].append(claim["id"])
        group["source_spans"].extend(claim["source_spans"])
        group["boundary_ids"] = sorted(
            {*group["boundary_ids"], *claim.get("boundary_ids", [])}
        )
    for group in groups:
        group["count"] = len(group["claims"])
    return groups


def focused_answer(
    card: dict[str, Any], claims: list[dict[str, Any]]
) -> dict[str, str] | None:
    """Summarize only counts and relationships already present in a focused card."""
    view = card.get("view", "full")
    if view == "return" and claims:
        direct = [claim for claim in claims if not claim.get("call_chain")]
        propagated_count = len(claims) - len(direct)
        inputs = sorted({
            name
            for claim in direct
            for name in claim["statement"].get("inputs", [])
        })
        count = len(direct)
        headline = (
            f"{count} return {'path' if count == 1 else 'paths'} in the selected function."
        )
        detail = (
            "Recorded caller result dependencies include " + ", ".join(inputs) + "."
            if inputs
            else "Open a path to inspect its recorded values, calls, and limits."
        )
        if propagated_count:
            detail += (
                f" {propagated_count} more return "
                f"{'path is' if propagated_count == 1 else 'paths are'} kept inside local-call evidence."
            )
        return {"headline": headline, "detail": detail}

    if view == "mutation":
        direct = [claim for claim in claims if not claim.get("call_chain")]
        propagated = [claim for claim in claims if claim.get("call_chain")]
        writes = [claim for claim in direct if claim["kind"] == "attempted_write"]
        effects = [claim for claim in direct if claim["kind"] == "known_effect"]
        propagated_writes = [
            claim for claim in propagated if claim["kind"] == "attempted_write"
        ]
        propagated_effects = [
            claim for claim in propagated if claim["kind"] == "known_effect"
        ]
        if not writes and not effects and not propagated_writes and not propagated_effects:
            if card.get("boundaries"):
                return {
                    "headline": "No supported write sites or registered effect sites.",
                    "detail": "Effect-relevant unresolved calls are listed as limits, not treated as effects.",
                }
            return None
        targets = {
            claim["statement"].get("source_text", claim["statement"]["text"])
            for claim in writes
        }
        parts = []
        if writes:
            parts.append(
                f"{len(writes)} recorded {'write site' if len(writes) == 1 else 'write sites'} "
                f"across {len(targets)} {'target' if len(targets) == 1 else 'targets'}"
            )
        if effects:
            parts.append(
                f"{len(effects)} registered external "
                f"{'effect site' if len(effects) == 1 else 'effect sites'}"
            )
        if propagated_writes:
            parts.append(
                f"{len(propagated_writes)} "
                f"{'write site' if len(propagated_writes) == 1 else 'write sites'} "
                "inside local calls"
            )
        if propagated_effects:
            parts.append(
                f"{len(propagated_effects)} registered external "
                f"{'effect site' if len(propagated_effects) == 1 else 'effect sites'} "
                "inside local calls"
            )
        headline = " and ".join(parts).capitalize() + "."
        detail = (
            "Unresolved calls remain separate because Saga cannot classify their effects."
        )
        return {"headline": headline, "detail": detail}

    if view == "failure" and claims:
        direct = [claim for claim in claims if not claim.get("call_chain")]
        rejected = sum(claim["kind"] == "rejected_input" for claim in direct)
        escaping = sum(claim["kind"] == "explicit_exception" for claim in direct)
        parts = []
        if rejected:
            parts.append(
                f"{rejected} rejected-input {'case' if rejected == 1 else 'cases'}"
            )
        if escaping:
            parts.append(
                f"{escaping} explicit {'exception' if escaping == 1 else 'exceptions'}"
            )
        return {
            "headline": " and ".join(parts).capitalize() + ".",
            "detail": "Conditions and handler evidence remain attached to each result.",
        }

    if view == "boundary" and card.get("boundaries"):
        keys = {
            (item["kind"], item["target"]["text"], item["reason"])
            for item in card["boundaries"]
        }
        count = len(keys)
        return {
            "headline": f"{count} analysis {'limit' if count == 1 else 'limits'}.",
            "detail": "Direct caller limits appear before evidence carried through local calls and routine unresolved calls.",
        }
    return None


def prioritize_boundary_groups(
    groups: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    view: str,
) -> dict[str, list[dict[str, Any]]]:
    """Order limits using explicit evidence links and call scope only."""
    claim_by_boundary: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        for boundary_id in claim.get("boundary_ids", []):
            claim_by_boundary.setdefault(boundary_id, []).append(claim)

    tiers = {"primary": [], "related": [], "propagated": [], "routine": []}
    for group in groups:
        linked = []
        seen_claims = set()
        for boundary_id in group["boundary_ids"]:
            for claim in claim_by_boundary.get(boundary_id, []):
                if claim["id"] not in seen_claims:
                    linked.append(claim)
                    seen_claims.add(claim["id"])
        propagated = any(
            occurrence.get("call_chain") for occurrence in group["occurrences"]
        )
        presented = {**group}
        presented["limited_claim_ids"] = [claim["id"] for claim in linked]
        presented["limited_claim_kinds"] = sorted({claim["kind"] for claim in linked})
        if group["category"] == "routine":
            tier = "routine"
            relevance = "Retained as an unresolved routine call without modeled semantics."
        elif propagated:
            tier = "propagated"
            relevance = "Comes from evidence inside a module-local call."
        elif linked or view == "boundary":
            tier = "primary"
            if linked:
                labels = sorted({_ANSWER_LABELS.get(claim["kind"], "displayed answer") for claim in linked})
                relevance = "Directly limits the " + " and ".join(labels) + "."
            else:
                relevance = "Stops analysis in the selected function."
        else:
            tier = "related"
            relevance = {
                "return": "May limit result dependencies that Saga could not resolve.",
                "mutation": "May add effects that Saga could not classify.",
                "failure": "May add failure behavior that Saga could not resolve.",
            }.get(view, "Stops analysis outside the displayed claims.")
        presented["priority"] = tier
        presented["relevance"] = relevance
        tiers[tier].append(presented)
    return tiers
