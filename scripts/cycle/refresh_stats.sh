#!/usr/bin/env bash
# Step "Refresh statistics dashboard and README snapshot" (id ``stats``)
# of .github/workflows/update-cycle.yml; the step starts this file with
# ``exec bash scripts/cycle/refresh_stats.sh`` and keeps its condition,
# ``continue-on-error``, its 4-minute limit and ``GEMINI_API_KEY``.
# Code and reasoning stood in the workflow until 2026-11-14 and were moved
# here word for word (docs/modularisierung.md, "teilen statt kürzen").
#
# Cadence split: the README STATS markers refresh on every
# ~30-min cycle tick (the snapshot covers the trailing 30 days,
# so a half-hour staleness window is the operator-visible
# budget). ``docs/statistik.md`` is the *yearly* dashboard —
# its values only meaningfully change once per day, so writing
# it 48× a day was pure churn (each tick refreshed the
# ``_Automatisch erzeugt am ..._`` timestamp at the top of the
# file and triggered a commit). The gate below pins the
# dashboard write to the first cycle tick after midnight
# Europe/Vienna: ``TZ=Europe/Vienna date +%H%M`` is DST-correct
# (the env var rebinds the active zone for that single ``date``
# invocation), and the ``-lt 30`` window absorbs up to ~29 min
# of IFTTT/dispatch trigger jitter on the 00:00 Vienna tick. If
# a tick is delayed beyond that window the dashboard misses
# one day; the next day's tick catches up. The README markers
# are unaffected.
#
# Shell options: the step names no shell, so GitHub ran the code with
# ``bash -e``. ``bash <file>`` does not set it, so ``set -e`` comes first.
set -e
vienna_hhmm=$(TZ=Europe/Vienna date +%H%M)
if [ "$vienna_hhmm" -lt 30 ]; then
  echo "Vienna-local $vienna_hhmm — refreshing dashboard + README markers."
  python scripts/generate_markdown_stats.py --fail-on-missing-review
else
  echo "Vienna-local $vienna_hhmm — README-only tick (dashboard skipped)."
  python scripts/generate_markdown_stats.py --skip-dashboard --fail-on-missing-review
fi
