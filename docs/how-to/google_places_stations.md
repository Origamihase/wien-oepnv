---
title: "Google Places Stations Import"
description: "Anleitung zum Abruf und zur Zusammenführung von Bahnhofs- und Haltestellendaten über die Google Places API in den lokalen Stationskatalog."
---

# Google Places Stations Import

Dieses Dokument beschreibt, wie Bahnhofsdatensätze aus der *Google Places API (New)* in `data/stations.json` eingespielt werden.

## Voraussetzungen

* Google Cloud Projekt mit aktivierter **Places API (New)**.
* Service API Key (als Secret `GOOGLE_ACCESS_ID`, Fallback `GOOGLE_MAPS_API_KEY` \(deprecated\)).
* Python 3.11 Umgebung – das Repository stellt ein Skript und Hilfsmodule bereit.

> 💡 Lokale `.env`-Dateien können über `WIEN_OEPNV_ENV_FILES` (siehe `src/utils/env.py`) geladen werden.

## Konfiguration

Alle Parameter lassen sich via Umgebungsvariablen steuern. Die wichtigsten:

| Variable | Standardwert | Beschreibung |
| --- | --- | --- |
| `GOOGLE_ACCESS_ID` | – | **Pflicht.** Primärer API-Key für Google Places. |
| `GOOGLE_MAPS_API_KEY` | – | Deprecated Fallback – wird automatisch verwendet, falls `GOOGLE_ACCESS_ID` fehlt. |
| `PLACES_INCLUDED_TYPES` | `train_station,subway_station,bus_station` | Komma-separierte Liste von Place-Typen. |
| `PLACES_LANGUAGE` | `de` | Sprache der API-Antworten. |
| `PLACES_REGION` | `AT` | Regions-Bias. |
| `PLACES_RADIUS_M` | `2500` | Radius je Suchkachel (Meter). |
| `PLACES_TILES` | Stephansplatz | JSON-Liste von Tile-Zentren. Kann via `--tiles-file` überschrieben werden. |
| `MERGE_MAX_DIST_M` | `150` | Distanzschwelle für Duplikate (Meter). |
| `BOUNDINGBOX_VIENNA` | – | JSON-Objekt mit `min_lat`, `min_lng`, `max_lat`, `max_lng` zur Heuristik `in_vienna`. |
| `OUT_PATH_STATIONS` | `data/stations.json` | Zielpfad für das Stations-JSON. |
| `REQUEST_TIMEOUT_S` | `25` | HTTP Timeout je Request (Sekunden). |
| `REQUEST_MAX_RETRIES` | `4` | Maximale Retry-Versuche bei 429/5xx. |

## Kostenkontrolle & Free-Cap

Die Places API darf nur im Rahmen des kostenlosen Kontingents genutzt werden. Das Repository bringt daher einen Quota-Manager mit, der die monatlichen Aufrufe (UTC-Monatsgrenzen) zählt und bei Erreichen der Limits auf bestehende Caches zurückfällt.

* Limits werden über folgende ENV-Variablen gesteuert (Defaults in Klammern): `PLACES_LIMIT_TOTAL` (4000), `PLACES_LIMIT_NEARBY` (1500), `PLACES_LIMIT_TEXT` (1500), `PLACES_LIMIT_DETAILS` (1000).
* Der Zählerstand wird in `data/places_quota.json` persistiert. Der Speicherort kann über `PLACES_QUOTA_STATE` überschrieben werden (Pfad muss innerhalb von `data/`, `docs/` oder `log/` liegen). Falls `STATE_PATH` gesetzt ist, landet die Datei automatisch dort.
* Beim Monatswechsel (UTC) wird der Zähler automatisch auf Null zurückgesetzt und der neue Stand gespeichert. Logs enthalten einen Hinweis „Quota reset for new month …“.
* Sind die Limits erreicht, werden keine externen Requests mehr abgesetzt. Stattdessen erscheint eine Warnung „Quota reached, using existing cache. No files were modified.“ und bestehende Cache-/Zieldateien bleiben unverändert.
* `--dry-run` zeigt die aktuellen Zähler sowie Limits im Log an und verändert weder State noch Ausgabedateien.

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

Für manuelle Tests gegen die API muss der Header `X-Goog-Api-Key` gesetzt sein:

```
curl \
  -H "X-Goog-Api-Key: ${GOOGLE_ACCESS_ID}" \
  -H "X-Goog-FieldMask: places.id" \
  "https://places.googleapis.com/v1/places:searchNearby" \
  -d '{"includedTypes": ["train_station"], "locationRestriction": {"circle": {"center": {"latitude": 48.2082, "longitude": 16.3738}, "radius": 2000}}}'
```

Zusatzoptionen:

* `--dump-new data/new_places.json` – schreibt nur neue & aktualisierte Einträge in eine separate Datei (hilfreich für Review/Artefakte).
* `--tiles-file tiles.json` – überschreibt `PLACES_TILES` mit einer lokalen Datei.

## Zugang schnell prüfen

Bevor der eigentliche Import läuft, kann der API-Schlüssel mit einem leichten Health-Check validiert werden:

```
python scripts/verify_google_places_access.py
```

Das Skript lädt die Standard-Konfiguration, fragt eine einzelne Kachel ab und bricht mit konkreten Hinweisen ab, falls `places.googleapis.com` blockiert oder der Key ungültig ist. Bei Erfolg erscheinen Log-Einträge „Places API access verified …”.

## Troubleshooting

* **Fehlender API-Key** → Skript bricht mit Exit-Code 2 ab und weist auf `GOOGLE_ACCESS_ID` hin.
* **PERMISSION_DENIED „Requests … are blocked.”** → API-Schlüssel berechtigt nicht für `places.googleapis.com`. In der Google Cloud Console die *Places API (New)* aktivieren und in den API-Restriktionen `https://places.googleapis.com` zulassen.
* **429/5xx** → automatische Retries mit exponentiellem Backoff. Bei dauerhaften Fehlern prüfen: Quoten, Billing, Projektrechte.
* **Schema-Warnungen** → Log-Level WARN signalisiert übersprungene Kacheln/Antworten; Daten bleiben unangetastet.
* **Dry-Run vs. Write** → `--dry-run` und `--write` schließen sich aus. Ohne `--write` wird keine Datei geändert.

## Automatisierung

Ein GitHub-Workflow (`.github/workflows/update-google-places-stations.yml`) führt regelmäßig einen Write-Run aus, nutzt das Secret `GOOGLE_ACCESS_ID` und lädt ein Artefakt mit den Änderungen (`--dump-new`).

### Preflight

Der Workflow führt vor dem eigentlichen Fetch einen minimalen `places:searchText`-Preflight aus. Damit wird schnell erkannt, ob der API-Key wegen Restriktionen oder fehlendem Billing blockiert ist. Die Anfrage setzt `X-Goog-FieldMask: places.id`, läuft mit einem Timeout von 20 Sekunden und versucht es bis zu drei Mal mit kurzen Backoffs. Sensible Daten werden nicht ausgegeben; der Key selbst erscheint nicht im Log.

Zusätzlich validiert ein Nearby-Preflight (`places:searchNearby`) den Request-Body. Er sendet eine einzelne Kachel mit `includedTypes=["train_station"]`, `maxResultCount=1` und dem FieldMask-Header `places.id,places.displayName,places.location`. Dadurch erkennt der Workflow ungültige Typen oder Feldmasken, bevor der eigentliche Import startet. Für die Abfrage werden kompatible Place-Typen (`train_station, subway_station, bus_station`) via ENV erzwungen. Schlägt der Preflight fehl, liefert ein anschließender Debug-Step (`if: failure()`) einmalig die komprimierte Server-Antwort zur Analyse.

## Migration

* Neue Setups sollten ausschließlich `GOOGLE_ACCESS_ID` pflegen.
* Bestehende Installationen mit `GOOGLE_MAPS_API_KEY` funktionieren weiterhin, erzeugen jedoch eine Log-Warnung. Sobald `GOOGLE_ACCESS_ID` gesetzt ist, wird automatisch auf den neuen Schlüssel umgestellt.
