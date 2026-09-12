import tempfile
import unittest
from pathlib import Path

from saga.inspect import inspect_function
from saga.views import focus_card


class TryEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write(self, source: str) -> str:
        path = Path(self.tempdir.name) / "target.py"
        path.write_text(source, encoding="utf-8")
        return str(path)

    @staticmethod
    def try_boundaries(card: dict) -> list[dict]:
        return [
            boundary
            for boundary in card["boundaries"]
            if boundary["kind"] == "unsupported_semantics"
            and boundary["target"]["text"] == "try statement"
        ]

    def test_try_reports_effects_from_every_child_block_as_conditional(self):
        path = self.write(
            "from pathlib import Path\n\n"
            "def bill(state, declined, notify, path):\n"
            "    try:\n"
            "        declined.append('body')\n"
            "        Path(path).write_text('charged')\n"
            "    except ValueError:\n"
            "        state.status = 'declined'\n"
            "        declined.append('value')\n"
            "    except TypeError:\n"
            "        notify(declined)\n"
            "    else:\n"
            "        declined.append('accepted')\n"
            "    finally:\n"
            "        state.finished = True\n"
            "        declined.append('finished')\n"
        )
        card = inspect_function(path, "bill")
        conditional = self.try_boundaries(card)
        self.assertEqual(len(conditional), 1)
        conditional_id = conditional[0]["id"]

        diagnostics = [item for item in card["diagnostics"] if "Try/except effect" in item["message"]]
        self.assertEqual(len(diagnostics), 1)

        writes = [claim for claim in card["claims"] if claim["kind"] == "attempted_write"]
        self.assertEqual(
            {claim["statement"]["source_text"] for claim in writes},
            {"state.status", "state.finished"},
        )
        self.assertTrue(all(conditional_id in claim["boundary_ids"] for claim in writes))
        self.assertTrue(all(claim["statement"]["text"].startswith("Attempts") for claim in writes))

        known = [claim for claim in card["claims"] if claim["kind"] == "known_effect"]
        self.assertEqual(len(known), 1)
        self.assertIn(conditional_id, known[0]["boundary_ids"])
        self.assertTrue(known[0]["statement"]["text"].startswith("May"))

        calls = [
            boundary
            for boundary in card["boundaries"]
            if boundary["kind"] == "unresolved_call"
        ]
        call_lines = {boundary["source_span"]["start_line"] for boundary in calls}
        self.assertTrue({5, 9, 11, 13, 16}.issubset(call_lines))
        self.assertTrue(all(conditional_id in boundary.get("boundary_ids", []) for boundary in calls))
        mutation = focus_card(card, "mutation")
        self.assertIn(conditional_id, {boundary["id"] for boundary in mutation["boundaries"]})

    def test_nested_try_effect_keeps_outer_and_inner_limits(self):
        path = self.write(
            "def update(state):\n"
            "    try:\n"
            "        try:\n"
            "            state.value = 1\n"
            "        finally:\n"
            "            state.inner_done = True\n"
            "    finally:\n"
            "        state.outer_done = True\n"
        )
        card = inspect_function(path, "update")
        conditional = sorted(self.try_boundaries(card), key=lambda item: item["source_span"]["start_line"])
        self.assertEqual(len(conditional), 2)
        outer_id, inner_id = [item["id"] for item in conditional]
        writes = {
            claim["statement"]["source_text"]: claim
            for claim in card["claims"]
            if claim["kind"] == "attempted_write"
        }
        self.assertEqual(set(writes["state.value"]["boundary_ids"]) & {outer_id, inner_id}, {outer_id, inner_id})
        self.assertEqual(set(writes["state.inner_done"]["boundary_ids"]) & {outer_id, inner_id}, {outer_id, inner_id})
        self.assertIn(outer_id, writes["state.outer_done"]["boundary_ids"])
        self.assertNotIn(inner_id, writes["state.outer_done"]["boundary_ids"])
        self.assertEqual(conditional[1].get("boundary_ids"), [outer_id])

    def test_local_effect_called_inside_try_keeps_the_caller_limit(self):
        path = self.write(
            "from pathlib import Path\n\n"
            "def persist(path):\n"
            "    Path(path).write_text('done')\n\n"
            "def update(path):\n"
            "    try:\n"
            "        persist(path)\n"
            "    except OSError:\n"
            "        pass\n"
        )
        card = inspect_function(path, "update")
        conditional_id = self.try_boundaries(card)[0]["id"]
        known = next(claim for claim in card["claims"] if claim["kind"] == "known_effect")
        self.assertTrue(known.get("call_chain"))
        self.assertIn(conditional_id, known["boundary_ids"])


class WhileEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write(self, source: str) -> str:
        path = Path(self.tempdir.name) / "target.py"
        path.write_text(source, encoding="utf-8")
        return str(path)

    @staticmethod
    def while_boundaries(card: dict) -> list[dict]:
        return [
            boundary
            for boundary in card["boundaries"]
            if boundary["kind"] == "unsupported_semantics"
            and boundary["target"]["text"] == "while statement"
        ]

    def test_while_inspects_test_body_and_else_as_conditional(self):
        path = self.write(
            "from pathlib import Path\n\n"
            "def update(state, values, poll, notify, path):\n"
            "    while poll():\n"
            "        state.current = values[0]\n"
            "        values.append(state.current)\n"
            "        Path(path).write_text('updated')\n"
            "    else:\n"
            "        state.finished = True\n"
            "        notify(state)\n"
        )
        card = inspect_function(path, "update")
        conditional = self.while_boundaries(card)
        self.assertEqual(len(conditional), 1)
        conditional_id = conditional[0]["id"]
        self.assertIn("conditional or repeated", conditional[0]["reason"])
        self.assertIn("does not determine which regions execute or how often", conditional[0]["reason"])

        diagnostics = [
            item
            for item in card["diagnostics"]
            if "While-loop analysis" in item["message"]
        ]
        self.assertEqual(len(diagnostics), 1)

        writes = {
            claim["statement"]["source_text"]: claim
            for claim in card["claims"]
            if claim["kind"] == "attempted_write"
        }
        self.assertEqual(set(writes), {"state.current", "state.finished"})
        self.assertTrue(
            all(conditional_id in claim["boundary_ids"] for claim in writes.values())
        )
        self.assertTrue(
            all(
                claim["statement"]["text"].startswith("Attempts")
                for claim in writes.values()
            )
        )

        known = [claim for claim in card["claims"] if claim["kind"] == "known_effect"]
        self.assertEqual(len(known), 1)
        self.assertIn(conditional_id, known[0]["boundary_ids"])
        self.assertTrue(known[0]["statement"]["text"].startswith("May"))

        calls = {
            boundary["target"]["text"]: boundary
            for boundary in card["boundaries"]
            if boundary["kind"] == "unresolved_call"
        }
        self.assertTrue(
            {"poll(...)", "values.append(...)", "notify(...)"}.issubset(calls)
        )
        self.assertTrue(
            all(
                conditional_id in calls[target].get("boundary_ids", [])
                for target in ("poll(...)", "values.append(...)", "notify(...)")
            )
        )

        mutation = focus_card(card, "mutation")
        mutation_boundary_ids = {boundary["id"] for boundary in mutation["boundaries"]}
        self.assertIn(conditional_id, mutation_boundary_ids)
        self.assertTrue(
            {
                calls[target]["id"]
                for target in ("poll(...)", "values.append(...)", "notify(...)")
            }.issubset(mutation_boundary_ids)
        )

    def test_nested_try_and_while_boundaries_stack(self):
        path = self.write(
            "def update(state, outer, inner):\n"
            "    try:\n"
            "        while outer():\n"
            "            try:\n"
            "                while inner():\n"
            "                    state.value = 1\n"
            "            finally:\n"
            "                state.inner_done = True\n"
            "    finally:\n"
            "        state.outer_done = True\n"
        )
        card = inspect_function(path, "update")
        try_boundaries = sorted(
            (
                boundary
                for boundary in card["boundaries"]
                if boundary["kind"] == "unsupported_semantics"
                and boundary["target"]["text"] == "try statement"
            ),
            key=lambda item: item["source_span"]["start_line"],
        )
        while_boundaries = sorted(
            self.while_boundaries(card),
            key=lambda item: item["source_span"]["start_line"],
        )
        self.assertEqual(len(try_boundaries), 2)
        self.assertEqual(len(while_boundaries), 2)
        outer_try, inner_try = try_boundaries
        outer_while, inner_while = while_boundaries

        self.assertEqual(outer_while.get("boundary_ids"), [outer_try["id"]])
        self.assertEqual(
            inner_try.get("boundary_ids"),
            [outer_try["id"], outer_while["id"]],
        )
        self.assertEqual(
            inner_while.get("boundary_ids"),
            [outer_try["id"], outer_while["id"], inner_try["id"]],
        )

        writes = {
            claim["statement"]["source_text"]: claim
            for claim in card["claims"]
            if claim["kind"] == "attempted_write"
        }
        self.assertEqual(
            writes["state.value"]["boundary_ids"][-4:],
            [
                outer_try["id"],
                outer_while["id"],
                inner_try["id"],
                inner_while["id"],
            ],
        )
        self.assertEqual(
            writes["state.inner_done"]["boundary_ids"][-3:],
            [outer_try["id"], outer_while["id"], inner_try["id"]],
        )
        self.assertIn(outer_try["id"], writes["state.outer_done"]["boundary_ids"])
        self.assertNotIn(outer_while["id"], writes["state.outer_done"]["boundary_ids"])

    def test_address_list_shape_reports_loop_mutations(self):
        path = self.write(
            "def get_address_list(value, address_list):\n"
            "    while value:\n"
            "        address_list.append(value.pop())\n"
            "        address_list.defects.append('invalid')\n"
            "        mailbox = parse_mailbox(value)\n"
            "        mailbox.token_type = 'mailbox'\n"
            "        mailbox.extend(value)\n"
            "    return address_list\n"
        )
        card = focus_card(inspect_function(path, "get_address_list"), "mutation")
        conditional = self.while_boundaries(card)
        self.assertEqual(len(conditional), 1)
        conditional_id = conditional[0]["id"]

        writes = [
            claim for claim in card["claims"] if claim["kind"] == "attempted_write"
        ]
        self.assertEqual(
            {claim["statement"]["source_text"] for claim in writes},
            {"mailbox.token_type"},
        )
        self.assertIn(conditional_id, writes[0]["boundary_ids"])

        calls = {
            boundary["target"]["text"]: boundary
            for boundary in card["boundaries"]
            if boundary["kind"] == "unresolved_call"
        }
        expected = {
            "address_list.append(...)",
            "address_list.defects.append(...)",
            "mailbox.extend(...)",
        }
        self.assertTrue(expected.issubset(calls))
        self.assertTrue(
            all(
                conditional_id in calls[target].get("boundary_ids", [])
                for target in expected
            )
        )


if __name__ == "__main__":
    unittest.main()
