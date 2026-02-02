# VOR API Status

* **Status**: 🟢 Operational (HTTP 200).
* **Endpoint**: Wir nutzen `departureBoard` (nicht `trafficInfo`).
* **IDs**: Es werden HAFAS Long-IDs benötigt (Format `A=1@O=...`).
* **Rate Limit**: Das Skript beachtet strikt das Limit von **100 Requests/Tag** (nur stündliche Ausführung + Safety Checks).
* **Known Issues**: Der Parameter `type=STOP` führt bei `location.name` zu Fehlern; er muss weggelassen werden.
