# Wien ÖPNV Feed – Projektdokumentation

### Status Badges
![Update VOR Cache](https://github.com/Origamihase/wien-oepnv/actions/workflows/update-vor-cache.yml/badge.svg)
![Update ÖBB Cache](https://github.com/Origamihase/wien-oepnv/actions/workflows/update-oebb-cache.yml/badge.svg)
![Test VOR API](https://github.com/Origamihase/wien-oepnv/actions/workflows/test-vor-api.yml/badge.svg)
![Run Tests](https://github.com/Origamihase/wien-oepnv/actions/workflows/test.yml/badge.svg)

[![Feed Build](https://github.com/origamihase/wien-oepnv/actions/workflows/build-feed.yml/badge.svg?branch=main)](https://github.com/origamihase/wien-oepnv/actions/workflows/build-feed.yml)
[![Tests](https://github.com/origamihase/wien-oepnv/actions/workflows/test.yml/badge.svg)](https://github.com/origamihase/wien-oepnv/actions/workflows/test.yml)
[![SEO-Checks](https://github.com/origamihase/wien-oepnv/actions/workflows/seo-guard.yml/badge.svg)](https://github.com/origamihase/wien-oepnv/actions/workflows/seo-guard.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Dieses Repository bündelt sämtliche Komponenten, um einen konsolidierten Meldungs-Feed für den öffentlichen Verkehr in Wien
und im niederösterreichisch-burgenländischen Umland zu erzeugen. Der Feed kombiniert offizielle Informationen der Wiener
Linien (WL), der ÖBB und der Verkehrsverbund Ost-Region GmbH (VOR) und stellt sie als aufbereitetes RSS-Dokument zur
Verfügung. Zusätzlich sind Werkzeuge zur Pflege des Stationsverzeichnisses, zur Verwaltung der Provider-Caches sowie eine
komplette Referenzdokumentation für die VOR/VAO-ReST-API enthalten.

## Projektziele

- **Zentrale Datenaufbereitung** – Störungsmeldungen, Baustellen und Hinweise mehrerer Provider werden vereinheitlicht,
  dedupliziert und mit konsistenten Metadaten versehen.
- **Reproduzierbarer Feed-Bau** – Sämtliche Schritte (Cache-Aktualisierung, Feed-Generierung, Tests) lassen sich lokal oder in
  CI/CD-Workflows reproduzieren.
- **Nachvollziehbare Datenbasis** – Alle externen Datenquellen, Lizenzen und Skripte zur Pflege des Stationsverzeichnisses sind
  dokumentiert und versioniert.

## Systemüberblick

Der Feed-Bau folgt einem klaren Ablauf:

1. **Provider-Caches** – Je Provider existiert ein Update-Skript (`scripts/update_*_cache.py`) sowie eine GitHub Action, die den
   Cache regelmäßig aktualisiert (`cache/<provider>/events.json`). Die Provider lassen sich über Umgebungsvariablen deaktivieren,
   ohne den restlichen Prozess zu beeinflussen.
2. **Feed-Generator** – `src/build_feed.py` liest die Cache-Dateien, normalisiert Texte, entfernt Duplikate und schreibt den
   RSS-Feed nach `docs/feed.xml`. Umfangreiche Guards gegen ungültige Umgebungsvariablen, Pfade oder Zeitzonen stellen stabile
   Builds sicher.
3. **Stationsdaten** – `data/stations.json` liefert vereinheitlichte Stations- und Haltestelleninformationen als Referenz für die
   Provider-Logik. Mehrere Skripte in `scripts/` und automatisierte Workflows pflegen diese Datei fortlaufend.
4. **Dokumentation & Audits** – Der Ordner `docs/` enthält Prüfberichte, API-Anleitungen und Audits, die das Verhalten des
   Systems transparent machen.

## Repository-Gliederung

| Pfad/Datei            | Inhalt                                                                                          |
| --------------------- | ------------------------------------------------------------------------------------------------ |
| `src/`                | Feed-Bau, Provider-Adapter, Utilities (Caching, Logging, Textaufbereitung, Stationslogik).       |
| `scripts/`            | Kommandozeilen-Werkzeuge für Cache-Updates, Stationspflege sowie API-Hilfsfunktionen.            |
| `cache/`              | Versionierte Provider-Zwischenspeicher (`wl`, `oebb`, `vor`, `baustellen`) für reproduzierbare Feed-Builds. |
| `data/`               | Stationsverzeichnis, GTFS-Testdaten und Hilfslisten (z. B. Pendler-Whitelist).                   |
| `docs/`               | Audit-Berichte, Referenzen, Beispiel-Feeds und das offizielle VAO/VOR-API-Handbuch.              |
| `.github/workflows/`  | Automatisierte Jobs für Cache-Updates, Stationspflege, Feed-Erzeugung und Tests.                |
| `tests/`              | Umfangreiche Pytest-Suite (>250 Tests) für Feed-Logik, Provider-Adapter und Utility-Funktionen.  |

## Repository-SEO & Promotion

- **About & Topics pflegen** – Verwende eine kurze, keyword-starke Projektbeschreibung (z. B. „RSS-Feed für Störungs- und Baustellenmeldungen im Wiener ÖPNV“) und ergänze Topics wie `vienna`, `public-transport`, `verkehrsmeldungen`, `rss-feed`, `python`. So verbesserst du das Ranking innerhalb der GitHub-Suche.
- **Feed prominent verlinken** – Der RSS-Feed ist unter [`docs/feed.xml`](docs/feed.xml) verfügbar. In GitHub Pages bindet der `<link rel="alternate" type="application/rss+xml">`-Eintrag den Feed direkt im HTML-Head ein, wodurch Google Discover & Co. ihn leichter finden.
- **Sitemap & Robots nutzen** – `docs/sitemap.xml` und `docs/robots.txt` sind bereits vorbereitet. Reiche die Sitemap in der Google Search Console ein, damit neue Meldungen schneller indexiert werden.
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
   ```
3. **Statische Analysen**: Die CI führt `ruff check` und `mypy` aus; lokal spiegelst du das Verhalten mit
   ```bash
   python -m pip install -r requirements-dev.txt
   scripts/run_static_checks.py
   ```
4. **Umgebungsvariablen**: Sensible Daten (Tokens, Basis-URLs) werden ausschließlich über die Umgebung gesetzt.
   Lokale `.env`-Dateien können über `WIEN_OEPNV_ENV_FILES` eingebunden werden.

   Das Skript `scripts/run_static_checks.py` führt neben `ruff` und `mypy` auch einen Secret-Scan
   aus (`scripts/scan_secrets.py`), sodass versehentlich eingecheckte Tokens früh auffallen.

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

`src/build_feed.py` liest zahlreiche Umgebungsvariablen. Für den Einstieg empfiehlt sich der
Assistent `scripts/configure_feed.py`, der eine bestehende `.env` einliest, die relevanten
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

Wird `build_feed` als Skript ausgeführt (`python -m src.build_feed`), richtet es seine Logging-Handler automatisch über
`configure_logging()` ein. Beim Einbinden des Moduls in andere Anwendungen bleibt die globale Logging-Konfiguration ab
Python-Import unverändert; rufe in diesem Fall `src.build_feed.configure_logging()` explizit auf, bevor du die Feed-Funktionen
verwendest.

### Fehlerprotokolle

- Läuft der Feed-Build über `src/build_feed.py`, landen Fehler- und Traceback-Ausgaben automatisch in `log/errors.log` (rotierende Log-Datei, konfigurierbar über `LOG_DIR`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`). Ohne Fehler bleibt die Datei unberührt.
- Ausführliche Statusmeldungen (z. B. zum VOR-Abruf) werden zusätzlich in `log/diagnostics.log` gesammelt.
- Beim manuellen Aufruf der Hilfsskripte, z. B. `scripts/update_vor_cache.py`, erscheinen Warnungen und Fehler direkt auf `stdout`. Für nachträgliche Analysen kannst du den jeweiligen Lauf zusätzlich mit `LOG_DIR` auf ein separates Verzeichnis umleiten.
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
  „baustellen“).【F:src/utils/cache.py†L38-L90】【F:src/build_feed.py†L706-L724】
- **Live-Abruf der Provider** – Die Module `src.providers.wl_fetch`, `src.providers.oebb` und `src.providers.vor` stellen
  jeweils eine Funktion `fetch_events()` bereit, die die Rohdaten der Wiener Linien, ÖBB bzw. der VOR/VAO-API direkt
  normalisiert. Authentifizierung und Ratenlimits der VOR-API werden dabei automatisch behandelt.【F:src/providers/wl_fetch.py†L520-L618】【F:src/providers/oebb.py†L109-L168】【F:src/providers/vor.py†L520-L677】

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
bleiben.【F:src/providers/wl_fetch.py†L568-L618】【F:src/providers/oebb.py†L143-L168】【F:src/providers/vor.py†L548-L677】

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

### Eigene Provider-Plugins

Zusätzliche Datenquellen lassen sich ohne Änderungen am Kerncode anbinden. Das
How-to [eigene Provider-Plugins anbinden](docs/how-to/provider_plugins.md)
erläutert den Workflow und verweist auf das Skript
`scripts/scaffold_provider_plugin.py`, das ein lauffähiges Modul-Skelett
erzeugt. Aktivierte Plugins erscheinen automatisch im Feed-Health-Report und
können über `WIEN_OEPNV_PROVIDER_PLUGINS` gesteuert werden.
- **Kontext**: Die Meldungen enthalten Metadaten zu Bezirk, Maßnahme, Zeitraum sowie geokodierte Adressen und ergänzen damit ÖPNV-Störungsmeldungen um bauzeitliche Einschränkungen.

## Feed-Ausführung lokal

Vor produktiven oder manuellen Abrufen empfiehlt sich ein schneller
Vollständigkeitscheck der benötigten Secrets:

```bash
python scripts/verify_vor_access_id.py
```

Das Skript lädt automatisch `.env`, `data/secrets.env` und
`config/secrets.env` und bricht mit Exit-Code `1` ab, wenn kein gültiger
`VOR_ACCESS_ID`-Token gefunden wurde.

```bash
export WL_ENABLE=true
export OEBB_ENABLE=true
export VOR_ENABLE=true
# Provider-spezifische Secrets/Tokens setzen (z. B. VOR_ACCESS_ID, VOR_BASE_URL ...)
python -m src.build_feed
```

Der Feed liegt anschließend unter `docs/feed.xml`. Bei Bedarf lässt sich `OUT_PATH` auf ein alternatives Verzeichnis umbiegen.

## Stationsverzeichnis

`data/stations.json` vereint ÖBB-, Wiener-Linien- und VOR-Haltestellen mit den Feldern `bst_id`, `bst_code`, `name`,
`in_vienna`, `pendler`, `source` sowie optionalen Aliasen. Die Datenbasis stammt aus folgenden Quellen:

- **ÖBB-Verkehrsstationen** (Download von `data.oebb.at`, Lizenz [CC BY 3.0 AT](https://creativecommons.org/licenses/by/3.0/at/)).
- **Wiener Linien OGD** (`wienerlinien-ogd-haltestellen.csv`, `wienerlinien-ogd-haltepunkte.csv`, Lizenz [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)).
- **VOR**: GTFS- oder CSV-Exporte unter [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- **Google**: Ergänzende Abgleiche via Google Maps Platform (Places API) zur Validierung von Geokoordinaten und Aliasen, Nutzung gemäß den [Google Maps Platform Nutzungsbedingungen](https://cloud.google.com/maps-platform/terms/).

### Aktualisierungsskripte

| Skript | Funktion |
| ------ | -------- |
| `python scripts/update_all_stations.py --verbose` | Führt alle Teilaktualisierungen (ÖBB, WL, VOR) in einem Lauf aus. |
| `python scripts/update_station_directory.py --verbose` | Aktualisiert das ÖBB-Basisverzeichnis und setzt `in_vienna`/`pendler`. |
| `python scripts/update_wl_stations.py --verbose` | Ergänzt WL-spezifische Haltestelleninformationen. |
| `python scripts/update_vor_stations.py --verbose [--use-api]` | Importiert VOR-Daten aus CSV oder API und reichert Stationen an. |


Die GitHub Action `.github/workflows/update-stations.yml` aktualisiert `data/stations.json` monatlich automatisch.

#### Automatisierte Qualitätsberichte

Nutze `python -m src.cli stations validate`, um einen Markdown-Bericht zum Stationsverzeichnis zu erzeugen. Der Standardlauf prüft Dubletten anhand der Geokoordinaten, meldet fehlende Alias-Einträge, erkennt Koordinaten-Anomalien (z. B. vertauschte Werte oder fehlende Angaben) und gleicht `vor_id`-Werte mit `data/gtfs/stops.txt` ab. Über `--output docs/stations_validation_report.md` wird der Bericht persistiert und kann in CI-Pipelines mit `--fail-on-issues` als Guardrail dienen.

### Pendler-Whitelist

`data/pendler_bst_ids.json` listet Stationen außerhalb der Stadtgrenze, die dennoch als Pendler:innen-Knoten im Verzeichnis
verbleiben sollen. Änderungen an dieser Liste wirken sich beim nächsten Lauf von `update_station_directory.py` aus.

### Zusätzliche Datenquellen

Weitere offene Datensätze (z. B. ÖBB-GTFS, Streckendaten, Wiener OGD, INSPIRE-Geodaten) können lokal in `data/` abgelegt und mit
Feed- oder Stationsdaten verknüpft werden. Hinweise zu Lizenzierung und Verknüpfung stehen in diesem Abschnitt, um eine saubere
Nachnutzung zu gewährleisten.

## Automatisierte Workflows

Die wichtigsten GitHub Actions:

- `update-wl-cache.yml`, `update-oebb-cache.yml`, `update-vor-cache.yml` – füllen die Provider-Caches.
- `update-stations.yml` – pflegt monatlich `data/stations.json`.
- `build-feed.yml` – erzeugt `docs/feed.xml` auf Basis der aktuellen Caches.
- `test.yml` & `test-vor-api.yml` – führen die vollständige Test-Suite bzw. VOR-spezifische Integrationstests aus; `test.yml` läuft bei jedem Push sowie Pull Request und stellt die kontinuierliche Testabdeckung sicher.

Alle Feed-Builds warten auf die Cache-Jobs (`needs`-Abhängigkeit), damit stets konsistente Daten verwendet werden.

## Entwicklung & Qualitätssicherung

- **Tests**: `python -m pytest` führt sämtliche Unit- und Integrationstests aus (`tests/`).
- **Kontinuierliche Tests**: Die GitHub Action `test.yml` automatisiert die im Audit empfohlene regelmäßige Testausführung und bricht Builds bei fehlschlagender Test-Suite ab.
- **Statische Analyse & Typprüfung**: `ruff check` (Stil/Konsistenz) und `mypy` (vollständige Typabdeckung über das gesamte Paket `src/`) laufen identisch zur CI via `python -m src.cli checks`. Optional lassen sich über `--fix` Ruff-Autofixes aktivieren oder zusätzliche Argumente an Ruff durchreichen.
- **Logging**: Zur Laufzeit entsteht `log/errors.log` mit rotierenden Dateien; Größe und Anzahl sind konfigurierbar.

## Developer Experience & Observability

### Einheitliche CLI für Betriebsaufgaben

Die neue Kommandozeile (`python -m src.cli`) bündelt bisher verstreute Skripte. Wichtige Unterbefehle:

- `python -m src.cli cache update <wl|oebb|vor>` – aktualisiert den jeweiligen Provider-Cache.
- `python -m src.cli stations update <all|directory|vor|wl>` – führt die bestehenden Stations-Skripte mit optionalem `--verbose` aus.
- `python -m src.cli feed build` – startet den Feed-Build mit der aktuellen Umgebung.
- `python -m src.cli feed lint` – prüft die aggregierten Items auf fehlende GUIDs oder unerwartete Duplikate.
- `python -m src.cli tokens verify <vor|google-places|vor-auth>` – validiert Secrets und API-Zugänge.
- `python -m src.cli checks [--fix] [--ruff-args …]` – ruft die statischen Prüfungen konsistent zur CI auf.

### Qualitätsberichte für das Stationsverzeichnis

`python -m src.cli stations validate --output docs/stations_validation_report.md` erstellt den Report `docs/stations_validation_report.md`. Die Ausgabe enthält zusammengefasste Kennzahlen und detaillierte Listen der gefundenen Probleme. Über `--decimal-places` lässt sich die Toleranz für Dubletten steuern.

### Logging & Beobachtbarkeit

Die CLI respektiert die vorhandene Logging-Konfiguration (`log/errors.log`, `log/diagnostics.log`). Für Ad-hoc-Audits lassen sich Berichte und Skriptausgaben über `--output`-Parameter in nachvollziehbaren Pfaden versionieren. Jeder Feed-Build erzeugt zusätzlich einen aktuellen Gesundheitsbericht unter [`docs/feed-health.md`](docs/feed-health.md).

## Authentifizierung & Sicherheit

- **Secrets**: (z. B. `VOR_ACCESS_ID`, `VOR_BASE_URL`) werden ausschließlich über Umgebungsvariablen bereitgestellt und niemals im
  Repository abgelegt. Das Skript `src/utils/secret_scanner.py` schützt proaktiv vor versehentlich eingecheckten Geheimnissen.
- **SSRF-Schutz**: Externe Netzwerkanfragen laufen über `fetch_content_safe` (in `src/utils/http.py`). Diese Funktion verhindert Server-Side Request Forgery, indem sie DNS-Rebinding blockiert, private IP-Adressen (Localhost, internes Netzwerk) ablehnt und DNS-Timeouts erzwingt.
- **Dateisystem**: Schreibvorgänge nutzen `atomic_write`, um Datenkorruption bei Abstürzen zu vermeiden. Pfadeingaben werden strikt validiert (`_resolve_env_path`), um Path-Traversal-Angriffe zu verhindern. Schreibzugriffe sind auf `docs/`, `data/` und `log/` beschränkt.
- **Logging-Sicherheit**: Kontrollzeichen in Logs werden maskiert, um Log-Injection-Attacken zu unterbinden.
- **Input-Validierung**: HTML-Ausgaben werden escaped und kritische XML-Felder in CDATA gekapselt, um XSS in Feed-Readern vorzubeugen.

## VOR / VAO ReST API Dokumentation

Die detaillierte API-Referenz ist vollständig in `docs/reference/manuals/Handbuch_VAO_ReST_API_2026-01-28.pdf` hinterlegt. Ergänzende Inhalte:

- `docs/reference/` – Endpunktbeschreibungen und Beispielanfragen.
- `docs/how-to/` – Schritt-für-Schritt-Anleitungen (z. B. Versionsabfragen).
- `docs/examples/` – Shell-Snippets, etwa `version-check.sh`.
- `docs/vor_api_review.md`, `docs/status_vor_api.md` – Audit- und Statusberichte.

Der Abschnitt „VOR ergänzen“ im Stationskapitel erläutert, wie API-basierte Stationsdaten in das Verzeichnis aufgenommen werden.

## Troubleshooting

- **Leerer Feed**: Prüfen, ob alle Provider aktiviert sind und ihre Cache-Dateien gültige JSON-Listen enthalten.
- **Abgelaufene Meldungen**: `MAX_ITEM_AGE_DAYS` und `ABSOLUTE_MAX_AGE_DAYS` anpassen; Logs geben Hinweise auf verworfene Items.
- **Timeouts**: `PROVIDER_TIMEOUT` erhöhen oder einzelne Provider temporär deaktivieren, um Fehlerquellen einzugrenzen.

---

Für vertiefende Audits, technische Reviews und historische Entscheidungen liegen zahlreiche Berichte in `docs/` (z. B.
`system_review.md`, `code_quality_review.md`). Diese Dokumente erleichtern die Einordnung vergangener Änderungen und liefern
Kontext für Weiterentwicklungen des Wien-ÖPNV-Feeds.
