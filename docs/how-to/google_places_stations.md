# Google Places Stations Import

Dieses Dokument beschreibt, wie Bahnhofsdatensätze aus der *Google Places API (New)* in `data/stations.json` eingespielt werden.

## Voraussetzungen

* Google Cloud Projekt mit aktivierter **Places API (New)**.
* Service API Key (wird in `GOOGLE_MAPS_API_KEY` hinterlegt).
* Python 3.11 Umgebung – das Repository stellt ein Skript und Hilfsmodule bereit.

> 💡 Lokale `.env`-Dateien können über `WIEN_OEPNV_ENV_FILES` (siehe `src/utils/env.py`) geladen werden.

## Konfiguration

Alle Parameter lassen sich via Umgebungsvariablen steuern. Die wichtigsten:

| Variable | Standardwert | Beschreibung |
| --- | --- | --- |
| `GOOGLE_MAPS_API_KEY` | – | **Pflicht.** API-Key für Google Places. |
| `PLACES_INCLUDED_TYPES` | `train_station,subway_station,transit_station` | Komma-separierte Liste von Place-Typen. |
| `PLACES_LANGUAGE` | `de` | Sprache der API-Antworten. |
| `PLACES_REGION` | `AT` | Regions-Bias. |
| `PLACES_RADIUS_M` | `2500` | Radius je Suchkachel (Meter). |
| `PLACES_TILES` | Stephansplatz | JSON-Liste von Tile-Zentren. Kann via `--tiles-file` überschrieben werden. |
| `MERGE_MAX_DIST_M` | `150` | Distanzschwelle für Duplikate (Meter). |
| `BOUNDINGBOX_VIENNA` | – | JSON-Objekt mit `min_lat`, `min_lng`, `max_lat`, `max_lng` zur Heuristik `in_vienna`. |
| `OUT_PATH_STATIONS` | `data/stations.json` | Zielpfad für das Stations-JSON. |
| `REQUEST_TIMEOUT_S` | `25` | HTTP Timeout je Request (Sekunden). |
| `REQUEST_MAX_RETRIES` | `4` | Maximale Retry-Versuche bei 429/5xx. |

## Nutzung des Skripts

```
python scripts/fetch_google_places_stations.py --dry-run
```

* Lädt Kacheln aus der Konfiguration.
* Führt `places:searchNearby` pro Kachel aus (mit Paginierung & Backoff).
* Merge-Logik: Duplikate per normalisiertem Namen oder Distanz < Schwellwert.
* Ausgabe: Diff (neu/aktualisiert/ignoriert) im Log.

Um Änderungen persistent zu speichern:

```
python scripts/fetch_google_places_stations.py --write
```

Zusatzoptionen:

* `--dump-new new_places.json` – schreibt nur neue & aktualisierte Einträge in eine separate Datei (hilfreich für Review/Artefakte).
* `--tiles-file tiles.json` – überschreibt `PLACES_TILES` mit einer lokalen Datei.

## Troubleshooting

* **Fehlender API-Key** → Skript bricht mit Exit-Code 2 ab.
* **429/5xx** → automatische Retries mit exponentiellem Backoff. Bei dauerhaften Fehlern prüfen: Quoten, Billing, Projektrechte.
* **Schema-Warnungen** → Log-Level WARN signalisiert übersprungene Kacheln/Antworten; Daten bleiben unangetastet.
* **Dry-Run vs. Write** → `--dry-run` und `--write` schließen sich aus. Ohne `--write` wird keine Datei geändert.

## Automatisierung

Ein GitHub-Workflow (`.github/workflows/update-google-places-stations.yml`) führt regelmäßig einen Write-Run aus, nutzt das Secret `GOOGLE_MAPS_API_KEY` und lädt ein Artefakt mit den Änderungen (`--dump-new`).
