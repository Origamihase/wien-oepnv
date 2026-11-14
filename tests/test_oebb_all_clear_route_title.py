"""An ÖBB all-clear keeps its label when its title is built from the route.

ÖBB on 14.11.2026 at 15:50: "Update 7 (14.11.2026 15:50) Aufhebung
Streckenunterbrechung: Wien Floridsdorf Bahnhof (U) - Wien Jedlersdorf
Bahnhst", text "Wegen einer Oberleitungsstörung waren zwischen Korneuburg
Bahnhof und Wien Jedlersdorf Bahnhst bis 15:34 Uhr keine Fahrten möglich."
The title built from the route in the text dropped the label and read
"Wien Jedlersdorf ↔ Korneuburg", like a running disruption; the deferral
of all-clears behind the field (operator decision 2026-09-25) never saw it,
and the item stood on place 5. So did every Wien all-clear with a route in
its text since 04.10.2026 (35 ÖBB versions).
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement

import src.build_feed as bf
from src.feed_build.cache_filters import _post_filter_oebb
from src.providers import oebb
from src.providers.oebb_route_title import _apply_route_title

_TITLE = (
    "Update 7 (14.11.2026 15:50) Aufhebung Streckenunterbrechung: "
    "Wien Floridsdorf Bahnhof (U) - Wien Jedlersdorf Bahnhst"
)
_DESC = (
    "14.11.2026<br/><br/>Wegen einer Oberleitungsstörung <b>waren</b> zwischen Korneuburg "
    "Bahnhof und Wien Jedlersdorf Bahnhst <b>bis </b><b>15:34 Uhr</b> keine Fahrten möglich. "
    "Planen Sie derzeit noch bis zu<b> 10 Minuten </b>mehr Reisezeit ein.<br>Wir bitten um "
    "Entschuldigung. "
)
_EXPECTED = "Aufhebung Streckenunterbrechung: Wien Jedlersdorf ↔ Korneuburg"


def _xml_item(title: str, desc: str) -> Element:
    item = Element("item")
    for tag, text in (
        ("title", title),
        ("description", desc),
        ("guid", "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&927095"),
        ("link", "https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&tpl=showmap_external&"),
        ("pubDate", "Sat, 14 Nov 2026 15:50:09 +0100"),
    ):
        SubElement(item, tag).text = text
    return item


def test_provider_keeps_the_label_in_front_of_the_route() -> None:
    built = oebb._build_item_from_xml(_xml_item(_TITLE, _DESC))
    assert isinstance(built, dict)
    assert built["title"] == _EXPECTED
    assert bf._is_all_clear(built)


def test_route_title_keeps_the_label_behind_a_line_prefix() -> None:
    desc = (
        "Wegen einer Weichenstörung waren zwischen <b>Wien Meidling Bahnhof (U)</b> und "
        "<b>Wien Liesing Bahnhof</b> bis 19:36 Uhr nur eingeschränkt Fahrten möglich."
    )
    title = _apply_route_title("S 3: Aufhebung Verkehrseinschränkung: Wien Meidling ↔ Wien Liesing", desc)
    assert title == "S 3: Aufhebung Verkehrseinschränkung: Wien Meidling ↔ Wien Liesing"


def test_a_running_disruption_gets_no_label() -> None:
    desc = _DESC.replace("<b>waren</b>", "sind").replace("<b>bis </b><b>15:34 Uhr</b>", "bis 17:00 Uhr")
    title = _apply_route_title("Streckenunterbrechung: Wien Floridsdorf ↔ Wien Jedlersdorf", desc)
    assert title == "Wien Jedlersdorf ↔ Korneuburg"


def test_cached_all_clear_keeps_the_label_through_the_build() -> None:
    item = {
        "source": "ÖBB",
        "category": "Störung",
        "title": _EXPECTED,
        "description": _DESC,
        "link": "",
        "guid": "https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&mapType=TRACKINFO&927095",
    }
    kept = _post_filter_oebb([item])
    assert [it["title"] for it in kept] == [_EXPECTED]
    assert bf._defer_all_clear_items([dict(item), {"title": "U4: Betriebsstörung"}])[-1]["title"] == _EXPECTED
