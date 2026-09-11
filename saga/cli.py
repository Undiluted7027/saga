"""Command-line entry point for Saga's inspection slice."""

from __future__ import annotations

import argparse
import json
import sys

from .inspect import inspect_function
from .render import terminal


def main(argv: list[str] | None = None) -> int:
    """Parse a selector, inspect it, and render the resulting evidence card."""
    parser = argparse.ArgumentParser(prog="saga")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="inspect one Python function")
    inspect_parser.add_argument("selector", help="target in the form path.py::qualified_name")
    inspect_parser.add_argument("--format", choices=("json", "terminal"), default="json")
    args = parser.parse_args(argv)
    if "::" not in args.selector or args.selector.count("::") != 1:
        parser.error("selector must have the form path.py::qualified_name")
    file_path, qualified_name = args.selector.split("::")
    card = inspect_function(file_path, qualified_name)
    if args.format == "json":
        print(json.dumps(card, indent=2, sort_keys=True))
    else:
        print(terminal(card))
    return 1 if card["diagnostics"] else 0


if __name__ == "__main__":
    sys.exit(main())
