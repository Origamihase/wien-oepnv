# VOR ReST API – Dokumentation (Version 2025-05-22)

Diese Dokumentation bündelt die wichtigsten Fakten aus dem offiziellen Handbuch zur VAO ReST API und verweist auf weiterführende Detailkapitel. Die vollständige Referenz liegt als PDF im Repository: [Handbuch_VAO_ReST_API_2025-08-11.pdf](docs/Handbuch_VAO_ReST_API_2025-08-11.pdf).

Die `<description>`-Elemente des Feeds bestehen aus zwei Zeilen: Der erste Satz fasst den Inhalt zusammen, die zweite Zeile nennt den Zeitraum (z. B. „Seit 05.01.2024“, „Ab 20.01.2024“, „Am 10.01.2024“ oder „01.06.2024 – 03.06.2024“). Fehlt ein sinnvolles Enddatum oder liegt es nicht nach dem Beginn, erscheint abhängig vom Datum automatisch „Seit <Datum>“ (Vergangenheit) bzw. „Ab <Datum>“ (zukünftig). Für zukünftige eintägige Intervalle wird „Am <Datum>“ verwendet. Redundante Überschriften wie „Bauarbeiten“ oder das Label „Zeitraum:“ werden automatisch entfernt. Die `<description>`- und `<content:encoded>`-Elemente liefern diese Zeilen mit `<br/>`-Trennzeichen.

## VOR ReST API Dokumentation

Für die VOR/VAO-ReST-API liegt ergänzende Dokumentation in diesem Repository vor. Sie umfasst das offizielle Handbuch sowie daraus abgeleitete Referenz- und How-to-Seiten:

- [Handbuch_VAO_ReST_API_2025-08-11.pdf](docs/Handbuch_VAO_ReST_API_2025-08-11.pdf)
- [docs/reference/](docs/reference/) mit einzelnen Endpunktbeschreibungen
- [docs/how-to/](docs/how-to/) mit Schritt-für-Schritt-Anleitungen
- [docs/examples/](docs/examples/) mit Shell-Snippets (z. B. `version-check.sh`)

Der Schnellstart für API-Aufrufe erfolgt über Umgebungsvariablen (Repository-Secrets) und ist im Abschnitt „Schnellstart“ der API-Dokumentation beschrieben. Alle API-spezifischen Informationen bleiben damit von den Feed-spezifischen Beschreibungen in diesem README getrennt.

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
Beim Aktualisieren gleicht das Skript die Stationsnamen mit dem bestehenden
Verzeichnis ab, nutzt GTFS- sowie Wiener-Linien-Geodaten zur Bestimmung von
`in_vienna` und markiert Haltepunkte außerhalb der Stadtgrenze als
`pendler`. Einträge, die weder in Wien liegen noch zum Pendlergürtel gehören,
werden automatisch entfernt.

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
  Stadt Wien mit zahlreichen Infrastruktur-, Verkehrs- und Sensordaten auf Basis
  der Creative-Commons-Lizenz [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.de).
- **[digitales.wien.gv.at](https://digitales.wien.gv.at/)** – Überblick über
  Digitalisierungs- und Smart-City-Projekte der Stadt, inklusive Datenzugängen und
  APIs; Veröffentlichungen folgen in der Regel der Wiener OGD-Lizenz (CC BY 4.0).
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
  Bevölkerungs-, Pendler- und Wirtschaftskennzahlen. Die offenen Datensätze („Open
  Data Österreich“) werden in der Regel unter CC BY 4.0 angeboten.
- **[umweltbundesamt.at](https://www.umweltbundesamt.at/)** – Umwelt- und
  Emissionsdaten, Luftgütemessungen sowie Lärmkarten. Der offene Datenbereich
  (data.umweltbundesamt.at) nutzt vorwiegend CC BY 4.0 oder DL-AT-2.0.
- **[viennaairport.com](https://www.viennaairport.com/)** – Betriebsinformationen
  und Verkehrsdaten des Flughafens Wien; die Inhalte unterliegen den
  Nutzungsbedingungen des Flughafens und sind meist nur mit Quellenangabe für
  redaktionelle Zwecke freigegeben.

Hinweise zur Kombination mit dem ÖPNV-Feed:

- **Umwelt- und Sensordaten (data.wien.gv.at, umweltbundesamt.at)** bieten
  ergänzende Werte wie Luftgüte, Temperatur oder Lärm, die bei Baustellen- und
  Störungsmeldungen zusätzliche Auswirkungen auf Anrainer:innen sichtbar machen.
- **Verkehrs- und Bewegungsdaten (mobilitaetsdaten.gv.at, bmk.gv.at)** helfen, die
  Netzbelastung während Störungen zu quantifizieren und alternative Routen oder
  Kapazitäten zu planen.
- **Geodaten (geoportal.inspire.gv.at, data.wien.gv.at)** erleichtern das Matching
  mit Stationskoordinaten, um betroffene Flächen, Schutzgebiete oder angrenzende
  Infrastrukturen (Park&Ride, Radwege) zu identifizieren.
- **Strukturdaten (statistik.at)** liefern Kontext zu Pendlerströmen und
  Bevölkerungsdichte, wodurch Priorisierungen oder Zielgruppen-Kommunikation
  verbessert werden können.
- **Flughafeninformationen (viennaairport.com)** lassen sich mit S-Bahn- und
  Bus-Störungen verknüpfen, um Auswirkungen auf Flugreisende oder Spitzenzeiten am
  Flughafen zu beurteilen.

### Automatische Aktualisierung

Die GitHub Action [`.github/workflows/update-stations.yml`](.github/workflows/update-stations.yml)
lädt monatlich (Cron `0 0 1 * *`) die aktuelle Excel-Datei und schreibt daraus eine
aktualisierte `data/stations.json`. Dabei werden bestehende Stationsnamen
harmonisiert, die `in_vienna`- und `pendler`-Flags anhand der Geodaten
neu berechnet und nicht relevante Einträge verworfen. Änderungen werden automatisch
in den Hauptzweig committet.

### Stationsverzeichnis komplett aktualisieren

```bash
python scripts/update_all_stations.py --verbose
```

Der Sammelbefehl führt nacheinander `update_station_directory.py`,
`update_vor_stations.py` und `update_wl_stations.py` aus. Damit entsteht das
vollständige `stations.json` in einem Durchlauf – Voraussetzung ist, dass die
benötigten Quelldateien (VOR-Export sowie die Wiener-Linien-CSV-Dateien) wie in
den folgenden Abschnitten beschrieben lokal vorliegen. Die Einzelskripte lassen
sich weiterhin separat starten, um gezielt Teilmengen zu aktualisieren.

### Manuelle Aktualisierung

```bash
python scripts/update_station_directory.py --verbose
```

Das Skript lädt die Excel-Datei herunter, extrahiert die benötigten Spalten und
aktualisiert `data/stations.json`. Über `-v/--verbose` lässt sich eine etwas
ausführlichere Protokollierung aktivieren. Optional können auch Quelle und Ziel
per Argumenten angepasst werden (`--source-url`, `--output`). Neue Pendler:innen-
Haltestellen werden vorab in `data/pendler_bst_ids.json` eingetragen, damit sie
beim Lauf nicht herausgefiltert werden. Liegt zusätzlich eine VOR-Haltestellen-
Liste (`data/vor-haltestellen.csv` oder `--vor-stops`) vor, werden die passenden
`vor_id`-Einträge automatisch den Wiener und Pendler-Stationen zugeordnet. Damit
steht ohne weitere Schritte ein vollständiger Fallback für `VOR_STATION_IDS`
bereit.

### Pendler-Whitelist pflegen

`data/pendler_bst_ids.json` enthält eine einfache Liste der `bst_id`-Werte, die
auch außerhalb der Stadtgrenze im Verzeichnis bleiben sollen (z. B. WL-Endpunkte
oder wichtige Umsteigepunkte). Bei Erweiterungen des Pendlergürtels wird die Liste
ergänzt; anschließend sorgt `update_station_directory.py` automatisch dafür, dass
die markierten Stationen als `pendler = true` ausgegeben werden.

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

### VOR ergänzen

```bash
python scripts/update_vor_stations.py --verbose
```

Die Haltestellendaten des Verkehrsverbund Ost-Region (VOR) stehen nach
Freischaltung im Open-Data-Portal als GTFS- bzw. CSV-Export zur Verfügung
(z. B. das GTFS-`stops.txt` oder die Datei „Haltestellen“) und lassen sich nun
alternativ direkt über die VOR-API abrufen. Für den CSV-Weg wird die Rohdatei
lokal nach `data/vor-haltestellen.csv` kopiert (alternativ lässt sich der Pfad
über `--source` anpassen). Wer die API verwenden möchte, übergibt `--use-api`
und stellt die benötigten Station-IDs entweder über `--station-id` bzw.
`--station-id-file` bereit oder lässt sie weiterhin aus der CSV-Datei
extrahieren. Die Daten sind unter [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
veröffentlicht; laut Portal lautet die empfohlene Namensnennung „Datenquelle:
Verkehrsverbund Ost-Region (VOR) GmbH“.

Das Skript extrahiert Stop-ID, Namen und WGS84-Koordinaten und aktualisiert die
bereits in `stations.json` hinterlegten Einträge um zusätzliche VOR-Metadaten
(z. B. `latitude`, `longitude`, `aliases`). Für Haltestellen, die nicht im
ÖBB-Verzeichnis enthalten sind, werden weiterhin eigene Einträge mit
`"source": "vor"` ergänzt. Damit bleibt das Verzeichnis frei von Dubletten,
liefert aber dennoch vollständige Koordinaten für alle VOR-Stationen.

Für Auswertungen in Kombination mit GTFS- oder Geodaten lohnt es sich, die
entpackten GTFS-Dateien (z. B. aus dem ÖBB- oder WL-Export) parallel in
`data/gtfs/` abzulegen. Die vom Skript erzeugten Felder `wl_diva` und
`wl_stops[].stop_id` lassen sich direkt mit den `stop_id`/`stop_code`-Spalten der
GTFS-Dateien verknüpfen, und die WGS84-Koordinaten aus den CSVs erleichtern das
Matching mit Streckendaten oder dem GeoNetz.

Für automatisierte Tests liegt im Repository eine stark verkleinerte GTFS-Datei
`data/gtfs/stops.txt`, die nur einige Wiener Beispiele enthält. Wer mit dem
vollständigen GTFS-Export arbeiten möchte, lädt das ZIP-Archiv
„Fahrplandaten GTFS“ aus dem ÖBB-Open-Data-Portal herunter (kostenlose Anmeldung
erforderlich) und entpackt die benötigten Dateien nach `data/gtfs/`, zum Beispiel:

```bash
mkdir -p data/gtfs
# heruntergeladenes Archiv ablegen, z. B. data/gtfs/oebb-gtfs.zip
unzip data/gtfs/oebb-gtfs.zip "stops.txt" "routes.txt" "trips.txt" "stop_times.txt" -d data/gtfs
```

Damit stehen die vollständigen Stop-Informationen für lokale Experimente zur
Verfügung.

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
# Zur Laufzeit setzen (Werte stammen aus Repository-Secrets/ENV)
export VOR_ACCESS_ID="${VOR_ACCESS_ID}"
export VOR_BASE_URL="${VOR_BASE_URL}"      # inkl. Versionspfad, z. B. /restproxy/<version>
export VOR_VERSIONS="${VOR_VERSIONS}"      # Endpoint mit Infos zu verfügbaren API-Versionen

# Verfügbare Versionen abfragen (GET)
curl -sS "${VOR_VERSIONS}" \
  -H "Accept: application/json" \
  -H "Authorization: Bearer ${VOR_ACCESS_ID}" || true

# Beispiel: Aufruf eines dokumentierten Endpunkts (Parameter anpassen)
# Platzhalter; exakte Pfade/Parameter NUR verwenden, wenn eindeutig aus der PDF extrahiert
curl -G -sS "${VOR_BASE_URL}/location.name" \
  --data-urlencode "accessId=${VOR_ACCESS_ID}" \
  --data-urlencode "input=Hauptbahnhof" \
  -H "Accept: application/json" || true
```

## Qualitätssicherung

Für lokale Qualitätsprüfungen stehen nun optionale statische Analysen bereit. Nach der Installation der benötigten Pakete –
beispielsweise über `python -m pip install ruff mypy` – lassen sich die Checks direkt im Projektstamm ausführen:

```bash
# Stil- und Konsistenzprüfungen (Ruff)
ruff check

# Typüberprüfung des Anwendungscodes
mypy
```

`ruff` respektiert die in `pyproject.toml` hinterlegte Konfiguration (u. a. maximale Zeilenlänge und ignorierte Verzeichnisse).
`mypy` fokussiert sich in der Grundeinstellung auf den Feed-Builder (`src/build_feed.py`) und blendet übrige Pakete sowie Tests
aus. Beide Werkzeuge liefern so vor Commits schnelle Hinweise auf mögliche Fehler oder Inkonsistenzen.

## Authentifizierung & Sicherheit

- Die VAO ReST API verwendet einen Access Token (`accessId`) zur Authentifizierung. Dieser wird als Query-Parameter übertragen und darf ausschließlich aus sicheren Umgebungsvariablen stammen.
- Secrets (Access-ID, Basis-URL, Versionen-Endpunkt) gehören nicht in Repository-Dateien, Issue-Tracker oder Protokolle. Auf Testsystemen sind sie vor jeder Abfrage per `export` zu setzen.
- Beispielskripte nutzen ausschließlich Umgebungsvariablen und setzen keine Klartextwerte.

## Versionierung

- Die verfügbaren API-Versionen liefert der Endpoint `${VOR_VERSIONS}`. Die Antwort enthält aktive Versionen inkl. Gültigkeitszeitraum (siehe Handbuch Kapitel 3.1).
- Für Requests empfiehlt das Handbuch, den gewünschten Versionspfad (z. B. `/restproxy/v1.11.0`) in `${VOR_BASE_URL}` zu hinterlegen. Änderungen an verfügbaren Versionen sind über `${VOR_VERSIONS}` prüfbar.
- Detailinformationen zu Release-Zyklen und Betriebsdauer finden sich in der PDF (Kapitel 3).

## Referenz & Beispiele

- **Referenz**: [docs/reference/](docs/reference/) – Parameter, Antwortstrukturen und Beispielaufrufe für dokumentierte Endpoints.
- **How-tos**: [docs/how-to/](docs/how-to/) – Schritt-für-Schritt-Anleitungen, z. B. für die Versionsabfrage.
- **Beispiele**: [docs/examples/](docs/examples/) – Shell-Snippets auf Basis von Umgebungsvariablen.

## Weitere Hinweise

- Für zusätzliche Services, Fehlercodes und Sonderfälle siehe das Handbuch (Kapitel 5–20).
- Unklare oder nicht eindeutig bestätigte Angaben sind in dieser Dokumentation als „TBD – siehe PDF“ gekennzeichnet.
