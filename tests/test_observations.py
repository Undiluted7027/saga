import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from saga.observations import evaluate_observations
from saga.pytest_plugin import TargetTracer
from saga.render import terminal
from saga.trace import contains_unusable_value, serialize_value


def card():
    """Build the smallest source-linked card needed by observation evaluation."""
    return {"target": {"source_span": {"path": "module.py", "start_line": 1, "start_column": 0, "end_line": 2, "end_column": 0}}}


class ObservationTests(unittest.TestCase):
    """Verify that runtime observations remain bounded and conservative."""

    def test_distinct_support_suppresses_duplicate_executions(self):
        trace = {"executions": [
            {"test_id": "test_a", "input": {"amount": 2}, "outcome": "return", "return": 4},
            {"test_id": "test_repeat", "input": {"amount": 2}, "outcome": "return", "return": 4},
            {"test_id": "test_b", "input": {"amount": 3}, "outcome": "return", "return": 6},
        ]}
        claims = evaluate_observations(card(), trace)
        detail = claims[0]["evidence"]["detail"]
        self.assertEqual(detail["support"], 2)
        self.assertEqual(detail["input_domain"]["amount"], {"kind": "numeric", "min": 2, "max": 3, "distinct": 2})
        self.assertEqual(detail["return_domain"], {"kind": "numeric", "min": 4, "max": 6, "distinct": 2})

    def test_constant_or_negative_candidates_are_not_claims(self):
        constant = {"executions": [
            {"test_id": "a", "input": {"x": 1}, "outcome": "return", "return": 1},
            {"test_id": "b", "input": {"x": 2}, "outcome": "return", "return": 1},
        ]}
        negative = {"executions": [
            {"test_id": "a", "input": {"x": 1}, "outcome": "return", "return": -1},
            {"test_id": "b", "input": {"x": 2}, "outcome": "return", "return": 2},
        ]}
        self.assertEqual(evaluate_observations(card(), constant), [])
        self.assertEqual(evaluate_observations(card(), negative), [])

    def test_sensitive_and_truncated_values_cannot_support_input_claims(self):
        serialized = serialize_value(SimpleNamespace(password="secret", nested=[1, 2, 3]))
        self.assertEqual(serialized["attributes"]["password"], {"kind": "redacted"})
        self.assertFalse(contains_unusable_value({"kind": "object", "attributes": {"ok": 1}}))
        self.assertTrue(contains_unusable_value(serialized))

    def test_terminal_observation_render_does_not_dump_serialized_inputs(self):
        observed_card = {
            "target": {"qualified_name": "target", "name": "target", "status": "supported", "path": "module.py", "signature": "target(value)", "source_span": card()["target"]["source_span"]},
            "claims": [],
            "boundaries": [],
            "diagnostics": [],
        }
        observed_card["claims"] = evaluate_observations(observed_card, {"executions": [
            {"test_id": "a", "input": {"value": {"attributes": {"secret": "redacted"}}}, "outcome": "return", "return": 1},
            {"test_id": "b", "input": {"value": {"attributes": {"secret": "redacted-2"}}}, "outcome": "return", "return": 2},
        ]})
        output = terminal(observed_card)
        self.assertIn("Input domain:", output)
        self.assertNotIn("attributes", output)

    def test_boundary_trace_records_returns_and_escaping_exceptions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "module.py"
            path.write_text("def target(value):\n    if value < 0:\n        raise ValueError('negative')\n    return value\n", encoding="utf-8")
            namespace = {}
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
            tracer = TargetTracer(str(path), "target")
            tracer.test_id = "test_boundary"
            previous = sys.gettrace()
            sys.settrace(tracer.trace)
            try:
                namespace["target"](2)
                with self.assertRaises(ValueError):
                    namespace["target"](-1)
            finally:
                sys.settrace(previous)
            self.assertEqual([item["outcome"] for item in tracer.executions], ["return", "raise"])
            self.assertEqual(tracer.executions[1]["exception"]["type"], "ValueError")


if __name__ == "__main__":
    unittest.main()
