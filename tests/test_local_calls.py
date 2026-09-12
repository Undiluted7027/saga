import tempfile
import unittest
from pathlib import Path

from saga.inspect import inspect_function
from saga.render import terminal


class LocalCallTests(unittest.TestCase):
    """Exercise bounded module-local summaries and their stop conditions."""

    def inspect(self, source: str, target: str = "caller") -> dict:
        """Inspect one function in a temporary module."""
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        path = Path(tempdir.name) / "module.py"
        path.write_text(source, encoding="utf-8")
        return inspect_function(str(path), target)

    def propagated(self, card: dict, kind: str) -> list[dict]:
        """Return claims of one kind that came through a local call."""
        return [claim for claim in card["claims"] if claim["kind"] == kind and claim.get("call_chain")]

    def test_direct_helper_explains_its_return_and_argument_binding(self):
        card = self.inspect(
            "def helper(value):\n"
            "    return value\n\n"
            "def caller(raw):\n"
            "    return helper(raw)\n"
        )
        claims = self.propagated(card, "return_dependency")
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["statement"]["text"], "helper(...) returns value.")
        self.assertEqual(claims[0]["evidence"]["method"], "one_hop_local_call")
        link = claims[0]["call_chain"][0]
        self.assertEqual((link["caller"], link["callee"]), ("caller", "helper"))
        self.assertEqual(link["argument_bindings"][0]["parameter"], "value")
        self.assertEqual(link["argument_bindings"][0]["argument"], "raw")
        self.assertEqual(claims[0]["source_spans"][0], link["call_site"])
        self.assertEqual(claims[0]["source_spans"][1], link["callee_span"])
        self.assertFalse([item for item in card["boundaries"] if item["source_span"] == link["call_site"]])
        caller_claim = next(
            claim
            for claim in card["claims"]
            if claim["kind"] == "return_dependency" and not claim.get("call_chain")
        )
        self.assertEqual(caller_claim["statement"]["inputs"], ["raw"])
        dependency = caller_claim["statement"]["local_call_dependencies"][0]
        self.assertEqual(dependency["callee_parameter"], "value")
        self.assertEqual(dependency["callee_scope"], "helper")
        self.assertEqual(dependency["caller_argument"], "raw")
        self.assertEqual(dependency["caller_inputs"], ["raw"])
        self.assertEqual(dependency["call_chain"][0], link)
        rendered = terminal(card)
        self.assertIn("Local call: caller -> helper", rendered)
        self.assertIn("Arguments: value = raw", rendered)
        self.assertIn("Callee binding: helper.value = raw", rendered)
        self.assertIn("Callee scope: helper (value)", rendered)

    def test_helper_propagates_write_effect_exception_and_boundary(self):
        card = self.inspect(
            "from pathlib import Path\n\n"
            "def helper(order, path):\n"
            "    if not order:\n"
            "        raise ValueError('order')\n"
            "    order.status = 'saved'\n"
            "    Path(path).write_text('saved')\n"
            "    unknown(order)\n"
            "    return path\n\n"
            "def caller(order, path):\n"
            "    return helper(order, path)\n"
        )
        self.assertEqual(len(self.propagated(card, "attempted_write")), 1)
        self.assertEqual(len(self.propagated(card, "known_effect")), 1)
        self.assertEqual(len(self.propagated(card, "explicit_exception")), 1)
        self.assertEqual(self.propagated(card, "explicit_exception")[0]["statement"]["text"], "helper(...) may raise ValueError when order is falsy.")
        propagated_boundaries = [item for item in card["boundaries"] if item.get("call_chain")]
        self.assertTrue(any(item["target"]["text"] == "unknown(...)" for item in propagated_boundaries))
        write = self.propagated(card, "attempted_write")[0]
        self.assertTrue(write["statement"]["text"].startswith("helper(...) may attempt to write"))
        self.assertTrue(all(item.startswith("local-") for item in write["boundary_ids"]))

    def test_second_hop_stops_and_records_both_links(self):
        card = self.inspect(
            "def leaf(value):\n"
            "    return value\n\n"
            "def middle(value):\n"
            "    return leaf(value)\n\n"
            "def caller(value):\n"
            "    return middle(value)\n"
        )
        boundaries = [item for item in card["boundaries"] if item["kind"] == "local_call_limit"]
        self.assertEqual(len(boundaries), 1)
        self.assertIn("one-hop", boundaries[0]["reason"])
        self.assertEqual([(item["caller"], item["callee"]) for item in boundaries[0]["call_chain"]], [("caller", "middle"), ("middle", "leaf")])
        self.assertFalse([claim for claim in card["claims"] if claim.get("call_chain") and claim["call_chain"][-1]["callee"] == "leaf"])

    def test_direct_and_mutual_recursion_stop(self):
        direct = self.inspect("def caller(value):\n    return caller(value)\n")
        self.assertEqual([item["kind"] for item in direct["boundaries"] if item["kind"] == "local_call_limit"], ["local_call_limit"])

        mutual = self.inspect(
            "def other(value):\n"
            "    return caller(value)\n\n"
            "def caller(value):\n"
            "    return other(value)\n"
        )
        boundary = next(item for item in mutual["boundaries"] if item["kind"] == "local_call_limit")
        self.assertIn("recurse", boundary["reason"])
        self.assertEqual(len(boundary["call_chain"]), 2)

    def test_unsupported_and_ambiguous_local_callees_are_boundaries(self):
        unsupported = self.inspect(
            "async def helper(value):\n"
            "    return value\n\n"
            "def caller(value):\n"
            "    return helper(value)\n"
        )
        self.assertTrue(any(item["kind"] == "unsupported_local_callee" for item in unsupported["boundaries"]))

        builtin_named = self.inspect(
            "async def ValueError(value):\n"
            "    return value\n\n"
            "def caller(value):\n"
            "    return ValueError(value)\n"
        )
        self.assertTrue(any(item["kind"] == "unsupported_local_callee" for item in builtin_named["boundaries"]))

        ambiguous = self.inspect(
            "def helper(value):\n"
            "    return value\n\n"
            "def helper(value):\n"
            "    return value + 1\n\n"
            "def caller(value):\n"
            "    return helper(value)\n"
        )
        self.assertTrue(any(item["kind"] == "ambiguous_local_callee" for item in ambiguous["boundaries"]))
        self.assertFalse(self.propagated(ambiguous, "return_dependency"))

        conditional_rebind = self.inspect(
            "flag = False\n\n"
            "def helper(value):\n"
            "    return value\n\n"
            "if flag:\n"
            "    helper = lambda value: value + 1\n\n"
            "def caller(value):\n"
            "    return helper(value)\n"
        )
        self.assertTrue(any(item["kind"] == "ambiguous_local_callee" for item in conditional_rebind["boundaries"]))

    def test_module_and_straight_line_local_aliases_resolve(self):
        module_alias = self.inspect(
            "def helper(value):\n"
            "    return value\n\n"
            "renamed = helper\n\n"
            "def caller(value):\n"
            "    return renamed(value)\n"
        )
        self.assertTrue(self.propagated(module_alias, "return_dependency")[0]["call_chain"][0]["via_alias"])

        local_alias = self.inspect(
            "def helper(value):\n"
            "    return value\n\n"
            "def caller(value):\n"
            "    renamed = helper\n"
            "    return renamed(value)\n"
        )
        claim = self.propagated(local_alias, "return_dependency")[0]
        self.assertEqual(claim["call_chain"][0]["invoked_as"], "renamed")
        self.assertTrue(claim["call_chain"][0]["via_alias"])

        early_alias = self.inspect(
            "renamed = helper\n\n"
            "def helper(value):\n"
            "    return value\n\n"
            "def caller(value):\n"
            "    return renamed(value)\n"
        )
        self.assertFalse(self.propagated(early_alias, "return_dependency"))

    def test_parameter_callables_and_shadowed_names_are_distinct(self):
        parameter = self.inspect(
            "def notify(value):\n"
            "    return value\n\n"
            "def caller(notify, value):\n"
            "    return notify(value)\n"
        )
        self.assertFalse(self.propagated(parameter, "return_dependency"))
        boundary = next(item for item in parameter["boundaries"] if item["kind"] == "unresolved_call")
        self.assertIn("supplied through parameter 'notify'", boundary["reason"])
        self.assertNotIn("module-level binding", boundary["reason"])
        self.assertEqual(
            boundary["callee_origin"],
            {"kind": "parameter", "name": "notify", "function": "caller"},
        )

        shadowed = self.inspect(
            "def helper(value):\n"
            "    return value\n\n"
            "def replacement():\n"
            "    return helper\n\n"
            "def caller(value):\n"
            "    helper = replacement()\n"
            "    return helper(value)\n"
        )
        boundary = next(
            item for item in shadowed["boundaries"]
            if item["kind"] == "unresolved_call" and item["target"]["text"] == "helper(...)"
        )
        self.assertIn("bound in the caller", boundary["reason"])
        self.assertNotIn("callee_origin", boundary)

        attribute = self.inspect(
            "def helper(value):\n"
            "    return value\n\n"
            "def caller(service, value):\n"
            "    return service.helper(value)\n"
        )
        self.assertFalse(self.propagated(attribute, "return_dependency"))
        self.assertTrue(any(item["target"]["text"] == "service.helper(...)" for item in attribute["boundaries"]))

        comprehension = self.inspect(
            "def helper(value):\n"
            "    return value\n\n"
            "def caller(handlers, value):\n"
            "    return [helper(value) for helper in handlers]\n"
        )
        self.assertFalse(self.propagated(comprehension, "return_dependency"))

        match_capture = self.inspect(
            "def helper(value):\n"
            "    return value\n\n"
            "def caller(value):\n"
            "    match value:\n"
            "        case helper:\n"
            "            return helper(value)\n"
        )
        self.assertFalse(self.propagated(match_capture, "return_dependency"))

    def test_propagated_return_names_are_labeled_with_callee_scope(self):
        card = self.inspect(
            "def choose_discount(customer_tier, subtotal):\n"
            "    return subtotal if customer_tier else 0\n\n"
            "def caller(tier, amount):\n"
            "    return choose_discount(subtotal=amount, customer_tier=tier)\n"
        )
        propagated = self.propagated(card, "return_dependency")[0]
        self.assertEqual(
            propagated["statement"]["scope"],
            {
                "kind": "callee",
                "function": "choose_discount",
                "names": ["customer_tier", "subtotal"],
            },
        )
        caller_claim = next(
            claim for claim in card["claims"]
            if claim["kind"] == "return_dependency" and not claim.get("call_chain")
        )
        dependencies = caller_claim["statement"]["local_call_dependencies"]
        self.assertEqual(
            {
                item["callee_parameter"]: (
                    item["callee_scope"],
                    item["caller_argument"],
                )
                for item in dependencies
            },
            {
                "customer_tier": ("choose_discount", "tier"),
                "subtotal": ("choose_discount", "amount"),
            },
        )
        rendered = terminal(card)
        self.assertIn("Callee scope: choose_discount (customer_tier, subtotal)", rendered)
        self.assertIn("Callee binding: choose_discount.customer_tier = tier", rendered)

    def test_non_shadowed_call_inside_comprehension_can_resolve(self):
        card = self.inspect(
            "def helper(value):\n"
            "    return value\n\n"
            "def caller(values):\n"
            "    return [helper(value) for value in values]\n"
        )
        claims = self.propagated(card, "return_dependency")
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["call_chain"][0]["callee"], "helper")
        self.assertFalse(
            any(
                boundary["kind"] == "unresolved_call"
                and boundary["target"]["text"] == "helper(...)"
                for boundary in card["boundaries"]
            )
        )

    def test_two_calls_keep_distinct_call_sites_without_duplicate_ids(self):
        card = self.inspect(
            "def helper(value):\n"
            "    return value\n\n"
            "def caller(first, second):\n"
            "    left = helper(first)\n"
            "    right = helper(second)\n"
            "    return left + right\n"
        )
        claims = self.propagated(card, "return_dependency")
        self.assertEqual(len(claims), 2)
        self.assertEqual(len({claim["id"] for claim in claims}), 2)
        self.assertEqual({claim["call_chain"][0]["call_site"]["start_line"] for claim in claims}, {5, 6})
        caller_claim = next(
            claim
            for claim in card["claims"]
            if claim["kind"] == "return_dependency" and not claim.get("call_chain")
        )
        self.assertEqual(caller_claim["statement"]["inputs"], ["first", "second"])
        self.assertEqual(
            len(caller_claim["statement"]["local_call_dependencies"]),
            2,
        )

    def test_helper_dependencies_follow_keyword_renaming_and_tuple_assignment(self):
        card = self.inspect(
            "def helper(price, rate):\n"
            "    return price, price * rate\n\n"
            "def caller(catalog, tax_rate):\n"
            "    price, taxed = helper(rate=tax_rate, price=catalog['sku'])\n"
            "    return taxed\n"
        )
        caller_claim = next(
            claim
            for claim in card["claims"]
            if claim["kind"] == "return_dependency" and not claim.get("call_chain")
        )
        self.assertEqual(caller_claim["statement"]["inputs"], ["catalog", "tax_rate"])
        dependencies = caller_claim["statement"]["local_call_dependencies"]
        self.assertEqual(
            {
                item["callee_parameter"]: (
                    item["caller_argument"],
                    item["caller_inputs"],
                )
                for item in dependencies
            },
            {
                "price": ("catalog['sku']", ["catalog"]),
                "rate": ("tax_rate", ["tax_rate"]),
            },
        )

    def test_handled_helper_exception_does_not_block_return_composition(self):
        card = self.inspect(
            "def helper(catalog, sku):\n"
            "    if sku not in catalog:\n"
            "        raise KeyError(sku)\n"
            "    return catalog[sku]\n\n"
            "def caller(records, catalog):\n"
            "    total = 0\n"
            "    for sku in records:\n"
            "        try:\n"
            "            total += helper(catalog, sku)\n"
            "        except KeyError:\n"
            "            continue\n"
            "    return total\n"
        )
        caller_claim = next(
            claim
            for claim in card["claims"]
            if claim["kind"] == "return_dependency" and not claim.get("call_chain")
        )
        self.assertEqual(caller_claim["statement"]["inputs"], ["catalog", "records"])
        self.assertFalse(self.propagated(card, "explicit_exception"))
        dependencies = caller_claim["statement"]["local_call_dependencies"]
        self.assertEqual(
            {
                item["callee_parameter"]: item["caller_inputs"]
                for item in dependencies
            },
            {"catalog": ["catalog"], "sku": ["records"]},
        )
        self.assertIn("helper(...)", [item["text"] for item in caller_claim["statement"]["calls"]])

    def test_starred_argument_binding_is_an_attached_boundary(self):
        card = self.inspect(
            "def helper(value):\n"
            "    return value\n\n"
            "def caller(values):\n"
            "    return helper(*values)\n"
        )
        caller_claim = next(
            claim
            for claim in card["claims"]
            if claim["kind"] == "return_dependency" and not claim.get("call_chain")
        )
        boundary = next(
            item
            for item in card["boundaries"]
            if item["id"].startswith("local-binding-")
        )
        self.assertEqual(boundary["kind"], "unsupported_semantics")
        self.assertIn("starred positional", boundary["reason"])
        self.assertIn(boundary["id"], caller_claim["boundary_ids"])
        self.assertNotIn("local_call_dependencies", caller_claim["statement"])

    def test_callee_return_boundary_limits_the_composed_caller_return(self):
        card = self.inspect(
            "def helper(value):\n"
            "    return external(value)\n\n"
            "def caller(raw):\n"
            "    return helper(raw)\n"
        )
        caller_claim = next(
            claim
            for claim in card["claims"]
            if claim["kind"] == "return_dependency" and not claim.get("call_chain")
        )
        dependency = caller_claim["statement"]["local_call_dependencies"][0]
        self.assertEqual(len(dependency["boundary_ids"]), 1)
        self.assertIn(dependency["boundary_ids"][0], caller_claim["boundary_ids"])
        boundary = next(
            item
            for item in card["boundaries"]
            if item["id"] == dependency["boundary_ids"][0]
        )
        self.assertEqual(boundary["target"]["text"], "external(...)")

    def test_callee_diagnostic_keeps_the_call_that_exposed_it(self):
        card = self.inspect(
            "def helper(resource):\n"
            "    with resource:\n"
            "        return 1\n\n"
            "def caller(resource):\n"
            "    return helper(resource)\n"
        )
        diagnostic = next(item for item in card["diagnostics"] if item.get("call_chain"))
        self.assertIn("module-local callee helper", diagnostic["message"])
        self.assertEqual(diagnostic["call_chain"][0]["caller"], "caller")


if __name__ == "__main__":
    unittest.main()
