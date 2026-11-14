#!/usr/bin/env bash
# Step "Ensure torch CPU wheel is present (self-healing)" of
# .github/workflows/update-cycle.yml; the step starts this file with
# ``exec bash scripts/cycle/ensure_torch.sh``.
# Code and reasoning stood in the workflow until 2026-11-14 and were moved
# here word for word; only "above" in the reasoning now reads "in
# update-cycle.yml" (docs/modularisierung.md, "teilen statt kürzen").
#
# Self-healing torch backstop. The cache-key fix in update-cycle.yml closes the
# drift channel that left ``torch`` missing from cached venvs
# after PR #1600, but it only takes effect when the workflow
# file itself changes. This idempotent step is the second layer:
# ``pip install torch …`` is a no-op (~1-2 s) when torch is
# already installed at the requested version, so it costs almost
# nothing on the healthy path. If a cached venv ever ends up
# missing torch again (silent cache restoration of an older
# pre-torch venv from a branch with a hash-collision, a future
# refactor that drops torch from the install step, an
# actions/cache restore-keys widening, …), this step heals it
# before the feed-build step runs and writes ``[Partially
# translated]`` markers into ``docs/feed.en.xml``. The ``--index-url``
# pin keeps the CPU-only wheel; without it pip would resolve
# against PyPI default and pull the multi-GiB CUDA build.
#
# Shell options: the step's ``shell: bash`` ran the code with ``-e`` and
# ``-o pipefail``. ``bash <file>`` sets neither, so ``set -e`` comes first;
# the code's own first line, ``set -uo pipefail``, adds the rest.
set -e
set -uo pipefail
# Healthy path: torch is already in the cached venv, so the
# import check short-circuits with ZERO network I/O. Previously
# this step ran an unconditional ``pip install`` on EVERY tick,
# so a transient ``download.pytorch.org`` outage red-badged the
# project's most important workflow even though torch was fine.
if python -c "import torch" 2>/dev/null; then
  echo "torch already present — skipping reinstall (no network)."
  exit 0
fi
# Genuinely missing: install the CPU-only wheel with a short
# retry so an index blip doesn't fail the tick. The ``--index-url``
# pin keeps the CPU wheel (else pip pulls the multi-GiB CUDA build).
for attempt in 1 2 3; do
  if pip install torch --index-url https://download.pytorch.org/whl/cpu; then
    echo "torch installed on attempt ${attempt}/3."
    exit 0
  fi
  echo "::warning title=torch-install::install attempt ${attempt}/3 failed; retrying."
  sleep "$((attempt * 3))"
done
# Soft degradation, never a hard fail: without torch the EN feed
# shows "[Partially translated]" for THIS tick only; the next
# tick self-heals. Far better than aborting the whole cycle.
echo "::warning title=torch-install::torch still missing after 3 attempts; EN feed may degrade to [Partially translated] this tick (next tick self-heals)."
exit 0
