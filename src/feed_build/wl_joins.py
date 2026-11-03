"""Joining WL entries: one line, one slot, two causes, tickers of some lines.

``_merge_wl_ticker_clusters`` is the entry point.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import cast

from ..feed_types import FeedItem
from ..feed.merge import member_guids
from ..utils.logging import sanitize_log_arg

from .base import _to_utc, log
from .wl_measures import _SHORT_LINE_MARKER_RE, _short_measure, _text_names_line
from .title_words import _HINDRANCE, _as_sentence, _is_wl_ticker, _rendered_title
from .wl_tickers import (
    _MAX_LISTED_WORDS, _cause_key, _chosen_cause, _distinct_messages, _group_span, _line_runs, _message_tokens,
    _run_incidents, _spans_meet, _telling_long_message, _ticker_groups, _ticker_line_tokens, _ticker_part,
    _ticker_text, _ticker_time, _without_cause,
)
from .wl_ticker_text import (
    _LONG_MESSAGE_TAIL_RE, _RIDER_CONSEQUENCE_RE, _combined_incidents, _incident_entry, _incident_start,
    _said_already,
)

__all__ = [
    "_split_by_cause", "_merge_wl_ticker_clusters",
]


def _with_members(entry: FeedItem, items: Sequence[FeedItem], groups: Sequence[list[int]]) -> FeedItem:
    """*entry* with the GUIDs of the messages of *groups* as ``_members``."""
    marked = cast(FeedItem, dict(entry))
    marked["_members"] = sorted({guid for group in groups for index in group for guid in member_guids(items[index])})
    return marked


def _merged_run(items: Sequence[FeedItem], run: Sequence[list[int]]) -> tuple[int, FeedItem]:
    """The entry for *run* and the index it takes.

    Its ``_members`` are the messages of the incident whose GUID it carries
    (see ``_carry_item_identity``): of several incidents, the newest. What
    belongs to no incident comes last and is no member (``_with_unowned``).
    """
    found, unowned = _run_incidents(items, run)
    incidents = [
        (place, _with_members(entry, items, groups))
        for groups in found
        for place, entry in (_incident_entry(items, groups),)
    ]
    if len(incidents) == 1:
        place, entry = incidents[0]
    else:
        ordered = sorted(
            incidents,
            key=lambda incident: (_incident_start(incident[1]), str(incident[1].get("guid") or "")),
            reverse=True,
        )
        place, entry = ordered[0][0], _combined_incidents([entry for _, entry in ordered])
    if unowned:
        entry = _with_unowned(entry, [items[index] for group in unowned for index in group])
    return place, entry


def _with_unowned(entry: FeedItem, members: Sequence[FeedItem]) -> FeedItem:
    """*entry* with what *members*, display texts older than its incidents, say, at the end.

    WL repeats the stops of planned works on the displays for weeks
    without a cause ("25: Ersatzbus 26E hält Karl-Waldbrunner-Platz vor
    Schloßhofer Straße", "Züge halten Donaufelder Straße 175-177", in the
    data since 22.09.2026). On 2026-10-09 at 09:36 they joined "25:
    Verspätungen" (Grund: Schadhaftes Fahrzeug) as its consequences: the
    feed read the works' stops in front of the delay and lost its cause,
    and when the delay ended, the stops kept its GUID and begin and stood
    as a new disruption from 12:01 to 20:30, on up to place 2. The line
    still takes one slot (operator decision 2026-10-01), but the stops
    follow the incident's own text, after its cause, and stay out of
    ``_members``: when the incident ends, the item falls back to their own
    GUID and begin.
    """
    text = _ticker_text(entry)
    said = _message_tokens(f"{_rendered_title(entry)} {text}")
    candidates = [(part.consequence or part.body).replace("*", "").strip() for part in map(_ticker_part, members)]
    messages = [
        m.rstrip(" .")
        for m in _distinct_messages(candidates, _rendered_title(entry))
        if m.casefold() != _HINDRANCE.casefold() and not _said_already(m, said)
    ]
    if not messages:
        return entry
    merged = cast(FeedItem, dict(entry))
    merged["description"] = f"{text} {_as_sentence('; '.join(messages))}" if text else _as_sentence("; ".join(messages))
    return merged


# --- Two causes in one publication window (2026-11-02) -----------------------
#
# A publication window held every WL disruption of its lines, whatever its
# cause, and ``_merged_ticker`` named only the most frequent cause, the
# earliest on a tie. On 2026-10-29 from 18:31 to 18:59 Vienna time WL had two
# incidents on the 71 nine minutes apart (I20261029-0032 and -0034), and the
# feed read::
#
#     71: Rettungseinsatz
#     Fahrtbehinderung wegen Polizeieinsatz.
#
# The second cause stood in the text only, as if it were the first one's
# consequence ("42: Falschparker" over "Fahrtbehinderung wegen
# Rettungseinsatz." the same evening). Two incidents ten minutes apart were
# already listed side by side ("U3: Polizeieinsatz, Rettungseinsatz",
# ``_combined_incidents``); within one window they were not.
#
# Now a window without a long message of its own (``_telling_long_message``)
# is split by cause, WL's synonyms counted (``_CAUSE_SYNONYMS``) and a cause
# with WL's advice behind it counted as the cause it opens with
# (``_window_cause``), before the
# line runs; each cause is an incident of the run, and the entry names each,
# the newest first. A ticker without a cause stays with the cause the window
# named so far. Where a cause is a sentence (``_MAX_LISTED_WORDS``) the
# window stays whole: it would take a slot of its own. Operator decision
# 2026-11-02 ("Titel nennt beide Ursachen").
def _split_by_cause(items: Sequence[FeedItem], groups: Sequence[list[int]]) -> list[list[int]]:
    """*groups* with each window of several causes split into one group per cause (see above)."""
    split: list[list[int]] = []
    for group in groups:
        members = [items[index] for index in group]
        parts = [_ticker_part(member) for member in members]
        keys = {_cause_key(part.cause) for part in parts if part.cause}
        causes = list(dict.fromkeys(_window_cause(part.cause, keys) for part in parts if part.cause))
        if (
            len(causes) < 2
            or _telling_long_message(members) is not None
            or any(len(cause.split()) > _MAX_LISTED_WORDS for cause in causes)
        ):
            split.append(group)
            continue
        chosen = _window_cause(_chosen_cause(parts), keys)
        by_cause: dict[str, list[int]] = {cause: [] for cause in causes}
        for index, part in zip(group, parts, strict=True):
            by_cause[_window_cause(part.cause, keys) if part.cause else chosen].append(index)
        split.extend(by_cause.values())
    return split


def _window_cause(cause: str, keys: set[str]) -> str:
    """*cause*'s key, or that of another cause of its window it opens with.

    "Gleisschaden - Linie 11 benutzen" is the "Gleisschaden" of the ticker
    beside it (O on 2026-10-24): WL's display wrote the advice behind the
    cause, and the title read "O: Gleisschaden - Linie 11 benutzen, Gleisschaden".
    """
    words = cause.casefold().split()
    for end in range(1, len(words)):
        key = _cause_key(" ".join(words[:end]))
        if key in keys:
            return key
    return _cause_key(cause)


def _merge_wl_ticker_clusters(items: list[FeedItem]) -> list[FeedItem]:
    """Join the WL disruptions of one incident, and of one line, into one item (see above)."""
    groups = _split_by_cause(items, _ticker_groups(items))
    replaced: dict[int, FeedItem] = {}
    dropped: set[int] = set()
    for run in _line_runs(items, groups):
        members = [index for group in run for index in group]
        if len(members) < 2:
            continue
        place, entry = _merged_run(items, run)
        replaced[place] = entry
        dropped.update(index for index in members if index != place)
        log.info(
            "WL-Kurzmeldungen zusammengelegt: %s ← %s",
            sanitize_log_arg(entry["title"]),
            sanitize_log_arg(" | ".join(_rendered_title(items[index]) for index in sorted(members))),
        )
    if dropped:
        items = [replaced.get(index, item) for index, item in enumerate(items) if index not in dropped]
    return _join_part_line_tickers(items)


# --- Tickers of some lines of a longer incident (2026-10-15) -----------------
#
# The merge above joins WL disruptions of the same line set. WL writes one
# long message for all lines of an incident but a display ticker per line,
# so these never met. On 2026-10-11 from 11:30 to 23:30 Vienna time a gas
# leak in Brünner Straße took four of the ten slots::
#
#     36B/30A/31A: Gasrohrgebrechen   Die Linie 36B wird … umgeleitet.
#     30A: Gasrohrbruch               Betrieb ab Gschweidlgasse.
#     36B: Gasrohrbruch               Betrieb ab Brünner Straße.
#     31A: Gasrohrbruch               Kein Betrieb.
#
# The provider folds a ticker into a long message only when the message
# repeats every word of the ticker (``wl_fetch._fold_display_tickers``);
# the display says "Gasrohrbruch" where the message says "Gasrohrgebrechen".
# "37/38/40/41/42: Schadhaftes Fahrzeug" stood beside "38/42: Schadhafter
# Zug" and "37: Schadhafter Zug" the same day.
#
# Now a WL disruption that is display tickers only joins the entry of a WL
# incident (one with a WL number) of more lines when it is that incident's
# ticker: its lines are among the incident's, it names the same cause, WL's
# synonyms counted (``_CAUSE_SYNONYMS``), it began at most
# ``wl_resolved.TICKER_LEAD`` before the incident and their validity meets.
# Of several such incidents the one begun last owns it, as in
# ``wl_resolved``. A ticker of another cause keeps its slot ("43:
# Falschparker" beside "43/44: Rettungseinsatz", 2026-10-09).
#
# A line the incident's text speaks of ("Die Linie 31A wird … umgeleitet",
# or the measure in front of the first label, whose "Linie 2:" the reading
# stripped: ``_stripped_label_line``) has WL's own statement there; the
# ticker of one stop ("Kein Betrieb" at the Brünner Straße display, while
# the 31A runs a detour) adds nothing to it and would contradict it on the
# screen. What a ticker of a line the text does
# not name announces is kept under its line ("37: Betrieb ab …").
_TICKER_LEAD = timedelta(minutes=10)  # ``wl_resolved.TICKER_LEAD``


def _has_wl_number(item: FeedItem) -> bool:
    """Whether *item* stands for a WL message with an incident number (not tickers only)."""
    return any(member.startswith("wl:") for member in member_guids(item))


def _stripped_label_line(text: str, lines: Sequence[str]) -> str | None:
    """The line whose "Linie X:" ``_post_filter_wl`` stripped from the start of *text*, if any.

    WL's "Linie 2: Umleitung … Linie 10: Betrieb nur …" reaches the merge as
    "Umleitung … Linie 10: Betrieb nur …": the text speaks of the 2 without
    naming it (2026-10-19, "2/10: Fremder Verkehrsunfall"). A text begins
    with that line's measure when one line is unnamed and what stands in
    front of the first label is a measure the short form knows
    (:func:`_short_measure`); a cause sentence there is no line's.
    """
    unnamed = [line for line in lines if not _text_names_line(text, line)]
    marker = _SHORT_LINE_MARKER_RE.search(text)
    if len(unnamed) != 1 or marker is None or marker.start() == 0:
        return None
    lead = text[: marker.start()].strip()
    return unnamed[0] if _short_measure(lead) is not None else None


def _ticker_owner(ticker: FeedItem, incidents: Sequence[tuple[int, FeedItem]]) -> int | None:
    """The index of the incident entry whose ticker *ticker* is, if any (see above)."""
    lines = {token.casefold() for token in _ticker_line_tokens(ticker)}
    cause = _cause_key(_ticker_part(ticker).cause)
    began, span = _ticker_time(ticker), _group_span([ticker])
    if not lines or not cause or began is None or span is None:
        return None
    fitting: list[tuple[datetime, int]] = []
    for index, entry in incidents:
        start, entry_span = _ticker_time(entry), _group_span([entry])
        if start is None or entry_span is None or began < start - _TICKER_LEAD:
            continue
        if not lines < {token.casefold() for token in _ticker_line_tokens(entry)}:
            continue
        if _cause_key(_ticker_part(entry).cause) != cause or not _spans_meet(span, entry_span):
            continue
        fitting.append((start, index))
    return max(fitting)[1] if fitting else None


def _with_line_tickers(entry: FeedItem, tickers: Sequence[FeedItem]) -> FeedItem:
    """*entry* with its *tickers* joined (see above)."""
    text = _ticker_text(entry)
    said = _message_tokens(f"{_rendered_title(entry)} {text}")
    cause = _ticker_part(entry).cause.casefold()
    stripped = _stripped_label_line(text, _ticker_line_tokens(entry))
    added: list[str] = []
    for ticker in tickers:
        lines = _ticker_line_tokens(ticker)
        if all(line == stripped or _text_names_line(text, line) for line in lines):
            continue
        part = _ticker_part(ticker)
        message = _without_cause(part.consequence or part.extra or part.body, cause).replace("*", "").strip(" .")
        if not message or message.casefold() == _HINDRANCE.casefold() or _said_already(message, said):
            continue
        added.append(f"{'/'.join(lines)}: {message}")
    merged = cast(FeedItem, dict(entry))
    merged["_members"] = sorted({guid for item in (entry, *tickers) for guid in member_guids(item)})
    ends = [e for e in (item.get("ends_at") for item in (entry, *tickers)) if isinstance(e, datetime)]
    merged["ends_at"] = max(ends, key=_to_utc) if len(ends) == 1 + len(tickers) else None
    if not added:
        return merged
    # Placed as in ``_with_consequences``: behind the measure, in front of
    # the advice, the expected end and the cause; in front of a text that
    # names no measure (WL's stock sentence); what tells a rider nothing to
    # do at the end.
    rider = [m for m in added if _RIDER_CONSEQUENCE_RE.search(m)]
    rest = [m for m in added if not _RIDER_CONSEQUENCE_RE.search(m)]
    tail = _LONG_MESSAGE_TAIL_RE.search(text)
    cut = tail.start() if tail else len(text)
    measure = text[:cut].rstrip()
    if rider and _RIDER_CONSEQUENCE_RE.search(measure):
        text = f"{measure} {_as_sentence('; '.join(rider))}{text[cut:]}"
    elif rider:
        text = f"{_as_sentence('; '.join(rider))} {text}"
    if rest:
        text = f"{text} {_as_sentence('; '.join(rest))}"
    merged["description"] = text
    return merged


def _join_part_line_tickers(items: list[FeedItem]) -> list[FeedItem]:
    """Join display tickers of some lines into their incident of more lines (see above)."""
    incidents = [(index, item) for index, item in enumerate(items) if _is_wl_ticker(item) and _has_wl_number(item)]
    if not incidents:
        return items
    owned: dict[int, list[FeedItem]] = defaultdict(list)
    dropped: set[int] = set()
    for index, item in enumerate(items):
        if not _is_wl_ticker(item) or _has_wl_number(item):
            continue
        owner = _ticker_owner(item, incidents)
        if owner is None:
            continue
        owned[owner].append(item)
        dropped.add(index)
    if not dropped:
        return items
    out: list[FeedItem] = []
    for index, item in enumerate(items):
        if index in dropped:
            continue
        if index in owned:
            item = _with_line_tickers(item, owned[index])
            log.info(
                "WL-Kurzmeldungen in die Meldung mehrerer Linien aufgenommen: %s ← %s",
                sanitize_log_arg(_rendered_title(item)),
                sanitize_log_arg(" | ".join(_rendered_title(ticker) for ticker in owned[index])),
            )
        out.append(item)
    return out
