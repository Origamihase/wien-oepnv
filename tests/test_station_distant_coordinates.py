"""Regression tests for distant terminus station coordinates.

The directory carries a handful of long-distance terminus stations
(Berlin Hbf, Roma Termini, Praha hl.n., Venezia S.L., Zagreb Glavni
kolodvor, Budapest-Keleti). Their coordinates lie outside the Austrian
Bounding-Box that ``_coerce_lat``/``_coerce_lon`` historically enforced,
which silently dropped one component of each pair (latitude or
longitude, depending on which value crossed the bound). The bounds now
span Europe so both components survive.
"""

from __future__ import annotations

from src.utils.stations import is_in_vienna, station_info


_DISTANT_TERMINI = (
    "Berlin Hbf",
    "Roma Termini",
    "Praha hl.n.",
    "Venezia Santa Lucia",
    "Zagreb Glavni kolodvor",
    "Budapest-Keleti",
)


def test_distant_termini_keep_both_coordinates() -> None:
    for name in _DISTANT_TERMINI:
        info = station_info(name)
        assert info is not None, f"{name} is missing from stations.json"
        assert info.latitude is not None, (
            f"{name} latitude was dropped — bounds in _coerce_lat may have "
            f"regressed to Austria-only"
        )
        assert info.longitude is not None, (
            f"{name} longitude was dropped — bounds in _coerce_lon may have "
            f"regressed to Austria-only"
        )


def test_distant_termini_are_outside_vienna_polygon() -> None:
    # The wider bounds must NOT make foreign cities resolve as in-Vienna.
    # The polygon test still does the actual containment check.
    for name in _DISTANT_TERMINI:
        info = station_info(name)
        assert info is not None
        assert info.latitude is not None and info.longitude is not None
        assert is_in_vienna(info.latitude, info.longitude) is False, (
            f"{name} resolves as in-Vienna — polygon test broke"
        )
