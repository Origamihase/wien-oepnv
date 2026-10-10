#!/usr/bin/env python3
"""Feed-Wächter: check the built German feed against the rules learned since September 2026.

Runs at the end of every Update cycle, after the feed is built and before it
is published (``.github/workflows/update-cycle.yml``, step "Feed-Wächter").
It reads ``docs/feed.xml``, the provider caches and the raw answers in
``data/raw/`` and reports every rule the run breaks. It never changes the feed
and never stops the publish: a finding is a note, and an error of the
watcher itself is only logged (exit code 0 in every case).

Why: most defects of the feed so far were found by an audit, hours to weeks
after they first showed on the TV. On 2026-10-09 a stop ticker without a
cause took over the GUID and start of a delay on line 25 and stood as a new
disruption from 12:01 to 20:30, up to place 2; ÖBB's second sentence ("what
still applies") was missing in 59 of 106 texts since September; "2/71:
Polizeieinsatz [Seit 13:08]" stood for an incident from 20:08. The watcher
catches such cases on the day they happen, and new ones when Wiener Linien,
ÖBB or the City of Vienna change their data in a way no audit foresaw.

Never red (operator decision 2026-10-10, Michael, for the Health check:
"Der Check soll nur auf rot, wenn der Abruf nicht funktioniert. Wenn die
Technik funktioniert gehört er auch auf grün."). A rule the feed breaks is
content, not a broken fetch: the watcher reports it as a ``::warning``
annotation in the job log and the job summary, in ``data/feed_watch.json``
(read by the Health check as a note) and, with ``FEED_WATCH_ISSUES=1``, as
one GitHub issue per broken rule. The issue is opened once (or reopened
when it was closed less than a day ago) and closes itself once the rule has
held in two runs in a row.

Every rule held on every good day of the feed history from 2026-09-01 to
2026-10-10 (1,975 builds) and was loud on the days of its defect; see
``docs/reference/feed_watch.md`` for the rules, the replay and the reasons behind
each threshold.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from logging import DEBUG, INFO, getLogger
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from defusedxml import ElementTree as ET

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.feed.config import InvalidPathError, validate_path
from src.feed.logging_safe import setup_script_logging
from src.utils.files import atomic_write, read_capped_json, read_capped_text
from src.utils.logging import sanitize_log_arg
from src.utils.serialize import scrub_trojan_source_primitives

LOGGER = getLogger("feed_watch")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FEED = REPO_ROOT / "docs" / "feed.xml"
DEFAULT_STATE = REPO_ROOT / "data" / "feed_watch.json"
DEFAULT_CACHE_DIR = REPO_ROOT / "cache"
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw"
VIENNA = ZoneInfo("Europe/Vienna")

MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 8 * 1024 * 1024

# The TV shows ten items (EasySignage, ``MaxItems``); the builder's default.
DEFAULT_MAX_ITEMS = 10
# EasySignage cuts a description at 180 characters, the builder at the same
# limit (``_truncate_summary_180``); the time line comes on top.
BODY_CHAR_LIMIT = 180

# "Seit hh:mm" for an incident the feed shows for the first time, although
# a line of it was not on screen and the screen had room: WL enters a
# disruption at most 9 minutes after its start and the fetch sees it at most
# 79 minutes later (all 366 unplanned WL messages from 2026-10-04 to
# 2026-10-10, ``reports/seit-zeit-2026-10-10.md``); a display ticker may move
# the begin up to 60 minutes earlier (PR #2001). Three hours lie above all of
# that; "2/71: Polizeieinsatz [Seit 13:08]" appeared at 20:30, 7 h 22 min late.
SINCE_LATE = timedelta(hours=3)
# The previous build counts as "the screen before" only when it is this recent.
PREVIOUS_MAX_GAP = timedelta(hours=2)
# A message older than a day that the feed counts as first seen today has
# taken over the identity of another one (line 25 on 2026-10-09: a stop
# ticker from 07.10. with the GUID and first_seen of a delay from 09:36).
OLD_AS_NEW = timedelta(hours=24)
# A feed older than this when the watcher runs was not rebuilt by this run.
FEED_MAX_AGE = timedelta(minutes=90)
# An issue closes after the rule held in this many runs in a row.
HELD_RUNS_TO_CLOSE = 2
# A closed issue reopens instead of a new one when it closed this recently.
REOPEN_WINDOW = timedelta(hours=24)

# Source drift: one sample per run, compared with the samples of the last
# seven days. The thresholds held from 2026-10-04 (the first raw answers) to
# 2026-10-10 on every run; the raw counts moved between 56 and 196 (WL
# disruptions), 74 and 97 (WL notices), 164 and 179 (ÖBB) and 66 and 77
# (Baustellen).
SAMPLE_WINDOW = timedelta(days=7)
MAX_SAMPLES = 400
MIN_REFERENCE_SAMPLES = 24
SOURCE_DROP_FACTOR = 0.5
SOURCE_DROP_MIN_REFERENCE = 10
DISCARD_SHARE_JUMP = 0.25
EMPTY_CACHE_MIN_REFERENCE = 5

_NBSP = "\u00a0"
_WEEKDAY = r"(?:Mo|Di|Mi|Do|Fr|Sa|So)"
_DAY = rf"(?:{_WEEKDAY}{_NBSP})?\d{{2}}\.\d{{2}}\.(?:\d{{4}})?"
# Every form ``format_local_times`` writes (operator decisions 2026-10-02,
# 2026-10-03; words joined with U+00A0 since #1971).
_TIME_LINE_RE = re.compile(
    rf"Heute|Seit{_NBSP}heute|Seit{_NBSP}\d{{2}}:\d{{2}}|Seit{_NBSP}{_DAY}"
    rf"|Bis{_NBSP}{_DAY}|Am{_NBSP}{_DAY}|Ab{_NBSP}{_DAY}(?:{_NBSP}bis{_NBSP}{_DAY})?"
)
_TIME_LINE_AT_END_RE = re.compile(r"\s*\[([^\[\]]*)\]\s*$")
_SINCE_CLOCK_RE = re.compile(r"Seit\s(\d{2}):(\d{2})")
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})?")
_EXPECTED_UNTIL_RE = re.compile(r"(?:^|[.!?]\s)Voraussichtlich bis (\d{1,2})[:.](\d{2})")
_LINE_PREFIX_RE = re.compile(r"^([A-Z0-9]{1,6}(?:/[A-Z0-9]{1,6})*):\s")
_COURTESY = "Wir bitten um Entschuldigung"

# ÖBB: the second sentence says what still applies (#1993). Where it would
# not fit, the first sentence leaves out the stations the title names.
_OEBB_COURTESY_RE = re.compile(
    r"Wir bitten um Entschuldigung|Sobald uns weitere Informationen vorliegen|Details finden Sie hier"
)
_OEBB_DATE_PREFIX_RE = re.compile(r"^\s*\d{2}\.\d{2}\.\d{4}(?:\s*-\s*\d{2}\.\d{2}\.\d{4})?")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])")
_ABBREVIATION_END_RE = re.compile(r"\b(?:St|Nr|ca|bzw|Hbf|Str|Bhf)\.$")
_OEBB_PLACE_RE = re.compile(
    r"\s(?:zwischen\s.+?\sund\s.+?|in\s.+?|im Bereich\s.+?)"
    r"(?=\s(?:bis|derzeit|keine|kein|nur|Zugfahrten|können|sind|waren)\b)"
)
_STATION_WORDS_RE = re.compile(r"\b(?:Bahnhof|Bahnhst|Bhf|Hbf)\b\.?|\((?:U|Wien)\)")


@dataclass(frozen=True)
class Rule:
    """A rule of the German feed and why it matters on the TV."""

    rule_id: str
    title: str
    rank: str
    why: str


RULES: dict[str, Rule] = {
    rule.rule_id: rule
    for rule in (
        Rule(
            "doppelter-eintrag",
            "Doppelter Eintrag",
            "Rang 1",
            "Zwei Einträge mit derselben GUID oder demselben Titel. Auf dem TV verdrängt "
            "jeder doppelte Eintrag eine andere Störung vollständig.",
        ),
        Rule(
            "anzahl",
            "Weniger oder mehr als zehn Einträge",
            "Rang 1",
            "Der TV zeigt zehn Folien. Fehlen Einträge, sind Meldungen verloren gegangen "
            "oder fälschlich verworfen worden.",
        ),
        Rule(
            "alte-meldung-als-neu",
            "Alte Meldung als neue Störung",
            "Rang 1",
            "Eine Meldung, die älter als einen Tag ist, gilt als heute neu und steht als "
            "aktuelle Störung vorne (Linie 25 am 09.10.2026: eine Haltestellen-Anzeige vom "
            "07.10. übernahm GUID und Beginn einer Verspätung und stand bis auf Platz 2).",
        ),
        Rule(
            "seit-zu-frueh",
            "„Seit“-Zeit früher als die Störung",
            "Rang 2",
            "Eine Störung erscheint zum ersten Mal, obwohl ihre „Seit“-Zeit mehr als drei "
            "Stunden zurückliegt und der Feed vorher Platz hatte („2/71: Polizeieinsatz "
            "[Seit 13:08]“ für einen Einsatz ab 20:08 am 09.10.2026).",
        ),
        Rule(
            "oebb-zweiter-satz",
            "ÖBB-Text ohne zweiten Satz",
            "Rang 2",
            "ÖBB sagt im zweiten Satz, was noch gilt. Er fehlt, obwohl er Platz hätte, wenn "
            "der erste Satz die Stationen weglässt, die der Titel schon nennt (#1993).",
        ),
        Rule(
            "zeitzeile",
            "Zeitzeile fehlt oder hat eine unbekannte Form",
            "Rang 2",
            "Jede Beschreibung endet mit einer Zeitzeile in einer der festgelegten Formen "
            "(Heute, Seit, Bis, Am, Ab), Wörter mit geschütztem Leerzeichen verbunden (#1971).",
        ),
        Rule(
            "ende-vorbei",
            "Zeitangabe schon vorbei",
            "Rang 2",
            "Die Zeitzeile nennt ein Ende vor heute, eine „Seit“-Zeit in der Zukunft, oder der "
            "Text sagt „Voraussichtlich bis <Uhrzeit>“, obwohl die Uhrzeit vorbei ist (#1975).",
        ),
        Rule(
            "textlaenge",
            "Beschreibung länger als 180 Zeichen",
            "Rang 2",
            "EasySignage schneidet nach 180 Zeichen ab; der Feed kürzt selbst, damit kein "
            "Satz mitten im Wort endet.",
        ),
        Rule(
            "hoeflichkeitssatz",
            "„Wir bitten um Entschuldigung“ im Text",
            "Rang 2",
            "ÖBBs Höflichkeitssatz sagt am TV nichts und nimmt Platz (#1993).",
        ),
        Rule(
            "quelle-leer",
            "Quelle liefert nichts mehr für den Feed",
            "Quelle",
            "Der Cache eines Anbieters ist leer, obwohl er in den letzten sieben Tagen "
            "regelmäßig Meldungen hatte.",
        ),
        Rule(
            "quelle-einbruch",
            "Quelle liefert deutlich weniger",
            "Quelle",
            "Die Rohantwort eines Anbieters hat weniger als die Hälfte der Meldungen, die sie "
            "in den letzten sieben Tagen üblicherweise hatte.",
        ),
        Rule(
            "verwurf-sprung",
            "Anteil verworfener Meldungen springt",
            "Quelle",
            "Der Anteil der beim Abruf verworfenen Meldungen weicht um mehr als 25 "
            "Prozentpunkte vom Üblichen der letzten sieben Tage ab: Die Quelle hat ihr Format "
            "oder ihren Inhalt geändert, oder ein Filter greift falsch.",
        ),
        Rule(
            "feed-alter",
            "Feed nicht neu gebaut",
            "Betrieb",
            "Der Feed war beim Lauf des Wächters älter als 90 Minuten: Der Lauf hat ihn nicht "
            "neu gebaut.",
        ),
    )
}


@dataclass(frozen=True)
class Item:
    """One ``<item>`` of the German feed, as the TV shows it."""

    title: str
    guid: str
    description: str
    pub_date: datetime | None = None
    first_seen: datetime | None = None
    starts_at: datetime | None = None

    @property
    def time_line(self) -> str | None:
        match = _TIME_LINE_AT_END_RE.search(self.description)
        return match.group(1) if match else None

    @property
    def body(self) -> str:
        return _TIME_LINE_AT_END_RE.sub("", self.description).strip()


@dataclass(frozen=True)
class Feed:
    built: datetime | None
    items: list[Item]


@dataclass(frozen=True)
class Finding:
    rule_id: str
    subject: str
    text: str


@dataclass
class SourceSample:
    """What the providers delivered in this run (counts only)."""

    raw: dict[str, int] = field(default_factory=dict)
    discarded: dict[str, int] = field(default_factory=dict)
    cache: dict[str, int] = field(default_factory=dict)


def _clean(value: object) -> str:
    return str(sanitize_log_arg(str(value)))


def _local(moment: datetime) -> datetime:
    return moment.astimezone(VIENNA)


def _parse_rfc2822(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, IndexError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(content: str) -> Feed:
    """The items of the German feed. Raises ``ET.ParseError`` for invalid XML."""
    root = ET.fromstring(content)
    channel = root.find("channel")
    built = _parse_rfc2822(channel.findtext("lastBuildDate") if channel is not None else None)
    items: list[Item] = []
    for node in root.iter("item"):
        fields = {_local_name(child.tag): (child.text or "").strip() for child in node}
        items.append(
            Item(
                title=fields.get("title", ""),
                guid=fields.get("guid", ""),
                description=fields.get("description", ""),
                pub_date=_parse_rfc2822(fields.get("pubDate")),
                first_seen=_parse_rfc2822(fields.get("first_seen")),
                starts_at=_parse_rfc2822(fields.get("starts_at")),
            )
        )
    return Feed(built, items)


def _normalize(text: str) -> str:
    return " ".join(text.replace(_NBSP, " ").split())


def _line_keys(title: str) -> frozenset[str]:
    """The lines of a title (``2/71: …`` → {2, 71}); the whole title for one without lines."""
    match = _LINE_PREFIX_RE.match(title)
    if match:
        return frozenset(match.group(1).split("/"))
    return frozenset({_normalize(title).casefold()})


def _since_clock(time_line: str | None, built: datetime) -> datetime | None:
    """The moment of a "Seit hh:mm" time line, on the Vienna day of the build."""
    if time_line is None:
        return None
    match = _SINCE_CLOCK_RE.fullmatch(time_line.replace(_NBSP, " "))
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return _local(built).replace(hour=hour, minute=minute, second=0, microsecond=0)


def _is_current(time_line: str | None) -> bool:
    """Whether the time line presents the item as today's disruption."""
    if time_line is None:
        return False
    return time_line == "Heute" or _SINCE_CLOCK_RE.fullmatch(time_line.replace(_NBSP, " ")) is not None


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
        clock = f"{since:%H:%M}"
        still = any(
            keys & set(entry.get("lines") or [])
            and isinstance(entry.get("since"), str)
            and since <= _local(built).replace(
                hour=int(entry["since"][:2]), minute=int(entry["since"][3:5]), second=0, microsecond=0
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
        if since is not None and since > local + timedelta(minutes=1):
            findings.append(
                Finding(
                    "ende-vorbei",
                    item.title,
                    f"[{_normalize(time_line)}] liegt nach dem Bau um {local:%H:%M}",
                )
            )
        if not _is_current(time_line):
            continue
        for match in _EXPECTED_UNTIL_RE.finditer(item.body):
            hour, minute = int(match.group(1)), int(match.group(2))
            if hour > 23 or minute > 59:
                continue
            if local.replace(hour=hour, minute=minute, second=0, microsecond=0) < local - timedelta(minutes=1):
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


# --- source drift ---------------------------------------------------------------


def _count_list(payload: object, *keys: str) -> int | None:
    node: object = payload
    for key in keys:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return len(node) if isinstance(node, list) else None


def read_sources(cache_dir: Path, raw_dir: Path) -> SourceSample:
    """Counts of the provider caches and the raw answers of this run."""
    sample = SourceSample()
    if cache_dir.is_dir():
        for directory in sorted(cache_dir.iterdir()):
            provider = directory.name.split("_", 1)[0]
            if provider not in {"wl", "oebb", "baustellen"}:
                continue
            payload = read_capped_json(directory / "events.json", MAX_SOURCE_BYTES, label="cache", logger=LOGGER)
            if isinstance(payload, list):
                sample.cache[provider] = len(payload)
    raw: dict[str, tuple[str, tuple[str, ...]]] = {
        "wl-stoerungen": ("wl/trafficInfoList.json", ("data", "trafficInfos")),
        "wl-hinweise": ("wl/newsList.json", ("data", "pois")),
        "oebb": ("oebb/rss.json", ("items",)),
        "baustellen-linien": ("baustellen/BAUSTELLENLINOGD.json", ("features",)),
        "baustellen-punkte": ("baustellen/BAUSTELLENPKTOGD.json", ("features",)),
    }
    for name, (relative, keys) in raw.items():
        payload = read_capped_json(raw_dir / relative, MAX_SOURCE_BYTES, label="raw", logger=LOGGER)
        count = _count_list(payload, *keys)
        if count is not None:
            sample.raw[name] = count
    for provider in ("wl", "oebb", "baustellen"):
        payload = read_capped_json(
            raw_dir / provider / "verworfen.json", MAX_SOURCE_BYTES, label="raw", logger=LOGGER
        )
        count = _count_list(payload, "verworfen")
        if count is not None:
            sample.discarded[provider] = count
    return sample


_DISCARD_BASE: dict[str, tuple[str, ...]] = {
    "wl": ("wl-stoerungen", "wl-hinweise"),
    "oebb": ("oebb",),
    "baustellen": ("baustellen-linien", "baustellen-punkte"),
}


def _discard_share(sample: Mapping[str, Any], provider: str) -> float | None:
    raw = sample.get("raw") or {}
    discarded = sample.get("discarded") or {}
    if not isinstance(raw, Mapping) or not isinstance(discarded, Mapping) or provider not in discarded:
        return None
    names = _DISCARD_BASE[provider]
    if not all(name in raw for name in names):
        return None
    total = sum(int(raw[name]) for name in names)
    return int(discarded[provider]) / total if total else None


def check_sources(sample: SourceSample, history: Sequence[Mapping[str, Any]], now: datetime) -> list[Finding]:
    """Compare this run's counts with the samples of the last seven days."""
    reference = [
        entry
        for entry in history
        if isinstance(entry, Mapping)
        and (moment := _parse_iso(entry.get("t"))) is not None
        and now - SAMPLE_WINDOW <= moment < now
    ]
    if len(reference) < MIN_REFERENCE_SAMPLES:
        return []
    findings: list[Finding] = []

    def values(section: str, name: str) -> list[float]:
        out: list[float] = []
        for entry in reference:
            part = entry.get(section)
            if isinstance(part, Mapping) and isinstance(part.get(name), int | float):
                out.append(float(part[name]))
        return out

    for provider, count in sample.cache.items():
        past = values("cache", provider)
        if count == 0 and past and statistics.median(past) >= EMPTY_CACHE_MIN_REFERENCE:
            findings.append(
                Finding(
                    "quelle-leer",
                    provider,
                    f"Cache leer, üblich {statistics.median(past):.0f} Meldungen",
                )
            )
    for name, count in sample.raw.items():
        past = values("raw", name)
        if not past:
            continue
        usual = statistics.median(past)
        if usual >= SOURCE_DROP_MIN_REFERENCE and count < SOURCE_DROP_FACTOR * usual:
            findings.append(
                Finding("quelle-einbruch", name, f"{count} Meldungen, üblich {usual:.0f}")
            )
    current = {"raw": sample.raw, "discarded": sample.discarded}
    for provider in _DISCARD_BASE:
        share = _discard_share(current, provider)
        if share is None:
            continue
        past_shares = [s for entry in reference if (s := _discard_share(entry, provider)) is not None]
        if not past_shares:
            continue
        usual = statistics.median(past_shares)
        if abs(share - usual) > DISCARD_SHARE_JUMP:
            findings.append(
                Finding(
                    "verwurf-sprung",
                    provider,
                    f"{share:.0%} verworfen, üblich {usual:.0%}",
                )
            )
    return findings


# --- one run ---------------------------------------------------------------------


@dataclass
class Result:
    findings: list[Finding]
    state: dict[str, Any]


def evaluate(
    feed: Feed,
    *,
    now: datetime,
    state: Mapping[str, Any],
    oebb_cache: Mapping[str, str],
    sources: SourceSample | None,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> Result:
    """Every finding of this run and the state for the next one. Pure: no I/O."""
    built = feed.built or now
    items = feed.items
    findings: list[Finding] = []
    findings += check_duplicates(items)
    findings += check_count(items, max_items)
    findings += check_old_as_new(items)
    previous = state.get("previous_feed")
    flagged = state.get("seit_zu_frueh")
    since_findings, keep = check_since_too_early(
        items,
        built,
        previous if isinstance(previous, Mapping) else None,
        flagged if isinstance(flagged, list) else [],
    )
    findings += since_findings
    findings += check_oebb_second_sentence(items, oebb_cache)
    findings += check_time_line(items)
    findings += check_past_end(items, built)
    findings += check_length(items)
    findings += check_courtesy(items)
    findings += check_feed_age(feed.built, now)

    history = state.get("samples")
    samples: list[Mapping[str, Any]] = (
        [entry for entry in history if isinstance(entry, Mapping)] if isinstance(history, list) else []
    )
    if sources is not None:
        findings += check_sources(sources, samples, now)
        samples.append(
            {"t": now.isoformat(), "raw": sources.raw, "discarded": sources.discarded, "cache": sources.cache}
        )
    kept = [
        dict(entry)
        for entry in samples
        if (moment := _parse_iso(entry.get("t"))) is not None and now - moment <= SAMPLE_WINDOW
    ][-MAX_SAMPLES:]

    rules_state = _next_rule_state(state.get("rules"), findings, now)
    new_state: dict[str, Any] = {
        "version": 1,
        "description": (
            "Feed-Wächter (scripts/feed_watch.py): the rules the last Update run broke, "
            "with the state the next run needs. A finding is a note; the feed is never "
            "changed. See docs/reference/feed_watch.md."
        ),
        "updated": now.isoformat(),
        "built": feed.built.isoformat() if feed.built else None,
        "findings": [
            {
                "rule": f.rule_id,
                "title": RULES[f.rule_id].title,
                "rank": RULES[f.rule_id].rank,
                "subject": f.subject,
                "text": f.text,
            }
            for f in findings
        ],
        "rules": rules_state,
        "previous_feed": {
            "built": built.isoformat(),
            "items": [{"title": item.title, "time_line": item.time_line or ""} for item in items],
        },
        "seit_zu_frueh": keep,
        "samples": kept,
    }
    return Result(findings, new_state)


def _next_rule_state(previous: object, findings: Sequence[Finding], now: datetime) -> dict[str, Any]:
    """Per broken rule: since when, how many runs it has held since, and its issue.

    A rule leaves the state once it has held in :data:`HELD_RUNS_TO_CLOSE`
    runs in a row and has no open issue; with one, :func:`sync_issues`
    removes it when it closes the issue.
    """
    old = previous if isinstance(previous, Mapping) else {}
    broken = {f.rule_id for f in findings}
    out: dict[str, Any] = {}
    for rule_id in RULES:
        raw = old.get(rule_id)
        entry = dict(raw) if isinstance(raw, Mapping) else {}
        if rule_id in broken:
            entry.setdefault("since", now.isoformat())
            entry["last"] = now.isoformat()
            entry["held_runs"] = 0
        elif entry:
            entry["held_runs"] = int(entry.get("held_runs") or 0) + 1
            if entry["held_runs"] >= HELD_RUNS_TO_CLOSE and not entry.get("issue"):
                continue
        if entry:
            out[rule_id] = entry
    return out


# --- GitHub issues ---------------------------------------------------------------

ISSUE_LABEL = "feed-waechter"
ISSUE_TITLE_PREFIX = "Feed-Wächter: "


class IssueClient:
    """The few GitHub REST calls the watcher needs, through the project's safe HTTP stack."""

    def __init__(self, repository: str, token: str, api_url: str) -> None:
        self.repository = repository
        self.token = token
        self.api_url = api_url.rstrip("/")

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Any:
        from src.utils.files import _reject_non_finite_constant, _reject_non_finite_float
        from src.utils.http import request_safe, session_with_retries, validate_http_url

        url = f"{self.api_url}/repos/{self.repository}{path}"
        if not validate_http_url(url.split("?", 1)[0]):
            raise ValueError("unsafe GitHub API URL")
        with session_with_retries(
            user_agent="wien-oepnv-feed-watch",
            allowed_methods=("GET", "POST", "PATCH"),
            raise_on_status=False,
        ) as session:
            session.headers.update(
                {
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                }
            )
            kwargs: dict[str, Any] = {"json": dict(payload)} if payload is not None else {}
            response = request_safe(session, url, method=method, timeout=10, raise_for_status=False, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"GitHub {method} {path.split('?', 1)[0]}: HTTP {response.status_code}")
        if not response.content:
            return None
        try:
            return response.json(
                parse_constant=_reject_non_finite_constant,
                parse_float=_reject_non_finite_float,
            )
        except (ValueError, RecursionError) as exc:
            raise RuntimeError(f"GitHub {method}: unreadable answer") from exc

    def issues(self) -> list[dict[str, Any]]:
        data = self._request("GET", f"/issues?state=all&labels={ISSUE_LABEL}&per_page=50&sort=updated")
        return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []

    def create(self, title: str, body: str) -> int | None:
        data = self._request("POST", "/issues", {"title": title, "body": body, "labels": [ISSUE_LABEL]})
        number = data.get("number") if isinstance(data, dict) else None
        return number if isinstance(number, int) else None

    def comment(self, number: int, body: str) -> None:
        self._request("POST", f"/issues/{number}/comments", {"body": body})

    def set_state(self, number: int, state: str) -> None:
        payload: dict[str, Any] = {"state": state}
        if state == "closed":
            payload["state_reason"] = "completed"
        self._request("PATCH", f"/issues/{number}", payload)


def issue_client_from_env() -> IssueClient | None:
    """A client when ``FEED_WATCH_ISSUES=1`` and a trusted token and repository are set."""
    from src.feed.reporting import _is_trusted_github_api, _is_valid_github_repo_slug
    from src.utils.env import get_bool_env, read_secret

    if not get_bool_env("FEED_WATCH_ISSUES", False):
        return None
    repository = os.getenv("GITHUB_REPOSITORY") or ""
    token = read_secret("GITHUB_TOKEN")
    api_url = os.getenv("GITHUB_API_URL") or "https://api.github.com"
    if not repository or not token:
        LOGGER.warning("Feed-Wächter: keine Issues, GITHUB_TOKEN oder GITHUB_REPOSITORY fehlt")
        return None
    if not _is_valid_github_repo_slug(repository) or not _is_trusted_github_api(api_url):
        LOGGER.warning("Feed-Wächter: keine Issues, Repository oder API-URL nicht vertrauenswürdig")
        return None
    return IssueClient(repository, token, api_url)


def _run_link() -> str:
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repository = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    if repository and run_id.isdigit():
        return f"{server}/{repository}/actions/runs/{run_id}"
    return ""


def _issue_body(rule: Rule, findings: Sequence[Finding], now: datetime) -> str:
    lines = [
        f"**{rule.title}** ({rule.rank}), festgestellt {_local(now):%d.%m.%Y %H:%M} Wiener Zeit.",
        "",
        rule.why,
        "",
        "Im Lauf gefunden:",
        "",
    ]
    lines += [f"- {f.subject}: {f.text}" for f in findings[:10]]
    if len(findings) > 10:
        lines.append(f"- … und {len(findings) - 10} weitere")
    link = _run_link()
    lines += [
        "",
        "Das ist ein Hinweis, kein Fehler des Laufs: Der Feed wurde wie gebaut veröffentlicht, "
        "der Update-Lauf bleibt grün (Regel „Inhaltsfunde nie rot“, docs/reference/feed_watch.md). "
        f"Dieses Issue schließt sich selbst, sobald die Regel {HELD_RUNS_TO_CLOSE} Läufe in Folge hält.",
    ]
    if link:
        lines += ["", f"Lauf: {link}"]
    return "\n".join(lines)


def sync_issues(client: IssueClient, result: Result, now: datetime) -> None:
    """Open, reopen or close one issue per rule. Errors are logged, never raised."""
    by_rule: dict[str, list[Finding]] = {}
    for finding in result.findings:
        by_rule.setdefault(finding.rule_id, []).append(finding)
    rules_state: dict[str, Any] = result.state["rules"]
    to_open = [r for r in by_rule if not rules_state.get(r, {}).get("issue")]
    to_close = [
        r
        for r, entry in rules_state.items()
        if r not in by_rule and entry.get("issue") and int(entry.get("held_runs") or 0) >= HELD_RUNS_TO_CLOSE
    ]
    if not to_open and not to_close:
        return
    try:
        existing = client.issues() if to_open else []
    except Exception as exc:  # network, permission: retried next run
        LOGGER.warning("Feed-Wächter: Issues nicht lesbar: %s", _clean(exc))
        return
    for rule_id in to_open:
        rule = RULES[rule_id]
        title = f"{ISSUE_TITLE_PREFIX}{rule.title}"
        body = _issue_body(rule, by_rule[rule_id], now)
        try:
            number = _open_or_reopen(client, existing, title, body, now)
        except Exception as exc:
            LOGGER.warning("Feed-Wächter: Issue zu %s nicht angelegt: %s", rule_id, _clean(exc))
            continue
        if number is not None:
            rules_state.setdefault(rule_id, {})["issue"] = number
            LOGGER.info("Feed-Wächter: Issue #%s für %s", number, rule_id)
    for rule_id in to_close:
        entry = rules_state[rule_id]
        number = int(entry["issue"])
        try:
            client.comment(
                number,
                f"Die Regel hält wieder seit {HELD_RUNS_TO_CLOSE} Läufen "
                f"(Stand {_local(now):%d.%m.%Y %H:%M} Wiener Zeit). Automatisch geschlossen.",
            )
            client.set_state(number, "closed")
        except Exception as exc:
            LOGGER.warning("Feed-Wächter: Issue #%s nicht geschlossen: %s", number, _clean(exc))
            continue
        rules_state.pop(rule_id, None)
        LOGGER.info("Feed-Wächter: Issue #%s geschlossen (%s hält wieder)", number, rule_id)


def _open_or_reopen(
    client: IssueClient, existing: Sequence[Mapping[str, Any]], title: str, body: str, now: datetime
) -> int | None:
    same = [entry for entry in existing if entry.get("title") == title and "pull_request" not in entry]
    for entry in same:
        if entry.get("state") == "open" and isinstance(entry.get("number"), int):
            return int(entry["number"])
    for entry in same:
        closed = _parse_iso(str(entry.get("closed_at") or "").replace("Z", "+00:00"))
        if closed is not None and now - closed <= REOPEN_WINDOW and isinstance(entry.get("number"), int):
            number = int(entry["number"])
            client.set_state(number, "open")
            client.comment(number, body)
            return number
    return client.create(title, body)


# --- I/O --------------------------------------------------------------------------


def load_oebb_cache(cache_dir: Path) -> dict[str, str]:
    """GUID → raw description of every ÖBB message in the cache."""
    out: dict[str, str] = {}
    if not cache_dir.is_dir():
        return out
    for directory in sorted(cache_dir.glob("oebb*")):
        payload = read_capped_json(directory / "events.json", MAX_SOURCE_BYTES, label="ÖBB cache", logger=LOGGER)
        for entry in payload if isinstance(payload, list) else []:
            if isinstance(entry, Mapping) and isinstance(entry.get("guid"), str):
                out[entry["guid"]] = str(entry.get("description") or "")
    return out


def load_state(path: Path) -> dict[str, Any]:
    payload = read_capped_json(path, MAX_STATE_BYTES, label="Feed-Wächter", logger=LOGGER)
    return dict(payload) if isinstance(payload, Mapping) else {}


def write_state(path: Path, state: Mapping[str, Any]) -> None:
    scrubbed = scrub_trojan_source_primitives(dict(state))
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        json.dump(scrubbed, handle, ensure_ascii=False, indent=1, allow_nan=False)
        handle.write("\n")


def _report(findings: Sequence[Finding], now: datetime) -> None:
    in_actions = os.getenv("GITHUB_ACTIONS") == "true"
    for finding in findings:
        rule = RULES[finding.rule_id]
        LOGGER.warning("Feed-Wächter %s: %s: %s", finding.rule_id, _clean(finding.subject), _clean(finding.text))
        if in_actions:
            message = f"{finding.subject}: {finding.text}".replace("\n", " ").replace("::", ":")
            print(f"::warning title=Feed-Wächter: {rule.title}::{_clean(message)}")
    if not findings:
        LOGGER.info("Feed-Wächter: alle %d Regeln halten", len(RULES))
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [f"## Feed-Wächter — {_local(now):%d.%m.%Y %H:%M} Wiener Zeit", ""]
    if findings:
        lines += ["| Regel | Rang | Eintrag | Befund |", "| --- | --- | --- | --- |"]
        for finding in findings:
            rule = RULES[finding.rule_id]
            cells = [rule.title, rule.rank, finding.subject, finding.text]
            lines.append("| " + " | ".join(_clean(c).replace("|", "/") for c in cells) + " |")
        lines += ["", "Hinweise, kein Fehler: Der Feed wurde wie gebaut veröffentlicht."]
    else:
        lines.append(f"Alle {len(RULES)} Regeln halten.")
    try:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        # Die Zusammenfassung ist Zierde; Log und ::warning tragen die Funde schon.
        LOGGER.debug("Feed-Wächter: Step-Summary nicht schreibbar: %s", _clean(str(exc)))


def run(args: argparse.Namespace, now: datetime) -> int:
    state_path = validate_path(args.state, "--state")
    content = read_capped_text(args.feed, MAX_FEED_BYTES, label="Feed", logger=LOGGER)
    if content is None:
        raise ValueError("feed missing, unreadable or too large")
    feed = parse_feed(content)
    try:
        from src.feed import config as feed_config

        max_items = int(feed_config.MAX_ITEMS) or DEFAULT_MAX_ITEMS
    except Exception:  # pragma: no cover - config import guard
        max_items = DEFAULT_MAX_ITEMS
    state = load_state(state_path)
    result = evaluate(
        feed,
        now=now,
        state=state,
        oebb_cache=load_oebb_cache(args.cache_dir),
        sources=read_sources(args.cache_dir, args.raw_dir),
        max_items=max_items,
    )
    _report(result.findings, now)
    # Written before the issue calls, so a step killed by its time limit still
    # leaves this run's state; an issue opened then is found again by title.
    write_state(state_path, result.state)
    client = None if args.no_issues else issue_client_from_env()
    if client is not None:
        sync_issues(client, result, now)
        write_state(state_path, result.state)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--feed", type=Path, default=DEFAULT_FEED)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--no-issues", action="store_true", help="never touch GitHub issues")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_script_logging(DEBUG if args.verbose else INFO)
    now = datetime.now(UTC)
    try:
        return run(args, now)
    except (InvalidPathError, Exception) as exc:  # never block the publish
        LOGGER.error("Feed-Wächter konnte nicht prüfen: %s: %s", type(exc).__name__, _clean(exc))
        try:
            write_state(
                validate_path(args.state, "--state"),
                {**load_state(validate_path(args.state, "--state")), "updated": now.isoformat(),
                 "error": f"{type(exc).__name__}: {_clean(exc)}"[:300]},
            )
        except Exception as write_exc:  # pragma: no cover - last resort
            LOGGER.error("Feed-Wächter: Status nicht geschrieben: %s", _clean(write_exc))
        return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())

