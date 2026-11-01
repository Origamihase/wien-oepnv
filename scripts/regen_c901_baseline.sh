#!/usr/bin/env bash
# Regenerate .c901-baseline.txt from the current source tree.
#
# Run this after intentionally changing the set of C901 violations or their
# values — a refactor that makes a function simpler (locks the improvement
# in; the gate fails until the baseline follows), a new function above the
# threshold that a reviewer approved (rarer — say so in the PR description),
# or a ruff upgrade that measures differently.
#
# The measurement and the file format live in one place,
# scripts/check_complexity.py (``--write-baseline``): src/ and scripts/,
# one ``<path>::<function> <complexity>`` line per function above 15. It
# uses the ruff of the running interpreter, so run it in the dev venv
# (``pip install -r requirements-dev.txt``), the same ruff CI uses. If ruff
# cannot measure, nothing is written and the script exits 2.
#
# Usage:
#   bash scripts/regen_c901_baseline.sh
# The script overwrites .c901-baseline.txt at the repo root.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

exec python scripts/check_complexity.py --write-baseline
