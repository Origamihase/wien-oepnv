"""Bookkeeping across runs: occurrences, first_seen and the identity carry.

Moved verbatim from ``src/build_feed.py`` (700-line rule, Etappe 8); ``src.build_feed``
re-exports every name that src/, scripts/ or the tests read from it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, cast

from ..feed_types import FeedItem
from ..feed.merge import _is_wl_disruption, member_guids
from ..utils.logging import sanitize_log_arg
from ..providers.wl_plausibility import incident_begin
from ..providers.wl_text import STOP_NOTICE_LEAD_RE
from ..utils.text import html_to_text

from .base import _PLANNED_DISRUPTION_RE, _parse_datetime, _parse_first_seen, _parse_state_time, _to_utc, log
from .state import _lookup_state
from .time_line import _wl_incident_start
from .title_words import _rendered_title

__all__ = [
    "_OCCURRENCE_GAP", "_NEW_OCCURRENCE_KEY", "_restart_recurring_occurrences", "_note_announced_starts",
    "_count_new_items", "_initial_first_seen", "_carry_item_identity", "_remember_item_identity",
]


# A Wiener-Linien message that was absent from the provider data for longer
# than this before its new validity start is a new occurrence — see
# :func:`_restart_recurring_occurrences`. Four update cycles: a message that
# drops out of one or two fetches is still the same occurrence.
_OCCURRENCE_GAP = timedelta(hours=2)
# A planned measure (:data:`_PLANNED_DISRUPTION_RE`) pauses longer: WL issues
# "66A: Busse halten Salvatorianerplatz" every night for 04:40 to 01:00, a
# night line's stop only for the night. Under the two hours above it counted
# as new every morning: since 2026-09-26 such a measure stood in front in
# 370 of 387 German feed versions, 441 slots (66A alone 286), although it
# had been in the feed since 28.08. Unplanned incidents keep the two hours.
_PLANNED_OCCURRENCE_GAP = timedelta(hours=36)
# Set by :func:`_restart_recurring_occurrences` on an entry whose message
# begins a new occurrence; :func:`_update_item_state` writes its statistics
# row the first time the occurrence is shown and removes the mark.
_NEW_OCCURRENCE_KEY = "new_occurrence"


def _restart_recurring_occurrences(
    items: Sequence[FeedItem], state: dict[str, dict[str, Any]], now: datetime
) -> int:
    """Give a recurring Wiener-Linien disruption its own ``first_seen``.

    The WL ``guid`` is category + topic + line set — no date. "94A:
    Verkehrsunfall" on 2026-09-25 therefore carried the guid of the accident
    on 2026-07-04 and inherited its ``first_seen``: the feed sorted a
    disruption that had started minutes earlier as 83 days old, behind every
    newer stop relocation, and it never reached the ``MAX_ITEMS`` slots
    (six live disruptions missing from the feed at once that afternoon).

    A WL ``pubDate`` is the start of the message's validity, and the provider
    drops messages before that start — this occurrence cannot have been seen
    earlier. An entry whose ``first_seen`` precedes the ``pubDate`` therefore
    stems from an earlier occurrence, *unless* the message was in the data
    right up to the new start: WL re-issues running measures ("Busse halten
    …") with a fresh validity window every day or few days, and those must
    keep their place ("man kennt die Meldung schon"). ``last_seen`` tells the
    two apart — stamped here on every build for every WL item with an entry:

    * last seen within ``_OCCURRENCE_GAP`` before the new start — within
      ``_PLANNED_OCCURRENCE_GAP`` for a planned measure, which WL re-issues
      after its nightly pause — → the same message continues,
      ``first_seen`` stays;
    * last seen earlier, or never (entries from before ``last_seen``
      existed) → a new occurrence, ``first_seen`` moves to the ``pubDate``.

    The entry is moved, not dropped: without an entry an item ranks as unseen
    in every build until it is emitted, which would rotate a batch of such
    items through the feed one ``MAX_ITEMS`` page at a time. Other providers
    are untouched — an ÖBB ``pubDate`` is the publication time of the latest
    update, not the start of an occurrence.

    Returns the number of entries moved.
    """
    now_utc = _to_utc(now)
    stamp = now_utc.isoformat()
    restarted = 0
    for it in items:
        if str(it.get("source") or "").strip().casefold() != "wiener linien":
            continue
        _, entry = _lookup_state(it, state)
        if entry is None:
            continue
        first_seen = _parse_first_seen(entry, None)
        pub = _parse_datetime(it.get("pubDate"))
        if first_seen is not None and isinstance(pub, datetime):
            pub_utc = _to_utc(pub)
            last_seen = _parse_state_time(entry, "last_seen")
            text = f"{it.get('title') or ''} {html_to_text(str(it.get('description') or ''))}"
            gap = _PLANNED_OCCURRENCE_GAP if _PLANNED_DISRUPTION_RE.search(text) else _OCCURRENCE_GAP
            if first_seen < pub_utc <= now_utc and (
                last_seen is None or last_seen < pub_utc - gap
            ):
                entry["first_seen"] = pub_utc.isoformat()
                # Counted in the statistics once it reaches the feed
                # (:func:`_update_item_state`), like a disruption never seen.
                entry[_NEW_OCCURRENCE_KEY] = True
                restarted += 1
        entry["last_seen"] = stamp
    if restarted:
        log.info(
            "%d wiederkehrende WL-Meldung(en) als neues Auftreten eingeordnet.",
            restarted,
        )
    return restarted


def _note_announced_starts(
    items: Sequence[FeedItem], state: dict[str, dict[str, Any]], now: datetime
) -> int:
    """Remember the start of every item seen before it begins.

    The feed sorts by ``first_seen``, and an announcement is seen long before
    it applies: "S1/S2/S3/S4/S7: S-Bahn-Stammstrecke (Phase 2)" since 10.06.
    for 07.09.2026, the ÖBB closure "Wien Hauptbahnhof ↔ Gramatneusiedl"
    since 08.07. for 03.10.2026. On its first day each counted as months old
    and stood behind every newer notice: the Stammstrecke closure held none
    of the ten slots on 07.09., the ÖBB closure stood on place 49 on 04.10.

    For every item with a state entry whose ``starts_at`` lies in the future
    this stores that start as ``announced_start`` (a postponed start
    overwrites it). :func:`_sort_moment` then counts the item as new from
    that start, behind the current incidents (:func:`_is_current_incident`).
    The start is taken only while it is still ahead, so a measure WL re-issues every night
    with a fresh validity ("66A: Busse halten Salvatorianerplatz", starts at
    04:40 each morning, seen from 04:40) never gets one and keeps its place
    (:data:`_PLANNED_OCCURRENCE_GAP`).

    Returns the number of entries written.
    """
    now_utc = _to_utc(now)
    noted = 0
    for it in items:
        start = _parse_datetime(it.get("starts_at"))
        if not isinstance(start, datetime) or _to_utc(start) <= now_utc:
            continue
        _, entry = _lookup_state(it, state)
        if entry is None:
            continue
        value = _to_utc(start).isoformat()
        if entry.get("announced_start") != value:
            entry["announced_start"] = value
            noted += 1
    return noted


def _count_new_items(
    items: Sequence[FeedItem],
    state: dict[str, dict[str, Any]],
) -> int:
    count = 0
    for it in items:
        if not isinstance(it, dict):
            continue  # type: ignore[unreachable]
        # The state is persisted under the guid-preferring key scheme
        # (:func:`_state_key_for_item`, with a legacy-identity fallback) used
        # by both the writer and the age-out. Comparing the raw content
        # identity (:func:`_identity_for_item`) against guid-keyed state
        # counted every guid-bearing item as "new" on every run — use the
        # same lookup so an already-tracked item is recognised.
        _key, entry = _lookup_state(it, state)
        if entry is None:
            count += 1
    return count


def _initial_first_seen(item: FeedItem, now_utc: datetime) -> datetime:
    """The ``first_seen`` an item gets when the build sees it for the first time.

    Normally ``now``: the item has just appeared. A Wiener-Linien stop
    relocation or closure ("Haltestellenverlegung …", "Haltestellenauflassung
    …") starts from when WL published it (``pubDate``, WL's ``time.start``)
    instead. Since 2026-10-04 every such notice reaches the feed (operator
    decision "Alle aufnehmen"); before, the fetch dropped 24 of 37 (Update run
    of 2026-10-04 14:00 UTC).
    Counted as new, those 24 — some running since 2023 — would all have led
    the feed at once and filled its ten places. Each takes the place it
    would have had if the fetch had never dropped it: a notice published on
    28.09. for 05.10. ("26E/N20: Fultonstraße") sorts as seen on 28.09., and
    :func:`_defer_upcoming_items` still holds it back until the day before.
    A notice WL publishes from now on is new as before.
    """
    if item.get("source") != "Wiener Linien" or str(item.get("category") or "").casefold() != "hinweis":
        return now_utc
    published = _parse_datetime(item.get("pubDate"))
    if not isinstance(published, datetime):
        return now_utc
    published_utc = _to_utc(published)
    if published_utc >= now_utc:
        return now_utc
    if not STOP_NOTICE_LEAD_RE.match(html_to_text(str(item.get("description") or ""))[:200]):
        return now_utc
    return published_utc


# --- One message, one GUID while it runs (2026-10-06) ------------------------
#
# A merged item carries the GUID and the ``pubDate`` of its messages: the
# GUID of the earliest one or of the long message that says more
# (``_incident_entry``), of the survivor of ``deduplicate_fuzzy``; the
# earliest ``pubDate`` (``_span_group``). When the message whose GUID or
# time the item carries leaves the data before the others, the item takes
# the next one's, and the feed shows the same disruption as a new item under
# a new time. "43: Verkehrsunfall" on 2026-10-06: its display tickers (the
# first one from 00:03:28 Vienna time) ran out at 01:03, the long message
# (start 00:04) stood alone, and the rebuild at 01:14, without new data,
# showed it under the long message's GUID and "[Seit 00:04]" instead of
# "[Seit 00:03]". At 11:30 "1: Verkehrsunfall" lost its long message (start
# 10:40, resolved) and read "[Seit 10:43]" under the GUID of a ticker. On
# 05.10. at 01:30 "41/42: Verkehrsunfall" changed its GUID when WL replaced
# the incident by one follow-up per line, at 19:01 "9: Falschparker" when
# the 42 of "9/42" ended.
#
# So every build notes in the state entry of each item the messages it
# stands for (``members``: their GUIDs and WL's incident numbers, see
# ``member_guids``) and when (``members_seen``); a Wiener-Linien disruption
# also the earliest ``pubDate`` it carried (``earliest_published``). An item
# that was no item of a build within _OCCURRENCE_GAP under its own GUID
# takes the GUID of the item that shared one of its messages there, if it
# continues that item (``_continued_by``); an item continued so keeps that
# earliest ``pubDate``, the begin its time line showed. Nothing changes for
# an item whose messages stay; the first build after the change writes the
# notes and changes no GUID. An entry of several incidents counts the
# messages of the newest one only (``_merged_run``): a new incident still
# opens the feed under its own GUID, and the entry falls back to the older
# one's when it ends.


def _noted_recently(entry: dict[str, Any] | None, now_utc: datetime) -> datetime | None:
    """When a build within _OCCURRENCE_GAP last noted *entry*'s messages, if one did."""
    seen = _parse_state_time(entry, "members_seen")
    if seen is None or seen < now_utc - _OCCURRENCE_GAP:
        return None
    return seen


def _continues(entry: dict[str, Any] | None, item: FeedItem, now_utc: datetime) -> bool:
    """Whether *item* continues the item *entry* noted.

    It does if a build within _OCCURRENCE_GAP noted the entry and *item*'s
    message was published by then: WL gives a recurring disruption the GUID
    of the last one ("43: Verkehrsunfall"), and a new one keeps its own time.
    """
    seen = _noted_recently(entry, now_utc)
    published = _parse_datetime(item.get("pubDate"))
    return (
        seen is not None
        and isinstance(published, datetime)
        and _to_utc(published) <= seen
        and not _other_incident(entry, item)
    )


def _other_incident(entry: dict[str, Any] | None, item: FeedItem) -> bool:
    """Whether WL numbers *item* and the item *entry* noted as different incidents.

    Both carry incident numbers and share none: WL closed one disruption
    and opened another for the same lines. "13A/14A: Falschparker" on
    2026-10-06: ``I20261006-0030`` (start 17:22) ended, ``I20261006-0033``
    (start 17:44) took its GUID and showed "[Seit 17:22]". The new one
    keeps its own begin. Without a number on either side (display tickers)
    nothing says so.
    """
    members = entry.get("members") if isinstance(entry, dict) else None
    if not isinstance(members, list):
        return False
    noted = {m for m in members if isinstance(m, str) and m.startswith("wl:")}
    own = {m for m in member_guids(item) if m.startswith("wl:")}
    return bool(noted) and bool(own) and noted.isdisjoint(own)


def _continued_by(entry: dict[str, Any], item: FeedItem, own: dict[str, Any] | None = None) -> bool:
    """Whether *item*, which shares a message with the item *entry* noted, continues it.

    Never if *item* stood in the feed under its own GUID before that item
    did (*own*, its state entry: noted by a build since its ``first_seen``,
    which is older than the other's): it was there first, and the other
    took it up. The stops of the line 25 track works (in the feed since
    23.09.) became members of "25: Verspätungen" on 2026-10-09 at 09:36,
    before ``_with_unowned`` kept them out; when the delay ended they
    carried its GUID and begin and stood as a new disruption on up to
    place 2, and every later build carried them on again. An entry no
    build noted in this occurrence does not count: its ``first_seen`` may
    be the message's own start (``_restart_recurring_occurrences``), and
    WL dates a long message back ("60: Schadhafter PKW" on 2026-10-01,
    dated 18:48, came after the tickers of 18:50 and carries their GUID).

    It does if the message whose GUID *item* carries was in that item: its
    other messages ran out ("43: Verkehrsunfall"). Or if that message is no
    newer than the begin the item showed: WL enters the long message after
    its tickers and dates it back (``I20261006-0012-F01``, "1:
    Verkehrsunfall", created 10:54 with start 10:40, tickers from 10:43).
    A newer message is a new incident that took up a running one ("52:
    Rettungseinsatz" on 2026-10-05 with a ticker of 30.09.): it keeps its
    own GUID and opens the feed. So does one WL numbers as another incident
    (``_other_incident``).
    """
    if _other_incident(entry, item):
        return False
    first, owner_first = _parse_state_time(own, "first_seen"), _parse_state_time(entry, "first_seen")
    noted = _parse_state_time(own, "members_seen")
    if first is not None and owner_first is not None and noted is not None and first < owner_first and noted >= first:
        return False
    members = entry.get("members")
    if isinstance(members, list) and str(item.get("guid") or "") in members:
        return True
    begin = _parse_state_time(entry, "earliest_published")
    published = _parse_datetime(item.get("pubDate"))
    return begin is not None and isinstance(published, datetime) and _to_utc(published) <= begin


def _predecessors(state: dict[str, dict[str, Any]], now_utc: datetime) -> dict[str, list[str]]:
    """Message GUID → GUIDs of the items recent builds noted it in."""
    owners: dict[str, list[str]] = defaultdict(list)
    for guid, entry in state.items():
        members = entry.get("members") if isinstance(entry, dict) else None
        if not isinstance(members, list) or _noted_recently(entry, now_utc) is None:
            continue
        for member in members:
            if isinstance(member, str):
                owners[member].append(guid)
    return owners


def _carry_item_identity(
    items: list[FeedItem], state: dict[str, dict[str, Any]], now: datetime
) -> list[FeedItem]:
    """*items* with the GUID and begin they had while they run (see above)."""
    now_utc = _to_utc(now)
    owners = _predecessors(state, now_utc)
    taken = {str(item.get("guid")) for item in items if item.get("guid")}
    claims: list[tuple[int, int, str]] = []
    for index, item in enumerate(items):
        guid = str(item.get("guid") or "")
        if not guid or _noted_recently(state.get(guid), now_utc) is not None:
            continue
        shared: dict[str, int] = defaultdict(int)
        for member in member_guids(item):
            for owner in owners.get(member, ()):
                shared[owner] += 1
        claims.extend(
            (-count, index, owner)
            for owner, count in shared.items()
            if owner not in taken and _continued_by(state[owner], item, state.get(guid))
        )
    out = list(items)
    carried: set[int] = set()
    for _count, index, owner in sorted(claims):
        if index in carried or owner in taken:
            continue
        item = cast(FeedItem, dict(items[index]))
        item["_members"] = member_guids(item)  # its own message stays one of them
        log.info(
            "Meldung behält ihre GUID: %s (%s statt %s)",
            sanitize_log_arg(_rendered_title(item)),
            owner[:12],
            str(item.get("guid"))[:12],
        )
        item["guid"] = owner
        out[index] = item
        carried.add(index)
        taken.add(owner)
    for index, item in enumerate(out):
        if not _is_wl_disruption(item):
            continue
        entry = state.get(str(item.get("guid") or ""))
        earliest = incident_begin(_parse_state_time(entry, "earliest_published"), _wl_incident_start(item))
        published = _parse_datetime(item.get("pubDate"))
        if earliest is None or not isinstance(published, datetime) or not _continues(entry, item, now_utc):
            continue
        if earliest < _to_utc(published):
            moved = cast(FeedItem, dict(item))
            moved["pubDate"] = earliest
            out[index] = moved
    return out


def _remember_item_identity(
    items: Sequence[FeedItem], state: dict[str, dict[str, Any]], now: datetime
) -> None:
    """Note in each item's state entry what ``_carry_item_identity`` reads back.

    Only entries that exist: the build creates them for what it shows.
    """
    now_utc = _to_utc(now)
    for item in items:
        guid = str(item.get("guid") or "")
        entry = state.get(guid) if guid else None
        if entry is None:
            continue
        published = _parse_datetime(item.get("pubDate"))
        if _is_wl_disruption(item) and isinstance(published, datetime):
            earliest = _to_utc(published)
            known = _parse_state_time(entry, "earliest_published")
            if known is not None and known < earliest and _continues(entry, item, now_utc):
                earliest = known
            entry["earliest_published"] = earliest.isoformat()
        entry["members"] = member_guids(item)
        entry["members_seen"] = now_utc.isoformat()
