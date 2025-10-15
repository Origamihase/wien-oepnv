# GET /arrivalBoard

## Kurzbeschreibung
Liefert eine Ankunftstafel mit kommenden Fahrten an einer Station oder einem Steig. Die Antwort enthält Ankunftszeiten, Echtzeitinformationen, Richtung (`origin`) sowie `JourneyDetailRef` für Detailabfragen.

## Request

| Parameter | Pflicht | Werte | Beschreibung |
| --- | --- | --- | --- |
| `accessId` | ja | Zeichenkette | Access-ID für die Authentifizierung. |
| `id` | ja | Zeichenkette | Stations- oder Steig-ID aus Location-Services; ab V1.3.3 auch `altId`. |
| `direction` | nein | Zeichenkette | Filtert Ankünfte aus einer bestimmten Richtung (ID einer vorangehenden Station). |
| `date` | nein | `YYYY-MM-DD` | Ankunftsdatum. |
| `time` | nein | `HH:MM` | Ankunftszeit. |
| `duration` | nein | `0`–`1439`, Standard `60` | Zeitfenster in Minuten. |
| `products` | nein | Dezimalwert | Filtert Produktklassen. |
| `lines` | nein | Liste | Filtert Linien (`,` getrennt, `!` für Ausschluss). |
| `maxJourneys` | nein | Zahl | Maximale Anzahl der gelieferten Fahrten (weiche Grenze). |
| `rtMode` | nein | `SERVER_DEFAULT` (Standard), `OFF` | Echtzeitmodus. |
| `type` | nein | `ARR`, `ARR_STATION`, `ARR_MAST`, `ARR_EQUIVS` | Steuert den Umfang der angezeigten Haltestellen. |

## Antwort

- Jede `Arrival` enthält Soll- und Echtzeit (`time`, `rtTime`), Richtung (`origin`) sowie optional `altId`.
- `ProductAtStop` liefert Verkehrsart, Liniennummer und Betreiber, sofern verfügbar.
- `JourneyDetailRef` verweist auf das JourneyDetail-Service.

## Scrolling

Analog zur Abfahrtstafel: letzte Ankunftszeit um eine Minute erhöhen und erneut abfragen (Kapitel 14.2).

## Fehlercodes

- TBD – siehe PDF, Kapitel 20.

## Beispiel

```bash
curl -G "${VOR_BASE_URL}/arrivalBoard" \
  --data-urlencode "accessId=${VOR_ACCESS_ID}" \
  --data-urlencode "id=490118400" \
  --data-urlencode "date=2025-05-22" \
  --data-urlencode "time=17:00" \
  --data-urlencode "duration=60" \
  -H "Accept: application/json"
```
