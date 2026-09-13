import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from saga.inspect import inspect_function
from saga.render import terminal
from saga.views import EMPTY_MESSAGES, focus_card, view_is_empty

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
        mutation_related = {
            boundary_id
            for claim in mutation["claims"]
            for boundary_id in claim["boundary_ids"]
        }
        self.assertEqual(
            {item["id"] for item in mutation["boundaries"]}, mutation_related
        )
        failure_related = {
            boundary_id
            for claim in failure["claims"]
            for boundary_id in claim["boundary_ids"]
        }
        self.assertTrue(
            failure_related <= {item["id"] for item in failure["boundaries"]}
        )
        self.assertTrue(
            all(
                item["id"] in failure_related
                or "exceptions" in item.get("concerns", [])
                for item in failure["boundaries"]
            )
        )

    def test_mutation_view_keeps_only_effect_relevant_opaque_calls(self):
        path = Path(self.tempdir.name) / "effects.py"
        path.write_text(
            "from pathlib import Path\n\n"
            "def target(notify, client, values, data, path):\n"
            "    notify(data)\n"
            "    response = client.send(data)\n"
            "    values.append(response)\n"
            "    size = len(data)\n"
            "    transformed = calculate(size)\n"
            "    Path(path).write_text(str(transformed))\n"
            "    return transformed\n",
            encoding="utf-8",
        )
        full = inspect_function(str(path), "target")
        focused = focus_card(full, "mutation")
        targets = {item["target"]["text"] for item in focused["boundaries"]}
        self.assertEqual(
            targets,
            {"notify(...)", "client.send(...)", "values.append(...)"},
        )
        self.assertTrue(
            all("effects" in item["concerns"] for item in focused["boundaries"])
        )
        self.assertTrue(
            all("cannot determine" in item["reason"] for item in focused["boundaries"])
        )
        self.assertNotIn("len(...)", targets)
        self.assertNotIn("calculate(...)", targets)
        self.assertEqual(
            [claim["kind"] for claim in focused["claims"]],
            ["known_effect"],
        )
        self.assertTrue(
            {"len(...)", "calculate(...)"}
            <= {item["target"]["text"] for item in full["boundaries"]}
        )

    def test_effect_boundary_alone_makes_mutation_view_nonempty(self):
        path = Path(self.tempdir.name) / "callback.py"
        path.write_text(
            "def target(notify, payload):\n"
            "    notify(payload)\n",
            encoding="utf-8",
        )
        focused = focus_card(inspect_function(str(path), "target"), "mutation")
        self.assertFalse(focused["claims"])
        self.assertEqual(
            [item["target"]["text"] for item in focused["boundaries"]],
            ["notify(...)"],
        )
        self.assertFalse(view_is_empty(focused))
        self.assertNotIn(EMPTY_MESSAGES["mutation"], terminal(focused))

    def test_callback_concern_survives_guard_boundary_rewriting(self):
        path = Path(self.tempdir.name) / "guard_callback.py"
        path.write_text(
            "def target(check, payload):\n"
            "    if check(payload):\n"
            "        return 1\n"
            "    return 0\n",
            encoding="utf-8",
        )
        focused = focus_card(inspect_function(str(path), "target"), "mutation")
        self.assertEqual(len(focused["boundaries"]), 1)
        boundary = focused["boundaries"][0]
        self.assertEqual(boundary["concerns"], ["effects", "exceptions"])
        self.assertIn("cannot determine whether this call mutates state", boundary["reason"])

    def test_failure_view_keeps_unresolved_calls_as_exception_limits(self):
        path = Path(self.tempdir.name) / "failure_callback.py"
        path.write_text(
            "def target(callback, payload):\n"
            "    result = callback(payload)\n"
            "    return result\n",
            encoding="utf-8",
        )
        focused = focus_card(inspect_function(str(path), "target"), "failure")
        self.assertFalse(focused["claims"])
        self.assertEqual(
            [item["target"]["text"] for item in focused["boundaries"]],
            ["callback(...)"],
        )
        self.assertIn("exceptions", focused["boundaries"][0]["concerns"])
        output = terminal(focused)
        self.assertIn("No supported explicit failure claims", output)
        self.assertIn("callback(...)", output)
        self.assertFalse(view_is_empty(focused))

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
        focused = focus_card(full, "return")
        self.assertEqual(
            {analysis for item in focused["diagnostics"] for analysis in item["analyses"]},
            {"returns"},
        )
        self.assertEqual(focused["hidden_diagnostics"]["group_count"], 1)
        self.assertIn("open the full card", focused["hidden_diagnostics"]["message"])
        self.assertIn(focused["hidden_diagnostics"]["message"], terminal(focused))

    def test_each_focused_view_keeps_only_relevant_diagnostic_analyses(self):
        path = Path(self.tempdir.name) / "diagnostic_views.py"
        path.write_text(
            "def target(state, items):\n"
            "    for item in items:\n"
            "        try:\n"
            "            state.value = item\n"
            "        except ValueError:\n"
            "            continue\n"
            "    return state\n",
            encoding="utf-8",
        )
        full = inspect_function(str(path), "target")
        expected = {
            "return": {"returns"},
            "mutation": {"effects"},
            "failure": set(),
            "boundary": set(),
        }
        for view, analyses in expected.items():
            with self.subTest(view=view):
                focused = focus_card(full, view)
                retained = {
                    analysis
                    for diagnostic in focused["diagnostics"]
                    for analysis in diagnostic["analyses"]
                }
                self.assertEqual(retained, analyses)
                self.assertIn("hidden_diagnostics", focused)
        mutation = focus_card(full, "mutation")
        self.assertEqual(len(mutation["diagnostics"]), 1)
        self.assertIn("Try/except effect", mutation["diagnostics"][0]["message"])
        self.assertFalse(any("Continue" in item["message"] for item in mutation["diagnostics"]))
        self.assertGreater(len(full["diagnostics"]), len(mutation["diagnostics"]))

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
