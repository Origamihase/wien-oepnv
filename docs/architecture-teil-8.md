[Architektur-Karte — Wien ÖPNV Feed](architecture.md), Teil 8.

## 5. Stationsverzeichnis-Anreicherung (GeoNetz → HAFAS)

Stationskoordinaten und -Metadaten werden über eine geschichtete
Anreicherungspipeline befüllt, die von
`scripts/update_station_directory.py` orchestriert wird. Eine Station
behält ihre bisherige Koordinate (Koordinaten-Trägheit). Fehlt ihr eine,
bekommt sie zuerst den GeoNetz-Punkt ihrer `bst_id`
(`_fill_coordinates_from_geonetz`, ÖBB-Infrastruktur, CC BY 3.0 AT);
HAFAS (ÖBB Scotty) fragt der Lauf nur noch für den Rest. Eine Station,
die auch HAFAS nicht auflöst, bleibt ohne Koordinate; der Lauf nennt sie
in einer Warnung.

> **Entscheidung 2026-10-17 (Michael): keine OpenStreetMap-Stufe mehr.**
> Bis dahin war OpenStreetMap (Overpass) die erste Stufe des Stationslaufs
> und im WL-Lauf der Schiedsrichter, wenn WL und HAFAS sich bei einer
> gemeinsamen Station widersprachen. OSM-Werte stehen unter der ODbL 1.0;
> je Station mit WL-, GeoNetz- und HAFAS-Werten in einer Datei gemischt,
> wird `data/stations.json` eine abgeleitete Datenbank, die als Ganzes
> unter der ODbL stehen müsste. Das geht nicht, weil die Datei auch Daten
> ohne geklärte Weitergaberechte enthält. Auf die Frage „OSM-Koordinaten
> durch GeoNetz/WL ersetzen und den wöchentlichen OSM-Lauf keine
> Koordinaten mehr schreiben lassen?“ antwortete er „Ja, bitte“. Alle 150
> Einträge mit dem Token `osm` haben seitdem die Koordinate aus GeoNetz
> oder der WL-OGD-Liste (bei WL-Stationen die WL-Koordinate, die der
> Abgleich ohne Schiedsrichter behält); 93 lagen schon unter 1 m daneben,
> 56 unter 200 m. Die eine große Abweichung war ein Fehler der OSM-Stufe:
> `Siebenhirten` (`bst_id` 1371, bei Mistelbach) trug über den Namen die
> Koordinate des gleichnamigen Ortsteils in Liesing. Deshalb füllt
> `_fill_coordinates_from_geonetz` nur über die `bst_id`, nie über den
> Namen. Derselbe Namensfehler steckte in der Wien-Grenze: `in_vienna`
> prüfte die Koordinate, die der Lauf über den Namen fand, und das war für
> Siebenhirten die Liesinger WL-Haltestelle. Seitdem prüft
> `_annotate_station_flags` eine Station mit GeoNetz-Punkt ihrer `bst_id`
> an diesem Punkt, und die WL-Hochstufung zu `pendler` gilt nur, wenn der
> geprüfte Ort der über den Namen gefundene ist. Siebenhirten liegt weder in
> Wien noch im Pendlergürtel und ist aus dem Verzeichnis gegangen (im Nachbau
> ohne Folge für den Feed). Weil die Grenze damit `Kaiserebersdorf` (1059)
> als Wien erkennt, für das schon der Ersatzeintrag `Wien Kaiserebersdorf`
> (900105) steht, und das ÖBB-Excel `Handelskai` (1586) neben
> `Wien Handelskai` (779) führt, behält `_drop_duplicate_eva` je EVA-Nummer
> nur einen Eintrag, den schon vorhandenen. Entfernt sind `_enrich_with_osm`, der Schalter
> `--osm-enrich`/`WIEN_OEPNV_OSM_ENRICH`, der OSM-Schiedsrichter in
> `scripts/update_wl_stations.py` (ein Widerspruch WL/HAFAS behält WL) und
> der Overpass-Vorabtest in `update-stations.yml`. `src/places/osm_client.py`
> und `scripts/check_overpass_status.py` bleiben mit ihren Tests im
> Repository, ohne Aufruf im Produktivlauf. Der Feed war nie betroffen:
> GeoRSS nimmt nur GeoNetz, WL-OGD und Stadt Wien (§1); der Nachbau von
> 198 Feed-Ständen (13.–17.10.2026) mit altem und neuem Verzeichnis war
> identisch, ein Stationslauf mit dem neuen Code änderte außer Siebenhirten
> kein `in_vienna` und kein `pendler`.

> **Falsche WL-Koordinaten (seit 2026-10-25):** Die Regel „nur offen
> lizenzierte Koordinaten“ gilt auch für die Korrekturschicht
> `data/stations_overrides.json`. Drei ihrer Einträge setzten die
> Koordinate der Haltestellen Leopoldine-Padaurek-Straße, Halblehenweg und
> Wassermanngasse (DIVA 60201954 bis 60201956), die die WL-OGD-Liste seit
> Mai im Waldviertel führt; zwei dieser Werte waren nach OpenStreetMap
> bzw. Google Maps bestimmt. Sie sind entfernt. Stattdessen nimmt
> `scripts/update_wl_stations.py` für jede DIVA, deren OGD-Haltepunkte alle
> außerhalb des WL-Netzes liegen, dieselbe Haltestelle aus den
> GTFS-Fahrplandaten der Wiener Linien (`data/wienerlinien-gtfs-stops.csv`,
> CC BY 4.0, wöchentlich geladen). Das deckt jeden künftigen Fall dieser
> Art ohne Handarbeit ab. Im Nachbau des Stationslaufs mit altem und neuem
> Code änderten sich nur diese drei Einträge, um 5 bis 19 m, kein
> `in_vienna`, kein `pendler`, keine Quarantäne.
>
> **Tote Stationsquellen fallen auf (seit 2026-10-25):** Scheitert ein
> Abruf, rechnet der Stationslauf mit der letzten guten Kopie unter
> `data/` weiter und bleibt grün. Jede gute Antwort (200 oder 304) stempelt
> deshalb `checked_at` in die `.cache.json`-Datei neben der Kopie, und der
> Health check („Stationsquellen“, `scripts/health_check.py`) wird rot,
> wenn ein Stempel älter als 15 Tage ist, also zwei Wochenläufe in Folge
> ohne Antwort blieben. Grund ist die Regel des Betreibers, dass eine
> Quelle, die nicht mehr läuft, ihm auffallen muss (16.10.2026); eine
> Warnung im Protokoll reicht nicht.

> **Entscheidung 2026-10-16 (Michael): keine Google-Places-Stufe mehr.**
> Bis dahin war Google Places die dritte Stufe für die Restmenge. Das
> Verzeichnis soll nur Koordinaten führen, deren Herkunft und
> Bedingungen bekannt sind (`NOTICE.md`); die 18 Google-Koordinaten
> wurden in #2025 durch ÖBB-GeoNetz und WL-OGD ersetzt. Der Schlüssel
> erreichte zudem seit 2026-05-11 keinen Workflow mehr, die Stufe lief
> also längst still ins Leere. Entfernt sind der Client
> (`src/places/client.py`, `quota.py`, `tiling.py`, `diagnostics.py`),
> die Skripte `fetch_google_places_stations.py` und
> `verify_google_places_access.py`, `data/places_quota.json`, die
> Quota-Prüfung `--check places` in `scripts/preflight_quota_check.py`
> und `places.googleapis.com` aus `PROXY_TRUSTED_HOSTS`. Die
> Datenklasse `Place` liegt jetzt in `src/places/place.py`.

> **Hinweis zur GeoNetz-Anreicherung:** Orthogonal zur Koordinaten-Kaskade
> läuft `_enrich_with_geonetz`. Es liest die gepinnte
> `data/oebb_geonetz_stops.json` (eine kompakte Stops-Projektion, die
> `scripts/extract_oebb_geonetz_stops.py` aus dem 23 MiB großen
> ÖBB-Infrastruktur-`GeoNetz_*.zip` extrahiert) und hängt **Identifier-
> Metadaten** an — EVA-Nummer und IFOPT-ID, markiert über den Source-Token
> `oebb_geonetz`. Das ist mit ~390 Stationen die zweithäufigste
> Source-Klasse im Verzeichnis, **liefert aber keine Koordinaten** und ist
> daher keine eigene Tier-Stufe.

```mermaid
flowchart LR
    A[ÖBB-Verzeichnis<br/>(Excel)] --> B[Stationsliste<br/>mit bisherigen Koordinaten]
    B --> H[Stationen ohne Koordinaten?]
    H -- ja --> G[GeoNetz-Punkt der bst_id<br/>(_fill_coordinates_from_geonetz)]
    G --> N[Rest noch ohne Koordinaten?]
    N -- ja --> K[HAFAS Mgate<br/>(src/places/hafas_client.py)]
    K --> L[HAFAS CircuitBreaker<br/>5 Fails / 5 Min Cool-off]
    L --> M[Rest noch ohne Koordinaten?]
    M -- ja --> I[Warnung mit Stationsnamen<br/>(Station bleibt ohne Koordinate)]
    M -- nein --> J[stations.json]
    N -- nein --> J
    H -- nein --> J
    I --> J
```

**Warum HAFAS der Level-2-Fallback ist:****Warum HAFAS der Level-2-Fallback ist:**

- **Betreiber-authoritative Koordinaten und EVA-Nummern.** HAFAS ist
  die hauseigene Routing-Backbone der ÖBB. Eine `LocMatch`-Query
  liefert den kanonischen Stationsrecord zusammen mit der EVA-Nummer
  (`extId`, z. B. `"8100353"` für Wien Hauptbahnhof), die das Projekt
  in jeder HAFAS-aufgelösten Station als Top-Level-Feld
  `hafas_extId` persistiert. Die Antwort-Koordinaten sind in
  Mikrograd codiert (`x=16377778` → `16.377778°`) und werden von
  `src/places/hafas_client.py:_extract_first_location` zu
  WGS84-Floats skaliert.
- **Kein Tages-/Monats-Request-Budget.** Anders als der
  VAO-ReST-Endpoint (gedeckelt auf 100 Req/Tag, reserviert für den
  Stammstrecken-Monitor — siehe §7) hat die HAFAS-Mgate-API kein
  publiziertes per-Konsumenten-Limit, das die Cron-Pipeline
  routinemäßig reißen würde.
- **Selbstheilende Credential-Rotation.** Die ÖBB rotiert das
  Mgate-`salt`/`ver`/`aid`-Tripel ohne Vorankündigung. Das
  Begleit-Skript `scripts/sync_hafas_profile.py` läuft in
  `update-stations.yml` **vor** der Anreicherung, extrahiert die
  aktuellen Werte aus dem Open-Source-Profil
  `public-transport/hafas-client` (OEBB-Profil:
  `p/oebb/index.js` + `p/oebb/base.js`) und persistiert sie atomar
  nach `data/hafas_profile.json`. Ein rotiertes Upstream-Profil
  fließt damit automatisch in den nächsten Cron-Tick ein — das
  zwischengespeicherte Profil wird bei jedem Lauf aus der kanonischen
  Quelle neu aufgebaut.
- **Schlanke Integration.** Es wird **keine** externe
  HAFAS-Client-Bibliothek verwendet. Der Mgate-`LocMatch`-Body wird
  direkt konstruiert, durch
  `json.dumps(payload, separators=(',', ':'))` serialisiert
  (sodass die On-the-Wire-Bytes byte-genau dem MAC-Input entsprechen),
  optional mit `MD5(body + salt)` signiert, sobald das Upstream-Profil
  ein Salt trägt (aktuell nicht), und durch dieselbe
  `request_safe`-Security-State-Machine (§2) abgesetzt wie jeder
  andere Provider. Ein dedizierter modulweiter
  `CircuitBreaker("hafas_enrichment", failure_threshold=5,
  recovery_timeout=300.0)` kühlt das Upstream nach einer Fehlerserie
  fünf Minuten lang — eine ÖBB-Störung kann die Cron-Pipeline damit
  nicht in Selbst-DDoS treiben. `CircuitBreakerOpen` wird am
  öffentlichen Einstiegspunkt zu `None` konvertiert: ein
  HAFAS-Schluckauf crasht das Skript nicht; die betroffenen Stationen
  behalten ihre bisherige Koordinate oder bleiben ohne.
  Den Transport (Profil, Umschlag, MAC, `request_safe`, JSON-Hooks)
  kapselt seit 2026-09-25 `post_mgate(svcReqL, max_bytes=…)`, damit
  weitere Mgate-Methoden (z. B. `StationBoard` für die Linien je
  ÖBB-Bahnhof, Stufe 2 der Linien-Prüfung aus A.14) denselben Weg
  nehmen; der Circuit Breaker bleibt Sache des jeweiligen Aufrufers.
  Die Antwortform dafür erhebt das manuelle Diagnoseskript
  `scripts/probe_hafas_lines.py` im Workflow `probe-hafas-lines.yml`
  (nur `workflow_dispatch`, höchstens sechs Anfragen, schreibt nichts).
  Erster Lauf (2026-09-25): `LocMatch` liefert je Station nur die
  Produktklassen (`pCls`, Bitmaske laut `hafas-client`: 32 = S-Bahn,
  16 = R/REX, 256 = U-Bahn, 512 = Straßenbahn), keine Linien. Eine
  `StationBoard` mit `getPasslist` lehnt ÖBB mit `err=PARSE` ab.

**ÖBB-Linien je Bahnhof (Stufe 2 der Linien-Prüfung, seit 2026-09-25).**
`scripts/update_oebb_station_lines.py` läuft wöchentlich in
`update-stations.yml` (eigener Schritt, `continue-on-error`, 25 Minuten) und
schreibt ausschließlich `data/oebb_station_lines.json`:

- Umfang: die ÖBB-Bahnhöfe (`bst_id`) in Wien und im Pendlerraum, derzeit
  162. Die HAFAS-Stations-ID ermittelt `LocMatch` mit bis zu acht
  Kandidaten (`pick_rail_location`): Es zählt der nächstgelegene Kandidat,
  dessen Produktklassen (`pCls`) R/REX oder S-Bahn enthalten (16 | 32) und
  der höchstens 800 m entfernt liegt. Nur diese Züge tragen Liniennummern.
  Irgendeine Bahnklasse reichte nicht: Sie ließ am 26.09. das Busterminal
  am Flughafen Wien (pCls 1090, „CAT by bus“) vor dem Bahnhof gewinnen.
  Findet der volle Name keinen Bahn-Halt, folgt eine zweite Anfrage mit
  der ÖBB-Abkürzung (`short_name`: „Wien Hauptbahnhof“ → „Wien Hbf“);
  „Wien Hauptbahnhof“ lieferte nur Meidling, Floridsdorf, Hütteldorf und
  den Flughafen. Der Ort bleibt in der Anfrage, und der 800-m-Umkreis um
  die eigenen Koordinaten schließt jeden anderen Hauptbahnhof aus
  (St. Pölten: 56 km). Ein Treffer der zweiten Anfrage muss außerdem „Hbf“
  im Namen tragen: Quartier Belvedere liegt 526 m vom Hauptbahnhof.
  Gespeichert werden ID, HAFAS-Name, Klassen und Abstand.
  Zuordnungen nach älteren Regeln werden einmal neu ermittelt, ein
  Bahnhof ohne Linie bei jedem Lauf.
- Derselbe Ort, mehrere Halte: HAFAS führt manche Bahnhöfe als mehrere
  Halte mit denselben Koordinaten, und nur einer davon trägt die
  Abfahrten („Simmering (Wien)“ und „Wien Simmering Bahnhof (U)“,
  „Himberg b.Wien Bahnhof“ und „Himberg b.Wien“; gewählt war jeweils der
  mit leeren Tafeln). Zeigen die Tafeln des gewählten Halts keine Linie,
  werden bis zu zwei weitere R/REX- oder S-Bahn-Halte im Umkreis von 50 m
  um ihn abgefragt (`same_place_stops`); der erste mit Linien wird
  gespeichert. Nicht weiter: Quartier Belvedere liegt 526 m vom
  Hauptbahnhof.
- Diagnose im Log: jede Zuordnung mit Name, Klassen und Abstand; ohne
  Bahn-Halt alle `LocMatch`-Kandidaten (`describe_candidates`); bei Tafeln
  ohne Linie je Zeitfenster Abfahrten, Produkte und deren `catOut`/`line`/
  `lineId` (`board_summary`), dazu die Kandidaten.
- Je Bahnhof und Lauf vier `StationBoard`-Anfragen: nächster Dienstag und
  der Dienstag fünf Wochen später, jeweils 06:00–09:00 und 15:00–18:00,
  nur Bahnklassen (Filter 4159), `maxJny` 400. Ohne `maxJny` lieferte
  HAFAS im Messlauf nur rund 50 Abfahrten.
- Linien stammen aus `prodCtx.lineId` (`at:obb:vor|S45:` → `S45`), sonst
  aus `catOut` + `line`. Fernzüge (RJ, IC, WESTbahn) tragen nur
  Zugnummern und zählen nicht; Schienenersatzverkehr (`catOut` Bus bzw.
  „Schienenersatzverkehr“) zählt nie als Linie.
- Jede Linie trägt das Datum, an dem sie zuletzt gesehen wurde, und fällt
  erst nach drei Jahren ohne Nachweis heraus (bis 2026-09-26: 56 Tage).
  Lange Sperren sind die Regel, eine Linie soll sie überdauern. Fällt eine
  Linie wirklich weg, bleibt sie so lange bekannt; Stufe 3 übersieht dann
  höchstens einen Befund, einen falschen erzeugt das nicht. Ein Bahnhof
  ohne Antwort behält seine Linien unverändert. Nach fünf Fehlschlägen in
  Folge endet der Lauf und schreibt, was er hat. Zwischen den Anfragen
  liegen 0,5 Sekunden.
- **Grenze:** Die Datei beschreibt, was fährt oder in den letzten drei
  Jahren gefahren ist, nicht das geplante Netz. Eine Sperre, die begann,
  bevor Stufe 2 die Linie je gesehen hat, versteckt sie ganz. Das betrifft
  die Sperren, die vor dem ersten Lauf (September 2026) begannen. Belegt:
  keine S80 in Hütteldorf und Speising
  (Schienenersatzverkehr auf der Verbindungsbahn bis Ende 2027); keine
  Linie in Wien Mitte-Landstraße, Rennweg und Quartier Belvedere, weil die
  Stammstrecke zwischen Praterstern und Hauptbahnhof/St. Marx vom
  07.09.2026 bis Ende Oktober 2027 gesperrt ist (ÖBB-Folder „Sperren S-Bahn
  Wien Stammstrecke 2026/27“, SNNB-Anhang 2.5.1; in Wien Mitte fährt „CAT
  by bus“); keine Linie in Himberg, dessen Bahnhof umgebaut wird
  (ÖBB-Rahmenplan: Inbetriebnahme 2026). Die Prüfung in Stufe 3
  liest ein Fehlen deshalb nie als Beweis (siehe dort, „Unbekannt ist nicht
  falsch“).

**Linien-Prüfung des Feeds (Stufe 3, seit 2026-09-26).**
`scripts/check_feed_lines.py` läuft in `update-cycle.yml` nach der
Statistik und vor dem Veröffentlichen (`continue-on-error`, 1 Minute; es
braucht rund eine Sekunde) und prüft `docs/feed.xml`. Nur Bericht: Das
Skript ändert nie eine Meldung. Es schreibt nur seine Sammlung
`data/feed_line_anomalies.json`, die im selben Commit wie der Feed landet.

- Geprüft werden Meldungen mit Linien-Präfix (`7A/N65/N66: …`). Erstens:
  Gibt es die Linie? Bekannt sind die Linien der Wiener Linien
  (`data/wienerlinien-ogd-linien.csv`, alle `wl_lines`), die HAFAS-Linien
  aus `data/oebb_station_lines.json` und die gepflegten Linien aus
  `data/planned_station_lines.json`. Eine Ersatzlinie `<Linie>E` zählt,
  wenn `<Linie>` bekannt ist. Zweitens: Bedient eine der Linien die
  Bahnhöfe, die Titel oder Beschreibung nennen?
- Nur ÖBB-Bahnhöfe werden geprüft. Wiener-Linien-Meldungen nennen oft
  vorübergehende Haltestellen einer anderen Linie („37A: Busse halten
  Pasettistraße … (bei Linie 5A)“). Die Linien eines Bahnhofs sind seine
  HAFAS-Linien, seine gepflegten Linien, seine `wl_lines` und die
  `wl_lines` der Wiener-Linien-Haltestellen im Umkreis von 200 m. Eine
  Haltestelle in diesem Umkreis gilt als Teil des Bahnhofs; weiter nicht,
  „Hauptbahnhof Ost“ liegt 354 m von Quartier Belvedere und näher an ihm
  als am Hauptbahnhof.
- Namen werden aus Stationsnamen und Aliasen gesucht, der längste zuerst
  („Meidling Hauptstraße“ ist nicht der Bahnhof Meidling). Kürzel unter
  vier Zeichen, reine Nummern, allgemeine Wörter („Hauptbahnhof“) und
  mehrdeutige Namen zählen nicht, ebenso ein Name direkt nach „Richtung“:
  Er nennt das Ziel, keinen Halt.
- **Unbekannt ist nicht falsch** (seit 2026-09-26, Betreiberentscheidung).
  Eine Zuglinie, also eine Linie, die HAFAS oder die gepflegte Liste an
  irgendeinem Bahnhof kennt, wird an einem Bahnhof nur bewertet, wenn dort
  je ein Zug gesehen wurde. Ein Bahnhof ohne jede bekannte Linie wird gar
  nicht bewertet. Das Ergebnis heißt dann „not judged“ und ist kein
  Befund. Die Regel gilt für alle Bahnhöfe gleich und braucht keine
  Einträge: Himberg, Wien Mitte-Landstraße, Rennweg, Quartier Belvedere
  und Speising fallen darunter, solange HAFAS dort keinen Zug zeigt.
  Wiener-Linien-Linien werden dort weiter bewertet. Der Preis: Eine
  falsche Zuglinie an einem solchen Bahnhof fällt nicht auf, ebenso an
  Bahnhöfen ohne Bahnverkehr (Karlsplatz, Schottentor, Stephansplatz,
  Siebenhirten).
- Die gepflegte Liste ist nur noch Starthilfe: Sie nennt Linien, die
  Stufe 2 an einem Bahnhof nie gesehen hat, an dem andere Züge fahren. Das
  betrifft nur Sperren, die vor dem ersten Lauf begannen. Einziger Eintrag:
  S80 in Hütteldorf bis 11.12.2027 (Verbindungsbahn; ÖBB-Folder „Sperren
  S-Bahn Wien Stammstrecke 2026/27“, SNNB-Anhang 2.5.1, B-92487). Die
  Einträge für Stammstrecke, Speising und Himberg sind seit 2026-09-26
  entfallen, ihre Quellen stehen im Audit vom 26.09. (Update 16:40).
  Ein abgelaufener Eintrag zählt nicht mehr. Eine Warnung im Log und in
  der Sammlung gibt es nur, solange HAFAS seine Linien am Bahnhof noch
  nicht zeigt; fährt die Linie wieder, genügt eine Info-Zeile.
- Befunde lauten „not confirmed“, nie „falsch“, und stehen im Log des
  Schritts. Rückschau über 678 Stände des Feeds (482 verschiedene
  Meldungen, 432 mit Präfix): drei Befunde, alle berechtigt. „1: Bhf.
  Hütteldorf ÖBB-Ersatzbus für 80“ (der Anlass der Prüfung), „71/72: Dies
  ist eine Testmeldung“ (keine Linie 72) und „66A: Demonstration Betrieb
  ab Quartier Belvedere“. Mit den Regeln vom 26.09. ergibt die Rückschau
  über 685 Stände dieselben drei Befunde und kein „not judged“.
- **Sammlung zur späteren Auswertung:** `data/feed_line_anomalies.json`
  hält jede Auffälligkeit als Datensatz: `kind` (`unknown_line`,
  `not_confirmed`, `not_judged`, `planned_expired`), `subject` (Linie oder
  Bahnhof), `title` (Titel der Meldung), `detail`, `first_seen`,
  `last_seen` und `days_seen` (Tage in Europe/Vienna). Dieselbe
  Auffälligkeit zählt je Tag einmal, weitere Zyklen desselben Tages ändern
  die Datei nicht. Datensätze bleiben, auch wenn die Meldung verschwindet;
  über 1000 fallen die am längsten nicht mehr gesehenen weg. Titel und
  Text sind auf 300 Zeichen gekürzt. `--no-record` prüft nur, etwa für
  Rückschauen über alte Feed-Stände.

**`STATE_PATH` hat nur eine Bedeutung (seit 2026-10-10).** Der
Feed-Build (`src/feed/config.py`) liest die Variable als **Datei**
(`data/first_seen.json`). Bis 2026-10-10 las der Google-Places-Zähler sie
ersatzweise als **Verzeichnis**. Betreiberentscheidung vom 2026-10-10:
Jeder Zweck bekommt eine eigene Variable, kein Name hat zwei Bedeutungen.
`tests/test_state_path_env_reader.py` wacht darüber, dass außer
`src/feed/config.py` kein Modul `STATE_PATH` liest.

**Netzwerk-Resilienz-Schichten:**

1. **HAFAS-Profil-Sync** (`scripts/sync_hafas_profile.py`) läuft im
   selben Workflow vor `update_all_stations.py` und aktualisiert
   `data/hafas_profile.json` über `request_safe` aus dem Upstream-
   Community-Profil. Persistiert wird über
   `src.utils.files.atomic_write`, sodass ein abgebrochener Download
   keine korrupte JSON-Sidecar-Datei hinterlassen kann.
2. **`urllib3`-JitterRetry** (`session_with_retries`) behandelt
   transiente 5xx und Connection-Resets innerhalb eines einzelnen
   HAFAS-Aufrufs.
3. **`CircuitBreaker`** — `src/places/hafas_client.py:_BREAKER`
   öffnet nach fünf aufeinanderfolgenden Fehlern und bleibt fünf
   Minuten offen; er konvertiert `CircuitBreakerOpen` zu einem soft-fail
   `None` pro Call, sodass die Cron-Pipeline weiterläuft.
4. **`request_safe`** umhüllt jeden HAFAS-Call in der
   Security-State-Machine (§2): SSRF, Redirect, Content-Type,
   Slowloris, Payload-Cap.
5. **Test-Isolation** — `tests/conftest.py` registriert eine
   autouse-Fixture `reset_circuit_breakers`, die über
   `CircuitBreaker.iter_instances()` (ein prozessweites
   `weakref.WeakSet`, das jeder `CircuitBreaker.__init__` automatisch
   befüllt) läuft und vor und nach jedem Test auf jedem registrierten
   Breaker `.reset()` aufruft. Damit werden **alle** Breaker — inkl.
   des HAFAS-Breakers (`hafas_enrichment`) und der Stammstrecke-Breaker
   — ohne manuelles Inventar erfasst; ein neu hinzugefügter Breaker ist
   automatisch isoliert, ein gesondertes `breaker.reset()` pro Test ist
   nicht nötig.

Relevante CLI-Flags / Env-Vars:

| Flag / Env | Default | Zweck |
|---|---|---|
| `WIEN_OEPNV_MANUAL_ENRICH` | `1` | `0` überspringt die Anreicherung der manuellen Einträge (Testlauf des Orchestrators) |
| `WIEN_OEPNV_AT_RECONCILE` | `1` | `0` überspringt den Koordinaten-Abgleich WL/HAFAS im WL-Lauf |

HAFAS selbst hat keinen CLI-Schalter — die Stufe ist aktiv, sobald die
Profil-Sidecar-Datei existiert. Wer HAFAS zu Debug-Zwecken überspringen
will, löscht schlicht `data/hafas_profile.json`; der Client kurz-
schließt dann für jede Station auf `None`, hinterlässt eine einzige
Log-Zeile `HAFAS enrichment disabled: …`.

Weiter in [Teil 9](architecture-teil-9.md).
