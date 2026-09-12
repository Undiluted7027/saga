"""Run pytest with opt-in target instrumentation and merge observations into a card."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .diagnostics import card_exit_code
from .inspect import inspect_function
from .observations import evaluate_observations
from .trace import TRACE_SCHEMA_VERSION


def _selector(selector: str) -> tuple[str, str]:
    """Split and validate the single-target selector used by Saga commands."""
    if "::" not in selector or selector.count("::") != 1:
        raise ValueError("selector must have the form path.py::qualified_name")
    return tuple(selector.split("::"))  # type: ignore[return-value]


def run_tests(selector: str, pytest_args: list[str], trace_output: str = ".saga/trace.json", cwd: str | None = None) -> tuple[dict[str, Any], int]:
    """Run pytest for one supported target and return its combined evidence card."""
    file_path, qualified_name = _selector(selector)
    card = inspect_function(file_path, qualified_name)
    if card_exit_code(card):
        return card, card_exit_code(card)
    destination = Path(trace_output)
    if not destination.is_absolute():
        destination = Path(cwd or os.getcwd()) / destination
    # A failed pytest startup must not make a previous run look current.
    destination.unlink(missing_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as settings_file:
        settings_path = Path(settings_file.name)
        json.dump({"path": file_path, "qualified_name": qualified_name, "output": str(destination)}, settings_file)
    environment = os.environ.copy()
    environment["SAGA_TRACE_CONFIG"] = str(settings_path)
    try:
        process = subprocess.run([sys.executable, "-m", "pytest", "-p", "saga.pytest_plugin", *pytest_args], cwd=cwd, env=environment, capture_output=True, text=True)
    except OSError as exc:
        card["diagnostics"].append({"kind": "test_run", "message": f"Could not start pytest: {exc}", "analyses": ["observations"]})
        return card, 1
    finally:
        settings_path.unlink(missing_ok=True)
    if process.returncode != 0:
        card["diagnostics"].append({"kind": "test_run", "message": f"Pytest exited with status {process.returncode}.", "analyses": ["observations"]})
    if not destination.exists():
        card["diagnostics"].append({"kind": "instrumentation", "message": "Saga did not receive a trace from pytest.", "analyses": ["observations"]})
        return card, process.returncode or 1
    try:
        trace = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        card["diagnostics"].append({"kind": "instrumentation", "message": f"Could not read the Saga trace: {exc}", "analyses": ["observations"]})
        return card, process.returncode or 1
    if trace.get("trace_schema_version") != TRACE_SCHEMA_VERSION:
        card["diagnostics"].append({"kind": "instrumentation", "message": "Saga received an unsupported trace schema version.", "analyses": ["observations"]})
        return card, process.returncode or 1
    observations = evaluate_observations(card, trace)
    card["claims"].extend(observations.claims)
    card["observation_status"] = observations.status
    return card, process.returncode or card_exit_code(card)
