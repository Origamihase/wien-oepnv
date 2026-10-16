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



@pytest.mark.parametrize(
    ("lang", "modified"),
    [("de", "gekürzt und bearbeitet"), ("en", "shortened, edited and machine-translated")],
)
def test_channel_copyright_meets_cc_by_attribution(lang: str, modified: str) -> None:
    """CC BY 4.0, section 3(a)(1): say the texts were modified and link the licence.

    The feed shortens and reformats every notice and the English feed is a
    machine translation, so the modification note is required. A feed reader
    cannot resolve a bare "NOTICE.md", hence the full URL.
    """
    text = build_feed._channel_metadata(lang)["copyright"]
    assert modified in text
    assert "https://creativecommons.org/licenses/by/4.0/" in text
    assert "https://github.com/Origamihase/wien-oepnv/blob/main/NOTICE.md" in text
