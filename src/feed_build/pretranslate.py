"""German normalisation before the translation and label records without the model.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from ..feed.glossary import _FIELD_LABEL_EN

from .masking import _station_base_forms

__all__ = [
    "_HOUR_ONLY_CLOCK_RE", "_DECIMAL_COMMA_RE", "_normalise_for_translation", "_LABEL_RECORD_RE", "_MIN_LABELS_FOR_RECORD",
    "_split_label_record", "_RECORD_EXPECTED_UNTIL_RE", "_RECORD_BIS_VORAUSSICHTLICH_RE", "_gloss_record_values",
    "_REASON_LABEL_RE", "_join_record",
]


# German clock-time suffix. ``15:13 Uhr`` has no English equivalent —
# the bare time IS the English idiom — but Marian has to render the
# token somehow and picks the everyday-prose sense of the noun, so the
# EN feed published "Wr.Neustadt Hbf to 15:13 watch" and "to expected
# 23:59 clock" (``docs/archive/audits/audit-2026-09-09.md``,
# ``audit-2026-09-07.md``). Dropping the word together with its
# leading space BEFORE the model sees it removes the failure mode at
# the source; a glossary entry could not, because every English
# rendering of the token is wrong. Only a time-anchored ``Uhr`` is
# dropped, so ``Uhrzeit`` / ``Turmuhr`` / a standalone noun are
# untouched.
_CLOCK_SUFFIX_RE: re.Pattern[str] = re.compile(
    r"(?<=\d)(?<!\d\d\d)\s*Uhr\b|(?<=\d\d:\d\d)\s*Uhr\b"
)

# A clock time WL writes as a bare hour ("Voraussichtliche Dauer: 13 Uhr") or
# with a period ("13.50 Uhr"). Dropping only the "Uhr" left a bare number the
# English reader cannot take for a time: "Expected Duration: 13." and "The
# fault lasts expected to 16." (2026-10-03, under epoch 19). Written as
# ``13:00`` / ``13:50`` first, the time survives as a time. Anchored on the
# "Uhr", so a date ("02.11.2026") and a house number are untouched.
_HOUR_ONLY_CLOCK_RE: re.Pattern[str] = re.compile(
    r"(?<![\d.:])(\d{1,2})(?:\.(\d{2}))?(?=\s*Uhr\b)"
)

# A German decimal comma in a number with a unit ("24,2 min", "1,5 km"). The
# German feed writes it so (issue #2200, the Stammstrecke text read "24.2
# min"); English takes a point. The model sometimes turned the comma into a
# point itself and then rendered the rest of the sentence differently
# ("in the direction of" for "towards"), so the point is set before the model
# sees the text and the English stays as it was.
_DECIMAL_COMMA_RE: re.Pattern[str] = re.compile(
    r"(?<![\d,.])(\d+),(\d+)(?=\s*(?:min\b|Min\.|Minuten\b|km\b|m\b|%))"
)

# WL's stock referral closes on a separable "aus": "Weichen Sie ersatzweise
# auf die Linien U3, 5, 12 und 48A aus." The glossary renders the opening
# phrase; dropping the "aus" makes the line list the end of the sentence, so
# nothing is left for the model to re-attach around a list of placeholders.
_ALTERNATIVE_LINES_AUS_RE: re.Pattern[str] = re.compile(
    r"\b(Weichen Sie ersatzweise auf die Linien? [^!?]+?)\.?\s+aus\b"
)

# "Busse halten bei Haltestelle N71": the stop named after a line. The model
# dropped the noun ("Buses stop at N71", in the feed on 2026-10-03), which
# reads as if the buses stopped at the line. Written out as "Haltestelle der
# Linie", the glossary renders it whole.
_STOP_OF_LINE_RE: re.Pattern[str] = re.compile(
    r"\bbei (?:der )?Haltestelle (?=(?:U[1-6]|N\d{1,2}|\d{1,3}[A-Z]{0,2})\b(?![./-]\w))"
)


# WL's stop tickers name where the vehicles stop: a place right after the
# verb ("66A: Busse halten Salvatorianerplatz"), "bei" and a place, or "bei"
# and the stop of another line ("52: Züge halten bei Linien 6 und 18").
# The model rendered each with whatever preposition came to it, from one
# build to the next ("Buses stop Salvatorianerplatz" in 72 of the EN states
# sampled since 2026-09-01, "Buses stop at Salvatorianerplatz" in others,
# "Trains stop for lines 6 and 18", "Trains stop for Währinger Gürtel 164",
# "Buses stop on line 14A"; Jules audits 2026-10-02 and 2026-10-04). The
# German is rewritten into the forms the glossary renders whole: a place
# takes "an" ("buses stop at"), another line's stop is written out as
# "bei Haltestelle der Linie". Every other word after the verb ("in", "auf",
# "vor", "nach", "gegenüber", "Richtung", …) is left to the model, which
# renders those prepositions itself. A single vehicle may carry its line
# ("Ersatzbus 26E hält Karl-Waldbrunner-Platz", "Linie 26E hält Donaufelder
# Straße 148", "Ersatzbus 41E hält bei Währinger Str 200", in the WL data
# since September): with the line between noun and verb the place form was
# never written, and the model read "hält" as "holds" ("replacement bus 26E
# holds Karl-Waldbrunner-Platz ahead of …", 09.10.2026) or left the "at"
# out. "hält an" and "hält bei Haltestelle der Linie" are glossary forms of
# their own for that reason.
_STOP_LINE = r"(?:[A-Z]?\d{1,3}[A-Z]?|[A-Z])"
_STOP_VERB = (
    r"\b(?i:(Busse|Züge|Ersatzbusse) halten?"
    rf"|(Ersatzbus(?: {_STOP_LINE})?|Linie {_STOP_LINE}|Ersatzverkehr) hält)"
)
_STOP_AT_LINES_RE: re.Pattern[str] = re.compile(
    _STOP_VERB + r" (?:(?i:bei) )?(?:den )?(?i:Linien) (?=[A-Z0-9])"
)
_STOP_AT_LINE_RE: re.Pattern[str] = re.compile(
    _STOP_VERB + r" (?:(?i:bei) )?(?:der )?(?i:Linie) (?=[A-Z0-9])"
)
# A capitalised word after the verb is a place unless it is a preposition
# at the start of a ticker line ("Busse halten Auf der Hauptfahrbahn").
_STOP_AT_PLACE_RE: re.Pattern[str] = re.compile(
    _STOP_VERB
    + r" (?:bei (?!(?i:der |den )?(?i:Haltestellen?|Linien?)\b)"
    + r"|(?=[A-ZÄÖÜ])(?!(?i:Auf|Am|An|Bei|Beim|Im|In|Vor|Nach|Hinter|Neben|Gegenüber"
    + r"|Ggü|Vis|Richtung|Fahrtrichtung|Haltestellen?|Linien?|Nicht)\b))"
)


def _stop_verb(match: re.Match[str]) -> str:
    """``"Busse halten"`` / ``"Ersatzbus hält"`` with the typo ``halte`` repaired."""
    if match.group(1):
        return f"{match.group(1)} halten"
    return f"{match.group(2)} hält"


# "hält an" at the end of a clause is the other verb, "anhalten" ("Die
# Störung hält an."): it must not meet the glossary's "hält an" ("stops
# at"), which only a stop's place may follow.
_PERSISTS_RE: re.Pattern[str] = re.compile(r"\bhält an(?=\s*(?:[.,;:!?)]|$))")


def _normalise_stop_verbs(text: str) -> str:
    """Rewrite WL's stop tickers into the forms the glossary renders whole."""
    text = _PERSISTS_RE.sub("dauert an", text)
    text = _STOP_AT_LINES_RE.sub(
        lambda m: f"{_stop_verb(m)} bei den Haltestellen der Linien ", text
    )
    text = _STOP_AT_LINE_RE.sub(
        lambda m: f"{_stop_verb(m)} bei Haltestelle der Linie ", text
    )
    return _STOP_AT_PLACE_RE.sub(lambda m: f"{_stop_verb(m)} an ", text)


# WL's display-board shorthand, written out before the model sees it. The
# model kept "Li. 62", "Ri. Lainz" German ("Trains stop at Li. 31 Ri.
# Schottenring", 11 texts since July), read "Hlst." as "Mostly" ("38A: Mostly
# moved to Grinzinger Allee 47") and the cut "Fahrleitungsgebr" as "Boarding
# contact line". Each abbreviation only where WL's tickers use it: before a
# line, a place, or as the stub of a fault.
_WL_SHORTHAND_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bLi\.\s*(?=[A-Z0-9])"), "Linie "),
    (re.compile(r"\bRi\.\s*(?=[A-ZÄÖÜ])"), "Richtung "),
    (re.compile(r"\b(?:Hlst|Haltest)\.(?=\s)"), "Haltestelle"),
    (re.compile(r"\b(Fahrleitungs|Oberleitungs)(?:gebr\b\.?|-\s+gebrechen\b)"), r"\1gebrechen"),
    # The Stadt-Wien roadworks write a house number "ONr.67" or "in Höhe
    # ONr.42"; the model left the abbreviation German or made "ONo" of it.
    (re.compile(r"\b(?:in Höhe )?ONr\.\s*(?=\d)"), "Nr. "),
    # "Züge halten Steig A": the platform letter of a WL stop, "Ascent A" and
    # "Climb A" through the model.
    (re.compile(r"\bSteig (?=[A-H1-9]\b)"), "Bahnsteig "),
)

# Where the vehicles board, the stop of another line: "Einstieg bei Linie
# 44", "Einstieg Thaliastraße bei Linie 44". The model said "for line 44"
# (19 texts since July); written as "bei Haltestelle der Linie" the glossary
# renders it "at the stop of line", as for the stop verbs.
_BOARDING_AT_LINE_RE: re.Pattern[str] = re.compile(
    r"\b((?i:Einstieg|Ausstieg|Umstieg)(?: [A-ZÄÖÜ][\w.-]*){0,3}) bei (?:der )?Linie (?=[A-Z0-9])"
)
# A cross street behind "nach" in a stop ticker or a boarding place:
# "Busse halten Gumpendorfer Straße nach Kopernikusgasse" means just past it,
# like "vor" means just before it. The model read "according to".
_PLACE_AFTER_CROSS_STREET_RE: re.Pattern[str] = re.compile(
    r"\b((?:halten?|hält) an|(?i:Einstieg|Ausstieg|Umstieg)(?: bei| in der| beim)?)"
    r"((?: [A-ZÄÖÜ0-9][\w./-]*)+) nach (?=[A-ZÄÖÜ])"
)

# A stop the vehicles leave out: WL writes it "kann nicht eingehalten werden"
# or "hält die Station … ein", which the model rendered "cannot be
# maintained", "cannot comply with", "is not respected" and "stops the
# station" (13 texts since July). Rewritten into the plain German verbs
# "anfahren" and "halten in/an", the glossary renders "not served" and the model
# writes "stops at".
_STOP_NOT_KEPT_PLACE: dict[str, str] = {
    "Haltestelle": "an der Haltestelle",
    "Haltestellen": "an den Haltestellen",
    "Station": "in der Station",
    "Stationen": "in den Stationen",
}
# Who leaves the stop out, when WL puts it behind the verb: "die Linie U6",
# "die Züge der Linie U2", "die Busse der Linien 54A und 54B".
_STOP_RULE_SUBJECT = r"die (?:(?:Züge|Busse|Straßenbahnen) der )?Linien? \S+(?:(?:, | und )\S+)*"
_STOP_NOT_SERVED_RULES: tuple[
    tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]], ...
] = (
    (
        re.compile(r"\bkann((?: [^.!?;]*?)?) nicht eingehalten werden\b"),
        r"wird\1 nicht angefahren",
    ),
    (
        re.compile(r"\bkönnen((?: [^.!?;]*?)?) nicht eingehalten werden\b"),
        r"werden\1 nicht angefahren",
    ),
    (re.compile(r"\b(wird|werden)((?: [^.!?;]*?)?) nicht eingehalten\b"), r"\1\2 nicht angefahren"),
    # The subject behind the verb ("Derzeit können die Züge der Linie U6 die
    # Station Alser Straße in beiden Richtungen nicht einhalten", U1, U2 and
    # U6 since July) is put in front of it, so verb and place stand side by side
    # for the glossary ("die Züge der Linie U6 halten nicht in der Station …"
    # -> "do not stop at"); the direction follows the station. Written as
    # "halten die Züge der Linie U6 in beiden Richtungen nicht in der Station"
    # the model got nothing glossed, and what it wrote changed with the
    # placeholder nonce of the second pass ("stop towards Floridsdorf do not
    # stop at the station", "stop in the direction of Floridsdorf not in the
    # station", "are unable to meet the Stadlau station"; 16.10.2026). The
    # same order as ``_join_glossed_verb`` gives a passive.
    (
        re.compile(
            r"\b(kann|können) (" + _STOP_RULE_SUBJECT + r") die Station"
            r" ([^.!?;]+?) ((?:in beiden Richtungen|in Richtung [^.!?;]+?) )?nicht einhalten\b"
        ),
        lambda m: (
            f"{m[2]} {'hält' if m[1] == 'kann' else 'halten'} "
            f"nicht in der Station {m[3]}{' ' + m[4].rstrip() if m[4] else ''}"
        ),
    ),
    (
        re.compile(r"\b(hält|halten) (die Linie \S+|die Züge der Linie \S+) die Station ([^.!?;]+?) ein\b(?=\s*[.!;,]|\s*$)"),
        r"\1 \2 an der Station \3",
    ),
    # Every other "kann/können … die Haltestelle(n)/Station(en) … nicht
    # einhalten", the subject mostly in front of the verb: "Die Linie 66A
    # kann die Haltestelle Atzgersdorf, Kirchenplatz in Richtung Reumannplatz
    # nicht einhalten" became "Line 66A cannot follow the stop …" (13.10.2026,
    # still so with the real model on 15.10.), "… umgeleitet und können die
    # Haltestellen Klinik Ottakring und Waidäckergasse nicht einhalten". A
    # subject behind the verb goes in front of it, as above. "Station" is
    # spelt out with its plural: "Stationen?" took only "Statione(n)", and
    # "Die Linie U6 kann die Station Alser Straße nicht einhalten" stayed
    # unglossed (check of 2026-10-16).
    (
        re.compile(
            r"\b(kann|können) ((?:" + _STOP_RULE_SUBJECT + r") )?"
            r"die (Haltestellen?|Station(?:en)?) ((?:[^.!?;]|\.(?=\s?\d))+?) nicht einhalten\b"
        ),
        lambda m: (
            f"{m[2] or ''}{'hält' if m[1] == 'kann' else 'halten'} nicht "
            f"{_STOP_NOT_KEPT_PLACE[m[3]]} {m[4]}"
        ),
    ),
)

# German puts the participle at the end of the clause, away from its
# auxiliary: "Die Haltestellen … werden dadurch nicht angefahren", "wird die
# Linie 5A kurzgeführt", "Der linke Fahrstreifen wird in Fahrtrichtung …
# gesperrt", "muss … der rechte Ausgang … gesperrt werden". When the
# glossary renders only the participle, the model loses the auxiliary ("line
# 5A curtailed", "will be in the direction of the city closed") or doubles
# it ("can be be maintained"). Auxiliary and participle are put side by side,
# so the glossary renders the verb whole ("is curtailed", "must be closed").
# When the words between them open with an article, they are the subject
# ("wird die Linie 5A kurzgeführt") and stay in front of the verb; otherwise
# they are adverbs ("derzeit in beiden Fahrtrichtungen", "in der Zeit von …")
# and follow it, which is where English puts them.
_GLOSSED_PARTICIPLE = (
    r"kurz ?geführt|gesperrt|nicht angefahren|aufrecht ?gehalten|aufrecht ?erhalten"
)
_GLOSSED_VERB_MIDDLE = (
    r"((?:(?!\b(?:wird|werden|ist|sind|kann|können|muss|müssen|und|oder)\b)"
    r"(?:[^.!?;:]|\.(?=\d))){1,90}?)"
)
_GLOSSED_PARTICIPLE_RE: re.Pattern[str] = re.compile(
    r"\b(wird|werden) " + _GLOSSED_VERB_MIDDLE
    + r" (" + _GLOSSED_PARTICIPLE + r")\b(?! werden)"
)
_GLOSSED_MODAL_PARTICIPLE_RE: re.Pattern[str] = re.compile(
    r"\b(kann|können|muss|müssen) " + _GLOSSED_VERB_MIDDLE
    + r" (" + _GLOSSED_PARTICIPLE + r") werden\b"
)
_SUBJECT_ARTICLE_RE: re.Pattern[str] = re.compile(
    r"(?:die|der|das|den|dem|ein|eine|einer|kein|keine|alle)\b"
)


def _join_glossed_verb(match: re.Match[str]) -> str:
    """Auxiliary (or modal) and glossed participle side by side."""
    verb = match[1]
    middle = match[2]
    participle = match[0][match.start(3) - match.start(0):]
    if _SUBJECT_ARTICLE_RE.match(middle):
        return f"{middle} {verb} {participle}"
    return f"{verb} {participle} {middle}"


# WL's referral to another line by its bare number: "Umleitung zur
# Wattgasse, 10A ausweichen" ("diversion to avoid Wattgasse"). Written as
# WL's stock referral, the glossary renders "alternatively use line 10A".
_USE_LINE_INSTEAD_RE: re.Pattern[str] = re.compile(
    r"(?:(?<=^)|(?<=[,;:] )|\bauf (?:die )?(?:Linie )?)(U[1-6]|N?\d{1,3}[A-Z]?) ausweichen\b"
)


def _normalise_for_translation(text: str) -> str:
    """Strip German-only surface forms the NMT model cannot render.

    Runs BEFORE glossary substitution and entity masking in
    :func:`_translate_text_attempt`; the German feed is unaffected
    because the whole translation path is EN-only.
    """
    if not text:
        return text
    # The stop sign's form of a declined stop name, so that the masker
    # shields it ("bis Hohen Warte" → "bis Hohe Warte", see
    # ``_station_base_forms``).
    text = _station_base_forms(text)
    text = _DECIMAL_COMMA_RE.sub(r"\1.\2", text)
    text = _HOUR_ONLY_CLOCK_RE.sub(
        lambda m: f"{m.group(1)}:{m.group(2) or '00'}", text
    )
    for pattern, replacement in _WL_SHORTHAND_RULES:
        text = pattern.sub(replacement, text)
    text = _ALTERNATIVE_LINES_AUS_RE.sub(r"\1", text)
    text = _USE_LINE_INSTEAD_RE.sub(r"Weichen Sie ersatzweise auf die Linie \1", text)
    for rule, rewrite in _STOP_NOT_SERVED_RULES:
        text = rule.sub(rewrite, text)
    text = _GLOSSED_MODAL_PARTICIPLE_RE.sub(_join_glossed_verb, text)
    text = _GLOSSED_PARTICIPLE_RE.sub(_join_glossed_verb, text)
    text = _STOP_OF_LINE_RE.sub("bei Haltestelle der Linie ", text)
    text = _BOARDING_AT_LINE_RE.sub(r"\1 bei Haltestelle der Linie ", text)
    text = _normalise_stop_verbs(text)
    text = _PLACE_AFTER_CROSS_STREET_RE.sub(r"\1\2 unmittelbar nach ", text)
    return _CLOCK_SUFFIX_RE.sub("", text)


# Finds a WL field label. Built from :data:`_FIELD_LABEL_EN` so the splitter
# and the glossary can never disagree about what a label is.
#
# Sorted longest-first for symmetry with :func:`_domain_glossary_pattern`,
# where the order IS load-bearing. Here it is not: the required ``:`` makes
# the engine backtrack out of ``Haltestelle`` when the text says
# ``Haltestellen:``, so either order matches the same spans. Kept so the two
# patterns read alike, and stated plainly so nobody later "relies" on it.
_LABEL_RECORD_RE: re.Pattern[str] = re.compile(
    r"(?<!\w)(?:"
    + "|".join(
        re.escape(label)
        for label in sorted(_FIELD_LABEL_EN, key=len, reverse=True)
    )
    + r"):\s"
)

# How many labels make a run a *record* rather than a sentence that happens to
# end in one. Measured over 157 distinct published descriptions: 130 carry no
# label at all, 24 carry exactly one — almost all of them the ``Grund: …``
# tail, which translates correctly today and must keep going through the model
# as prose. Only 3 carry two or more, and those three are precisely the
# relocation items this split exists for.
_MIN_LABELS_FOR_RECORD = 2


def _split_label_record(text: str) -> tuple[str, str]:
    """Split *text* into prose and a trailing ``Label: value`` record.

    Returns ``(text, "")`` when there is no record, so the caller's normal
    path is untouched for the 154 of 157 published descriptions that have
    fewer than :data:`_MIN_LABELS_FOR_RECORD` labels.
    """
    matches = list(_LABEL_RECORD_RE.finditer(text))
    # The expected end the DE feed writes for WL's "Voraussichtliche Dauer:"
    # (``_expected_end_sentence``) opens the record in its place. Alone it is
    # a record too when it is the last sentence: like the label it replaced,
    # it is a table cell, and the model has nothing to add to it.
    until = list(_EXPECTED_UNTIL_RE.finditer(text))
    starts = sorted(m.start() for m in (*matches, *until))
    if len(starts) < _MIN_LABELS_FOR_RECORD and not (
        until and not matches and not _LETTER_SENTENCE_END_RE.search(text, until[0].end())
    ):
        return text, ""
    cut = starts[0]
    # WL's incident template opens its record with a qualified label,
    # "Voraussichtliche Dauer: 10:10 Uhr. Grund: …". Cut at "Dauer:" alone,
    # the qualifier stayed behind as the last word of the prose, and the model
    # either dropped it ("Duration: 10:10", 52 published descriptions) or
    # rendered it on its own ("Expected Duration: 10:10", 41). It belongs to
    # the label, which the glossary renders whole.
    qualifier = _LABEL_QUALIFIER_RE.search(text, 0, cut)
    if qualifier is not None:
        cut = qualifier.start()
    return text[:cut].rstrip(), text[cut:].strip()


# "Voraussichtlich bis 14:10 Uhr." at the start of a sentence: the DE
# feed's form of WL's "Voraussichtliche Dauer:" (``_expected_end_sentence``).
# Capitalised only, so prose that runs on with "… voraussichtlich bis …"
# (ÖBB, WL's "Die Störung dauert voraussichtlich bis …") stays prose.
_EXPECTED_UNTIL_RE: re.Pattern[str] = re.compile(r"(?<!\w)Voraussichtlich bis\s")
# A full stop behind a letter before a capital: a sentence end that a date's
# day ("19. September") is not.
_LETTER_SENTENCE_END_RE: re.Pattern[str] = re.compile(r"(?<=[^\W\d])\.\s+[A-ZÄÖÜ]")
# A record that opens with it is rendered as "bis …" ("Until 14:10",
# "Until approx. 22:00") and takes "Expected" in front, the English of the
# German sentence. The glossary would render the adverb alone, as
# "expected" before a German "bis".
_RECORD_EXPECTED_UNTIL_RE: re.Pattern[str] = re.compile(r"Voraussichtlich bis\s+")

# The adjective in front of a record's first label (see ``_split_label_record``).
_LABEL_QUALIFIER_RE: re.Pattern[str] = re.compile(r"(?<!\w)[Vv]oraussichtliche\s+$")


# What ``_render_label_record`` still left German once the labels and the
# nouns had gone through the glossary, measured over 557 distinct WL items
# (60 carry a record, 40 of them a ``Dauer:``)::
#
#     Duration: Ab 30. September 2026, etwa 13:00, bis expected Mai 2027
#     To: etwa 20 Meter in Richtung Eduard-Kittenberger-Gasse
#     Duration: Ab 3. Juli 2026 bis auf Widerruf
#
# The values follow a small grammar — a date preposition, a day, a month,
# "etwa", a clock time, one of four open-end phrases — and the model never
# sees them, so nothing else will translate them. These rules apply ONLY
# inside a record: "ab", "am", "bis", "vor" and "nach" are ordinary German in
# prose ("Betrieb ab Hütteldorf", "bei der Linie"), and a global glossary
# entry would hit every sentence. The date prepositions are anchored to the
# digit (or the "…" of a truncated value) that follows them, so the stop
# names "Am Schöpfwerk" and "Am Bahnhof" stay what they are.
#
# Order matters: a phrase is listed before the word it starts with, so
# "bis auf Widerruf" wins over "bis". The third field says whether the
# English keeps a fixed spelling (month names) or takes a capital only at
# the start of a value ("Duration: From …", but "…, until further notice").
_RECORD_MONTH_DE = (
    r"(?:J[äa]nner|Januar|Februar|März|April|Mai|Juni|Juli|August|September"
    r"|Oktober|November|Dezember)\b"
)
_RECORD_VALUE_RULES: tuple[tuple[str, str, bool], ...] = (
    (r"bis auf Widerruf", "until further notice", False),
    (r"auf (?:derzeit )?unbestimmte Zeit", "until further notice", False),
    (r"Dauer derzeit unbekannt", "duration currently unknown", False),
    (r"Dauer unbekannt", "duration unknown", False),
    (r"Nicht absehbar", "not foreseeable", False),
    (r"in Richtung", "towards", False),
    # A relocation's distance, measured as the third gap of the EN audit
    # 2026-10-03: "Approx. 30 metres gegen die Fahrtrichtung" and a value
    # ending "auf Dauer" stayed German.
    (r"gegen die Fahrtrichtung", "against the direction of travel", False),
    # "20 Meter in Fahrtrichtung Kagran": with a place after it the travel
    # direction is that place's.
    (r"in Fahrtrichtung(?=\s+[A-ZÄÖÜ])", "in the direction of", False),
    (r"in Fahrtrichtung", "in the direction of travel", False),
    (r"auf Dauer", "permanently", False),
    (r"gegen", "against", False),
    (r"im Zuge", "along", False),
    (r"provisorische Haltestelle", "temporary stop", False),
    # "Hartäckerstraße 65 → zur Haltestelle Döblinger Friedhof" (40A,
    # 06.10.2026) stayed German behind the arrow.
    (r"zur Haltestelle", "to the stop", False),
    (r"Nebenfahrbahn", "service road", False),
    # Where on the street a relocated stop stands. Every word that still
    # reached the EN feed German, over the 101 relocations WL published from
    # July to 23.10.2026 (Jules audits 18. and 22.10.: "Simmeringer
    # Hauptstraße opposite 120-122, Gleiskörper → …"). "unter" only small:
    # the record also holds names ("Unter-Laa").
    (r"Haltestelle Straßenbahn", "tram stop", False),
    (r"Gleiskörper", "track area", False),
    (r"Zufahrt zum Campingplatz", "the campsite access road", False),
    (r"Zufahrt", "access road", False),
    (r"Parkanlage", "the park", False),
    (r"Hauseinfahrt", "the building entrance", False),
    (r"Einfahrt", "the driveway", False),
    (r"Stiege", "staircase", False),
    (r"Richtung", "towards", False),
    (r"Ampel", "the traffic lights", False),
    (r"(?-i:unter)", "under", False),
    (r"ab(?=\s+(?:\d|…))", "from", False),
    (r"am(?=\s+\d)", "on", False),
    (r"von(?=\s+(?:\d|etwa|ca\.))", "from", False),
    (r"bis", "until", False),
    (r"etwa", "approx.", False),
    (r"ca\.", "approx.", False),
    (r"Ende", "end of", False),
    (r"heute", "today", False),
    # The values of "Voraussichtlich bis …" (``_expected_end_sentence``):
    # "Sonntag Betriebsschluss", "circa 22:00 Uhr", "Anfang September". Only
    # in front of a month, so the station "Wien Mitte" keeps its name.
    (r"circa", "approx.", False),
    (r"Anfang(?=\s+" + _RECORD_MONTH_DE + r")", "beginning of", False),
    (r"Mitte(?=\s+" + _RECORD_MONTH_DE + r")", "middle of", False),
    (r"Meter", "metres", False),
    (r"vor", "before", False),
    (r"nach", "after", False),
    (r"J[äa]nner|Januar", "January", True),
    (r"Februar", "February", True),
    (r"März", "March", True),
    (r"Mai", "May", True),
    (r"Juni", "June", True),
    (r"Juli", "July", True),
    (r"Oktober", "October", True),
    (r"Dezember", "December", True),
    (r"Montag", "Monday", True),
    (r"Dienstag", "Tuesday", True),
    (r"Mittwoch", "Wednesday", True),
    (r"Donnerstag", "Thursday", True),
    (r"Freitag", "Friday", True),
    (r"Samstag", "Saturday", True),
    (r"Sonntag", "Sunday", True),
)
_RECORD_VALUE_RE: re.Pattern[str] = re.compile(
    r"(?<!\w)(?:"
    + "|".join(f"(?P<r{i}>{pattern})" for i, (pattern, _, _) in enumerate(_RECORD_VALUE_RULES))
    + r")(?!\w)",
    re.IGNORECASE,
)
# "Eipeldauer Straße 12 bis 14" is a house-number range and WL writes it
# "12-14" elsewhere; a clock-time range ("09:00 bis 21:30") is not touched.
# Neither is the digit closing a label's placeholder: "<label> bis 19.
# September" came out "Expected-19 September".
_HOUSE_NUMBER_RANGE_RE: re.Pattern[str] = re.compile(
    r"(?<![\w:])(\d{1,4}[A-Za-z]?)\s+bis\s+(\d{1,4}[A-Za-z]?)(?![\d:])"
)
# "30. September" → "30 September": the day's period is German only. Runs
# after the month names are English, and needs the month to follow, so
# "1. Haidequerstraße" and "27.09.2026" keep their periods.
_DAY_PERIOD_RE: re.Pattern[str] = re.compile(
    r"\b(\d{1,2})\.(?=\s+(?:January|February|March|April|May|June|July|"
    r"August|September|October|November|December)\b)"
)


# Inside a record "bis voraussichtlich" is "bis etwa": the value rules then
# render "until approx." and capitalise it at the start of a value
# ("Duration: Until approx. 22:00"). The glossary phrase would win first and
# leave it small (audit 2026-09-25, A.3).
_RECORD_BIS_VORAUSSICHTLICH_RE: re.Pattern[str] = re.compile(
    r"(?<!\w)(bis)\s+voraussichtlich(?!\w)", re.IGNORECASE
)


# A relocation's place may name the entrance of a park, a cemetery or a
# building: "Filmteichstraße vor Nordeingang des Kurparks" (68A, 05.11.2026)
# read "Filmteichstraße before Nordeingang des Kurparks" in English, the only
# such value among the 247 distinct "Von:"/"Nach:"/"Dauer:" values in WL's
# cache since February 2026. Bound to the genitive or "zum"/"zur" behind
# it, so a stop that carries the word ("Kurpark Nordeingang", "Laaer Berg,
# Nordosteingang") stays its name. The name behind it keeps its German
# spelling in its base form, as the EN feed writes every name ("closure of
# Margaretengürtel"): "des Kurparks" → "of Kurpark".
_ENTRANCE_SIDES_EN: dict[str, str] = {
    "": "", "Nord": "north ", "Süd": "south ", "Ost": "east ", "West": "west ",
    "Nordost": "northeast ", "Nordwest": "northwest ", "Südost": "southeast ",
    "Südwest": "southwest ", "Haupt": "main ", "Seiten": "side ", "Neben": "side ",
    "Hinter": "rear ",
}
# The same holds for every way in or out of a place, not only the entrance
# on foot: "Würzburggasse nach Einfahrt zum Friedhof" (8A, 13.11.2026,
# KI-Wächter issue #2209) read "Würzburggasse after the driveway zum
# Friedhof", the word table knew "Einfahrt" but not what follows it. Each
# noun has its English here; "Zufahrt zum Campingplatz" keeps the wording of
# ``_RECORD_VALUE_RULES``. A bare "Ausgang" is already "exit" in the domain
# glossary before this pass runs, so only its compounds ("Seitenausgang des
# Bades") come here; an U-Bahn line behind the article ("Zugang zur U4") is
# a stop's name and stays.
_ACCESS_NOUNS_EN: dict[str, str] = {
    "eingang": "entrance", "ausgang": "exit", "zugang": "access", "einfahrt": "driveway",
    "ausfahrt": "exit", "zufahrt": "access road", "hauseinfahrt": "building entrance",
}
_ENTRANCE_OF_RE: re.Pattern[str] = re.compile(
    r"(?<![\w-])(?!Zufahrt\s+zum\s+Campingplatz)"
    r"(?P<side>Nordost|Nordwest|Südost|Südwest|Nord|Süd|Ost|West|Haupt|Seiten|Neben|Hinter)?"
    r"(?P<noun>(?(side)(?:eingang|ausgang|zugang|einfahrt|ausfahrt|zufahrt)"
    r"|(?:Eingang|Zugang|Einfahrt|Ausfahrt|Zufahrt|Hauseinfahrt)))"
    r"\s+(?P<article>des|der|zum|zur)\s+(?!U\d)(?P<name>[A-ZÄÖÜ][\wäöüß-]*)"
)


def _entrance_of_en(match: re.Match[str], label_placeholders: frozenset[str]) -> str:
    """``Nordeingang des Kurparks`` → ``the north entrance of Kurpark``, ``Einfahrt zum Friedhof`` → ``the driveway to Friedhof``."""
    name = match["name"]
    if match["article"] == "des" and len(name) > 4 and name.endswith("s"):
        stem = name[:-2] if name.endswith("es") and name[-3] not in "aeiouäöüs" else name[:-1]
        name = stem
    link = "to" if match["article"] in ("zum", "zur") else "of"
    noun = _ACCESS_NOUNS_EN[match["noun"].casefold()]
    english = f"{_ENTRANCE_SIDES_EN[match['side'] or '']}{noun} {link} {name}"
    before = match.string[: match.start()].split()
    if not before or before[-1] in label_placeholders:
        return english[0].upper() + english[1:]
    return f"the {english}"


def _gloss_record_values(text: str, label_placeholders: frozenset[str]) -> str:
    """Put the values of a glossed label record into English, without a model.

    *text* is the record after :func:`_apply_domain_glossary`, so the labels
    are already placeholders; *label_placeholders* names them, which is how
    a match knows it stands at the start of a value and takes a capital.
    """
    text = _HOUSE_NUMBER_RANGE_RE.sub(r"\1-\2", text)
    text = _ENTRANCE_OF_RE.sub(lambda m: _entrance_of_en(m, label_placeholders), text)

    def _replace(match: re.Match[str]) -> str:
        _, english, fixed = _RECORD_VALUE_RULES[int(str(match.lastgroup)[1:])]
        if fixed:
            return english
        before = match.string[: match.start()].split()
        at_value_start = not before or before[-1] in label_placeholders
        return english[0].upper() + english[1:] if at_value_start else english

    return _DAY_PERIOD_RE.sub(r"\1", _RECORD_VALUE_RE.sub(_replace, text))


# The reason of a record: WL's incident template ends with it ("Grund:
# Fremder Verkehrsunfall im Haltestellenbereich Broßmannplatz."). Unlike the
# rest of the record it is prose, and nothing deterministic renders a reason
# the glossary does not know: 54 of 60 such reasons since July stayed partly
# German in the EN feed ("Reason: Tiere im Gleis im Haltestellenbereich
# Reumannplatz"). See ``_render_label_record``.
_REASON_LABEL_RE: re.Pattern[str] = re.compile(r"(?<!\w)Grund:\s")


def _join_record(translated: str, record_en: str) -> str:
    """Re-attach a rendered record to the translated prose in front of it."""
    if not record_en:
        return translated
    if not translated:
        return record_en
    return f"{translated} {record_en}"
