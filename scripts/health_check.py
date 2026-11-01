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
   and HAFAS station lines run with ``continue-on-error``; the daily
   KI-Wächter (``ki-waechter.yml``) records its run the same way. Red while
   the last run of one of them failed (operator decision 2026-10-28: "ein
   Fehler sollte auffallen"; see :func:`check_side_steps`).

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
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
# Only the repository root goes on the path: every project module is
# imported once, as ``src.…`` (docs/development.md, "Skripte im Überblick").
# Using the capped file helpers (instead of ``Path.read_text`` /
# ``json.loads``) is mandatory: the repo-wide audit-walker tests reject bare
# reads/parses in src/ and scripts/.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config.defaults import DEFAULT_PAGES_BASE_URL  # noqa: E402
from src.utils.files import loads_finite, read_capped_json, read_capped_text  # noqa: E402
from src.utils.http import fetch_content_safe, session_with_retries  # noqa: E402
from src.utils.logging import sanitize_log_arg  # noqa: E402

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
# Published copy: GitHub Pages serves ``docs/`` from ``main``; a deploy follows
# a commit within minutes. A live feed whose ``<lastBuildDate>`` is this far
# behind the committed one means Pages has stopped deploying (the Update runs
# stay green, so nothing else would notice, see :func:`check_published_feeds`).
LIVE_LAG_MAX_H = _env_float("HEALTH_LIVE_LAG_MAX_HOURS", 4.0)


def _pages_base_url() -> str:
    """Where GitHub Pages serves ``docs/``: ``PAGES_BASE_URL``, else derived
    from ``GITHUB_REPOSITORY`` (``owner/repo`` → ``https://owner.github.io/repo``,
    as build-feed.yml does, so a fork checks its own site), else the default."""
    explicit = os.getenv("PAGES_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    owner, _, repo = os.getenv("GITHUB_REPOSITORY", "").strip().partition("/")
    if owner and repo:
        return f"https://{owner.lower()}.github.io/{repo}"
    return str(DEFAULT_PAGES_BASE_URL)


PAGES_BASE_URL = _pages_base_url()
LIVE_FETCH_TIMEOUT_S = _env_float("HEALTH_LIVE_FETCH_TIMEOUT_SECONDS", 20.0)
LIVE_FEED_MAX_BYTES = 2 * 1024 * 1024
LIVE_FEED_CONTENT_TYPES = ("application/xml", "application/rss+xml", "text/xml")
# Stations: update-stations runs weekly (Sun 01:00 UTC). 8 days = one full
# cycle + jitter; older means a weekly run was missed.
STATIONS_MAX_AGE_H = _env_float("HEALTH_STATIONS_MAX_AGE_HOURS", 192.0)
# The KI-Wächter runs once a day from 9:00 Vienna time (again two hours
# after a failure); 36 h leaves room for a late first Update cycle of the
# next day.
KI_WAECHTER_MAX_AGE_H = _env_float("HEALTH_KI_WAECHTER_MAX_AGE_HOURS", 36.0)
# SEO run (seo-guard.yml): daily cron, which GitHub delivers hours late (see
# docs/development.md, "Geplante Läufe"), plus every push touching docs/.
# 48 h = two days without any run before its side-step record counts as stale.
SEO_MAX_AGE_H = _env_float("HEALTH_SEO_MAX_AGE_HOURS", 48.0)
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
# Key of each updater in the Feed-Wächter's record of the Update runs'
# fetch exit codes (``data/feed_watch.json`` → ``fetch``).
FETCH_KEYS: dict[str, str] = {
    "update_wl_cache.py": "wl",
    "update_oebb_cache.py": "oebb",
    "update_baustellen_cache.py": "baustellen",
}
# A source is red once the Update runs have had no usable answer from it for
# this long: the same three hours as the Stammstrecke poll (operator decision
# 2026-10-28, see :func:`assess_source`).
SOURCE_STALE_H = _env_float("HEALTH_SOURCE_STALE_HOURS", 3.0)

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

    Only the repository root goes on ``PYTHONPATH``: the updaters import
    ``src.…`` (docs/development.md, "Skripte im Überblick"), and ``src/`` on
    the path would load their modules a second time as ``utils.…``.
    """
    env = dict(os.environ)
    extra = str(REPO_ROOT)
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


def _fetch_record(key: str | None, now: datetime) -> datetime | None:
    """Last usable answer the Update runs got from a source, or ``None``.

    Read from the Feed-Wächter's record of the fetch exit codes (exit 0 =
    usable answer). ``None`` when the record cannot be trusted: no key, no
    readable state, a watcher that could not check, or a state older than
    :data:`FEED_MAX_AGE_H` (then ``Feed-Build`` or ``Feed-Wächter`` is red on
    its own). Before the first usable answer the watcher saw, the time of the
    first failure stands in, as in the watcher's own rule.
    """
    if key is None:
        return None
    data = read_capped_json(FEED_WATCH_STATE, label="feed_watch")
    if not isinstance(data, dict) or data.get("error"):
        return None
    updated = _parse_utc(data.get("updated"))
    if updated is None or (now - updated).total_seconds() > FEED_MAX_AGE_H * 3600:
        return None
    fetch = data.get("fetch")
    entry = fetch.get(key) if isinstance(fetch, dict) else None
    if not isinstance(entry, dict):
        return None
    return _parse_utc(entry.get("last_ok")) or _parse_utc(entry.get("failing_since"))


def assess_source(probe: Check, script: str, now: datetime) -> Check:
    """Red when the Update runs got no usable answer for :data:`SOURCE_STALE_H`.

    Operator decision 2026-10-28 (card "Linie, Issue als Frühwarnung"): red
    means the technology does not work, and every source gets the same
    three-hour rule as the Stammstrecke poll. Before, Wiener Linien, ÖBB and
    Baustellen were red only when this check's own probe failed at the very
    moment it ran: a seven-hour WL outage on 21.10.2026 turned one run red by
    chance, a short blip could do the same. Now the probe decides only when
    the Update runs' record cannot be trusted (:func:`_fetch_record`); a
    failed probe within the three hours is a note. The Feed-Wächter still
    opens its issue "Quelle ohne frische Antwort" after two hours, as the
    early warning.
    """
    last_ok = _fetch_record(FETCH_KEYS.get(script), now)
    if last_ok is None:
        return probe
    age_s = (now - last_ok).total_seconds()
    probe_text = probe.summary + (f" — {probe.detail}" if probe.detail else "")
    if age_s >= SOURCE_STALE_H * 3600:
        return Check(
            probe.name,
            ok=False,
            summary=f"FEHLER — seit {_fmt_age(age_s)} keine brauchbare Antwort im "
            f"Update-Lauf (Grenze {_fmt_age(SOURCE_STALE_H * 3600)})",
            detail=f"Stichprobe jetzt: {probe_text}",
        )
    if not probe.ok:
        return Check(
            probe.name,
            ok=True,
            note=True,
            summary=f"HINWEIS — Stichprobe gescheitert, letzte brauchbare Antwort im "
            f"Update-Lauf vor {_fmt_age(age_s)}",
            detail=probe_text,
        )
    return probe


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


def _last_build_date(text: str) -> datetime | str:
    """``<lastBuildDate>`` of an RSS document as an aware datetime, or the error."""
    m = re.search(r"<lastBuildDate>\s*(.*?)\s*</lastBuildDate>", text)
    if not m:
        return "<lastBuildDate> fehlt"
    try:
        built = parsedate_to_datetime(m.group(1))
    except (TypeError, ValueError) as exc:
        return f"<lastBuildDate> nicht parsebar: {m.group(1)!r}: {exc}"[:200]
    if built.tzinfo is None:
        built = built.replace(tzinfo=UTC)
    return built


def check_feed_freshness(now: datetime) -> Check:
    name = "Feed-Build"
    path = REPO_ROOT / "docs" / "feed.xml"
    text = read_capped_text(path, label="feed.xml")
    if text is None:
        return Check(name, ok=False,
                     summary="FEHLER — feed.xml fehlt, ist zu groß oder nicht lesbar")
    built = _last_build_date(text)
    if isinstance(built, str):
        if built.startswith("<lastBuildDate> fehlt"):
            return Check(name, ok=False,
                         summary="FEHLER — <lastBuildDate> fehlt in feed.xml")
        return Check(name, ok=False,
                     summary="FEHLER — <lastBuildDate> nicht parsebar",
                     detail=built.removeprefix("<lastBuildDate> nicht parsebar: "))
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


PUBLISHED_FEEDS: tuple[str, ...] = ("feed.xml", "feed.en.xml")


def _fmt_moment(moment: datetime) -> str:
    """A moment in Vienna time with its zone name (UTC when tzdata is missing)."""
    if _VIENNA is not None:
        return moment.astimezone(_VIENNA).strftime("%d.%m.%Y %H:%M %Z")
    return moment.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")


def _fetch_live_feed(url: str) -> str | None:
    """The published feed as text, or ``None`` when it cannot be fetched."""
    with session_with_retries("wien-oepnv-health-check", timeout=LIVE_FETCH_TIMEOUT_S) as session:
        content: bytes = fetch_content_safe(
            session,
            url,
            max_bytes=LIVE_FEED_MAX_BYTES,
            timeout=LIVE_FETCH_TIMEOUT_S,
            allowed_content_types=LIVE_FEED_CONTENT_TYPES,
        )
    return content.decode("utf-8", errors="replace")


def check_published_feeds(now: datetime) -> Check:
    """Red when the feeds GitHub Pages serves lag the committed ones for hours.

    The chain ends at the TV, not at ``docs/feed.xml`` in the repository:
    between the commit and the display sits the Pages build, a stage without
    a check of its own until 2026-10-31 (Architektur-Review). Every other
    check reads the checkout, so a Pages deploy that stops leaves the live
    feed stale while Update run, Health check and Feed-Wächter stay green.

    Rule, following the alarm line of 2026-10-28 (red only for a lasting
    technical fault): red when a live feed's ``<lastBuildDate>`` is more than
    :data:`LIVE_LAG_MAX_H` behind the committed copy's. A live copy that is
    newer than the checkout (a tick published since this run checked out) is
    fine. A live copy that cannot be fetched or read is a note: one failed
    request says nothing about duration, and a Pages outage keeps serving the
    last deployment rather than failing, so the stale case is the one that
    matters. The committed copy that cannot be read is already red in
    ``Feed-Build``; here it is a note so the two do not double-count.
    """
    name = "Veröffentlichung"
    limit = f"Grenze {_fmt_age(LIVE_LAG_MAX_H * 3600)}"
    problems: list[str] = []
    notes: list[str] = []
    oks: list[str] = []
    for file_name in PUBLISHED_FEEDS:
        local_text = read_capped_text(REPO_ROOT / "docs" / file_name, label=file_name)
        local_built = _last_build_date(local_text) if local_text is not None else "nicht lesbar"
        if isinstance(local_built, str):
            notes.append(f"{file_name}: Repo-Kopie {local_built}")
            continue
        url = f"{PAGES_BASE_URL}/{file_name}"
        try:
            live_text = _fetch_live_feed(url)
        except Exception as exc:
            reason = str(sanitize_log_arg(str(exc)))[:120]
            notes.append(f"{file_name}: Live-Abruf fehlgeschlagen ({type(exc).__name__}: {reason})")
            continue
        live_built = _last_build_date(live_text) if live_text is not None else "nicht lesbar"
        if isinstance(live_built, str):
            notes.append(f"{file_name}: Live-Kopie {live_built}")
            continue
        lag_s = (local_built - live_built).total_seconds()
        if lag_s > LIVE_LAG_MAX_H * 3600:
            problems.append(
                f"{file_name}: Live-Stand {_fmt_age(lag_s)} hinter dem Repo "
                f"(live {_fmt_moment(live_built)}, Repo {_fmt_moment(local_built)})"
            )
        elif lag_s > 0:
            oks.append(f"{file_name} {_fmt_age(lag_s)} hinter dem Repo")
        else:
            oks.append(f"{file_name} aktuell")
    if problems:
        return Check(name, ok=False,
                     summary=f"FEHLER — GitHub Pages liefert nicht mehr aus ({limit})",
                     detail="; ".join(problems + notes))
    if notes:
        return Check(name, ok=True, note=True,
                     summary="HINWEIS — Live-Stand nicht geprüft: " + "; ".join(notes),
                     detail="; ".join(oks))
    return Check(name, ok=True, summary=f"OK — {', '.join(oks)} ({limit})")


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
    # ``tooling`` secrets are not passed to the Health check; whether they
    # still work shows in ``Werkzeuge`` (latest run of claude-code-review.yml)
    # and ``Nebenschritte`` (Repository-Metadaten in seo-guard.yml), since
    # 2026-10-31 (Architektur-Review, Fund B).
    WorkflowSecret("CLAUDE_CODE_OAUTH_TOKEN", "tooling",
                   "Claude-Workflows (Code-Review, @claude)"),
    # A key of the free tier without billing (operator decision 2026-10-31).
    # A missing or rejected key fails the KI-Wächter run, and that shows in
    # "Nebenschritte".
    WorkflowSecret("GEMINI_API_KEY", "tooling",
                   "KI-Wächter (Gemini API, Gratis-Kontingent)"),
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
    ("ki-waechter.json", "KI-Wächter", KI_WAECHTER_MAX_AGE_H),
    # Repository-Metadaten via REPO_ADMIN_TOKEN (Architektur-Review 2026-10-31, Fund B).
    ("seo-guard.json", "SEO-Lauf", SEO_MAX_AGE_H),
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
        if not isinstance(data, dict) or not isinstance(steps, dict):
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


# Workflows that run on pull requests and therefore cannot record a side step
# into ``data/side_steps/`` (no write to ``main``): the Health check reads the
# conclusion of their latest completed run from the Actions API instead
# (file name, label). Architektur-Review 2026-10-31, Fund B: until then the
# review step ran with ``continue-on-error`` and an expired
# ``CLAUDE_CODE_OAUTH_TOKEN`` left no trace but a green check without comments.
TOOLING_WORKFLOWS: tuple[tuple[str, str], ...] = (
    ("claude-code-review.yml", "Code-Review"),
)
GITHUB_API_MAX_BYTES = 1024 * 1024


def _github_api_json(path: str) -> Any:
    """GET ``/repos/<GITHUB_REPOSITORY><path>`` from the GitHub API as JSON.

    Read-only, with the run's own ``GITHUB_TOKEN`` (``actions: read``). Raises
    on a missing token or repository, on an HTTP error and on bad JSON; the
    caller turns every exception into a note.
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        raise RuntimeError("GITHUB_TOKEN oder GITHUB_REPOSITORY fehlt")
    api = os.environ.get("GITHUB_API_URL", "").strip().rstrip("/") or "https://api.github.com"
    url = f"{api}/repos/{repository}{path}"
    with session_with_retries("wien-oepnv-health-check", timeout=LIVE_FETCH_TIMEOUT_S) as session:
        session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        content: bytes = fetch_content_safe(
            session,
            url,
            max_bytes=GITHUB_API_MAX_BYTES,
            timeout=LIVE_FETCH_TIMEOUT_S,
            allowed_content_types=("application/json",),
        )
    return loads_finite(content)


def check_tooling_runs(now: datetime) -> Check:
    """Red while the latest run of a tooling workflow failed.

    Same rule as ``Nebenschritte`` (operator decision 2026-10-28, "ein Fehler
    sollte auffallen"), for the workflows that run on pull requests and so
    cannot write a record into ``data/side_steps/``: the Code-Review
    (``claude-code-review.yml``) with ``CLAUDE_CODE_OAUTH_TOKEN``. Its step
    ran with ``continue-on-error`` until 2026-10-31, so an expired token left
    only a green check without comments (Architektur-Review 2026-10-31,
    Fund B). Now the run fails, and this check reads the conclusion of the
    latest completed run: ``success`` is green, a cancelled or skipped run is
    a note, every other conclusion (``failure``, ``timed_out``,
    ``startup_failure``) is red and names the run. An API that cannot be
    asked, or a workflow that never ran, is a note: nothing says the tool is
    broken.
    """
    name = "Werkzeuge"
    problems: list[str] = []
    notes: list[str] = []
    oks: list[str] = []
    for file_name, label in TOOLING_WORKFLOWS:
        try:
            data = _github_api_json(
                f"/actions/workflows/{file_name}/runs?status=completed&per_page=1"
            )
        except Exception as exc:
            reason = str(sanitize_log_arg(str(exc)))[:120]
            notes.append(f"{label}: Actions-API nicht abgefragt ({type(exc).__name__}: {reason})")
            continue
        runs = data.get("workflow_runs") if isinstance(data, dict) else None
        run = runs[0] if isinstance(runs, list) and runs and isinstance(runs[0], dict) else None
        if run is None:
            notes.append(f"{label}: noch kein abgeschlossener Lauf")
            continue
        conclusion = str(run.get("conclusion") or "")
        finished = _parse_utc(run.get("updated_at"))
        when = f" am {_vienna_hhmm(finished)}" if finished is not None else ""
        link = str(run.get("html_url") or "")
        if conclusion == "success":
            oks.append(f"{label} zuletzt sauber{when}")
        elif conclusion in {"cancelled", "skipped", "neutral"}:
            notes.append(f"{label}: letzter Lauf {conclusion}{when}")
        else:
            text = f"{label} gescheitert{when} ({conclusion or 'ohne Ergebnis'})"
            if link:
                text += f" — {link}"
            problems.append(text)
    if problems:
        return Check(name, ok=False, summary=f"FEHLER — {problems[0]}",
                     detail="; ".join(problems[1:] + notes + oks))
    if notes and not oks:
        return Check(name, ok=True, note=True,
                     summary="HINWEIS — " + "; ".join(notes))
    summary = "OK — " + ", ".join(oks)
    if notes:
        return Check(name, ok=True, note=True, summary=summary, detail="; ".join(notes))
    return Check(name, ok=True, summary=summary)


# GitHub Pages builds ``docs/`` from ``main`` in a workflow GitHub creates
# itself ("pages build and deployment"). It has no file in the repository and
# the Actions API takes no path as its id, so the id is looked up by path.
PAGES_WORKFLOW_PATH = "dynamic/pages/pages-build-deployment"
# Runs read per check: a streak of failures over about ten hours of ticks
# (a superseded run is cancelled and skipped), some 300 KB of JSON, well
# under GITHUB_API_MAX_BYTES (50 runs were 757 KB on 2026-11-01).
PAGES_RUNS_PER_PAGE = 20
_PAGES_SKIPPED = frozenset({"cancelled", "skipped", "neutral"})
# The Jekyll step of the build job ("Build with Jekyll"); the step before it,
# "Pull ghcr.io/actions/jekyll-build-pages:…", is GitHub's side. Should GitHub
# rename the step, a content failure counts as GitHub's side and turns red
# with the next run, which fails the same way.
_JEKYLL_STEP_RE = re.compile(r"build\b.*\bjekyll\b", re.IGNORECASE)


def _pages_runs() -> list[dict[str, Any]] | None:
    """The latest completed Pages runs, newest first; ``None`` without Pages."""
    data = _github_api_json("/actions/workflows?per_page=100")
    workflows = data.get("workflows") if isinstance(data, dict) else None
    if not isinstance(workflows, list):
        workflows = []
    ids = [
        wf.get("id")
        for wf in workflows
        if isinstance(wf, dict) and wf.get("path") == PAGES_WORKFLOW_PATH
    ]
    if not ids or not isinstance(ids[0], int):
        return None
    runs = _github_api_json(
        f"/actions/workflows/{ids[0]}/runs?status=completed&per_page={PAGES_RUNS_PER_PAGE}"
    )
    listed = runs.get("workflow_runs") if isinstance(runs, dict) else None
    return [run for run in listed if isinstance(run, dict)] if isinstance(listed, list) else []


def _pages_failure_kind(run: dict[str, Any]) -> str:
    """Which part of a failed Pages run failed.

    ``"jekyll"``: the Jekyll step, so the content under ``docs/`` does not
    build, and every later run fails the same way until it is fixed (the
    case of 2026-11-01). ``"github"``: another step or job (image pull,
    upload, deploy), GitHub's side; in October 2026 four such failures, each
    gone with the next run. ``""``: the jobs could not be read.
    """
    try:
        data = _github_api_json(f"/actions/runs/{int(run.get('id') or 0)}/jobs?per_page=20")
    except Exception:
        return ""
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list) or not jobs:
        return ""
    failed_steps = [
        str(step.get("name") or "")
        for job in jobs if isinstance(job, dict)
        for step in (job.get("steps") or []) if isinstance(step, dict)
        if step.get("conclusion") == "failure"
    ]
    return "jekyll" if any(_JEKYLL_STEP_RE.match(step) for step in failed_steps) else "github"


def check_pages_build(now: datetime) -> Check:
    """Red as soon as the latest Pages run failed in its Jekyll build.

    Operator decision (Michael, 2026-11-01, card "Soll ein gescheiterter
    Pages-Build künftig sofort auffallen?", answer "Beides bauen"): on
    2026-11-01 a bare Liquid tag in ``docs/reference/ki_waechter.md`` stopped
    every Pages build from 08:43 Vienna time (22 failed, 4 cancelled until
    11:13);
    the live feed the TV reads stayed at 08:30 while the Update runs stayed
    green, and ``Veröffentlichung`` would have turned red only at about
    13:00 (4 h lag). This check reads the latest completed run of "pages
    build and deployment" from the Actions API (``actions: read``), skipping
    runs GitHub cancelled because a newer commit came:

    * ``success``: green.
    * failed in the Jekyll step: red at once. The content does not build,
      and it stays so until someone fixes ``docs/``.
    * failed elsewhere (image pull, upload, deploy): GitHub's side. Red when
      the run before failed too, a single failure is a note (alarm line of
      2026-10-28: a single hiccup sends no mail; the next tick deploys
      again). In October 2026 there were four, each gone with the next run.
    * jobs that cannot be read: red, the failure is real.

    An Actions API that does not answer, no Pages workflow, or no finished
    run is a note. ``Veröffentlichung`` stays as the backstop for an outage
    that shows no failed run.
    """
    name = "Pages-Build"
    try:
        runs = _pages_runs()
    except Exception as exc:
        reason = str(sanitize_log_arg(str(exc)))[:120]
        return Check(name, ok=True, note=True,
                     summary=f"HINWEIS — Actions-API nicht abgefragt ({type(exc).__name__}: {reason})")
    if runs is None:
        return Check(name, ok=True, note=True,
                     summary=f"HINWEIS — kein Workflow {PAGES_WORKFLOW_PATH} gefunden")
    finished = [run for run in runs if str(run.get("conclusion") or "") not in _PAGES_SKIPPED]
    if not finished:
        return Check(name, ok=True, note=True,
                     summary="HINWEIS — kein abgeschlossener Pages-Lauf gefunden")
    latest = finished[0]
    ended = _parse_utc(latest.get("updated_at"))
    when = f" am {_vienna_hhmm(ended)}" if ended is not None else ""
    if str(latest.get("conclusion") or "") == "success":
        return Check(name, ok=True, summary=f"OK — letzter Lauf sauber{when}")

    streak = 0
    for run in finished:
        if str(run.get("conclusion") or "") == "success":
            break
        streak += 1
    kind = _pages_failure_kind(latest)
    what = {
        "jekyll": "Jekyll-Build gescheitert",
        "github": "Auslieferung durch GitHub gescheitert",
    }.get(kind, "Pages-Lauf gescheitert")
    text = f"{what}{when}"
    # The first failed run of the streak names the commit that broke the
    # build (on 2026-11-01 the merge of #2092); later ones are only ticks.
    # When every listed run failed, the start lies before the listed runs.
    first_failed: dict[str, Any] | None = finished[streak - 1]
    if streak > 1 and streak == len(finished):
        text += f", mindestens {streak} Läufe in Folge"
        first_failed = None
    elif streak > 1:
        started = _parse_utc(finished[streak - 1].get("created_at"))
        since = f" seit {_vienna_hhmm(started)}" if started is not None else ""
        text += f", {streak} Läufe in Folge{since}"
    details: list[str] = []
    commit = first_failed.get("head_commit") if first_failed is not None else None
    message = commit.get("message") if isinstance(commit, dict) else None
    if isinstance(message, str) and message.strip():
        first_line = str(sanitize_log_arg(message.strip().splitlines()[0]))[:80]
        details.append(f"zuerst nach Commit „{first_line}“" if streak > 1 else f"Commit „{first_line}“")
    link = str(latest.get("html_url") or "")
    if link:
        details.append(link)
    detail = " — ".join(details)
    if kind == "github" and streak == 1:
        return Check(name, ok=True, note=True,
                     summary=f"HINWEIS — {text}; der nächste Lauf liefert neu aus",
                     detail=detail)
    if kind == "jekyll":
        text += " (Inhalt unter docs/ prüfen)"
    return Check(name, ok=False,
                 summary=f"FEHLER — {text}; GitHub Pages liefert den alten Stand aus",
                 detail=detail)


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
        check_published_feeds(now),
        check_pages_build(now),
        check_stations(now),
        check_station_sources(now),
        check_stammstrecke_abruf(now),
        check_stammstrecke_directions(now),
        check_feed_watch(now),
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


if __name__ == "__main__":
    raise SystemExit(main())
