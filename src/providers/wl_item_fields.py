"""Reading the fields of a WL item: timestamps, status and validity.

Moved verbatim from ``src/providers/wl_fetch.py`` (700-line rule,
2026-11-02). The logger keeps the name ``src.providers.wl_fetch``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, UTC
from typing import Any, cast

from dateutil import parser as dtparser

from ..utils.logging import sanitize_log_arg
from ..utils.vienna_clock import vienna_wall_clock
from ..feed.config import ENDS_AT_GRACE_MINUTES

from .wl_plausibility import (
    note_corrections,
    plausible_end,
    plausible_start,
)
from . import wl_resolved


log = logging.getLogger("src.providers.wl_fetch")


# ---------------- Zeit & Utils ----------------

def _iso(s: object) -> datetime | None:
    """Parst ISO (inkl. 'Z' / TZ ohne Doppelpunkt) robust zu aware datetime.

    Accepts ``object`` rather than ``str | None`` because every call site
    passes a raw ``dict.get(...)`` value (typed ``Any``) straight from the
    upstream JSON — see ``_best_ts`` (``time.start`` / ``updated`` / …) and
    the two item loops in ``fetch_events``. A *truthy non-string* value (a
    numeric epoch like ``1700000000``, a list, a dict) would otherwise reach
    ``s.replace(...)`` below and raise ``AttributeError`` — which the
    ``(ValueError, OverflowError)`` guard around ``isoparse`` does NOT catch.
    That exception then propagates out of the unguarded ``fetch_events`` item
    loop and disables the entire WL cache refresh for the cycle
    (``update_wl_cache.py`` swallows it via a broad ``except Exception``).
    Type-guarding here upholds the documented "one bad timestamp must not
    abort the fetch" contract below.
    """

    if not isinstance(s, str) or not s:
        return None
    s = s.replace("Z", "+00:00")
    if len(s) >= 5 and (s[-5] in "+-") and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    try:
        dt = dtparser.isoparse(s)
    except (ValueError, OverflowError) as exc:
        # Fail soft: a single malformed upstream timestamp must NOT
        # propagate out of fetch_events. The fetch_events item loops
        # have no per-item guard, so an unhandled ValueError aborts the
        # whole fetch; update_wl_cache.py then swallows it via its broad
        # ``except Exception`` and silently keeps the stale cache — one
        # bad date field disables the entire WL refresh for that cycle.
        # Mirrors the defensive parsing in the sibling timestamp parsers
        # ``oebb._parse_dt_rfc2822`` and ``wl_text.extract_date_from_title``.
        # ``isoparse``'s ValueError embeds a fragment of the upstream
        # input, so the bound name is sanitised before logging.
        log.debug(
            "WL-Zeitstempel nicht parsebar (%s) – ignoriere Feld.",
            sanitize_log_arg(str(exc)),
        )
        return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=UTC)
    # In winter time WL gives summer times the winter offset (20.03.2026:
    # "2027-06-30T11:11:00+01:00"); the wall clock is what it meant.
    return cast('datetime | None', vienna_wall_clock(dt))


def _coerce_dict(value: Any) -> dict[str, Any]:
    """Return ``value`` if it is a dict, otherwise an empty dict.

    Zero Trust: ``_extract_wl_items`` filters non-dict elements at the list
    boundary, but per-item lookups like ``ti.get("attributes")`` and
    ``ti.get("time")`` still return ``Any``. The previous ``... or {}`` shape
    only collapses *falsy* values (``None``, ``0``, ``""``); a misbehaving /
    compromised upstream peer could ship a truthy non-dict (``[1, 2]``,
    ``"abc"``, ``42``, ``True``) which then raises ``AttributeError`` on the
    very next ``.get(...)`` and propagates out of ``fetch_events``, silently
    disabling the WL cache refresh (``update_wl_cache.py`` swallows it via
    ``except Exception:``). Same drift shape as the documented ``or [] /
    or {}`` rounds for the outer collection.
    """
    return value if isinstance(value, dict) else {}


def _best_ts(obj: dict[str, Any]) -> datetime | None:
    """The start of a WL item: ``time.start``, else when WL published it.

    ``time.end`` is no candidate. As the start of an item without
    ``time.start`` it made ``_is_active`` see a disruption that had not
    begun yet, and the item stayed out of the feed until it ended
    (``time.end`` 31.12. → dropped until 31.12.). Without any of these the
    start stays open and the time line reads "Bis …".
    """
    t = _coerce_dict(obj.get("time"))
    attrs = _coerce_dict(obj.get("attributes"))
    for cand in (
        _iso(t.get("start")),
        _iso(obj.get("updated")),
        _iso(obj.get("timestamp")),
        _iso(attrs.get("lastUpdate")),
        _iso(attrs.get("created")),
    ):
        if cand:
            return cand
    return None


# Status values (``status`` / ``state`` on the POI or its ``attributes``)
# that mark a disruption finished / inactive — such items are dropped.
# Matched on WORD BOUNDARIES, not bare substrings: the German *active*
# statuses ``laufende`` (ongoing) and ``ausstehende`` (pending) — and the
# noun ``Wochenende`` (weekend) — all contain the substring ``ende`` and were
# wrongly dropped by the prior ``"ende" in blob`` membership test, silently
# discarding valid live disruptions. All keywords are ASCII, so ``\b`` is safe.
# ``resolved`` is what WL actually sends: a finished incident stays in
# ``trafficInfoList`` for one more fetch with that status, then disappears.
# Kept as active, it showed outdated text on the TV for up to 30 minutes
# (2026-10-05 11:01: "Die Linie U6 fährt derzeit nicht zwischen Westbahnhof
# und Längenfeldgasse" while the U6 already ran again; 12 cases from
# 2026-10-04 to 2026-10-05) and, merged with its active ``-F01`` follow-up,
# hid the follow-up's current text. Its display tickers carry no status;
# ``wl_resolved`` sends them after it.
_INACTIVE_STATUS_RE = re.compile(
    r"\b(?:finished|inactive|inaktiv|done|closed|nicht aktiv|ended|ende|"
    r"abgeschlossen|beendet|geschlossen|resolved)\b"
)


def _is_inactive_status(*values: object) -> bool:
    """True when any status/state *value* marks the item finished/inactive."""
    blob = " ".join(str(v or "") for v in values).lower()
    return _INACTIVE_STATUS_RE.search(blob) is not None


def _is_finished(info: Any) -> bool:
    """True when the WL message *info* is finished by its status fields."""
    info = _coerce_dict(info)
    attrs = _coerce_dict(info.get("attributes"))
    return _is_inactive_status(info.get("status"), attrs.get("status"), attrs.get("state"))


def _status_drop_reason(info: Any, stale: set[str]) -> str | None:
    """Why the traffic info *info* leaves at once, ``None`` when it stays.

    A finished message goes, and so do the display tickers of a finished
    incident (*stale*, see ``wl_resolved``), which WL sends without a status.
    """
    if _is_finished(info):
        return "Status inaktiv"
    if isinstance(info, dict) and wl_resolved.ticker_key(info) in stale:
        return "Kurzmeldung einer erledigten Störung"
    return None


def _is_active(start: datetime | None, end: datetime | None, now: datetime) -> bool:
    if start and start > now:
        return False
    if end and end < (now - timedelta(minutes=ENDS_AT_GRACE_MINUTES)):
        return False
    return True


def _effective_end(
    desc_raw: str, end: datetime | None, start: datetime | None
) -> datetime | None:
    """WL's ``time.end``, or the end its "Zeitraum:" names instead of an 11:11 one.

    See :func:`wl_plausibility.plausible_end`.
    """
    return plausible_end(desc_raw, end, start)


def _effective_start(
    title_raw: str,
    desc_raw: str,
    start: datetime | None,
    end: datetime | None,
    now: datetime,
) -> datetime | None:
    """When the measure of a WL item begins, for ``starts_at``.

    The sources of the item (``time.start``/``time.end``, the title date,
    the "Zeitraum:" section) are weighed by
    :func:`wl_plausibility.plausible_start`; every contradiction it settles
    is logged and collected for ``data/wl_plausibility_anomalies.json``.
    """
    effective, corrections = plausible_start(title_raw, desc_raw, start, end, now)
    note_corrections(title_raw, corrections)
    return effective


def _intervals_overlap(
    start_a: datetime | None,
    end_a: datetime | None,
    start_b: datetime | None,
    end_b: datetime | None,
) -> bool:
    """Return True if the time intervals [start_a, end_a] and [start_b, end_b] overlap."""

    s_a = start_a or datetime.min.replace(tzinfo=UTC)
    s_b = start_b or datetime.min.replace(tzinfo=UTC)
    e_a = end_a or datetime.max.replace(tzinfo=UTC)
    e_b = end_b or datetime.max.replace(tzinfo=UTC)

    return s_a <= e_b and s_b <= e_a

def _as_list(val: Any) -> list[Any]:
    if val is None:
        return []
    return list(val) if isinstance(val, list | tuple | set) else [val]
