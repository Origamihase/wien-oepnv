"""Shared parts of the Health check: thresholds, the ``Check`` record, time helpers.

Moved verbatim from ``scripts/health_check.py`` (700-line rule, 2026-11-02).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config.defaults import DEFAULT_PAGES_BASE_URL

# The repository root, the same path as ``REPO_ROOT`` in scripts/health_check.py.
REPO_ROOT = Path(__file__).resolve().parents[2]


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
# A KI-Wächter run that got no answer only because Gemini was overloaded
# (outcome "overloaded") is a note until the last clean audit is this old
# (Michael 2026-11-04, card "Ja, so bauen": 503 at 9:00 on every day from
# 01.11. to 04.11. turned the Health check red although the feed was fine;
# the retry at 11:31 got through on 02.11. and 03.11.). 30 h = a whole day
# with all its tries lost. A rejected key or a broken step stays red at once.
KI_WAECHTER_OVERLOAD_MAX_H = _env_float("HEALTH_KI_WAECHTER_OVERLOAD_HOURS", 30.0)
# SEO run (seo-guard.yml): daily cron, which GitHub delivers hours late (see
# docs/development.md, "Geplante Läufe"), plus every push touching docs/.
# 48 h = two days without any run before its side-step record counts as stale.
SEO_MAX_AGE_H = _env_float("HEALTH_SEO_MAX_AGE_HOURS", 48.0)
# Station sources: the weekly run downloads them and falls back to the copy
# under data/ when a download fails, so a dead source would only show in the
# log. 15 days = two weekly runs in a row without a good answer (one failed
# run is tolerated, the station data changes slowly).
STATION_SOURCE_MAX_AGE_H = _env_float("HEALTH_STATION_SOURCE_MAX_AGE_HOURS", 360.0)


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


def _vienna_hhmm(moment: datetime) -> str:
    local = moment.astimezone(_VIENNA) if _VIENNA is not None else moment
    return local.strftime("%d.%m. %H:%M")


def _parse_utc(raw: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
