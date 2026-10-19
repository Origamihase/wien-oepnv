"""Every item carries the same plain white image.

Michael, 19.10.2026: the red Wiener-Linien background on the TV slides does
not look good, the grey one does. EasySignage shows the og:image of the item
link when an item has no image of its own, and wienerlinien.at/betriebsinfo
serves either a missing "image.png" (grey) or the red WL logo (red) at random.
A white image of our own keeps every slide the same grey.
"""

from __future__ import annotations

import struct
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import src.build_feed as bf

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
IMAGE = Path(__file__).resolve().parents[1] / "docs" / "assets" / "feed-item.png"


def _item(source: str) -> dict[str, object]:
    return {
        "source": source,
        "category": "Störung",
        "title": "U1: Verspätungen",
        "description": "Unregelmäßige Intervalle.",
        "guid": f"guid-{source}",
        "pubDate": NOW - timedelta(minutes=5),
        "starts_at": NOW - timedelta(minutes=5),
    }


@pytest.mark.parametrize("lang", ["de", "en"])
@pytest.mark.parametrize(
    "source", ["Wiener Linien", "ÖBB", "Stadt Wien – Baustellen", "VOR/VAO", "unbekannt"]
)
def test_every_item_carries_the_plain_image(source: str, lang: str) -> None:
    _, elem, _ = bf._emit_item(_item(source), NOW, {}, lang=lang)  # type: ignore[arg-type]
    enclosures = elem.findall("enclosure")
    assert len(enclosures) == 1
    assert enclosures[0].attrib == {
        "url": bf._ITEM_IMAGE_URL,
        "length": str(bf._ITEM_IMAGE_BYTES),
        "type": "image/png",
    }
    media = elem.findall(f"{{{bf.MEDIA_NS}}}content")
    assert len(media) == 1
    assert media[0].get("url") == bf._ITEM_IMAGE_URL
    assert media[0].get("medium") == "image"


def test_the_image_file_matches_the_feed_attributes() -> None:
    data = IMAGE.read_bytes()
    assert len(data) == bf._ITEM_IMAGE_BYTES
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (bf._ITEM_IMAGE_WIDTH, bf._ITEM_IMAGE_HEIGHT)
    assert bf._ITEM_IMAGE_URL.endswith("/assets/" + IMAGE.name)


def test_the_image_is_plain_white() -> None:
    data = IMAGE.read_bytes()
    pos, idat = 8, b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        if kind == b"IDAT":
            idat += data[pos + 8 : pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)
    rows = [raw[i : i + 1 + bf._ITEM_IMAGE_WIDTH] for i in range(0, len(raw), 1 + bf._ITEM_IMAGE_WIDTH)]
    assert len(rows) == bf._ITEM_IMAGE_HEIGHT
    assert all(row[1:] == b"\xff" * bf._ITEM_IMAGE_WIDTH for row in rows)
