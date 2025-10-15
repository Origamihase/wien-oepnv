# GET /tti

## Kurzbeschreibung
Gibt die Eckdaten der aktuellen Fahrplanperiode zurück (Beginn, Ende, letzter Datenstand). Diese Information begrenzt gültige Abfragezeiträume für Trip- und Stationboard-Services.

## Request

| Parameter | Pflicht | Werte | Beschreibung |
| --- | --- | --- | --- |
| `accessId` | ja | Zeichenkette | Access-ID für die Authentifizierung. |

## Antwort

- Enthält Attribute wie `date` (letztes Datenupdate), `begin` (Start der Fahrplanperiode abzüglich 62 Tage) und `end` (Ende der Periode).
- Der Service unterstützt Typen wie `ST` für Stationen; Werte definieren zulässige Anfrageintervalle.
- Abfragen sind generell bis zu 62 Tage rückwirkend sowie bis zum Ende der Fahrplanperiode möglich.

## Fehlercodes

- TBD – siehe PDF, Kapitel 20.

## Beispiel

```bash
curl -G "${VOR_BASE_URL}/tti" \
  --data-urlencode "accessId=${VOR_ACCESS_ID}" \
  -H "Accept: application/json"
```
