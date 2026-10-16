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
Feeds nennen die Quellen im `<copyright>`-Element des Kanals.

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
| ÖBB-Infrastruktur GeoNetz (`data/oebb_geonetz_stops.json`) | Koordinaten der ÖBB-Bahnhöfe in `<georss:point>`/`<georss:line>` | CC BY 4.0 | „Datenquelle: ÖBB-Infrastruktur AG (data.oebb.at, mobilitaetsdaten.gv.at)“ |
| Wiener Linien OGD Haltestellen (`data/wienerlinien-ogd-haltestellen.csv`) | Koordinaten verlegter WL-Haltestellen in `<georss:point>` | CC BY 4.0 | „Datenquelle: Wiener Linien“ |
| Stadt Wien (OGD, Baustellen-Layer) | Koordinate der Baustelle in `<georss:point>` | CC BY 4.0 | „Datenquelle: Stadt Wien – data.wien.gv.at“ |

Die GeoRSS-Koordinaten kommen nur aus diesen drei offenen Datensätzen
(`src.utils.stations.open_data_coordinate`, Entscheidung von Michael vom
16.10.2026), nie aus den zusammengeführten Koordinaten des Stationsverzeichnisses
(OpenStreetMap, HAFAS, Google). Details: `docs/architecture.md` §1, „Geodaten im Feed“.

## Stationsverzeichnis und weitere Dateien unter `data/`

`data/stations.json` führt pro Eintrag ein Feld `source`, das die Herkunft der
Werte nennt. Die Einträge mischen mehrere Quellen:

| Quelle | Dateien / Einsatz | Lizenz / Bedingungen | Quellenangabe |
| --- | --- | --- | --- |
| Wiener Linien OGD | `data/wienerlinien-ogd-*.csv`, Stationen mit `source` `wl` | CC BY 4.0 | „Datenquelle: Wiener Linien“ |
| ÖBB-Infrastruktur, Verzeichnis der Verkehrsstationen | `data/oebb-verkehrsstationen.xlsx`, `data/gtfs/stops.txt`, `source` `oebb` | CC BY 3.0 AT | „Datenquelle: ÖBB-Infrastruktur AG“ |
| ÖBB-Infrastruktur GeoNetz | `data/oebb_geonetz_stops.json`, `source` `oebb_geonetz` | CC BY 4.0 | „Datenquelle: ÖBB-Infrastruktur AG (data.oebb.at, mobilitaetsdaten.gv.at)“ |
| VOR Verkehrsverbund Ost-Region | `data/vor-haltestellen.csv`, `data/vor-haltestellen.mapping.json`, `source` `vor` | CC BY 4.0 | „Datenquelle: VOR Verkehrsverbund Ost-Region“ |
| Stadt Wien, Stadtgrenze (MA 41) | `data/LANDESGRENZEOGD.json` | CC BY 4.0 | „Datenquelle: Stadt Wien – data.wien.gv.at“ |
| OpenStreetMap (Overpass) | Koordinaten mit `source` `osm` | [ODbL](https://opendatacommons.org/licenses/odbl/) | „© OpenStreetMap-Mitwirkende“ |
| HAFAS (ÖBB Scotty) | Koordinaten und EVA-Nummern mit `source` `hafas`, `data/oebb_station_lines.json` | **ungeklärt**: öffentlich erreichbares Auskunftssystem der ÖBB, keine offene Lizenz bekannt | „Fahrplandaten: ÖBB“ |
| Google Places | **kein Eintrag**. Die 18 früheren Google-Koordinaten sind seit 16.10.2026 durch ÖBB-GeoNetz (Bahnhöfe mit EVA-Nummer) oder die WL-OGD-Haltestelle ersetzt, und die Google-Stufe von `scripts/update_station_directory.py` ist am selben Tag entfernt worden; es kommen keine neuen hinzu | – | – |
| Eigene Pflege | `source` `manual`, Overrides, Mapping-Dateien | MIT wie der Code | – |

Die Lizenzangaben der offenen Datensätze stammen aus der Tabelle
„Datenquellen und Lizenzen“ in [`docs/development.md`](docs/development.md).

## Statistik (`data/stats/`, `docs/statistik.md`, Website-Dashboard)

Die Störungsstatistik zählt die Feed-Meldungen; es gelten die Angaben zum Feed.
Die Stammstrecken-Statistik beruht auf Abfragen der VOR/VAO ReST API; für diese
Antworten gelten die Nutzungsbedingungen von VOR/VAO.

## Abhängigkeiten

Die Python-Abhängigkeiten in `requirements.txt` stehen unter ihren eigenen
Lizenzen. Das Übersetzungsmodell `Helsinki-NLP/opus-mt-de-en` wird zur Laufzeit
geladen und nicht im Repository verteilt; seine Lizenz steht auf der Modellkarte
bei Hugging Face.

---

Dieser Hinweis ist eine Zusammenstellung des Projekts, keine Rechtsberatung.
Maßgeblich sind die Bedingungen der Anbieter.
