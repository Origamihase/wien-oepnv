import re
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

import src.build_feed as build_feed
import src.providers.vor as vor


def _emit_item_str(item, now, state):
    ident, elem, replacements = build_feed._emit_item(item, now, state)
    ET.register_namespace("content", "http://purl.org/rss/1.0/modules/content/")
    xml_str = ET.tostring(elem, encoding="unicode")
    for ph, content in replacements.items():
        xml_str = xml_str.replace(ph, content)
    return ident, xml_str


def test_title_has_line_prefix(monkeypatch):
    # Mock station info so it doesn't get filtered out
    from src.utils.stations import StationInfo
    monkeypatch.setattr("src.providers.vor.station_info", lambda x: StationInfo(name=None, in_vienna=True, pendler=False))

    payload = {
        "DepartureBoard": {
            "Messages": {
                "Message": [
                    {
                        "id": "1",
                        "act": "true",
                        "head": "Baustelle …",
                        "products": {
                            "Product": [
                                {
                                    "catOutS": "S",
                                    "displayNumber": "1",
                                }
                            ]
                        },
                    }
                ]
            }
        }
    }
    items = vor._collect_from_board("123", payload)
    assert len(items) == 1
    assert items[0]["title"] == "S1: Baustelle …"


def test_vor_description_keeps_extra_lines(monkeypatch):
    # Mock station info
    from src.utils.stations import StationInfo
    monkeypatch.setattr("src.providers.vor.station_info", lambda x: StationInfo(name="Wien", in_vienna=True, pendler=False))

    payload = {
        "DepartureBoard": {
            "Messages": {
                "Message": [
                    {
                        "id": "2",
                        "act": "true",
                        "head": "Ersatzverkehr",
                        "text": "Ersatzverkehr zwischen Floridsdorf und Praterstern.",
                        "sDate": "2023-07-15",
                        "sTime": "00:00",
                        "products": {
                            "Product": [
                                {
                                    "catOutS": "S",
                                    "displayNumber": "1",
                                }
                            ]
                        },
                        "affectedStops": {
                            "Stops": {
                                "Stop": [
                                    {"name": "Wien Praterstern"},
                                    {"name": "Wien Floridsdorf"},
                                ]
                            }
                        },
                    }
                ]
            }
        }
    }

    items = vor._collect_from_board("123", payload)
    assert len(items) == 1
    now = datetime(2023, 7, 20, 12, 0, tzinfo=timezone.utc)
    _, xml_item = _emit_item_str(items[0], now, {})

    desc_match = re.search(
        r"<description>(.*?)</description>",
        xml_item,
        re.DOTALL,
    )
    content_match = re.search(
        r"<content:encoded><!\[CDATA\[(.*?)\]\]></content:encoded>",
        xml_item,
        re.DOTALL,
    )

    assert desc_match and content_match
    desc_html = desc_match.group(1)
    content_html = content_match.group(1)

    content_lines = content_html.split("<br/>")

    assert "Ersatzverkehr zwischen Floridsdorf und Praterstern." in desc_html
    assert "Ersatzverkehr zwischen Floridsdorf und Praterstern." in content_html

    # Verify the plain text description format
    assert desc_html == "Ersatzverkehr zwischen Floridsdorf und Praterstern. [Seit 15.07.2023]"

    # Verify the HTML layout in content:encoded
    assert len(content_lines) == 2
    assert content_lines[0] == "Ersatzverkehr zwischen Floridsdorf und Praterstern."
    assert content_lines[1] == "[Seit 15.07.2023]"
