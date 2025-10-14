# VOR API Authentifizierungstest (2025-10-14)

- **Tool:** `scripts/check_vor_auth.py`
- **Ergebnis:** Authentifizierung fehlgeschlagen.
- **HTTP-Status:** `401`
- **Fehlercode:** `API_AUTH`
- **Fehlermeldung:** `access denied for VAO on departureBoard identified by departureBoard`
- **Request-URL:** `https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/departureboard?format=json&id=430470800&accessId=VAO`

Das System verwendet weiterhin den historischen Fallback-Schlüssel `VAO`. Dieser wird vom Backend abgelehnt, sodass keine Daten
abgerufen werden können. Ein gültiger Zugang muss in den Umgebungsvariablen `VOR_ACCESS_ID` bzw. `VAO_ACCESS_ID` hinterlegt
werden.
