import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from saga.inspect import inspect_function
from saga.render import terminal
from saga.views import EMPTY_MESSAGES, focus_card

SOURCE = """\
from pathlib import Path

def target(order, path):
    if not order.items:
        raise ValueError("empty")
    order.status = "ready"
    Path(path).write_text("ready")
    total = calculate(order)
    return total
"""


class FocusedViewTests(unittest.TestCase):
    """Verify fixed views are projections of one full evidence card."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "module.py"
        self.path.write_text(SOURCE, encoding="utf-8")
        self.full = inspect_function(str(self.path), "target")

    def test_return_view_keeps_claim_identity_and_only_related_boundaries(self):
        focused = focus_card(self.full, "return")
        self.assertEqual({claim["kind"] for claim in focused["claims"]}, {"return_dependency"})
        self.assertIs(focused["claims"][0], next(
            claim for claim in self.full["claims"] if claim["kind"] == "return_dependency"
        ))
        related_ids = set(focused["claims"][0]["boundary_ids"])
        self.assertEqual({item["id"] for item in focused["boundaries"]}, related_ids)
        self.assertEqual(
            focused["claims"][0]["source_spans"],
            next(
                claim["source_spans"]
                for claim in self.full["claims"]
                if claim["id"] == focused["claims"][0]["id"]
            ),
        )

    def test_mutation_and_failure_views_use_fixed_claim_kinds(self):
        mutation = focus_card(self.full, "mutation")
        failure = focus_card(self.full, "failure")
        self.assertEqual(
            {claim["kind"] for claim in mutation["claims"]},
            {"attempted_write", "known_effect"},
        )
        self.assertEqual(
            {claim["kind"] for claim in failure["claims"]},
            {"rejected_input", "explicit_exception"},
        )
        for focused in (mutation, failure):
            related = {
                boundary_id
                for claim in focused["claims"]
                for boundary_id in claim["boundary_ids"]
            }
            self.assertEqual({item["id"] for item in focused["boundaries"]}, related)

    def test_boundary_view_has_boundaries_without_unrelated_claims(self):
        focused = focus_card(self.full, "boundary")
        self.assertFalse(focused["claims"])
        self.assertEqual(focused["boundaries"], self.full["boundaries"])
        self.assertIs(focused["boundaries"][0], self.full["boundaries"][0])

    def test_full_view_returns_the_unmodified_card(self):
        self.assertIs(focus_card(self.full, "full"), self.full)
        self.assertNotIn("view", self.full)

    def test_unsupported_target_diagnostic_survives_a_focused_view(self):
        unsupported_path = Path(self.tempdir.name) / "unsupported.py"
        unsupported_path.write_text("async def target():\n    pass\n", encoding="utf-8")
        focused = focus_card(
            inspect_function(str(unsupported_path), "target"),
            "return",
        )
        self.assertEqual(focused["target"]["status"], "unsupported")
        self.assertEqual(focused["diagnostics"][0]["kind"], "unsupported_target")

    def test_partial_analysis_diagnostic_survives_a_focused_view(self):
        partial_path = Path(self.tempdir.name) / "partial.py"
        partial_path.write_text(
            "def target(resource):\n"
            "    with resource:\n"
            "        return 1\n",
            encoding="utf-8",
        )
        full = inspect_function(str(partial_path), "target")
        self.assertTrue(full["diagnostics"])
        self.assertEqual(
            focus_card(full, "return")["diagnostics"],
            full["diagnostics"],
        )

    def test_empty_view_disclaims_absence_and_points_to_full_card(self):
        empty_path = Path(self.tempdir.name) / "empty.py"
        empty_path.write_text("def target():\n    pass\n", encoding="utf-8")
        focused = focus_card(inspect_function(str(empty_path), "target"), "failure")
        output = terminal(focused)
        self.assertIn(EMPTY_MESSAGES["failure"], output)
        self.assertIn("Full card: omit --view.", output)
        self.assertNotIn("cannot fail", EMPTY_MESSAGES["failure"].split("This does not establish")[0])

    def test_cli_exposes_each_view_in_json_and_terminal(self):
        for view in ("return", "mutation", "failure", "boundary"):
            with self.subTest(view=view):
                json_result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "saga.cli",
                        "inspect",
                        f"{self.path}::target",
                        "--view",
                        view,
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                card = json.loads(json_result.stdout)
                self.assertEqual(card["view"], view)
                terminal_result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "saga.cli",
                        "inspect",
                        f"{self.path}::target",
                        "--view",
                        view,
                        "--format",
                        "terminal",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertIn("View:", terminal_result.stdout)


if __name__ == "__main__":
    unittest.main()
