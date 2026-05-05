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
