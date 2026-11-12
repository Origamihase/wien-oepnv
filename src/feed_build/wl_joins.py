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
from ..utils.text import names_planned_measure

from .abbreviations import without_full_stop
from .base import _to_utc, log
from .time_line import _scheduled_clock
from .wl_measures import _SHORT_LINE_MARKER_RE, _short_measure, _text_names_line
from .title_words import _HINDRANCE, _as_sentence, _is_wl_ticker, _rendered_title, _then_sentence
from .wl_tickers import (
    _MAX_LISTED_WORDS, _group_cause, _cause_key, _chosen_cause, _distinct_messages, _group_span, _line_runs, _message_tokens,
    _run_incidents, _spans_meet, _telling_long_message, _ticker_groups, _ticker_line_tokens, _ticker_part,
    _names_incident,
    _ticker_text, _ticker_time, _without_cause, _WL_STOCK_SENTENCE_RE,
)
from .wl_ticker_text import (
    _LONG_MESSAGE_TAIL_RE, _RIDER_CONSEQUENCE_RE, _combined_incidents, _incident_entry, _incident_start,
    _said_already, _title_cause,
)

__all__ = [
    "_split_by_cause", "_merge_wl_ticker_clusters",
]


def _with_members(entry: FeedItem, items: Sequence[FeedItem], groups: Sequence[list[int]]) -> FeedItem:
    """*entry* with the GUIDs of the messages of *groups* as ``_members``."""
    marked = cast(FeedItem, dict(entry))
    marked["_members"] = sorted({guid for group in groups for index in group for guid in member_guids(items[index])})
    return marked


# --- An incident never joins planned works (operator decision 2026-11-03) ---
#
# "Störungen haben höchste Priorität im Feed, Hinweise sind weniger wichtig."
# One line took one slot (2026-10-01), so a running incident joined the
# display texts of the line's planned works: "1: Rettungseinsatz,
# Gleisbauarbeiten". The works' word made the whole entry planned
# (``_incident_since``), and the incident lost its "[Seit hh:mm]" and its
# place in front: on 2026-10-21 at 15:31 Vienna time that entry stood on
# place 9, under two stop relocations, and on 2026-10-16 at 12:01 "D:
# Rettungseinsatz, Gleisbauarbeiten" read only the works' stops ("Züge
# halten in Schleife, Wipplingerstr 39; …"). Now a run whose incidents are
# partly planned measures (their cause names one, ``names_planned_measure``)
# gives two entries: the running incidents, and the planned ones with the
# display texts older than every incident (``_with_unowned``) that are
# planned themselves, the works' stops whose words name a planned measure
# (``_planned_texts``; stops that name none follow the incident). Such stops
# beside a running incident alone make an entry of their own as well. The works' entry then goes into its notice
# (``_absorb_works_tickers``) or keeps its own place. Incidents among
# themselves, and planned measures among themselves, still share one slot.
def _planned_texts(items: Sequence[FeedItem], group: Sequence[int]) -> bool:
    """Whether a group without a cause names a planned measure in its texts.

    Only the words count, not a start on a full hour: stops that name no
    measure ("25: Züge halten Donaufelder Straße 175-177") leave the incident
    its "[Seit]" and still follow it in its slot (check of 2026-10-09).
    """
    return any(
        names_planned_measure(f"{_rendered_title(items[index])} {_ticker_text(items[index])}") for index in group
    )


def _planned_incident(items: Sequence[FeedItem], groups: Sequence[list[int]]) -> bool:
    """Whether an incident of a run (its groups, the first one with its cause) is a planned measure.

    Its cause names one, or it names no incident and every message of it
    began on a full hour, the mark of a measure WL entered ahead
    (``_scheduled_clock``, the rule of the time line): "26E: Ersatzbus hält
    Karl-Waldbrunner-Platz …" (no cause) switched on at 00:00:14 belongs to
    the works of the 26E. A cause that names an incident wins over the clock
    (operator decision 2026-11-03, "Störungswort gewinnt"): "11A: Fahrtbehinderung
    Schadhafter PKW" began at 00:00:28 on 2026-10-24 and stood as "[Heute]"
    beside "11A: Falschparker [Seit 00:03]", a second entry for the line that
    pushed a notice off place 10. An incident begins on a full hour about once
    in sixty (about 60 WL tickers with an incident cause since July).
    """
    cause = _group_cause([items[index] for index in groups[0]])
    if names_planned_measure(cause):
        return True
    if _names_incident(cause):
        return False
    starts = [_ticker_time(items[index]) for group in groups for index in group]
    return all(start is not None and _scheduled_clock(start) for start in starts)


def _merged_runs(items: Sequence[FeedItem], run: Sequence[list[int]]) -> list[tuple[int, FeedItem, list[int]]]:
    """The entries of *run*: the index each takes, the entry and the indices it stands for (see above)."""
    found, unowned = _run_incidents(items, run)
    planned = [groups for groups in found if _planned_incident(items, groups)]
    running = [groups for groups in found if not _planned_incident(items, groups)]
    # Display texts older than every incident and planned themselves ("62A:
    # Straßenbau Haltestelle verlegt …" beside "62A: Verkehrsunfall" on
    # 2026-10-22) stay with the planned measures, too.
    stops = [group for group in unowned if _planned_texts(items, group)]
    rest = [group for group in unowned if not _planned_texts(items, group)]
    if not running or not (planned or stops):
        place, entry = _merged_run(items, found, unowned)
        return [(place, entry, [index for group in run for index in group])]
    parts: list[tuple[int, FeedItem, list[int]]] = []
    for incidents, others in ((running, rest), (planned, stops)):
        if not incidents:
            # Planned stops without a planned incident: one entry of their own,
            # as ``_run_incidents`` makes of a run without an incident.
            incidents, others = ([list(others)] if others else []), []
        if not incidents:
            continue
        indices = [index for groups in incidents for group in groups for index in group]
        indices += [index for group in others for index in group]
        if len(indices) < 2:
            parts.append((indices[0], items[indices[0]], indices))
            continue
        place, entry = _merged_run(items, incidents, others)
        parts.append((place, entry, indices))
    return parts


def _merged_run(
    items: Sequence[FeedItem], found: Sequence[list[list[int]]], unowned: Sequence[list[int]]
) -> tuple[int, FeedItem]:
    """The entry for a run's incidents *found* and the groups of none, *unowned*, and the index it takes.

    Its ``_members`` are the messages of the incident whose GUID it carries
    (see ``_carry_item_identity``): of several incidents, the newest. What
    belongs to no incident comes last and is no member (``_with_unowned``).
    """
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
    as a new disruption from 12:01 to 22:02, on up to place 2. The line
    still takes one slot (operator decision 2026-10-01), but the stops
    follow the incident's own text, after its cause, and stay out of
    ``_members``: when the incident ends, the item falls back to their own
    GUID and begin.
    """
    text = _ticker_text(entry)
    said = _message_tokens(f"{_rendered_title(entry)} {text}")
    candidates = [(part.consequence or part.body).replace("*", "").strip() for part in map(_ticker_part, members)]
    messages = [
        without_full_stop(m)
        for m in _distinct_messages(candidates, _rendered_title(entry))
        if m.casefold() != _HINDRANCE.casefold() and not _said_already(m, said)
    ]
    if not messages:
        return entry
    merged = cast(FeedItem, dict(entry))
    # The incident's text closes first: a display ticker ends without a full
    # stop (``_then_sentence``, 26 on 2026-11-05).
    joined = _as_sentence("; ".join(messages))
    merged["description"] = _then_sentence(text, joined) if text else joined
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
#
# A window with a long message stays whole: the display names the long
# message's incident in its own word ("Falschparker" beside "Grund: Fremder
# Verkehrsunfall", 48A on 2026-09-27; "Verkehrsunfall" beside "Grund:
# Polizeieinsatz", 12 on 2026-11-05; "Betriebsstörung" beside "Grund:
# Fahrleitungsgebrechen", 2 the same day), and the long message stands for
# it with what the display adds (``_merged_ticker``). Two things are in the
# window only where a message of another cause is a long message itself, or
# where one names a planned measure and the other an incident
# (``_another_incident``): "1: Rettungseinsatz Betrieb ab Matzleinsdorfer
# Platz" beside the long message of the works of the 1 (2026-10-20), the
# works' stop of the N8 beside its "Feuerwehreinsatz" (2026-10-30). Such a
# window is split like one without a long message, and a ticker without a
# cause stays with the long message. An incident never joins planned works
# (operator decision 2026-11-03); until 2026-11-06 the whole window stood
# under the long message and dropped the other message (WL cache since
# 2026-10-16: 147 windows with a long message, 17 with another cause, 5 of
# them with a consequence).
def _split_by_cause(items: Sequence[FeedItem], groups: Sequence[list[int]]) -> list[list[int]]:
    """*groups* with each window of several causes split into one group per cause (see above)."""
    split: list[list[int]] = []
    for group in groups:
        members = [items[index] for index in group]
        parts = [_ticker_part(member) for member in members]
        keys = {_cause_key(part.cause) for part in parts if part.cause}
        causes = list(dict.fromkeys(_window_cause(part.cause, keys) for part in parts if part.cause))
        long_message = _telling_long_message(members)
        if (
            len(causes) < 2
            or (long_message is not None and not _another_incident(members, long_message))
            or any(len(cause.split()) > _MAX_LISTED_WORDS for cause in causes)
        ):
            split.append(group)
            continue
        own = _ticker_part(long_message).cause if long_message is not None else _chosen_cause(parts)
        chosen = _window_cause(own, keys)
        by_cause: dict[str, list[int]] = {cause: [] for cause in causes}
        for index, part in zip(group, parts, strict=True):
            by_cause[_window_cause(part.cause, keys) if part.cause else chosen].append(index)
        split.extend(by_cause.values())
    return split


def _another_incident(members: Sequence[FeedItem], long_message: FeedItem) -> bool:
    """Whether a message of another cause than *long_message*'s stands for an incident of its own (see above).

    A long message of another cause does; so does a ticker that announces
    more than a bare "Fahrtbehinderung" where one of the two names a planned
    measure (``names_planned_measure``) and the other does not.
    """
    own = _ticker_part(long_message)
    key, planned = _cause_key(own.cause), names_planned_measure(own.cause)
    for member in members:
        part = _ticker_part(member)
        if member is long_message or not part.cause or _cause_key(part.cause) == key:
            continue
        if _telling_long_message([member]) is not None:
            return True
        consequence = part.consequence.strip()
        if consequence and consequence.casefold() != _HINDRANCE.casefold() and names_planned_measure(part.cause) != planned:
            return True
    return False


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
        if sum(len(group) for group in run) < 2:
            continue
        for place, entry, members in _merged_runs(items, run):
            if len(members) < 2:
                continue
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
#
# Two gaps closed on 2026-11-10 (operator decision, card "Zusammenfassen
# prüfen"). On 2026-11-09 from 12:30 to 14:30 Vienna time "2/12/31:
# Beschädigte Oberleitung durch einen LKW" stood beside "12:
# Fahrleitungsgebr.", "31: Fahrleitungsgebr." and "2: Fahrleitungsgebr",
# four of the ten slots: the agent behind "durch" stayed in the compared
# cause, and the display's cut "Fahrleitungsgebr." was no known cause.
# The cause is now compared as the combined title names it
# (``_title_cause``) and a cut cause is written out (``_cause_key``). On
# 2026-10-23 ("40/41/42: Polizeieinsatz" beside "40/41: Polizeieinsatz")
# and 2026-11-10 ("37/40/41/42: Verkehrsunfall" beside "41: Verkehrsunfall")
# the roles were the other way round: the display tickers named every
# line, WL's numbered message only some, with nothing but its stock
# sentence. The entry of more lines now takes the one of fewer whenever
# exactly one of the two carries a WL number; two numbered messages, or
# two tickers only, keep their slots. WL's stock sentence adds nothing to
# an entry with a text of its own. Another cause, or none, still keeps its
# slot ("60: Falschparker" beside "10/60: Polizeieinsatz", 2026-11-10).
# Replay of all 2,207 feed states from 2026-09-29 to 2026-11-10: 11 change,
# all at these incidents and "12: Fahrleitungsgebr, Beschädigte Oberleitung"
# (2026-11-05), now "12: Beschädigte Oberleitung".
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
    """The index of the entry of more lines that takes *ticker*, if any (see above).

    Exactly one of the two carries a WL number: the ticker joins WL's
    message, or WL's message of some lines joins the display tickers of all.
    """
    lines = {token.casefold() for token in _ticker_line_tokens(ticker)}
    cause = _cause_key(_title_cause(_ticker_part(ticker).cause))
    numbered = _has_wl_number(ticker)
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
        if _has_wl_number(entry) == numbered:
            continue
        if _cause_key(_title_cause(_ticker_part(entry).cause)) != cause or not _spans_meet(span, entry_span):
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
        message = without_full_stop(
            _without_cause(part.consequence or part.extra or part.body, cause).replace("*", "").lstrip(" .")
        )
        if not message or message.casefold() == _HINDRANCE.casefold() or _said_already(message, said):
            continue
        if text and _WL_STOCK_SENTENCE_RE.match(f"{message}."):
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
        text = f"{_then_sentence(measure, _as_sentence('; '.join(rider)))}{text[cut:]}"
    elif rider:
        text = f"{_as_sentence('; '.join(rider))} {text}"
    if rest:
        text = _then_sentence(text, _as_sentence("; ".join(rest)))
    merged["description"] = text
    return merged


def _join_part_line_tickers(items: list[FeedItem]) -> list[FeedItem]:
    """Join display tickers of some lines into their incident of more lines (see above)."""
    owners = [
        (index, item) for index, item in enumerate(items) if _is_wl_ticker(item) and len(_ticker_line_tokens(item)) > 1
    ]
    if not owners:
        return items
    owned: dict[int, list[FeedItem]] = defaultdict(list)
    dropped: set[int] = set()
    for index, item in enumerate(items):
        if not _is_wl_ticker(item):
            continue
        # An entry that took others keeps its slot; one already taken takes none.
        owner = _ticker_owner(item, [(at, entry) for at, entry in owners if at != index and at not in dropped])
        if owner is None or index in owned:
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
