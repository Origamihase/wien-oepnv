"""Teil 2 der Tests aus tests/test_utils_stats.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.utils import stats as stats_utils  # noqa: E402

from ._support import (
    VIENNA_TZ,
    _LONG_WL_DESCRIPTION,
    _read_csv,
    _wl_item,
)


def test_append_disruption_row_does_not_modify_safe_text(tmp_path: Path) -> None:
    """Legitimate provider/location strings must round-trip byte-exactly."""
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_disruption_row(
        timestamp=when,
        provider="ÖBB",
        location_name="Wien Floridsdorf",
        stats_dir=tmp_path,
    )
    _, rows = _read_csv(tmp_path / "stoerungen_2026.csv")
    assert rows[0][3] == "ÖBB"
    assert rows[0][4] == "Wien Floridsdorf"


def test_append_stammstrecke_row_does_not_modify_safe_text(tmp_path: Path) -> None:
    """Legitimate direction strings must round-trip byte-exactly."""
    when = datetime(2026, 5, 4, 7, 30, tzinfo=VIENNA_TZ)
    stats_utils.append_stammstrecke_row(
        timestamp=when,
        direction="Meidling",
        delay_minutes=5.5,
        stats_dir=tmp_path,
    )
    _, rows = _read_csv(tmp_path / "stammstrecke_2026.csv")
    assert rows[0][3] == "Meidling"
    # Numeric formatting unchanged.
    assert rows[0][4] == "5.50"


def test_read_recent_observations_localizes_year_at_new_year_boundary(
    tmp_path: Path,
) -> None:
    """File selection must use the Vienna-local year (matching the writer).

    A row written at 2027-01-01 00:30 Vienna (= 2026-12-31 23:30 UTC) lands in
    ``stammstrecke_2027.csv``. Reading with a UTC-aware ``now`` that is still
    2026 in UTC but already 2027 in Vienna must still find that row, i.e. the
    reader must scan the 2027 ledger rather than only the UTC ``now.year``.
    """
    written_at = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)
    stats_utils.append_stammstrecke_row(
        timestamp=written_at,
        direction="Meidling",
        delay_minutes=6.0,
        stats_dir=tmp_path,
    )
    # Confirm the writer placed the row in the *new* year's ledger.
    assert (tmp_path / "stammstrecke_2027.csv").exists()

    now_utc = datetime(2026, 12, 31, 23, 45, tzinfo=UTC)  # 2027-01-01 00:45 Vienna
    observations = stats_utils.read_recent_stammstrecke_observations(
        now=now_utc,
        window=timedelta(hours=2),
        stats_dir=tmp_path,
    )
    assert len(observations) == 1
    assert observations[0].direction == "Meidling"


def test_extract_location_name_reads_haltestelle_after_long_description() -> None:
    """The label is honoured even when it sits far past the 1024-char mark."""
    description = _LONG_WL_DESCRIPTION + " | Haltestelle: Dornbach"
    assert len(description) > 1024
    assert stats_utils.extract_location_name(_wl_item(description)) == "Wien Dornbach (WL)"


def test_extract_location_name_haltestelle_result_is_length_independent() -> None:
    """Same marker, short vs. long description — identical result."""
    short = stats_utils.extract_location_name(
        _wl_item("<p>Kurze Meldung.</p> | Haltestelle: Dornbach")
    )
    long = stats_utils.extract_location_name(
        _wl_item(_LONG_WL_DESCRIPTION + " | Haltestelle: Dornbach")
    )
    assert short == long == "Wien Dornbach (WL)"


def test_extract_location_name_labelled_scan_stays_bounded() -> None:
    """Beyond ``MAX_LABELLED_SCAN_CHARS`` the marker is still out of scope."""
    description = "x" * (stats_utils.MAX_LABELLED_SCAN_CHARS + 100) + " | Haltestelle: Dornbach"
    assert stats_utils.extract_location_name(_wl_item(description)) == "unbekannt"


def test_extract_location_name_station_label_after_long_description() -> None:
    """The ``| Station:`` fallback shares the widened window."""
    description = _LONG_WL_DESCRIPTION + " | Station: Wien Floridsdorf"
    assert stats_utils.extract_location_name(_wl_item(description)) == "Wien Floridsdorf"


def test_extract_location_name_unanchored_scan_keeps_1024_bound() -> None:
    """Widening the label window must not widen the fuzzy scan.

    Without a label, a station mentioned only past 1024 chars stays
    unresolved — the scan returns the first directory hit in reading order,
    which that deep into a description is typically a street mentioned in
    passing rather than the affected stop.
    """
    description = ("Arbeiten im Bereich der Nebenfahrbahn. " * 40) + " Dornbach"
    assert len(description) > 1024
    assert stats_utils.extract_location_name(_wl_item(description)) == "unbekannt"


def test_extract_location_name_generic_lokalbahn_does_not_pick_a_winner() -> None:
    """A bare "Lokalbahn" must not collapse onto one Badner-Bahn stop.

    Four stops up to 5.6 km apart claim that alias; before audit 2026-09-13
    the tie-break silently filed every generic Badner-Bahn mention under
    "Wien Guntramsdorf Lokalbahn (WL)".
    """
    item = {
        "title": "Badner Bahn: Störung Lokalbahn",
        "description": "",
        "source": "Wiener Linien",
    }
    assert stats_utils.extract_location_name(item) == "unbekannt"


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Störung Wien Traiskirchen Lokalbahn", "Wien Traiskirchen Lokalbahn (WL)"),
        ("Störung Wien Guntramsdorf Lokalbahn", "Wien Guntramsdorf Lokalbahn (WL)"),
    ],
)
def test_extract_location_name_named_lokalbahn_stops_still_resolve(
    title: str, expected: str
) -> None:
    """Filtering the generic token must not hide the fully-named stops."""
    item = {"title": title, "description": "", "source": "Wiener Linien"}
    assert stats_utils.extract_location_name(item) == expected
