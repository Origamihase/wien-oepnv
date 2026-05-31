"""bug b11: opaque 3-char ÖBB Betriebsstellen-codes (HAK/STK/REN/HET/SUE/…)
were admitted into the Vienna-detection regex as whole-word alternatives and
matched everyday tokens — most notably the Handelsakademie abbreviation
"HAK" — as a false Vienna signal. Raising the minimum alias length to 4
(mirroring _non_vienna_stations_regex) drops these opaque codes while keeping
every real station reference, which always uses the full name.
"""
from __future__ import annotations

import pytest

from src.utils.stations import text_has_vienna_connection


@pytest.mark.parametrize(
    "text",
    [
        "Die HAK feiert ein Fest.",  # Handelsakademie abbreviation, not a station
        "Abschnitt Stk wird saniert.",
        "Der Zug nach Ren ist verspätet.",
    ],
)
def test_opaque_three_char_codes_no_longer_match(text: str) -> None:
    assert text_has_vienna_connection(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "Sperre bei Wien Mitte.",
        "Störung Wien Hauptbahnhof.",
        "Umbau bei Karlsplatz.",
    ],
)
def test_real_vienna_references_still_match(text: str) -> None:
    assert text_has_vienna_connection(text) is True
