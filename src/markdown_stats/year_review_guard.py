"""The guard of the year's Baustellen review: the rules a model's text must pass.

Moved verbatim out of ``src/markdown_stats/year_review.py`` (which still
offers every name another file imports or a test reads): the limits and closed lists of the rules a to j, one
helper per rule (``_length_reasons`` to ``_language_reasons``),
:func:`count_sentences` and :func:`check_review`, which gives the reasons
why a text may not be published.
"""
from __future__ import annotations

import itertools
import re
from collections.abc import Sequence
from typing import Final

from src.markdown_stats.baustellen_year import (
    DISTRICT_NAMES, MONTH_NAMES, BaustelleSite, district_numbers, fact_counts, facts_block, months_touched,
)
from src.markdown_stats.year_review_prompt import source_text
from src.utils.vienna_clock import PLAUSIBLE_YEARS

__all__ = [
    "MAX_REVIEW_CHARS",
    "MAX_SENTENCES",
    "MIN_GERMAN_SHARE",
    "MIN_REVIEW_CHARS",
    "MIN_SENTENCES",
    "STREET_SUFFIXES",
    "check_review",
    "count_sentences",
]

MIN_REVIEW_CHARS: Final = 200
MAX_REVIEW_CHARS: Final = 1500
MIN_SENTENCES: Final = 3
MAX_SENTENCES: Final = 8

# Other names of a month a German text may use.
_MONTH_ALIASES: Final = {"Januar": 1, "Feber": 2}
_MONTH_NUMBER: Final[dict[str, int]] = {**{name: number for number, name in enumerate(MONTH_NAMES, 1)}, **_MONTH_ALIASES}

# Characters that would act as Markdown or HTML on the page, or as Liquid;
# "$$" opens kramdown's math (review of 2026-11-10).
_MARKUP_CHARS: Final = "<>[]{}|`*_#\\$"
# A list marker that opens the text. clean_review makes a list answer one
# line, and Markdown would read the whole review as one list item.
_LIST_START_RE: Final = re.compile(r"(?:[-+]|\d+[.)])\s")
# A token of letters and digits; "." "," ":" join it only between two
# digits (14.08.2026, 20:00, 3,00), so "U4-Trasse" gives "U4" and "ONr.67"
# gives "67".
_TOKEN_RE: Final = re.compile(r"[^\W_]+(?:(?<=\d)[.,:](?=\d)[^\W_]+)*")
_FULL_DATE_RE: Final = re.compile(r"(\d{1,2}\.\d{1,2})\.(\d{4})")
# A word: letters, with hyphens inside ("Ludwig-Koeßler-Platz").
_WORD_RE: Final = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")
# The endings of rule d: the last parts of Vienna's street names.
STREET_SUFFIXES: Final[tuple[str, ...]] = (
    "straße", "strasse", "gasse", "platz", "ring", "gürtel", "brücke", "kai",
    "weg", "allee", "markt", "steg", "zeile", "lände", "damm", "ufer",
)
# Ordinary words with such an ending that a review of roadworks uses; alone
# they name no street, also when they open a sentence (review of 2026-11-10:
# "Radweg", "Einbahnstraße", "Gelände", "Durchweg", "Gering"). "Ring", "Gürtel"
# and "Kai" are left out on purpose: alone they name streets of Vienna.
_ORDINARY_WORDS: Final = frozenset(
    "straße strasse gasse platz weg brücke markt allee steg zeile damm ufer hinweg umweg radweg gehweg schutzweg "
    "fußweg fahrweg verkehrsweg gelände parkplatz stellplatz einbahnstraße nebenstraße sackgasse "
    "durchweg vorweg rundweg schlechtweg glattweg kurzweg gering".split()
)
# Capitalised words ending in "er" that open a sentence and are no part of
# a street name ("Der Gürtel …"); inside a sentence they are lower case
# and never taken for one.
_NOT_A_NAME_PART: Final = frozenset(
    {"Der", "Dieser", "Jeder", "Einer", "Aber", "Oder", "Unter", "Über", "Hinter", "Außer", "Immer", "Wieder", "Weiter"}
)
# Words before a full stop that do not end a sentence.
_ABBREVIATIONS: Final = frozenset({"bzw", "ca", "nr", "onr", "dr", "st", "inkl", "ggf", "evtl", "vgl", "sog", "usw", "etc", "str", "tel"})
_SENTENCE_END_RE: Final = re.compile(r"(\S+?)([.!?]+)(?=\s|$)")
_SHORT_NUMBER_RE: Final = re.compile(r"\d{1,2}(?:\.\d{1,2})*")
_NEXT_WORD_RE: Final = re.compile(r"\s+([^\W\d_]+)")
# Capitalised words after "3." that continue the sentence ("3. Bezirk", "4. Mai").
_AFTER_ORDINAL: Final = frozenset({"Bezirk", "Bezirks", "Bezirkes", "Bauphase", "Halbjahr", "Quartal", *_MONTH_NUMBER})
# A number in digits directly before "Baustelle(n)" (rule h); the tail of a
# date, a time or a decimal number is none (a year: :func:`_count_reasons`).
_SITE_COUNT_RE: Final = re.compile(r"(?<![\w.,:])(\d+)\s+Baustellen?(?![\w-])")
# Items a reason names before it only counts the rest: a reason is one log
# line and one line of the probe's report.
_MAX_NAMED: Final = 5
# First person, address and advice (rule g): the text has no author who
# speaks ("ich" and its forms), the project does not speak in the city's name
# or its own ("wir", "uns", "unser"), and the reader is neither addressed
# ("du", "euch") nor advised ("Sie sollten", "bitte"). The list is closed and
# small on purpose: each entry is a form a review of roadworks has no use for.
# "ihr" (also "her", "their"), "sollten" and "empfiehlt" stay out: they stand
# in neutral sentences, and the city writes "Die empfohlene Route".
_VOICE_MARKERS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("ich", re.compile(r"(?<!\w)(?:ich|mich|mir|mein(?:e[mnrs]?)?)(?!\w)", re.IGNORECASE)),
    ("wir", re.compile(r"(?<!\w)(?:wir|uns|uns(?:er|re)(?:e?[mnrs]?)?)(?!\w)", re.IGNORECASE)),
    ("du", re.compile(r"(?<!\w)(?:du|dich|dir|dein(?:e[mnrs]?)?|euch|euer|eure[mnrs]?)(?!\w)", re.IGNORECASE)),
    ("Sie sollten", re.compile(r"(?<!\w)Sie\s+sollten(?!\w)", re.IGNORECASE)),
    ("bitte", re.compile(r"(?<!\w)bitte(?!\w)", re.IGNORECASE)),
)
# Words that judge and describe nothing (rule i). Closed like rule g: none has
# a use in a neutral sentence about roadworks; a word outside the list passes.
_JUDGEMENT_RE: Final = re.compile(
    r"(?<!\w)(?:leider|bedauerlich\w*|erfreulich\w*|glücklicherweise|ärgerlich\w*|vorbildlich\w*"
    r"|katastrophal\w*|\w*chaos\w*|zumutung\w*|skandal\w*|versag\w*)(?!\w)",
    re.IGNORECASE,
)
# Frequent German function words (rule j). Share of them, measured on
# 2026-11-11 with the ledger of 2026: 25 correct texts 0.33 to 0.52 (the real
# answer of 2026-11-10 0.35), the city's descriptions 0.19 or more, an
# English text 0.06, a text half in English 0.26 (passes, see check_review).
_GERMAN_WORDS: Final = frozenset(
    "der die das den dem des und in im mit für von zu zur zum auf an am bei wurde wurden war waren ist sind "
    "ein eine einer einem einen sowie oder auch nicht sich als aus bis nach über unter vor durch wegen".split()
)
MIN_GERMAN_SHARE: Final = 0.15


# ---- the model's text ---------------------------------------------------------


def _opens_sentence(text: str, position: int) -> bool:
    """Whether the word after *position* is capitalised and no part of an ordinal (``3. Bezirk``, ``4. Mai``)."""
    following = _NEXT_WORD_RE.match(text, position)
    return following is not None and following.group(1)[0].isupper() and following.group(1) not in _AFTER_ORDINAL


def count_sentences(text: str) -> int:
    """How many sentences *text* has, counted by their closing punctuation: a loose bound, not grammar.

    A full stop after one letter or a common abbreviation closes no
    sentence; after a number of one or two digits only when a capitalised
    word follows that does not continue an ordinal (``3. Bezirk``, ``4.
    Mai``). Until the review of 2026-11-10 "Im Juli begannen 9. In
    Favoriten lagen 4." counted as one sentence. Left: "im 2. Abschnitt"
    counts one sentence too many.
    """
    count = 0
    last = 0
    for match in _SENTENCE_END_RE.finditer(text):
        word = match.group(1).lstrip("(„\"'‚»«")
        if "." in match.group(2) and "!" not in match.group(2) and "?" not in match.group(2):
            if _SHORT_NUMBER_RE.fullmatch(word):
                if not _opens_sentence(text, match.end()):
                    continue
            elif len(word) == 1 or word.lower() in _ABBREVIATIONS:
                continue
        count += 1
        last = match.end()
    return count + (1 if text[last:].strip() else 0)


def _reason(what: str, items: Sequence[str]) -> list[str]:
    """One reason line that names up to :data:`_MAX_NAMED` of *items*; none without items."""
    if not items:
        return []
    rest = len(items) - _MAX_NAMED
    return [f"{what}: {', '.join(items[:_MAX_NAMED])}" + (f" und {rest} weitere" if rest > 0 else "")]


def _length_reasons(text: str) -> list[str]:
    """Rule a: 200 to 1,500 characters, 3 to 8 sentences."""
    reasons: list[str] = []
    if not MIN_REVIEW_CHARS <= len(text) <= MAX_REVIEW_CHARS:
        reasons.append(f"Länge {len(text)} Zeichen, erlaubt sind {MIN_REVIEW_CHARS} bis {MAX_REVIEW_CHARS}")
    sentences = count_sentences(text)
    if not MIN_SENTENCES <= sentences <= MAX_SENTENCES:
        noun = "Satz" if sentences == 1 else "Sätze"
        reasons.append(f"{sentences} {noun}, erlaubt sind {MIN_SENTENCES} bis {MAX_SENTENCES}")
    return reasons


def _markup_reasons(text: str) -> list[str]:
    """Rule b: no markup characters, no list marker at the start and no link."""
    reasons = _reason("Formatierungszeichen im Text", sorted({char for char in text if char in _MARKUP_CHARS}))
    if _LIST_START_RE.match(text):
        reasons.append("Aufzählungszeichen am Anfang")
    lowered = text.lower()
    if "http" in lowered or "www." in lowered:
        reasons.append("Link im Text")
    return reasons


def _digit_tokens(text: str) -> set[str]:
    """Every token of *text* that contains a digit, and the short form and the year of a full date.

    ``25.05.2026`` also gives ``25.05`` (what ``25.05.`` in a sentence
    reads as) and ``2026``: "bis 2027" was rejected for sites that end on
    31.08.2027 (review of 2026-11-10). A day or a month alone is not derived.
    """
    tokens = {token for token in _TOKEN_RE.findall(text) if any(char.isdigit() for char in token)}
    for token in list(tokens):
        full_date = _FULL_DATE_RE.fullmatch(token)
        if full_date:
            tokens.update(full_date.groups())
    return tokens


def _number_reasons(text: str, allowed: str) -> list[str]:
    """Rule c: every token with a digit stands in the same form in *allowed* (data and facts)."""
    return _reason("Zahl oder Kennung nicht in den Daten", sorted(_digit_tokens(text) - _digit_tokens(allowed)))


def _is_name_part(word: str) -> bool:
    """Whether *word* before a street word belongs to the name (``Wiedner`` in ``Wiedner Hauptstraße``)."""
    return len(word) >= 4 and word[0].isupper() and word.endswith("er") and word not in _NOT_A_NAME_PART


def _street_names(text: str) -> list[str]:
    """The street-like names in *text*: one word, or two when a word in ``-er`` stands before it.

    Only a capitalised word is read as a name: "hinweg" and "gering" were
    taken for streets until the review of 2026-11-10. A single word of
    :data:`_ORDINARY_WORDS` is none in any case ("Durchweg wurden …");
    behind a word in ``-er`` it is ("Mariahilfer Straße"), so "Neuer Radweg"
    at the start of a sentence is still read as a name.
    """
    names: list[str] = []
    previous: re.Match[str] | None = None
    for match in _WORD_RE.finditer(text):
        word = match.group()
        if word[0].isupper() and word.lower().endswith(STREET_SUFFIXES):
            joined = previous is not None and text[previous.end():match.start()] == " "
            if previous is not None and joined and _is_name_part(previous.group()):
                names.append(f"{previous.group()} {word}")
            elif word.lower() not in _ORDINARY_WORDS:
                names.append(word)
        previous = match
    return names


def _street_reasons(text: str, source: str) -> list[str]:
    """Rule d: every street-like name of the text stands in the source text.

    A word counts as street-like when it starts with a capital letter and
    ends in one of :data:`STREET_SUFFIXES` (see :func:`_street_names` for
    what is exempt); another noun with such an ending ("Vorplatz") must
    stand in the data as well. One word is looked up as a word, a
    two-word name as two neighbouring words.
    """
    found = list(_WORD_RE.finditer(source))
    words = {match.group().lower() for match in found}
    pairs = {
        f"{first.group().lower()} {second.group().lower()}"
        for first, second in itertools.pairwise(found)
        if source[first.end():second.start()] == " "
    }
    missing = {name for name in _street_names(text) if name.lower() not in (pairs if " " in name else words)}
    return _reason("Straßenname nicht in den Daten", sorted(missing))


def _named(name: str, text: str) -> bool:
    """Whether *name* stands in *text* as a word of its own (``Wieden``, not ``Wiedner``).

    Two usual forms count as the name: ``Wien-Liesing`` and the declined
    ``in der Inneren Stadt``. Until the review of 2026-11-10 both passed
    rule e unread, whatever the data said.
    """
    pattern = re.escape(name).replace("Innere", "Inneren?")
    return re.search(rf"(?<![\w-])(?:Wien-)?{pattern}(?![\w-])", text) is not None


def _district_reasons(text: str, sites: Sequence[BaustelleSite], source: str) -> list[str]:
    """Rule e: a district the text names is the district of a site or stands in the source text."""
    of_sites = {number for site in sites for number in district_numbers(site.district)}
    missing = [name for number, name in enumerate(DISTRICT_NAMES, 1)
               if _named(name, text) and number not in of_sites and not _named(name, source)]
    return _reason("Bezirk nicht in den Daten", missing)


def _month_reasons(text: str, sites: Sequence[BaustelleSite], year: int) -> list[str]:
    """Rule f: a month the text names is touched by the period of at least one site."""
    touched = months_touched(sites, year)
    missing = [name for name, number in _MONTH_NUMBER.items() if number not in touched and re.search(rf"(?<!\w){name}(?!\w)", text)]
    return _reason("Monat außerhalb aller Zeiträume", missing)


def _voice_reasons(text: str) -> list[str]:
    """Rule g: no first person, no address, no advice (:data:`_VOICE_MARKERS`)."""
    return _reason("Ich-Form, Anrede oder Rat im Text", [label for label, pattern in _VOICE_MARKERS if pattern.search(text)])


def _count_reasons(text: str, sites: Sequence[BaustelleSite], year: int, source: str | None = None) -> list[str]:
    """Rule h: a number in digits directly before ``Baustelle(n)`` is a count of the facts block.

    Rule c asks only whether a number stands somewhere: "21 Baustellen"
    passed for 19 sites because the data holds "21., Floridsdorf" (review
    of 2026-11-10). The counts come from ``fact_counts``, the computation
    that prints the facts block. A number word is not read: a correct
    text says "Zwei davon" or "je eine", and "eine" is also the article.
    The year of the page and a year in the data (*source*) are a time, not
    a count: "Es gab 2026 Baustellen in 13 Bezirken" (review of 2026-11-10).
    """
    years = {str(year), *(token for token in _digit_tokens(source_text(sites) if source is None else source)
                          if len(token) == 4 and token.isdigit() and PLAUSIBLE_YEARS[0] <= int(token) <= PLAUSIBLE_YEARS[1])}
    counted = {str(count) for count in fact_counts(sites, year=year)}
    stated = set(_SITE_COUNT_RE.findall(text)) - counted - years
    return _reason("Anzahl der Baustellen nicht aus dem Faktenblock", sorted(stated, key=lambda raw: (len(raw), raw)))


def _judgement_reasons(text: str) -> list[str]:
    """Rule i: no word of the closed list :data:`_JUDGEMENT_RE`."""
    return _reason("Wertung im Text", sorted({match.group().lower() for match in _JUDGEMENT_RE.finditer(text)}))


def _language_reasons(text: str) -> list[str]:
    """Rule j: at least :data:`MIN_GERMAN_SHARE` of the words are German function words (a share, not a language)."""
    words = [word.lower() for word in _WORD_RE.findall(text)]
    german = sum(word in _GERMAN_WORDS for word in words)
    if not words or german >= MIN_GERMAN_SHARE * len(words):
        return []
    noun = "Wort" if len(words) == 1 else "Wörtern"
    return [f"Anteil deutscher Funktionswörter unter {round(MIN_GERMAN_SHARE * 100)} %: {german} von {len(words)} {noun}"]


def check_review(text: str, sites: Sequence[BaustelleSite], *, year: int) -> list[str]:
    """The guard: why *text* may not be published, one German line per reason; empty when accepted.

    The rules, each a helper of its own:

    a. 200 to 1,500 characters and 3 to 8 sentences;
    b. no markup characters (``< > [ ] { } | ` * _ # \\ $``), no list
       marker at the start and no link;
    c. every token with a digit (number, date, year, line such as 13A or
       U4, house number) stands in the same form in the source text or in
       the facts block;
    d. every street-like word stands in the source text;
    e. every district it names is a site's district or stands in the
       source text;
    f. every month it names is touched by the period of a site in *year*;
    g. no first person, address or advice;
    h. a number directly before "Baustelle(n)" is a count of the facts block or a year;
    i. no word of a closed list of judging words;
    j. German: at least 15 % of the words are German function words.

    The limits of this guard. It proves presence, not that the facts are
    put together correctly: "Im Mai begann die Baustelle in der Burggasse"
    passes when May and Burggasse both stand in the data, whichever site
    they belong to. A count is read only directly before "Baustelle(n)"
    and only against the set of counted values, so a count that equals
    another one, or a count in words, passes. Tone is read from closed
    lists only: a judgement, an insult or an instruction in other words
    passes, and so does a text half in another language. Hence the page
    labels the review as written by a model and takes count and list from
    the ledger (``docs/architecture-teil-6.md``: "ließe sich zudem nicht
    mechanisch belegen wie ein Fund"); deleting the year's review file
    makes the next dashboard run ask again.
    """
    source = source_text(sites)
    allowed = f"{source}\n{facts_block(sites, year=year)}"
    return [
        *_length_reasons(text),
        *_markup_reasons(text),
        *_number_reasons(text, allowed),
        *_street_reasons(text, source),
        *_district_reasons(text, sites, source),
        *_month_reasons(text, sites, year),
        *_voice_reasons(text),
        *_count_reasons(text, sites, year, source),
        *_judgement_reasons(text),
        *_language_reasons(text),
    ]
