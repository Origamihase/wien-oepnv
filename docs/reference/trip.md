---
title: "GET /trip"
description: "Ausführliche Parameterliste für Trip-Anfragen inklusive Via-Optionen, Produktfiltern und Rückgabestruktur der VAO ReST API."
---

# GET /trip

## Kurzbeschreibung
Berechnet Verbindungen zwischen zwei Ortspunkten für ÖV- und IV-Modalitäten. Der Service unterstützt IDs und Koordinaten sowie zahlreiche Filteroptionen (Produktklassen, Via-Orte, Echtzeit, Meta-Profile).

## Grundlegende Parameter

| Parameter | Pflicht | Werte | Beschreibung |
| --- | --- | --- | --- |
| `accessId` | ja | Zeichenkette | Access-ID für die Authentifizierung. |
| `originId` / `destId` | ja\* | Zeichenkette | Start- und Ziel-ID aus `location.name` oder `location.nearbystops`. |
| `originCoordLat` / `originCoordLong` | ja\* | Dezimalgrad | Koordinaten, wenn keine IDs angegeben sind (siehe Kapitel 5.2). |
| `destCoordLat` / `destCoordLong` | ja\* | Dezimalgrad | Zielkoordinaten, wenn keine IDs verwendet werden. |
| `via` | nein | `viaId|waittime||products` | Komplexe Struktur für mehrere Via-Orte; mehrere Einträge mit `;` trennen (Kapitel 11.7). |
| `viaId` / `viaWaitTime` | nein | Zeichenkette / Minuten | Einfache Via-Variante; bei Nutzung von `via` nicht erforderlich. |
| `groupFilter` | nein | z. B. `API_OEV`, `API_CAR` | Wählt Mono- oder intermodale Profile (Kapitel 11.2). |
| `products` | nein | Dezimalwert | Bitmaske der ÖV-Produktklassen (Kapitel 5.8). |
| `categories` | nein | Betreiber-/Kategoriecodes | Filtert nach Linienkategorien (z. B. `categories=CAT` oder `!CAT`). |

\* Mindestens IDs oder Koordinaten müssen gesetzt werden.

## Zeitliche Steuerung

| Parameter | Pflicht | Werte | Beschreibung |
| --- | --- | --- | --- |
| `date` | nein | `YYYY-MM-DD` | Abfahrtsdatum (Kapitel 5.3). |
| `time` | nein | `HH:MM[:SS]` | Abfahrtszeit. |
| `searchForArrival` | nein | `0`/`1`, Standard `0` | Sucht nach Ankunftszeit statt Abfahrt. |
| `numF` / `numB` | nein | `1`–`6` / `0`–`3` | Anzahl der Verbindungen nach bzw. vor der Suchzeit (Summe ≤ 6). |
| `context` | nein | Werte aus `scrB`/`scrF` | Ermöglicht Scrolling in ältere bzw. spätere Verbindungen. |

## Optionale Funktionen

| Parameter | Zweck |
| --- | --- |
| `minChangeTime`, `maxChangeTime`, `addChangeTime`, `maxChange` | Steuerung von Umstiegszeiten (Kapitel 5.3).
| `poly`, `polyEnc` | Aktivieren Polylinien bzw. Google-encoded Ausgabe. |
| `passlist`, `showPassingPoints` | Liefert zusätzliche Zwischenhaltestellen. |
| `originWalk`, `originBike`, `destWalk`, `destBike` | Steuern Vor- und Nachläufe für Fuß- bzw. Radwege. |
| `totalMeta`, `totalCar`, `mobilityProfile` | Aktivieren Meta-Profile für IV-Routing (Kapitel 11.5/11.6). |
| `bikeCarriage` | Bevorzugt Verbindungen mit Fahrradmitnahme. |
| `rtMode` | Echtzeitmodus (`SERVER_DEFAULT` oder `OFF`). |
| `economic` | Aktiviert den „ökonomischen“ Suchmodus. |
| `includeDrt` | Schließt liniengebundene Bedarfsverkehre aus (`0`) oder ein (`1`, Standard). |
| `tariff` | Aktiviert (`1`) oder deaktiviert (`0`) die Tarifausgabe. |

Weitere Parameter (z. B. für detaillierte Rad-/Auto-Profile) sind im Handbuch Kapitel 11.1 beschrieben und bei Bedarf als „TBD – siehe PDF“ zu betrachten.

## Antwort

- Enthält eine Liste von `Trip`-Elementen mit Fahrten. Jede Fahrt besitzt `JourneyDetailRef` zur Abfrage von Linienverläufen.
- `ctxRecon` ermöglicht den späteren Zugriff über den Reconstruction-Service.
- Bei aktivierter Passliste werden Zwischenhalte (`Stop`) mit Echtzeit und Gleisinformationen geliefert.

## Fehlercodes

- TBD – siehe PDF, Kapitel 20.

## Beispiel

```bash
curl -G "${VOR_BASE_URL}/trip" \
  --data-urlencode "accessId=${VOR_ACCESS_ID}" \
  --data-urlencode "originId=490118400" \
  --data-urlencode "destId=490190301" \
  --data-urlencode "date=2025-05-22" \
  --data-urlencode "time=08:15" \
  --data-urlencode "numF=3" \
  -H "Accept: application/json"
```
