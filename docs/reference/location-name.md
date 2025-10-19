---
title: "GET /location.name"
description: "Referenz für die Namenssuche nach Ortspunkten mit Filteroptionen für Typen, Produkte und Standortgewichtung."
---

# GET /location.name

## Kurzbeschreibung
Sucht Ortspunkte (Adressen, Haltestellen, POIs) anhand eines Namenseingabe und liefert passende Treffer zur weiteren Verwendung in anderen Services (z. B. Trip, DepartureBoard). Die Ergebnisse werden standardmäßig nach bester Übereinstimmung sortiert und im Element `LocationList` zurückgegeben.

## Request

| Parameter | Pflicht | Werte | Beschreibung |
| --- | --- | --- | --- |
| `accessId` | ja | Zeichenkette | Access-ID für die Authentifizierung. |
| `input` | ja | Zeichenkette | Suchbegriff für den Ortspunkt. |
| `maxNo` | nein | `1`–`1000`, Standard `10` | Maximale Anzahl der gelieferten Ortspunkte. |
| `type` | nein | `ALL`, `S`, `A`, `P`, `SA`, `SP`, `AP` | Filtert nach Ortspunkttypen (Alle, Stationen, Adressen, POIs oder Kombinationen). |
| `products` | nein | Dezimalwert | Bitmaske der zu berücksichtigenden ÖV-Produktklassen (siehe Handbuch Kapitel 6). |
| `coordLong` / `coordLat` | nein | Dezimalgrad | Koordinate zur standortbasierten Gewichtung (ab V1.5.0). |
| `r` | nein | `1`–`10000`, Standard `1000` | Radius in Metern für die standortbasierte Auflösung. |
| `filterMode` | nein | `DIST_PERI`, `EXCL_PERI`, `SLCT_PERI` | Steuerung der Ergebnismenge bei Nutzung eines Koordinatenradius. |
| `refineId` | nein | Zeichenkette | Referenz-ID aus einer früheren Anfrage zur weiteren Verfeinerung. |
| `poolId` | nein | `103`, `104`, Kombination mit `,` oder Ausschluss mit `!` | Filtert Adresspools (in- oder ausländisch). |
| `stations` | nein | `41`–`49` | Beschränkt die Ausgabe auf ÖV-Teilnetze (Bundesländer). |
| `meta` | nein | Meta-Filter | Aktiviert optionale Filter, z. B. für POI-Kategorien (Kapitel 8.3). |

## Antwort

- Die Antwortstruktur entspricht `LocationList` mit `CoordLocation`-Elementen je Treffer.
- `refinable="true"` kennzeichnet Orte, die über `refineId` weiter auflösbar sind.
- Zusatzinformationen erscheinen innerhalb von `LocationNotes`, z. B. POI-Kategorien.

## Fehlercodes

- TBD – siehe PDF, Kapitel 20.

## Beispiel

```bash
curl -G "${VOR_BASE_URL}/location.name" \
  --data-urlencode "accessId=${VOR_ACCESS_ID}" \
  --data-urlencode "input=Hauptbahnhof" \
  --data-urlencode "maxNo=5" \
  -H "Accept: application/json"
```
