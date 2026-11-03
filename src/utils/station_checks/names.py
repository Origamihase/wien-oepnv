"""Station validation: names and aliases that point at another station.

Moved verbatim from ``src/utils/stations_validation.py`` (700-line rule, 2026-11-02).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence

from src.utils.geo import haversine_m
from src.utils.station_checks.basic import _extract_float, _format_identifier
from src.utils.station_checks.issues import (
    AliasCollisionIssue,
    CrossNameAliasIssue,
    NameOwnershipIssue,
)
from src.utils.stations import _normalize_token


# Distance beyond which an alias / ``wl_stop`` label that doubles as a
# *different* station's canonical name is treated as contamination rather
# than a legitimate interchange label. The 2 km value mirrors the
# write-time guard ``_drop_distant_name_contamination`` in
# ``scripts/update_wl_stations.py``; the validator reuses it as the
# post-merge backstop but matches one step more broadly — it bares the
# label as well as the owner name (see ``_find_cross_name_alias_issues``),
# closing the full-form ``Wien X`` case the guard leaves in place. A
# directory sweep showed the legitimate cross-name interchange labels
# cluster at <= ~500 m, so 2 km leaves a wide margin and yields zero hits
# on the current clean directory in every matching variant.
_CROSS_NAME_DISTANCE_THRESHOLD_M = 2000.0

# Two claimants further apart than this are describing different places, so
# whichever one the loader happens to keep is a coin toss. Same value as the
# cross-name guard above: both answer "is this label pointing somewhere else?"
_ALIAS_COLLISION_DISTANCE_THRESHOLD_M = 2000.0


def _find_alias_collision_issues(
    stations: Sequence[Mapping[str, object]],
    *,
    threshold_m: float = _ALIAS_COLLISION_DISTANCE_THRESHOLD_M,
) -> Iterator[AliasCollisionIssue]:
    """Yield normalized alias keys whose claimants disagree about location.

    Mirrors the key derivation in ``src.utils.stations._station_lookup``:
    :func:`_normalize_token` over every alias and ``wl_stop`` label. The two
    normalisers are byte-identical over all 249047 label surfaces in the live
    directory — pinned by
    ``test_the_validator_derives_the_same_keys_as_the_loader`` so this check
    cannot drift into measuring something the loader never does. That drift is
    exactly what made the old "0 alias issues" reading misleading.

    Silent for a key claimed by several records describing the same place:
    the loser is dropped, the lookup still lands right.
    """
    claimants: dict[str, list[Mapping[str, object]]] = {}
    for entry in stations:
        seen_keys: set[str] = set()
        for _kind, label in _iter_entry_labels(entry):
            key = _normalize_token(label)
            # One entry claiming a key through seven cosmetic spellings is
            # one claimant, not seven.
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            claimants.setdefault(key, []).append(entry)

    # Group by the set of claimants, not by key. The four Badner-Bahn stops
    # that share a generic "Lokalbahn" alias contest eleven spellings of it
    # ("lokalbahn", "wien lokalbahn", "vienna lokalbahn u vor", …); reporting
    # each separately would restate one fact eleven times — the same noise the
    # loader used to produce.
    grouped: dict[tuple[str, ...], tuple[list[Mapping[str, object]], list[str]]] = {}
    for key, entries in sorted(claimants.items()):
        if len(entries) < 2:
            continue
        if _describe_claimant_disagreement(entries, threshold_m) is None:
            continue
        signature = tuple(_format_identifier(e) for e in entries)
        grouped.setdefault(signature, (entries, []))[1].append(key)

    for signature, (entries, keys) in grouped.items():
        reason = _describe_claimant_disagreement(entries, threshold_m)
        if reason is None:  # pragma: no cover - re-checked above
            continue
        yield AliasCollisionIssue(
            alias_keys=tuple(keys),
            names=tuple(
                str(e.get("name", "")).strip() or "<unknown>" for e in entries
            ),
            identifiers=signature,
            reason=reason,
        )


def _entry_stop_names(entry: Mapping[str, object]) -> tuple[str, ...]:
    """Return the ``wl_stops`` labels of *entry*, stripped, in file order.

    Deliberately not ``_iter_entry_labels(entry)`` filtered to ``wl_stop``:
    that generator feeds a different check and yields alias labels verbatim,
    blanks included. Here the labels are also reported to an operator as
    :attr:`NameOwnershipIssue.winner_stop_names`, so they are stripped and
    empties dropped. Keeping the two apart is the point — widening either to
    serve both is how one check starts measuring the other's surface.
    """
    stops = entry.get("wl_stops")
    if not isinstance(stops, Sequence) or isinstance(stops, str | bytes):
        return ()
    names: list[str] = []
    for stop in stops:
        if isinstance(stop, Mapping):
            stop_name = stop.get("name")
            if isinstance(stop_name, str) and stop_name.strip():
                names.append(stop_name.strip())
    return tuple(names)


def _bears_name(entry: Mapping[str, object], token: str) -> bool:
    """Return ``True`` when one of *entry*'s stops is actually called *token*.

    Compared through :func:`_bare_station_name` on both sides, so the
    ``Wien`` prefix and the ``(WL)`` suffix that every canonical name carries
    do not have to appear on the stop label. Equality, not containment:
    ``Schottenring, Herminengasse`` is a neighbouring stop that merely
    mentions ``Schottenring``, and treating it as bearing the name would
    silence the one case this check exists for.
    """
    return any(_bare_station_name(name) == token for name in _entry_stop_names(entry))


def _find_name_ownership_issues(
    stations: Sequence[Mapping[str, object]]
) -> Iterator[NameOwnershipIssue]:
    """Yield same-name groups whose lookup winner does not bear the name.

    See :class:`NameOwnershipIssue` for what the check is for and why it is
    this narrow. Mechanically:

    1. Group entries by ``_normalize_token(name)`` — the same key
       :func:`src.utils.stations._station_lookup` files the canonical name
       under. Groups of one are the overwhelming majority and are skipped.
    2. Within a group the loader's same-name shortcut keeps the *first*
       registration, and ``_station_entries`` preserves file order, so
       ``group[0]`` is the entry the name resolves to.
    3. Report only when that winner bears no stop of the name while at least
       one sibling does — an unowned name (no member bears it) is a labelling
       convention, not a misresolution, and an owned one is already right.
    """
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for entry in stations:
        key = _normalize_token(str(entry.get("name", "")).strip())
        if key:
            grouped.setdefault(key, []).append(entry)

    for _key, group in grouped.items():
        # Short-circuit, not a guard: a group of one has no ``rest``, so the
        # ``if not owners`` test below already discards it. It is here to
        # skip the ``_bare_station_name`` call for the 2227 singleton groups
        # that make up the directory, and removing it changes no result.
        if len(group) < 2:
            continue
        winner, *rest = group
        token = _bare_station_name(winner.get("name"))
        if not token or _bears_name(winner, token):
            continue
        owners = [entry for entry in rest if _bears_name(entry, token)]
        if not owners:
            continue
        yield NameOwnershipIssue(
            name=str(winner.get("name", "")).strip() or "<unknown>",
            winner_identifier=_format_identifier(winner),
            winner_stop_names=_entry_stop_names(winner),
            owner_identifiers=tuple(_format_identifier(e) for e in owners),
        )


def _describe_claimant_disagreement(
    entries: Sequence[Mapping[str, object]], threshold_m: float
) -> str | None:
    """Return why these claimants conflict, or ``None`` if they agree.

    Two independent signals, because either one on its own leaves a real
    conflict unreported: a station just outside the city boundary sits within
    metres of one inside it (distance alone would miss the flipped
    ``in_vienna``), and two stops that share an ``in_vienna`` verdict can still
    be 30 km apart (the verdict alone would miss that).
    """
    verdicts = {bool(e.get("in_vienna")) for e in entries}
    if len(verdicts) > 1:
        return "claimants disagree on in_vienna"

    coords = [
        (lat, lon)
        for e in entries
        if (lat := _extract_float(e.get("latitude"))) is not None
        and (lon := _extract_float(e.get("longitude"))) is not None
    ]
    if len(coords) < 2:
        return None
    worst = max(
        haversine_m(a[0], a[1], b[0], b[1])
        for i, a in enumerate(coords)
        for b in coords[i + 1 :]
    )
    if worst > threshold_m:
        return f"claimants {round(worst)} m apart"
    return None

_WL_VOR_SUFFIX_RE = re.compile(r"\s*\((?:WL|VOR)\)\s*$")
_WIEN_PREFIX_RE = re.compile(r"^Wien\s+")


def _bare_station_name(name: object) -> str:
    """Reduce a station name *or* a label to a cross-name comparison token.

    Strips the ``(WL)`` / ``(VOR)`` provider suffix and a leading
    ``Wien`` before :func:`_normalize_token`. Applied to *both* the
    canonical names (the owner index) and the alias / ``wl_stop`` labels
    being checked, so the two sides compare on equal footing. This bares
    one step further than ``_drop_distant_name_contamination``, which
    bares only the owner side and matches raw-normalised labels — the
    asymmetry that lets a full-form ``Wien X`` label slip past the guard.
    """
    text = _WL_VOR_SUFFIX_RE.sub("", str(name or ""))
    return _normalize_token(_WIEN_PREFIX_RE.sub("", text))


def _iter_entry_labels(entry: Mapping[str, object]) -> Iterator[tuple[str, str]]:
    """Yield ``(kind, label)`` for every alias and ``wl_stop`` name on *entry*.

    ``kind`` is ``"alias"`` or ``"wl_stop"``. The ``wl_stop`` names are
    included because :func:`src.utils.stations._station_lookup` re-derives
    them as text aliases at lookup time, so a contaminated stop name is
    just as misleading as a contaminated alias.
    """
    aliases = entry.get("aliases")
    if isinstance(aliases, Sequence) and not isinstance(aliases, str | bytes):
        for alias in aliases:
            if isinstance(alias, str):
                yield "alias", alias
    stops = entry.get("wl_stops")
    if isinstance(stops, Sequence) and not isinstance(stops, str | bytes):
        for stop in stops:
            if isinstance(stop, Mapping):
                stop_name = stop.get("name")
                if isinstance(stop_name, str):
                    yield "wl_stop", stop_name


def _build_cross_name_index(
    stations: Sequence[Mapping[str, object]]
) -> dict[str, tuple[str, list[tuple[float, float]]]]:
    """Map bare canonical-name token → (display name, owner coordinates)."""
    index: dict[str, tuple[str, list[tuple[float, float]]]] = {}
    for entry in stations:
        lat = _extract_float(entry.get("latitude"))
        lon = _extract_float(entry.get("longitude"))
        if lat is None or lon is None:
            continue
        token = _bare_station_name(entry.get("name"))
        if not token:
            continue
        display = str(entry.get("name", "")).strip() or "<unknown>"
        index.setdefault(token, (display, []))[1].append((lat, lon))
    return index


def _find_cross_name_alias_issues(
    stations: Sequence[Mapping[str, object]],
    *,
    threshold_m: float = _CROSS_NAME_DISTANCE_THRESHOLD_M,
) -> Iterator[CrossNameAliasIssue]:
    """Flag alias / ``wl_stop`` labels matching a far-away station's name.

    See :class:`CrossNameAliasIssue`. Both the label and the owner names
    are reduced with :func:`_bare_station_name`, so a label is flagged
    when its bare form equals the bare canonical name of one or more
    *other* stations and **every** such owner sits more than *threshold_m*
    away. Baring both sides is deliberately broader than the write-time
    guard (which bares only the owner names): it additionally catches a
    full-form ``Wien X`` label, the belt-and-suspenders case the
    conservative guard leaves in place. The all-owners-distant rule keeps
    a label that also resolves to a nearby interchange of the same name.
    """
    name_index = _build_cross_name_index(stations)

    for entry in stations:
        lat = _extract_float(entry.get("latitude"))
        lon = _extract_float(entry.get("longitude"))
        if lat is None or lon is None:
            continue
        own = _bare_station_name(entry.get("name"))
        for kind, label in _iter_entry_labels(entry):
            # Bare the label too (not just owner names): a full-form
            # "Wien Karlsplatz" label bares to the same token as the
            # "Karlsplatz" owner, so it is caught alongside the short form.
            token = _bare_station_name(label)
            if not token or token == own:
                continue
            owner = name_index.get(token)
            if owner is None:
                continue
            owner_name, coords = owner
            distances = [haversine_m(lat, lon, olat, olon) for olat, olon in coords]
            if distances and all(dist > threshold_m for dist in distances):
                yield CrossNameAliasIssue(
                    identifier=_format_identifier(entry),
                    name=str(entry.get("name", "")).strip() or "<unknown>",
                    label=label.strip(),
                    label_kind=kind,
                    colliding_name=owner_name,
                    distance_m=round(min(distances)),
                )
