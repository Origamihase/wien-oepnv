#!/usr/bin/env bash
# Regenerate .c901-baseline.txt from the current source tree.
#
# Run this after intentionally changing the set of C901 violations —
# typically when a refactor reduces a function below the threshold
# (good — locks in the improvement) or when a new function exceeds
# threshold and is reviewer-approved (rarer — requires explicit
# acknowledgement in the PR description).
#
# Requirements:
# - ruff (matches the project's pinned version)
#
# Usage:
#   bash scripts/regen_c901_baseline.sh
# The script overwrites .c901-baseline.txt at the repo root.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

THRESHOLD=15

# Extract `<function_name> <complexity>` pairs from ruff's C901 output.
# Format: "<path>:<line>:<col>: C901 `name` is too complex (N > M)"
#
# ``ruff check`` exits non-zero when violations are found; that's
# expected here (we're enumerating them, not gating on them). The
# trailing ``|| true`` keeps the pipeline on the success path so a
# clean tree (no violations) still produces an empty baseline file
# rather than aborting with set -e.
{
    ruff check \
        --select C901 \
        --no-cache \
        --output-format concise \
        --config "lint.mccabe.max-complexity=${THRESHOLD}" \
        src 2>/dev/null \
        || true
} | sed -nE 's/.*C901 `([^`]+)` is too complex \(([0-9]+) > [0-9]+\).*/\1 \2/p' \
   | sort -u > .c901-baseline.txt

echo "Wrote .c901-baseline.txt ($(wc -l < .c901-baseline.txt) function(s) above C901=${THRESHOLD})"
