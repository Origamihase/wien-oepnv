"""AT coordinate reconciliation (WL -> HAFAS) of ``scripts/update_wl_stations.py``.

Moved verbatim from the script. HAFAS stays lazy: ``_build_hafas_lookup``
resolves it through ``base._load_enrich_station_with_hafas``.
A test that patches a constant or function used here must patch it
on this module, not on the script.
"""
from __future__ import annotations

from typing import Any
from collections.abc import Callable, Iterable, Mapping

from src.wl_stations.base import (
    _load_enrich_station_with_hafas,
    log,
    resolve_at_coordinate,
    sanitize_log_arg,
)
from src.wl_stations.merge import _merge_sources
from src.wl_stations.ogd_csv import _normalize_key


def _wl_coord_index(
    wl_entries: Iterable[Mapping[str, object]],
) -> dict[str, tuple[float, float]]:
    """Map ``wl_diva`` → authoritative WL ``(lat, lon)`` from the payloads.

    The first DIVA wins (payloads are already de-duplicated upstream).
    Non-finite / out-of-range coordinates are dropped so the consensus
    resolver never receives an invalid WL anchor.
    """

    index: dict[str, tuple[float, float]] = {}
    for payload in wl_entries:
        diva = str(payload.get("wl_diva") or "").strip()
        if not diva or diva in index:
            continue
        lat = payload.get("latitude")
        lon = payload.get("longitude")
        if (
            isinstance(lat, int | float)
            and isinstance(lon, int | float)
            and -90.0 <= lat <= 90.0
            and -180.0 <= lon <= 180.0
        ):
            index[diva] = (float(lat), float(lon))
    return index


def _coord_from_hafas_hit(hit: object) -> tuple[float, float] | None:
    if not isinstance(hit, Mapping):
        return None
    lat = hit.get("lat")
    lon = hit.get("lon")
    if isinstance(lat, int | float) and isinstance(lon, int | float):
        return (float(lat), float(lon))
    return None


def _build_hafas_lookup() -> Callable[[str], tuple[float, float] | None]:
    """Return a name → ``(lat, lon)`` HAFAS lookup backed by ÖBB Scotty.

    Results are cached per name and every failure degrades to ``None`` so a
    HAFAS outage can never shift a coordinate or crash the WL merge.
    """

    enrich = _load_enrich_station_with_hafas()
    cache: dict[str, tuple[float, float] | None] = {}

    def lookup(name: str) -> tuple[float, float] | None:
        if name in cache:
            return cache[name]
        try:
            hit = enrich(name)
        except Exception:  # nosec B902 - HAFAS must never crash the merge
            log.warning(
                "HAFAS lookup raised during coordinate reconciliation for %s",
                sanitize_log_arg(name),
            )
            hit = None
        coord = _coord_from_hafas_hit(hit)
        cache[name] = coord
        return coord

    return lookup


def _apply_coordinate_decision(entry: dict[str, object], decision: Any) -> None:
    """Write a :class:`CoordinateDecision` onto a merged station entry.

    Coordinates are rounded to 6 decimals to match the WL aggregation
    precision and keep the committed ``stations.json`` diff stable, and the
    decision's provider tokens are folded into the ``source`` field.
    """

    entry["latitude"] = round(float(decision.latitude), 6)
    entry["longitude"] = round(float(decision.longitude), 6)
    entry["source"] = _merge_sources(entry.get("source"), *decision.sources)


def _reconcile_at_overlap(
    entries: list[dict[str, object]],
    wl_coord_by_diva: Mapping[str, tuple[float, float]],
    *,
    hafas_lookup: Callable[[str], tuple[float, float] | None],
    osm_index_loader: Callable[[], Mapping[str, tuple[float, float]]] | None = None,
    agree_tolerance_m: float = 150.0,
    osm_sanity_radius_m: float = 500.0,
) -> None:
    """Apply the ``WL → HAFAS → OSM`` coordinate priority to overlap entries.

    Only entries carrying BOTH a ``wl_diva`` with a known WL coordinate and
    a HAFAS identity (``hafas_extId`` / ``eva_nr``) are reconciled — the
    multimodal hubs where a Wiener-Linien stop and an ÖBB station were
    unified. WL is authoritative; HAFAS is cross-checked; OSM (fetched
    lazily, only when a disagreement exists) arbitrates by endorsing the
    candidate closer to it — only when a caller passes *osm_index_loader*;
    ``main()`` passes none since 2026-10-17, so a disagreement keeps WL.
    A missing HAFAS result is a deliberate no-op:
    a transient outage must never shift a coordinate. An unresolvable
    conflict keeps WL (highest priority) and is logged for review.
    """

    pending_osm: list[
        tuple[dict[str, object], tuple[float, float], tuple[float, float]]
    ] = []
    agreed = 0
    skipped_no_hafas = 0

    for entry in entries:
        diva = str(entry.get("wl_diva") or "").strip()
        if not diva:
            continue
        wl = wl_coord_by_diva.get(diva)
        if wl is None:
            continue
        if not (entry.get("hafas_extId") or entry.get("eva_nr")):
            # WL-only stop: no second Austrian source to reconcile against.
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        hafas = hafas_lookup(name.strip())
        if hafas is None:
            skipped_no_hafas += 1
            continue
        decision = resolve_at_coordinate(
            wl=wl, hafas=hafas, agree_tolerance_m=agree_tolerance_m
        )
        if decision.decision == "wl_hafas_agree":
            _apply_coordinate_decision(entry, decision)
            agreed += 1
        else:
            # WL and HAFAS disagree → defer until OSM (the arbiter) is loaded.
            pending_osm.append((entry, wl, hafas))

    osm_index: Mapping[str, tuple[float, float]] = {}
    if pending_osm and osm_index_loader is not None:
        osm_index = osm_index_loader()

    picked_wl = picked_hafas = unresolved = 0
    for entry, wl, hafas in pending_osm:
        name = str(entry.get("name") or "")
        osm = osm_index.get(_normalize_key(name))
        decision = resolve_at_coordinate(
            wl=wl,
            hafas=hafas,
            osm=osm,
            agree_tolerance_m=agree_tolerance_m,
            osm_sanity_radius_m=osm_sanity_radius_m,
        )
        _apply_coordinate_decision(entry, decision)
        if decision.decision == "osm_picked_hafas":
            picked_hafas += 1
            log.warning(
                "WL/HAFAS coordinate conflict for %s: OSM endorsed HAFAS",
                sanitize_log_arg(name),
            )
        elif decision.decision == "osm_picked_wl":
            picked_wl += 1
            log.warning(
                "WL/HAFAS coordinate conflict for %s: OSM endorsed WL",
                sanitize_log_arg(name),
            )
        else:
            unresolved += 1
            log.warning(
                "WL/HAFAS coordinate conflict for %s unresolved (OSM "
                "unavailable or implausible); kept WL",
                sanitize_log_arg(name),
            )

    if agreed or pending_osm or skipped_no_hafas:
        log.info(
            "AT coordinate reconciliation: %d agreed, %d OSM->WL, %d OSM->HAFAS, "
            "%d unresolved, %d not cross-checkable",
            agreed,
            picked_wl,
            picked_hafas,
            unresolved,
            skipped_no_hafas,
        )
