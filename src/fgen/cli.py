"""Command line entry point: ``uv run fgen <cmd>``."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fgen", description="Factorio blueprint generator")
    parser.add_subparsers(dest="cmd")
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
