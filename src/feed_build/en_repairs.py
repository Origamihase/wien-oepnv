"""English repairs after the model pass (articles, capitals, quotes).

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re

from ..feed.glossary import _GLOSSARY_BASE, _GLOSSARY_BY_CATEGORY, _GLOSSARY_BY_SOURCE

from .base import _LINE_PREFIX_RE
from .entities import _STREET_SUFFIX_RE
from .placeholders import _MASK_WORD_CHAR_RE

__all__ = [
    "_agreeing_article", "_ARTICLE_BEFORE_STREET_RE", "_street_names_nominative", "_drop_article_before_street",
    "_drop_article_before_line", "_fix_glossary_articles", "_entities_dropped_by_translation",
    "_redirected_towards", "_cause_due_to", "_english_weekdays_capitalised", "_capitalise_title_body",
    "_capitalise_sentence_start", "_capitalise_glossary_after_stop", "_drop_unpaired_quote",
]


def _agreeing_article(article: str, following: str) -> str:
    """Return ``a``/``an`` agreeing with *following*, keeping *article*'s case."""
    want = "an" if following[:1].lower() in "aeiou" else "a"
    return want.capitalize() if article[:1].isupper() else want


# German names its transit lines with an article — ``die Linie 17A``,
# ``die Linien 36A und 36B`` — and the model renders that faithfully.
# English transit usage does not: it is "line 17A is diverted", the way
# Wiener Linien write it on their own English pages. The article is not a
# model error, it is a convention difference the model cannot resolve,
# because the identifier after it is a masked placeholder.
#
# Measured over 294 published EN items::
#
#     the lines 36A and 36B are being redirected
#     The line 79B is redirected in both directions
#     trains stop on the lines 1, 18, 62 WLB, the line O
#     the folding ramps of the underground trains of the line U4
#
# 11 occurrences of ``the line <X>`` across 10 items, plus 12 items with
# ``the lines …`` — together 22 of 294.
#
# The lookahead for an identifier is what keeps the rule honest: ``the
# line is divided`` and ``at the end of the line`` are ordinary English and
# stay. Only an article directly in front of ``line``/``lines`` plus a
# line identifier is dropped. In ``the lines 86A, 87A and the call bus
# 86A`` exactly the first article goes.
_ARTICLE_BEFORE_LINE_RE: re.Pattern[str] = re.compile(
    r"(?<!\w)([Tt])he\s+([Ll]ines?)\s+(?=[A-Z0-9])"
)


# The same convention gap, one word class over: German articles its street
# and square names — ``in der Althanstraße``, ``zum Karlsplatz`` — and
# English leaves them bare. Published::
#
#     Because of roadworks in the Kästenbaumgasse, …
#     Trains will be redirected to the Karlsplatz.
#
# 5 occurrences across 310 published EN items. Only the ARTICLE is
# dropped; the preposition is left exactly as the model chose it. Whether
# "in Althanstraße" should read "on Althanstraße" is a separate question
# with a far less certain answer, and this rule does not touch it.
#
# The test for "is this a street name" is :data:`_STREET_SUFFIX_RE` — the
# very pattern the masker uses to shield these names in the first place,
# reused rather than re-invented as a second suffix list that could drift
# away from it. That is also what keeps the rule off the cases where the
# article is defensible English: ``the Ernst-Happel-Stadion`` and ``the
# Wiener Linien`` are not street names and stay untouched.
_ARTICLE_BEFORE_STREET_RE: re.Pattern[str] = re.compile(
    r"(?<!\w)[Tt]he\s+(?=" + _STREET_SUFFIX_RE.pattern + r")"
)


# German declines its street names, English does not: "Streckensperre des
# Algenwegs" is "closure of Algenweg", the name as the street sign spells it.
# The masker shields the genitive form whole (``_STREET_GENITIVE_SUFFIX``),
# so the restore hands back the base form. Only a surface whose base form is
# itself a street name by :data:`_STREET_SUFFIX_RE` changes.
_STREET_GENITIVE_END_RE: re.Pattern[str] = re.compile(
    r"(?:(?<=[Ww]eg)|(?<=[Mm]arkt)|(?<=[Ss]tieg)|(?<=[Ss]teig)|(?<=[Dd]amm)"
    r"|(?<=[Rr]ing))(?:es|s)$|(?<=[Pp]latz)es$"
    r"|(?:(?<=[Kk]ai)|(?<=[Gg]raben)|(?<=[Gg]ürtel))s$"
)


def _street_names_nominative(mapping: dict[str, str]) -> dict[str, str]:
    """Return *mapping* with genitive street names in their base form."""
    result = dict(mapping)
    for placeholder, surface in mapping.items():
        if not placeholder.startswith("XENT") or not _STREET_SUFFIX_RE.fullmatch(surface):
            continue
        base = _STREET_GENITIVE_END_RE.sub("", surface)
        if base != surface and _STREET_SUFFIX_RE.fullmatch(base):
            result[placeholder] = base
    return result


def _drop_article_before_street(text: str) -> str:
    """Drop the definite article in front of a German street or square name."""
    return _ARTICLE_BEFORE_STREET_RE.sub("", text)


def _drop_article_before_line(text: str) -> str:
    """Drop the definite article in front of ``line <identifier>``.

    Sentence-initial ``The line D is divided.`` becomes ``Line D is
    divided.`` — the capital moves onto the noun rather than being lost.
    """

    def _swap(match: re.Match[str]) -> str:
        article, noun = match.group(1), match.group(2)
        if article.isupper():
            noun = noun[0].upper() + noun[1:]
        return noun + " "

    return _ARTICLE_BEFORE_LINE_RE.sub(_swap, text)


def _fix_glossary_articles(text: str, mapping: dict[str, str]) -> str:
    """Repair ``a``/``an`` in front of a word the glossary put there.

    The model is not being careless here — it is being correct about what it
    was shown. Every placeholder starts with ``X``, whose letter name begins
    with a vowel sound, so "an XGLO…X0X" is the right article for the token
    in front of it. Unmasking then swaps in a consonant-initial English term
    and strands the article. Published 2026-09-18::

        Due to an switch fault between Tullnerfeld … and Wien Meidling …
        Due to an demonstration in the area of Schwarzenbergplatz and Ring
        After an service obstruction there are different intervals.

    Measured over 314 published EN texts: 15 occurrences, three distinct
    words — ``service obstruction`` (11), ``switch fault`` (3),
    ``demonstration`` (1) — and every one of them a glossary value. Against
    that, the eight article pairs the model got right are left alone.

    Restricted to glossary substitutions on purpose. The naive letter rule
    is safe for all 97 glossary values (checked: the only vowel-lettered
    entries are genuinely vowel-sounding, and none begin with a silent
    ``h``), but it is NOT safe in general — "a U-Bahn" and "an hour" both
    go the other way, and entity placeholders restore exactly that kind of
    token (``U6``, ``S45``). Prose the model wrote itself is never touched.
    """
    for placeholder, value in mapping.items():
        if not placeholder.startswith("XGLO") or not value:
            continue
        pattern = re.compile(
            r"\b(an?)(\s+)(" + re.escape(value) + r")", re.IGNORECASE
        )
        # ``value`` is bound as a default so the closure cannot read a later
        # iteration's value (ruff B023). ``sub`` runs immediately here, so the
        # two are equivalent today — the binding keeps them equivalent if the
        # call ever becomes lazy.
        def _agree(match: re.Match[str], term: str = value) -> str:
            return (
                _agreeing_article(match.group(1), term)
                + match.group(2)
                + match.group(3)
            )

        text = pattern.sub(_agree, text)
    return text


def _entities_dropped_by_translation(
    masked_text: str, translated: str, mapping: dict[str, str]
) -> list[str]:
    """Return the verbatim entities the model failed to carry through.

    :func:`_unmask_entities` is deliberately tolerant of a dropped
    placeholder: it restores what it finds and discards the rest, so no raw
    sentinel can reach a subscriber. That tolerance is right for the output
    and wrong as a verdict on the translation — a dropped placeholder is a
    dropped *fact*, and the sentence that survives reads as authoritative.

    Published 2026-09-18, ``docs/feed.en.xml``::

        DE: Von: 1. Haidequerstraße 2   Nach: 1. Haidequerstraße 510
        EN: From: 1. Haidequerstraße 510   Duration: From 1. Haidequerstraße 510

    The model dropped the placeholder holding ``2`` and looped on the rest.
    The English does not merely lose the origin — it presents the
    destination as the origin, so a reader is told the stop is moving away
    from number 510. Nothing in the pipeline objected, because every
    placeholder still present unmasked cleanly.

    Only ``XENT`` (verbatim) placeholders count: a ``XGLO`` glossary term is
    *supposed* to come back as different text. Only surfaces carrying a word
    character count, which is the calibration that makes the check usable —
    over 400 published DE/EN pairs, requiring every mask to survive fires on
    12.5% of them, 21 of those losses being nothing but a dropped ``…`` or
    ``–``. Restricted to word-bearing surfaces it fires on 7.2%, and those
    are exactly three distinct items: the two above and a ``Justgasse`` the
    model swallowed.
    """
    dropped: list[str] = []
    for placeholder, surface in mapping.items():
        if not placeholder.startswith("XENT"):
            continue
        if not _MASK_WORD_CHAR_RE.search(surface):
            continue
        if translated.count(placeholder) < masked_text.count(placeholder):
            dropped.append(surface)
    return dropped


# "Die Linie 65A wird in Richtung Wienerberg City zwischen … umgeleitet": the
# model wrote "Line 65A is redirected to Wienerberg City between …" in 16 of
# the 814 distinct model inputs since July with the short placeholders, and in
# 24 of the 64 such EN lines the feed published since July under the nonce, as
# if the line now ran there. "in Richtung" is the direction the diverted line
# travels in. Glossing it ("XGLO1X") made it worse in the real-model probe
# ("redirected to towards", "the towards Aspern buses"), so the verb's
# preposition is set right after the model instead, only when the German says
# "in Richtung" and "umgeleitet".
_REDIRECTED_TO_RE: re.Pattern[str] = re.compile(
    r"\b(redirected|diverted|rerouted) to (?!wards\b)(?=\S)"
)


def _redirected_towards(masked_text: str, english: str) -> str:
    """"redirected to X" → "redirected towards X" for a German "in Richtung X … umgeleitet"."""
    if "in Richtung" not in masked_text or "umgeleitet" not in masked_text:
        return english
    return _REDIRECTED_TO_RE.sub(r"\1 towards ", english)


# A cause reads "Due to …" everywhere in the EN feed (operator decision
# 2026-10-05). The model renders "Wegen" as "Because of" as often as "Due
# to", from one input to the next: of the 309 distinct sentences the EN feed
# opened with a cause between July and 2026-10-08, 72 began "Because of"
# ("Because of crane works … line 63A is redirected.", "Because of roadworks
# in the Giefinggasse …, the 29B buses …"), three more said "because of"
# mid-sentence, and the ÖBB and WL cause renderers took either as a cause.
# The class is the model's choice of synonym, so the synonyms of "due to"
# become "due to" after the model, wherever they stand; "following", "after"
# and "as a result," (no "of") are no cause prepositions and stay. Cached
# values get the same treatment on the way out (``_cached_translation``),
# so no epoch bump and no re-translation is needed. "in view of" never came
# out of the model so far, but ``_CAUSE_EN_RE`` takes it as a cause, so it
# is rewritten like the others (EN check 2026-10-08).
_CAUSE_SYNONYM_EN_RE: re.Pattern[str] = re.compile(
    r"\b(?:because of|owing to|on account of|as a result of|in view of)\b", re.IGNORECASE
)


def _cause_due_to(english: str) -> str:
    """Every "because of"/"owing to"/… as "due to", keeping the first capital."""
    return _CAUSE_SYNONYM_EN_RE.sub(
        lambda m: "Due to" if m.group(0)[:1].isupper() else "due to", english
    )


# English writes its weekdays with a capital. The model now and then does
# not: "(jeweils Samstag, Sonntag und Feiertag)" came back "(saturday, Sunday
# and holiday)" (ÖBB, Wien Floridsdorf–Flughafen, June 2026; second check of
# #2072 on 27.10.2026, since ÖBB's time window stays in the sentence). Only
# the seven weekday names: lower-case "may" or "march" are English words.
# Cached values get the same treatment on the way out, like ``_cause_due_to``.
_LOWER_WEEKDAY_EN_RE: re.Pattern[str] = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
)


def _english_weekdays_capitalised(english: str) -> str:
    """*english* with "saturday" as "Saturday"."""
    return _LOWER_WEEKDAY_EN_RE.sub(lambda m: m.group(0).capitalize(), english)


def _capitalise_title_body(title: str) -> str:
    """Upper-case the first letter of an EN title's body.

    German capitalises every noun, so a headline like ``44: Veranstaltung``
    or ``3A: Busse halten …`` starts its body with a capital. The English
    equivalents are ordinary common nouns, and the NMT model renders them in
    lower case — giving ``44: event`` and ``3A: buses stop …``. After the
    ``NN:`` line prefix that reads like a typo, and it is the first thing an
    English subscriber sees on every item.

    Only the single leading letter is touched. ``str.capitalize`` would lower
    the rest and destroy line codes, station names and acronyms
    (``46/49/52: Track construction works`` must not become ``… works`` with
    a lower-cased ``Kärntner Ring``). A body that starts with a digit or
    symbol is left alone, as is one that is already capitalised.
    """
    if not title:
        return title
    match = _LINE_PREFIX_RE.match(title)
    start = match.end() if match else 0
    return title[:start] + _capitalise_sentence_start(title[start:])


def _capitalise_sentence_start(text: str) -> str:
    """Upper-case the first letter of an EN text that opens in lower case.

    German capitalises every noun, so a description that opens with one —
    "Haltestellenverlegung der Linie 7A …", "Intervallschwankungen …" —
    starts with a capital. The glossary renders those nouns as English
    common nouns in lower case, because the same words also occur
    mid-sentence, and nothing after the unmask capitalised the sentence
    start: the C.3 fix (:func:`_capitalise_title_body`) reached the title
    only. Published 2026-09-24 in ``docs/feed.en.xml``, 5 of 10 items::

        stop relocation of line 7A towards Meidling Hauptstraße U …
        stop closure of line N31 towards Schwedenplatz U …

    Over the 169 distinct EN descriptions published since 2026-09-10, 45
    opened in lower case while their German source did not ("irregular
    intervals" 22 times, "stop relocation" 14).

    Only the single leading letter is touched — ``str.capitalize`` would
    lower the rest and destroy line codes, station names and acronyms. A
    text that starts with a digit, a symbol or a capital is left alone.
    """
    if not text or not text[0].islower():
        return text
    return text[0].upper() + text[1:]


def _glossary_term_after_stop_pattern() -> re.Pattern[str]:
    """Every lower-case glossary value, right after a sentence end."""
    values = set(_GLOSSARY_BASE.values())
    for overlay in (*_GLOSSARY_BY_SOURCE.values(), *_GLOSSARY_BY_CATEGORY.values()):
        values.update(overlay.values())
    # Only the first letter changes, so the order of the terms is irrelevant;
    # sorted for a stable pattern.
    terms = sorted(v for v in values if v[:1].islower())
    return re.compile(r"(?<=[.!?]\s)(?:" + "|".join(map(re.escape, terms)) + r")(?!\w)")


_GLOSSARY_TERM_AFTER_STOP_RE: re.Pattern[str] = _glossary_term_after_stop_pattern()
# The period of an abbreviation ends no sentence: "Meidlinger Hauptstr. ggü.
# 197" came back as "Hauptstr. opp 197" and must not become "Opp".
_ABBREVIATION_BEFORE_RE: re.Pattern[str] = re.compile(
    r"(?:\w*str|(?<!\w)(?:approx|ca|bzw|nr|ggü|opp))\.\s$", re.IGNORECASE
)


def _capitalise_glossary_after_stop(text: str) -> str:
    """Upper-case a glossary term that opens a sentence inside an EN text.

    Glossary values are lower-case because the same words occur
    mid-sentence, and :func:`_capitalise_sentence_start` reaches only the
    start of the whole text. A German sentence that opens with the term keeps
    it small in the middle of the English one (audit 2026-09-25, A.4)::

        … in both directions via track 2. expected duration: 10:10 …
        … over lines 42 and 9. diversion to Gersthof …

    115 cached values carried ". expected". Only glossary values are raised,
    so an ordinary word ("approx. every") stays as the model wrote it, and
    not after an abbreviation ("Hauptstr. opp 197"). Applied at render time:
    cached values need no epoch bump.
    """

    def _raise(match: re.Match[str]) -> str:
        term = match.group(0)
        if _ABBREVIATION_BEFORE_RE.search(text, 0, match.start()):
            return term
        return term[0].upper() + term[1:]

    return _GLOSSARY_TERM_AFTER_STOP_RE.sub(_raise, text)


def _drop_unpaired_quote(text: str) -> str:
    """Remove a lone unmatched ``"`` from translated output.

    The NMT model occasionally opens a quotation it never closes. Observed
    live on 2026-09-13, ``docs/feed.en.xml`` item 8::

        DE  … Hadikgasse, auf Seite Otto Wagner Hofpavillon…
        EN  … Hadikgasse, on page "Otto Wagner Hofpavillon

    The title was 96 characters against a 256-character limit, so this is not
    our truncation cutting a closing quote off — the model simply emitted an
    opener and dropped the trailing ellipsis.

    An odd number of ``"`` is unambiguously wrong regardless of how the
    sentence is worded, so dropping the unmatched one is a safe repair that
    needs no judgement about meaning. The LAST quote is removed: it is the one
    left open. Balanced text (including text with no quotes at all) is
    returned untouched, and only the ASCII quote is considered — the German
    typographic pair is not something the model has been seen to unbalance,
    and inventing handling for it would be guesswork.
    """
    if text.count('"') % 2 == 0:
        return text
    cut = text.rfind('"')
    if cut < 0:  # pragma: no cover - unreachable while the count is odd
        return text
    return (text[:cut] + text[cut + 1:]).strip()
