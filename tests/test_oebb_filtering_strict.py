
import pytest
import responses
from src.providers.oebb import fetch_events

# Full description from cache, containing "Wien Franz-Josefs-Bahnhof"
XML_CONTENT = """<?xml version="1.0" encoding="ISO-8859-1"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:ext="http://oebb.at/rss/ext/1.0">
<channel>
<title>ÖBB - Streckeninfo</title>
<item>
<title>
<![CDATA[ Gmünd NÖ ↔ Ceske Velenice ]]>
</title>
<link>https://fahrplan.oebb.at/bin/help.exe/dn?L=vs_scotty&amp;tpl=showmap_external&amp;</link>
<guid isPermaLink="false">https://fahrplan.oebb.at/bin/query.exe/dn?ujm=1&amp;mapType=TRACKINFO&amp;833447</guid>
<pubDate>Mon, 02 Feb 2026 08:04:19 +0000</pubDate>
<description>
<![CDATA[ 02.03.2026 - 13.03.2026
Wegen Bauarbeiten der Tschechischen Bahnen (CD) können
zwischen Gmünd NÖ Bahnhof und Ceske Velenice
von 02.03.2026 bis 07.03.2026 und
von 09.03.2026 bis 13.03.2026
einige REX41-Züge nicht fahren.
Der Zug REX41(322/326) kann von Schwarzenau im Waldviertel Bahnhof nach Ceské Velenice nicht fahren
Ein Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet.
Der Zug REX41(321) kann von Ceské Velenice nach Schwarzenau im Waldviertel Bahnhof nicht fahren. Wir haben für Sie einen Schienenersatzverkehr zwischen Ceské Velenice und Gmünd NÖ eingerichtet. Ab Gmünd NÖ haben Sie die Möglichkeit, den Zug REX41(2119) Richtung Wien Franz-Josefs-Bahnhof zu nehmen.
ACHTUNG:
Ihre Reisezeit verlängert sich um bis zu 15 Minuten.
Anschlussverbindungen können nicht gewährleistet werden.
HINWEISE:
Fahrräder können nicht befördert werden.
Wir bitten um Entschuldigung.
Details finden Sie hier... ]]>
</description>
</item>
</channel>
</rss>
"""

@responses.activate
def test_oebb_filtering_strict_route():
    """
    Ensures that a route between two Outer stations (Gmünd NÖ <-> Ceske Velenice) is filtered out
    even if the description mentions "Wien Franz-Josefs-Bahnhof".
    This requires 'Gmünd NÖ' and 'Ceske Velenice' to be in stations.json (as non-Vienna, non-Pendler).
    """
    from src.providers import oebb
    url = oebb.OEBB_URL

    responses.add(
        responses.GET,
        url,
        body=XML_CONTENT,
        status=200,
        content_type='application/xml'
    )

    events = fetch_events()

    # Assert item is filtered out
    assert len(events) == 0, f"Expected 0 events, got {len(events)}. The item should be filtered out by Strict Route Filter."

if __name__ == "__main__":
    pytest.main([__file__])
