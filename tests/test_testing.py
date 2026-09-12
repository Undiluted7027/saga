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


if __name__ == "__main__":
    unittest.main()
