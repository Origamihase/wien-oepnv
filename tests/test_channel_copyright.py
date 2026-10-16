"""Both feeds name their sources once, in the channel ``<copyright>``.

Michael decided on 2026-10-16 that the code stays MIT while the notice
texts remain with their sources (see ``NOTICE.md``). The line lives on
channel level only, so items, and with them the EasySignage displays,
stay unchanged.
"""

from __future__ import annotations

import datetime

import pytest
from defusedxml import ElementTree as ET

from src import build_feed

_NOW = datetime.datetime(2026, 10, 16, 12, 0, 0, tzinfo=datetime.UTC)


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("de", ("MIT-Lizenz", "Wiener Linien", "data.wien.gv.at", "CC BY 4.0", "© ÖBB", "NOTICE.md")),
        ("en", ("MIT licence", "Wiener Linien", "data.wien.gv.at", "CC BY 4.0", "© ÖBB", "NOTICE.md")),
    ],
)
def test_channel_copyright_names_sources(lang: str, expected: tuple[str, ...]) -> None:
    root = ET.fromstring(build_feed._make_rss([], _NOW, {}, lang=lang))
    channel = root.find("channel")
    assert channel is not None
    copyrights = channel.findall("copyright")
    assert len(copyrights) == 1
    text = copyrights[0].text or ""
    for part in expected:
        assert part in text
    # ÖBB texts are not openly licensed: CC BY must not be attached to them,
    # and VOR/VAO feeds no item.
    assert "ÖBB (CC" not in text
    assert "VOR" not in text and "VAO" not in text

