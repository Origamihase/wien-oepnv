#!/usr/bin/env bash
# Step "Refresh free-API caches in parallel" (id ``fetch``) of
# .github/workflows/update-cycle.yml; the step starts this file with
# ``exec bash scripts/cycle/fetch_free_apis.sh`` and keeps its condition,
# its 3-minute limit and ``RAW_CAPTURE``.
# Code and reasoning stood in the workflow until 2026-11-14 and were moved
# here word for word (docs/modularisierung.md, "teilen statt kürzen").
#
# Parallel free-API fetchers (replaces three sequential
# ``continue-on-error: true`` steps). Wiener Linien, ÖBB and the
# Stadt-Wien-OGD Baustellen API are independent upstreams — no
# shared rate limit, no shared connection pool — and each script
# writes to its own ``cache/<provider>_<hash>/events.json`` so
# concurrent execution is race-free. Wall-clock ~30 s vs ~90 s
# sequential; preserves the same best-effort semantics (a failed
# fetcher leaves the last committed cache in place and the
# downstream ``feed build`` reads what is on disk). Per-fetcher
# exit codes surface as ``::warning::`` annotations so a silent
# upstream outage stays operator-visible — the previous
# ``continue-on-error: true`` raised an orange badge per failed
# step; the warning emits the same operator-visible signal here.
#
# Shell options: the step names no shell, so GitHub ran the code with
# ``bash -e``; the code's own first line, ``set +e``, switched ``-e`` off.
# ``bash <file>`` starts without it, so no option line is needed here.
set +e

log_dir="$RUNNER_TEMP/fetcher-logs"
mkdir -p "$log_dir"

# Per-process Slowloris-defence wrapper. ``timeout`` (GNU
# coreutils, ships on ubuntu-latest) sends SIGTERM after 90s
# and follows up with SIGKILL 10s later via ``--kill-after``
# so a fetcher stuck in an uninterruptible C-extension call
# (e.g. a hung HTTPS socket below urllib3's Python-layer
# read deadline) cannot survive the SIGTERM-ignore path.
# Exit code 124 = SIGTERM-on-timeout, 137 = SIGKILL — the
# report() helper prints both via the existing ``(exit ${rc})``
# annotation so operators see ``::warning::`` lines for the
# killed fetcher. 90s budget covers the WORST documented
# legitimate run (WL: 2 endpoints × ~25s timeout × app-retry
# ≈ 60-80s under heavy upstream retry-load); healthy fetchers
# finish in <10s.
timeout --kill-after=10 90 python scripts/update_wl_cache.py         > "$log_dir/wl.log"         2>&1 &
pid_wl=$!
timeout --kill-after=10 90 python scripts/update_oebb_cache.py       > "$log_dir/oebb.log"       2>&1 &
pid_oebb=$!
timeout --kill-after=10 90 python scripts/update_baustellen_cache.py > "$log_dir/baustellen.log" 2>&1 &
pid_baustellen=$!

wait "$pid_wl";         rc_wl=$?
wait "$pid_oebb";       rc_oebb=$?
wait "$pid_baustellen"; rc_baustellen=$?

# Exit codes for the Feed-Wächter (rule "Quelle ohne frische
# Antwort", docs/reference/feed_watch.md): 0 = fresh answer,
# anything else = the feed keeps the source's last good answer.
echo "exit_codes=wl=${rc_wl} oebb=${rc_oebb} baustellen=${rc_baustellen}" >> "$GITHUB_OUTPUT"

report() {
  local name=$1
  local rc=$2
  echo "::group::${name} fetcher (exit ${rc})"
  cat "$log_dir/${name}.log"
  echo "::endgroup::"
  if [ "$rc" -eq 3 ]; then
    echo "::warning title=${name} cache fetcher::part of the source gave no usable answer (exit 3) — cache refreshed, that part taken from its last good answer in data/raw/."
  elif [ "$rc" -ne 0 ]; then
    echo "::warning title=${name} cache fetcher::non-zero exit (${rc}) — last committed cache will be used by feed build."
  fi
}

report wl         "$rc_wl"
report oebb       "$rc_oebb"
report baustellen "$rc_baustellen"

# Best-effort: never fail the workflow on individual fetcher
# errors. Mirrors the previous per-step ``continue-on-error: true``.
exit 0
