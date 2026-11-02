"""Station labels, aliases and ``build_wl_entries`` of ``scripts/update_wl_stations.py``.

Moved verbatim from the script.
A test that patches a constant or function used here must patch it
on this module, not on the script.
"""
from __future__ import annotations

import math
import re
import sys
from collections.abc import Iterable, Mapping

from src.wl_stations.base import (
    _project_root,
    is_in_vienna,
    is_plausible_wl_coordinate,
    log,
    sanitize_log_arg,
)
from src.wl_stations.colocated import _merge_colocated_duplicates
from src.wl_stations.ogd_csv import (
    Haltepunkt,
    Haltestelle,
    _normalize_key,
    _station_lines,
)


def _canonical_name(raw: str) -> str:
    cleaned = re.sub(r"\s+\([^)]*\)", "", raw).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if not cleaned:
        cleaned = raw.strip()
    if cleaned.casefold().startswith("wien"):
        base = cleaned
    else:
        base = f"Wien {cleaned}".strip()
    if "(WL)" not in base:
        base = f"{base} (WL)"
    return base


# Haltestelle PlatformText values that carry no location information
# on their own and are routinely shared across far-apart stops in the
# OGD-Echtzeit data (e.g. ``Lokalbahn`` × 4 across 5.6 km of greater
# Vienna, ``Bahnhof`` × 2 across 9.4 km). Whenever a station's
# PlatformText is in this set, ``_derive_station_label`` overrides
# it with the more specific haltepunkte ``StopText`` even when the
# StopText does not contain the PlatformText as a substring (e.g.
# PlatformText ``Bahnhof`` → StopText ``Tribuswinkel - Josefsthal``).
_GENERIC_PLATFORM_TEXTS: frozenset[str] = frozenset(
    {
        "bahnhof",
        "bahn",
        "hauptbahnhof",
        "lokalbahn",
        "station",
        "halt",
        "bf",
        "hbf",
        "u-bahn",
    }
)

# Bahnsteig-direction-/Bedarfshalt-Markers that should be stripped
# from haltepunkte StopText before deriving the canonical label.
# ``(Richtung X)`` describes which way the platform faces; ``(Bedarf)``
# marks request stops. Neither is part of the stop's identity.
_STOPTEXT_QUALIFIER_RE = re.compile(
    r"\s*(?:\([^)]*Richtung[^)]*\)|\([^)]*Bedarf[^)]*\)|→.*|←.*)$"
)


def _derive_station_label(platform_text: str, stops: Iterable[Haltepunkt]) -> str:
    """Override generic haltestelle ``PlatformText`` labels with the
    more informative haltepunkte ``StopText``.

    Wiener Linien's OGD-Echtzeit ``haltestellen.csv`` ``PlatformText``
    field is sometimes a generic transport-typed token
    (``Bahnhof``, ``Lokalbahn``, …) that gives the operator no useful
    location context. The corresponding haltepunkte carry a much more
    specific ``StopText`` value (e.g. ``Bahnhof`` →
    ``Tribuswinkel - Josefsthal``). This helper picks the more
    informative label **only** when the PlatformText is one of those
    generic tokens; the much-larger common case (named haltestellen
    like ``Karlsplatz``, ``Stephansplatz``, ``Bhf. Atzgersdorf``)
    keeps the PlatformText untouched so ÖBB / VOR name-based joins
    in ``merge_into_stations`` remain stable and the displayed label
    does not silently rotate to the first haltepunkte StopText (which
    may carry idiosyncratic suffixes like ``Bhf. Atzgersdorf S``).

    Strategy:

    * Strip Bahnsteig direction / Bedarfshalt qualifiers from each
      haltepunkte StopText (``Karlsplatz U (Richtung Reumannplatz)``
      → ``Karlsplatz U``).
    * If ``platform_text`` is in :data:`_GENERIC_PLATFORM_TEXTS` and
      the haltepunkte produce a single cleaned StopText, return the
      StopText (overrides ``Bahnhof`` / ``Lokalbahn`` / …).
    * Otherwise return ``platform_text`` unchanged.
    """
    if platform_text.casefold() not in _GENERIC_PLATFORM_TEXTS:
        return platform_text

    cleaned: set[str] = set()
    for stop in stops:
        normalized = _STOPTEXT_QUALIFIER_RE.sub("", stop.name).strip()
        if normalized:
            cleaned.add(normalized)

    if len(cleaned) == 1:
        return next(iter(cleaned))
    return platform_text


# Plausibility gate for a single Wiener-Linien stop coordinate, applied
# where per-stop values become the station's authoritative coordinate.
# The existing gates (``_coerce_float``'s finiteness check and the WGS-84
# range check on the VOR branch) only reject values that are not
# coordinates at all; this one catches a well-formed coordinate that points
# at the wrong place. Threshold and measurements live with the shared helper
# ``src.utils.stations.is_plausible_wl_coordinate``, which the GeoRSS
# coordinate lookup uses as well.
def _is_plausible_wl_stop(latitude: float, longitude: float) -> bool:
    """Return ``True`` when a stop coordinate can belong to the WL network."""

    return bool(is_plausible_wl_coordinate(latitude, longitude))


def _aggregate_coordinates(stops: Iterable[Haltepunkt]) -> tuple[float | None, float | None]:
    """Return the mean coordinate of *stops*, ignoring implausible ones.

    Stops without coordinates are skipped, as are stops whose coordinate
    fails :func:`_is_plausible_wl_stop` — averaging a Waldviertel outlier
    into a Vienna station drags the result kilometres off and, because
    ``in_vienna`` is derived from this aggregate, silently drops the
    station out of the feed's Vienna-relevance filtering.

    The offending value is only excluded from the aggregate, not deleted
    from ``wl_stops``: that array stays a faithful mirror of the upstream
    export so the defect remains visible to the validator and to anyone
    comparing against the OGD source.

    Args:
        stops: The haltepunkte belonging to one DIVA.

    Returns:
        The rounded ``(latitude, longitude)`` mean, or ``(None, None)``
        when no usable stop remains.
    """

    latitudes: list[float] = []
    longitudes: list[float] = []
    for stop in stops:
        if stop.latitude is None or stop.longitude is None:
            continue
        if not _is_plausible_wl_stop(stop.latitude, stop.longitude):
            log.warning(
                "Implausible WL stop coordinate for stop %s (%s); "
                "excluded from the station aggregate",
                sanitize_log_arg(str(stop.stop_id)),
                sanitize_log_arg(str(stop.name)),
            )
            continue
        latitudes.append(stop.latitude)
        longitudes.append(stop.longitude)
    if not latitudes or not longitudes:
        return None, None
    avg_lat = round(sum(latitudes) / len(latitudes), 6)
    avg_lon = round(sum(longitudes) / len(longitudes), 6)
    return avg_lat, avg_lon


def _alias_variants(
    station_name: str, canonical: str, resolved: str | None
) -> set[str]:
    base = f"Wien {station_name}".strip()

    # Skip the ``U`` / ``Bahnhof`` / ``Station`` augmentations when the
    # base name is too generic. Wiener Linien's OGD has stations like
    # ``Bahnhof`` (multiple DIVAs, disambiguated to ``Wien Bahnhof
    # (WL 60205022)`` post-PR #1448) whose ``_normalize_token``
    # already strips the ``bahnhof``/``hbf``/``bf`` stem and the
    # ``(wl …)`` parenthetical to a single token (``"wien"``).
    # Appending ``U`` to such a base yields ``Wien Bahnhof U`` which
    # normalises to ``"wien u"`` — a catch-all key that shadowed every
    # ``canonical_name("Wien X (U)")`` lookup, breaking the
    # ``test_clean_title_expands_wien_hbf_abbreviation`` regression
    # on ``main`` after the disambiguation landed.
    # ``_is_generic_base`` keeps the standard variants for the common
    # case (multi-token station names like ``Wien Karlsplatz``) and
    # drops them only for the degenerate ``Wien <stem>`` shape.
    is_generic = _is_generic_base(base)

    variants = {canonical, base, f"{base} (WL)"}
    if not is_generic:
        variants.update(
            {
                f"{base} U",
                f"{base} U (VOR)",
                f"{base} Bahnhof",
                f"Bahnhof {base}",
                f"{base} Station",
            }
        )
    english_base = base
    if base.lower().startswith("wien "):
        english_base = f"Vienna {base[5:]}".strip()
        variants.update({english_base, f"{english_base} (WL)"})
        if not is_generic:
            variants.update(
                {
                    f"{english_base} U",
                    f"{english_base} U (VOR)",
                    f"{english_base} Station",
                }
            )
    variants.add(base.replace(" ", "-"))
    variants.add(canonical.replace(" ", "-"))
    if resolved:
        variants.add(resolved)
        variants.add(f"{resolved} (VOR)")
    return {variant for variant in variants if variant.strip()}


def _is_generic_base(base: str) -> bool:
    """Return True when *base* normalises to a single token that would
    collapse the ``U``/``Bahnhof``/``Station`` augmentations into
    catch-all alias keys. The check imports the canonical normaliser
    lazily so this module stays importable when the orchestrator
    starts before ``src.utils.stations`` is on the path.
    """
    base_dir = _project_root()
    if str(base_dir) not in sys.path:  # pragma: no cover - defensive
        sys.path.insert(0, str(base_dir))
    from src.utils.stations import _normalize_token

    tokens = _normalize_token(base).split()
    # ``_normalize_token`` strips ``bahnhof``/``hbf``/``bf``/``bahnhst``
    # plus the parenthetical ``(WL <diva>)`` augmentations. If the
    # base reduces to a single token (typically just ``"wien"``), the
    # augmentations collide with every other Vienna lookup.
    return len(tokens) <= 1


def build_wl_entries(
    haltestellen: dict[str, Haltestelle],
    haltepunkte: Iterable[Haltepunkt],
    vor_mapping: Mapping[str, Mapping[str, object]] | None = None,
    lines_by_stop: Mapping[str, Iterable[str]] | None = None,
    gtfs_coordinates: Mapping[str, tuple[float, float]] | None = None,
) -> list[dict[str, object]]:
    grouped: dict[str, list[Haltepunkt]] = {}
    for halt in haltepunkte:
        station = haltestellen.get(halt.station_id)
        if station is None:
            continue
        key = station.diva or station.station_id
        grouped.setdefault(key, []).append(halt)

    entries: list[dict[str, object]] = []
    for stops in grouped.values():
        if not stops:
            continue
        station = haltestellen.get(stops[0].station_id)
        if station is None:
            continue
        station_identifier = station.diva or station.station_id
        aliases = {station.name}
        stops_payload = []
        for stop in stops:
            # Sanitise both WL ``StopText`` direction markers — '>' and
            # '<' are in the stations validator's ``_UNSAFE_CHARS_RE``
            # (XSS / HTML metacharacters). Replace '>' with U+2192 (→)
            # and '<' with U+2190 (←); both arrows are typographically
            # correct for "Richtung X" / "Aus Richtung X", outside the
            # unsafe-char regex, and preserve direction information for
            # the operator-facing permutations built downstream by
            # ``_alias_variants``.
            stop_name = stop.name.replace(">", "→").replace("<", "←")
            aliases.add(stop_name)
            # Do NOT add ``stop.stop_id`` to ``aliases``. In the legacy
            # ``data.wien.gv.at`` proxy CSV ``STOP_ID`` was an 8-digit
            # RBL-Nummer (semantic Echtzeit-Anker). In the canonical
            # ``wienerlinien.at`` OGD-Echtzeit CSV ``StopID`` collapsed
            # to a small in-CSV row counter (1, 2, 3, …) with no
            # cross-system meaning. Either way the per-platform RBL is
            # reachable via the structured ``wl_stops[].stop_id`` field
            # — adding it to ``aliases`` only invites cross-station-id
            # collisions with other entries' ``bst_id`` / ``wl_diva``.
            stops_payload.append(
                {
                    "stop_id": stop.stop_id,
                    "name": stop_name,
                    "latitude": stop.latitude,
                    "longitude": stop.longitude,
                }
            )
        # Do NOT add ``station.diva`` to ``aliases``. The DIVA is the
        # canonical WL identifier — it is exposed via the structured
        # ``wl_diva`` field on the entry and recognised as an
        # identity-class alias by ``src/utils/stations._station_lookup``
        # at lookup time. Duplicating it into ``aliases`` is both
        # redundant and dangerous: WL has renumbered DIVAs at least
        # once between the data.wien.gv.at proxy era and the current
        # wienerlinien.at OGD-Echtzeit era (e.g. ``60201076`` was
        # Karlsplatz pre-PR #1442 and is Ratzenhofergasse today). A
        # stale alias copy from a prior run trivially collides with
        # another entry's current ``wl_diva``.
        aliases.add(f"Wien {station.name}")
        # The haltestelle ``PlatformText`` is sometimes a generic
        # transport-typed label (``Bahnhof``, ``Lokalbahn``, …) that
        # gives the operator no useful context — multiple haltestellen
        # share it across all of Vienna's outskirts. The corresponding
        # haltepunkte often carry a much more specific ``StopText``
        # (e.g. ``Tribuswinkel - Josefsthal`` for one of the four
        # WLB ``Lokalbahn`` stops). ``_derive_station_label`` picks
        # the most informative label between PlatformText and the
        # haltepunkte StopText, falling back to PlatformText when the
        # StopText carries no additional information (Karlsplatz,
        # Stephansplatz, … stay untouched so ÖBB / VOR name-based
        # joins remain stable).
        display_label = _derive_station_label(station.name, stops)
        if display_label != station.name:
            aliases.add(f"Wien {display_label}")
        canonical = _canonical_name(display_label)
        aliases.add(canonical)
        latitude, longitude = _aggregate_coordinates(stops)
        if latitude is None and gtfs_coordinates:
            # Every OGD haltepunkt of this DIVA is missing or implausible:
            # take the same stop from the WL GTFS timetable (same licensor,
            # same licence). ``wl_stops`` keeps mirroring the OGD export.
            fallback = gtfs_coordinates.get(str(station.diva or ""))
            if fallback is not None:
                latitude, longitude = fallback
                log.info(
                    "WL station %s (%s): coordinate from the WL GTFS stops",
                    station.name,
                    station_identifier,
                )
        # Compute ``in_vienna`` from the aggregate (mean) coordinates
        # rather than any-stop-wins, so the flag stays consistent with
        # the coordinates that will land in the stations.json entry.
        # Pre-fix variant flipped to True the moment any haltepunkt
        # fell inside Vienna's polygon, which produced false-positives
        # for boundary stations: e.g. ``Wien Lohnergasse (WL)`` has
        # one bahnsteig (8802) at (48.2821, 16.3692) just inside the
        # polygon and another (8803) at (48.2821, 16.3690) just
        # outside; the agg mean falls outside, so the entry's coords
        # would say "outside Vienna" while the flag said "inside" —
        # exactly the inconsistency that ``test_coordinates_match_in_
        # vienna_flag`` regression-checks.
        if latitude is not None and longitude is not None:
            in_vienna = is_in_vienna(latitude, longitude)
        else:
            log.warning(
                "WL station %s (%s) lacks coordinates; falling back to name lookup",
                station.name,
                station_identifier,
            )
            in_vienna = is_in_vienna(station.name)
        vor_entry: Mapping[str, object] | None = None
        if vor_mapping:
            for candidate in (
                canonical,
                station.name,
                f"Wien {station.name}",
                station_identifier,
            ):
                key = _normalize_key(str(candidate))
                if key and key in vor_mapping:
                    vor_entry = vor_mapping[key]
                    break
        resolved_name = ""
        if vor_entry:
            vor_id = str(vor_entry.get("vor_id") or "").strip()
            if vor_id:
                aliases.add(vor_id)
            resolved_name = str(vor_entry.get("resolved_name") or "").strip()
            if resolved_name:
                aliases.add(resolved_name)
            if latitude is None or longitude is None:
                lat_val = vor_entry.get("latitude")
                lon_val = vor_entry.get("longitude")
                # Mirror the finiteness + WGS84 range gate every other
                # coordinate-ingestion point enforces (``_coerce_float``,
                # ``_wl_coord_index``, ``extract_oebb_geonetz_stops``); a
                # planted out-of-range value (e.g. latitude 999.0) in the
                # VOR mapping must not flow through as a "valid" coordinate.
                if (
                    isinstance(lat_val, int | float)
                    and isinstance(lon_val, int | float)
                    and not isinstance(lat_val, bool)
                    and not isinstance(lon_val, bool)
                    and math.isfinite(lat_val)
                    and math.isfinite(lon_val)
                    and -90.0 <= lat_val <= 90.0
                    and -180.0 <= lon_val <= 180.0
                ):
                    latitude = round(float(lat_val), 6)
                    longitude = round(float(lon_val), 6)
                    # The flags above were derived from the unreliable name
                    # lookup because aggregate coords were missing. Now that the
                    # VOR mapping's coordinates have been adopted, re-derive
                    # ``in_vienna`` from them (mirroring the aggregate-coord and
                    # co-located-merge branches) so the persisted flag stays
                    # consistent with the persisted coordinates — the invariant
                    # ``test_coordinates_match_in_vienna_flag`` checks.
                    in_vienna = is_in_vienna(latitude, longitude)
        aliases.update(_alias_variants(station.name, canonical, resolved_name or None))
        # Mirror the legacy WL-auto-promote heuristic from
        # ``update_station_directory._annotate_station_flags`` (a WL-sourced
        # station outside Vienna becomes pendler=True; see the regression
        # test ``test_wl_outside_station_becomes_pendler``). Without this,
        # unmatched WL entries from ``build_wl_entries`` reach
        # ``merge_into_stations`` with ``in_vienna=False, pendler=False``
        # and trip ``_find_naming_issues`` → auto-quarantine. ÖBB-mirrored
        # WL stations are unaffected because ``_merge_wl_payload`` does
        # not overwrite the existing entry's flag pair.
        entry = {
            "name": canonical,
            "in_vienna": in_vienna,
            "pendler": not in_vienna,
            "wl_diva": station_identifier,
            "wl_stops": sorted(
                stops_payload,
                key=lambda item: str(item["stop_id"]),
            ),
            "aliases": sorted(
                {alias for alias in aliases if isinstance(alias, str) and alias.strip()}
            ),
            "source": "wl",
        }
        # Do NOT synthesise ``bst_id`` / ``bst_code`` for WL-only
        # entries. ``bst_id`` / ``bst_code`` are ÖBB's namespace
        # ("Betriebsstellennummer" / Stellencode); reusing them for
        # WL-only entries via the prior ``9{DIVA}`` / ``WL-{tok[:3]}``
        # heuristics produced two failure modes at production scale:
        #
        #   (a) ``_find_cross_station_id_conflicts`` flagged
        #       ``alias DIVA`` collisions against synthetic
        #       ``bst_id = 9{DIVA}`` on other entries.
        #   (b) name-based truncation to the first three letters
        #       produced hundreds of duplicates
        #       (``WL-ABS`` for both Absbergbrücke and Absberggasse,
        #       ``WL-ADA`` for Ada-Christen-Gasse and four others, …).
        #
        # The canonical WL identifier is ``wl_diva`` (already set
        # above), which lives in its own namespace and never collides.
        # Existing source="vor"/"google_places"/"manual" entries also
        # carry no ``bst_id`` for similar reasons; downstream lookup
        # is via the structured per-source fields, not bst_id-as-alias.
        if latitude is not None and longitude is not None:
            entry["latitude"] = latitude
            entry["longitude"] = longitude
        if vor_entry:
            vor_id = str(vor_entry.get("vor_id") or "").strip()
            if vor_id:
                entry["vor_id"] = vor_id
        lines = _station_lines((stop.stop_id for stop in stops), lines_by_stop or {})
        if lines:
            entry["wl_lines"] = lines
        entries.append(entry)
    entries.sort(key=lambda item: (str(item.get("name")), str(item.get("wl_diva"))))
    entries = _merge_colocated_duplicates(entries)
    # ``_disambiguate_duplicate_names`` retired 2026-05-12. The
    # stations validator no longer enforces canonical-name uniqueness
    # (PR after #1451) — structured identifiers (``wl_diva``,
    # ``bst_id``, ``vor_id``) carry the project's
    # eindeutigkeits-Garantie. The canonical display name stays
    # ``Wien <PlatformText> (WL)`` for every WL haltestelle, even when
    # the same PlatformText is shared by another DIVA, so the RSS
    # feed renders ``Wien Bahnhof (WL)`` cleanly instead of the
    # ``Wien Bahnhof (WL 60205022)`` DIVA suffix that pre-existed.
    return entries
