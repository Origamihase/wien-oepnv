
import pytest
import responses
from src.providers.oebb import fetch_events

# Full description from cache. "Wien" mention removed to ensure filter drops it.
# Changed encoding to UTF-8 to handle special chars safely in test environment.
XML_CONTENT = """<?xml version="1.0" encoding="utf-8"?>
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
Der Zug REX41(321) kann von Ceské Velenice nach Schwarzenau im Waldviertel Bahnhof nicht fahren.
Wir haben für Sie einen Schienenersatzverkehr zwischen Ceské Velenice und Gmünd NÖ eingerichtet.
Ab Gmünd NÖ haben Sie die Möglichkeit, den Zug REX41(2119) Richtung ... zu nehmen.
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
    Ensures that a route between two Outer stations (Gmünd NÖ <-> Ceske Velenice) is filtered out.
    This requires 'Gmünd NÖ' and 'Ceske Velenice' to be in stations.json (as non-Vienna, non-Pendler).
    """
    from unittest.mock import patch, MagicMock

    # We need to mock DNS and PinnedHTTPSAdapter.send because request_safe bypasses session adapters for HTTPS.
    # This renders responses.add ineffective for request_safe calls on HTTPS.

    with patch("src.utils.http._resolve_hostname_safe") as mock_resolve:
        mock_resolve.return_value = [(2, 1, 6, '', ('93.184.216.34', 443))]

        with patch("src.utils.http.PinnedHTTPSAdapter.send") as mock_send:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.is_redirect = False
            # Use utf-8 encoding to match XML declaration
            encoded_content = XML_CONTENT.encode("utf-8")
            mock_resp.content = encoded_content
            mock_resp.headers = {"Content-Type": "application/xml"}
            mock_resp.iter_content.return_value = [encoded_content]
            mock_resp.raw = MagicMock()
            # Mock getpeername to return safe IP
            mock_resp.raw.connection.sock.getpeername.return_value = ('93.184.216.34', 443)
            mock_resp.raw._connection.sock.getpeername.return_value = ('93.184.216.34', 443)

            # Context manager support: request_safe uses 'with adapter.send(...) as r:'
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.__exit__.return_value = None

            mock_send.return_value = mock_resp

            events = fetch_events()

    # Assert item is filtered out
    assert len(events) == 0, f"Expected 0 events, got {len(events)}. The item should be filtered out by Strict Route Filter."

if __name__ == "__main__":
    pytest.main([__file__])
