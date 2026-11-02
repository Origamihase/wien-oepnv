"""Feed-Wächter: the rules of the German feed.

Moved verbatim from ``scripts/feed_watch.py``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from html import unescape
from typing import Any

from src.feed_watch.model import (
    _ABBREVIATION_END_RE,
    _COURTESY,
    _DATE_RE,
    _EXPECTED_UNTIL_RE,
    _NBSP,
    _OEBB_COURTESY_RE,
    _OEBB_DATE_PREFIX_RE,
    _OEBB_PLACE_RE,
    _SENTENCE_SPLIT_RE,
    _SINCE_CLOCK_RE,
    _STATION_WORDS_RE,
    _TIME_LINE_RE,
    BODY_CHAR_LIMIT,
    EXPECTED_END_BEFORE_MESSAGE,
    FEED_MAX_AGE,
    OLD_AS_NEW,
    PREVIOUS_MAX_GAP,
    SINCE_LATE,
    Finding,
    Item,
    _is_current,
    _line_keys,
    _local,
    _normalize,
    _parse_iso,
    _since_clock,
    _vienna_clock,
)

# --- rules of the German feed ------------------------------------------------


def check_duplicates(items: Sequence[Item]) -> list[Finding]:
    findings: list[Finding] = []
    seen_guids: dict[str, int] = {}
    seen_titles: dict[str, int] = {}
    for place, item in enumerate(items, start=1):
        title_key = _normalize(item.title).casefold()
        if item.guid and item.guid in seen_guids:
            findings.append(
                Finding(
                    "doppelter-eintrag",
                    item.title,
                    f"Platz {place} hat dieselbe GUID wie Platz {seen_guids[item.guid]}",
                )
            )
        elif title_key and title_key in seen_titles:
            findings.append(
                Finding(
                    "doppelter-eintrag",
                    item.title,
                    f"Platz {place} hat denselben Titel wie Platz {seen_titles[title_key]}",
                )
            )
        seen_guids.setdefault(item.guid, place)
        seen_titles.setdefault(title_key, place)
    return findings


def check_count(items: Sequence[Item], max_items: int) -> list[Finding]:
    if len(items) == max_items:
        return []
    return [Finding("anzahl", "Feed", f"{len(items)} statt {max_items} Einträge")]


def check_old_as_new(items: Sequence[Item]) -> list[Finding]:
    """A message older than a day that the feed counts as first seen today."""
    findings: list[Finding] = []
    for item in items:
        if not _is_current(item.time_line) or item.first_seen is None or item.pub_date is None:
            continue
        older = item.first_seen - item.pub_date > OLD_AS_NEW
        started = item.starts_at is None or item.first_seen - item.starts_at > OLD_AS_NEW
        if older and started:
            findings.append(
                Finding(
                    "alte-meldung-als-neu",
                    item.title,
                    f"Meldung vom {_local(item.pub_date):%d.%m. %H:%M}, im Feed erstmals "
                    f"{_local(item.first_seen):%d.%m. %H:%M}, Zeitzeile [{_normalize(item.time_line or '')}]",
                )
            )
    return findings


def check_since_too_early(
    items: Sequence[Item],
    built: datetime,
    previous: Mapping[str, Any] | None,
    flagged: Sequence[Mapping[str, Any]],
) -> tuple[list[Finding], list[dict[str, Any]]]:
    """Rule ``seit-zu-frueh``; also returns the incidents to keep flagging.

    An incident (time line "Seit hh:mm") that appears for the first time,
    while none of its lines stood on screen as an incident in the previous
    build and that build had room (an item that was no incident: incidents
    always come first), began long before it appeared only when its time is
    wrong. Once flagged, the same lines stay flagged on that day while they
    show a "Seit" time no later than an hour after the flagged one: on
    2026-10-09 "2/71 [Seit 13:08]" became "2 [Seit 13:08]" and "71 [Seit
    13:10]" at the next build.
    """
    today = _local(built).date().isoformat()
    keep: list[dict[str, Any]] = [
        dict(entry) for entry in flagged if isinstance(entry, Mapping) and entry.get("day") == today
    ]
    shown: set[str] = set()
    room = False
    usable = False
    if isinstance(previous, Mapping):
        previous_built = _parse_iso(previous.get("built"))
        rows = previous.get("items")
        if previous_built is not None and isinstance(rows, list):
            usable = timedelta(0) < built - previous_built <= PREVIOUS_MAX_GAP
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                time_line = str(row.get("time_line") or "")
                if _SINCE_CLOCK_RE.fullmatch(time_line.replace(_NBSP, " ")):
                    shown |= _line_keys(str(row.get("title") or ""))
                else:
                    room = True
    findings: list[Finding] = []
    for item in items:
        since = _since_clock(item.time_line, built)
        if since is None or built - since <= SINCE_LATE:
            continue
        keys = _line_keys(item.title)
        clock = f"{_local(since):%H:%M}"
        still = any(
            keys & set(entry.get("lines") or [])
            and isinstance(entry.get("since"), str)
            and since
            <= _vienna_clock(
                _local(built).date(), int(entry["since"][:2]), int(entry["since"][3:5]), built
            )
            + timedelta(hours=1)
            for entry in keep
        )
        new = usable and room and not (keys & shown)
        if not (still or new):
            continue
        findings.append(
            Finding(
                "seit-zu-frueh",
                item.title,
                f"[Seit {clock}] erscheint um {_local(built):%H:%M} zum ersten Mal, "
                "obwohl der Feed vorher Platz hatte",
            )
        )
        if new:
            keep.append({"day": today, "lines": sorted(keys), "since": clock})
    return findings, keep


def _oebb_source_sentences(description: str) -> list[str]:
    text = re.sub(r"<br\s*/?>", " ", description, flags=re.IGNORECASE)
    text = unescape(re.sub(r"<[^>]+>", "", text))
    text = _normalize(_OEBB_DATE_PREFIX_RE.sub("", _normalize(text)))
    sentences: list[str] = []
    for part in _SENTENCE_SPLIT_RE.split(text):
        if sentences and _ABBREVIATION_END_RE.search(sentences[-1]):
            sentences[-1] = f"{sentences[-1]} {part}"
        else:
            sentences.append(part)
    return [s for s in sentences if s and not _OEBB_COURTESY_RE.search(s)]


def _oebb_key(name: str) -> str:
    key = re.sub(r"[\s.()]", "", _STATION_WORDS_RE.sub("", name)).casefold()
    return key.removeprefix("wien")


def _oebb_title_names(place: str, title: str) -> bool:
    """Whether *title* names every station of the place phrase *place*."""
    core = re.sub(r"^(?:zwischen|im Bereich|in)\s+", "", place.strip())
    shown = re.sub(r"[\s.()]", "", title).casefold().replace("hbf", "hauptbahnhof")
    keys = [_oebb_key(part).replace("hbf", "hauptbahnhof") for part in re.split(r"\s+und\s+", core)]
    return all(len(key) >= 4 and key in shown for key in keys)


def check_oebb_second_sentence(items: Sequence[Item], oebb_cache: Mapping[str, str]) -> list[Finding]:
    """ÖBB's second sentence missing although it would fit without the stations (#1993)."""
    findings: list[Finding] = []
    for item in items:
        source = oebb_cache.get(item.guid)
        if not source:
            continue
        sentences = _oebb_source_sentences(source)
        if len(sentences) < 2:
            continue
        first, second = sentences[0], sentences[1]
        if second in _normalize(item.body):
            continue
        place = _OEBB_PLACE_RE.search(first)
        if place is None or not _oebb_title_names(place.group(0), item.title):
            continue
        shorter = first[: place.start()] + first[place.end() :]
        if len(shorter) + 1 + len(second) <= BODY_CHAR_LIMIT:
            findings.append(
                Finding("oebb-zweiter-satz", item.title, f"fehlt: „{second[:120]}“")
            )
    return findings


def check_time_line(items: Sequence[Item]) -> list[Finding]:
    findings: list[Finding] = []
    for item in items:
        time_line = item.time_line
        if time_line is None:
            findings.append(Finding("zeitzeile", item.title, "keine Zeitzeile"))
        elif not _TIME_LINE_RE.fullmatch(time_line):
            shown = time_line.replace(_NBSP, "·").replace(" ", "‿")
            findings.append(Finding("zeitzeile", item.title, f"unbekannte Form [{shown}]"))
    return findings


def _time_line_end(time_line: str, today: date) -> date | None:
    """The last day a "Bis …", "Am …" or "Ab … bis …" time line names."""
    words = time_line.replace(_NBSP, " ")
    if not words.startswith(("Bis ", "Am ", "Ab ")):
        return None
    if words.startswith("Ab ") and " bis " not in words:
        return None
    match = list(_DATE_RE.finditer(words))
    if not match:
        return None
    last = match[-1]
    try:
        return date(int(last.group(3) or today.year), int(last.group(2)), int(last.group(1)))
    except ValueError:
        return None


def check_past_end(items: Sequence[Item], built: datetime) -> list[Finding]:
    findings: list[Finding] = []
    local = _local(built)
    for item in items:
        time_line = item.time_line
        if time_line is None:
            continue
        end = _time_line_end(time_line, local.date())
        if end is not None and end < local.date():
            findings.append(
                Finding("ende-vorbei", item.title, f"[{_normalize(time_line)}] liegt vor heute")
            )
        since = _since_clock(time_line, built)
        if since is not None and since > built.astimezone(UTC) + timedelta(minutes=1):
            findings.append(
                Finding(
                    "ende-vorbei",
                    item.title,
                    f"[{_normalize(time_line)}] liegt nach dem Bau um {local:%H:%M}",
                )
            )
        if not _is_current(time_line):
            continue
        # The clock time belongs to the day of the item's newest time, as in
        # the builder (``_drop_passed_expected_end``): more than three hours
        # before it, it is the next morning. "[Seit 23:30]" with
        # "Voraussichtlich bis 00:30" at 23:45 is not past.
        times = [t for t in (item.pub_date, item.starts_at, since) if t is not None]
        anchor = _local(max(times, key=lambda t: t.astimezone(UTC)) if times else built)
        for match in _EXPECTED_UNTIL_RE.finditer(item.body):
            hour, minute = int(match.group(1)), int(match.group(2))
            if hour > 23 or minute > 59:
                continue
            until = _vienna_clock(anchor.date(), hour, minute, built)
            if until < anchor.astimezone(UTC) - EXPECTED_END_BEFORE_MESSAGE:
                until = _vienna_clock(anchor.date() + timedelta(days=1), hour, minute, built)
            if until < built.astimezone(UTC) - timedelta(minutes=1):
                findings.append(
                    Finding(
                        "ende-vorbei",
                        item.title,
                        f"„Voraussichtlich bis {hour:02d}:{minute:02d}“ um {local:%H:%M}",
                    )
                )
    return findings


def check_length(items: Sequence[Item]) -> list[Finding]:
    return [
        Finding("textlaenge", item.title, f"{len(item.body)} Zeichen")
        for item in items
        if len(item.body) > BODY_CHAR_LIMIT
    ]


def check_courtesy(items: Sequence[Item]) -> list[Finding]:
    return [
        Finding("hoeflichkeitssatz", item.title, "„Wir bitten um Entschuldigung.“")
        for item in items
        if _COURTESY in item.description
    ]


def check_feed_age(built: datetime | None, now: datetime) -> list[Finding]:
    if built is None:
        return [Finding("feed-alter", "Feed", "keine lesbare <lastBuildDate>")]
    if now - built > FEED_MAX_AGE:
        minutes = int((now - built).total_seconds() // 60)
        return [Finding("feed-alter", "Feed", f"zuletzt gebaut {_local(built):%d.%m. %H:%M}, vor {minutes} Minuten")]
    return []
