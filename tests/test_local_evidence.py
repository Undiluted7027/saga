import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from saga.inspect import inspect_function
from saga.local_evidence import partition_local_call_evidence
from saga.render import terminal
from saga.views import focus_card


def span(line: int) -> dict:
    return {
        "path": "target.py",
        "start_line": line,
        "start_column": 4,
        "end_line": line,
        "end_column": 12,
    }


def link(callee: str, line: int) -> dict:
    return {
        "caller": "target",
        "callee": callee,
        "invoked_as": callee,
        "via_alias": False,
        "call_site": span(line),
        "callee_span": span(line + 100),
        "argument_bindings": [],
    }


class LocalEvidencePartitionTests(unittest.TestCase):
    def test_groups_evidence_by_first_call_site_without_mutating_the_card(self):
        first = link("helper", 5)
        second = link("helper", 9)
        card = {
            "claims": [
                {"id": "direct"},
                {"id": "first-claim", "call_chain": [first]},
                {"id": "second-claim", "call_chain": [second]},
            ],
            "boundaries": [
                {"id": "first-boundary", "call_chain": [first]},
                {"id": "direct-boundary"},
            ],
            "diagnostics": [
                {"message": "First gap", "call_chain": [first]},
                {"message": "Direct gap"},
            ],
        }
        before = copy.deepcopy(card)

        result = partition_local_call_evidence(card)

        self.assertEqual(
            result["direct"],
            {
                "claims": [card["claims"][0]],
                "boundaries": [card["boundaries"][1]],
                "diagnostics": [card["diagnostics"][1]],
            },
        )
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(result["groups"][0]["call_site"], span(5))
        self.assertEqual(
            result["groups"][0]["claims"],
            [card["claims"][1]],
        )
        self.assertEqual(
            result["groups"][0]["boundaries"],
            [card["boundaries"][0]],
        )
        self.assertEqual(
            result["groups"][0]["diagnostics"],
            [card["diagnostics"][0]],
        )
        self.assertEqual(result["groups"][1]["call_site"], span(9))
        self.assertEqual(card, before)

    def test_uses_only_the_first_hop_for_nested_call_chains(self):
        outer = link("helper", 5)
        inner = {
            **link("nested", 105),
            "caller": "helper",
        }
        item = {"id": "nested-boundary", "call_chain": [outer, inner]}

        result = partition_local_call_evidence(
            {"claims": [], "boundaries": [item], "diagnostics": []}
        )

        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["callee"], "helper")
        self.assertEqual(result["groups"][0]["boundaries"], [item])


class LocalEvidenceRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "target.py"
        self.path.write_text(
            "def helper(state, notify):\n"
            "    state.remote = 1\n"
            "    while state.pending:\n"
            "        notify(state)\n\n"
            "def target(state, notify):\n"
            "    state.local = 1\n"
            "    helper(state, notify)\n",
            encoding="utf-8",
        )
        self.full = inspect_function(str(self.path), "target")
        self.focused = focus_card(self.full, "mutation")

    def test_focused_terminal_keeps_direct_evidence_ahead_of_collapsed_callee(self):
        before = copy.deepcopy(self.focused)

        compact = terminal(self.focused)

        direct = compact.index("Attempts to write to state.local")
        grouped = compact.index("Local call helper(...)")
        self.assertLess(direct, grouped)
        self.assertNotIn("helper(...) may attempt to write to state.remote", compact)
        self.assertIn("Evidence is collapsed", compact)
        self.assertIn("1 claims, 3 boundary groups, 1 diagnostic groups", compact)
        self.assertEqual(self.focused, before)

    def test_expansion_preserves_propagated_claims_boundaries_and_call_chain(self):
        expanded = terminal(self.focused, show_local_call_evidence=True)
        partition = partition_local_call_evidence(self.focused)

        self.assertIn("helper(...) may attempt to write to state.remote", expanded)
        self.assertIn("notify(...)", expanded)
        self.assertIn("state.remote", expanded)
        self.assertIn("While-loop analysis is outside", expanded)
        self.assertIn("Local call: target -> helper", expanded)
        for group in partition["groups"]:
            spans = [
                *(span for claim in group["claims"] for span in claim["source_spans"]),
                *(boundary["source_span"] for boundary in group["boundaries"]),
                *(
                    diagnostic["source_span"]
                    for diagnostic in group["diagnostics"]
                    if diagnostic.get("source_span")
                ),
            ]
            for source_span in spans:
                self.assertIn(
                    f"{source_span['path']}:{source_span['start_line']}",
                    expanded,
                )

    def test_full_card_keeps_existing_evidence_expanded(self):
        rendered = terminal(self.full)

        self.assertIn("helper(...) may attempt to write to state.remote", rendered)
        self.assertNotIn("Evidence is collapsed", rendered)

    def test_cli_flag_expands_local_call_evidence(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "saga.cli",
                "inspect",
                f"{self.path}::target",
                "--format",
                "terminal",
                "--view",
                "mutation",
                "--show-local-call-evidence",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertIn("helper(...) may attempt to write to state.remote", result.stdout)
        self.assertIn("notify(...)", result.stdout)

    def test_cli_grouping_flag_does_not_change_json(self):
        command = [
            sys.executable,
            "-m",
            "saga.cli",
            "inspect",
            f"{self.path}::target",
            "--format",
            "json",
            "--view",
            "mutation",
        ]
        compact = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
        )
        expanded = subprocess.run(
            [*command, "--show-local-call-evidence"],
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(compact.stdout, expanded.stdout)


if __name__ == "__main__":
    unittest.main()
