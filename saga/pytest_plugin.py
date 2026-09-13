"""Opt-in pytest boundary instrumentation for selected Saga targets."""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from .trace import TRACE_SCHEMA_VERSION, serialize_value


class TargetTracer:
    """Record only calls and outcomes for one selected function boundary."""

    def __init__(self, path: str, qualified_name: str) -> None:
        self.path = os.path.realpath(path)
        self.name = qualified_name.rsplit(".", 1)[-1]
        self.qualified_name = qualified_name
        self.test_id: str | None = None
        self.frames: dict[int, dict[str, Any]] = {}
        self.executions: list[dict[str, Any]] = []

    def _matches(self, frame: Any) -> bool:
        """Match by code path and function name without importing the target."""
        return (
            os.path.realpath(frame.f_code.co_filename) == self.path
            and frame.f_code.co_name == self.name
            and frame.f_code.co_qualname == self.qualified_name
        )

    def _inputs(self, frame: Any) -> dict[str, Any]:
        """Capture bounded argument structure at the selected function boundary."""
        code = frame.f_code
        count = code.co_argcount + code.co_kwonlyargcount
        names = list(code.co_varnames[:count])
        if code.co_flags & 0x04:
            names.append(code.co_varnames[count])
            count += 1
        if code.co_flags & 0x08:
            names.append(code.co_varnames[count])
        return {name: serialize_value(frame.f_locals.get(name), name) for name in names if name in frame.f_locals}

    def trace(self, frame: Any, event: str, arg: Any) -> Any:
        """Trace target calls, returns, and escaping exceptions while ignoring all other frames."""
        frame_id = id(frame)
        if event == "call" and self._matches(frame):
            self.frames[frame_id] = {"test_id": self.test_id or "<unknown>", "input": self._inputs(frame), "pending_exception": None}
        execution = self.frames.get(frame_id)
        if execution is not None:
            if event == "exception":
                exception_type = type(arg[1]).__name__ if isinstance(arg, tuple) and len(arg) > 1 else "Exception"
                execution["pending_exception"] = {"type": exception_type}
            elif event == "line":
                execution["pending_exception"] = None
            elif event == "return":
                if execution["pending_exception"] is not None:
                    result = {"test_id": execution["test_id"], "input": execution["input"], "outcome": "raise", "exception": execution["pending_exception"]}
                else:
                    result = {"test_id": execution["test_id"], "input": execution["input"], "outcome": "return", "return": serialize_value(arg)}
                self.executions.append(result)
                self.frames.pop(frame_id, None)
        return self.trace

    def write(self, output_path: str) -> None:
        """Write the versioned trace after pytest has finished executing tests."""
        payload = {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "target": {"path": self.path, "qualified_name": self.qualified_name},
            "environment": {"python_version": sys.version.split()[0], "platform": platform.platform()},
            "executions": self.executions,
        }
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _tracer(config: Any) -> TargetTracer | None:
    """Load instrumentation settings supplied by the Saga test command."""
    settings_path = os.environ.get("SAGA_TRACE_CONFIG")
    if not settings_path:
        return None
    settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    tracer = TargetTracer(settings["path"], settings["qualified_name"])
    config._saga_tracer = tracer
    config._saga_trace_output = settings["output"]
    return tracer


def pytest_configure(config: Any) -> None:
    """Initialize selected-target instrumentation when Saga explicitly requests it."""
    _tracer(config)


def pytest_runtest_call(item: Any) -> Any:
    """Wrap each pytest call so recorded executions retain their supporting test ID."""
    tracer = getattr(item.config, "_saga_tracer", None)
    if tracer is None:
        # Hookwrappers must yield even when instrumentation was not configured;
        # returning early makes pytest report a broken wrapper instead of running
        # the test normally.
        yield
        return
    previous = sys.gettrace()
    tracer.test_id = item.nodeid
    sys.settrace(tracer.trace)
    try:
        yield
    finally:
        sys.settrace(previous)
        tracer.test_id = None


pytest_runtest_call.hookwrapper = True


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """Persist traces even when pytest reports a failing test run."""
    tracer = getattr(session.config, "_saga_tracer", None)
    if tracer is not None:
        tracer.write(session.config._saga_trace_output)
