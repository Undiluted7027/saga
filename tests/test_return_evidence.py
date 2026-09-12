import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from saga.inspect import inspect_function
from saga.render import terminal
from saga.return_evidence import return_path_presentation
from saga.views import focus_card


def span(line: int) -> dict:
    return {
        "path": "target.py",
        "start_line": line,
        "start_column": 4,
        "end_line": line,
        "end_column": 16,
    }


def dependency(
    kind: str,
    line: int,
    *,
    names: list[str] | None = None,
    reads: list[str] | None = None,
    calls: list[str] | None = None,
) -> dict:
    return {
        "kind": kind,
        "names": names or [],
        "reads": reads or [],
        "calls": [
            {"text": call, "source_span": span(line)} for call in calls or []
        ],
        "source_span": span(line),
    }


def return_claim(dependencies: list[dict], extra_spans: list[dict] | None = None) -> dict:
    return {
        "id": "return-20-4",
        "kind": "return_dependency",
        "statement": {
            "text": "Returns result when enabled is truthy.",
            "type": "return_dependency",
            "source_text": "result",
            "return_expression": "result",
            "path_conditions": [
                {
                    "text": "enabled is truthy",
                    "source_text": "enabled",
                    "source_span": span(2),
                }
            ],
            "dependencies": dependencies,
        },
        "evidence": {
            "method": "intraprocedural_may_affect",
            "evidence_class": "derived",
            "detail": {"return_source_span": span(20)},
        },
        "source_spans": [
            *(item["source_span"] for item in dependencies),
            *(extra_spans or []),
        ],
        "assumptions": [],
        "boundary_ids": [],
    }


class ReturnPathPresentationTests(unittest.TestCase):
    def test_groups_all_recorded_facts_without_mutating_the_claim(self):
        claim = return_claim(
            [
                dependency("definition", 3, names=["result"], reads=["seed"]),
                dependency("definition", 4, names=["result"], reads=["item"]),
                dependency("weak_definition", 5, names=["result"], reads=["factor"]),
                dependency("control_predicate", 6, reads=["enabled"]),
                dependency("statement", 7, reads=["result"], calls=["normalize(...)"]),
                dependency("statement", 8, calls=["audit(...)"]),
                dependency("statement", 9, reads=["result"]),
                dependency("statement", 10, reads=["fallback"]),
                dependency("return", 20, reads=["result"]),
            ]
        )
        before = copy.deepcopy(claim)

        view = return_path_presentation(claim)

        self.assertTrue(view["compact"])
        self.assertEqual(view["site_count"], 9)
        self.assertEqual(
            [group["kind"] for group in view["groups"]],
            ["return", "definition", "weak_definition", "control_predicate", "statement"],
        )
        definitions = next(group for group in view["groups"] if group["kind"] == "definition")
        self.assertEqual(definitions["count"], 2)
        self.assertEqual(definitions["names"], ["result"])
        self.assertEqual(definitions["reads"], ["item", "seed"])
        statements = next(group for group in view["groups"] if group["kind"] == "statement")
        self.assertEqual(statements["calls"], ["audit(...)", "normalize(...)"])
        self.assertEqual(claim, before)

    def test_threshold_keeps_eight_sites_expanded_and_groups_nine(self):
        eight = return_claim(
            [dependency("statement", line) for line in range(1, 8)]
            + [dependency("return", 20)]
        )
        nine = return_claim(
            [dependency("statement", line) for line in range(1, 9)]
            + [dependency("return", 20)]
        )

        self.assertFalse(return_path_presentation(eight)["compact"])
        self.assertTrue(return_path_presentation(nine)["compact"])


class ReturnPathRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "target.py"
        self.path.write_text(
            "def choose(enabled, seed, items):\n"
            "    if enabled:\n"
            "        result = []\n"
            "        result.append(seed)\n"
            "        result.append(items[0])\n"
            "        result.append(items[1])\n"
            "        result.append(items[2])\n"
            "        result.append(items[3])\n"
            "        result.append(items[4])\n"
            "        result.append(items[5])\n"
            "        return result\n"
            "    return seed\n",
            encoding="utf-8",
        )
        self.full = inspect_function(str(self.path), "choose")
        self.focused = focus_card(self.full, "return")

    def test_large_and_small_paths_remain_separate_and_scannable(self):
        rendered = terminal(self.focused)

        self.assertIn("Return path 1: result", rendered)
        self.assertIn("Return path 2: seed", rendered)
        self.assertIn("Dependency sites:", rendered)
        self.assertIn("--show-return-sites", rendered)
        self.assertLessEqual(rendered.count("Source:"), 8)

    def test_expansion_preserves_every_dependency_and_claim_source_span(self):
        large = next(
            claim
            for claim in self.focused["claims"]
            if len(claim["statement"]["dependencies"]) > 8
        )
        additional = span(99)
        large["source_spans"].append(additional)

        rendered = terminal(self.focused, show_return_sites=True)

        for item in large["statement"]["dependencies"]:
            source = item["source_span"]
            self.assertIn(f"{source['path']}:{source['start_line']}", rendered)
        self.assertIn("Additional claim source: target.py:99", rendered)

    def test_cli_flag_does_not_change_json(self):
        command = [
            sys.executable,
            "-m",
            "saga.cli",
            "inspect",
            f"{self.path}::choose",
            "--format",
            "json",
            "--view",
            "return",
        ]
        compact = subprocess.run(command, capture_output=True, text=True, check=True)
        expanded = subprocess.run(
            [*command, "--show-return-sites"],
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(compact.stdout, expanded.stdout)
        json.loads(compact.stdout)


if __name__ == "__main__":
    unittest.main()
