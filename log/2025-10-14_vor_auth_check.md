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

## Testlauf 3 (15.10.2025, 16:20 UTC)

- **Tool:** `scripts/check_vor_auth.py`
- **Ergebnis:** Authentifizierung fehlgeschlagen.
- **HTTP-Status:** `401`
- **Fehlercode:** `API_AUTH`
- **Fehlermeldung:** `access denied for VAO on departureBoard identified by departureBoard`
- **Request-URL:** `https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/departureboard?format=json&id=430470800&accessId=***`

Der Test zeigt weiterhin, dass der Laufzeitkontext keinen gültigen Token erhält: `VOR_ACCESS_ID` fällt noch immer auf den Fallback
`VAO` zurück, wodurch der Server jede Anfrage mit HTTP `401` beantwortet. Die Authentifizierung ist damit unverändert nicht
funktionsfähig.

## Nachbereitung (16.10.2025)

- Die VOR-Komponenten lesen `VOR_ACCESS_ID` jetzt vor jedem Request erneut aus den Secrets ein. Dadurch greifen nachgeladene
  Zugangsdaten sofort – ohne einen Modul-Reload – und die Prüfskripte verwenden keinen veralteten Fallback mehr.【F:src/providers/vor.py†L399-L438】【F:scripts/check_vor_auth.py†L92-L111】
- Ein erneuter Live-Test ist notwendig, sobald der produktive Schlüssel hinterlegt ist. Mit `scripts/check_vor_auth.py` lässt
  sich die Authentifizierung dann unmittelbar validieren.
