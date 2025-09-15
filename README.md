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
(`bst_id`, `bst_code`, `name`, `in_vienna`, `pendler`). Die Daten stammen aus dem
Datensatz „[Verzeichnis der Verkehrsstationen](https://data.oebb.at/de/datensaetze~verzeichnis-der-verkehrsstationen~)“
auf dem ÖBB-Open-Data-Portal (Excel-Datei „Verzeichnis der Verkehrsstationen.xlsx“)
und stehen unter [CC BY 3.0 AT](https://creativecommons.org/licenses/by/3.0/at/).
Die empfohlene Namensnennung lautet laut Portal „Datenquelle: ÖBB-Infrastruktur AG“.

Zusätzlich sind Wiener-Linien-Haltestellen enthalten. Die Quelldateien
(`wienerlinien-ogd-haltestellen.csv` und `wienerlinien-ogd-haltepunkte.csv`)
basieren auf dem OGD-Angebot der Stadt Wien (Lizenz
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)) und werden in
`stations.json` mit `source = "wl"` markiert. Die Einträge enthalten pro Station die
DIVA-ID, alle bekannten StopIDs sowie die jeweiligen WGS84-Koordinaten.

### Weitere Datengrundlagen

Das Stationsverzeichnis lässt sich mit zusätzlichen ÖBB-Geodaten anreichern, die
ebenfalls regelmäßig aktualisiert werden:

- **ÖBB Fahrplandaten (GTFS)**: Kompletter Fahrplanexport mit Linien-, Fahrt- und
  Halteinformationen (`stop.txt`, `routes.txt`, …). Die ZIP-Datei steht im
  Open-Data-Portal unter
  `https://data.oebb.at/de/datensaetze~fahrplandaten-gtfs~` bereit und fällt unter
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Attribution laut
  Portal: „Datenquelle: ÖBB-Personenverkehr AG“.
- **ÖBB Streckendaten Personenverkehr**: Geometriedaten der von ÖBB PV bedienten
  Streckenabschnitte (Shape/GeoJSON), abrufbar über
  `https://data.oebb.at/de/datensaetze~streckendaten-personenverkehr~` (Lizenz
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), Namensnennung
  „ÖBB-Infrastruktur AG“).
- **ÖBB GeoNetz**: Topologisch aufbereitete Gleisabschnitte mit Kilometrierung
  (`https://data.oebb.at/de/datensaetze~geonetz~`), veröffentlicht unter
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) mit demselben
  Attributionshinweis.

Die Datensätze sind nicht im Repository enthalten. Für lokale Experimente können
sie in `data/` abgelegt werden (z. B. `data/gtfs/`, `data/strecken.geojson`,
`data/geonetz/`).

### Weitere Datenquellen

Darüber hinaus stehen zahlreiche offene Verwaltungs- und Fachdaten zur Verfügung,
die sich mit dem Stationsverzeichnis oder den Feed-Ergebnissen kombinieren lassen:

- **[data.wien.gv.at](https://data.wien.gv.at/)** – Open-Government-Data-Portal der
  Stadt Wien mit zahlreichen Infrastruktur-, Verkehrs- und Sensordaten. Lizenz:
  Creative Commons Namensnennung 4.0 International (CC BY 4.0).
- **[digitales.wien.gv.at](https://digitales.wien.gv.at/)** – Überblick über
  Digitalisierungs- und Smart-City-Projekte der Stadt, inklusive Datenzugängen und
  APIs; Veröffentlichungen folgen in der Regel der Wiener OGD-Lizenz
  (CC BY 4.0).
- **[mobilitaetsdaten.gv.at](https://www.mobilitaetsdaten.gv.at/)** – Nationale
  Mobilitätsdatenplattform des BMK mit Verkehrszählungen, Echtzeit- und
  Planungsdaten. Die Datensätze sind überwiegend unter CC BY 4.0 oder der
  Datenlizenz Österreich – Namensnennung 2.0 verfügbar.
- **[geoportal.inspire.gv.at](https://geoportal.inspire.gv.at/)** – Zugangspunkt zu
  INSPIRE-konformen Geodaten und OGC-Diensten (z. B. Verkehrsflächen,
  Schutzgebiete), meist unter der Datenlizenz Österreich – Namensnennung 2.0 oder
  spezifischen Fachlizenzen.
- **[bmk.gv.at](https://www.bmk.gv.at/)** – Fachinformationen, Studien und
  Verkehrsdaten des Klimaschutzministeriums; offene Publikationen stehen häufig
  unter CC BY 4.0 oder ausgewiesenen Sonderlizenzen.
- **[statistik.at](https://www.statistik.at/)** – Statistik Austria mit
  Bevölkerungs-, Pendler- und Wirtschaftskennzahlen. Die offenen Datensätze
  („Open Data Österreich“) werden in der Regel unter CC BY 4.0 angeboten.
- **[umweltbundesamt.at](https://www.umweltbundesamt.at/)** – Umwelt- und
  Emissionsdaten, Luftgütemessungen sowie Lärmkarten. Der offene Datenbereich
  (data.umweltbundesamt.at) nutzt vorwiegend CC BY 4.0 oder DL-AT-2.0.
- **[viennaairport.com](https://www.viennaairport.com/)** – Betriebsinformationen
  und Verkehrsdaten des Flughafens Wien; die Inhalte unterliegen den
  Nutzungsbedingungen des Flughafens und sind meist nur mit Quellenangabe für
  redaktionelle Zwecke freigegeben.

Solche Daten lassen sich nutzen, um den Feed mit zusätzlichen Kontexten zu
bereichern: Luftqualitäts- oder Lärmmesswerte können bei Bau- und
Verkehrsmaßnahmen die Auswirkungen auf Anrainer:innen illustrieren, und
Flughafen-Betriebsdaten helfen dabei, ÖPNV-Störungen mit Flugbewegungen oder
Reisendenströmen zu korrelieren. Flächen- und Sensordaten aus den genannten
Portalen erleichtern zudem die räumliche Verknüpfung der Stationsinformationen mit
weiteren Infrastrukturen (z. B. Park&Ride, Radwege, Umweltzonen) und ermöglichen
Auswertungen zu Pendlerströmen oder multimodalen Umsteigepunkten.

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

### Wiener Linien ergänzen

```bash
python scripts/update_wl_stations.py --verbose
```

Die CSV-Dateien der Wiener Linien werden nicht automatisch heruntergeladen. Nach
dem Aktualisieren der offiziellen OGD-Exporte müssen beide Dateien in `data/`
abgelegt werden (`wienerlinien-ogd-haltestellen.csv` und
`wienerlinien-ogd-haltepunkte.csv`). Das Skript liest beide CSVs ein, verknüpft
StopIDs und DIVA, berechnet einheitliche Namen und ergänzt die Einträge in
`stations.json`. Bereits vorhandene WL-Einträge (`"source": "wl"`) werden dabei ersetzt.

Für Auswertungen in Kombination mit GTFS- oder Geodaten lohnt es sich, die
entpackten GTFS-Dateien (z. B. aus dem ÖBB- oder WL-Export) parallel in
`data/gtfs/` abzulegen. Die vom Skript erzeugten Felder `wl_diva` und
`wl_stops[].stop_id` lassen sich direkt mit den `stop_id`/`stop_code`-Spalten der
GTFS-Dateien verknüpfen, und die WGS84-Koordinaten aus den CSVs erleichtern das
Matching mit Streckendaten oder dem GeoNetz.

### Pendlerstationen

Die Datei `data/pendler_bst_ids.json` enthält eine manuell gepflegte Liste an
BST-IDs für Pendlerstationen. Änderungen an der Auswahl (z. B. neue oder
wegfallende Stationen) müssen von Hand in dieser Datei nachgezogen werden, damit
das Aktualisierungsskript die entsprechenden Einträge in `data/stations.json`
über das Feld `pendler` markieren kann.

### Richtlinien für Änderungen an Aktualisierungsskripten

- Änderungen an `scripts/update_station_directory.py` und
  `scripts/update_wl_stations.py` sollten rückwärtskompatibel bleiben. Neue
  Optionen stets mit sinnvollen Standardwerten versehen und in der README
  dokumentieren.
- Wenn sich die Struktur von `stations.json` oder den WL-Einträgen ändert,
  müssen die begleitenden Tests in `tests/` sowie die GitHub Actions angepasst
  werden.
- Zusätzliche Datenquellen oder Lizenzen unbedingt im Abschnitt
  „Stationsverzeichnis“ nachziehen und Quellen/Attributionen ergänzen.
- Nach Codeänderungen beide Skripte lokal gegen die aktuellen Rohdaten laufen
  lassen und das Ergebnis per `python -m pytest` absichern, bevor Änderungen
  committed werden.

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
