# Entwicklerdokumentation – Wien ÖPNV Feed

Diese Anleitung bündelt sämtliche entwicklungsrelevanten Inhalte des Projekts:
Setup, lokale Workflows, CLI, Konfiguration, Provider-Logik, Stationsverzeichnis,
Sicherheit sowie die zugehörigen GitHub-Actions-Pipelines. Wer das Projekt
einfach nur konsumieren möchte, findet im [README](../README.md) eine
verdichtete Übersicht. Hinweise zum Beitrag (Branching, PRs, Pre-Commit) stehen
in der [`CONTRIBUTING.md`](../CONTRIBUTING.md).

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
| `cache/`              | Versionierte Provider-Zwischenspeicher (`wl`, `oebb`, `baustellen`) für reproduzierbare Feed-Builds plus die Stammstrecke-Sidecars (`pending_trips.json`, `recently_finalised.json`); VOR hat seit 2026-05-11 kein eigenes Cache-Verzeichnis mehr (siehe Hinweis unten). |
| `data/`               | Stationsverzeichnis, GTFS-Testdaten und Hilfslisten (z. B. Pendler-Whitelist).                   |
| `docs/`               | Audit-Berichte, Referenzen, Beispiel-Feeds und das offizielle VAO/VOR-API-Handbuch.              |
| `.github/workflows/`  | Automatisierte Jobs für Cache-Updates, Stationspflege, Feed-Erzeugung und Tests.                |
| `tests/`              | Umfangreiche Pytest-Suite (über 3200 Tests in rund 450 Modulen) für Feed-Logik, Provider-Adapter und Utility-Funktionen. |


> **Hinweis zu Cache-Pfaden:** Die tatsächlichen Verzeichnisse unter `cache/` tragen einen Hash-Suffix zur Cache-Versionierung (Stand Mai 2026: `cache/wl_9d709a/`, `cache/oebb_c40d21/`, `cache/baustellen_d438c3/`). In dieser Dokumentation werden aus Lesbarkeitsgründen verkürzte Schreibweisen wie `cache/wl/events.json` verwendet — sie verweisen jeweils auf das aktuelle Provider-Verzeichnis. Ein eigenes VOR-Cache-Verzeichnis existiert seit der 2026-05-11-Migration nicht mehr (VOR ist auf den Stammstrecken-Monitor beschränkt). Der **Stammstrecke-Monitor** schreibt seit der 2026-05-09-Migration **kein** `events.json` mehr — der Feed-Builder liest die Beobachtungen direkt aus dem CSV-Ledger `data/stats/stammstrecke_<YYYY>.csv`. Unter `cache/stammstrecke/` liegen weiterhin die internen Sidecar-Dateien `pending_trips.json` und `recently_finalised.json`, mit denen der Hbf-Producer (siehe [`docs/reference/stammstrecke_provider_logic.md`](reference/stammstrecke_provider_logic.md)) Trip-IDs zur Doppel-Vermeidung über mehrere Cron-Ticks hinweg trackt.

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
| `FEED_TTL`               | Cache-Hinweis für Clients in Minuten (Standard 15).                             |
| `MAX_ITEM_AGE_DAYS`      | Maximales Alter von Meldungen aus den Caches (Standard 365).                    |
| `ABSOLUTE_MAX_AGE_DAYS`  | Harte Altersgrenze für Meldungen (Standard 540).                                |
| `ENDS_AT_GRACE_MINUTES`  | Kulanzfenster für vergangene Endzeiten (Standard 10 Minuten).                   |
| `FRESH_PUBDATE_WINDOW_MIN` | Toleranzfenster (Minuten) für „frische" pubDates beim Aging-Check (Standard 5). |
| `CACHE_MAX_AGE_HOURS`    | Maximalalter der Provider-Cache-Dateien, ab dem eine Warnung im Log erscheint (Standard 24). |
| `FEED_TITLE_CHAR_LIMIT` / `DESCRIPTION_CHAR_LIMIT` | Maximale Zeichenzahl für Item-Titel/Beschreibungen (Standards 256 / 4000). Negative Werte werden auf `0` geklammert; eine obere Schranke wird derzeit nicht erzwungen (siehe `src/feed/config.py`). |
| `PROVIDER_TIMEOUT`       | Globales Timeout für Netzwerkprovider (Standard 25 Sekunden). Per Provider via `PROVIDER_TIMEOUT_<NAME>` oder `<NAME>_TIMEOUT` anpassbar. |
| `PROVIDER_MAX_WORKERS`   | Anzahl paralleler Worker (0 = automatisch). Feiner steuerbar über `PROVIDER_MAX_WORKERS_<GRUPPE>` bzw. `<GRUPPE>_MAX_WORKERS`. |
| `WL_ENABLE` / `OEBB_ENABLE` / `BAUSTELLEN_ENABLE` / `STAMMSTRECKE_ENABLE` | Aktiviert bzw. deaktiviert die einzelnen Default-Provider (alle Standard: aktiv). `STAMMSTRECKE_ENABLE` steuert den VOR/VAO-basierten Verspätungs- und Ausfall-Monitor. Eine separate `VOR_ENABLE`-Variable existiert seit der 2026-05-11-Konsolidierung **nicht mehr**. |
| `WL_RSS_URL` / `OEBB_RSS_URL` / `BAUSTELLEN_DATA_URL` / `OVERPASS_URL` | Override der Upstream-URLs. Validiert gegen eine Allow-List bekannter Hosts; abweichende Werte werden ignoriert und der Default verwendet (siehe Modul-Docstrings für Details). |
| `OEBB_ONLY_VIENNA`       | Strikte ÖBB-Filterung (`1`/`true`/`0`/`false`, Standard `false`): nur Meldungen mit explizitem Wien-Bezug akzeptieren — keine Pendlerbahnhof-Fallbacks (siehe [`docs/reference/oebb_provider_logic.md`](reference/oebb_provider_logic.md)). |
| `WIEN_OEPNV_OSM_ENRICH`  | Setzt `0` im CI, sobald `scripts/check_overpass_status.py` einen Mirror-Outage detektiert; überspringt dann den OSM-Anreicherungs-Schritt in `scripts/update_station_directory.py`. |
| `WIEN_OEPNV_MANUAL_ENRICH` | Toggle (Standard `1`) für die Nachreicherung manuell gepflegter Auslands-/Distant-AT-Knoten (`type=manual_*`) in `scripts/update_station_directory.py:_enrich_manual_stations`. Auf `0` gesetzt, um in Test-Sandboxen die ~296 realen HAFAS-Round-trips zu vermeiden (siehe `tests/test_update_all_stations_wrapper.py`). |
| `WIEN_OEPNV_PROVIDER_PLUGINS` | Komma-separierte Liste optionaler Provider-Plugin-Module (siehe [`docs/how-to/provider_plugins.md`](how-to/provider_plugins.md)). Standard leer; nicht gesetzte Module werden ignoriert. |
| `WIEN_OEPNV_ENV_FILES` | Komma-separierte Liste zusätzlicher `.env`-Dateien, die vor der Konfiguration eingelesen werden (`src/utils/env.py`). Standard liest `.env`, `data/secrets.env`, `config/secrets.env`. |
| `LOG_LEVEL`, `LOG_DIR`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`, `LOG_FORMAT` | Steuerung der Logging-Ausgabe (`log/errors.log`, `log/diagnostics.log`). `LOG_LEVEL` Standard `INFO`; `LOG_FORMAT=json` aktiviert JSON-Logs. |
| `STATE_PATH`, `STATE_RETENTION_DAYS` | Pfad & Aufbewahrungstage für `data/first_seen.json` (Standard 60 Tage).        |
| `WIEN_OEPNV_CACHE_PRETTY` | Steuert die Formatierung der Cache-Dateien (`1` = gut lesbar, `0` = kompakt). |
| `WIEN_OEPNV_DEBUG`       | Auf `1` gesetzt zeigt die CLI (`python -m src.cli`) bei Fehlern den vollständigen Traceback; Standard verhält sich fail-secure (keine Trace-Ausgabe). |
| `VOR_USER_AGENT`         | Custom User-Agent für VOR/VAO-API-Calls (Standard `wien-oepnv/1.0 (+https://github.com/Origamihase/wien-oepnv)`). |
| `VOR_REQUEST_COUNT_FILE` | Override für den Persistenzpfad des VAO-Tagesbudget-Counters (Standard `data/vor_request_count.json`). |
| `VOR_AUTH_TYPE`          | Erzwingt das Auth-Schema bei der VAO-Token-Normalisierung (`bearer` oder `basic`, Standard: automatische Erkennung aus dem Token-Format in `src/providers/vor.py:_normalise_access_token`). |
| `BAUSTELLEN_TIMEOUT`     | Per-Request-Timeout (Sekunden) für `scripts/update_baustellen_cache.py` (Standard `20`, hart geklammert auf `MAX_BAUSTELLEN_TIMEOUT`). |
| `BAUSTELLEN_FALLBACK_PATH` | Pfad zur lokalen JSON-Fallback-Datei (Standard `data/samples/baustellen_sample.geojson`), die verwendet wird, wenn der OGD-Endpoint der Stadt Wien nicht erreichbar ist. |
| `SITE_BASE_URL`          | Basis-URL für die Sitemap-Generierung (`scripts/generate_sitemap.py`). Standard identisch mit `PAGES_BASE_URL`; gegen die GitHub-Pages-Allow-List validiert. |
| `WIEN_TOKEN`             | Override des Wien-Token-Matches für die `in_vienna`-Heuristik in `src/utils/stations.py` (Standard `wien`). Diakritik wird automatisch geklammert; nur für Test-Sandboxen interessant. |

Alle Pfade werden durch `resolve_env_path` (in `src/feed/config.py`) auf `docs/`, `data/` oder `log/` beschränkt, um Path-Traversal zu verhindern.

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

Der Meldungsfeed sammelt offizielle Störungs- und Hinweisinformationen der Wiener Linien (WL), der Verkehrsverbund Ost-Region GmbH (VOR), der ÖBB sowie ergänzende Baustelleninformationen der Stadt Wien.

### Wiener Linien (WL)

- **Anforderung**: "Alle Meldungen sind interessant." (Die Wiener Linien sind per Definition Wien-fokussiert).
- **Umsetzung**: Der Provider verarbeitet sämtliche Meldungen der Realtime-Schnittstelle. Es erfolgt lediglich eine Filterung nach Status (aktiv) sowie eine Ausschlussprüfung für irrelevante Wartungsinformationen. Eine explizite Geo-Filterung ist nicht notwendig und findet nicht statt.
- **Quelle**: Realtime-Störungs-Endpoint (`WL_RSS_URL`, Default: `https://www.wienerlinien.at/ogd_realtime`).
- **Cache**: `cache/wl/events.json`.

### ÖBB

- **Anforderung**:
  1. Pendlerbahnhöfe mit gestörter Verbindung nach Wien.
  2. Wien nach Pendlerbahnhof.
  3. Innerhalb von Wien (alle Störungen).
- **Umsetzung**: Der Provider implementiert einen **strengen Geo-Filter** (`_is_relevant`):
  - Eine Meldung wird akzeptiert, wenn sie das Keyword "Wien"/"Vienna" oder einen expliziten Wiener Bahnhof enthält.
  - Meldungen, die *nur* Pendlerbahnhöfe (ohne Wien-Bezug) oder *nur* ferne Bahnhöfe erwähnen, werden verworfen.
  - Dies stellt sicher, dass "Störungen im Bereich Mödling" ohne Wien-Bezug (z. B. Richtung Süden) nicht einfließen, solange keine Auswirkung auf die Wien-Verbindung explizit genannt ist (siehe [data/stations.json](../data/stations.json) für Definitionen von `in_vienna` und `pendler`).
  - Mit `OEBB_ONLY_VIENNA=1` lässt sich der Fallback auf reine Pendler-Bahnhof-Routen abschalten — siehe [`docs/reference/oebb_provider_logic.md`](reference/oebb_provider_logic.md).
- **Quelle**: Offizielle ÖBB-Störungsinformationen (RSS-Feed; Default-URL via `OEBB_RSS_URL` überschreibbar, validiert gegen die `fahrplan.oebb.at`-Allow-List).
- **Cache**: `cache/oebb/events.json`.

### Verkehrsverbund Ost-Region (VOR)

- **Anforderung**: VAO-Tagesbudget (100 Requests/Tag) wird seit 2026-05-11 ausschließlich vom S-Bahn-Stammstrecken-Monitor verbraucht. Seit der 2026-05-15-Migration auf `/departureBoard` am Wien Hauptbahnhof sind das **48 Calls/Tag** (1 Hbf-Call × ~48 Cycles statt vorher 2 `/trip`-Calls × 48 Cycles). Ein automatisiertes Disruption-Polling existiert nicht mehr (Operator-Policy "VOR nur für die Stammstrecke").
- **Quelle**: VOR/VAO-ReST-API (`/departureBoard`-Endpunkt am Wien Hauptbahnhof), authentifiziert über Access Token.
- **Persistenz**: keine eigene JSON-Cache-Datei mehr; der Stammstrecken-Monitor schreibt Beobachtungen direkt in zwei CSV-Ledgers: `data/stats/stammstrecke_<YYYY>.csv` (aggregierte Verspätungen pro Richtung und Tick) und `data/stats/ausfaelle_<YYYY>.csv` (eine Zeile pro entdecktem Ausfall, dedupliziert via Pending-Trip-Ledger unter `cache/stammstrecke/`). Siehe [`docs/reference/stammstrecke_provider_logic.md`](reference/stammstrecke_provider_logic.md).

### Stadt Wien – Baustellen

- **Quelle**: Open-Government-Data-Baustellenfeed der Stadt Wien (`BAUSTELLEN_DATA_URL`, Default: offizieller WFS-Endpoint).
- **Cache**: `cache/baustellen/events.json`, gepflegt via `scripts/update_baustellen_cache.py`.
- **Fallback**: Schlägt der Remote-Abruf fehl (z. B. wegen Rate-Limits), nutzt das Skript `data/samples/baustellen_sample.geojson` als Grunddatensatz, damit der Feed konsistent bleibt.
- **Kontext**: Die Meldungen enthalten Metadaten zu Bezirk, Maßnahme, Zeitraum sowie geokodierte Adressen und ergänzen damit ÖPNV-Störungsmeldungen um bauzeitliche Einschränkungen.

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

Unabhängig vom Provider folgen alle Ereignisse derselben Struktur, die auch im Feed verwendet wird. Die wichtigsten Felder im
JSON-Cache (Strings im ISO-8601-Format) bzw. bei direkter Python-Nutzung (Python-`datetime`-Objekte) sind:

| Feld        | Beschreibung                                                                                  |
| ----------- | --------------------------------------------------------------------------------------------- |
| `source`    | Ursprungsquelle der Meldung (`"Wiener Linien"`, `"ÖBB"`, `"VOR/VAO"`, …).                      |
| `category`  | Typ der Meldung, z. B. „Störung“, „Hinweis“, „Baustelle“.                                       |
| `title`     | Bereinigter, menschenlesbarer Titel mit Linienkürzeln.                                         |
| `description` | Ausführliche Beschreibung inkl. Zusatzinfos wie Umleitungen, betroffene Haltestellen usw.     |
| `link`      | Referenz-URL zur Originalmeldung oder weiterführenden Infos.                                   |
| `guid`      | Stabile eindeutige Kennung, geeignet als Primärschlüssel.                                      |
| `pubDate`   | Veröffentlichungszeitpunkt der Meldung.                                                        |
| `starts_at` | Technischer Startzeitpunkt des Ereignisses (häufig identisch mit `pubDate`).                    |
| `ends_at`   | Optionales Ende der Maßnahme; `null`, wenn unbekannt oder bereits vergangen.                   |
| `_identity` | Projektinterner Schlüssel zur Nachverfolgung des „first seen“-Zeitpunkts (optional vorhanden). |

Eine formale Beschreibung steht als [JSON-Schema](schema/events.schema.json)
bereit und eignet sich für Validierungen in Drittprojekten. Alle Felder sind als
Unicode-Strings hinterlegt, zusätzliche provider-spezifische Hilfsfelder werden
vor dem JSON-Export entfernt, sodass die Datensätze stabil und schema-konform
bleiben.

## Stationsverzeichnis

`data/stations.json` vereint ÖBB-, Wiener-Linien-, VOR- und manuell
gepflegte Auslandsknoten in einer Datei. Das Format ist als JSON Schema
unter [`docs/schema/stations.schema.json`](schema/stations.schema.json)
formal definiert; ein Pin-Test (`tests/test_stations_schema.py`)
verhindert Drift.

### Felder pro Eintrag

| Feld | Pflicht | Beschreibung |
| ---- | ------- | ------------ |
| `name` | ✓ | Operator-facing Anzeige-Name (wird im Feed verwendet). **Nicht unique**: zwischen mehreren Einträgen kann derselbe Name vorkommen, etwa bei multi-modalen Knoten, wo Bus- und Tram-Bahnsteige unter zwei DIVAs am selben Knoten geführt werden. Die strukturelle Eindeutigkeits-Garantie tragen `bst_id`/`bst_code`/`vor_id`/`wl_diva`; siehe PR #1452. |
| `in_vienna` | ✓ | `true` wenn die Koordinaten innerhalb des LANDESGRENZEOGD-Polygons liegen. |
| `pendler` | ✓ | `true` für Pendler-Knoten **außerhalb** Wiens (siehe `data/pendler_bst_ids.json`). **Exklusiv zu `in_vienna`**: jede Station ist entweder Wien-Station ODER Pendler, niemals beides. Ausnahmen sind manuell gepflegte Knoten außerhalb des Pendlergürtels: `type: manual_foreign_city` (z. B. München Hauptbahnhof, Roma Termini, Bratislava hl.st.) und `type: manual_distant_at` (z. B. Salzburg Hbf, Graz Hbf, Linz Hbf, Innsbruck Hbf) — bei beiden Sondertypen sind beide Flags `false`. Verstöße werden vom Validator als NamingIssue gemeldet und vom Updater automatisch korrigiert (in_vienna gewinnt). |
| `aliases` | ✓ | Schreibvarianten und IDs zur Erkennung in Provider-Texten. |
| `latitude` / `longitude` | ✓ | WGS84-Koordinaten (validiert gegen das Wien-Polygon für `in_vienna`-Einträge). |
| `source` | ✓ | Komma-getrennte Provider-Tokens (kein Whitespace) aus `oebb,vor,wl,google_places,manual`. |
| `bst_id`, `bst_code` | ÖBB | ÖBB-Stellen-ID und -Stellencode aus dem Excel-Verzeichnis (data.oebb.at). |
| `vor_id` | ÖBB/VOR | VOR/VAO-Stop-ID (numerisch oder volles HAFAS-Token); entspricht typischerweise GTFS-`stop_id`. |
| `wl_diva` | WL | Wiener-Linien-DIVA aus `wienerlinien-ogd-haltestellen.csv`. |
| `wl_stops` | WL | Einzelhaltepunkte (Bahnsteige/Richtungen) inkl. eigener `stop_id`. |
| `type` | – | Sondertyp für manuell gepflegte Knoten außerhalb des Pendlergürtels: `manual_foreign_city` für Auslandsknoten (München, Roma, Bratislava) und `manual_distant_at` für distante österreichische Hauptbahnhöfe (Salzburg, Graz, Linz, Innsbruck etc.). Bei beiden ist die Coordinate-Bounds-Prüfung tolerant. |

Lookups laufen über `src/utils/stations.py:station_info(name)` mit
diakritik-tolerantem Token-Normalizer (Umlaut-Faltung erst ab Token-Länge 4,
damit kurze Stellencodes wie `Sue`/`Su` distinkt bleiben).

### Datenquellen und Lizenzen

| Quelle | Datei(en) | Lizenz | Pflicht-Attribution |
|---|---|---|---|
| **ÖBB-Verkehrsstationen** (`data.oebb.at`) | extrahiert aus dem Excel „Verzeichnis der Verkehrsstationen"; eine atomar-geschriebene Cache-Kopie liegt unter `data/oebb-verkehrsstationen.xlsx` (Soft-Fail-Snapshot seit PR #1450 — wird bei `data.oebb.at`-Outage automatisch verwendet). Zusätzlich `data/gtfs/stops.txt`. | [CC BY 3.0 AT](https://creativecommons.org/licenses/by/3.0/at/) | „Datenquelle: ÖBB-Infrastruktur AG" |
| **Wiener Linien OGD** | `data/wienerlinien-ogd-haltestellen.csv`, `data/wienerlinien-ogd-haltepunkte.csv` (Quelle: `www.wienerlinien.at/ogd_realtime/doku/ogd/`, seit PR #1442; der vorherige `data.wien.gv.at/csv/`-Proxy wurde in der 60. OGD-Phase im September 2025 retired) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | „Datenquelle: Wiener Linien" |
| **VOR (Verkehrsverbund Ost-Region)** | `data/vor-haltestellen.csv`, `data/vor-haltestellen.mapping.json` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | „Datenquelle: VOR Verkehrsverbund Ost-Region" |
| **Wien-Stadtgrenzen-Polygon** | `data/LANDESGRENZEOGD.json` (Layer `ogdwien:LANDESGRENZEOGD` der MA 41 – Stadtvermessung, WFS-API von data.wien.gv.at, `srsName=EPSG:4326`, `outputFormat=json`) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | **„Datenquelle: Stadt Wien – data.wien.gv.at"** |
| **OpenStreetMap Overpass** (Stations-Tier 1) | `src/places/osm_client.py`, Live-Fetch ohne lokalen Cache | [ODbL](https://opendatacommons.org/licenses/odbl/) | „© OpenStreetMap-Mitwirkende" |
| **HAFAS (ÖBB Scotty)** (Stations-Tier 2) | Profil-Sidecar `data/hafas_profile.json` (extrahiert von `scripts/sync_hafas_profile.py`), Live-Anreicherung via `src/places/hafas_client.py`. Liefert hochpräzise Koordinaten und die EVA-Nummer (`hafas_extId`) für Stationen, die OSM nicht abdeckt — schont das Google-Kontingent. | Öffentlich erreichbares Mgate-Backend; Profil aus dem MIT-lizenzierten Open-Source-Projekt [`public-transport/hafas-client`](https://github.com/public-transport/hafas-client) | „Fahrplandaten: ÖBB" |
| **Google Places** (Stations-Tier 3, optional) | Enrichment für die strikte Restmenge nach OSM + HAFAS | [Maps Platform-AGB](https://cloud.google.com/maps-platform/terms/) | – |

### Aktualisierungsskripte

| Skript | Funktion |
| ------ | -------- |
| `python -m src.cli stations update all --verbose` | Führt alle Teilaktualisierungen (ÖBB, WL) in einem Lauf aus. |
| `python -m src.cli stations update directory --verbose` | Aktualisiert das ÖBB-Basisverzeichnis und setzt `in_vienna`/`pendler`. |
| `python scripts/update_wl_stations.py [--no-download] -v` | Lädt die WL-OGD-CSVs vom kanonischen Wiener-Linien-OGD-Endpoint `www.wienerlinien.at/ogd_realtime/doku/ogd/` und führt sie mit `data/stations.json` zusammen. `--no-download` nutzt die lokal gepinnten CSVs (Sandbox/Offline-Modus). Beim Download-Erfolg wird die jeweilige CSV atomar geschrieben; bei Netzwerk-Fehlern wird das gepinnte Snapshot beibehalten. |


Die GitHub Action `.github/workflows/update-stations.yml` aktualisiert
`data/stations.json` wöchentlich automatisch (Cron `0 1 * * 0`, Sonntag 01:00 UTC). Pipeline-Schritte:

1. **VOR-Stop-Liste**: gepinnt in `data/vor-haltestellen.csv`. Seit
   2026-05-11 existiert kein automatisiertes VOR-Stop-Refresh-Skript
   mehr; die CSV wird redaktionell gepflegt. VOR-Stop-IDs ändern sich
   nur selten (Jahre).
2. **Sub-Skripte** (`scripts/update_all_stations.py`) – `update_station_directory.py` →
   `update_wl_stations.py` → `enrich_station_aliases.py`,
   alle gegen ein Temp-File. Erst nach erfolgreicher Validierung wird per
   `atomic_write` ins Repo zurückkopiert. Ein VOR-Stations-Sub-Skript
   gibt es seit 2026-05-11 nicht mehr.
3. **Validation-Gate** – die Sub-Skript-Ausgabe wird vom selben Wrapper
   validiert. Vier Kategorien blockieren den Commit (Working Tree bleibt
   bytewise unverändert): `provider_issues`, `cross_station_id_issues`,
   `naming_issues` (Mutual-Exclusivity, Source-Format, Namens-
   Eindeutigkeit) und `security_issues`. Andere Kategorien
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
   Variante des Validation-Reports (alle 8 Kategorien) für Review-Zwecke.

#### Automatisierte Qualitätsberichte

`python -m src.cli stations validate` erzeugt einen Markdown-Bericht mit
neun Issue-Kategorien: **geographic duplicates**, **alias issues**,
**coordinate anomalies**, **GTFS mismatches**, **security warnings**,
**provider issues** (VOR-/OEBB-Konsistenz), **cross-station ID
collisions**, **identity field conflicts** (raw `wl_diva`/`bst_id`/
`vor_id`/`bst_code`-Kollisionen zwischen Stationen, ergänzt 2026-05-16
in PR #1539) und **naming issues** (kanonische Namens-Eindeutigkeit +
no-space-Source-Format + Vienna/Pendler Mutual-Exclusivity).
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
WARNING bzw. blockieren den Atomic-Write.

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

### Zusätzliche Datenquellen

Weitere offene Datensätze (z. B. ÖBB-GTFS, Streckendaten, Wiener OGD, INSPIRE-Geodaten) können lokal in `data/` abgelegt und mit
Feed- oder Stationsdaten verknüpft werden. Hinweise zu Lizenzierung und Verknüpfung stehen in diesem Abschnitt, um eine saubere
Nachnutzung zu gewährleisten.

Wichtige Sidecar-Dateien unter `data/`, die im obigen Lizenz-Block
nicht einzeln gelistet sind:

| Datei / Verzeichnis | Zweck |
| ------------------- | ----- |
| `data/stations_metadata.json` | Kurate VZG-Streckennummern → Kilometer-Mapping für Stammstrecken-Auswertungen. Testabgesichert via `tests/test_stations_metadata.py`. |
| `data/places_quota.json` | Persistenter Monats-Quota-State des Google-Places-Tiers (siehe [`docs/how-to/google_places_stations.md`](how-to/google_places_stations.md)). Vom Cron-Runner geschrieben. |
| `data/new_places.json` | Optionales Diff-Artefakt aus `scripts/fetch_google_places_stations.py --dump-new`. |
| `data/streckendaten/` | Platzhalter-Verzeichnis für lokale VZG-Schnittdaten (per `.gitignore` versioniert, Inhalt nicht commited). |
| `data/stations_last_run.json` | Heartbeat des wöchentlichen Stations-Refreshs (Validation-Summary, Sub-Skript-Laufzeiten). |
| `data/stations_overrides.json` | Manuelle Korrekturschicht — siehe Abschnitt darüber. |

## Automatisierte Workflows

Die wichtigsten GitHub Actions:

- `update-cycle.yml` – die zentrale Refresh-Pipeline. Trigger: `repository_dispatch: ifttt_feed_trigger` (ein externes IFTTT-Applet feuert ~alle 30 Minuten auf :00/:30 — zuverlässiger als der frühere GitHub-Cron `0,30 * * * *`, der am 2026-05-10 in Commit `55ca72f` entfernt wurde) sowie `workflow_dispatch` für manuelle Operator-Läufe. Einziger Job, der in einem Runner sequenziell die Provider-Cache-Fetcher (WL, ÖBB, Baustellen), den VAO-Pre-flight + die Stammstrecke-Abfrage, den Feed-Build (`docs/feed.xml`, `data/first_seen.json`, `data/stats/stoerungen_<YYYY>.csv`) sowie das README-/`docs/statistik.md`-Render ausführt und alles in einem Auto-Commit zusammenfasst. Hält die `external-api-fetch`-Concurrency-Lane, damit nie zwei API-Cycles parallel laufen.
- VOR ist **ausschließlich** für den S-Bahn-Stammstrecke-Verspätungs-Monitor in `update-cycle.yml` eingesetzt (seit 2026-05-15: `/departureBoard`-Endpunkt am Wien Hauptbahnhof, ~48 Calls/Tag von 100 VAO-Start-Tier-Quota; davor zwei `/trip`-Calls pro Tick = 96/Tag). Eine VOR-Disruption-Polling- bzw. Stations-Anreicherungs-Automatisierung gibt es nicht mehr (Entscheidung 2026-05-11); die zugehörigen Helper-Scripts (`update_vor_cache.py`, `update_vor_stations.py`, `fetch_vor_haltestellen.py`) wurden ebenfalls entfernt. Diagnose-Aufrufe gegen den VOR-Auth-Pfad bleiben via `scripts/verify_vor_access_id.py` und `scripts/check_vor_auth.py` möglich.
- `build-feed.yml` – Code-Change-Verifikationspfad. Der reguläre Cron wurde 2026-05-09 in `update-cycle.yml` migriert; dieser Workflow läuft nur noch auf `push` (für `src/**`, `requirements.txt`, `pyproject.toml`, `.github/workflows/build-feed.yml`) sowie auf `workflow_dispatch` und baut den Feed aus den vorhandenen Caches neu — ohne neue API-Abfrage.
- `update-stations.yml` – pflegt wöchentlich (Sonntag 01:00 UTC, Cron `0 1 * * 0`) `data/stations.json`. Die Anreicherung ist als **drei-stufige Kaskade** modelliert: OpenStreetMap (Overpass API) liefert die primären Koordinaten; der vorgeschaltete Smoke-Test (`scripts/check_overpass_status.py`) bricht den OSM-Schritt aber kontrolliert ab, falls der Mirror down ist. HAFAS (ÖBB Scotty) übernimmt seit 2026-05-14 als **Tier-2-Fallback** alle Stationen ohne OSM-Koordinaten — eingebettet in `request_safe` und durch einen eigenen `CircuitBreaker` abgesichert (siehe `docs/architecture.md` §5). Direkt davor läuft `scripts/sync_hafas_profile.py` als eigener Workflow-Step und aktualisiert das Mgate-Profil-Sidecar aus dem Open-Source-Projekt `public-transport/hafas-client`. Google Places bleibt der **Tier-3-Notausgang** für die strikte Restmenge, die weder OSM noch HAFAS auflösen konnten. VOR ist seit 2026-05-11 **nicht mehr** Teil des Stations-Refreshs (VOR-Stop-IDs aus gepinnter `data/vor-haltestellen.csv`). Manuell gepflegte Auslands-/Distant-AT-Knoten (`type=manual_*`, source=`manual`) durchlaufen die ÖBB-Filter-Stufe und damit auch die Enrichment-Kaskade nicht; sie werden direkt vor dem `write_json` von `_enrich_manual_stations` über den bereits im Speicher liegenden `location_index` (GTFS + VOR) sowie HAFAS LocMatch nachgereichert (idempotent, einträge mit Koordinaten überspringt der Helper). Env-toggle: `WIEN_OEPNV_MANUAL_ENRICH=0` deaktiviert den Schritt — analog zu `WIEN_OEPNV_OSM_ENRICH=0` vom Wrapper-Test (`tests/test_update_all_stations_wrapper.py:test_wrapper_atomic_on_success`) verwendet, weil 296 reale HAFAS-Round-trips eines GitHub-hosted Runners das 180-Sekunden-pytest-Budget reißen würden. Die Stammstrecke-Abfrage und die tägliche `docs/statistik.md`-Regeneration laufen jeweils als Schritt in `update-cycle.yml`.
- `manual-full-refresh.yml` – `workflow_dispatch`-only-Komplettlauf für Disaster-Recovery. Führt sequenziell alles aus, was sonst auf mehrere Cron-Schedules verteilt ist: WL-/ÖBB-/Baustellen-Cache-Refresh, VOR-Secret-Validation, Stammstrecke-`/departureBoard`-Tick, vollständiger Stations-Refresh (OSM/HAFAS/Google-Kaskade + WL-OGD-Merge inkl. Re-Validierung), Feed-Build und Statistik-/README-Regeneration — und committet alles in einem einzigen Commit. Teilt die `external-api-fetch`-Concurrency-Lane mit `update-cycle.yml`.
- `test.yml` & `test-vor-api.yml` – führen die vollständige Test-Suite bzw. VOR-spezifische Integrationstests aus; `test.yml` läuft bei jedem Push sowie Pull Request und stellt die kontinuierliche Testabdeckung sicher.
- `mypy-strict.yml`, `bandit.yml`, `codeql.yml`, `complexity-gate.yml`, `seo-guard.yml` – ergänzende Qualitäts-Gates (strikte Typprüfung, Security-Lint, CodeQL-Scan, Komplexitäts-Baseline, SEO/Sitemap-Pflege).

Der `update-cycle.yml`-Job committet alle Cache-, Feed- und Statistik-Outputs in einem einzigen Commit; ein direkter `needs:`-Trigger zwischen Workflows ist damit unnötig. Eigenständige `update-<provider>-cache.yml`-Workflows gibt es seit der DAG-zu-Single-Job-Migration (2026-05-09) nicht mehr — alle Cache-Fetcher (`update_wl_cache.py`, `update_oebb_cache.py`, `update_baustellen_cache.py`) sind Schritte innerhalb von `update-cycle.yml`. Wer einzelne Cache-Fetcher außerhalb des Cycles manuell auslösen will, ruft das jeweilige Skript per `python -m src.cli cache update <provider>` direkt auf oder triggert den vollständigen `manual-full-refresh.yml`-Job.

## Skripte im Überblick

Der Ordner `scripts/` versammelt alle Wartungs- und Hilfsskripte. Die
meisten werden auch über die einheitliche CLI (`python -m src.cli …`)
gekapselt; ein Direktaufruf bleibt jedoch sinnvoll, wenn Sondermodi
benötigt werden (z. B. `--no-download` für die WL-OGD-CSVs).

### Provider-Caches & Feed-Daten

| Skript | Aufgabe |
| --- | --- |
| `update_wl_cache.py` | Liest die Realtime-Störungen der Wiener Linien und schreibt `cache/wl/events.json`. CLI: `python -m src.cli cache update wl`. |
| `update_oebb_cache.py` | Holt die ÖBB-Störungs-RSS-Feeds, filtert sie strikt auf Wien-Bezug (`_is_relevant`) und persistiert sie. CLI: `python -m src.cli cache update oebb`. |
| `update_baustellen_cache.py` | Lädt den Baustellen-Layer der Stadt Wien (oder den `data/samples/baustellen_sample.geojson`-Fallback) und legt Events ab. CLI: `python -m src.cli cache update baustellen`. |
| `update_stammstrecke_hbf.py` | Aktiver Refresh-Producer für den S-Bahn-Stammstrecke-Monitor (seit 2026-05-15; wird vom IFTTT-getriggerten `update-cycle.yml` ~alle 30 Min aufgerufen). Fragt einmal pro Tick `/departureBoard` am Wien Hauptbahnhof ab, klassifiziert die Abfahrten per Bahnsteig-1/2-Filter + Endhaltestellen-Whitelist und schreibt aggregierte Verspätungs-Zeilen pro Richtung nach `data/stats/stammstrecke_<YYYY>.csv` sowie eine Zeile pro Ausfall nach `data/stats/ausfaelle_<YYYY>.csv` (siehe [Reference](reference/stammstrecke_provider_logic.md)). |
| `update_stammstrecke_status.py` | Legacy-Producer (`/trip` × 2 Richtungen, vor 2026-05-15). Wird vom Cron-Workflow nicht mehr direkt aufgerufen, bleibt aber als Modul importierbar — `update_stammstrecke_hbf.py` re-used die geteilte Pending-Trip- / Recently-Finalised-Infrastruktur, den Quota-Charger sowie das CircuitBreaker-Tuning daraus. |
| `generate_markdown_stats.py` | Aggregiert die CSV-Ledger zu `docs/statistik.md` (30-Tage-Fenster) und patcht die `<!-- STATS:* -->`-Marker im README. |

### Stationsverzeichnis

| Skript | Aufgabe |
| --- | --- |
| `update_all_stations.py` | Wrapper für den vollständigen Stationsverzeichnis-Refresh; ruft die folgenden Sub-Skripte gegen ein Temp-File auf und committet erst nach erfolgreicher Validierung. CLI: `python -m src.cli stations update all`. |
| `update_station_directory.py` | Lädt das ÖBB-Excel und ergänzt Koordinaten in drei Stufen: OSM (Primär) → HAFAS (Tier-2-Fallback) → Google Places (Tier-3-Notausgang). Details siehe `docs/architecture.md` §5. Direkt vor dem Schreiben läuft zusätzlich `_enrich_manual_stations` über den Manual-Block (`type=manual_distant_at` / `manual_foreign_city`), der sonst den ÖBB-Filter umgeht und damit die Enrichment-Kaskade auslässt; er nutzt denselben `location_index` (GTFS + VOR) als billige erste Stufe und fällt auf HAFAS LocMatch zurück. Idempotent: einträge mit bereits gesetzten Koordinaten werden übersprungen. |
| `sync_hafas_profile.py` | Holt `salt` / `ver` / `aid` des ÖBB-Mgate-Profils aus dem Open-Source-Projekt `public-transport/hafas-client` und persistiert sie atomar in `data/hafas_profile.json`. Läuft in `update-stations.yml` als eigener Schritt unmittelbar vor `update_station_directory.py`, damit ÖBB-seitige Credential-Rotation automatisch nachgezogen wird. |
| `update_wl_stations.py` | Lädt `wienerlinien-ogd-haltestellen.csv` und `wienerlinien-ogd-haltepunkte.csv` vom kanonischen Endpoint `www.wienerlinien.at/ogd_realtime/doku/ogd/` und merged sie in `data/stations.json`. Soft-fail mit den gepinnten lokalen CSVs bei Upstream-Outage. Mit `--no-download` werden ausschließlich die lokal gepinnten CSVs verwendet. |
| `enrich_station_aliases.py` | Sucht alternative Schreibweisen pro Station und schreibt sie ins Verzeichnis. |
| `apply_station_overrides.py` | Wendet die kuratierte Korrekturschicht aus `data/stations_overrides.json` auf `data/stations.json` an: drei Operationen (`restore` / `patch_coords` / `remove`), idempotent, defensive Logs bei fehlenden Ziel-DIVAs. Behebt Upstream-Defekte der Wiener Linien OGD (falsche Koordinaten für einzelne DIVAs, fehlende Haltepunkte bei aktiven Stationen, geografisch identische Haltepunkte unterschiedlicher DIVAs), die sich nicht durch Tuning der Merge-Logik beseitigen lassen. Läuft in `update_all_stations.py` zwischen `enrich_station_aliases.py` und dem Validator-Gate. Jeder Override trägt `reason` + `expires_when`-Prädikat, damit er retirable bleibt, sobald der Upstream-Feed gefixt ist. |
| `fetch_google_places_stations.py` | Optionaler Tier-3-Notausgang für Stationen, die weder OSM noch HAFAS auflösen konnten; manueller Direktaufruf, nutzt das Quota-Stateful-Modul aus `src/places/`. Die OSM/HAFAS/Google-Kaskade läuft auch automatisch in `update-stations.yml`. |
| `validate_stations.py` | CLI-Front-end für `src.utils.stations_validation`; das gleiche Verhalten ist via `python -m src.cli stations validate` erreichbar. |
| `validate_vor_mapping.py` | Prüft die statische `data/vor-haltestellen.mapping.json` (VOR-ID ↔ Name) auf duplikate IDs und Format-Drift. Die Mapping-Datei wird seit 2026-05-11 redaktionell gepflegt. |

### Auth- & Diagnose-Helfer

| Skript | Aufgabe |
| --- | --- |
| `verify_vor_access_id.py` | Smoke-Test für `VOR_ACCESS_ID` und `VOR_BASE_URL`. CLI: `python -m src.cli tokens verify vor`. |
| `verify_google_places_access.py` | Health-Check der Google-Places-Schlüssel (deckt FieldMask-/PERMISSION_DENIED-Fälle auf). CLI: `python -m src.cli tokens verify google-places`. |
| `check_vor_auth.py` | Prüft den vollständigen Auth-Pfad (`VorAuth`) inklusive Header. CLI: `python -m src.cli tokens verify vor-auth`. |
| `check_overpass_status.py` | OSM-Mirror-Smoke-Test mit `out count`-Query; setzt `WIEN_OEPNV_OSM_ENRICH=0` im CI, falls der Mirror down ist. |
| `preflight_quota_check.py` | Hard-Gate für `update-cycle.yml`: bricht **vor** jeder API-Anfrage ab, wenn das persistierte Tagesbudget bereits ausgeschöpft ist. Stdlib-only, eigene Exit-Codes. |
| `scan_secrets.py` | Repository-Scan via `src.utils.secret_scanner`. CLI: `python -m src.cli security scan`. |
| `configure_feed.py` | Interaktiver Konfigurations-Assistent (schreibt `.env`). CLI: `python -m src.cli config wizard`. |
| `scaffold_provider_plugin.py` | Erzeugt ein lauffähiges Provider-Plugin-Skelett (`register_providers`-Hook); siehe [How-to](how-to/provider_plugins.md). |

### Statische Analyse & Build-Hygiene

| Skript | Aufgabe |
| --- | --- |
| `run_static_checks.py` | Dispatcher für `ruff check`, `mypy --strict`, `pip-audit` und den Secret-Scanner; CI-äquivalent. CLI: `python -m src.cli checks`. |
| `check_complexity.py` | C901-Komplexitäts-Gate (Threshold 15, Allowlist `.c901-baseline.txt`). |
| `regen_c901_baseline.sh` | Regeneriert die Baseline nach gezielten Refactors; lokal ausführen, Diff committen. |
| `regen_mypy_baseline.sh` | Regeneriert `.mypy-baseline.txt` (gleiche Mechanik wie c901). |
| `generate_sitemap.py` | Generiert `docs/sitemap.xml` und `docs/feed.xml`-Hinweise; läuft im `seo-guard.yml`-Workflow. |
| `gtfs.py` | GTFS-Hilfsmodul (`read_gtfs_stops`); wird von Tests und vom Stations-Validator konsumiert. |
| `optimize_site_assets.py` | Erzeugt minifizierte `site.min.css` / `site.min.js` aus den lesbaren Quellen sowie WebP-Geschwister für `train.png` / `footer-bg.jpg`. Wird vom Pre-Commit-Hook `site-assets-minified --check` aufgerufen, um Drift zwischen Quelle und committetem Bundle zu verhindern. Idempotent (`--check`/`--skip-images` ohne Image-Tools nutzbar). |

## Entwicklung & Qualitätssicherung

- **Tests**: `python -m pytest` führt über 3200 Unit- und Integrationstests in rund 450 Modulen unter `tests/` aus.
- **Kontinuierliche Tests**: Die GitHub Action `test.yml` automatisiert die im Audit empfohlene regelmäßige Testausführung und bricht Builds bei fehlschlagender Test-Suite ab.
- **Statische Analyse & Typprüfung**: `ruff check` (Stil/Konsistenz, Regelgruppen `E`, `F`, `S`, `B`, `UP` — siehe `pyproject.toml`) und `mypy --strict` (vollständige Typabdeckung über `src/` und `tests/`, derzeit 0 Errors) laufen identisch zur CI via `python -m src.cli checks`. Optional lassen sich über `--fix` Ruff-Autofixes aktivieren oder zusätzliche Argumente an Ruff durchreichen. Ein zusätzlicher `mypy-strict.yml`-Workflow setzt das Allowlist-Gate auf Pull Requests durch.
- **Pre-Commit-Hooks**: `.pre-commit-config.yaml` aktiviert lokale Checks bei jedem `git commit`: Ruff, `mypy --strict`, Bandit, der eigene Secret-Scanner (`scripts/scan_secrets.py`), das C901-Komplexitäts-Gate (`scripts/check_complexity.py`) sowie Whitespace-/Merge-Conflict-/YAML-/TOML-/JSON-/Large-File-Hygiene. Einmalig nach dem Klonen `pre-commit install` ausführen — Details in [`CONTRIBUTING.md`](../CONTRIBUTING.md).
- **Logging**: Zur Laufzeit entsteht `log/errors.log` mit rotierenden Dateien; Größe und Anzahl sind konfigurierbar.

## Developer Experience & Observability

### Einheitliche CLI für Betriebsaufgaben

Die neue Kommandozeile (`python -m src.cli`) bündelt bisher verstreute Skripte. Wichtige Unterbefehle:

- `python -m src.cli cache update <wl|oebb|baustellen>` – aktualisiert den jeweiligen Provider-Cache. (VOR ist seit 2026-05-11 nicht mehr cache-fähig — der Stammstrecke-Monitor läuft als eigener Workflow-Step und schreibt direkt in den CSV-Ledger.)
- `python -m src.cli stations update <all|directory|wl>` – führt die bestehenden Stations-Skripte mit optionalem `--verbose` aus. (VOR-Stations-Refresh wurde 2026-05-11 entfernt — `data/vor-haltestellen.csv` wird redaktionell gepflegt.)
- `python -m src.cli feed build` – startet den Feed-Build mit der aktuellen Umgebung.
- `python -m src.cli feed lint` – prüft die aggregierten Items auf fehlende GUIDs oder unerwartete Duplikate.
- `python -m src.cli tokens verify <vor|google-places|vor-auth>` – validiert Secrets und API-Zugänge.
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

- **Secrets**: (z. B. `VOR_ACCESS_ID`, `VOR_BASE_URL`) werden ausschließlich über Umgebungsvariablen bereitgestellt und niemals im
  Repository abgelegt. Das Skript `src/utils/secret_scanner.py` schützt proaktiv vor versehentlich eingecheckten Geheimnissen.
  Optionale Secondary-Variablen für den VOR/VAO-Stack: `VOR_BASE` (Legacy-Alias für `VOR_BASE_URL`, akzeptiert in `src/providers/vor.py:_validated_vor_base_url`), `VOR_VERSION` und `VOR_VERSIONS` (Versions-Strings für die VAO-URL, z. B. `v1.11.0` — `VOR_VERSIONS` ist Fallback-Alias für `VOR_VERSION`). Beide werden in `.github/workflows/update-cycle.yml` als Build-Secrets durchgereicht.
- **SSRF-Schutz**: Externe Netzwerkanfragen laufen über `fetch_content_safe` (in `src/utils/http.py`). Diese Funktion verhindert Server-Side Request Forgery, indem sie DNS-Rebinding blockiert, private IP-Adressen (Localhost, internes Netzwerk) ablehnt und DNS-Timeouts erzwingt.
- **Dateisystem**: Schreibvorgänge nutzen `atomic_write`, um Datenkorruption bei Abstürzen zu vermeiden. Pfadeingaben werden strikt validiert (`resolve_env_path` / `validate_path` aus `src/feed/config.py`), um Path-Traversal-Angriffe zu verhindern. Schreibzugriffe sind auf `docs/`, `data/` und `log/` beschränkt.
- **Logging-Sicherheit**: Kontrollzeichen in Logs werden maskiert, um Log-Injection-Attacken zu unterbinden.
- **Input-Validierung**: HTML-Ausgaben werden escaped und kritische XML-Felder in CDATA gekapselt, um XSS in Feed-Readern vorzubeugen.

## VOR / VAO ReST API Dokumentation

Die detaillierte API-Referenz ist vollständig in `docs/reference/manuals/Handbuch_VAO_ReST_API_latest.pdf` hinterlegt. Ergänzende Inhalte:

- [`docs/reference/`](reference/) – Endpunktbeschreibungen und Beispielanfragen.
- [`docs/how-to/`](how-to/) – Schritt-für-Schritt-Anleitungen (z. B. Versionsabfragen).
- [`docs/examples/`](examples/) – Shell-Snippets, etwa `version-check.sh`.

Der Abschnitt [„Stationsverzeichnis"](#stationsverzeichnis) erläutert, wie API-basierte Stationsdaten in das Verzeichnis aufgenommen werden.

## Repository-SEO & Promotion

- **About & Topics pflegen** – Verwende eine kurze, keyword-starke Projektbeschreibung (z. B. „RSS-Feed für Störungs- und Baustellenmeldungen im Wiener ÖPNV") und ergänze Topics wie `vienna`, `public-transport`, `verkehrsmeldungen`, `rss-feed`, `python`. So verbesserst du das Ranking innerhalb der GitHub-Suche.
- **Feed prominent verlinken** – Der RSS-Feed ist unter [https://origamihase.github.io/wien-oepnv/feed.xml](https://origamihase.github.io/wien-oepnv/feed.xml) verfügbar. Nutze idealerweise immer diese absolute URL für RSS-Reader, um durchgängig aktuelle Updates zu erhalten. In GitHub Pages bindet der `<link rel="alternate" type="application/rss+xml">`-Eintrag den Feed direkt im HTML-Head ein, wodurch Google Discover & Co. ihn leichter finden.
- **Sitemap & Robots nutzen** – `docs/robots.txt` verweist auf `docs/sitemap.xml`, die täglich vom `seo-guard.yml`-Workflow über `scripts/generate_sitemap.py` regeneriert und in den Branch committed wird. Reiche die Sitemap in der Google Search Console ein, damit neue Meldungen schneller indexiert werden.
- **Externe Signale aufbauen** – Stelle das Projekt in Blogposts, Foren (z. B. Reddit, Mastodon, lokale ÖPNV-Gruppen) oder Newsletter vor. Backlinks von thematisch relevanten Seiten erhöhen die Sichtbarkeit in klassischen Suchmaschinen.
- **Monitoring etablieren** – Beobachte GitHub Insights (Stars, Forks, Traffic) sowie Feed-Validatoren wie <https://validator.w3.org/feed/>. Automatisierte Checks helfen, strukturelle Probleme früh zu erkennen.

## Troubleshooting

- **Leerer Feed**: Prüfen, ob alle Provider aktiviert sind und ihre Cache-Dateien gültige JSON-Listen enthalten.
- **Abgelaufene Meldungen**: `MAX_ITEM_AGE_DAYS` und `ABSOLUTE_MAX_AGE_DAYS` anpassen; Logs geben Hinweise auf verworfene Items.
- **Timeouts**: `PROVIDER_TIMEOUT` erhöhen oder einzelne Provider temporär deaktivieren, um Fehlerquellen einzugrenzen.

## Audits & historische Reviews

Für vertiefende Audits, technische Reviews und historische Entscheidungen liegen zahlreiche Berichte in `docs/archive/audits/`
(z. B. [`system_review.md`](archive/audits/system_review.md), [`code_quality_review.md`](archive/audits/code_quality_review.md);
ein Index steht unter [`docs/archive/audits/INDEX.md`](archive/audits/INDEX.md)). Diese Dokumente erleichtern die
Einordnung vergangener Änderungen und liefern Kontext für Weiterentwicklungen des Wien-ÖPNV-Feeds.
