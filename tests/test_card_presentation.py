from __future__ import annotations

import copy
import unittest

from saga.boundaries import group_boundaries
from saga.card_presentation import focused_answer, group_claims, prioritize_boundary_groups
from saga.inspect import inspect_function
from saga.views import focus_card


class CardPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.card = inspect_function("fixture/process_order.py", "process_order")

    def test_repeated_writes_group_without_changing_records(self) -> None:
        write = next(claim for claim in self.card["claims"] if claim["kind"] == "attempted_write")
        repeated = copy.deepcopy(write)
        repeated["id"] = "second-write"
        repeated["source_spans"][0]["start_line"] += 1
        claims = [write, repeated]
        before = copy.deepcopy(claims)

        groups = group_claims(claims)

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(groups[0]["claim_ids"], [write["id"], "second-write"])
        self.assertEqual(groups[0]["claims"], claims)
        self.assertEqual(claims, before)

    def test_different_write_targets_do_not_group(self) -> None:
        write = next(claim for claim in self.card["claims"] if claim["kind"] == "attempted_write")
        other = copy.deepcopy(write)
        other["id"] = "other-write"
        other["statement"]["source_text"] = "other.status"
        self.assertEqual(len(group_claims([write, other])), 2)

    def test_mutation_answer_keeps_unknown_effects_separate(self) -> None:
        focused = focus_card(self.card, "mutation")
        answer = focused_answer(focused, focused["claims"])
        self.assertIsNotNone(answer)
        self.assertIn("possible write", answer["headline"])
        self.assertIn("Unresolved calls remain separate", answer["detail"])

    def test_boundaries_are_ranked_by_explicit_claim_links_and_scope(self) -> None:
        focused = focus_card(self.card, "mutation")
        groups = group_boundaries(focused)
        before = copy.deepcopy(groups)

        tiers = prioritize_boundary_groups(groups, focused["claims"], "mutation")

        linked_ids = {item for claim in focused["claims"] for item in claim["boundary_ids"]}
        for group in tiers["primary"]:
            self.assertTrue(linked_ids.intersection(group["boundary_ids"]))
            self.assertIn("Directly limits", group["relevance"])
        retained = sum((tier for tier in tiers.values()), [])
        self.assertCountEqual(
            [boundary_id for group in retained for boundary_id in group["boundary_ids"]],
            [boundary_id for group in groups for boundary_id in group["boundary_ids"]],
        )
        self.assertEqual(groups, before)


if __name__ == "__main__":
    unittest.main()
