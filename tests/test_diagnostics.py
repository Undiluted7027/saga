import copy
import unittest

from saga.diagnostics import group_diagnostics
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
