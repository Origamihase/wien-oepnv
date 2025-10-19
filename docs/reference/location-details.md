---
title: "GET /location.details"
description: "Endpunktbeschreibung für detaillierte Ortspunktausgaben inklusive Ausstattung, Sharing-Angeboten und optionalen Wetterdaten."
---

# GET /location.details

## Kurzbeschreibung
Liefert Detailinformationen zu einem Ortspunkt aus vorherigen Location-Services, darunter Haltestellenausstattung, Sharing-Fahrzeuge, Ladestationen oder Wetterdaten.

## Request

| Parameter | Pflicht | Werte | Beschreibung |
| --- | --- | --- | --- |
| `accessId` | ja | Zeichenkette | Access-ID für die Authentifizierung. |
| `id` | ja | Zeichenkette | Identifier des Ortspunktes (Haltestelle, POI, Fahrzeug, Ladestation, VMOBIL-Box). |
| `weather` | nein | `true`/`false`, Standard `false` | Aktiviert die Wetterprognose (2 h Prognosezeitraum). |

## Antwort

- `StopLocation`-Elemente enthalten Produktlisten (`productAtStop`) für Haltestellen.
- POIs und Sharing-Fahrzeuge liefern Zusatzangaben in `LocationNotes`, z. B. Betreiber, Verfügbarkeit oder Tarifinformationen.
- Bei Ladestellen werden Steckertypen und weitere Attribute laut E-Control-Daten ausgegeben.

## Fehlercodes

- TBD – siehe PDF, Kapitel 20.

## Beispiel

```bash
curl -G "${VOR_BASE_URL}/location.details" \
  --data-urlencode "accessId=${VOR_ACCESS_ID}" \
  --data-urlencode "id=A=1@O=Graz%20Hauptbahnhof@X=15417507@Y=47072481@U=81@L=460304000@" \
  --data-urlencode "weather=true" \
  -H "Accept: application/json"
```
