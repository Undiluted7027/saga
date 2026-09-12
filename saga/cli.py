"""Command-line entry point for Saga's inspection slice."""

from __future__ import annotations

import argparse
import json
import sys

from .diagnostics import card_exit_code
from .inspect import inspect_function
from .render import terminal
from .testing import run_tests
from .views import focus_card


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
    inspect_parser.add_argument(
        "--view",
        choices=("full", "return", "mutation", "failure", "boundary"),
        default="full",
        help="show one fixed evidence view without changing analysis",
    )
    inspect_parser.add_argument(
        "--show-routine-boundaries",
        action="store_true",
        help="expand groups of routine unresolved calls in terminal output",
    )
    inspect_parser.add_argument(
        "--show-boundary-sites",
        action="store_true",
        help="list every source location in repeated boundary groups",
    )
    inspect_parser.add_argument(
        "--show-diagnostic-sites",
        action="store_true",
        help="list source locations in repeated diagnostic groups",
    )
    inspect_parser.add_argument(
        "--show-local-call-evidence",
        action="store_true",
        help="expand evidence grouped beneath module-local call sites",
    )
    inspect_parser.add_argument(
        "--show-return-sites",
        action="store_true",
        help="expand dependency sites in large return paths",
    )
    test_parser = subparsers.add_parser("test", help="run pytest with selected-target instrumentation")
    test_parser.add_argument("selector", help="target in the form path.py::qualified_name")
    test_parser.add_argument("--format", choices=("json", "terminal"), default="json")
    test_parser.add_argument(
        "--view",
        choices=("full", "return", "mutation", "failure", "boundary"),
        default="full",
        help="show one fixed evidence view without changing analysis",
    )
    test_parser.add_argument("--trace-output", default=".saga/trace.json")
    test_parser.add_argument(
        "--show-routine-boundaries",
        action="store_true",
        help="expand groups of routine unresolved calls in terminal output",
    )
    test_parser.add_argument(
        "--show-boundary-sites",
        action="store_true",
        help="list every source location in repeated boundary groups",
    )
    test_parser.add_argument(
        "--show-diagnostic-sites",
        action="store_true",
        help="list source locations in repeated diagnostic groups",
    )
    test_parser.add_argument(
        "--show-local-call-evidence",
        action="store_true",
        help="expand evidence grouped beneath module-local call sites",
    )
    test_parser.add_argument(
        "--show-return-sites",
        action="store_true",
        help="expand dependency sites in large return paths",
    )
    args = parser.parse_args(raw_argv)
    if "::" not in args.selector or args.selector.count("::") != 1:
        parser.error("selector must have the form path.py::qualified_name")
    if args.command == "inspect":
        file_path, qualified_name = args.selector.split("::")
        card = inspect_function(file_path, qualified_name)
        exit_code = card_exit_code(card)
    else:
        card, exit_code = run_tests(args.selector, pytest_args, args.trace_output)
    card = focus_card(card, args.view)
    if args.format == "json":
        print(json.dumps(card, indent=2, sort_keys=True))
    else:
        print(
            terminal(
                card,
                show_routine_boundaries=args.show_routine_boundaries,
                show_boundary_sites=args.show_boundary_sites,
                show_diagnostic_sites=args.show_diagnostic_sites,
                show_local_call_evidence=args.show_local_call_evidence,
                show_return_sites=args.show_return_sites,
            )
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
