#!/usr/bin/env bash
# Regenerate .mypy-baseline.txt from the current source tree.
#
# Run this after intentionally changing the set of mypy errors —
# typically when fixing pre-existing errors or adding new code that
# legitimately surfaces new errors that should be allowlisted.
#
# Requirements:
# - Python 3.11
# - mypy 1.10.x (matches CI; pinned in requirements-dev.txt)
#
# Usage:
#   bash scripts/regen_mypy_baseline.sh
# The script overwrites .mypy-baseline.txt at the repo root.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Ensure a CI-matching mypy version is installed.
pip install -q "mypy>=1.10,<1.11"

CURRENT="$(mktemp)"
trap 'rm -f "$CURRENT"' EXIT

# `--no-pretty` flattens each error to a single line for parseable diffs.
# `python3 -m mypy` (not bare `mypy`) ensures we run the pip-installed
# version, not a PATH-earlier copy from uv/pipx that may be a different
# version. CI is unaffected (fresh runner has no shadow), but local dev
# environments often do.
PYTHONPATH=src python3 -m mypy --no-pretty src tests > "$CURRENT" 2>&1 || true

grep -E " error:" "$CURRENT" \
  | sed -E 's/^([^:]+):[0-9]+(:[0-9]+)?: /\1: /' \
  | sort > .mypy-baseline.txt

echo "Wrote .mypy-baseline.txt ($(wc -l < .mypy-baseline.txt) lines)"
