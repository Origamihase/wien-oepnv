# Entwicklerdokumentation – Wien ÖPNV Feed

Diese Anleitung bündelt sämtliche entwicklungsrelevanten Inhalte des Projekts:
Setup, lokale Workflows, CLI, Konfiguration, Provider-Logik, Stationsverzeichnis,
Sicherheit sowie die zugehörigen GitHub-Actions-Pipelines. Wer das Projekt
einfach nur konsumieren möchte, findet im [README](https://github.com/Origamihase/wien-oepnv/blob/main/README.md) eine
verdichtete Übersicht. Hinweise zum Beitrag (Branching, PRs, Pre-Commit) stehen
in der [`CONTRIBUTING.md`](https://github.com/Origamihase/wien-oepnv/blob/main/CONTRIBUTING.md).

## Inhaltsverzeichnis

- [Projektziele](#projektziele)
- [Systemüberblick](#systemüberblick)
- [Repository-Gliederung](#repository-gliederung)
- [Installation & Setup](#installation--setup)
- [Entwickler-CLI](#entwickler-cli)
- [Konfiguration des Feed-Builds](#konfiguration-des-feed-builds)
- [Feed-Ausführung lokal](#feed-ausführung-lokal)
- [Provider-spezifische Workflows](#provider-spezifische-workflows)
- [Nutzung als Datenquelle in Drittprojekten](#nutzung-als-datenquelle-in-drittprojekten)
- [Stationsverzeichnis](#stationsverzeichnis)
- [Automatisierte Workflows](#automatisierte-workflows)
- [Skripte im Überblick](#skripte-im-überblick)
- [Entwicklung & Qualitätssicherung](#entwicklung--qualitätssicherung)
- [Developer Experience & Observability](#developer-experience--observability)
- [Authentifizierung & Sicherheit](#authentifizierung--sicherheit)
- [VOR / VAO ReST API Dokumentation](#vor--vao-rest-api-dokumentation)
- [Repository-SEO & Promotion](#repository-seo--promotion)
- [Troubleshooting](#troubleshooting)
- [Audits & historische Reviews](#audits--historische-reviews)

## Projektziele

- **Zentrale Datenaufbereitung** – Störungsmeldungen, Baustellen und Hinweise mehrerer Provider werden vereinheitlicht,
  dedupliziert und mit konsistenten Metadaten versehen.
- **Reproduzierbarer Feed-Build** – Sämtliche Schritte (Cache-Aktualisierung, Feed-Generierung, Tests) lassen sich lokal oder in
  CI/CD-Workflows reproduzieren.
- **Nachvollziehbare Datenbasis** – Alle externen Datenquellen, Lizenzen und Skripte zur Pflege des Stationsverzeichnisses sind
  dokumentiert und versioniert.

## Systemüberblick

> **🪶 Architektur-Karte für neue Mitwirkende:** Eine visuelle
> Erklärung der Fetch-Pipeline, der `request_safe`-Sicherheitskette
> und der Resilience-Schichten findet sich in
> [`docs/architecture.md`](architecture.md) (mit Mermaid-Diagrammen).

Der Feed-Build folgt einem klaren Ablauf:

1. **Provider-Caches** – Je Provider existiert ein Update-Kommando (`python -m src.cli cache update <provider>`) sowie eine GitHub Action, die den
   Cache regelmäßig aktualisiert (`cache/<provider>/events.json`). Die Provider lassen sich über Umgebungsvariablen deaktivieren,
   ohne den restlichen Prozess zu beeinflussen.
2. **Feed-Generator** – `python -m src.cli feed build` liest die Cache-Dateien, normalisiert Texte, entfernt Duplikate und schreibt den
   RSS-Feed nach `docs/feed.xml`. Umfangreiche Guards gegen ungültige Umgebungsvariablen, Pfade oder Zeitzonen stellen stabile
   Builds sicher.
3. **Stationsdaten** – `data/stations.json` liefert vereinheitlichte Stations- und Haltestelleninformationen als Referenz für die
   Provider-Logik. Mehrere Skripte in `scripts/` und automatisierte Workflows pflegen diese Datei fortlaufend.
4. **Dokumentation & Audits** – Der Ordner `docs/` enthält Prüfberichte, API-Anleitungen und Audits, die das Verhalten des
   Systems transparent machen.

## Repository-Gliederung

| Pfad/Datei            | Inhalt                                                                                          |
| --------------------- | ------------------------------------------------------------------------------------------------ |
| `src/`                | Feed-Build, Provider-Adapter, Utilities (Caching, Logging, Textaufbereitung, Stationslogik).     |
| `scripts/`            | Kommandozeilen-Werkzeuge für Cache-Updates, Stationspflege sowie API-Hilfsfunktionen.            |
| `cache/`              | Versionierte Provider-Zwischenspeicher (`wl`, `oebb`, `baustellen`) für reproduzierbare Feed-Builds plus die Stammstrecke-Sidecars (`pending_trips.json`, `recently_finalised.json`, `episode_starts.json`, `poll_status.json`); VOR hat seit 2026-05-11 kein eigenes Cache-Verzeichnis mehr (siehe Hinweis unten). |
| `data/`               | Stationsverzeichnis, GTFS-Testdaten und Hilfslisten (z. B. Pendler-Whitelist).                   |
| `docs/`               | Audit-Berichte, Referenzen und Beispiel-Feeds (das VAO-Handbuch ist nur verlinkt, siehe unten). |
| `.github/workflows/`  | Automatisierte Jobs für Cache-Updates, Stationspflege, Feed-Erzeugung und Tests.                |
| `tests/`              | Umfangreiche Pytest-Suite (über 11 000 Tests in rund 640 Modulen, Stand 2026-10-10) für Feed-Logik, Provider-Adapter und Utility-Funktionen. |


> **Hinweis zu Cache-Pfaden:** Die tatsächlichen Verzeichnisse unter `cache/` tragen einen Hash-Suffix zur Cache-Versionierung (Stand Mai 2026: `cache/wl_9d709a/`, `cache/oebb_c40d21/`, `cache/baustellen_d438c3/`). In dieser Dokumentation werden aus Lesbarkeitsgründen verkürzte Schreibweisen wie `cache/wl/events.json` verwendet — sie verweisen jeweils auf das aktuelle Provider-Verzeichnis. Ein eigenes VOR-Cache-Verzeichnis existiert seit der 2026-05-11-Migration nicht mehr (VOR ist auf den Stammstrecken-Monitor beschränkt). Der **Stammstrecke-Monitor** schreibt seit der 2026-05-09-Migration **kein** `events.json` mehr — der Feed-Builder liest die Beobachtungen direkt aus dem CSV-Ledger `data/stats/stammstrecke_<YYYY>.csv`. Unter `cache/stammstrecke/` liegen weiterhin die internen Sidecar-Dateien `pending_trips.json` und `recently_finalised.json`, mit denen der Hbf-Producer (siehe [`docs/reference/stammstrecke_provider_logic.md`](reference/stammstrecke_provider_logic.md)) Trip-IDs zur Doppel-Vermeidung über mehrere Cron-Ticks hinweg trackt, dazu `episode_starts.json` (Beginn laufender Störungsphasen) und `poll_status.json` (Zeitpunkt des letzten erfolgreichen Abrufs, Grundlage der Health-Check-Prüfung `Stammstrecke-Abruf`).

## Installation & Setup

1. **Python-Version**: Das Projekt ist auf Python 3.11 ausgelegt (`pyproject.toml`).
2. **Abhängigkeiten installieren**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   # Für lokale Entwicklung (Tests, mypy, ruff, bandit, pip-audit):
   python -m pip install -r requirements-dev.txt
   ```
3. **Statische Analysen**: Die CI führt `ruff check` und `mypy` aus; lokal spiegelst du das Verhalten mit
   ```bash
   python -m src.cli checks
   ```
4. **Umgebungsvariablen**: Sensible Daten (Tokens, Basis-URLs) werden ausschließlich über die Umgebung gesetzt.
   Lokale `.env`-Dateien können über `WIEN_OEPNV_ENV_FILES` eingebunden werden.

   Der Befehl `python -m src.cli checks` führt neben `ruff` und `mypy` auch einen Secret-Scan
   aus (`python -m src.cli security scan`), sodass versehentlich eingecheckte Tokens früh auffallen.

## Entwickler-CLI

Für wiederkehrende Aufgaben steht eine gebündelte Kommandozeile zur Verfügung. Der Aufruf `python -m src.cli` bündelt die
wichtigsten Skripte und sorgt für konsistente Exit-Codes – ideal für lokale Reproduzierbarkeit oder CI-Jobs.

```bash
# Alle Provider-Caches sequenziell aktualisieren (Standardverhalten).
python -m src.cli cache update

# Nur ausgewählte Provider aktualisieren.
python -m src.cli cache update wl oebb

# Alle Provider explizit; beim ersten Fehler abbrechen statt
# alle Läufe durchzuführen.
python -m src.cli cache update --all --stop-on-error

# Feed generieren (äquivalent zu python -m src.build_feed).
python -m src.cli feed build

# Aggregierte Items auf strukturelle Probleme prüfen (kein Output-File).
python -m src.cli feed lint

# Zugangsdaten prüfen und beim ersten Fehler abbrechen.
python -m src.cli tokens verify --stop-on-error

# Alle bekannten Zugangsdaten explizit validieren.
python -m src.cli tokens verify --all

# Stationsverzeichnis prüfen und Bericht speichern; CI-äquivalentes
# Fail-on-Issues für eine harte Pipeline-Bremse.
python -m src.cli stations validate --output docs/stations_validation_report.md --fail-on-issues

# Ruff + mypy wie in der CI ausführen.
python -m src.cli checks --fix

# Interaktiven Konfigurationsassistenten starten (schreibt .env).
python -m src.cli config wizard

# Repository auf versehentlich eingecheckte Secrets prüfen.
python -m src.cli security scan
```

Die Unterbefehle akzeptieren standardmäßig alle bekannten Ziele (z. B. Provider `wl`, `oebb`, `baustellen`) und lassen sich bei Bedarf
präzise einschränken. Der Stations-Refresh-Wrapper `scripts/update_all_stations.py` (aufgerufen via
`python -m src.cli stations update all`) akzeptiert zusätzlich `--python`, um einen alternativen Interpreter für die internen
Sub-Skripte zu setzen — die unified CLI selbst kennt diese Option nicht.

## Konfiguration des Feed-Builds

Der Feed-Generator liest zahlreiche Umgebungsvariablen. Für den Einstieg empfiehlt sich der
Assistent `python -m src.cli config wizard`, der eine bestehende `.env` einliest, die relevanten
Schlüssel erklärt und wahlweise interaktiv oder per `--accept-defaults` eine neue Konfiguration
schreibt. Die wichtigsten Parameter:

| Variable                 | Zweck / Standardwert                                                            |
| ------------------------ | ------------------------------------------------------------------------------- |
| `OUT_PATH`               | Zielpfad für den RSS-Feed (Standard `docs/feed.xml`).                           |
| `FEED_HEALTH_PATH` / `FEED_HEALTH_JSON_PATH` | Zielpfade für die nach jedem Build erzeugten Health-Reports (Standards: `docs/feed-health.md` / `docs/feed-health.json`). Beide nicht im Repository versioniert. |
| `FEED_TITLE` / `FEED_DESC` | Titel und Beschreibung des Feeds (Standards: `"ÖPNV Störungen Wien & Pendler"` / `"Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen"`). |
| `FEED_LINK`              | Referenz-URL (nur http/https, Standard: GitHub-Repository).                     |
| `PAGES_BASE_URL`         | Basis-URL der GitHub-Pages-Site für absolute Permalinks (Standard `https://origamihase.github.io/wien-oepnv`). Wird gegen die Pages-Host-Allow-List validiert; abweichende Werte fallen auf den Standard zurück. |
| `MAX_ITEMS`              | Anzahl der Einträge im Feed (Standard 10).                                      |
| `MAX_ITEMS_PER_TOPIC`    | Höchstens so viele Einträge je Ursachenwort und Tag in den vorderen Plätzen; weitere rutschen hinter das Feld (Standard 3, 0 schaltet ab). Ohne eigene Variable gilt zusätzlich: Von mehreren ÖBB-Einträgen mit wortgleichem Titel (eine Strecke, mehrere Bauphasen) behält nur der mit dem frühesten Zeitfenster seinen Platz, eine laufende Störung auf dieser Strecke geht vor. |
| `UPCOMING_PREVIEW_DAYS`  | Was später als so viele Wiener Kalendertage nach heute beginnt, rückt hinter alles, was schon gilt (Standard 1: ab dem Vortag des Beginns vorn, freitags und samstags bis einschließlich Montag; höchstens 365). 0 lässt nur heute Beginnendes vorn. |
| `FEED_TTL`               | Cache-Hinweis für Clients in Minuten (Standard 15).                             |
| `MAX_ITEM_AGE_DAYS`      | Maximales Alter von Meldungen aus den Caches (Standard 365).                    |
| `ABSOLUTE_MAX_AGE_DAYS`  | Harte Altersgrenze für Meldungen (Standard 540).                                |
| `ENDS_AT_GRACE_MINUTES`  | Kulanzfenster für vergangene Endzeiten (Standard 10 Minuten), höchstens bis zur Wiener Mitternacht nach dem Tag, den die Zeitzeile nennt (`_drop_after` in `src/build_feed.py`, seit 2026-10-24). |
| `FRESH_PUBDATE_WINDOW_MIN` | Toleranzfenster (Minuten) für „frische" pubDates beim Aging-Check (Standard 5). |
| `CACHE_MAX_AGE_HOURS`    | Maximalalter der Provider-Cache-Dateien, ab dem eine Warnung im Log erscheint (Standard 24). |
| `FEED_TITLE_CHAR_LIMIT` / `DESCRIPTION_CHAR_LIMIT` | Maximale Zeichenzahl für Item-Titel/Beschreibungen (Standards 256 / 4000). Negative Werte werden auf `0` geklammert; eine obere Schranke wird derzeit nicht erzwungen (siehe `src/feed/config.py`). |
| `PROVIDER_TIMEOUT`       | Globales Timeout für Netzwerkprovider (Standard 25 Sekunden). Per Provider via `PROVIDER_TIMEOUT_<NAME>` oder `<NAME>_TIMEOUT` anpassbar. Der Standard ist zugleich die Obergrenze (`MAX_PROVIDER_TIMEOUT`, Schutz gegen hängende Upstreams): Werte lassen sich nur senken, höhere werden auf 25 Sekunden geklammert. |
| `PROVIDER_MAX_WORKERS`   | Anzahl paralleler Worker (0 = automatisch). Feiner steuerbar über `PROVIDER_MAX_WORKERS_<GRUPPE>` bzw. `<GRUPPE>_MAX_WORKERS`. |
| `WL_ENABLE` / `OEBB_ENABLE` / `BAUSTELLEN_ENABLE` / `STAMMSTRECKE_ENABLE` | Aktiviert bzw. deaktiviert die einzelnen Default-Provider (alle Standard: aktiv). `STAMMSTRECKE_ENABLE` steuert den VOR/VAO-basierten Verspätungs- und Ausfall-Monitor. Eine separate `VOR_ENABLE`-Variable existiert seit der 2026-05-11-Konsolidierung **nicht mehr**. |
| `WL_RSS_URL` / `OEBB_RSS_URL` / `BAUSTELLEN_DATA_URL` / `OVERPASS_URL` | Override der Upstream-URLs. Validiert gegen eine Allow-List bekannter Hosts; abweichende Werte werden ignoriert und der Default verwendet (siehe Modul-Docstrings für Details). |
| `OEBB_ONLY_VIENNA`       | Strikte ÖBB-Filterung (`1`/`true`/`0`/`false`, Standard `false`): nur Meldungen mit explizitem Wien-Bezug akzeptieren — keine Pendlerbahnhof-Fallbacks (siehe [`docs/reference/oebb_provider_logic.md`](reference/oebb_provider_logic.md)). |
| `WIEN_OEPNV_AT_RECONCILE` | Toggle (Standard `1`) für den netzgestützten Koordinaten-Abgleich der Stationen, die WL und ÖBB gemeinsam führen (WL → HAFAS, bei Widerspruch bleibt WL; seit 2026-10-17 ohne OSM-Schiedsrichter, `scripts/update_wl_stations.py`); `0` überspringt ihn (Wrapper-Test, Offline-Läufe). |
| `WIEN_OEPNV_MANUAL_ENRICH` | Toggle (Standard `1`) für die Nachreicherung manuell gepflegter Auslands-/Distant-AT-Knoten (`type=manual_*`) in `scripts/update_station_directory.py:_enrich_manual_stations`. Auf `0` gesetzt, um in Test-Sandboxen die ~296 realen HAFAS-Round-trips zu vermeiden (siehe `tests/test_update_all_stations_wrapper.py`). |
| `WIEN_OEPNV_PROVIDER_PLUGINS` | Komma-separierte Liste optionaler Provider-Plugin-Module (siehe [`docs/how-to/provider_plugins.md`](how-to/provider_plugins.md)). Standard leer; nicht gesetzte Module werden ignoriert. |
| `WIEN_OEPNV_ENV_FILES` | Komma-separierte Liste zusätzlicher `.env`-Dateien, die vor der Konfiguration eingelesen werden (`src/utils/env.py`). Standard liest `.env`, `data/secrets.env`, `config/secrets.env`. |
| `LOG_LEVEL`, `LOG_DIR`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`, `LOG_FORMAT` | Steuerung der Logging-Ausgabe (`log/errors.log`, `log/diagnostics.log`). `LOG_LEVEL` Standard `INFO`; `LOG_FORMAT=json` aktiviert JSON-Logs. |
| `STATE_PATH`, `STATE_RETENTION_DAYS` | Pfad (eine Datei, nur für den Feed-Build) & Aufbewahrungstage für `data/first_seen.json` (Standard 600 Tage = absolute Altersgrenze 540 + 60; seit 2026-09-12, vorher 60). |
| `WIEN_OEPNV_CACHE_PRETTY` | Steuert die Formatierung der Cache-Dateien (`1` = gut lesbar, `0` = kompakt). |
| `WIEN_OEPNV_DEBUG`       | Auf `1` gesetzt zeigt die CLI (`python -m src.cli`) bei Fehlern den vollständigen Traceback; Standard verhält sich fail-secure (keine Trace-Ausgabe). |
| `VOR_ACCESS_ID`          | **Pflicht-Secret** für den Stammstrecken-Monitor (VAO-Access-Token). Niemals committen — laden via `.env`, `data/secrets.env` oder `config/secrets.env`. Validierbar mit `python -m src.cli tokens verify vor`. |
| `VOR_BASE_URL`           | Optionales Secret: Basis-URL der VAO-ReST-API (validiert und auf den VAO-Host festgenagelt in `src/providers/vor.py:_validated_vor_base_url`). Leer gilt der Standard aus `src/providers/vor.py`. Legacy-Alias `VOR_BASE` (nur lokal; kein Workflow reicht ihn seit 2026-10-16 weiter). |
| `VOR_USER_AGENT`         | Custom User-Agent für VOR/VAO-API-Calls (Standard `wien-oepnv/1.0 (+https://github.com/Origamihase/wien-oepnv)`). |
| `VOR_REQUEST_COUNT_FILE` | Override für den Persistenzpfad des VAO-Tagesbudget-Counters (Standard `data/vor_request_count.json`). |
| `VOR_AUTH_TYPE`          | Erzwingt das Auth-Schema bei der VAO-Token-Normalisierung (`bearer` oder `basic`, Standard: automatische Erkennung aus dem Token-Format in `src/providers/vor.py:_normalise_access_token`). |
| `VOR_VERSION` / `VOR_VERSIONS` | Versions-String für die VAO-URL (z. B. `v1.12.0`); `VOR_VERSIONS` ist Fallback-Alias für `VOR_VERSION`. Optional, nur lokal: Kein Workflow reicht sie weiter (bis 2026-10-16 übergaben zwei Workflows ein nie angelegtes Secret `VOR_VERSION`), es gilt die Version, die der Stammstrecken-Monitor aus der VAO-Liste wählt (`cache/stammstrecke/vao_version.json`, seit 2026-10-29), sonst `DEFAULT_VERSION` aus `src/providers/vor.py`. Ist sie gesetzt, wählt der Monitor nicht. |
| `RAW_CAPTURE`            | `1` schreibt beim Abruf von WL, ÖBB und Baustellen die normalisierten Rohantworten und die verworfenen Meldungen nach `data/raw/<quelle>/` (`src/utils/raw_capture.py`, `data/raw/README.md`). Standard aus; gesetzt nur im Fetcher-Schritt von `update-cycle.yml`. |
| `BAUSTELLEN_TIMEOUT`     | Per-Request-Timeout (Sekunden) für `scripts/update_baustellen_cache.py` (Standard `20`, hart geklammert auf `MAX_BAUSTELLEN_TIMEOUT`). |
| `BAUSTELLEN_FALLBACK_PATH` | Pfad zur lokalen JSON-Fallback-Datei (Standard `data/samples/baustellen_sample.geojson`), die verwendet wird, wenn der OGD-Endpoint der Stadt Wien nicht erreichbar ist und noch kein Cache existiert. |
| `SITE_BASE_URL`          | Basis-URL für die Sitemap-Generierung (`scripts/generate_sitemap.py`). Standard identisch mit `PAGES_BASE_URL`; gegen die GitHub-Pages-Allow-List validiert. |
| `WIEN_TOKEN`             | Override des Wien-Token-Matches für die `in_vienna`-Heuristik in `src/utils/stations.py` (Standard `wien`). Diakritik wird automatisch geklammert; nur für Test-Sandboxen interessant. |

Alle Pfade werden durch `resolve_env_path` (in `src/feed/config.py`) auf `docs/`, `data/` oder `log/` beschränkt, um Path-Traversal zu verhindern.

### Reihenfolge im Feed

Vorher, gleich nach dem Einsammeln, verwirft `_drop_test_messages`
Testmeldungen der Anbieter: Titel oder Text enthält „Testmeldung“, oder
Titel bzw. Text sind höchstens fünf Wörter lang und enthalten das Wort
„Test“ (ein Bindestrich gehört zum Wort: „Test-Fahrten“ ist keine
Testmeldung). Seit 2026-10-03 auch „Testfall“, „Testtext“ und ein Text
beliebiger Länge, der nach dem Linienpräfix nur aus „Test“ besteht
(„Test Test Tes Test Test Test“). Anlass: Zwei Testmeldungen der Wiener Linien standen am 23.09.2026
je einen Zyklus im Feed („71/72: Dies ist eine Testmeldung“, „62: F57f
Test“). In 488 Meldungen des Feeds und 952 der Caches traf die erste Fassung
nur diese beiden; „Haltestelle“ oder „Testbetrieb“ enthalten das Wort nicht.
Die heutige Regel trifft in der ganzen Cache-Historie 26 Titel, alle Tests.

Nach Altersfilter und beiden Dedupe-Stufen legt `_merge_wl_ticker_clusters`
die WL-Störungen eines Vorfalls zusammen: dieselben Linien, veröffentlicht
innerhalb von zehn Minuten nach der ersten. Titel ist Linie plus häufigste
Ursache, die Beschreibung sammelt alle Folgen („62: ÖBB Bauarbeiten“ über
„Betrieb ab Kliebergasse; Züge halte bei Linie 18, Richtung Burggasse; Kein
Betrieb.“). Sagt eine ausführliche Meldung der Gruppe mehr als WLs
Standardsatz, steht stattdessen sie, so wie allein (Details in
`docs/architecture.md`). Kurzmeldungen einzelner Linien gehen in die
ausführliche Meldung eines Vorfalls mehrerer Linien auf, wenn sie dieselbe
Ursache nennen und höchstens zehn Minuten vor ihr begannen
(`_join_part_line_tickers`, seit 2026-10-15; „30A/31A/36B:
Gasrohrgebrechen“ stand am 11.10. sonst neben drei Tickern „Gasrohrbruch“).
Zeigt ein solcher Eintrag nur die Baustelle eines
WL-Hinweises (gleiche Ursache, Linien, Zeitraum und eine gemeinsame Straße),
geht er in diesem Hinweis auf (`_absorb_works_tickers`). Meldet umgekehrt
eine laufende WL-Störung mit Vorfallsbeginn (ausführliche Meldung) dieselbe
Ursache an derselben Straße für jede Linie eines geltenden Hinweises, tritt
der Hinweis zurück, solange sie läuft (`_yield_notices_to_incidents`, seit
2026-11-01, Betreiberentscheidung „Störung bleibt“ und Grundsatz „Störungen
haben höchste Priorität im Feed! Hinweise sind weniger wichtig.“; Anlass „1/2/71/D:
Nationalfeiertag“ neben „1/2/71/D: Veranstaltung“ am 26.10.). Tests dazu lesen die Meldungen wie der Feed, über
`_post_filter_wl`. Danach sortiert der Build die
Items nach `first_seen` (neueste zuerst; Gleichstand: Störung vor Baustelle,
dann `pubDate`). Davor stehen immer die aktuellen Störungen
(`_is_current_incident`, Betreiberentscheidung vom 2026-10-04: „Aktuelle
Störungen sollen oberste Priorität haben. Vorangekündigte Baustellen sollen
eine niedrigere Priorität haben.“): ungeplante Störungen, deren Zeitzeile
„[Seit hh:mm]“ zeigt (`_incident_since`), die begonnen haben und seit
höchstens `_CURRENT_INCIDENT_WINDOW` (24 h) im Feed stehen. Untereinander
gilt in beiden Gruppen dieselbe Sortierung. Ohne diese Stufe stand am
03.10. um 15:01 „43A: Veranstaltung“ vor „9A: Rettungseinsatz“, am 04.10. um
16:30 „U6: Neue Donau, kein Halt“ vor vier laufenden Störungen. Die 24 h
halten Langläufer, die wie Störungen klingen, in der normalen Reihenfolge
(„18: Haltestelle Stadionbrücke … aufgelassen“ seit 13.07., der täglich
neue „S80: ÖBB-Ersatzbus“ seit 25.09.). Im Nachbau von 36 Feed-Ständen (Wiener Zeit, Fr
02.10. 07:00–18:30, Sa 03.10. 15:00 bis So 04.10. 22:30) änderte sich in 14
nur die Reihenfolge innerhalb der zehn Plätze, keine Meldung kam dazu oder
fiel heraus. Eine neue Meldung zählt mit `first_seen` = jetzt, eine
WL-Haltestellenverlegung oder -auflassung dagegen ab ihrer Veröffentlichung
bei WL (`pubDate`, `_initial_first_seen`, seit 2026-10-04): Mit „Alle
aufnehmen“ kamen auf einen Schlag 24 solche Meldungen dazu, manche seit 2023
gültig, und hätten als neu alle zehn Plätze belegt. So steht jede dort, wo
sie ohne den alten Filter stünde; „26E/N20: Fultonstraße“ (veröffentlicht
28.09., ab 05.10.) zählt ab dem 28.09. und rückt wie jede Ankündigung erst
ab dem Vortag nach vorn. Im Feed-Nachbau der zehn echten Läufe vom
2026-10-04 (09:31–14:00 UTC) kamen so zwei Meldungen unter die ersten zehn
(„16A/N65: Grohnergasse“, „26E/N20: Fultonstraße“), keine Welle.

Wiederkehrende WL-Meldungen: Die WL-GUID besteht aus Kategorie, Thema und
Linien, ein Datum enthält sie nicht. „94A: Verkehrsunfall“ hat an jedem Tag
dieselbe GUID. Ohne Gegenmaßnahme erbt ein neuer Unfall das `first_seen` des
Unfalls vor Monaten und landet hinter allen Haltestellenverlegungen. Deshalb
setzt `_restart_recurring_occurrences` vor dem Altersfilter `first_seen` auf
den `pubDate` (Gültigkeitsbeginn) der Meldung, sobald zwei Bedingungen
zutreffen: `first_seen` liegt vor diesem Beginn, und die Meldung fehlte
zuletzt länger als `_OCCURRENCE_GAP` (2 h) in den Daten. Wann eine Meldung
zuletzt da war, steht im State-Feld `last_seen`. Der Build stempelt es bei
jedem Lauf für jedes WL-Item mit State-Eintrag. Laufende Maßnahmen, die WL
täglich mit neuem Gültigkeitsfenster neu ausgibt („Busse halten …“), waren
bis zum neuen Beginn da und behalten ihren Platz. Geplante Maßnahmen
(dieselben Wörter wie bei der Zeitzeile: Bauarbeiten, Veranstaltung,
verlegte Haltestelle usw.) pausieren länger, etwa „66A: Busse halten
Salvatorianerplatz“ jede Nacht von 01:00 bis 04:40; für sie gilt
`_PLANNED_OCCURRENCE_GAP` (36 h). Mit 2 h zählte 66A jeden Morgen als neu
und stand seit 26.09. in 370 von 387 Feed-Ständen vorn, obwohl es seit
28.08. im Feed war (Audit und Betreiberentscheidung 2026-10-03). ÖBB, Baustellen und
Stammstrecke bleiben unberührt, denn dort ist `pubDate` kein Beginn eines
Auftretens.

Eine Meldung, eine GUID (`_carry_item_identity`, seit 2026-10-06): Ein
zusammengelegter Eintrag trägt GUID und `pubDate` einer seiner Meldungen,
etwa der frühesten Kurzmeldung (`_merge_wl_ticker_clusters`) oder des
Überlebenden von `deduplicate_fuzzy`. Fiel genau diese Meldung weg,
während die übrigen blieben, bekam der Eintrag die GUID der nächsten, und
die Zeitzeile sprang. „43: Verkehrsunfall“ stand am 06.10. ab 00:30 als
„[Seit 00:03]“ im Feed; um 01:03 liefen die Kurzmeldungen aus, und der
Rebuild um 01:14 zeigte ohne neue Daten eine neue GUID und „[Seit 00:04]“.
Ebenso „1: Verkehrsunfall“ am 06.10. um 11:30 (ausführliche Meldung
erledigt, „[Seit 10:40]“ wurde „[Seit 10:43]“), „41/42: Verkehrsunfall“
am 05.10. um 01:30 (WL ersetzte die Störung durch je eine Folgemeldung pro
Linie) und „9: Falschparker“ am 05.10. um 19:01. Seither merkt sich der
Build nach jedem Lauf im State-Eintrag jedes Items, für welche Meldungen es
steht (`members`: deren GUIDs und, für WL, die Störungsnummer
`I20261004-0019` ohne `-F01`, die `wl_fetch` als `_wl_ids` in den Cache
schreibt), wann (`members_seen`) und bei WL-Störungen das früheste `pubDate`
(`earliest_published`). Ein Item, das in keinem Lauf der letzten
`_OCCURRENCE_GAP` (2 h) unter seiner eigenen GUID stand, übernimmt die GUID
des Items, mit dem es dort eine Meldung teilte, und dessen frühestes
`pubDate`, wenn seine eigene Meldung darin enthalten war oder nicht neuer
ist als dessen Beginn (WL trägt die ausführliche Meldung oft nach den
Kurzmeldungen ein und datiert sie zurück). Eine neuere Meldung ist eine
neue Störung, die eine laufende aufnimmt („52: Rettungseinsatz“ am 05.10.
mit einer Kurzmeldung vom 30.09.): Sie behält ihre GUID und steht vorn.
Tragen beide Seiten WL-Störungsnummern und teilen keine, ist es ebenfalls
eine neue Störung (`_other_incident`): Sie übernimmt weder GUID noch Beginn.
„13A/14A: Falschparker“ am 06.10.: `I20261006-0030` (Beginn 17:22) endete,
`I20261006-0033` (Beginn 17:44) kam unter derselben GUID und zeigte ab 18:30
„[Seit 17:22]“.
Ein Eintrag mehrerer Störungen einer Linie zählt nur die Meldungen der
neuesten; endet sie, kehrt er zur GUID der älteren zurück. Eine Meldung
ohne Ursache, die älter ist als jede dieser Störungen, zählt nie mit
(`_with_unowned`, seit 2026-10-09): Die Haltestellen-Meldungen der
Gleisbauarbeiten der Linie 25 (seit 23.09.) übernahmen sonst am 09.10.
nach dem Ende von „25: Verspätungen“ deren GUID und Beginn und standen von
12:01 bis 20:30 als neue Störung im Feed, bis auf Platz 2. Und ein Item, das
unter seiner eigenen GUID früher im Feed stand als das Item, mit dem es eine
Meldung teilt, übernimmt dessen GUID nie: Es war zuerst da, das andere hat
es aufgenommen. Der Zustand aus der Zeit vor der Regel hielt die
Haltestellen der Linie 25 sonst auch nach dem Merge unter der GUID der
Verspätung, im Lauf um 22:00 auf Platz 2, und jeder weitere Lauf hätte das
fortgesetzt. „Im Feed stand“ heißt: Ein Lauf hat den eigenen State-Eintrag
seit dessen `first_seen` notiert (`members_seen`), und dieses `first_seen`
ist älter als das des anderen. `first_seen` allein genügt nicht: Bei einer
wiederkehrenden WL-GUID ist es der Beginn der Meldung selbst
(`_restart_recurring_occurrences`), und WL datiert die lange Meldung zurück
(„60: Schadhafter PKW“ am 01.10., datiert 18:48, kam nach den Tafeln von
18:50 und trägt deren GUID weiter). Der erste Lauf
nach der Änderung schreibt nur die Notizen und ändert keine GUID, es gibt
also keine Welle. Im Feed-Nachbau aller 117 Läufe vom 04.10. 11:31 bis
06.10. 14:31 (Wiener Zeit, WL-Rohdaten neu durch `wl_fetch` geschickt, State
weitergetragen) unterschieden sich 11 Feed-Stände, alle an diesen Fällen.

Angekündigte Maßnahmen zählen ab ihrem Beginn als neu
(`_note_announced_starts`, `_sort_moment`, seit 2026-10-04), stehen aber wie
alles Geplante hinter den aktuellen Störungen: Solange der Beginn (`starts_at`) eines Items mit
State-Eintrag noch bevorsteht, merkt sich der Build ihn im State-Feld
`announced_start` (ein verschobener Beginn überschreibt ihn). Ist er
erreicht, sortiert das Item mit diesem Zeitpunkt statt mit seinem
`first_seen` und altert danach wie jedes andere. Vorher stand eine Wochen
früher angekündigte Sperre schon an ihrem ersten Tag hinten: die
S-Bahn-Stammstrecke Phase 2 (gesehen 10.06.) war am 07.09. auf keinem der
zehn Plätze, die ÖBB-Sperre Hbf–Gramatneusiedl (gesehen 08.07., gültig
03.–05.10.) stand am 04.10. auf Platz 49. Was erst ab seinem Beginn in den
Daten steht, bekommt kein `announced_start`; WL-Maßnahmen, die jede Nacht neu
ausgegeben werden („66A: Busse halten Salvatorianerplatz“), behalten so
ihren Platz. Im Nachbau (Wiener Zeit) Fr 02.10. 21:00 bis Sa 03.10. 10:30 (33 Stände, State
weitergetragen) änderte diese Regel nur eines: die ÖBB-Sperre stand ab
Samstag 00:00 auf Platz 1 bis 4, auch mit der Störungsstufe davor. Mi 30.09. 20:00 bis Do 01.10. 11:30 (32 Stände)
blieb unverändert.

Danach greifen vier Regeln, die Plätze freihalten, ohne etwas zu
löschen. Alle vier stellen Items nur hinter das Feld, von wo sie nachrücken:

1. `_defer_repeated_route_titles`: Von mehreren ÖBB-Items mit wortgleichem
   Titel (dieselbe Strecke in mehreren Bauphasen, z. B. dreimal
   `Wien Hauptbahnhof ↔ Gramatneusiedl`) bleibt nur das mit dem frühesten
   Zeitfenster vorn. Zusammengeführt wird nicht — die Phasen sind
   verschiedene Maßnahmen. Seit 2026-10-05 behält eine laufende Störung
   (`_is_current_incident`) den Platz ihres Titels: ÖBB benennt eine
   Störung wie eine Bauphase nach der Strecke, und eine laufende Bauphase
   hat immer das frühere Fenster. Ein Unfall zwischen Wien Hbf und Wien
   Westbahnhof wäre sonst neben den Bauarbeiten dort (11.09. bis
   01.11.2026) hinter das Feld gerutscht. Seit Juli kam das noch nicht vor.
2. `_apply_topic_budget`: höchstens `MAX_ITEMS_PER_TOPIC` Einträge je
   Ursachenwort und Tag in den vorderen Plätzen.
3. `_defer_upcoming_items`: Was erst nach `UPCOMING_PREVIEW_DAYS` Tagen ab
   heute (Wiener Kalendertag, Standard 1) beginnt, steht hinter allem, was
   gilt oder bald beginnt. Betreiberentscheidung vom 02.10.2026: Am 02.10.
   um 18:00 belegten fünf der zehn Plätze Meldungen, die noch nicht
   begonnen hatten („20A: Bauarbeiten“ ab 13.10., die Sperre der R 40 ab
   31.10.), während „N71: Ersatzverkehr“ und „62: ÖBB Bauarbeiten“, beide
   an diesem Abend gültig, auf den Plätzen 11 und 12 standen. Anfangs galten
   drei Tage Vorlauf. Nachgerechnet über 3.875 Feed-Stände seit 15.07.
   belegten Ankündigungen mit Beginn in ein bis drei Tagen so 1.442 Plätze
   in 1.083 Ständen, jedes Mal mit einer laufenden Meldung auf Platz 11 oder
   dahinter (Audit und Betreiberentscheidung 2026-10-03). Seitdem steht eine
   Ankündigung ab dem Vortag ihres Beginns vorn, freitags und samstags
   außerdem alles, was bis einschließlich Montag beginnt
   (`_preview_last_day`, Betreiberentscheidung „Wochenende mit“): Wer die
   Anzeige nur werktags sieht, liest die Umleitung ab Montag noch am Freitag.
   Die Sperre in vier Wochen rückt nach, sobald ein Platz frei ist.
4. `_defer_all_clear_items`: Entwarnungen der ÖBB („Aufhebung
   Verkehrseinschränkung: …“, „Aufhebung Streckenunterbrechung: …“) stehen
   hinter allen anderen Items. Betreiberentscheidung vom 25.09.2026: Eine
   laufende Störung ist wichtiger als eine Entwarnung. Eine Entwarnung ist
   aber besser als ein leerer Platz. Sie erscheint deshalb nur, wenn weniger
   als `MAX_ITEMS` andere Items vorliegen.

Erst dann schneidet `MAX_ITEMS` ab.

### Logging-Initialisierung als Bibliothek verwenden

Wird `build_feed` als Skript ausgeführt (`python -m src.cli feed build`), richtet es seine Logging-Handler automatisch über
`configure_logging()` ein. Beim Einbinden des Moduls in andere Anwendungen bleibt die globale Logging-Konfiguration ab
Python-Import unverändert; rufe in diesem Fall `src.build_feed.configure_logging()` explizit auf, bevor du die Feed-Funktionen
verwendest.

### Fehlerprotokolle

- Läuft der Feed-Build über `python -m src.cli feed build`, landen Fehler- und Traceback-Ausgaben automatisch in `log/errors.log` (rotierende Log-Datei, konfigurierbar über `LOG_DIR`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`). Ohne Fehler bleibt die Datei unberührt.
- Ausführliche Statusmeldungen (z. B. zum VOR-Abruf) werden zusätzlich in `log/diagnostics.log` gesammelt.
- Beim manuellen Aufruf der Hilfsskripte (bzw. `python -m src.cli cache update wl`) erscheinen Warnungen und Fehler direkt auf `stdout`. Für nachträgliche Analysen kannst du den jeweiligen Lauf zusätzlich mit `LOG_DIR` auf ein separates Verzeichnis umleiten.
- Setzt du `LOG_FORMAT=json`, schreibt das Projekt strukturierte JSON-Logs mit Zeitstempeln im Format `Europe/Vienna`. Ohne Angabe bleibt das klassische Textformat aktiv.

## Feed-Ausführung lokal

Vor produktiven oder manuellen Abrufen empfiehlt sich ein schneller
Vollständigkeitscheck der benötigten Secrets:

```bash
python -m src.cli tokens verify
```

Das Skript lädt automatisch `.env`, `data/secrets.env` und
`config/secrets.env` und bricht mit Exit-Code `1` ab, wenn kein gültiger
`VOR_ACCESS_ID`-Token gefunden wurde.

```bash
export WL_ENABLE=true
export OEBB_ENABLE=true
export BAUSTELLEN_ENABLE=true
export STAMMSTRECKE_ENABLE=true
# Stammstrecke-Monitor benötigt VOR-Secrets: VOR_ACCESS_ID, VOR_BASE_URL.
# Eine eigenständige VOR_ENABLE-Variable gibt es seit der 2026-05-11-
# Konsolidierung nicht mehr (VOR ist Stammstrecke-only).
python -m src.cli feed build
```

Der Feed liegt anschließend unter `docs/feed.xml`. Bei Bedarf lässt sich `OUT_PATH` auf ein alternatives Verzeichnis umbiegen.

## Provider-spezifische Workflows

Der Meldungsfeed sammelt offizielle Störungs- und Hinweisinformationen der Wiener Linien (WL) und der ÖBB sowie ergänzende Baustelleninformationen der Stadt Wien. Die VOR/VAO-API liefert seit 2026-05-11 keine Meldungen mehr, sondern nur noch die Messwerte des Stammstrecken-Monitors (Abschnitt unten).

### Wiener Linien (WL)

- **Anforderung**: "Alle Meldungen sind interessant." (Die Wiener Linien sind per Definition Wien-fokussiert).
- **Umsetzung**: Der Provider verarbeitet sämtliche Meldungen der Realtime-Schnittstelle (`trafficInfoList` mit `stoerunglang`/`stoerungkurz`, dazu `newsList`). Verworfen werden beim Abruf nur: inaktive Meldungen (seit 2026-10-05 auch Status „resolved“: so liefert WL eine erledigte Störung noch einen Abruf lang mit, vorher stand ihr alter Text bis zu 30 Minuten im Feed und verdeckte, wenn es eine gab, die aktive Folgemeldung `…-F01`), die Anzeigetafel-Kurzmeldungen (`stoerungkurz`) einer solchen erledigten Störung, solange WL sie noch schickt (seit 2026-10-05, `src/providers/wl_resolved.py`: die Kurzmeldung gehört zur jüngsten Meldung ihrer Art auf ihren Linien, die höchstens zehn Minuten nach ihr begann – geplante Maßnahme oder nicht nach `PLANNED_DISRUPTION_RE` im Titel –, und begann vor dem Ende der Störung; ihre Wörter zählen seit 2026-10-08 nicht mehr, vorher blieb etwa „6: Rettungseinsatz züge halten Favoritenstraße 113“ vier Stunden nach dem Abschluss im Feed; eine jüngere laufende Störung einer anderen WL-Nummer auf denselben Linien behält ihre Kurzmeldungen; ist die erledigte Meldung eine Folgemeldung „Nach einer Fahrtbehinderung …“ (`…-F01`), endete die Störung, als WL sie anlegte, und eine Kurzmeldung, die mehr als zwei Minuten danach beginnt, gehört zu einer neuen Störung; Kurzmeldungen haben keinen Status und liefen bis zu viereinhalb Stunden weiter; gemerkt über die Läufe in `data/wl_resolved_tickers.json`), Meldungen mit reinem Aufzug-/Fahrtreppen-Titel (seit 2026-10-09 zählt auch die Klapprampe der Züge dazu, Betreiberentscheidung „Auch verwerfen“: „U1: Klapprampensperre am 10.10.2026“, WLs einzige Schreibweise seit Juli; die Züge fahren normal), Meldungen über einen gesperrten Aufgang, Ausgang oder Zugang einer Station (seit 2026-10-09, Betreiberentscheidung „wie ein defekter Aufzug“; `is_station_access_only` in `src/utils/text.py`: der Titel nennt einen Aufgang, Ausgang, Zugang, Eingang oder Stiegen-/Treppenabgang, auch im Wortinneren wie „Aufgangssperre“, und keine Maßnahme für die Linie selbst wie „kein Halt“, „fahren durch“, „Ersatzverkehr“, seit der Prüfung vom 2026-10-09 auch die Zugwörter der ÖBB wie „Zugausfall“, „Verspätung“, „Zugangebot“, „eingleisig“, „Streckensperre“; vorher stand „U1: Nestroyplatz, Aufgangssperre“ ab 2026-10-09 im Feed, oft auf Platz 1 oder 2; ÖBB verwirft solche Titel ebenso, bisher schickte ÖBB keine), Meldungen außerhalb ihres Zeitraums, Störungen mit Ausschluss-Stichwort (`KW_EXCLUDE`: „Gewinnspiel“, „Eröffnung“, „Info“ …) ohne Einschränkungs-Stichwort, und Hinweise (`newsList`), deren Text kein Einschränkungs-Stichwort trägt (`KW_RESTRICTION` in `src/providers/wl_text.py`: Wortstämme wie „sperr“, „umleitung“, „kurzführung“ und seit 2026-10-04 auch die Maßnahme als Verb: „kurz geführt“, „umgeleitet“, „durchfahren“, „kein Halt“, „kein Betrieb“, „eingestellt“, „entfällt“; seit 2026-10-04 außerdem „verleg“ und „auflass“/„aufgelassen“: jede Haltestellenverlegung und -auflassung kommt in den Feed, Betreiberentscheidung „Alle aufnehmen“ vom 2026-10-04 – vorher kam sie nur durch, wenn ihr Grund „…arbeiten“ hieß, 13 von 37). Die Stichwörter prüfen den Text ohne HTML (`_gate_text` in `src/providers/wl_fetch.py`): WL liefert die Beschreibungen der Hinweise als HTML mit Umlauten als Entities („gef&uuml;hrt“), vorher traf darin kein Stichwort mit Umlaut. Beides zusammen hielt „18: LCC-Herbstmarathon am 11.10.2026“ und „U6: Neue Donau, kein Halt Richtung Floridsdorf“ aus dem Feed (Rohdaten vom 2026-10-04). Welche Meldung warum verworfen wurde, steht in `data/raw/wl/verworfen.json`; eine Klapprampe steht dort seit 2026-10-10 mit dem Grund „nur Klapprampe“, vorher als „nur Aufzug/Fahrtreppe“. Eine explizite Geo-Filterung ist nicht notwendig und findet nicht statt.
- **Quelle**: Realtime-Störungs-Endpoint (`WL_RSS_URL`, Default: `https://www.wienerlinien.at/ogd_realtime`).
- **Item-Link**: Jede WL-Meldung verlinkt seit 2026-10-17 auf die Betriebsinfo-Seite `https://www.wienerlinien.at/betriebsinfo` (`WL_INFO_URL` in `src/providers/wl_fetch.py`). Vorher stand dort die Adresse der Schnittstelle, die ein Browser mit 404 beantwortet (Live-Prüfung vom 17.10.). Eine eigene Seite je Meldung gibt es bei den Wiener Linien nicht; GUID, `first_seen` und die Reihenfolge ändern sich nicht. Seit demselben Tag prüft der Feed für jede Quelle, dass der Link beim Betreiber bleibt, und nennt die Quelle im Element `<source>` (`_ITEM_SOURCES`, [Architektur](architecture.md)).
- **Cache**: `cache/wl/events.json`.
- **Titel-Präfix**: Der Feed-Build parst gecachte Titel bei jedem Lauf neu (`_post_filter_wl`) und setzt die Linien aus `relatedLines` als `L1/L2:`-Präfix davor (`src/providers/wl_lines.py`). Wiederholt der Titeltext die Linienliste selbst (`4A. 80A, N29: …`, `N66, Rufbus N68: …`), wird sie in das Präfix gefaltet — Trenner `/`, `+`, `,` und `.` mit folgendem Leerraum, damit `13.10:` keine Linienliste ist; `Rufbus N68` im Text und `N68R` aus `relatedLines` gelten als eine Linie.
- **Störung ohne Linie**: `_post_filter_wl` verwirft eine Störung, deren Titel keine erkennbare Linie trägt (`Sperre Bahnsteig Richtung Siebenhirten`). Als Linie gilt ein Code mit Ziffer (`13A`, `N66`, auch die Rufbus-Form `44BR`), ein einzelner Buchstabe (`D`) oder seit 2026-10-03 die Badner Bahn `LB` (bis Juni 2026 `WLB`). Vorher fiel jede Badner-Bahn-Störung samt den Straßenbahnen desselben Vorfalls aus dem Feed: 15 verschiedene Störungstitel seit 15.07., darunter `1/18/62/LB: Signalstörung` am 10.09.
- **Sammel- und Teilmeldungen**: Nach der Bündelung entfernt der Provider eine Meldung für mehrere Linien, wenn jede ihrer Linien eine eigene Meldung derselben Kategorie mit überlappendem Zeitraum hat (E), und eine Meldung, deren Linien in einer solchen Meldung für mehr Linien stecken (F) — seit 2026-10-03 nur noch, wenn jedes Wort ihres Titels (ohne die Liniennummern) im Text der anderen steht oder sie (nur bei F) in Titel bzw. erster Zeile deren Thema nennt („37: Betrieb ab Nußdorfer Straße“ mit „Gleisbauarbeiten“ neben „5/12/37/…: Gleisbauarbeiten“). Vorher zählten nur Linien, Kategorie und Zeitraum: „2A: Bauarbeiten Renngasse“ fehlte zehn Tage, solange die Regenbogenparade auf 2A angekündigt war; seit April traf das 108 Meldungen. Eine Haltestellenverlegung oder -auflassung entfernen E und F nur, wenn die andere Meldung dieselbe Haltestelle betrifft (`_covers_stop_notice`, seit 2026-10-04): Ihr Titel ist nur die Haltestelle, und andere Meldungen nennen sie als Richtung („in Richtung Oper, Karlsplatz“) – so nahm die Verlegung Schellinggasse die von „3A: Oper, Karlsplatz“ mit. Eine Anzeigetafel-Kurzmeldung deckt nie eine ausführliche Störungsmeldung (mit WL-Nummer `I…`) ab (`_may_cover`, seit 2026-10-07): Ihr Titel ist die Ursache, die auch im Titel der ausführlichen steht, und so entfernte E „10, 60: Polizeieinsatz“ samt Maßnahmen und Dauer, weil zwei Kurzmeldungen „Polizeieinsatz“ nannten; der Feed zeigte 10 und 60 auf zwei Plätzen.

### ÖBB

- **Anforderung**:
  1. Pendlerbahnhöfe mit gestörter Verbindung nach Wien.
  2. Wien nach Pendlerbahnhof.
  3. Innerhalb von Wien (alle Störungen).
- **Umsetzung**: Der Provider implementiert einen **strengen Geo-Filter** (`_is_relevant`):
  - Nennt die Meldung Strecken („zwischen A und B“, „A ↔ B“), muss mindestens eine davon Wien ↔ Wien oder Wien ↔ Pendlerbahnhof sein; Wien ↔ fern, Pendler ↔ Pendler und Strecken mit unbekanntem Endpunkt fallen weg.
  - Ohne erkennbare Strecke reicht ein Wiener oder Pendler-Bahnhof im Text, solange kein ferner Bahnhof mitgenannt ist (so kommen „Polizeieinsatz in Mödling Bahnhof“ oder „Weichenstörung in Hollabrunn Bahnhof“ in den Feed, Anforderung 1); zuletzt prüft eine Text-Heuristik auf Wien-Bezug (U-Bahn usw.).
  - Bahnhöfe im Text sucht ein gleitendes Fenster über bis zu vier Wörter (`_find_stations_in_text`). Ein solches Bruchstück zählt seit 2026-10-05 nur über einen Stationsnamen oder Text-Alias mit mindestens drei Buchstaben, nie über einen Code oder eine ID (`station_named_in_text` in `src/utils/stations.py`). Vorher wurde „Stellwerkstörung am Bahnhof“ zur WL-Haltestelle „Am Bahnhof“, die Zugnummer „1730“ zur WL-Haltestellen-ID von „Klinik Hietzing“ und „Update 2 (…)“ zur Haltestelle „Venediger Au“; so kamen Störungen in Wolfurt, Ebensee, Hinterstoder, Telfs-Pfaffenhofen und Lind-Rosegg als Wien-Meldungen in den Feed (sechs seit Juli). Dieselbe Regel gilt für den Titel-Check `_title_has_unknown_endpoint` und für die Ortssuche der Statistik.
  - Bahnhofsnamen löst `station_info` (`src/utils/stations.py`) auch in ÖBB-Schreibweise auf: „Bruck/Leitha“ als „Bruck an der Leitha“, und seit 2026-10-04 einen Ortsnamen mit Fluss-, Regions- oder Landeszusatz („Mistelbach/Zaya“, „Wolkersdorf im Weinviertel“, „Traisen NÖ“) als den bloßen Ort, sofern dieser außerhalb Wiens liegt. Vorher galt „Wien Leopoldau ↔ Mistelbach/Zaya“ (S2) als Wien ↔ unbekannt und fiel weg.
  - Meldungen, die *nur* ferne Bahnhöfe erwähnen, werden verworfen, ebenso eine Strecke zwischen zwei Pendlerbahnhöfen („Pfaffstätten ↔ Bad Vöslau“, siehe [data/stations.json](https://github.com/Origamihase/wien-oepnv/blob/main/data/stations.json) für Definitionen von `in_vienna` und `pendler`). Ein einzelner Pendlerbahnhof ohne Strecke genügt dagegen (oben).
  - Vor dem Geo-Filter fallen Meldungen weg, deren Titel nur eine Anlage der Station betrifft (`_is_facility_or_weather_only` in `src/providers/oebb.py`): jeder Titel mit Aufzug-/Lift-/Fahrtreppen-Wort, seit 2026-10-09 auch ein gesperrter Aufgang, Ausgang, Zugang oder Eingang (`is_station_access_only`, wie bei WL), außer der Titel nennt zugleich, was die Züge tun („Zugausfall“, „Verspätungen“, „kein Halt“, „Schienenersatzverkehr“ …). Reine Wetterwarnungen ohne Störungswort im Titel fallen ebenso weg; „Sturmschaden: Strecke Wien-Mödling gesperrt“ bleibt. Den Grund jeder verworfenen ÖBB-Meldung hält `data/raw/oebb/verworfen.json` fest (`_drop_reason`), seit 2026-10-10 mit denselben Etiketten wie bei WL: „nur Aufzug/Fahrtreppe“, „nur Klapprampe“, „nur Aufgang/Ausgang“, dazu „nur Wetterwarnung“ und für den Geo-Filter „nicht Wien-relevant“; vorher stand dort für jede verworfene Meldung „nicht Wien-relevant“, auch für den defekten Personenlift in Wien Leopoldau.
  - Mit `OEBB_ONLY_VIENNA=1` lässt sich der Fallback auf reine Pendler-Bahnhof-Routen abschalten — siehe [`docs/reference/oebb_provider_logic.md`](reference/oebb_provider_logic.md).
- **Quelle**: Offizielle ÖBB-Störungsinformationen (RSS-Feed; Default-URL via `OEBB_RSS_URL` überschreibbar, validiert gegen die `fahrplan.oebb.at`-Allow-List).
- **Cache**: `cache/oebb/events.json`.
- **Routentitel**: Aus „zwischen A und B" bzw. „von A nach B" leitet der Provider `A ↔ B`-Titel ab. Ein großgeschriebener Ortszusatz mit „im"/„am" direkt vor `Bahnhof`/`Bf`/`Hbf` („Baumgarten im Bgld-Schattendorf Bahnhof") bleibt Teil des Endpunkts (`_with_place_qualifier`), sonst schrumpft der Endpunkt auf ein Wort und löst auf eine falsche Wiener Haltestelle auf.
- **Update-Meldungen**: ÖBB veröffentlicht laufende Störungen als „Update N (TT.MM.JJJJ hh:mm) Kategorie: Ort". Das Präfix wird entfernt (`_strip_update_prefix`), die Kategorie wie bei jedem ÖBB-Titel verworfen — außer bei Entwarnungen: „Aufhebung …: Ort" bleibt vollständig stehen.

### Verkehrsverbund Ost-Region (VOR)

- **Anforderung**: VAO-Tagesbudget (100 Requests/Tag) wird seit 2026-05-11 ausschließlich vom S-Bahn-Stammstrecken-Monitor verbraucht. Seit der 2026-05-15-Migration auf `/departureBoard` am Wien Hauptbahnhof sind das **48 Calls/Tag** (1 Hbf-Call × ~48 Cycles statt vorher 2 `/trip`-Calls × 48 Cycles). Ein automatisiertes Disruption-Polling existiert nicht mehr (Operator-Policy "VOR nur für die Stammstrecke").
- **Quelle**: VOR/VAO-ReST-API (`/departureBoard`-Endpunkt am Wien Hauptbahnhof), authentifiziert über Access Token.
- **Persistenz**: keine eigene JSON-Cache-Datei mehr; der Stammstrecken-Monitor schreibt Beobachtungen direkt in zwei CSV-Ledgers: `data/stats/stammstrecke_<YYYY>.csv` (aggregierte Verspätungen pro Richtung und Tick) und `data/stats/ausfaelle_<YYYY>.csv` (eine Zeile pro entdecktem Ausfall, dedupliziert via Pending-Trip-Ledger unter `cache/stammstrecke/` und je Abfahrt: ein Eintrag mit Zugnummer zur Minute eines Linieneintrags derselben Richtung ist derselbe Zug). Siehe [`docs/reference/stammstrecke_provider_logic.md`](reference/stammstrecke_provider_logic.md).

### Stadt Wien – Baustellen

- **Quelle**: Open-Government-Data-Baustellenfeed der Stadt Wien (`BAUSTELLEN_DATA_URL`, Default: offizieller WFS-Endpoint).
- **Cache**: `cache/baustellen/events.json`, gepflegt via `scripts/update_baustellen_cache.py`.
- **Ausfall**: Schlägt der Abruf fehl (z. B. wegen Rate-Limits), bleibt der bestehende Cache stehen (Exit-Code 1). Fällt nur einer der beiden WFS-Layer aus, kommt er aus seiner letzten guten Antwort unter `data/raw/baustellen/` (Exit-Code 3). Nur in einem Checkout ohne Cache nutzt das Skript `data/samples/baustellen_sample.geojson` als Grunddatensatz (Exit-Code 2). Details: `docs/architecture.md`, „Ausfall einer Quelle“.
- **Kontext**: Die Meldungen enthalten Metadaten zu Bezirk, Maßnahme, Zeitraum sowie geokodierte Adressen und ergänzen damit ÖPNV-Störungsmeldungen um bauzeitliche Einschränkungen.
- **Auswahl**: In den Feed kommt eine Baustelle nur, wenn ihr eigener Text sagt, was sich für Öffis ändert: eine Haltestelle verlegt oder aufgelassen, eine Linie umgeleitet oder kurzgeführt, der Straßenbahnbetrieb eingestellt, ein Schienenersatzverkehr (`mentions_oepnv` in `src/providers/baustellen.py`, geprüft beim Abruf und noch einmal beim Bauen des Feeds; Betreiberentscheidung 2026-10-06, „Nur mit Öffi-Folgen“). Nicht zählen: die Nähe zu einem Bahnhof (bis 2026-10-06 reichte eine Baustelle bis 150 m neben einem Bahnhof, etwa eine nächtliche Fahrstreifensperre am Rennweg); die U-Bahn als Bauherr („Im Zuge des U-Bahnbaus“, „Bauvorhaben U2 / U5“; in dieser Quelle seit Mai nie eine Folge für die U-Bahn selbst); Sätze, die Öffis als unberührt nennen („in der betriebslosen Zeit der Straßenbahn“, „nicht beeinträchtigt“, „einschließlich des öffentlichen Verkehrs … aufrecht gehalten“); der Verweis „Nähere Informationen … sind/ist der Auskunft der Wiener Linien … zu entnehmen“; der Firmenname „Wiener Linien“ und die „öffentliche Verkehrsfläche“ (die Straße). Gemessen an der Feed-Historie vom 23.05. bis 06.10.2026: Von 10.027 Plätzen gingen rund 4.800 an Baustellen ohne solche Folge; in einem Nachbau von 110 Ständen fielen 17 von 30 Baustellen-Plätzen weg, an ihre Stelle kamen WL-Meldungen und Baustellen mit echter Folge.
- **WL-Zwilling**: Meldet ein WL-Hinweis, der bereits gilt, dieselbe Maßnahme, fällt die Baustelle beim Bauen des Feeds weg (`_drop_baustellen_twins`, Betreiberentscheidung 2026-10-09, „WL-Meldung genügt“). Zwilling ist ein WL-Hinweis, dessen Ursachensatz („Wegen Bauarbeiten im Bereich Neilreichgasse # Davidgasse …“, „Aufgrund von Bauarbeiten in der Maxingstraße …“) die Straße der Baustelle nennt (Kopf des Titels, `site_street`; `names_site_street`), oder der dieselbe Adresse nennt wie der Baustellentext („nach Burggasse ONr. 69“ / „Nach: Burggasse 69“, `shares_address`). Eine Straße nur in einer Umleitungsstrecke, einer Haltestellenliste oder einem Haltestellennamen zählt nicht: Lange Straßen tragen mehrere Maßnahmen zugleich (Burggasse im Sommer: 48A St.-Ulrichs-Platz, Burggasse 25 → 27, und eine Flohmarkt-Umleitung des 13A). Anlass: „Burggasse 67“ (Haltestelle „von Burggasse ONr.67 nach Burggasse ONr. 69“) stand am 08./09.10. auf Platz 9; WL meldete dieselbe Verlegung als „48A: Neubaugasse, Burggasse“ mit Linie und Richtung. Ohne Zwilling bleibt eine Baustelle im Feed.
- **Verlängerung**: Die GUID enthält das Enddatum, und der State einer abgelaufenen Meldung wird gelöscht. Eine verlängerte Baustelle kommt deshalb mit neuer GUID zurück. Für die Reihenfolge zählt sie trotzdem nie später als an ihrem Starttag als neu (`_baustelle_start_cap`, seit 2026-10-09).
- **Beginn**: `OBJEKT_BEGINN` und `OBJEKT_ENDE` kommen als UTC-Datum eines Wiener Zeitpunkts (`2026-06-22Z`). Der Beginn (Wiener Mitternacht) fällt dabei auf den Vortag, das Ende (Ende des letzten Tages) bleibt. Belegt an den Baustellentexten selbst: 13 von 13 nennen als ersten Tag den Tag nach `OBJEKT_BEGINN`, 8 von 9 als letzten Tag genau `OBJEKT_ENDE`. Das Cache-Skript setzt den Beginn deshalb auf den Folgetag (`_first_day`, seit 2026-10-06); die GUID bleibt am gelieferten Datum, damit keine Baustelle neu erscheint. Liefert die Stadt das Datum eines Tages korrigiert, stünde der Beginn einen Tag zu spät: Dann nennen die Texte („Bauphase 1 (23.06.2026 …)“) wieder das Datum aus `OBJEKT_BEGINN`.
- **Leerer Cache**: Nach der Auswahl bleiben oft nur wenige oder gar keine Baustellen. Ein Live-Abruf schreibt das Ergebnis trotzdem, auch leer (`write_cache(..., allow_shrink=True)`, Exit 0); ob die Antwort der Stadt brauchbar war, prüft das Skript vorher je Layer (`_usable_layer`, letzte gute Antwort). Liefert die Stadt gar keine Baustelle, ist das ein Ausfall, kein ruhiger Tag (beide Layer führen immer Langläufer): Der Cache bleibt stehen, Exit 1. Leer werden darf er nur, wenn die Stadt Baustellen liefert und keine eine Folge für Öffis hat. Die 20-%-Sperre von `write_cache` gilt weiter für das Demo-Beispiel. Im Feed-Bericht steht ein leerer Baustellen-Cache als `ok-empty` (`register_provider(..., empty_is_normal=True)`), es sei denn, die Cache-Datei fehlt oder ist unlesbar.
- **Titel**: Die Stadt kappt `BEZEICHNUNG` bei 100 Zeichen; das Cache-Skript markiert den Schnitt mit „…" und lässt den Text unangetastet (die GUID leitet sich vom Rohtitel ab). Beim Bauen des Feeds vervollständigt `_repair_baustellen_title` (`src/build_feed.py`) ein abgeschnittenes Wort aus der Beschreibung desselben Items, wenn es dort eindeutig ist („… bis Rad…" → „… bis Radetzkybrücke"), und streicht den Platzhalter „Unbenannte Verkehrsfläche" aus einer Endpunkt-Liste, die auch einen echten Namen nennt. Danach stellt `_post_filter_baustellen` den Bahnhof, neben dem die Baustelle liegt (150 m), als Präfix voran; ein U-Bahn-Präfix („U2/U5: …“) gibt es seit 2026-10-06 nicht mehr, es las sich wie eine U-Bahn-Störung. Hausnummern stehen als Adresse („Rennweg 33A–37“, „Altmannsdorfer Straße 76–76A“ statt „ONr.76 bis ONr.76A“). Im Text (und in WL-Titeln und -Texten) wird „ONr.“, „O.Nr.“ und „ON“ vor einer Hausnummer zu „Nr.“ („von Burggasse Nr. 67 nach Burggasse Nr. 69“, „von der Nr. 125 bis zur Nr. 141“; `_ordnungsnummer_as_nr`, seit 2026-10-09).

### Eigene Provider-Plugins

Zusätzliche Datenquellen lassen sich ohne Änderungen am Kerncode anbinden. Das
How-to [eigene Provider-Plugins anbinden](how-to/provider_plugins.md)
erläutert den Workflow und verweist auf das Skript
`scripts/scaffold_provider_plugin.py`, das ein lauffähiges Modul-Skelett
erzeugt. Aktivierte Plugins erscheinen automatisch im Feed-Health-Report und
können über `WIEN_OEPNV_PROVIDER_PLUGINS` gesteuert werden.

## Nutzung als Datenquelle in Drittprojekten

Das Repository stellt die aufbereiteten Meldungen nicht nur als RSS-Feed bereit, sondern bietet auch stabile JSON-Datensätze und
wiederverwendbare Python-Helfer für die Integration in andere Anwendungen.

### Schnellstart für Datenkonsumenten

1. Repository klonen und in ein virtuelles Environment wechseln (`python -m venv .venv && source .venv/bin/activate`).
2. Projektabhängigkeiten installieren (`python -m pip install -r requirements.txt`).
3. Die gewünschten Cache-Dateien unter `cache/<provider>/events.json` konsumieren oder die Python-Helfer aus `src/` nutzen.

Die Cache-Dateien werden von den GitHub-Actions regelmäßig aktualisiert und enthalten ausschließlich strukturierte JSON-Listen.
Sie sind damit ohne zusätzlichen Build-Schritt sofort für externe Automationen verwendbar.

### Programmgesteuerter Zugriff via Python

Für Python-Anwendungen existieren zwei bequeme Zugriffspfade:

- **Direkter Cache-Zugriff** – `src.utils.cache.read_cache()` liest die zwischengespeicherten Provider-Events als Python-Liste
  von Dictionaries ein (Wrapper wie `src.build_feed.read_cache_wl()` sind bereits vorkonfiguriert für „wl", „oebb" und
  „baustellen"; zusätzlich erzeugt `src.build_feed.read_cache_stammstrecke()` die Stammstrecke-Events on-the-fly aus dem
  CSV-Ledger statt aus einer Cache-Datei). VOR hat seit 2026-05-11 keine eigene JSON-Cache-Datei mehr (siehe `cache/`-Hinweis oben).
- **Live-Abruf der Provider** – Die Module `src.providers.wl_fetch` und `src.providers.oebb` stellen jeweils eine Funktion
  `fetch_events()` bereit, die die Rohdaten der Wiener Linien bzw. ÖBB direkt normalisiert. `src.providers.vor` ist seit der
  2026-05-11-Konsolidierung **kein Disruption-Provider** mehr — das Modul exportiert nur noch
  Authentifizierungs- und Quota-Helfer (`VorAuth`, `apply_authentication`, `load_request_count`, `save_request_count`,
  `refresh_access_credentials`, `refresh_base_configuration`) für den Stammstrecken-Monitor. Eine eigenständige
  `fetch_events`-Funktion gibt es nicht mehr.

Minimalbeispiel für den Cache-Zugriff:

```python
from src.utils.cache import read_cache

wl_events = read_cache("wl")
for event in wl_events:
    print(event["title"], event["starts_at"])
```

### Datenformat der Ereignisse

Unabhängig vom Provider folgen alle Ereignisse derselben Struktur, die auch im Feed verwendet wird. Die Zeitfelder liegen
im JSON-Cache als ISO-8601-Strings (bzw. bei direkter Python-Nutzung als `datetime`-Objekte) vor und dürfen `null` sein.
Die wichtigsten Felder sind:

| Feld        | Beschreibung                                                                                  |
| ----------- | --------------------------------------------------------------------------------------------- |
| `source`    | Ursprungsquelle der Meldung (`"Wiener Linien"`, `"ÖBB"`, `"VOR/VAO"`, …).                      |
| `category`  | Typ der Meldung, z. B. „Störung“, „Hinweis“, „Baustelle“.                                       |
| `title`     | Bereinigter, menschenlesbarer Titel mit Linienkürzeln.                                         |
| `description` | Ausführliche Beschreibung inkl. Zusatzinfos wie Umleitungen, betroffene Haltestellen usw.     |
| `link`      | Referenz-URL zur Originalmeldung oder weiterführenden Infos.                                   |
| `guid`      | Stabile eindeutige Kennung, geeignet als Primärschlüssel.                                      |
| `pubDate`   | Veröffentlichungszeitpunkt der Meldung; `null`, wenn die Quelle keinen parsebaren Zeitstempel liefert (z. B. WL-Items ohne Zeitfeld). |
| `starts_at` | Technischer Startzeitpunkt der Maßnahme (häufig identisch mit `pubDate`); `null`, wenn nicht ermittelbar.                    |
| `ends_at`   | Optionales Ende der Maßnahme; `null`, wenn unbekannt oder bereits vergangen.                   |
| `first_seen` | Zeitpunkt, an dem die Meldung erstmals im Feed erschien (projektintern via `data/first_seen.json` gepflegt). Bei einer wiederkehrenden WL-Meldung zählt der Beginn des aktuellen Auftretens (siehe „Reihenfolge im Feed“). |
| `_identity` | Projektinterner Schlüssel zur Nachverfolgung des „first seen“-Zeitpunkts (optional vorhanden). |

Eine formale Beschreibung steht als [JSON-Schema](schema/events.schema.json)
bereit und eignet sich für Validierungen in Drittprojekten. Pflichtfelder
sind `source`, `category`, `title`, `description`, `link`, `guid`, `pubDate`
und `starts_at`; die Zeitfelder `pubDate`, `starts_at` und `ends_at` dürfen
`null` sein. Alle übrigen Werte sind Unicode-Strings; zusätzliche
provider-spezifische Hilfsfelder werden vor dem JSON-Export entfernt, sodass
die Datensätze stabil und schema-konform bleiben.

## Stationsverzeichnis

`data/stations.json` vereint ÖBB-, Wiener-Linien-, VOR- und manuell
gepflegte Auslandsknoten in einer Datei. Das Format ist als JSON Schema
unter [`docs/schema/stations.schema.json`](schema/stations.schema.json)
formal definiert; ein Pin-Test (`tests/test_stations_schema.py`, braucht
`jsonschema` aus `requirements-dev.txt`) verhindert Drift.

### Felder pro Eintrag

| Feld | Pflicht | Beschreibung |
| ---- | ------- | ------------ |
| `name` | ✓ | Operator-facing Anzeige-Name (wird im Feed verwendet). **Nicht unique**: zwischen mehreren Einträgen kann derselbe Name vorkommen, etwa bei multi-modalen Knoten, wo Bus- und Tram-Bahnsteige unter zwei DIVAs am selben Knoten geführt werden. Die strukturelle Eindeutigkeits-Garantie tragen `bst_id`/`bst_code`/`vor_id`/`wl_diva`; siehe PR #1452. |
| `in_vienna` | ✓ | `true` wenn die Koordinaten innerhalb des LANDESGRENZEOGD-Polygons liegen. |
| `pendler` | ✓ | `true` für Pendler-Knoten **außerhalb** Wiens (siehe `data/pendler_bst_ids.json`). **Exklusiv zu `in_vienna`**: jede Station ist entweder Wien-Station ODER Pendler, niemals beides. Ausnahmen sind manuell gepflegte Knoten außerhalb des Pendlergürtels: `type: manual_foreign_city` (z. B. München Hauptbahnhof, Roma Termini, Bratislava hl.st.) und `type: manual_distant_at` (z. B. Salzburg Hbf, Graz Hbf, Linz Hbf, Innsbruck Hbf) — bei beiden Sondertypen sind beide Flags `false`. Verstöße werden vom Validator als NamingIssue gemeldet und vom Updater automatisch korrigiert (in_vienna gewinnt). |
| `aliases` | ✓ | Schreibvarianten und IDs zur Erkennung in Provider-Texten. |
| `latitude` / `longitude` | ✓ | WGS84-Koordinaten (validiert gegen das Wien-Polygon für `in_vienna`-Einträge). |
| `source` | ✓ | Komma-getrennte Provider-Tokens (kein Whitespace, alphabetisch): `hafas`, `manual`, `oebb`, `oebb_geonetz`, `vor`, `wl`. Das Feld nennt alle beteiligten Quellen eines Eintrags, nicht nur die seiner Koordinate. `google_places` ist nur noch als Altwert zulässig; seit 2026-10-16 trägt kein Eintrag ihn, die Google-Stufe ist entfernt. Für `osm` gilt dasselbe seit 2026-10-17 (OSM-Stufe entfernt, `docs/architecture.md` §5). |
| `bst_id`, `bst_code` | ÖBB | ÖBB-Stellen-ID und -Stellencode aus dem Excel-Verzeichnis (data.oebb.at). |
| `vor_id` | ÖBB/VOR | VOR/VAO-Stop-ID (numerisch oder volles HAFAS-Token); entspricht typischerweise GTFS-`stop_id`. |
| `wl_diva` | WL | Wiener-Linien-DIVA aus `wienerlinien-ogd-haltestellen.csv`. |
| `wl_stops` | WL | Einzelhaltepunkte (Bahnsteige/Richtungen) inkl. eigener `stop_id`. |
| `wl_lines` | WL | Linien, die an mindestens einem der `wl_stops` halten (z. B. `["47A", "N49", "U4"]`), aus `wienerlinien-ogd-linien.csv` und `wienerlinien-ogd-fahrwegverlaeufe.csv`. Fehlt, wenn keine Liniendaten vorliegen. |
| `type` | – | Sondertyp für manuell gepflegte Knoten außerhalb des Pendlergürtels: `manual_foreign_city` für Auslandsknoten (München, Roma, Bratislava) und `manual_distant_at` für distante österreichische Hauptbahnhöfe (Salzburg, Graz, Linz, Innsbruck etc.). Bei beiden ist die Coordinate-Bounds-Prüfung tolerant. |

Lookups laufen über `src/utils/stations.py:station_info(name)` mit
diakritik-tolerantem Token-Normalizer (Umlaut-Faltung erst ab Token-Länge 4,
damit kurze Stellencodes wie `Sue`/`Su` distinkt bleiben).

### Datenquellen und Lizenzen

Die MIT-Lizenz gilt nur für den Code. Welche Lizenz für Feed-Texte, Statistik und
die Dateien unter `data/` gilt, fasst [`NOTICE.md`](https://github.com/Origamihase/wien-oepnv/blob/main/NOTICE.md) zusammen; beide
Feeds nennen die Quellen im `<copyright>`-Element des Kanals (Entscheidung
Michael, 16.10.2026).

| Quelle | Datei(en) | Lizenz | Pflicht-Attribution |
|---|---|---|---|
| **ÖBB-Verkehrsstationen** (`data.oebb.at`) | extrahiert aus dem Excel „Verzeichnis der Verkehrsstationen"; eine atomar-geschriebene Cache-Kopie liegt unter `data/oebb-verkehrsstationen.xlsx` (Soft-Fail-Snapshot seit PR #1450 — wird bei `data.oebb.at`-Outage automatisch verwendet). | [CC BY 3.0 AT](https://creativecommons.org/licenses/by/3.0/at/deed.de) (Datensatzseite auf `data.oebb.at`) | „Datenquelle: [ÖBB-Infrastruktur AG](https://infrastruktur.oebb.at/de/)" (der Name als Link, so verlangt es die Datensatzseite) |
| **ÖBB GeoNetz** (`data.oebb.at`) | `data/oebb_geonetz_stops.json` — kompakte Stops-Projektion (EVA-Nummer, IFOPT-ID), extrahiert von `scripts/extract_oebb_geonetz_stops.py` aus dem `GeoNetz_*.zip`. Source-Token `oebb_geonetz`. | [CC BY 3.0 AT](https://creativecommons.org/licenses/by/3.0/at/deed.de) (Datensatzseite auf `data.oebb.at`) | „Datenquelle: [ÖBB-Infrastruktur AG](https://infrastruktur.oebb.at/de/)" |
| **Wiener Linien OGD** | `data/wienerlinien-ogd-haltestellen.csv`, `data/wienerlinien-ogd-haltepunkte.csv`, `data/wienerlinien-ogd-linien.csv`, `data/wienerlinien-ogd-fahrwegverlaeufe.csv` (Quelle: `www.wienerlinien.at/ogd_realtime/doku/ogd/`, seit PR #1442; der vorherige `data.wien.gv.at/csv/`-Proxy wurde in der 60. OGD-Phase im September 2025 retired) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | „Datenquelle: Wiener Linien" |
| **Wiener Linien GTFS** (data.gv.at „Wiener Linien – Fahrplandaten GTFS Wien“) | `data/wienerlinien-gtfs-stops.csv` = die Datei `stops.txt` des Fahrplans (`www.wienerlinien.at/ogd_realtime/doku/ogd/gtfs/stops.txt`), wöchentlich von `update_wl_stations.py` geladen. Nur Ersatz, wenn die OGD-Haltepunkte einer DIVA außerhalb des WL-Netzes liegen (seit 2026-10-25, siehe „Manuelle Station-Overrides“). | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | „Datenquelle: Wiener Linien" |
| **VOR (Verkehrsverbund Ost-Region)** | `data/vor-haltestellen.csv`, `data/vor-haltestellen.mapping.json` und der daraus abgeleitete Auszug `data/gtfs/stops.txt`. Bis 11.05.2026 vom gelöschten `fetch_vor_haltestellen.py` beim Auskunftssystem der AnachB-Webapp (`anachb.vor.at/hamm/gate`) abgefragt, seither eingefroren; seit 17.10.2026 nur noch ID und Name, ohne Koordinaten (Michaels Entscheidung, `NOTICE.md`). | **ungeklärt**, keine offene Lizenz bekannt (bis 17.10.2026 hier fälschlich CC BY 4.0; ein offener VOR-Datensatz dafür existiert auf `data.gv.at` nicht) | „Haltestellendaten: VOR" |
| **Wien-Stadtgrenzen-Polygon** | `data/LANDESGRENZEOGD.json` (Layer `ogdwien:LANDESGRENZEOGD` der MA 41 – Stadtvermessung, WFS-API von data.wien.gv.at, `srsName=EPSG:4326`, `outputFormat=json`) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | **„Datenquelle: Stadt Wien – data.wien.gv.at"** |
| **OpenStreetMap Overpass** | **kein Eintrag.** Seit 2026-10-17 läuft keine OSM-Stufe mehr; die 150 früheren OSM-Koordinaten sind durch ÖBB-GeoNetz oder WL-OGD ersetzt (`docs/architecture.md` §5). `src/places/osm_client.py` bleibt ohne Aufruf im Produktivlauf | – | – |
| **HAFAS (ÖBB Scotty)** (Stations-Fallback) | Profil-Sidecar `data/hafas_profile.json` (extrahiert von `scripts/sync_hafas_profile.py`), Live-Anreicherung via `src/places/hafas_client.py`. Liefert hochpräzise Koordinaten und die EVA-Nummer (`hafas_extId`) für Stationen, die weder Bestand noch GeoNetz abdecken. Seit 2026-09-25 auch die Bahnlinien je ÖBB-Bahnhof (`data/oebb_station_lines.json`, `scripts/update_oebb_station_lines.py`). | **ungeklärt**: öffentlich erreichbares Mgate-Backend der ÖBB ohne offene Lizenz (Anfrage bei der ÖBB läuft, siehe `NOTICE.md`); Profil aus dem ISC-lizenzierten Open-Source-Projekt [`public-transport/hafas-client`](https://github.com/public-transport/hafas-client) | „Fahrplandaten: ÖBB" |

### Aktualisierungsskripte

| Skript | Funktion |
| ------ | -------- |
| `python -m src.cli stations update all --verbose` | Führt alle Teilaktualisierungen (ÖBB, WL) in einem Lauf aus. |
| `python -m src.cli stations update directory --verbose` | Aktualisiert das ÖBB-Basisverzeichnis und setzt `in_vienna`/`pendler`. |
| `python scripts/update_wl_stations.py [--no-download] -v` | Lädt die WL-OGD-CSVs vom kanonischen Wiener-Linien-OGD-Endpoint `www.wienerlinien.at/ogd_realtime/doku/ogd/` und führt sie mit `data/stations.json` zusammen. Dazu lädt es die GTFS-Haltestellen der Wiener Linien (`data/wienerlinien-gtfs-stops.csv`), die nur einspringen, wenn alle OGD-Haltepunkte einer DIVA außerhalb des WL-Netzes liegen. `--no-download` nutzt die lokal gepinnten CSVs (Sandbox/Offline-Modus). Beim Download-Erfolg wird die jeweilige CSV atomar geschrieben; bei Netzwerk-Fehlern wird das gepinnte Snapshot beibehalten. |


Die GitHub Action `.github/workflows/update-stations.yml` aktualisiert
`data/stations.json` wöchentlich automatisch (Cron `0 1 * * 0`, Sonntag 01:00 UTC). Pipeline-Schritte:

1. **VOR-Stop-Liste**: gepinnt in `data/vor-haltestellen.csv`. Seit
   2026-05-11 existiert kein automatisiertes VOR-Stop-Refresh-Skript
   mehr; die CSV wird redaktionell gepflegt. Ihre Werte stammen aus dem
   Auskunftssystem der AnachB-Webapp und stehen unter keiner bekannten
   offenen Lizenz (`NOTICE.md`, „VOR-Haltestellendaten“); seit 17.10.2026
   enthält sie deshalb keine Koordinaten mehr, und der lokale
   Koordinatenindex (`_build_location_index`) bekommt aus ihr und aus
   `data/gtfs/stops.txt` keine Werte mehr. VOR-Stop-IDs ändern sich
   nur selten (Jahre).
2. **Sub-Skripte** (`scripts/update_all_stations.py`) – `update_station_directory.py` →
   `update_wl_stations.py` → `enrich_station_aliases.py`,
   → `apply_station_overrides.py`, alle gegen ein Temp-File. Erst nach der
   Validierung wird per `atomic_write` ins Repo zurückkopiert. Ein VOR-Stations-Sub-Skript
   gibt es seit 2026-05-11 nicht mehr.
3. **Validation-Gate** – die Sub-Skript-Ausgabe wird vom selben Wrapper
   validiert. Vier Kategorien lösen die **Auto-Quarantäne** aus: Die
   betroffenen Einträge werden aus dem Ergebnis herausgenommen und mit
   Begründung in `data/quarantine.json` zur Prüfung abgelegt, der Lauf
   schreibt die übrigen Stationen weiter (ein Teilfehler der Quelle hält
   den Refresh nicht auf): `provider_issues`, `cross_station_id_issues`,
   `naming_issues` (Vienna/Pendler-Mutual-Exclusivity + no-space-
   Source-Format; die kanonische Namens-Eindeutigkeit wurde pre-
   2026-05-12 entfernt — siehe Code-Kommentar in
   `src/utils/stations_validation.py:_find_naming_issues`) und
   `security_issues`. Andere Kategorien
   (`alias_issues`, `coordinate_issues` mit Exemption für die
   Sondertypen `manual_foreign_city` und `manual_distant_at`) sind
   tolerant.
4. **Beobachtbarkeit** – nach erfolgreichem Atomic-Write schreibt der
   Wrapper zwei Artefakte:
   - `data/stations_last_run.json` – Heartbeat mit Timestamp,
     Sub-Skript-Laufzeiten und Exit-Codes, Validation-Summary nach
     Kategorie, Diff-Summary und aktuelle Polygon-Vertex-Zahl.
   - `docs/stations_diff.md` – menschenlesbarer Diff (added / removed /
     renamed / Koordinaten-Drift ≥ 100 m) gegen den Pre-Update-Snapshot.
     Ein leerer Bericht bestätigt den No-Change-Lauf (Heartbeat-Funktion).
5. **Validation-Report regenerieren** – `python -m src.cli stations validate
   --output docs/stations_validation_report.md` schreibt die Markdown-
   Variante des Validation-Reports (alle 12 Kategorien) für Review-Zwecke.

Der Workflow ruft dazu vor dem Wrapper `scripts/sync_hafas_profile.py`
(Mgate-Profil-Sidecar) und danach `scripts/update_oebb_station_lines.py`
(Linien je ÖBB-Station über HAFAS) als eigene Schritte auf.

#### Automatisierte Qualitätsberichte

`python -m src.cli stations validate` erzeugt einen Markdown-Bericht mit
zwölf Issue-Kategorien: **geographic duplicates**, **alias issues**,
**coordinate anomalies**, **GTFS mismatches**, **security warnings**,
**provider issues** (VOR-/OEBB-Konsistenz), **cross-station ID
collisions**, **identity field conflicts** (raw `wl_diva`/`bst_id`/
`vor_id`/`bst_code`-Kollisionen zwischen Stationen, ergänzt 2026-05-16
in PR #1539), **naming issues** (no-space-Source-Format +
Vienna/Pendler Mutual-Exclusivity; die pre-2026-05-12 vorhandene
kanonische Namens-Eindeutigkeits-Prüfung wurde entfernt, weil
`name` ein operator-facing Display-Label ist, dessen
strukturelle Eindeutigkeit von `wl_diva`/`bst_id`/`vor_id`/
`bst_code` getragen wird), **cross-name alias collisions**
(Namens-Alias-Kollisionen zwischen Stationen) und **alias-key
collisions** (mehrere Stationen beanspruchen denselben normalisierten
Alias und sind sich über den Ort uneins — abweichendes `in_vienna` oder
mehr als 2 km Abstand; ergänzt 2026-09-12 in PR #1801, siehe
`docs/architecture.md` §5) und **Namens-Zuordnungskonflikte** (zwei
Einträge tragen denselben Namen, und der Lookup löst ihn auf den Eintrag
auf, der keine Haltestelle dieses Namens hat; ergänzt 2026-09-18).
Über `--output docs/stations_validation_report.md` wird der Bericht
persistiert; mit `--fail-on-issues` bricht die CLI bei jedem Befund mit
einem Fehlercode ab. In CI läuft der Validator als Pflicht-Gate (siehe
`.github/workflows/test.yml`); zusätzlich regeneriert
`update-stations.yml` den persistenten Report im wöchentlichen Daten-Refresh.

### Pendler-Whitelist

Zwei komplementäre Dateien legen fest, welche Bahnhöfe außerhalb der
Stadtgrenze als Pendler-Knoten ins Verzeichnis aufgenommen werden:

- **`data/pendler_bst_ids.json`** – Liste von ÖBB-`bst_id`-Werten.
  Eintrag wirkt sofort: ist die ID im ÖBB-Excel-Verzeichnis vorhanden,
  wird die Station mit `pendler=true` übernommen.
- **`data/pendler_candidates.json`** – name-basierte Wishlist (siehe
  [`docs/schema/pendler_candidates.schema.json`](schema/pendler_candidates.schema.json)).
  Sinnvoll, wenn der `bst_id` der gewünschten Station unbekannt ist —
  der Updater matcht den Stationsnamen aus dem ÖBB-Excel gegen diese
  Liste und ergänzt die fehlende ID automatisch.

Die Auswahl ist in beiden Dateien **redaktionell kuratiert** und
priorisiert die für Wien-Pendler:innen relevantesten Bahnhöfe.
Änderungen wirken beim nächsten Lauf von
`python -m src.cli stations update directory`. Die Mutual-Exclusivity
zu `in_vienna` (Vienna-Station vs. Pendler) wird sowohl vom Updater als
auch vom Validator und JSON-Schema erzwungen — Verstöße führen zu einer
WARNING bzw. nehmen den Eintrag per Auto-Quarantäne aus dem Ergebnis (`data/quarantine.json`).

### Manuelle Station-Overrides

`data/stations_overrides.json` ist eine schmal definierte
Korrekturschicht, die `scripts/apply_station_overrides.py` zwischen
WL-Merge und Validator-Gate auf `data/stations.json` anwendet.
Sie behebt Upstream-Defekte der Wiener-Linien-OGD, die sich nicht
durch Tuning der Merge-Logik beheben lassen (etwa fehlerhaft
fehlende Haltepunkte, falsche Koordinaten einzelner DIVAs).

Drei Operationen sind erlaubt — andere Schemata werden hart
abgelehnt, damit ein bösartiger oder unaufmerksamer Override nicht
das gesamte Verzeichnis umformen kann:

| `op` | Wirkung |
| ---- | ------- |
| `restore` | Schreibt einen kompletten Station-Eintrag (inkl. `wl_stops`) zurück, wenn er beim letzten Cron-Tick verschwunden war. |
| `patch_coords` | Setzt `latitude` / `longitude` auf der bestehenden Station neu — z. B. bei nachweislich falscher OGD-Koordinate. |
| `remove` | Entfernt einen kompletten Eintrag (selten benötigt). |

Jeder Eintrag trägt zwingend `reason` (kurze Begründung) und
`expires_when` (Bedingung, unter der der Override retire-bar wird) —
damit verfallene Workarounds beim nächsten Audit auffallen.
Implementierung und Tests: `scripts/apply_station_overrides.py`,
`tests/test_apply_station_overrides.py`.

Eine Override-Koordinate muss aus einem offenen Datensatz stammen, wie
jede Koordinate im Verzeichnis. Seit 2026-10-25 brauchen falsche
WL-Koordinaten keinen Override mehr: Liegen alle OGD-Haltepunkte einer
DIVA mehr als 50 km vom Netzmittelpunkt entfernt
(`src.utils.stations.is_plausible_wl_coordinate`), nimmt
`update_wl_stations.py` den Mittelwert derselben Haltestelle aus den
GTFS-Fahrplandaten der Wiener Linien (`data/wienerlinien-gtfs-stops.csv`,
GTFS-`stop_id` `at:49:1956:…` = DIVA 60201956), und die GeoRSS-Koordinate
(`open_data_coordinate`) folgt derselben Regel. Das betraf am 25.10.2026
Leopoldine-Padaurek-Straße, Halblehenweg und Wassermanngasse (60201954 bis
60201956), die WL seit Mai im Waldviertel führt. Ihre drei
`patch_coords`-Overrides sind entfernt; zwei davon trugen eine Koordinate,
die nach OpenStreetMap bzw. Google Maps bestimmt war, was der Regel „nur
offen lizenzierte Koordinaten“ (Michael, 16./17.10.2026) widersprach. Die
GTFS-Werte liegen 5 bis 19 m von den früheren Override-Werten entfernt. Der
Override für die Hummelgasse (60200558) ist ebenfalls entfernt, weil seine
`expires_when`-Bedingung eingetreten ist: WL führt wieder einen Haltepunkt.

### Zusätzliche Datenquellen

Weitere offene Datensätze (z. B. ÖBB-GTFS, Streckendaten, Wiener OGD, INSPIRE-Geodaten) können lokal in `data/` abgelegt und mit
Feed- oder Stationsdaten verknüpft werden. Hinweise zu Lizenzierung und Verknüpfung stehen in diesem Abschnitt, um eine saubere
Nachnutzung zu gewährleisten.

Wichtige Sidecar-Dateien unter `data/`, die im obigen Lizenz-Block
nicht einzeln gelistet sind:

| Datei / Verzeichnis | Zweck |
| ------------------- | ----- |
| `data/stations_metadata.json` | Kurate VZG-Streckennummern → Kilometer-Mapping für Stammstrecken-Auswertungen. Testabgesichert via `tests/test_stations_metadata.py`. |
| `data/streckendaten/` | Platzhalter-Verzeichnis für lokale VZG-Schnittdaten (per `.gitignore` versioniert, Inhalt nicht commited). |
| `data/stations_last_run.json` | Heartbeat des wöchentlichen Stations-Refreshs (Validation-Summary, Sub-Skript-Laufzeiten). |
| `data/stations_overrides.json` | Manuelle Korrekturschicht — siehe Abschnitt darüber. |

## Automatisierte Workflows

Die wichtigsten GitHub Actions:

- `update-cycle.yml` – die zentrale Refresh-Pipeline. Trigger: `repository_dispatch: ifttt_feed_trigger` (ein externes IFTTT-Applet feuert ~alle 30 Minuten auf :00/:30 — präziser als der frühere dichte GitHub-Cron `0,30 * * * *`, der am 2026-05-10 in Commit `55ca72f` zugunsten von IFTTT entfernt wurde), ein sparsamer GitHub-nativer Backup-Cron `schedule: '17 * * * *'` (Safety-Net mit Freshness-Gate, das sich selbst überspringt, wenn bereits ein frischer Tick committet wurde; GitHub stellt ihn nur unzuverlässig zu, siehe „Geplante Läufe“ unten) sowie `workflow_dispatch` für manuelle Operator-Läufe. Einziger Job, der in einem Runner sequenziell die Provider-Cache-Fetcher (WL, ÖBB, Baustellen), den VAO-Pre-flight + die Stammstrecke-Abfrage, den Feed-Build (`docs/feed.xml`, `data/first_seen.json`, `data/stats/stoerungen_<YYYY>.csv`) sowie das README-/`docs/statistik.md`-Render ausführt und alles in einem Commit über `scripts/publish_to_main.sh` veröffentlicht (siehe „Veröffentlichen auf `main`“ unten). Checkt flach aus (`fetch-depth: 1`, nur der letzte Stand); nur die Frische-Prüfung des Backup-Crons holt bei Bedarf 50 Commits nach. Hält die `external-api-fetch`-Concurrency-Lane, damit nie zwei API-Cycles parallel laufen.
- VOR ist **ausschließlich** für den S-Bahn-Stammstrecke-Verspätungs-Monitor in `update-cycle.yml` eingesetzt (seit 2026-05-15: `/departureBoard`-Endpunkt am Wien Hauptbahnhof, ~48 Calls/Tag von 100 VAO-Start-Tier-Quota; davor zwei `/trip`-Calls pro Tick = 96/Tag). Eine VOR-Disruption-Polling- bzw. Stations-Anreicherungs-Automatisierung gibt es nicht mehr (Entscheidung 2026-05-11); die zugehörigen Helper-Scripts (`update_vor_cache.py`, `update_vor_stations.py`, `fetch_vor_haltestellen.py`) wurden ebenfalls entfernt. Diagnose-Aufrufe gegen den VOR-Auth-Pfad bleiben via `scripts/verify_vor_access_id.py` und `scripts/check_vor_auth.py` möglich.
- `build-feed.yml` – Code-Change-Verifikationspfad. Der reguläre Cron wurde 2026-05-09 in `update-cycle.yml` migriert; dieser Workflow läuft nur noch auf `push` (für `src/**`, `requirements.txt`, `pyproject.toml`, `.github/workflows/build-feed.yml`) sowie auf `workflow_dispatch` und baut den Feed aus den vorhandenen Caches neu — ohne neue API-Abfrage. Veröffentlicht wird seit 2026-09-24 mit reinem `git`, seit 2026-10-10 über `scripts/publish_to_main.sh` wie in `update-cycle.yml` (XML-Validierung, Allowlist `data/first_seen.json`, `docs/feed.xml`, `docs/feed.en.xml`, `README.md`, `data/stats/stoerungen_*.csv`, Push mit Wiederholungen, nie rot). Ausgecheckt wird seit 2026-10-05 der Stand des Branches beim Jobstart (`ref: github.ref`), nicht der gepushte Commit: Die Concurrency-Gruppe lässt den Job erst nach einem laufenden Update-Tick starten, und aus dem gepushten Commit gebaut hätte er dessen ältere Caches veröffentlicht; die zuvor genutzte `git-auto-commit-action` hatte mit dem mehrzeiligen `file_pattern` nur `data/first_seen.json` committet.
- `update-stations.yml` – pflegt wöchentlich (Sonntag 01:00 UTC, Cron `0 1 * * 0`) `data/stations.json`. Bestehende Koordinaten bleiben; einer Station ohne Koordinate gibt `_fill_coordinates_from_geonetz` zuerst den GeoNetz-Punkt ihrer `bst_id`. HAFAS (ÖBB Scotty) übernimmt als **Fallback** nur den Rest — eingebettet in `request_safe` und durch einen eigenen `CircuitBreaker` abgesichert (siehe `docs/architecture.md` §5). Direkt davor läuft `scripts/sync_hafas_profile.py` als eigener Workflow-Step und aktualisiert das Mgate-Profil-Sidecar aus dem Open-Source-Projekt `public-transport/hafas-client`. Eine Station, die weder GeoNetz noch HAFAS auflöst, bleibt ohne Koordinate und wird im Lauf mit Namen gewarnt; Google Places ist seit 2026-10-16 entfernt, OpenStreetMap seit 2026-10-17 (Michaels Entscheidungen, `docs/architecture.md` §5). VOR ist seit 2026-05-11 **nicht mehr** Teil des Stations-Refreshs (VOR-Stop-IDs aus gepinnter `data/vor-haltestellen.csv`). Manuell gepflegte Auslands-/Distant-AT-Knoten (`type=manual_*`, source=`manual`) durchlaufen die ÖBB-Filter-Stufe und damit auch die Enrichment-Kaskade nicht; sie werden direkt vor dem `write_json` von `_enrich_manual_stations` über den bereits im Speicher liegenden `location_index` (GTFS + VOR) sowie HAFAS LocMatch nachgereichert (idempotent, Einträge mit Koordinaten überspringt der Helper). Env-toggle: `WIEN_OEPNV_MANUAL_ENRICH=0` deaktiviert den Schritt — vom Wrapper-Test (`tests/test_update_all_stations_wrapper.py:test_wrapper_atomic_on_success`) verwendet, weil 296 reale HAFAS-Round-trips eines GitHub-hosted Runners das 180-Sekunden-pytest-Budget reißen würden. Die Stammstrecke-Abfrage und die tägliche `docs/statistik.md`-Regeneration laufen jeweils als Schritt in `update-cycle.yml`.
- `manual-full-refresh.yml` – `workflow_dispatch`-only-Komplettlauf für Disaster-Recovery. Führt sequenziell aus, was sonst auf `update-cycle.yml` und `update-stations.yml` verteilt ist: WL-/ÖBB-/Baustellen-Cache-Refresh, VOR-Secret-Validation, VAO-Pre-flight und Stammstrecke-`/departureBoard`-Tick (übersprungen, wenn das Tagesbudget ausgeschöpft ist), vollständiger Stations-Refresh (GeoNetz/HAFAS-Kaskade ohne OSM-Stufe + WL-OGD-Merge inkl. Re-Validierung), Feed-Build und Statistik-/README-Regeneration — und committet alles in einem einzigen Commit. Nicht enthalten sind die beiden HAFAS-Schritte von `update-stations.yml` (`sync_hafas_profile.py`, `update_oebb_station_lines.py`). Teilt die `external-api-fetch`-Concurrency-Lane mit `update-cycle.yml`, `test-vor-api.yml` und `build-feed.yml` (nur auf `main`).
- `test.yml` & `test-vor-api.yml` – führen die vollständige Test-Suite bzw. VOR-spezifische Integrationstests aus; `test.yml` läuft bei jedem Push auf `main` sowie bei jedem Pull Request und stellt die kontinuierliche Testabdeckung sicher.
- `mypy-strict.yml`, `bandit.yml`, `codeql.yml`, `complexity-gate.yml`, `seo-guard.yml` – ergänzende Qualitäts-Gates (strikte Typprüfung, Security-Lint, CodeQL-Scan, Komplexitäts-Baseline, SEO/Sitemap-Pflege).
- `ki-waechter.yml` – täglicher KI-Wächter (seit 2026-10-31): nach dem ersten erfolgreichen Update-Lauf ab 9:00 Wiener Zeit, nach einem Fehler frühestens zwei Stunden später erneut (`workflow_run`, Job `gate`), dazu per `workflow_dispatch`; schickt alle Meldungstexte der letzten 24 Stunden mit ihren Quellen in einer Anfrage ohne Werkzeuge an die Gemini API (Gratis-Kontingent, Secret `GEMINI_API_KEY`, höchstens 10 Anfragen am Tag, neuer Versuch nur bei überlastetem Modell), lässt nur wörtlich belegte Funde gelten und legt den Bericht in `docs/archive/audits/` ab; belegte Funde als Issue (Label `ki-waechter`). Ein Pull Request auf Skript, Prompt oder Workflow spielt ihn an zwei Tagen mit bekannten Funden und am aktuellen Feed ab ([`reference/ki_waechter.md`](reference/ki_waechter.md)).
- `health-check.yml` – stündlich nach dem ersten Update-Lauf jeder Stunde (`workflow_run`, der Job `gate` lässt nur Läufe bis UTC-Minute 29 durch; seit 2026-10-28), dazu per Cron alle 6 Stunden als Rückfall, falls der Update-Lauf ausfällt (`0 */6 * * *`; GitHub stellt davon in der Praxis etwa drei am Tag zu, Stunden verspätet, siehe „Geplante Läufe“ unten), und per `workflow_dispatch`, unabhängig vom Feed-Build. Welche Prüfung rot wird, welche ein Issue des Feed-Wächters auslöst und welche nur ein Hinweis ist, regelt [`architecture.md`](architecture.md), §7, „Alarmwege: rot, Issue, Hinweis“ (Betreiberentscheidung 2026-10-28). `scripts/health_check.py` startet die echten Cache-Updater (WL, ÖBB, Baustellen) als Stichprobe. Rot wird eine dieser Quellen seit 2026-10-28 wie die Stammstrecke erst, wenn die Update-Läufe seit drei Stunden keine brauchbare Antwort bekamen (Abrufstand in `data/feed_watch.json`, `fetch`; auch ein Teilausfall, Exit-Code 3, zählt nicht als brauchbar; `docs/architecture.md`, „Ausfall einer Quelle“); eine gescheiterte Stichprobe davor ist ein Hinweis. Nur wenn dieser Stand fehlt oder älter als vier Stunden ist, entscheidet die Stichprobe allein. Daneben prüft er, ob `docs/feed.xml` (`<lastBuildDate>`) und `data/stations_last_run.json` frisch sind. Die Prüfung `Stationsquellen` (seit 2026-10-25) ist rot, wenn eine Quelle des wöchentlichen Stationslaufs (ÖBB-Verkehrsstationen, die vier WL-OGD-Dateien, die WL-GTFS-Haltestellen) seit 15 Tagen keine gültige Antwort gab, also zwei Wochenläufe in Folge auf die letzte gute Kopie zurückfielen; sie liest dafür `checked_at` aus der `.cache.json`-Datei neben jeder Kopie, die der Lauf bei jeder Antwort 200 oder 304 schreibt. Liefert eine Stammstrecken-Richtung 6 Stunden lang keine Messwerte, während die Gegenrichtung liefert (`find_silent_directions` in `src/utils/stats.py`), meldet er das nur als Hinweis (ℹ️, `::warning`-Annotation); der Lauf bleibt grün. Für Leser zeigt die Website die Lücke (Abdeckungshinweis). Rot wird die Stammstrecke nur, wenn ihr Abruf nicht funktioniert (Prüfung `Stammstrecke-Abruf`, seit 2026-10-10): seit drei Stunden kein erfolgreicher Abruf laut `cache/stammstrecke/poll_status.json`, den der Monitor bei jedem Abruf schreibt. Eine gültige Antwort ohne Stammstrecken-Zug bleibt grün; dass keine Züge fahren, meldet die Website (`docs/architecture.md`, §7, „Überwachung: Abruf kaputt oder keine Züge“). Was der Feed-Wächter im letzten Update-Lauf fand (`data/feed_watch.json`), zeigt die Prüfung `Feed-Wächter` nur als Hinweis; Inhaltsfunde werden nie rot ([`reference/feed_watch.md`](reference/feed_watch.md)). Rot ist sie seit 2026-10-28, wenn der Wächter nicht prüfen konnte (Feld `error`). Die Prüfung `Nebenschritte` (seit 2026-10-28) ist rot, solange der letzte Lauf eines Nebenschritts scheiterte: Statistik, Linienprüfung und Feed-Wächter im Update-Lauf, ÖBB-Stationslinien (HAFAS) im Stationslauf, seit 2026-10-31 der tägliche KI-Wächter. Diese Schritte laufen mit `continue-on-error` und halten die Veröffentlichung nie auf; ihr Ergebnis (`steps.<id>.outcome`) schreibt `scripts/record_side_steps.py` am Ende des Laufs nach `data/side_steps/` (eine Datei je Workflow). Rot ist sie auch, wenn diese Aufzeichnung älter ist als die Grenze ihres Workflows (4 h, 192 h, KI-Wächter 36 h), und sie nennt den Schritt, den Zeitpunkt, den letzten sauberen Lauf und den Link zum Lauf. Betreiberentscheidung (Michael, 2026-10-28): „Ich tendiere zu ‚er soll rot werden‘, denn ein Fehler sollte auffallen.“ Für Nebenschritte ersetzt das die frühere Regel „rot nur, wenn der Abruf nicht funktioniert“ (2026-10-10); die Schwellen für Quellenausfälle (drei Stunden bei der Stammstrecke) bleiben ([`architecture.md`](architecture.md), §7, „Überwachung: Nebenschritte“). Die Prüfung `Secrets` (seit 2026-10-16) ist rot, wenn ein Secret, das die Feed-Pipeline braucht (derzeit `VOR_ACCESS_ID`), leer oder gar nicht hinterlegt ankommt; sie nennt den Namen, nie den Wert (siehe „Authentifizierung & Sicherheit“). Ein roter Lauf löst die Mail von GitHub aus; auf den Feed-Build wirkt er nicht. Die Stammstrecke wird wegen des VAO-Tagesbudgets nicht live geprüft, sondern über den Abrufstatus des Monitors.
- `probe-hafas-lines.yml` – nur `workflow_dispatch`: Diagnose für Stufe 2 der Linien-Prüfung (`scripts/probe_hafas_lines.py`, siehe unten).
- `claude.yml`, `claude-code-review.yml` – Claude-Code-Integration: Antworten auf `@claude` in Issues und Pull Requests bzw. automatische Review jedes Pull Requests. Ihr Workflow-Token hat nur Leserechte (`contents: read`). Kann das Review nicht laufen (Token leer oder abgelaufen, API-Störung), wird der Lauf seit 2026-10-31 rot (kein `continue-on-error` mehr; kein Pflicht-Check, hält keinen Merge auf), und der Health check meldet es stündlich (Prüfung `Werkzeuge`). Pull Requests aus Forks und von Dependabot haben keine Secrets und werden übersprungen.

Der `update-cycle.yml`-Job committet alle Cache-, Feed- und Statistik-Outputs in einem einzigen Commit; ein direkter `needs:`-Trigger zwischen Workflows ist damit unnötig. Eigenständige `update-<provider>-cache.yml`-Workflows gibt es seit der DAG-zu-Single-Job-Migration (2026-05-09) nicht mehr — alle Cache-Fetcher (`update_wl_cache.py`, `update_oebb_cache.py`, `update_baustellen_cache.py`) sind Schritte innerhalb von `update-cycle.yml`. Wer einzelne Cache-Fetcher außerhalb des Cycles manuell auslösen will, ruft das jeweilige Skript per `python -m src.cli cache update <provider>` direkt auf oder triggert den vollständigen `manual-full-refresh.yml`-Job.

### Veröffentlichen auf `main`

Sechs Workflows schreiben auf `main`: `update-cycle.yml`, `build-feed.yml`, `seo-guard.yml`, `update-stations.yml`, `manual-full-refresh.yml` und seit 2026-10-31 `ki-waechter.yml` (nur der Tagesbericht und seine Aufzeichnung). Seit 2026-10-10 veröffentlichen alle über dasselbe Skript `scripts/publish_to_main.sh`: Commit, dann ein **gewöhnlicher Push, nie `--force` oder `--force-with-lease`**. Lehnt GitHub ihn ab, weil inzwischen ein anderer Commit gelandet ist (ein Update-Tick, ein gemergter PR), holt das Skript den neuen Stand mit `git pull --rebase`, setzt den eigenen Commit darauf und versucht es erneut (vier Versuche, 2/4/8 Sekunden Abstand). In einem Rebase-Konflikt gewinnen die lokal gebauten Zeilen, alle übrigen Zeilen des anderen Commits bleiben (`keep_local_lines`, seit 2026-10-11): Dateien, die ein Lauf neu erzeugt, werden teils auch von Hand geändert (`README.md` neben dem Statistik-Block, `data/stations.json`). Bis dahin ersetzte die lokale Kopie die ganze Datei, und eine solche Änderung ging bei grünem Lauf verloren. Ohne alle drei Fassungen (Datei auf einer Seite neu oder gelöscht) oder bei Binärdateien bleibt es bei der ganzen lokalen Kopie. Lässt sich ein Rebase nicht abschließen, bricht das Skript ihn ab und versucht es von vorn; der Commit auf `main` bleibt in jedem Fall stehen.

Was ein Lauf nicht veröffentlicht, ist weg (Architektur-Review 2026-10-31, Fund C): `data/first_seen.json`, `data/vor_request_count.json`, `data/feed_watch.json` und `data/side_steps/*.json` leben nur im Commit des Laufs, ein zweiter Speicherort gibt es nicht. `update-cycle.yml` ruft `publish_to_main.sh --validate-feeds --all` ohne `--strict`: Scheitern alle vier Push-Versuche oder ist der gebaute Feed kein wohlgeformtes XML, endet der Lauf grün und ohne Commit. Folgen, nachgerechnet: Der VAO-Zähler untertreibt um einen Request je verlorenem Lauf (bei 48 von 100 am Tag müssten über 50 Läufe am selben Tag verloren gehen, bevor das Budget reißt); `first_seen` wird im nächsten Lauf neu gestempelt, die „Seit“-Zeit rückt um einen Takt nach hinten; ein ausgebliebener Feed fällt nach 90 Minuten als Issue (`feed-alter`) und nach vier Stunden rot im Health check auf. Das ist so gewollt: Ein roter Update-Lauf wegen eines Push-Konflikts wäre eine Mail ohne Handlung.

Alle sechs checken den Stand des Branches beim Jobstart aus (`ref: github.ref`), nicht den Commit, der den Lauf ausgelöst hat (`github.sha`, Vorgabe von `actions/checkout` ohne `ref`). Eine Concurrency-Gruppe hält einen Lauf zurück, solange ein anderer pusht; aus dem älteren Commit gebaut, gewännen seine Zustandsdateien (`data/first_seen.json`, `data/vor_request_count.json`, `data/feed_watch.json`) den Rebase-Konflikt gegen die neueren. `build-feed.yml` tut das seit 2026-10-05, die übrigen vier seit 2026-10-25. Gesehen einmal in 791 Update-Läufen vom 10.10. bis 25.10.2026: Am 17.10. um 08:30 UTC wartete der Tick 57 Sekunden auf den Rebuild nach dem Merge von #2034, baute auf dem Commit davor und landete im einzigen Rebase-Konflikt dieser Zeit (ohne verlorenen Eintrag, weil seine Zeilen die neueren waren). `tests/test_publish_to_main.py` prüft, dass jeder veröffentlichende Workflow so auscheckt.

Die gemeinsame Concurrency-Gruppe `external-api-fetch` teilen nur die drei Workflows, die dieselben Feed- und Zustandsdateien schreiben (`update-cycle.yml`, `build-feed.yml` auf `main`, `manual-full-refresh.yml`, dazu `test-vor-api.yml` wegen des VAO-Tageslimits). `seo-guard.yml` (`docs/sitemap.xml`, `docs/llms.txt`) und `update-stations.yml` (`data/stations.json`) schreiben andere Dateien und haben eigene Gruppen; ein gleichzeitiger Push dort endet im Rebase oben. Eine gemeinsame Gruppe für alle hätte einen Nachteil: GitHub hält je Gruppe nur einen wartenden Lauf, ein weiterer wartender bricht ihn ab (so am 06.10. und 16.10.2026 zwei wartende Rebuilds). Die Audit-Dateien von Jules in `docs/archive/audits/` kamen als Pull Request; seit 2026-10-31 schreibt `ki-waechter.yml` dort einmal am Tag `ki-waechter-YYYY-MM-DD.md` (eigene Gruppe `ki-waechter-main`, andere Dateien als jeder andere Workflow).

Warum: `update-cycle.yml` und `build-feed.yml` pushten bis dahin mit `--force-with-lease`. Der `git pull` zwischen zwei Versuchen holt `origin/main` und stellt damit das Lease auf genau den Commit, den es schützen soll (derselbe Mechanismus, der am 2026-09-11 den Merge von PR #1783 aus `main` entfernte, `docs/archive/audits/audit-2026-09-12-force-push-history-loss.md`). Musste der Rebase abgebrochen werden, überschrieb der nächste Versuch den dazwischen gelandeten Commit; `main` hatte damals keinen Branch-Schutz (seit 2026-10-16 schon, siehe unten). `seo-guard.yml`, `update-stations.yml` und `manual-full-refresh.yml` pushten einmal über `git-auto-commit-action` und wurden rot, sobald ein Update-Tick dazwischenkam (SEO Verify, 2026-10-04). Der Feed-Lauf und der Rebuild bleiben nie rot (Warnung, der nächste Tick baut neu), ebenso der SEO-Lauf; `update-stations.yml` und `manual-full-refresh.yml` werden rot (`--strict`), wenn nach allen Versuchen nichts veröffentlicht ist, damit ein verlorener Stations-Refresh auffällt. `tests/test_publish_to_main.py` prüft das an Wegwerf-Repositories und dass kein Workflow mehr selbst pusht.

**Branch-Schutz (seit 2026-10-16):** Das Repository-Ruleset „main schützen“ (Status *Active*, Ziel: Standard-Branch, Bypass-Liste leer) setzt auf `main` genau zwei Regeln: *Restrict deletions* (`main` lässt sich nicht löschen) und *Block force pushes* (nur Pushes, die den bisherigen Stand fortsetzen). Alle übrigen Regeln bleiben bewusst aus, Michaels Entscheidung vom 2026-10-16: Der Update-Lauf und die anderen fünf Workflows pushen als `github-actions[bot]` direkt auf `main`, ohne Pull Request. *Require a pull request*, *Require status checks*, *Restrict updates*, *Require signed commits* oder *Require code scanning results* würden diese Pushes ablehnen, und der Feed bliebe stehen. Die zwei Regeln stören die Workflows nicht, weil `publish_to_main.sh` nur gewöhnlich pusht. Prüfen lässt sich der Stand ohne Admin-Rechte über `GET /repos/Origamihase/wien-oepnv/rules/branches/main` (erwartet: `deletion` und `non_fast_forward`). Lehnt GitHub einen Push trotzdem wegen einer Regel ab, bleibt der Update-Lauf grün (nur die Warnung `git push failed after 4 attempts`), der Feed wird aber nicht mehr veröffentlicht; dann im Ruleset den Status auf *Disabled* stellen und die Ursache suchen.

### Geplante Läufe (`schedule`)

GitHub startet geplante Läufe in diesem Repository stark verspätet und lässt viele aus (Auswertung aller Läufe vom 10.09. bis 10.10.2026): Vom stündlichen Backup-Cron des Update-Laufs kamen 157 von rund 730 an, vier bis sieben am Tag; der Health check (`0 */6 * * *`) kam bis Anfang Oktober meist viermal, seit 05.10. dreimal am Tag, jeweils zwei bis sieben Stunden nach der geplanten Zeit; der SEO-Lauf (`23 3 * * *`) startete seit 25.09. zwischen 08:40 und 10:41 UTC, die Stationspflege (`0 1 * * 0`) zwischen 05:40 und 06:37 UTC. Den 30-Minuten-Takt des Feeds trägt deshalb allein IFTTT (`repository_dispatch`: 1 466 Ticks in diesen 30 Tagen, nie mehr als 40 Minuten Abstand; ohne Commit blieb nur der GitHub-Runner-Ausfall am 05.10.). Fällt IFTTT aus, hält der Backup-Cron den Feed nur noch alle paar Stunden aktuell; der Health check meldet einen Feed, der älter als vier Stunden ist, mit seinem nächsten Lauf. Am 28.10.2026 kam zwischen 13:19 und 23:19 (Wiener Zeit) kein geplanter Health check; seither startet ihn zusätzlich der erste Update-Lauf jeder Stunde (`workflow_run`), der Cron bleibt als Rückfall.

Der IFTTT-Auslöser selbst steht nicht im Repository (Architektur-Review 2026-10-31, Fund D): Das Applet ruft `repository_dispatch` mit einem persönlichen Zugriffstoken des Betreibers auf (Recht `contents: write` bzw. `repo`), und nur der Betreiber sieht in seinen GitHub-Einstellungen, ob und wann es abläuft. Läuft es ab oder fällt IFTTT aus, gibt es keinen Fehler-Lauf, sondern Stille: Der Backup-Cron hält den Feed nur noch alle paar Stunden aktuell, nach 90 Minuten ohne Build öffnet der Feed-Wächter das Issue `feed-alter`, nach vier Stunden wird der Health check rot. Die Grenze der Concurrency-Gruppe (GitHub hält je Gruppe nur einen wartenden Lauf) steht unter „Veröffentlichen auf `main`“.

## Skripte im Überblick

Der Ordner `scripts/` versammelt alle Wartungs- und Hilfsskripte. Die
meisten werden auch über die einheitliche CLI (`python -m src.cli …`)
gekapselt; ein Direktaufruf bleibt jedoch sinnvoll, wenn Sondermodi
benötigt werden (z. B. `--no-download` für die WL-OGD-CSVs).

Skripte importieren Projektmodule nur über das Paket `src` (`from src.utils.cache import …`); auf `sys.path` kommt dafür allein das Repository-Wurzelverzeichnis. Lag zusätzlich `src/` selbst auf dem Pfad, lud Python dieselbe Datei zweimal, als `utils.cache` und als `src.utils.cache`, mit getrenntem Modulzustand: Ein am einen Modul registrierter Alarm-Hook sah einen Alarm aus dem anderen nicht, der HTTP-Sitzungs-Cache hielt zwei Aufräum-Threads, und mypy prüfte die nackt importierten Namen gar nicht (`ignore_missing_imports`), weshalb zwei überflüssige Casts und eine ungeprüfte Verzweigung im Health check erst mit der Umstellung auffielen (Code-Audit vom 31.10.2026, Fund A; Entscheidung des Betreibers am selben Tag). `tests/test_scripts_import_src_only.py` prüft die Regel für jedes Skript; die früheren Import-Rückfälle auf nackte Modulnamen (`except ModuleNotFoundError: from utils… import …`) sind entfallen, weil sie nie liefen. `scripts/update_wl_stations.py` lädt seine `src`-Helfer weiterhin erst beim Aufruf, damit es ohne Pfad-Vorbereitung importierbar bleibt. Weil `src/__init__.py` den Feed-Builder importiert, starten `scan_secrets.py` und `configure_feed.py` seit der Umstellung etwa 0,3 s später und laufen wie jedes Skript mit `src`-Importen aus dem Repository-Wurzelverzeichnis (der Pfad-Wächter in `src/feed/config.py` prüft `LOG_DIR` schon beim Import).

### Provider-Caches & Feed-Daten

| Skript | Aufgabe |
| --- | --- |
| `update_wl_cache.py` | Liest die Realtime-Störungen der Wiener Linien und schreibt `cache/wl/events.json`; liest und schreibt außerdem `data/wl_resolved_tickers.json` (Kurzmeldungen erledigter Störungen, `src/providers/wl_resolved.py`). CLI: `python -m src.cli cache update wl`. |
| `update_oebb_cache.py` | Holt die ÖBB-Störungs-RSS-Feeds, filtert sie strikt auf Wien-Bezug (`_is_relevant`) und persistiert sie. CLI: `python -m src.cli cache update oebb`. |
| `update_baustellen_cache.py` | Lädt die Baustellen-Layer der Stadt Wien (ohne Cache ersatzweise den `data/samples/baustellen_sample.geojson`-Fallback) und legt Events ab. CLI: `python -m src.cli cache update baustellen`. |
| `update_stammstrecke_hbf.py` | Aktiver Refresh-Producer für den S-Bahn-Stammstrecke-Monitor (seit 2026-05-15; wird vom IFTTT-getriggerten `update-cycle.yml` ~alle 30 Min aufgerufen). Fragt einmal pro Tick `/departureBoard` am Wien Hauptbahnhof ab, klassifiziert die Abfahrten per Bahnsteig-1/2-Filter + Endhaltestellen-Whitelist und schreibt aggregierte Verspätungs-Zeilen pro Richtung nach `data/stats/stammstrecke_<YYYY>.csv` sowie eine Zeile pro Ausfall nach `data/stats/ausfaelle_<YYYY>.csv` (siehe [Reference](reference/stammstrecke_provider_logic.md)). |
| `update_stammstrecke_status.py` | Legacy-Producer (`/trip` × 2 Richtungen, vor 2026-05-15). Wird vom Cron-Workflow nicht mehr direkt aufgerufen, bleibt aber als Modul importierbar — `update_stammstrecke_hbf.py` re-used die geteilte Pending-Trip- / Recently-Finalised-Infrastruktur, den Quota-Charger sowie das CircuitBreaker-Tuning daraus. |
| `generate_markdown_stats.py` | Aggregiert die CSV-Ledger zu `docs/statistik.md` (laufendes Kalenderjahr), patcht die `<!-- STATS:* -->`-Marker im README und schreibt `docs/stats-summary.json` — die vorverdichteten Kennzahlen, aus denen das Live-Dashboard rendert. Die Aggregation läuft bei **jedem** Tick (auch mit `--skip-dashboard`), weil die Website alle fünf Minuten neu lädt; nur der Markdown-Render hängt am Flag. Der erste Dashboard-Lauf eines neuen Jahres schreibt außerdem das ganze Vorjahr nach `docs/statistik-<Vorjahr>.md` (Jahresarchiv, danach unverändert). |

### Stationsverzeichnis

| Skript | Aufgabe |
| --- | --- |
| `update_all_stations.py` | Wrapper für den vollständigen Stationsverzeichnis-Refresh; ruft die folgenden Sub-Skripte gegen ein Temp-File auf und committet erst nach erfolgreicher Validierung. CLI: `python -m src.cli stations update all`. |
| `update_station_directory.py` | Lädt das ÖBB-Excel und ergänzt fehlende Koordinaten aus GeoNetz (über die `bst_id`), dann über HAFAS (Fallback); die Wien-Grenze prüft eine Station mit GeoNetz-Punkt an diesem Punkt, und je EVA-Nummer bleibt ein Eintrag (`_drop_duplicate_eva`). Details siehe `docs/architecture.md` §5. Zusätzlich reichert `_enrich_with_geonetz` aus der gepinnten `data/oebb_geonetz_stops.json` EVA-Nummer und IFOPT-ID an (Source-Token `oebb_geonetz`, keine Koordinaten-Stufe). Direkt vor dem Schreiben läuft `_enrich_manual_stations` über den Manual-Block (`type=manual_distant_at` / `manual_foreign_city`), der sonst den ÖBB-Filter umgeht und damit die Enrichment-Kaskade auslässt; er nutzt denselben `location_index` (GTFS + VOR) als billige erste Stufe und fällt auf HAFAS LocMatch zurück. Idempotent: Einträge mit bereits gesetzten Koordinaten werden übersprungen. |
| `extract_oebb_geonetz_stops.py` | Extrahiert aus dem ÖBB-Infrastruktur-GeoNetz-Datensatz (23 MiB `GeoNetz_*.zip`, CC BY 3.0 AT) eine kompakte Stops-Projektion (`STP_*`-Stop-Points: EVA-Nummer, IFOPT-ID, Betriebsstellen-ID, autoritative Koordinaten) nach `data/oebb_geonetz_stops.json`. Liefert die Datengrundlage für die `oebb_geonetz`-Anreicherung in `update_station_directory.py`. Manuell ausgeführt, wenn ÖBB einen neuen GeoNetz-Stand veröffentlicht. |
| `sync_hafas_profile.py` | Holt `salt` / `ver` / `aid` des ÖBB-Mgate-Profils aus dem Open-Source-Projekt `public-transport/hafas-client` und persistiert sie atomar in `data/hafas_profile.json`. Läuft in `update-stations.yml` als eigener Schritt unmittelbar vor `update_station_directory.py`, damit ÖBB-seitige Credential-Rotation automatisch nachgezogen wird. |
| `update_oebb_station_lines.py` | Stufe 2 der Linien-Prüfung (A.14): sammelt für die ÖBB-Bahnhöfe in Wien und im Pendlerraum die Bahnlinien aus HAFAS-Abfahrtstafeln (zwei Stichtage, zwei Zeitfenster, Bahnklassen) und schreibt sie mit „zuletzt gesehen“ nach `data/oebb_station_lines.json` (Frist drei Jahre). Läuft wöchentlich in `update-stations.yml` als eigener Schritt mit `continue-on-error`. Beschreibt, was fährt oder gefahren ist: Eine Sperre, die begann, bevor eine Linie je gesehen wurde, versteckt sie. |
| `check_feed_lines.py` | Stufe 3 der Linien-Prüfung (A.14), nur Bericht: prüft für jede Meldung des deutschen Feeds mit Linien-Präfix, ob es die Linie gibt (Wiener-Linien-Verzeichnis, `wl_lines`, HAFAS-Linien, gepflegte Liste `data/planned_station_lines.json`) und ob sie die genannten Bahnhöfe bedient. Richtungsangaben („Richtung Westbahnhof“) und Haltestellen ohne Bahnhof zählen nicht. Zuglinien an Bahnhöfen, an denen nie ein Zug gesehen wurde, bewertet es nicht („not judged“). Schreibt Befunde („not confirmed“) ins Log und sammelt alle Auffälligkeiten mit erstem und letztem Tag in `data/feed_line_anomalies.json`; `--no-record` lässt die Sammlung unverändert. Ändert nichts am Feed. Läuft in `update-cycle.yml` vor dem Veröffentlichen mit `continue-on-error`. |
| `feed_watch.py` | Feed-Wächter (seit 2026-10-10), nur Bericht: prüft am Ende jedes Update-Laufs den fertigen deutschen Feed gegen die Regeln seit September 2026 (doppelte Einträge, alte Meldung als neue Störung, „Seit“-Zeit früher als die Störung, ÖBB-Text ohne zweiten Satz, Zeitzeile, Länge) und vergleicht die Quellen mit den letzten sieben Tagen. Ändert den Feed nie, blockiert die Veröffentlichung nie, Funde nie rot; Meldung als `::warning`, in `data/feed_watch.json`, als Hinweis im Health check und je Regel als GitHub-Issue (Label `feed-waechter`, schließt sich selbst). Kann er nicht prüfen, ist der Health check seit 2026-10-28 rot. Regeln und Rückprüfung: [`reference/feed_watch.md`](reference/feed_watch.md). |
| `ki_waechter.py` | KI-Wächter (seit 2026-10-31) für `ki-waechter.yml`: `prepare` schreibt die Eingabe (jeder Meldungstext der Feed-Fassungen aus 24 Stunden Git-Historie, DE und EN, Cache-Ereignisse je Linie, auch bis zu einem älteren Commit mit `--rev`), `ask` schickt sie in einer Anfrage an die Gemini API, `verify` prüft jedes Zitat der KI wörtlich gegen Feed und Quellen und schreibt den Bericht; Kopf, GUIDs und Tabelle kommen aus dem Skript. Prompt: `ki_waechter_prompt.md`. Ändert den Feed nie ([`reference/ki_waechter.md`](reference/ki_waechter.md)). |
| `probe_hafas_lines.py` | Manuelles Diagnoseskript für Stufe 2 der Linien-Prüfung (A.14): fragt für „Wien Hütteldorf“ und „Wien Meidling“ je `LocMatch` (Produktklassen `pCls`) und die Bahn-Abfahrten eines ganzen Tages (`StationBoard` in der Form von `hafas-client`) bei HAFAS ab und gibt nur die Form der Antwort aus (Liniennamen, `cls`, Größe, bei Fehlern `errTxt`). Läuft nur im Workflow `probe-hafas-lines.yml` (`workflow_dispatch`), weil die Entwicklungs-Sandbox `fahrplan.oebb.at` nicht erreicht. Schreibt nichts. |
| `update_wl_stations.py` | Lädt `wienerlinien-ogd-haltestellen.csv` und `wienerlinien-ogd-haltepunkte.csv` vom kanonischen Endpoint `www.wienerlinien.at/ogd_realtime/doku/ogd/` und merged sie in `data/stations.json`. Vom selben Endpoint kommen `wienerlinien-ogd-linien.csv` und `wienerlinien-ogd-fahrwegverlaeufe.csv`, aus denen jede WL-Station ihre `wl_lines` erhält (optional: fehlen sie, läuft der Merge ohne Linien). Soft-fail mit den gepinnten lokalen CSVs bei Upstream-Outage. Mit `--no-download` werden ausschließlich die lokal gepinnten CSVs verwendet. |
| `enrich_station_aliases.py` | Sucht alternative Schreibweisen pro Station und schreibt sie ins Verzeichnis. |
| `apply_station_overrides.py` | Wendet die kuratierte Korrekturschicht aus `data/stations_overrides.json` auf `data/stations.json` an: drei Operationen (`restore` / `patch_coords` / `remove`), idempotent, defensive Logs bei fehlenden Ziel-DIVAs. Behebt Upstream-Defekte der Wiener Linien OGD (falsche Koordinaten für einzelne DIVAs, fehlende Haltepunkte bei aktiven Stationen, geografisch identische Haltepunkte unterschiedlicher DIVAs), die sich nicht durch Tuning der Merge-Logik beseitigen lassen. Läuft in `update_all_stations.py` zwischen `enrich_station_aliases.py` und dem Validator-Gate. Jeder Override trägt `reason` + `expires_when`-Prädikat, damit er retirable bleibt, sobald der Upstream-Feed gefixt ist. |
| `validate_stations.py` | CLI-Front-end für `src.utils.stations_validation`; das gleiche Verhalten ist via `python -m src.cli stations validate` erreichbar. |
| `validate_vor_mapping.py` | Prüft die statische `data/vor-haltestellen.mapping.json` (VOR-ID ↔ Name) auf duplikate IDs und Format-Drift. Die Mapping-Datei wird seit 2026-05-11 redaktionell gepflegt. |

### Auth- & Diagnose-Helfer

| Skript | Aufgabe |
| --- | --- |
| `verify_vor_access_id.py` | Smoke-Test für `VOR_ACCESS_ID` und `VOR_BASE_URL`. CLI: `python -m src.cli tokens verify vor`. |
| `check_vor_auth.py` | Prüft den vollständigen Auth-Pfad (`VorAuth`) inklusive Header. CLI: `python -m src.cli tokens verify vor-auth`. |
| `check_overpass_status.py` | OSM-Mirror-Smoke-Test mit `out count`-Query; seit 2026-10-17 nur noch beratend im Test-Workflow, der Stationslauf nutzt OSM nicht mehr. |
| `health_check.py` | Eigenständige Gesundheitsprüfung für `health-check.yml`: Quellen rot nach drei Stunden ohne brauchbare Antwort im Update-Lauf, dazu eine Stichprobe über die echten Cache-Updater und Frische von `docs/feed.xml` und `data/stations_last_run.json`; die Prüfung `Veröffentlichung` (seit 2026-10-31) vergleicht die Feeds auf GitHub Pages mit dem Repo und ist rot, wenn die Live-Kopie vier Stunden hinterherhängt; den Stand des Feed-Wächters zeigt er als Hinweis (rot, wenn der Wächter nicht prüfen konnte); die Prüfung `Nebenschritte` (seit 2026-10-28) ist rot, solange der letzte Lauf eines Nebenschritts scheiterte (`data/side_steps/`); die Prüfung `Werkzeuge` (seit 2026-10-31) ist rot, solange der letzte Lauf von `claude-code-review.yml` scheiterte (Actions-API, `GITHUB_TOKEN` des Laufs mit `actions: read`); die Stammstrecke ist rot nur bei kaputtem Abruf (`cache/stammstrecke/poll_status.json`), eine stille Richtung ist nur ein Hinweis. Die Prüfung `Secrets` (seit 2026-10-16) ist rot, wenn ein Pflicht-Secret aus `WORKFLOW_SECRETS` leer ankommt, und nennt nur den Namen. Schwellen per Umgebungsvariable einstellbar: `HEALTH_FEED_MAX_AGE_HOURS` (4), `HEALTH_LIVE_LAG_MAX_HOURS` (4, Prüfung `Veröffentlichung`: Live-Feeds auf GitHub Pages hinter dem Repo) und `HEALTH_LIVE_FETCH_TIMEOUT_SECONDS` (20), `HEALTH_SEO_MAX_AGE_HOURS` (48, Aufzeichnung des SEO-Laufs in `data/side_steps/seo-guard.json`), `HEALTH_STATIONS_MAX_AGE_HOURS` (192), `HEALTH_KI_WAECHTER_MAX_AGE_HOURS` (36, Aufzeichnung des KI-Wächters in `data/side_steps/ki-waechter.json`), `HEALTH_STATION_SOURCE_MAX_AGE_HOURS` (360, Prüfung `Stationsquellen`), `HEALTH_FETCH_TIMEOUT_SECONDS` (120), `HEALTH_STAMMSTRECKE_POLL_STALE_HOURS` (3), `HEALTH_SOURCE_STALE_HOURS` (3, WL, ÖBB und Baustellen ohne brauchbare Antwort im Update-Lauf, seit 2026-10-28) und `HEALTH_STAMMSTRECKE_WINDOW_HOURS` (6). |
| `record_side_steps.py` | Letzter Schritt vor der Veröffentlichung in `update-cycle.yml` und `update-stations.yml` (seit 2026-10-28): schreibt das Ergebnis (`steps.<id>.outcome`) jedes Nebenschritts mit `continue-on-error` nach `data/side_steps/<workflow>.json` (Ergebnis, Zeitpunkt, letzter sauberer Lauf, Link zum Lauf). `skipped`, `cancelled` und ein leeres Ergebnis lassen den alten Eintrag stehen. Grundlage der Health-Check-Prüfung `Nebenschritte`. |
| `preflight_quota_check.py` | Hard-Gate für `update-cycle.yml` und `manual-full-refresh.yml`: bricht **vor** jeder API-Anfrage ab, wenn das persistierte Tagesbudget bereits ausgeschöpft ist. Stdlib-only, eigene Exit-Codes. |
| `scan_secrets.py` | Repository-Scan via `src.utils.secret_scanner`. CLI: `python -m src.cli security scan`. |
| `configure_feed.py` | Interaktiver Konfigurations-Assistent (schreibt `.env`). CLI: `python -m src.cli config wizard`. |
| `scaffold_provider_plugin.py` | Erzeugt ein lauffähiges Provider-Plugin-Skelett (`register_providers`-Hook); siehe [How-to](how-to/provider_plugins.md). |

### Statische Analyse & Build-Hygiene

| Skript | Aufgabe |
| --- | --- |
| `run_static_checks.py` | Dispatcher für `ruff check`, `mypy --strict`, `bandit`, den Secret-Scanner, das C901-Gate (`check_complexity.py`), das i18n-Gate (`check_i18n_coverage.py`) und `pip-audit`; CI-äquivalent. CLI: `python -m src.cli checks`. |
| `check_complexity.py` | C901-Komplexitäts-Gate (Threshold 15, Allowlist `.c901-baseline.txt`). |
| `regen_c901_baseline.sh` | Regeneriert die Baseline nach gezielten Refactors; lokal ausführen, Diff committen. |
| `regen_mypy_baseline.sh` | Regeneriert `.mypy-baseline.txt` (gleiche Mechanik wie c901). |
| `generate_sitemap.py` | Generiert `docs/sitemap.xml` und `docs/feed.xml`-Hinweise; läuft im `seo-guard.yml`-Workflow. `<lastmod>` wird in einem einzigen gestreamten `git log` ermittelt (kein N+1-Subprozess pro Datei). |
| `generate_llms_txt.py` | Generiert `docs/llms.txt` (kuratierter Markdown-Index der informationsdichtesten Seiten nach [llms.txt-Standard](https://llmstxt.org/)) für LLM-/KI-Crawler; läuft im `seo-guard.yml`-Workflow. URLs/Host-Pin werden mit `generate_sitemap` geteilt; Ausgabe ist deterministisch. |
| `gtfs.py` | GTFS-Hilfsmodul (`read_gtfs_stops`); wird von Tests und vom Stations-Validator konsumiert. |
| `optimize_site_assets.py` | Erzeugt minifizierte `site.min.css` / `site.min.js` aus den lesbaren Quellen sowie WebP-Geschwister für `train.png` / `footer-bg.jpg`. Wird vom Pre-Commit-Hook `site-assets-minified --check` aufgerufen, um Drift zwischen Quelle und committetem Bundle zu verhindern. Idempotent (`--check`/`--skip-images` ohne Image-Tools nutzbar). |
| `check_i18n_coverage.py` | i18n-Coverage-Gate für das Live-Dashboard (`docs/site.html`): prüft, dass jedes `data-i18n*`-Attribut einen passenden Schlüssel im `I18N_EN`-Dictionary in `docs/assets/site.js` hat. Fehlende oder leere Übersetzungen brechen den Lauf ab, verwaiste Schlüssel sind eine Warnung. Zusätzlich werden die **JS-eigenen DE/EN-Wörterbuchpaare** abgeglichen (`CHART_TEXT_DE`/`_EN`, `WEEKDAY_LONG_DE`/`_EN`, `STATUS_TEXT.de`/`.en` …): Ein Schlüssel, den nur eine Seite führt, fällt zur Laufzeit stumm auf die andere Sprache zurück. Läuft als Pre-Commit-Hook (`i18n-coverage`) und in CI via `run_static_checks.py`. |

### Mehrsprachiges Dashboard (i18n)

Das öffentliche Live-Dashboard (`docs/site.html`) ist zweisprachig: **Deutsch ist die Quellsprache** (direkt im HTML-Markup), die englische Fassung liegt als `I18N_EN`-Wörterbuch in `docs/assets/site.js` und wird zur Laufzeit client-seitig über `data-i18n*`-Attribute eingespielt. Die gewählte Sprache wird im `localStorage` (`wienoepnv:lang`) gemerkt; ein Umschalter im UI wechselt zwischen „Deutsch" und „English". Wer eine neue deutsche UI-Zeichenkette ergänzt, muss den passenden `I18N_EN`-Schlüssel mitliefern — sonst schlägt das `check_i18n_coverage.py`-Gate fehl (Pre-Commit **und** CI). Zeichenketten, die das JavaScript selbst erzeugt (Diagramm-Beschriftungen, Wetterwörter, Wochentage, Status- und Fehlerzeilen), haben keinen `data-i18n`-Knoten und liegen deshalb in DE/EN-Wörterbuchpaaren; sie lösen durchwegs als `dict[key] || DE[key] || key` auf, weshalb dasselbe Gate beide Seiten auf Schlüsselgleichheit prüft. Diese Übersetzungsschicht betrifft ausschließlich das Dashboard; die übrige Projektdokumentation ist durchgängig deutsch.

## Entwicklung & Qualitätssicherung

- **Tests**: `python -m pytest` führt rund 11 900 Unit- und Integrationstests in rund 650 Modulen unter `tests/` aus (Stand 2026-10-28).
- **Test-Isolation**: Autouse-Fixtures in `tests/conftest.py` halten jeden Test von echten Dateien und der Umgebung fern:
  - `isolate_stats_writes` und `isolate_episode_starts_writes` leiten die Statistik-Ledger und `cache/stammstrecke/episode_starts.json` nach `tmp_path` um.
  - `reset_vor_request_count` tut das für den VAO-Zähler.
  - `forget_resolved_tickers` leert das Gedächtnis von `src/providers/wl_resolved.py` vor und nach jedem Test und leitet `data/wl_resolved_tickers.json` nach `tmp_path` um; vorher leerte ein Test, der `update_wl_cache.main()` ausführt, die echte Datei (Gegenprüfung 2026-10-05).
  - `reset_build_feed_state` und `reset_circuit_breakers` setzen Modulzustand zurück.
  - `_environment_stays` stellt nach jedem Test `os.environ` wieder her. `load_env_file` schreibt selbst in die Umgebung, und `monkeypatch.delenv(..., raising=False)` merkt sich eine fehlende Variable nicht; bis 2026-10-28 liefen deshalb alle Tests nach `tests/test_env_loader.py` mit `VOR_ACCESS_ID=token`, und ein Health-Check-Test bestand nur dadurch.
  - `_cache_dir_stays` leitet `_CACHE_DIR` beider Modulkopien von `utils.cache` (`src.utils.cache` und `utils.cache`) nach `tmp_path` um. `write_cache` räumt nach dem Schreiben das Verzeichnis seiner Quelle nach Dateialter auf; zwei Baustellen-Tests lenkten nur `_cache_file` um und löschten so den echten `cache/baustellen_*/events.json`, sobald dessen Datei älter als 48 Stunden war (älterer Klon, Sandbox; Prüfung 2026-10-28).
  - `_without_host_proxy` entfernt Proxy-Variablen, damit die Suite in einer Sandbox mit Proxy dasselbe Ergebnis liefert wie in der CI (`PROXY_TRUSTED_HOSTS`).
  - `_health_report_stays_untouched` lässt jeden Test scheitern, der `docs/feed-health.*` anlegt oder ändert (Audit 2026-09-25, A.7).
  - `_no_real_dns` beantwortet jede DNS-Abfrage mit einer festen öffentlichen Adresse (`STUB_PUBLIC_IP`); kein Test fragt das echte DNS. Wer eine andere Antwort braucht, patcht `dns.resolver.Resolver.resolve` oder `_resolve_hostname_safe` selbst.
  - `_restore_replaced_modules` setzt nach jedem Test die Module zurück, die er aus `sys.modules` entfernt oder ersetzt hat (31 Dateien laden `src.build_feed` neu), und stellt den Inhalt jedes Moduls wieder her, das er mit `importlib.reload` neu geladen hat. Sonst griffen `reset_build_feed_state` und `time_line_today` für den Rest des Laufs ins Leere, und Tests hielten Klassen, die das Modul nicht mehr wirft.
  - `_feed_config_stays` stellt jeden Wert von `src.feed.config` wieder her, den ein Test (etwa über `refresh_from_env()`) geändert hat.
  - `_root_logger_stays_clean` entfernt Handler, die ein Test am Root-Logger hinterlässt, und stellt Formatter und Level der übrigen wieder her (`configure_logging` setzt einen `SafeFormatter` auch auf pytests Capture-Handler).
  - Hypothesis läuft ohne Zeitlimit pro Beispiel (Profil `wien-oepnv`); Hänger fängt `pytest-timeout`.
  - Das Streckendaten-Sample unter `data/streckendaten/` legt die Session-Fixture `streckendaten_dataset` an. Mit `pytest-xdist` teilen sich die Worker diesen festen Pfad: Ein Dateilock im Temp-Verzeichnis serialisiert Anlegen und Aufräumen, und erst der letzte Worker räumt auf. Vorher las ein Worker das halb geschriebene Archiv oder fand es gelöscht, und alle Tests dieses Workers scheiterten im Setup (Prüfung 2026-10-04: 4.190 Fehler in einem Lauf mit `-n 4 -p randomly`).

  Die Suite besteht in beliebiger Reihenfolge, seriell wie parallel (`pytest -n 4`), ohne Netz, in jeder Zeitzone des Rechners, mit jedem heutigen Datum und in einer Arbeitskopie, deren Dateien Tage alt sind (Prüfung 2026-10-04: Zufallsreihenfolge mit `pytest-randomly`, Lauf ohne Netz per `unshare -rn`, Uhr mit `time-machine` auf beide Zeitumstellungen, Silvester, Neujahr, Schalttag 2028 und 2030 gestellt, `TZ` auf New York und Kiribati; wiederholt 2026-10-28 mit zwei Zufallsreihenfolgen, ohne Netz, Uhr auf 01.11.2026, Wiener Mitternacht, Neujahr und Sommerzeit 2027 sowie allen Dateien unter `cache/`, `data/`, `docs/`, `log/` zehn Tage alt). Ausnahme beim verstellten Datum: Tests, die Dateien schreiben und deren Alter am Dateisystem messen (Cache-Bereinigung), oder Unterprozesse starten, sehen die echte Uhr des Dateisystems bzw. des Unterprozesses. CI und alle Workflows laufen mit Python 3.11; unter Python 3.13 sind drei Tests rot, weil Python sich geändert hat (Unicode 15.1 kennt sieben neue Formatzeichen, die `_INVISIBLE_DANGEROUS_RE` nicht enthält; `Path.resolve()` meldet Symlink-Schleifen nicht mehr als Fehler).

  Tests, die `main()` ausführen, leiten `OUT_PATH`, `STATE_FILE`, `FEED_HEALTH_PATH` und `FEED_HEALTH_JSON_PATH` nach `tmp_path` um. Sie patchen am Modulobjekt (`patch.object(bf, …)`), nicht über den String-Pfad `"src.build_feed.…"`: Einige Tests laden das Modul neu, und der String-Patch träfe dann ein anderes Modul als das, dessen `main()` läuft.
- **Kontinuierliche Tests**: Die GitHub Action `test.yml` automatisiert die im Audit empfohlene regelmäßige Testausführung und bricht Builds bei fehlschlagender Test-Suite ab.
- **Statische Analyse & Typprüfung**: `ruff check` (Stil/Konsistenz, Regelgruppen `E`, `F`, `S`, `B`, `UP` — siehe `pyproject.toml`) und `mypy --strict` laufen via `python -m src.cli checks`; Mypy prüft dort `src/` (Konfiguration in `pyproject.toml`). Die volle Typabdeckung über `src/`, `tests/` und `scripts/` (derzeit 0 Errors) prüft der Workflow `mypy-strict.yml` auf jedem Push auf `main` und jedem Pull Request. Optional lassen sich über `--fix` Ruff-Autofixes aktivieren oder zusätzliche Argumente an Ruff durchreichen. Er vergleicht gegen die leere Allowlist `.mypy-baseline.txt`, neue Fehler lassen ihn rot werden.
- **Pre-Commit-Hooks**: `.pre-commit-config.yaml` aktiviert lokale Checks bei jedem `git commit`: Ruff, `mypy --strict`, Bandit, der eigene Secret-Scanner (`scripts/scan_secrets.py`), das C901-Komplexitäts-Gate (`scripts/check_complexity.py`), der Site-Asset-Drift-Check (`site-assets-minified` → `scripts/optimize_site_assets.py --check`), das Dashboard-i18n-Gate (`i18n-coverage` → `scripts/check_i18n_coverage.py`) sowie Whitespace-/Merge-Conflict-/YAML-/TOML-/JSON-/Large-File-Hygiene. Einmalig nach dem Klonen `pre-commit install` ausführen — Details in [`CONTRIBUTING.md`](https://github.com/Origamihase/wien-oepnv/blob/main/CONTRIBUTING.md).
- **Logging**: Zur Laufzeit entsteht `log/errors.log` mit rotierenden Dateien; Größe und Anzahl sind konfigurierbar.

## Developer Experience & Observability

### Einheitliche CLI für Betriebsaufgaben

Die neue Kommandozeile (`python -m src.cli`) bündelt bisher verstreute Skripte. Wichtige Unterbefehle:

- `python -m src.cli cache update <wl|oebb|baustellen>` – aktualisiert den jeweiligen Provider-Cache. (VOR ist seit 2026-05-11 nicht mehr cache-fähig — der Stammstrecke-Monitor läuft als eigener Workflow-Step und schreibt direkt in den CSV-Ledger.)
- `python -m src.cli stations update <all|directory|wl>` – führt die bestehenden Stations-Skripte mit optionalem `--verbose` aus. (VOR-Stations-Refresh wurde 2026-05-11 entfernt — `data/vor-haltestellen.csv` wird redaktionell gepflegt.)
- `python -m src.cli feed build` – startet den Feed-Build mit der aktuellen Umgebung.
- `python -m src.cli feed lint` – prüft die aggregierten Items auf fehlende GUIDs oder unerwartete Duplikate. Seit 2026-10-15 läuft dafür dieselbe Feed-Kette wie im Build (`_assemble_feed_items`); die Zählwerte entsprechen damit dem Health-Report, und fehlende GUIDs zählen nur bei Einträgen, die in den Feed kämen.
- `python -m src.cli tokens verify <vor|vor-auth>` – validiert Secrets und API-Zugänge.
- `python -m src.cli checks [--fix] [--ruff-args …]` – ruft die statischen Prüfungen konsistent zur CI auf.

### Qualitätsberichte für das Stationsverzeichnis

`python -m src.cli stations validate --output docs/stations_validation_report.md` erstellt den Report `docs/stations_validation_report.md`. Die Ausgabe enthält zusammengefasste Kennzahlen und detaillierte Listen der gefundenen Probleme.

| Flag | Zweck |
| ---- | ----- |
| `--stations PATH` | Alternativer Pfad zur `stations.json` (Default `data/stations.json`). |
| `--gtfs PATH` | Alternativer Pfad zur GTFS-`stops.txt` (Default `data/gtfs/stops.txt`). |
| `--decimal-places N` | Toleranz beim Koordinaten-Matching (Default 5 Nachkommastellen). |
| `--output PATH` | Schreibt den Markdown-Bericht zusätzlich an den angegebenen Ort. |
| `--fail-on-issues` | Beendet den Lauf mit Exit-Code 1, sobald irgendeine Issue-Kategorie nicht leer ist (CI-Gate). |

### Logging & Beobachtbarkeit

Die CLI respektiert die vorhandene Logging-Konfiguration (`log/errors.log`, `log/diagnostics.log`). Für Ad-hoc-Audits lassen sich Berichte und Skriptausgaben über `--output`-Parameter in nachvollziehbaren Pfaden versionieren. Jeder Feed-Build erzeugt zusätzlich zwei Gesundheitsberichte unter `docs/feed-health.md` (menschenlesbar) und `docs/feed-health.json` (maschinenlesbar) — beide werden lokal nach jedem Build geschrieben und sind nicht im Repository versioniert.

### Optionale GitHub-Issue-Auto-Erstellung bei Feed-Build-Fehlern

Operator:innen können den Feed-Builder so konfigurieren, dass er bei Fehlern automatisch ein GitHub Issue im konfigurierten Repository öffnet (`src/feed/reporting.py:_GithubIssueConfig`). Die Funktion ist standardmäßig **deaktiviert**; sie wird erst aktiv, wenn `FEED_GITHUB_CREATE_ISSUES=true` gesetzt ist und sowohl ein Repository als auch ein Token vorliegen:

| Variable | Zweck |
| --- | --- |
| `FEED_GITHUB_CREATE_ISSUES` | Master-Switch (`true`/`false`, Standard `false`). |
| `FEED_GITHUB_REPOSITORY` | Ziel-Repository im Format `owner/name`. Fallback `GITHUB_REPOSITORY` (von GitHub Actions automatisch gesetzt). Wird gegen die GitHub-Slug-Grammatik validiert. |
| `FEED_GITHUB_TOKEN` | API-Token mit `issues:write`. Fallback `GITHUB_TOKEN`. Wird ausschließlich an vertrauenswürdige GitHub-API-Hosts gesendet. |
| `FEED_GITHUB_API_URL` | Optionaler API-Base-Override (z. B. GitHub Enterprise `https://<host>/api/v3`). Fallback `GITHUB_API_URL`, Default `https://api.github.com`. |
| `FEED_GITHUB_ENTERPRISE_HOSTS` | CSV-Allowlist erlaubter GHE-Hosts; muss bei nicht-public-GitHub-API gesetzt sein, sonst lehnt der Reporter den Call ab und der Token verlässt den Prozess nicht. |
| `FEED_GITHUB_ISSUE_LABELS` / `_ASSIGNEES` | Komma-separierte Listen für Issue-Labels/Assignees. |
| `FEED_GITHUB_ISSUE_TITLE_PREFIX` | Titel-Präfix (Standard `"Fehlerbericht"`). |

Sicherheits-Gates: Der Reporter validiert das Repo-Slug gegen GitHubs Naming-Grammatik (`owner` 1-39 Zeichen alphanumerisch/Bindestrich, `name` 1-100 Zeichen `[A-Za-z0-9._-]`) und akzeptiert als API-Host **nur** `api.github.com` (öffentliches GitHub) oder Hosts, die explizit über `FEED_GITHUB_ENTERPRISE_HOSTS` gewhitelistet wurden. Seit [PR #1512](https://github.com/Origamihase/wien-oepnv/pull/1512) ist der Scheme zusätzlich auf **`https://` gepinnt** — `http://`-URLs werden auch bei korrektem Host abgelehnt, damit der Bearer-Token niemals im Klartext über die Leitung geht. Eine fehlkonfigurierte `FEED_GITHUB_API_URL` führt deshalb nicht zur Token-Exfiltration.

## Authentifizierung & Sicherheit

- **Secrets**: Pflicht- und optionale Variablen (`VOR_ACCESS_ID`, `VOR_BASE_URL`, `VOR_VERSION` / `VOR_VERSIONS`, …) sind im Tabellenblock [„Konfiguration des Feed-Builds"](#konfiguration-des-feed-builds) gelistet. Sie liegen nie im Repository. Die Zugangsdaten `VOR_ACCESS_ID`/`VAO_ACCESS_ID` und `FEED_GITHUB_TOKEN`/`GITHUB_TOKEN` liest `read_secret` (`src/utils/env.py`) in dieser Reihenfolge: systemd Credentials (`$CREDENTIALS_DIRECTORY/<Name>`), Docker Secrets (`/run/secrets/<Name>`), Umgebungsvariable (auch aus den `.env`-Dateien, siehe `WIEN_OEPNV_ENV_FILES`); alle übrigen Werte kommen aus der Umgebung; das Skript `src/utils/secret_scanner.py` schützt proaktiv vor versehentlich eingecheckten Geheimnissen. In `.github/workflows/update-cycle.yml` werden diese Werte als Build-Secrets durchgereicht.
- **Repository-Secrets und ihre Überwachung** (seit 2026-10-16): `WORKFLOW_SECRETS` in `scripts/health_check.py` listet jedes Repository-Secret, das ein Workflow verwendet, mit seiner Art: `required` (die Feed-Pipeline braucht es: `VOR_ACCESS_ID`), `optional` (der Code hat einen Standard: `VOR_BASE_URL`) und `tooling` (Werkzeug-Workflows ohne Bezug zum Feed: `CLAUDE_CODE_OAUTH_TOKEN`, `GEMINI_API_KEY` für den KI-Wächter, `REPO_ADMIN_TOKEN`). Der Health check bekommt die `required`- und `optional`-Secrets übergeben und wird rot, wenn ein `required`-Secret leer ankommt; er prüft nur, ob ein Wert da ist, und gibt nie einen Wert aus. Ob ein hinterlegter Schlüssel noch angenommen wird, zeigt `Stammstrecke-Abruf` (abgelehnte Abrufe färben ihn nach drei Stunden rot). Die `tooling`-Secrets werden seit 2026-10-31 über ihre Läufe überwacht (Architektur-Review, Fund B): `REPO_ADMIN_TOKEN` als Nebenschritt des SEO-Laufs (`data/side_steps/seo-guard.json`), `CLAUDE_CODE_OAUTH_TOKEN` über das Ergebnis des letzten Review-Laufs (Prüfung `Werkzeuge`), `GEMINI_API_KEY` als Nebenschritt des KI-Wächters (`data/side_steps/ki-waechter.json`); leer oder abgelaufen heißt in allen Fällen rot. `tests/test_workflow_secrets.py` gleicht in CI die Liste mit den `secrets.*`-Verweisen der Workflows und den Umgebungsnamen ab, die der Code liest: Ein Workflow, der ein unbekanntes Secret übergibt, und ein gelistetes Secret, das kein Workflow mehr übergibt, lassen den Test scheitern. Anlass (Michael, 16.10.2026: „Es sollte doch auffallen, wenn hinterlegte Secrets nicht mehr funktionieren“): Der Google-Places-Schlüssel erreichte seit 2026-05-11 keinen Workflow mehr, und zwei Workflows reichten die nie angelegten Secrets `VOR_VERSION` und `VOR_BASE` weiter, ohne dass es auffiel. Secrets, die in den Repository-Einstellungen liegen, aber in keinem Workflow vorkommen, kann ein Workflow nicht sehen; die Liste dort gleicht nur der Betreiber ab.
- **SSRF-Schutz**: Externe Netzwerkanfragen laufen über `fetch_content_safe` (in `src/utils/http.py`). Diese Funktion verhindert Server-Side Request Forgery, indem sie DNS-Rebinding blockiert, private IP-Adressen (Localhost, internes Netzwerk) ablehnt und DNS-Timeouts erzwingt.
- **Dateisystem**: Schreibvorgänge nutzen `atomic_write`, um Datenkorruption bei Abstürzen zu vermeiden. Pfadeingaben werden strikt validiert (`resolve_env_path` / `validate_path` aus `src/feed/config.py`), um Path-Traversal-Angriffe zu verhindern. Schreibzugriffe sind auf `docs/`, `data/` und `log/` beschränkt.
- **Logging-Sicherheit**: Kontrollzeichen in Logs werden maskiert, um Log-Injection-Attacken zu unterbinden.
- **Input-Validierung**: HTML-Ausgaben werden escaped und kritische XML-Felder in CDATA gekapselt, um XSS in Feed-Readern vorzubeugen.

## VOR / VAO ReST API Dokumentation

Die verbindliche API-Referenz ist das Handbuch der VAO GmbH („Handbuch VAO REST API“, zuletzt Version 2026-01-28). Die VAO stellt es mit den Release Notes öffentlich bereit: [Schnittstellen-Dokumentation](https://cloud.verkehrsauskunft.at/index.php/s/oXxTKf2tEH5fKJr) (verlinkt auf [verkehrsauskunft.at/start](https://www.verkehrsauskunft.at/start)). Das Repository führt seit 17.10.2026 keine eigene Kopie mehr, damit immer die aktuelle Fassung gilt und das Dokument der VAO nicht unter der MIT-Lizenz des Projekts mitverteilt wird (Michael: „Die Schnittstellen Dokumentation liegt bereits öffentlich im Internet … du kannst sie auch gerne entfernen“). Ergänzende Inhalte:

- [`docs/reference/`](https://github.com/Origamihase/wien-oepnv/tree/main/docs/reference) – Endpunktbeschreibungen und Beispielanfragen.
- [`docs/how-to/`](https://github.com/Origamihase/wien-oepnv/tree/main/docs/how-to) – Schritt-für-Schritt-Anleitungen (z. B. Versionsabfragen).
- [`docs/examples/`](https://github.com/Origamihase/wien-oepnv/tree/main/docs/examples) – Shell-Snippets, etwa `version-check.sh`.

Der Abschnitt [„Stationsverzeichnis"](#stationsverzeichnis) erläutert, wie API-basierte Stationsdaten in das Verzeichnis aufgenommen werden.

## Repository-SEO & Promotion

- **About & Topics pflegen** – Verwende eine kurze, keyword-starke Projektbeschreibung (z. B. „RSS-Feed für Störungs- und Baustellenmeldungen im Wiener ÖPNV") und ergänze Topics wie `vienna`, `public-transport`, `verkehrsmeldungen`, `rss-feed`, `python`. So verbesserst du das Ranking innerhalb der GitHub-Suche.
- **Feed prominent verlinken** – Der RSS-Feed ist unter [https://origamihase.github.io/wien-oepnv/feed.xml](https://origamihase.github.io/wien-oepnv/feed.xml) verfügbar. Nutze idealerweise immer diese absolute URL für RSS-Reader, um durchgängig aktuelle Updates zu erhalten. In GitHub Pages bindet der `<link rel="alternate" type="application/rss+xml">`-Eintrag den Feed direkt im HTML-Head ein, wodurch Google Discover & Co. ihn leichter finden.
- **Sitemap & Robots nutzen** – `docs/robots.txt` verweist auf `docs/sitemap.xml`, die täglich vom `seo-guard.yml`-Workflow über `scripts/generate_sitemap.py` regeneriert und in den Branch committed wird. Reiche die Sitemap in der Google Search Console ein, damit neue Meldungen schneller indexiert werden.
- **KI-/LLM-Sichtbarkeit (GEO) nutzen** – `docs/llms.txt` (nach [llms.txt-Standard](https://llmstxt.org/)) liefert generativen Suchmaschinen und LLM-Crawlern eine kuratierte, Markdown-formatierte Karte der informationsdichtesten Seiten. Sie wird im selben `seo-guard.yml`-Lauf über `scripts/generate_llms_txt.py` regeneriert und committed. `docs/robots.txt` erlaubt bereits alle Crawler (`User-agent: *` / `Allow: /`), sodass KI-Agenten wie GPTBot/ClaudeBot/PerplexityBot nicht ausgesperrt sind.
- **Externe Signale aufbauen** – Stelle das Projekt in Blogposts, Foren (z. B. Reddit, Mastodon, lokale ÖPNV-Gruppen) oder Newsletter vor. Backlinks von thematisch relevanten Seiten erhöhen die Sichtbarkeit in klassischen Suchmaschinen.
- **Monitoring etablieren** – Beobachte GitHub Insights (Stars, Forks, Traffic) sowie Feed-Validatoren wie <https://validator.w3.org/feed/>. Automatisierte Checks helfen, strukturelle Probleme früh zu erkennen.

## Troubleshooting

- **Leerer Feed**: Prüfen, ob alle Provider aktiviert sind und ihre Cache-Dateien gültige JSON-Listen enthalten.
- **Abgelaufene Meldungen**: `MAX_ITEM_AGE_DAYS` und `ABSOLUTE_MAX_AGE_DAYS` anpassen; Logs geben Hinweise auf verworfene Items.
- **Timeouts**: Einzelne Provider temporär deaktivieren, um Fehlerquellen einzugrenzen. `PROVIDER_TIMEOUT` lässt sich nur senken, nicht über 25 Sekunden anheben (siehe Konfigurationstabelle).

## Audits & historische Reviews

Für vertiefende Audits, technische Reviews und historische Entscheidungen liegen zahlreiche Berichte in `docs/archive/audits/`
(z. B. [`system_review.md`](archive/audits/system_review.md), [`code_quality_review.md`](archive/audits/code_quality_review.md);
ein Index steht unter [`docs/archive/audits/INDEX.md`](archive/audits/INDEX.md)). Diese Dokumente erleichtern die
Einordnung vergangener Änderungen und liefern Kontext für Weiterentwicklungen des Wien-ÖPNV-Feeds.
