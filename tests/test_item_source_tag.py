"""Every item links to its own operator and names its source in ``<source>``.

Michael, 17.10.2026: "Meldungen von der ÖBB sollen zu ÖBB verlinken und eine
Quellenangabe als Tag beinhalten. Meldungen der Wiener Linien sollen zu den
Wiener Linien verlinken [...] Das Selbe für Baustellenmeldungen der Stadt
Wien u.s.w." Before, the Baustellen items linked to a data.gv.at catalogue
page that answers 404 since the portal moved, and the Stammstrecke item to a
WL page that only redirected to the WL start page.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET

import pytest

import src.build_feed as bf
from src.feed import stammstrecke

NOW = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
OEBB_INFO = "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&tpl=showmap_external&"


def _item(source: str, link: str | None, category: str = "Störung") -> dict[str, object]:
    item: dict[str, object] = {
        "source": source,
        "category": category,
        "title": "U6: Signalstörung",
        "description": "Unregelmäßige Intervalle.",
        "guid": f"guid-{source}-{link}",
        "pubDate": NOW - timedelta(minutes=5),
        "starts_at": NOW - timedelta(minutes=5),
    }
    if link is not None:
        item["link"] = link
    return item


def _emit(item: dict[str, object], lang: str = "de") -> ET.Element:
    _, elem, _ = bf._emit_item(item, NOW, {}, lang=lang)  # type: ignore[arg-type]
    return elem


@pytest.mark.parametrize(
    ("source", "link", "want_link", "want_name", "want_url"),
    [
        (
            "Wiener Linien",
            "https://www.wienerlinien.at/betriebsinfo",
            "https://www.wienerlinien.at/betriebsinfo",
            "Wiener Linien",
            "https://www.wienerlinien.at/betriebsinfo",
        ),
        (
            "ÖBB",
            "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&tpl=showmap_external&",
            OEBB_INFO,
            "ÖBB",
            OEBB_INFO,
        ),
        (
            # The dead catalogue link still sits in the Baustellen cache.
            "Stadt Wien – Baustellen",
            "https://www.data.gv.at/katalog/en/dataset/baustellen-wien-verkehrsbeeintraechtigungen",
            "https://www.wien.gv.at/verkehr/baustellen",
            "Stadt Wien",
            "https://www.wien.gv.at/verkehr/baustellen",
        ),
        (
            # Old cached Stammstrecke link: a WL page for ÖBB trains.
            "VOR/VAO",
            "https://www.wienerlinien.at/web/wienerlinien/oeffis-stoerungen-strecke",
            OEBB_INFO,
            "VOR/VAO",
            "https://www.verkehrsauskunft.at/start",
        ),
    ],
)
def test_item_links_to_its_operator_and_tags_its_source(
    source: str, link: str, want_link: str, want_name: str, want_url: str
) -> None:
    elem = _emit(_item(source, link, "Baustelle" if "Baustellen" in source else "Störung"))
    assert elem.findtext("link") == want_link
    tag = elem.find("source")
    assert tag is not None
    assert tag.text == want_name
    assert tag.get("url") == want_url


def test_link_on_another_host_falls_back_to_the_operator_page() -> None:
    elem = _emit(_item("ÖBB", "https://www.wienerlinien.at/betriebsinfo"))
    assert elem.findtext("link") == OEBB_INFO


def test_operator_subdomain_link_is_kept() -> None:
    link = "https://infrastruktur.oebb.at/de/projekte"
    assert _emit(_item("ÖBB", link)).findtext("link") == link


def test_lookalike_host_is_not_the_operator() -> None:
    elem = _emit(_item("Wiener Linien", "https://evilwienerlinien.at/x"))
    assert elem.findtext("link") == "https://www.wienerlinien.at/betriebsinfo"


def test_english_feed_names_the_city_in_english() -> None:
    elem = _emit(_item("Stadt Wien – Baustellen", None, "Baustelle"), lang="en")
    tag = elem.find("source")
    assert tag is not None and tag.text == "City of Vienna"


def test_unknown_source_keeps_link_and_gets_no_tag() -> None:
    elem = _emit(_item("Plugin", "https://example.org/x"))
    assert elem.findtext("link") == "https://example.org/x"
    assert elem.find("source") is None


def test_producers_already_link_to_their_operator() -> None:
    import importlib

    assert stammstrecke.EVENT_LINK == OEBB_INFO
    from src.providers import oebb

    assert oebb.OEBB_INFO_URL == OEBB_INFO
    baustellen = importlib.import_module("scripts.update_baustellen_cache")
    assert baustellen.DEFAULT_INFO_URL == "https://www.wien.gv.at/verkehr/baustellen"
