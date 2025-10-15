# VOR ReST API – Dokumentation (Version 2025-05-22)

Diese Dokumentation bündelt die wichtigsten Fakten aus dem offiziellen Handbuch zur VAO ReST API und verweist auf weiterführende Detailkapitel. Die vollständige Referenz liegt als PDF im Repository: [Handbuch_VAO_ReST_API_2025-08-11.pdf](docs/Handbuch_VAO_ReST_API_2025-08-11.pdf).

## Schnellstart

```bash
# Zur Laufzeit setzen (Werte stammen aus Repository-Secrets/ENV)
export VOR_ACCESS_ID="${VOR_ACCESS_ID}"
export VOR_BASE_URL="${VOR_BASE_URL}"      # inkl. Versionspfad, z. B. /restproxy/<version>
export VOR_VERSIONS="${VOR_VERSIONS}"      # Endpoint mit Infos zu verfügbaren API-Versionen

# Verfügbare Versionen abfragen (GET)
curl -sS "${VOR_VERSIONS}" \
  -H "Accept: application/json" \
  -H "Authorization: Bearer ${VOR_ACCESS_ID}" || true

# Beispiel: Aufruf eines dokumentierten Endpunkts (Parameter anpassen)
# Platzhalter; exakte Pfade/Parameter NUR verwenden, wenn eindeutig aus der PDF extrahiert
curl -G -sS "${VOR_BASE_URL}/location.name" \
  --data-urlencode "accessId=${VOR_ACCESS_ID}" \
  --data-urlencode "input=Hauptbahnhof" \
  -H "Accept: application/json" || true
```

## Authentifizierung & Sicherheit

- Die VAO ReST API verwendet einen Access Token (`accessId`) zur Authentifizierung. Dieser wird als Query-Parameter übertragen und darf ausschließlich aus sicheren Umgebungsvariablen stammen.
- Secrets (Access-ID, Basis-URL, Versionen-Endpunkt) gehören nicht in Repository-Dateien, Issue-Tracker oder Protokolle. Auf Testsystemen sind sie vor jeder Abfrage per `export` zu setzen.
- Beispielskripte nutzen ausschließlich Umgebungsvariablen und setzen keine Klartextwerte.

## Versionierung

- Die verfügbaren API-Versionen liefert der Endpoint `${VOR_VERSIONS}`. Die Antwort enthält aktive Versionen inkl. Gültigkeitszeitraum (siehe Handbuch Kapitel 3.1).
- Für Requests empfiehlt das Handbuch, den gewünschten Versionspfad (z. B. `/restproxy/v1.11.0`) in `${VOR_BASE_URL}` zu hinterlegen. Änderungen an verfügbaren Versionen sind über `${VOR_VERSIONS}` prüfbar.
- Detailinformationen zu Release-Zyklen und Betriebsdauer finden sich in der PDF (Kapitel 3).

## Referenz & Beispiele

- **Referenz**: [docs/reference/](docs/reference/) – Parameter, Antwortstrukturen und Beispielaufrufe für dokumentierte Endpoints.
- **How-tos**: [docs/how-to/](docs/how-to/) – Schritt-für-Schritt-Anleitungen, z. B. für die Versionsabfrage.
- **Beispiele**: [docs/examples/](docs/examples/) – Shell-Snippets auf Basis von Umgebungsvariablen.

## Weitere Hinweise

- Für zusätzliche Services, Fehlercodes und Sonderfälle siehe das Handbuch (Kapitel 5–20).
- Unklare oder nicht eindeutig bestätigte Angaben sind in dieser Dokumentation als „TBD – siehe PDF“ gekennzeichnet.
