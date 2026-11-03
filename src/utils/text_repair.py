"""Repair of run-together words in upstream prose.

Part of :mod:`src.utils.text`, which re-exports the public names.
"""

import re


# ---------------------------------------------------------------------------
# Repair of run-together words in upstream prose
# ---------------------------------------------------------------------------
#
# The Stadt-Wien OGD Baustellen feed ships descriptions whose line breaks were
# dropped WITHOUT a replacement space somewhere upstream, so neighbouring words
# collide. Live examples from ``cache/baustellen_d438c3/events.json`` that
# reached ``docs/feed.xml`` verbatim:
#
#   * ``"… kann aufrecht gehalten werden.Nähere Informationen …"``
#   * ``"… - Hadikgasse.DerFußgänger- und Radverkehr …"``
#   * ``"Schönbrunner Schloßstraße - SchönbrunnerSchloßbrücke"``
#   * ``"Die Kennedybrücke wird in Fahrtrichtung 14. Bezirk(Hadikgasse) gesperrt."``
#
# Three independent repairs, all deliberately conservative — a false split is
# a worse display bug than the glue it fixes:
#
#   1. Sentence punctuation immediately followed by an uppercase letter. The
#      character BEFORE the punctuation must be a letter (at least two
#      lower-case ones for ``.``) or a closing bracket, which rules out
#      ordinals and dates (``14. Bezirk``, ``14.09.2026``), decimal numbers
#      and times (``1,5``, ``14:30``), and single-letter abbreviations
#      (``Gerasdorf b. Wien``, ``Karlsplatz U.``).
#   2. An opening bracket glued to the preceding word.
#   3. A lower→upper transition INSIDE a word, with two guards: the
#      upper-case letter must be followed by another lower-case letter (so
#      the company forms ``GmbH`` / ``mbH`` and any trailing acronym stay
#      intact), and the Binnen-I gender form (``MitarbeiterInnen``,
#      ``SchülerIn``) is excluded.
_GLUED_SENTENCE_RE = re.compile(
    r"(?<=[a-zäöüß]{2}[.!?])(?=[A-ZÄÖÜ])"
    r"|(?<=[A-Za-zÄÖÜäöüß)][:;,])(?=[A-ZÄÖÜ])"
    # ``Freitag,11. September 2026`` — a digit may follow the separator too,
    # but only when a LETTER precedes it, so a decimal comma ("1,5 Mio")
    # and a time ("14:30") are never touched.
    r"|(?<=[A-Za-zÄÖÜäöüß][:;,])(?=\d)"
)
_GLUED_BRACKET_RE = re.compile(r"(?<=[A-Za-zÄÖÜäöüß0-9])(?=\()")
# The lookbehind demands TWO lowercase letters, which silently excludes a
# whole shape: a two-letter capitalised function word glued to the next one.
# ``DerFußgängerverkehr`` has ``er`` in front of the break and is repaired;
# ``ImBaustellenbereich`` has ``Im``, whose ``I`` is uppercase, so only ONE
# lowercase letter precedes the position and the rule cannot fire. The Stadt-
# Wien roadworks feed publishes exactly that word.
#
# The second alternative closes it with a CLOSED LIST rather than a relaxed
# lookbehind. ``(?<=[A-ZÄÖÜ][a-zäöüß])`` would be shorter and would also split
# ``McDonalds`` into ``Mc Donalds``; naming the function words that actually
# open a German clause cannot. Checked over 848 texts — the whole cache plus
# the complete published history of both feeds — the list fires on exactly one
# place, ``ImBaustellenbereich``, and on nothing else.
_GLUED_WORD_RE = re.compile(
    r"(?:(?<=[a-zäöüß]{2})|(?<=\b(?:Im|Am|An|Um|Ab|In|Zu|Es)))"
    r"(?!I(?:nnen|n)\b)(?=[A-ZÄÖÜ][a-zäöüß])"
)


# All three rules above look for an upper-case letter, so they cannot see
# two words glued where the second one starts lower-case. The Stadt-Wien
# roadworks texts carry that shape too, in the same notices:
#
#   * ``"Derlinke Fahrstreifen"``, ``"Derrechte Fahrstreifen"``
#   * ``"Außerhalbder Arbeitszeit"``, ``"Die Zufahrt zuden …"``
#   * ``"Die Haltestelleder betroffenen …"``, ``"Umleitungab Volksoper"``
#
# Case gives no signal here, so the repair names both halves in closed
# lists and only splits where neither half can continue the other into a
# real word: an article before a lane adjective, a preposition before an
# article, and a function word after a street/stop noun or an ``-ung``
# noun. Checked over 16 362 distinct texts (both feeds' published history
# and every provider cache), the rule fires on 23 places, all of them the
# shapes listed above, and on nothing else. ``zu`` only takes
# ``den``/``der``, so ``zudem`` stays whole; ``indem``, ``beiden`` and
# ``derzeit`` never match because their halves are not on the lists.
_GLUED_LOWER_RE = re.compile(
    r"\b(?:[Dd]er|[Dd]ie|[Dd]as)(?=(?:link|recht|äußer|inner|mittler)e[nr]?\b)"
    r"|\b(?:[Aa]ußerhalb|[Ii]nnerhalb)(?=(?:der|den|dem|des|die|das)\b)"
    r"|\bzu(?=de[nr]\b)"
    r"|\b\w*[a-zäöüß](?:straße|gasse|stelle|platz|ung)"
    r"(?=(?:der|die|das|den|dem|des|bei|ab|bis|und|zur|zum|von|vom|mit)\b)"
)


# ``St.Pölten Hbf``, ``St.Andrä-Wördern``, ``St.Marx``: ÖBB writes the
# abbreviation of "Sankt" without the space the German form needs (and
# without which the title of the same notice, "St. Pölten Hauptbahnhof",
# disagrees with its description). The capital after the full stop is
# the signal; ``St`` is the only abbreviation ÖBB glues to a name.
_GLUED_SAINT_RE = re.compile(r"\bSt\.(?=[A-ZÄÖÜ])")


def repair_saint_abbreviation(text: str) -> str:
    """``St.Pölten`` -> ``St. Pölten`` (see :data:`_GLUED_SAINT_RE`)."""
    return _GLUED_SAINT_RE.sub("St. ", text)


def repair_glued_words(text: str) -> str:
    """Insert the spaces an upstream feed dropped between words.

    Purely additive: the function only ever inserts a single space at a
    position where two words collided, never removes or reorders
    characters. Input that carries no glue round-trips unchanged, so it
    is safe to run over every provider's text.
    """
    if not text:
        return text
    repaired = _GLUED_SENTENCE_RE.sub(" ", text)
    repaired = _GLUED_BRACKET_RE.sub(" ", repaired)
    repaired = repair_saint_abbreviation(repaired)
    repaired = _GLUED_LOWER_RE.sub(r"\g<0> ", repaired)
    return _GLUED_WORD_RE.sub(" ", repaired)
