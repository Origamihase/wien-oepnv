"""ÖBB texts: the routes of a title, the lead section and the second sentence.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from itertools import pairwise
from typing import Any

from ..feed_types import FeedItem
from ..utils.text import html_to_text

from .base import _OEBB_SOURCE, _SENTENCE_SPLIT_RE, _VIENNA_TZ, _WHITESPACE_CLEANUP_RE, _sanitize_text
from .display_titles import _DATE_RANGE_PREFIX_RE, _DATE_SINGLE_PREFIX_RE, _display_title_parts
from .en_templates import _OEBB_SLOT_NOT_A_NAME_RE
from .wl_measures import _WL_DESC_LINIE_PREFIX_RE
from .summary_text import _close_blocks, _truncate_summary_180

__all__ = [
    "_oebb_title_route_first", "_oebb_shown_sentences", "_oebb_lead_section_end", "_end_with_oebb_lead_section",
    "_with_second_sentence",
    "_OEBB_COURTESY_RE", "_oebb_sentence_without_place", "_oebb_first_two_sentences", "_OEBB_TITLE_LINES_RE",
]


# Words of the first measure that must still stand behind the head after the
# 180 cut for the measure block to ride along: "Linie D: Umleitung der Züge
# …" says what happens, "Linie D: …" or "Linie D: Umleitung …" does not.
_MEASURE_MIN_WORDS_SHOWN = 3


def _oebb_route_keys(text: str) -> set[frozenset[str]]:
    """The station pairs of every ``zwischen A und B`` route in *text*.

    ÖBB writes alternatives into one slot ("zwischen Salzburg Hbf und
    Flughafen Wien Bahnhof bzw. Hegyeshalom"); each one is its own pair.
    """
    from ..providers.oebb import _extract_zwischen_routes
    from ..utils.stations import station_named_in_text

    def names(slot: str) -> list[str]:
        infos = (station_named_in_text(part.strip()) for part in re.split(r"\s+bzw\.?\s+", slot))
        return [info.name for info in infos if info is not None]

    return {
        frozenset((a, b))
        for slot_a, slot_b in _extract_zwischen_routes(text)
        for a in names(slot_a)
        for b in names(slot_b)
    }


def _oebb_title_route_keys(title: str) -> set[frozenset[str]]:
    """The station pairs of the ``A ↔ B`` routes in an ÖBB title."""
    from ..utils.stations import station_named_in_text

    rest = _OEBB_TITLE_LINES_RE.sub("", title.strip(), count=1)
    keys: set[frozenset[str]] = set()
    for segment in re.split(r"\s+/\s+", rest):
        infos = [station_named_in_text(part.strip()) for part in segment.split("↔")]
        for info_a, info_b in pairwise(infos):
            if info_a is not None and info_b is not None:
                keys.add(frozenset((info_a.name, info_b.name)))
    return keys


def _oebb_title_route_first(sentences: list[str], title: str, *, oebb: bool) -> list[str]:
    """Lead an ÖBB text with the sentence that names the title's route.

    The provider titles an ÖBB notice with its Wien-relevant route, which
    may come from a later section of the text (``_apply_route_title``),
    while the display shows sentence one. Published 2026-10-17 from the
    ÖBB notice "Salzburg Hbf => Flughafen Wien Bahnhof/Hegyeshalom"::

        Wien Hütteldorf ↔ Wien Hauptbahnhof
        Wegen Bauarbeiten fahren zwischen Salzburg Hbf und Flughafen Wien
        Bahnhof bzw. Hegyeshalom von 10.10.2026 (22:30 Uhr) bis … [Bis Mi 21.10.]

    The title came from "TEILAUSFALL: … kann der Zug RJ 263 zwischen Wien
    Hütteldorf Bahnhof (U) und Wien Hbf (U) nicht fahren.", a sentence the
    TV viewer never saw: title and text named two different routes.

    When sentence one names a route and none of its stations stands in the
    title, the first later sentence naming a title route moves to the
    front; sentence one follows it when both fit
    (:func:`_with_second_sentence`). Sentence one stays first when it names
    no route (it then speaks for every route) or shares a station with the
    title: "zwischen Gänserndorf und Deutsch Wagram" under "Wien Floridsdorf
    ↔ Gänserndorf" is the same works seen from the neighbouring stretch,
    and its first sentence carries the dates the later one lacks.
    """
    if not oebb or len(sentences) < 2:
        return sentences
    title_keys = _oebb_title_route_keys(title)
    first_keys = _oebb_route_keys(sentences[0])
    if not title_keys or not first_keys:
        return sentences
    if set().union(*first_keys) & set().union(*title_keys):
        return sentences
    for index, sentence in enumerate(sentences[1:], start=1):
        if _oebb_route_keys(sentence) & title_keys:
            return [sentence, *sentences[:index], *sentences[index + 1:]]
    return sentences


_OEBB_SECTION_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
# A section whose end is no date: its date range runs on "bis" to anything
# but a date ("von 04.07.2026 (ca. 02:00 Uhr) bis voraussichtlich Ende 2027",
# "bis auf Weiteres"), or it names a start date ("ab 04.07.2026", "seit …")
# and no end. Its last date is where it begins, not where it ends. A "bis"
# date anywhere in the section ends it: a "bis" elsewhere belongs to a
# weekday window ("von 31.10.2026 bis 30.11.2026 (jeweils von Samstag 23:15
# Uhr bis Sonntag 04:40 Uhr …)"), a clock time or a route ("nur bis Wien
# Meidling"), not to the range.
_OEBB_SECTION_OPEN_END_RE = re.compile(
    r"\b\d{1,2}\.\s?\d{1,2}\.(?:\d{4})?\s*(?:\([^)]*\)\s*)?bis\b", re.IGNORECASE
)
_OEBB_SECTION_OPEN_START_RE = re.compile(r"\b(?:ab|seit)\s+(?:dem\s+)?\d{1,2}\.\s?\d{1,2}\.", re.IGNORECASE)
# "bis 19.10.2026", "bis zum 19.10.", "bis voraussichtlich Sonntag, 01.11.2026".
_OEBB_SECTION_BIS_DATE_RE = re.compile(
    r"\bbis\s+(?:[^\W\d]+\.?,?\s+){0,2}\d{1,2}\.\s?\d{1,2}\.", re.IGNORECASE
)


def _oebb_section_end_is_open(section: str) -> bool:
    """Whether *section* ends without a date (see ``_OEBB_SECTION_OPEN_END_RE``)."""
    if _OEBB_SECTION_BIS_DATE_RE.search(section):
        return False
    return bool(_OEBB_SECTION_OPEN_END_RE.search(section) or _OEBB_SECTION_OPEN_START_RE.search(section))


def _oebb_shown_sentences(it: Any) -> tuple[list[str], list[str]]:
    """An ÖBB text's sentences as ÖBB wrote them, and in the order the feed shows them.

    The second list leads with the sentence the display shows first
    (:func:`_oebb_title_route_first`). The end of that section
    (:func:`_oebb_lead_section_end`) and the closure days of the feed order
    (``_is_oebb_closure_day`` in :mod:`.oebb_days`) are read from it.
    """
    raw_title, _ = _display_title_parts(it)
    text, _ = _close_blocks(
        html_to_text(it.get("description") or "", collapse_newlines=True, mark_block_ends=True),
        raw_title,
    )
    text = _sanitize_text(text).strip()
    text = _DATE_SINGLE_PREFIX_RE.sub("", _DATE_RANGE_PREFIX_RE.sub("", text))
    text = _WHITESPACE_CLEANUP_RE.sub(" ", text.replace("•", " ")).strip()
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    return sentences, _oebb_title_route_first(sentences, raw_title, oebb=True)


def _oebb_lead_section_end(it: Any) -> datetime | None:
    """The end of the ÖBB section that leads the shown text, when it ends sooner.

    Operator decision 2026-10-19 (card "Ja, als Regel"): when
    :func:`_oebb_title_route_first` moves a later section to the front
    because it names the title's route, the end marker and the item's end
    follow that section. Published 2026-10-19 from TRACKINFO 901477 (notice
    17.10.–21.10. for works between Salzburg and Flughafen Wien)::

        Wien Hütteldorf ↔ Wien Hauptbahnhof
        Teilausfall: Von 18.10.2026 bis 19.10.2026 kann der Zug RJ 263
        zwischen Wien Hütteldorf Bahnhof (U) und Wien Hbf (U) nicht fahren.
        [Bis Mi 21.10.]

    The text and the marker disagreed, and from 20.10. the slide would
    have shown a cancellation already over. The section's last date counts
    (end of that day, Vienna); it only ever shortens ``ends_at``, never
    extends it, and a section without a date keeps the notice's end.

    So does a section whose end is no date (``_oebb_section_end_is_open``):
    "Von 04.07.2026 (ca. 02:00 Uhr) bis voraussichtlich Ende 2027" or "Ab
    04.07.2026" names only its start, and the start day as the end would
    take a closure of a year off the feed after its first day (independent
    check 2026-10-24; no such section has led an ÖBB text yet).
    """
    if it.get("source") != _OEBB_SOURCE:
        return None
    ends_at = it.get("ends_at")
    if not isinstance(ends_at, datetime):
        return None
    sentences, lead = _oebb_shown_sentences(it)
    if not lead or not sentences or lead[0] == sentences[0]:
        return None
    if _oebb_section_end_is_open(lead[0]):
        return None
    days: list[date] = []
    for day, month, year in _OEBB_SECTION_DATE_RE.findall(lead[0]):
        try:
            days.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    if not days:
        return None
    last = max(days)
    end = datetime(last.year, last.month, last.day, 23, 59, 59, tzinfo=_VIENNA_TZ)
    starts_at = it.get("starts_at")
    if isinstance(starts_at, datetime) and end < starts_at:
        return None
    return end if end < ends_at else None


def _end_with_oebb_lead_section(items: list[FeedItem]) -> None:
    """Shorten ``ends_at`` to the shown ÖBB section (:func:`_oebb_lead_section_end`)."""
    for it in items:
        if isinstance(it, dict):
            end = _oebb_lead_section_end(it)
            if end is not None:
                it["ends_at"] = end


def _with_second_sentence(first: str, second: str, *, oebb: bool, raw_title: str) -> str:
    """The summary's first sentence, with the second one when it may follow.

    The second sentence follows whenever both fit the 180 characters. ÖBB's
    second sentence says what still goes on (``_oebb_first_two_sentences``).

    A WL measure block (see :func:`_prefer_measures`) follows even when it
    does not fit, and ``_truncate_summary_180`` cuts it with an ellipsis:
    the block is joined into one sentence, so as a whole it nearly never
    fits behind a kept sentence, and the 180-check dropped every measure.
    Published 2026-10-13 for the demonstration on the Ring::

        1/71/D: Demonstration am 13.10.2026
        Wegen einer Demonstration kommt es zu einer Einschränkung des
        öffentlichen Verkehrs. [Heute]

    while the text went on "Linie D: Umleitung der Züge zwischen
    Absberggasse und Börse …" (18 texts since July, Jules audit
    2026-10-13). It follows only when the cut still shows the first
    measure's line and :data:`_MEASURE_MIN_WORDS_SHOWN` words of it;
    behind a long first sentence a bare "Linie D: …" says nothing.
    """
    candidate = f"{first} {second}"
    if len(candidate) > 180 and oebb:
        candidate = _oebb_first_two_sentences(first, second, raw_title) or candidate
    if len(candidate) <= 180:
        return candidate
    label = _WL_DESC_LINIE_PREFIX_RE.match(second)
    if label is None:
        return first
    shown = _truncate_summary_180(candidate)[len(first):].removesuffix("…").strip()
    label_shown = _WL_DESC_LINIE_PREFIX_RE.match(shown)
    if label_shown is None or len(shown[label_shown.end():].split()) < _MEASURE_MIN_WORDS_SHOWN:
        return first
    return candidate


# ÖBB says in its second sentence what is still going on: "Planen Sie
# derzeit noch bis zu 10 Minuten mehr Reisezeit ein." under a disruption
# that is over, "Über die Dauer der Unterbrechung kann derzeit noch keine
# Angabe gemacht werden." under one that runs. The German summary keeps two
# sentences within 180 characters, and the first one, with the two stations
# in ÖBB's long form ("zwischen Wien Leopoldau Bahnhst (U) und Wien
# Süßenbrunn Bahnhst"), took 140 of them: in 59 of the 106 ÖBB texts the
# German feed showed since September the second sentence was cut, and no
# text kept it. "Wien Leopoldau ↔ Wien Süßenbrunn" on 09.10.2026 at 20:30
# read "… waren … bis 20:18 Uhr keine Fahrten möglich. [Heute]", as if all
# were over. The title names the stations already, so where the second
# sentence would not fit, the first one leaves out the place it repeats.
# ÖBB's courtesy sentences tell a reader at the display nothing ("Wir
# bitten um Entschuldigung." closes 310 of the 316 ÖBB texts since June) and
# would take that room, so they go first.
_OEBB_COURTESY_RE: re.Pattern[str] = re.compile(
    r"\s*(?:Wir bitten um Entschuldigung|Sobald uns weitere Informationen vorliegen,"
    r" informieren wir Sie|Details finden Sie hier)\s*[.!…]*"
)
_OEBB_PLACE_SUFFIX_RE: re.Pattern[str] = re.compile(
    r"(?:\s+\[[^\]]*\]|\s+\((?:U|Wien)\)|\s+(?:Bahnhof|Bahnhst\.?|Bhf\.?))+$"
)


def _oebb_place_key(name: str) -> str:
    """*name* compared without ÖBB's suffixes, spaces and dots ("Wien Hbf (U)" → "wienhauptbahnhof")."""
    core = _OEBB_PLACE_SUFFIX_RE.sub("", name.strip())
    core = re.sub(r"\bHbf\b\.?", "Hauptbahnhof", core)
    return re.sub(r"[\s.]", "", core).casefold()


def _oebb_title_names(place: str, title: str) -> bool:
    """Whether the ÖBB *title* names the station *place* of a templated sentence."""
    key = _oebb_place_key(place)
    bare = key.removeprefix("wien")
    shown = re.sub(r"[\s.]", "", title).casefold()
    return len(bare) >= 4 and bare in shown


# Where the place stands is a matter of sentence structure, not of one
# template: "Wegen <Ursache> <Verb> … zwischen <A> und <B> | in <A> | im
# Bereich <A> …". Each station runs to ÖBB's suffixes ("Bahnhof", "(U)",
# "(Wien)") or to the first word that opens no name, and the title must name
# all of it. Read from the template's slots instead, a sentence that varies the
# wording behind the second station kept its place, and its second sentence
# went: "zwischen Wien Meidling Bahnhof (U) und Liesing (Wien) Zugfahrten
# voraussichtlich 23:00 Uhr nur eingeschränkt möglich." (no "bis", ÖBB on
# 13.08.2026) put "Zugfahrten voraussichtlich 23:00 Uhr" into the station
# slot, and "Es kommt zu vereinzelten Zugausfällen und Verspätungen bis zu 10
# Minuten." never reached the display. ÖBB's planned works ("Wegen
# Bauarbeiten können zwischen <A> und <B> am 01.11.2026 … keine S45-Züge
# fahren.") are the same sentence and lost "Ein Schienenersatzverkehr mit
# Autobussen wird für Sie eingerichtet." the same way. The place follows the
# main verb, so a place inside the cause ("Wegen eines Polizeieinsatzes in
# Wien Meidling …") stays; a place in a list (", und zwischen …") stays too.
# So does one that a list continues behind it ("zwischen A und B bzw. C und
# D, bis …", "in A und B …") or that another route follows ("zwischen A und B
# der Zug REX6 (7799) zwischen C und D der Zug S60 (26093) …"): without it
# the sentence read "Wegen Verlängerung der Bauarbeiten sind bzw. Wien Hbf
# (U) und Flughafen Wien Bahnhof, bis …" (ÖBB, 07.09.2026, ten characters
# short of the display), or gave the REX 6 the other route.
_OEBB_MAIN_VERB_RE: re.Pattern[str] = re.compile(
    r"Wegen .+? (?:sind|waren|ist|war|können|kann|konnten|fahren|fährt|werden|wird) "
)
_OEBB_PLACE_LEAD_RE: re.Pattern[str] = re.compile(r" (?P<lead>zwischen|im Bereich|in) ")
_OEBB_NAME_SUFFIXES_RE: re.Pattern[str] = re.compile(
    r"(?: (?:Bahnhof|Bahnhst\.?|Bhf\.?|\((?:U|Wien)\)|\[[^\]]*\]))*"
)


def _oebb_station_end(sentence: str, start: int, title: str, *, before_und: bool) -> int | None:
    """End of the station that begins at *start*, when *title* names it, else ``None``.

    The station is the shortest run of words, with ÖBB's suffixes, that " und "
    follows (*before_und*) or, behind it, the rest of the sentence: after a
    suffix any word, without one a word that opens no name ("bis",
    "Zugfahrten", "am 01.11.2026"). "Brunn am Gebirge-Maria Enzersdorf
    Bahnhof" thus stays one name, and a title with "Brunn am Gebirge" does
    not name it.
    """
    for gap in re.finditer(r" |$", sentence[start:]):
        name = sentence[start : start + gap.start()]
        if not name or "," in name or _OEBB_SLOT_NOT_A_NAME_RE.search(name):
            return None
        cut = start + gap.start()
        suffixes = _OEBB_NAME_SUFFIXES_RE.match(sentence, cut)
        end = suffixes.end() if suffixes is not None else cut
        if _oebb_name_ends_at(sentence[end:], suffixed=end > cut, before_und=before_und):
            return end if _oebb_title_names(name, title) else None
    return None


# A word behind a station without suffix that opens no name: the template's
# own words, a date or a clock time ("am 01.11.2026", "bis 20:18 Uhr").
_OEBB_AFTER_NAME_RE: re.Pattern[str] = re.compile(
    r"(?:(?:bis|derzeit|keine?|nur|einige|erneut|noch|Zugfahrten|Fahrten)"
    r"|\S*\d\S*|(?:am|um|von|vom|ab) \S*\d\S*"
    r")(?=[ .,;)]|$)"
)


# Words behind a station that continue a list of places.
_OEBB_PLACE_LIST_RE: re.Pattern[str] = re.compile(r" (?:und|bzw\.?|sowie|oder|u\.) ")


def _oebb_name_ends_at(rest: str, *, suffixed: bool, before_und: bool) -> bool:
    """Whether a station name may end where *rest* begins (see above)."""
    if before_und:
        return rest.startswith(" und ")
    if rest in {"", ".", "!"}:
        return True
    if not rest.startswith(" ") or _OEBB_PLACE_LIST_RE.match(rest):
        return False
    if suffixed:
        return True
    return _OEBB_AFTER_NAME_RE.match(rest, 1) is not None


def _oebb_sentence_without_place(sentence: str, title: str) -> str | None:
    """*sentence* without the place the ÖBB *title* already names, else ``None`` (see above)."""
    verb = _OEBB_MAIN_VERB_RE.match(sentence)
    if verb is None:
        return None
    lead = _OEBB_PLACE_LEAD_RE.search(sentence, verb.end() - 1)
    if lead is None:
        return None
    if lead["lead"] == "zwischen":
        first = _oebb_station_end(sentence, lead.end(), title, before_und=True)
        if first is None:
            return None
        end = _oebb_station_end(sentence, first + len(" und "), title, before_und=False)
    else:
        end = _oebb_station_end(sentence, lead.end(), title, before_und=False)
    if end is None or re.search(r"\bzwischen\b", sentence[end:]):
        return None
    head, rest = sentence[: lead.start()], sentence[end:]
    if head.endswith(":") and rest in {"", ".", "!"}:
        # The place stood alone behind the colon that
        # ``_oebb_restriction_first`` put there: "… keine Fahrten möglich:
        # zwischen A und B." without it reads "… keine Fahrten möglich."
        head = head[:-1]
    return head + rest


# ÖBB ends a sentence after a short word too: "Planen Sie bis zu 15 Minuten
# mehr Reisezeit ein.", "Die Züge warten die Sperre vorerst ab.",
# "… informieren wir Sie." The summary's split wants four letters before the
# full stop (for "Gerasdorf b. Wien", "Bahnhst bzw. Gerasdorf") and took such
# a sentence together with the next one as the second; the two did not fit,
# and the second sentence went with the third (Leopoldau ↔ Süßenbrunn,
# 09.10.2026, 20:00: "… Reisezeit ein. Fahrgäste mit ÖBB-Tickets können …").
# Where that happens, the second sentence ends at the first full stop that
# follows no abbreviation.
_OEBB_SECOND_SENTENCE_END_RE: re.Pattern[str] = re.compile(
    r"(?<=[.!])(?<!\b[A-Za-z]\.)(?<!\bbzw\.)(?<!\bzw\.)(?<!\bca\.)(?<!\bNr\.)(?<!\bSt\.)"
    r"(?<!\bStr\.)(?<!\bWr\.)(?<!\bHbf\.)(?<!\bBhf\.)\s+(?=[A-ZÄÖÜ])"
)


def _oebb_first_two_sentences(first: str, second: str, title: str) -> str | None:
    """ÖBB's first sentence with its *second*, within 180 characters, else ``None``.

    The first sentence leaves out the place the title names only where the
    second would not fit otherwise (operator decision 2026-10-09); *second*
    may hold a third sentence the summary's split did not see, which then
    goes before the place does.
    """
    shorter = _oebb_sentence_without_place(first, title)
    alone = _OEBB_SECOND_SENTENCE_END_RE.split(second, maxsplit=1)[0]
    candidates = [f"{first} {second}"]
    if shorter is not None:
        candidates.append(f"{shorter} {second}")
    if alone != second:
        candidates.append(f"{first} {alone}")
        if shorter is not None:
            candidates.append(f"{shorter} {alone}")
    return next((c for c in candidates if len(c) <= 180), None)


_OEBB_TITLE_LINES_RE = re.compile(
    r"^[A-Z]{1,4}\s?\d{1,3}[A-Z]?(?:\s*/\s*[A-Z]{1,4}\s?\d{1,3}[A-Z]?)*:\s+"
)
