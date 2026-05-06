"""Audit-round-7 regression: HTML residue in _find_stations_in_text.

Bug S surfaced from the live ÖBB cache. Items like
``Bauarbeiten: Bruck/Mur Graz`` (Distant↔Distant route) used to slip
into the feed because the description carried HTML-bolded station
names::

    nach <b>Graz Hbf</b> in der Nacht von …

The single-station fall-through tokenises by whitespace + slash, so the
``</b>`` tag attached to the next token: ``Hbf<``. That residue
bypassed the ``_GENERIC_STATION_TOKENS`` filter (which only knew the
clean ``hbf`` form) and ``canonical_name("Hbf<")`` cheerfully resolved
to ``Wien Hauptbahnhof`` via the directory's alias normalisation.
``has_relevant`` therefore turned True and the message was kept.

The fix strips HTML tags and unescapes entities at the start of
``_find_stations_in_text`` so tokens are never contaminated with
markup.
"""

from __future__ import annotations

from src.providers.oebb import _find_stations_in_text, _is_relevant


class TestHtmlResidueDoesNotMatchWien:
    def test_hbf_with_closing_tag_does_not_match(self) -> None:
        text = "nach <b>Graz Hbf</b> in der Nacht"
        assert _find_stations_in_text(text) == []

    def test_bahnhof_with_closing_tag_does_not_match(self) -> None:
        text = "von <b>Bruck/Mur Bahnhof</b> nach Graz"
        assert _find_stations_in_text(text) == []

    def test_html_entity_does_not_break_match(self) -> None:
        text = "Wien&nbsp;Hauptbahnhof gesperrt"
        # Entity decoded to non-breaking space, which still tokenises fine.
        result = _find_stations_in_text(text)
        assert "Wien Hauptbahnhof" in result


class TestDistantDistantWithHtmlInDescription:
    """Real cache items that previously slipped through."""

    def test_bruck_mur_graz_dropped(self) -> None:
        title = "Bauarbeiten: Bruck/Mur Graz"
        desc = (
            "14.02.2026 - 22.11.2026<br/><br/>Wegen Bauarbeiten kann<br>"
            "von <b>Bruck/Mur Bahnhof</b> nach <b>Graz Hbf</b><br>"
            "in der Nacht von <b>14.02.</b> auf <b>15.02.2026</b>."
        )
        assert _is_relevant(title, desc) is False

    def test_voecklabruck_salzburg_dropped(self) -> None:
        title = "Bauarbeiten: Vöcklabruck/Salzburg"
        desc = (
            "18.07.2026 - 26.07.2026<br/><br/>Wegen Bauarbeiten können<br>"
            "in <b>Vöcklabruck Bahnhof</b><br>von <b>18.07.2026</b> bis "
            "<b>19.07.2026 </b>einige Fernverkehrszüge nicht halten."
        )
        assert _is_relevant(title, desc) is False


class TestVonNachAbBisRoutes:
    """Bug T: 'von X nach Y' / 'ab X bis Y' is the alternative ÖBB
    phrasing for a directional connection. Without recognising it the
    single-station fall-through accepted Wien↔Distant routes whenever
    the distant station was missing from the directory."""

    def test_ab_bis_wien_distant_dropped(self) -> None:
        assert (
            _is_relevant(
                "Bauarbeiten",
                "Strecke ab Wien Hbf bis Graz Hbf gesperrt.",
            )
            is False
        )

    def test_von_nach_wien_pendler_kept(self) -> None:
        assert (
            _is_relevant(
                "Bauarbeiten",
                "Wegen Bauarbeiten kann von Wien Hbf nach Mödling "
                "nicht gefahren werden.",
            )
            is True
        )

    def test_von_nach_distant_distant_dropped(self) -> None:
        assert (
            _is_relevant(
                "Bauarbeiten",
                "Von Bruck/Mur nach Graz nicht gefahren werden.",
            )
            is False
        )

    def test_lone_von_without_nach_kept(self) -> None:
        # No second endpoint — falls through to single-station path.
        assert _is_relevant("Bauarbeiten", "Verspätungen von Wien Hauptbahnhof") is True


class TestLineNoiseTokens:
    """Bug V: 'Linie 17', 'Linie S50' etc. should not be misread as
    implicit second endpoints in the title-residual heuristic."""

    def test_linie_token_in_title_kept(self) -> None:
        assert (
            _is_relevant("Bauarbeiten Wien Hbf — Linie 17", "Linie 17 ist betroffen.")
            is True
        )

    def test_strecke_token_in_title_kept(self) -> None:
        assert (
            _is_relevant("Bauarbeiten Wien Hbf — Strecke betroffen", "x") is True
        )
