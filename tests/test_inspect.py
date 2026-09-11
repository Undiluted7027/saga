import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from saga.inspect import inspect_function
from saga.render import terminal


class InspectFunctionTests(unittest.TestCase):
    """Exercise successful inspection and conservative target diagnostics."""

    def write(self, text: str) -> str:
        """Write a temporary Python module and clean it up after the test."""
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "module.py"
        path.write_text(text, encoding="utf-8")
        self.addCleanup(self.tempdir.cleanup)
        return str(path)

    def test_valid_module_function_has_metadata_and_return_claim(self):
        path = self.write("def greet(name: str = 'world') -> str:\n    return name\n")
        card = inspect_function(path, "greet")
        self.assertEqual(card["target"]["status"], "supported")
        self.assertEqual(card["target"]["signature"], "greet(name: str='world')")
        self.assertEqual(card["target"]["source_span"]["start_line"], 1)
        self.assertEqual([claim["kind"] for claim in card["claims"]], ["return_dependency"])
        self.assertEqual(card["diagnostics"], [])

    def test_missing_file_and_target_are_diagnostic(self):
        missing = inspect_function("no-such-file.py", "greet")
        self.assertEqual(missing["diagnostics"][0]["kind"], "missing_file")
        path = self.write("def other():\n    pass\n")
        card = inspect_function(path, "greet")
        self.assertEqual(card["diagnostics"][0]["kind"], "target_not_found")

    def test_unsupported_targets_are_distinct(self):
        path = self.write("@decorator\ndef decorated():\n    pass\n\nasync def async_fn():\n    pass\n\ndef generated():\n    yield 1\n")
        self.assertEqual(inspect_function(path, "decorated")["diagnostics"][0]["kind"], "unsupported_target")
        self.assertIn("Decorated", inspect_function(path, "decorated")["diagnostics"][0]["message"])
        self.assertIn("Async", inspect_function(path, "async_fn")["diagnostics"][0]["message"])
        self.assertIn("Generator", inspect_function(path, "generated")["diagnostics"][0]["message"])
        self.assertEqual(inspect_function(path, "outer.inner")["diagnostics"][0]["kind"], "unsupported_target")

    def test_malformed_and_ambiguous_inputs_have_spans_or_details(self):
        malformed = self.write("def broken(:\n")
        diagnostic = inspect_function(malformed, "broken")["diagnostics"][0]
        self.assertEqual(diagnostic["kind"], "parsing")
        self.assertIn("source_span", diagnostic)
        duplicate = self.write("def same():\n    pass\n\ndef same():\n    pass\n")
        diagnostic = inspect_function(duplicate, "same")["diagnostics"][0]
        self.assertEqual(diagnostic["kind"], "ambiguous_target")
        self.assertIn("2", diagnostic["message"])

    def test_cli_json_and_terminal_render_the_same_card(self):
        path = self.write("def greet(name):\n    return name\n")
        result = subprocess.run([sys.executable, "-m", "saga.cli", "inspect", f"{path}::greet"], capture_output=True, text=True, check=True)
        card = json.loads(result.stdout)
        self.assertEqual(card["target"]["status"], "supported")
        terminal_result = subprocess.run([sys.executable, "-m", "saga.cli", "inspect", f"{path}::greet", "--format", "terminal"], capture_output=True, text=True, check=True)
        self.assertIn(card["target"]["signature"], terminal_result.stdout)
        self.assertIn(card["target"]["status"], terminal_result.stdout)

    def test_guard_and_exception_claims_preserve_structure_and_spans(self):
        path = self.write("def charge(amount):\n    if amount <= 0:\n        raise ValueError('amount')\n    return amount\n")
        card = inspect_function(path, "charge")
        self.assertEqual([claim["kind"] for claim in card["claims"][:2]], ["rejected_input", "explicit_exception"])
        guard = card["claims"][0]
        self.assertEqual(guard["statement"]["type"], "entry_guard")
        self.assertEqual(guard["statement"]["condition"]["kind"], "comparison")
        self.assertEqual(guard["statement"]["condition"]["operators"], ["<="])
        self.assertEqual([span["start_line"] for span in guard["source_spans"]], [2, 3])
        self.assertTrue(guard["assumptions"])
        self.assertEqual(guard["statement"]["exit"]["exception"]["name"], "ValueError")

    def test_assertion_records_debug_assumption_and_boolean_negation(self):
        path = self.write("def active(account):\n    assert not account.active\n    return account\n")
        card = inspect_function(path, "active")
        claim = card["claims"][0]
        self.assertEqual(claim["statement"]["type"], "assertion")
        self.assertEqual(claim["statement"]["condition"]["kind"], "unary")
        self.assertEqual(claim["statement"]["condition"]["operator"], "not")
        self.assertIn("__debug__", claim["assumptions"][0]["text"])
        self.assertEqual(card["boundaries"][0]["kind"], "dynamic_dispatch")

    def test_uncertain_condition_keeps_claim_and_attaches_boundaries(self):
        path = self.write("def check(amount, account):\n    if is_valid(amount) and account.active:\n        raise ValueError()\n    return amount\n")
        card = inspect_function(path, "check")
        claim = card["claims"][0]
        self.assertEqual(claim["statement"]["condition"]["kind"], "boolean")
        self.assertEqual({boundary["kind"] for boundary in card["boundaries"]}, {"unresolved_call", "dynamic_dispatch"})
        self.assertEqual({boundary["id"] for boundary in card["boundaries"]}, set(claim["boundary_ids"]))

    def test_late_and_nested_guards_are_not_entry_requirements(self):
        path = self.write("def late(amount):\n    total = amount\n    if amount <= 0:\n        raise ValueError()\n    return total\n\ndef nested(amount):\n    if amount:\n        if amount <= 0:\n            raise ValueError()\n    return amount\n")
        self.assertFalse([claim for claim in inspect_function(path, "late")["claims"] if claim["kind"] == "rejected_input"])
        self.assertFalse([claim for claim in inspect_function(path, "nested")["claims"] if claim["kind"] == "rejected_input"])

    def test_unmodeled_condition_is_diagnostic(self):
        path = self.write("def unknown(amount):\n    if amount <= limit:\n        raise ValueError()\n    return amount\n")
        card = inspect_function(path, "unknown")
        self.assertFalse([claim for claim in card["claims"] if claim["kind"] == "rejected_input"])
        self.assertEqual(card["diagnostics"][0]["kind"], "unsupported_semantics")
        self.assertIn("source_span", card["diagnostics"][0])

    def test_writes_and_unresolved_calls_are_source_linked(self):
        path = self.write("def mutate(order, values):\n    global total\n    order.status = 'ready'\n    values[0] = 1\n    total = 2\n    unknown(values)\n    return order\n")
        card = inspect_function(path, "mutate")
        writes = [claim for claim in card["claims"] if claim["kind"] == "attempted_write"]
        self.assertEqual([claim["statement"]["target"]["kind"] for claim in writes], ["attribute", "subscript", "name"])
        self.assertEqual(len([boundary for boundary in card["boundaries"] if boundary["kind"] == "assignment_hooks"]), 2)
        self.assertEqual(len([boundary for boundary in card["boundaries"] if boundary["kind"] == "unresolved_call"]), 1)
        self.assertTrue(all(claim["source_spans"] for claim in writes))

    def test_registry_resolves_pathlib_aliases_without_unresolved_boundaries(self):
        path = self.write("import pathlib as pl\nfrom pathlib import Path as FilePath\n\ndef write_one(path):\n    pl.Path(path).write_text('one')\n    FilePath(path).write_text('two')\n    return path\n")
        card = inspect_function(path, "write_one")
        effects = [claim for claim in card["claims"] if claim["kind"] == "known_effect"]
        self.assertEqual(len(effects), 2)
        self.assertTrue(all(claim["statement"]["effect"]["callee"] == "pathlib.Path.write_text" for claim in effects))
        self.assertFalse(card["boundaries"])

    def test_repeated_effects_keep_distinct_spans(self):
        path = self.write("from pathlib import Path\n\ndef write_twice(path):\n    Path(path).write_text('one')\n    Path(path).write_text('two')\n")
        effects = [claim for claim in inspect_function(path, "write_twice")["claims"] if claim["kind"] == "known_effect"]
        self.assertEqual(len(effects), 2)
        self.assertNotEqual(effects[0]["source_spans"], effects[1]["source_spans"])

    def test_return_slice_keeps_reaching_definitions_and_excludes_unrelated_work(self):
        card = inspect_function("fixture/process_order.py", "process_order")
        return_claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        lines = {span["start_line"] for span in return_claim["source_spans"]}
        self.assertTrue({3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 18}.issubset(lines))
        self.assertNotIn(15, lines)
        self.assertIn("effect-boundary-9-10-unresolved_call", return_claim["boundary_ids"])
        self.assertIn("effect-boundary-11-19-unresolved_call", return_claim["boundary_ids"])

    def test_multiple_returns_keep_separate_slices(self):
        path = self.write("def choose(value):\n    if value:\n        positive = value + 1\n        return positive\n    negative = value - 1\n    return negative\n")
        claims = [claim for claim in inspect_function(path, "choose")["claims"] if claim["kind"] == "return_dependency"]
        self.assertEqual(len(claims), 2)
        self.assertNotEqual(claims[0]["id"], claims[1]["id"])
        self.assertTrue(any(dependency["names"] == ["positive"] for dependency in claims[0]["statement"]["dependencies"]))
        self.assertTrue(any(dependency["names"] == ["negative"] for dependency in claims[1]["statement"]["dependencies"]))

    def test_loop_slice_is_conservative_and_unknown_call_is_attached(self):
        path = self.write("def total(values):\n    result = 0\n    for value in values:\n        result = add(result, value)\n    return result\n")
        card = inspect_function(path, "total")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        lines = {span["start_line"] for span in claim["source_spans"]}
        self.assertTrue({2, 3, 4, 5}.issubset(lines))
        self.assertTrue(any(boundary["kind"] == "unresolved_call" and boundary["source_span"]["start_line"] == 4 for boundary in card["boundaries"]))
        self.assertTrue(any(boundary["source_span"]["start_line"] == 4 and boundary["id"] in claim["boundary_ids"] for boundary in card["boundaries"]))

    def test_raising_a_modeled_builtin_exception_is_not_an_unresolved_call(self):
        path = self.write("def fail(order):\n    order.status = 'failed'\n    raise RuntimeError('stop')\n")
        card = inspect_function(path, "fail")
        self.assertFalse([boundary for boundary in card["boundaries"] if boundary["kind"] == "unresolved_call"])

    def test_late_guard_is_a_control_dependency_of_the_return(self):
        path = self.write("def f(amount):\n    total = amount\n    if amount <= 0:\n        raise ValueError()\n    result = total * 2\n    return result\n")
        card = inspect_function(path, "f")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        lines = {span["start_line"] for span in claim["source_spans"]}
        self.assertIn(3, lines)

    def test_partial_analysis_keeps_return_claim_beside_unsupported_behavior(self):
        card = inspect_function("fixture/partial_analysis.py", "partial_result")
        self.assertEqual(card["target"]["status"], "supported")
        self.assertTrue(any(claim["kind"] == "return_dependency" for claim in card["claims"]))
        self.assertTrue(any(diagnostic["kind"] == "unsupported_semantics" for diagnostic in card["diagnostics"]))

    def test_final_fixture_marks_async_target_unsupported(self):
        card = inspect_function("fixture/partial_analysis.py", "unsupported_async")
        self.assertEqual(card["target"]["status"], "unsupported")
        self.assertEqual(card["diagnostics"][0]["kind"], "unsupported_target")


if __name__ == "__main__":
    unittest.main()
