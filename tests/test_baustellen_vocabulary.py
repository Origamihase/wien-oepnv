"""Words that looked like an ÖPNV mention at the Baustellen gate but are none.

Prüfung vom 2026-10-06 über alle 69 Baustellen der Cache-Historie seit Mai.
The sentences are verbatim from ``cache/baustellen_d438c3/events.json``
(git history).
"""

from __future__ import annotations

import pytest

from src.providers.baustellen import REFERRAL_BOILERPLATE_RE, mentions_oepnv, oepnv_lead

# Favoritenstraße, a pedestrian zone: in the feed 2026-05-24/29 on the
# operator's name alone.
_OPERATOR_AS_BUILDER = (
    "Die Instandsetzungs- und Umgestaltungarbeiten in der Fußgängerzone "
    "Favoritenstraße werden nach den derzeit laufenden Abdichtungsarbeiten der "
    "Wiener Linien GmbH & Co KG an der Decke des U Bahn-Bauwerks bei Freihaltung "
    "entsprechend breiter Flächen für den FußgängerInnen- bzw. Lieferverkehr "
    "durchgeführt."
)
# Aumannplatz.
_OPERATOR_TRACK_WORKS = (
    "Im Kreuzungsbereich bzw. im Schatten der Gleisbauarbeiten der Wiener Linien "
    "GmbH & Co KG erfolgen die Rohrlegungsarbeiten."
)
# Matzleinsdorfer Platz: the road, not the transit.
_ROAD_AREA = (
    "Seit 10. Jänner 2023 wird der Verkehr nunmehr rechts an dem neu in der Mitte "
    "der öffentlichen Verkehrsfläche eingerichteten Baustellenbereich vorbeigeführt."
)
# Johnstraße (in the feed 2026-07-02/06): the referral with the verb in the singular.
_REFERRAL_SINGULAR = (
    "Nähere Informationen zur Umleitung sowie Haltestellenverlegung des "
    "betroffenen öffentlichen Verkehrsmittels ist der Auskunft der Wiener Linien "
    "GmbH & Co KG zu entnehmen."
)


@pytest.mark.parametrize(
    "text", [_OPERATOR_AS_BUILDER, _OPERATOR_TRACK_WORKS, _ROAD_AREA, _REFERRAL_SINGULAR]
)
def test_not_an_oepnv_mention(text: str) -> None:
    assert not mentions_oepnv(text)


@pytest.mark.parametrize(
    "text",
    [
        # Eßlinger Hauptstraße 96.
        "Der Busverkehr der Wiener Linien GmbH & Co KG wird über den Kreisverkehr bei der Kreuzung geführt.",
        # Atzgersdorfer Straße.
        "Die Haltestelle des betroffenen öffentlichen Verkehrsmittels wird von Atzgersdorfer Straße ONr.42 nach ONr.46 verlegt.",
        "Die Linie 19A wird über Favoritenstraße umgeleitet.",
        "Der öffentliche Verkehr wird umgeleitet.",
        "Die Buslinie 7A wird in diesen Zeitraum über die Rothenhofgasse umgeleitet.",
    ],
)
def test_still_an_oepnv_mention(text: str) -> None:
    assert mentions_oepnv(text)


def test_singular_referral_leaves_the_summary() -> None:
    text = "Die Johnstraße wird zur Einbahn. " + _REFERRAL_SINGULAR
    assert oepnv_lead(text) == text
    assert REFERRAL_BOILERPLATE_RE.sub("", text).strip() == (
        "Die Johnstraße wird zur Einbahn."
    )
