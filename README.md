# Wien ÖPNV Feed

Störungen und Einschränkungen für den Großraum Wien aus offiziellen Quellen.

## Erweiterungen

Der RSS-Feed deklariert den Namespace `ext` (`xmlns:ext="https://wien-oepnv.example/schema"`) für zusätzliche Metadaten:

- `ext:first_seen`: Zeitpunkt, wann eine Meldung erstmals im Feed aufgetaucht ist.
- `ext:starts_at`: Beginn der Störung bzw. Maßnahme.
- `ext:ends_at`: Ende der Störung bzw. Maßnahme.

## Entwicklung/Tests lokal

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python -u src/build_feed.py  # erzeugt docs/feed.xml
```

Der erzeugte Feed liegt unter `docs/feed.xml`.

## Umgebungsvariablen

### Allgemein (`src/build_feed.py`)

| Variable | Typ | Standardwert | Beschreibung |
| --- | --- | --- | --- |
| `LOG_LEVEL` | str | `"INFO"` | Log-Level für Ausgaben. |
| `OUT_PATH` | str | `"docs/feed.xml"` | Zielpfad für den erzeugten Feed. |
| `FEED_TITLE` | str | `"ÖPNV Störungen Wien & Umgebung"` | Titel des RSS-Feeds. |
| `FEED_LINK` | str | `"https://github.com/Origamihase/wien-oepnv"` | Link zur Projektseite. |
| `FEED_DESC` | str | `"Aktive Störungen/Baustellen/Einschränkungen aus offiziellen Quellen"` | Beschreibung des RSS-Feeds. |
| `DESCRIPTION_CHAR_LIMIT` | int | `170` | Maximale Länge der Item-Beschreibung. |
| `FRESH_PUBDATE_WINDOW_MIN` | int | `5` | Zeitfenster (Minuten), in dem Meldungen ohne Datum als „frisch“ gelten und mit aktuellem `pubDate` versehen werden. |
| `MAX_ITEMS` | int | `60` | Maximale Anzahl an Items im Feed. |
| `MAX_ITEM_AGE_DAYS` | int | `45` | Entfernt Items, die älter als diese Anzahl an Tagen sind. |
| `ABSOLUTE_MAX_AGE_DAYS` | int | `365` | Harte Obergrenze für das Alter von Items. |
| `STATE_PATH` | str | `"data/first_seen.json"` | Speicherort der `first_seen`-Daten. |
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

### VOR / VAO (`src/providers/vor.py`)

| Variable | Typ | Standardwert | Beschreibung |
| --- | --- | --- | --- |
| `VOR_ACCESS_ID` / `VAO_ACCESS_ID` | str | – | API-Zugangsschlüssel. Ohne Wert bleibt der Provider inaktiv. |
| `VOR_STATION_IDS` | Liste (kommagetrennt) | – | Stations-IDs für Abfragen. Ohne Angabe bleibt der Provider inaktiv. |
| `VOR_BASE` | str | `"https://routenplaner.verkehrsauskunft.at/vao/restproxy"` | Basis-URL der VAO-API. |
| `VOR_VERSION` | str | `"v1.3"` | API-Version. |
| `VOR_BOARD_DURATION_MIN` | int | `60` | Zeitraum (Minuten) für die DepartureBoard-Abfrage. |
| `VOR_HTTP_TIMEOUT` | int | `15` | Timeout (Sekunden) für HTTP-Anfragen. |
| `VOR_MAX_STATIONS_PER_RUN` | int | `2` | Anzahl der Stations-IDs pro Durchlauf. |
| `VOR_ROTATION_INTERVAL_SEC` | int | `1800` | Zeitraum (Sekunden) für Round-Robin der Stationsauswahl. |
| `VOR_ALLOW_BUS` | bool (`"1"`/`"0"`) | `"0"` | Auch Buslinien berücksichtigen. |
| `VOR_BUS_INCLUDE_REGEX` | Regex | `"(?:\\b[2-9]\\d{2,4}\\b)"` | Muster für zusätzliche Buslinien. |
| `VOR_BUS_EXCLUDE_REGEX` | Regex | `"^(?:N?\\d{1,2}[A-Z]?)$"` | Muster zum Ausschließen von Buslinien. |
