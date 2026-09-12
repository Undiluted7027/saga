import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from saga.testing import run_tests
from saga.trace import TRACE_SCHEMA_VERSION


class TestCommandStatusTests(unittest.TestCase):
    """Keep static and instrumented inspection success semantics aligned."""

    def test_successful_test_run_ignores_nonblocking_static_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "module.py"
            target.write_text(
                "def billing(records):\n"
                "    while records:\n"
                "        return records[0]\n"
                "    return None\n",
                encoding="utf-8",
            )
            trace_path = root / "trace.json"

            def completed_run(*args, **kwargs):
                config = json.loads(
                    Path(kwargs["env"]["SAGA_TRACE_CONFIG"]).read_text(encoding="utf-8")
                )
                Path(config["output"]).write_text(
                    json.dumps({
                        "trace_schema_version": TRACE_SCHEMA_VERSION,
                        "executions": [],
                    }),
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch("saga.testing.subprocess.run", side_effect=completed_run):
                card, exit_code = run_tests(
                    f"{target}::billing",
                    [],
                    str(trace_path),
                    directory,
                )

        self.assertEqual(exit_code, 0)
        self.assertTrue(any(
            item["kind"] == "unsupported_semantics"
            for item in card["diagnostics"]
        ))

    def test_missing_trace_is_an_instrumentation_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "module.py"
            target.write_text("def target(value):\n    return value\n", encoding="utf-8")
            with patch(
                "saga.testing.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
            ):
                card, exit_code = run_tests(
                    f"{target}::target",
                    [],
                    str(Path(directory) / "missing-trace.json"),
                    directory,
                )

        self.assertEqual(exit_code, 1)
        self.assertTrue(any(
            item["kind"] == "instrumentation"
            for item in card["diagnostics"]
        ))

    def test_instrumented_callbacks_leave_one_distinct_usable_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "exposure.py"
            target.write_text(
                "def compute_exposure(account_id, fetch_balance):\n"
                "    return fetch_balance(account_id)\n",
                encoding="utf-8",
            )
            tests = root / "test_exposure.py"
            tests.write_text(
                "from exposure import compute_exposure\n\n"
                "def test_low():\n"
                "    assert compute_exposure(7, lambda _: 10) == 10\n\n"
                "def test_medium():\n"
                "    assert compute_exposure(7, lambda _: 20) == 20\n\n"
                "def test_high():\n"
                "    assert compute_exposure(7, lambda _: 30) == 30\n",
                encoding="utf-8",
            )
            card, exit_code = run_tests(
                f"{target}::compute_exposure",
                [str(tests), "-q"],
                str(root / "trace.json"),
                directory,
            )

        self.assertEqual(exit_code, 0)
        self.assertFalse([
            claim for claim in card["claims"]
            if claim["kind"] == "test_observation"
        ])
        self.assertEqual(card["observation_status"]["returned_executions"], 3)
        self.assertEqual(card["observation_status"]["distinct_inputs"], 1)
        self.assertEqual(
            card["observation_status"]["excluded_parameters"],
            [{
                "name": "fetch_balance",
                "serialization_kinds": ["unsupported"],
                "types": ["builtins.function"],
            }],
        )


if __name__ == "__main__":
    unittest.main()
