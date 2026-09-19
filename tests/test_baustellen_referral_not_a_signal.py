"""The Stadt-Wien referral is not an ÖPNV mention (audit 2026-09-19, F.1).

Every Stadt-Wien roadworks description that touches a bus or tram route
closes with a sentence that tells the reader to ask somebody else::

    Nähere Informationen zu den betroffenen öffentlichen Verkehrsmittel
    sind der Auskunft der Wiener Linien GmbH & Co KG zu entnehmen.

Since 2026-09-18 the feed emitter drops that sentence from the summary,
because it names no line and no stop. The relevance gate, however, still
counted it as *the* ÖPNV mention: on the 2026-09-19 cache, 5 of 22 sites
reached the feed on this sentence alone, and over the preceding seven days
four of them held one of the ten slots in 151 of 338 published revisions —
each time showing a street name with nothing about transit underneath
(``Ruthnergasse Kreuzung Justgasse``, place 10 on 2026-09-19).

One definition now serves both decisions: what says nothing about ÖPNV on
the display says nothing about ÖPNV at the gate. These tests pin the gate
side; ``test_wl_referral_boilerplate.py`` pins the display side.

Mutations checked against this file (each one caught, by the test named):

* ``mentions_oepnv`` searches the raw text again →
  ``test_referral_alone_is_not_a_mention`` and the ingestion test.
* ``\\s*`` before ``öffentlichen`` reverts to ``\\s+`` →
  ``test_the_glued_kennedybruecke_wording_is_recognised``.
* ``transit_text`` skips ``repair_glued_words`` →
  ``test_the_glued_naehere_informationen_wording_is_recognised``.
* ``oepnv_lead`` goes back to the raw vocabulary regex →
  ``test_oepnv_lead_prefers_the_sentence_that_names_a_line``.
"""

from __future__ import annotations

from typing import Any

import pytest

from src import build_feed
from src.providers import baustellen
from src.providers.baustellen import (
    REFERRAL_BOILERPLATE_RE,
    is_transit_relevant,
    mentions_oepnv,
    oepnv_lead,
    transit_text,
)

# The wordings, verbatim from ``cache/baustellen_d438c3/events.json``
# (2026-09-19). The last two carry the upstream's lost spaces.
_ZU_DEN = (
    "Nähere Informationen zu den betroffenen öffentlichen Verkehrsmittel "
    "sind der Auskunft der Wiener Linien GmbH & Co KG zu entnehmen."
)
_ZUR_UMLEITUNG = (
    "Nähere Informationen zur Umleitung sowie Haltestellenverlegung der "
    "betroffenen öffentlichen Verkehrsmittel sind der Auskunft der Wiener "
    "Linien GmbH & Co KG zu entnehmen."
)
_ZUR_VERLEGUNG = (
    "Nähere Informationen zur Haltestellenverlegungen der betroffenen "
    "öffentlichen Verkehrsmittel sind der Auskunft der Wiener Linien "
    "GmbH & Co KG zu entnehmen."
)
_GLUED_NAEHERE = (
    "Der Fußgängerverkehr kann aufrecht gehalten werden.NähereInformationen "
    "zu den betroffenen öffentlichen Verkehrsmittel sind der Auskunft der "
    "Wiener Linien GmbH & CoKG zu entnehmen."
)
_GLUED_KENNEDY = (
    "Die Umleitung erfolgt über: Schönbrunner Schloßstraße - "
    "SchönbrunnerSchloßbrücke - Hadikgasse.DerFußgänger- und Radverkehr kann "
    "aufrecht gehalten werden.NähereInformationen zu den "
    "betroffenenöffentlichen Verkehrsmittel sind der Auskunft der Wiener "
    "Linien GmbH & CoKG zu entnehmen. Die Kennedybrücke wird in Fahrtrichtung "
    "14. Bezirk(Hadikgasse) gesperrt."
)

# A street far from any rail Bahnhof (Ruthnergasse, 21st district).
_FAR_FROM_RAIL = {"coordinates": {"lat": 48.2712, "lon": 16.4239}}


def _site(description: str, *, title: str = "Ruthnergasse Kreuzung Justgasse") -> dict[str, Any]:
    return {"title": title, "description": description, "location": _FAR_FROM_RAIL}


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wording", [_ZU_DEN, _ZUR_UMLEITUNG, _ZUR_VERLEGUNG])
def test_referral_alone_is_not_a_mention(wording: str) -> None:
    assert mentions_oepnv(wording) is False
    assert mentions_oepnv(f"Im Baustellenbereich wird ein Fahrstreifen freigehalten. {wording}") is False


def test_a_sentence_that_names_a_line_still_counts_next_to_the_referral() -> None:
    text = f"Die Buslinie 7A wird über die Rothenhofgasse umgeleitet. {_ZU_DEN}"
    assert mentions_oepnv(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Die Buslinie 7A wird umgeleitet.",
        "Es wird lediglich die bestehende Bushaltestelle bei der Rabengasse verlegt.",
        "Für den Neubau der U-Bahnstation der U2 Pilgramgasse wird die Rechte Wienzeile gesperrt.",
        "betrifft die öffentlichen Verkehrsmittel",
    ],
)
def test_own_words_about_transit_are_untouched(text: str) -> None:
    assert mentions_oepnv(text) is True
    assert transit_text(text) == text


def test_the_glued_naehere_informationen_wording_is_recognised() -> None:
    # Upstream lost the space in "NähereInformationen"; the repair restores it
    # before the referral pattern runs, so the sentence is still removed.
    assert mentions_oepnv(_GLUED_NAEHERE) is False
    assert "entnehmen" not in transit_text(_GLUED_NAEHERE)


def test_the_glued_kennedybruecke_wording_is_recognised() -> None:
    # "betroffenenöffentlichen" is a lowercase collision the word repair
    # cannot see; the pattern tolerates the missing space itself.
    assert mentions_oepnv(_GLUED_KENNEDY) is False
    assert "Die Kennedybrücke wird in Fahrtrichtung" in transit_text(_GLUED_KENNEDY)


def test_transit_text_is_empty_for_empty_input() -> None:
    assert transit_text("") == ""
    assert mentions_oepnv("") is False


def test_referral_only_site_far_from_rail_is_not_relevant() -> None:
    assert is_transit_relevant(_site(_ZU_DEN)) is False


def test_referral_only_site_at_a_bahnhof_stays_relevant_on_geography() -> None:
    # Wien Floridsdorf: the geographic ground is independent of the text.
    site = _site(_ZU_DEN, title="Franz-Jonas-Platz")
    site["location"] = {"coordinates": {"lat": 48.2571, "lon": 16.4003}}
    assert is_transit_relevant(site) is True


# ---------------------------------------------------------------------------
# The lead sentence
# ---------------------------------------------------------------------------


def test_oepnv_lead_prefers_the_sentence_that_names_a_line() -> None:
    text = (
        f"Die Zufahrt zu den Stellplätzen ist möglich. {_ZU_DEN} "
        "Die Buslinie 7A wird in diesem Zeitraum über die Rothenhofgasse umgeleitet."
    )
    led = oepnv_lead(text)
    assert led.startswith("Die Buslinie 7A wird")
    # Nothing is lost, only reordered.
    assert _ZU_DEN in led and "Die Zufahrt zu den Stellplätzen ist möglich." in led


def test_oepnv_lead_leaves_a_referral_only_text_in_place() -> None:
    text = f"Im Baustellenbereich wird ein Fahrstreifen freigehalten. {_ZU_DEN}"
    assert oepnv_lead(text) == text


# ---------------------------------------------------------------------------
# Wiring: one definition, both call sites
# ---------------------------------------------------------------------------


def test_the_emitter_and_the_gate_share_one_pattern() -> None:
    assert build_feed._WL_REFERRAL_BOILERPLATE_RE is REFERRAL_BOILERPLATE_RE
    assert build_feed._WL_REFERRAL_BOILERPLATE_RE is baustellen.REFERRAL_BOILERPLATE_RE


def test_the_ingestion_gate_drops_a_referral_only_site(monkeypatch: pytest.MonkeyPatch) -> None:
    # Through ``update_baustellen_cache.main()``: the live fetch is replaced
    # by the six 2026-09-19 sites plus one that names a line, the cache write
    # is captured. Only the site that names a line may reach the cache.
    from scripts import update_baustellen_cache as updater

    sites = [
        _site(f"Der Fußgängerverkehr kann aufrecht gehalten werden. {_ZU_DEN}", title="Märzstraße 49"),
        _site(_GLUED_KENNEDY, title="Kennedybrücke"),
        _site(f"Die Zufahrt ist möglich. {_ZUR_UMLEITUNG}", title="Erzherzog-Karl-Straße"),
        _site(f"Es kommt zu Behinderungen. {_ZU_DEN}", title="Donaufelder Straße 79 bis 137"),
        _site(f"Die Frauenstiftgasse wird gesperrt. {_ZUR_VERLEGUNG}", title="Frauenstiftgasse"),
        _site(f"Im Baustellenbereich wird ein Fahrstreifen freigehalten. {_ZU_DEN}"),
        _site("Die Buslinie 7A wird in diesen Zeitraum über die Rothenhofgasse umgeleitet.", title="Neilreichgasse"),
    ]
    written: list[tuple[str, list[dict[str, Any]]]] = []

    def fake_fetch_layers(data_url: str, timeout: int) -> list[dict[str, Any]]:
        return [dict(site) for site in sites]

    def capture_cache(provider: str, items: list[dict[str, Any]]) -> None:
        written.append((provider, items))

    monkeypatch.setattr(updater, "_fetch_layers", fake_fetch_layers)
    monkeypatch.setattr(updater, "write_cache", capture_cache)

    assert updater.main() == 0
    assert written and written[0][0] == "baustellen"
    assert [item["title"] for item in written[0][1]] == ["Neilreichgasse"]


def test_the_feed_side_gate_drops_a_cached_referral_only_site() -> None:
    cached = [
        {**_site(_ZU_DEN), "guid": "referral-only", "source": "Stadt Wien"},
        {**_site("Die Buslinie 7A wird umgeleitet.", title="Neilreichgasse"), "guid": "names-a-line", "source": "Stadt Wien"},
    ]
    kept = build_feed._post_filter_baustellen(cached)
    assert [item["guid"] for item in kept] == ["names-a-line"]
