import tempfile
import unittest
from pathlib import Path

from saga.inspect import inspect_function
from saga.render import terminal


class EscapingExceptionTests(unittest.TestCase):
    """Exercise explicit raise flow without inferring implicit exceptions."""

    def inspect(self, source: str, target: str = "target") -> dict:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        path = Path(tempdir.name) / "module.py"
        path.write_text(source, encoding="utf-8")
        return inspect_function(str(path), target)

    def exceptions(self, card: dict) -> list[dict]:
        return [claim for claim in card["claims"] if claim["kind"] == "explicit_exception"]

    def test_conditional_raise_after_entry_prefix_can_escape(self):
        card = self.inspect(
            "def target(value):\n"
            "    result = value + 1\n"
            "    if result < 0:\n"
            "        raise ValueError('negative')\n"
            "    return result\n"
        )
        claim = self.exceptions(card)[0]
        self.assertEqual(claim["statement"]["text"], "Raises ValueError when result is less than 0.")
        self.assertEqual(claim["evidence"]["method"], "explicit_raise_flow")
        self.assertEqual([span["start_line"] for span in claim["source_spans"]], [3, 4])

    def test_compound_condition_is_parenthesized_before_outer_conjunction(self):
        card = self.inspect(
            "def target(left, right, failed):\n"
            "    if left or right:\n"
            "        if failed:\n"
            "            raise RuntimeError('failed')\n"
        )
        claim = self.exceptions(card)[0]
        self.assertEqual(
            claim["statement"]["text"],
            "Raises RuntimeError when (left is truthy or right is truthy) and failed is truthy.",
        )

    def test_assert_after_state_change_reports_conditional_assertion_error(self):
        card = self.inspect(
            "def target(value):\n"
            "    normalized = value.strip()\n"
            "    assert normalized\n"
            "    return normalized\n"
        )
        claim = next(
            item
            for item in self.exceptions(card)
            if item["statement"]["exception"].get("name") == "AssertionError"
        )
        self.assertEqual(
            claim["statement"]["text"],
            "May raise AssertionError unless normalized is truthy.",
        )
        self.assertEqual(claim["evidence"]["method"], "assert_statement")
        self.assertIn("not (normalized)", claim["statement"]["condition_source_text"])
        self.assertIn("__debug__", claim["assumptions"][0]["text"])

    def test_caught_assertion_error_does_not_escape(self):
        card = self.inspect(
            "def target(value):\n"
            "    try:\n"
            "        assert value\n"
            "    except AssertionError:\n"
            "        return None\n"
        )
        self.assertFalse(self.exceptions(card))

    def test_matching_and_broad_handlers_remove_caught_exceptions(self):
        exact = self.inspect(
            "def target():\n"
            "    try:\n"
            "        raise ValueError()\n"
            "    except ValueError:\n"
            "        return 0\n"
        )
        broad = self.inspect(
            "def target():\n"
            "    try:\n"
            "        raise ValueError()\n"
            "    except Exception:\n"
            "        return 0\n"
        )
        mismatch = self.inspect(
            "def target():\n"
            "    try:\n"
            "        raise ValueError()\n"
            "    except TypeError:\n"
            "        return 0\n"
        )
        self.assertFalse(self.exceptions(exact))
        self.assertFalse(self.exceptions(broad))
        self.assertEqual(self.exceptions(mismatch)[0]["statement"]["exception"]["name"], "ValueError")

    def test_reraise_keeps_handler_evidence_and_uncertainty(self):
        typed = self.inspect(
            "def target():\n"
            "    try:\n"
            "        work()\n"
            "    except ValueError:\n"
            "        raise\n"
        )
        broad = self.inspect(
            "def target():\n"
            "    try:\n"
            "        work()\n"
            "    except Exception:\n"
            "        raise\n"
        )
        typed_claim = self.exceptions(typed)[0]
        self.assertEqual(typed_claim["statement"]["text"], "Re-raises an exception matched by ValueError.")
        self.assertEqual(typed_claim["statement"]["exception"]["handler_types"], ["ValueError"])
        self.assertEqual([span["start_line"] for span in typed_claim["source_spans"]], [4, 5])
        self.assertEqual(
            self.exceptions(broad)[0]["statement"]["text"],
            "Re-raises an exception of unknown concrete type matched by Exception.",
        )

        bare = self.inspect(
            "def target():\n"
            "    try:\n"
            "        work()\n"
            "    except:\n"
            "        raise\n"
        )
        self.assertEqual(self.exceptions(bare)[0]["statement"]["text"], "Re-raises a caught exception of unknown type.")

    def test_explicit_raise_names_the_handler_that_makes_it_reachable(self):
        card = self.inspect(
            "def target(value):\n"
            "    try:\n"
            "        decode(value)\n"
            "    except ValueError:\n"
            "        raise TypeError('bad value')\n"
        )
        claim = self.exceptions(card)[0]
        self.assertEqual(
            claim["statement"]["text"],
            "Raises TypeError when the ValueError handler runs.",
        )
        self.assertEqual(
            claim["statement"]["condition_source_text"],
            "except ValueError",
        )

    def test_finally_raise_is_reported_without_hiding_protected_raise(self):
        card = self.inspect(
            "def target(flag):\n"
            "    try:\n"
            "        if flag:\n"
            "            raise ValueError()\n"
            "    finally:\n"
            "        if not flag:\n"
            "            raise RuntimeError()\n"
        )
        names = {claim["statement"]["exception"]["name"] for claim in self.exceptions(card)}
        self.assertEqual(names, {"ValueError", "RuntimeError"})

        replacing = self.inspect(
            "def target():\n"
            "    try:\n"
            "        raise ValueError()\n"
            "    finally:\n"
            "        raise RuntimeError()\n"
        )
        self.assertEqual(
            [claim["statement"]["exception"]["name"] for claim in self.exceptions(replacing)],
            ["RuntimeError"],
        )

    def test_unreachable_raise_is_not_reported(self):
        direct = self.inspect("def target():\n    return 1\n    raise ValueError()\n")
        branches = self.inspect(
            "def target(flag):\n"
            "    if flag:\n"
            "        return 1\n"
            "    else:\n"
            "        return 2\n"
            "    raise ValueError()\n"
        )
        self.assertFalse(self.exceptions(direct))
        self.assertFalse(self.exceptions(branches))

        handled_exit = self.inspect(
            "def target():\n"
            "    try:\n"
            "        raise ValueError()\n"
            "    except ValueError:\n"
            "        return 1\n"
            "    raise RuntimeError()\n"
        )
        self.assertFalse(self.exceptions(handled_exit))

    def test_unknown_matching_emits_a_boundary_instead_of_guessing(self):
        card = self.inspect(
            "def target(problem):\n"
            "    try:\n"
            "        raise problem\n"
            "    except DomainError:\n"
            "        return 0\n"
        )
        claim = self.exceptions(card)[0]
        boundary = next(item for item in card["boundaries"] if item["kind"] == "exception_matching")
        self.assertIn(boundary["id"], claim["boundary_ids"])
        self.assertEqual(boundary["source_span"]["start_line"], 4)
        self.assertEqual(claim["source_spans"][-1]["start_line"], 4)

        bare = self.inspect(
            "def target(problem):\n"
            "    try:\n"
            "        raise problem\n"
            "    except:\n"
            "        return 0\n"
        )
        self.assertFalse(self.exceptions(bare))
        self.assertFalse([item for item in bare["boundaries"] if item["kind"] == "exception_matching"])

        fallback = self.inspect(
            "def target(problem):\n"
            "    try:\n"
            "        raise problem\n"
            "    except DomainError:\n"
            "        return 1\n"
            "    except:\n"
            "        return 0\n"
        )
        self.assertFalse(self.exceptions(fallback))
        self.assertFalse([item for item in fallback["boundaries"] if item["kind"].startswith("exception_")])

    def test_shadowed_builtin_exception_name_is_not_trusted(self):
        card = self.inspect(
            "def ValueError(message):\n"
            "    return make_problem(message)\n\n"
            "def target():\n"
            "    raise ValueError('bad')\n"
        )
        claim = self.exceptions(card)[0]
        self.assertEqual(claim["statement"]["exception"]["kind"], "unknown_exception")
        self.assertNotIn("builtin exception class", " ".join(item["text"] for item in claim["assumptions"]))
        self.assertTrue(any(item["kind"] == "exception_dispatch" for item in card["boundaries"]))

    def test_local_callee_exception_is_propagated_or_locally_handled(self):
        source = (
            "def helper(value):\n"
            "    if value:\n"
            "        raise ValueError('bad')\n\n"
            "def target(value):\n"
            "    return helper(value)\n"
        )
        card = self.inspect(source)
        propagated = [claim for claim in self.exceptions(card) if claim.get("call_chain")]
        self.assertEqual(len(propagated), 1)
        self.assertEqual(propagated[0]["statement"]["text"], "helper(...) may raise ValueError when value is truthy.")
        self.assertEqual(propagated[0]["call_chain"][0]["callee"], "helper")

        handled = self.inspect(
            "def helper():\n"
            "    raise ValueError()\n\n"
            "def target():\n"
            "    try:\n"
            "        helper()\n"
            "    except ValueError:\n"
            "        return 0\n"
        )
        self.assertFalse([claim for claim in self.exceptions(handled) if claim.get("call_chain")])

    def test_unknown_callee_does_not_invent_exception_claims(self):
        card = self.inspect("def target(value):\n    return external(value)\n")
        self.assertFalse(self.exceptions(card))
        self.assertTrue(any(item["kind"] == "unresolved_call" for item in card["boundaries"]))

    def test_context_manager_suppression_is_an_explicit_limit(self):
        direct = self.inspect(
            "def target(manager):\n"
            "    with manager:\n"
            "        raise ValueError()\n"
        )
        claim = self.exceptions(direct)[0]
        boundary = next(item for item in direct["boundaries"] if item["kind"] == "exception_matching")
        self.assertIn(boundary["id"], claim["boundary_ids"])
        self.assertIn("suppress", boundary["reason"])

        propagated = self.inspect(
            "def helper():\n"
            "    raise ValueError()\n\n"
            "def target(manager):\n"
            "    with manager:\n"
            "        helper()\n"
        )
        claim = next(item for item in self.exceptions(propagated) if item.get("call_chain"))
        self.assertTrue(claim["boundary_ids"])
        self.assertTrue(any(item["kind"] == "exception_matching" for item in propagated["boundaries"]))

    def test_terminal_uses_the_canonical_exception_text(self):
        card = self.inspect("def target(value):\n    if value:\n        raise ValueError()\n")
        claim = self.exceptions(card)[0]
        self.assertIn(claim["statement"]["text"], terminal(card))

        checked = self.inspect(
            "def target():\n"
            "    try:\n"
            "        raise ValueError()\n"
            "    except TypeError:\n"
            "        return 0\n"
        )
        self.assertIn("Handler checked:", terminal(checked))


if __name__ == "__main__":
    unittest.main()
