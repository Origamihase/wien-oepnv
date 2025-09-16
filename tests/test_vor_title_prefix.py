import re
from datetime import datetime, timezone

import src.build_feed as build_feed
import src.providers.vor as vor


def test_title_has_line_prefix():
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


def test_vor_description_keeps_extra_lines():
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
    _, xml_item = build_feed._emit_item(items[0], now, {})

    desc_match = re.search(
        r"<description><!\[CDATA\[(.*?)\]\]></description>",
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
    assert desc_html == content_html

    desc_lines = desc_html.split("<br/>")
    assert desc_lines[0] == "Ersatzverkehr zwischen Floridsdorf und Praterstern."
    assert desc_lines[1] == "Linien: S1"
    assert (
        desc_lines[2]
        == "Betroffene Haltestellen: Wien Floridsdorf, Wien Praterstern"
    )
    assert desc_lines[-1] == "Seit 15.07.2023"
