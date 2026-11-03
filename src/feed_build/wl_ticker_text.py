"""The text of a merged WL entry: consequences, incidents and their causes.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, UTC
from typing import cast

from ..feed_types import FeedItem
from ..providers.wl_plausibility import incident_begin

from .base import _SENTENCE_SPLIT_RE, _to_utc
from .time_line import _wl_incident_start
from .title_words import _HINDRANCE, _as_sentence, _rendered_title
from .wl_tickers import (
    _WL_STOCK_SENTENCE_RE, _cause_key, _chosen_cause, _distinct_messages, _message_tokens, _publication_key,
    _shared_openings, _telling_long_message, _ticker_part, _ticker_prefix, _ticker_text, _twin_labels,
    _union_prefix, _without_cause,
)

__all__ = [
    "_LONG_MESSAGE_TAIL_RE", "_RIDER_CONSEQUENCE_RE", "_said_already", "_incident_entry", "_incident_start",
    "_title_cause", "_combined_incidents",
]


def _span_group(merged: FeedItem, members: Sequence[FeedItem]) -> None:
    """Give *merged* the time span of its group.

    The start stays the lead's: WL can reuse an old ticker for a new
    incident, and its start must not spread to the group (25 on 2026-09-27:
    a breakdown that morning under "23.09.2026 – 27.09.2026").
    """
    ends = [e for e in (m.get("ends_at") for m in members) if isinstance(e, datetime)]
    # An open end of one member keeps the incident open.
    merged["ends_at"] = max(ends, key=_to_utc) if len(ends) == len(members) else None
    published = [p for p in (m.get("pubDate") for m in members) if isinstance(p, datetime)]
    incidents = [s for s in map(_wl_incident_start, members) if s is not None]
    if incidents:
        merged["_incident_start"] = min(incidents, key=_to_utc)
    if published:
        # A ticker WL rewrote for this incident keeps an old start
        # (``wl_plausibility.incident_begin``).
        merged["pubDate"] = incident_begin(min(published, key=_to_utc), _wl_incident_start(merged))


def _merged_ticker(members: Sequence[FeedItem]) -> FeedItem:
    """One item for the WL disruptions *members* (publication order, lead first)."""
    lead = members[0]
    merged = cast(FeedItem, dict(lead))
    _span_group(merged, members)
    long_message = _telling_long_message(members)
    if long_message is not None:
        # WL's own text stands. A ticker of another cause stays out: beside
        # the text it read as a second cause (48A on 2026-09-27: "Falschparker"
        # over "Grund: Fremder Verkehrsunfall"). Tickers of the same cause, or
        # of none, keep what the text does not say (``_with_consequences``):
        # "Betrieb ab Mühlbreiten" of 64A on 2026-10-01 stood nowhere else.
        cause = _cause_key(_ticker_part(long_message).cause)
        same = [m for m in members if m is not long_message and _cause_key(_ticker_part(m).cause) in ("", cause)]
        stands = _with_consequences(long_message, same) if same else long_message
        union = _union_prefix(members)
        title = str(stands.get("title") or "")
        # Twins of several lines (see ``_join_twin_groups``) stand under all of them.
        merged["title"] = f"{union}{title[len(_ticker_prefix(title)):]}" if union else title
        merged["description"] = str(stands.get("description") or "")
        return merged
    union = _union_prefix(members)
    shown, labels = _twin_labels(members, union)
    parts = [_ticker_part(member) for member in shown]
    chosen = _chosen_cause(parts)
    lead_title = _rendered_title(lead)
    prefix = union or _ticker_prefix(lead_title)
    if chosen:
        cause = next(part.cause for part in parts if part.cause.casefold() == chosen)
        title = f"{prefix}{cause}"
    else:
        title = f"{prefix}{lead_title[len(_ticker_prefix(lead_title)):]}"
    candidates: list[str] = []
    for part, label in zip(parts, labels, strict=True):
        if part.cause and _cause_key(part.cause) != _cause_key(chosen):
            candidates.append(label + part.body)  # WL's wording keeps the other cause with its consequence
        elif part.consequence:
            candidates.append(label + _without_cause(part.consequence, chosen))
        if part.extra:
            candidates.append(part.extra)
    messages = _shared_openings(_distinct_messages(candidates, title))
    merged["title"] = title
    # One sentence: the summary keeps at most two (``_SENTENCE_SPLIT_RE``), and
    # three short consequences would lose the third. The 180-character cap
    # still applies; the order is WL's.
    merged["description"] = "; ".join(m.rstrip(" .") for m in messages) + ("." if messages else "")
    return merged


# A WL long message names the measure first, then the advice, the expected
# end and the cause: "Betrieb nur zwischen Schottenring U und Stephansplatz U.
# Voraussichtliche Dauer: 14:45 Uhr. Grund: Polizeieinsatz …". The sentence
# split cannot see the end behind "U.", these openings can.
_LONG_MESSAGE_TAIL_RE = re.compile(r"\s+(?:Weichen Sie|Voraussichtliche Dauer|Grund:)")


# What a rider has to do, as WL's texts say it.
_RIDER_CONSEQUENCE_RE = re.compile(
    r"\b(?:Kein Betrieb|Betrieb (?:ab|nur|über|zwischen|bis)|Züge halten|Busse halten|hält|Umleitung|umgeleitet"
    r"|Ersatzbus|Shuttlebus|Einstieg|benützen|benutzen|ausweichen)\b",
    re.IGNORECASE,
)
# A negation says nothing a text in other words does not: "Kein Betrieb"
# beside "Derzeit ist ein Betrieb nicht möglich" (1A on 2026-09-22).
_NEGATIONS: frozenset[str] = frozenset({"kein", "keine", "keinen", "nicht"})


def _said_already(message: str, said: frozenset[str]) -> bool:
    """Whether every word of *message* that carries meaning is among *said*.

    Words of up to three letters carry none here: "Betrieb ab
    Hofwiesengasse" adds nothing to "Betrieb nur zwischen Westbahnhof S U
    und Hofwiesengasse" (60 on 2026-09-22). A longer or shorter form counts
    from five letters on: "Verspätung" is in "Verspätungen".
    """
    return all(
        token in said
        or any(len(token) >= 5 and len(word) >= 5 and (word.startswith(token) or token.startswith(word)) for word in said)
        for token in _message_tokens(message)
        if len(token) > 3 and token not in _NEGATIONS
    )


def _with_consequences(entry: FeedItem, members: Sequence[FeedItem]) -> FeedItem:
    """*entry* with what *members* announce and its own text does not.

    A long message stands for its incident, within its window and beyond;
    without this it would drop the tickers of the same cause, or of none,
    with their stops. A bare "Fahrtbehinderung" adds nothing. Where its
    measure says what a rider has to do, the tickers join its sentence
    (60 on 2026-09-22: "Betrieb nur zwischen Westbahnhof S U und
    Hofwiesengasse; Züge halten bei der Linie 62 Fahrtrichtung Lainz.
    Weichen Sie …"). Where it does not, they come first (26E from
    2026-09-25: "Busse halten Satzingerweg 41, …; Ersatzbus hält
    Karl-Waldbrunner-Platz …" before "Die Kapazitäten der Ersatzlinie
    26E …"). What tells a rider nothing to do comes last.
    """
    text = _ticker_text(entry)
    said = _message_tokens(f"{_rendered_title(entry)} {text}")
    cause = _ticker_part(entry).cause.casefold()
    # The cause glued in front ("Gasrohrgebrechen Shuttlebus eingerichtet, …")
    # and WL's display markup ("**Verspätung**") say nothing.
    candidates = [
        _without_cause(part.consequence or part.body, cause).replace("*", "").strip()
        for part in map(_ticker_part, members)
    ]
    messages = [
        m for m in _distinct_messages(candidates, _rendered_title(entry))
        if m.casefold() != _HINDRANCE.casefold() and not _said_already(m, said)
    ]
    if not messages:
        return entry
    rider = [m.rstrip(" .") for m in _shared_openings(messages) if _RIDER_CONSEQUENCE_RE.search(m)]
    rest = [m.rstrip(" .") for m in messages if not _RIDER_CONSEQUENCE_RE.search(m)]
    tail = _LONG_MESSAGE_TAIL_RE.search(text)
    cut = tail.start() if tail else len(text)
    measure = text[:cut].rstrip()
    if rider and _RIDER_CONSEQUENCE_RE.search(measure):
        # In the measure's own sentence, before the advice, expected end and
        # cause: the summary keeps two sentences, and "Wipplingerstr 39." ends
        # none it can see (D on 2026-09-30).
        text = f"{measure.rstrip('.')}; {_as_sentence('; '.join(rider))}{text[cut:]}"
    elif rider:
        text = f"{_as_sentence('; '.join(rider))} {text}"
    if rest:
        # What tells a rider nothing to do ("Derzeit längere Wartezeiten!")
        # must not push the text's own cause or end out of the summary.
        text = f"{text} {_as_sentence('; '.join(rest))}"
    merged = cast(FeedItem, dict(entry))
    merged["description"] = text
    return merged


def _incident_entry(items: Sequence[FeedItem], groups: Sequence[list[int]]) -> tuple[int, FeedItem]:
    """The entry of one incident and the index it takes.

    A long message stands for its incident, as within its window; of
    several, the newest. What the groups without a long message announce
    beyond its text is kept (``_with_consequences``). Otherwise all
    messages of the incident are merged as one window would be.
    """
    telling = [group for group in groups if _telling_long_message([items[index] for index in group]) is not None]
    if not telling:
        members = sorted((index for group in groups for index in group), key=lambda i: _publication_key(items, i))
        if len(members) == 1:
            return members[0], items[members[0]]
        return members[0], _merged_ticker([items[index] for index in members])
    chosen = max(telling, key=lambda group: _publication_key(items, group[0]))
    entry = items[chosen[0]] if len(chosen) == 1 else _merged_ticker([items[index] for index in chosen])
    others = [items[index] for group in groups if group not in telling for index in group]
    return chosen[0], _with_consequences(entry, others) if others else entry


def _incident_start(entry: FeedItem) -> datetime:
    when = entry.get("starts_at") or entry.get("pubDate")
    return _to_utc(when) if isinstance(when, datetime) else datetime.min.replace(tzinfo=UTC)


def _listed_incident(entry: FeedItem) -> tuple[str, str]:
    """Label and detail of one incident in a combined entry.

    ``Demonstration`` and ``Betrieb nur zwischen St. Marx S und Landstraße S
    U; Betrieb ab Landstraße`` — the measure a long message opens with,
    without the advice, the expected end and the "Grund: …" the label
    already says (``_LONG_MESSAGE_TAIL_RE``), then the consequence the
    display names where it adds to that ("Busse halten bei der Linie 14A");
    a bare "Fahrtbehinderung" beside a measure adds nothing. "" when
    nothing is left.
    """
    part = _ticker_part(entry)
    label = part.cause or part.body
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(_LONG_MESSAGE_TAIL_RE.split(part.extra, maxsplit=1)[0])]
    reason = f"grund: {label.casefold()}"
    measure = next((s for s in sentences if s and s.rstrip(" .").casefold() != reason), "").rstrip(" .")
    consequence = part.consequence.strip().rstrip(" .")
    if consequence and _WL_STOCK_SENTENCE_RE.match(f"{measure}."):
        measure = ""  # WL's stock sentence gives way to anything concrete
    details = [measure] if measure else []
    if consequence and not (
        measure and (consequence.casefold() == _HINDRANCE.casefold() or _message_tokens(consequence) <= _message_tokens(measure))
    ):
        details.append(consequence)
    return label, "; ".join(d for d in details if d.casefold() != label.casefold())


def _incident_sentences(listed: Sequence[tuple[str, str]]) -> list[str]:
    """One sentence per incident that says more than its label, within 180 characters.

    The summary keeps two sentences and 180 characters. An incident that
    only has its label (or WL's stock sentence beside something concrete)
    is named by the title and takes no sentence; over the budget, the
    longest detail gives up its last consequence first (62 on 2026-09-28:
    "ÖBB Bauarbeiten: Züge halten bei Linie 18, Richtung Burggasse; Kein
    Betrieb" beside "Oberleitungsgebrechen: …"). A label whose agent the
    title leaves out (:func:`_title_cause`) says it here, with or without
    a detail: "Beschädigte Oberleitung durch einen LKW."
    """
    concrete = any(detail and not _WL_STOCK_SENTENCE_RE.match(f"{detail}.") for _, detail in listed)
    said = [
        (label, detail if detail and not (concrete and _WL_STOCK_SENTENCE_RE.match(f"{detail}.")) else "")
        for label, detail in listed
    ]
    kept = [(label, detail.split("; ") if detail else []) for label, detail in said if detail or _title_cause(label) != label]
    if not kept:
        return [_as_sentence(label) for label, _ in listed]

    def sentence(label: str, parts: Sequence[str]) -> str:
        return _as_sentence(f"{label}: {'; '.join(parts)}" if parts else label)

    shared = {"; ".join(parts).casefold() for _, parts in kept if parts}
    if len(kept) > 1 and len(shared) == 1 and all(parts or _title_cause(label) != label for label, parts in kept):
        # Two incidents with one consequence say it once: "Fahrtbehinderung."
        # under "71: Polizeieinsatz, Rettungseinsatz" (2026-10-29), not once
        # per cause; an agent the title leaves out says it with it.
        agents = [label for label, _ in kept if _title_cause(label) != label]
        parts = next(parts for _, parts in kept if parts)
        return [sentence(" und ".join(agents), parts) if agents else _as_sentence("; ".join(parts))]

    while len(" ".join(sentence(label, parts) for label, parts in kept)) > 180:
        longest = max(kept, key=lambda entry: len("; ".join(entry[1])))
        if len(longest[1]) < 2:
            break
        longest[1].pop()
    return [sentence(label, parts) for label, parts in kept]


# Who caused it stands in the text, not in the title (operator decision
# 2026-11-02): "27: Stromstörung, Signalstörung, Beschädigte Oberleitung"
# over "Beschädigte Oberleitung durch einen LKW." (2026-10-27). WL's causes
# since July with such an agent: "Beschädigte Oberleitung durch einen LKW",
# "Fahrleitungsgebrechen durch LKW"; a place ("PKW im Gleis", "Störung an
# einem Bahnübergang") is part of the cause and stays.
_CAUSE_AGENT_RE = re.compile(r"^(?P<cause>[A-ZÄÖÜ].*?)\s+durch\s+\S.*$")


def _title_cause(label: str) -> str:
    """*label* as a combined title names it: without the agent behind "durch"."""
    match = _CAUSE_AGENT_RE.match(label)
    return match["cause"] if match else label


def _combined_incidents(entries: Sequence[FeedItem]) -> FeedItem:
    """One entry for different incidents of the same lines, newest first (see above)."""
    lead = entries[0]
    merged = cast(FeedItem, dict(lead))
    listed = [_listed_incident(entry) for entry in entries]
    merged["title"] = _ticker_prefix(_rendered_title(lead)) + ", ".join(_title_cause(label) for label, _ in listed)
    merged["description"] = " ".join(_incident_sentences(listed))
    ends = [e for e in (entry.get("ends_at") for entry in entries) if isinstance(e, datetime)]
    merged["ends_at"] = max(ends, key=_to_utc) if len(ends) == len(entries) else None
    merged["_wl_incidents"] = len(entries)
    return merged
