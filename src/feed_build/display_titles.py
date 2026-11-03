"""Display titles: WL sentence titles, Baustellen titles, title vocabulary.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..feed_types import FeedItem
from ..providers.wl_text import _MONTHS_DE

__all__ = [
    "_DISPLAY_TITLE_TARGET", "_shorten_wl_sentence_title", "_repair_baustellen_title",
    "_compact_baustellen_section", "_ordnungsnummer_as_nr", "_mark_house_numbers", "_compact_line_prefix",
    "_display_title_parts", "_display_title", "_section_collisions", "_split_reason_title",
    "_CATEGORY_PREFIX_WORDS", "_TITLE_REASON_WORDS", "_TICKER_ABBREVIATIONS", "_TITLE_BODY_RE",
    "_DATE_RANGE_PREFIX_RE", "_DATE_SINGLE_PREFIX_RE",
]


# ---- Display-length titles ----------------------------------------------------
#
# The German feed runs on Full-HD info displays (EasySignage), read from a
# distance, one title at a time. A title beyond this many characters wraps
# into a block the eye has to work through; WL notices and Stadt Wien
# Baustellen above it are shortened at their own seams below — never cut —
# and whatever leaves the title is still in the description. Both happen
# when the item is rendered (:func:`_display_title`), after every dedupe and
# merge: those compare titles, and a shortened title would change what they
# see — "18: Haltestelle Stadionbrücke aufgelassen" reads as a short cause
# and ``_line_runs`` joined it with that evening's "18: Gleisschaden".
# 50, operator request 2026-10-02 ("70 Zeichen … sollte echt ne Ausnahme
# sein"): of the 68 titles in the feed that evening the median had 33
# characters and three in four at most 41; above 50 stood the Baustellen
# with a "von … bis …" section, two ÖBB titles of several lines and one WL
# notice. The section moves into the description
# (:func:`_baustellen_display`); ÖBB route titles are not shortened.
_DISPLAY_TITLE_TARGET = 50

# A sentence end inside a WL title: three lowercase letters, a full stop,
# exclamation or question mark, then a capital. Abbreviations WL writes in
# titles (``Bhf.``, ``Str.``, ``ggü. 197``) have fewer lowercase letters or
# no capital after them.
_WL_SENTENCE_END_RE = re.compile(r"(?<=[a-zäöüß]{3})[.!?]\s+(?=[A-ZÄÖÜ])")
_WL_LINE_PREFIX_HEAD_RE = re.compile(r"^\S{1,40}:\s+")
# The reason in "Haltestelle Stadionbrücke im Rahmen des Straßenbahn-Neubaus
# der Linie 18 aufgelassen": from the causal preposition up to the closing
# participle (and an adverb in front of it, which stays).
_WL_REASON_CLAUSE_RE = re.compile(
    r"\s+(?:im\s+Rahmen|im\s+Zuge|zur|zum|wegen|aufgrund|infolge)\s+.+?"
    r"(?=\s+(?:(?:dauerhaft|vorübergehend|ersatzlos|kurzfristig)\s+)?"
    r"(?:aufgelassen|gesperrt|verlegt|eingestellt|umgeleitet|unterbrochen)$)",
    re.IGNORECASE,
)


def _shorten_wl_sentence_title(title: str, description: str) -> str:
    """A WL title written as whole sentences keeps only what it reports.

    Published 2026-10-02 with the date as the only description, because WL
    sent the same text as title and description and the duplicate check
    emptied the latter::

        18: Haltestelle Stadionbrücke im Rahmen des Straßenbahn-Neubaus der
        Linie 18 aufgelassen. Bitte auf nahegelegene Haltestellen ausweichen.

    The title keeps its first sentence; when that is still longer than
    :data:`_DISPLAY_TITLE_TARGET`, the reason clause goes too
    (``18: Haltestelle Stadionbrücke aufgelassen``). Both only when the
    description already says every word that leaves the title, so nothing
    is lost; the description then reads in full, because it no longer
    repeats the title.
    """
    head_match = _WL_LINE_PREFIX_HEAD_RE.match(title)
    head = head_match.group(0) if head_match else ""
    body = title[len(head):].strip()
    if " ".join(body.split()).casefold() not in " ".join(description.split()).casefold():
        return title
    shortened = _WL_SENTENCE_END_RE.split(body, maxsplit=1)[0].rstrip(".!? ")
    if len(head) + len(shortened) > _DISPLAY_TITLE_TARGET:
        shortened = _WL_REASON_CLAUSE_RE.sub("", shortened, count=1)
    if shortened == body or len(shortened.split()) < 2:
        return title
    return f"{head}{shortened}"


# ---- Baustellen title repair ------------------------------------------------
#
# Stadt Wien caps ``BEZEICHNUNG`` at 100 characters; the cache marks the cut
# with an ellipsis and leaves the text alone (``_mark_upstream_truncation`` in
# ``scripts/update_baustellen_cache.py``). Published 2026-09-24, ``docs/feed.xml``
# item 8, on a display read from a distance::
#
#     U4: Vordere Zollamtsstraße von Marxergasse und Kleine Marxerbrücke bis
#     Unbenannte Verkehrsfläche und Rad…
#
# The description of the same item spells the endpoint out ("… von der Kleine
# Marxerbrücke bis und in Richtung zur Radetzkybrücke …"), so the cut word is
# not lost: it is completed from the item's own text, never guessed. The
# fragment must be at least three letters, and when the description offers
# more than one completion ("Radetzkybrücke" and "Radweg" here) only a single
# candidate that follows a range preposition (``bis``, ``zur``, ``Richtung`` …)
# is taken; two such candidates, or none, leave the title as delivered.
# Measured over the 22 distinct titles cached since June 2026: 3 truncated,
# 2 completable (``Rad…`` → ``Radetzkybrücke``, ``Schlachthausgas…`` →
# ``Schlachthausgasse``), 1 not (``Hofpavillon…`` is already a whole word).
#
# ``Unbenannte Verkehrsfläche`` is the city's placeholder for a road segment
# without a name. Next to a named endpoint it tells the reader nothing
# ("bis Unbenannte Verkehrsfläche und Radetzkybrücke"), so it is dropped from
# such a list; as the only endpoint it stays.
#
# Neither repair touches the identity of the item: ``first_seen`` and the
# translation cache are keyed on the guid (``_state_key_for_item``), which the
# cache derives from the RAW title. The English title re-translates once,
# through the source-digest guard, because its German source changed.
_TITLE_CUT_FRAGMENT_RE: re.Pattern[str] = re.compile(
    r"(?<![^\W\d_])([^\W\d_]{3,}(?:-[^\W\d_]+)*)…$"
)
_RANGE_PREPOSITION_RE: re.Pattern[str] = re.compile(
    r"\b(?:bis|zur|zum|zu|nach|von|vom|ab|zwischen|Richtung)\s+"
    r"(?:(?:der|die|das|dem|den|des)\s+)?$"
)
_UNNAMED_AREA = r"Unbenannte\s+Verkehrsfläche"
_UNNAMED_AREA_LEADING_RE: re.Pattern[str] = re.compile(
    r"\b(von|bis|zwischen|und)\s+" + _UNNAMED_AREA + r"\s+und\s+", re.IGNORECASE
)
_UNNAMED_AREA_TRAILING_RE: re.Pattern[str] = re.compile(
    r"\s+und\s+" + _UNNAMED_AREA + r"\b(?=\s*(?:,|bis\b|und\b|$))", re.IGNORECASE
)


def _completion_from_description(fragment: str, description: str) -> str | None:
    """Return the one word of ``description`` that completes ``fragment``.

    A candidate starts with ``fragment`` at a word boundary and is longer
    than it. One distinct candidate is the answer. Several are narrowed to
    those that follow a range preposition in the description (``bis``,
    ``zur``, ``Richtung`` …) — the position a cut endpoint of a
    "von … bis …" title occupies — and only a single survivor counts.
    ``None`` means: leave the title as delivered.
    """
    pattern = re.compile(
        r"(?<![^\W\d_-])" + re.escape(fragment) + r"[^\W\d_]*(?:-[^\W\d_]+)*"
    )
    found = [
        (m.group(0), m.start())
        for m in pattern.finditer(description)
        if len(m.group(0)) > len(fragment)
    ]
    distinct = list(dict.fromkeys(word for word, _ in found))
    if len(distinct) == 1:
        return distinct[0]
    after_preposition = list(
        dict.fromkeys(
            word for word, pos in found if _RANGE_PREPOSITION_RE.search(description[:pos])
        )
    )
    return after_preposition[0] if len(after_preposition) == 1 else None


def _repair_baustellen_title(title: str, description: str) -> str:
    """Complete an upstream-cut last word and drop a placeholder endpoint.

    ``title`` is the cached Baustellen title (the ellipsis marks the city's
    100-character cut), ``description`` the item's own description. Returns
    the title unchanged when neither repair applies or the completion would
    be a guess (see :func:`_completion_from_description`).
    """
    if not title:
        return title
    repaired = title
    cut = _TITLE_CUT_FRAGMENT_RE.search(repaired)
    if cut:
        word = _completion_from_description(cut.group(1), description)
        if word:
            repaired = repaired[: cut.start(1)] + word
    repaired = _UNNAMED_AREA_LEADING_RE.sub(r"\1 ", repaired)
    return _UNNAMED_AREA_TRAILING_RE.sub("", repaired)


# A section "von A bis B" in a Baustellen title, as Stadt Wien writes it:
# ``Rechte Wienzeile von Kreuzung Ramperstorffergasse bis Kreuzung
# Pilgramgasse und Pilgrambrücke``. "Kreuzung" says what a cross-street
# name already says, and a second name joined with "und" marks the same
# end of the section once more.
_SECTION_CROSSING_RE: re.Pattern[str] = re.compile(r"\b(von|bis)\s+Kreuzung\s+")
_SECTION_SECOND_NAME_RE: re.Pattern[str] = re.compile(
    r"\b(von|bis)\s+([^,]+?)\s+und\s+[^,]+?(?=\s+bis\b|,|$)"
)


def _compact_baustellen_section(title: str) -> str:
    """Shorten the "von … bis …" section of a long Baustellen title.

    Published 2026-10-02 on the info displays, 97 and 85 characters::

        U2: Rechte Wienzeile von Kreuzung Ramperstorffergasse bis Kreuzung
        Pilgramgasse und Pilgrambrücke
        U4: Vordere Zollamtsstraße von Marxergasse und Kleine Marxerbrücke
        bis Radetzkybrücke

    Only a title longer than :data:`_DISPLAY_TITLE_TARGET` is touched, in
    two steps that each stop once it fits: "Kreuzung" in front of an end
    goes, then every name after the first at one end
    (``U2: Rechte Wienzeile von Ramperstorffergasse bis Pilgramgasse``).
    The street and both ends stay; the description describes the site.
    """
    if len(title) <= _DISPLAY_TITLE_TARGET:
        return title
    title = _SECTION_CROSSING_RE.sub(r"\1 ", title)
    if len(title) <= _DISPLAY_TITLE_TARGET:
        return title
    return _SECTION_SECOND_NAME_RE.sub(r"\1 \2", title)


# A house number as Stadt Wien writes it in a Baustellen title: ``33A``,
# ``1``, ``189``. Not ``3-4`` (a span of its own) and not a line code: in
# these titles a number after "von" or "bis" is always an address.
_HOUSE_NUMBER = r"\d{1,4}[A-Za-z]?"
_HOUSE_NUMBER_SPAN_RE: re.Pattern[str] = re.compile(
    rf"(?:\bvon\s+)?(?<![\w-])({_HOUSE_NUMBER})\s+bis\s+({_HOUSE_NUMBER})(?![\w-])"
)
# "bis 5" after a number is the end of an address span (``Matzleinsdorfer
# Platz 3-4 bis 5``), not a house number on its own.
_HOUSE_NUMBER_END_RE: re.Pattern[str] = re.compile(
    rf"(?<!\d\s)\b(von|bis)\s+({_HOUSE_NUMBER})(?![\w-])"
)


# "ONr." (Ordnungsnummer) is the city's word for a house number. In a title
# it stood in front of both ends: ``Altmannsdorfer Straße ONr.76 bis
# ONr.76A`` (published 2026-08-28 to 2026-09-02 as "Wien Hetzendorf:
# Altmannsdorfer Straße ONr.76 bis ONr.76A", in English "ONo76"). Without it
# the span reads as an address like every other one: ``… 76–76A``.
_ORDNUNGSNUMMER_RE: re.Pattern[str] = re.compile(r"\b(?:O\.\s?Nr|ONr)\.\s*(?=\d)")


# The same abbreviation in running text, and its short form "ON" ("in die
# Wolfersberggasse ON 1", "von der ON 125 bis zur ON 141"): written "Nr.",
# which reads right in every position ("von der Nr. 125", "in Höhe Nr. 42",
# "Burggasse Nr. 67"). In the German feed since May 2026 from Stadt Wien
# ("von Burggasse ONr.67 nach Burggasse ONr. 69") and from WL ("Busse halten
# Währinger Straße ONr. 200-202", "Nach: Heiligenstädter Straße ONr. 2").
# "ON" only in capitals and before a number.
_ORDNUNGSNUMMER_TEXT_RE: re.Pattern[str] = re.compile(
    r"\b(?:(?:O\.\s?Nr|ONr)\.|ON)\s*(?=\d)"
)


def _ordnungsnummer_as_nr(text: str) -> str:
    """``ONr.67`` / ``O.Nr. 67`` / ``ON 67`` → ``Nr. 67`` (see the pattern)."""
    return _ORDNUNGSNUMMER_TEXT_RE.sub("Nr. ", text)


def _mark_house_numbers(title: str) -> str:
    """Write the house numbers of a Baustellen title as an address.

    Published 2026-10-02 on the info displays: ``Rennweg von 33A bis 37``,
    read from a distance as the bus 33A and the tram 37. A span of two
    house numbers becomes the address form ``Rennweg 33A–37`` (also
    ``Kirchengasse 1 bis 30`` → ``Kirchengasse 1–30``); a single house
    number at one end of a section gets "Nr." (``Siebenbrunnengasse von
    Siebenbrunnenplatz bis Nr. 44``).
    """
    title = _ORDNUNGSNUMMER_RE.sub("", title)
    title = _HOUSE_NUMBER_SPAN_RE.sub(r"\1–\2", title)
    return _HOUSE_NUMBER_END_RE.sub(r"\1 Nr. \2", title)


# The line list in front of an ÖBB title, as ``_normalize_line_token``
# renders it: ``R 40/REX 41/REX 4/S 40: …``.
_SPACED_LINE_PREFIX_RE: re.Pattern[str] = re.compile(
    r"(?:^|(?<=:\s))(?:[A-Z]{1,4}\s\d{1,3}/)*[A-Z]{1,4}\s\d{1,3}(?=:\s)"
)


def _compact_line_prefix(title: str) -> str:
    """``R 40/REX 41/REX 4/S 40: …`` → ``R40/REX41/REX4/S40: …``.

    The way WL lines (``U6``, ``N66``) and the Stammstrecke monitor
    (``S1/S2/S3``) are written; operator request 2026-10-02. Only the list
    in front of the colon changes (also behind an all-clear label), the
    description keeps ÖBB's text.
    """
    return _SPACED_LINE_PREFIX_RE.sub(lambda match: match.group(0).replace(" ", ""), title, count=1)


# The section of a Baustellen title: "von A bis B" or "Kreuzung A und B"
# behind the street.
_SECTION_TAIL_RE: re.Pattern[str] = re.compile(r"\s+((?:von|Kreuzung)\s+\S.*)$")


def _baustellen_display(title: str, *, drop_section: bool) -> tuple[str, str]:
    """Title and description lead of a Stadt Wien Baustelle as shown.

    The section is compacted (:func:`_compact_baustellen_section`) and its
    house numbers marked (:func:`_mark_house_numbers`). Still longer than
    :data:`_DISPLAY_TITLE_TARGET`, the title keeps the street and the
    section opens the description, as a label in front of its first
    sentence so the 180-character summary keeps both::

        Landstraßer Hauptstraße
        Von Emmerich-Teuber-Platz und Juchgasse und Apostelgasse bis
        Schlachthausgasse: Es wird lediglich die bestehende Bushaltestelle …

    The lead is there even when the description names the places too: the
    feed shows only its first sentences ("U2/U5: Bacherplatz" names
    Arbeitergasse and Schwarzhorngasse in its third). ``drop_section=False`` keeps the
    section in the title: another visible item would read the same
    (:func:`_section_collisions`).
    """
    marked = _mark_house_numbers(title)
    compact = _mark_house_numbers(_compact_baustellen_section(title))
    if not drop_section or len(compact) <= _DISPLAY_TITLE_TARGET:
        return compact, ""
    match = _SECTION_TAIL_RE.search(marked)
    head = marked[: match.start()] if match else ""
    if match is None or not head.split(": ", 1)[-1].strip():
        return compact, ""
    section = _SECTION_CROSSING_RE.sub(r"\1 ", match.group(1))
    return head, f"{section[0].upper()}{section[1:]}"


# ``ab 07. April 2026``: the start WL writes into a notice title. The item's
# time line says it (``[Ab 07.04.2026]``); the numeric form ``ab 14.09.26``
# is dropped at fetch time already (``_AB_DATE_RE`` in ``wl_text``).
_WL_SPELLED_START_RE: re.Pattern[str] = re.compile(
    r"\s+ab\s+\d{1,2}\.\s*(?:" + "|".join(_MONTHS_DE) + r")(?:\s+\d{4})?(?=\s*$)",
    re.IGNORECASE,
)


def _display_title_parts(item: FeedItem, *, drop_section: bool = True) -> tuple[str, str]:
    """The title *item* is shown with, and a lead for its description.

    Applied when the item is rendered, after every dedupe and merge (see
    :data:`_DISPLAY_TITLE_TARGET`). WL titles lose a spelled-out start date
    when the item has a start, and a title written as sentences goes
    through :func:`_shorten_wl_sentence_title`; Stadt Wien Baustellen go
    through :func:`_baustellen_display`, ÖBB titles through
    :func:`_compact_line_prefix`; every other title as is. The lead is ""
    unless text moved from the title into the description.
    """
    title = str(item.get("title") or "Mitteilung")
    description = str(item.get("description") or "")
    source = str(item.get("source") or "").strip().casefold()
    if source == "wiener linien":
        if item.get("starts_at"):
            title = _WL_SPELLED_START_RE.sub("", title)
        return _shorten_wl_sentence_title(title, description), ""
    if source.startswith("stadt wien"):
        return _baustellen_display(title, drop_section=drop_section)
    if source in {"öbb", "oebb"}:
        return _compact_line_prefix(title), ""
    return title, ""


def _display_title(item: FeedItem, *, drop_section: bool = True) -> str:
    """The title *item* is shown with (see :func:`_display_title_parts`)."""
    return _display_title_parts(item, drop_section=drop_section)[0]


def _section_collisions(items: Sequence[FeedItem]) -> set[int]:
    """Indices in *items* whose Baustelle keeps its section in the title.

    Without the section two sites on one street would read the same on the
    display; both keep it, like :func:`_short_title_collisions` does for WL
    tickers.
    """
    short = [_display_title(item).casefold() for item in items]
    full = [_display_title(item, drop_section=False).casefold() for item in items]
    counts: dict[str, int] = {}
    for key in short:
        counts[key] = counts.get(key, 0) + 1
    return {
        index
        for index, (key, whole) in enumerate(zip(short, full, strict=True))
        if key != whole and counts[key] > 1
    }


def _split_reason_title(title: str) -> tuple[str, str] | None:
    """Split a ``<lines>: <Reason> – <fragment>`` title into its two halves.

    The shape is the one :func:`_separate_reason_word` produces: after the
    line prefix, exactly one word from :data:`_TITLE_REASON_WORDS`, then
    the en dash, then the ticker fragment. Anything else — no dash, a
    multi-word head, a head that is not a reason word (``Wien – Mödling``),
    an empty tail — returns ``None`` and is translated whole.
    """
    if not title or " – " not in title:
        return None
    match = _TITLE_BODY_RE.match(title)
    body = match.group(1) if match else title
    prefix = title[: match.start(1)] if match else ""
    head, _sep, tail = body.partition(" – ")
    head = head.strip()
    tail = tail.strip()
    if not head or not tail or " " in head:
        return None
    if head.casefold() not in _TITLE_REASON_WORDS:
        return None
    return f"{prefix}{head}", tail


# Category-prefix duplicate stripping is extracted from
# :func:`_format_item_content` so the complexity gate (C901, baseline 31)
# still tolerates the lang/state additions introduced for the bilingual
# feed. The block was a tight-knit sub-block of the formatter (no
# external dependencies beyond ``re`` and the inputs) — pulling it out
# preserves behaviour byte-for-byte while freeing ~8 branches from the
# caller.
_CATEGORY_PREFIX_WORDS: frozenset[str] = frozenset({
    "bauarbeiten",
    "gleisbauarbeiten",
    "straßenbauarbeiten",
    "strassenbauarbeiten",
    "rohrleitungsarbeiten",
    "kranarbeiten",
    "brückenbauarbeiten",
    "brueckenbauarbeiten",
    "brückenarbeiten",
    "brueckenarbeiten",
    "schienenarbeiten",
    "veranstaltung",
    "demonstration",
    "filmaufnahmen",
    "falschparker",
})

# Incident causes in front of a WL ticker fragment. They get the same en dash
# as the planned-work words above, but only in the TITLE: the summary dedupe
# and the topic budget keep working on ``_CATEGORY_PREFIX_WORDS`` alone.
#
# Published 2026-09-25 12:05, ``docs/feed.xml`` item 1::
#
#     6: Fremdunfall Züge halten bei der Linie O
#
# Measured over the WL cache since June 2026 (125 revisions, 455 distinct
# titles): 30 titles open with one of these words directly followed by the
# consequence ("Betrieb", "Umleitung", "Züge halten" …) — Fremdunfall 10,
# Rettungseinsatz 7, Oberleitungsgebr(echen) 4, Polizeieinsatz 3,
# Verkehrsunfall 2, Gleisschaden 2, Feuerwehreinsatz 1, Polizeiübung 1.
_INCIDENT_REASON_WORDS: frozenset[str] = frozenset({
    "fremdunfall",
    "verkehrsunfall",
    "rettungseinsatz",
    "polizeieinsatz",
    "feuerwehreinsatz",
    "polizeiübung",
    "gleisschaden",
    "oberleitungsgebrechen",
    # Not a cause but the kind of hindrance WL puts in front of one
    # ("Fahrtbehinderung Verkehrsunfall"). Here so the dash of a long title
    # (``18: Fahrtbehinderung – Verkehrsunfall``, kept where the short one
    # would collide) is known to the English splitter too. The short title
    # takes the cause — see ``_HINDRANCE_RE``.
    "fahrtbehinderung",
})
# Every word the separator puts a dash behind. ``_split_reason_title`` must
# know the same set: a dashed title it does not recognise goes to the model
# whole, with the dash as a placeholder between two words — the shape that
# produced "Demonstration –Xservice" on 2026-09-19.
_TITLE_REASON_WORDS: frozenset[str] = _CATEGORY_PREFIX_WORDS | _INCIDENT_REASON_WORDS

# WL's display-board tickers cut long words to fit. "Oberleitungsgebr" (4 of
# the titles above) is "Oberleitungsgebrechen", a fault in the overhead line;
# on a display the stub reads as a typo. Only abbreviations the cache history
# shows, spelled out in full.
_TICKER_ABBREVIATIONS: dict[str, str] = {
    "oberleitungsgebr": "Oberleitungsgebrechen",
}

_TITLE_BODY_RE = re.compile(r"^[A-Za-z0-9/]+:\s*(\S.*)$")
_DATE_RANGE_PREFIX_RE = re.compile(
    r"^\d{2}\.\d{2}\.\d{4}\s*-\s*\d{2}\.\d{2}\.\d{4}\s*•\s*"
)
_DATE_SINGLE_PREFIX_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}\s*•\s*")
