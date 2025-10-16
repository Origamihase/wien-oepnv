#!/usr/bin/env python3
"""Run the configured static analysis tools in a single step."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LINT_TARGETS = ["src", "tests", "scripts"]
TYPE_TARGETS = ["src/build_feed.py"]
COMMANDS = [
    ("ruff", ["check", *LINT_TARGETS]),
    ("mypy", [*TYPE_TARGETS]),
]


def main() -> int:
    exit_code = 0
    for binary, args in COMMANDS:
        executable = shutil.which(binary)
        if executable is None:
            print(
                f"{binary} ist nicht installiert. Bitte 'pip install -r requirements-dev.txt' ausführen.",
                file=sys.stderr,
            )
            exit_code = 1
            continue

        print(f"→ {binary} {' '.join(args)}", flush=True)
        result = subprocess.run([executable, *args], cwd=REPO_ROOT)
        if result.returncode != 0:
            exit_code = result.returncode
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
