# VOR API Authentifizierungstest (2025-10-14)

## Testlauf 1 (09:00 UTC)

- **Tool:** `scripts/check_vor_auth.py`
- **Ergebnis:** Authentifizierung fehlgeschlagen.
- **HTTP-Status:** `401`
- **Fehlercode:** `API_AUTH`
- **Fehlermeldung:** `access denied for VAO on departureBoard identified by departureBoard`
- **Request-URL:** `https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/departureboard?format=json&id=430470800&accessId=VAO`

Das System verwendete den historischen Fallback-Schlüssel `VAO`. Dieser wird vom Backend abgelehnt, sodass keine Daten
abgerufen werden können. Ein gültiger Zugang muss in den Umgebungsvariablen `VOR_ACCESS_ID` bzw. `VAO_ACCESS_ID` hinterlegt
werden.

## Testlauf 2 (11:30 UTC)

- **Tool:** `scripts/check_vor_auth.py`
- **Ergebnis:** Authentifizierung fehlgeschlagen.
- **HTTP-Status:** `401`
- **Fehlercode:** `API_AUTH`
- **Fehlermeldung:** `access denied for VAO on departureBoard identified by departureBoard`
- **Request-URL:** `https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/departureboard?format=json&id=430470800&accessId=***`

Auch nach dem Eintrag eines angeblich neuen Tokens meldet `src.providers.vor.VOR_ACCESS_ID` weiterhin den Fallback-Wert `VAO`.
Die Umgebungsvariable `VOR_ACCESS_ID` scheint im aktuellen Laufzeitkontext nicht gesetzt zu sein, wodurch die Authentifizierung
unverändert fehlschlägt.

## Nachbereitung (15.10.2025)

- Die VOR-Anfragen ergänzen den hinterlegten Zugangsschlüssel nun zusätzlich im HTTP-Header `Authorization: Bearer …`, sodass
  neue Sicherheitsanforderungen der API erfüllt werden, ohne dass der Token in Logs oder Testausgaben sichtbar ist.
- Sobald der gültige Schlüssel über `VOR_ACCESS_ID` bereitsteht, sollte `scripts/check_vor_auth.py` mit Exit-Code `0`
  abschließen und ein erfolgreiches `authenticated: true` melden.

## Produktivtest (20:48 UTC)

- **Tool:** `scripts/test_vor_api.py`
- **Ergebnis:** Beide StationBoard-Aufrufe liefern HTTP 401 `API_AUTH`; keine Störungsdaten empfangen.
- **Request-Zähler:** von `count = 6` auf `count = 8` gestiegen (Δ = 2) trotz fehlender Daten.
- **Hinweis:** Das Skript spiegelt die produktive Logik wider und eignet sich zur Überwachung der täglichen Request-Limits.
