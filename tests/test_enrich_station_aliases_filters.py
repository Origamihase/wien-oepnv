"""Tests for the alias filters in scripts/enrich_station_aliases.py.

Pin two filters added after the 2026-05 cron audit revealed bad
alias data in stations.json:

1. Generic-alias blocklist — bare "Mitte", "Flughafen" etc. must not
   end up as aliases. They match too broadly in feed text ("in der
   Mitte", "Berlin Mitte", "Flughafen München").

2. Cross-station-collision filter — an alias whose normalized form
   equals the canonical name of a *different* station in the directory
   is dropped. Catches "Wien Rennweg" alias "Rennweg" colliding with
   the U3 station "Rennweg", "Mistelbach Stadt" alias "Mistelbach"
   colliding with the separate Mistelbach Hbf entry, etc.
"""
from __future__ import annotations

from scripts.enrich_station_aliases import (
    _GENERIC_ALIAS_BLOCKLIST,
    _alias_candidates,
)


def test_generic_blocklist_drops_bare_mitte() -> None:
    """The user's example: ``Mitte`` alone is too generic."""
    station = {"name": "Wien Mitte-Landstraße", "aliases": ["Wien Mitte", "Mitte"]}
    aliases = _alias_candidates(station, vor_names={}, vor_mapping={}, gtfs_index={})
    assert "Mitte" not in aliases
    # The Wien-prefixed variant is acceptable (still disambiguated)
    assert "Wien Mitte" in aliases


def test_generic_blocklist_drops_bare_flughafen() -> None:
    """``Flughafen`` is generic for any airport; only the Wien-prefixed
    or Schwechat-disambiguated forms are acceptable."""
    station = {
        "name": "Flughafen Wien",
        "aliases": ["Flughafen", "Flughafen Wien", "Vienna Airport"],
    }
    aliases = _alias_candidates(station, vor_names={}, vor_mapping={}, gtfs_index={})
    assert "Flughafen" not in aliases
    assert "Flughafen Wien" in aliases
    assert "Vienna Airport" in aliases


def test_generic_blocklist_drops_bare_muenchen_substring_of_muenchendorf() -> None:
    """``München`` alone is a *substring* of the canonical name
    "Münchendorf" (a NÖ pendler station). When both stations live in
    the same directory, the bare alias would match Münchendorf-related
    feed text against München Hbf. Only the disambiguated forms
    ("München Hbf", "München Hauptbahnhof") survive the blocklist."""
    station = {
        "name": "München Hauptbahnhof",
        "aliases": [
            "München Hauptbahnhof",
            "München",
            "Muenchen",
            "München Hbf",
            "Muenchen Hbf",
        ],
    }
    aliases = _alias_candidates(station, vor_names={}, vor_mapping={}, gtfs_index={})
    assert "München" not in aliases
    assert "Muenchen" not in aliases
    # The disambiguated forms remain — they pin the entry to "Hbf".
    assert "München Hauptbahnhof" in aliases
    assert "München Hbf" in aliases
    assert "Muenchen Hbf" in aliases


def test_generic_blocklist_covers_directions_and_rail_vocab() -> None:
    """A defensive set: cardinal directions ("Nord", "Süd"), generic
    transport vocabulary ("Bahnhof", "Hbf") and quarter words ("Stadt",
    "Mitte", "Zentrum") must never appear alone as aliases."""
    expected = {
        "wien",
        "vienna",
        "mitte",
        "nord",
        "sud",
        "ost",
        "west",
        "zentrum",
        "stadt",
        "flughafen",
        "bahnhof",
        "hauptbahnhof",
        "hbf",
        "bf",
        "bhf",
        "bahnhst",
        "markt",
        "ort",
        "platz",
        "munchen",
        "muenchen",
    }
    assert expected <= _GENERIC_ALIAS_BLOCKLIST


def test_cross_station_collision_drops_other_station_canonical() -> None:
    """Wien Rennweg's alias 'Rennweg' must be dropped because 'Rennweg'
    is the canonical name of the U3 station — feeding both ÖBB and
    Wiener Linien data they're separate entries that must not share
    aliases. Regression for the cron warning visible in the
    2026-05 stations-coverage audit."""
    wien_rennweg = {
        "name": "Wien Rennweg",
        "aliases": ["Wien Rennweg", "Rennweg", "Bf Rennweg", "Bahnhof Rennweg"],
    }
    # The other station's canonical name normalizes to "rennweg"
    other_keys = frozenset({"rennweg"})

    aliases = _alias_candidates(
        wien_rennweg,
        vor_names={},
        vor_mapping={},
        gtfs_index={},
        other_canonical_keys=other_keys,
    )
    # Bare "Rennweg" colliding with another canonical name → dropped
    assert "Rennweg" not in aliases
    # Wien-prefixed variants must remain (they don't collide)
    assert "Wien Rennweg" in aliases


def test_cross_station_collision_drops_mistelbach_phantom() -> None:
    """Mistelbach Stadt is a *separate* station from Mistelbach (Hbf).
    Its alias list previously contained 'Mistelbach' (matches the
    other station's canonical name) — must be dropped."""
    mistelbach_stadt = {
        "name": "Mistelbach Stadt",
        "aliases": ["Mistelbach Stadt", "Mistelbach", "Bf Mistelbach Stadt"],
    }
    other_keys = frozenset({"mistelbach"})

    aliases = _alias_candidates(
        mistelbach_stadt,
        vor_names={},
        vor_mapping={},
        gtfs_index={},
        other_canonical_keys=other_keys,
    )
    assert "Mistelbach" not in aliases
    assert "Mistelbach Stadt" in aliases


def test_missing_map_adds_wien_prefix_to_ubahn_stations() -> None:
    """The 12 bare-named Wien U-Bahn stations from Google Places get a
    "Wien <Name>" alias added via missing_map so feed-text matching
    against the colloquial "Wien Stadtpark" form still resolves."""
    station = {"name": "Stadtpark", "aliases": ["Stadtpark"]}
    aliases = _alias_candidates(station, vor_names={}, vor_mapping={}, gtfs_index={})
    assert "Wien Stadtpark" in aliases
    assert "Stadtpark" in aliases  # canonical preserved


def test_missing_map_does_not_collide_with_wien_rennweg_sbahn() -> None:
    """The U3 station "Rennweg" must NOT pick up "Wien Rennweg" as an
    alias because that's the canonical name of the separate S-Bahn
    station. The cross-station-collision filter enforces this even
    if a missing_map entry tried to add it."""
    rennweg_u3 = {"name": "Rennweg", "aliases": ["Rennweg"]}
    other_keys = frozenset({"wien rennweg"})  # canonical of the S-Bahn station
    aliases = _alias_candidates(
        rennweg_u3,
        vor_names={},
        vor_mapping={},
        gtfs_index={},
        other_canonical_keys=other_keys,
    )
    assert "Wien Rennweg" not in aliases
    assert "Rennweg" in aliases


def test_cross_station_collision_does_not_drop_own_canonical() -> None:
    """The station's own canonical name normalizes to a key that is in
    other_canonical_keys (passed in as the full canonical-name set);
    the filter must distinguish own-canonical from others-canonical."""
    station = {"name": "Pfaffstätten", "aliases": ["Pfaffstätten", "Pfaffstaetten"]}
    other_keys = frozenset({"pfaffstatten"})  # normalize_key result for this name

    aliases = _alias_candidates(
        station,
        vor_names={},
        vor_mapping={},
        gtfs_index={},
        other_canonical_keys=other_keys,
    )
    # Own canonical must be preserved
    assert "Pfaffstätten" in aliases


def test_pendler_alt_names_propagated_to_aliases() -> None:
    """Pendler_candidates.json's alternative_names should land in the
    matching station's aliases. Closes the gap that "Angern" station
    didn't list "Angern an der March", "Angern (March)", "Angern March"
    even though those forms are explicitly named in the candidates list
    for the resolver.

    The loader keys the alternative_names by every variant's normalized
    form. ``_normalize_key`` strips parenthetical groups including
    their content, so "Angern (March)" → "angern". That's how the
    propagation finds the bare-named "Angern" station entry: its
    canonical "Angern" → "angern" → matches the loader-generated
    "angern" key.
    """
    angern = {"name": "Angern", "aliases": ["Angern", "Angern Bahnhof"]}
    pendler_alt_names = {
        # Loader output: normalized canonical → all variant strings
        "angern an der march": [
            "Angern an der March", "Angern (March)", "Angern March",
        ],
        "angern": [
            "Angern an der March", "Angern (March)", "Angern March",
        ],
        "angern march": [
            "Angern an der March", "Angern (March)", "Angern March",
        ],
    }
    aliases = _alias_candidates(
        angern,
        vor_names={},
        vor_mapping={},
        gtfs_index={},
        pendler_alt_names=pendler_alt_names,
    )
    assert "Angern an der March" in aliases
    assert "Angern (March)" in aliases
    assert "Angern March" in aliases


def test_pendler_alt_names_loader_keys_strip_parens() -> None:
    """The loader normalizes via ``_normalize_key`` which strips paren
    groups along with their content. Pin that "Angern (March)" → key
    "angern" so the bare-named station entry actually matches."""
    from scripts.enrich_station_aliases import (
        _load_pendler_alternative_names,
    )
    import json
    import tempfile
    from pathlib import Path

    payload = {
        "candidates": [
            {
                "name": "Angern an der March",
                "alternative_names": ["Angern (March)", "Angern March"],
            }
        ]
    }
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(payload, fh)
        path = Path(fh.name)

    try:
        result = _load_pendler_alternative_names(path)
    finally:
        path.unlink(missing_ok=True)

    # Each variant produces a key entry; the value is the full list
    assert "angern an der march" in result
    assert "angern" in result  # from "Angern (March)" via paren-strip
    assert "angern march" in result
    expected = {"Angern an der March", "Angern (March)", "Angern March"}
    for key in ("angern an der march", "angern", "angern march"):
        assert set(result[key]) == expected, f"variants for {key!r}: {result[key]}"


def test_pendler_alt_names_skipped_if_dict_is_empty_or_none() -> None:
    """The propagation must be a no-op when no pendler-candidate data
    is supplied (back-compat with callers that don't pass it)."""
    station = {"name": "Angern", "aliases": ["Angern"]}
    aliases = _alias_candidates(
        station, vor_names={}, vor_mapping={}, gtfs_index={}, pendler_alt_names=None
    )
    assert "Angern an der March" not in aliases


def test_bst_code_pushed_into_aliases() -> None:
    """The validator's "missing required aliases" rule expects the
    station's own bst_code (ÖBB Stellencode) to be present in the
    aliases array. Closes the legacy 155-entry alias_issues backlog
    where the ÖBB-Excel import flow wrote bst_code to its own field
    but never to aliases."""
    station = {
        "name": "St.Andrä-Wördern",
        "bst_id": "100",
        "bst_code": "Aw",
        "aliases": ["St.Andrä-Wördern"],
    }
    aliases = _alias_candidates(
        station, vor_names={}, vor_mapping={}, gtfs_index={}
    )
    assert "Aw" in aliases


def test_bst_code_bypasses_generic_blocklist() -> None:
    """Pfaffstätten's bst_code happens to be "Bf" — the generic-
    blocklist suppresses "Bf" coming from arbitrary alias generation,
    but the *own* bst_code must always survive. Otherwise the
    validator's missing-alias check fires for a code the station
    actually has."""
    station = {
        "name": "Pfaffstätten",
        "bst_id": "148",
        "bst_code": "Bf",
        "aliases": ["Pfaffstätten"],
    }
    aliases = _alias_candidates(
        station, vor_names={}, vor_mapping={}, gtfs_index={}
    )
    assert "Bf" in aliases, (
        "the station's own bst_code must bypass the generic-blocklist "
        "so the JSON aliases stay aligned with the bst_code field"
    )
