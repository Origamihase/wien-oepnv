[Architektur-Karte — Wien ÖPNV Feed](architecture.md), Teil 9.

### 5a. Wiener-Linien-OGD-Merge (die vierte Quelle)

Nachdem die GeoNetz-/HAFAS-Anreicherung die ÖBB-verwurzelte
`stations.json` geschrieben hat, läuft `scripts/update_wl_stations.py`
als eigene Stufe im Wrapper-Orchestrator. Dieser Schritt faltet die
Wiener-Linien-OGD-Echtzeit-Stations- und Stop-Daten in das Verzeichnis,
damit der publizierte Feed auch U-Bahn-, Straßenbahn- und Bus-Stops
kennt, die kein ÖBB-Gegenstück haben.

**Quell-Endpoint** (seit PR #1442): das kanonische
`www.wienerlinien.at/ogd_realtime/doku/ogd/wienerlinien-ogd-{haltestellen,haltepunkte}.csv`.
Der historische Proxy `data.wien.gv.at/csv/` wurde in der 60.
Wien-OGD-Phase (September 2025) ausgemustert; `haltepunkte.csv`
lieferte dort HTTP 404. Die Migration ist in
`scripts/update_wl_stations.py:OGD_HALTESTELLEN_URL` dokumentiert.
Beide Dateien (seit 2026-09-25 zusätzlich `linien.csv` und
`fahrwegverlaeufe.csv`, siehe Schritt 6 unten) werden in jedem Lauf frisch
heruntergeladen (`--download` ist Default-on) und atomar nach
`data/wienerlinien-ogd-*.csv` geschrieben.
Bei Download-Fehlschlag behält die Pipeline den gepinnten lokalen
Snapshot und läuft weiter.

**Einträge bauen** (`build_wl_entries`):

1. Haltepunkte nach ihrer Haltestellen-DIVA gruppieren.
2. Je Gruppe den kanonischen Namen über `_derive_station_label`
   ableiten: Wenn der Haltestellen-`PlatformText` ein generisches,
   verkehrs-typisches Token ist (`Bahnhof`, `Lokalbahn`,
   `Hauptbahnhof`, `Station`, `Halt`, `Bf`, `Hbf`, `Bahn`, `U-Bahn`)
   und die Haltepunkte einen einzigen bereinigten `StopText` ergeben,
   wird der `StopText` verwendet. Andernfalls bleibt der `PlatformText`
   stehen. Diese Regel (PR #1453) verwandelt operator-nutzlose Labels
   wie `Wien Bahnhof (WL)` in informative Labels wie
   `Wien Tribuswinkel - Josefsthal (WL)`, während der häufige Fall
   (`Karlsplatz`, `Stephansplatz`, …) erhalten bleibt.
3. `in_vienna` aus den aggregierten (Mittelwert-)Haltepunkt-Koordinaten
   auflösen, damit das Flag mit den persistierten
   `latitude`/`longitude`-Werten konsistent bleibt (durch
   `test_coordinates_match_in_vienna_flag` nach PR #1449 gepinnt).
4. `pendler = not in_vienna` setzen, um die historische
   WL-Auto-Promote-Heuristik für Stops in Grenzregionen zu spiegeln
   (PR #1443).
5. Den Eintrag mit `wl_diva`, `wl_stops`, `aliases` bauen — ohne
   synthetische `bst_id`/`bst_code` (PR #1446-Redesign).
6. `wl_lines` setzen (seit 2026-09-25): die Linien, die an mindestens
   einem der `wl_stops` halten, in natürlicher Reihenfolge
   (`5`, `13A`, `49`, `D`, `N49`, `U4`). Quelle sind zwei weitere
   Dateien vom selben Endpoint: `wienerlinien-ogd-linien.csv`
   (`LineID` → `LineText`) und `wienerlinien-ogd-fahrwegverlaeufe.csv`
   (je Zeile ein Halt eines Linienwegs: `LineID`, `StopID`); die
   `StopID` ist die der `wl_stops`. Die Liniendaten sind optionale
   Anreicherung: Fehlen die Dateien, bekommt keine Station `wl_lines`,
   der Merge läuft weiter. Stand 2026-09-25: 205 Linien, 4461 von 4582
   Haltepunkten liegen auf einem Linienweg. Die S-Bahn-Linien (`S1` …
   `S80`, `ptTrainS`) stehen zwar in `linien.csv`, haben aber keine
   Fahrwege; `wl_lines` enthält deshalb nie S-Bahn-Linien. Beim Co-Lokations-Merge werden die Linien
   vereinigt; ein ÖBB-Eintrag, in den ein WL-Payload gemergt wird, trägt
   genau die Linien dieses Laufs. Zweck: Grundlage einer
   Plausibilitätsprüfung „hält Linie X bei Y?“ für Störungsmeldungen
   (Audit 2026-09-25, A.14: WL meldete „ÖBB-Ersatzbus für <80“ in
   Hütteldorf unter der Straßenbahnlinie 1).

**Co-lokierter-Haltestellen-Merge** (PR #1451): Ein Post-Build-Pass
`_merge_colocated_duplicates` faltet Gruppen mit identischem
kanonischen Namen **und** Abstand ≤ 150 m zu einem einzigen Eintrag
zusammen. Wiener-Linien-OGD-Echtzeit listet manche physischen Stops
zweimal (Gegenrichtungs-Bahnsteige mit eigener DIVA); der Merge sammelt
alle Haltepunkte unter einem Eintrag mit der lexikografisch kleinsten
DIVA als `wl_diva`. Gruppen, in denen mindestens ein Paar ≥ 150 m
auseinander liegt, bleiben separat — das sind multimodale Stops an
einem Standort (Bus + Tram-Paar) oder generic-PlatformText-Zufälle,
für die der gemeinsame Display-Name semantisch korrekt ist.

**Namens-Eindeutigkeits-Vertrag** (PR #1452): Der Stationsvalidator
erzwingt die kanonische Namens-Eindeutigkeit nicht mehr. `name` ist
ein operator-zugewandtes Display-Label; die strukturelle Eindeutigkeit
lebt in `wl_diva`/`bst_id`/`vor_id`/`bst_code`. Wiener Linien liefert
legitim duplizierte `PlatformText`-Werte in nicht-mergebaren
Multi-DIVA-Gruppen (10 solche Gruppen im Mai-2026-CSV-Snapshot,
darunter `Lokalbahn` × 4 verteilt über 5,6 km und `Bahnhof` × 2 mit
9,4 km Abstand) — der frühere DIVA-Suffix-Workaround
`_disambiguate_duplicate_names` (`Wien Bahnhof (WL 60205022)` usw.)
hat den RSS-Feed zugemüllt und wurde zusammen mit dem Validator-Gate
entfernt.

**Alias-Schlüssel-Kollisionen** (PR #1801, Audit 2026-09-12 Befund 6):
Dieselbe `Lokalbahn`-Gruppe taucht seitdem im Validierungsbericht wieder
auf — als **gemeldeter**, nicht als blockierender Befund, und aus einem
anderen Grund. Der oben entfernte Vertrag betraf die Eindeutigkeit des
kanonischen `name`. Der neue Check `_find_alias_collision_issues` fragt
etwas anderes: ob mehrere Stationen denselben **normalisierten Alias**
beanspruchen und dabei über den Ort uneins sind — verschiedene
`in_vienna`-Urteile oder mehr als 2 km Abstand. Das ist relevant, weil
`_station_lookup` je Schlüssel genau einen Gewinner behält und
`station_info` über `is_in_vienna` mitentscheidet, ob eine ÖBB-Meldung
überhaupt in den Feed kommt.

Die vier Badner-Bahn-Stopps sind heute folgenlos: Alle vier liegen
außerhalb Wiens, stimmen also in `in_vienna` überein — nachgemessen über
alle 44 (Schlüssel, Verlierer, Gewinner)-Tripel des Live-Verzeichnisses.
Gemeldet werden sie, weil das eine Eigenschaft der **Daten** ist und
keine des Codes: Käme ein Lokalbahn-Stopp innerhalb Wiens hinzu, der den
generischen Schlüssel ebenfalls beansprucht, kippte die Auflösung still.
`Wien Inzersdorf Lokalbahn (WL)` liegt in Wien und beansprucht ihn
heute nur deshalb nicht, weil alle seine Aliase `Inzersdorf` tragen.

**Namens-Zuordnungskonflikte** (seit 2026-09-18,
`_find_name_ownership_issues` in `src/utils/station_checks/names.py`):
Tragen zwei Einträge denselben kanonischen Namen, behält `_station_lookup`
den zuerst registrierten, ohne Stärke und Quelle abzuwägen. Gemeldet wird
nur der Fall, in dem dieser Gewinner keine Haltestelle des Namens hat,
ein anderer Eintrag der Gruppe aber schon; dann löst der Name auf den
falschen Ort auf. Auch dieser Befund ist gemeldet, nicht blockierend.

Dieselbe Abhängigkeit gilt für **abgeschnittene Endpunkte**. Die
Routenregeln in `src/providers/oebb.py` beenden einen Endpunkt an
Zeitpräpositionen wie `im`/`am`; ein Ortsname, der eine davon trägt, fiel
dadurch auf sein erstes Wort zurück, und dieses Wort löste über einen
Wiener-Linien-Alias auf. Am 2026-09-22 wurde so aus „Baumgarten im
Bgld-Schattendorf" die Haltestelle „Wien Baumgarten (WL)", und eine
Burgenland-Route stand als `REX 6: Wien Baumgarten (WL) ↔ Ebenfurth` im
Feed. `_with_place_qualifier` hängt den Zusatz wieder an, wenn direkt
danach das Stationssuffix folgt (`… im Bgld-Schattendorf Bahnhof`);
Zeitangaben haben diese Form nie. Die 1751 reinen WL-Einträge des
Verzeichnisses bleiben als ÖBB-Endpunkt grundsätzlich auflösbar — einige
tragen Namen, die auch S-Bahn-Halte sein könnten, ein pauschaler
Ausschluss könnte echte Meldungen verwerfen.

Derselbe Weg führte am 2026-09-13, 09-17 und 09-29 zu **Wien Hauptbahnhof
statt Wien Meidling**. ÖBB schreibt „Wien Meidling Bahnhof (U)“; mit dem
Umsteigezeichen „(U)“ traf die Suche den Alias „Wien Bhf. Meidling U“ der
WL-Haltestelle „Wien Bhf. Meidling (WL)“. `_normalize_endpoint_name` hielt
den Punkt in „Bhf.“ dann für ein Satzende und kürzte auf „Wien Bhf“, das
zum Hauptbahnhof auflöst. Im Feed standen „Wien Hauptbahnhof ↔ Wien
Hauptbahnhof“ und „Wien Hauptbahnhof ↔ Tullnerfeld“. Seither sucht
`_clean_title_keep_places` ohne Umsteigezeichen (`_without_transfer_markers`;
kein Alias des Verzeichnisses endet auf eines), und ein Name, der als
Ganzes auflöst, wird nicht am Punkt gekürzt. Betroffen waren sieben
Wiener Bahnhöfe mit einer Haltestelle „Wien Bhf. X“. Dazu endet die
Beschreibungsroute jetzt vor ÖBBs Störungswortlaut („… Bahnhof (U)
Zugfahrten derzeit nur eingeschränkt möglich“): 11 Endpunkte der
Cache-Historie liefen hinein, lösten nicht auf, und eine Meldung ohne
Route im Titel wäre verworfen worden.

**Schrägstrich statt „an der“.** ÖBB schreibt „Bruck/Leitha“ und
„Tulln/Donau“; das Verzeichnis kennt „Bruck an der Leitha“ und „Tulln an
der Donau“. Eine Route Wien ↔ Bruck/Leitha galt als Wien ↔ unbekannt, und
die strenge Routenprüfung verwarf die Meldung. Der Cache kann solche Fälle
nicht zeigen, weil eine verworfene Meldung nie gespeichert wird. Seit
2026-10-01 versucht `_candidate_values` (`src/utils/stations_lookup.py`) für
„X/Y“ zusätzlich „X an der Y“ und „X am Y“, nach allen anderen Varianten;
ein Name, der wie geschrieben auflöst, behält seinen Bahnhof. Betroffen
waren fünf Pendler-Bahnhöfe: Bruck an der Leitha, Tulln an der Donau,
Brunn am Gebirge, Neusiedl am See und Hadersdorf am Kamp.

**Ortszusatz.** ÖBB hängt manchen Orten Fluss, Region oder Bundesland an:
„Mistelbach/Zaya“, „Wolkersdorf im Weinviertel“, „Traisen NÖ“, „Wolfsberg
in Ktn“. Das Verzeichnis kennt die bloßen Orte. Die Rohdaten des
Update-Laufs zeigten am 2026-10-04 „Wien Leopoldau Bahnhst (U) =>
Mistelbach/Zaya Bahnhof“ (S2, Schienenersatzverkehr) als verworfen; seit
Juli hatte keine ÖBB-Meldung zu Mistelbach oder Wolkersdorf den Cache
erreicht. `station_info` versucht deshalb nach allen anderen Varianten den
Namen ohne diesen Zusatz am Namensende (`_PLACE_QUALIFIER_RE`) und nimmt das
Ergebnis nur, wenn der Bahnhof außerhalb Wiens liegt: ein Ort mit Zusatz
liegt nie in Wien, und „Baumgarten im Burgenland“ darf nicht die
WL-Haltestelle Baumgarten werden. Über alle Namen der ÖBB-Rohdaten und alle
Aliase des Verzeichnisses ändern sich nur die qualifizierten Namen; die 80
ÖBB-Meldungen im Cache seit Juli behalten ihre Entscheidung.

**Zwei Bahnhöfe in einem Endpunkt (seit 2026-11-06).** ÖBB nennt die
Alternativen einer Seite im Endpunkt selbst: „Wien Hbf/Wien St. Marx <=> Wien
Praterstern“ im Titel, „zwischen Wien Hbf bzw. Wien St.Marx und Wien
Praterstern“ im Text (Sperre der Stammstrecke 07.09.2026 bis Oktober 2027). Das
Verzeichnis kennt keinen solchen Doppelnamen; die Strecke galt als Wien ↔
unbekannt, und weil eine Strecke gefunden war, lief die Einzelbahnhof-Prüfung
nicht: Die Meldung fiel zwei Monate als „nicht Wien-relevant“ weg.
`_endpoint_alternatives` (`src/providers/oebb_routes.py`) teilt einen Endpunkt,
der als Ganzes nicht auflöst, in eine Strecke je Teil – „bzw.“ und „oder“
trennen immer, der Schrägstrich nur einen Teil, der als Ganzes nicht auflöst
(„Tulln/Donau bzw. Krems/Donau“ sind zwei Bahnhöfe, „Bruck/Leitha“ bleibt
einer) – und nur, wenn jeder Teil ein Bahnhof der Eisenbahn ist, nicht nur eine
Haltestelle der Wiener Linien oder des VOR: ein überlanger Endpunkt aus den
Alternativ-Texten und zwei Straßenbahnhaltestellen bleiben unbekannt wie
bisher. Die zwei Strecken der Sperre bildeten zudem nach der Form des Graphen
die Kette „Wien Hauptbahnhof ↔ Wien Praterstern ↔ Wien St. Marx“, als führen
Züge über Wien Praterstern. Eine Kette gilt nur noch, wenn jede Zwischenstation
auf dem Weg zwischen ihren Nachbarn liegt (Umweg-Maßstab aus Teil 3 ohne
Linienbedingung, `_chain_is_corridor`; unbekannte Koordinaten lassen die Kette
stehen); sonst steht die Station, an der alle Strecken enden, einmal vorn
(`_try_star_routes`, jetzt ab zwei Strecken): „Wien Praterstern ↔ Wien
Hauptbahnhof / Wien St. Marx“; Strecken ohne gemeinsame Station stehen einzeln
mit „ / “. Das Nachspiel steht im Changelog.

**Ein Ort, zwei Namen (seit 2026-11-08).** Das Verzeichnis führt einen
Bahnhof und die Haltestelle der Wiener Linien davor als zwei Einträge: „Wien
Kaiserebersdorf“ (ÖBB) und „Wien Bhf. Kaiserebersdorf (WL)“. Die ÖBB-Meldung
zu Bauarbeiten zwischen Wien Zentralfriedhof und Wien Kaiserebersdorf
(14.05.2026) stand als „Wien Zentralfriedhof ↔ Wien Bhf. Kaiserebersdorf (WL)“
im Feed; mit dem Bahnhof aus dem Text las der Titel später „Wien
Zentralfriedhof ↔ Wien Bhf. Kaiserebersdorf (WL) / Wien Kaiserebersdorf“.
Dass beide ein Ort sind, steht schon in der Alias-Tabelle des Verzeichnisses:
der Name der Haltestelle ohne „(WL)“ ist ein Alias des Bahnhofs.
`railway_station_of` (`src/utils/stations_lookup.py`) gibt für eine Haltestelle
der Wiener Linien oder des VOR den Bahnhof zurück, auf den ihr Name ohne
Herkunftszusatz auflöst, wenn beide höchstens 500 m auseinander liegen (zwei
Bahnhöfe in Wien liegen mindestens 526 m auseinander). Ein
ÖBB-Titel nennt dann den Bahnhof, denn ÖBB meldet Züge (`_same_place_names` in
`src/providers/oebb_route_title.py`): „Wien Zentralfriedhof ↔ Wien
Kaiserebersdorf“. Eine Strecke von der Haltestelle zu ihrem eigenen Bahnhof
liegt in einem Ort: neben anderen Strecken fällt sie weg, allein nennt der
Titel den Bahnhof einmal. Ein Titel aus dem Cache, der nur die Haltestelle
nennt („Wien Bhf. Meidling (WL)“, Stellwerksstörung 11.09.2026), wird beim
Bauen zum Bahnhof („Wien Meidling“, `railway_station_names`, auch beim
Abruf). Beschreibungen behalten den Wortlaut der Quelle.
Heute sind es sieben Paare („Wien Bhf. Atzgersdorf/Blumental/Hütteldorf/
Kaiserebersdorf/Meidling/Süßenbrunn/Zentralfriedhof (WL)“);
`tests/test_oebb_same_place_endpoints.py` prüft das ganze Verzeichnis nach
jeder Aktualisierung.

Der Name-Eindeutigkeits-Vertrag aus PR #1452 bleibt davon unberührt —
duplizierte `PlatformText`-Werte sind weiterhin legitim und werden
weiterhin nicht erzwungen.

**Resilienz**: derselbe `session_with_retries` +
`fetch_content_safe`-Stack wie die anderen Quellen, plus gepinnter
Snapshot-Fallback. Anders als das ÖBB-Workbook (bis PR #1450) haben
die WL-CSVs schon seit der ersten Form von `_download_ogd_csv` einen
Soft-Fail-Pfad.

---

### Anzeige von Hauptbahnhöfen (seit 2026-11-02)

> **Entscheidung 2026-11-02 (Michael):** „Wenn der Ort länger als sieben
> Zeichen lang ist, wird Hauptbahnhof abgekürzt.“ Es geht dabei auch um
> die Optik: „Wien Hauptbahnhof“ und „Linz Hauptbahnhof“ lesen sich
> schöner, „St. Pölten Hbf“ besser als „St. Pölten Hauptbahnhof“.

Ein Stationsname, der auf „Hauptbahnhof“ oder „Hbf“ endet, erscheint im
Feed mit „Hauptbahnhof“, wenn der Ort davor höchstens sieben Zeichen hat
(Leerzeichen und Punkt mitgezählt), sonst mit „Hbf“: „Wien Hauptbahnhof“,
„Linz Hauptbahnhof“, „Graz Hauptbahnhof“, „Villach Hauptbahnhof“, aber
„Salzburg Hbf“, „St. Pölten Hbf“, „Wiener Neustadt Hbf“. Die Regel steht
in der Anzeigeschicht des Verzeichnisses (`display_name` in
`src/utils/stations.py`, `_main_station_form` in `src/utils/stations_lookup.py`), nicht in
`data/stations.json`: Das Verzeichnis schreibt beide Formen („Linz Hbf“,
„St. Pölten Hauptbahnhof“), und jede Aktualisierung durch
`scripts/update_station_directory.py` darf das weiter tun, die Anzeige
bleibt gleich. `tests/test_main_station_display.py` prüft jeden
Hauptbahnhof des Verzeichnisses gegen die Regel, also auch nach jeder
Aktualisierung in CI. Ein Name, in dem „Hauptbahnhof“ nicht am Ende steht
(„Wien Hauptbahnhof Ost (WL)“), bleibt wie er ist; Beschreibungen behalten
den Wortlaut der Quelle. Die Statistik liest ältere Zeilen der Ledger
(„St. Pölten Hauptbahnhof“) in der heutigen Form (`current_location_label`
in `src/utils/stats.py`), damit ein Ort bei einer Auswertung nach Orten
nicht zweimal zählt. Das Dashboard zeigt heute keine Orte (siehe §6), seine
Ausgabe bleibt daher gleich.

### Haltestellen ohne „S“ und „U“ (seit 2026-11-04)

> **Entscheidung 2026-11-04 (Michael):** „Der Name „Rennweg S“ gefällt mir
> nicht. „Rennweg“ ohne S würde mir besser gefallen. … Gibt es weitere
> Bahnhöfe mit einem S oder einem U? Dies würde mich auch in den Meldungen
> stören.“

Die Wiener Linien hängen einer Haltestelle mit Umstieg zur S-Bahn oder
U-Bahn ein einzelnes „S“, „U“ oder „S U“ an („Rennweg S“, „Schottentor U“,
„Praterstern S U“, einmal davor: „Parlament, U Volkstheater“), und so
stehen die Namen in ihren Meldungen. Der Feed zeigt den Ort ohne diesen
Zusatz, im Titel und im Text, deutsch und englisch: „N71: Rennweg“,
„Fahrtbehinderung in Richtung Floridsdorf“, „zwischen Kagran und
Praterstern“ (`without_stop_markers` in `src/utils/wl_stop_markers.py`).

Die Regel ist strukturell, keine Wortliste: Ein „S“ oder „U“ fällt nur hinter
einem Wort weg, mit dem im WL-Haltestellenverzeichnis ein Name endet
(`data/wienerlinien-ogd-haltepunkte.csv`, Spalte `StopText`, und
`data/wienerlinien-ogd-haltestellen.csv`, Spalte `PlatformText`; beide
aktualisiert der wöchentliche Stationslauf). So gehen auch Namen, die WL im
Text mit Zusatz schreibt, im Verzeichnis aber ohne („Tscherttegasse U“,
„Michelbeuern-AKH U“) oder mit Punkten, die das Verzeichnis weglässt („V.I.C.
U“ für „VIC“, Teil 16, §13), und eine neue Haltestelle kommt mit dem nächsten
Stationslauf ohne Codeänderung dazu. Vor einem Namen fällt ein „U“ nur vor
einem Wort weg, vor dem es im Verzeichnis steht („U Volkstheater“). Ein Zusatz
in Klammern, der nur aus „S“ und „U“ besteht, fällt ganz weg, auch ohne
Verzeichnis: ÖBB schreibt jeden Umstieg so in den Text („Wien Hbf (U)“, „Wien
Leopoldau Bahnhst (U)“; im Titel fiel er schon vorher weg,
`_without_transfer_markers` in `src/providers/oebb_routes.py`), WL selten
(„Simmering (S U)“). Ebenso geht der Zusatz vor einem Namen am Satzende
(„Parlament, U Volkstheater.“) und hinter einem Namen mit angeklebtem
Listenstrich („-Johnstraße U“) (unabhängige Prüfung 2026-11-04). Kein
Haltestellenzusatz und daher unverändert: ein „U“ oder „S“ hinter einem Wort,
das keinen Haltestellennamen beendet („in beiden Richtungen U.“, ein von WL
abgeschnittenes „U6“), ein Buchstabe mit Bindestrich oder Ziffer („S-Bahn“,
„U6“, „S 45“, „Kagran U 26A“), eine Klammer mit mehr als diesen Buchstaben
(„(S1)“) und Tippfehler der Quelle, deren Wort kein Name des Verzeichnisses
beendet („Matzleinsdorfer Plaz S“, „Floridsorf S U“, Entscheidung zu #2023).

Die Regel wirkt nur auf das, was der Leser sieht, ganz am Ende von
`_format_item_content` (`src/feed_build/item_format.py`): Satzgrenzen, die
180-Zeichen-Grenze, der Vergleich von Titel und Text („Haltestelle:“, das
nur den Titel wiederholt), das Zusammenführen von Meldungen, die
Baustellen-Zwillinge und der GeoRSS-Punkt lesen weiter die Namen, wie WL
sie schickt. Ohne Zusatz käme sonst hinter „Donauspital“ eine Satzgrenze,
wo „Donauspital U.“ keine war, und eine Meldung verlöre ihren Grund und
ihr voraussichtliches Ende (im Nachbau 12 Texte). Der Zusatz ändert so
keine Satzwahl und keinen Schnitt, nur den Namen.
`tests/test_wl_stop_markers.py` prüft jede Haltestelle des Verzeichnisses
mit Zusatz, also auch nach jeder Aktualisierung in CI.

> **Entscheidung 2026-11-08 (Michael):** „Den Zusatz „WL“ kannst du gerne,
> so wie „S“ oder „U“ auch entfernen.“

Das Verzeichnis setzt „(WL)“ hinter den Namen einer Haltestelle der Wiener
Linien, um sie von einem gleichnamigen Bahnhof zu trennen. In den Feed kam
der Zusatz über ÖBB-Titel aus dem Verzeichnis („REX 6: Wien Baumgarten (WL)
↔ Ebenfurth“ im September, „Wien Liechtenwerder Platz (WL) ↔ Mödling ↔ Wien
Liesing“ im Mai 2026). Er sagt, woher der Eintrag stammt, nicht wo die
Haltestelle ist, und keine Quelle schreibt ihn; `without_stop_markers`
entfernt ihn an derselben Stelle wie „S“ und „U“. Steht eine solche
Haltestelle vor einem Bahnhof, nennt ein ÖBB-Titel schon vorher den Bahnhof
(Teil 9, „Ein Ort, zwei Namen“).

Weiter in [Teil 10](architecture-teil-10.md).
