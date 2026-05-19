---
title: "Google Places Stations Import"
description: "Anleitung zum Tier-3-Fallback-Abruf von Bahnhofs- und Haltestellendaten über die Google Places API in den lokalen Stationskatalog (OSM primär, HAFAS als Tier-2-Fallback davor)."
---

# Google Places Stations Import

> ⚠️ **Status: Tier-3-Fallback (Notausgang).** Google Places ist **nicht** die primäre Datenquelle für `data/stations.json` — und seit 2026-05-14 auch nicht mehr der einzige Fallback. Die kanonische Anreicherungskette ist drei-stufig:
>
> 1. **Tier 1 — OpenStreetMap (Overpass API).** Befüllt alle Stationen, die ohne Koordinaten aus dem ÖBB-Verzeichnis ankommen — siehe `docs/architecture.md` §5.
> 2. **Tier 2 — HAFAS (ÖBB Scotty).** `scripts/update_station_directory.py:_enrich_with_hafas` läuft direkt im Anschluss über jede Station ohne OSM-Koordinaten. Liefert hochpräzise Koordinaten und die EVA-Nummer (`hafas_extId`); ist nicht durch ein Tagesbudget limitiert und schont damit das Google-Kontingent. Implementiert in `src/places/hafas_client.py`, abgesichert durch einen eigenen `CircuitBreaker` und eingebettet in `request_safe`.
> 3. **Tier 3 — Google Places.** Erst danach prüft `_stations_missing_coordinates`, welche Einträge **noch immer** keine `latitude`/`longitude` tragen. Nur diese strikte Restmenge wird über `_enrich_with_google_places(..., missing_subset=…)` an die Places API weitergereicht. Decken OSM und HAFAS gemeinsam alles ab, wird der Google-Aufruf vollständig übersprungen — das Monatskontingent bleibt unangetastet.
>
> Stationen, deren Koordinaten OSM oder HAFAS bereits aufgelöst haben, werden **nicht** neu verschlüsselt — selbst wenn ein Google Place denselben Namen trägt. Die Demotion ist absichtlich harsch: Open-Data-Erstanbieter (Overpass), gefolgt vom kostenfreien Operator-Backend (HAFAS), kommen vor dem kommerziellen Anbieter (Google).

Dieses Dokument beschreibt die *Mechanik* des Tier-3-Imports — den Quota-Manager, die Health-Checks und den Workflow. Wer einen reinen, vollständigen Stationskatalog mit Koordinaten benötigt, sollte zuerst den OSM-Pfad verifizieren (siehe `scripts/check_overpass_status.py`) und das HAFAS-Profil prüfen (`data/hafas_profile.json`, befüllt durch `scripts/sync_hafas_profile.py`); Google Places wird nur dann ausgelöst, wenn beide vorgelagerten Tiers nachweislich Lücken hinterlassen.

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

* Limits werden über folgende ENV-Variablen gesteuert (Defaults in Klammern): `PLACES_LIMIT_TOTAL` (4000), `PLACES_LIMIT_NEARBY` (1500), `PLACES_LIMIT_TEXT` (1500), `PLACES_LIMIT_DETAILS` (1000), `PLACES_LIMIT_DAILY` (200).
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

Die drei-stufige Kaskade (OSM → HAFAS → Google Places) läuft automatisch als Schritt in `.github/workflows/update-stations.yml` (wöchentlich, Sonntag 01:00 UTC). Direkt vor dem Stations-Refresh aktualisiert ein eigener Schritt `Synchronize HAFAS Profile` das Mgate-Profil-Sidecar `data/hafas_profile.json`, damit ÖBB-seitige Credential-Rotation transparent nachgezogen wird. Der Google-Schritt nutzt anschließend das Secret `GOOGLE_ACCESS_ID` und ruft Google Places ausschließlich für die Restmenge auf, die weder OSM noch HAFAS auflösen konnten (`_stations_missing_coordinates` nach beiden Tiers). Ein separater Standalone-Workflow existiert nicht mehr — für Out-of-Band-Refreshes der gesamten Places-Anreicherung steht `python scripts/fetch_google_places_stations.py --write` als lokaler/CI-Direktaufruf bereit; der Operator akzeptiert dann bewusst den Quota-Verbrauch.

### Preflight (historisch, Workflow entfernt 2026-05-11)

Der frühere Standalone-Workflow `update-google-places-stations.yml` führte vor dem eigentlichen Fetch einen minimalen `places:searchText`-Preflight aus. Damit wird schnell erkannt, ob der API-Key wegen Restriktionen oder fehlendem Billing blockiert ist. Die Anfrage setzt `X-Goog-FieldMask: places.id`, läuft mit einem Timeout von 20 Sekunden und versucht es bis zu drei Mal mit kurzen Backoffs. Sensible Daten werden nicht ausgegeben; der Key selbst erscheint nicht im Log.

Zusätzlich validierte ein Nearby-Preflight (`places:searchNearby`) den Request-Body — siehe die Historie von `update-google-places-stations.yml` vor der Entfernung 2026-05-11. Bei einem direkten Aufruf von `scripts/fetch_google_places_stations.py` werden ungültige Typen oder Field-Masks vom Python-Client beim ersten echten Request sichtbar; das Quota-Stateful-Modul (`src/places/quota.py`) schützt das Monatsbudget.

## Migration

* Neue Setups sollten ausschließlich `GOOGLE_ACCESS_ID` pflegen.
* Bestehende Installationen mit `GOOGLE_MAPS_API_KEY` funktionieren weiterhin, erzeugen jedoch eine Log-Warnung. Sobald `GOOGLE_ACCESS_ID` gesetzt ist, wird automatisch auf den neuen Schlüssel umgestellt.

## Drei-Tier-Fallback-Reihenfolge

Im Cron-Pfad `scripts/update_station_directory.py` läuft die Anreicherung in genau dieser Reihenfolge:

1. **Tier 1 — OSM.** `--osm-enrich` (Default an) ruft `fetch_osm_places()` auf. Ergebnisse werden über `merge_places()` mit Distanzschwelle `MERGE_MAX_DIST_M` (150 m Default) verschmolzen. Stationen erhalten `source="osm"`.
2. **Tier 2 — HAFAS.** `_enrich_with_hafas` iteriert über die Restmenge ohne OSM-Koordinaten und ruft je Station `enrich_station_with_hafas()` aus `src/places/hafas_client.py` auf. Treffer setzen `latitude`/`longitude`, persistieren `hafas_extId` auf der Station und ergänzen `source` um den Token `hafas`. Ein Ausfall (Breaker offen, Netzwerkfehler, fehlendes Profil) liefert pro Station `None` zurück — das Skript läuft weiter.
3. **Tier 3 — Google Places.** Wenn `--google-enrich` aktiv ist, ermittelt das Skript erneut `_stations_missing_coordinates(stations)` nach dem HAFAS-Lauf. Nur diese reduzierte Liste wird an `_enrich_with_google_places(stations, tiles_file=…, missing_subset=missing)` übergeben.
4. Ist die Liste leer, erscheint im Log `HAFAS resolved every remaining station; skipping Google Places enrichment` bzw. `Skipping Google Places enrichment: OSM already covered all <N> stations with coordinates` — kein Outbound-Request, kein Quota-Verbrauch.
5. Wenn der CI-Workflow den Overpass-Smoke-Check (`scripts/check_overpass_status.py`) als fehlgeschlagen meldet, wird `WIEN_OEPNV_OSM_ENRICH=0` gesetzt; HAFAS übernimmt dann zuerst alle Stationen aus dem ÖBB-Excel und Google Places kümmert sich nur um die Restmenge, die auch HAFAS nicht auflösen konnte (klassischer Notfall-Pfad).

Damit ist garantiert, dass Google Places nie als „Hauptquelle" arbeitet, sondern ausschließlich Lücken stopft, die weder der primäre OSM-Pfad noch das HAFAS-Tier schließen konnten.
