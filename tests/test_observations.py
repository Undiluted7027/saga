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
        result = evaluate_observations(card(), trace)
        detail = result.claims[0]["evidence"]["detail"]
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
        constant_result = evaluate_observations(card(), constant)
        negative_result = evaluate_observations(card(), negative)
        self.assertFalse(constant_result.claims)
        self.assertEqual(
            constant_result.status["reason"],
            "insufficient_distinct_outputs",
        )
        self.assertFalse(negative_result.claims)
        self.assertEqual(negative_result.status["reason"], "template_contradicted")

    def test_empty_observation_reasons_are_distinct(self):
        cases = {
            "no_recorded_executions": {"executions": []},
            "unsupported_return_shape": {"executions": [
                {"test_id": "dict", "input": {"x": 1}, "outcome": "return", "return": {"kind": "mapping", "items": []}},
            ]},
            "insufficient_distinct_inputs": {"executions": [
                {"test_id": "same", "input": {"x": 1}, "outcome": "return", "return": 2},
                {"test_id": "repeat", "input": {"x": 1}, "outcome": "return", "return": 2},
            ]},
        }
        for reason, trace in cases.items():
            with self.subTest(reason=reason):
                result = evaluate_observations(card(), trace)
                self.assertFalse(result.claims)
                self.assertEqual(result.status["state"], "no_claim")
                self.assertEqual(result.status["reason"], reason)

    def test_opaque_callback_is_excluded_without_inflating_distinct_inputs(self):
        opaque = {"kind": "unsupported", "type": "builtins.function"}
        trace = {"executions": [
            {"test_id": "a", "input": {"account_id": 7, "fetch_balance": opaque}, "outcome": "return", "return": 10},
            {"test_id": "b", "input": {"account_id": 7, "fetch_balance": opaque}, "outcome": "return", "return": 20},
            {"test_id": "c", "input": {"account_id": 7, "fetch_balance": opaque}, "outcome": "return", "return": 30},
        ]}
        result = evaluate_observations(card(), trace)
        self.assertFalse(result.claims)
        self.assertEqual(result.status["returned_executions"], 3)
        self.assertEqual(result.status["distinct_inputs"], 1)
        self.assertEqual(result.status["reason"], "insufficient_distinct_inputs")
        self.assertEqual(result.status["excluded_parameters"], [{
            "name": "fetch_balance",
            "serialization_kinds": ["unsupported"],
            "types": ["builtins.function"],
        }])
        self.assertEqual(result.status["input_domain"]["fetch_balance"], {
            "kind": "excluded",
            "serialization_kinds": ["unsupported"],
            "types": ["builtins.function"],
        })
        self.assertIn("after excluding fetch_balance", result.status["message"])

    def test_distinct_usable_inputs_can_support_a_claim_with_opaque_parameters(self):
        trace = {"executions": [
            {"test_id": "a", "input": {"account_id": 1, "fetch_balance": {"kind": "unsupported", "type": "tests.FakeBalance"}}, "outcome": "return", "return": 10},
            {"test_id": "b", "input": {"account_id": 2, "fetch_balance": {"kind": "unsupported", "type": "tests.OtherBalance"}}, "outcome": "return", "return": 20},
        ]}
        result = evaluate_observations(card(), trace)
        self.assertEqual(result.status["reason"], "supported")
        self.assertEqual(result.status["distinct_inputs"], 2)
        self.assertEqual(len(result.claims), 1)
        domain = result.claims[0]["evidence"]["detail"]["input_domain"]
        self.assertEqual(domain["account_id"], {"kind": "numeric", "min": 1, "max": 2, "distinct": 2})
        self.assertEqual(domain["fetch_balance"]["kind"], "excluded")
        self.assertEqual(domain["fetch_balance"]["types"], ["tests.FakeBalance", "tests.OtherBalance"])

    def test_several_opaque_parameters_report_only_safe_metadata(self):
        trace = {"executions": [
            {"test_id": "a", "input": {"x": 1, "callback": {"kind": "unsupported", "type": "builtins.function"}, "token": {"kind": "redacted"}}, "outcome": "return", "return": 1},
            {"test_id": "b", "input": {"x": 2, "callback": {"kind": "unsupported", "type": "builtins.function"}, "token": {"kind": "redacted"}}, "outcome": "return", "return": 2},
        ]}
        result = evaluate_observations(card(), trace)
        self.assertEqual([item["name"] for item in result.status["excluded_parameters"]], ["callback", "token"])
        self.assertEqual(result.status["excluded_parameters"][1]["serialization_kinds"], ["redacted"])
        self.assertEqual(result.status["excluded_parameters"][1]["types"], [])

    def test_unusable_returns_still_prevent_evaluation(self):
        returns = [
            {"kind": "unsupported", "type": "custom.Result"},
            {"kind": "redacted"},
            {"kind": "non_finite", "type": "float"},
        ]
        for value in returns:
            with self.subTest(value=value):
                result = evaluate_observations(card(), {"executions": [
                    {"test_id": "a", "input": {"x": 1}, "outcome": "return", "return": value},
                    {"test_id": "b", "input": {"x": 2}, "outcome": "return", "return": 2},
                ]})
                self.assertFalse(result.claims)
                self.assertEqual(result.status["reason"], "unusable_serialized_values")

    def test_successful_observation_keeps_environment_and_run_counts(self):
        trace = {
            "environment": {"python_version": "3.12.7", "platform": "test-os"},
            "executions": [
                {"test_id": "a", "input": {"x": 1}, "outcome": "return", "return": 2},
                {"test_id": "b", "input": {"x": 2}, "outcome": "return", "return": 4},
                {"test_id": "c", "input": {"x": -1}, "outcome": "raise", "exception": {"type": "ValueError"}},
            ],
        }
        result = evaluate_observations(card(), trace)
        self.assertEqual(result.status["state"], "claim_produced")
        self.assertEqual(result.status["execution_count"], 3)
        self.assertEqual(result.status["raised_executions"], 1)
        detail = result.claims[0]["evidence"]["detail"]
        self.assertEqual(detail["environment"], trace["environment"])
        self.assertEqual(detail["raised_executions"], 1)

    def test_sensitive_and_truncated_values_cannot_support_input_claims(self):
        serialized = serialize_value(SimpleNamespace(password="secret", nested=[1, 2, 3]))
        self.assertEqual(serialized["attributes"]["password"], {"kind": "redacted"})
        self.assertEqual(
            serialize_value(lambda: None),
            {"kind": "unsupported", "type": "builtins.function"},
        )
        self.assertFalse(contains_unusable_value({"kind": "object", "attributes": {"ok": 1}}))
        self.assertTrue(contains_unusable_value(serialized))

    def test_terminal_observation_render_does_not_dump_serialized_inputs(self):
        observed_card = {
            "target": {"qualified_name": "target", "name": "target", "status": "supported", "path": "module.py", "signature": "target(value)", "source_span": card()["target"]["source_span"]},
            "claims": [],
            "boundaries": [],
            "diagnostics": [],
        }
        result = evaluate_observations(observed_card, {"executions": [
            {"test_id": "a", "input": {"value": {"attributes": {"secret": "redacted"}}}, "outcome": "return", "return": 1},
            {"test_id": "b", "input": {"value": {"attributes": {"secret": "redacted-2"}}}, "outcome": "return", "return": 2},
        ]})
        observed_card["claims"] = result.claims
        observed_card["observation_status"] = result.status
        output = terminal(observed_card)
        self.assertIn("Input domain:", output)
        self.assertNotIn("attributes", output)

    def test_terminal_explains_unsupported_return_without_a_claim(self):
        status_card = {
            "target": {"qualified_name": "target", "name": "target", "status": "supported", "path": "module.py", "signature": "target(value)", "source_span": card()["target"]["source_span"]},
            "claims": [],
            "boundaries": [],
            "diagnostics": [],
        }
        result = evaluate_observations(status_card, {"executions": [
            {"test_id": "dict", "input": {"value": 1}, "outcome": "return", "return": {"kind": "mapping", "items": []}},
        ]})
        status_card["observation_status"] = result.status
        output = terminal(status_card)
        self.assertIn("unsupported_return_shape", output)
        self.assertIn("no current observation template", output)

    def test_terminal_explains_excluded_parameters_and_usable_input_count(self):
        status_card = {
            "target": {"qualified_name": "target", "name": "target", "status": "supported", "path": "module.py", "signature": "target(value, callback)", "source_span": card()["target"]["source_span"]},
            "claims": [],
            "boundaries": [],
            "diagnostics": [],
        }
        result = evaluate_observations(status_card, {"executions": [
            {"test_id": "a", "input": {"value": 1, "callback": {"kind": "unsupported", "type": "builtins.function"}}, "outcome": "return", "return": 2},
            {"test_id": "b", "input": {"value": 1, "callback": {"kind": "unsupported", "type": "builtins.function"}}, "outcome": "return", "return": 3},
        ]})
        status_card["observation_status"] = result.status
        output = terminal(status_card)
        self.assertIn("Distinct usable inputs: 1", output)
        self.assertIn("Excluded parameters: callback (unsupported; types: builtins.function)", output)
        self.assertIn("callback: excluded", output)

    def test_static_card_does_not_report_a_test_run(self):
        static_card = {
            "target": {"qualified_name": "target", "name": "target", "status": "supported", "path": "module.py", "signature": "target()", "source_span": card()["target"]["source_span"]},
            "claims": [],
            "boundaries": [],
            "diagnostics": [],
        }
        self.assertNotIn("Observation status", terminal(static_card))

    def test_boundary_trace_records_returns_and_escaping_exceptions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "module.py"
            path.write_text("def target(value):\n    if value < 0:\n        raise ValueError('negative')\n    return value\n", encoding="utf-8")
            namespace = {}
            exec(  # noqa: S102 - isolated fixture code exercises frame tracing
                compile(path.read_text(encoding="utf-8"), str(path), "exec"),
                namespace,
            )
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

    def test_method_trace_matches_the_qualified_name_not_a_same_named_function(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "module.py"
            path.write_text(
                "def run(value):\n    return value + 1\n\n"
                "class Worker:\n"
                "    def run(self, value):\n"
                "        return value * 2\n",
                encoding="utf-8",
            )
            namespace = {}
            exec(  # noqa: S102 - isolated fixture code exercises frame tracing
                compile(path.read_text(encoding="utf-8"), str(path), "exec"),
                namespace,
            )
            tracer = TargetTracer(str(path), "Worker.run")
            tracer.test_id = "test_method"
            previous = sys.gettrace()
            sys.settrace(tracer.trace)
            try:
                namespace["run"](3)
                namespace["Worker"]().run(3)
            finally:
                sys.settrace(previous)

        self.assertEqual(len(tracer.executions), 1)
        self.assertEqual(tracer.executions[0]["return"], 6)


if __name__ == "__main__":
    unittest.main()
