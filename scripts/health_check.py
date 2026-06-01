#!/usr/bin/env python3
"""Standalone health probe for the Wien-ÖPNV pipeline.

Runs OUTSIDE the feed build (own workflow: ``.github/workflows/health-check.yml``)
and is *allowed to fail* (exit 1 → red run) so that GitHub e-mails the
maintainer. It is fully independent of ``update-cycle.yml``: a red health check
never affects the feed build, and the feed build is never made to fail by this
script.

It runs two complementary kinds of checks:

1. **Quellen-Erreichbarkeit (Live-Canary).** Runs the *real* production cache
   updaters (``update_wl_cache.py`` / ``update_oebb_cache.py`` /
   ``update_baustellen_cache.py``) as subprocesses and reads their exit code.
   This is the most faithful "is the source usable right now?" signal — it
   exercises the exact fetch + parse + relevance-filter + degradation-guard
   path the feed depends on:

     * ``0`` → source delivered usable data.
     * ``1`` → unreachable / empty / malformed / degraded.
     * ``2`` → (Baustellen only) live WFS failed, cache fell back to bundled
       demo data — i.e. the live source is effectively down. Treated as a
       failure here so this otherwise-silent fallback gets surfaced.

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

Stammstrecke/VOR is deliberately NOT probed live: the VAO ReST API is capped at
100 requests/day and a recurring health probe would burn that contractual
budget.

Thresholds are env-tunable (see the constants below); the defaults match the
two workflows' cadences.
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:  # Vienna local time is a nice-to-have header detail; tzdata may be absent.
    _VIENNA: ZoneInfo | None = ZoneInfo("Europe/Vienna")
except Exception:  # pragma: no cover - tzdata missing
    _VIENNA = None

REPO_ROOT = Path(__file__).resolve().parents[1]


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
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Check(name, ok=False, summary="FEHLER — feed.xml nicht lesbar",
                     detail=str(exc)[:200])
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
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Check(name, ok=False,
                     summary="FEHLER — stations_last_run.json nicht lesbar",
                     detail=str(exc)[:200])

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


def _render_plain(now: datetime, sources: list[Check], outputs: list[Check]) -> str:
    line = "=" * 64
    when = now.strftime("%Y-%m-%d %H:%M UTC")
    if _VIENNA is not None:
        when += now.astimezone(_VIENNA).strftime("  (%H:%M %Z)")
    rows: list[str] = [line, "  Wien-ÖPNV Health-Check", f"  {when}", line]

    def block(title: str, checks: list[Check]) -> None:
        rows.append(title)
        width = max(len(c.name) for c in checks)
        for c in checks:
            mark = "✅" if c.ok else "❌"
            rows.append(f"  {mark} {c.name.ljust(width)}  {c.summary}")
            if c.detail:
                rows.append(f"      └─ {c.detail}")
        rows.append("")

    block("QUELLEN (Live-Abruf)", sources)
    block("AKTUALITÄT (committete Outputs)", outputs)

    failed = [c for c in sources + outputs if not c.ok]
    rows.append(line)
    if failed:
        names = ", ".join(c.name for c in failed)
        rows.append(f"ERGEBNIS: {len(failed)} Problem(e) ❌  →  {names}")
    else:
        rows.append("ERGEBNIS: Alles gesund ✅")
    rows.append(line)
    return "\n".join(rows)


def _render_markdown(now: datetime, sources: list[Check], outputs: list[Check]) -> str:
    failed = [c for c in sources + outputs if not c.ok]
    head = "❌ Probleme gefunden" if failed else "✅ Alles gesund"
    lines = [
        f"## Wien-ÖPNV Health-Check — {head}",
        f"_{now.strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "| Status | Prüfung | Ergebnis |",
        "| :----: | ------- | -------- |",
    ]
    for c in sources + outputs:
        mark = "✅" if c.ok else "❌"
        cell = c.summary + (f"<br>↳ `{c.detail}`" if c.detail else "")
        lines.append(f"| {mark} | **{c.name}** | {cell} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    now = datetime.now(UTC)

    sources = [check_source(name, script) for name, script in UPDATERS]
    outputs = [check_feed_freshness(now), check_stations(now)]
    all_checks = sources + outputs
    failed = [c for c in all_checks if not c.ok]

    print(_render_plain(now, sources, outputs))

    # GitHub Actions annotations — make each failure jump out in the run UI
    # (and the failure e-mail's linked summary).
    for c in failed:
        msg = c.summary + (f" — {c.detail}" if c.detail else "")
        print(f"::error title=Health: {c.name}::{msg}")

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(_render_markdown(now, sources, outputs))
        except OSError:
            pass  # summary is best-effort; never let it mask the real result

    # Non-zero exit ⇒ red run ⇒ GitHub failure e-mail.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
