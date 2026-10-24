---
title: "API-Versionen prüfen"
description: "Leitfaden, um die aktuell genutzte VAO ReST API-Version zu identifizieren und Änderungen zwischen Versionen nachzuverfolgen."
---

# API-Versionen prüfen

Dieser Leitfaden zeigt, wie sich die im Projekt aktive VAO ReST API-Version identifizieren lässt und wie sich aktive Versionen am VAO-Endpunkt prüfen lassen.

## Hinweis zur Namensgebung

Im Projekt-Code (`src/providers/vor.py`) sind `VOR_VERSION` (primär) und `VOR_VERSIONS` (Fallback-Alias) **beide Versions-Strings**, die in die Basis-URL eingebaut werden (z. B. `v1.13.0`), **keine URLs**. Standard ist `DEFAULT_VERSION` im Code.

## Welche Versionen die VAO betreibt

Die VAO listet ihre Versionen mit Endpunkt und Abschaltdatum unter
<https://routenplaner.verkehrsauskunft.at/vao/restproxy> (Handbuch Kapitel 3.2.2 und 4.2;
der Aufruf braucht keine Access-ID und zählt nicht zum Tageskontingent):

```bash
curl -s https://routenplaner.verkehrsauskunft.at/vao/restproxy
```

Stand 24.10.2026: v1.9.0 bis 31.08.2026, v1.11.0 bis 31.05.2027, v1.12.0 bis
30.08.2027, **v1.13.0 bis 30.06.2028** (im Projekt aktiv seit 24.10.2026). Das
Handbuch vom 28.01.2026 kennt v1.13.0 noch nicht; die Schnittstelle jeder Version
beschreiben `…/restproxy/<version>/api-doc` (OpenAPI) und `…/<version>/<service>?wadl`,
ebenfalls ohne Access-ID.

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
   - Strukturelle Änderungen kommen laut Handbuch Kapitel 3.1 nur mit neuen Versionen; Unterschiede zeigt der Vergleich von OpenAPI und WADL (unten).

## Version anheben

Vor dem Abschaltdatum der aktiven Version (heute v1.13.0, 30.06.2028):

1. OpenAPI und WADL von alter und neuer Version vergleichen, mindestens für
   `departureBoard`: die Request-Parameter aus
   `scripts/update_stammstrecke_hbf.py` und die gelesenen Felder (`date`, `time`,
   `rtDate`, `rtTime`, `track`/`rtTrack`, `cancelled`, `direction`, `Product`).
2. Dieselbe `/departureBoard`-Abfrage an beide Versionen schicken und durch den
   echten Parser laufen lassen; Abfahrten und Stammstrecken-Beobachtungen müssen
   gleich sein. Jede Abfrage zählt zum Tageskontingent (100 pro Tag).
3. `DEFAULT_VERSION` in `src/providers/vor.py` und `tests/test_vor_default_version.py`
   anheben. Die Umgebungsvariable `VOR_VERSION` wirkt nur lokal: Seit 2026-10-16
   reicht kein Workflow sie weiter, ein Repository-Secret dieses Namens bliebe
   wirkungslos (`tests/test_workflow_secrets.py` lässt CI scheitern, sobald ein
   Workflow ein nicht registriertes Secret übergibt).

So lief der Umstieg von v1.11.0 auf v1.13.0 am 24.10.2026: Die Schnittstelle kam
nur um Felder dazu (`DestinationStop`, `OriginStop`, `globalId`, `LineInfo`,
`himId`), die Abfrage an alle drei Versionen lieferte dieselben 28 Abfahrten und
dieselben Beobachtungen.
