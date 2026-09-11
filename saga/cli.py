"""Command-line entry point for Saga's inspection slice."""

from __future__ import annotations

import argparse
import json
import sys

from .inspect import inspect_function
from .render import terminal
from .testing import run_tests


def main(argv: list[str] | None = None) -> int:
    """Parse a selector, inspect it, and render the resulting evidence card."""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    pytest_args: list[str] = []
    if "test" in raw_argv and "--" in raw_argv:
        separator = raw_argv.index("--")
        pytest_args = raw_argv[separator + 1:]
        raw_argv = raw_argv[:separator]
    parser = argparse.ArgumentParser(prog="saga")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="inspect one Python function")
    inspect_parser.add_argument("selector", help="target in the form path.py::qualified_name")
    inspect_parser.add_argument("--format", choices=("json", "terminal"), default="json")
    test_parser = subparsers.add_parser("test", help="run pytest with selected-target instrumentation")
    test_parser.add_argument("selector", help="target in the form path.py::qualified_name")
    test_parser.add_argument("--format", choices=("json", "terminal"), default="json")
    test_parser.add_argument("--trace-output", default=".saga/trace.json")
    args = parser.parse_args(raw_argv)
    if "::" not in args.selector or args.selector.count("::") != 1:
        parser.error("selector must have the form path.py::qualified_name")
    if args.command == "inspect":
        file_path, qualified_name = args.selector.split("::")
        card = inspect_function(file_path, qualified_name)
        exit_code = 1 if card["diagnostics"] else 0
    else:
        card, exit_code = run_tests(args.selector, pytest_args, args.trace_output)
    if args.format == "json":
        print(json.dumps(card, indent=2, sort_keys=True))
    else:
        print(terminal(card))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
