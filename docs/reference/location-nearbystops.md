# GET /location.nearbystops

## Kurzbeschreibung
Führt eine Umkreissuche um eine Koordinate durch und liefert Stationen, POIs sowie optionale EntryPoints oder Sharing-Fahrzeuge im angegebenen Radius. Treffer sind nach Entfernung sortiert.

## Request

| Parameter | Pflicht | Werte | Beschreibung |
| --- | --- | --- | --- |
| `accessId` | ja | Zeichenkette | Access-ID für die Authentifizierung. |
| `originCoordLat` | ja | Dezimalgrad | Breitengrad des Suchmittelpunkts. |
| `originCoordLong` | ja | Dezimalgrad | Längengrad des Suchmittelpunkts. |
| `r` | nein | `1`–`10000`, Standard `1000` | Radius in Metern um die Koordinate. |
| `maxNo` | nein | `1`–`1000`, Standard `10` | Maximale Anzahl der Ergebnisse. |
| `type` | nein | `S`, `P`, `SE`, `PE`, `SP`, `SPE` | Filtert Ortstypen (Stationen, POIs, EntryPoints und Kombinationen). |
| `products` | nein | Dezimalwert | Bitmaske der zu berücksichtigenden ÖV-Produktklassen (siehe Handbuch Kapitel 6). |
| `meta` | nein | Meta-Filter | Aktiviert zusätzliche Inhalte wie Sharing-Angebote oder POI-Kategorien (Kapitel 8.2/8.3). |

## Antwort

- Die Antwort enthält `CoordLocation`-Elemente mit Distanz (`dist`) sowie optional `childLocation` für zugehörige Fahrzeuge.
- EntryPoints werden mit `entry="true"` gekennzeichnet und dienen der Kartendarstellung, nicht als Trip-Eingaben.
- Sharing-spezifische Details stehen in `LocationNotes` (z. B. Betreiber, Verfügbarkeit).

## Fehlercodes

- TBD – siehe PDF, Kapitel 20.

## Beispiel

```bash
curl -G "${VOR_BASE_URL}/location.nearbystops" \
  --data-urlencode "accessId=${VOR_ACCESS_ID}" \
  --data-urlencode "originCoordLat=48.20849" \
  --data-urlencode "originCoordLong=16.37208" \
  --data-urlencode "r=750" \
  -H "Accept: application/json"
```
