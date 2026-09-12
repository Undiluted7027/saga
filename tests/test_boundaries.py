import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from saga.boundaries import group_boundaries
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


def boundary(
    boundary_id: str,
    line: int,
    target: str = "external(...)",
    reason: str = "The callee is unresolved.",
    kind: str = "unresolved_call",
    category: str = "important",
) -> dict:
    return {
        "id": boundary_id,
        "kind": kind,
        "target": {"text": target},
        "reason": reason,
        "category": category,
        "source_span": span(line),
    }


def card(boundaries: list[dict], claims: list[dict] | None = None) -> dict:
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
        "claims": claims or [],
        "boundaries": boundaries,
        "diagnostics": [],
    }


class BoundaryGroupingTests(unittest.TestCase):
    """Check that presentation grouping never erases analysis evidence."""

    def test_repeated_target_and_reason_form_one_source_preserving_group(self):
        raw = card([
            boundary("one", 4),
            boundary("two", 9),
        ])
        before = copy.deepcopy(raw)
        groups = group_boundaries(raw)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(groups[0]["boundary_ids"], ["one", "two"])
        self.assertEqual(
            [item["source_span"]["start_line"] for item in groups[0]["occurrences"]],
            [4, 9],
        )
        self.assertEqual(raw, before)

    def test_same_target_with_different_stop_reasons_stays_separate(self):
        groups = group_boundaries(card([
            boundary("one", 4, reason="The callee is unresolved."),
            boundary("two", 9, reason="The call exceeds the local hop limit."),
        ]))
        self.assertEqual(len(groups), 2)

    def test_classes_distinguish_local_dynamic_external_and_routine_calls(self):
        groups = group_boundaries(card([
            boundary("local", 2, kind="local_call_limit"),
            boundary("dynamic", 3, target="order.status", kind="assignment_hooks"),
            boundary("external", 4),
            boundary("routine", 5, target="len(...)", category="routine"),
        ]))
        self.assertEqual(
            [group["boundary_class"] for group in groups],
            [
                "module_local",
                "dynamic_behavior",
                "external_or_unresolved_call",
                "routine_call",
            ],
        )

    def test_group_records_which_claims_are_limited(self):
        return_claim = {
            "id": "return",
            "kind": "return_dependency",
            "statement": {"text": "Returns result."},
            "evidence": {"method": "test", "evidence_class": "derived", "detail": {}},
            "source_spans": [span(8)],
            "assumptions": [],
            "boundary_ids": ["one"],
        }
        effect_claim = {
            **copy.deepcopy(return_claim),
            "id": "effect",
            "kind": "known_effect",
        }
        group = group_boundaries(
            card([boundary("one", 4)], [return_claim, effect_claim])
        )[0]
        self.assertEqual(group["claim_kinds"], ["known_effect", "return_dependency"])

    def test_group_preserves_boundaries_that_limit_other_boundaries(self):
        call = boundary("call", 4)
        call["boundary_ids"] = ["conditional"]
        group = group_boundaries(card([
            call,
            boundary("conditional", 2, target="try statement", kind="unsupported_semantics"),
        ]))[0]
        self.assertEqual(group["limiting_boundary_ids"], ["conditional"])
        self.assertEqual(group["occurrences"][0]["boundary_ids"], ["conditional"])
        self.assertIn("limited by conditional", terminal(card([call])))

    def test_terminal_groups_prominent_sites_and_hides_routine_details_by_default(self):
        raw = card([
            boundary("one", 4),
            boundary("two", 9),
            boundary("routine-one", 10, target="len(...)", category="routine"),
            boundary("routine-two", 12, target="len(...)", category="routine"),
        ])
        compact = terminal(raw)
        self.assertIn("Boundaries: 4 sites in 2 groups", compact)
        self.assertIn("external(...) — 2 call sites", compact)
        self.assertNotIn("module.py:4", compact)
        self.assertIn("--show-boundary-sites", compact)
        self.assertIn("Routine unresolved calls: 2 sites in 1 group", compact)
        self.assertNotIn("routine-one", compact)
        self.assertIn("--show-routine-boundaries", compact)

        expanded = terminal(
            raw,
            show_routine_boundaries=True,
            show_boundary_sites=True,
        )
        self.assertIn("routine-one", expanded)
        self.assertIn("module.py:4", expanded)
        self.assertIn("module.py:9", expanded)
        self.assertIn("module.py:10", expanded)
        self.assertIn("module.py:12", expanded)

    def test_cli_flags_expand_routine_groups_and_repeated_sites(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        path = Path(tempdir.name) / "module.py"
        path.write_text(
            "def target(first, second):\n"
            "    left = len(first)\n"
            "    right = len(second)\n"
            "    return left + right\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "saga.cli",
                "inspect",
                f"{path}::target",
                "--format",
                "terminal",
                "--show-routine-boundaries",
                "--show-boundary-sites",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("len(...) — 2 call sites", result.stdout)
        self.assertIn(f"{path}:2", result.stdout)
        self.assertIn(f"{path}:3", result.stdout)

    def test_mutating_method_is_prominent_while_read_only_method_is_routine(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        path = Path(tempdir.name) / "module.py"
        path.write_text(
            "def target(values, mapping):\n"
            "    values.append(1)\n"
            "    return mapping.get('key')\n",
            encoding="utf-8",
        )
        groups = group_boundaries(inspect_function(str(path), "target"))
        by_target = {group["target"]: group for group in groups}
        self.assertEqual(by_target["values.append(...)"]["category"], "important")
        self.assertEqual(by_target["mapping.get(...)"]["category"], "routine")


if __name__ == "__main__":
    unittest.main()
