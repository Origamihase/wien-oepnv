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
   A begin date before the publication whose day falls, in a later year,
   shortly after the publication and not after an end that can confirm it
   carries the year of an older notice; that day is the begin
   (:func:`_restated_year`, :func:`_confirming_end`).
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
gives (:func:`plausible_end`), a begin date only moves the start later (a
stale year aside, rule 1), and a text date past the end is not the start
but a later phase's date. An
11:11 end the text backs with neither an end nor a duration is not shown
(:func:`end_unknown`). An end at 23:59 exactly a year after the publication
is the same date set by hand (:func:`placeholder_end`, operator decision
2026-11-06).

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
# The same date set by hand, at the end of the day: "5B: Bauarbeiten",
# published 06.11.2026 14:11 for works "ab Montag, den 9. November 2026 …
# für ca. drei Wochen", ended 06.11.2027 23:59, a year to the day after its
# publication; the feed read "[Ab Mo 09.11. bis 06.11.2027]" for three
# weeks of works. Of the 11 notices with an end at 23:59 in the cache of
# 2026-11-06 it was the only one a year after its publication; the others
# ended within days. Operator decision 2026-11-06 ("Ja, bitte"): such an
# end counts as the 11:11 one.
DAY_END = (23, 59)

# How long before WL's incident message (``I20261010-0007``) a display ticker
# of the same incident may have begun. WL switches the displays on before it
# enters the incident: from 04.10. to 10.10.2026 up to 39 minutes ("9:
# Falschparker", ticker 14:21, incident 15:00), and the ticker's time is the
# true begin. A ticker that began earlier still is one WL rewrote for the new
# incident and that kept the start of the old one: on 09.10. the tickers of a
# demonstration from 13:08 read "Fahrtbehinderung wegen Polizeieinsatz" for
# a police operation from 20:08, and the feed showed "2/71: Polizeieinsatz
# [Seit 13:08]". The 60-minute window is Michael's decision of 11.10.2026
# (decision card, "Fenster 60 Minuten"): it keeps every real ticker lead seen
# and corrects the rewritten ticker; do not change it without asking him.
TICKER_LEAD_MAX = timedelta(minutes=60)


def incident_begin(published: datetime | None, incident_start: datetime | None) -> datetime | None:
    """The earliest ``pubDate`` of an item, unless a ticker dated it before its incident.

    *incident_start* is the earliest start of the item's WL incident
    messages. A ``pubDate`` more than :data:`TICKER_LEAD_MAX` before it
    came from a rewritten ticker and gives way to the incident's start.
    Without an incident message nothing says so, and the ``pubDate`` stays.
    """
    if published is None or incident_start is None:
        return published
    return incident_start if published < incident_start - TICKER_LEAD_MAX else published

# How far ahead of its publication a measure may begin on one source's word.
# In the WL cache of 2026-10-02 the longest real lead is the Stammstrecke
# phase 2 (published 10.06., begins 07.09.): 88 days. The next is 370 days,
# the 47B typo above.
MAX_LEAD_DAYS = 365

# A begin date in a year before its publication is one WL carried over from
# an older notice. "39B: Friedhofsverkehr", published 11.11.2026 15:55 for
# a bus on Christmas Eve, read "Zeitraum: Am 24. Dezember 2024, zwischen
# ca. 8:00 Uhr und 17:00 Uhr" beside ``time.end`` 24.12.2026 17:00; a begin
# date before the publication counted as a phase already running, so the
# publication stayed the start and the feed held the notice from 11.11. on
# among what runs, "[Bis 24.12.]", as if the bus ran then. In the WL data
# since 21.02.2026 (8,826 cache versions, raw answers since 04.10.) four
# notices named a begin 357 to 687 days before their publication, all such
# typos: 5B Muthgasse ("Dienstag, 17. März 2025", published 09.03.2026),
# 53A ("Montag, 15. Juni 2025 … bis Freitag, 19. Juni 2026", published
# 10.06.2026), N25/SEV U1 Kaisermühlen ("Ab 19. Mai 2025", published
# 13.05.2026) and 39B. Every other begin date before its publication lay at
# most 40 days before it, a phase already running ("18: Ende der
# Kurzführung ab 19.12.2025", published 28.01.2026).
#
# The day moves into the first later year in which it is not before the
# publication (that year's weekday is the one 5B and 53A name), when it
# then lies at most this many days after the publication and not after an
# end that can confirm the year (:func:`_confirming_end`: WL's own end or
# the one the text names, never a placeholder or an estimate from a
# duration). WL announced 99 % of the 486 measures that began after their
# publication at most 89 days ahead (Stammstrecke phase 2, 10.06. to
# 07.09.); the four typos 5 to 43 days. The limit keeps a measure that has
# run for months, published anew after New Year with its first year (like
# 18 above), from being taken for one that begins next December. What it
# cannot tell apart is a measure running for 9 to 12 months, published anew
# within this many days of the anniversary of its begin and beside an end
# that confirms; there was none since 21.02.2026. Without such an end (N25:
# 11:11) the date stays unread as before.
#
# Operator decision 2026-11-11 (Michael, "ja, bitte" to: a begin date before
# the publication whose day lies, in another year, between the publication
# and WL's real end takes that year, and the correction is recorded). Added
# in the build: the limit, title dates, and the end the text names in place
# of a placeholder (5B).
STALE_YEAR_MAX_LEAD_DAYS = 90

# The kinds of correction.
YEAR_CONFLICT = "year_conflict"
STALE_YEAR = "stale_year"
BEGIN_AFTER_END = "begin_after_end"
UNCONFIRMED_LEAD = "unconfirmed_lead"
SOURCE_START_AFTER_END = "source_start_after_end"
# In the order the anomaly file names them (:func:`write_collection`).
_KIND_ORDER = (YEAR_CONFLICT, STALE_YEAR, BEGIN_AFTER_END, UNCONFIRMED_LEAD, SOURCE_START_AFTER_END)
CORRECTION_KINDS = frozenset(_KIND_ORDER)


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


def _anniversary(day: date) -> date:
    """*day* a year on; the 29.02. becomes the 28.02."""
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, day=28)


def placeholder_end(end: datetime, published: datetime | None) -> bool:
    """Whether *end* is a date WL set by hand, not one it knew (see :data:`PLACEHOLDER_END`).

    An end at 11:11 Europe/Vienna is one whatever its day; an end at 23:59
    is one when its day lies exactly a year after the day *published* names
    (WL's ``time.start`` of a notice is its publication). Without a
    publication a 23:59 end is an end.
    """
    local = end.astimezone(_VIENNA_TZ)
    when = (local.hour, local.minute)
    if when == PLACEHOLDER_END:
        return True
    if when != DAY_END or published is None:
        return False
    return local.date() == _anniversary(published.astimezone(_VIENNA_TZ).date())


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
    """WL's ``time.end``, or the end its "Zeitraum:" gives instead of a placeholder.

    Only an end WL set by hand gives way (:func:`placeholder_end`: 11:11, or
    23:59 a year after the publication *start*); every other end, and an
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
    if not placeholder_end(end, start):
        return end
    reference = start or end
    named = extract_end_from_description(desc_raw, reference_date=reference)
    if named is None:
        estimated = _duration_end(desc_raw, reference)
        named = estimated if estimated is not None and estimated < end else None
    if named is None or (start is not None and named < start):
        return end
    return named


def end_unknown(desc_raw: str, end: datetime | None, published: datetime | None = None) -> bool:
    """Whether *end* is WL's expiry date set by hand and the text names no end of its own.

    The date is the 11:11 one, or the 23:59 one a year after *published*
    (:func:`placeholder_end`); the time line treats both alike.

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
    if end is None or not placeholder_end(end, published):
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


def _first_day_from(day: date, earliest: date) -> date | None:
    """*day*'s day and month in the first year in which it is not before *earliest*.

    ``None`` when that day exists in neither of the two years in question
    (a 29.02. outside a leap year).
    """
    for year in (earliest.year, earliest.year + 1):
        try:
            candidate = date(year, day.month, day.day)
        except ValueError:
            continue
        if candidate >= earliest:
            return candidate
    return None


def _confirming_end(desc_raw: str, start: datetime | None, end: datetime | None) -> datetime | None:
    """*end* when it can confirm the year of a begin date (:func:`_restated_year`), else ``None``.

    WL's own end counts, and so does the end the "Zeitraum:" names in place
    of a placeholder (:func:`plausible_end`): 5B Muthgasse, "Dienstag, 17.
    März 2025, etwa 08:00 Uhr bis voraussichtlich Juni 2026", published
    09.03.2026 beside 09.03.2027 23:59: the Tuesday it names is 17.03.2026. A
    placeholder (:func:`placeholder_end`) confirms nothing, and neither does
    an end :func:`plausible_end` estimated from the text's duration: that
    end counts from the very begin date in question and so agrees with
    whatever year the date carries. "1A: Habsburgergasse" ("Ab 19. Jänner
    2026 … für etwa ein Jahr", an 11:11 end, estimated end 20.07.2027),
    published anew in December 2026 while it ran, would otherwise have
    waited for 19.01.2027.
    """
    if start is None or end is None or placeholder_end(end, start):
        return None
    if end == _duration_end(desc_raw, start):
        return None
    return end


def _restated_year(
    named: datetime | None, start: datetime | None, end: datetime | None
) -> datetime | None:
    """*named* in the year WL meant, when its own year is a stale one; else ``None``.

    A begin date before the publication *start* whose day, in the first
    later year not before the publication, lies at most
    :data:`STALE_YEAR_MAX_LEAD_DAYS` after it and not after *end*, an end
    that confirms the year (:func:`_confirming_end`). Midnight
    Europe/Vienna of that day, like every begin date. A day that lands on
    the publication day itself would change nothing and gives ``None``.
    """
    if named is None or start is None or end is None:
        return None
    published = start.astimezone(_VIENNA_TZ).date()
    day = named.astimezone(_VIENNA_TZ).date()
    if day >= published:
        return None
    moved = _first_day_from(day, published)
    if (
        moved is None
        or moved == published
        or (moved - published).days > STALE_YEAR_MAX_LEAD_DAYS
        or moved > end.astimezone(_VIENNA_TZ).date()
    ):
        return None
    return datetime(moved.year, moved.month, moved.day, tzinfo=_VIENNA_TZ)


def _restate_stale_years(
    titled: datetime | None,
    texted: datetime | None,
    start: datetime | None,
    end: datetime | None,
) -> tuple[datetime | None, datetime | None, dict[str, Correction]]:
    """Rule 1, second half: a begin date with the year of an older notice takes the year WL meant.

    Title and text are restated each on its own, against *end* as
    :func:`_confirming_end` gives it. The correction of each restated date
    comes back under its source ("Titel", "Zeitraum"); only the one whose
    date becomes the start is recorded (:func:`plausible_start`).
    """
    notes: dict[str, Correction] = {}
    restated: list[datetime | None] = []
    for source, named in (("Titel", titled), ("Zeitraum", texted)):
        moved = _restated_year(named, start, end)
        restated.append(moved or named)
        if moved is None or named is None or start is None or end is None:
            continue
        notes[source] = Correction(
            STALE_YEAR,
            f"{source} {_day(named)} liegt vor der Veröffentlichung {_day(start)}; "
            f"gilt {_day(moved)} (Jahr einer älteren Meldung, Ende {_day(end)})",
        )
    return restated[0], restated[1], notes


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
    start later, never earlier: an earlier date is a phase already running,
    unless its year is one carried over from an older notice
    (:func:`_restated_year`, :data:`STALE_YEAR_MAX_LEAD_DAYS`); that
    correction is recorded only when the restated date becomes the start.
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
    titled, texted, stale = _restate_stale_years(titled, texted, start, _confirming_end(desc_raw, start, end))
    if titled is not None and end is not None and titled > end:
        corrections.append(Correction(BEGIN_AFTER_END, f"Titel nennt {_day(titled)}, Ende ist {_day(end)}; Titeldatum verworfen"))
        titled = None
    if texted is not None and end is not None and texted > end:
        texted = None  # a later phase's date, not a contradiction
    named, other, source = (titled, texted, "Titel") if titled is not None else (texted, None, "Zeitraum")
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
    restated = stale.get(source)
    return named, corrections if restated is None else [*corrections, restated]


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
            "'Zeitraum:' text) and how src/providers/wl_plausibility.py settled them: "
            f"{', '.join(_KIND_ORDER)}. One record per kind and WL title; days in Europe/Vienna."
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
    "DAY_END",
    "MAX_LEAD_DAYS",
    "MIN_DURATION_BUFFER",
    "PLACEHOLDER_END",
    "SOURCE_START_AFTER_END",
    "STALE_YEAR",
    "STALE_YEAR_MAX_LEAD_DAYS",
    "UNCONFIRMED_LEAD",
    "YEAR_CONFLICT",
    "collected_corrections",
    "merge_corrections",
    "note_corrections",
    "placeholder_end",
    "plausible_end",
    "plausible_start",
    "record_corrections",
    "reset_corrections",
]
