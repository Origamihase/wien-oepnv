# Audit: GeoNetz-Schema-Anreicherung (2026-05-21, PR β)

## Auslöser

Follow-up zur [GeoNetz-Reconciliation](stations_geonetz_reconciliation_2026-05-21.md)
(PR α) vom selben Tag. Der Reconciliation-Audit hatte zwei aufgeschobene
Punkte explizit benannt:

> **PR β**: Schema-Anreicherung mit `eva_nr` (UIC), `ifopt_id` (ÖPNV),
> `address` aus GeoNetz für die 147 ÖBB-Stationen mit `bsts_id`-Match
> (Tor zu HAFAS/DB-API).
>
> **PR γ**: HAFAS-Drift-Detection bei OSM-Coords >200 m von GeoNetz.

Dieser Bericht dokumentiert PR β. PR γ folgt separat.

## Datenquelle

Statt die 23 MiB rohe `OEBB_NETWORK.json` (`GeoNetz_12-2024.zip` von
[data.oebb.at](https://data.oebb.at/), CC BY 4.0) bei jedem Cron-Lauf
neu zu parsen, wird das Verzeichnis einmalig auf die sieben Felder
destilliert, die der Enrichment-Loader konsumiert:

- `bsts_id` (Join-Schlüssel = unser `bst_id`)
- `name` (Fallback-Join-Schlüssel)
- `lat` / `lon` (für die spätere PR γ, hier nicht verbraucht)
- `eva_nr` (UIC-Stationsnummer, z. B. `8103000` für Wien Hauptbahnhof)
- `ifopt_id` (DELFI-ÖPNV-Standard, z. B. `at:49:1349`)
- `address` (`STP_ROADNAME`, z. B. `1100 Wien, Am Hauptbahnhof 1`)

Die Distillation läuft in `scripts/extract_oebb_geonetz_stops.py` und
erzeugt `data/oebb_geonetz_stops.json` (1 056 Stop-Points, 234 KiB,
alphabetisch sortiert, deduplizziert über `bsts_id`). Re-Run bei jedem
SNNB-Fahrplanwechsel (typisch jährlich Mitte Dezember). Fahrplanperiode
des aktuellen Datensatzes: 2024-12-15 – 2025-12-13.

## Schema-Änderungen

`docs/schema/stations.schema.json` erhält drei optionale Felder nach
`hafas_extId`:

| Feld | Pattern | Bedeutung |
|---|---|---|
| `eva_nr` | `^[0-9]{6,8}$` | UIC EVA-Nummer (internationaler DB-/ÖBB-Schlüssel) |
| `ifopt_id` | `^[a-z]{2}:[0-9]+:[0-9]+$` | IFOPT-Stop-ID, DELFI-konform |
| `address` | `minLength: 1` | Postalische Adresse (Display-only) |

Keine Änderungen an Required-Listen oder `allOf`-Constraints — die
Felder sind zusätzliche Metadaten, nicht strukturell zwingend.

## Enrichment-Loader (`_enrich_with_geonetz`)

Neue Funktion in `scripts/update_station_directory.py`, läuft direkt
nach `_filter_relevant_stations` in `main()`. Zwei-Stufen-Join:

1. **Primärer Join** über `Station.bst_id` ↔ GeoNetz-`bsts_id`.
   Deckt die kanonischen 147 ÖBB-Einträge sauber ab.
2. **Sekundärer Join** über exakten Namens-Match auf der **eindeutigen**
   Namens-Teilmenge der GeoNetz-Daten. Fängt synthetische 900xxx-IDs
   (`bst_id=900100` für „Wien Hauptbahnhof", etc.) sowie OSM-/WL-Einträge,
   deren Stationsname zufällig in GeoNetz steht.

Schutz-Mechanismen:

- **Koordinaten werden nicht überschrieben.** Die GeoNetz-Coord-
  Reconciliation hat PR α erledigt; PR β ist explizit metadaten-only.
- **Pre-existing `eva_nr` bleibt erhalten.** Falls eine Station bereits
  einen UIC-Wert (z. B. aus einem manuellen Override) trägt, schreibt
  der Loader nicht darüber.
- **Source-Token alphabetisch eingefügt.** `oebb_geonetz` wird dem
  bestehenden `source`-String hinzugefügt und re-sortiert (gleiche
  Konvention wie `places/merge.py:182`).
- **Idempotent.** Zweiter Lauf mit identischem Lookup ändert nichts.
- **Duplicate-Name-Safety.** Wenn ein GeoNetz-Name auf zwei `BSTS_IDs`
  vorkommt (selten — typischerweise operative Sub-Stationen), wird der
  Sekundär-Join übersprungen, damit nie der falsche `eva_nr`-Wert
  attached wird.

CLI-Argument `--geonetz-stops PATH` mit Default
`data/oebb_geonetz_stops.json`.

## Coverage

Mit der ersten Anreicherung tragen **385 von 2 235 Stationen** die drei
neuen Felder:

- ~147 über den primären `bst_id`-Join (alle `source=oebb`-Einträge mit
  GeoNetz-Match)
- ~238 über den sekundären Namens-Join (manual_foreign/distant-Knoten
  wie `Salzburg Hbf`, `Graz Hbf`, `Linz Hbf`, dazu OSM/WL-Einträge mit
  exaktem Bahnstationsnamen)
- 671 GeoNetz-Stops bleiben unverbraucht (Betriebs-Sub-Stationen,
  Awanst, Bahnknoten ohne Pendler-Relevanz).

## Loader-Kontrakt

`_load_geonetz_stops(path)` degradiert sanft:

- Datei fehlt → leeres Lookup, keine Exception (cron-friendly)
- Malformed JSON → leeres Lookup + WARNING (keine Pipeline-Abbruch)
- Einträge ohne `bsts_id` werden geskippt
- Liest via `read_capped_bytes` (50 MiB cap, mirror der `MAX_JSON_FILE_BYTES`-
  Konvention von `update_station_directory.py`) gegen den
  size-bomb / FIFO / `/dev/zero` Threat-Modell-Vektor

## Tests

`tests/test_geonetz_enrichment.py` (neu, 18 Tests):

- **Loader-Kontrakt** (4 Tests): fehlende Datei, malformed JSON,
  fehlender `bsts_id`-Key, live-Datei-Shape (1000 < n < 1200, Wien
  Hauptbahnhof unter `BSTS_ID=2393`).
- **Enrichment-Verhalten** (7 Tests): primärer Join, sekundärer Join,
  Duplicate-Name-Skip, Coordinates-bleiben-untouched, Idempotenz,
  bestehender `eva_nr` wird nicht überschrieben, Source-Token
  alphabetisch eingefügt.
- **Live-Daten-Pins** (5 Tests, parametrisiert): Westbahnhof (8100003),
  Laxenburg-Biedermannsdorf (8101122), Mistelbach Stadt (8102007),
  Himberg (8100950); plus: jede Station mit `eva_nr` trägt
  `oebb_geonetz` im `source`-Feld.

Zusätzlich:

- `test_no_basicconfig_in_scripts` (preflight-Sentinel) – der neue
  `extract_oebb_geonetz_stops.py` nutzt `setup_script_logging` statt
  `logging.basicConfig`.
- `test_no_unbounded_read_bytes_in_src_or_scripts` (workbook-Sentinel)
  – der Raw-GeoNetz-Read läuft über `read_capped_bytes` mit explizitem
  50-MiB-Cap.

## Validierungs-Ergebnis (`stations.json`)

- `provider_issues`: 0
- `cross_station_id_issues`: 0
- `naming_issues`: 0
- `security_issues`: 0
- `duplicates`: 1 (Bad Fischau / Bad Fischau-Brunn — pre-existing,
  out-of-scope, dokumentiert seit PR α)
- `coordinate_issues`: 0
- `alias_issues`: 0
- `identity_field_conflicts`: 0
- 2 235 Stationen (unverändert gegenüber PR α)
- **+385 Stationen mit `eva_nr` / `ifopt_id` / `address`**
- **+385 `source`-Token `oebb_geonetz`**

## Auswirkung auf Konsumenten

Mit dem `eva_nr`-Feld kann der DB-HAFAS-Tier (`transport.rest`,
`bahn.guru`, Hafas-mgate gegen `fahrplan.oebb.at`) eine Station ohne
Namens-Search direkt über die UIC-Nummer abfragen. Das ist die
Vorbedingung für PR γ (Drift-Detection mit HAFAS als Schiedsrichter).

Der `ifopt_id` ist der DACH-weite Identifikationsstandard für ÖPNV-Stops
und enables zukünftige Integrationen mit DELFI / VBN / VVS-Datensätzen.

`address` ist read-only Display-Information.

## Aufgeschoben

Weiterhin **PR γ** (HAFAS-Drift-Detection): OSM-Coordinates gegen die
GeoNetz-`lat`/`lon` validieren, bei Drift >200 m HAFAS als
Schiedsrichter konsultieren. Die GeoNetz-Lat/Lon-Daten sind bereits
vorhanden in der compact JSON, der Detection-Validator und die
HAFAS-Anbindung stehen noch aus.
