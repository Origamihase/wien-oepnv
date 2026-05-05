# Stationsverzeichnis – Vollständigkeits-Audit 2026-05

Vollständigkeits-Vergleich der `data/stations.json` gegen das ÖBB-/VOR-
Bahnhofsverzeichnis (Stand Mai 2026, alle aktiven S-Bahn-/REX-/R-Halte
des VOR-Kerngebiets) und resultierende Anpassungen am Updater.

## Zusammenfassung der Recherche-Befunde

| Kennzahl | Wert | Bewertung |
|---|---|---|
| Wien-Stationen (`in_vienna: true`) im Verzeichnis | 48 | vollständig (siehe unten) |
| NÖ/Bgld-Pendlerstationen im Verzeichnis | 41 | systematische Lücken |
| Recherche-Behauptung „Wien Mitte fehlt" | falsch | Eintrag existiert als `Wien Mitte-Landstraße` (bst_id 900102) mit Aliasen `Wien Mitte`, `Mitte`, … |
| Tatsächlich kritisch fehlende Pendlerstationen | **12** | siehe Tabelle |
| Wichtige Pendlerstationen 2. Priorität | **57** | siehe `data/pendler_candidates.json` |

## Verifikation der Recherche-Behauptung „Wien Mitte fehlt"

Falsch. `data/stations.json` enthält den Eintrag:

```json
{
  "name": "Wien Mitte-Landstraße",
  "vor_id": "490074300",
  "bst_id": "900102",
  "aliases": ["Wien Mitte", "Mitte", "Mitte-Landstraße", … (~100 weitere)]
}
```

Lookup-Tests bestätigen: `station_info("Wien Mitte")` → `Wien Mitte-Landstraße`.
Die Recherche hat den kanonischen Namen übersehen — **kein Datenfehler**.

## Wirklich fehlende Pendlerstationen (Top-12)

Verifiziert per Cross-Check gegen Existing-Aliase:

| Station | Linie | Begründung |
|---|---|---|
| Pfaffstätten | S-Bahn Südbahn | starkes Pendleraufkommen Bezirk Baden |
| Gumpoldskirchen | S-Bahn Südbahn | starkes Pendleraufkommen |
| Guntramsdorf Südbahn | S-Bahn Südbahn | Pendlerbahnhof zwischen Mödling und Baden |
| Hennersdorf | S60-Verstärker Pottendorfer Linie | erster Halt nach Wien Blumental |
| Achau | S60-Verstärker Pottendorfer Linie | modernisiert 2019 |
| Münchendorf | S60-Verstärker Pottendorfer Linie | modernisiert 2019 |
| Gramatneusiedl | S60/REX6/REX64 Ostbahn | wichtiger Knoten Industrieviertel |
| Götzendorf an der Leitha | S60/REX6 Ostbahn | Pendlerknoten Bruck/Leitha |
| Himberg bei Wien | S60/REX6 Ostbahn | sehr starker Pendlerbahnhof (z.Z. baulich gesperrt bis Ende 2026) |
| Felixdorf | S3/S4/REX | Verzweigung Süd-/Aspangbahn |
| Sollenau | S-Bahn Südbahn / Aspangbahn | Knoten |
| Traiskirchen Aspangbahn | R95 Innere Aspangbahn | Hauptbahnhof Bezirk Baden |

Hinzu kommen ~57 weitere wichtige Pendlerstationen aus den Linien Südbahn,
Pottendorfer, Innere Aspangbahn, Pressburger Bahn, Marchegger Ostbahn,
Nordbahn, Laaer Ostbahn, Nordwestbahn, Franz-Josefs-Bahn und Westbahn.

## Maßnahme dieses Audits

Die `bst_id`-Werte der fehlenden Stationen sind nicht aus den lokalen
GTFS-/VOR-CSV-Dateien ableitbar (diese decken nur die bereits ergänzten
Stationen ab — 97 / 93 Zeilen). Sie liegen ausschließlich im
ÖBB-Excel-Verzeichnis „Verkehrsstation", das beim monatlichen Lauf von
`update-stations.yml` von `data.oebb.at` heruntergeladen wird.

Statt die Stationen heute manuell mit (potenziell falschen) Provisorisch-
IDs zu ergänzen, führt dieser PR eine **name-basierte Pendler-Whitelist**
ein:

### Neue Datei `data/pendler_candidates.json`

```json
{
  "candidates": [
    {"name": "Pfaffstätten", "line": "S-Bahn Südbahn", "priority": 1},
    {"name": "Gumpoldskirchen", "line": "S-Bahn Südbahn", "priority": 1},
    …
    {"name": "Pottenbrunn", "line": "REX51/R Westbahn", "priority": 2}
  ]
}
```

69 normalisierte Name-Keys (12 Top-Priorität + 57 weitere). JSON-Schema:
[`docs/schema/pendler_candidates.schema.json`](../../schema/pendler_candidates.schema.json).

### Updater-Erweiterung in `scripts/update_station_directory.py`

`_annotate_station_flags` bekommt einen optionalen Parameter
`pendler_name_candidates: set[str]`. Beim Verarbeiten jedes ÖBB-Excel-
Eintrags wird zusätzlich zur `bst_id`-Whitelist (`pendler_bst_ids.json`)
auch der normalisierte Stationsname gegen die Kandidaten-Liste geprüft.

```python
pendler_candidate = station.bst_id in pendler_ids
if not pendler_candidate and name_candidates:
    for key in _normalize_location_keys(station.name):
        if key and key in name_candidates:
            pendler_candidate = True
            break
```

Die mutual-exclusivity-Garantie aus PR #1192 bleibt unangetastet:
`in_vienna=true` schlägt jeden Pendler-Marker (sowohl bst_id- als auch
namensbasiert) und loggt eine WARNING.

## Auswirkung auf den nächsten Workflow-Lauf

Beim nächsten monatlichen `update-stations.yml`-Cron (`0 1 1 * *`):

1. `update_station_directory.py` lädt das ÖBB-Excel von `data.oebb.at`.
2. Für jeden Excel-Eintrag wird geprüft:
   - bst_id ∈ `pendler_bst_ids.json`? (alt)
   - normalisierter Name ∈ `pendler_candidates.json`? (neu)
3. Trifft beides oder eines davon zu, wird der Eintrag mit
   `pendler=true` ins Verzeichnis übernommen.
4. Ihre echten `bst_id`/`bst_code`/`vor_id`-Werte kommen direkt aus der
   ÖBB-Quelle — keine ID-Raterei, keine manuellen Korrekturen nötig.
5. Der Heartbeat (`data/stations_last_run.json`, eingeführt in PR #1200)
   meldet die neuen Einträge in `diff.added`; `docs/stations_diff.md`
   listet sie namentlich.

Stationen, die im Excel nicht auftauchen (z.B. weil ÖBB sie
zwischenzeitlich umbenannt hat), bleiben in `pendler_candidates.json`
stehen, ohne den Lauf zu blockieren.

## Nachbesserung nach dem ersten Cron-Lauf (Mai 2026)

Der erste Lauf nach Einführung von `pendler_candidates.json` hat 56 neue
Pendlerstationen reingeholt — aber **alle ohne Koordinaten und ohne
vor_id**. Drei Ursachen, in einer Folge-Iteration adressiert:

1. **Henne-Ei zwischen `fetch_vor_haltestellen.py` und neuen Pendler-Namen.**
   Das Fetch-Skript las nur `data/stations.json` als Quelle der
   aufzulösenden Namen — die Pendler-Kandidaten waren zu dem Zeitpunkt
   noch nicht in stations.json (kommen erst im nachfolgenden Schritt
   `update_station_directory.py` rein). Behoben: Fetcher liest jetzt
   zusätzlich `data/pendler_candidates.json` (`name` + `alternative_names`)
   und resolviert sie vorab. Beim nächsten Lauf landen ihre VOR-IDs
   schon in `vor-haltestellen.csv`, bevor `update_station_directory.py`
   startet.
2. **`_build_location_index` nutzte VOR-CSV nicht.** Lokales GTFS deckt
   nur die ~99 bereits ingestierten Halte ab; WL nur U-Bahn-Knoten;
   die Pendler-Lücke fand keine Coords. Behoben: `_build_location_index`
   nimmt jetzt `data/vor-haltestellen.csv` als dritte Quelle (mit
   Präzedenz GTFS > WL > VOR — Vienna-Halte behalten ihre
   höher-präzisen GTFS-Coords).
3. **3 von 12 Top-Pendlern fehlten** (Guntramsdorf Südbahn, Götzendorf
   an der Leitha, Himberg bei Wien). ÖBB-Excel verwendet kürzere Namen.
   Behoben: `pendler_candidates.json`-Schema bekommt ein optionales
   `alternative_names`-Feld; die drei Einträge listen jetzt auch
   `Guntramsdorf`, `Götzendorf`, `Himberg` als Match-Alternativen.

Außerdem: Schema lockert die unbedingten Pflichtfelder
`latitude`/`longitude`/`source` zu **bedingt erforderlich** für
`in_vienna=true`-Einträge. Pendler-Einträge dürfen vorübergehend
ohne Coords leben (bis der nächste Cron-Lauf sie über VOR-CSV ergänzt) —
Wien-Einträge müssen sie weiterhin zwingend tragen.

## Nicht in diesem Audit

- **Perchtoldsdorf**: laut Recherche kein aktiver ÖBB-Personenverkehr (Kalten­
  leutgebener Bahn seit 1951 ohne Personenverkehr). Der Eintrag wird
  vorerst behalten, weil die VOR-ID `430450000` für die Bus-Haltestelle
  „Perchtoldsdorf Bahnhof" der WLB Badner Bahn weiter relevant ist.
  Reklassifizierung erfordert Schema-Erweiterung um `transport_modes`
  oder `note` und ist außerhalb dieses PRs.
- **Geplante Stationen** (Wien Hietzinger Hauptstraße, Wien Stranzenberg-
  brücke aus dem ÖBB-Projekt „Attraktivierung Verbindungsbahn",
  ev. Reaktivierung Wien Lobau): noch nicht in Betrieb — werden bei
  Inbetriebnahme nachgepflegt.
- **VOR-Kernzone-Flag** (`kernzone_wien: true` für Stationen wie Gerasdorf,
  Schwechat, Purkersdorf Sanatorium, Kledering): Schema-Erweiterung mit
  Auswirkung auf den Feed-Code; eigener Folge-PR.
- **Hauptbahnhof S-Bahn-Bahnsteige** als sekundäre VOR-ID: erfordert
  Schema-Anpassung zu `vor_ids: list[string]` statt `vor_id: string`;
  invasiv und außerhalb dieses Scopes.
