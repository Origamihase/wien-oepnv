---
title: "API-Versionen prüfen"
description: "Leitfaden, um die aktuell genutzte VAO ReST API-Version zu identifizieren und Änderungen zwischen Versionen nachzuverfolgen."
---

# API-Versionen prüfen

Dieser Leitfaden zeigt, wie sich die im Projekt aktive VAO ReST API-Version identifizieren lässt und wie sich aktive Versionen am VAO-Endpunkt prüfen lassen.

## Hinweis zur Namensgebung

Im Projekt-Code (`src/providers/vor.py`) sind `VOR_VERSION` (primär) und `VOR_VERSIONS` (Fallback-Alias) **beide Versions-Strings**, die in die Basis-URL eingebaut werden (z. B. `v1.11.0`), **keine URLs zu einem „/versions"-Endpunkt**. Eine zentrale „Liste aller aktiven API-Versionen" stellt die VAO-API selbst nicht als REST-Endpoint bereit; verbindliche Quelle bleibt das offizielle PDF-Handbuch (`docs/reference/manuals/Handbuch_VAO_ReST_API_latest.pdf`, Kapitel 3).

## Voraussetzungen

- Zugriff auf die Repository-Secrets als Umgebungsvariablen (`VOR_ACCESS_ID`, `VOR_BASE_URL`).
- `curl` zum Senden von HTTP-Anfragen.

## Schritte

1. **Im Projekt aktive Version inspizieren**

   ```bash
   python -c "from src.providers.vor import VOR_BASE_URL, VOR_VERSION; print(VOR_BASE_URL, VOR_VERSION)"
   ```

   Ausgabe ist die zur Build-Zeit aufgelöste Basis-URL plus der Versions-String, der in den eigentlichen API-Pfad eingebaut wird.

2. **Metadaten am VAO-Endpunkt abfragen** (`/datainfo` liefert Produkt- und Betreiber-Metadaten):

   ```bash
   curl -G "${VOR_BASE_URL}datainfo" \
     --data-urlencode "accessId=${VOR_ACCESS_ID}" \
     -H "Accept: application/json"
   ```

   Die Antwort enthält keinen expliziten Versionswert, aber die Produkt- und Operator-Listen geben Aufschluss darüber, welche Daten in der aktuellen Version verfügbar sind. Details siehe [`docs/reference/datainfo.md`](../reference/datainfo.md).

3. **Antwort interpretieren**
   - Änderungen zwischen API-Versionen sind dem Handbuch Kapitel 3 zu entnehmen; bei Unsicherheiten: „TBD – siehe PDF".

## Nächste Schritte

- Falls eine neue Version aktiviert werden soll, `VOR_VERSION` als Umgebungsvariable setzen (Default ist im Code gepinnt).
- Referenzkapitel der PDF konsultieren, falls Parameter zwischen Versionen variieren.
