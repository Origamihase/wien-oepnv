"""Every item carries the same image of our own.

Michael, 19.10.2026: the red Wiener-Linien background on the TV slides does
not look good, the grey one does. EasySignage shows the og:image of the item
link when an item has no image of its own, and wienerlinien.at/betriebsinfo
serves either a missing "image.png" (grey) or the red WL logo (red) at random.
An image of our own keeps every slide the same grey.

Michael, 23.10.2026: the grey becomes a woodchip wallpaper ("Raufaser") in
Full HD, in the same grey. The image is a near-white woodchip wall from
scripts/make_feed_item_image.py; under EasySignage's dark layer (white becomes
#7d7d7d) its median stays that grey.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageStat

import src.build_feed as bf

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
IMAGE = Path(__file__).resolve().parents[1] / "docs" / "assets" / "feed-item-raufaser.jpg"


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
        "type": "image/jpeg",
    }
    media = elem.findall(f"{{{bf.MEDIA_NS}}}content")
    assert len(media) == 1
    assert media[0].get("url") == bf._ITEM_IMAGE_URL
    assert media[0].get("type") == "image/jpeg"
    assert media[0].get("medium") == "image"


def test_the_image_file_matches_the_feed_attributes() -> None:
    data = IMAGE.read_bytes()
    assert len(data) == bf._ITEM_IMAGE_BYTES
    assert data[:3] == b"\xff\xd8\xff"
    with Image.open(IMAGE) as image:
        assert image.format == "JPEG"
        assert image.size == (bf._ITEM_IMAGE_WIDTH, bf._ITEM_IMAGE_HEIGHT)
    assert bf._ITEM_IMAGE_URL.endswith("/assets/" + IMAGE.name)


def test_the_image_is_a_near_white_grey_woodchip_wall() -> None:
    """Grey without a tint, mostly white, with visible relief.

    Under EasySignage's dark layer white becomes the grey Michael chose, so
    the median must stay (almost) white for the slides to keep that grey,
    and the spread must show the woodchip relief without drowning the text.
    """
    with Image.open(IMAGE) as image:
        rgb = image.convert("RGB")
    red, green, blue = (ImageStat.Stat(band).mean[0] for band in rgb.split())
    assert abs(red - green) < 1 and abs(green - blue) < 1
    grey = rgb.convert("L")
    stat = ImageStat.Stat(grey)
    assert 248 <= stat.mean[0] <= 253
    assert 4 <= stat.stddev[0] <= 14
    assert stat.median[0] >= 250
