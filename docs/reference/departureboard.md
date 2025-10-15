# GET /DepartureBoard

## Kurzbeschreibung
Gibt eine Abfahrtstafel für eine Station oder einen Steig zurück. Enthält Abfahrtszeiten, Echtzeitinformationen, Linienangaben und eine `JourneyDetailRef` zur Detailabfrage.

## Request

| Parameter | Pflicht | Werte | Beschreibung |
| --- | --- | --- | --- |
| `accessId` | ja | Zeichenkette | Access-ID für die Authentifizierung. |
| `id` | ja | Zeichenkette | Haltestellen- oder Steig-ID aus Location-Services. |
| `date` | nein | `YYYY-MM-DD` | Datum der Abfrage (Standard: aktuelle Serverzeit). |
| `time` | nein | `HH:MM` | Startzeitpunkt der Anzeige. |
| `duration` | nein | `0`–`1439`, Standard `60` | Zeitspanne in Minuten (max. 24 h). |
| `products` | nein | Dezimalwert | Filtert auf bestimmte ÖV-Produktklassen. |
| `lines` | nein | Liste | Begrenzt auf bestimmte Liniencodes (`,` als Trennzeichen, `!` für Ausschluss). |
| `maxJourneys` | nein | Zahl | Maximalanzahl der gelieferten Fahrten (weiche Grenze). |
| `rtMode` | nein | `SERVER_DEFAULT` (Standard), `OFF` | Echtzeitmodus. |
| `type` | nein | `DEP_EQUIVS`, `DEP_STATION`, `DEP_MAST` | Steuert den Umfang der äquivalenten Haltestellen. |

## Antwort

- Die Antwort enthält `Departure`-Elemente mit Soll- und Echtzeit (`time`, `rtTime`) sowie Richtung (`direction`).
- `ProductAtStop` liefert Kategorie, Liniennummer und Betreiber, sofern verfügbar.
- Jede Abfahrt beinhaltet eine `JourneyDetailRef` zur Weiterverarbeitung im JourneyDetail-Service.

## Scrolling

Zur Verlängerung des Zeitraums wird die Abfahrtszeit der letzten Fahrt um eine Minute erhöht und dieselbe Abfrage erneut ausgeführt (siehe Kapitel 13.2).

## Fehlercodes

- TBD – siehe PDF, Kapitel 20.

## Beispiel

```bash
curl -G "${VOR_BASE_URL}/DepartureBoard" \
  --data-urlencode "accessId=${VOR_ACCESS_ID}" \
  --data-urlencode "id=490118400" \
  --data-urlencode "date=2025-05-22" \
  --data-urlencode "time=17:00" \
  --data-urlencode "duration=60" \
  -H "Accept: application/json"
```
