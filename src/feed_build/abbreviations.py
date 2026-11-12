"""Whether a period belongs to an abbreviation or ends the sentence.

Wiener Linien and ÖBB abbreviate inside running text: "Bhf. Meidling",
"Hlst. Li. 95A", "prov. Haltestelle", "bzw. Lainzer Tor", "Josefstädter
Str.", "Fahrleitungsgebr.", "Johann-N.-B.-Pl.", "Zentralfriedhof, 4. Tor",
"bis 03. Juni". Every step that looks for the end of a sentence, or takes
the full stop off a sentence, has to tell those periods from a real one
("… und S80. Die …", "… durch einen PKW. Grund: …", "… Fahrzeit ein.").
Until 2026-11-12 each step had its own idea of it: the cut behind a whole
sentence took "Bhf." for an end, the title kept "2: Fahrleitungsgebr"
without its period, and the cut at 180 characters dropped "ein." and "D."
as if they were abbreviations (operator request 2026-11-11, Wächter
quality check classes 15 and 16).

The rule is the shape of the word, not a list of every abbreviation:

* A word of letters without a vowel is an abbreviation ("Bhf", "Hbf",
  "Hlst", "Str", "St", "Nr", "Wr", "bzw", "zw", "b"): a German word has one
  ("Uhr", "aus", "ein").
* No German word ends in a consonant followed by "r", except "hr" ("Uhr",
  "Verkehr") and "rr" ("Herr"): "Fahrleitungsgebr", "Hauptstr", "Hausnr" are
  cut short.
* Words in capitals are names, lines or acronyms and end a sentence like any
  word ("PKW", "KG", "WLB", "GmbH", the stop markers "S" and "U", the line
  "D"); so do lines with digits ("S80", "N20"). A single capital glued to
  the word in front ("J-Baumann-G.", "V.I.C.") is an initial of a name.
* A few abbreviations have a vowel or mix capitals; :data:`_WITH_VOWEL`
  holds those that WL, ÖBB and Stadt Wien wrote in the cache between April
  and November 2026 ("ca", "ggü", "prov", "geg", "Li", "Ri", "ONr", …) and
  those the cut behind a sentence knew before ("usw", "sog", "evtl",
  "inkl"). "Pkw", "Lkw" and "Kfz" have no vowel but stand for a noun and
  end a sentence like one.
* A number of one or two digits is an ordinal, not a sentence end, when a
  month, "Tor", "Bezirk" or the street "Haidequerstraße" follows ("bis 03.
  Juni", "Zentralfriedhof, 4. Tor", "1. Bezirk", "1. Haidequerstraße"), an
  abbreviation with its own period (WL's "Li. 6. Ri. Geiereckstr." of
  05.04.2026), or when it stands inside a bracket ("(Simmeringer Platz,
  Busbahnhof, 3. und 4. Haltestelle)"); "die Linie 49. Voraussichtliche
  Dauer" and "der Linien 43 und 9. Haltestelle Mayssengasse" end one.

Measured over every title and description of the WL, ÖBB and Baustellen
caches since 2026-04-01 and the published DE feed: every word the rule calls
an abbreviation is one. What it cannot see are words cut short that keep a
vowel and end otherwise ("Quartier Belv.", "Matzleinsd. Platz",
"Schwarzenbergpl.", 1 to 3 times each), an abbreviation that ends a
sentence ("Oberleitung def. Betrieb ab Kagran"), whose period is both, and
an ordinal before a misspelt month ("31. Auust").
"""

from __future__ import annotations

import re

from ..providers.wl_text import _MONTHS_DE

__all__ = ["is_abbreviation", "is_abbreviation_period", "without_full_stop"]

_VOWEL_RE = re.compile(r"[aeiouyäöüáàâéèêíìîóòôúùû]", re.IGNORECASE)
# A consonant other than "h" or "r", then "r", at the end of the word.
_CUT_SHORT_RE = re.compile(r"[bcdfgjklmnpqstvwxzß]r$", re.IGNORECASE)
# Abbreviations with a vowel, as written in the caches since April 2026:
# "ca. 07:30", "Simmeringer Hauptstraße ggü. 197", "prov. Haltestelle",
# "Rotensterngasse geg. ONr. 35", "Li. 31 Ri. Schottenring", "Haltest.
# verlegt", "gem. m. Li. 85A", "mind. 3,50 m", "etc."; "usw.", "sog.",
# "evtl." and "inkl." from the list the cut behind a sentence used before.
_WITH_VOWEL: frozenset[str] = frozenset({
    "ca", "ggü", "prov", "geg", "li", "ri", "haltest", "gem", "mind", "onr",
    "usw", "etc", "sog", "evtl", "inkl",
})
# Short words without a vowel that stand for a noun: "durch einen Pkw."
_VOWELLESS_NOUNS: frozenset[str] = frozenset({"pkw", "lkw", "kfz"})
# What follows an ordinal number: a month, a gate of the Zentralfriedhof, a
# district, and the streets of Simmering named with one ("1. Haidequerstraße").
_ORDINAL_NOUNS: frozenset[str] = frozenset({*_MONTHS_DE, "tor", "bezirk", "Haidequerstraße".casefold()})
_WORD_BEFORE_RE = re.compile(r"([^\W_]+)$")
_WORD_AFTER_RE = re.compile(r"\s+([^\W\d_]+)(\.?)")


def is_abbreviation(word: str) -> bool:
    """Whether *word*, written with a period, is an abbreviation.

    Only words of letters can be one; see the module docstring for the rule.
    """
    if not word.isalpha():
        return False
    folded = word.casefold()
    if folded in _WITH_VOWEL:
        return True
    if folded in _VOWELLESS_NOUNS:
        return False
    if len(word) == 1:
        return word.islower()  # "b. Wien", "u. a."; "U.", "S.", "D." end a sentence
    if not (word.islower() or (word[0].isupper() and word[1:].islower())):
        return False
    if _VOWEL_RE.search(word) is None:
        return True
    return _CUT_SHORT_RE.search(word) is not None


def is_abbreviation_period(text: str, index: int) -> bool:
    """Whether the period at ``text[index]`` belongs to an abbreviation or an ordinal.

    False for a period that may end the sentence. The word in front decides
    (:func:`is_abbreviation`); a single capital glued to a word ("G" of
    "J-Baumann-G.") is an initial; a number of one or two digits is an
    ordinal when one of :data:`_ORDINAL_NOUNS` or an abbreviation with its
    period follows it, or inside a bracket that is still open.
    """
    match = _WORD_BEFORE_RE.search(text, 0, index)
    if match is None:
        return False
    word = match.group(1)
    before = text[match.start() - 1] if match.start() > 0 else " "
    if word.isdigit():
        if len(word) > 2 or not (before.isspace() or before in "(,"):
            return False
        after = _WORD_AFTER_RE.match(text, index + 1)
        if after is None:
            return False
        return (
            after.group(1).casefold() in _ORDINAL_NOUNS
            or (after.group(2) == "." and is_abbreviation(after.group(1)))
            or text.count("(", 0, index) > text.count(")", 0, index)
        )
    if len(word) == 1 and word.isupper() and before in "-.":
        return True
    return is_abbreviation(word)


def without_full_stop(text: str) -> str:
    """*text* without the full stop and spaces at its end; an abbreviation keeps its period.

    For joining sentences with "; ": "Betrieb ab Karlsplatz." gives
    "Betrieb ab Karlsplatz", "Betrieb nur bis Bhf. Meidling Dörfelstr." stays
    as it is.
    """
    stripped = text.rstrip(" ")
    if stripped.endswith(".") and is_abbreviation_period(stripped, len(stripped) - 1):
        return stripped
    return stripped.rstrip(" .")
