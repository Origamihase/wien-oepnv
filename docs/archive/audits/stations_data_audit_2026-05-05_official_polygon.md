# Wien-Polygon: offizielle LANDESGRENZEOGD-Quelle 2026-05-05

Ergänzung zum [Follow-up-Audit](stations_data_audit_2026-05-05_followup.md):
das hand-kuratierte 31-Vertex-Polygon wird durch die offizielle Wiener
Stadtgrenze ersetzt.

## Quelle

`data/LANDESGRENZEOGD.json` — vom Stadt-Wien-Open-Data-Portal über die
WFS-API:

```
https://data.wien.gv.at/daten/geo?service=WFS&request=GetFeature&version=1.1.0
  &typeName=ogdwien:LANDESGRENZEOGD&srsName=EPSG:4326&outputFormat=json
```

Eigenschaften des heruntergeladenen Datensatzes:

| Feld | Wert |
|---|---|
| Format | GeoJSON FeatureCollection, Single Polygon, Single Outer Ring |
| Vertex-Anzahl | **5.637** (vorher: 31 Hand-kuratiert, davor: 8 Konvex-Hülle) |
| CRS | `urn:ogc:def:crs:EPSG::4326` (WGS84) |
| `properties.NAME_LANDK` | `Wien` |
| `properties.FLAECHE` | 414.871.084,46 m² (≈ 414,87 km², offizielle Wien-Fläche) |
| `properties.UMFANG` | 136.475,7 m |
| `properties.LU_DATE` | 2025-06-01 (Last-Update aus Stadt Wien) |

## Maßnahme

1. `data/LANDESGRENZEOGD.json` als kanonische Polygonquelle eingecheckt.
2. `data/vienna_boundary.geojson` (vorheriges 31-Vertex-Polygon) entfernt.
3. `src/utils/stations.py:_VIENNA_POLYGON_PATH` zeigt jetzt auf
   `data/LANDESGRENZEOGD.json`.

`_vienna_polygons()` parst das Originalformat ohne Anpassung:
`type=FeatureCollection` → `type=Feature` → `geometry.type=Polygon` ist
genau der Standard-Pfad, den der existierende Parser unterstützt.

## Validierung

Alle 107 `stations.json`-Einträge klassifizieren mit dem offiziellen
Polygon korrekt:

```
$ python3 -c "..."
All 107 station classifications match the official polygon ✓
```

| Station | mit offiziellem Polygon |
|---|---|
| Wien Liesing (VOR-Coords `48.134853, 16.284229`) | INSIDE ✓ |
| Klosterneuburg-Weidling (`48.297585, 16.334586`) | outside ✓ |
| Perchtoldsdorf (`48.123023, 16.285559`) | outside ✓ |
| Brunn am Gebirge (`48.10509, 16.288094`) | outside ✓ |
| Kledering (`48.132453, 16.439724`) | outside ✓ |
| Schwechat (`48.143195, 16.482055`) | outside ✓ |
| Korneuburg (`48.343574, 16.328474`) | outside ✓ |
| Stockerau (`48.382308, 16.213016`) | outside ✓ |

Tests laufen unverändert grün (1029/1029, 1 skipped). Die in PR #1189
hinzugefügten Pin-Tests
(`test_polygon_includes_liesing_authoritative_coords`,
`test_polygon_excludes_close_pendler_stations`) sind weiterhin erfüllt.

## Genauigkeit

Vorheriges Polygon: 31 Vertices, ~200 m Genauigkeit.
Neues Polygon: 5637 Vertices, **~1–2 m** Genauigkeit (entspricht der
amtlichen Vermessungspräzision der Stadt Wien).

Damit ist auch zukünftig keine manuelle Polygon-Pflege mehr nötig — bei
einer Boundary-Änderung lädt man einfach den aktualisierten Stadt-Wien-
Datensatz neu.
