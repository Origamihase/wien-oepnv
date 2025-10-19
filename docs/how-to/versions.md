---
title: "API-Versionen prüfen"
description: "Leitfaden, um aktive VAO ReST API-Versionen über den Versions-Endpunkt abzurufen und Änderungen nachzuverfolgen."
---

# API-Versionen prüfen

Dieser Leitfaden zeigt, wie die aktiven VAO ReST API-Versionen per `${VOR_VERSIONS}` abgefragt werden.

## Voraussetzungen

- Zugriff auf die Repository-Secrets als Umgebungsvariablen (`VOR_ACCESS_ID`, `VOR_VERSIONS`).
- `curl` zum Senden von HTTP-Anfragen.

## Schritte

1. **Umgebungsvariablen setzen**
   ```bash
   export VOR_ACCESS_ID="${VOR_ACCESS_ID}"
   export VOR_VERSIONS="${VOR_VERSIONS}"
   ```
2. **Versionen abfragen**
   ```bash
   curl -sS "${VOR_VERSIONS}" \
     -H "Accept: application/json" \
     -H "Authorization: Bearer ${VOR_ACCESS_ID}"
   ```
3. **Antwort interpretieren**
   - Die JSON-Antwort listet verfügbare Versionen (z. B. `v1.11.0`) samt Gültigkeitszeitraum (`validFrom`, `validUntil`).
   - Änderungen sind dem Handbuch Kapitel 3 zu entnehmen; bei Unsicherheiten: „TBD – siehe PDF“.

## Nächste Schritte

- Version in `${VOR_BASE_URL}` aktualisieren.
- Referenzkapitel der PDF konsultieren, falls Parameter zwischen Versionen variieren.
