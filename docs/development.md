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
- [Konfiguration des Feed-Builds](development-teil-2.md#konfiguration-des-feed-builds)
- [Feed-Ausführung lokal](development-teil-2.md#feed-ausführung-lokal)
- [Provider-spezifische Workflows](development-teil-3.md#provider-spezifische-workflows)
- [Nutzung als Datenquelle in Drittprojekten](development-teil-3.md#nutzung-als-datenquelle-in-drittprojekten)
- [Stationsverzeichnis](development-teil-3.md#stationsverzeichnis)
- [Automatisierte Workflows](development-teil-4.md#automatisierte-workflows)
- [Skripte im Überblick](development-teil-4.md#skripte-im-überblick)
- [Entwicklung & Qualitätssicherung](development-teil-4.md#entwicklung--qualitätssicherung)
- [Developer Experience & Observability](development-teil-4.md#developer-experience--observability)
- [Authentifizierung & Sicherheit](development-teil-4.md#authentifizierung--sicherheit)
- [VOR / VAO ReST API Dokumentation](development-teil-4.md#vor--vao-rest-api-dokumentation)
- [Repository-SEO & Promotion](development-teil-4.md#repository-seo--promotion)
- [Troubleshooting](development-teil-4.md#troubleshooting)
- [Audits & historische Reviews](development-teil-4.md#audits--historische-reviews)

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

Weiter in [Teil 2](development-teil-2.md).
