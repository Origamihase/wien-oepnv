# VOR API Status

Stand: 2025-10-14T20:38:18Z

* `python scripts/check_vor_auth.py` liefert weiterhin HTTP 401 mit dem Fehlercode `API_AUTH`, wenn nur der Fallback-Zugang `VAO`
  verwendet wird. Es werden daher aktuell keine Störungsdaten von der VOR-API empfangen.
* Die Abfrage des Auth-Checks verändert den Tageszähler (`data/vor_request_count.json`) nicht: Der Stand blieb nach dem Test bei
  `2025-10-14` und `count = 2`, weil das Skript keinen Provider-Aufruf ausführt, der den Zähler über `save_request_count` erhöht.
* Für produktive Abrufe sind weiterhin gültige Zugangsdaten über `VOR_ACCESS_ID` bzw. `VAO_ACCESS_ID` erforderlich. Erst dann
  würde jede tatsächliche DepartureBoard-Anfrage den Zähler hochzählen, da `save_request_count` bei jedem HTTP-Versuch innerhalb
  von `_fetch_stationboard` ausgeführt wird.
