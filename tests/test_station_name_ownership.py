"""A name shared by two stations must resolve to the one that bears it.

Audit 2026-09-17, Befund D.2. The finding as written there was wrong about
the mechanism and about the size, and both corrections matter for what this
check may do.

**Wrong about the mechanism.** It read ``naming_issues: 0`` against eight
demonstrable canonical-name collisions and concluded duplicate detection was
coordinate-based only. It is not: ``_find_alias_collision_issues`` already
groups all eight as contested claimants. It stays silent on them by its own
stated contract — the claimants agree on ``in_vienna`` and sit 157-479 m
apart, so no lookup can land outside the right neighbourhood. The 0 was a
correct answer to the question that check asks.

**Wrong about the size.** The proposed repair was to report all eight and
allowlist them until cleaned up. Measured, seven are not defects at all:
Vienna runs distinct stops under one name (``Märzstraße`` twice, 479 m
apart) and both members of those seven carry a ``wl_stops`` entry of the
shared name, so the lookup lands on a stop of the right name whichever wins.
Reporting them would re-create the canonical-name uniqueness gate that was
deliberately removed on 2026-05-12 — the gate that produced the ``Wien
Bahnhof (WL 60205022)`` feed clutter PR #1448 had to paper over with DIVA
suffixes, and that once fanned 30 name issues out into 1759 quarantined WL
entries.

What is left after those two corrections is one real defect, and a predicate
narrow enough to name it without touching the other seven: the entry that
wins the name does not bear it, while a sibling does.

    Wien Heizwerkstraße (WL) / wl_diva 60200228 → stops "Deutschstraße" ×2
    Wien Heizwerkstraße (WL) / wl_diva 60201742 → stops "Heizwerkstraße" ×2

60200228 is registered first and keeps the key, so
``station_info("Heizwerkstraße")`` returns the other stop's DIVA, stop IDs
and coordinates, 463 m west of the stop it names — and ``Deutschstraße``,
which the directory houses nowhere else, answers under the name ``Wien
Heizwerkstraße (WL)``.

The negative half of this file is the load-bearing half. A check that fires
on the other seven is the removed gate wearing a new name, and the
quarantine path would act on it.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.utils.stations_validation import (
    NameOwnershipIssue,
    _find_name_ownership_issues,
)


def _station(name: str, stops: list[str], *, diva: str = "") -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "wl_stops": [{"name": stop, "stop_id": f"s{i}"} for i, stop in enumerate(stops)],
    }
    if diva:
        entry["wl_diva"] = diva
    return entry


def _issues(*entries: dict[str, Any]) -> list[NameOwnershipIssue]:
    return list(_find_name_ownership_issues(list(entries)))


# ---------------- what must be reported ----------------


def test_the_winner_that_does_not_bear_the_name_is_reported() -> None:
    """The live shape, reduced: first registration wins and is the wrong one."""
    issues = _issues(
        _station("Wien Heizwerkstraße (WL)", ["Deutschstraße"], diva="60200228"),
        _station("Wien Heizwerkstraße (WL)", ["Heizwerkstraße"], diva="60201742"),
    )

    assert len(issues) == 1
    assert "60200228" in issues[0].winner_identifier
    assert issues[0].owner_identifiers == ("wl_diva:60201742",)
    assert issues[0].winner_stop_names == ("Deutschstraße",)


def test_the_issue_names_the_stop_the_lookup_actually_returns() -> None:
    """Without the winner's stop labels the report cannot be acted on.

    "The name resolves to the wrong entry" is not enough to repair anything;
    "it resolves to an entry whose stops are called Deutschstraße" says both
    what went wrong and what the entry should have been called.
    """
    issues = _issues(
        _station("Wien X (WL)", ["Deutschstraße", "Deutschstraße"], diva="1"),
        _station("Wien X (WL)", ["X"], diva="2"),
    )

    assert issues[0].winner_stop_names == ("Deutschstraße", "Deutschstraße")
    assert issues[0].name == "Wien X (WL)"


def test_the_order_in_the_file_decides_who_is_reported() -> None:
    """Swap the pair and the group is clean — the winner now bears the name.

    This is the whole reason the check may assume a winner at all: for two
    entries with the *same* name the loader returns before its tie-break
    runs, so registration order alone decides. Pinned here as behaviour and
    against the real loader in
    ``test_the_validator_agrees_with_the_loader_about_the_winner``.
    """
    ordered = _issues(
        _station("Wien X (WL)", ["Deutschstraße"], diva="1"),
        _station("Wien X (WL)", ["X"], diva="2"),
    )
    swapped = _issues(
        _station("Wien X (WL)", ["X"], diva="2"),
        _station("Wien X (WL)", ["Deutschstraße"], diva="1"),
    )

    assert len(ordered) == 1
    assert swapped == []


# ---------------- what must stay silent ----------------


def test_twins_that_both_bear_the_name_are_not_an_issue() -> None:
    """Seven of the live eight. Reporting these is the removed gate again.

    ``Märzstraße`` really is two stops 479 m apart, both called Märzstraße.
    Whichever wins, the caller gets a stop of the name it asked for.
    """
    assert _issues(
        _station("Wien Märzstraße (WL)", ["Märzstraße"], diva="60200909"),
        _station("Wien Märzstraße (WL)", ["Märzstraße"], diva="60200911"),
    ) == []


def test_a_name_nobody_bears_is_a_convention_not_a_defect() -> None:
    """``Betriebshof X`` entries carry stops called ``Bahnhof X`` — all of them.

    There is no better claimant to hand the name to, so there is nothing to
    report. Firing here would flag the naming convention itself.
    """
    assert _issues(
        _station("Wien Betriebshof Kagran (WL)", ["Bahnhof Kagran"], diva="1"),
        _station("Wien Betriebshof Kagran (WL)", ["Bahnhof Kagran Ost"], diva="2"),
    ) == []


def test_a_neighbouring_stop_does_not_count_as_bearing_the_name() -> None:
    """The live ``Schottenring`` pair, and why the match is equality.

    ``wl_diva 60201591``'s only stop is ``Schottenring, Herminengasse`` — a
    different stop that mentions Schottenring. The winner 60201182 does carry
    a plain ``Schottenring``, so the lookup is right and the group is silent.
    Were containment used instead of equality, the loser would count as
    bearing the name here — harmless — and in the Heizwerkstraße group
    ``Deutschstraße`` would still not match, so the check would survive. What
    containment really costs is the reverse direction, pinned below.
    """
    assert _issues(
        _station("Wien Schottenring (WL)", ["Schottenring", "Schottenring U"], diva="60201182"),
        _station("Wien Schottenring (WL)", ["Schottenring, Herminengasse"], diva="60201591"),
    ) == []


def test_containment_would_silence_the_live_defect() -> None:
    """The direction that makes equality load-bearing.

    A winner whose stop merely *contains* the name — ``Heizwerkstraße,
    Betriebsbahnhof`` — is still not the stop called Heizwerkstraße. Relaxing
    ``_bears_name`` to a substring test makes this case pass silently, which
    is the same failure the check exists to end.
    """
    issues = _issues(
        _station("Wien Heizwerkstraße (WL)", ["Heizwerkstraße, Betriebsbahnhof"], diva="1"),
        _station("Wien Heizwerkstraße (WL)", ["Heizwerkstraße"], diva="2"),
    )
    assert len(issues) == 1


def test_a_full_form_stop_label_still_bears_the_name() -> None:
    """Both sides are bared, and the asymmetry is the documented bug shape.

    Stop labels normally arrive short (``Heizwerkstraße``) while canonical
    names carry the ``Wien`` prefix and the ``(WL)`` suffix, so baring the
    label side is a no-op — measured over all 4578 live labels, it changes
    nothing today. It is not decoration: bare only the owner side and a
    full-form label stops matching its own station, which is precisely the
    hole ``_find_cross_name_alias_issues`` documents in the write-time guard
    it backstops.

    Here that asymmetry would invent a finding out of a correctly labelled
    pair. Pinned with a synthetic label because the live export happens not
    to ship one — "happens not to" is the part that changes without warning.
    """
    assert _issues(
        _station("Wien X (WL)", ["Wien X (WL)"], diva="1"),
        _station("Wien X (WL)", ["X"], diva="2"),
    ) == []


def test_a_unique_name_is_never_examined() -> None:
    """105 live entries bear no stop of their own name and are all fine.

    ``Wien Hernals`` carries only ``Hernals S``; ``Wien Barnabitengasse (WL)``
    only ``Haus des Meeres``. The check never looks at them because nothing
    contests their name — which is what keeps it at one finding instead of
    105.
    """
    assert _issues(_station("Wien Hernals", ["Hernals S"], diva="1")) == []


def test_an_entry_without_stops_cannot_take_a_name_from_one_with_them() -> None:
    """A stopless winner bears nothing, so a bearing sibling is reported.

    VOR/ÖBB entries routinely have no ``wl_stops`` at all. When such an entry
    shares a canonical name with a WL entry whose stops carry it, the lookup
    really does answer from the stopless record.
    """
    issues = _issues(
        {"name": "Wien X (WL)", "wl_diva": "1"},
        _station("Wien X (WL)", ["X"], diva="2"),
    )
    assert len(issues) == 1
    assert issues[0].winner_stop_names == ()


def test_malformed_stop_entries_are_skipped_not_crashed_on() -> None:
    """``wl_stops`` is external data; a well-formed list is not guaranteed.

    A string where a list belongs, ``None`` and an int where mappings belong,
    a stop whose ``name`` is null — each is skipped, and the one usable label
    still counts. The validator runs in the cron path that writes
    ``stations.json``; raising here would abort the refresh over a shape the
    check has no stake in.
    """
    issues = _issues(
        {"name": "Wien X (WL)", "wl_stops": "not-a-list", "wl_diva": "1"},
        {
            "name": "Wien X (WL)",
            "wl_stops": [None, 42, {"name": None}, {"name": "X"}],
            "wl_diva": "2",
        },
    )

    # The second entry's single usable label bears the name; the first bears
    # nothing at all, so the pair reports exactly as the clean shape does.
    assert len(issues) == 1
    assert issues[0].winner_identifier == "wl_diva:1"
    assert issues[0].winner_stop_names == ()


def test_a_blank_name_is_skipped() -> None:
    assert _issues({"name": "   "}, {"name": ""}) == []


# ---------------- agreement with the loader ----------------


def test_the_validator_agrees_with_the_loader_about_the_winner() -> None:
    """"First in file order wins" must be what ``station_info`` really does.

    The check does not re-implement ``_station_lookup``'s tie-break; it
    relies on the same-name shortcut returning before that tie-break is
    reached, which leaves registration order as the only decider. That is an
    assumption about another module, so it is measured against it rather than
    asserted — over every same-name group in the live directory, not just the
    one that fires.

    This is the test to read first if the check ever starts reporting the
    wrong entry: the shortcut moved, and the winner is no longer ``group[0]``.
    """
    from collections import defaultdict

    from src.utils.stations import _normalize_token, _station_entries, station_info

    entries = list(_station_entries())
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        key = _normalize_token(str(entry.get("name", "")).strip())
        if key:
            grouped[key].append(entry)

    groups = {k: v for k, v in grouped.items() if len(v) > 1}
    assert groups, "the directory has no same-name groups left — retire this check"

    for group in groups.values():
        # Without this the comparison below can pass on ``None == None`` for a
        # group of DIVA-less entries and prove nothing.
        divas = [e.get("wl_diva") for e in group]
        assert all(divas), f"{group[0]['name']!r}: group member without a DIVA"
        assert len(set(divas)) == len(divas), f"{group[0]['name']!r}: DIVA repeated"

        resolved = station_info(str(group[0]["name"]))
        assert resolved is not None
        assert resolved.wl_diva == group[0].get("wl_diva"), (
            f"{group[0]['name']!r} resolves to {resolved.wl_diva}, "
            f"not to the first entry {group[0].get('wl_diva')}"
        )


def test_the_live_directory_reports_only_the_known_defect() -> None:
    """Guards the count against silent growth in either direction.

    A second finding means a new mislabelled entry reached the directory. A
    zero means either the data was repaired (then delete this assertion with
    the repair) or the check stopped working.
    """
    from src.utils.stations import _station_entries

    issues = list(_find_name_ownership_issues(list(_station_entries())))
    assert len(issues) == 1, [(i.name, i.winner_identifier) for i in issues]
    assert "60200228" in issues[0].winner_identifier
    assert issues[0].winner_stop_names == ("Deutschstraße", "Deutschstraße")


def test_the_live_defect_is_a_real_misresolution() -> None:
    """Not a report artefact: the lookup genuinely answers from the wrong stop.

    Both halves are the damage. ``Heizwerkstraße`` returns a record whose
    stop IDs belong to Deutschstraße, and ``Deutschstraße`` — which the
    directory houses in no other entry — answers under a name that is not
    its own.
    """
    from src.utils.stations import station_info

    heizwerk = station_info("Heizwerkstraße")
    assert heizwerk is not None
    assert heizwerk.wl_diva == "60200228"
    assert {stop.name for stop in heizwerk.wl_stops} == {"Deutschstraße"}

    deutschstrasse = station_info("Deutschstraße")
    assert deutschstrasse is not None
    assert deutschstrasse.name == "Wien Heizwerkstraße (WL)"


# ---------------- what the check may NOT do ----------------


@pytest.mark.parametrize(
    "collector",
    ["_collect_blocking_issues", "_collect_quarantine_identifiers"],
)
def test_the_check_never_reaches_the_quarantine_path(collector: str) -> None:
    """Deleting the reported entry would cost more than the defect does.

    ``naming_issues`` feeds both collectors, and the quarantine path removes
    the matching entry from ``stations.json``. Routed there, this check would
    delete ``wl_diva 60200228`` — the directory's only holder of the
    ``Deutschstraße`` stops — on the next cron tick, turning a wrong name
    into a missing station. The repair is a rename or a merge, and that is a
    decision for an operator.

    Pinned by source inspection rather than by a run because the cheapest way
    to break it is a one-line addition to either collector.
    """
    import inspect

    from scripts import update_all_stations

    source = inspect.getsource(getattr(update_all_stations, collector))
    assert "name_ownership_issues" not in source, (
        f"{collector} now quarantines on name-ownership issues; "
        "see this test's docstring for why that deletes a live station"
    )


def test_the_report_names_both_sides() -> None:
    """An operator reads the Markdown, not the dataclass."""
    from pathlib import Path

    from src.utils.stations_validation import validate_stations

    markdown = validate_stations(Path("data/stations.json")).to_markdown()

    assert "*Namens-Zuordnungskonflikte*: 1" in markdown
    assert "## Namens-Zuordnungskonflikte" in markdown
    assert "60200228" in markdown
    assert "60201742" in markdown
    assert "Deutschstraße" in markdown
