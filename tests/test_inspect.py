import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from saga.inspect import inspect_function


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
        claim = card["claims"][0]
        self.assertEqual(claim["statement"]["text"], "Returns name.")
        self.assertEqual(claim["statement"]["source_text"], "name")

    def test_missing_file_and_target_are_diagnostic(self):
        missing = inspect_function("no-such-file.py", "greet")
        self.assertEqual(missing["diagnostics"][0]["kind"], "missing_file")
        path = self.write("def other():\n    pass\n")
        card = inspect_function(path, "greet")
        self.assertEqual(card["diagnostics"][0]["kind"], "target_not_found")

    def test_unsupported_targets_are_distinct(self):
        path = self.write("async def async_fn():\n    pass\n\ndef generated():\n    yield 1\n")
        async_card = inspect_function(path, "async_fn")
        self.assertEqual(async_card["target"]["status"], "supported")
        self.assertEqual(
            next(
                claim for claim in async_card["claims"]
                if claim["kind"] == "return_dependency"
            )["statement"]["text"],
            "Falls through and returns None.",
        )
        self.assertIn("Generator", inspect_function(path, "generated")["diagnostics"][0]["message"])
        self.assertEqual(inspect_function(path, "outer.inner")["diagnostics"][0]["kind"], "target_not_found")

    def test_class_method_and_async_method_are_supported_targets(self):
        path = self.write(
            "class Service:\n"
            "    def choose(self, enabled):\n"
            "        if enabled:\n"
            "            return self\n"
            "        return None\n\n"
            "    async def send(self, payload):\n"
            "        await deliver(payload)\n"
        )
        method = inspect_function(path, "Service.choose")
        async_method = inspect_function(path, "Service.send")
        self.assertEqual(method["target"]["status"], "supported")
        self.assertEqual(async_method["target"]["status"], "supported")
        self.assertEqual(method["target"]["qualified_name"], "Service.choose")
        async_boundary = next(
            boundary for boundary in async_method["boundaries"]
            if boundary["target"]["text"] == "async execution"
        )
        self.assertIn("cancellation", async_boundary["reason"])
        self.assertTrue(
            any(
                claim["statement"].get("implicit")
                for claim in async_method["claims"]
                if claim["kind"] == "return_dependency"
            )
        )

    def test_overload_declarations_yield_to_the_decorated_implementation(self):
        path = self.write(
            "from typing import overload\n\n"
            "@overload\n"
            "def parse(value: str) -> str: ...\n\n"
            "@overload\n"
            "def parse(value: int) -> int: ...\n\n"
            "@contract\n"
            "def parse(value):\n"
            "    return value\n"
        )
        card = inspect_function(path, "parse")
        self.assertEqual(card["target"]["status"], "supported")
        self.assertEqual(card["target"]["source_span"]["start_line"], 10)
        claim = next(item for item in card["claims"] if item["kind"] == "return_dependency")
        boundary = next(
            item
            for item in card["boundaries"]
            if item["target"]["text"] == "@contract"
        )
        self.assertIn(boundary["id"], claim["boundary_ids"])
        self.assertIn("may replace or wrap", boundary["reason"])

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
        self.assertIn(card["claims"][0]["statement"]["text"], terminal_result.stdout)
        self.assertIn("Source syntax: name", terminal_result.stdout)
        self.assertIn("Method: intraprocedural_may_affect", terminal_result.stdout)

    def test_cli_succeeds_with_useful_claims_and_partial_analysis_diagnostics(self):
        path = self.write(
            "def billing(records):\n"
            "    while records:\n"
            "        return records[0]\n"
            "    return None\n"
        )
        result = subprocess.run(
            [sys.executable, "-m", "saga.cli", "inspect", f"{path}::billing"],
            capture_output=True,
            text=True,
            check=False,
        )
        card = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(card["claims"])
        self.assertTrue(any(
            item["kind"] == "unsupported_semantics"
            for item in card["diagnostics"]
        ))

    def test_guard_and_exception_claims_preserve_structure_and_spans(self):
        path = self.write("def charge(amount):\n    if amount <= 0:\n        raise ValueError('amount')\n    return amount\n")
        card = inspect_function(path, "charge")
        self.assertEqual([claim["kind"] for claim in card["claims"][:2]], ["rejected_input", "explicit_exception"])
        guard = card["claims"][0]
        self.assertEqual(guard["statement"]["type"], "entry_guard")
        self.assertEqual(guard["statement"]["condition"]["kind"], "comparison")
        self.assertEqual(guard["statement"]["condition"]["operators"], ["<="])
        self.assertEqual(guard["statement"]["text"], "Rejects input when amount is less than or equal to 0.")
        self.assertEqual(guard["statement"]["source_text"], "amount <= 0")
        self.assertEqual(card["claims"][1]["statement"]["text"], "Raises ValueError when amount is less than or equal to 0.")
        self.assertEqual(card["claims"][1]["statement"]["source_text"], "raise ValueError('amount')")
        self.assertEqual(card["claims"][1]["statement"]["condition_source_text"], "amount <= 0")
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
        self.assertEqual(claim["statement"]["text"], "Requires account.active is falsy.")
        self.assertIn("__debug__", claim["assumptions"][0]["text"])
        self.assertEqual(card["boundaries"][0]["kind"], "dynamic_dispatch")

    def test_uncertain_condition_keeps_claim_and_attaches_boundaries(self):
        path = self.write("def check(amount, account):\n    if is_valid(amount) and account.active:\n        raise ValueError()\n    return amount\n")
        card = inspect_function(path, "check")
        claim = card["claims"][0]
        self.assertEqual(claim["statement"]["condition"]["kind"], "boolean")
        self.assertEqual({boundary["kind"] for boundary in card["boundaries"]}, {"unresolved_call", "dynamic_dispatch"})
        self.assertEqual({boundary["id"] for boundary in card["boundaries"]}, set(claim["boundary_ids"]))

    def test_compound_guard_wording_preserves_boolean_meaning(self):
        path = self.write("def check(items, amount):\n    if not items or amount <= 0:\n        raise ValueError()\n    return amount\n")
        card = inspect_function(path, "check")
        guard = next(claim for claim in card["claims"] if claim["kind"] == "rejected_input")
        returned = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        self.assertEqual(guard["statement"]["text"], "Rejects input when items is falsy or amount is less than or equal to 0.")
        self.assertEqual(returned["statement"]["text"], "Returns amount when items is truthy and amount is greater than 0.")

    def test_nested_boolean_wording_keeps_required_grouping(self):
        path = self.write("def check(a, b, c):\n    if not (a and b) and c:\n        raise ValueError()\n    return a\n")
        card = inspect_function(path, "check")
        guard = next(claim for claim in card["claims"] if claim["kind"] == "rejected_input")
        self.assertEqual(guard["statement"]["text"], "Rejects input when (a is falsy or b is falsy) and c is truthy.")

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
        self.assertEqual(writes[0]["statement"]["text"], "Attempts to write to order.status.")
        self.assertEqual(writes[0]["statement"]["source_text"], "order.status")

    def test_common_unresolved_routines_are_classified_without_being_hidden(self):
        path = self.write("def count(values):\n    return len(values)\n")
        boundaries = inspect_function(path, "count")["boundaries"]
        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0]["kind"], "unresolved_call")
        self.assertEqual(boundaries[0]["category"], "routine")

    def test_routine_builtins_stay_boundaries_in_guards_and_returns(self):
        path = self.write(
            "def price(items, amount, gateway):\n"
            "    if len(items) == 0:\n"
            "        raise ValueError('items')\n"
            "    rounded = round(abs(amount), 2)\n"
            "    gateway.authorize(rounded)\n"
            "    return rounded\n"
        )
        card = inspect_function(path, "price")
        calls = [
            boundary for boundary in card["boundaries"]
            if boundary["kind"] == "unresolved_call"
        ]
        by_target = {}
        for boundary in calls:
            by_target.setdefault(boundary["target"]["text"], set()).add(
                boundary.get("category", "important")
            )
        self.assertEqual(by_target["len(items)"], {"routine"})
        self.assertEqual(by_target["round(...)"], {"routine"})
        self.assertEqual(by_target["abs(...)"], {"routine"})
        self.assertEqual(by_target["gateway.authorize(...)"], {"important"})

    def test_registry_resolves_pathlib_aliases_without_unresolved_boundaries(self):
        path = self.write("import pathlib as pl\nfrom pathlib import Path as FilePath\n\ndef write_one(path):\n    pl.Path(path).write_text('one')\n    FilePath(path).write_text('two')\n    return path\n")
        card = inspect_function(path, "write_one")
        effects = [claim for claim in card["claims"] if claim["kind"] == "known_effect"]
        self.assertEqual(len(effects), 2)
        self.assertTrue(all(claim["statement"]["effect"]["callee"] == "pathlib.Path.write_text" for claim in effects))
        self.assertTrue(all(claim["statement"]["text"] == "May write text to the filesystem through pathlib.Path.write_text(...)." for claim in effects))
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
        self.assertNotIn("effect-boundary-9-10-unresolved_call", return_claim["boundary_ids"])
        self.assertNotIn("effect-boundary-11-19-unresolved_call", return_claim["boundary_ids"])
        local_returns = [claim for claim in card["claims"] if claim["kind"] == "return_dependency" and claim.get("call_chain")]
        self.assertEqual({claim["call_chain"][0]["callee"] for claim in local_returns}, {"lookup_tax", "select_discount"})

    def test_multiple_returns_keep_separate_slices(self):
        path = self.write("def choose(value):\n    if value:\n        positive = value + 1\n        return positive\n    negative = value - 1\n    return negative\n")
        claims = [claim for claim in inspect_function(path, "choose")["claims"] if claim["kind"] == "return_dependency"]
        self.assertEqual(len(claims), 2)
        self.assertNotEqual(claims[0]["id"], claims[1]["id"])
        self.assertEqual(claims[0]["statement"]["text"], "Returns positive when value is truthy.")
        self.assertEqual(claims[1]["statement"]["text"], "Returns negative when value is falsy.")
        self.assertEqual(claims[0]["statement"]["source_text"], "positive")
        self.assertTrue(any(dependency["names"] == ["positive"] for dependency in claims[0]["statement"]["dependencies"]))
        self.assertTrue(any(dependency["names"] == ["negative"] for dependency in claims[1]["statement"]["dependencies"]))

    def test_returns_inside_try_regions_are_kept_with_a_limit(self):
        path = self.write(
            "def choose(value, fallback):\n"
            "    try:\n"
            "        if value:\n"
            "            return value\n"
            "    except ValueError:\n"
            "        return fallback\n"
            "    else:\n"
            "        return None\n"
        )
        card = inspect_function(path, "choose")
        claims = [
            claim
            for claim in card["claims"]
            if claim["kind"] == "return_dependency" and not claim.get("call_chain")
        ]
        self.assertEqual(
            {claim["statement"]["return_expression"] for claim in claims},
            {"value", "fallback", "None"},
        )
        boundary = next(
            item
            for item in card["boundaries"]
            if item["kind"] == "unsupported_semantics"
            and item["target"]["text"] == "try statement"
        )
        self.assertTrue(
            all(boundary["id"] in claim["boundary_ids"] for claim in claims)
        )

    def test_return_inside_while_is_not_silently_omitted(self):
        path = self.write(
            "def first(items):\n"
            "    while items:\n"
            "        if items[0]:\n"
            "            return items[0]\n"
            "        items = items[1:]\n"
            "    return None\n"
        )
        card = inspect_function(path, "first")
        claims = [
            claim
            for claim in card["claims"]
            if claim["kind"] == "return_dependency" and not claim.get("call_chain")
        ]
        self.assertEqual(
            {claim["statement"]["return_expression"] for claim in claims},
            {"items[0]", "None"},
        )
        self.assertTrue(
            any(
                "traverses this while statement" in item["message"]
                for item in card["diagnostics"]
            )
        )

    def test_continue_guards_limit_a_later_return(self):
        path = self.write(
            "def choose(items, title):\n"
            "    for item in items:\n"
            "        if not item.enabled:\n"
            "            continue\n"
            "        if title:\n"
            "            if item.title != title:\n"
            "                continue\n"
            "        return item\n"
            "    return None\n"
        )
        card = inspect_function(path, "choose")
        claim = next(
            claim
            for claim in card["claims"]
            if claim["kind"] == "return_dependency"
            and claim["statement"]["return_expression"] == "item"
        )
        conditions = {
            item["source_text"] for item in claim["statement"]["path_conditions"]
        }
        self.assertIn("not item.enabled", conditions)
        self.assertTrue(
            any(
                "item.title" in condition
                and "title" in condition
                for condition in conditions
            )
        )
        self.assertIn("item.enabled is truthy", claim["statement"]["text"])
        self.assertIn("item.title equals title", claim["statement"]["text"])

    def test_fallthrough_branches_do_not_add_tautological_conditions(self):
        path = self.write(
            "def choose(flag, nested):\n"
            "    if flag:\n"
            "        value = 1\n"
            "    if nested:\n"
            "        if nested is True:\n"
            "            value = 2\n"
            "    return value\n"
        )
        claim = next(
            claim for claim in inspect_function(path, "choose")["claims"]
            if claim["kind"] == "return_dependency"
        )
        self.assertEqual(claim["statement"]["path_conditions"], [])
        self.assertNotIn(" or ", claim["statement"]["text"])

    def test_augmented_assignment_reads_the_previous_definition(self):
        path = self.write("def add_to_seed(y):\n    x = 1\n    x += y\n    return x\n")
        claim = next(claim for claim in inspect_function(path, "add_to_seed")["claims"] if claim["kind"] == "return_dependency")
        lines = {span["start_line"] for span in claim["source_spans"]}
        self.assertEqual(lines, {2, 3, 4})
        self.assertEqual(claim["statement"]["inputs"], ["y"])
        self.assertEqual(claim["statement"]["text"], "Returns x.")

    def test_return_claim_names_inputs_definitions_and_calls(self):
        path = self.write("def total(amount, rate):\n    subtotal = amount\n    total = add_tax(subtotal, rate)\n    return total\n")
        claim = next(claim for claim in inspect_function(path, "total")["claims"] if claim["kind"] == "return_dependency")
        statement = claim["statement"]
        self.assertEqual(statement["inputs"], ["amount", "rate"])
        self.assertEqual(statement["definitions"], ["subtotal", "total"])
        self.assertEqual([call["text"] for call in statement["calls"]], ["add_tax(...)"])

    def test_loop_slice_is_conservative_and_unknown_call_is_attached(self):
        path = self.write("def total(values):\n    result = 0\n    for value in values:\n        result = add(result, value)\n    return result\n")
        card = inspect_function(path, "total")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        lines = {span["start_line"] for span in claim["source_spans"]}
        self.assertTrue({2, 3, 4, 5}.issubset(lines))
        self.assertTrue(any(boundary["kind"] == "unresolved_call" and boundary["source_span"]["start_line"] == 4 for boundary in card["boundaries"]))
        self.assertTrue(any(boundary["source_span"]["start_line"] == 4 and boundary["id"] in claim["boundary_ids"] for boundary in card["boundaries"]))

    def test_callable_parameter_boundary_attaches_through_returned_argument(self):
        path = self.write(
            "def build(out, callback):\n"
            "    callback(out)\n"
            "    return out\n"
        )
        card = inspect_function(path, "build")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        boundary = next(
            boundary for boundary in card["boundaries"]
            if boundary["kind"] == "unresolved_call" and boundary["source_span"]["start_line"] == 2
        )
        self.assertIn(boundary["id"], claim["boundary_ids"])
        self.assertEqual(boundary["target"]["text"], "callback(...)")
        self.assertIn("cannot determine", boundary["reason"])

    def test_unknown_method_boundary_attaches_through_returned_receiver(self):
        path = self.write(
            "def refresh(out):\n"
            "    out.refresh()\n"
            "    return out\n"
        )
        card = inspect_function(path, "refresh")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        boundary = next(boundary for boundary in card["boundaries"] if boundary["kind"] == "unresolved_call")
        self.assertIn(boundary["id"], claim["boundary_ids"])

    def test_unrelated_opaque_call_does_not_limit_return(self):
        path = self.write(
            "def build(out, values):\n"
            "    inspect_unknown(values)\n"
            "    return out\n"
        )
        card = inspect_function(path, "build")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        boundary = next(boundary for boundary in card["boundaries"] if boundary["kind"] == "unresolved_call")
        self.assertNotIn(boundary["id"], claim["boundary_ids"])

    def test_opaque_calls_attach_by_slice_names_inside_and_outside_loops(self):
        path = self.write(
            "def build(out, values, other):\n"
            "    before(out)\n"
            "    for value in values:\n"
            "        unrelated(other)\n"
            "        inside(out, value)\n"
            "        out.append(value)\n"
            "    return out\n"
        )
        card = inspect_function(path, "build")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        boundaries = {
            boundary["source_span"]["start_line"]: boundary
            for boundary in card["boundaries"]
            if boundary["kind"] == "unresolved_call"
        }
        self.assertIn(boundaries[2]["id"], claim["boundary_ids"])
        self.assertNotIn(boundaries[4]["id"], claim["boundary_ids"])
        self.assertIn(boundaries[5]["id"], claim["boundary_ids"])
        self.assertIn(boundaries[6]["id"], claim["boundary_ids"])

    def test_zero_argument_call_used_as_return_value_keeps_its_boundary(self):
        path = self.write(
            "def build():\n"
            "    result = opaque()\n"
            "    return result\n"
        )
        card = inspect_function(path, "build")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        boundary = next(boundary for boundary in card["boundaries"] if boundary["kind"] == "unresolved_call")
        self.assertIn(boundary["id"], claim["boundary_ids"])

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

    def test_final_fixture_analyzes_async_target_conservatively(self):
        card = inspect_function("fixture/partial_analysis.py", "unsupported_async")
        self.assertEqual(card["target"]["status"], "supported")
        self.assertTrue(
            any(claim["kind"] == "return_dependency" for claim in card["claims"])
        )

    def test_except_return_names_the_handler_that_makes_it_reachable(self):
        path = self.write(
            "def parse(value):\n"
            "    try:\n"
            "        convert(value)\n"
            "    except ValueError:\n"
            "        return None\n"
            "    return value\n"
        )
        card = inspect_function(path, "parse")
        handled = next(
            claim for claim in card["claims"]
            if claim["kind"] == "return_dependency"
            and claim["statement"]["return_expression"] == "None"
            and not claim["statement"].get("implicit")
        )
        self.assertIn(
            "the ValueError handler runs",
            handled["statement"]["text"],
        )

    def test_loop_fallthrough_explains_a_return_after_early_loop_return(self):
        path = self.write(
            "def allowed(conn, scopes):\n"
            "    for scope in scopes:\n"
            "        if scope not in conn.scopes:\n"
            "            return False\n"
            "    return True\n"
        )
        card = inspect_function(path, "allowed")
        accepted = next(
            claim for claim in card["claims"]
            if claim["kind"] == "return_dependency"
            and claim["statement"]["return_expression"] == "True"
        )
        self.assertIn(
            "for every scope in scopes, scope is in conn.scopes",
            accepted["statement"]["text"],
        )

    def test_returned_nested_callable_has_an_explicit_behavior_boundary(self):
        path = self.write(
            "def factory(client):\n"
            "    def wrapped(value):\n"
            "        return client.send(value)\n"
            "    return wrapped\n"
        )
        card = inspect_function(path, "factory")
        claim = next(
            item for item in card["claims"] if item["kind"] == "return_dependency"
        )
        boundary = next(
            item for item in card["boundaries"]
            if item["target"]["text"] == "wrapped"
        )
        self.assertIn(boundary["id"], claim["boundary_ids"])
        self.assertIn("another caller invokes it", boundary["reason"])

    def test_nested_generator_does_not_make_its_factory_a_generator(self):
        path = self.write(
            "def factory():\n"
            "    def generated():\n"
            "        yield 1\n"
            "    return generated\n"
        )
        card = inspect_function(path, "factory")
        self.assertEqual(card["target"]["status"], "supported")
        self.assertTrue(
            any(
                boundary["target"]["text"] == "generated"
                for boundary in card["boundaries"]
            )
        )

    def test_subscript_and_attribute_writes_reach_the_return(self):
        path = self.write(
            "def build(items, factor, sink):\n"
            "    out = []\n"
            "    for i in items:\n"
            "        out.append(i * factor)\n"
            "    sink.total = factor\n"
            "    return out, sink\n"
        )
        card = inspect_function(path, "build")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        self.assertIn("factor", claim["statement"]["inputs"])
        lines = {span["start_line"] for span in claim["source_spans"]}
        self.assertIn(4, lines)
        self.assertIn(5, lines)
        self.assertEqual(sorted(claim["statement"]["weak_definitions"]), ["out", "sink"])
        boundary_kinds = {
            boundary["kind"]
            for boundary in card["boundaries"]
            if boundary["id"] in claim["boundary_ids"]
        }
        self.assertIn("assignment_hooks", boundary_kinds)

    def test_conditional_subscript_write_is_a_weak_definition(self):
        path = self.write(
            "def maybe_set(values, index, value, keep):\n"
            "    if keep:\n"
            "        values[index] = value\n"
            "    return values\n"
        )
        card = inspect_function(path, "maybe_set")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        self.assertIn("values", claim["statement"]["weak_definitions"])
        self.assertIn("value", claim["statement"]["inputs"])
        self.assertIn("index", claim["statement"]["inputs"])

    def test_weak_write_does_not_discard_the_prior_strong_definition(self):
        path = self.write(
            "def build(seed):\n"
            "    out = compute_default(seed)\n"
            "    out.append(1)\n"
            "    return out\n"
        )
        card = inspect_function(path, "build")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        lines = {span["start_line"] for span in claim["source_spans"]}
        self.assertIn(2, lines)
        self.assertIn(3, lines)
        self.assertIn("seed", claim["statement"]["inputs"])

    def test_strong_reassignment_after_a_weak_write_replaces_it(self):
        path = self.write(
            "def build(seed, values):\n"
            "    out = []\n"
            "    out.append(seed)\n"
            "    out = values\n"
            "    return out\n"
        )
        card = inspect_function(path, "build")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        lines = {span["start_line"] for span in claim["source_spans"]}
        self.assertIn(4, lines)
        self.assertNotIn(2, lines)
        self.assertNotIn(3, lines)
        self.assertNotIn("seed", claim["statement"]["inputs"])

    def test_unresolvable_write_target_is_a_boundary_on_the_return_claim(self):
        path = self.write(
            "def build(seed):\n"
            "    get_container()[0] = seed\n"
            "    return seed\n"
        )
        card = inspect_function(path, "build")
        boundary = next(
            boundary for boundary in card["boundaries"]
            if boundary["kind"] == "unsupported_semantics" and "cannot determine which name" in boundary["reason"]
        )
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        self.assertIn(boundary["id"], claim["boundary_ids"])

    def test_match_case_mutation_reaches_the_return(self):
        path = self.write(
            "def build(seed, mode):\n"
            "    out = []\n"
            "    match mode:\n"
            "        case 'add':\n"
            "            out.append(seed)\n"
            "        case _:\n"
            "            pass\n"
            "    return out\n"
        )
        card = inspect_function(path, "build")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        self.assertIn("seed", claim["statement"]["inputs"])
        self.assertIn("out", claim["statement"]["weak_definitions"])

    def test_call_inside_nested_uncalled_function_is_not_a_weak_write(self):
        path = self.write(
            "def build(seed):\n"
            "    out = []\n"
            "    def later():\n"
            "        out.append(seed)\n"
            "    return out\n"
        )
        card = inspect_function(path, "build")
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        self.assertNotIn("seed", claim["statement"]["inputs"])
        self.assertNotIn("out", claim["statement"].get("weak_definitions", []))

    def test_unresolved_write_nested_in_a_coarse_node_is_still_a_boundary(self):
        path = self.write(
            "def get_container():\n"
            "    return []\n\n"
            "def build(seed):\n"
            "    try:\n"
            "        get_container()[0] = seed\n"
            "    finally:\n"
            "        pass\n"
            "    return seed\n"
        )
        card = inspect_function(path, "build")
        boundary = next(
            boundary for boundary in card["boundaries"]
            if boundary["kind"] == "unsupported_semantics" and "cannot determine which name" in boundary["reason"]
        )
        claim = next(claim for claim in card["claims"] if claim["kind"] == "return_dependency")
        self.assertIn(boundary["id"], claim["boundary_ids"])

    def test_trystar_gets_its_own_unsupported_semantics_diagnostic(self):
        path = self.write(
            "def build(seed):\n"
            "    try:\n"
            "        out = seed\n"
            "    except* ValueError:\n"
            "        out = 0\n"
            "    return out\n"
        )
        card = inspect_function(path, "build")
        self.assertTrue(any(
            diagnostic["kind"] == "unsupported_semantics" and "TryStar" in diagnostic["message"]
            for diagnostic in card["diagnostics"]
        ))

    def test_unresolved_write_boundary_only_limits_reachable_returns(self):
        path = self.write(
            "def get_container():\n"
            "    return []\n\n"
            "def build(seed, early):\n"
            "    if early:\n"
            "        return 'x'\n"
            "    get_container()[0] = seed\n"
            "    return 'y'\n"
        )
        card = inspect_function(path, "build")
        boundary = next(
            boundary for boundary in card["boundaries"]
            if boundary["kind"] == "unsupported_semantics" and "cannot determine which name" in boundary["reason"]
        )
        early_return = next(
            claim for claim in card["claims"]
            if claim["kind"] == "return_dependency" and claim["statement"]["source_text"] == "'x'"
        )
        late_return = next(
            claim for claim in card["claims"]
            if claim["kind"] == "return_dependency" and claim["statement"]["source_text"] == "'y'"
        )
        self.assertNotIn(boundary["id"], early_return["boundary_ids"])
        self.assertIn(boundary["id"], late_return["boundary_ids"])


if __name__ == "__main__":
    unittest.main()
