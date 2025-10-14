# VOR API Test (2025-10-15)

- **Tool:** `scripts/check_vor_auth.py`
- **Station:** Default `430470800`
- **Ergebnis:** HTTP 401 mit Fehlercode `API_AUTH`; keine Betriebsdaten empfangen.
- **Zähler:** Der Aufruf verwendet nur das Auth-Testskript und erhöht den Tageszähler `data/vor_request_count.json` nicht.
- **Hinweis:** Ein gültiger Zugangsschlüssel liegt als Secret `VOR_ACCESS_ID` vor und muss vor dem nächsten Testlauf in die Umgebung geladen werden, damit die API Anfragen akzeptiert.
