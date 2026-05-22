"""Regression test for Bug 35A (generic-word title prefix mis-classified as line code).

User feedback: a WL meldung surfaced with no line attribution::

    Title: Einstieg: Brünner Straße 31-31A
    Description: Bauarbeiten Einstieg: Brünner Straße 31-31A
                 [Am 22.05.2026]

User asked: "Diese Meldung sagt leider recht wenig aus, da die Linie
fehlt." → without a line code the meldung is useless to a subscriber
who relies on the feed for line-attribution.

Root cause
==========
``_post_filter_wl`` carries a "drop Störung items without a line
prefix" guard. Pre-fix the guard used a permissive regex
``^[A-Za-z0-9]+(?:/[A-Za-z0-9]+)*\\s*:\\s+\\S`` that matched ANY
alphanumeric word followed by ``:``. ``Einstieg:`` happily matched
even though ``Einstieg`` (German "boarding/entry point") is a
generic noun, not a transit line code. WL's identity
``wl|störung|L=|D=2026-05-22`` confirms the source dropped the line
attribution entirely; the title's leading ``Einstieg:`` is just an
unfortunate German prose shape.

A second real cache item ``Sperre Bahnsteig Richtung Siebenhirten``
(``L=`` empty too) suffered the same fate — its title has no colon
at all and the permissive regex didn't match, but ``Sperre`` /
``Bahnsteig`` aren't line codes either, so the item is equally
useless to the user.

Fix
===
Replace the permissive regex check with
:func:`src.providers.wl_lines._extract_prefix_lines`, which uses the
strict-line-token gate (``[A-Z]{0,4}\\d{1,3}[A-Z]?`` or single bare
uppercase letter). Generic German nouns (``Einstieg``, ``Sperre``,
``Achtung``, ``Hinweis``, ``Information``) all fail both shapes and
the item is dropped. Real line codes (``U6``, ``41E``, ``10A``,
``D``, ``S50``, ``40+41``) continue to pass.
"""

from __future__ import annotations

from typing import Any

from src.build_feed import _post_filter_wl


def _stoerung(title: str, description: str = "", guid: str = "t") -> dict[str, Any]:
    return {
        "source": "Wiener Linien",
        "category": "Störung",
        "title": title,
        "description": description,
        "link": "",
        "guid": guid,
    }


class TestGenericWordTitlePrefixDropped:
    def test_einstieg_brunner_strasse_dropped(self) -> None:
        # The user's exact reproduction.
        out = _post_filter_wl([
            _stoerung(
                "Einstieg: Brünner Straße 31-31A",
                "Bauarbeiten\nEinstieg: Brünner Straße 31-31A",
            )
        ])
        assert out == [], (
            f"Generic-word-prefix Störung item survived: {out}"
        )

    def test_sperre_bahnsteig_richtung_siebenhirten_dropped(self) -> None:
        # Second real cache item with the same L= empty signature.
        out = _post_filter_wl([
            _stoerung(
                "Sperre Bahnsteig Richtung Siebenhirten",
                "Sperre Bahnsteig Richtung Siebenhirten",
            )
        ])
        assert out == []

    def test_other_generic_german_prefixes_dropped(self) -> None:
        for title in [
            "Achtung: Sperre wegen Bauarbeiten",
            "Hinweis: Verspätung erwartet",
            "Information: Umleitung der Linie",
            "Bauarbeiten: Strecke gesperrt",
            "Veranstaltung: Demonstration im 9. Bezirk",
        ]:
            out = _post_filter_wl([_stoerung(title)])
            assert out == [], (
                f"Generic prefix Störung leaked through: {title!r}"
            )


class TestRealLinePrefixedStoerungKept:
    """The drop must not over-trigger on legitimate line-prefixed items."""

    def test_u6_kept(self) -> None:
        out = _post_filter_wl([
            _stoerung("U6: Verspätung wegen Schadhaftem Fahrzeug")
        ])
        assert len(out) == 1
        assert out[0]["title"] == "U6: Verspätung wegen Schadhaftem Fahrzeug"

    def test_tram_d_kept(self) -> None:
        # Single-letter line code (the Round 34 case).
        out = _post_filter_wl([
            _stoerung("D: Demonstration", "Linie D: Unregelmäßige Intervalle.")
        ])
        assert len(out) == 1
        assert out[0]["title"] == "D: Demonstration"

    def test_bus_compound_kept(self) -> None:
        # ``41E/10A:`` — multi-line compound with letter-suffix tokens.
        out = _post_filter_wl([
            _stoerung("41E/10A: Ersatzbus 41E hält beim 10A")
        ])
        assert len(out) == 1

    def test_pure_digit_kept(self) -> None:
        out = _post_filter_wl([_stoerung("40: Falschparker")])
        assert len(out) == 1

    def test_plus_separator_kept(self) -> None:
        out = _post_filter_wl([_stoerung("40+41: Betrieb ab Gersthof")])
        assert len(out) == 1
        # Plus separator also canonicalises to slash.
        assert out[0]["title"] == "40/41: Betrieb ab Gersthof"

    def test_s_bahn_kept(self) -> None:
        out = _post_filter_wl([_stoerung("S50: Sperre Hauptbahnhof")])
        assert len(out) == 1

    def test_night_bus_kept(self) -> None:
        out = _post_filter_wl([_stoerung("N20: Umleitung Stephansplatz")])
        assert len(out) == 1


class TestNonStoerungCategoriesUnaffected:
    """Drop guard applies ONLY to category=Störung. Other categories pass through."""

    def test_baustelle_without_line_prefix_passes(self) -> None:
        # Baustelle/Hinweis items don't need line attribution to be useful.
        item = _stoerung("Bauarbeiten Gürtel")
        item["category"] = "Baustelle"
        out = _post_filter_wl([item])
        assert len(out) == 1, "Non-Störung was dropped"

    def test_hinweis_without_line_prefix_passes(self) -> None:
        item = _stoerung("Veranstaltung am Heldenplatz")
        item["category"] = "Hinweis"
        out = _post_filter_wl([item])
        assert len(out) == 1
