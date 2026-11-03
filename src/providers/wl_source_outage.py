"""The WL response lists and a partial outage of the source.

Moved verbatim from ``src/providers/wl_fetch.py`` (700-line rule,
2026-11-02). ``_fallback_parts`` lives only here. The logger keeps the
name ``src.providers.wl_fetch``.
"""

from __future__ import annotations

import logging
from typing import Any

from ..utils import raw_capture, source_shape


log = logging.getLogger("src.providers.wl_fetch")


def _extract_wl_items(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Extract a list-of-dicts collection from a WL API response.

    Zero Trust: ``_get_json`` validates the top-level shape is a dict, but
    the ``data["data"]`` value and the inner ``data["data"][key]`` value
    extracted from it are still ``Any``. The previous one-liner
    ``(data.get("data", {}) or {}).get(key, []) or []`` collapses *falsy*
    JSON shapes (``None``, ``0``, ``""``, ``[]``, ``{}``) to a safe empty
    container, but lets *truthy non-Mapping* / *truthy non-list* shapes
    through. A misbehaving / compromised upstream peer (or a tampered
    proxy response) could ship ``{"data": [1, 2]}``, ``{"data": "abc"}``,
    ``{"data": True}``, or ``{"data": {"trafficInfos": "abc"}}``; the
    resulting ``.get(key, [])`` then raises ``AttributeError`` (lists,
    strings, bools have no ``.get``) or the iteration in ``fetch_events``
    raises ``AttributeError`` / ``TypeError`` on each non-dict element.
    Both failure modes propagate out of ``fetch_events`` and disable the
    cache refresh entirely (``update_wl_cache.py`` falls into the
    ``except Exception:`` defensive branch). Mirror the
    ``isinstance(payload, list)`` shape guard already landed for the
    sibling Baustellen / VOR loaders so the documented empty-list
    fallback runs instead, and additionally drop non-dict elements so
    downstream ``.get(...)`` calls in ``fetch_events`` are safe.
    """
    inner = data.get("data")
    if not isinstance(inner, dict):
        return []
    items = inner.get(key)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _raw_snapshot(data: dict[str, Any], key: str) -> dict[str, Any]:
    """The WL response as kept in ``data/raw/wl/`` (``src/utils/raw_capture.py``).

    Everything WL sent, minus the ``serverTime`` that changes on every
    call, with the item list in a stable order.
    """
    snapshot = dict(data)
    message = snapshot.get("message")
    if isinstance(message, dict):
        snapshot["message"] = {k: v for k, v in message.items() if k != "serverTime"}
    inner = snapshot.get("data")
    if isinstance(inner, dict) and isinstance(inner.get(key), list):
        snapshot["data"] = {
            **inner,
            key: raw_capture.sorted_records(inner[key], "name", "title"),
        }
    return snapshot


# ---------------- Ausfall eines Teils der Quelle ----------------
#
# WL liefert in zwei Listen: ``trafficInfoList`` (Störungen) und
# ``newsList`` (Hinweise). Fiel eine davon aus, ging der Abruf früher mit
# der anderen allein durch: Am echten Stand vom 2026-10-04 16:01 UTC
# verschwanden ohne Störungsliste alle fünf laufenden Störungen aus den
# zehn Plätzen des deutschen Feeds, ohne Hinweisliste vier Hinweise — mit
# Exit-Code 0, also ohne Warnung und mit grünem Health check. Dasselbe bei
# einer Antwort, der ein Feld fehlt (``src/utils/source_shape.py``).
#
# Jetzt gilt eine Liste nur, wenn sie da ist und ihre Felder trägt, und eine
# leere nur, wenn auch die letzte gute Antwort (fast) leer war. Sonst
# kommt sie aus ihrer letzten guten Antwort (``data/raw/wl/``), die andere
# bleibt frisch; der Abruf meldet das (``fallback_parts``, Exit-Code 3 im
# Cache-Updater). Fehlt auch die letzte gute Antwort oder fallen beide
# Listen aus, scheitert der Abruf, und der Cache bleibt wie er ist — wie
# bisher bei einem vollständigen Ausfall.

_WL_REQUIRED = {
    "title": (source_shape.has("title"), source_shape.MIN_RECORDS),
    "description": (source_shape.has("description"), source_shape.MIN_RECORDS),
    "time": (source_shape.has("time"), source_shape.MIN_RECORDS),
    # Nicht jede Störung nennt eine Linie (69 von 80 am 2026-10-04).
    "relatedLines": (source_shape.has("relatedLines"), 10),
}

_fallback_parts: list[str] = []


class SourceIncompleteError(RuntimeError):
    """A part of the WL source gave no usable answer and has no last good one."""


def fallback_parts() -> list[str]:
    """The WL lists the last ``fetch_events`` took from their last good answer."""
    return list(_fallback_parts)


def _usable_items(data: Any, key: str, name: str) -> list[dict[str, Any]] | None:
    """The items of a WL answer, or ``None`` when the answer is not usable."""
    if not isinstance(data, dict):
        return None
    inner = data.get("data")
    if not isinstance(inner, dict) or not isinstance(inner.get(key), list):
        return None
    items = _extract_wl_items(data, key)
    missing = source_shape.missing_fields(items, _WL_REQUIRED)
    if missing:
        log.warning(
            "WL %s: Feld(er) %s fehlen in allen %d Einträgen – Antwort unbrauchbar.",
            name,
            ", ".join(missing),
            len(items),
        )
        return None
    return items


def _list_items(name: str, key: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """The items of list *name*: fresh when usable, else from its last good answer.

    An empty list counts as no answer when the last good one held at least
    ``source_shape.MIN_RECORDS`` items: both lists carry long-running
    notices and were never empty, so an empty one is a server in trouble.
    """
    items = _usable_items(data, key, name)
    if items:
        raw_capture.write_snapshot("wl", name, _raw_snapshot(data, key))
        return items
    last = _usable_items(raw_capture.read_snapshot("wl", name), key, name)
    if items is not None and (last is None or len(last) < source_shape.MIN_RECORDS):
        raw_capture.write_snapshot("wl", name, _raw_snapshot(data, key))
        return items
    if last is None:
        raise SourceIncompleteError(f"WL {name}: keine brauchbare Antwort")
    log.warning(
        "WL %s: %s – letzte gute Antwort (%d Einträge) verwendet.",
        name,
        "leere Liste" if items is not None else "keine brauchbare Antwort",
        len(last),
    )
    _fallback_parts.append(name)
    return last
