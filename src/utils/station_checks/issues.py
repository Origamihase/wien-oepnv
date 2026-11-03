"""Station validation: the issue records the checks report.

Moved verbatim from ``src/utils/stations_validation.py`` (700-line rule, 2026-11-02).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DuplicateGroup:
    """Group of stations that share the same coordinates."""

    latitude: float
    longitude: float
    identifiers: tuple[str, ...]
    names: tuple[str, ...]


@dataclass(frozen=True)
class AliasIssue:
    """Station entries with missing or incomplete alias metadata."""

    identifier: str
    name: str
    reason: str


@dataclass(frozen=True)
class GTFSIssue:
    """Stations whose ``vor_id`` is not contained in the GTFS stops file."""

    identifier: str
    name: str
    vor_id: str


@dataclass(frozen=True)
class CoordinateIssue:
    """Stations whose geographic metadata appears to be malformed."""

    identifier: str
    name: str
    reason: str


@dataclass(frozen=True)
class SecurityIssue:
    """Stations containing potentially unsafe characters (XSS risk)."""

    identifier: str
    name: str
    reason: str


@dataclass(frozen=True)
class CrossStationIDIssue:
    """Aliases that collide with identity fields of other stations."""

    identifier: str
    name: str
    alias: str
    colliding_identifier: str
    colliding_name: str
    colliding_field: str


@dataclass(frozen=True)
class ProviderIssue:
    """VOR/OEBB consistency issue (e.g. invalid VOR identifier, OEBB collision)."""

    identifier: str
    name: str
    reason: str


@dataclass(frozen=True)
class NamingIssue:
    """Station whose canonical ``name`` field violates the uniqueness or formatting policy."""

    identifier: str
    name: str
    reason: str


@dataclass(frozen=True)
class IdentityFieldConflict:
    """Two stations declaring the same value in a structural identifier field.

    Caught the 2026-05-16 Innenstadt-U-Bahn drift: ten ``wl_diva`` values
    (Herrengasse, Schwedenplatz, Volkstheater, …) were shared between a
    ``google_places``-sourced stub and the canonical ``source: wl`` entry
    because ``scripts/update_wl_stations.py:merge_into_stations``
    indexed existing entries by ``vor_id`` / ``bst_id`` / ``name`` but
    not by ``wl_diva``. ``_find_cross_station_id_conflicts`` did not
    fire — it only compares aliases against other stations' identity
    fields, not identity-vs-identity. ``_find_duplicate_coordinate_
    groups`` did not fire either — the stub and the master coordinates
    differed by ~7-50 m, below the rounding granularity but above the
    5-decimal-place bucket boundary.

    Distinct from :class:`CrossStationIDIssue`: this dataclass flags a
    raw identity-field collision (same ``wl_diva`` declared by two
    entries), whereas ``CrossStationIDIssue`` captures the more
    indirect case where one entry's *alias* shadows another entry's
    identity field.
    """

    field: str
    value: str
    identifiers: tuple[str, ...]
    names: tuple[str, ...]


@dataclass(frozen=True)
class AliasCollisionIssue:
    """One normalized alias key claimed by stations that disagree about where
    they are.

    ``src.utils.stations._station_lookup`` maps normalized alias → station and
    keeps exactly one winner per key; every later claimant is dropped. That is
    fine while the claimants describe the same place (``Wien Bhf. Hütteldorf
    (WL)`` and ``Wien Hütteldorf``) and a silent misresolution when they do
    not — ``station_info`` feeds ``is_in_vienna``, which decides whether an
    ÖBB disruption reaches the feed at all.

    Audit 2026-09-12, Befund 6: the loader logged 111 lines per process for
    22 contested keys and the validator reported none of it, because
    :func:`_find_alias_issues` checks something else entirely (whether an
    entry *has* a usable alias list). Nothing anywhere compared the
    claimants. Measured at the time: all 44 (key, loser, winner) triples
    agreed on ``in_vienna``, so no collision could change an answer — which
    is a fact about the data, not a property of the code. This check turns it
    into one.

    Reported only when the claimants actually disagree: a different
    ``in_vienna`` verdict, or coordinates further apart than
    :data:`_ALIAS_COLLISION_DISTANCE_THRESHOLD_M`. A key contested by two
    records for the same place stays silent, so the count means "a lookup
    could resolve to the wrong place", not "aliases overlap".
    """

    alias_keys: tuple[str, ...]
    names: tuple[str, ...]
    identifiers: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class CrossNameAliasIssue:
    """Alias or ``wl_stop`` label that doubles as a *different*, far-away
    station's canonical name.

    Post-merge safety-net mirroring the write-time guard
    ``_drop_distant_name_contamination`` in
    ``scripts/update_wl_stations.py`` (the 2026-05 Grinzing→Karlsplatz
    drift: a stop sitting at Grinzing carried the name ``Karlsplatz``
    6.4 km away, so ``station_info("Karlsplatz")`` resolved to the wrong
    stop). The guard strips such labels pre-merge on the Wiener-Linien
    entries only; this check runs over the *final* merged directory, so a
    label that slips in via any other path — a manual edit, a future
    non-WL source — is still surfaced. It reuses the guard's 2 km
    threshold (:data:`_CROSS_NAME_DISTANCE_THRESHOLD_M`) but matches one
    step more broadly: both the label and the owner names are reduced
    with :func:`_bare_station_name`, so a *full-form* ``Wien Karlsplatz``
    label — which the guard leaves in place — is caught alongside the
    short ``Karlsplatz`` form. It still fires only on genuine
    contamination: legitimate sub-2-km interchange labels
    (``Oper, Karlsplatz`` → ``Karlsplatz`` at ~160 m) are kept. Distinct
    from :class:`CrossStationIDIssue`, which matches an alias against
    another station's structural *identity* field rather than its
    display name.
    """

    identifier: str
    name: str
    label: str
    label_kind: str
    colliding_name: str
    distance_m: int


@dataclass(frozen=True)
class NameOwnershipIssue:
    """Two entries share a canonical name and the wrong one wins the lookup.

    ``src.utils.stations._station_lookup`` registers every entry's canonical
    name as a lookup key. Where a later entry claims a key an earlier one
    already holds, the match-strength and source tie-break decides — except
    for one shortcut taken before any of it runs::

        if existing_record == alias_record or existing_record.name == alias_record.name:
            continue

    Same name, so there is nothing to choose between: the first registration
    keeps the key, the later one is dropped, and unlike every other collision
    on that path it is not even logged.

    The shortcut is sound while both entries describe the same place, which
    is what the 2026-05-12 removal of the canonical-name uniqueness gate
    relied on. That removal was right: Vienna genuinely runs distinct stops
    under one name — ``Märzstraße`` appears twice, 479 m apart — and gating
    on the name itself produced the ``Wien Bahnhof (WL 60205022)`` feed
    clutter that PR #1448 then had to paper over with DIVA suffixes, and
    once fanned 30 name issues out into 1759 quarantined WL entries (see
    ``_format_identifier``).

    What the shortcut cannot see is *which* of the two the name belongs to.
    Measured over the 2026-09-18 directory (2243 entries, 8 same-name
    groups): in seven, both members carry a ``wl_stops`` entry of that name
    — genuine twins, and the lookup lands on a stop of the right name either
    way. One group does not. ``Wien Heizwerkstraße (WL)`` / ``wl_diva
    60200228`` holds two stops, both named ``Deutschstraße``, and takes the
    key from ``wl_diva 60201742``, whose stops *are* ``Heizwerkstraße``,
    463 m to the east. Two live consequences:
    ``station_info("Heizwerkstraße")`` hands back the DIVA, stop IDs and
    coordinates of a different stop, and ``station_info("Deutschstraße")``
    — the directory holds no other entry for that label — answers under the
    name ``Wien Heizwerkstraße (WL)``.

    Fires only when a *sibling* bears the name, which is what separates this
    from the gate that was removed. A group where nobody carries a matching
    stop stays silent (the ``Betriebshof X`` entries, whose stops are all
    ``Bahnhof X``), and so does one where the winner carries it — the
    ``Schottenring`` pair, where the loser's sole stop is ``Schottenring,
    Herminengasse``. The count means "this name resolves to the wrong one of
    two", never "this name occurs twice".

    Restricted to identical canonical names on purpose: that is exactly the
    region where the winner is predictable without re-implementing the
    tie-break, because the shortcut above returns before the tie-break is
    reached. Winner therefore means *first in directory order*, which
    ``test_the_validator_agrees_with_the_loader_about_the_winner`` pins
    against the real loader for every group in the live directory.

    Report-only, deliberately, and not wired into
    ``update_all_stations.py:_collect_blocking_issues``. That path quarantines
    by deleting the offending entry from ``stations.json``; deleting 60200228
    would take the directory's only ``Deutschstraße`` stops with it and cost
    more than the misresolution does. The repair is a data decision — rename
    the mislabelled entry or merge the pair — not a deletion.
    """

    name: str
    winner_identifier: str
    winner_stop_names: tuple[str, ...]
    owner_identifiers: tuple[str, ...]
