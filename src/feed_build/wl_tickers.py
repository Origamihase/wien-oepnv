"""WL tickers: their parts, publication windows and groups, causes and line runs.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, UTC
from typing import NamedTuple

from ..feed.glossary import _GLOSSARY_BASE, _GLOSSARY_BY_SOURCE
from ..feed_types import FeedItem
from ..providers.wl_lines import line_order_key
from ..utils.text import html_to_text, names_planned_measure

from .abbreviations import is_abbreviation_period
from .base import _to_utc
from .display_titles import _TITLE_BODY_RE
from .title_words import _is_wl_ticker, _reason_and_fragment, _rendered_title

__all__ = [
    "_WL_STOCK_SENTENCE_RE", "_ticker_prefix", "_ticker_text", "_ticker_part", "_message_tokens",
    "_distinct_messages", "_shared_openings", "_without_cause", "_ticker_time", "_ticker_lines",
    "_publication_key", "_ticker_groups", "_ticker_line_tokens", "_union_prefix", "_twin_labels",
    "_telling_long_message", "_cause_key", "_chosen_cause", "_group_span", "_spans_meet", "_MAX_LISTED_WORDS",
    "_line_runs", "_run_incidents", "_names_incident", "_incident_cause",
]


# --- One incident, one slot (operator decision 2026-09-26) -----------------
#
# WL sends one display-board ticker per consequence of an incident. On
# 2026-09-26 line 62 took three of the ten slots within 86 seconds::
#
#     15:10:21  62: ÖBB Bauarbeiten Betrieb ab Kliebergasse
#     15:10:51  62: Züge halte bei Linie 18, Richtung Burggasse
#     15:11:47  62: ÖBB Bauarbeiten Kein Betrieb
#
# Over 693 feed versions 242 carried such a group, 485 slots in all. The
# provider folds a ticker into a long message that already says it
# (``_fold_display_tickers``), and ``_short_title_collisions`` keeps equal
# short titles apart; tickers among themselves were never joined. Now WL
# disruptions of the same lines published within WL_TICKER_CLUSTER_SECONDS of
# the first one become one item: the most frequent cause in the title, every
# consequence in the description, in the order WL published them::
#
#     62: ÖBB Bauarbeiten
#     Betrieb ab Kliebergasse; Züge halte bei Linie 18, Richtung Burggasse; Kein Betrieb.
#
# Nothing a ticker says is dropped unless another one of the group, or the
# title, already says it; WL's stock sentence gives way to anything concrete.
# A long message that says more than the stock sentence stands, and its
# tickers take no slot of their own (2026-09-27). What tickers of its cause,
# or of none, announce beyond its text is kept (``_with_consequences``,
# 2026-10-01); a ticker of another cause stays out of it.
WL_TICKER_CLUSTER_SECONDS = 600

_WL_STOCK_SENTENCE_RE = re.compile(
    r"^Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen\.?$", re.IGNORECASE
)
_TICKER_ARROWS = " <>"  # a ticker line may end in the display's arrow
_MAX_CAUSE_WORDS = 3


class _TickerPart(NamedTuple):
    """What one WL disruption contributes to its group."""

    cause: str  # "" when it names none
    body: str  # the title without the line prefix, as WL wrote it
    consequence: str  # "" for a message that only names its cause
    extra: str  # text beyond the title (a long message's sentences)


def _ticker_prefix(title: str) -> str:
    """``62: ÖBB Bauarbeiten`` → ``62: `` (empty without a line prefix)."""
    match = _TITLE_BODY_RE.match(title)
    return title[: match.start(1)] if match else ""


def _ticker_text(item: FeedItem) -> str:
    """*item*'s description as one line, without the display's arrows.

    The arrows ("Hütteldorfer Straße > mit Linie 46") point, they say nothing.
    """
    return " ".join(
        word for word in html_to_text(str(item.get("description") or "")).split() if word not in ("<", ">")
    )


# A mark that may end a sentence inside a text: more text follows it.
_INNER_SENTENCE_MARK_RE = re.compile(r"[.!?](?=\s)")


def _is_long_message(text: str) -> bool:
    """WL's long message (``stoerunglang``) is written in sentences.

    A display ticker is one or two board lines without a full stop. The
    ``Linie 48A:`` label cannot mark the long message: ``_post_filter_wl``
    strips it when the feed reads the cache, and some long messages never
    had it ("Die Linie U1 fährt derzeit …"). WL cache, September 2026: all
    504 texts in sentences end in a full stop, none of 247 tickers does.

    A ticker can end in the period of an abbreviation ("Züge halten bei
    Linie 12 Richtung Josefstädter Str."); that is no sentence
    (:func:`is_abbreviation_period`) unless another one ends inside the
    text. Taken for a long message on 23.10.2026, the ticker kept its cause
    and the feed read "12: Fremdunfall" over "Fremdunfall Züge halten bei
    Linie 12 Richtung Josefstädter Str. Fremder Verkehrsunfall.".
    """
    if not text.endswith("."):
        return False
    if not is_abbreviation_period(text, len(text) - 1):
        return True
    return any(
        mark.group() != "." or not is_abbreviation_period(text, mark.start())
        for mark in _INNER_SENTENCE_MARK_RE.finditer(text)
    )


def _ticker_part(item: FeedItem) -> _TickerPart:
    """Split one WL disruption into cause, consequence and further text."""
    title = _rendered_title(item)
    prefix = _ticker_prefix(title)
    body = title[len(prefix):].strip()
    text = _ticker_text(item)
    split = _reason_and_fragment(title)
    if _is_long_message(text):
        if split is not None:
            return _TickerPart(split[0][len(_ticker_prefix(split[0])):], body, split[1], text)
        return _TickerPart(body, body, "", text)
    if split is not None:
        return _TickerPart(split[0][len(_ticker_prefix(split[0])):], body, split[1], "")
    shown = text
    if shown.casefold().endswith(body.casefold()):
        # The display's first line is the cause when the title left it out:
        # "Gleisbauarbeiten / Betrieb ab Johnstraße U" under "12A: Betrieb ab Johnstraße U".
        lead = shown[: len(shown) - len(body)].strip(" -–:")
        words = lead.split()
        if 0 < len(words) <= _MAX_CAUSE_WORDS and lead[:1].isupper() and not any(ch.isdigit() for ch in lead):
            return _TickerPart(lead, body, body, "")
        return _TickerPart("", body, body, "")
    return _TickerPart("", body, body, text)


# WL causes that name the measure, not a reason: "N71: Ersatzverkehr Busse
# halten …", "31: Kein Betrieb …" (the parser reads "Kein"), "26E: Busse
# halten Satzingerweg 41", "27: Linie 25 in Tokiostraße benützen", "40/41/42:
# Derzeit kein Linienbetrieb", "12: Betrieb ab Franz-Josefs-Bahnhof" (a title
# without a cause reads as one). Over the WL cache since July 2026 such
# messages began on a full hour; they keep the clock rule
# (:func:`time_line._scheduled_clock`).
_MEASURE_CAUSE_RE = re.compile(
    r"^(?:ersatz(?:verkehr|bus)|(?:derzeit\s+)?kein|(?:busse|züge)\s+halten|linie\b.*\bbenützen"
    r"|betrieb\s+(?:ab|über|bis|nur|zwischen)|umleitung)\b",
    re.IGNORECASE,
)
# The display text names the reason of a delay: "Unregelmäßige Intervalle in
# beiden Richtungen. Grund: Schadhaftes Fahrzeug." under "U2: Verspätungen".
_GRUND_RE = re.compile(r"\bGrund:\s*([^.#]+)")


def _names_incident(cause: str) -> bool:
    """Whether *cause* names an incident: set, no planned measure, no measure word."""
    cause = cause.strip()
    return bool(cause) and not names_planned_measure(cause) and not _MEASURE_CAUSE_RE.match(cause)


def _incident_cause(item: FeedItem) -> bool:
    """Whether the WL disruption *item* names an incident as its cause.

    The cause of its title (:func:`_ticker_part`) or the "Grund:" of its
    text. Such a disruption is an incident even when it began on a full hour
    (operator decision 2026-11-03, "Eine Störung kann auch zur vollen Stunde
    gemeldet werden").
    """
    if _names_incident(_ticker_part(item).cause):
        return True
    match = _GRUND_RE.search(html_to_text(str(item.get("description") or "")))
    return match is not None and _names_incident(match.group(1))


def _message_tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"\w+", text.casefold()))


def _distinct_messages(candidates: Sequence[str], title: str) -> list[str]:
    """*candidates* without what the title or a fuller candidate already says."""
    title_tokens = _message_tokens(title[len(_ticker_prefix(title)):])
    texts = [c.strip().rstrip(_TICKER_ARROWS).strip() for c in candidates]
    texts = [t for t in texts if t and not _message_tokens(t) <= title_tokens]
    kept: list[str] = []
    for index, text in enumerate(texts):
        tokens = _message_tokens(text)
        covered = any(
            tokens < _message_tokens(other) or (tokens == _message_tokens(other) and other_index < index)
            for other_index, other in enumerate(texts)
            if other_index != index
        )
        if not covered:
            kept.append(text)
    concrete = [t for t in kept if not _WL_STOCK_SENTENCE_RE.match(t)]
    return concrete or kept


def _shared_openings(messages: Sequence[str]) -> list[str]:
    """Consequences that open with the same two words name the opening once.

    ``Busse halten Bessemerstraße 1-3; Busse halten auf Hauptfahrbahn`` reads
    ``Busse halten Bessemerstraße 1-3, auf Hauptfahrbahn``. Until 2026-10-01
    ``deduplicate_fuzzy`` wrote that list when it joined two tickers first
    (``_collapse_common_prefix``); the WL merge joins them now. A
    consequence with a comma of its own ("Züge halten bei Li. 18 Richtung
    Burggasse, Stadthalle") stays apart: a list inside the list would read
    as one more stop.
    """
    openings: dict[str, int] = {}
    joined: list[str] = []
    for message in messages:
        words = message.split()
        opening = " ".join(words[:2]).casefold()
        if len(words) < 3 or "," in message or any(ch.isdigit() for ch in opening):
            joined.append(message)
            continue
        index = openings.get(opening)
        if index is None:
            openings[opening] = len(joined)
            joined.append(message)
        else:
            joined[index] = f"{joined[index]}, {' '.join(words[2:])}"
    return joined


def _without_cause(text: str, cause: str) -> str:
    """Drop the title's cause from the front of a consequence the split missed.

    "Stromstörung Züge fahren bis Johann-Nepomuk-Berger-Platz" under
    ``43: Stromstörung`` reads "Züge fahren bis …".
    """
    if cause and text.casefold().startswith(f"{cause} "):
        return text[len(cause):].strip(" –-:")
    return text


def _ticker_time(item: FeedItem) -> datetime | None:
    when = item.get("pubDate") or item.get("starts_at")
    return _to_utc(when) if isinstance(when, datetime) else None


def _ticker_lines(item: FeedItem) -> str:
    """``62: ÖBB Bauarbeiten`` → ``62`` (casefolded, empty without a line prefix)."""
    return _ticker_prefix(_rendered_title(item)).strip().rstrip(":").casefold()


def _publication_key(items: Sequence[FeedItem], index: int) -> tuple[datetime, int, str, int]:
    """WL's publication order: time, a long message before tickers of the same second, GUID."""
    item = items[index]
    when = _ticker_time(item) or datetime.min.replace(tzinfo=UTC)
    long_first = 0 if _is_long_message(_ticker_text(item)) else 1
    return when, long_first, str(item.get("guid") or ""), index


def _ticker_groups(items: Sequence[FeedItem]) -> list[list[int]]:
    """Indices of WL disruptions by incident: one line set, one publication window."""
    by_lines: dict[str, list[tuple[datetime, int, str, int]]] = defaultdict(list)
    for index, item in enumerate(items):
        if not _is_wl_ticker(item):
            continue
        prefix = _ticker_lines(item)
        if not prefix or _ticker_time(item) is None:
            continue
        by_lines[prefix].append(_publication_key(items, index))
    groups: list[list[int]] = []
    for members in by_lines.values():
        members.sort()
        start: datetime | None = None
        for when, _long_first, _guid, index in members:
            if start is None or (when - start).total_seconds() > WL_TICKER_CLUSTER_SECONDS:
                groups.append([])
                start = when
            groups[-1].append(index)
    return _join_twin_groups(items, groups)


# --- One ticker, several lines (2026-10-02) ----------------------------------
#
# WL sends the same display ticker once per line. On 2026-10-02 at 01:00:12
# "N65: Busse halten Laxenburger Straße 66" and "N66: Busse halten
# Laxenburger Straße 66" came in the same second; grouped by line, the stop
# stood in two of the ten slots, once as "N65: Busse halten Laxenburger
# Straße 66" and once inside "N66: Bauarbeiten". Two tickers of different
# lines with the same title body and the same text, published within
# WL_TICKER_CLUSTER_SECONDS, now join their groups: one entry under all
# their lines. Nothing is lost, the texts are equal; a consequence that only
# some of the lines announced keeps their label::
#
#     N65/N66: Bauarbeiten
#     Busse halten Laxenburger Straße 66; N66: Busse halten Salvatorianerplatz.
#
# A group with a long message joins only a group that says exactly the same,
# message for message (2026-10-03). WL sends its long message once per line
# too: "6: Schadhaftes Fahrzeug" and "18: Schadhaftes Fahrzeug", both
# "Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen."
# in the same minute, and four such messages for 16A, 17A, 67A and 67B
# ("Störung an einem Bahnübergang"). Replayed over the 3,875 German feed
# versions since 2026-07-15, such twins stood in 540 of them and took 808
# slots that other disruptions lacked; 9 are left. Where the groups differ
# in any message, the long message speaks for its own line set and the
# groups stay apart.


def _twin_key(item: FeedItem) -> str:
    """What two tickers of different lines must share to be one: body and text."""
    return f"{_ticker_part(item).body.casefold()}\n{_ticker_text(item).casefold()}"


def _ticker_line_tokens(item: FeedItem) -> list[str]:
    """``N65/N66: …`` → ``["N65", "N66"]``."""
    return [token.strip() for token in _ticker_prefix(_rendered_title(item)).strip().rstrip(":").split("/") if token.strip()]


def _join_twin_groups(items: Sequence[FeedItem], groups: list[list[int]]) -> list[list[int]]:
    """*groups* with those joined that share a ticker of another line (see above)."""
    owner = {index: number for number, group in enumerate(groups) for index in group}
    has_long = {number for number, group in enumerate(groups) if any(_is_long_message(_ticker_text(items[i])) for i in group)}
    parent = list(range(len(groups)))

    def root(number: int) -> int:
        while parent[number] != number:
            number = parent[number]
        return number

    by_key: dict[str, list[tuple[datetime, int, str, int]]] = defaultdict(list)
    for index, number in owner.items():
        if number not in has_long:
            by_key[_twin_key(items[index])].append(_publication_key(items, index))
    for number in has_long:
        # A group with a long message joins only a group that says exactly
        # the same, message for message (see below).
        signature = "\n\n".join(sorted({_twin_key(items[index]) for index in groups[number]}))
        by_key[f"long\n{signature}"].append(_publication_key(items, groups[number][0]))
    for twins in by_key.values():
        twins.sort()
        for (when, _l, _g, first), (later, _l2, _g2, second) in zip(twins, twins[1:], strict=False):
            if (later - when).total_seconds() <= WL_TICKER_CLUSTER_SECONDS and _ticker_lines(items[first]) != _ticker_lines(items[second]):
                parent[root(owner[second])] = root(owner[first])
    if all(parent[number] == number for number in range(len(groups))):
        return groups
    joined: dict[int, list[int]] = {}
    for number, group in enumerate(groups):
        joined.setdefault(root(number), []).extend(group)
    return [sorted(group, key=lambda index: _publication_key(items, index)) for group in joined.values()]


def _union_prefix(members: Sequence[FeedItem]) -> str | None:
    """``N65/N66: `` for members of several line sets, ``None`` for one set."""
    if len({_ticker_lines(member) for member in members}) < 2:
        return None
    tokens: dict[str, str] = {}
    for member in members:
        for token in _ticker_line_tokens(member):
            tokens.setdefault(token.casefold(), token)
    return "/".join(sorted(tokens.values(), key=line_order_key)) + ": "


def _group_lines_key(members: Sequence[FeedItem]) -> str:
    """The line set a group stands for: its union for joined twins (see above)."""
    union = _union_prefix(members)
    return union.rstrip(": ").casefold() if union else _ticker_lines(members[0])


def _twin_labels(members: Sequence[FeedItem], union: str | None) -> tuple[list[FeedItem], list[str]]:
    """*members* with each twin set once, and the line label each message keeps.

    A message all lines of the union announced needs no label; one only some
    of them announced is prefixed with those (``N66: ``).
    """
    if union is None:
        return list(members), [""] * len(members)
    all_lines = {token.casefold() for token in union.rstrip(": ").split("/")}
    shown: dict[str, tuple[FeedItem, dict[str, str]]] = {}
    for member in members:
        _item, lines = shown.setdefault(_twin_key(member), (member, {}))
        for token in _ticker_line_tokens(member):
            lines.setdefault(token.casefold(), token)
    kept = [item for item, _lines in shown.values()]
    labels = [
        "" if set(lines) == all_lines else "/".join(sorted(lines.values(), key=line_order_key)) + ": "
        for _item, lines in shown.values()
    ]
    return kept, labels


def _telling_long_message(members: Sequence[FeedItem]) -> FeedItem | None:
    """The first of *members* that is a long message saying more than the stock sentence."""
    for member in members:
        text = _ticker_text(member)
        if _is_long_message(text) and not _WL_STOCK_SENTENCE_RE.match(text):
            return member
    return None


# WL names one incident differently on the display and in the long message.
# Within one publication window, cache since September: "Schadhafter Zug"
# beside "Schadhaftes Fahrzeug" 14 times, "Fremdunfall" or "Verkehrsunfall"
# beside "Fremder Verkehrsunfall" 9, "Beschädigte Oberleitung" beside
# "Oberleitungsgebrechen" 6, "Schadhafter Bus" beside "Schadhaftes
# Fahrzeug" once, "Gasrohrbruch" beside "Gasrohrgebrechen" (2026-10-11).
# Only what WL wrote side by side; "Bauarbeiten" and
# "Gleisbauarbeiten" can be two sites and stay apart.
_CAUSE_SYNONYMS: dict[str, str] = {
    "schadhafter zug": "schadhaftes fahrzeug",
    "schadhafter bus": "schadhaftes fahrzeug",
    "fremdunfall": "fremder verkehrsunfall",
    "verkehrsunfall": "fremder verkehrsunfall",
    "beschädigte oberleitung": "oberleitungsgebrechen",
    "oberleitungsgebr": "oberleitungsgebrechen",
    "rettungseinatz": "rettungseinsatz",
    "gasrohrbruch": "gasrohrgebrechen",
    # "62: Beschädigte Oberleitung" beside "62: Fahrleitungsschaden Betrieb
    # ab Hofwiesengasse" four minutes later (2026-10-28).
    "fahrleitungsschaden": "oberleitungsgebrechen",
}


def _same_translation() -> dict[str, str]:
    """The causes the glossary renders with one English term, each mapped to the first term listed.

    "13A: Wasserrohrbruch, Wasserrohrgebrechen" (2026-11-05 20:31 to 06.11.
    05:30): the display wrote "Wasserrohrbruch", the long message
    "Wasserrohrgebrechen", and the English title read "Burst water pipe,
    burst water pipe" — ``src/feed/glossary.py`` renders both as "burst
    water pipe". Two causes the English feed cannot tell apart are one
    cause, and the glossary says which ("Reparaturarbeiten" and
    "Instandsetzungsarbeiten", "Oberleitungsstörung" and
    "Fahrleitungsgebrechen"). Operator decision 2026-11-06.
    """
    first_for: dict[str, str] = {}
    alike: dict[str, str] = {}
    for german, english in {**_GLOSSARY_BASE, **_GLOSSARY_BY_SOURCE.get("Wiener Linien", {})}.items():
        key = " ".join(german.casefold().split())
        first = first_for.setdefault(" ".join(english.casefold().split()), key)
        if first != key:
            alike[key] = first
    return alike


_SAME_TRANSLATION: dict[str, str] = _same_translation()


def _cause_key(cause: str) -> str:
    """*cause* for comparison: casefolded, one space, WL's synonyms and the glossary's resolved.

    Both maps are applied twice: a synonym can lead into a glossary group
    ("Fahrleitungsschaden" → "Oberleitungsgebrechen" → "Oberleitungsstörung")
    and a glossary group into a synonym ("Schadhaftem Zug" → "Schadhafter
    Zug" → "Schadhaftes Fahrzeug").
    """
    return _expanded_abbreviation(_resolved(" ".join(cause.casefold().split())))


def _resolved(key: str) -> str:
    """*key* with WL's synonyms and the glossary's groups resolved, twice (see above)."""
    for _ in range(2):
        key = _CAUSE_SYNONYMS.get(key, key)
        key = _SAME_TRANSLATION.get(key, key)
    return key


# WL's display cuts a long cause to fit its line: "12: Fahrleitungsgebr."
# and "2: Fahrleitungsgebr" stood beside the long message "2/12/31:
# Beschädigte Oberleitung durch einen LKW" on 2026-11-09 from 12:30 to 14:30
# Vienna time, and "12: Fahrleitungsgebr, Beschädigte Oberleitung" read as
# two causes on 2026-11-05. A cause the synonyms and the glossary do not
# know is written out when, without a closing period and with at least six
# letters, it opens the causes of exactly one group ("fahrleitungsgebr" →
# "fahrleitungsgebrechen" → "oberleitungsstörung"); one that opens two
# groups stays as WL wrote it. A rule for every cut cause, not a list of
# cut words (operator decision 2026-11-10, card "Zusammenfassen prüfen").
_MIN_ABBREVIATION = 6


def _known_causes() -> frozenset[str]:
    """Every cause the synonyms and the glossary know, casefolded."""
    glossary = {**_GLOSSARY_BASE, **_GLOSSARY_BY_SOURCE.get("Wiener Linien", {})}
    return frozenset({*_CAUSE_SYNONYMS, *_CAUSE_SYNONYMS.values(), *(" ".join(g.casefold().split()) for g in glossary)})


_KNOWN_CAUSES = _known_causes()


def _expanded_abbreviation(key: str) -> str:
    """*key* written out when WL's display cut it (see above)."""
    if key in _KNOWN_CAUSES:
        return key
    stem = key.rstrip(". ")
    if len(stem) < _MIN_ABBREVIATION:
        return key
    groups = {_resolved(name) for name in _KNOWN_CAUSES if name.startswith(stem)}
    return groups.pop() if len(groups) == 1 else key


def _chosen_cause(parts: Sequence[_TickerPart]) -> str:
    """The most frequent cause, the earliest on a tie; casefolded, "" for none."""
    counts: dict[str, int] = defaultdict(int)
    for part in parts:
        if part.cause:
            counts[part.cause.casefold()] += 1
    order = [part.cause.casefold() for part in parts if part.cause]
    return max(counts, key=lambda cause: (counts[cause], -order.index(cause)), default="")


def _group_span(members: Sequence[FeedItem]) -> tuple[datetime, datetime | None] | None:
    """Earliest start and latest end of *members* in UTC; an open end stays open."""
    starts = [_to_utc(s) for s in (m.get("starts_at") or m.get("pubDate") for m in members) if isinstance(s, datetime)]
    if not starts:
        return None
    ends = [e for e in (m.get("ends_at") for m in members) if isinstance(e, datetime)]
    return min(starts), (max(_to_utc(e) for e in ends) if len(ends) == len(members) else None)


def _spans_meet(a: tuple[datetime, datetime | None], b: tuple[datetime, datetime | None]) -> bool:
    """Whether two validity spans overlap; ``None`` is an open end."""
    return (b[1] is None or a[0] <= b[1]) and (a[1] is None or b[0] <= a[1])


# --- One line, one slot (operator decision 2026-10-01) ---------------------
#
# "Mehrere Störungsmeldungen zur selben Linie sollte so gut wie möglich
# zusammengefasst werden"; where different lines are affected, each keeps its
# entry. An incident above ends with its publication window. On 2026-10-01
# the four tickers of 18's "Gleisschaden" came at 16:25, 16:31, 16:33 and
# 16:37, and 66A stood twice for an afternoon: "Rettungseinsatz" over
# "Busse halten Salvatorianerplatz" (Bauarbeiten, 01.10.–02.10.). Over the
# 236 feed versions since 2026-09-27, 112 had a line in more than one entry.
#
# So after the windows, the WL disruptions of the same lines whose validity
# meets (``_line_runs``) become one entry; this also brings a long message
# and its tickers of another window together:
#
# * one cause, WL's synonyms counted (``_CAUSE_SYNONYMS``): one incident,
#   merged as above; a long message stands for it, and what the other
#   tickers announce beyond its text is kept (``_incident_entry``). A
#   disruption without a cause joins the incident published nearest to it;
# * several causes: one entry naming each, the newest first, under the GUID
#   of the newest, so a new incident still opens the feed::
#
#     66A: Rettungseinsatz, Bauarbeiten
#     Rettungseinsatz: Unregelmäßige Intervalle in beiden Richtungen. Bauarbeiten: Busse halten Salvatorianerplatz.
#
# A cause longer than _MAX_LISTED_WORDS words is a sentence, not a cause to
# list: 18's "Haltestelle Stadionbrücke im Rahmen des Straßenbahn-Neubaus der
# Linie 18 aufgelassen. Bitte …", a disruption in the cache since July, keeps
# its own entry and joins no other.
_MAX_LISTED_WORDS = 6


def _group_cause(members: Sequence[FeedItem]) -> str:
    """The cause the entry of a group names, casefolded; "" for none."""
    long_message = _telling_long_message(members)
    if long_message is not None:
        return _ticker_part(long_message).cause.casefold()
    return _chosen_cause([_ticker_part(member) for member in members])


def _line_runs(items: Sequence[FeedItem], groups: Sequence[list[int]]) -> list[list[list[int]]]:
    """*groups* of the same lines whose validity meets, transitively.

    A group with a sentence for a cause, or without a span, stands as a run
    of its own.
    """
    by_lines: dict[str, list[tuple[datetime, datetime | None, list[int]]]] = defaultdict(list)
    runs: list[list[list[int]]] = []
    for group in groups:
        members = [items[index] for index in group]
        span = _group_span(members)
        if span is None or len(_group_cause(members).split()) > _MAX_LISTED_WORDS:
            runs.append([group])
            continue
        by_lines[_group_lines_key(members)].append((span[0], span[1], group))
    for entries in by_lines.values():
        entries.sort(key=lambda entry: entry[0])
        run: list[list[int]] = []
        reach: datetime | None = None
        for start, end, group in entries:
            if run and (reach is None or start <= reach):
                run.append(group)
                reach = None if reach is None or end is None else max(reach, end)
                continue
            if run:
                runs.append(run)
            run, reach = [group], end
        runs.append(run)
    return runs


def _run_incidents(
    items: Sequence[FeedItem], run: Sequence[list[int]]
) -> tuple[list[list[list[int]]], list[list[int]]]:
    """The incidents of *run*, its groups by cause (WL's synonyms counted), and the groups of none.

    A group without a cause joins the incident published nearest to it, of
    those that had begun when it appeared: a display text cannot announce
    an incident that came later. Tickers that come a few minutes ahead of
    their incident (``wl_resolved.TICKER_LEAD``) share its group anyway
    (``WL_TICKER_CLUSTER_SECONDS``). One older than every incident belongs
    to none of them and comes back second (``_with_unowned``). Without any
    incident, the groups without a cause are the one incident.
    """
    incidents: dict[str, list[list[int]]] = {}
    causeless: list[list[int]] = []
    for group in run:
        cause = _cause_key(_group_cause([items[index] for index in group]))
        if cause:
            incidents.setdefault(cause, []).append(group)
        else:
            causeless.append(group)
    if not incidents:
        return ([causeless] if causeless else []), []
    alone: list[list[int]] = []
    for group in causeless:
        when = _publication_key(items, group[0])[0]
        begun = [
            groups
            for groups in incidents.values()
            if min(_publication_key(items, other[0])[0] for other in groups) <= when
        ]
        if not begun:
            alone.append(group)
            continue
        nearest = min(
            begun,
            key=lambda groups: min(abs((_publication_key(items, other[0])[0] - when).total_seconds()) for other in groups),
        )
        nearest.append(group)
    return list(incidents.values()), alone
