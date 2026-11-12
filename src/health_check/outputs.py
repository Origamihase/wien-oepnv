"""Health checks on the committed outputs and the published feeds.

Moved verbatim from ``scripts/health_check.py`` (700-line rule, 2026-11-02).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from src.health_check.common import (
    _VIENNA,
    FEED_MAX_AGE_H,
    KI_WAECHTER_MAX_AGE_H,
    KI_WAECHTER_OVERLOAD_MAX_H,
    LIVE_FEED_CONTENT_TYPES,
    LIVE_FEED_MAX_BYTES,
    LIVE_FETCH_TIMEOUT_S,
    LIVE_LAG_MAX_H,
    PAGES_BASE_URL,
    REPO_ROOT,
    SEO_MAX_AGE_H,
    SEO_OVERLOAD_MAX_H,
    STATION_SOURCE_MAX_AGE_H,
    STATIONS_MAX_AGE_H,
    Check,
    _fmt_age,
    _parse_utc,
    _vienna_hhmm,
)
from src.utils.files import read_capped_json, read_capped_text
from src.utils.http import fetch_content_safe, session_with_retries
from src.utils.logging import sanitize_log_arg


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


TRANSLATION_STATUS = REPO_ROOT / "data" / "translation_status.json"


def check_translation(now: datetime) -> Check:
    """Show whether the last build that needed the translation model loaded it.

    Every build that needs the model records the outcome
    (``src.feed_build.translator._record_translation_status``). A model that cannot load
    turns the EN feed into a German copy for every new item; until 2026-11-01
    that was only a WARNING line in the log (Dependabot #2114: with
    ``transformers`` 5 the old pipeline task was gone, the PR check stayed
    green). Operator decision 2026-11-01 ("Ja, Umstieg bauen"): the first
    failed build is a note (a single failed torch download heals on the next
    tick), red from the second failed build in a row. A missing record stays
    a note: builds whose EN texts all come from the cache do not load the
    model and write nothing.
    """
    name = "Übersetzung"
    data = read_capped_json(TRANSLATION_STATUS, label="translation_status")
    if not isinstance(data, dict):
        return Check(name, ok=True, note=True,
                     summary="HINWEIS — noch kein Übersetzungsstatus "
                     "(data/translation_status.json fehlt oder ist unlesbar)")
    updated = _parse_utc(data.get("updated"))
    when = f"Stand {_vienna_hhmm(updated)}" if updated else "Zeitpunkt unbekannt"
    failed = data.get("failed_builds")
    failed_builds = failed if isinstance(failed, int) and not isinstance(failed, bool) else 0
    if data.get("loaded") is True:
        return Check(name, ok=True, summary=f"OK — Übersetzungsmodell geladen ({when})")
    detail = str(data.get("error") or "")[:200]
    if failed_builds >= 2:
        return Check(name, ok=False,
                     summary=f"FEHLER — Übersetzungsmodell lädt nicht, {failed_builds} Builds "
                     f"in Folge; neue EN-Meldungen bleiben deutsch ({when})",
                     detail=detail)
    return Check(name, ok=True, note=True,
                 summary=f"HINWEIS — Übersetzungsmodell lud im letzten Build nicht ({when})",
                 detail=detail)


# One record per workflow, written by ``scripts/record_side_steps.py`` at the
# end of the run (file name, workflow, oldest age before the record itself
# counts as missing: the workflow's own freshness limit, and how long an
# "overloaded" step stays a note; None: such a step is red at once).
SIDE_STEP_RECORDS: list[tuple[str, str, float, float | None]] = [
    # "overloaded" comes only from the build's "KI-Entscheidung" (Gemini gave a
    # new unclear message no answer, the rules decided): a note like the
    # KI-Wächter's (owner 2026-11-12, ``src.ki_entscheidung``).
    ("update-cycle.json", "Update-Lauf", FEED_MAX_AGE_H, KI_WAECHTER_OVERLOAD_MAX_H),
    ("update-stations.json", "Stationslauf", STATIONS_MAX_AGE_H, None),
    # Gemini overloaded: a note for 30 h after the last clean audit
    # (Michael 2026-11-04, see KI_WAECHTER_OVERLOAD_MAX_H).
    ("ki-waechter.json", "KI-Wächter", KI_WAECHTER_MAX_AGE_H, KI_WAECHTER_OVERLOAD_MAX_H),
    # Repository-Metadaten via REPO_ADMIN_TOKEN (Architektur-Review 2026-10-31, Fund B);
    # GitHub only disturbed (5xx): a note for 48 h (Michael 2026-11-04).
    ("seo-guard.json", "SEO-Lauf", SEO_MAX_AGE_H, SEO_OVERLOAD_MAX_H),
]
SIDE_STEP_DIR = REPO_ROOT / "data" / "side_steps"


def _side_step_finding(
    label: str, workflow: str, entry: dict[str, object], now: datetime, overload_max_h: float | None
) -> tuple[str, bool] | None:
    """What one failed side step says, and whether it is red (None: it ran cleanly).

    ``overloaded`` (an outside service was busy, 2026-11-04) is a note while
    the last success, or the first failure when there never was one, is
    younger than ``overload_max_h``; every other failure is red.
    """
    outcome = entry.get("outcome")
    if outcome not in ("failure", "overloaded"):
        return None
    at = _parse_utc(entry.get("at"))
    last_ok = _parse_utc(entry.get("last_success"))
    busy = outcome == "overloaded"
    text = f"{label} ({workflow}) " + ("ohne Antwort, Dienst überlastet" if busy else "gescheitert")
    if at is not None:
        text += f" am {_vienna_hhmm(at)}"
    text += f", zuletzt sauber am {_vienna_hhmm(last_ok)}" if last_ok else ", noch nie sauber"
    red = True
    if busy and overload_max_h is not None:
        since = last_ok or _parse_utc(entry.get("failing_since")) or at
        if since is not None:
            red_at = since + timedelta(hours=overload_max_h)
            red = now >= red_at
            if not red:
                text += f"; rot ab {_vienna_hhmm(red_at)}, falls bis dahin keine Prüfung gelingt"
            else:
                text += f"; seit {_fmt_age((now - since).total_seconds())} keine gelungene Prüfung"
    if entry.get("run"):
        text += f" — {entry['run']}"
    return text, red


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
    the first run with the recorder) is a note. A KI-Wächter day without an
    answer only because Gemini was overloaded is a note until 30 h after the
    last clean audit (Michael 2026-11-04, card "Ja, so bauen").
    """
    name = "Nebenschritte"
    problems: list[str] = []
    waits: list[str] = []
    notes: list[str] = []
    counted = 0
    for file_name, workflow, max_age_h, overload_max_h in SIDE_STEP_RECORDS:
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
            finding = _side_step_finding(label, workflow, entry, now, overload_max_h)
            if finding is not None:
                (problems if finding[1] else waits).append(finding[0])
    if problems:
        return Check(name, ok=False, summary=f"FEHLER — {problems[0]}",
                     detail="; ".join(problems[1:] + waits + notes))
    if waits:
        return Check(name, ok=True, note=True, summary=f"HINWEIS — {waits[0]}",
                     detail="; ".join(waits[1:] + notes))
    if notes and not counted:
        return Check(name, ok=True, note=True,
                     summary="HINWEIS — noch kein Stand der Nebenschritte",
                     detail="; ".join(notes))
    summary = f"OK — alle {counted} Nebenschritte liefen zuletzt sauber"
    if notes:
        return Check(name, ok=True, note=True, summary=summary, detail="; ".join(notes))
    return Check(name, ok=True, summary=summary)
