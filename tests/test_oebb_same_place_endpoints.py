"""One place, two names: an ÖBB title names a station once.

ÖBB message 877052 (14 May 2026, works between Wien Zentralfriedhof and
Wien Kaiserebersdorf) named its end once as the Wiener Linien stop "Wien
Bhf. Kaiserebersdorf (WL)" (from the cached title) and once as the railway
station "Wien Kaiserebersdorf" (from the text). The title read
``Wien Zentralfriedhof ↔ Wien Bhf. Kaiserebersdorf (WL) / Wien
Kaiserebersdorf``. It was first published as ``Wien Zentralfriedhof ↔ Wien Bhf.
Kaiserebersdorf (WL)``. The station directory knows which stop stands at
which railway station (``railway_station_of``); an ÖBB title names the
station.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.providers.oebb_route_title import _apply_route_title, _format_route_title
from src.utils.stations import railway_station_names, railway_station_of

_DESC_877052 = (
    "14.05.2026<br/><br/>Wegen Bauarbeiten sind zwischen <b>Wien Zentralfriedhof "
    "Bahnhst</b> und <b>Wien Kaiserebersdorf Bahnhst</b> <b>derzeit keine Fahrten</b> "
    "möglich. Über die Dauer der Unterbrechung kann derzeit noch keine Angabe gemacht "
    "werden."
)


def test_cached_wl_name_and_text_station_name_one_end() -> None:
    title = _apply_route_title(
        "Wien Zentralfriedhof ↔ Wien Bhf. Kaiserebersdorf (WL)", _DESC_877052
    )
    assert title == "Wien Zentralfriedhof ↔ Wien Kaiserebersdorf"


def test_railway_name_wins_whatever_comes_first() -> None:
    routes = [
        ("Wien Zentralfriedhof", "Kaiserebersdorf (Wien)"),
        ("Wien Zentralfriedhof", "Wien Bhf. Kaiserebersdorf (WL)"),
    ]
    assert _format_route_title(routes) == "Wien Zentralfriedhof ↔ Wien Kaiserebersdorf"
    assert _format_route_title(routes[::-1]) == "Wien Zentralfriedhof ↔ Wien Kaiserebersdorf"


def test_stretch_inside_one_place_names_it_once() -> None:
    """A stretch from a stop to its own station never reads "X ↔ X", neither
    from the title formatter nor after the provider's and the cache's
    ``railway_station_names`` (found by review 2026-11-08)."""
    from src.feed_build.cache_filters import _post_filter_oebb

    assert _format_route_title([("Wien Bhf. Meidling (WL)", "Wien Meidling")]) == "Wien Meidling"
    chain = [("Wien Bhf. Hütteldorf (WL)", "Wien Hütteldorf"), ("Wien Hütteldorf", "Purkersdorf")]
    assert railway_station_names(_format_route_title(chain)) == "Wien Hütteldorf ↔ Purkersdorf"
    desc = "Zwischen Wien Bhf. Meidling (WL) und Wien Meidling fahren keine Züge."
    assert railway_station_names(_apply_route_title("Bauarbeiten", desc)) == "Wien Meidling"
    (out,) = _post_filter_oebb([{"source": "ÖBB", "title": "Bauarbeiten", "description": desc, "guid": "y"}])
    assert "Meidling ↔" not in out["title"]


def test_stop_with_street_addition_is_a_place_of_its_own() -> None:
    routes = [
        ("Wien Zentralfriedhof", "Wien Kaiserebersdorf"),
        ("Wien Zentralfriedhof", "Wien Kaiserebersdorf, Zinnergasse (WL)"),
    ]
    assert "Zinnergasse" in _format_route_title(routes)


def test_stop_alone_becomes_its_station() -> None:
    """Published 14 May 2026: an ÖBB title naming the stop, not the station."""
    title = _format_route_title([("Wien Zentralfriedhof", "Wien Bhf. Kaiserebersdorf (WL)")])
    assert title == "Wien Zentralfriedhof ↔ Wien Kaiserebersdorf"


# The stops the directory puts at a railway station (its alias table resolves
# the stop's name without "(WL)" to the station), as of 2026-11-08.
_EXPECTED = {
    "Wien Bhf. Atzgersdorf (WL)": "Wien Atzgersdorf",
    "Wien Bhf. Blumental (WL)": "Wien Blumental",
    "Wien Bhf. Hütteldorf (WL)": "Wien Hütteldorf",
    "Wien Bhf. Kaiserebersdorf (WL)": "Wien Kaiserebersdorf",
    "Wien Bhf. Meidling (WL)": "Wien Meidling",
    "Wien Bhf. Süßenbrunn (WL)": "Wien Süßenbrunn",
    "Wien Bhf. Zentralfriedhof (WL)": "Wien Zentralfriedhof",
}


def _directory_names() -> list[str]:
    path = Path(__file__).resolve().parents[1] / "data" / "stations.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data["stations"] if isinstance(data, dict) else data
    return sorted({entry["name"] for entry in entries})


def test_directory_pairs_are_stops_named_after_their_station() -> None:
    """Every pair over the whole directory is a stop "Wien Bhf. X (WL)" and
    the railway station "Wien X"; it holds after every directory refresh.
    A new pair of that form is fine, any other form needs a look."""
    pairs = {name: railway_station_of(name) for name in _directory_names()}
    pairs = {stop: station for stop, station in pairs.items() if station}
    assert pairs["Wien Bhf. Kaiserebersdorf (WL)"] == "Wien Kaiserebersdorf"
    for stop, station in pairs.items():
        assert stop.startswith("Wien Bhf. ") and stop.endswith(" (WL)"), (stop, station)
        assert station == "Wien " + stop[len("Wien Bhf. "):-len(" (WL)")], (stop, station)
    assert set(_EXPECTED.items()) <= set(pairs.items())


def test_no_pair_for_railway_stations_or_other_stops() -> None:
    assert railway_station_of("Wien Kaiserebersdorf") is None
    assert railway_station_of("Wien Hauptbahnhof Süd (WL)") is None
    assert railway_station_of("Wien Kaiserebersdorf, Zinnergasse (WL)") is None
    assert railway_station_of("Unbekannter Ort") is None


def test_lone_stop_title_names_its_station() -> None:
    """TRACKINFO 911065 (11.09.2026, signal box fault at Wien Meidling) stood
    in the feed as "Wien Bhf. Meidling (WL)"."""
    assert railway_station_names("Wien Bhf. Meidling (WL)") == "Wien Meidling"
    assert (
        railway_station_names("Wien Zentralfriedhof ↔ Wien Bhf. Kaiserebersdorf (WL)")
        == "Wien Zentralfriedhof ↔ Wien Kaiserebersdorf"
    )
    # A stop without a railway station keeps its name here; the display
    # drops only its "(WL)" (``without_stop_markers``).
    assert railway_station_names("REX 6: Wien Baumgarten (WL) ↔ Ebenfurth") == (
        "REX 6: Wien Baumgarten (WL) ↔ Ebenfurth"
    )
    assert railway_station_names("Wien Bhf. Meidling (WL)X") == "Wien Bhf. Meidling (WL)X"


def test_cached_lone_stop_title_is_rederived() -> None:
    from src.feed_build.cache_filters import _post_filter_oebb

    item = {
        "source": "ÖBB",
        "title": "Wien Bhf. Meidling (WL)",
        "description": "Wegen einer Stellwerkstörung am Bahnhof waren in Wien Meidling "
        "Bahnhof (U) bis 08:09 Uhr keine Fahrten möglich.",
        "guid": "x",
    }
    (out,) = _post_filter_oebb([item])
    assert out["title"] == "Wien Meidling"
