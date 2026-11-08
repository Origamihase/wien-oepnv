"""WL's own two-part stop names stay German in the EN feed (KI-Wächter, 2026-11-08).

"Haltestellenverlegung der Linie 68A in Richtung Laaer Berg, Nordosteingang"
read "towards Laaer Berg, northeast entrance": the directory names the stop
"Wien Kurpark Nordosteingang (WL)", WL "Laaer Berg, Kurpark Nordosteingang",
and its direction drops "Kurpark". Only "Laaer Berg" was shielded.
"""

from __future__ import annotations

from src.feed_build.masking import _mask_entities, _station_entity_names, _wl_stop_names


def _shielded(text: str) -> list[str]:
    return sorted(_mask_entities(text)[1].values())


def test_the_68a_direction_is_shielded_whole() -> None:
    text = "Haltestellenverlegung der Linie 68A in Richtung Laaer Berg, Nordosteingang."
    assert "Laaer Berg, Nordosteingang" in _shielded(text)


def test_wl_names_of_the_directory_are_shielded() -> None:
    names = set(_station_entity_names())
    assert {"Laaer Berg, Kurpark Nordosteingang", "Atzgersdorf, Friedhof", "Liesing, Feuerwache"} <= names


def test_the_short_form_keeps_its_place_in_front() -> None:
    # "Nordosteingang" or "Friedhof" alone is running text, not a name.
    assert _shielded("Der Friedhof ist geöffnet.") == []
    assert _shielded("Der Nordosteingang ist gesperrt.") == []


def test_markers_and_one_part_names_give_no_form() -> None:
    entry = {
        "wl_stops": [
            {"name": "Oper, Karlsplatz →"},
            {"name": "Parlament, U Volkstheater (Bedarfs-Hst.)"},
            {"name": "Amongasse"},
            {"name": "A, Bahnstraße"},
        ]
    }
    assert _wl_stop_names(entry) == {
        "Oper, Karlsplatz",
        "Parlament, U Volkstheater",
        "Parlament, Volkstheater",
    }
