"""Feed-Wächter: logger, thresholds, rule catalogue, data model and parsing helpers.

Moved verbatim from ``scripts/feed_watch.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from logging import getLogger
from zoneinfo import ZoneInfo

from defusedxml import ElementTree as ET

from src.utils.logging import sanitize_log_arg

LOGGER = getLogger("feed_watch")

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
# How far before an item's newest time "Voraussichtlich bis <Uhrzeit>" may
# lie and still mean that day (``_EXPECTED_END_BEFORE_MESSAGE`` in the builder).
EXPECTED_END_BEFORE_MESSAGE = timedelta(hours=3)
# The previous build counts as "the screen before" only when it is this recent.
PREVIOUS_MAX_GAP = timedelta(hours=2)
# A message older than a day that the feed counts as first seen today has
# taken over the identity of another one (line 25 on 2026-10-09: a stop
# ticker from 07.10. with the GUID and first_seen of a delay from 09:36).
OLD_AS_NEW = timedelta(hours=24)
# How long after its first sighting the feed counts an item as new: the
# builder's ``_CURRENT_INCIDENT_WINDOW`` (tests/test_feed_watch pins the two
# together). An item first seen longer ago does not lead the feed as a new
# disruption, however old its message is: "R95: Wien Hauptbahnhof ↔
# Felixdorf" (works on single days from 10.02. to 10.11.2026, in the feed
# since 24.05.) read "[Heute]" on its last day, and that is what it was.
NEW_IN_FEED = timedelta(hours=24)
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

# Source without a fresh answer: the feed has been built from a source's last
# good answer for this long. From 2026-10-01 to 2026-10-23 (1,087 runs with a
# fetch) every outage of WL, ÖBB and Baustellen lasted at most 1 h except WL on
# 2026-10-21 (502 Bad Gateway from 16:00 to 23:00, Health check red only once,
# at 18:49); the VAO poll never went longer than 1 h without a success except
# the same evening (17:31-20:01). Two hours stay silent on every short outage
# and speak at 18:00 (WL) and 19:01 (VAO) on 2026-10-21.
SOURCE_STALE = timedelta(hours=2)
# A version change of the VAO API is reported in the run that made it and the
# next one (two Update runs), so the issue opens once and closes itself.
VAO_VERSION_REPORT = timedelta(minutes=45)
# The free sources whose fetch exit code the Update run hands over
# (``FEED_WATCH_FETCH_EXIT``); 0 = fresh answer, anything else = the feed keeps
# the last good answer (1, 2, 3, 124, 137: see the fetch step in update-cycle.yml).
FETCH_SOURCES = ("wl", "oebb", "baustellen")
SOURCE_LABELS = {
    "wl": "Wiener Linien",
    "oebb": "ÖBB",
    "baustellen": "Baustellen (Stadt Wien)",
    "stammstrecke": "Stammstrecke (VAO)",
}

_NBSP = "\u00a0"
_TIME_LINE_AT_END_RE = re.compile(r"\s*\[([^\[\]]*)\]\s*$")
_SINCE_CLOCK_RE = re.compile(r"Seit\s(\d{2}):(\d{2})")
_LINE_PREFIX_RE = re.compile(r"^([A-Z0-9]{1,6}(?:/[A-Z0-9]{1,6})*):\s")


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
            "meldung-uebersprungen",
            "Meldung beim Bau übersprungen",
            "Rang 1",
            "Ein Schritt der Feed-Kette ist an einer Meldung gescheitert. Der Bau hat diese "
            "Meldung übersprungen und den Feed mit den übrigen gebaut. Der Fund nennt Quelle, "
            "Titel, Fehlerklasse und Stelle im Code; mehr steht im Protokoll des Laufs. Die "
            "Meldung fehlt im Feed, bis die Ursache behoben ist.",
        ),
        Rule(
            "alte-meldung-als-neu",
            "Alte Meldung als neue Störung",
            "Rang 1",
            "Eine Meldung, die älter als einen Tag ist, gilt als heute neu und steht als "
            "aktuelle Störung vorne (Linie 25 am 09.10.2026: eine Haltestellen-Anzeige vom "
            "07.10. übernahm GUID und Beginn einer Verspätung und stand bis auf Platz 2). "
            "Neu ist ein Eintrag in den ersten 24 Stunden nach seiner ersten Sichtung; ein "
            "länger bekannter Eintrag, der an seinem letzten Tag „[Heute]“ zeigt, ist kein Fund.",
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
            "quelle-ohne-antwort",
            "Quelle ohne frische Antwort",
            "Quelle",
            "Ein Anbieter antwortet seit mindestens zwei Stunden nicht brauchbar; der Feed "
            "läuft mit seiner letzten guten Antwort weiter und zeigt neue oder erledigte "
            "Meldungen nicht (Wiener Linien am 21.10.2026 von 16:00 bis 23:00, 502 Bad Gateway).",
        ),
        Rule(
            "vao-version",
            "VAO-Version gewechselt",
            "Quelle",
            "Die Stammstrecke fragt eine andere VAO-Version als vorher: Die bisherige "
            "antwortete nicht mehr brauchbar oder die VAO listet eine neuere "
            "(automatische Wahl seit 29.10.2026; am 28.10.2026 zog die VAO v1.13.0 zurück). "
            "Nur zur Kenntnis, nichts zu tun.",
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


def _vienna_clock(day: date, hour: int, minute: int, built: datetime) -> datetime:
    """The moment *hour*:*minute* Vienna time on *day*, in UTC.

    In the hour that repeats when summer time ends (25.10.2026, 02:00 to
    03:00 twice) a clock time names two moments; it is the later one that
    is not after the build (the builder's own rule, ``format_local_times``
    in src/build_feed.py, compares moments in UTC). ``replace()`` on the
    build's local time kept the build's pass instead: at 02:01 winter time
    "[Seit 02:26]" (summer time, 35 minutes before) read as 25 minutes
    after the build, and the rule ``ende-vorbei`` fired.
    """
    wall = datetime(day.year, day.month, day.day, hour, minute)
    moments = sorted({wall.replace(tzinfo=VIENNA, fold=fold).astimezone(UTC) for fold in (0, 1)})
    limit = built.astimezone(UTC) + timedelta(minutes=1)
    passed = [moment for moment in moments if moment <= limit]
    return passed[-1] if passed else moments[0]


def _since_clock(time_line: str | None, built: datetime) -> datetime | None:
    """The moment (UTC) of a "Seit hh:mm" time line, on the Vienna day of the build."""
    if time_line is None:
        return None
    match = _SINCE_CLOCK_RE.fullmatch(time_line.replace(_NBSP, " "))
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return _vienna_clock(_local(built).date(), hour, minute, built)


def _is_current(time_line: str | None) -> bool:
    """Whether the time line presents the item as today's disruption."""
    if time_line is None:
        return False
    return time_line == "Heute" or _SINCE_CLOCK_RE.fullmatch(time_line.replace(_NBSP, " ")) is not None
