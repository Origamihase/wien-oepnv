# Wien ÖPNV Feed – Projektdokumentation

### Status Badges
[![Update VOR Cache](https://github.com/Origamihase/wien-oepnv/actions/workflows/update-vor-cache.yml/badge.svg)](https://github.com/Origamihase/wien-oepnv/actions/workflows/update-vor-cache.yml)
[![Update ÖBB Cache](https://github.com/Origamihase/wien-oepnv/actions/workflows/update-oebb-cache.yml/badge.svg)](https://github.com/Origamihase/wien-oepnv/actions/workflows/update-oebb-cache.yml)
[![Test VOR API](https://github.com/Origamihase/wien-oepnv/actions/workflows/test-vor-api.yml/badge.svg)](https://github.com/Origamihase/wien-oepnv/actions/workflows/test-vor-api.yml)

[![Feed Build](https://github.com/Origamihase/wien-oepnv/actions/workflows/build-feed.yml/badge.svg?branch=main)](https://github.com/Origamihase/wien-oepnv/actions/workflows/build-feed.yml)
[![Tests](https://github.com/Origamihase/wien-oepnv/actions/workflows/test.yml/badge.svg)](https://github.com/Origamihase/wien-oepnv/actions/workflows/test.yml)
[![SEO-Checks](https://github.com/Origamihase/wien-oepnv/actions/workflows/seo-guard.yml/badge.svg)](https://github.com/Origamihase/wien-oepnv/actions/workflows/seo-guard.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Subscribe to Feed](https://img.shields.io/badge/RSS-Subscribe_to_Feed-orange?style=flat&logo=rss)](https://origamihase.github.io/wien-oepnv/feed.xml)

Dieses Repository bündelt sämtliche Komponenten, um einen konsolidierten Meldungs-Feed für den öffentlichen Verkehr in Wien
und im niederösterreichisch-burgenländischen Umland zu erzeugen. Der Feed kombiniert offizielle Informationen der Wiener
Linien (WL), der ÖBB und der Verkehrsverbund Ost-Region GmbH (VOR) und stellt sie als aufbereitetes RSS-Dokument zur
Verfügung. Zusätzlich sind Werkzeuge zur Pflege des Stationsverzeichnisses, zur Verwaltung der Provider-Caches sowie eine
komplette Referenzdokumentation für die VOR/VAO-ReST-API enthalten.

## Projektziele

- **Zentrale Datenaufbereitung** – Störungsmeldungen, Baustellen und Hinweise mehrerer Provider werden vereinheitlicht,
  dedupliziert und mit konsistenten Metadaten versehen.
- **Reproduzierbarer Feed-Build** – Sämtliche Schritte (Cache-Aktualisierung, Feed-Generierung, Tests) lassen sich lokal oder in
  CI/CD-Workflows reproduzieren.
- **Nachvollziehbare Datenbasis** – Alle externen Datenquellen, Lizenzen und Skripte zur Pflege des Stationsverzeichnisses sind
  dokumentiert und versioniert.

## Systemüberblick

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
| `cache/`              | Versionierte Provider-Zwischenspeicher (`wl`, `oebb`, `vor`, `baustellen`) für reproduzierbare Feed-Builds. |
| `data/`               | Stationsverzeichnis, GTFS-Testdaten und Hilfslisten (z. B. Pendler-Whitelist).                   |
| `docs/`               | Audit-Berichte, Referenzen, Beispiel-Feeds und das offizielle VAO/VOR-API-Handbuch.              |
| `.github/workflows/`  | Automatisierte Jobs für Cache-Updates, Stationspflege, Feed-Erzeugung und Tests.                |
| `tests/`              | Umfangreiche Pytest-Suite (~1000 Tests) für Feed-Logik, Provider-Adapter und Utility-Funktionen.  |


> **Hinweis zu Cache-Pfaden:** Die tatsächlichen Verzeichnisse unter `cache/` tragen einen Hash-Suffix zur Cache-Versionierung (Stand Mai 2026: `cache/wl_9d709a/`, `cache/oebb_c40d21/`, `cache/vor_929f1c/`). In dieser Dokumentation werden aus Lesbarkeitsgründen verkürzte Schreibweisen wie `cache/wl/events.json` verwendet — sie verweisen jeweils auf das aktuelle Provider-Verzeichnis.

## Repository-SEO & Promotion

- **About & Topics pflegen** – Verwende eine kurze, keyword-starke Projektbeschreibung (z. B. „RSS-Feed für Störungs- und Baustellenmeldungen im Wiener ÖPNV“) und ergänze Topics wie `vienna`, `public-transport`, `verkehrsmeldungen`, `rss-feed`, `python`. So verbesserst du das Ranking innerhalb der GitHub-Suche.
- **Feed prominent verlinken** – Der RSS-Feed ist unter [https://origamihase.github.io/wien-oepnv/feed.xml](https://origamihase.github.io/wien-oepnv/feed.xml) verfügbar. Nutze idealerweise immer diese absolute URL für RSS-Reader, um durchgängig aktuelle Updates zu erhalten. In GitHub Pages bindet der `<link rel="alternate" type="application/rss+xml">`-Eintrag den Feed direkt im HTML-Head ein, wodurch Google Discover & Co. ihn leichter finden.
- **Sitemap & Robots nutzen** – `docs/robots.txt` verweist auf `docs/sitemap.xml`, die täglich vom `seo-guard.yml`-Workflow über `scripts/generate_sitemap.py` regeneriert und in den Branch committed wird. Reiche die Sitemap in der Google Search Console ein, damit neue Meldungen schneller indexiert werden.
- **Externe Signale aufbauen** – Stelle das Projekt in Blogposts, Foren (z. B. Reddit, Mastodon, lokale ÖPNV-Gruppen) oder Newsletter vor. Backlinks von thematisch relevanten Seiten erhöhen die Sichtbarkeit in klassischen Suchmaschinen.
- **Monitoring etablieren** – Beobachte GitHub Insights (Stars, Forks, Traffic) sowie Feed-Validatoren wie <https://validator.w3.org/feed/>. Automatisierte Checks helfen, strukturelle Probleme früh zu erkennen.

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

# Feed generieren (äquivalent zu python -m src.build_feed).
python -m src.cli feed build

# Zugangsdaten prüfen und beim ersten Fehler abbrechen.
python -m src.cli tokens verify --stop-on-error

# Stationsverzeichnis prüfen und Bericht speichern.
python -m src.cli stations validate --output docs/stations_validation_report.md

# Ruff + mypy wie in der CI ausführen.
python -m src.cli checks --fix

# Interaktiven Konfigurationsassistenten starten (schreibt .env).
python -m src.cli config wizard

# Repository auf versehentlich eingecheckte Secrets prüfen.
python -m src.cli security scan
```

Die Unterbefehle akzeptieren standardmäßig alle bekannten Ziele (z. B. Provider `wl`, `oebb`, `vor`) und lassen sich bei Bedarf
präzise einschränken. Über `--python` kann ein alternativer Interpreter für die Hilfsskripte gesetzt werden.

## Konfiguration des Feed-Builds

Der Feed-Generator liest zahlreiche Umgebungsvariablen. Für den Einstieg empfiehlt sich der
Assistent `python -m src.cli config wizard`, der eine bestehende `.env` einliest, die relevanten
Schlüssel erklärt und wahlweise interaktiv oder per `--accept-defaults` eine neue Konfiguration
schreibt. Die wichtigsten Parameter:

| Variable                 | Zweck / Standardwert                                                            |
| ------------------------ | ------------------------------------------------------------------------------- |
| `OUT_PATH`               | Zielpfad für den RSS-Feed (Standard `docs/feed.xml`).                           |
| `FEED_TITLE` / `DESC`    | Titel und Beschreibung des Feeds.                                               |
| `FEED_LINK`              | Referenz-URL (nur http/https, Standard: GitHub-Repository).                     |
| `MAX_ITEMS`              | Anzahl der Einträge im Feed (Standard 10).                                      |
| `FEED_TTL`               | Cache-Hinweis für Clients in Minuten (Standard 15).                             |
| `MAX_ITEM_AGE_DAYS`      | Maximales Alter von Meldungen aus den Caches (Standard 365).                    |
| `ABSOLUTE_MAX_AGE_DAYS`  | Harte Altersgrenze für Meldungen (Standard 540).                                |
| `ENDS_AT_GRACE_MINUTES`  | Kulanzfenster für vergangene Endzeiten (Standard 10 Minuten).                   |
| `PROVIDER_TIMEOUT`       | Globales Timeout für Netzwerkprovider (Standard 25 Sekunden). Per Provider via `PROVIDER_TIMEOUT_<NAME>` oder `<NAME>_TIMEOUT` anpassbar. |
| `PROVIDER_MAX_WORKERS`   | Anzahl paralleler Worker (0 = automatisch). Feiner steuerbar über `PROVIDER_MAX_WORKERS_<GRUPPE>` bzw. `<GRUPPE>_MAX_WORKERS`. |
| `WL_ENABLE` / `OEBB_ENABLE` / `VOR_ENABLE` | Aktiviert bzw. deaktiviert die einzelnen Provider (Standard: aktiv). |
| `BAUSTELLEN_ENABLE`      | Steuert den neuen Baustellen-Provider (Default: aktiv, nutzt Stadt-Wien-OGD bzw. Fallback-Daten). |
| `LOG_DIR`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`, `LOG_FORMAT` | Steuerung der Logging-Ausgabe (`log/errors.log`, `log/diagnostics.log`). |
| `STATE_PATH`, `STATE_RETENTION_DAYS` | Pfad & Aufbewahrung für `data/first_seen.json`.                      |
| `WIEN_OEPNV_CACHE_PRETTY` | Steuert die Formatierung der Cache-Dateien (`1` = gut lesbar, `0` = kompakt). |

Alle Pfade werden durch `_resolve_env_path` auf `docs/`, `data/` oder `log/` beschränkt, um Path-Traversal zu verhindern.

### Logging-Initialisierung als Bibliothek verwenden

Wird `build_feed` als Skript ausgeführt (`python -m src.cli feed build`), richtet es seine Logging-Handler automatisch über
`configure_logging()` ein. Beim Einbinden des Moduls in andere Anwendungen bleibt die globale Logging-Konfiguration ab
Python-Import unverändert; rufe in diesem Fall `src.build_feed.configure_logging()` explizit auf, bevor du die Feed-Funktionen
verwendest.

### Fehlerprotokolle

- Läuft der Feed-Build über `python -m src.cli feed build`, landen Fehler- und Traceback-Ausgaben automatisch in `log/errors.log` (rotierende Log-Datei, konfigurierbar über `LOG_DIR`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`). Ohne Fehler bleibt die Datei unberührt.
- Ausführliche Statusmeldungen (z. B. zum VOR-Abruf) werden zusätzlich in `log/diagnostics.log` gesammelt.
- Beim manuellen Aufruf der Hilfsskripte (bzw. `python -m src.cli cache update vor`) erscheinen Warnungen und Fehler direkt auf `stdout`. Für nachträgliche Analysen kannst du den jeweiligen Lauf zusätzlich mit `LOG_DIR` auf ein separates Verzeichnis umleiten.
- Setzt du `LOG_FORMAT=json`, schreibt das Projekt strukturierte JSON-Logs mit Zeitstempeln im Format `Europe/Vienna`. Ohne Angabe bleibt das klassische Textformat aktiv.

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
  von Dictionaries ein (Wrapper wie `src.build_feed.read_cache_wl()` sind bereits vorkonfiguriert für „wl“, „oebb“, „vor“ und
  „baustellen“).
- **Live-Abruf der Provider** – Die Module `src.providers.wl_fetch`, `src.providers.oebb` und `src.providers.vor` stellen
  jeweils eine Funktion `fetch_events()` bereit, die die Rohdaten der Wiener Linien, ÖBB bzw. der VOR/VAO-API direkt
  normalisiert. Authentifizierung und Ratenlimits der VOR-API werden dabei automatisch behandelt.

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
| `category`  | Typ der Meldung, z. B. „Störung“, „Hinweis“, „Baustelle“.                                       |
| `title`     | Bereinigter, menschenlesbarer Titel mit Linienkürzeln.                                         |
| `description` | Ausführliche Beschreibung inkl. Zusatzinfos wie Umleitungen, betroffene Haltestellen usw.     |
| `link`      | Referenz-URL zur Originalmeldung oder weiterführenden Infos.                                   |
| `guid`      | Stabile eindeutige Kennung, geeignet als Primärschlüssel.                                      |
| `pubDate`   | Veröffentlichungszeitpunkt der Meldung.                                                        |
| `starts_at` | Technischer Startzeitpunkt des Ereignisses (häufig identisch mit `pubDate`).                    |
| `ends_at`   | Optionales Ende der Maßnahme; `null`, wenn unbekannt oder bereits vergangen.                   |
| `_identity` | Projektinterner Schlüssel zur Nachverfolgung des „first seen“-Zeitpunkts (optional vorhanden). |

Eine formale Beschreibung steht als [JSON-Schema](docs/schema/events.schema.json)
bereit und eignet sich für Validierungen in Drittprojekten. Alle Felder sind als
Unicode-Strings hinterlegt, zusätzliche provider-spezifische Hilfsfelder werden
vor dem JSON-Export entfernt, sodass die Datensätze stabil und schema-konform
bleiben.

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
  - Dies stellt sicher, dass "Störungen im Bereich Mödling" ohne Wien-Bezug (z.B. Richtung Süden) nicht einfließen, solange keine Auswirkung auf die Wien-Verbindung explizit genannt ist (siehe [data/stations.json](data/stations.json) für Definitionen von `in_vienna` und `pendler`).
- **Quelle**: Offizielle ÖBB-Störungsinformationen.
- **Cache**: `cache/oebb/events.json`.

### Verkehrsverbund Ost-Region (VOR)

- **Anforderung**: Nur Abfragen für "Flughafen Wien" und "Hauptbahnhof Wien".
- **Umsetzung**: Der Provider verwendet standardmäßig eine Whitelist (`VOR_MONITOR_STATIONS_WHITELIST`), die auf `"Wien Hauptbahnhof,Flughafen Wien"` voreingestellt ist.
  - Dies minimiert API-Requests ("VAO Start" Kontingent) und fokussiert auf die zentralen Pendlerknoten.
  - Weitere Stationen werden nur bei expliziter Konfiguration abgerufen.
- **Quelle**: VOR/VAO-ReST-API, authentifiziert über Access Token.
- **Cache**: `cache/vor/events.json`.

### Stadt Wien – Baustellen

- **Quelle**: Open-Government-Data-Baustellenfeed der Stadt Wien (`BAUSTELLEN_DATA_URL`, Default: offizieller WFS-Endpoint).
- **Cache**: `cache/baustellen/events.json`, gepflegt via `scripts/update_baustellen_cache.py`.
- **Fallback**: Schlägt der Remote-Abruf fehl (z. B. wegen Rate-Limits), nutzt das Skript `data/samples/baustellen_sample.geojson` als Grunddatensatz, damit der Feed konsistent bleibt.
- **Kontext**: Die Meldungen enthalten Metadaten zu Bezirk, Maßnahme, Zeitraum sowie geokodierte Adressen und ergänzen damit ÖPNV-Störungsmeldungen um bauzeitliche Einschränkungen.

### Eigene Provider-Plugins

Zusätzliche Datenquellen lassen sich ohne Änderungen am Kerncode anbinden. Das
How-to [eigene Provider-Plugins anbinden](docs/how-to/provider_plugins.md)
erläutert den Workflow und verweist auf das Skript
`scripts/scaffold_provider_plugin.py`, das ein lauffähiges Modul-Skelett
erzeugt. Aktivierte Plugins erscheinen automatisch im Feed-Health-Report und
können über `WIEN_OEPNV_PROVIDER_PLUGINS` gesteuert werden.

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
export VOR_ENABLE=true
# Provider-spezifische Secrets/Tokens setzen (z. B. VOR_ACCESS_ID, VOR_BASE_URL ...)
python -m src.cli feed build
```

Der Feed liegt anschließend unter `docs/feed.xml`. Bei Bedarf lässt sich `OUT_PATH` auf ein alternatives Verzeichnis umbiegen.

## Stationsverzeichnis

`data/stations.json` vereint ÖBB-, Wiener-Linien-, VOR- und manuell
gepflegte Auslandsknoten in einer Datei. Das Format ist als JSON Schema
unter [`docs/schema/stations.schema.json`](docs/schema/stations.schema.json)
formal definiert; ein Pin-Test (`tests/test_stations_schema.py`)
verhindert Drift.

### Felder pro Eintrag

| Feld | Pflicht | Beschreibung |
| ---- | ------- | ------------ |
| `name` | ✓ | Kanonischer Anzeige-Name (eindeutig, wird im Feed verwendet). |
| `in_vienna` | ✓ | `true` wenn die Koordinaten innerhalb des LANDESGRENZEOGD-Polygons liegen. |
| `pendler` | ✓ | `true` für Pendler-Knoten **außerhalb** Wiens (siehe `data/pendler_bst_ids.json`). **Exklusiv zu `in_vienna`**: jede Station ist entweder Wien-Station ODER Pendler, niemals beides. Einzige Ausnahme: `type: manual_foreign_city` (München, Roma) — beide Flags `false`. Verstöße werden vom Validator als NamingIssue gemeldet und vom Updater automatisch korrigiert (in_vienna gewinnt). |
| `aliases` | ✓ | Schreibvarianten und IDs zur Erkennung in Provider-Texten. |
| `latitude` / `longitude` | ✓ | WGS84-Koordinaten (validiert gegen das Wien-Polygon für `in_vienna`-Einträge). |
| `source` | ✓ | Komma-getrennte Provider-Tokens (kein Whitespace) aus `oebb,vor,wl,google_places,manual`. |
| `bst_id`, `bst_code` | ÖBB | ÖBB-Stellen-ID und -Stellencode aus dem Excel-Verzeichnis (data.oebb.at). |
| `vor_id` | ÖBB/VOR | VOR/VAO-Stop-ID (numerisch oder volles HAFAS-Token); entspricht typischerweise GTFS-`stop_id`. |
| `wl_diva` | WL | Wiener-Linien-DIVA aus `wienerlinien-ogd-haltestellen.csv`. |
| `wl_stops` | WL | Einzelhaltepunkte (Bahnsteige/Richtungen) inkl. eigener `stop_id`. |
| `type` | – | `manual_foreign_city` für die Auslandsknoten München Hauptbahnhof und Roma Termini. |

Lookups laufen über `src/utils/stations.py:station_info(name)` mit
diakritik-tolerantem Token-Normalizer (Umlaut-Faltung erst ab Token-Länge 4,
damit kurze Stellencodes wie `Sue`/`Su` distinkt bleiben).

### Datenquellen und Lizenzen

| Quelle | Datei(en) | Lizenz | Pflicht-Attribution |
|---|---|---|---|
| **ÖBB-Verkehrsstationen** (`data.oebb.at`) | extrahiert aus dem Excel „Verzeichnis der Verkehrsstationen" + `data/gtfs/stops.txt` | [CC BY 3.0 AT](https://creativecommons.org/licenses/by/3.0/at/) | „Datenquelle: ÖBB-Infrastruktur AG" |
| **Wiener Linien OGD** | `data/wienerlinien-ogd-haltestellen.csv`, `data/wienerlinien-ogd-haltepunkte.csv` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | „Datenquelle: Stadt Wien – data.wien.gv.at" |
| **VOR (Verkehrsverbund Ost-Region)** | `data/vor-haltestellen.csv`, `data/vor-haltestellen.mapping.json` | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | „Datenquelle: VOR Verkehrsverbund Ost-Region" |
| **Wien-Stadtgrenzen-Polygon** | `data/LANDESGRENZEOGD.json` (Layer `ogdwien:LANDESGRENZEOGD` der MA 41 – Stadtvermessung, WFS-API von data.wien.gv.at, `srsName=EPSG:4326`, `outputFormat=json`) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | **„Datenquelle: Stadt Wien – data.wien.gv.at"** |
| **Google Places** (optional) | Enrichment | [Maps Platform-AGB](https://cloud.google.com/maps-platform/terms/) | – |

### Aktualisierungsskripte

| Skript | Funktion |
| ------ | -------- |
| `python -m src.cli stations update all --verbose` | Führt alle Teilaktualisierungen (ÖBB, WL, VOR) in einem Lauf aus. |
| `python -m src.cli stations update directory --verbose` | Aktualisiert das ÖBB-Basisverzeichnis und setzt `in_vienna`/`pendler`. |
| `python scripts/update_wl_stations.py [--no-download] -v` | Lädt die WL-OGD-CSVs von `data.wien.gv.at` und führt sie zusammen. `--no-download` nutzt die lokalen Dateien (Sandbox/Offline-Modus). |
| `python -m src.cli stations update vor --verbose` | Importiert VOR-Daten aus CSV oder API und reichert Stationen an. |


Die GitHub Action `.github/workflows/update-stations.yml` aktualisiert
`data/stations.json` monatlich automatisch (Cron `0 1 1 * *`). Pipeline-Schritte:

1. **VOR-Stop-Liste auffrischen** – `scripts/fetch_vor_haltestellen.py`
   holt die aktuelle Liste vom HAFAS-Endpoint `anachb.vor.at` und
   überschreibt `data/vor-haltestellen.csv`. Best-effort: bei Netzwerk-
   oder Rate-Limit-Fehler wird die gepinnte CSV weitergenutzt
   (`continue-on-error: true`, mit GitHub-`::warning::`).
2. **Sub-Skripte** (`scripts/update_all_stations.py`) – `update_station_directory.py` →
   `update_vor_stations.py` → `update_wl_stations.py` → `enrich_station_aliases.py`,
   alle gegen ein Temp-File. Erst nach erfolgreicher Validierung wird per
   `atomic_write` ins Repo zurückkopiert.
3. **Validation-Gate** – die Sub-Skript-Ausgabe wird vom selben Wrapper
   validiert. Vier Kategorien blockieren den Commit (Working Tree bleibt
   bytewise unverändert): `provider_issues`, `cross_station_id_issues`,
   `naming_issues` (Mutual-Exclusivity, Source-Format, Namens-
   Eindeutigkeit) und `security_issues`. Andere Kategorien
   (`alias_issues`, `coordinate_issues` mit `manual_foreign_city`-
   Exemption) sind tolerant.
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
acht Issue-Kategorien: **geographic duplicates**, **alias issues**,
**coordinate anomalies**, **GTFS mismatches**, **security warnings**,
**provider issues** (VOR-/OEBB-Konsistenz), **cross-station ID
collisions** und **naming issues** (kanonische Namens-Eindeutigkeit +
no-space-Source-Format + Vienna/Pendler Mutual-Exclusivity).
Über `--output docs/stations_validation_report.md` wird der Bericht
persistiert; mit `--fail-on-issues` bricht die CLI bei jedem Befund mit
einem Fehlercode ab. In CI läuft der Validator als Pflicht-Gate (siehe
`.github/workflows/test.yml`); zusätzlich regeneriert
`update-stations.yml` den persistenten Report im monatlichen Daten-Refresh.

### Pendler-Whitelist

Zwei komplementäre Dateien legen fest, welche Bahnhöfe außerhalb der
Stadtgrenze als Pendler-Knoten ins Verzeichnis aufgenommen werden:

- **`data/pendler_bst_ids.json`** – Liste von ÖBB-`bst_id`-Werten.
  Eintrag wirkt sofort: ist die ID im ÖBB-Excel-Verzeichnis vorhanden,
  wird die Station mit `pendler=true` übernommen.
- **`data/pendler_candidates.json`** – name-basierte Wishlist (siehe
  [`docs/schema/pendler_candidates.schema.json`](docs/schema/pendler_candidates.schema.json)).
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

### Zusätzliche Datenquellen

Weitere offene Datensätze (z. B. ÖBB-GTFS, Streckendaten, Wiener OGD, INSPIRE-Geodaten) können lokal in `data/` abgelegt und mit
Feed- oder Stationsdaten verknüpft werden. Hinweise zu Lizenzierung und Verknüpfung stehen in diesem Abschnitt, um eine saubere
Nachnutzung zu gewährleisten.

## Automatisierte Workflows

Die wichtigsten GitHub Actions:

- `update-wl-cache.yml`, `update-oebb-cache.yml`, `update-vor-cache.yml`, `update-baustellen-cache.yml` – füllen die Provider-Caches.
- `update-stations.yml` – pflegt monatlich `data/stations.json`.
- `update-google-places-stations.yml` – reichert das Stationsverzeichnis optional mit Google-Places-Metadaten an.
- `build-feed.yml` – erzeugt `docs/feed.xml` auf Basis der aktuellen Caches.
- `test.yml` & `test-vor-api.yml` – führen die vollständige Test-Suite bzw. VOR-spezifische Integrationstests aus; `test.yml` läuft bei jedem Push sowie Pull Request und stellt die kontinuierliche Testabdeckung sicher.
- `mypy-strict.yml`, `bandit.yml`, `codeql.yml`, `seo-guard.yml` – ergänzende Qualitäts-Gates (strikte Typprüfung, Security-Lint, CodeQL-Scan, SEO/Sitemap-Pflege).

Cache-Update-Workflows committen ihre Ergebnisse in den Branch; der Feed-Build liest beim nächsten Lauf den jeweils aktuellen Stand. Eine direkte `needs:`-Abhängigkeit zwischen Workflows ist in GitHub Actions nicht vorgesehen — bei zeitkritischer Konsistenz lässt sich stattdessen ein `workflow_run`-Trigger ergänzen.

## Entwicklung & Qualitätssicherung

- **Tests**: `python -m pytest` führt rund 1000 Unit- und Integrationstests aus (`tests/`).
- **Kontinuierliche Tests**: Die GitHub Action `test.yml` automatisiert die im Audit empfohlene regelmäßige Testausführung und bricht Builds bei fehlschlagender Test-Suite ab.
- **Statische Analyse & Typprüfung**: `ruff check` (Stil/Konsistenz, Regelgruppen `E`, `F`, `S`, `B`) und `mypy --strict` (vollständige Typabdeckung über `src/` und `tests/`, derzeit 0 Errors) laufen identisch zur CI via `python -m src.cli checks`. Optional lassen sich über `--fix` Ruff-Autofixes aktivieren oder zusätzliche Argumente an Ruff durchreichen. Ein zusätzlicher `mypy-strict.yml`-Workflow setzt das Allowlist-Gate auf Pull Requests durch.
- **Pre-Commit-Hooks**: `.pre-commit-config.yaml` aktiviert lokale Checks (Ruff, mypy, Secret-Scan, Whitespace-Hygiene) bei jedem `git commit`. Einmalig nach dem Klonen `pre-commit install` ausführen — Details in [`CONTRIBUTING.md`](CONTRIBUTING.md).
- **Logging**: Zur Laufzeit entsteht `log/errors.log` mit rotierenden Dateien; Größe und Anzahl sind konfigurierbar.

## Developer Experience & Observability

### Einheitliche CLI für Betriebsaufgaben

Die neue Kommandozeile (`python -m src.cli`) bündelt bisher verstreute Skripte. Wichtige Unterbefehle:

- `python -m src.cli cache update <wl|oebb|vor|baustellen>` – aktualisiert den jeweiligen Provider-Cache.
- `python -m src.cli stations update <all|directory|vor|wl>` – führt die bestehenden Stations-Skripte mit optionalem `--verbose` aus.
- `python -m src.cli feed build` – startet den Feed-Build mit der aktuellen Umgebung.
- `python -m src.cli feed lint` – prüft die aggregierten Items auf fehlende GUIDs oder unerwartete Duplikate.
- `python -m src.cli tokens verify <vor|google-places|vor-auth>` – validiert Secrets und API-Zugänge.
- `python -m src.cli checks [--fix] [--ruff-args …]` – ruft die statischen Prüfungen konsistent zur CI auf.

### Qualitätsberichte für das Stationsverzeichnis

`python -m src.cli stations validate --output docs/stations_validation_report.md` erstellt den Report `docs/stations_validation_report.md`. Die Ausgabe enthält zusammengefasste Kennzahlen und detaillierte Listen der gefundenen Probleme. Über `--decimal-places` lässt sich die Toleranz für Dubletten steuern.

### Logging & Beobachtbarkeit

Die CLI respektiert die vorhandene Logging-Konfiguration (`log/errors.log`, `log/diagnostics.log`). Für Ad-hoc-Audits lassen sich Berichte und Skriptausgaben über `--output`-Parameter in nachvollziehbaren Pfaden versionieren. Jeder Feed-Build erzeugt zusätzlich einen aktuellen Gesundheitsbericht unter `docs/feed-health.md` (lokal nach jedem Build, nicht im Repository versioniert).

## Authentifizierung & Sicherheit

- **Secrets**: (z. B. `VOR_ACCESS_ID`, `VOR_BASE_URL`) werden ausschließlich über Umgebungsvariablen bereitgestellt und niemals im
  Repository abgelegt. Das Skript `src/utils/secret_scanner.py` schützt proaktiv vor versehentlich eingecheckten Geheimnissen.
- **SSRF-Schutz**: Externe Netzwerkanfragen laufen über `fetch_content_safe` (in `src/utils/http.py`). Diese Funktion verhindert Server-Side Request Forgery, indem sie DNS-Rebinding blockiert, private IP-Adressen (Localhost, internes Netzwerk) ablehnt und DNS-Timeouts erzwingt.
- **Dateisystem**: Schreibvorgänge nutzen `atomic_write`, um Datenkorruption bei Abstürzen zu vermeiden. Pfadeingaben werden strikt validiert (`_resolve_env_path`), um Path-Traversal-Angriffe zu verhindern. Schreibzugriffe sind auf `docs/`, `data/` und `log/` beschränkt.
- **Logging-Sicherheit**: Kontrollzeichen in Logs werden maskiert, um Log-Injection-Attacken zu unterbinden.
- **Input-Validierung**: HTML-Ausgaben werden escaped und kritische XML-Felder in CDATA gekapselt, um XSS in Feed-Readern vorzubeugen.

## VOR / VAO ReST API Dokumentation

Die detaillierte API-Referenz ist vollständig in `docs/reference/manuals/Handbuch_VAO_ReST_API_latest.pdf` hinterlegt. Ergänzende Inhalte:

- `docs/reference/` – Endpunktbeschreibungen und Beispielanfragen.
- `docs/how-to/` – Schritt-für-Schritt-Anleitungen (z. B. Versionsabfragen).
- `docs/examples/` – Shell-Snippets, etwa `version-check.sh`.

Der Abschnitt „VOR ergänzen“ im Stationskapitel erläutert, wie API-basierte Stationsdaten in das Verzeichnis aufgenommen werden.

## Troubleshooting

- **Leerer Feed**: Prüfen, ob alle Provider aktiviert sind und ihre Cache-Dateien gültige JSON-Listen enthalten.
- **Abgelaufene Meldungen**: `MAX_ITEM_AGE_DAYS` und `ABSOLUTE_MAX_AGE_DAYS` anpassen; Logs geben Hinweise auf verworfene Items.
- **Timeouts**: `PROVIDER_TIMEOUT` erhöhen oder einzelne Provider temporär deaktivieren, um Fehlerquellen einzugrenzen.

---

Für vertiefende Audits, technische Reviews und historische Entscheidungen liegen zahlreiche Berichte in `docs/archive/audits/`
(z. B. [`system_review.md`](docs/archive/audits/system_review.md), [`code_quality_review.md`](docs/archive/audits/code_quality_review.md);
ein Index steht unter [`docs/archive/audits/INDEX.md`](docs/archive/audits/INDEX.md)). Diese Dokumente erleichtern die
Einordnung vergangener Änderungen und liefern Kontext für Weiterentwicklungen des Wien-ÖPNV-Feeds.
