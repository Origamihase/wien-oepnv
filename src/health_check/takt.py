"""Health check "Takt": IFTTT ticks, or the Takt-Fallback keeps the feed fresh.

Operator decision Michael 2026-11-04: "Wenn IFTTT nicht funktioniert erstelle
einen Issue. Wenn der Fallback auch nicht funktioniert, und der Feed nicht
mehr aktualisiert werden kann, mach den Health Check rot." The fallback
(``.github/workflows/takt-fallback.yml``, ``src/takt_fallback.py``) starts
the Update cycle 40 minutes after the last run while IFTTT is silent, so a
feed without a new build for 80 minutes means that the fallback failed
twice as well.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.health_check.common import REPO_ROOT, Check, _env_float, _fmt_age, _vienna_hhmm
from src.health_check.github_runs import _github_api_json
from src.health_check.outputs import _last_build_date
from src.takt_fallback import FALLBACK_AFTER, UPDATE_WORKFLOW, newest_ifttt, parse_runs, tick_time
from src.utils.files import read_capped_text

# Two fallback starts (after 40 and after 80 minutes) without a new build.
TAKT_RED_AFTER_MIN = _env_float("HEALTH_TAKT_RED_MINUTES", 80.0)


def check_takt(now: datetime) -> Check:
    """Green while IFTTT ticks; a note while the fallback carries the feed; red after that."""
    name = "Takt"
    try:
        payload = _github_api_json(f"/actions/workflows/{UPDATE_WORKFLOW}/runs?branch=main&per_page=30")
    except Exception as exc:  # noqa: BLE001 - an API hiccup says nothing about the cadence
        return Check(name, ok=True, note=True,
                     summary="HINWEIS — Update-Läufe nicht lesbar",
                     detail=f"{type(exc).__name__}: {str(exc)[:160]}")
    runs = parse_runs(payload)
    ifttt = newest_ifttt(runs)
    if ifttt is not None and now - tick_time(ifttt, now) < FALLBACK_AFTER:
        return Check(name, ok=True, summary=f"OK — IFTTT tickt (letzter Tick {_vienna_hhmm(ifttt.created)})")
    since = f"seit {_vienna_hhmm(ifttt.created)}" if ifttt is not None else "seit unbekannt"
    text = read_capped_text(REPO_ROOT / "docs" / "feed.xml", label="feed.xml")
    built = _last_build_date(text) if text is not None else "feed.xml fehlt"
    if isinstance(built, str):
        return Check(name, ok=False, summary=f"FEHLER — IFTTT schweigt {since}, Feed-Alter unbekannt",
                     detail=built)
    age = now - built
    fallbacks = sum(1 for run in runs if run.is_fallback)
    if age <= timedelta(minutes=TAKT_RED_AFTER_MIN):
        return Check(name, ok=True, note=True,
                     summary=f"HINWEIS — IFTTT schweigt {since}; der Takt-Fallback hält den Feed "
                     f"aktuell (Build vor {_fmt_age(age.total_seconds())})",
                     detail=f"Fallback-Läufe unter den letzten {len(runs)} Update-Läufen: {fallbacks}; "
                     "Issue ifttt-ausfall.")
    return Check(name, ok=False,
                 summary=f"FEHLER — IFTTT schweigt {since}, und auch der Takt-Fallback bringt keinen "
                 f"neuen Build (seit {_fmt_age(age.total_seconds())}, Grenze {int(TAKT_RED_AFTER_MIN)}m)",
                 detail="IFTTT-Applet und sein Token prüfen; Läufe von takt-fallback.yml und "
                 "update-cycle.yml ansehen.")
