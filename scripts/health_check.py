#!/usr/bin/env python3
"""Standalone health probe for the Wien-ÖPNV pipeline.

Runs OUTSIDE the feed build (own workflow: ``.github/workflows/health-check.yml``)
and is *allowed to fail* (exit 1 → red run) so that GitHub e-mails the
maintainer. It is fully independent of ``update-cycle.yml``: a red health check
never affects the feed build, and the feed build is never made to fail by this
script.

It runs two complementary kinds of checks and shows a third:

1. **Quellen-Erreichbarkeit (Live-Canary).** Runs the *real* production cache
   updaters (``update_wl_cache.py`` / ``update_oebb_cache.py`` /
   ``update_baustellen_cache.py``) as subprocesses and reads their exit code.
   This is the most faithful "is the source usable right now?" signal — it
   exercises the exact fetch + parse + relevance-filter + degradation-guard
   path the feed depends on:

     * ``0`` → source delivered usable data.
     * ``1`` → unreachable / empty / malformed / degraded.
     * ``2`` → (Baustellen only) live WFS failed in a checkout without a
       cache, which fell back to bundled demo data — i.e. the live source is
       effectively down. Treated as a failure here so this otherwise-silent
       fallback gets surfaced. With a cache in place a failed WFS is ``1``.
     * ``3`` → (WL / Baustellen) one part of the source (a WL list, a WFS
       layer) gave no usable answer; the cache was refreshed with that part
       from its last good answer. Never seen here in practice: the health
       check runs without ``RAW_CAPTURE``, so the updater has no last good
       answer to read and reports ``1`` instead.

   The updaters write into the ephemeral runner checkout's ``cache/`` dir;
   nothing is committed (the workflow has ``contents: read``).

   Since 2026-10-28 the probe alone no longer turns a source red: red is a
   source the Update runs got no usable answer from for three hours, as
   recorded by the Feed-Wächter (:func:`assess_source`); a failed probe
   inside that window is a note. The probe decides alone only when that
   record cannot be trusted.

2. **Aktualität der Outputs (Frische).** Reads the committed artefacts and
   verifies they are still being refreshed. A dead source alone does NOT trip
   the freshness check (the build happily reuses the last cache — exactly why
   check #1 exists alongside it); a stalled *workflow* does.

     * ``docs/feed.xml`` ``<lastBuildDate>`` — proves the ~30-min update cycle
       still produces a feed.
     * **Takt** — IFTTT still starts the Update cycle (``repository_dispatch``
       within 40 min); while it is silent the Takt-Fallback carries the feed
       (note), and a feed without a new build for 80 min then is red
       (operator decision 2026-11-04; see :func:`check_takt`).
     * ``data/stations_last_run.json`` — proves the weekly station-directory
       refresh still runs, every sub-script exited 0, and validation found no
       security issues.
     * the ``.cache.json`` sidecars of the station sources under ``data/`` —
       prove each source still answers; the station run falls back to its
       last good copy, so a dead source would otherwise stay green
       (:func:`check_station_sources`).

Stammstrecke/VOR is deliberately NOT probed live: the VAO ReST API is capped at
100 requests/day and a recurring health probe would burn that contractual
budget. Instead the monitor records every poll in
``cache/stammstrecke/poll_status.json``, and ``Stammstrecke-Abruf`` turns red
when no poll has worked for three hours (operator decision 2026-10-10: red
only when the fetch is broken, never because no train runs).

3. **Feed-Wächter** (``data/feed_watch.json``, written by
   ``scripts/feed_watch.py`` at the end of every Update run): the rules the
   German feed broke in the last run. A finding is a note, never red; a
   watcher that could not check is red (see :func:`check_feed_watch`).

   **Übersetzung** (``data/translation_status.json``, written by every build
   that needs the translation model): a model that did not load is a note
   for one build and red from the second in a row (operator decision
   2026-11-01; see :func:`check_translation`).

   **Nebenschritte** (``data/side_steps/``, written by
   ``scripts/record_side_steps.py``): statistics, line check, Feed-Wächter
   and HAFAS station lines run with ``continue-on-error``; the daily
   KI-Wächter (``ki-waechter.yml``) records its run the same way. Red while
   the last run of one of them failed (operator decision 2026-10-28: "ein
   Fehler sollte auffallen"; see :func:`check_side_steps`). A KI-Wächter
   that got no answer only because Gemini was overloaded is a note until
   30 h after its last clean audit (operator decision 2026-11-04).

4. **Zugangsdaten (Secrets).** Every repository secret a workflow passes on is
   listed in :data:`WORKFLOW_SECRETS`. A secret the feed pipeline needs that
   arrives empty turns the run red and is named; its value is never read
   beyond "empty or not" and never printed (see :func:`check_secrets`).

Thresholds are env-tunable (see the constants below); the defaults match the
two workflows' cadences.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# Only the repository root goes on the path: every project module is
# imported once, as ``src.…`` (docs/development.md, "Skripte im Überblick").
# Using the capped file helpers (instead of ``Path.read_text`` /
# ``json.loads``) is mandatory: the repo-wide audit-walker tests reject bare
# reads/parses in src/ and scripts/.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The checks live in ``src/health_check/`` since 2026-11-02 (700-line rule).
# ``main`` looks every check up by its name in this module, so a test that
# replaces ``check_*`` or ``assess_source`` here still reaches ``main``.
from src.health_check.common import _VIENNA as _VIENNA  # noqa: E402
from src.health_check.common import FEED_MAX_AGE_H as FEED_MAX_AGE_H  # noqa: E402
from src.health_check.common import LIVE_LAG_MAX_H as LIVE_LAG_MAX_H  # noqa: E402
from src.health_check.common import PAGES_BASE_URL as PAGES_BASE_URL  # noqa: E402
from src.health_check.common import STATIONS_MAX_AGE_H as STATIONS_MAX_AGE_H  # noqa: E402
from src.health_check.common import Check as Check  # noqa: E402
from src.health_check.common import _clean_line as _clean_line  # noqa: E402
from src.health_check.common import _env_float as _env_float  # noqa: E402
from src.health_check.common import _fmt_age as _fmt_age  # noqa: E402
from src.health_check.common import _mark as _mark  # noqa: E402
from src.health_check.common import _pages_base_url as _pages_base_url  # noqa: E402
from src.health_check.github_runs import GITHUB_API_MAX_BYTES as GITHUB_API_MAX_BYTES  # noqa: E402
from src.health_check.github_runs import PAGES_MAX_PAGES as PAGES_MAX_PAGES  # noqa: E402
from src.health_check.github_runs import PAGES_RUNS_PER_PAGE as PAGES_RUNS_PER_PAGE  # noqa: E402
from src.health_check.github_runs import PAGES_WORKFLOW_PATH as PAGES_WORKFLOW_PATH  # noqa: E402
from src.health_check.github_runs import TOOLING_RUNS_PER_PAGE as TOOLING_RUNS_PER_PAGE  # noqa: E402
from src.health_check.github_runs import TOOLING_WORKFLOWS as TOOLING_WORKFLOWS  # noqa: E402
from src.health_check.github_runs import _github_api_json as _github_api_json  # noqa: E402
from src.health_check.github_runs import check_pages_build as check_pages_build  # noqa: E402
from src.health_check.github_runs import check_tooling_runs as check_tooling_runs  # noqa: E402
from src.health_check.outputs import PUBLISHED_FEEDS as PUBLISHED_FEEDS  # noqa: E402
from src.health_check.outputs import SIDE_STEP_RECORDS as SIDE_STEP_RECORDS  # noqa: E402
from src.health_check.outputs import STATION_SOURCES as STATION_SOURCES  # noqa: E402
from src.health_check.outputs import _fmt_moment as _fmt_moment  # noqa: E402
from src.health_check.outputs import check_feed_freshness as check_feed_freshness  # noqa: E402
from src.health_check.outputs import check_published_feeds as check_published_feeds  # noqa: E402
from src.health_check.outputs import check_side_steps as check_side_steps  # noqa: E402
from src.health_check.outputs import check_station_sources as check_station_sources  # noqa: E402
from src.health_check.outputs import check_stations as check_stations  # noqa: E402
from src.health_check.outputs import check_translation as check_translation  # noqa: E402
from src.health_check.sources import SOURCE_STALE_H as SOURCE_STALE_H  # noqa: E402
from src.health_check.sources import UPDATERS as UPDATERS  # noqa: E402
from src.health_check.sources import assess_source as assess_source  # noqa: E402
from src.health_check.sources import check_feed_watch as check_feed_watch  # noqa: E402
from src.health_check.sources import check_source as check_source  # noqa: E402
from src.health_check.workflow_secrets import WORKFLOW_SECRETS as WORKFLOW_SECRETS  # noqa: E402
from src.health_check.workflow_secrets import WorkflowSecret as WorkflowSecret  # noqa: E402
from src.health_check.takt import check_takt as check_takt  # noqa: E402
from src.health_check.workflow_secrets import check_secrets as check_secrets  # noqa: E402


def check_stammstrecke_abruf(now: datetime) -> Check:
    """Red only when the Stammstrecke monitor's fetch does not work.

    Operator decision 2026-10-10 (Michael): "Der Check soll nur auf rot, wenn
    der Abruf nicht funktioniert. Wenn die Technik funktioniert gehört er auch
    auf grün. Wenn wirklich keine Züge fahren, gehört dies auf der Homepage
    gemeldet, aber der Check bleibt grün."

    The monitor records every poll in ``cache/stammstrecke/poll_status.json``.
    Broken = no working poll for :data:`POLL_STALE_HOURS` (a failed request,
    an unreadable or incomplete answer, a skipped poll, or a monitor that no
    longer runs) or no readable status at all. A valid answer without a single
    Stammstrecke train is a working fetch and stays green; the website names
    the train-less corridor instead. Before this check a completely dead
    monitor turned nothing red, because the direction check below never fails
    (#1939) and a corridor without ledger rows looks exactly like a night.
    The rule lives in :mod:`src.utils.stammstrecke_poll`, shared with the
    website summary, so the check and the banner cannot disagree.
    """
    name = "Stammstrecke-Abruf"
    try:
        from src.utils.stammstrecke_poll import (
            POLL_STALE_HOURS,
            assess_poll,
            load_poll_status,
        )
    except Exception as exc:  # pragma: no cover - import-environment guard
        return Check(
            name,
            ok=False,
            summary="FEHLER — Abruf-Prüfung nicht lauffähig",
            detail=_clean_line(str(exc)),
        )
    stale_h = _env_float("HEALTH_STAMMSTRECKE_POLL_STALE_HOURS", POLL_STALE_HOURS)
    verdict = assess_poll(load_poll_status(), now=now, stale_hours=stale_h)
    return Check(name, ok=verdict.working, summary=verdict.summary, detail=verdict.detail)


def check_stammstrecke_directions(now: datetime) -> Check:
    """Note when one Stammstrecke direction has gone silent while the other reports.

    Audit B.1/B.2: the northbound direction stopped producing delay rows on
    2026-08-14 and no check noticed for 30 days. The feed-freshness probe above
    cannot catch this — the feed keeps building every 30 min from the surviving
    direction — and the live-canary probes only cover the three free providers.
    Meanwhile README and dashboard kept publishing a half-corridor sample as a
    whole-corridor figure.

    The rule itself lives in :func:`src.utils.stats.find_silent_directions`, so
    this check and the monitor script (which logs the same condition per tick)
    cannot drift apart. It compares the directions against each other rather
    than against an absolute age: the corridor legitimately goes quiet at night
    (measured maxima 7.8 h / 11.5 h between rows during healthy operation), so
    a plain "last row older than N hours" rule either false-alarms or has to be
    set so high it stops being useful. See that module for the measurements.

    A silent direction is a note, not a failure (operator decision 2026-10-03):
    Praterstern has been silent since 2026-08-14 because of a closure that
    runs until the end of October 2027, and the check turned every run red from
    2026-09-13 on, so the failure e-mail every 6 h no longer told a real outage
    apart from the known closure. The note stays in the log, the job summary
    and as a ``::warning`` annotation. Only a check that cannot run at all
    still fails.
    """
    name = "Stammstrecke-Richtungen"
    try:
        from src.utils.stats import (
            DIRECTION_SILENCE_WINDOW_HOURS,
            find_silent_directions,
            summarise_direction_activity,
        )
        from src.stammstrecke.hbf_board import DIRECTION_LABELS
    except Exception as exc:  # pragma: no cover - import-environment guard
        return Check(
            name,
            ok=False,
            summary="FEHLER — Richtungs-Prüfung nicht lauffähig",
            detail=_clean_line(str(exc)),
        )

    window_h = _env_float(
        "HEALTH_STAMMSTRECKE_WINDOW_HOURS", DIRECTION_SILENCE_WINDOW_HOURS
    )
    window = timedelta(hours=window_h)

    activity = summarise_direction_activity(
        directions=DIRECTION_LABELS, now=now, window=window
    )
    breakdown = ", ".join(f"{a.direction}={a.rows}" for a in activity)
    silent = find_silent_directions(
        directions=DIRECTION_LABELS, now=now, window=window
    )

    if silent:
        return Check(
            name,
            ok=True,
            note=True,
            summary=(
                f"HINWEIS — Richtung {', '.join(silent)} ohne Messwerte in den "
                f"letzten {_fmt_age(window_h * 3600)}, Gegenrichtung liefert"
            ),
            detail=(
                f"Zeilen im Fenster: {breakdown}. Veröffentlichte "
                "Korridor-Kennzahlen decken derzeit nur die aktive Richtung ab."
            ),
        )

    if all(a.rows == 0 for a in activity):
        # Both directions quiet: night-time, a real stop of service, or a
        # broken fetch — the last one is ``Stammstrecke-Abruf``'s to report
        # (red), the others are not a fault. Not a direction fault either —
        # do not double-alarm.
        return Check(
            name,
            ok=True,
            summary=(
                f"OK — beide Richtungen im {_fmt_age(window_h * 3600)}-Fenster "
                "ruhig (kein Richtungs-Ausfall)"
            ),
        )

    return Check(
        name,
        ok=True,
        summary=f"OK — beide Richtungen liefern ({breakdown})",
    )


def _render_plain(
    now: datetime, sources: list[Check], outputs: list[Check], config: list[Check]
) -> str:
    line = "=" * 64
    when = now.strftime("%Y-%m-%d %H:%M UTC")
    if _VIENNA is not None:
        when += now.astimezone(_VIENNA).strftime("  (%H:%M %Z)")
    rows: list[str] = [line, "  Wien-ÖPNV Health-Check", f"  {when}", line]

    def block(title: str, checks: list[Check]) -> None:
        rows.append(title)
        width = max(len(c.name) for c in checks)
        for c in checks:
            mark = _mark(c)
            rows.append(f"  {mark} {c.name.ljust(width)}  {c.summary}")
            if c.detail:
                rows.append(f"      └─ {c.detail}")
        rows.append("")

    block("QUELLEN (Live-Abruf)", sources)
    block("AKTUALITÄT (committete Outputs)", outputs)
    block("ZUGANGSDATEN (Secrets)", config)

    failed = [c for c in sources + outputs + config if not c.ok]
    rows.append(line)
    if failed:
        names = ", ".join(c.name for c in failed)
        rows.append(f"ERGEBNIS: {len(failed)} Problem(e) ❌  →  {names}")
    else:
        rows.append("ERGEBNIS: Alles gesund ✅")
    rows.append(line)
    return "\n".join(rows)


def _render_markdown(
    now: datetime, sources: list[Check], outputs: list[Check], config: list[Check]
) -> str:
    failed = [c for c in sources + outputs + config if not c.ok]
    head = "❌ Probleme gefunden" if failed else "✅ Alles gesund"
    lines = [
        f"## Wien-ÖPNV Health-Check — {head}",
        f"_{now.strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "| Status | Prüfung | Ergebnis |",
        "| :----: | ------- | -------- |",
    ]
    for c in sources + outputs + config:
        mark = _mark(c)
        cell = c.summary + (f"<br>↳ `{c.detail}`" if c.detail else "")
        lines.append(f"| {mark} | **{c.name}** | {cell} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    now = datetime.now(UTC)

    sources = [assess_source(check_source(name, script), script, now) for name, script in UPDATERS]
    outputs = [
        check_feed_freshness(now),
        check_takt(now),
        check_published_feeds(now),
        check_pages_build(now),
        check_stations(now),
        check_station_sources(now),
        check_stammstrecke_abruf(now),
        check_stammstrecke_directions(now),
        check_feed_watch(now),
        check_translation(now),
        check_side_steps(now),
        check_tooling_runs(now),
    ]
    config = [check_secrets()]
    all_checks = sources + outputs + config
    failed = [c for c in all_checks if not c.ok]

    print(_render_plain(now, sources, outputs, config))

    # GitHub Actions annotations — make each failure jump out in the run UI
    # (and the failure e-mail's linked summary).
    for c in failed:
        msg = c.summary + (f" — {c.detail}" if c.detail else "")
        print(f"::error title=Health: {c.name}::{msg}")
    for c in all_checks:
        if c.ok and c.note:
            msg = c.summary + (f" — {c.detail}" if c.detail else "")
            print(f"::warning title=Health: {c.name}::{msg}")

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(_render_markdown(now, sources, outputs, config))
        except OSError:
            pass  # summary is best-effort; never let it mask the real result

    # Non-zero exit ⇒ red run ⇒ GitHub failure e-mail.
    return 1 if failed else 0


# Names other files and tests read through this script; the code lives in
# src/ (700-line limit, 2026-11-02). Listing them marks the imports as
# deliberate re-exports.
__all__ = [
    "REPO_ROOT",
    "check_stammstrecke_abruf",
    "check_stammstrecke_directions",
    "_render_plain",
    "_render_markdown",
    "main",
    "_VIENNA",
    "Check",
    "_clean_line",
    "_env_float",
    "_fmt_age",
    "_mark",
    "check_pages_build",
    "check_tooling_runs",
    "check_takt",
    "check_feed_freshness",
    "check_published_feeds",
    "check_side_steps",
    "check_station_sources",
    "check_stations",
    "check_translation",
    "UPDATERS",
    "assess_source",
    "check_feed_watch",
    "check_source",
    "check_secrets",
    "FEED_MAX_AGE_H",
    "LIVE_LAG_MAX_H",
    "PAGES_BASE_URL",
    "STATIONS_MAX_AGE_H",
    "_pages_base_url",
    "GITHUB_API_MAX_BYTES",
    "PAGES_MAX_PAGES",
    "PAGES_RUNS_PER_PAGE",
    "PAGES_WORKFLOW_PATH",
    "TOOLING_RUNS_PER_PAGE",
    "TOOLING_WORKFLOWS",
    "_github_api_json",
    "PUBLISHED_FEEDS",
    "SIDE_STEP_RECORDS",
    "STATION_SOURCES",
    "_fmt_moment",
    "SOURCE_STALE_H",
    "WORKFLOW_SECRETS",
    "WorkflowSecret",
]


if __name__ == "__main__":
    raise SystemExit(main())
