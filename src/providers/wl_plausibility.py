"""Plausibility check for when a Wiener-Linien item applies.

WL states the validity of a notice up to three times: in ``time.start`` /
``time.end``, as an ``ab``/``am`` date in the title, and in the "Zeitraum:"
section of the description. A typo usually sits in one of them only. On
2026-10-02 the title "47B: Laufveranstaltung am 04.10.2027" stood beside
``time.end`` 04.10.2026 13:00 and the text "Sonntag, 4. Oktober 2026"; the
title alone moved the start a year past the end, and the German feed read
"[Ab 04.10.2027]" for a run that Sunday.

The rules, in this order:

1. **Majority on a contradiction.** A title date and a text date with the
   same day and month but different years disagree on the year only; the
   year nearer the publication wins (the publication is the third voice).
2. **Hard limits.** A begin date past the end is outvoted by the end. A
   begin date more than :data:`MAX_LEAD_DAYS` after the publication counts
   only when another source names it too (the other date, or an end at or
   after it).
3. **Conservative when unresolved.** What remains falls back to WL's own
   ``time.start``. An item is never hidden because its sources disagree.
4. **Every correction is recorded.** :func:`plausible_start` returns the
   corrections; :func:`note_corrections` logs them and collects them for
   ``scripts/update_wl_cache.py``, which keeps them in
   ``data/wl_plausibility_anomalies.json``. Nothing is changed silently.

The established rules stay as they were and are no correction: an 11:11
end gives way to the end "Zeitraum:" names, or to the one its duration
gives (:func:`plausible_end`), a begin date only moves the start later, and
a text date past the end is not the start but a later phase's date. An
11:11 end the text backs with neither an end nor a duration is not shown
(:func:`end_unknown`).

What this cannot see: a date that is equally wrong in every source.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..utils.files import atomic_write, read_capped_json
from ..utils.logging import sanitize_log_arg
from ..utils.serialize import scrub_trojan_source_primitives
from .wl_text import (
    extract_date_from_title,
    extract_duration_from_description,
    extract_end_from_description,
    extract_start_from_description,
)

log = logging.getLogger(__name__)

_VIENNA_TZ = ZoneInfo("Europe/Vienna")

# WL closes 23 of 34 notices (2026-10-01) at exactly 11:11, mostly a year
# after publication or on 11.11. — an end set by hand, not a date that was
# known. "44A: Kurzführung" ended "Ende September 2026" by its text and by
# its ``time.end`` on 22.07.2027, so the finished notice stayed a candidate
# for the ten feed slots.
PLACEHOLDER_END = (11, 11)

# How far ahead of its publication a measure may begin on one source's word.
# In the WL cache of 2026-10-02 the longest real lead is the Stammstrecke
# phase 2 (published 10.06., begins 07.09.): 88 days. The next is 370 days,
# the 47B typo above.
MAX_LEAD_DAYS = 365

# The kinds of correction.
YEAR_CONFLICT = "year_conflict"
BEGIN_AFTER_END = "begin_after_end"
UNCONFIRMED_LEAD = "unconfirmed_lead"
SOURCE_START_AFTER_END = "source_start_after_end"
CORRECTION_KINDS = frozenset({YEAR_CONFLICT, BEGIN_AFTER_END, UNCONFIRMED_LEAD, SOURCE_START_AFTER_END})


@dataclass(frozen=True)
class Correction:
    """One contradiction between the sources of a WL item, and what won."""

    kind: str
    detail: str


# "auf Dauer von etwa sechs Wochen" names no end, only a duration from the
# start the text gives. Four notices on 2026-10-02 read so, all with an
# 11:11 end; "65A/66A" (from 12.08., "etwa zwei Wochen") was still listed
# with 31.08.2027. WL writes "etwa", and a measure that runs over must not
# leave the feed while WL still lists it, so the end gets a buffer: half the
# duration, at least a week.
MIN_DURATION_BUFFER = timedelta(days=7)


def _duration_end(desc_raw: str, reference: datetime) -> datetime | None:
    """23:59 Europe/Vienna of the text's start plus its duration and the buffer."""
    duration = extract_duration_from_description(desc_raw)
    if duration is None:
        return None
    begin = extract_start_from_description(desc_raw, reference_date=reference)
    if begin is None:
        return None
    last = (begin + duration + max(MIN_DURATION_BUFFER, duration / 2)).date()
    return datetime(last.year, last.month, last.day, 23, 59, tzinfo=_VIENNA_TZ)


def plausible_end(desc_raw: str, end: datetime | None, start: datetime | None) -> datetime | None:
    """WL's ``time.end``, or the end its "Zeitraum:" gives instead of an 11:11 one.

    Only an end at 11:11 Europe/Vienna gives way; every other end, and an
    open one, stays. A named end takes its place; without one, a duration
    from the text's start plus a buffer (:data:`MIN_DURATION_BUFFER`), but
    only to shorten the end, never to extend it. The text's end counts only
    when it does not lie before the start.

    Once that end has passed, the notice expires even while WL still lists
    it (operator decision 2026-10-09, "Text-Ende beendet"): kept, its own
    text ("bis etwa Ende Juli 2026") would show it had ended. Keeping such
    notices until WL took them off (#1989) was reverted by #1990.
    """
    if end is None:
        return None
    local = end.astimezone(_VIENNA_TZ)
    if (local.hour, local.minute) != PLACEHOLDER_END:
        return end
    reference = start or end
    named = extract_end_from_description(desc_raw, reference_date=reference)
    if named is None:
        estimated = _duration_end(desc_raw, reference)
        named = estimated if estimated is not None and estimated < end else None
    if named is None or (start is not None and named < start):
        return end
    return named


def end_unknown(desc_raw: str, end: datetime | None) -> bool:
    """Whether *end* is WL's 11:11 expiry date and the text names no end of its own.

    Such an end is not shown (operator decision 2026-10-08, "Kein Ende"):
    of the 124 notices with an 11:11 end that WL closed between 21.02. and
    08.10.2026, 122 left before that date, half of them more than 336 days
    early. The date is set by hand, mostly a year after publication, so the
    notice expires; WL never meant it as an end. "72A: Haidestraße S"
    ("Dauer: Ab 08. Oktober 2026, etwa 16:00 Uhr", nothing more) read
    "[Bis 08.10.2027]", "38A: Fernsprechamt Heiligenstadt" ("auf derzeit
    unbekannte Zeit") "[Bis 31.12.2027]".

    An end or a duration in the text keeps the end: :func:`plausible_end`
    has then put the text's end in its place, or kept the 11:11 one because
    the duration lasts longer. The end itself stays the item's expiry, only
    the time line leaves it out.

    A vague end ("bis voraussichtlich Mitte August 2026", "bis etwa
    Frühjahr 2027") names no day and counts as none. Read as the month's
    last day it would also have expired notices WL still listed: "85A:
    Straßenbauarbeiten" ("Mitte Mai") was listed until 02.08.2026.
    """
    if end is None:
        return False
    local = end.astimezone(_VIENNA_TZ)
    if (local.hour, local.minute) != PLACEHOLDER_END:
        return False
    return (
        extract_end_from_description(desc_raw, reference_date=end) is None
        and extract_duration_from_description(desc_raw) is None
    )


def _day(value: datetime) -> str:
    return f"{value.astimezone(_VIENNA_TZ):%d.%m.%Y}"


def _settle_year(
    titled: datetime | None, texted: datetime | None, reference: datetime
) -> tuple[datetime | None, datetime | None, list[Correction]]:
    """Rule 1: title and text that differ in the year only agree on the nearer one."""
    if titled is None or texted is None or titled == texted:
        return titled, texted, []
    if (titled.month, titled.day) != (texted.month, texted.day):
        return titled, texted, []
    winner = min((titled, texted), key=lambda when: (abs((when - reference).total_seconds()), when))
    correction = Correction(
        YEAR_CONFLICT,
        f"Titel {_day(titled)}, Zeitraum {_day(texted)}; gilt {_day(winner)} (näher an der Veröffentlichung)",
    )
    return winner, winner, [correction]


def _confirmed(named: datetime, other: datetime | None, end: datetime | None) -> bool:
    """Whether a second source supports the begin date *named*."""
    return (other is not None and other.date() == named.date()) or (end is not None and end >= named)


def plausible_start(
    title_raw: str,
    desc_raw: str,
    start: datetime | None,
    end: datetime | None,
    now: datetime,
) -> tuple[datetime | None, list[Correction]]:
    """When the measure of a WL item begins, for ``starts_at``, and what was corrected.

    WL's ``time.start`` is when a message is published and valid, not when
    the measure begins. The title (``ab``/``am`` a date) names the begin,
    else the "Zeitraum:" section of the description. Either only moves the
    start later, never earlier: an earlier date is a phase already running.
    A date from the description that lies past the end is not taken (the
    first date behind the heading is then not the start). Contradictions
    between the sources follow the module's rules. Both dates are anchored
    to Europe/Vienna midnight, so the API start is projected to Vienna
    before comparing calendar days — otherwise the decision drifts by one
    near midnight UTC.
    """
    reference = start or now
    titled = extract_date_from_title(title_raw, reference_date=reference)
    texted = extract_start_from_description(desc_raw, reference_date=reference)
    titled, texted, corrections = _settle_year(titled, texted, reference)
    if titled is not None and end is not None and titled > end:
        corrections.append(Correction(BEGIN_AFTER_END, f"Titel nennt {_day(titled)}, Ende ist {_day(end)}; Titeldatum verworfen"))
        titled = None
    if texted is not None and end is not None and texted > end:
        texted = None  # a later phase's date, not a contradiction
    named, other = (titled, texted) if titled is not None else (texted, None)
    if (
        named is not None
        and start is not None
        and (named - start).days > MAX_LEAD_DAYS
        and not _confirmed(named, other, end)
    ):
        detail = (
            f"Beginn {_day(named)} liegt über {MAX_LEAD_DAYS} Tage nach der Veröffentlichung "
            f"{_day(start)}, keine zweite Angabe bestätigt ihn"
        )
        corrections.append(Correction(UNCONFIRMED_LEAD, detail))
        named = None
    if start is not None and end is not None and start > end:
        corrections.append(Correction(SOURCE_START_AFTER_END, f"time.start {_day(start)} liegt nach time.end {_day(end)}"))
    if named is None:
        return start, corrections
    if start and named.date() <= start.astimezone(_VIENNA_TZ).date():
        return start, corrections
    return named, corrections


# --- Collection -------------------------------------------------------------

_collected: list[tuple[str, Correction]] = []


def reset_corrections() -> None:
    """Forget what earlier fetches collected (called at the start of a fetch)."""
    _collected.clear()


def note_corrections(title: str, corrections: Iterable[Correction]) -> None:
    """Log each correction of the item *title* and keep it for the collection."""
    for correction in corrections:
        log.warning(
            "WL-Plausibilität (%s): %s – %s",
            correction.kind,
            sanitize_log_arg(title),
            sanitize_log_arg(correction.detail),
        )
        _collected.append((title, correction))


def collected_corrections() -> list[tuple[str, Correction]]:
    """The corrections noted since the last :func:`reset_corrections`."""
    return list(_collected)


MAX_ANOMALY_BYTES = 1024 * 1024
MAX_ANOMALY_RECORDS = 500
MAX_RECORD_TEXT = 300


def _is_day(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _record_key(record: object) -> tuple[str, str] | None:
    """``(kind, title)`` of a valid collection record, else ``None``."""
    if not isinstance(record, dict):
        return None
    kind, title, days = record.get("kind"), record.get("title"), record.get("days_seen")
    if kind not in CORRECTION_KINDS or not isinstance(title, str):
        return None
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        return None
    if not _is_day(record.get("first_seen")) or not _is_day(record.get("last_seen")):
        return None
    return str(kind), title


def _text(value: str) -> str:
    # Scrubbed as the writer scrubs, so a record read back keeps its key.
    return str(scrub_trojan_source_primitives(value))[:MAX_RECORD_TEXT]


def merge_corrections(
    records: Iterable[object], corrections: Iterable[tuple[str, Correction]], today: date
) -> list[dict[str, Any]]:
    """Add today's *corrections* to the collection *records*.

    One record per kind and WL title; a known one moves its ``last_seen``
    to *today* and counts one more day. Invalid records are dropped; beyond
    :data:`MAX_ANOMALY_RECORDS` the ones seen longest ago go.
    """
    stamp = today.isoformat()
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        key = _record_key(record)
        if key is not None and isinstance(record, dict):
            merged[key] = dict(record)
    for title, correction in corrections:
        text = _text(title)
        record = merged.setdefault(
            (correction.kind, text),
            {"kind": correction.kind, "title": text, "first_seen": stamp, "last_seen": stamp, "days_seen": 1},
        )
        if record["last_seen"] < stamp:
            record["last_seen"] = stamp
            record["days_seen"] += 1
        record["detail"] = _text(correction.detail)
    kept = sorted(merged.values(), key=lambda r: r["last_seen"], reverse=True)[:MAX_ANOMALY_RECORDS]
    return sorted(kept, key=lambda r: (r["first_seen"], r["kind"], r["title"]))


def load_collection(path: Path) -> list[object]:
    """The records of the collection at *path*; empty when missing or invalid."""
    if not path.exists():
        return []
    payload = read_capped_json(path, MAX_ANOMALY_BYTES, label="WL plausibility anomalies", logger=log)
    records = payload.get("anomalies") if isinstance(payload, dict) else None
    return list(records) if isinstance(records, list) else []


def write_collection(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    """Write the collection atomically, in a stable order for small diffs."""
    document = {
        "version": 1,
        "description": (
            "Contradictions between the sources of a Wiener-Linien item (time fields, title date, "
            "'Zeitraum:' text) and how src/providers/wl_plausibility.py settled them: year_conflict, "
            "begin_after_end, unconfirmed_lead, source_start_after_end. One record per kind and WL "
            "title; days in Europe/Vienna."
        ),
        "anomalies": list(records),
    }
    scrubbed = scrub_trojan_source_primitives(document)
    with atomic_write(path, mode="w", encoding="utf-8", permissions=0o644) as handle:
        json.dump(scrubbed, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def record_corrections(path: Path, corrections: Sequence[tuple[str, Correction]], today: date) -> int:
    """Merge *corrections* into the collection at *path*; the number of new records.

    The file is only written when there is something to merge or it exists,
    so a quiet day leaves no new file behind.
    """
    if not corrections and not path.exists():
        return 0
    before = load_collection(path)
    known = {key for key in map(_record_key, before) if key is not None}
    merged = merge_corrections(before, corrections, today)
    write_collection(path, merged)
    return sum(1 for record in merged if (record["kind"], record["title"]) not in known)


__all__ = [
    "BEGIN_AFTER_END",
    "CORRECTION_KINDS",
    "Correction",
    "MAX_LEAD_DAYS",
    "MIN_DURATION_BUFFER",
    "PLACEHOLDER_END",
    "SOURCE_START_AFTER_END",
    "UNCONFIRMED_LEAD",
    "YEAR_CONFLICT",
    "collected_corrections",
    "merge_corrections",
    "note_corrections",
    "plausible_end",
    "plausible_start",
    "record_corrections",
    "reset_corrections",
]
