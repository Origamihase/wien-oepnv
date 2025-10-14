# VOR API Status

Stand: 2025-10-14T20:48:32Z

* `python scripts/test_vor_api.py` spiegelt einen kompletten Produktivlauf wider und ruft zwei StationBoards ab. Beide Versuche
  enden mit HTTP 401 (`API_AUTH`), sodass weiterhin keine Störungsdaten eintreffen. Der Tageszähler in
  `data/vor_request_count.json` steigt trotzdem von `count = 6` auf `count = 8` (Δ = 2), weil jeder HTTP-Versuch unmittelbar in
  `_fetch_stationboard` gezählt wird.
* `python scripts/check_vor_auth.py` liefert nach wie vor HTTP 401 mit dem Fehlercode `API_AUTH`, erhöht den Zähler aber nicht,
  da das Skript keinen Provider-Aufruf startet.
* Für produktive Abrufe sind weiterhin gültige Zugangsdaten über `VOR_ACCESS_ID` bzw. `VAO_ACCESS_ID` erforderlich.
