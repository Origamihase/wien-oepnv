# Wien ÖPNV Feed – Projektdokumentation

Dieses Repository bündelt sämtliche Komponenten, um einen konsolidierten Meldungs-Feed für den öffentlichen Verkehr in Wien
und dem niederösterreichisch-burgenländischen Umland zu erzeugen. Der Feed kombiniert offizielle Informationen der Wiener
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
| `cache/`              | Versionierte Provider-Zwischenspeicher (`wl`, `oebb`, `vor`) für reproduzierbare Feed-Builds.    |
| `data/`               | Stationsverzeichnis, GTFS-Testdaten und Hilfslisten (z. B. Pendler-Whitelist).                   |
| `docs/`               | Audit-Berichte, Referenzen, Beispiel-Feeds und das offizielle VAO/VOR-API-Handbuch.              |
| `.github/workflows/`  | Automatisierte Jobs für Cache-Updates, Stationspflege, Feed-Erzeugung und Tests.                |
| `tests/`              | Umfangreiche Pytest-Suite (>250 Tests) für Feed-Logik, Provider-Adapter und Utility-Funktionen.  |

## Installation & Setup

1. **Python-Version**: Das Projekt ist auf Python 3.11 ausgelegt (`pyproject.toml`).
2. **Abhängigkeiten installieren**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```
3. **Optionale Tools**: Für statische Analysen empfiehlt sich `python -m pip install ruff mypy`.
4. **Umgebungsvariablen**: Sensible Daten (Tokens, Basis-URLs) werden ausschließlich über die Umgebung gesetzt.
   Lokale `.env`-Dateien können über `WIEN_OEPNV_ENV_FILES` eingebunden werden.

## Konfiguration des Feed-Builds

`src/build_feed.py` liest zahlreiche Umgebungsvariablen. Die wichtigsten Parameter:

| Variable                 | Zweck / Standardwert                                                            |
| ------------------------ | ------------------------------------------------------------------------------- |
| `OUT_PATH`               | Zielpfad für den RSS-Feed (Standard `docs/feed.xml`).                           |
| `FEED_TITLE` / `DESC`    | Titel und Beschreibung des Feeds.                                               |
| `FEED_LINK`              | Referenz-URL (Standard: GitHub-Repository).                                     |
| `MAX_ITEMS`              | Anzahl der Einträge im Feed (Standard 10).                                      |
| `FEED_TTL`               | Cache-Hinweis für Clients in Minuten (Standard 15).                             |
| `MAX_ITEM_AGE_DAYS`      | Maximales Alter von Meldungen aus den Caches (Standard 365).                    |
| `ABSOLUTE_MAX_AGE_DAYS`  | Harte Altersgrenze für Meldungen (Standard 540).                                |
| `ENDS_AT_GRACE_MINUTES`  | Kulanzfenster für vergangene Endzeiten (Standard 10 Minuten).                   |
| `PROVIDER_TIMEOUT`       | Timeout für Cache-Ladevorgänge (Standard 25 Sekunden).                          |
| `PROVIDER_MAX_WORKERS`   | Anzahl paralleler Worker (0 = automatisch).                                     |
| `WL_ENABLE` / `OEBB_ENABLE` / `VOR_ENABLE` | Aktiviert bzw. deaktiviert die einzelnen Provider (Standard: aktiv). |
| `LOG_DIR`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT` | Steuerung der Logging-Ausgabe (`log/errors.log`, `log/diagnostics.log`). |
| `STATE_PATH`, `STATE_RETENTION_DAYS` | Pfad & Aufbewahrung für `data/first_seen.json`.                      |

Alle Pfade werden durch `_resolve_env_path` auf `docs/`, `data/` oder `log/` beschränkt, um Path-Traversal zu verhindern.

### Fehlerprotokolle

- Läuft der Feed-Build über `src/build_feed.py`, landen Fehler- und Traceback-Ausgaben automatisch in `log/errors.log` (rotierende Log-Datei, konfigurierbar über `LOG_DIR`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`). Ohne Fehler bleibt die Datei unberührt.
- Ausführliche Statusmeldungen (z. B. zum VOR-Abruf) werden zusätzlich in `log/diagnostics.log` gesammelt.
- Beim manuellen Aufruf der Hilfsskripte, z. B. `scripts/update_vor_cache.py`, erscheinen Warnungen und Fehler direkt auf `stdout`. Für nachträgliche Analysen kannst du den jeweiligen Lauf zusätzlich mit `LOG_DIR` auf ein separates Verzeichnis umleiten.

## Provider-spezifische Workflows

### Wiener Linien (WL)

- **Quelle**: Realtime-Störungs-Endpoint (`WL_RSS_URL`, Default: `https://www.wienerlinien.at/ogd_realtime`).
- **Cache**: `cache/wl/events.json`, aktualisiert durch `scripts/update_wl_cache.py` bzw. `.github/workflows/update-wl-cache.yml`.
- **Spezifika**: Aufbereitung und Zeitleistenlogik befinden sich in `src/providers/wiener_linien.py` und `src/providers/wl_fetch.py`.

### ÖBB

- **Quelle**: Offizielle ÖBB-Störungsinformationen gemäß interner Whitelist.
- **Cache**: `cache/oebb/events.json`, gepflegt über `scripts/update_oebb_cache.py` sowie `.github/workflows/update-oebb-cache.yml`.
- **Spezifika**: Provider-Adapter `src/providers/oebb.py` normalisiert die vielfältigen Meldungsformate und setzt die WL/ÖBB
  Namenskonventionen um.

### Verkehrsverbund Ost-Region (VOR)

- **Quelle**: VOR/VAO-ReST-API, authentifiziert über einen Access Token (`VOR_ACCESS_ID`).
- **Cache**: `cache/vor/events.json`, gepflegt mittels `scripts/update_vor_cache.py` und `.github/workflows/update-vor-cache.yml`.
- **Rate-Limit**: Tageslimits werden automatisch überwacht (`MAX_REQUESTS_PER_DAY` in `src/providers/vor.py`). Wiederholte
  Cache-Aktualisierungen werden bei ausgeschöpftem Limit übersprungen.
- **Unterstützung**: Für lokale Experimente stehen Hilfsskripte wie `scripts/check_vor_auth.py` oder
  `scripts/fetch_vor_haltestellen.py` bereit.

## Feed-Ausführung lokal

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

- **ÖBB-Verkehrsstationen** (`Verzeichnis der Verkehrsstationen.xlsx`, Lizenz [CC BY 3.0 AT](https://creativecommons.org/licenses/by/3.0/at/)).
- **Wiener Linien OGD** (`wienerlinien-ogd-haltestellen.csv`, `wienerlinien-ogd-haltepunkte.csv`, Lizenz [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)).
- **VOR**: GTFS- oder CSV-Exporte unter [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

### Aktualisierungsskripte

| Skript | Funktion |
| ------ | -------- |
| `python scripts/update_all_stations.py --verbose` | Führt alle Teilaktualisierungen (ÖBB, WL, VOR) in einem Lauf aus. |
| `python scripts/update_station_directory.py --verbose` | Aktualisiert das ÖBB-Basisverzeichnis und setzt `in_vienna`/`pendler`. |
| `python scripts/update_wl_stations.py --verbose` | Ergänzt WL-spezifische Haltestelleninformationen. |
| `python scripts/update_vor_stations.py --verbose [--use-api]` | Importiert VOR-Daten aus CSV oder API und reichert Stationen an. |


Die GitHub Action `.github/workflows/update-stations.yml` aktualisiert `data/stations.json` monatlich automatisch.

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
- `test.yml` & `test-vor-api.yml` – führen die vollständige Test-Suite bzw. VOR-spezifische Integrationstests aus.

Alle Feed-Builds warten auf die Cache-Jobs (`needs`-Abhängigkeit), damit stets konsistente Daten verwendet werden.

## Entwicklung & Qualitätssicherung

- **Tests**: `python -m pytest` führt sämtliche Unit- und Integrationstests aus (`tests/`).
- **Statische Analyse**: `ruff check` (Stil/Konsistenz) und `mypy` (selektive Typprüfung, Fokus auf `src/build_feed.py`).
- **Logging**: Zur Laufzeit entsteht `log/errors.log` mit rotierenden Dateien; Größe und Anzahl sind konfigurierbar.

## Authentifizierung & Sicherheit

- Secrets (z. B. `VOR_ACCESS_ID`, `VOR_BASE_URL`) werden ausschließlich über Umgebungsvariablen bereitgestellt und niemals im
  Repository abgelegt.
- Beispielskripte und Tests nutzen Platzhalter oder `export`-Statements und schreiben keine Klartextwerte in Logs.
- Pfadangaben sind auf kontrollierte Verzeichnisse beschränkt; ungültige Eingaben lösen Warnungen oder Fallbacks aus.

## VOR / VAO ReST API Dokumentation

Die detaillierte API-Referenz ist vollständig in `docs/Handbuch_VAO_ReST_API_2025-08-11.pdf` hinterlegt. Ergänzende Inhalte:

- `docs/reference/` – Endpunktbeschreibungen und Beispielanfragen.
- `docs/how-to/` – Schritt-für-Schritt-Anleitungen (z. B. Versionsabfragen).
- `docs/examples/` – Shell-Snippets, etwa `version-check.sh`.
- `docs/vor_api_review.md`, `docs/status_vor_api.md` – Audit- und Statusberichte.

Der Abschnitt „VOR ergänzen“ im Stationskapitel erläutert, wie API-basierte Stationsdaten in das Verzeichnis aufgenommen werden.

## Troubleshooting

- **Leerer Feed**: Prüfen, ob alle Provider aktiviert sind und ihre Cache-Dateien gültige JSON-Listen enthalten.
- **Abgelaufene Meldungen**: `MAX_ITEM_AGE_DAYS` und `ABSOLUTE_MAX_AGE_DAYS` anpassen; Logs geben Hinweise auf verworfene Items.
- **API-Authentifizierung**: Mit `python scripts/check_vor_auth.py` lässt sich die Gültigkeit des Tokens verifizieren.
- **Timeouts**: `PROVIDER_TIMEOUT` erhöhen oder einzelne Provider temporär deaktivieren, um Fehlerquellen einzugrenzen.

---

Für vertiefende Audits, technische Reviews und historische Entscheidungen liegen zahlreiche Berichte in `docs/` (z. B.
`system_review.md`, `code_quality_review.md`). Diese Dokumente erleichtern die Einordnung vergangener Änderungen und liefern
Kontext für Weiterentwicklungen des Wien-ÖPNV-Feeds.
