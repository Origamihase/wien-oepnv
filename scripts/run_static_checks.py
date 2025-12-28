#!/usr/bin/env python3
"""Run project static analysis helpers (ruff + mypy).

This utility mirrors the checks executed in the CI workflow so that
contributors can reproduce the results locally with a single command.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str]) -> int:
    """Execute *command* inside the project root and stream the output."""
    print("→", " ".join(command), flush=True)
    try:
        # Enforce a 5-minute timeout for static checks
        completed = subprocess.run(
            command, cwd=PROJECT_ROOT, check=False, timeout=300
        )
        return completed.returncode
    except subprocess.TimeoutExpired:
        print(f"Command timed out after 300s: {' '.join(command)}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Allow ruff to apply autofixes before running mypy.",
    )
    parser.add_argument(
        "--ruff-args",
        nargs=argparse.REMAINDER,
        help="Additional arguments forwarded to 'ruff check'.",
    )
    args = parser.parse_args()

    ruff_command = ["ruff", "check"]
    if args.fix:
        ruff_command.append("--fix")
    if args.ruff_args:
        ruff_command.extend(args.ruff_args)

    exit_code = _run(ruff_command)

    if exit_code == 0:
        exit_code = _run(["mypy"])

    if exit_code == 0:
        scanner = PROJECT_ROOT / "scripts" / "scan_secrets.py"
        exit_code = _run([sys.executable, str(scanner)])

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
