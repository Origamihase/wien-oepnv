#!/usr/bin/env bash
# Step "Backup-cron freshness gate" (id ``freshness``) of
# .github/workflows/update-cycle.yml; the step starts this file with
# ``exec bash scripts/cycle/freshness_gate.sh``.
# Code and reasoning stood in the workflow until 2026-11-14 and were moved
# here word for word; only "above" in the reasoning now reads "in
# update-cycle.yml" (docs/modularisierung.md, "teilen statt kürzen").
#
# Backup-cron freshness gate. The hourly ``schedule`` tick is a
# safety net (see "Trigger model" in update-cycle.yml), NOT a second cadence: when
# IFTTT is healthy a cycle tick has already run within the last
# ~30 min, so a backup tick would only burn a redundant VAO request
# (the daily budget is metered) plus free-API fetches. This gate
# makes a ``schedule`` run SKIP the fetch/build/publish work when a
# cycle tick committed inside the freshness window, so the backup
# cron costs ~zero API calls on healthy days and does real work only
# when IFTTT has genuinely gone silent. IFTTT (``repository_dispatch``)
# and operator (``workflow_dispatch``) runs are NEVER gated.
#
# Signal: the commit time of ``data/vor_request_count.json`` /
# ``docs/feed.xml`` — the counter is rewritten on every VAO poll and
# the feed on every content change, so "most recent commit touching
# either" tracks the last real tick. The checkout is shallow, so the
# gate first deepens it by 50 commits (one backup tick per hour, well
# under a megabyte); if the newest commit touching either file is
# still the shallow boundary, its commit time says nothing about the
# file (a boundary commit "adds" every path), so the gate treats the
# age as unknown and runs. The 35-min window sits just above
# IFTTT's ~30-min cadence (drift headroom) so a slightly-late IFTTT
# tick is not misread as an outage; a true outage is caught by the
# next hourly tick. Unknown / parse-fail defaults to "run" (fail safe).
#
# Shell options: the step's ``shell: bash`` ran the code with ``-e`` and
# ``-o pipefail``. ``bash <file>`` sets neither, so ``set -e`` comes first;
# the code's own first line, ``set -uo pipefail``, adds the rest.
set -e
set -uo pipefail
if [ "${GITHUB_EVENT_NAME}" != "schedule" ]; then
  echo "Trigger '${GITHUB_EVENT_NAME}' is not the backup cron — running the full cycle."
  echo "should_run=true" >> "$GITHUB_OUTPUT"
  exit 0
fi
git fetch --no-tags --quiet --deepen=50 origin "${GITHUB_REF_NAME}" 2>/dev/null \
  || echo "Could not deepen the shallow checkout; deciding from the commits present."
last=0
last_commit=$(git log -1 --format=%H -- data/vor_request_count.json docs/feed.xml 2>/dev/null || true)
shallow_file=$(git rev-parse --git-path shallow)
if [ -n "${last_commit}" ] && ! { [ -f "${shallow_file}" ] && grep -qx "${last_commit}" "${shallow_file}"; }; then
  last=$(git log -1 --format=%ct "${last_commit}" 2>/dev/null || echo 0)
fi
last=${last:-0}
now=$(date +%s)
age=$(( now - last ))
window=2100  # 35 min, just above IFTTT's ~30-min cadence
if [ "${last}" -gt 0 ] && [ "${age}" -lt "${window}" ]; then
  echo "Backup cron: last cycle tick was ${age}s ago (< ${window}s) — IFTTT looks healthy; skipping this redundant tick (no API calls)."
  echo "should_run=false" >> "$GITHUB_OUTPUT"
else
  echo "Backup cron: last cycle tick was ${age}s ago (>= ${window}s or unknown) — IFTTT may be down; running the full cycle as a safety net."
  echo "should_run=true" >> "$GITHUB_OUTPUT"
fi
