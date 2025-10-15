# Trip-Abfrage durchführen

Diese Anleitung zeigt die grundlegenden Schritte, um mit der VAO ReST API eine Verbindung zu berechnen.

## 1. Ortspunkte bestimmen

1. Suche Start- und Zielort über `location.name` oder `location.nearbystops`.
2. Wähle aus der Antwort eine passende `id` oder `extId` aus (`CoordLocation`/`StopLocation`).

```bash
curl -G "${VOR_BASE_URL}/location.name" \
  --data-urlencode "accessId=${VOR_ACCESS_ID}" \
  --data-urlencode "input=Schottentor" \
  --data-urlencode "maxNo=3"
```

## 2. Trip-Service aufrufen

```bash
curl -G "${VOR_BASE_URL}/trip" \
  --data-urlencode "accessId=${VOR_ACCESS_ID}" \
  --data-urlencode "originId=<START_ID>" \
  --data-urlencode "destId=<ZIEL_ID>" \
  --data-urlencode "date=2025-05-22" \
  --data-urlencode "time=08:15" \
  --data-urlencode "numF=3"
```

- `numF` und `numB` steuern die Anzahl der Fahrten nach bzw. vor der Suchzeit (Kapitel 11.1).
- Echtzeitdaten lassen sich über `rtMode=SERVER_DEFAULT` (Standard) oder `rtMode=OFF` regulieren.

## 3. Ergebnisse interpretieren

- Jede Fahrt enthält `JourneyDetailRef` für Detailaufrufe (`journeyDetail`).
- `ctxRecon` ermöglicht spätere Rekonstruktionen über den `recon`-Service.
- Für Via-Orte oder Meta-Profile siehe Handbuch Kapitel 11.2–11.7 („TBD – siehe PDF“ bei weiteren Spezialfällen).
