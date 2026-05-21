# Audit: GeoNetz-Reconciliation (2026-05-21)

## Auslöser

Maintainer hat die GeoNetz-Datei `OEBB_NETWORK.json` (aus
`GeoNetz_12-2024.zip` von [data.oebb.at](https://data.oebb.at/de/datensaetze))
für eine Quervalidierung gegen `data/stations.json` bereitgestellt.
Die Datei ist ÖBB-Infrastruktur-publiziert (1056 Stop-Points,
2885 Routing-Points, 2704 Routing-Links; Fahrplanperiode 2024-12-15 –
2025-12-13). Sie führt für jede Station BSTS_ID (= unser `bst_id`),
EVA_NR, IFOPT_ID, präzise Koordinaten sowie eine Postadresse.

## Befunde

### 1. Drei hand-kuratierte Koordinaten aus PR #1224 waren falsch

PR #1224 hatte für vier in der VOR-Resolver-Pipeline nicht auflösbare
Pendler-Stationen Koordinaten von Hand ergänzt. Drei davon weichen
substantiell von den offiziellen GeoNetz-Werten ab:

| Station | PR #1224 | GeoNetz | Δ |
|---|---|---|---|
| Laxenburg-Biedermannsdorf | (47.9893, 16.3640) | (48.0776, 16.3566) | **9836 m** (Möllersdorf-Region statt Bahnhof zwischen Laxenburg und Biedermannsdorf) |
| Mistelbach Stadt | (48.5754, 16.5821) | (48.5702, 16.5688) | 1135 m (Ortskern statt zentrale Bahn-Lage) |
| Himberg | (48.0828, 16.4392) | (48.0814, 16.4455) | 493 m (Westseite statt Bahn-Ostseite des Orts) |

Wikipedia-Lokalitäts-Beschreibungen (Laxenburg-Biedermannsdorf:
„zwischen Laxenburg und Biedermannsdorf"; Mistelbach Stadt:
„centrally located") bestätigen die GeoNetz-Werte als kanonisch.

### 2. Weigelsdorf ist operativ stillgelegt (seit 2023-07-01)

Mit der Modernisierung der Pottendorfer Linie wurde am 1. Juli 2023 der
alte Bahnhof Ebreichsdorf außer Betrieb genommen, das alte Gleis durch
Weigelsdorf und Ebreichsdorf rückgebaut. Seit 4. September 2023 ersetzt
der neue Bahnhof Ebreichsdorf die **Haltestelle Weigelsdorf**
([Quelle: LOK-Report](https://www.lok-report.de/news/europa/item/43641-oesterreich-pottendorfer-linie-neuer-bahnhof-ebreichsdorf-eroeffnet.html),
[ÖBB-Infrastruktur](https://infrastruktur.oebb.at/de/projekte-fuer-oesterreich/bahnstrecken/suedstrecke-wien-villach/pottendorfer-linie)).

Konsequenzen für die alten PRs:

- `pendler_candidates.json` hatte Weigelsdorf seit dem 2026-05-05 Audit
  als Priority-2-Pendler whitelisted. Die VOR-Resolver-Pipeline hat ihn
  deshalb in jedem Cron-Lauf erneut aufgelöst und dabei (weil keine
  Bahnstation mehr existiert) nur Bus-Stops zurückbekommen. Daraus
  entstand die „Whack-a-Mole"-Saga der PRs #1207-#1209 mit fortlaufend
  erweiterten Bus-Suffix-Filtern (`Volksschule`, `B60/Boschansiedlung`,
  `Judenweg`, `Kienergasse`, `Grenzweg`).
- PR #1230 hatte ihm zusätzlich eine hand-kuratierte VOR-ID
  (430515600) verpasst, die auf eine nicht mehr existente Bahn-Stelle
  zeigt.
- PR #1224 hatte hand-kurierte Koordinaten (47.9484, 16.4082).
- GeoNetz hat Weigelsdorf konsequenterweise nicht mehr im Datensatz.

### 3. Wien Handelskai trägt zwei unterschiedliche bst_ids

Unser Wert: `bst_id=779` (aus der alten `Verzeichnis der
Verkehrsstationen.xlsx`-Quelle). GeoNetz: `BSTS_ID=1586` (mit
`EVA_NR=8101934`). Beides referenziert denselben S-Bahn-Knotenpunkt;
die GeoNetz-Variante ist neuer und vermutlich aus der Re-Numerierung
nach der S-Bahn-Modernisierung. Aufgeschoben für eine separate
Schema-Erweiterung (`eva_nr`/`ifopt_id` als optionale Felder), weil
die bst_id ein laufendes Vertrag mit `_restore_existing_metadata` ist.

## Maßnahmen (PR α dieses Audits)

1. **Koordinaten korrigiert** auf die GeoNetz-Werte (gerundet auf 6
   Nachkommastellen) für Himberg, Laxenburg-Biedermannsdorf und
   Mistelbach Stadt. `source` der drei Einträge um `oebb_geonetz`
   erweitert.
2. **Weigelsdorf entfernt** aus `data/stations.json` (Eintrag gelöscht),
   `data/pendler_candidates.json` (Whitelist-Eintrag gelöscht, Hinweis
   in der `notes`-Liste ergänzt) und `data/gtfs/stops.txt` (abgeleiteter
   GTFS-Stub gelöscht).
3. **Regression-Tests** unter
   `tests/test_station_directory_geonetz_corrections.py`: drei Punkte
   pinnen (a) die GeoNetz-Werte mit ≤200 m Toleranz, (b) explizit
   gegen die PR #1224-Werte als Tripwire, (c) Weigelsdorf-Tombstone in
   allen drei Datenpfaden.

## Validierungs-Ergebnis

- `provider_issues`: 0
- `cross_station_id_issues`: 0
- `naming_issues`: 0
- `security_issues`: 0
- `duplicates`: 1 (Bad Fischau / Bad Fischau-Brunn — pre-existing,
  out-of-scope)
- `coordinate_issues`: 0
- `alias_issues`: 0
- `identity_field_conflicts`: 0
- 2236 Stationen (vorher 2237 — minus Weigelsdorf)

## Aufgeschoben

- **PR β** (Schema-Anreicherung): `eva_nr` (UIC), `ifopt_id` (ÖPNV),
  `bsts_id` (= aktuelle GeoNetz-BSTS_ID) und `address` als optionale
  Felder. GeoNetz-Loader mit Cron-Refresh + 147-Stations-Anreicherung.
- **PR γ** (HAFAS-Trigger): Den HAFAS-Tier auch dann anrufen, wenn
  OSM-Koordinaten schon existieren aber gegen GeoNetz driften (>200
  m), damit systematisch falsche OSM-Werte nicht persistieren.
