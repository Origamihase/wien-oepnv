"""ÖBB summary text: date grammar, trains that cannot run, section labels.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .base import _SENTENCE_SPLIT_RE, _VIENNA_TZ

__all__ = [
    "_OEBB_DATE", "_oebb_upcoming_dates", "_OEBB_DAYS_IN_WORDS", "_oebb_long_date_lists_in_words", "_OEBB_TRAINS",
    "_OEBB_CANNOT_RUN_RE", "_OEBB_TRAINS_RE", "_OEBB_OTHER_VERB_RE", "_OEBB_SOME_DAYS",
    "_oebb_what_does_not_run_first", "_oebb_section_labels_cased", "_OEBB_LABELLED_SENTENCE_RE",
    "_oebb_section_label_en",
]


# ÖBB heads the sections of a long notice in capitals ("TEILAUSFALL:",
# "HALTAUSFALL:", "GEÄNDERTE FAHRZEITEN:", "ACHTUNG:", "HINWEISE:"). Once a
# section leads the shown text (:func:`_oebb_title_route_first`) the label
# stands on the TV; it reads "Teilausfall:" there, operator request
# 2026-10-17 ("„Teilausfall“ gefällt mir besser als „TEILAUSFALL“"). Only a
# capitals run in front of a colon is a label; "ÖBB" or "RJ 263" stay.
_OEBB_SECTION_LABEL_RE = re.compile(r"\b([A-ZÄÖÜ]{4,}(?:[ /-][A-ZÄÖÜ]{2,})*)(?=\s*:)")


# ÖBB sets the days of a notice that recurs as a list, one day or range per
# line ("am 10.02.2026<br>am 10.03.2026<br>…<br>am 13.10.2026 und<br>am
# 10.11.2026 (jeweils 14:00 Uhr - 22:00 Uhr) keine R95-Züge fahren."). The
# line breaks became plain spaces, and the list kept the days that were
# over: on 24.10.2026 the TV read "… am 10.02.2026 am 10.03.2026 am
# 14.04.2026 am 12.05.2026 am 09.06.2026 am 14.07.2026 am 11.08.2026 …" —
# eight past days, and the cut took the one still to come and what does not
# run. 20 ÖBB notices since December 2025 have such a list. A day whose
# last date is over (Vienna) now leaves the list, and the rest are joined as
# a sentence ("am 10.11.2026 (jeweils …) keine R95-Züge fahren."; "von
# 24.10.2026 bis 26.10.2026, von 31.10.2026 bis 01.11.2026 …"). A list on
# one line counts the same: from 26.10.2026 the S2 would have read "am
# 25.10.2026 und 01.11.2026 der S2-Zug 23360 nicht fahren", REX 51 "am
# 29.09.2026 (…) und am 01.10.2026 (…)" on 30.09. (follow-up check
# 2026-10-25). A day without its own "am"/"von" takes the one before it
# when that one leaves ("am 01.11.2026 der S2-Zug …"). A single day or
# range stays, and so does a list none of whose days is over.
_OEBB_DATE = r"\d{2}\.\d{2}\.\d{4}"
_OEBB_DATE_NOTE = r"(?:\s*\([^()]{1,40}\))?"
_OEBB_DATE_DAY = rf"{_OEBB_DATE}{_OEBB_DATE_NOTE}(?:\s+bis\s+{_OEBB_DATE}{_OEBB_DATE_NOTE})?"
_OEBB_DATE_ENTRY = rf"(?:am|von)\s+{_OEBB_DATE_DAY}"
# A list whose line breaks are gone already ("am 19.02.2026 am 19.03.2026")
# is one too: a space before the next "am"/"von".
_OEBB_DATE_SEPARATOR = r"(?:(?:,|\s+und)?\s*•\s*|,\s*(?:und\s+)?|\s+und\s+|\s+(?=(?:am|von)\s))"
_OEBB_DATE_LIST_RE = re.compile(
    rf"{_OEBB_DATE_ENTRY}(?:{_OEBB_DATE_SEPARATOR}(?:(?:am|von)\s+)?{_OEBB_DATE_DAY})+"
)
_OEBB_DATE_ENTRY_RE = re.compile(rf"(?:(am|von)\s+)?{_OEBB_DATE_DAY}")
_OEBB_DATE_VALUE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
# Behind the list the sentence goes on with "und" (a second route): the
# list then takes commas only.
_OEBB_DATE_LIST_AND_FOLLOWS_RE = re.compile(r"\s*(?:•\s*)?und\b")


def _oebb_upcoming_dates(text: str, today: date | None = None) -> str:
    """An ÖBB date list without its past days, joined as a sentence (see above).

    *today* defaults to Vienna's date of the build.
    """
    if today is None:
        today = datetime.now(_VIENNA_TZ).date()

    def last_day(entry: str) -> date | None:
        days = []
        for day, month, year in _OEBB_DATE_VALUE_RE.findall(entry):
            try:
                days.append(date(int(year), int(month), int(day)))
            except ValueError:
                return None
        return max(days) if days else None

    def rewrite(match: re.Match[str]) -> str:
        entries: list[str] = []
        ends: list[date | None] = []
        word = ""
        dropped = False
        for m in _OEBB_DATE_ENTRY_RE.finditer(match[0]):
            end = last_day(m[0])
            if end is None:
                return match[0]
            if end < today:
                dropped = True
            elif m[1] is None and ends[-1] is None:
                # Its "am"/"von" left with the day before: it takes it along.
                entries.append(f"{word} {m[0]}")
                ends.append(end)
                continue
            word = m[1] or word
            entries.append(m[0])
            ends.append(end if end >= today else None)
        kept = [entry for entry, end in zip(entries, ends, strict=True) if end is not None]
        if not kept or (not dropped and "•" not in match[0]):
            return match[0]
        if len(kept) == 1 or _OEBB_DATE_LIST_AND_FOLLOWS_RE.match(text, match.end()):
            return ", ".join(kept)
        return f"{', '.join(kept[:-1])} und {kept[-1]}"

    return _OEBB_DATE_LIST_RE.sub(rewrite, text)


# A list that is still too long for the display after the past days left
# takes the room of what the notice says: on 24.10.2026 the S1 read "…
# zwischen Deutsch Wagram Bahnhof und Gänserndorf Bahnhof von 24.10.2026 bis
# 26.10.2026, von 31.10.2026 bis 01.11.2026, von 14.11.2026 bis 15.11.2026
# …", and the cut took "der S1-Zug 23626 nicht fahren" — on the TV the whole
# route looked closed for one train. A sentence over the 180 characters now
# says "an einzelnen Tagen" in place of its lists, longest first, until it
# fits; the time line names the period. Two routes that both lost their
# list share the one phrase behind the second ("zwischen A und B und
# zwischen C und D an einzelnen Tagen der S1-Zug 23626 …"); once a list has
# gone, a single day of another route in that sentence goes the same way
# if the sentence still does not fit. A list that fits stays (operator decision 2026-10-25, "klingt gut. mach das bitte").
_OEBB_DAYS_IN_WORDS = "an einzelnen Tagen"
_OEBB_DAYS_IN_WORDS_LIMIT = 180
_OEBB_EVERY_DAY_NOTE_RE = re.compile(r"\(jeweils\b[^()]*\)$")
_OEBB_DATE_ENTRY_ONLY_RE = re.compile(_OEBB_DATE_ENTRY)


def _oebb_long_date_lists_in_words(text: str) -> str:
    """Every ÖBB sentence over 180 characters with its date lists in words (see above)."""

    def shorten(sentence: str) -> str:
        while len(sentence) > _OEBB_DAYS_IN_WORDS_LIMIT:
            lists = list(_OEBB_DATE_LIST_RE.finditer(sentence))
            if not lists and _OEBB_DAYS_IN_WORDS in sentence:
                # A list went and it still does not fit: the single days of
                # the other routes go too ("… und zwischen C und D am
                # 01.11.2026 der S1-Zug …" once the 25.10. is over). ÖBB's
                # own "(jeweils an einzelnen Tagen, …)" counts the same, and
                # its period goes too.
                lists = list(_OEBB_DATE_ENTRY_ONLY_RE.finditer(sentence))
            if not lists:
                break
            longest = max(lists, key=lambda m: len(m[0]))
            head, tail = sentence[: longest.start()], sentence[longest.end():]
            sentence = f"{head}{_OEBB_DAYS_IN_WORDS}{tail}"
            # "(jeweils 08:45 Uhr - 14:45 Uhr)" is said of every day: it stays
            # when the sentence still fits with it.
            every_day = _OEBB_EVERY_DAY_NOTE_RE.search(longest[0])
            with_note = f"{head}{_OEBB_DAYS_IN_WORDS} {every_day[0]}{tail}" if every_day else ""
            if with_note and len(with_note) <= _OEBB_DAYS_IN_WORDS_LIMIT:
                sentence = with_note
            if sentence.count(_OEBB_DAYS_IN_WORDS) > 1:
                sentence = sentence.replace(f" {_OEBB_DAYS_IN_WORDS}", "", 1)
        return sentence

    return " ".join(shorten(part) for part in _SENTENCE_SPLIT_RE.split(text))


# ÖBB says what does not run at the very end of its sentence, behind the
# routes and days: "Wegen Bauarbeiten kann zwischen … und zwischen … an
# einzelnen Tagen der S1-Zug 23626 nicht fahren." A sentence that is still
# over the 180 characters once its lists read in words lost exactly that to
# the cut: on 25.10.2026 the TV read "… an einzelnen Tagen der S1-Zug …"
# (Jules audit 2026-10-25), and 45 ÖBB notices since December 2025 ended
# the same way ("… von 31.10.2026 bis 30.11.2026 …", "… einige
# Nahverkehrszüge …"). Such a sentence now names the trains and what they
# cannot do first, and the routes and days follow behind a colon: "Wegen
# Bauarbeiten kann der S1-Zug 23626 nicht fahren: zwischen … an einzelnen
# Tagen." The cut then takes the end of the routes and days, which the
# title and the time line also name. Only ÖBB's own words are moved; a
# sentence that fits keeps its order. A sentence with two subjects ("der
# Zug REX6 (7799) zwischen … der Zug S60 …") or another verb ("werden …
# umgeleitet und können … nicht halten") keeps its order too. A list of
# three or more trains that keeps the sentence over the limit reads as their
# count with their lines ("die Züge S1 (28632), S1 (28684), … und S2
# (28153)" → "11 Züge der Linien S1, S2 und S3"; "die Züge IC 1110, …, D
# 1141" → "8 Züge (IC und D)"): eleven numbers took the 180 characters on
# 02.08.2026, route and "nicht fahren" included. Every train counts once,
# also one with a short number ("EC 71") and each number of a group ("REX 1
# (2334, 2338 und 2342)" are three). A list of bare numbers names no line
# and keeps its noun ("34 Nahverkehrszüge", "7 S4-Züge"). The lines stay
# named because the count alone did not say which trains (operator decision
# 2026-10-25: "Nenne die Linien, nicht die Anzahl an Linien", "11 Züge der
# Linie S1 ist in Ordnung, aber nicht 3 Linien sind betroffen").
_OEBB_TRAIN_NUMBER = (
    r"(?:[A-Z]{1,4} ?\d{1,5}[A-Z]?(?: ?\(\d{2,5}\))?|\d{2,5}"
    r"|\(\d{2,5}(?:, \d{2,5})*(?: und \d{2,5})?\))"
)
_OEBB_TRAINS = (
    r"(?:der|die|keine|einige) (?:\S+ ){0,3}?\S*(?:Zug|[Zz]üge)"
    rf"(?:(?:,? | und ){_OEBB_TRAIN_NUMBER})*"
)
_OEBB_CANNOT_RUN_RE = re.compile(
    r"(?P<lead>(?:[A-ZÄÖÜ][a-zäöü]+: )?(?:Wegen|Von|Am|An|Ab|Aufgrund) [^.]*? (?:kann|können))"
    rf" (?P<middle>.+?) (?P<trains>{_OEBB_TRAINS}) (?P<verb>(?:nicht )?fahren)\."
)
_OEBB_TRAINS_RE = re.compile(rf"\b{_OEBB_TRAINS}\b")
_OEBB_LISTED_TRAIN_RE = re.compile(
    r"(?:[A-Z]{1,4} ?\d{1,5}[A-Z]? ?)?\((?P<group>\d{2,5}(?:, \d{2,5})*(?: und \d{2,5})?)\)"
    r"|[A-Z]{1,4} ?\d{1,5}[A-Z]?|(?<![\d.])\d{2,5}(?![\d.])"
)
_OEBB_LISTED_TRAINS_RE = re.compile(r"die (?P<noun>\S*[Zz]üge) ")
_OEBB_OTHER_VERB_RE = re.compile(r"\b(?:wird|werden|ist|sind|war|waren)\b")


def _oebb_listed_trains_by_line(trains: str) -> str:
    """``"die Züge S1 (28632), …, S2 (28153)"`` → ``"11 Züge der Linien S1, S2 und S3"`` (see above)."""
    listed = _OEBB_LISTED_TRAINS_RE.match(trains)
    if listed is None:
        return trains
    count, lines = 0, []
    for entry in _OEBB_LISTED_TRAIN_RE.finditer(trains, listed.end()):
        count += len(re.findall(r"\d+", entry["group"])) if entry["group"] else 1
        if entry["group"]:
            lines.append(entry[0].split("(", 1)[0].strip())
        else:
            lines.append(re.sub(r"[^A-Z].*", "", entry[0]))
    if count < 3:
        return trains
    noun = listed["noun"]
    if not lines or not all(lines):
        # Bare numbers name no line ("die Nahverkehrszüge 7713, …").
        return f"{count} {noun}"
    named = list(dict.fromkeys(lines))
    names = named[0] if len(named) == 1 else ", ".join(named[:-1]) + f" und {named[-1]}"
    if all(re.search(r"\d", line) for line in named):
        # Lines of ÖBB's network: S1, REX 1.
        return f"{count} {noun} der Linie{'n' if len(named) > 1 else ''} {names}"
    # Train categories: EC, RJ, IC.
    return f"{count} {noun} ({names})"


# A time window ÖBB puts behind the period ("von 31.10.2026 bis 30.11.2026
# (jeweils von Samstag 23:15 Uhr bis Sonntag 04:40 Uhr und …)") says when
# the trains do not run; cut off, "keine Nahverkehrszüge … von 31.10.2026
# bis 30.11.2026 …" read as a month without trains (Wien Hbf–Gramatneusiedl,
# weekend nights only). When the sentence does not fit, the route goes
# first, since the title names it, then the period, when the time line ends
# on its last day; the window stays (operator decision 2026-10-26: "Fenster
# statt Strecke und Zeitraum"). "(an einigen Tagen)" goes with the trains
# like "an einzelnen Tagen" (Südstrecke, spring 2026).
_OEBB_PERIOD = (
    r"(?:von \d{2}\.\d{2}\.\d{4}(?: \(\d{2}:\d{2} Uhr\))? bis (?P<end>\d{2}\.\d{2}\.\d{4})"
    r"(?: \(\d{2}:\d{2} Uhr\))?|am (?P<day>\d{2}\.\d{2}\.\d{4}))"
)
_OEBB_WINDOW_MIDDLE_RE = re.compile(
    rf"zwischen (?P<a>(?:(?!zwischen ).)+?) und (?P<b>(?:(?!zwischen ).)+?)"
    rf" (?P<period>{_OEBB_PERIOD})(?: (?P<window>\(jeweils [^()]+\)))?"
)
_OEBB_SOME_DAYS = "an einigen Tagen"
# Words of a station name that do not tell it apart ("Wien Hbf (U)",
# "Flughafen Wien Bahnhof").
_OEBB_ROUTE_FILLER = frozenset({"wien", "bahnhof", "bahnhst", "hbf", "hauptbahnhof", "(u)", "u"})


def _oebb_title_names_station(station: str, title: str) -> bool:
    """Whether *title* names *station* ("Gramatneusiedl Bahnhof" in "Wien Hauptbahnhof ↔ Gramatneusiedl").

    One telling word is enough: ÖBB joins alternatives with "/"
    ("Bruck/Mur Bahnhof/Graz Hbf" under "Wien Hauptbahnhof Graz").
    """
    words = [w for w in re.split(r"[\s/]+", station.casefold()) if w and w not in _OEBB_ROUTE_FILLER]
    title_words = set(re.split(r"[\s/↔<>-]+", title.casefold()))
    if not words:
        # "Wien Hbf (U)": the title calls it "Wien Hauptbahnhof" or "Wien Hbf".
        return bool({"hauptbahnhof", "hbf"} & title_words)
    return any(w in title_words for w in words)


def _oebb_window_kept(lead: str, head: str, middle: str, title: str, shown_end: date | None) -> str:
    """*middle* without route and, if need be, period, so that the window fits (see above)."""
    match = _OEBB_WINDOW_MIDDLE_RE.fullmatch(middle)
    if match is None or not title or not (match["window"] or _OEBB_SOME_DAYS in head):
        return middle
    if not (_oebb_title_names_station(match["a"], title) and _oebb_title_names_station(match["b"], title)):
        return middle
    without_route = f"{match['period']} {match['window'] or ''}".rstrip()
    if len(f"{lead} {head}: {without_route}.") <= _OEBB_DAYS_IN_WORDS_LIMIT:
        return without_route
    last = match["end"] or match["day"]
    if not match["window"]:
        return middle
    if shown_end is None or last != shown_end.strftime("%d.%m.%Y"):
        return middle
    return match["window"][1:-1]


# ÖBB writes a disruption in one more fixed form, again with what does not
# run at the very end: "Wegen <Ursache> sind|waren zwischen <A> und <B>
# [bzw. <C> und <D>][,] [Zugfahrten] [bis voraussichtlich <hh:mm> Uhr |
# derzeit] keine Fahrten | nur eingeschränkt | nicht möglich." (the grammar
# of ``_OEBB_RESTRICTION_RE`` in en_templates.py). With two routes the
# sentence is over the 180 characters, and the cut took the one thing the
# TV viewer needs: "Wegen eines Polizeieinsatzes sind zwischen Tullnerfeld
# Bahnhof und St. Pölten Hbf bzw. Tullnerfeld Bahnhof und
# Moosbierbaum-Heiligeneich Bahnhof, bis voraussichtlich 13:30 …"
# (KI-Wächter 12. and 13.11.2026, issue #2209; 20 ÖBB disruptions since
# December 2025 read the same, among them Stadlau–Simmering,
# Meidling–Liesing and Hütteldorf–Handelskai). Same rule as for the trains
# above: such a sentence says what is not possible, with its time, first,
# and the routes follow behind a colon: "Wegen eines Polizeieinsatzes sind
# bis voraussichtlich 13:30 Uhr keine Fahrten möglich: zwischen Tullnerfeld
# Bahnhof und St. Pölten Hbf bzw. …". The cut then takes the end of the
# routes, which the title names. Only ÖBB's own words move, the word cut
# stays (operator decision 2026-11-05, "Wortweise bleibt"); a sentence that
# fits keeps its order, and one with anything else after the routes ("keine
# Fahrten der S 80 zwischen … möglich") too.
_OEBB_RESTRICTION_LONG_RE = re.compile(
    r"(?P<lead>Wegen [^.:]+?) (?P<tense>sind|waren)"
    r" (?P<place>(?:zwischen|in|im Bereich) [^\d:]+?),?"
    r"(?P<subject> Zugfahrten| für Züge der Linie [A-Z]{1,4} ?\d{1,3})?"
    r"(?P<when> (?:bis (?:voraussichtlich )?|voraussichtlich bis )"
    r"(?:\d{2}\.\d{2}\.\d{4}(?:,? \d{1,2}:\d{2}(?: Uhr)?)?|\d{1,2}:\d{2}(?: Uhr)?)| derzeit)?"
    r" (?P<core>keine Fahrten|nur eingeschränkt(?: Fahrten)?|nicht) möglich\."
)
# What may not stand in the place: the template's own words. A place that
# holds one is not a list of stations, and the sentence keeps its order.
_OEBB_RESTRICTION_PLACE_NOT_A_NAME_RE = re.compile(r"\b(?:bis|derzeit|voraussichtlich|für|Zugfahrten|Fahrten|Züge)\b")


def _oebb_restriction_first(sentence: str) -> str:
    """ÖBB's "Wegen … sind zwischen … keine Fahrten möglich." with what is not possible first (see above)."""
    match = _OEBB_RESTRICTION_LONG_RE.fullmatch(sentence)
    if (
        match is None
        or _OEBB_OTHER_VERB_RE.search(match["lead"])
        or _OEBB_RESTRICTION_PLACE_NOT_A_NAME_RE.search(match["place"])
    ):
        return sentence
    return (
        f"{match['lead']} {match['tense']}{match['subject'] or ''}{match['when'] or ''}"
        f" {match['core']} möglich: {match['place']}."
    )


def _oebb_what_does_not_run_first(
    sentence: str, *, title: str = "", shown_end: date | None = None
) -> str:
    """An ÖBB sentence over 180 characters with its trains in front (see above)."""
    if len(sentence) <= _OEBB_DAYS_IN_WORDS_LIMIT:
        return sentence
    if sentence.startswith("Wegen ") and not _OEBB_CANNOT_RUN_RE.fullmatch(sentence):
        return _oebb_restriction_first(sentence)
    match = _OEBB_CANNOT_RUN_RE.fullmatch(sentence)
    if (
        match is None
        or _OEBB_OTHER_VERB_RE.search(match["lead"])
        or _OEBB_TRAINS_RE.search(match["middle"])
        or (match["verb"] == "fahren") != match["trains"].startswith("keine ")
    ):
        return sentence
    trains, middle = match["trains"], match["middle"]
    trains = _oebb_listed_trains_by_line(trains)
    # "an einzelnen Tagen" at the end of the routes says when the trains do
    # not run: it goes with them ("kann der S1-Zug 23626 an einzelnen Tagen
    # nicht fahren: zwischen …"), where the cut cannot take it.
    days = ""
    if middle.endswith(f" {_OEBB_DAYS_IN_WORDS}"):
        middle, days = middle.removesuffix(f" {_OEBB_DAYS_IN_WORDS}"), _OEBB_DAYS_IN_WORDS
    elif middle.endswith(f" ({_OEBB_SOME_DAYS})"):
        middle, days = middle.removesuffix(f" ({_OEBB_SOME_DAYS})"), _OEBB_SOME_DAYS
    if not days:
        head = f"{trains} {match['verb']}"
    elif trains.startswith(("der ", "die ")):
        head = f"{trains} {days} {match['verb']}"
    else:
        head = f"{days} {trains} {match['verb']}"
    if len(f"{match['lead']} {head}: {middle}.") > _OEBB_DAYS_IN_WORDS_LIMIT:
        middle = _oebb_window_kept(match["lead"], head, middle, title, shown_end)
    return f"{match['lead']} {head}: {middle}."


def _oebb_section_labels_cased(text: str) -> str:
    """``"TEILAUSFALL: …"`` → ``"Teilausfall: …"``, every word of the label."""
    return _OEBB_SECTION_LABEL_RE.sub(
        lambda m: re.sub(r"[A-ZÄÖÜ]+", lambda w: w[0][0] + w[0][1:].lower(), m[1]), text
    )


# The English of ÖBB's section labels, fixed instead of left to the model,
# which read "Teilausfall:" as "Partial failure:". Every label of the ÖBB
# texts in the cache history (926 notices, 2025-09 to 2026-10); ÖBB joins two
# of them with "/" ("TEILAUSFALL/SCHIENENERSATZVERKEHR:"), each part is looked
# up on its own. A label not listed here goes through the model as before.
_OEBB_SECTION_LABELS_EN: dict[str, str] = {
    "achtung": "Please note",
    "ausfall": "Cancellation",
    "ersatzbeförderung": "Replacement service",
    "geänderte fahrzeiten": "Timetable changes",
    "haltausfall": "Cancelled stops",
    "hinweis": "Note",
    "hinweise": "Notes",
    "schienenersatzverkehr": "Rail replacement service",
    "teilausfall": "Partial cancellation",
}

# A sentence headed by a label in the form :func:`_oebb_section_labels_cased`
# leaves it: capitalised words joined by a space or "/", then the colon.
_OEBB_LABELLED_SENTENCE_RE = re.compile(
    r"(?P<label>[A-ZÄÖÜ][a-zäöüß]+(?:[ /][A-ZÄÖÜa-zäöüß][a-zäöüß]+)*):\s+(?P<body>\S.*)",
    re.DOTALL,
)


def _oebb_section_label_en(label: str) -> str | None:
    """``"Teilausfall/Schienenersatzverkehr"`` → ``"Partial cancellation/rail
    replacement service"``; ``None`` unless every part is a known label."""
    english: list[str] = []
    for part in label.split("/"):
        name = _OEBB_SECTION_LABELS_EN.get(part.strip().casefold())
        if name is None:
            return None
        english.append(name[0].lower() + name[1:] if english else name)
    return "/".join(english)
