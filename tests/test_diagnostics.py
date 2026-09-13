import copy
import tempfile
import unittest
from pathlib import Path

from saga.diagnostics import (
    BLOCKING_DIAGNOSTIC_KINDS,
    card_exit_code,
    deduplicate_diagnostics,
    group_diagnostics,
)
from saga.inspect import inspect_function
from saga.render import terminal


def span(line: int) -> dict:
    return {
        "path": "module.py",
        "start_line": line,
        "start_column": 4,
        "end_line": line,
        "end_column": 12,
    }


def card(diagnostics: list[dict]) -> dict:
    return {
        "schema_version": "0.1",
        "target": {
            "path": "module.py",
            "qualified_name": "target",
            "name": "target",
            "signature": "target()",
            "status": "supported",
            "source_span": span(1),
        },
        "claims": [],
        "boundaries": [],
        "diagnostics": diagnostics,
    }


class DiagnosticGroupingTests(unittest.TestCase):
    """Check that human grouping retains the raw diagnostic evidence."""

    def test_equal_reports_group_without_changing_raw_diagnostics(self):
        raw = card([
            {"kind": "unsupported_semantics", "message": "Continue is unsupported.", "source_span": span(8)},
            {"kind": "unsupported_semantics", "message": "Continue is unsupported.", "source_span": span(12)},
            {"kind": "unsupported_semantics", "message": "Continue is unsupported.", "source_span": span(12)},
        ])
        before = copy.deepcopy(raw)
        groups = group_diagnostics(raw)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["report_count"], 3)
        self.assertEqual(groups[0]["site_count"], 2)
        self.assertEqual(
            [item["source_span"]["start_line"] for item in groups[0]["occurrences"]],
            [8, 12],
        )
        self.assertEqual(raw, before)

    def test_different_kinds_or_messages_remain_separate(self):
        groups = group_diagnostics(card([
            {"kind": "unsupported_semantics", "message": "Try is unsupported.", "source_span": span(4)},
            {"kind": "unsupported_semantics", "message": "Continue is unsupported.", "source_span": span(5)},
            {"kind": "instrumentation", "message": "Try is unsupported.", "source_span": span(4)},
        ]))
        self.assertEqual(len(groups), 3)

    def test_only_failures_that_prevent_a_usable_card_exit_nonzero(self):
        partial = card([
            {"kind": "unsupported_semantics", "message": "Part of the function was not modeled."},
        ])
        self.assertEqual(card_exit_code(partial), 0)

        expected = {
            "missing_file",
            "invalid_file",
            "file_error",
            "parsing",
            "unsupported_target",
            "target_not_found",
            "ambiguous_target",
            "test_run",
            "instrumentation",
        }
        self.assertEqual(BLOCKING_DIAGNOSTIC_KINDS, expected)
        for kind in expected:
            with self.subTest(kind=kind):
                self.assertEqual(
                    card_exit_code(card([{"kind": kind, "message": "blocked"}])),
                    1,
                )

    def test_producer_deduplication_uses_kind_message_and_exact_span(self):
        repeated = {"kind": "unsupported_semantics", "message": "Continue is unsupported.", "analyses": ["returns"], "source_span": span(8)}
        other_analysis = {**copy.deepcopy(repeated), "analyses": ["effects"]}
        other_site = {**copy.deepcopy(repeated), "source_span": span(12)}
        result = deduplicate_diagnostics([repeated, copy.deepcopy(repeated), other_analysis, other_site])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["analyses"], ["effects", "returns"])
        self.assertEqual([item["source_span"]["start_line"] for item in result], [8, 12])

    def test_nested_control_flow_keeps_four_distinct_continue_sites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "billing.py"
            path.write_text(
                "def bill(records, declined):\n"
                "    for record in records:\n"
                "        try:\n"
                "            if record == 1:\n"
                "                continue\n"
                "            if record == 2:\n"
                "                continue\n"
                "        except ValueError:\n"
                "            continue\n"
                "        finally:\n"
                "            if record == 3:\n"
                "                continue\n"
                "    return declined\n",
                encoding="utf-8",
            )
            card = inspect_function(str(path), "bill")
        continues = [
            diagnostic for diagnostic in card["diagnostics"]
            if diagnostic["message"].startswith("Saga uses this continue")
        ]
        self.assertEqual(len(continues), 4)
        self.assertEqual(
            {item["source_span"]["start_line"] for item in continues},
            {5, 7, 9, 12},
        )

    def test_terminal_collapses_repeated_reports_and_can_expand_sites(self):
        raw = card([
            {"kind": "unsupported_semantics", "message": "Try is unsupported.", "source_span": span(4)},
            {"kind": "unsupported_semantics", "message": "Try is unsupported.", "source_span": span(9)},
        ])
        compact = terminal(raw)
        self.assertIn("2 reports at 2 sites", compact)
        self.assertIn("--show-diagnostic-sites", compact)
        self.assertNotIn("module.py:4", compact)
        expanded = terminal(raw, show_diagnostic_sites=True)
        self.assertIn("module.py:4", expanded)
        self.assertIn("module.py:9", expanded)


if __name__ == "__main__":
    unittest.main()
