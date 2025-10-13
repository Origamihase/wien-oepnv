# VOR API Status

Stand: 2025-10-13T22:36:16Z

* `VOR_ACCESS_ID` fällt auf den dokumentierten Standardwert `VAO` zurück.
* Es sind keine `VOR_STATION_IDS` konfiguriert.
* Der Provider `src/providers/vor.py` beendet `fetch_events()` daher weiterhin sofort ohne eine Anfrage an die VOR API zu senden.
