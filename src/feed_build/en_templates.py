"""Non-recursive EN templates for ÖBB and WL sentences.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from .placeholders import _resolve_glossary
from .oebb_summary import (
    _OEBB_CANNOT_RUN_RE, _OEBB_DATE, _OEBB_DAYS_IN_WORDS, _OEBB_OTHER_VERB_RE, _OEBB_SOME_DAYS, _OEBB_TRAINS,
    _OEBB_TRAINS_RE,
)

__all__ = [
    "_OEBB_RESTRICTION_RE", "_OEBB_RESTRICTION_FRONT_RE", "_oebb_cannot_run_middle_en", "_OEBB_NO_TRAINS_RE",
    "_OEBB_SLOT_NOT_A_NAME_RE", "_OEBB_SENTENCE_SPLIT_RE",
    "_oebb_station_en", "_CAUSE_EN_RE", "_oebb_slots_are_names", "_OebbCannotRun", "_oebb_cannot_run_parts",
    "_render_oebb_follow_up", "_WL_BECAUSE_RE", "_WL_THEREFORE_EN_RE", "_WL_MAIN_CLAUSE_SMALL_WORDS",
    "_WL_SENTENCE_SPLIT_RE", "_RELOCATION_CLOSED_LINE_RE", "_WL_IRREGULAR_INTERVALS_RE",
    "_render_wl_irregular_intervals", "_WL_SERVICE_CHANGES_RE", "_WL_HEADED_BECAUSE_RE",
]


# ÖBB's disruption notices are written from a closed grammar: 455 of the 474
# distinct ÖBB sentences published since 2026-05-20 open with "Wegen", and
# almost all of them are one sentence with five slots::
#
#     Wegen <Ursache> sind|waren zwischen <A> und <B> | in <A> [Zugfahrten
#     | für Züge der Linie <L>]
#     [bis [voraussichtlich] | voraussichtlich bis] [<TT.MM.JJJJ>,] <hh:mm> Uhr
#     | <TT.MM.JJJJ> | derzeit]
#     keine Fahrten | nur eingeschränkt [Fahrten] möglich.
#
# The line and the bare end day were added on 2026-11-13 with the order of
# ``_oebb_restriction_first``: "für Züge der Linie S 45 bis voraussichtlich
# 24.07.2026, 01:00 Uhr" and "bis voraussichtlich 07.09.2026" went through
# the model and read "trains on line S 45 until approx. 24.07.2026, 01:00 no
# journeys are possible" and "until approx. 07.09.2026 is not possible".
#
# The model handled the verb at the end badly, and the meaning went with it
# (EN audit 2026-10-03, all from the feed)::
#
#     Due to a police operation between Wien Hernals station and Wien
#     Hütteldorf Station (U) to 23:48 were not possible.
#     Due to a switch fault in Wien Stadlau station (U) Train journeys until
#     approx. 21:30 are only limited.
#     … between Wien Franz-Josefs-Bahnhof and St.Andrä-Wördern train station
#     no R 40 train.
#
# The sentence is rendered from its slots instead; only the cause ("Wegen
# eines Polizeieinsatzes") still goes through the model, as the short phrase
# it is. The station names stay as ÖBB writes them, like every other name in
# the EN feed; only the trailing "Bahnhof"/"Bahnhst" becomes "station", as the
# glossary has rendered it since 2026-09-07.
#
# The German summary leaves the place out where the title names it and the
# sentence would otherwise push ÖBB's second one out of the 180 characters
# (:func:`_oebb_sentence_without_place`), so the place slot may be empty.
_OEBB_RESTRICTION_RE: re.Pattern[str] = re.compile(
    r"Wegen (?P<cause>.+?) (?P<tense>sind|waren)"
    r"(?P<place> (?:zwischen (?P<a>.+?) und (?P<b>.+?)|in (?P<at>.+?)|im Bereich (?P<area>.+?)))?"
    r"(?: Zugfahrten| für Züge der Linie (?P<line>[A-Z]{1,4} ?\d{1,3}))?"
    r"(?: (?:bis (?P<expected>voraussichtlich )?|(?P<expected_first>voraussichtlich) bis )"
    r"(?:(?:(?P<date>\d{2}\.\d{2}\.\d{4}),? )?(?P<time>\d{1,2}:\d{2})(?: Uhr)?"
    r"|(?P<until_day>\d{2}\.\d{2}\.\d{4}))"
    r"| (?P<now>derzeit))?"
    r" (?:(?P<none>keine Fahrten|nicht)|nur eingeschränkt(?: Fahrten)?) möglich\."
)
# The same sentence with what is not possible first and the places behind a
# colon, as :func:`_oebb_restriction_first` gives it when it is over the 180
# characters, cut or not: "Wegen eines Polizeieinsatzes sind bis
# voraussichtlich 13:30 Uhr keine Fahrten möglich: zwischen Tullnerfeld
# Bahnhof und St. Pölten Hbf bzw. Tullnerfeld Bahnhof …". The head is the
# template above without its place, the places are read like those of
# "… kann … nicht fahren: …" (``_oebb_cannot_run_middle_en``).
_OEBB_RESTRICTION_FRONT_RE: re.Pattern[str] = re.compile(
    r"(?P<head>Wegen .+? möglich): (?P<middle>(?:zwischen|in|im Bereich) .+?)(?:\.|(?P<cut> …))"
)
# The planned-works variant: "Wegen Bauarbeiten können [von D bis D]
# [zwischen A und B] [von D bis D] keine R 40-Züge fahren." (the German
# summary may leave the place out, as above).
_OEBB_NO_TRAINS_RE: re.Pattern[str] = re.compile(
    r"Wegen (?P<cause>.+?) können"
    r"(?: von (?P<d1>\d{2}\.\d{2}\.\d{4}) bis (?P<d2>\d{2}\.\d{2}\.\d{4}))?"
    r"(?: zwischen (?P<a>.+?) und (?P<b>.+?))?"
    r"(?: von (?P<d3>\d{2}\.\d{2}\.\d{4}) bis (?P<d4>\d{2}\.\d{2}\.\d{4}))?"
    r" keine (?P<trains>[A-Z]{1,3} ?\d{1,3})-Züge fahren\."
)
# A place slot holds a station name and nothing else. The slots are lazy, so
# a sentence that varies the template anywhere after the second station still
# matched, and the variation landed in the station slot, German inside the
# English frame (fourth check 2026-10-03, 10 of 286 templated ÖBB sentences in
# the feed since July; five more kept "Bahnhof" before a bracketed
# municipality): "… between Wien Hbf (U) and Gramatneusiedl Bahnhof
# Zugfahrten bis voraussichtlich 27.08.2026, 23:59 Uhr.", "… at Wien Hbf (U)
# bzw Wien Meidling Zugfahrten erneut.", "… and Wien Handelskai Bahnhst (U) am
# 01.11.2026 (von 01:10 Uhr bis 04:10 Uhr).". A slot with a digit or one of
# these words is no station name; such a sentence takes the ordinary path.
# "in der" stays allowed ("Wien Wolf in der Au"), and so does ÖBB's bracketed
# municipality ("Hinterstoder Bahnhof [in St.Pankraz]"), which reads the same
# in English.
_OEBB_SLOT_NOT_A_NAME_RE: re.Pattern[str] = re.compile(
    r"\d|\b(?:und|bzw|sowie|oder|bis|von|ab|am|um|für|nach|Uhr"
    r"|voraussichtlich|derzeit|erneut|noch|Zugfahrten|Fahrten|Züge)\b"
)
# "St." opens a name ("in St. Pölten Hbf", "[in St. Pankraz]"), never ends a
# sentence: split there, the templated sentence went through the model in
# two halves (42 of the 443 ÖBB texts in the cache on 2026-10-09). The same
# holds for ÖBB's other abbreviations in front of a name: "bzw." ("zwischen
# Budapest-Keleti und Wien Hbf bzw. Salzburg Hbf"), "Wr." ("Wr. Neustadt") and
# "b." ("Gerasdorf b. Wien"); a section label in front of such a sentence
# (``_OEBB_LABELLED_SENTENCE_RE``) kept only its first half.
_OEBB_SENTENCE_SPLIT_RE: re.Pattern[str] = re.compile(
    r"(?<!\bSt\.)(?<!\bbzw\.)(?<!\bWr\.)(?<!\bb\.)(?<=[.!])\s+(?=[A-ZÄÖÜ])"
)
_OEBB_STATION_SUFFIX_RE: re.Pattern[str] = re.compile(
    r" (?:Bahnhof|Bahnhst\.?)(?=(?: \(U\))?(?: \[[^\]]*\])?$)"
)


def _oebb_station_en(name: str) -> str:
    """An ÖBB station name for the EN feed: verbatim, "Bahnhof" → "station"."""
    return _OEBB_STATION_SUFFIX_RE.sub(" station", name.strip())


_CAUSE_EN_RE: re.Pattern[str] = re.compile(
    r"(?:Due to|Because of|Owing to|As a result of|On account of|In view of)\b",
    re.IGNORECASE,
)


def _oebb_slots_are_names(match: re.Match[str], *slots: str) -> bool:
    """Whether every filled place slot of *match* is a bare station name."""
    return not any(
        match[slot] and _OEBB_SLOT_NOT_A_NAME_RE.search(match[slot]) for slot in slots
    )


# ÖBB's sentence for trains that do not run: "Wegen Bauarbeiten kann zwischen
# Wien Leopoldau Bahnhst (U) und Mistelbach/Zaya Bahnhof am 25.10.2026 und
# 01.11.2026 der S2-Zug 23360 nicht fahren." The model lost the verb at the
# end behind the routes and days (Jules audit 2026-10-25, both from the EN
# feed): "… the S2 train can't drive 23360." and "Construction works allows
# between … on individual days of the S1 train …", as if the train ran. The
# sentence is rendered from its parts, in ÖBB's order or in the one
# :func:`_oebb_what_does_not_run_first` gives it: "Due to construction works,
# the S2 train 23360 cannot run between Wien Leopoldau station (U) and
# Mistelbach/Zaya station on 25.10.2026 and 01.11.2026." Only the cause goes
# through the model. Routes and days are read from a closed set (zwischen A
# und B, am …, von … bis …, an einzelnen Tagen, "(jeweils hh:mm Uhr - hh:mm
# Uhr)"); a sentence with anything else ("in der Nacht von …", "(jeweils
# Samstag, Sonntag und Feiertag)") keeps the rendered trains and verb, and
# only its routes and days go through the model, behind a colon as in the
# German. A German summary cut at the 180 characters ends in " …" and keeps
# it.
# A line or category the German names (_oebb_listed_trains_by_line): "S1",
# "REX 1", "IC".
_OEBB_LINE_CODE = r"[A-Z]{1,4}(?: ?\d{1,3})?"
_OEBB_CANNOT_RUN_FRONT_RE = re.compile(
    rf"Wegen (?P<cause>[^.:]+?) (?:kann|können)(?P<days1> (?:{_OEBB_DAYS_IN_WORDS}|{_OEBB_SOME_DAYS}))?"
    rf" (?P<trains>(?:\d{{1,3}} \S*[Zz]üge|\d{{1,3}} \S*[Zz]üge (?:der Linien? |\()"
    rf"(?:{_OEBB_LINE_CODE}, )*(?:{_OEBB_LINE_CODE} und )?{_OEBB_LINE_CODE}\)?|{_OEBB_TRAINS}))"
    rf"(?P<days2> (?:{_OEBB_DAYS_IN_WORDS}|{_OEBB_SOME_DAYS}))?"
    r" (?P<verb>(?:nicht )?fahren): (?P<middle>.+?)(?:\.|(?P<cut> …))"
)
_OEBB_CANNOT_RUN_EN_TRAIN_NOUNS: dict[str, str] = {
    "zug": "train",
    "züge": "trains",
    "nahverkehrszüge": "local trains",
    "fernverkehrszüge": "long-distance trains",
    "nachtzüge": "night trains",
}
_OEBB_EN_TIME = r"\d{1,2}:\d{2}"
_OEBB_EN_NAME = r"[A-ZÄÖÜ][^,()\d…]*?(?: \(U\))?"
_OEBB_EN_NAME_END = r"(?= und zwischen| bzw\. zwischen|, (?:und )?zwischen| (?:am|von|an)\b| \(|$)"
_OEBB_EN_SEGMENT_RE = re.compile(
    rf"zwischen (?P<a>{_OEBB_EN_NAME}(?: bzw\. {_OEBB_EN_NAME})?)"
    rf" und (?P<b>{_OEBB_EN_NAME}(?: bzw\. {_OEBB_EN_NAME})?){_OEBB_EN_NAME_END}"
    rf"|von (?P<d1>{_OEBB_DATE})(?: \((?P<c1>ca\. )?(?P<t1>{_OEBB_EN_TIME}) Uhr\))?"
    rf" bis (?:(?P<d2>{_OEBB_DATE})(?: \((?P<c2>ca\. )?(?P<t2>{_OEBB_EN_TIME}) Uhr\))?"
    rf"|voraussichtlich(?: Ende (?P<year>\d{{4}})|(?=$)))"
    rf"|am (?P<on>{_OEBB_DATE}(?:(?:,| und)(?: am)? {_OEBB_DATE})*)"
    rf"|(?P<days>{_OEBB_DAYS_IN_WORDS})"
    rf"|\(jeweils (?P<e1>{_OEBB_EN_TIME}) Uhr - (?P<e2>{_OEBB_EN_TIME}) Uhr\)"
)
_OEBB_EN_JOINER_RE = re.compile(r"(?:,? und | bzw\. |, | )")
# What the cut left of a last route: "zwischen Wien Leopoldau Bahnhst".
_OEBB_EN_CUT_ROUTE_RE = re.compile(rf"zwischen (?P<a>{_OEBB_EN_NAME})(?: und (?P<b>{_OEBB_EN_NAME}))?")


def _oebb_cannot_run_trains_en(trains: str, verb: str) -> str | None:
    """``"der S1-Zug 23626"`` → ``"the S1 train 23626"``; ``None`` when unknown."""
    article, rest = trains.split(" ", 1)
    noun_match = re.search(r"(?P<pre>(?:\S+ )*?)(?P<noun>\S*?)(?P<word>Zug|[Zz]üge)\b(?P<tail>.*)", rest)
    if noun_match is None:
        return None
    compound = (noun_match["noun"] + noun_match["word"]).casefold()
    named = noun_match["noun"].removesuffix("-")
    if compound in _OEBB_CANNOT_RUN_EN_TRAIN_NOUNS:
        noun = _OEBB_CANNOT_RUN_EN_TRAIN_NOUNS[compound]
    elif named and noun_match["noun"].endswith("-") and not named[0].islower():
        noun = f"{named} {_OEBB_CANNOT_RUN_EN_TRAIN_NOUNS[noun_match['word'].casefold()]}"
    else:
        return None
    pre = noun_match["pre"].replace("- und ", " and ").replace(" und ", " and ")
    if pre and re.search(r"[a-zäöüß]{3}", pre.replace(" and ", " ")):
        return None
    tail = noun_match["tail"].replace(" und ", " and ")
    tail = tail.replace(" der Linien ", " on lines ").replace(" der Linie ", " on line ")
    lead = {"der": "the ", "die": "the ", "keine": "no ", "einige": "some "}.get(article)
    if lead is None and article.isdigit():
        lead = f"{article} "
    if lead is None or (verb == "fahren") != (article == "keine"):
        return None
    if lead == "the " and not named and tail:
        # "der Zug RJ 650", "die Züge IC 1110, …": the numbers name them.
        lead = ""
    return f"{lead}{pre}{noun}{tail}"


def _oebb_cannot_run_middle_en(middle: str, *, cut: bool) -> str | None:
    """The routes and days of the sentence in English; ``None`` when unknown."""
    english: list[str] = []
    pos = 0
    while pos < len(middle):
        if pos:
            joiner = _OEBB_EN_JOINER_RE.match(middle, pos)
            if joiner is None:
                return None
            word = joiner[0].strip(" ,")
            english.append({"und": " and", "bzw.": " or"}.get(word, "," if "," in joiner[0] else ""))
            pos = joiner.end()
        segment = _OEBB_EN_SEGMENT_RE.match(middle, pos)
        if segment is None and cut:
            segment = _OEBB_EN_CUT_ROUTE_RE.fullmatch(middle, pos)
        if segment is None:
            return None
        groups = segment.groupdict()
        if groups.get("a") is not None:
            names = [groups["a"], groups["b"]] if groups.get("b") else [groups["a"]]
            alternatives = [name.split(" bzw. ") for name in names]
            if any(_OEBB_SLOT_NOT_A_NAME_RE.search(n) for alt in alternatives for n in alt):
                return None
            english.append(" between " + " and ".join(
                " or ".join(_oebb_station_en(n) for n in alt) for alt in alternatives
            ))
        elif groups.get("d1") is not None:
            def at(day: str, approx: str | None, time: str | None) -> str:
                return f"{day} ({'approx. ' if approx else ''}{time})" if time else day
            if groups["year"] is not None:
                until = f"until probably the end of {groups['year']}"
            elif groups["d2"] is None:
                until = "until probably"
            else:
                until = f"to {at(groups['d2'], groups['c2'], groups['t2'])}"
            english.append(f" from {at(groups['d1'], groups['c1'], groups['t1'])} {until}")
        elif groups.get("on") is not None:
            days = re.split(r"(?:,| und)(?: am)? ", groups["on"])
            english.append(" on " + (f"{', '.join(days[:-1])} and {days[-1]}" if len(days) > 1 else days[0]))
        elif groups.get("days") is not None:
            english.append(" on certain days")
        else:
            english.append(f" (each day {groups['e1']} - {groups['e2']})")
        pos = segment.end()
    return "".join(english)


class _OebbCannotRun(NamedTuple):
    """ÖBB's "… kann … nicht fahren." in parts (:func:`_oebb_cannot_run_parts`)."""

    cause: str
    # The English of the trains and the verb ("the S2 train 23360 cannot run").
    head: str
    # The English of the routes and days, ``None`` when outside the closed set.
    rest: str | None
    # Their German, for the model then.
    middle: str


def _oebb_cannot_run_parts(sentence: str) -> _OebbCannotRun | None:
    """ÖBB's "… kann … nicht fahren." in parts (see above), ``None`` for any other.

    The routes and days may be outside the closed set only in the order with
    the trains first.
    """
    front = _OEBB_CANNOT_RUN_FRONT_RE.fullmatch(sentence)
    if front is not None:
        cause, trains, verb, middle = front["cause"], front["trains"], front["verb"], front["middle"]
        days = bool(front["days1"] or front["days2"])
        cut = bool(front["cut"])
    else:
        back = _OEBB_CANNOT_RUN_RE.fullmatch(sentence)
        if back is None or not back["lead"].startswith("Wegen "):
            return None
        cause = back["lead"].removeprefix("Wegen ").rsplit(" ", 1)[0]
        trains, verb, middle, days, cut = back["trains"], back["verb"], back["middle"], False, False
    if _OEBB_OTHER_VERB_RE.search(cause) or _OEBB_TRAINS_RE.search(middle):
        return None
    trains_en = _oebb_cannot_run_trains_en(trains, verb)
    if trains_en is None:
        return None
    middle_en = _oebb_cannot_run_middle_en(middle, cut=cut)
    if middle_en is None and front is None:
        return None
    verb_en = "can run" if verb == "fahren" else "cannot run"
    when = " on certain days" if days else ""
    end = " …" if cut else "."
    return _OebbCannotRun(
        cause=cause,
        head=f"{trains_en} {verb_en}{when}",
        rest=None if middle_en is None else f"{middle_en}{end}",
        middle=middle + end,
    )


# ÖBB's second sentence says what is still going on, in a handful of fixed
# forms (every ÖBB text in the cache on 2026-10-09: 249 of the 261 second
# sentences the German summary keeps since then). They reached the model
# only cut off until then. Each has one meaning and is written out; any other
# wording ("Fernverkehrszüge und CJX Züge werden umgeleitet.") goes through
# the model as before.
_OEBB_EXTRA_TIME_RE: re.Pattern[str] = re.compile(
    r"Planen Sie(?P<area> in diesem Bereich)?(?P<still> derzeit(?: noch)?| noch)?"
    r" bis zu (?P<minutes>\d{1,3}) Minuten mehr Reisezeit ein\."
)
_OEBB_LONGER_TRIP_RE: re.Pattern[str] = re.compile(
    r"Ihre Reisezeit verlängert sich um bis zu (?P<minutes>\d{1,3}) Minuten\."
)
_OEBB_SOME_CANCELLED_RE: re.Pattern[str] = re.compile(
    r"Es kommt(?P<still> noch)? zu vereinzelten Zugausfällen und (?:Verzögerungen|Verspätungen)"
    r"(?: bis zu (?P<minutes>\d{1,3}) Minuten)?\."
)
_OEBB_FIXED_FOLLOW_UPS: dict[str, str] = {
    "Über die Dauer der Unterbrechung kann derzeit noch keine Angabe gemacht werden.":
        "It is not yet known how long the interruption will last.",
    "Die Züge warten die Sperre vorerst ab.":
        "For now, trains are waiting until the line reopens.",
    "Fahrgäste mit ÖBB-Tickets können die Wiener Linien benutzen.":
        "Passengers with ÖBB tickets can use Wiener Linien.",
    "Fahrgäste mit ÖBB-Tickets können in diesem Bereich die Wiener Linien benutzen.":
        "Passengers with ÖBB tickets can use Wiener Linien in this area.",
}


def _render_oebb_follow_up(sentence: str) -> str:
    """English for one of ÖBB's fixed follow-up sentences; ``""`` for any other."""
    fixed = _OEBB_FIXED_FOLLOW_UPS.get(sentence)
    if fixed is not None:
        return fixed
    match = _OEBB_EXTRA_TIME_RE.fullmatch(sentence)
    if match is not None:
        lead = "For now, allow" if match["still"] else "Allow"
        area = " in this area" if match["area"] else ""
        return f"{lead} up to {match['minutes']} minutes of extra travel time{area}."
    match = _OEBB_LONGER_TRIP_RE.fullmatch(sentence)
    if match is not None:
        return f"Your journey takes up to {match['minutes']} minutes longer."
    match = _OEBB_SOME_CANCELLED_RE.fullmatch(sentence)
    if match is not None:
        still = " still" if match["still"] else ""
        delays = f"delays of up to {match['minutes']} minutes" if match["minutes"] else "delays"
        return f"There are{still} occasional train cancellations and {delays}."
    return ""


# WL writes its causes the same way ÖBB does, "Wegen <Ursache>" in front of
# the main clause, and the model lost the sense on it in the same way. From
# the EN feed since July (DE/EN pairs, check of 2026-10-05)::
#
#     Wegen Bauarbeiten im Bereich Wildbadgasse wird die Linie 20A umgeleitet.
#     Construction works in the area of Wildbadgasse is redirected to line 20A.
#     Wegen Sanierung des Bahnsteigs hält die Linie U6 die Station Neue Donau
#     U nur in Richtung Siebenhirten U ein.
#     The U6 station stops at Neue Donau U only in the direction of
#     Siebenhirten U.
#
# Unlike ÖBB's, WL's main clauses are free prose, so there are no slots: the
# cause goes through the model as the short phrase it is (like ÖBB's), and
# the main clause behind it as a sentence of its own, opened with "Deshalb"
# so the verb stays in second place ("Deshalb wird die Linie 20A
# umgeleitet."). The model's "Therefore," is dropped and the cause put in its
# place. When the model opens the main clause any other way, or the cause
# holds a comma (a relative clause, where the first verb need not be the
# main verb), the sentence goes through the model whole, as before.
_WL_BECAUSE_RE: re.Pattern[str] = re.compile(
    r"Wegen (?P<cause>[^,]+?) (?P<verb>wird|werden|kommt|kommen|hält|halten|muss|müssen"
    r"|kann|können|ist|sind|fährt|fahren|verkehrt|verkehren|bleibt|bleiben"
    r"|entfällt|entfallen|gibt|endet|enden|beginnt|beginnen) (?P<rest>.+[.!…])"
)
_WL_THEREFORE_EN_RE: re.Pattern[str] = re.compile(
    r"(?:Therefore|That is why|This is why|For this reason|Hence|Thus|As a result"
    r"|Consequently|Accordingly|So)\b,?\s+",
    re.IGNORECASE,
)
# The first word of the main clause gets a small letter behind the cause only
# when it is an ordinary word; a name ("Neue Donau", "U6") keeps its spelling.
_WL_MAIN_CLAUSE_SMALL_WORDS: frozenset[str] = frozenset({
    "the", "line", "lines", "bus", "buses", "tram", "trams", "train", "trains",
    "there", "it", "a", "an", "this", "these", "passengers", "you", "in", "on",
    "at", "between", "from", "only", "no", "all",
})


# A WL sentence ends at ". " before a capital, except behind an ordinal that
# opens an address ("1. Haidequerstraße 2 → 1. Haidequerstraße 510": at the
# start or behind the arrow) or an abbreviation that WL's addresses carry
# ("ggü. 40", "Bhf. Meidling"). A house number ends a sentence as usual
# ("→ Perchtoldsdorfer Straße 2. Haltestellenverlegung …").
_WL_SENTENCE_SPLIT_RE: re.Pattern[str] = re.compile(
    r"(?<!^\d\.)(?<!^\d\d\.)(?<!→ \d\.)(?<!→ \d\d\.)(?<!\bggü\.)(?<!\bNr\.)(?<!\bStr\.)(?<!\bBhf\.)(?<!\bca\.)"
    r"(?<!\bbzw\.)(?<!\bprov\.)(?<!\bHst\.)(?<=[.!])\s+(?=[A-ZÄÖÜ])"
)


# A closed stop's line from :func:`_relocation_arrows` ("Hartäckerstraße 65:
# ersatzlos aufgelassen.") is the same record as an arrow line. So is the
# bare closure under a direction heading (``_relocation_by_direction``,
# "Richtung Hietzing U: aufgelassen."): it went through the model while the
# arrow line before it did not, and one notice read "Towards Bhf.
# Atzgersdorf S: … Direction Hietzing U: Discontinued." (EN feed
# 2026-10-25). Both headings now read "Towards".
_RELOCATION_CLOSED_LINE_RE: re.Pattern[str] = re.compile(
    r"\S:\s+(?:ersatzlos\s+)?aufgelassen\.?$"
)


# WL's two sentences for irregular intervals: "Derzeit kommt es bei der Linie
# N31 in beiden Richtungen zu unterschiedlichen Intervallen." (20 texts since
# July) and "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen
# Intervallen." (25). The model wrote "the N31 line has different intervals"
# and "there are different intervals"; with the phrase glossed, "irregular
# intervals is used … on the N31 line" and "it comes to irregular
# intervals". Each has one meaning and is written out. A display ticker's
# label in front ("Verspätungen: …") keeps its glossary English.
_WL_IRREGULAR_INTERVALS_RE: re.Pattern[str] = re.compile(
    r"(?:(?P<label>[A-ZÄÖÜ][a-zäöüß]+): )?"
    r"(?:(?P<now>Derzeit)|(?P<after>Nach einer Fahrtbehinderung)) kommt es"
    r"(?: bei der Linie (?P<line>(?:U[1-6]|N?\d{1,3}[A-Z]?|[A-Z])))?"
    r"(?P<both> in beiden Richtungen)? zu unterschiedlichen Intervallen\."
)


def _render_wl_irregular_intervals(match: re.Match[str], source: str | None) -> str:
    """English for WL's "… kommt es zu unterschiedlichen Intervallen."; ``""`` when unknown."""
    label = ""
    if match["label"]:
        glossary = _resolve_glossary(source, None)
        english = next(
            (value for key, value in glossary.items() if key.casefold() == match["label"].casefold()),
            None,
        )
        if english is None:
            return ""
        label = f"{english[:1].upper()}{english[1:]}: "
    lead = "Currently" if match["now"] else "After a service obstruction"
    line = f" on line {match['line']}" if match["line"] else ""
    both = " in both directions" if match["both"] else ""
    return f"{label}{lead}, there are irregular intervals{line}{both}."


# WL's stock opening for planned measures: "Wegen einer Demonstration kommt es
# zu (folgenden) Verkehrsmaßnahmen (für die Linie 74A)." The model rendered
# its main clause "transport is taking place", "it comes to traffic measures"
# and "the following transport measures are taking place" (15 texts since
# July). The main clause has one meaning, so it is written out here; only the
# cause goes through the model. The wording "service changes" is the
# operator's choice (2026-10-08). The cause may hold a comma here
# ("im Bereich Innenstadt, Währinger Straße"): the main clause is fixed, so
# no verb can hide behind it. Twice in July WL ran the "Zeitraum:" record on
# without a period; those sentences keep the ordinary path, since the
# sentence split has already cut their record at its date.
_WL_SERVICE_CHANGES_RE: re.Pattern[str] = re.compile(
    r"Wegen (?P<cause>[^.!?]+?) (?:kommt|kommen) es zu (?P<following>folgenden )?"
    r"Verkehrsmaßnahmen(?: (?:für|bei) (?:die|der|den) (?P<noun>Linien?) (?P<lines>[^.!?]+?))?"
    r"[.!]"
)


_WL_HEADED_BECAUSE_RE: re.Pattern[str] = re.compile(
    r"(?P<head>[A-ZÄÖÜ][a-zäöüß]{3,}) (?P<body>Wegen .+)"
)
