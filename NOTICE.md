# Lizenz- und Quellenhinweise (NOTICE)

Die [MIT-Lizenz](LICENSE) gilt **für den Quellcode und die eigene Dokumentation**
dieses Repositorys. Sie gilt **nicht** für fremde Inhalte, die das Projekt abruft,
weiterverarbeitet oder als Datei mitführt. Für diese Inhalte gelten die Lizenzen
und Nutzungsbedingungen der jeweiligen Anbieter; die Rechte bleiben bei ihnen.

Entschieden von Michael am 16.10.2026: Der Code bleibt MIT, weil Städte,
Signage-Betreiber und andere Projekte ihn ohne Hürden übernehmen sollen. Die
Meldungstexte und Datensätze gehören den Quellen und werden deshalb nicht unter
MIT weiterlizenziert, sondern mit ihrer Herkunft ausgewiesen.

## Generierte Feeds (`docs/feed.xml`, `docs/feed.en.xml`)

Die Meldungstexte stammen von den Anbietern. Der englische Feed ist eine
maschinelle Übersetzung dieser Texte und ändert nichts an ihrer Herkunft. Beide
Feeds nennen die Quellen im `<copyright>`-Element des Kanals, und seit
17.10.2026 nennt jedes Item seine Quelle im Element `<source>`.

Die Texte erscheinen **bearbeitet**: Titel und Beschreibungen werden gekürzt und
vereinheitlicht, der englische Feed ist maschinell übersetzt. CC BY 4.0
(Abschnitt 3(a)(1)) verlangt deshalb neben der Quelle den Hinweis auf die
Bearbeitung und einen Link zur Lizenz. Beides steht im `<copyright>`-Element
beider Feeds und in der Fußzeile der Webseite (DE und EN), zusammen mit dem
vollständigen Link auf diese Datei.

| Quelle | Inhalt im Feed | Lizenz / Bedingungen | Quellenangabe |
| --- | --- | --- | --- |
| Wiener Linien (OGD-Echtzeitdaten, `wienerlinien.at/ogd_realtime`) | Störungs- und Baustellenmeldungen | CC BY 4.0 laut [Wiener Linien Open Data](https://www.wienerlinien.at/open-data) („unter Angabe der Datenherkunft“) | „Datenquelle: Wiener Linien“ |
| Stadt Wien (OGD, `data.wien.gv.at`, Layer `ogdwien:BAUSTELLEN…OGD`) | Baustellen mit Öffi-Bezug | CC BY 4.0 | „Datenquelle: Stadt Wien – data.wien.gv.at“ |
| ÖBB (RSS-Meldungen von `fahrplan.oebb.at`) | Bahnmeldungen mit Wien-Bezug | **keine offene Lizenz bekannt**; es gelten die Nutzungsbedingungen der ÖBB | „© ÖBB“ |
| VOR/VAO ReST API (Abfahrtsdaten) | die Meldung „S-Bahn Stammstrecke Verspätungen“, wenn der Monitor eine Verspätungsepisode erkennt; ihr Text („Durchschnittliche Verspätung von … min in Richtung …“) ist eine eigene Auswertung, kein Text von VOR/VAO | VAO-„Start“-Vertrag der Verkehrsauskunft Österreich VAO GmbH (siehe „Statistik“ unten) | „Stammstrecken-Daten: Grundlage ist der VAO-„Start“-Zugang der Verkehrsauskunft Österreich VAO GmbH.“ im `<copyright>` beider Feeds; `<source>` VOR/VAO am Item |
| ÖBB-Infrastruktur GeoNetz (`data/oebb_geonetz_stops.json`) | Koordinaten der ÖBB-Bahnhöfe in `<georss:point>`/`<georss:line>` | [CC BY 3.0 AT](https://creativecommons.org/licenses/by/3.0/at/deed.de) | „Datenquelle: [ÖBB-Infrastruktur AG](https://infrastruktur.oebb.at/de/)“ |
| Wiener Linien OGD Haltestellen (`data/wienerlinien-ogd-haltestellen.csv`), wo deren Koordinate außerhalb des WL-Netzes liegt die GTFS-Haltestellen der Wiener Linien (`data/wienerlinien-gtfs-stops.csv`) | Koordinaten verlegter WL-Haltestellen in `<georss:point>` | CC BY 4.0 | „Datenquelle: Wiener Linien“ |
| Stadt Wien (OGD, Baustellen-Layer) | Koordinate der Baustelle in `<georss:point>` | CC BY 4.0 | „Datenquelle: Stadt Wien – data.wien.gv.at“ |

Die GeoRSS-Koordinaten kommen nur aus diesen drei offenen Datensätzen
(`src.utils.stations.open_data_coordinate`, Entscheidung von Michael vom
16.10.2026), nie aus den zusammengeführten Koordinaten des Stationsverzeichnisses
(HAFAS). Details: `docs/architecture.md` §1, „Geodaten im Feed“.

### ÖBB-Inhalte

Für die ÖBB-Meldungstexte und die HAFAS-Abfragen (Koordinaten, EVA-Nummern,
`data/oebb_station_lines.json`) gibt es keine offene Lizenz; die allgemeinen
Nutzungsbedingungen von oebb.at verlangen für das Vervielfältigen eine
schriftliche Genehmigung. Michael hat die ÖBB am 16.10.2026 über den Kontakt
von data.oebb.at um diese Genehmigung gebeten. Bis zur Antwort bleiben die
Inhalte im Feed. Entschieden von Michael am 16.10.2026: Lehnt die ÖBB ab,
werden die ÖBB-Inhalte aus dem Projekt entfernt, weil er die ÖBB als
Eigentümerin der Daten sieht.

## Stationsverzeichnis und weitere Dateien unter `data/`

`data/stations.json` führt pro Eintrag ein Feld `source`, das die Herkunft der
Werte nennt. Die Einträge mischen mehrere Quellen:

| Quelle | Dateien / Einsatz | Lizenz / Bedingungen | Quellenangabe |
| --- | --- | --- | --- |
| Wiener Linien OGD | `data/wienerlinien-ogd-*.csv`, Stationen mit `source` `wl` | CC BY 4.0 | „Datenquelle: Wiener Linien“ |
| Wiener Linien, Fahrplandaten GTFS Wien (`stops.txt`) | `data/wienerlinien-gtfs-stops.csv`; Koordinate einer WL-Station, deren OGD-Haltepunkte alle außerhalb des WL-Netzes liegen (seit 25.10.2026) | CC BY 4.0 | „Datenquelle: Wiener Linien“ |
| ÖBB-Infrastruktur, Verzeichnis der Verkehrsstationen | `data/oebb-verkehrsstationen.xlsx`, `source` `oebb` | [CC BY 3.0 AT](https://creativecommons.org/licenses/by/3.0/at/deed.de) | „Datenquelle: [ÖBB-Infrastruktur AG](https://infrastruktur.oebb.at/de/)“ |
| ÖBB-Infrastruktur GeoNetz | `data/oebb_geonetz_stops.json`, `source` `oebb_geonetz` | [CC BY 3.0 AT](https://creativecommons.org/licenses/by/3.0/at/deed.de) | „Datenquelle: [ÖBB-Infrastruktur AG](https://infrastruktur.oebb.at/de/)“ |
| VOR Verkehrsverbund Ost-Region (Auskunft AnachB) | `data/vor-haltestellen.csv`, `data/vor-haltestellen.mapping.json`, `data/gtfs/stops.txt`, `source` `vor`, Feld `vor_id` | **ungeklärt**: Haltestellen-IDs und -Namen aus dem Auskunftssystem der AnachB-Webapp des VOR (`anachb.vor.at`), keine offene Lizenz bekannt; Koordinaten seit 17.10.2026 entfernt (siehe unten, „VOR-Haltestellendaten“) | „Haltestellendaten: VOR“ |
| Stadt Wien, Stadtgrenze (MA 41) | `data/LANDESGRENZEOGD.json` | CC BY 4.0 | „Datenquelle: Stadt Wien – data.wien.gv.at“ |
| OpenStreetMap (Overpass) | **kein Eintrag**. Die 150 früheren OSM-Koordinaten sind seit 17.10.2026 durch ÖBB-GeoNetz oder die WL-OGD-Haltestelle ersetzt, und der Stationslauf hat keine OSM-Stufe mehr: OSM-Werte stehen unter der [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), deren Weitergabepflicht bei gemischten Einträgen das ganze Verzeichnis erfasst hätte. Ältere Fassungen von `data/stations.json` in der Git-Historie enthalten noch OSM-Werte („© [OpenStreetMap-Mitwirkende](https://www.openstreetmap.org/copyright)“, ODbL 1.0) | – | – |
| HAFAS (ÖBB Scotty) | Koordinaten und EVA-Nummern mit `source` `hafas`, `data/oebb_station_lines.json`; die Zugangskennung in `data/hafas_profile.json` stammt aus dem ISC-lizenzierten Projekt [`public-transport/hafas-client`](https://github.com/public-transport/hafas-client) | **ungeklärt**: öffentlich erreichbares Auskunftssystem der ÖBB, keine offene Lizenz bekannt | „Fahrplandaten: ÖBB“ |
| Google Places | **kein Eintrag**. Die 18 früheren Google-Koordinaten sind seit 16.10.2026 durch ÖBB-GeoNetz (Bahnhöfe mit EVA-Nummer) oder die WL-OGD-Haltestelle ersetzt, und die Google-Stufe von `scripts/update_station_directory.py` ist am selben Tag entfernt worden; es kommen keine neuen hinzu | – | – |
| Eigene Pflege (Override-Koordinaten stammen aus den offenen Datensätzen oben; die zwei nach OpenStreetMap und Google Maps bestimmten WL-Koordinaten sind seit 25.10.2026 durch die GTFS-Haltestellen ersetzt) | `source` `manual`, `data/stations_overrides.json`, `data/stations_metadata.json`, `data/pendler_bst_ids.json`, `data/pendler_candidates.json`, `data/planned_station_lines.json`, `data/quarantine.json`, `data/stations_last_run.json`, `data/vor_request_count.json` | MIT wie der Code | – |

Die Lizenzangaben sind am 17.10.2026 gegen die Originalseiten geprüft
(Datensatzseiten auf `data.gv.at` und `data.oebb.at`, Wiener Linien Open
Data); dieselbe Tabelle mit Dateidetails steht unter
„Datenquellen und Lizenzen“ in [`docs/development.md`](docs/development.md).

### VOR-Haltestellendaten

`data/vor-haltestellen.csv` (VOR-Haltestellen-ID und Name) ist kein offener
Datensatz. Das inzwischen gelöschte Skript `fetch_vor_haltestellen.py` hat die
Werte bis 11.05.2026 beim Auskunftssystem der AnachB-Webapp des VOR
(`anachb.vor.at/hamm/gate`, mit der Zugangskennung aus deren
Webapp-Konfiguration) abgefragt; seither ist die Datei eingefroren. Bis
17.10.2026 nannte diese Datei dafür fälschlich CC BY 4.0; eine Lizenz des VOR
für diese Daten ist nicht bekannt.

Michael hat am 17.10.2026 entschieden, die Koordinaten aus dieser Quelle
nicht mehr zu führen, weil für sie keine Nutzungsrechte geklärt sind:

- `data/vor-haltestellen.csv` enthält nur noch ID und Name, die
  `data/vor-haltestellen.mapping.json` keine Koordinaten mehr, und der daraus
  abgeleitete Auszug `data/gtfs/stops.txt` (157 der 167 Zeilen mit
  VOR-Haltestellen-ID) hat leere `stop_lat`/`stop_lon`.
- Die 101 Stationen in `data/stations.json`, deren Koordinate aus dieser
  Quelle stammte, tragen jetzt die Koordinate des ÖBB-Infrastruktur-GeoNetz
  (CC BY 3.0 AT, oben); ihre `source` nennt `oebb_geonetz`. Keine Station
  wechselte dabei zwischen Wien und Umland.
- Was bleibt, sind Haltestellen-IDs und Namen (`vor_id`, Aliase), die der
  Stationsabgleich braucht. Der Stammstrecken-Monitor fragt die VAO-API mit
  Michaels eigenem Zugang ab (Statistik oben).

Der Feed zeigt keine dieser Koordinaten: GeoRSS-Punkte kommen nur aus den
drei offenen Datensätzen oben.

## Rohantworten und Betriebszustand (`data/raw/`, Zustandsdateien)

| Dateien | Inhalt | Lizenz / Bedingungen |
| --- | --- | --- |
| `data/raw/wl/*` | letzte gute Antworten der Wiener-Linien-Echtzeitdaten und die verworfenen Meldungen | CC BY 4.0, „Datenquelle: Wiener Linien“ |
| `data/raw/baustellen/*`, `data/samples/baustellen_sample.geojson` | Baustellen-Layer der Stadt Wien (ohne Geometrie bis auf die erste Position) | CC BY 4.0, „Datenquelle: Stadt Wien – data.wien.gv.at“ |
| `data/raw/oebb/*` | **alle** Items des ÖBB-RSS, auch die ohne Wien-Bezug | wie die ÖBB-Meldungstexte (oben, „ÖBB-Inhalte“): keine offene Lizenz, gehört zu den ÖBB-Inhalten |
| `data/first_seen.json`, `data/feed_watch.json`, `data/wl_resolved_tickers.json`, `data/wl_plausibility_anomalies.json`, `data/feed_line_anomalies.json` | Zustand des Feed-Builds mit Titeln und Texten der Meldungen (auch der englischen Übersetzung) | wie die Feeds oben; eigene Felder MIT |

Wer `data/` als Ganzes weiterverwendet, übernimmt diese Bedingungen je Datei.

## Statistik (`data/stats/`, `docs/statistik.md`, Website-Dashboard)

Die Störungsstatistik zählt die Feed-Meldungen; es gelten die Angaben zum Feed.
Die Stammstrecken-Statistik beruht auf Abfragen der VOR/VAO ReST API über den
kostenlosen VAO-„Start“-Zugang der Verkehrsauskunft Österreich VAO GmbH (bis zu
100 Abfragen pro Tag). Der Vertrag erlaubt den produktiven Einsatz und das
Einbetten der Abfrageergebnisse in den eigenen Dienst, räumt aber keine Rechte an
den Daten selbst ein (Punkt 1.3). Veröffentlicht werden deshalb nur eigene
Auswertungen (Verspätungen, Ausfälle), keine Rohantworten. Punkt 5.3 verlangt im
Dienst und in den Projektdarstellungen den deutlichen Hinweis, dass der
VAO-„Start“-Zugang die Grundlage ist. Michael hat am 17.10.2026 entschieden, ihn
überall zu führen: im `<copyright>` beider Feeds, in der Fußzeile der Webseite
(DE und EN), auf `docs/statistik.md` (unter „Stammstrecke“ und „Ausfälle“),
im Dashboard an diesen beiden Abschnitten („Quelle: VOR/VAO“), im README und
hier; jede andere Quelle behält ihren eigenen Hinweis. Endet der Vertrag,
müssen diese Hinweise wieder entfernt werden (Punkt 7.7). Der Vertragstext selbst
liegt nicht im Repository.

## Bilder der Webseite (`docs/assets/`)

`train.png` (der fahrende Zug, mit `train.webp`, `train-small.webp`) und
`footer-bg.jpg` (Hintergrund der Fußzeile, mit `footer-bg.webp`) sind laut
Michael KI-generiert. Die ÖBB-Marken, die die Bilder zeigten
(Logo, Schriftzug „cityjet“), sind seit 17.10.2026 entfernt, weil die
Nutzungsbedingungen von oebb.at auch für Markennamen eine Genehmigung
verlangen. Die übrigen Grafiken (`site-favicon.svg`, `og-default.svg`, das
Logo im Seitenkopf) sind eigene Arbeiten. Das Bild jeder Feed-Meldung,
`feed-item-raufaser.jpg` (Raufaser-Wand, 1920 × 1080), berechnet
`scripts/make_feed_item_image.py` ohne Foto oder fremde Vorlage; das frühere
weiße `feed-item.png` ist ebenfalls eigene Arbeit.

Das Projekt ist **inoffiziell** und steht in keiner Verbindung zu Wiener
Linien, ÖBB, Stadt Wien oder VOR/VAO; maßgeblich sind die verlinkten
Originalmeldungen. Das steht auch am Anfang des `<copyright>` beider Feeds,
in der Fußzeile der Webseite und im README.

## Wetter auf der Webseite (`docs/site.html`)

Das Dashboard zeigt im Kopf die aktuelle Temperatur am Wien Hauptbahnhof. Der
Browser des Besuchers holt sie direkt bei der Open-Meteo-API
(`api.open-meteo.com`, Modell GeoSphere Austria AROME); das Projekt speichert
oder verteilt diese Daten nicht. Open-Meteo stellt die API-Daten unter
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) bereit und verlangt
einen Link neben jeder Stelle, an der die Daten erscheinen
([Lizenzseite](https://open-meteo.com/en/licence)). Das Wetter-Widget ist
deshalb selbst ein Link auf open-meteo.com („Wetterdaten von Open-Meteo.com“
im Tooltip und für Screenreader), und die Fußzeile nennt die Quelle unter
„Datenquellen“. Die kostenlose API ist nur für nicht-kommerzielle Nutzung
freigegeben; das Dashboard ist werbefrei und ohne Abo.

## Abhängigkeiten

Die Python-Abhängigkeiten in `requirements.txt` stehen unter ihren eigenen,
durchweg freizügigen Lizenzen (Stand 17.10.2026, gelesen auf PyPI: requests,
transformers, sentencepiece Apache-2.0; python-dateutil BSD/Apache-2.0;
defusedxml PSF; openpyxl MIT; dnspython ISC). PyTorch (`torch`, von der
Build-Pipeline als CPU-Paket installiert) steht unter BSD-3-Clause, das Paket
enthält mitgelieferte Teile unter Apache-2.0, BSD-2-Clause, BSL-1.0 und MIT.
Sie werden installiert, nicht im Repository mitverteilt.

Die englischen Übersetzungen erzeugt OPUS-MT (`Helsinki-NLP/opus-mt-de-en`,
Jörg Tiedemann und Language Technology Research Group, Universität Helsinki).
Die [Modellkarte auf Hugging Face](https://huggingface.co/Helsinki-NLP/opus-mt-de-en)
nennt Apache-2.0; die ursprünglichen Modellgewichte gibt das
[OPUS-MT-Projekt](https://github.com/Helsinki-NLP/OPUS-MT-train) unter
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) heraus; diese
Namensnennung folgt deshalb CC BY 4.0. Das Modell wird zur
Laufzeit geladen und nicht im Repository verteilt. Die
Dokumentationsseiten nutzen das Jekyll-Theme Primer (MIT) und laden
`anchor-js` (MIT) von cdnjs.

---

Dieser Hinweis ist eine Zusammenstellung des Projekts, keine Rechtsberatung.
Maßgeblich sind die Bedingungen der Anbieter.
