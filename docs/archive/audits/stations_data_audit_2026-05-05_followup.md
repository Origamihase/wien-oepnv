# Stationsverzeichnis – Follow-up Audit 2026-05-05

Adressiert die drei „Nicht in diesem Fix"-Punkte aus dem
[Erst-Audit](stations_data_audit_2026-05-05.md):

1. ⚠️ Wien-Polygon-Vereinfachung (Liesing-Edge-Case)
2. ⚠️ WL-DIVA-Lücke
3. ⚠️ Alias-Token-Kollision Sue ↔ Su

## 1. Wien-Polygon-Verfeinerung

`data/vienna_boundary.geojson` enthielt die **8-Vertex-Konvex-Hülle der
in_vienna-Stationskoordinaten** (das Quellfeld `properties.source` sagte
das auch explizit). Damit konnten echte Stadtgrenzen-Effekte nicht
abgebildet werden — Wien Liesing fiel mit den präzisen VOR-Koordinaten
(`48.134853, 16.284229`) knapp aus dem Polygon, weil exakt die alte
Liesing-Position einer der 8 Hüllen-Vertices war.

**Maßnahme**: Hand-kuratierte 31-Vertex-Approximation der echten Wiener
Stadtgrenzen. Geht im Uhrzeigersinn vom NW (Salmannsdorfer Höhe) über
den Bisamberg (Wien-Nordpunkt), die Lobau-Ostbucht in Richtung
Donau-Auen, südöstlich an Albern/Kaiserebersdorf vorbei (klar westlich
von Kledering und Schwechat), durch Liesing/Mauer (südlich der Bahnstation
und nördlich von Perchtoldsdorf), den Wienerwald (Kalksburg, Mauerwald,
Lainzer Tiergarten, Hadersdorf) hoch nach Norden zurück.

**Validierung**:

| Station | Vorher | Nachher |
|---|---|---|
| Wien Liesing (`48.134853, 16.284229`) | OUTSIDE ❌ | INSIDE ✓ |
| Klosterneuburg-Weidling (`48.297585, 16.334586`) | outside ✓ | outside ✓ |
| Perchtoldsdorf (`48.123023, 16.285559`) | outside ✓ | outside ✓ |
| Brunn am Gebirge (`48.10509, 16.288094`) | outside ✓ | outside ✓ |
| Kledering (`48.132453, 16.439724`) | outside ✓ | outside ✓ |
| Schwechat (`48.143195, 16.482055`) | outside ✓ | outside ✓ |

Alle 107 stations.json-Einträge produzieren mit dem neuen Polygon die
in `is_in_vienna` erwartete Klassifikation. Der existierende Test
`test_coordinates_match_in_vienna_flag` läuft weiter grün; zwei neue
Tests pinnen Liesing und vier kritische Pendler explizit fest.

**Liesing-Koordinaten** (`bst_id 1205`) auf VOR-Werte
`48.134853, 16.284229` aktualisiert (vorher GTFS-Rundung `48.1366, 16.2883`).

## 2. Alias-Token-Kollision Sue ↔ Su

`_normalize_token` in `src/utils/stations.py` führte die ASCII-Umlaut-
Faltung `ae→a`, `oe→o`, `ue→u` für ALLE Token-Längen aus. Damit
kollidierten der ÖBB-Stellencode `Sue` (Wien Süßenbrunn) und `Su`
(Stockerau) beide auf `su`. Der Lookup `station_info("Sue")` lieferte
fälschlich `Stockerau` statt `Wien Süßenbrunn`, plus eine deutliche
"Duplicate station alias" WARNING beim Modul-Import.

**Maßnahme**: Die Faltung wird nur auf Token-Länge ≥ 4 angewendet. Für
2- oder 3-Zeichen-Token (typisch Identifier-Stellencodes) bleibt die
Originalform erhalten:

```python
if len(text) > 3:
    text = text.replace("ae", "a").replace("oe", "o").replace("ue", "u")
```

ASCII-Transliterationen wie `Mueller` (Länge 7) werden weiterhin auf
`muller` gefaltet — das Verhalten für normale Stationsnamen ändert sich
nicht.

**Validierung**: nach dem Fix:

```
station_info("Sue")  → Wien Süßenbrunn ✓
station_info("Su")   → Stockerau ✓
station_info("Süßenbrunn") → Wien Süßenbrunn ✓
station_info("Mueller") → ... (long-token fold unchanged)
```

Neuer Test in `test_station_alias_collision.py` pinnt das Verhalten ein.

## 3. WL-DIVA-Lücke

Die OGD-CSVs `wienerlinien-ogd-haltestellen.csv` und
`wienerlinien-ogd-haltepunkte.csv` im `data/`-Verzeichnis sind
3- bzw. 6-Zeilen-**Samples**, kein Vollexport. Damit fehlt für 14
U-Bahn-Knoten die `wl_diva`-Verknüpfung (Westbahnhof, Hütteldorf,
Heiligenstadt, Floridsdorf, Spittelau, Hauptbahnhof, Meidling,
Ottakring, Leopoldau, Simmering, Stadlau, Aspern Nord, Handelskai,
Mitte-Landstraße).

**Maßnahme**: `scripts/update_wl_stations.py` lädt jetzt vor dem Merge
beide OGD-CSVs aus `data.wien.gv.at` herunter (per
`session_with_retries`/`fetch_content_safe` aus `src.utils.http`). Bei
Netzwerkfehlern degradiert die Funktion still und nutzt den lokalen
Sample-Stand weiter. Im monatlichen GitHub-Action-Lauf
(`.github/workflows/update-stations.yml`) wird damit der Vollstand
abgerufen und über das normale Merge-Verfahren in `stations.json`
eingespielt.

```python
parser.add_argument(
    "--download/--no-download",
    default=True,
    help="Download the latest WL OGD CSVs from data.wien.gv.at",
)
```

**Validierung**: zwei neue Tests in `test_update_wl_stations_merge.py`
prüfen einerseits den Erfolgsfall (Datei wird atomar geschrieben),
andererseits das graceful-Fallback bei `OSError` (Funktion liefert
`False`, lokaler Stand bleibt erhalten).

In der Sandbox dieses PRs ist `data.wien.gv.at` per Proxy-Policy
(`host_not_allowed`) blockiert — die `wl_diva`-Werte für die 14
U-Bahn-Knoten werden also **erst** beim nächsten CI-Lauf von
`update-stations.yml` materialisiert. Die Code-Änderung in dieser PR
ist die einmalige Voraussetzung dafür.

## Zusammenfassung der nicht angetasteten Punkte

Nach diesem Follow-up sind die Restpunkte aus dem Erst-Audit erledigt.
Verbleibende low-priority Beobachtungen:

- 7-stellige `bst_id`-Werte (`4773541`, `4407597`, `2968384`, `1251757`,
  `1251761`) bei einigen ÖBB-Einträgen sind nicht im offiziellen
  Stellencode-Verzeichnis dokumentiert. Validator lässt sie durch;
  Auswirkungen unklar, aber kein bekannter Bug.
- Der genaue Wien-Polygon ist 31-Vertex-Approximation, nicht die exakte
  Bezirksgrenze. Genauigkeit ~200 m, ausreichend für Stations-Klassifikation.
  Für Anwendungsfälle, die feinere Auflösung brauchen, wäre der
  offizielle Stadt-Wien-Datensatz `LANDESGRENZEOGD` aus dem Open-Data-Portal
  einzubinden (~1500 Vertices).
