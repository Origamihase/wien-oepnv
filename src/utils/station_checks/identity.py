"""Station validation: identifiers, providers and naming.

Moved verbatim from ``src/utils/stations_validation.py`` (700-line rule, 2026-11-02).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence

from src.utils.station_checks.basic import _format_identifier
from src.utils.station_checks.issues import (
    CrossStationIDIssue,
    IdentityFieldConflict,
    NamingIssue,
    ProviderIssue,
)
from src.utils.stations import _normalize_token


# Pattern for the synthetic ``bst_id``/``bst_code`` values assigned to
# VOR-sourced station entries (e.g. ``900100``–``900112`` for the Wien
# departure-board stops). Real VOR/HAFAS stop IDs are 9 digits and live in the
# ``vor_id`` field — they are not validated here. The 5-digit form is kept as
# a tolerated fallback for legacy synthetic ids such as ``93010``.
_VOR_ID_PATTERN = re.compile(r"9\d{4,5}")


def is_synthetic_vor_id(value: object) -> bool:
    """Return ``True`` when *value* is a synthetic VOR station id.

    Synthetic ids occupy the ``9xxxx`` / ``9xxxxx`` range
    (:data:`_VOR_ID_PATTERN`) assigned to VOR-sourced departure-board
    stops — e.g. ``900100``–``900112`` for the Wien U-Bahn stops, plus
    the tolerated legacy 5-digit form such as ``93010``. The live ÖBB
    ``Verzeichnis der Verkehrsstationen`` workbook never carries these,
    so an entry keyed by one cannot round-trip through an Excel re-pull
    and must be preserved verbatim rather than rebuilt. Real ÖBB
    Betriebsstellen ids (e.g. ``1371``) and non-string values return
    ``False``.

    Single source of truth for the synthetic-id test, shared by the VOR
    provider validation below and the directory builder's manual/ÖBB
    classifier (``scripts/update_station_directory.py``) — keeping the
    two in lock-step so a real workbook station can never be mistaken
    for a synthetic one (or vice versa) by only one of them.
    """
    return isinstance(value, str) and _VOR_ID_PATTERN.fullmatch(value) is not None


def _find_cross_station_id_conflicts(
    stations: Sequence[Mapping[str, object]]
) -> Iterator[CrossStationIDIssue]:
    id_map: dict[str, list[tuple[Mapping[str, object], str]]] = defaultdict(list)

    for entry in stations:
        for field in ("bst_id", "bst_code", "vor_id", "wl_diva"):
            val = entry.get(field)
            if isinstance(val, str | int):
                norm_val = _normalize_token(str(val))
                if norm_val:
                    id_map[norm_val].append((entry, field))

    for entry in stations:
        aliases_obj = entry.get("aliases")
        if not isinstance(aliases_obj, Sequence) or isinstance(aliases_obj, str | bytes):
            continue

        for alias in aliases_obj:
            if not isinstance(alias, str):
                continue

            norm_alias = _normalize_token(alias)
            if not norm_alias:
                continue

            if norm_alias in id_map:
                for colliding_entry, field in id_map[norm_alias]:
                    if colliding_entry is entry:
                        continue
                    # Skip when the alias is simply the entry's OWN identity
                    # code in the same field. ``enrich_station_aliases``
                    # deliberately lists a station's own ``bst_code`` among
                    # its aliases, so a duplicate / shared-code record (e.g.
                    # two "Mb  H2H" Siebenhirten entries) makes each copy's
                    # own-code alias "shadow" the other's ``bst_code``. That
                    # is a duplicate / shared-identity collision — surfaced
                    # (non-blocking) by ``_find_identity_field_conflicts`` —
                    # not a genuine alias-shadows-another-station ambiguity.
                    # Firing here would auto-quarantine *every* copy and drop
                    # the station entirely (Wien Siebenhirten / Handelskai).
                    own_val = entry.get(field)
                    if (
                        isinstance(own_val, str | int)
                        and _normalize_token(str(own_val)) == norm_alias
                    ):
                        continue
                    yield CrossStationIDIssue(
                        identifier=_format_identifier(entry),
                        name=str(entry.get("name", "")).strip() or "<unknown>",
                        alias=alias.strip(),
                        colliding_identifier=_format_identifier(colliding_entry),
                        colliding_name=str(colliding_entry.get("name", "")).strip() or "<unknown>",
                        colliding_field=field,
                    )


def _find_identity_field_conflicts(
    stations: Sequence[Mapping[str, object]]
) -> Iterator[IdentityFieldConflict]:
    """Yield one issue per value that appears on more than one station.

    Checks each of the five structural identifier fields
    (``wl_diva`` / ``vor_id`` / ``bst_id`` / ``bst_code`` / ``eva_nr``).
    The project's eindeutigkeits-Garantie pins these as primary keys; any
    duplicate means two stations claim the same physical asset (post
    PR #1538 a ``google_places`` stub and a ``source: wl`` master
    shared the same DIVA for ten Innenstadt-U-Bahn stops because the
    WL merge step indexed by name and ``_normalize_key`` rendered
    ``Herrengasse`` and ``Wien Herrengasse (WL)`` as distinct keys).
    ``eva_nr`` (the UIC station number) was added after an
    ``oebb_geonetz`` Betriebsstelle record (``Handelskai``, bst_code
    ``Nw  H2``) duplicated the canonical ``Wien Handelskai`` Verkehrs-
    station — same ``eva_nr`` 8101934 — and slipped past the other four
    keys because their ``bst_code`` / ``bst_id`` differ.

    Whitespace-stripped ``int`` / ``str`` values are compared
    **case-sensitively** — ÖBB ``bst_code``s are case-significant
    (``Aw`` and ``aw`` are distinct Betriebsstellen), so case-folding
    here would manufacture false-positive collisions; the alias-shadow
    sibling :func:`_find_cross_station_id_conflicts` *does* casefold via
    :func:`_normalize_token` because it matches free-text aliases.
    ``None`` and empty strings are ignored.  Distinct from
    :func:`_find_cross_station_id_conflicts`, which fires only when an
    *alias* on one station shadows an *identity* field on a different
    station — this function fires on raw identity collisions.
    """
    for field in ("wl_diva", "vor_id", "bst_id", "bst_code", "eva_nr"):
        seen: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for entry in stations:
            val = entry.get(field)
            if not isinstance(val, str | int):
                continue
            key = str(val).strip()
            if not key:
                continue
            seen[key].append(entry)
        for value, entries in seen.items():
            if len(entries) <= 1:
                continue
            identifiers = tuple(_format_identifier(e) for e in entries)
            names = tuple(
                str(e.get("name", "")).strip() or "<unknown>" for e in entries
            )
            yield IdentityFieldConflict(
                field=field,
                value=value,
                identifiers=identifiers,
                names=names,
            )


def _extract_source_tokens(value: object) -> set[str]:
    """Return the set of provider source tokens for a stations entry.

    Mirrors the behaviour of the inline logic that previously lived in
    ``scripts/validate_stations.py``: comma-separated strings are split and
    stripped, lists are taken as-is (filtered to strings).
    """
    if isinstance(value, str):
        return {token.strip() for token in value.split(",") if token.strip()}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()


def _find_provider_issues(
    stations: Sequence[Mapping[str, object]]
) -> Iterator[ProviderIssue]:
    """Find VOR/OEBB consistency issues.

    Replicates the checks from the previous inline implementation in
    ``scripts/validate_stations.py``:

    1. At least two stations must declare VOR as a source.
    2. Each VOR station's ``bst_id`` and ``bst_code`` must match the VOR id
       pattern (``_VOR_ID_PATTERN``).
    3. No VOR station's ``bst_code`` may collide with the ``bst_code`` of any
       OEBB-sourced station.
    """
    vor_entries: list[Mapping[str, object]] = []
    # Normalise to ``str`` so an OEBB code serialised as int 900100 and a
    # VOR code serialised as the string "900100" collide correctly (a set
    # treats ``int`` and ``str`` members as distinct), mirroring the
    # ``str(...)`` normalisation used by the sibling identity finders.
    oebb_codes: set[str] = set()

    for entry in stations:
        sources = _extract_source_tokens(entry.get("source"))
        if "vor" in sources:
            vor_entries.append(entry)
        if "oebb" in sources:
            bst_code = entry.get("bst_code")
            # Type-guard mirrors every other ``_find_*`` finder in this
            # module: per the documented threat model the on-disk
            # ``stations.json`` is a planted-input boundary, so a
            # malformed entry with a list / dict ``bst_code`` must yield
            # a ``ProviderIssue`` (or be skipped), not crash the
            # validator with ``TypeError: unhashable type``. Without the
            # guard ``oebb_codes.add(bst_code)`` (set add of a list /
            # dict) and the later ``in oebb_codes`` membership test both
            # raise and propagate out of the public ``validate_stations``
            # entry point.
            if isinstance(bst_code, str | int) and bst_code:
                oebb_codes.add(str(bst_code))

    if len(vor_entries) < 2:
        yield ProviderIssue(
            identifier="<global>",
            name="<global>",
            reason="Need at least two VOR entries",
        )
        return

    for entry in vor_entries:
        identifier = _format_identifier(entry)
        name = str(entry.get("name", "")).strip() or "<unknown>"
        for key in ("bst_id", "bst_code"):
            value = entry.get(key)
            if not is_synthetic_vor_id(value):
                yield ProviderIssue(
                    identifier=identifier,
                    name=name,
                    reason=f"Invalid {key} for VOR: {value}",
                )

    for entry in vor_entries:
        # A station sourced from BOTH ``oebb`` and ``vor`` already contributed
        # its own ``bst_code`` to ``oebb_codes`` above, so an ``in oebb_codes``
        # test would flag it as colliding with itself. It IS the OEBB entry,
        # not a distinct VOR-only station, so skip the cross-provider check.
        if "oebb" in _extract_source_tokens(entry.get("source")):
            continue
        bst_code = entry.get("bst_code")
        # Same type-guard as the OEBB-side ``oebb_codes.add`` above: a
        # malformed list / dict ``bst_code`` would otherwise raise
        # ``TypeError: unhashable`` on the ``in oebb_codes`` membership
        # test and crash the validator. A non-hashable VOR ``bst_code``
        # cannot in principle collide with the (string / int) OEBB
        # entries that were admitted above, so skipping it here is
        # semantically correct as well as crash-safe.
        if isinstance(bst_code, str | int) and str(bst_code) in oebb_codes:
            yield ProviderIssue(
                identifier=_format_identifier(entry),
                name=str(entry.get("name", "")).strip() or "<unknown>",
                reason="VOR bst_code collides with OEBB",
            )


def _find_naming_issues(
    stations: Sequence[Mapping[str, object]]
) -> Iterator[NamingIssue]:
    """Validate flag-consistency invariants.

    Two checks (was three pre-2026-05-12):

    1. **Source-field formatting** – the comma-separated provider source
       string must not contain whitespace inside tokens (e.g.
       ``"google_places, vor"``). Whitespace breaks naive ``==``-based
       lookup branches and signals an unnormalized write path.
    2. **Vienna/pendler mutual exclusivity** – ``in_vienna`` and ``pendler``
       partition the directory: every entry is *either* inside the city
       limits *or* a commuter-belt station outside, never both. The
       exceptions are ``type: manual_foreign_city`` (München, Roma) and
       ``type: manual_distant_at`` (Salzburg, Graz, Linz etc.) where
       both flags may legitimately be ``false``.

    The pre-2026-05-12 canonical-name uniqueness check has been
    removed. ``name`` is the operator-facing display label, not a
    primary key; structured identifiers (``wl_diva``, ``bst_id``,
    ``vor_id``, ``bst_code``) carry the project's eindeutigkeits-
    Garantie. Wiener Linien's OGD-Echtzeit ``PlatformText`` is
    legitimately duplicated for the ten remaining non-mergeable
    multi-DIVA groups (Lokalbahn × 4, Bahnhof × 2, etc.); blocking
    them on name-uniqueness produced exactly the RSS feed clutter
    (``Wien Bahnhof (WL 60205022)``) that the disambiguation work
    in PR #1448 had to introduce. Removing the gate lets the
    upstream ``_disambiguate_duplicate_names`` step retire too, so
    the published feed shows ``Wien Bahnhof (WL)`` without a DIVA
    suffix.
    """

    for entry in stations:
        source = entry.get("source")
        if not isinstance(source, str) or not source:
            continue
        # The format is comma-separated tokens; only the comma is a valid
        # delimiter. Any internal whitespace (including the leading or
        # trailing whitespace around a token) signals inconsistent
        # serialisation.
        if any(part.strip() != part for part in source.split(",")) or " " in source:
            identifier = _format_identifier(entry)
            name = str(entry.get("name", "")).strip() or "<unknown>"
            yield NamingIssue(
                identifier=identifier,
                name=name,
                reason=f"source field has whitespace: {source!r} (expected comma-separated, no spaces)",
            )

    for entry in stations:
        in_vienna = bool(entry.get("in_vienna"))
        pendler = bool(entry.get("pendler"))
        identifier = _format_identifier(entry)
        name = str(entry.get("name", "")).strip() or "<unknown>"
        entry_type = entry.get("type")

        if in_vienna and pendler:
            yield NamingIssue(
                identifier=identifier,
                name=name,
                reason=(
                    "in_vienna and pendler are both true — flags must be "
                    "mutually exclusive (a Vienna station cannot also be a "
                    "commuter-belt station)"
                ),
            )
        elif (
            not in_vienna
            and not pendler
            and entry_type not in ("manual_foreign_city", "manual_distant_at")
        ):
            yield NamingIssue(
                identifier=identifier,
                name=name,
                reason=(
                    "in_vienna and pendler are both false — entry should "
                    "either be classified as a Vienna station, a commuter "
                    "station, or marked with type='manual_foreign_city' "
                    "or type='manual_distant_at'"
                ),
            )
