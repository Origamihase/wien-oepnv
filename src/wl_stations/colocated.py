"""Co-located duplicate merge and name contamination of ``scripts/update_wl_stations.py``.

Moved verbatim from the script.
A test that patches a constant or function used here must patch it
on this module, not on the script.
"""
from __future__ import annotations

import re
import sys

from src.wl_stations.base import _project_root, is_in_vienna, log
from src.wl_stations.ogd_csv import _line_sort_key


_COLOCATED_MERGE_DISTANCE_M = 150.0


def _merge_colocated_duplicates(
    entries: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Merge entries that share a canonical name AND are within 150 m
    of each other.

    Wiener Linien's OGD-Echtzeit ``haltestellen.csv`` lists some
    physical stops twice: opposing-direction bahnsteige at the same
    intersection get distinct DIVAs despite sharing a name and lying
    a few dozen metres apart. Forensic analysis of the current CSV
    surfaces six such groups (Soldanellenweg 27 m, Bhf. Atzgersdorf
    57 m, Vorgartenstraße 71 m, Am Rosenhügel 88 m, Stock im Weg
    121 m, Lieblgasse 122 m). Without this pass each one shows up
    as two near-identical entries in ``data/stations.json``
    (disambiguated by DIVA suffix downstream — see
    ``_disambiguate_duplicate_names``).

    Merging rules (all additive, no schema break):

    * ``name``: unchanged base name (kept; the disambiguation pass
      that runs next is a no-op because the group collapses to one).
    * ``wl_diva``: the lexicographically lowest DIVA in the group
      (deterministic).
    * ``wl_stops``: union of ``wl_stops`` from every merged entry,
      sorted by ``stop_id``.
    * ``aliases``: union, sorted.
    * ``latitude`` / ``longitude``: arithmetic mean across the
      group's coordinates.
    * ``in_vienna`` / ``pendler``: re-derived from the new mean
      coordinates via the same polygon check
      ``build_wl_entries`` uses for single-entry groups
      (keeps the flag pair consistent with the persisted coords —
      same invariant pinned by ``test_coordinates_match_in_vienna_
      flag``).
    * Other identity-class fields (``bst_id``, ``bst_code``,
      ``vor_id`` if any picked up from the VOR mapping): primary's
      values win; absent on every WL-only entry by construction
      after PR #1446's redesign.

    Multi-modal stops at the same venue (150-500 m apart — e.g.
    tram + bus pair at one intersection) are NOT merged; they remain
    separate entries with the existing DIVA-suffix disambiguation,
    because operating-line-level disambiguation cannot be inferred
    from the OGD-Echtzeit columns alone.
    """
    from collections import defaultdict

    if not entries:
        return entries

    by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in entries:
        name = str(entry.get("name") or "")
        if name:
            by_name[name].append(entry)

    merged_total = 0
    out: list[dict[str, object]] = []
    consumed_ids: set[int] = set()
    for _name, group in by_name.items():
        if len(group) < 2:
            continue
        if all(id(e) not in consumed_ids for e in group):
            if _all_pairs_within(group, _COLOCATED_MERGE_DISTANCE_M):
                merged = _merge_entry_group(group)
                out.append(merged)
                merged_total += len(group) - 1
                for e in group:
                    consumed_ids.add(id(e))

    if not consumed_ids:
        return entries

    # Re-assemble: keep all entries that were NOT consumed by a merge,
    # plus the merged primaries we just produced. Preserve the original
    # sort order downstream by re-sorting at the end.
    for entry in entries:
        if id(entry) not in consumed_ids:
            out.append(entry)
    out.sort(key=lambda item: (str(item.get("name")), str(item.get("wl_diva"))))
    log.info(
        "Merged %d co-located WL haltestellen (≤%.0f m) into existing "
        "neighbour entries",
        merged_total,
        _COLOCATED_MERGE_DISTANCE_M,
    )
    return out


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres, :func:`src.utils.geo.haversine_m`.

    Imported lazily like the other ``src`` helpers of this module.
    """
    base_dir = _project_root()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    from src.utils.geo import haversine_m

    return haversine_m(lat1, lon1, lat2, lon2)


def _all_pairs_within(
    group: list[dict[str, object]], max_distance_m: float
) -> bool:
    """Return True when every pair of entries in *group* sits within
    *max_distance_m* of each other. Entries missing coordinates are
    treated as "cannot merge" — return False so the group is left to
    the downstream DIVA-suffix disambiguation.
    """
    coords: list[tuple[float, float]] = []
    for entry in group:
        lat = entry.get("latitude")
        lon = entry.get("longitude")
        if not isinstance(lat, int | float) or not isinstance(lon, int | float):
            return False
        coords.append((float(lat), float(lon)))

    for i, (lat_i, lon_i) in enumerate(coords):
        for lat_j, lon_j in coords[i + 1:]:
            if _haversine_m(lat_i, lon_i, lat_j, lon_j) >= max_distance_m:
                return False
    return True


def _drop_distant_name_contamination(
    entries: list[dict[str, object]], *, threshold_m: float = 2000.0
) -> int:
    """Strip aliases / wl_stops whose name is a *different* station located
    more than *threshold_m* away.

    A Wiener-Linien DIVA legitimately groups physically nearby stops, and a
    grouped stop's StopText sometimes equals a neighbouring station's name
    (interchanges, depots) — those resolve to a station a few hundred metres
    away and stay. But a genuinely mislabelled stop pollutes the alias index:
    e.g. a stop sitting *at Grinzing* carrying the name ``Karlsplatz`` (6.4 km
    away) made ``station_info("Karlsplatz")`` resolve to Grinzing. Only the
    > threshold cases are dropped; the sweep that motivated this guard found
    exactly one such case across the whole directory, with no false positives
    among the legitimate sub-2-km interchange names. Returns the number of
    alias/stop tokens dropped. Mutates *entries* in place.
    """
    from src.utils.stations import _normalize_token

    def _bare(name: object) -> str:
        text = re.sub(r"\s*\((?:WL|VOR)\)\s*$", "", str(name or ""))
        return _normalize_token(re.sub(r"^Wien\s+", "", text))

    # bare canonical-name token -> coordinates of the owning station(s)
    name_coords: dict[str, list[tuple[float, float]]] = {}
    for entry in entries:
        lat, lon = entry.get("latitude"), entry.get("longitude")
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            token = _bare(entry.get("name"))
            if token:
                name_coords.setdefault(token, []).append((float(lat), float(lon)))

    def _is_distant(label: object, own: str, elat: float, elon: float) -> bool:
        if not isinstance(label, str):
            return False
        token = _normalize_token(label)
        owners = name_coords.get(token)
        if not token or token == own or not owners:
            return False
        return all(
            _haversine_m(elat, elon, owner_lat, owner_lon) > threshold_m
            for owner_lat, owner_lon in owners
        )

    dropped = 0
    for entry in entries:
        lat, lon = entry.get("latitude"), entry.get("longitude")
        if not isinstance(lat, int | float) or not isinstance(lon, int | float):
            continue
        elat, elon, own = float(lat), float(lon), _bare(entry.get("name"))

        aliases = entry.get("aliases")
        if isinstance(aliases, list):
            kept = [a for a in aliases if not _is_distant(a, own, elat, elon)]
            if len(kept) != len(aliases):
                dropped += len(aliases) - len(kept)
                entry["aliases"] = kept

        stops = entry.get("wl_stops")
        if isinstance(stops, list):
            kept_stops = [
                s
                for s in stops
                if not (isinstance(s, dict) and _is_distant(s.get("name"), own, elat, elon))
            ]
            if len(kept_stops) != len(stops):
                dropped += len(stops) - len(kept_stops)
                entry["wl_stops"] = kept_stops

    return dropped


def _merge_entry_group(group: list[dict[str, object]]) -> dict[str, object]:
    """Fold a list of co-located duplicate entries into one.

    See ``_merge_colocated_duplicates`` for the merge-rule rationale.
    """
    # Pick the primary by lexicographically lowest wl_diva so the
    # output is deterministic across cron ticks.
    primary = min(
        group,
        key=lambda e: str(e.get("wl_diva") or ""),
    )
    extras = [e for e in group if e is not primary]

    merged: dict[str, object] = dict(primary)

    # Union wl_stops, dedup by stop_id, sort
    seen_stop_ids: set[str] = set()
    combined_stops: list[dict[str, object]] = []
    for entry in (primary, *extras):
        stops = entry.get("wl_stops")
        if not isinstance(stops, list):
            continue
        for stop in stops:
            if not isinstance(stop, dict):
                continue
            sid = str(stop.get("stop_id") or "")
            if sid and sid not in seen_stop_ids:
                seen_stop_ids.add(sid)
                combined_stops.append(dict(stop))
    merged["wl_stops"] = sorted(
        combined_stops, key=lambda item: str(item.get("stop_id") or "")
    )

    # Union wl_lines: the merged station is served by every member's lines
    combined_lines: set[str] = set()
    for entry in (primary, *extras):
        lines = entry.get("wl_lines")
        if isinstance(lines, list):
            combined_lines.update(line for line in lines if isinstance(line, str))
    if combined_lines:
        merged["wl_lines"] = sorted(combined_lines, key=_line_sort_key)

    # Union aliases
    combined_aliases: set[str] = set()
    for entry in (primary, *extras):
        aliases = entry.get("aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and alias.strip():
                    combined_aliases.add(alias)
    merged["aliases"] = sorted(combined_aliases)

    # Mean coordinates (only over members that have them, all do by
    # _all_pairs_within precondition)
    lats: list[float] = []
    lons: list[float] = []
    for entry in (primary, *extras):
        lat = entry.get("latitude")
        lon = entry.get("longitude")
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            lats.append(float(lat))
            lons.append(float(lon))
    if lats and lons:
        mean_lat = round(sum(lats) / len(lats), 6)
        mean_lon = round(sum(lons) / len(lons), 6)
        merged["latitude"] = mean_lat
        merged["longitude"] = mean_lon
        # Re-derive in_vienna against the persisted mean — keeps the
        # flag pair consistent with the coords.
        merged["in_vienna"] = is_in_vienna(mean_lat, mean_lon)
        merged["pendler"] = not bool(merged["in_vienna"])

    return merged
