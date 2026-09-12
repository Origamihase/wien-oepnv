"""A contested alias key must be caught where it can fail a run.

Audit 2026-09-12, Befund 6. ``_station_lookup`` keeps exactly one winner per
normalized alias key and drops every later claimant. Harmless while the
claimants describe the same place (``Wien Bhf. Hütteldorf (WL)`` and
``Wien Hütteldorf``); a silent misresolution when they do not, because
``station_info`` feeds ``is_in_vienna``, which decides whether an ÖBB
disruption reaches the feed at all.

The audit read the loader's 111 WARNING lines against the validator's
"0 alias issues" as a contradiction. It is not one: :func:`_find_alias_issues`
checks whether an entry *has* a usable alias list — never whether two entries
fight over the same key. Nothing anywhere compared the claimants, so the 0 was
not a statement about collisions at all.

Two halves, and the second is what makes the first defensible:

* the loader's log moved to DEBUG and to one line per key
  (``test_station_alias_collision.py``);
* this check reports the collisions that can actually change an answer.

Reporting only real disagreement is the point. A check that fired on every
overlap would report 22 keys on the live directory, be ignored, and buy
nothing over the log line it replaced.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.utils.stations_validation import _find_alias_collision_issues


def _station(
    name: str,
    aliases: list[str],
    *,
    lat: float | None = None,
    lon: float | None = None,
    in_vienna: bool | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "aliases": aliases}
    if lat is not None:
        entry["latitude"] = lat
    if lon is not None:
        entry["longitude"] = lon
    if in_vienna is not None:
        entry["in_vienna"] = in_vienna
    return entry


def _issues(*entries: dict[str, Any]) -> list[Any]:
    return list(_find_alias_collision_issues(list(entries)))


# ---------------- what must be reported ----------------


def test_claimants_that_disagree_on_in_vienna_are_reported() -> None:
    """The signal that matters: the verdict ``station_info`` actually feeds."""
    issues = _issues(
        _station("Inside", ["Shared"], in_vienna=True, lat=48.20, lon=16.37),
        _station("Outside", ["Shared"], in_vienna=False, lat=48.20, lon=16.37),
    )
    assert len(issues) == 1
    assert issues[0].reason == "claimants disagree on in_vienna"
    assert set(issues[0].names) == {"Inside", "Outside"}


def test_claimants_far_apart_are_reported() -> None:
    """Same verdict, different places — the live ``Lokalbahn`` case.

    Four Badner-Bahn stops all claim a generic ``Lokalbahn`` alias. All four
    sit outside Vienna, so ``in_vienna`` alone would miss it; they are 5.6 km
    apart, so whichever one the loader keeps is a coin toss.
    """
    issues = _issues(
        _station("Guntramsdorf", ["Lokalbahn"], in_vienna=False, lat=48.048, lon=16.31),
        _station("Traiskirchen", ["Lokalbahn"], in_vienna=False, lat=48.015, lon=16.29),
    )
    assert len(issues) == 1
    assert issues[0].reason.endswith("m apart")


def test_both_signals_are_needed() -> None:
    """Neither signal subsumes the other.

    A station just outside the boundary can sit metres from one inside it —
    distance alone would miss the flipped verdict. Two stops sharing a verdict
    can be far apart — the verdict alone would miss the distance.
    """
    # Adjacent, but on opposite sides of the boundary.
    near = _issues(
        _station("Inside", ["Shared"], in_vienna=True, lat=48.1200, lon=16.2600),
        _station("Outside", ["Shared"], in_vienna=False, lat=48.1201, lon=16.2601),
    )
    assert [i.reason for i in near] == ["claimants disagree on in_vienna"]

    # Same verdict, far apart.
    far = _issues(
        _station("A", ["Shared"], in_vienna=False, lat=48.00, lon=16.20),
        _station("B", ["Shared"], in_vienna=False, lat=48.20, lon=16.40),
    )
    assert far and far[0].reason.endswith("m apart")


# ---------------- what must stay silent ----------------


def test_the_same_place_under_two_names_is_not_an_issue() -> None:
    """69 of the 111 live log lines were this, and all were harmless.

    ``Wien Bhf. Hütteldorf (WL)`` and ``Wien Hütteldorf`` are one station.
    Whichever wins the key, the lookup lands right.
    """
    assert (
        _issues(
            _station(
                "Wien Bhf. Hütteldorf (WL)",
                ["Bhf. Hütteldorf"],
                in_vienna=True,
                lat=48.1920,
                lon=16.2620,
            ),
            _station(
                "Wien Hütteldorf",
                ["Hütteldorf"],
                in_vienna=True,
                lat=48.1921,
                lon=16.2621,
            ),
        )
        == []
    )


def test_a_key_claimed_by_one_station_is_not_an_issue() -> None:
    assert _issues(_station("Solo", ["Unique"], in_vienna=True)) == []


def test_missing_coordinates_do_not_invent_a_collision() -> None:
    """Fail-closed the other way: no data is not evidence of conflict."""
    assert (
        _issues(
            _station("A", ["Shared"], in_vienna=True),
            _station("B", ["Shared"], in_vienna=True),
        )
        == []
    )


# ---------------- one fact, one row ----------------


def test_cosmetic_spellings_collapse_into_one_issue() -> None:
    """Eleven spellings of one collision must not become eleven findings.

    Before grouping, the live directory produced 11 rows for the single
    ``Lokalbahn`` fact — the same restatement the loader used to emit.
    """
    issues = _issues(
        _station(
            "Guntramsdorf",
            ["Lokalbahn", "Wien Lokalbahn", "Vienna Lokalbahn"],
            in_vienna=False,
            lat=48.048,
            lon=16.31,
        ),
        _station(
            "Traiskirchen",
            ["Lokalbahn", "Wien Lokalbahn", "Vienna Lokalbahn"],
            in_vienna=False,
            lat=48.015,
            lon=16.29,
        ),
    )
    assert len(issues) == 1
    assert set(issues[0].alias_keys) == {"lokalbahn", "wien lokalbahn", "vienna lokalbahn"}


def test_one_entry_claiming_a_key_many_ways_is_one_claimant() -> None:
    """Seven spellings on ONE station are not seven contenders."""
    assert (
        _issues(
            _station(
                "Solo",
                ["Collision", "collision", "Bahnhof Collision", "Collision Bahnhof"],
                in_vienna=True,
                lat=48.2,
                lon=16.3,
            )
        )
        == []
    )


# ---------------- the load-bearing assumption ----------------


def test_the_validator_uses_the_loader_s_own_normalizer() -> None:
    """The check must key on exactly what the loader keys on.

    Not "two implementations agree" — ``stations_validation`` imports
    ``_normalize_token`` from ``src.utils.stations``, so it is the same
    function object and any comparison of the two would be tautological.
    What this pins is that the sharing continues: give the validator its own
    normalizer later and the two drift apart silently, and the check starts
    reporting collisions the loader never has (or missing ones it does).

    That failure mode is not hypothetical — it is what made the original
    finding wrong. ``_find_alias_issues`` and the loader's duplicate-alias log
    were read as contradicting each other while measuring unrelated things.
    """
    from src.utils import stations, stations_validation

    # Zugriff über das Modul-Dict: mypy lehnt den direkten Zugriff auf einen
    # re-exportierten privaten Namen ab, ruff lehnt ``getattr`` mit konstantem
    # Namen ab — und die Weiterreichung selbst ist hier der Prüfgegenstand.
    assert vars(stations_validation)["_normalize_token"] is stations._normalize_token


def test_the_live_directory_reports_only_the_known_collision() -> None:
    """Guards the count against silent growth.

    One finding today: the four Badner-Bahn stops sharing a generic
    ``Lokalbahn`` alias. A new one appearing means a real regression in the
    station data, not noise to be tuned away.
    """
    from src.utils.stations import _station_entries

    issues = list(_find_alias_collision_issues(list(_station_entries())))
    assert len(issues) == 1, [(i.alias_keys, i.names) for i in issues]
    assert all("lokalbahn" in key for key in issues[0].alias_keys)


@pytest.mark.parametrize("reason_field", ["alias_keys", "names", "identifiers"])
def test_the_issue_carries_enough_to_act_on(reason_field: str) -> None:
    issues = _issues(
        _station("Inside", ["Shared"], in_vienna=True),
        _station("Outside", ["Shared"], in_vienna=False),
    )
    assert getattr(issues[0], reason_field), reason_field
