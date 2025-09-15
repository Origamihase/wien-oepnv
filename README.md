# Wien ÖPNV Feed

Störungen und Einschränkungen für den Großraum Wien aus offiziellen Quellen.

## Erweiterungen

Der RSS-Feed deklariert den Namespace `ext` (`xmlns:ext="https://wien-oepnv.example/schema"`) für zusätzliche Metadaten:

- `ext:first_seen`: Zeitpunkt, wann eine Meldung erstmals im Feed aufgetaucht ist.
- `ext:starts_at`: Beginn der Störung bzw. Maßnahme.
- `ext:ends_at`: Ende der Störung bzw. Maßnahme.

## Cache-Dateien

Drei GitHub Actions pflegen die Zwischenstände der Provider-Abfragen und legen sie im Repository ab:

- [`.github/workflows/update-wl-cache.yml`](.github/workflows/update-wl-cache.yml) schreibt `cache/wl/events.json`.
- [`.github/workflows/update-oebb-cache.yml`](.github/workflows/update-oebb-cache.yml) schreibt `cache/oebb/events.json`.
- [`.github/workflows/update-vor-cache.yml`](.github/workflows/update-vor-cache.yml) schreibt `cache/vor/events.json`.

Der Feed-Workflow wartet vor dem Build auf diese Jobs (`needs`) und kann dadurch direkt auf die aktuellen JSON-Dateien zugreifen.
Der eigentliche Feed-Build liest ausschließlich diese Cache-Dateien; externe API-Abfragen finden beim Generieren des Feeds nicht statt.
Da die Cache-Dateien versioniert im Repository liegen, steht der Feed auch dann zur Verfügung, wenn einer der Upstream-Dienste vorübergehend offline ist.

## Stationsverzeichnis

`data/stations.json` enthält eine vereinfachte Zuordnung der ÖBB-Verkehrsstationen
(`bst_id`, `bst_code`, `name`, `in_vienna`, `pendler`). Die Daten stammen aus dem Datensatz
„[Verzeichnis der Verkehrsstationen](https://data.oebb.at/de/datensaetze~verzeichnis-der-verkehrsstationen~)“
auf dem ÖBB-Open-Data-Portal (Excel-Datei „Verzeichnis der Verkehrsstationen.xlsx“).

### Automatische Aktualisierung

Die GitHub Action [`.github/workflows/update-stations.yml`](.github/workflows/update-stations.yml)
lädt monatlich (Cron `0 0 1 * *`) die aktuelle Excel-Datei und schreibt daraus eine
aktualisierte `data/stations.json`. Änderungen werden automatisch in den Hauptzweig
committet.

### Manuelle Aktualisierung

```bash
python scripts/update_station_directory.py --verbose
```

Das Skript lädt die Excel-Datei herunter, extrahiert die benötigten Spalten und
aktualisiert `data/stations.json`. Über `-v/--verbose` lässt sich eine etwas
ausführlichere Protokollierung aktivieren. Optional können auch Quelle und Ziel
per Argumenten angepasst werden (`--source-url`, `--output`).

### Pendlerstationen

Die Datei `data/pendler_bst_ids.json` enthält eine manuell gepflegte Liste an
BST-IDs für Pendlerstationen. Änderungen an der Auswahl (z. B. neue oder
wegfallende Stationen) müssen von Hand in dieser Datei nachgezogen werden, damit
das Aktualisierungsskript die entsprechenden Einträge in `data/stations.json`
über das Feld `pendler` markieren kann.

## Entwicklung/Tests lokal

```bash
python -m pip install -r requirements.txt  # installiert auch pytest
python -m pytest -q
python -u src/build_feed.py  # erzeugt docs/feed.xml
```

Der erzeugte Feed liegt unter `docs/feed.xml`.

Fehlerprotokolle landen in `log/errors.log`. Das Verzeichnis `log/` wird bei Bedarf
automatisch angelegt. Wird die Datei größer als `LOG_MAX_BYTES`, rotiert sie und
ältere Versionen werden als `errors.log.1` usw. (max. `LOG_BACKUP_COUNT`) im selben
Ordner abgelegt.

Die Werte lassen sich über die Umgebungsvariablen `LOG_MAX_BYTES` (in Byte)
und `LOG_BACKUP_COUNT` anpassen. Beispiel: eine Rotation ab 2 MB und das
Behalten von zehn Backups:

```bash
LOG_MAX_BYTES=2097152 LOG_BACKUP_COUNT=10 python -u src/build_feed.py
```

## Umgebungsvariablen

### Allgemein (`src/build_feed.py`)

| Variable | Typ | Standardwert | Beschreibung |
| --- | --- | --- | --- |
| `LOG_LEVEL` | str | `"INFO"` | Log-Level für Ausgaben. |
| `LOG_DIR` | str | `"log"` | Basisverzeichnis für Log-Dateien. |
| `LOG_MAX_BYTES` | int | `1000000` | Maximale Größe von `errors.log` bevor rotiert wird. |
| `LOG_BACKUP_COUNT` | int | `5` | Anzahl der Vorversionen von `errors.log`, die behalten werden. |
| `OUT_PATH` | str | `"docs/feed.xml"` | Zielpfad für den erzeugten Feed (muss unter `docs/` liegen). |
| `FEED_TITLE` | str | `"ÖPNV Störungen Wien & Umgebung"` | Titel des RSS-Feeds. |
| `FEED_LINK` | str | `"https://github.com/Origamihase/wien-oepnv"` | Link zur Projektseite. |
| `FEED_DESC` | str | `"Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen"` | Beschreibung des RSS-Feeds. |
| `FEED_TTL` | int | `15` | Minuten, die Clients den Feed im Cache halten dürfen. |
| `DESCRIPTION_CHAR_LIMIT` | int | `170` | Maximale Länge der Item-Beschreibung. |
| `FRESH_PUBDATE_WINDOW_MIN` | int | `5` | Zeitfenster (Minuten), in dem Meldungen ohne Datum als „frisch“ gelten und mit aktuellem `pubDate` versehen werden. |
| `MAX_ITEMS` | int | `60` | Maximale Anzahl an Items im Feed. |
| `MAX_ITEM_AGE_DAYS` | int | `365` | Entfernt Items, die älter als diese Anzahl an Tagen sind. |
| `ABSOLUTE_MAX_AGE_DAYS` | int | `540` | Harte Obergrenze für das Alter von Items. |
| `ENDS_AT_GRACE_MINUTES` | int | `10` | Kulanzfenster (Minuten), in dem Meldungen nach `ends_at` noch gezeigt werden. |
| `PROVIDER_TIMEOUT` | int | `25` | Timeout (Sekunden) für Provider-Aufrufe. |
| `STATE_PATH` | str | `"data/first_seen.json"` | Speicherort der `first_seen`-Daten (muss unter `data/` liegen). |
| `STATE_RETENTION_DAYS` | int | `60` | Aufbewahrungsdauer der `first_seen`-Daten. |
| `WL_ENABLE` | bool (`"1"`/`"0"`) | `"1"` | Provider „Wiener Linien“ aktivieren/deaktivieren. |
| `OEBB_ENABLE` | bool (`"1"`/`"0"`) | `"1"` | Provider „ÖBB“ aktivieren/deaktivieren. |
| `VOR_ENABLE` | bool (`"1"`/`"0"`) | `"1"` | Provider „VOR/VAO“ aktivieren/deaktivieren. |

### Wiener Linien (`src/providers/wl_fetch.py`)

| Variable | Typ | Standardwert | Beschreibung |
| --- | --- | --- | --- |
| `WL_RSS_URL` | str | `"https://www.wienerlinien.at/ogd_realtime"` | Basis-URL für den OGD-Endpunkt der Wiener Linien. |

### ÖBB (`src/providers/oebb.py`)

| Variable | Typ | Standardwert | Beschreibung |
| --- | --- | --- | --- |
| `OEBB_RSS_URL` | str | `"https://fahrplan.oebb.at/bin/help.exe/dnl?protocol=https:&tpl=rss_WI_oebb&"` | RSS-Quelle der ÖBB (kann über Secret überschrieben werden). |
| `OEBB_ONLY_VIENNA` | bool (`"1"`/`"0"` oder `"true"`/`"false"`, case-insens) | `"0"` | Nur Meldungen mit Endpunkten in Wien behalten. |

### VOR / VAO (`src/providers/vor.py`)

| Variable | Typ | Standardwert | Beschreibung |
| --- | --- | --- | --- |
| `VOR_ACCESS_ID` / `VAO_ACCESS_ID` | str | – | API-Zugangsschlüssel. Leere Werte werden ignoriert; ohne Wert bleibt der Provider inaktiv. |
| `VOR_STATION_IDS` | Liste (kommagetrennt) | – | Stations-IDs für Abfragen. Ohne Angabe bleibt der Provider inaktiv. |
| `VOR_BASE` | str | `"https://routenplaner.verkehrsauskunft.at/vao/restproxy"` | Basis-URL der VAO-API. |
| `VOR_VERSION` | str | `"v1.11.0"` | API-Version. |
| `VOR_BOARD_DURATION_MIN` | int | `60` | Zeitraum (Minuten) für die DepartureBoard-Abfrage. |
| `VOR_HTTP_TIMEOUT` | int | `15` | Timeout (Sekunden) für HTTP-Anfragen. |
| `VOR_MAX_STATIONS_PER_RUN` | int | `2` | Anzahl der Stations-IDs pro Durchlauf. |
| `VOR_ROTATION_INTERVAL_SEC` | int | `1800` | Zeitraum (Sekunden) für Round-Robin der Stationsauswahl. |
| `VOR_ALLOW_BUS` | bool (`"1"`/`"0"`) | `"0"` | Auch Buslinien berücksichtigen. |
| `VOR_BUS_INCLUDE_REGEX` | Regex | `"(?:\\b[2-9]\\d{2,4}\\b)"` | Muster für zusätzliche Buslinien. |
| `VOR_BUS_EXCLUDE_REGEX` | Regex | `"^(?:N?\\d{1,2}[A-Z]?)$"` | Muster zum Ausschließen von Buslinien. |

**Hinweis:** Standardmäßig werden pro Durchlauf höchstens zwei Stations-IDs abgefragt
(`VOR_MAX_STATIONS_PER_RUN = 2`), um API-Limits einzuhalten und Requests besser zu
verteilen.

## License

Dieses Projekt steht unter der [MIT License](LICENSE).
