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
    # ÖBB texts are not openly licensed: CC BY must not be attached to them.
    assert "ÖBB (CC" not in text


@pytest.mark.parametrize(
    ("lang", "basis"),
    [
        ("de", "Stammstrecken-Daten: Grundlage ist der VAO-„Start“-Zugang der Verkehrsauskunft Österreich VAO GmbH."),
        ("en", "S-Bahn trunk-line data: based on the VAO “Start” access of Verkehrsauskunft Österreich VAO GmbH."),
    ],
)
def test_channel_copyright_names_vao_start_as_basis(lang: str, basis: str) -> None:
    """VAO "Start" contract, point 5.3: the service names the VAO "Start" access as its basis.

    Michael chose on 2026-10-17 to carry the sentence in both feeds. VAO is a
    basis, not a licensor of notice texts: the Stammstrecke item is our own
    text computed from VAO departure data, so no licence is attached to it.
    """
    text = build_feed._channel_metadata(lang)["copyright"]
    assert basis in text
    assert "VAO (CC" not in text



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


@pytest.mark.parametrize("lang", ["de", "en"])
def test_channel_copyright_gives_geonetz_its_own_licence(lang: str) -> None:
    """The ÖBB-Infrastruktur GeoNetz is CC BY 3.0 AT, not CC BY 4.0.

    Its dataset page on data.oebb.at (read 17.10.2026) names CC BY 3.0
    Österreich and the attribution "Datenquelle: ÖBB-Infrastruktur AG" with
    the name linked to infrastruktur.oebb.at.
    """
    text = build_feed._channel_metadata(lang)["copyright"]
    assert "Datenquelle: ÖBB-Infrastruktur AG (https://infrastruktur.oebb.at/de/" in text
    assert "GeoNetz, CC BY 3.0 AT, https://creativecommons.org/licenses/by/3.0/at/deed.de" in text
