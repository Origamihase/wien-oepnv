#!/usr/bin/env python3
"""Check the lines of the German feed against the station directory (report only).

Stage 3 of the line plausibility check (audit 2026-09-25, A.14). For every
item of ``docs/feed.xml`` whose title starts with a line prefix
(``7A/N65/N66: …``) it asks two questions:

1. Does the line exist? Known lines are the Wiener Linien lines
   (``data/wienerlinien-ogd-linien.csv`` and every ``wl_lines`` in
   ``data/stations.json``), the ÖBB lines HAFAS saw
   (``data/oebb_station_lines.json``) and the curated planned lines
   (``data/planned_station_lines.json``). A replacement line ``<line>E``
   counts when ``<line>`` is known (``U6E``).
2. Does it serve the railway stations the item names? Only ÖBB stations
   are checked, because Wiener Linien items routinely name temporary stops
   off the line ("37A: Busse halten Pasettistraße … (bei Linie 5A)"). A
   station's lines are its HAFAS lines, its planned lines, its own
   ``wl_lines`` and the ``wl_lines`` of Wiener Linien stops within 200 m.

Unknown is not wrong. HAFAS shows what currently runs, and a station closed
since before stage 2 first looked shows no train at all (Himberg, the
Stammstrecke). A train line — one that HAFAS or the planned list knows at
any station — is therefore judged at a station only once a train was seen
there, and a station without any known line is not judged at all. Such a
case is "not judged", no finding.

Nothing in the feed is changed or dropped. A finding reads "not
confirmed", never "wrong". Findings go to the log. Findings, "not judged"
cases and expired planned entries whose lines HAFAS still does not show
are also collected in ``data/feed_line_anomalies.json`` for later review,
one record each with the first and last day seen (Europe/Vienna). Makes no
network request.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
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
from src.utils.geo import calculate_distance_meters
from src.utils.logging import sanitize_log_arg
from src.utils.serialize import scrub_trojan_source_primitives

LOGGER = getLogger("feed_line_check")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FEED = REPO_ROOT / "docs" / "feed.xml"
DEFAULT_STATIONS = REPO_ROOT / "data" / "stations.json"
DEFAULT_OEBB_LINES = REPO_ROOT / "data" / "oebb_station_lines.json"
DEFAULT_PLANNED = REPO_ROOT / "data" / "planned_station_lines.json"
DEFAULT_WL_LINES = REPO_ROOT / "data" / "wienerlinien-ogd-linien.csv"
DEFAULT_ANOMALIES = REPO_ROOT / "data" / "feed_line_anomalies.json"
VIENNA = ZoneInfo("Europe/Vienna")

MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_ANOMALY_BYTES = 2 * 1024 * 1024
# The collection keeps the records seen most recently; one record is ~300 bytes.
MAX_ANOMALY_RECORDS = 1000
MAX_RECORD_TEXT = 300
# Wiener Linien stops this close to an ÖBB station count as part of it.
# 200 m, not more: "Hauptbahnhof Ost" lies 354 m from Quartier Belvedere
# and nearer to it than to the Hauptbahnhof.
STATION_RADIUS_M = 200.0
MIN_ALIAS_LENGTH = 4
MAX_ALIAS_WORDS = 6
GENERIC_ALIASES = frozenset({"bahnhof", "hauptbahnhof", "station", "haltestelle", "wien", "vienna"})
# "Richtung Westbahnhof" names where a line is heading, not a stop on it: the
# replay of 678 feed versions (2026-09-26) flagged 43, 44, 64A and 71 for
# their direction only.
DIRECTION_WORDS = frozenset({"richtung", "ri."})

LINE_TOKEN_RE = re.compile(r"^[A-Z0-9]{1,6}$")
_TITLE_PREFIX_RE = re.compile(r"^([A-Z0-9]{1,6}(?:/[A-Z0-9]{1,6})*):\s+(.*)$", re.DOTALL)
_EDGE_PUNCTUATION = ",;:!?()[]{}\"'„“”‚‘’«»"
# ``Wien Meidling Hauptstraße (WL)`` → ``Meidling Hauptstraße``: the form feed
# texts use. Without it "Meidling" alone would match the railway station.
_CORE_NAME_RE = re.compile(r"^(?:wien\s+)?(.*?)(?:\s+\((?:wl|vor)\))?$")


def _clean(value: object) -> str:
    return str(sanitize_log_arg(str(value)))


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _today() -> date:
    return datetime.now(VIENNA).date()


# The kinds of anomaly. ``not_judged`` is no finding: it marks where the
# check is blind, so that the blind spots can be reviewed later too.
UNKNOWN_LINE = "unknown_line"
NOT_CONFIRMED = "not_confirmed"
NOT_JUDGED = "not_judged"
PLANNED_EXPIRED = "planned_expired"
FINDING_KINDS = frozenset({UNKNOWN_LINE, NOT_CONFIRMED})
ANOMALY_KINDS = FINDING_KINDS | {NOT_JUDGED, PLANNED_EXPIRED}


@dataclass(frozen=True)
class Station:
    """An ÖBB station and every line known to serve it."""

    bst_id: str
    name: str
    lines: frozenset[str]
    # The train lines among them (HAFAS and planned); empty while no train
    # was ever seen there.
    rail_lines: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PlannedLines:
    """A curated entry of ``data/planned_station_lines.json``."""

    bst_id: str
    lines: frozenset[str]
    until: date | None
    name: str = ""


@dataclass
class Directory:
    """What the check knows: all lines, and the names that lead to a station."""

    known_lines: frozenset[str]
    # normalized alias → the ÖBB station it names; ``None`` for places that
    # are no railway station (or ambiguous), so they still win a longer match.
    aliases: dict[str, Station | None] = field(default_factory=dict)
    # Every train line HAFAS or the planned list knows, at any station.
    rail_lines: frozenset[str] = frozenset()

    def is_known(self, line: str) -> bool:
        return line in self.known_lines or (line.endswith("E") and line[:-1] in self.known_lines)

    def stations_in(self, text: str) -> list[Station]:
        """The railway stations *text* names as stops, longest name first, each once.

        A name right after "Richtung" is a direction and does not count.
        """
        words = _normalize(text).split()
        found: dict[str, Station] = {}
        index = 0
        while index < len(words):
            size, station = self._match_at(words, index)
            direction = index > 0 and words[index - 1].strip(_EDGE_PUNCTUATION) in DIRECTION_WORDS
            if station is not None and not direction:
                found.setdefault(station.bst_id, station)
            index += size
        return list(found.values())

    def _match_at(self, words: Sequence[str], index: int) -> tuple[int, Station | None]:
        """Words taken by the longest name at *index* (at least one), and its station."""
        for size in range(min(MAX_ALIAS_WORDS, len(words) - index), 0, -1):
            phrase = " ".join(words[index : index + size]).strip(_EDGE_PUNCTUATION)
            for key in (phrase, phrase.rstrip(".")):
                if key in self.aliases:
                    return size, self.aliases[key]
        return 1, None


def parse_planned(payload: object, today: date) -> tuple[list[PlannedLines], list[PlannedLines]]:
    """Valid planned entries in force on *today*, and the expired ones."""
    entries = payload.get("stations") if isinstance(payload, dict) else None
    planned: list[PlannedLines] = []
    expired: list[PlannedLines] = []
    for entry in entries if isinstance(entries, list) else []:
        parsed = _parse_planned_entry(entry)
        if parsed is None:
            LOGGER.warning("Skipping invalid planned-lines entry: %s", _clean(entry)[:200])
            continue
        (expired if parsed.until is not None and parsed.until < today else planned).append(parsed)
    return planned, expired


def _parse_planned_entry(entry: object) -> PlannedLines | None:
    if not isinstance(entry, dict):
        return None
    bst_id, lines, until = entry.get("bst_id"), entry.get("lines"), entry.get("until")
    if not isinstance(bst_id, str) or not bst_id.isdigit():
        return None
    if not isinstance(lines, list) or not lines:
        return None
    if not all(isinstance(line, str) and LINE_TOKEN_RE.match(line) for line in lines):
        return None
    name = str(entry.get("name") or bst_id)
    if until is None:
        return PlannedLines(bst_id, frozenset(lines), None, name)
    if not isinstance(until, str):
        return None
    try:
        return PlannedLines(bst_id, frozenset(lines), date.fromisoformat(until), name)
    except ValueError:
        return None


def hafas_lines(state: object) -> set[str]:
    """The lines of one ``data/oebb_station_lines.json`` entry."""
    seen = state.get("lines") if isinstance(state, dict) else None
    return {line for line in seen if isinstance(line, str)} if isinstance(seen, dict) else set()


def _coordinates(entry: Mapping[str, Any]) -> tuple[float, float] | None:
    lat, lon = entry.get("latitude"), entry.get("longitude")
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    return float(lat), float(lon)


def _wl_lines(entry: Mapping[str, Any]) -> set[str]:
    lines = entry.get("wl_lines")
    return {line for line in lines if isinstance(line, str)} if isinstance(lines, list) else set()


def _aliases(entry: Mapping[str, Any]) -> set[str]:
    raw = [entry.get("name")]
    aliases = entry.get("aliases")
    raw += aliases if isinstance(aliases, list) else []
    names = {_normalize(value) for value in raw if isinstance(value, str)}
    names |= {match.group(1) for name in list(names) if (match := _CORE_NAME_RE.match(name))}
    return {
        name
        for name in names
        if len(name) >= MIN_ALIAS_LENGTH and not name.isdigit() and name not in GENERIC_ALIASES
    }


def _railway_stations(
    entries: Sequence[Mapping[str, Any]],
    oebb_lines: Mapping[str, Any],
    planned: Iterable[PlannedLines],
) -> dict[int, Station]:
    """``{entry index: Station}`` for the ÖBB stations with HAFAS data or planned lines."""
    planned_by_id: dict[str, set[str]] = {}
    for item in planned:
        planned_by_id.setdefault(item.bst_id, set()).update(item.lines)
    wl_stops = [
        (position, _wl_lines(entry))
        for entry in entries
        if not entry.get("bst_id") and (position := _coordinates(entry)) is not None and _wl_lines(entry)
    ]
    stations: dict[int, Station] = {}
    for index, entry in enumerate(entries):
        bst_id = entry.get("bst_id")
        if not isinstance(bst_id, str) or (bst_id not in oebb_lines and bst_id not in planned_by_id):
            continue
        rail = hafas_lines(oebb_lines.get(bst_id)) | planned_by_id.get(bst_id, set())
        lines = rail | _wl_lines(entry)
        position = _coordinates(entry)
        if position is not None:
            for stop, stop_lines in wl_stops:
                if calculate_distance_meters(*position, *stop) <= STATION_RADIUS_M:
                    lines |= stop_lines
        stations[index] = Station(bst_id, str(entry.get("name", bst_id)), frozenset(lines), frozenset(rail))
    return stations


def _nearest_station(
    position: tuple[float, float] | None, entries: Sequence[Mapping[str, Any]], stations: Mapping[int, Station]
) -> Station | None:
    if position is None:
        return None
    best: tuple[float, Station] | None = None
    for index, station in stations.items():
        other = _coordinates(entries[index])
        if other is None:
            continue
        distance = calculate_distance_meters(*position, *other)
        if distance <= STATION_RADIUS_M and (best is None or distance < best[0]):
            best = (distance, station)
    return best[1] if best else None


def build_directory(
    entries: Sequence[Mapping[str, Any]],
    oebb_lines: Mapping[str, Any],
    planned: Sequence[PlannedLines],
    wl_line_names: Iterable[str],
) -> Directory:
    """Combine the station directory, HAFAS lines, planned lines and WL lines."""
    stations = _railway_stations(entries, oebb_lines, planned)
    rail: set[str] = set()
    for state in oebb_lines.values():
        rail |= hafas_lines(state)
    for item in planned:
        rail |= item.lines
    known = set(wl_line_names) | rail
    for station in stations.values():
        known |= station.lines
    for entry in entries:
        known |= _wl_lines(entry)

    targets: dict[str, set[str | None]] = {}
    by_id: dict[str, Station] = {station.bst_id: station for station in stations.values()}
    for index, entry in enumerate(entries):
        target = stations.get(index)
        if target is None and not entry.get("bst_id"):
            target = _nearest_station(_coordinates(entry), entries, stations)
        for alias in _aliases(entry):
            targets.setdefault(alias, set()).add(target.bst_id if target is not None else None)

    directory = Directory(
        frozenset(line for line in known if LINE_TOKEN_RE.match(line)),
        rail_lines=frozenset(rail),
    )
    for alias, ids in targets.items():
        # One railway station, or none: ambiguous names are not judged.
        only = next(iter(ids)) if len(ids) == 1 else None
        directory.aliases[alias] = by_id[only] if only is not None else None
    return directory


def load_wl_line_names(path: Path) -> set[str]:
    """``LineText`` of the pinned Wiener Linien OGD line list; empty if unreadable."""
    content = read_capped_text(path, MAX_CSV_BYTES, label="WL line list", logger=LOGGER)
    if content is None:
        return set()
    try:
        return {
            row["LineText"].strip()
            for row in csv.DictReader(io.StringIO(content, newline=""), delimiter=";")
            if isinstance(row.get("LineText"), str) and row["LineText"].strip()
        }
    except csv.Error as exc:
        LOGGER.warning("Cannot read %s: %s", _clean(path.name), _clean(type(exc).__name__))
        return set()


@dataclass(frozen=True)
class FeedItem:
    title: str
    description: str


def load_feed_items(path: Path) -> list[FeedItem]:
    """Title and plain-text description of every ``<item>`` in *path*.

    Raises ``ValueError`` when the feed is missing, too large or not XML.
    """
    content = read_capped_text(path, MAX_FEED_BYTES, label="Feed", logger=LOGGER)
    if content is None:
        raise ValueError("feed missing, unreadable or too large")
    root = ET.fromstring(content)
    return [
        FeedItem((item.findtext("title") or "").strip(), (item.findtext("description") or "").strip())
        for item in root.iter("item")
    ]


def split_title(title: str) -> tuple[list[str], str]:
    """``7A/N65/N66: Arthaberplatz`` → ``(["7A", "N65", "N66"], "Arthaberplatz")``."""
    match = _TITLE_PREFIX_RE.match(title.strip())
    if not match:
        return [], title.strip()
    return match.group(1).split("/"), match.group(2)


@dataclass(frozen=True)
class Finding:
    """What the check noticed; *kind* is one of :data:`ANOMALY_KINDS`.

    *subject* is the line or station it is about; together with *kind* and
    *title* it names the record in the collection.
    """

    title: str
    text: str
    kind: str
    subject: str

    @property
    def is_finding(self) -> bool:
        return self.kind in FINDING_KINDS


def check_item(item: FeedItem, directory: Directory) -> list[Finding]:
    """What the check notices about one item; nothing without a line prefix."""
    lines, rest = split_title(item.title)
    if not lines:
        return []
    findings = [
        Finding(item.title, f"line {line} is in no directory", UNKNOWN_LINE, line)
        for line in lines
        if not directory.is_known(line)
    ]
    for station in directory.stations_in(f"{rest} {item.description}"):
        finding = _judge(item.title, lines, station, directory)
        if finding is not None:
            findings.append(finding)
    return findings


def _judge(title: str, lines: Sequence[str], station: Station, directory: Directory) -> Finding | None:
    """Whether *lines* serve *station*: confirmed (``None``), not confirmed or not judged."""
    if station.lines.intersection(lines):
        return None
    joined = "/".join(lines)
    if not station.lines:
        return Finding(title, f"{joined} at {station.name} not judged (no line known there)", NOT_JUDGED, station.name)
    if not station.rail_lines and all(line in directory.rail_lines for line in lines):
        return Finding(title, f"{joined} at {station.name} not judged (no train seen there)", NOT_JUDGED, station.name)
    return Finding(
        title,
        f"{joined} at {station.name} not confirmed (known there: {', '.join(sorted(station.lines))})",
        NOT_CONFIRMED,
        station.name,
    )


def expired_findings(expired: Iterable[PlannedLines], oebb_lines: Mapping[str, Any]) -> list[Finding]:
    """Expired planned entries whose lines HAFAS still does not show at the station.

    An entry whose lines HAFAS shows again needs no review and yields nothing.
    """
    findings = []
    for item in expired:
        missing = item.lines - hafas_lines(oebb_lines.get(item.bst_id))
        if missing:
            text = (
                f"planned {', '.join(sorted(item.lines))} expired on {item.until}; "
                f"HAFAS shows no {', '.join(sorted(missing))} there"
            )
            findings.append(Finding("", text, PLANNED_EXPIRED, item.name))
    return findings


def _record_key(record: object) -> tuple[str, str, str] | None:
    """``(kind, subject, title)`` of a valid collection record, else ``None``."""
    if not isinstance(record, dict):
        return None
    kind, subject, title = record.get("kind"), record.get("subject"), record.get("title")
    days = record.get("days_seen")
    if kind not in ANOMALY_KINDS or not isinstance(subject, str) or not isinstance(title, str):
        return None
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        return None
    if not _is_day(record.get("first_seen")) or not _is_day(record.get("last_seen")):
        return None
    return str(kind), subject, title


def _is_day(value: object) -> bool:
    """``YYYY-MM-DD`` exactly: the form the collection writes and compares."""
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _text(value: str) -> str:
    # Scrubbed as the writer scrubs, so a record read back keeps its key.
    return str(scrub_trojan_source_primitives(value))[:MAX_RECORD_TEXT]


def merge_anomalies(
    records: Iterable[object], findings: Iterable[Finding], today: date
) -> list[dict[str, Any]]:
    """Add today's *findings* to the collection *records*.

    A new anomaly gets a record; a known one moves its ``last_seen`` to
    *today* and counts one more day. Invalid records are dropped; beyond
    :data:`MAX_ANOMALY_RECORDS` the ones seen longest ago go.
    """
    stamp = today.isoformat()
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        key = _record_key(record)
        if key is not None and isinstance(record, dict):
            merged[key] = dict(record)
    for finding in findings:
        subject, title = _text(finding.subject), _text(finding.title)
        record = merged.setdefault(
            (finding.kind, subject, title),
            {"kind": finding.kind, "subject": subject, "title": title, "first_seen": stamp, "last_seen": stamp, "days_seen": 1},
        )
        if record["last_seen"] < stamp:
            record["last_seen"] = stamp
            record["days_seen"] += 1
        record["detail"] = _text(finding.text)
    kept = sorted(merged.values(), key=lambda r: r["last_seen"], reverse=True)[:MAX_ANOMALY_RECORDS]
    return sorted(kept, key=lambda r: (r["first_seen"], r["kind"], r["subject"], r["title"]))


def load_anomalies(path: Path) -> list[object]:
    """The records of the collection at *path*; empty when missing or invalid."""
    if not path.exists():
        return []
    payload = read_capped_json(path, MAX_ANOMALY_BYTES, label="Line check anomalies", logger=LOGGER)
    records = payload.get("anomalies") if isinstance(payload, dict) else None
    return list(records) if isinstance(records, list) else []


def write_anomalies(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    """Write the collection atomically, in a stable order for small diffs."""
    document = {
        "version": 1,
        "description": (
            "Anomalies of the report-only line check (scripts/check_feed_lines.py), collected for later "
            "review: findings (unknown_line, not_confirmed), blind spots (not_judged) and expired planned "
            "entries whose lines HAFAS still does not show (planned_expired). One record per kind, "
            "subject and feed title; days in Europe/Vienna. The feed is never changed."
        ),
        "anomalies": list(records),
    }
    scrubbed = scrub_trojan_source_primitives(document)
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        json.dump(scrubbed, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def record_anomalies(path: Path, findings: Sequence[Finding], today: date) -> int:
    """Merge *findings* into the collection at *path*; the number of new records."""
    before = load_anomalies(path)
    known = {key for key in map(_record_key, before) if key is not None}
    merged = merge_anomalies(before, findings, today)
    if merged != before or not path.exists():
        write_anomalies(path, merged)
    return sum(1 for record in merged if _record_key(record) not in known)


def _report_expired(expired: Sequence[PlannedLines], oebb_lines: Mapping[str, Any]) -> list[Finding]:
    """Rule 3: warn about an expired planned entry only while HAFAS still lacks its lines."""
    findings = expired_findings(expired, oebb_lines)
    for finding in findings:
        LOGGER.warning(
            "Planned lines for %s: %s; review data/planned_station_lines.json",
            _clean(finding.subject),
            _clean(finding.text),
        )
    reviewed = {finding.subject for finding in findings}
    for item in expired:
        if item.name not in reviewed:
            LOGGER.info("Planned lines for %s have expired; HAFAS shows them again", _clean(item.name))
    return findings


def _check_items(items: Sequence[FeedItem], directory: Directory) -> tuple[int, list[Finding]]:
    """The number of items with a line prefix, and what the check noticed about them."""
    checked = 0
    noticed: list[Finding] = []
    for item in items:
        if split_title(item.title)[0]:
            checked += 1
        for finding in check_item(item, directory):
            noticed.append(finding)
            LOGGER.info("Line check: %s: %s", _clean(finding.title), _clean(finding.text))
    return checked, noticed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--feed", type=Path, default=DEFAULT_FEED)
    parser.add_argument("--stations", type=Path, default=DEFAULT_STATIONS)
    parser.add_argument("--oebb-lines", type=Path, default=DEFAULT_OEBB_LINES)
    parser.add_argument("--planned", type=Path, default=DEFAULT_PLANNED)
    parser.add_argument("--wl-lines", type=Path, default=DEFAULT_WL_LINES)
    parser.add_argument("--anomalies", type=Path, default=DEFAULT_ANOMALIES, help="collection to update")
    parser.add_argument("--no-record", action="store_true", help="log only, leave the collection unchanged")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_script_logging(DEBUG if args.verbose else INFO)

    try:
        anomalies = None if args.no_record else validate_path(args.anomalies, "--anomalies")
    except InvalidPathError as exc:
        LOGGER.error("%s", _clean(exc))
        return 1
    try:
        items = load_feed_items(args.feed)
    except (OSError, ValueError, ET.ParseError) as exc:
        LOGGER.error("Cannot read the feed %s: %s", _clean(args.feed.name), _clean(type(exc).__name__))
        return 1
    directory_payload = read_capped_json(args.stations, label="Stations", logger=LOGGER)
    entries = directory_payload.get("stations") if isinstance(directory_payload, dict) else None
    if not isinstance(entries, list):
        LOGGER.error("Station directory missing or invalid")
        return 1
    oebb_payload = read_capped_json(args.oebb_lines, label="ÖBB station lines", logger=LOGGER)
    oebb_lines = oebb_payload.get("stations") if isinstance(oebb_payload, dict) else None
    oebb_lines = oebb_lines if isinstance(oebb_lines, dict) else {}
    today = _today()
    planned, expired = parse_planned(
        read_capped_json(args.planned, label="Planned station lines", logger=LOGGER), today
    )
    noticed = _report_expired(expired, oebb_lines)

    directory = build_directory(
        [entry for entry in entries if isinstance(entry, dict)],
        oebb_lines,
        planned,
        load_wl_line_names(args.wl_lines),
    )
    checked, item_notes = _check_items(items, directory)
    noticed += item_notes
    LOGGER.info(
        "Line check: %d items, %d with a line prefix, %d findings, %d not judged "
        "(report only, the feed is unchanged)",
        len(items),
        checked,
        sum(1 for finding in item_notes if finding.is_finding),
        sum(1 for finding in item_notes if finding.kind == NOT_JUDGED),
    )
    if anomalies is None:
        return 0
    try:
        new = record_anomalies(anomalies, noticed, today)
    except (OSError, ValueError) as exc:
        LOGGER.error("Cannot update %s: %s", _clean(anomalies.name), _clean(type(exc).__name__))
        return 1
    LOGGER.info("Line check: %d new anomalies, collected in %s", new, _clean(anomalies.name))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
