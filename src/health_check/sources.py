"""Live canary of the Health check: the updaters and the Feed-Wächter state.

Moved verbatim from ``scripts/health_check.py`` (700-line rule, 2026-11-02).
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404
import sys
from datetime import UTC, datetime

from src.health_check.common import (
    FEED_MAX_AGE_H,
    REPO_ROOT,
    Check,
    _clean_line,
    _env_float,
    _fmt_age,
    _parse_utc,
)
from src.utils.files import read_capped_json


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


def _extract_count(output: str) -> int | None:
    for pattern in _COUNT_RES:
        m = pattern.search(output)
        if m:
            return int(m.group(1))
    return None


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
