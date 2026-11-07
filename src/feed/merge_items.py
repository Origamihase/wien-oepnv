"""Item-level rules for :mod:`src.feed.merge`: who may merge, dates, validity, members.

Helper-to-helper calls resolve here; patch this module to reach them.
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from ..providers.wl_text import is_stop_notice


def _is_wl_disruption(item: Mapping[str, Any]) -> bool:
    """A Wiener-Linien disruption (``Störung``), not a notice (``Hinweis``)."""
    return (
        str(item.get("source") or "").strip().casefold() == "wiener linien"
        and str(item.get("category") or "").strip().casefold() == "störung"
    )


def _is_wl_stop_notice(item: Mapping[str, Any]) -> bool:
    """A Wiener-Linien stop relocation or closure ("Haltestellenverlegung …")."""
    return (
        str(item.get("source") or "").strip().casefold() == "wiener linien"
        and str(item.get("category") or "").strip().casefold() == "hinweis"
        and is_stop_notice(str(item.get("description") or ""))
    )


def _may_merge(
    line_overlap: float,
    existing: dict[str, Any],
    item: dict[str, Any],
    announced: Callable[[Mapping[str, Any]], bool] | None = None,
) -> bool:
    """Whether two items with *line_overlap* may merge here at all.

    Two Wiener-Linien disruptions of the same lines never do:
    ``_merge_wl_ticker_clusters`` in ``build_feed`` joins them, cause in the
    title and every consequence in the description. Joined here first, the
    WL merge never saw them and the two titles stood side by side —
    ``64A: Verkehrsunfall & Verkehrsunfall``, ``60: Schadhafter Pkw &
    Schadhafter Pkw Betrieb ab Anschützgasse`` (both 2026-10-01; 21 such
    titles since 2026-09-12, every one of a single line). Disruptions of
    overlapping but different lines still merge here (``40/41: Betrieb ab
    Gersthof`` with ``40: Falschparker Betrieb ab Gersthof``): the WL merge
    only joins equal line sets.

    What is only announced is not the same event as what runs (2026-11-07).
    *announced* says whether an item begins after the preview day — the day
    from which ``_defer_upcoming_items`` lets an announcement take its
    normal place — and an announced item merges only with another announced
    one. Without the rule the live disruption "7A: Bauarbeiten" (09.10.
    13:16, "Unregelmäßige Intervalle") went up in the notice "7A:
    Bauarbeiten" of the diversion from 14.10., and the merged item showed
    the diversion five days early; "18: Fahrtbehinderung Veranstaltung"
    (26.10. 17:07) merged with "18: Veranstaltung am 08.11.26" into the
    title "18: Veranstaltung am 08.11.26 & Fahrtbehinderung Veranstaltung",
    a notice; and the relocation "5A/5B/N31: Obere Augartenstraße" (until
    01.11.) took the one of 07.–09.11. and stood from 01.11. to 06.11.,
    when neither applied. The caller without *announced* (the tests) keeps
    the old behaviour.

    A stop relocation or closure never merges with a disruption (2026-11-07).
    Its title is the stop's name, and a disruption names the same stop as
    the place of its consequence: "12: Feuerwehreinsatz Betrieb ab
    Albertgasse" (30.10. 10:41) went up in the relocation "5/12: Albertgasse"
    (13.10.–03.11.), and the fire brigade operation left the ten places.
    Two stop notices of one stop still merge, as do a stop notice and a works
    notice of the same street.
    """
    if line_overlap <= 0.3:
        return False
    if line_overlap >= 1.0 and _is_wl_disruption(existing) and _is_wl_disruption(item):
        return False
    if announced is not None and announced(existing) != announced(item):
        return False
    if _is_wl_stop_notice(existing) and _is_wl_disruption(item):
        return False
    return not (_is_wl_stop_notice(item) and _is_wl_disruption(existing))


def _calculate_line_overlap(lines1: set[str], lines2: set[str]) -> float:
    if not lines1 or not lines2:
        return 0.0
    intersection = len(lines1 & lines2)
    union = len(lines1 | lines2)
    return intersection / union


def _promote_newer_dates(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Copy any date field from *source* into *target* when it is newer.

    The dedup loop tolerates four spellings of the publication date for
    historic compatibility (``pubDate`` / ``pubdate`` / ``pub_date`` /
    ``updated``); the VOR↔ÖBB merge branches must use the same set so an
    incoming item with a newer ÖBB report bumps the merged item forward
    rather than keeping the older VOR timestamp.
    """
    for date_key in ("pubDate", "pubdate", "pub_date", "updated"):
        target_date = target.get(date_key)
        source_date = source.get(date_key)
        if target_date and source_date:
            try:
                if source_date > target_date:
                    target[date_key] = source_date
            except TypeError:
                # Mixed datetime / str types — leave the target unchanged.
                pass
        elif source_date and not target_date:
            target[date_key] = source_date


def member_guids(item: Mapping[str, Any]) -> list[str]:
    """The GUIDs of the source messages *item* stands for, and WL incident numbers, sorted.

    A merge writes them to ``_members``; an item no merge touched stands
    for itself. ``feed_build.item_history._carry_item_identity`` follows an item from
    build to build by them, so its GUID does not change when the message
    whose GUID it carries leaves the data before the others.
    """
    members = item.get("_members")
    if isinstance(members, (list, tuple)) and members:
        return sorted({str(member) for member in members})
    guid = item.get("guid")
    own = {str(guid)} if guid else set()
    numbers = item.get("_wl_ids")
    if isinstance(numbers, (list, tuple)):
        # WL's incident numbers, which outlast the GUIDs of its messages
        # (``wl_fetch._incident_ids``).
        own.update(f"wl:{number}" for number in numbers if isinstance(number, str) and number)
    return sorted(own)


def _merge_validity(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Give *target* the validity of both items: earliest start, latest end.

    The merged item stands for both reports, so it must stay in the feed as
    long as either is valid. Keeping only the survivor's span dropped the
    other one's: a WL notice ending 02.10. absorbed the disruption of the
    same works ending 07.11. and took it out of the feed on 02.10.
    An open end of either keeps the merged item open, as in
    ``feed_build.wl_ticker_text._span_group``. Values that are not ``datetime`` are left
    alone; a naive/aware mix cannot be ordered and keeps the survivor's.
    """
    starts = [s for s in (target.get("starts_at"), source.get("starts_at")) if isinstance(s, datetime)]
    ends = [target.get("ends_at"), source.get("ends_at")]
    known_ends = [e for e in ends if isinstance(e, datetime)]
    try:
        start = min(starts) if starts else target.get("starts_at")
        end = max(known_ends) if len(known_ends) == 2 else target.get("ends_at")
    except TypeError:
        # Naive and aware datetimes cannot be ordered: keep the survivor's span.
        return
    if known_ends and None in ends:
        end = None
    target["starts_at"] = start
    target["ends_at"] = end
