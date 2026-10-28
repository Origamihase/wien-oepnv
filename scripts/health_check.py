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

2. **Aktualität der Outputs (Frische).** Reads the committed artefacts and
   verifies they are still being refreshed. A dead source alone does NOT trip
   the freshness check (the build happily reuses the last cache — exactly why
   check #1 exists alongside it); a stalled *workflow* does.

     * ``docs/feed.xml`` ``<lastBuildDate>`` — proves the ~30-min update cycle
       still produces a feed.
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

   **Nebenschritte** (``data/side_steps/``, written by
   ``scripts/record_side_steps.py``): statistics, line check, Feed-Wächter
   and HAFAS station lines run with ``continue-on-error``. Red while the last
   run of one of them failed (operator decision 2026-10-28: "ein Fehler
   sollte auffallen"; see :func:`check_side_steps`).

4. **Zugangsdaten (Secrets).** Every repository secret a workflow passes on is
   listed in :data:`WORKFLOW_SECRETS`. A secret the feed pipeline needs that
   arrives empty turns the run red and is named; its value is never read
   beyond "empty or not" and never printed (see :func:`check_secrets`).

Thresholds are env-tunable (see the constants below); the defaults match the
two workflows' cadences.
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
# Resolve the canonical size-/depth-capped file helpers regardless of how the
# script is launched. Both ``src`` (for ``from utils.…``) and the repo root go
# on the path, mirroring scripts/update_baustellen_cache.py. Using the capped
# helpers (instead of ``Path.read_text`` / ``json.loads``) is mandatory: the
# repo-wide audit-walker tests reject bare reads/parses in src/ and scripts/.
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.files import read_capped_json, read_capped_text  # noqa: E402

try:  # Vienna local time is a nice-to-have header detail; tzdata may be absent.
    _VIENNA: ZoneInfo | None = ZoneInfo("Europe/Vienna")
except Exception:  # pragma: no cover - tzdata missing
    _VIENNA = None


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# --- tunable thresholds (env-overridable) -----------------------------------
# Feed: update-cycle runs ~every 30 min (IFTTT) with an hourly cron floor, so a
# build older than 4 h means several missed cycles → something is wrong.
FEED_MAX_AGE_H = _env_float("HEALTH_FEED_MAX_AGE_HOURS", 4.0)
# Stations: update-stations runs weekly (Sun 01:00 UTC). 8 days = one full
# cycle + jitter; older means a weekly run was missed.
STATIONS_MAX_AGE_H = _env_float("HEALTH_STATIONS_MAX_AGE_HOURS", 192.0)
# Station sources: the weekly run downloads them and falls back to the copy
# under data/ when a download fails, so a dead source would only show in the
# log. 15 days = two weekly runs in a row without a good answer (one failed
# run is tolerated, the station data changes slowly).
STATION_SOURCE_MAX_AGE_H = _env_float("HEALTH_STATION_SOURCE_MAX_AGE_HOURS", 360.0)
# Per-source live fetch ceiling (the updaters retry internally; this is the
# hard wall so a hung source cannot stall the whole probe).
FETCH_TIMEOUT_S = int(_env_float("HEALTH_FETCH_TIMEOUT_SECONDS", 120))

# Live-canary: (display name, updater script). Order = report order.
UPDATERS: list[tuple[str, str]] = [
    ("Wiener Linien", "update_wl_cache.py"),
    ("ÖBB", "update_oebb_cache.py"),
    ("Baustellen (OGD)", "update_baustellen_cache.py"),
]

# Pull the item count out of either updater's success line:
#   "Updated ÖBB cache with 17 events."  /  "Cache mit 27 Einträgen aktualisiert."
_COUNT_RES = (
    re.compile(r"cache with (\d+) events", re.IGNORECASE),
    re.compile(r"Cache mit (\d+) Eintr", re.IGNORECASE),
)

# Noisy lines that must never be picked as the "reason" for a failure.
_NOISE_RE = re.compile(r"Duplicate station alias")
# Lines worth surfacing verbatim when a source fails.
_INTERESTING_RE = re.compile(
    r"\d{3}\s+(?:Client|Server)\s+Error|fehlgeschlagen|Network error|"
    r"Failed to fetch|Degrad|degrad|FALLBACK|Fallback|0 events|0 ÖPNV|"
    r"keeping existing|nicht aktualisiert|Unexpected|Forbidden|"
    r"Timeout|timed out|Unsichere|Ungültig",
    re.IGNORECASE,
)
# An HTTP status line is the most diagnostic reason ("…: 403 Client Error…"),
# so prefer it over a generic follow-up like "keeping existing cache".
_HTTP_ERR_RE = re.compile(
    r"\b[45]\d\d\b.*?(?:Error|Forbidden|Not Found|Unavailable|Bad Gateway|Timeout)",
    re.IGNORECASE,
)
# Strip the "2026-… LEVEL logger.name:" prefix so the message reads cleanly.
_LOG_PREFIX_RE = re.compile(
    r"^\S+\s+\S+\s+(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+\S+:\s*"
)


@dataclass
class Check:
    name: str
    ok: bool
    summary: str  # short one-liner shown after the name
    detail: str = ""  # optional extra line (e.g. the upstream error text)
    # A note is shown and annotated as a warning but never turns the run red.
    note: bool = False


def _mark(c: Check) -> str:
    if not c.ok:
        return "❌"
    return "ℹ️" if c.note else "✅"


def _extract_count(output: str) -> int | None:
    for pattern in _COUNT_RES:
        m = pattern.search(output)
        if m:
            return int(m.group(1))
    return None


def _clean_line(line: str) -> str:
    """Drop the log prefix and any flattened traceback tail.

    ``setup_script_logging``'s SafeFormatter collapses a multi-line record
    (e.g. an ``exc_info`` traceback) onto one physical line with *literal*
    ``\\n`` escapes; cut at the first such marker so the reason stays a single
    readable sentence instead of a wall of stack frames.
    """
    line = _LOG_PREFIX_RE.sub("", line)
    for marker in ("\\n", "Traceback", '  File "'):
        idx = line.find(marker)
        if idx != -1:
            line = line[:idx]
    return line.strip().rstrip(" ;:,")[:300]


def _extract_reason(output: str) -> str:
    """Return the most informative single line from a failed updater's log."""
    # Clean every candidate FIRST (strip prefix + flattened traceback) so the
    # HTTP-status preference below matches on a real status line — not on the
    # traceback tail of a generic "Network error …" record that happens to
    # carry the status further down the same flattened line.
    cleaned = [
        c
        for c in (_clean_line(ln) for ln in output.splitlines() if not _NOISE_RE.search(ln))
        if c
    ]
    if not cleaned:
        return "kein verwertbares Detail im Log"
    interesting = [ln for ln in cleaned if _INTERESTING_RE.search(ln)]
    http = [ln for ln in interesting if _HTTP_ERR_RE.search(ln)]
    # Prefer a concrete HTTP status, then the last interesting line, then the
    # last line of any kind.
    return (http or interesting or cleaned)[-1]


def _subprocess_env() -> dict[str, str]:
    """Env for the updater subprocesses.

    The updaters mix import styles (``from src.…`` needs the repo root on the
    path, ``from utils.…`` needs ``src/``), so expose both — mirrors what
    update-cycle.yml achieves via ``PYTHONPATH: src`` plus each script's own
    ``REPO_ROOT`` insertion.
    """
    env = dict(os.environ)
    extra = os.pathsep.join([str(REPO_ROOT), str(REPO_ROOT / "src")])
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{extra}{os.pathsep}{existing}" if existing else extra
    return env


def check_source(name: str, script: str) -> Check:
    """Run a production cache updater and translate its exit code to health."""
    script_path = REPO_ROOT / "scripts" / script
    try:
        proc = subprocess.run(  # nosec B603
            [sys.executable, str(script_path)],
            cwd=str(REPO_ROOT),
            env=_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=FETCH_TIMEOUT_S,
        )
        rc = proc.returncode
        output = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return Check(
            name,
            ok=False,
            summary=f"FEHLER — Zeitüberschreitung nach {FETCH_TIMEOUT_S}s "
            "(Quelle antwortet nicht)",
            detail=_extract_reason(f"{out}\n{err}"),
        )

    if rc == 0:
        count = _extract_count(output)
        suffix = f" — {count} Datensätze geholt" if count is not None else ""
        return Check(name, ok=True, summary=f"OK{suffix}")
    if rc == 2:  # Baustellen-specific: live WFS failed → bundled demo data.
        return Check(
            name,
            ok=False,
            summary="FEHLER — Live-Quelle nicht erreichbar, "
            "Cache nutzt FALLBACK-Demodaten",
            detail=_extract_reason(output),
        )
    if rc == 3:  # WL / Baustellen: one part of the source failed.
        return Check(
            name,
            ok=False,
            summary="FEHLER — Teil der Quelle ohne brauchbare Antwort, "
            "aus der letzten guten Antwort ergänzt",
            detail=_extract_reason(output),
        )
    return Check(
        name,
        ok=False,
        summary=f"FEHLER — Abruf fehlgeschlagen (exit {rc})",
        detail=_extract_reason(output),
    )


def _fmt_age(seconds: float) -> str:
    s = max(0, int(seconds))
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def check_feed_freshness(now: datetime) -> Check:
    name = "Feed-Build"
    path = REPO_ROOT / "docs" / "feed.xml"
    text = read_capped_text(path, label="feed.xml")
    if text is None:
        return Check(name, ok=False,
                     summary="FEHLER — feed.xml fehlt, ist zu groß oder nicht lesbar")
    m = re.search(r"<lastBuildDate>\s*(.*?)\s*</lastBuildDate>", text)
    if not m:
        return Check(name, ok=False,
                     summary="FEHLER — <lastBuildDate> fehlt in feed.xml")
    try:
        built = parsedate_to_datetime(m.group(1))
    except (TypeError, ValueError) as exc:
        return Check(name, ok=False,
                     summary="FEHLER — <lastBuildDate> nicht parsebar",
                     detail=f"{m.group(1)!r}: {exc}"[:200])
    if built.tzinfo is None:
        built = built.replace(tzinfo=UTC)
    age_s = (now - built).total_seconds()
    limit = f"Grenze {_fmt_age(FEED_MAX_AGE_H * 3600)}"
    if age_s > FEED_MAX_AGE_H * 3600:
        return Check(name, ok=False,
                     summary=f"FEHLER — seit {_fmt_age(age_s)} kein neuer Build "
                     f"({limit})",
                     detail="update-cycle.yml läuft nicht mehr oder bricht vor "
                     "dem Commit ab.")
    return Check(name, ok=True,
                 summary=f"OK — vor {_fmt_age(age_s)} gebaut ({limit})")


def check_stations(now: datetime) -> Check:
    name = "Stationsverzeichnis"
    path = REPO_ROOT / "data" / "stations_last_run.json"
    data = read_capped_json(path, label="stations_last_run")
    if not isinstance(data, dict):
        return Check(name, ok=False,
                     summary="FEHLER — stations_last_run.json fehlt, ist zu groß "
                     "oder kein gültiges JSON-Objekt")

    problems: list[str] = []

    ts_raw = data.get("timestamp")
    age_s: float | None = None
    if not ts_raw:
        problems.append("kein 'timestamp' im Lauf-Report")
    else:
        try:
            ran = datetime.fromisoformat(str(ts_raw))
            if ran.tzinfo is None:
                ran = ran.replace(tzinfo=UTC)
            age_s = (now - ran).total_seconds()
            if age_s > STATIONS_MAX_AGE_H * 3600:
                problems.append(
                    f"seit {_fmt_age(age_s)} keine Aktualisierung "
                    f"(Grenze {_fmt_age(STATIONS_MAX_AGE_H * 3600)})"
                )
        except ValueError:
            problems.append(f"timestamp nicht parsebar: {ts_raw!r}")

    for sub in data.get("sub_scripts", []) or []:
        if isinstance(sub, dict) and sub.get("exit_code") not in (0, None):
            problems.append(
                f"Teilschritt {sub.get('name', '?')} exit {sub.get('exit_code')}"
            )

    sec_issues = (data.get("validation") or {}).get("security_issues")
    if isinstance(sec_issues, int) and sec_issues > 0:
        problems.append(f"{sec_issues} Sicherheits-Problem(e) in der Validierung")

    if problems:
        return Check(name, ok=False,
                     summary=f"FEHLER — {problems[0]}",
                     detail="; ".join(problems[1:]))
    age_txt = f"vor {_fmt_age(age_s)} " if age_s is not None else ""
    return Check(name, ok=True,
                 summary=f"OK — {age_txt}aktualisiert, alle Teilschritte sauber")


# (display name, file under data/ whose ``.cache.json`` sidecar the weekly
# station run writes after every good answer: ``checked_at``, or
# ``fetched_at`` for a sidecar written before 2026-10-25).
STATION_SOURCES: list[tuple[str, str]] = [
    ("ÖBB Verkehrsstationen", "oebb-verkehrsstationen.xlsx"),
    ("WL-OGD Haltestellen", "wienerlinien-ogd-haltestellen.csv"),
    ("WL-OGD Haltepunkte", "wienerlinien-ogd-haltepunkte.csv"),
    ("WL-OGD Linien", "wienerlinien-ogd-linien.csv"),
    ("WL-OGD Fahrwegverläufe", "wienerlinien-ogd-fahrwegverlaeufe.csv"),
    ("WL-GTFS Haltestellen", "wienerlinien-gtfs-stops.csv"),
]


def check_station_sources(now: datetime) -> Check:
    """Red when a source of the weekly station run has stopped answering.

    The station run keeps the last good copy when a download fails, so the
    run stays green and ``stations_last_run.json`` looks healthy while the
    directory silently ages. Each good answer (200 or 304) stamps the
    file's sidecar; a stamp older than :data:`STATION_SOURCE_MAX_AGE_H`
    means the source failed in two weekly runs in a row.
    """
    name = "Stationsquellen"
    problems: list[str] = []
    for label, filename in STATION_SOURCES:
        sidecar = REPO_ROOT / "data" / f"{filename}.cache.json"
        data = read_capped_json(sidecar, label=f"{filename} sidecar")
        stamp = None
        if isinstance(data, dict):
            stamp = data.get("checked_at") or data.get("fetched_at")
        if not stamp:
            problems.append(f"{label}: kein Abrufzeitpunkt")
            continue
        try:
            checked = datetime.fromisoformat(str(stamp))
        except ValueError:
            problems.append(f"{label}: Abrufzeitpunkt nicht lesbar")
            continue
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=UTC)
        age_s = (now - checked).total_seconds()
        if age_s > STATION_SOURCE_MAX_AGE_H * 3600:
            problems.append(f"{label} seit {_fmt_age(age_s)} ohne gültige Antwort")
    if problems:
        return Check(name, ok=False,
                     summary=f"FEHLER — {problems[0]}",
                     detail="; ".join(problems[1:]))
    return Check(name, ok=True,
                 summary=f"OK — alle {len(STATION_SOURCES)} Quellen des Stationslaufs antworten")


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
        from scripts.update_stammstrecke_hbf import DIRECTION_LABELS
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


# The Feed-Wächter (scripts/feed_watch.py) runs at the end of every Update run.
# Its state is committed with the feed, so it is current whenever the feed is.
@dataclass(frozen=True)
class WorkflowSecret:
    """One repository secret that a workflow under ``.github/workflows`` uses.

    ``kind``:

    * ``required`` — a feed-pipeline step cannot do its job without it. The
      Health check gets it passed and turns red when it arrives empty.
    * ``optional`` — the code has a built-in default; empty is fine and only
      shown as a detail line.
    * ``tooling`` — used by a repository tool workflow, not by the feed
      pipeline. Not passed to the Health check, which has no use for it.
    """

    name: str
    kind: str
    purpose: str


# The complete list of repository secrets the workflows use. Operator decision
# 2026-10-16 (Michael): secrets that no longer reach their consumer must not go
# unnoticed, and the Health check is the place where that shows. Until then a
# missing secret fell back silently: the Google Places key had reached no
# workflow since 2026-05-11, and ``VOR_VERSION`` / ``VOR_BASE`` were passed on
# although no such secret existed. ``tests/test_workflow_secrets.py`` keeps
# this list, the workflows' ``secrets.*`` references and the env names the
# code reads in step, so the class cannot come back unnoticed.
WORKFLOW_SECRETS: tuple[WorkflowSecret, ...] = (
    WorkflowSecret("VOR_ACCESS_ID", "required",
                   "Stammstrecken-Monitor (VOR/VAO ReST API)"),
    WorkflowSecret("VOR_BASE_URL", "optional",
                   "VAO-Host, Standard in src/providers/vor.py"),
    WorkflowSecret("CLAUDE_CODE_OAUTH_TOKEN", "tooling",
                   "Claude-Workflows (Code-Review, @claude)"),
    WorkflowSecret("REPO_ADMIN_TOKEN", "tooling",
                   "SEO-Guard: Beschreibung und Homepage des Repositorys"),
)


def check_secrets() -> Check:
    """Red when a secret the feed pipeline needs arrives empty in this run.

    The Health-check workflow passes every ``required`` and ``optional``
    secret of :data:`WORKFLOW_SECRETS`. A secret that is not stored in the
    repository arrives as an empty string, exactly as it would in the Update
    run. Only emptiness is checked and only names are printed, never a value.
    Whether a stored key is still *accepted* shows in ``Stammstrecke-Abruf``:
    a rejected key makes every poll fail, and that check turns red after
    three hours.
    """
    name = "Secrets"
    checked = [s for s in WORKFLOW_SECRETS if s.kind in {"required", "optional"}]
    empty = [s for s in checked if not os.environ.get(s.name, "").strip()]
    missing = [s for s in empty if s.kind == "required"]
    defaulted = [s for s in empty if s.kind == "optional"]
    if missing:
        return Check(
            name,
            ok=False,
            summary="FEHLT — " + ", ".join(s.name for s in missing)
            + " leer oder nicht als Repository-Secret hinterlegt",
            detail="; ".join(f"{s.name}: {s.purpose}" for s in missing),
        )
    present = [s.name for s in checked if s not in empty]
    detail = "; ".join(
        f"{s.name} leer, es gilt der Standard aus dem Code ({s.purpose})" for s in defaulted
    )
    return Check(name, ok=True, summary="OK — " + ", ".join(present) + " gesetzt",
                 detail=detail)


FEED_WATCH_STATE = REPO_ROOT / "data" / "feed_watch.json"


def check_feed_watch(now: datetime) -> Check:
    """Show what the Feed-Wächter found in the last Update run.

    A finding is never red (operator decision 2026-10-10, Michael: red only
    when the fetch does not work). A rule the German feed breaks is content:
    the fetch, the build and the publish worked. The watcher reports it in the
    job log and as a GitHub issue; here it shows as a note (``ℹ️`` and a
    ``::warning``). A watcher that could not check (its ``error`` field) is
    red since 2026-10-28: a side step that cannot run must be noticed
    (:func:`check_side_steps`). A missing or stale state stays a note: a dead
    Update run already turns ``Feed-Build`` red, and a watcher step killed by
    its time limit shows under ``Nebenschritte``.
    """
    name = "Feed-Wächter"
    data = read_capped_json(FEED_WATCH_STATE, label="feed_watch")
    if not isinstance(data, dict):
        return Check(name, ok=True, note=True,
                     summary="HINWEIS — noch kein Stand des Feed-Wächters "
                     "(data/feed_watch.json fehlt oder ist unlesbar)")
    updated_raw = data.get("updated")
    try:
        updated = datetime.fromisoformat(str(updated_raw))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
    except ValueError:
        updated = None
    age = f"vor {_fmt_age((now - updated).total_seconds())}" if updated else "Zeitpunkt unbekannt"
    error = data.get("error")
    if error:
        return Check(name, ok=False,
                     summary=f"FEHLER — Wächter konnte nicht prüfen ({age})",
                     detail=str(error)[:200])
    if updated is None or (now - updated).total_seconds() > FEED_MAX_AGE_H * 3600:
        return Check(name, ok=True, note=True,
                     summary=f"HINWEIS — kein aktueller Stand des Feed-Wächters ({age})")
    findings = [f for f in data.get("findings") or [] if isinstance(f, dict)]
    if not findings:
        return Check(name, ok=True, summary=f"OK — alle Regeln halten (Stand {age})")
    titles: list[str] = []
    for finding in findings:
        title = str(finding.get("title") or finding.get("rule") or "?")
        if title not in titles:
            titles.append(title)
    first = findings[0]
    return Check(
        name,
        ok=True,
        note=True,
        summary=f"HINWEIS — {len(titles)} Regel(n) verletzt: {', '.join(titles)} (Stand {age})",
        detail=f"{first.get('subject', '')}: {first.get('text', '')}"[:200],
    )


# One record per workflow, written by ``scripts/record_side_steps.py`` at the
# end of the run (file name, workflow, oldest age before the record itself
# counts as missing: the workflow's own freshness limit).
SIDE_STEP_RECORDS: list[tuple[str, str, float]] = [
    ("update-cycle.json", "Update-Lauf", FEED_MAX_AGE_H),
    ("update-stations.json", "Stationslauf", STATIONS_MAX_AGE_H),
]
SIDE_STEP_DIR = REPO_ROOT / "data" / "side_steps"


def _vienna_hhmm(moment: datetime) -> str:
    local = moment.astimezone(_VIENNA) if _VIENNA is not None else moment
    return local.strftime("%d.%m. %H:%M")


def _parse_utc(raw: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def check_side_steps(now: datetime) -> Check:
    """Red while the last run of a side step failed (operator decision 2026-10-28).

    The side steps run with ``continue-on-error`` so they never block the
    publish: statistics, line check and Feed-Wächter in the Update run, the
    HAFAS station lines in the station run. Michael on 2026-10-28: "Ich
    tendiere zu ‚er soll rot werden', denn ein Fehler sollte auffallen." For
    side steps this replaces the earlier "red only when the fetch fails"
    (2026-10-10); a source outage keeps its own thresholds. A record older
    than its workflow's limit is red too: the recorder could not run, so a
    failure would go unseen again. A record that does not exist yet (before
    the first run with the recorder) is a note.
    """
    name = "Nebenschritte"
    problems: list[str] = []
    notes: list[str] = []
    counted = 0
    for file_name, workflow, max_age_h in SIDE_STEP_RECORDS:
        path = SIDE_STEP_DIR / file_name
        if not path.exists():
            notes.append(f"{workflow}: noch kein Stand ({path.name})")
            continue
        data = read_capped_json(path, label="side_steps")
        steps = data.get("steps") if isinstance(data, dict) else None
        if not isinstance(steps, dict):
            problems.append(f"{workflow}: {path.name} ist unlesbar")
            continue
        updated = _parse_utc(data.get("updated"))
        if updated is None or (now - updated).total_seconds() > max_age_h * 3600:
            age = f"seit {_fmt_age((now - updated).total_seconds())}" if updated else "nie"
            problems.append(f"{workflow}: Nebenschritte {age} nicht erfasst")
        for label, entry in sorted(steps.items()):
            if not isinstance(entry, dict):
                continue
            counted += 1
            if entry.get("outcome") != "failure":
                continue
            at = _parse_utc(entry.get("at"))
            last_ok = _parse_utc(entry.get("last_success"))
            text = f"{label} ({workflow}) gescheitert"
            if at is not None:
                text += f" am {_vienna_hhmm(at)}"
            text += (
                f", zuletzt sauber am {_vienna_hhmm(last_ok)}" if last_ok else ", noch nie sauber"
            )
            if entry.get("run"):
                text += f" — {entry['run']}"
            problems.append(text)
    if problems:
        return Check(name, ok=False, summary=f"FEHLER — {problems[0]}",
                     detail="; ".join(problems[1:] + notes))
    if notes and not counted:
        return Check(name, ok=True, note=True,
                     summary="HINWEIS — noch kein Stand der Nebenschritte",
                     detail="; ".join(notes))
    summary = f"OK — alle {counted} Nebenschritte liefen zuletzt sauber"
    if notes:
        return Check(name, ok=True, note=True, summary=summary, detail="; ".join(notes))
    return Check(name, ok=True, summary=summary)


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

    sources = [check_source(name, script) for name, script in UPDATERS]
    outputs = [
        check_feed_freshness(now),
        check_stations(now),
        check_station_sources(now),
        check_stammstrecke_abruf(now),
        check_stammstrecke_directions(now),
        check_feed_watch(now),
        check_side_steps(now),
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


if __name__ == "__main__":
    raise SystemExit(main())
