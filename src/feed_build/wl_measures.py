"""WL descriptions: line prefixes, repeated segments, measures first, every line in short.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..feed_types import FeedItem

from .base import _SENTENCE_SPLIT_RE

__all__ = [
    "_WL_DESC_LINE_TOKEN", "_WL_DESC_LINIE_PREFIX_RE", "_strip_wl_description_line_prefix",
    "_WL_DESC_LINE_SEGMENT_SPLIT_RE", "_collapse_repeated_wl_line_segments", "_tidy_wl_dangling_location",
    "_WL_MEASURE_DAY", "_wl_measure_labels_with_colon", "_prefer_measures", "_SHORT_LINE_MARKER_RE",
    "_short_measure", "_every_line_in_short", "_lines_in_short", "_text_names_line",
]


# Line prefixes WL puts at the start of descriptions are redundant
# noise — the title already carries the line attribution via the
# canonical ``40/41:`` prefix. Real cache examples:
#
#   * ``40+41: Betrieb ab Gersthof``
#   * ``Linie 40: Nach einer Fahrtbehinderung …``
#   * ``Linien 9/40/41/42: Umleitung``
#   * ``Linie D: Unregelmäßige Intervalle …`` (WL tram ``D`` — single
#     bare letter, no digit)
#
# Stripping the prefix at cache-read time cleans up the user-visible
# summary and avoids it being copied verbatim into the description
# during cross-line dedup-merge. The line-token shape mirrors
# ``_STRICT_LINE_TOKEN_RE`` in ``src/providers/wl_lines.py``: either a
# digit-bearing code (``[A-Z]{0,4}\d{1,3}[A-Z]?``, plus the Rufbus ``R``
# of ``44BR``), a single bare uppercase letter (WL tram ``D``) or the
# Badner Bahn ``LB``/``WLB``. Pure multi-letter German words
# (``Achtung``, ``Information``, ``Hinweis``) cannot match either
# shape so generic prefixes and time markers like ``17:30 Uhr…``
# stay untouched.
_WL_DESC_LINE_TOKEN = r"(?:[A-Z]{0,4}\d{1,3}(?:[A-Z]R?)?|[A-Z]|W?LB)"  # nosec B105  # noqa: S105 — regex fragment, not a secret
# WL's own name for its line LB stands in such a prefix as well: "Linie
# Badner Bahn: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen
# Intervallen." (LB, 2026-11-05 19:01) kept its prefix behind the title
# "LB: Fremder Verkehrsunfall", so the sentence missed its English template
# and read "Line Badner Bahn: After a service obstruction it comes to
# irregular intervals." Only the "Linie …:" form knows the name: "Badner
# Bahn:" without "Linie" marks the Badner Bahn's part of a multi-line text
# (``_SHORT_LINE_MARKER_RE``) and stays.
_WL_DESC_LINE_NAME = rf"(?:{_WL_DESC_LINE_TOKEN}|Badner\s+Bahn)"
_WL_DESC_LINIE_PREFIX_RE = re.compile(
    rf"^\s*Linien?\s+{_WL_DESC_LINE_NAME}"
    rf"(?:\s*[/+,]\s*{_WL_DESC_LINE_NAME}){{0,20}}\s*:\s+",
    re.IGNORECASE,
)
_WL_DESC_COMPACT_PREFIX_RE = re.compile(
    rf"^\s*(?:{_WL_DESC_LINE_TOKEN})"
    rf"(?:\s*[/+,]\s*(?:{_WL_DESC_LINE_TOKEN})){{0,20}}\s*:\s+",
    re.IGNORECASE,
)


def _strip_wl_description_line_prefix(desc: str) -> str:
    """Remove a leading WL ``Linie N:`` / ``40+41:`` prefix from a description.

    The title already carries the line attribution, so the prefix is
    pure noise that mirrors back into the rendered summary.
    """
    if not desc:
        return desc
    cleaned = _WL_DESC_LINIE_PREFIX_RE.sub("", desc, count=1)
    if cleaned == desc:
        cleaned = _WL_DESC_COMPACT_PREFIX_RE.sub("", desc, count=1)
    return cleaned


# WL attributes a multi-line disruption by listing every affected line in
# ONE description, each with its own ``Linie X:`` prefix. When the lines
# are hit the same way, every segment carries the IDENTICAL sentence::
#
#     Linie 5B: Unregelmäßige Intervalle in beiden Richtungen.
#     Linie 49A: Unregelmäßige Intervalle in beiden Richtungen.
#     Linie 50A: Unregelmäßige Intervalle in beiden Richtungen.
#     Grund: Verkehrsstörung.
#
# :func:`_strip_wl_description_line_prefix` removes only the FIRST prefix
# — the title already attributes the lines — so what reached the feed was
# the same sentence twice, one arbitrary line out of three named as if it
# were special, and ``Grund: Verkehrsstörung.`` pushed past the
# 180-character limit::
#
#     Unregelmäßige Intervalle in beiden Richtungen. Linie 49A:
#     Unregelmäßige Intervalle in beiden Richtungen. …
#
# 13 of 250 published German items looked like this. Dropping the repeats
# leaves ``Unregelmäßige Intervalle in beiden Richtungen. Grund:
# Verkehrsstörung.`` — shorter, and it finally says why.
#
# The segment boundary reuses :data:`_WL_DESC_LINE_TOKEN`, the same token
# shape the two prefix patterns above use, rather than a second list that
# could drift away from them.
_WL_DESC_LINE_SEGMENT_SPLIT_RE: re.Pattern[str] = re.compile(
    rf"(?<=[.!?])\s+(?=Linien?\s+(?:{_WL_DESC_LINE_TOKEN})"
    rf"(?:\s*[/+,]\s*(?:{_WL_DESC_LINE_TOKEN})){{0,20}}\s*:\s)",
    re.IGNORECASE,
)

# First sentence end inside a segment body. Deliberately not a general
# sentence splitter: it is only ever applied to the text of one segment,
# and the split above already happens at a ``Linie X:`` boundary, so a
# date like ``30.10.2026`` in a later sentence can never become a
# segment boundary.
_WL_DESC_SENTENCE_END_RE: re.Pattern[str] = re.compile(r"[.!?](?:\s|$)")


def _collapse_repeated_wl_line_segments(desc: str) -> str:
    """Drop ``Linie X:`` segments that repeat an earlier segment's sentence.

    The title names every affected line, so a segment whose sentence is
    word-identical to one already in the description carries no
    information — it only crowds out the trailing ``Grund: …`` field,
    which does.

    Only the repeated *leading sentence* of a segment is dropped;
    whatever follows it is kept in place. Measured over 267 revisions of
    the WL cache, that remainder is always the global ``Grund: …`` field
    and never line-specific text, so nothing loses its attribution.

    Segments whose sentences differ are left completely untouched,
    prefix included — there the ``Linie X:`` attribution is the whole
    point.

    Args:
        desc: A Wiener-Linien description, before the leading prefix is
            stripped by :func:`_strip_wl_description_line_prefix`.

    Returns:
        The description with repeated line segments removed, or *desc*
        unchanged when it holds fewer than two line segments.
    """
    segments = _WL_DESC_LINE_SEGMENT_SPLIT_RE.split(desc)
    if len(segments) < 2:
        return desc
    kept: list[str] = []
    seen: set[str] = set()
    for segment in segments:
        body = _WL_DESC_LINIE_PREFIX_RE.sub("", segment, count=1)
        end = _WL_DESC_SENTENCE_END_RE.search(body)
        lead = body[: end.end()] if end else body
        normalised = " ".join(lead.split())
        if normalised in seen:
            remainder = body[end.end() :].strip() if end else ""
            if remainder:
                kept.append(remainder)
            continue
        seen.add(normalised)
        kept.append(segment.strip())
    return " ".join(part for part in kept if part)


# WL sometimes ships the location slot of a ``Grund: …`` field empty::
#
#     Grund: Verkehrsüberlastung im Bereich .
#
# and sometimes ships it filled but with a stray space before the full
# stop::
#
#     Grund: Polizeieinsatz im Haltestellenbereich Atzgersdorfer Straße .
#
# 9 of 417 cached descriptions carry one of the two. Both used to sit
# past the 180-character limit and were cut away unseen; collapsing the
# repeated line segments above brings the ``Grund: …`` field back into
# view, so they would now reach the display. The dangling ``im Bereich``
# is only dropped when NOTHING follows it — a named location keeps its
# preposition.
_WL_DESC_EMPTY_LOCATION_RE: re.Pattern[str] = re.compile(
    r"\s+im\s+(?:Haltestellen)?[Bb]ereich\s*(?=[.!?]|$)"
)
_WL_DESC_SPACE_BEFORE_STOP_RE: re.Pattern[str] = re.compile(r"\s+(?=[.!?])")


def _tidy_wl_dangling_location(desc: str) -> str:
    """Repair a ``Grund: …`` field whose location slot WL left empty.

    Drops an ``im Bereich`` / ``im Haltestellenbereich`` that is followed
    by nothing, then removes the space WL leaves in front of the full
    stop. A location that IS named keeps its preposition and only loses
    the stray space.

    Args:
        desc: A Wiener-Linien description.

    Returns:
        The tidied description.
    """
    if not desc:
        return desc
    return _WL_DESC_SPACE_BEFORE_STOP_RE.sub(
        "", _WL_DESC_EMPTY_LOCATION_RE.sub("", desc)
    )

# A per-line measure sentence in a WL notice — ``Linie D: …``,
# ``Linien 56A und 56B: …`` — at the text start or right after a sentence
# end or a label colon (``Maßnahmen: Linie D: …``). Built on the shared
# ``_WL_DESC_LINE_TOKEN`` so it recognises exactly the line codes the
# other WL description rules do. The label may carry what WL writes
# between the lines and the colon — a note in brackets (``Linie 71
# (ganztägig):``), the direction (``Linie 11A in Richtung Stadion U:``,
# ``Linie N71 in beiden Richtungen:``) — and the days in front of it
# (``Montag-Freitag, Linie 80A …:``, ``Samstag, Sonntag und Feiertag,
# Linie 80A:``). Without them 19 notices in the cache since July showed only
# "… kommt es zu folgenden Verkehrsmaßnahmen." (see ``_prefer_measures``).
_WL_MEASURE_DAY = r"(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag|Feiertag)"
_WL_MEASURE_LABEL = (
    rf"(?:{_WL_MEASURE_DAY}(?:\s*(?:-|–|,|und|bis)\s*{_WL_MEASURE_DAY}){{0,6}},\s+)?"
    rf"Linien?\s+{_WL_DESC_LINE_TOKEN}"
    rf"(?:\s*(?:,|und|/|\+)\s*{_WL_DESC_LINE_TOKEN}){{0,20}}"
    r"(?:\s+\([^()]{1,40}\))?"
    r"(?:\s+in\s+(?:beiden\s+Richtungen|Richtung\s+(?:(?:St|Bhf|Hbf|Dr|Str)\.|[^:.;]){1,80}?))?\s*:\s"
)
_WL_MEASURE_START_RE: re.Pattern[str] = re.compile(
    rf"(?:^|(?<=[.!?:])\s+)(?={_WL_MEASURE_LABEL})"
)
_WL_MEASURE_JOIN_RE: re.Pattern[str] = re.compile(rf"[.!?]\s+(?={_WL_MEASURE_LABEL})")
# WL also sets the line as a heading of its own, the measure on the next
# line (``<u>Linie D</u><br>Umleitung …``), which reaches the summary as
# ``Linie D • Umleitung …``: the colon form above, once the bullet becomes
# the colon (``_wl_measure_labels_with_colon``).
_WL_BULLET_LABEL_RE: re.Pattern[str] = re.compile(
    rf"(?<=[.!?:]\s)(Linien?\s+{_WL_DESC_LINE_TOKEN}"
    rf"(?:\s*(?:,|und|/|\+)\s*{_WL_DESC_LINE_TOKEN}){{0,20}}"
    r"(?:\s+\([^()]{1,40}\))?)\s+•\s+"
)


def _wl_measure_labels_with_colon(summary: str) -> str:
    """``"Linie D • Umleitung …"`` → ``"Linie D: Umleitung …"`` (see above)."""
    return _WL_BULLET_LABEL_RE.sub(r"\1: ", summary)


# The sentence that only announces the list: ``… kommt es zu folgenden
# Verkehrsmaßnahmen.``, ``… treten bei der Linie 80A Maßnahmen in Kraft.``
# The title already names the reason.
_WL_ANNOUNCEMENT_RE: re.Pattern[str] = re.compile(
    r"\bfolgenden\s+\w*(?:maßnahmen|änderungen|einschränkungen|umleitungen)\b"
    r"|\bMaßnahmen\s+in\s+Kraft\b",
    re.IGNORECASE,
)
# A request to the reader in front of the measures (``Beachten Sie bitte
# die unterschiedlichen Maßnahmen werktags bzw. am Wochenende.``): the
# measures themselves say it.
_WL_REQUEST_SENTENCE_RE: re.Pattern[str] = re.compile(r"^(?:Bitte\s+)?[A-ZÄÖÜ]\w+en\s+Sie\b")
# A bare label left over before the measures: ``Maßnahmen:``.
_WL_LABEL_SENTENCE_RE: re.Pattern[str] = re.compile(r"^(?:\w+\s*){1,3}:$")


def _prefer_measures(summary: str) -> str:
    """Put a WL notice's per-line measures first — the display budget is 180.

    WL writes its full notices in one shape: an announcement, a period,
    then one measure per line. Published 2026-09-19 for the seven-line
    demonstration notice, the extract kept the announcement and nothing
    else::

        Wegen einer Demonstration im Bereich Schwarzenbergplatz und Ring
        kommt es zu folgenden Verkehrsmaßnahmen.

    ``folgenden`` — and then the 180 characters were spent. The measures
    (``Linie D: Derzeit kein Betrieb zwischen Börse und Quartier
    Belvedere. Linie 1: Umleitung …``) never reached the display, and 20
    of the 37 notices in the cache have that block.

    When a text carries a measure block, the sentences before it lose
    the announcement (the title names the reason) and the ``Zeitraum:``
    sentence (the item's own date line renders the period); a sentence
    with content of its own — ``Wegen Instandsetzungsarbeiten in der
    Röntgengasse wird die Linie 44A kurzgeführt.`` — stays. The measures
    are joined with ``;`` so the two-sentence extract downstream treats
    them as one and ``_truncate_summary_180`` cuts them with an ellipsis
    instead of stopping after the first line's measure. Texts without a
    measure block are returned unchanged.
    """
    match = _WL_MEASURE_START_RE.search(summary)
    if match is None:
        return summary
    head = summary[: match.start()].strip()
    measures = _WL_MEASURE_JOIN_RE.sub("; ", summary[match.end() :].strip())
    kept = [
        sentence
        for sentence in (part.strip() for part in _SENTENCE_SPLIT_RE.split(head))
        if sentence
        and not sentence.startswith("Zeitraum:")
        and not _WL_ANNOUNCEMENT_RE.search(sentence)
        and not _WL_REQUEST_SENTENCE_RE.match(sentence)
        and not _WL_LABEL_SENTENCE_RE.match(sentence)
    ]
    return " ".join([*kept, measures])


# --- Every line of a WL disruption, in short (operator decision 2026-10-15) --
#
# WL writes the long message of a disruption on several lines as one
# sentence per line. The summary keeps two sentences and 180 characters, so
# the feed showed only the first line's measure. On 2026-10-11 the gas leak
# in Brünner Straße read
#
#     30A/31A/36B: Gasrohrgebrechen
#     Die Linie 36B wird in Richtung Jedlesee, Bellgasse zwischen
#     Berzeliusgasse und Brünner Straße S über Brünner Straße umgeleitet.
#
# and said nothing of the detours of 30A and 31A. 70 WL texts since July
# lost the measure of at least one line this way; where the text labels
# each line ("Linie 37: … Linie 38: …"), the first label was stripped as
# well (``_strip_wl_description_line_prefix``), and "Betrieb nur zwischen
# Hohe Warte und Nußdorfer Straße U" stood without its line. Michael's
# card pick "Kurzform je Linie" (2026-10-15, "Am TV sieht man sonst nur,
# was mit einer von drei Linien passiert"): each line with its measure and
# its two end points, without direction and detour streets::
#
#     Umleitungen: 36B Berzeliusgasse – Brünner Straße S; 30A
#     Berzeliusgasse – Gschweidlgasse; 31A Berzeliusgasse – Großjedlersdorf,
#     Jochbergengasse.
#
# Lines with different measures name them each ("37: Betrieb nur Hohe
# Warte – Nußdorfer Straße U; 5: Umleitung …"). WL's expected end follows
# when it fits. It applies only where a line would go missing: a text
# whose shown part (first sentences, 180 cut) names every line stays as WL
# wrote it, and so does one with a measure the patterns below do not know; lines that do not fit the
# 180 characters give way whole, last first, behind "…".
_SHORT_LINE_MARKER_RE: re.Pattern[str] = re.compile(
    rf"(?:^|(?<=[.!?;])\s+)(?:Linien?\s+(?P<label>{_WL_DESC_LINE_TOKEN}"
    rf"(?:\s*(?:,|und|/|\+)\s*{_WL_DESC_LINE_TOKEN}){{0,20}})\s*:\s+"
    rf"|(?P<lb>Badner\s+Bahn)\s*:\s+"
    rf"|Die\s+(?:Züge\s+der\s+)?Linie\s+(?P<subject>{_WL_DESC_LINE_TOKEN})\s+(?=wird|werden|fährt))"
)
_SHORT_TAIL_RE: re.Pattern[str] = re.compile(
    r"\s+(?:Weichen Sie|Voraussichtlich bis|Voraussichtliche Dauer|Grund:)"
)
_SHORT_EXPECTED_END_RE: re.Pattern[str] = re.compile(r"Voraussichtlich bis [^.]*?\d{1,2}:\d{2} Uhr\.")
# A place ends before "über", "örtlich", "umgeleitet", "in Richtung", the
# sentence end or a clause of its own: "und" or a comma before a small word
# (WL names its stops with capitals: "Heinestraße, Taborstraße"). On
# 2026-05-10 the 1 "fährt nur zwischen Stefan-Fadinger-Platz und
# Radetzkyplatz und wird ab Kliebergasse über die Linien 18 und O
# umgeleitet", and the short form read "1: Betrieb nur Stefan-Fadinger-Platz
# – Radetzkyplatz und wird ab Kliebergasse …". What follows the clause goes
# the way of "Weiterfahrt …". WL's abbreviations ("Bhf.", "Str.", "St.",
# "Dr.-Adolf-…") and ordinals ("Zentralfriedhof, 4. Tor") are part of it.
_SHORT_PLACE = r"(?:Bhf\.|Str\.|St\.|Dr\.-?|\b\d{1,2}\.(?=\s)|[^.;])+?"
_SHORT_PLACE_END = (
    r"(?=\s+(?:über|örtlich|umgeleitet|in\s+Richtung)\b|(?:,|\s+und)\s+[a-zäöüß]|[.;]|$)"
)
_SHORT_MEASURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Kein Betrieb", re.compile(
        rf"\b(?:fährt\s+(?:derzeit\s+)?nicht|[Kk]ein\s+Betrieb)\s+zwischen\s+(?P<a>{_SHORT_PLACE})\s+und\s+"
        rf"(?P<b>{_SHORT_PLACE}){_SHORT_PLACE_END}")),
    ("Betrieb nur", re.compile(
        rf"\b(?:fährt\s+(?:derzeit\s+)?nur|Betrieb\s+nur)\s+zwischen\s+(?P<a>{_SHORT_PLACE})\s+und\s+"
        rf"(?P<b>{_SHORT_PLACE}){_SHORT_PLACE_END}")),
    ("Umleitung", re.compile(
        rf"^wird\b[^.;]*?\bzwischen\s+(?P<a>{_SHORT_PLACE})\s+und\s+(?P<b>{_SHORT_PLACE}){_SHORT_PLACE_END}"
        rf"[^;]*?\bumgeleitet\b")),
    ("Umleitung", re.compile(
        rf"^Umleitung\b[^.;]*?\bzwischen\s+(?P<a>{_SHORT_PLACE})\s+und\s+(?P<b>{_SHORT_PLACE}){_SHORT_PLACE_END}")),
)
# A measure without places: the line as a whole.
_SHORT_BARE_MEASURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Fahrtbehinderung", re.compile(r"^Fahrtbehinderung\b")),
    ("Unregelmäßige Intervalle", re.compile(r"^Unregelmäßige Intervalle\b")),
    ("Verspätungen", re.compile(r"^Verspätungen\b")),
    ("Kein Betrieb", re.compile(r"^(?:Kein Betrieb|Derzeit ist ein Betrieb nicht möglich)\s*(?:[.;]|$)")),
)
_SHORT_GROUP_LABEL = {"Umleitung": "Umleitungen"}
# WL's own name for its line LB (the title says "LB", the text "Badner Bahn:").
_SHORT_LINE_NAME = {"LB": "Badner Bahn"}


def _short_measure(body: str) -> tuple[str, str] | None:
    """``("Umleitung", "Berzeliusgasse – Gschweidlgasse")`` for one line's sentence, or ``None``."""
    for kind, pattern in _SHORT_MEASURES:
        match = pattern.search(body)
        if match is not None:
            a, b = match["a"].strip(" ,"), match["b"].strip(" ,")
            if not re.search(r"[A-Za-zÄÖÜäöüß]{2}", a) or not re.search(r"[A-Za-zÄÖÜäöüß]{2}", b):
                return None
            return kind, f"{a} – {b}"
    for kind, pattern in _SHORT_BARE_MEASURES:
        if pattern.match(body):
            return kind, ""
    return None


def _line_segments(text: str, title_lines: Sequence[str]) -> list[tuple[str, str]] | None:
    """``[(line label, its text), …]`` in WL's order, or ``None`` if a part belongs to no line.

    A text before the first label belongs to the one title line no label
    names: ``_post_filter_wl`` strips the first label (see above).
    """
    markers = list(_SHORT_LINE_MARKER_RE.finditer(text))
    if not markers:
        return None
    segments: list[tuple[str, str]] = []
    labels = [
        "/".join(re.split(r"\s*(?:,|und|/|\+)\s*", m["label"]))
        if m["label"]
        else "LB" if m["lb"] else str(m["subject"])
        for m in markers
    ]
    named = {line for label in labels for line in label.split("/")}
    lead = text[: markers[0].start()].strip()
    if lead:
        unnamed = [line for line in title_lines if line not in named]
        if len(unnamed) != 1:
            return None
        segments.append((unnamed[0], lead))
    for index, (marker, label) in enumerate(zip(markers, labels, strict=True)):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        body = text[marker.start("subject") + len(label) if marker["subject"] else marker.end() : end].strip()
        segments.append((label, body))
    return segments


def _title_line_list(title: str) -> list[str]:
    """``["30A", "31A", "36B"]`` for "30A/31A/36B: Gasrohrgebrechen"."""
    prefix = re.match(r"^([^:]+):", title)
    return prefix[1].split("/") if prefix else []


def _every_line_in_short(item: FeedItem, summary: str, shown: str, title: str) -> str | None:
    """The short form of a WL disruption's lines when *shown* misses one, else ``None``.

    *summary* is the whole text, *shown* what the TV would show without
    the short form (sentences and the 180 cut applied).
    """
    if item.get("source") != "Wiener Linien" or item.get("category") != "Störung":
        return None
    if all(_text_names_line(shown, line) for line in _title_line_list(title)):
        return None
    return _lines_in_short(summary, title)


def _lines_in_short(summary: str, title: str) -> str | None:
    """*summary* of a WL disruption on several lines with every line in short (see above).

    ``None`` when a line's measure is not one of the known kinds.
    """
    title_lines = _title_line_list(title)
    if len(title_lines) < 2:
        return None
    tail = _SHORT_TAIL_RE.search(summary)
    measures = summary[: tail.start()] if tail else summary
    segments = _line_segments(measures.strip(), title_lines)
    if segments is None or len(segments) < 2:
        return None
    parsed: list[tuple[str, str, str]] = []
    for label, body in segments:
        short = _short_measure(body)
        if short is None:
            return None
        parsed.append((_SHORT_LINE_NAME.get(label, label), *short))
    if {line for label, _b in segments for line in label.split("/")} != set(title_lines):
        return None
    kinds = {kind for _l, kind, _p in parsed}
    if len(kinds) == 1:
        (kind,) = kinds
        head = f"{_SHORT_GROUP_LABEL.get(kind, kind)}: "
        parts = [f"{label} {place}".strip() for label, _k, place in parsed]
        separator = ", " if not any(place for _l, _k, place in parsed) else "; "
    else:
        head = ""
        parts = [f"{label}: {kind} {place}".strip() for label, kind, place in parsed]
        separator = "; "
    text = f"{head}{separator.join(parts)}."
    while len(text) > 180 and len(parts) > 1:
        parts.pop()
        text = f"{head}{separator.join(parts)} …"
    expected = _SHORT_EXPECTED_END_RE.search(summary[tail.start():] if tail else "")
    if expected and len(f"{text} {expected[0]}") <= 180:
        text = f"{text} {expected[0]}"
    return text


def _text_names_line(text: str, line: str) -> bool:
    """Whether *text* speaks of *line* ("Linie 30A", "Linien 30A und 31A", "Linie 38:")."""
    return re.search(
        rf"\bLinien?\s+(?:[\w]+(?:,\s*|\s+und\s+|\s+sowie\s+))*{re.escape(line)}\b", text
    ) is not None
