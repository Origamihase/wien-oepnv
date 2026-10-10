"""Every drop in ``data/raw/<source>/verworfen.json`` names its real cause.

Until 2026-10-10 the ÖBB fetch wrote "nicht Wien-relevant" for every
dropped message, lifts included ("Personenlift in Wien Leopoldau", found
by the check of 2026-10-09), and WL wrote "nur Aufzug/Fahrtreppe" for the
folding ramps of the trains ("U1: Klapprampensperre am 10.10.2026"). One
cause now carries one label in both files (``src/utils/text.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from defusedxml import ElementTree as ET

import src.providers.oebb as oebb_provider
from src.providers import wl_fetch
from src.utils import raw_capture
from src.utils.text import DROP_ACCESS, DROP_LIFT, DROP_RAMP, facility_drop_label


def _item(n: int, title: str, desc: str) -> str:
    return f"""<item>
<title><![CDATA[ {title} ]]></title>
<link>https://fahrplan.oebb.at/bin/help.exe/dn?tpl=showmap_external&amp;{n}</link>
<guid isPermaLink="false">https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&amp;mapType=TRACKINFO&amp;90000{n}</guid>
<pubDate>Sat, 10 Oct 2026 08:00:00 +0200</pubDate>
<description><![CDATA[ {desc} ]]></description>
</item>"""


# Real ÖBB titles (``data/raw/oebb`` history, the lift in the shape ÖBB
# sends it) plus a constructed weather warning and closed access: ÖBB sent
# neither so far.
_OEBB_CASES = [
    (
        "Technische Störung des Personenlift in Wien Leopoldau Bahnhst (U) - Bahnsteig 1/2: Wien Leopoldau Bahnhst (U)",
        "Der Personenlift ist außer Betrieb.",
        DROP_LIFT,
    ),
    ("Wien Meidling: Aufgang Bahnsteig 1 gesperrt", "Bitte benützen Sie den Aufgang 2.", DROP_ACCESS),
    ("Sturmwarnung Wien", "Im Raum Wien ist mit Sturmböen zu rechnen.", "nur Wetterwarnung"),
    ("Linz Hbf ↔ Wels Hbf", "Wegen Bauarbeiten zwischen Linz Hbf und Wels Hbf …", "nicht Wien-relevant"),
]


def test_oebb_drop_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raw_capture, "RAW_ROOT", tmp_path / "raw")
    monkeypatch.setenv(raw_capture.RAW_CAPTURE_ENV, "1")
    kept = ("Wien Meidling ↔ Wien Floridsdorf", "Wegen einer Fahrzeugstörung zwischen Wien Meidling und Wien Floridsdorf …")
    body = "".join(
        _item(n, title, desc) for n, (title, desc, _) in enumerate([*_OEBB_CASES, (*kept, None)], start=1)
    )
    rss = f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>ÖBB</title>{body}</channel></rss>'
    monkeypatch.setattr(oebb_provider, "_fetch_xml", lambda url, timeout=25: ET.fromstring(rss))

    items = oebb_provider.fetch_events()

    assert len(items) == 1
    drops = json.loads((tmp_path / "raw" / "oebb" / "verworfen.json").read_text(encoding="utf-8"))
    assert {d["titel"]: d["grund"] for d in drops["verworfen"]} == {
        title: label for title, _, label in _OEBB_CASES
    }


@pytest.mark.parametrize(
    ("title", "label"),
    [
        ("U1: Klapprampensperre am 10.10.2026", DROP_RAMP),
        ("U4: Klapprampensperre am 16.10.2026", DROP_RAMP),
        ("U2: Rathaus - Kein Aufzug am Bahnsteig Richtung Seestadt", DROP_LIFT),
        ("U4: Pilgramgasse - Kein Aufzug am Bahnsteig Richtung Hütteldorf", DROP_LIFT),
        ("U3: Rolltreppe außer Betrieb", DROP_LIFT),
        ("U1: Nestroyplatz, Aufgangssperre ab 12.10.2026", DROP_ACCESS),
        ("U1: Verkehrsunfall", None),
    ],
)
def test_wl_facility_labels(title: str, label: str | None) -> None:
    assert wl_fetch._facility_drop_reason(title) == label


@pytest.mark.parametrize(
    ("title", "label"),
    [
        ("Klapprampe gesperrt: Wien Hbf", DROP_RAMP),
        ("Aufzug defekt: Wien Hbf", DROP_LIFT),
        ("Personenlift außer Betrieb: Stockerau", DROP_LIFT),
    ],
)
def test_oebb_facility_labels_match_wl(title: str, label: str) -> None:
    assert oebb_provider._drop_reason(title, "") == label


def test_mixed_ramp_and_lift_is_a_lift() -> None:
    assert facility_drop_label(["klapprampe", "aufzug"]) == DROP_LIFT
    assert facility_drop_label(["Klapprampensperre"]) == DROP_RAMP
    assert facility_drop_label([]) == DROP_LIFT
