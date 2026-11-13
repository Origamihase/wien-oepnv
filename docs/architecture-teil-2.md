[Architektur-Karte — Wien ÖPNV Feed](architecture.md), Teil 2.

* **ÖBB: Teilstrecken gehen in der Gesamtstrecke auf (seit 2026-10-02).**
  Liegt eine Strecke einer Meldung ganz auf einer anderen derselben Meldung,
  nennt der Titel nur die längere (`_drop_contained_routes`). Ein Bahnhof
  liegt „auf dem Weg“, wenn der Umweg über ihn höchstens 20 % länger ist als
  die Luftlinie (`_CORRIDOR_DETOUR_FACTOR`) und eine ÖBB-Linie alle drei
  Bahnhöfe bedient (`station_lines`, aus `data/oebb_station_lines.json`).
  So wurde der Titel oben (125 Zeichen) zu „R 40/REX 41/REX 4/S 40: Wien
  Franz-Josefs-Bahnhof ↔ Tulln an der Donau“ (70) und „REX 50/REX 51/S 50:
  Wien Hütteldorf ↔ Wien Westbahnhof ↔ St. Pölten Hauptbahnhof“ zu „… Wien
  Westbahnhof ↔ St. Pölten Hauptbahnhof“. Die Linienbedingung hält
  Flughafen Wien neben Bruck an der Leitha im Titel, obwohl er auf der
  Luftlinie dazwischen liegt. Die einzelnen Strecken stehen weiter in der
  Beschreibung. In 441 ÖBB-Cache-Ständen der Historie änderte das 11 Titel
  und keine Zahl der Einträge nach `deduplicate_fuzzy`.
* **Anzeigelänge des Titels (seit 2026-10-02).** Über 50 Zeichen
  (`_DISPLAY_TITLE_TARGET`; zuerst 70, auf Betreiberwunsch gesenkt: im Feed
  vom 02.10. Median 33, drei Viertel ≤ 41) wird ein Titel an seinen eigenen Fugen gekürzt,
  nie abgeschnitten. Das geschieht erst beim Rendern (`_display_title` in
  `_format_item_content`), nach Dedupe und Merges, die Titel vergleichen:
  Früher gekürzt, las sich „18: Haltestelle Stadionbrücke aufgelassen“ wie
  eine kurze Ursache, und `_line_runs` legte die Meldung mit „18:
  Gleisschaden“ zusammen.
  * WL: Ein Titel aus ganzen Sätzen behält den ersten Satz, und ist der
    noch zu lang, fällt dessen Begründung („im Rahmen …“, „zur …“,
    „wegen …“) vor dem Partizip weg (`_shorten_wl_sentence_title`; der Punkt
    einer Abkürzung bleibt, Teil 8, §13): „18: Haltestelle Stadionbrücke
    aufgelassen“ statt 137 Zeichen. Nur wenn die Beschreibung jedes weggenommene Wort enthält;
    sie erscheint dann vollständig, weil sie den Titel nicht mehr wiederholt.
  * Baustellen: Im Abschnitt „von … bis …“ fällt zuerst „Kreuzung“ weg, dann
    jeder zweite Name an einem Ende (`_compact_baustellen_section`): „Rechte
    Wienzeile von Ramperstorffergasse bis Pilgramgasse“ statt „… von
    Kreuzung Ramperstorffergasse bis Kreuzung Pilgramgasse und
    Pilgrambrücke“. Ist der Titel dann noch über 50, behält er die Straße,
    und der Abschnitt steht vor dem ersten Satz der Beschreibung
    (`_baustellen_display`): „Landstraßer Hauptstraße“, darunter „Von
    Emmerich-Teuber-Platz und Juchgasse und Apostelgasse bis
    Schlachthausgasse: Es wird …“. Hätten zwei sichtbare Baustellen dann
    denselben Titel, behalten beide den Abschnitt (`_section_collisions`).
  * WL: „ab 07. April 2026“ am Titelende fällt weg, wenn die Meldung einen
    Beginn hat; die Zeitzeile nennt ihn. Die Ziffernform „ab 14.09.26“ fiel
    schon beim Abruf weg.
  * Bewusste Ausnahmen über 50: ÖBB-Strecken mit mehreren Linien („R40/REX41/
    REX4/S40: Wien Franz-Josefs-Bahnhof ↔ Tulln an der Donau“, 66) und
    „66A/N66: Grenzackerstraße Richtung Reumannplatz bzw. Oper, Karlsplatz“
    (69), deren Richtung sie von der Gegenrichtung „66A/N66:
    Grenzackerstraße“ unterscheidet.
* **Linien und Hausnummern im Titel (seit 2026-10-02).** Ebenfalls beim
  Rendern (`_display_title`):
  * ÖBB: Die Linienliste vor dem Doppelpunkt steht ohne Leerzeichen
    (`_compact_line_prefix`): „R40/REX41/REX4/S40: …“ statt „R 40/REX 41/REX
    4/S 40: …“, wie WL („U6“) und der Stammstrecken-Monitor („S1/S2“),
    Betreiberwunsch. In der Beschreibung bleiben die Linien in ÖBBs
    Schreibweise; dort fallen seit 2026-10-09 nur ÖBBs Höflichkeitssätze und,
    wo der zweite Satz sonst nicht passt, die Stationen weg, die der Titel
    schon nennt (unten, „ÖBB: der zweite Satz“).
  * Baustellen: Hausnummern stehen als Adresse (`_mark_house_numbers`).
    „Rennweg von 33A bis 37“ las sich am Fernseher wie Bus 33A und
    Straßenbahn 37; jetzt „Rennweg 33A–37“, ebenso „Kirchengasse 1–30“. Eine
    einzelne Nummer an einem Abschnittsende wird „bis Nr. 44“.
* **ÖBB: alle betroffenen Linien vorn (seit 2026-10-01).** Das Präfix
  nennt jede Linie, die die Beschreibung als ausfallend nennt („keine R
  40-Züge“, „die REX 41-Züge … können nicht fahren“), in ihrer Reihenfolge
  und jede einmal (`_affected_lines`). Bis dahin stand nur die erste davon
  vorn, und `R` kannte das Muster nicht: Über „… keine R 40-Züge fahren“
  stand „REX 41: …“, die zweite von vier Linien. Im ÖBB-Cache seit September
  nannten 3 von 45 Meldungen mehrere Linien; eine nannte nur die R 95 und
  hatte kein Präfix. Ausweichverbindungen der Wiener Linien („Linie U4“)
  stehen ohne „-Züge“ und zählen nicht. `_extract_line_prefix` erkennt ein
  solches Präfix wieder, sonst setzte `_post_filter_oebb` es beim Lesen des
  Caches ein zweites Mal davor.
  Züge, die ÖBB als Ersatz anbietet, zählen nicht (seit 2026-11-05,
  `_OFFERED_TRAINS_RE`): „Als Ersatzbeförderung haben Sie die Möglichkeit
  die REX6-Züge … zu nehmen“ unter „keine REX8-Züge“ machte den Titel
  „REX 8/REX 6“, als fiele die REX 6 aus (Marchegg ↔ Bratislava, vier
  Meldungen März bis Juli), ebenso „REX 51/S 50“ für Neulengbach ↔
  Tullnerbach-Pressbaum (Februar/März). Ein Satz endet dazu an „.“ oder an
  einem Zeilenumbruch vor einem Großbuchstaben (`_sentence_around`).
* **ÖBB: jede Linie in Kurzform (seit 2026-11-05, Betreiberentscheidung
  „Ja, so bauen“).** Nennt der Titel mehrere Linien und verschweigt der
  gezeigte Text eine davon, zeigt er wie bei WL (unten, `_lines_in_short`)
  jede Linie mit ihrem Abschnitt und ihren Tagen
  (`src/feed_build/oebb_lines_short.py`): „Wegen Bauarbeiten fallen Züge
  aus: REX50 St. Pölten Hbf ↔ Wien Westbahnhof 03.–10.11.; REX51, S50 Wien
  Westbahnhof ↔ Wien Hütteldorf 02.–11.11.“ Vorher stand dort nur der erste
  Satz („… keine REX50-Züge fahren.“): ÖBB schreibt je Linie einen Satz,
  der zweite war eine Liste von rund 60 Zugnummern und passte nie in die
  180 Zeichen (Jules-Audits vom 31.10. und 02.11.2026). Linien eines
  Abschnitts stehen zusammen, die Tage reichen vom frühesten bis zum
  spätesten Datum ihrer Sätze, das Jahr steht in der Zeitzeile. Was nicht
  in 180 Zeichen passt, fällt von hinten weg, und „…“ schließt (die
  Franz-Josefs-Bahn im November 2026 hatte fünf Abschnitte). Die Kurzform
  steht nur, wenn jede Titellinie in einem Satz „zwischen … und …“ mit
  „nicht fahren“ oder „keine … fahren“ steht und sie mehr Linien nennt als
  der alte Text; sonst bleibt der Text. Seit Februar betraf das 2 von 1.141
  ÖBB-Texten im Cache (REX50/REX51/S50 und R40/REX41/REX4/S40). Englisch:
  „Due to construction works, trains are cancelled: …“, Linien, Stationen
  und Tage bleiben, wie sie stehen. Die Feed-Wache (`oebb-zweiter-satz`)
  prüft einen solchen Text nicht auf ÖBBs zweiten Satz.
* **WL: eine geschriebene Linienliste steht nur einmal (seit 2026-10-04).**
  Schreibt WL die Linien ohne Doppelpunkt oder mit einem Namen aus zwei
  Wörtern, erkannte der Präfix-Leser sie nicht, und der Titel nannte sie
  zweimal: „16A/N65: 16A, N65 Grohnergasse“, „N25: N25, SEV U1: Kaisermühlen,
  V.I.C.“, „1/18/62/LB: 1, 18, 62, Badner Bahn: Signalstörung“ (10.09.),
  „3A: 3A Netzänderung Betrieb ab Riemergasse“ (04.10.).
  `_strip_written_line_list` (`src/providers/wl_lines.py`) nimmt eine solche
  Liste in das Präfix. Mit Doppelpunkt muss jeder Name ohne Leerzeichen ein
  Liniencode sein („Badner Bahn“ ist `LB`, „SEV U1“ wird `SEVU1` wie „REX 41“
  `REX41`); ohne Doppelpunkt nur, wenn jeder Name eine Linie der Meldung ist
  („U1 Klapprampensperre“ bei einer U1-Meldung, nie „25 Jahre …“ bei einer
  ohne Linie 25). Bleibt danach nichts übrig oder folgt eine Einheit („5
  Minuten …“), bleibt der Titel, wie er war.

* **Absatzende ist Satzende (seit 2026-10-02).** WL baut ausführliche
  Meldungen aus Absätzen und Überschriften. Die Umwandlung in Text machte
  aus jeder Grenze ein Leerzeichen, und Überschrift und Felder liefen
  ineinander: „U1: Starke Nachfrage Die U1 wird …“, „… Schwedenplatz U
  Haltestelle: Stammersdorf Von: Brünner Straße gegenüber 262 …“
  (16 von 69 Meldungen am 02.10.). `html_to_text(mark_block_ends=True)`
  markiert jetzt das Ende jedes Absatzes, und `_close_blocks` setzt dort
  den Punkt, der fehlt. Eine erste Überschrift, deren Wörter alle schon im
  Titel stehen („Bauarbeiten S80“ unter „S80: Bauarbeiten“), fällt weg; ein
  einzelnes Wort aus `_CATEGORY_PREFIX_WORDS` (`<h2>Gleisbauarbeiten</h2>`)
  bleibt den bisherigen Regeln, jedes andere einzelne Wort fällt ebenfalls
  weg (seit 2026-10-05: „Bahnsteigsanierung Wegen Sanierung …“ stand so auf
  dem TV, 11 solche Überschriften seit Juli, darunter
  „Laufsportveranstaltung“, „Netzänderung“, „Kanalgebrechen“), und eine
  Überschrift ohne Text dahinter bleibt stehen. Zwei
  Ausnahmen vom Punkt: Endet ein Absatz auf ein Funktionswort („… der damit
  einhergehenden“) oder beginnt der nächste klein („prov. Einbahnführung
  …“), läuft der Satz weiter; ein Etikett mit Doppelpunkt („Maßnahmen:“)
  gehört zum nächsten Absatz und steht nie allein am Ende. Absätze mit
  „Zeitraum:“ und „Dauer:“ fallen in jedem mehrteiligen Text weg, denn die
  Zeitzeile nennt die Daten schon, und sie nahmen den Maßnahmen den Platz.
  Ein Maßnahmenblock („Linie D: …; Linie 1: …“) folgt einem stehen
  gebliebenen Einleitungssatz auch dann, wenn beide nicht in 180 Zeichen
  passen, und wird mit „…“ gekürzt, solange die Linie und mindestens drei
  Wörter ihrer Maßnahme sichtbar bleiben (`_with_second_sentence`, seit
  2026-10-14; vorher stand bei der Demo am Ring am 13.10. nur „Wegen einer
  Demonstration kommt es zu einer Einschränkung des öffentlichen
  Verkehrs.“). Als Etikett einer Maßnahme zählt seit 2026-10-24 auch, was
  WL zwischen Linie und Doppelpunkt schreibt, eine Klammer („Linie 71
  (ganztägig):“) oder die Richtung („Linie 11A in Richtung Stadion U:“,
  „in beiden Richtungen“), und die Wochentage davor („Montag-Freitag,
  Linie 80A …:“); eine Linie als eigene Zeile über ihrer Maßnahme
  („<u>Linie D</u><br>Umleitung …“) bekommt den Doppelpunkt
  (`_WL_MEASURE_LABEL`, `_wl_measure_labels_with_colon`). Vorher fanden
  die Regeln den Block nicht, und 19 WL-Meldungen im Cache seit Juli zeigten nur
  „… kommt es zu folgenden Verkehrsmaßnahmen.“, am 24.10. das
  Sicherheitsfest und der Nationalfeiertag (Linien 1, 2, D, 71). Als
  Ankündigung fällt auch „… treten bei der Linie 80A Maßnahmen in Kraft.“
  weg, ebenso eine Bitte an den Leser vor dem Block („Beachten Sie bitte
  die unterschiedlichen Maßnahmen …“). Bleibt nach dem Schnitt von einer
  Maßnahme nur ihr Etikett („…; Linie N8“), fällt es weg, und „…“ steht
  hinter der Maßnahme davor. Eine WL-Störung mehrerer Linien zeigt jede Linie in
  Kurzform, wenn der Text sonst eine Linie verschweigen würde
  (`_lines_in_short`, seit 2026-10-15; Betreiberentscheidung „Kurzform je
  Linie“, Begründung: „Am TV sieht man sonst nur, was mit einer von drei
  Linien passiert“). WL schreibt einen Satz je Linie, und beim
  Gasrohrbruch am 11.10. stand nur die Umleitung der 36B da. Jetzt:
  „Umleitungen: 36B Berzeliusgasse – Brünner Straße S; 30A Berzeliusgasse
  – Gschweidlgasse; 31A Berzeliusgasse – Großjedlersdorf, Jochbergengasse.“
  Je Linie stehen die Maßnahme und ihre beiden Endpunkte, ohne Richtung
  und Umleitungsstraßen. Ein Endpunkt endet auch dort, wo ein eigener
  Nebensatz beginnt, also bei „und“ oder einem Komma vor einem klein
  geschriebenen Wort; was danach kommt, fällt weg wie „Weiterfahrt …“
  (seit 2026-10-25, Nachprüfung: am 10.05. las die 1 „Betrieb nur
  Stefan-Fadinger-Platz – Radetzkyplatz und wird ab Kliebergasse …“).
  Haben alle Linien dieselbe Maßnahme, steht sie einmal vorn, sonst bei
  jeder Linie („25: Betrieb nur …; 30: Kein Betrieb.“). Bekannt sind „Kein Betrieb“, „Betrieb nur“, „Umleitung“
  sowie „Fahrtbehinderung“, „Unregelmäßige Intervalle“ und „Verspätungen“
  ohne Orte; „Badner Bahn:“ ist die Linie LB. WLs „Voraussichtlich bis
  hh:mm Uhr.“ folgt, wenn es passt. Linien, die nicht in 180 Zeichen
  passen, fallen ganz weg, die letzte zuerst, mit „…“. Nennt der gezeigte
  Text schon jede Linie („Linie 26: …; Linie 27: …“) oder kennt die Regel
  die Maßnahme einer Linie nicht, bleibt der Text wie bisher. Replay über
  alle 4.899 WL-Texte seit Juli: 62 ändern sich, alle nur dort, wo vorher
  mindestens eine Linie fehlte. Im EN-Feed ist der Kopf „Umleitungen:“ fest
  „diversions:“ (`src/feed/glossary.py`); nur mit seinem Doppelpunkt, denn
  als Wort im Satz ließ der feste Begriff dem Modell kein Nomen, um das es
  den Satz baut („kommt es zu Umleitungen“ → „diversions occurs“, bis
  2026-10-24 im EN-Feed). Dieser Satz selbst ist seit 2026-10-24 fest
  „there are diversions“: Dem Modell überlassen hieß er „there are
  redirections“, während jeder andere WL-Text „diversion“ sagt (Michael,
  2026-10-24: „Der Feed soll ja mehrere Quellen vereinheitlichen.“). Für
  WL ist dazu „im Kreuzungsbereich“ fest „at the intersection of“, sonst
  rückte „intersection“ hinter den festen Ausdruck ans Satzende. Beginnt der Text mit einer Überschrift oder einem Feld, gilt die
  Zwei-Sätze-Regel nicht, sonst stünde „Haltestelle: Stammersdorf.“ ohne
  die neue Lage da: gezeigt werden die Felder bis zum ersten ganzen Satz,
  danach greift die 180-Zeichen-Grenze. Bei einer Haltestellenverlegung
  steht der Weg der Haltestelle vorn, als „Donaufelder Straße 48 →
  Donaufelder Straße 40-42“ statt „Von: … Nach: …“ (Betreiberwunsch, spart
  Zeichen), dann die Linien mit ihren Richtungen; ein
  „Haltestelle:“, das nur den Titel wiederholt, fällt weg (seit 2026-10-05,
  Betreiberentscheidung „Von/Nach zuerst“, `_relocation_first`: vorher
  fehlte bei 46 von 139 Verlegungen das „Nach:“). Nennt eine Meldung zwei
  Richtungen mit verschiedenen Orten, steht jeder Pfeil vor seiner
  Richtung, damit jeder Ort bei seiner Richtung bleibt (seit 2026-10-05
  abends; bis dahin blieb dort die Reihenfolge, und „15A/7A/N62:
  Eibesbrunnergasse“ endete auf dem TV mit „Wienerbergstraße 27b-27c →
  …“). Eine ersatzlos aufgelassene Haltestelle („Nach: Ersatzlos
  aufgelassen“) bekommt keinen Pfeil, denn der heißt „verlegt nach“: sie
  steht als „Hartäckerstraße 65: ersatzlos aufgelassen“ da (seit
  2026-10-06; vorher „Hartäckerstraße 65 → Ersatzlos aufgelassen“ bei 40A,
  im August bei 5A/5B Malzgasse); ebenso ein bloßes „Nach: aufgelassen“.
  Setzt WL beide Richtungen als eigene Felder („Fahrtrichtung: Bhf.
  Atzgersdorf S bzw. Lainzer Tor“, darunter „Von:“ und „Nach:“), steht
  jedes Paar hinter seiner Richtung, dem ersten Ziel, das WL nennt; ein
  „Von:“, das schon bei der Richtung davor stand, steht nur einmal
  (`_relocation_by_direction`, seit 2026-10-24): „Richtung Bhf.
  Atzgersdorf S: Hermesstraße nach Strampfergasse → etwa 100 Meter in
  Richtung Dr.-Schober-Straße. Richtung Hietzing U: aufgelassen.“ Vorher
  stand auf dem TV „Hermesstraße nach Strampfergasse → etwa 100 Meter …
  Hermesstraße nach Strampfergasse → aufgelassen.“, verlegt und
  aufgelassen zugleich. Fiele ein Ziel hinter die 180-Zeichen-Grenze,
  entfallen die alten Orte und jedes Ziel behält seinen Pfeil vor seiner
  Richtung (Betreiberentscheidung 2026-10-29, „Von weglassen, wenn zu
  lang“): „Richtung Döblinger Friedhof, Felix-Dahn-Straße: →
  Liechtensteinstraße 38. Richtung Schottentor U: → Liechtensteinstraße
  39.“ Vorher endete „40A: Bauernfeldplatz“ mit „… gegenüber
  Bauernfeldplatz → …“. Eine Verlegung, die alle Ziele zeigt, bleibt wie
  sie ist; zeigt auch die kürzere Form nicht mehr Ziele (15A/7A/N62, drei
  Linien je Richtung), bleibt die alte. Ob ein Ziel passt, misst die
  Kürzung des Feeds selbst (`_truncate_summary_180`, seit 2026-11-11; vorher
  las „54B: Stock im Weg“ „… → Ghelengasse gegenüber …“, Issue #2200). Seit
  2026-11-12 behält sie „32.“ als Satzende (Teil 8, §13), und 54B zeigt
  „Ghelengasse 30 → Ghelengasse 28“ mit beiden Zielen. Seit Juli betrifft
  die kürzere Form 40A Bauernfeldplatz und 17A Josef-Enslein-Platz, 54B nur
  vom Abend des 11.11. bis 12.11.2026.
  Im englischen Feed lesen beide Überschriften
  „Towards …“: Auch der Satz mit dem bloßen „aufgelassen“ geht wie jede
  Pfeilzeile ohne Modell (`_RELOCATION_CLOSED_LINE_RE`, seit 2026-10-25);
  vorher stand „Towards Bhf. Atzgersdorf S: … Direction Hietzing U:
  Discontinued.“ WLs Überschrift „Haltestellenverlegung bzw. -auflassung“
  heißt fest „stop relocation or closure“ (vorher „Stop relocation resp.
  release“). Hausnummern behalten ihren Buchstaben („27b“, „2a“, DIN 5008):
  `html_to_text` trennt nur noch Einheiten aus einem Buchstaben („2 m“,
  „5 h“) ab, „27 b-27 c“ stand mit dem Pfeil sichtbar im Feed. Die Folge einer Kurzmeldung steht
  immer als Satz mit Punkt da („Busse halten bei Haltestelle N71.“), und
  ein doppelter Punkt aus der Quelle („umgeleitet..“) wird einer.
  Die Pfeile der WL-Anzeigetafeln („<“, „>“) stehen nur dort im Text, wo
  sie auf einen Ort zeigen („Betrieb ab Schwedenplatz > Praterstern“); vor
  einem kleinen Wort oder einer Klammer und am Textende fallen sie weg, wie
  im Titel (seit 2026-10-23, `_STANDALONE_ARROW_RE`). Vorher passte der
  Text nicht mehr zu seinem Titel und der Satz stand doppelt da: „42/9:
  Falschparker / Umleitung bis Gersthof über Linien 42 und 9! Umleitung bis
  Gersthof < über Linien 42 und 9!“ (21.10.2026, Jules-Audit). Von 5.300
  WL-Texten seit Juli ändern sich 8.
  Kürzt die 180-Zeichen-Grenze einen Text, endet er nie auf einem Wort,
  das nur auf Folgendes hinführt: Artikel, Präposition, Konjunktion,
  „Richtung“, „Linie(n)“ (`_DANGLING_TAIL_WORDS`, seit 2026-10-06). Auf
  dem TV standen am 06.10. „… und N62 in Richtung …“ und „… bzw. N66 in
  Richtung …“: eine Richtung angekündigt, aber nicht genannt. Jetzt
  endet der Text auf „… und N62 …“; ein Etikett davor fällt mit
  („Maßnahmen: Linie …“), was davor steht, bleibt ganz (Liniennummern,
  „Donaumarina U“, eine geschlossene Klammer, ein Satz mit Punkt). Von
  308 gekürzten Texten seit Juli endeten 95 so. Ein Wort mit Punkt fällt
  nur weg, wenn es eine Abkürzung ist, eine Zahl nur als Ordnungszahl;
  „ein.“, „Uhr.“, „D.“, „Gleis 2.“ bleiben (seit 2026-11-12, Teil 8, §13).
* **Verklebte Wörter (seit 2026-10-02).** Die Baustellentexte der Stadt
  Wien kamen mit Wörtern ohne Leerzeichen in den Feed („Derlinke
  Fahrstreifen“, „Außerhalbder Arbeitszeit“, „zuden“). Zwei Stellen: Der
  Cache-Schreiber (`scrub_trojan_source_primitives`) und `_sanitize_text`
  löschten Zeilenumbruch-Steuerzeichen (vertikaler Tab, U+2028 …) ersatzlos;
  sie werden jetzt zu einem Leerzeichen. Ob die Quelle selbst schon
  verklebt liefert, lässt sich aus der Sandbox nicht prüfen (kein Zugriff
  auf `data.wien.gv.at`); deshalb erkennt `repair_glued_words` die Form
  auch im Text: Artikel vor Fahrstreifen-Adjektiv, Präposition vor Artikel,
  Funktionswort hinter „-straße/-gasse/-stelle/-platz/-ung“. Über 16 362
  Texte aus Feed- und Cache-Historie trifft die Regel 23 Stellen, alle
  verklebt; „zudem“, „indem“, „beiden“, „derzeit“ bleiben ganz. Seit
  2026-11-06 auch eine Buslinie am nächsten Wort („2Akann“, Salztorbrücke;
  „40Ain“, WL): Ziffern und Linienbuchstabe A/B am Wortanfang vor einem
  Funktionswort oder Verb aus fester Liste, damit „10Uhr“, „U5“ und
  „7Bauarbeiten“ ganz bleiben; über 9 028 Texte genau diese zwei Stellen.
* **St.-Abkürzung, Leerzeichen vor Satzzeichen, Ortsangabe ohne Ort (seit
  2026-10-03).** Die Nachprüfung der Änderungen vom 02.10. rendert alle
  2.126 verschiedenen Meldungen der Cache-Historie neu und fand drei
  Formen übrig. ÖBB schreibt „St.Pölten Hbf“ ohne Leerzeichen, während der
  Titel derselben Meldung „St. Pölten Hauptbahnhof“ hieß;
  `repair_saint_abbreviation` setzt es in Titel und Text („St.“ vor
  Großbuchstabe, sonst nichts). Ein Leerzeichen vor Komma oder Semikolon
  („Betrieb ab Enkplatz , Grillgasse“) fällt in `_format_item_content` vor
  jedem Vergleich weg, sonst unterschieden sich Titel-Folge und Text und
  derselbe Satz stand zweimal; ein Leerzeichen vor einem Punkt („12:15
  Uhr .“) und „im Bereich .“ ohne Ort fallen aus der Beschreibung.
* **ÖBB: der zweite Satz sagt, was noch gilt (seit 2026-10-09, Betreiberentscheidung „Stationen weglassen“ vom selben Abend).** ÖBB
  sagt im zweiten Satz, was noch gilt: „Planen Sie derzeit noch bis zu 10
  Minuten mehr Reisezeit ein.“ unter einer Störung, die vorbei ist, „Über die Dauer
  der Unterbrechung kann derzeit noch keine Angabe gemacht werden.“ unter
  einer laufenden. Der erste Satz mit beiden Stationen in ÖBBs Langform
  („zwischen Wien Leopoldau Bahnhst (U) und Wien Süßenbrunn Bahnhst“)
  brauchte bis zu 140 der 180 Zeichen; in 59 der 106 ÖBB-Texte, die der
  deutsche Feed seit September zeigte, fiel der zweite Satz weg. „Wien
  Leopoldau ↔ Wien Süßenbrunn“ las sich am 09.10. um 20:30 „… waren bis
  20:18 Uhr keine Fahrten möglich. [Heute]“, als wäre alles vorbei. Passt
  der zweite Satz nicht, lässt der erste jetzt den Ort weg, wenn der Titel
  jede seiner Stationen nennt (`_oebb_sentence_without_place`): „Wegen
  einer Stellwerkstörung am Bahnhof waren bis 20:18 Uhr keine Fahrten
  möglich. Planen Sie derzeit noch bis zu 10 Minuten mehr Reisezeit ein.“
  Nennt der Titel eine Station nicht, bleibt der Ort stehen. ÖBBs
  Höflichkeitssätze („Wir bitten um Entschuldigung.“, am Ende von 310 der
  316 ÖBB-Texte seit Juni; „Sobald uns weitere Informationen vorliegen,
  informieren wir Sie.“, „Details finden Sie hier...“) sagen am Fernseher
  nichts und fallen vorher weg (`_OEBB_COURTESY_RE`). Über alle 443
  ÖBB-Texte im Cache seit Juni ändern sich 265: 251 behalten ihren
  zweiten Satz, die übrigen verlieren nur den Höflichkeitssatz. Rückblick:
  Eine Störung, die ÖBB als vorbei meldet („waren“), bleibt im Feed, weil
  98 der 100 solchen Texte seit Juni noch Folgen nennen (Reisezeit,
  Zugausfälle); der zweite Satz macht das jetzt sichtbar. Eine Liste von
  Tagen, die ÖBB Zeile für Zeile setzt („am 10.02.2026<br>am
  10.03.2026<br>…“), verliert die Tage, die vorbei sind (Wiener Datum),
  und steht als Satz da, mit Kommas und „und“ vor dem letzten
  (`_oebb_upcoming_dates`, seit 2026-10-24). Am 24.10. las R95 „… am
  10.02.2026 am 10.03.2026 am 14.04.2026 … am 11.08.2026 …“, acht
  vergangene Tage, und der Schnitt nahm den 10.11. und was nicht fährt;
  jetzt „… am 10.11.2026 (jeweils 14:00 Uhr - 22:00 Uhr) keine R95-Züge
  fahren.“ Eine Liste in einer Zeile zählt genauso („am 25.10.2026 und
  01.11.2026 der S2-Zug 23360 nicht fahren“ hieße ab 26.10. weiter so;
  jetzt „am 01.11.2026 der S2-Zug …“, Nachprüfung 25.10.): ein Tag ohne
  eigenes „am“/„von“ übernimmt das seines Vorgängers, wenn der wegfällt.
  Ein einzelner Tag oder Zeitraum bleibt, ebenso eine Liste in einer Zeile
  ohne vergangenen Tag (eine über mehrere Zeilen wird auch dann zum Satz);
  sind alle Tage vorbei, bleibt der Text wie er war
  (die Meldung ist dann ohnehin abgelaufen). 20 ÖBB-Meldungen seit Dezember 2025
  haben eine Liste über mehrere Zeilen, zwei (S2 Leopoldau–Mistelbach,
  REX 51 Marchtrenk) eine in einer Zeile. Bleibt ein Satz mit seinen
  restlichen Tagen über 180 Zeichen, liest die längste Liste „an
  einzelnen Tagen“, bis der Satz passt; die Zeitzeile nennt den Zeitraum
  (`_oebb_long_date_lists_in_words`, Michaels Entscheidung 2026-10-25
  „klingt gut. mach das bitte“). Eine Uhrzeitangabe „(jeweils …)“ der
  Liste bleibt, wenn der Satz mit ihr passt; ist eine Liste gegangen,
  geht ein einzelner Tag oder Zeitraum einer zweiten Strecke im selben
  Satz ebenso, wenn der Satz noch nicht passt. ÖBBs eigenes „an
  einzelnen Tagen“ im Satz zählt dabei wie eine gegangene Liste: „von
  13.04.2026 bis 28.05.2026 (jeweils an einzelnen Tagen, 23:20 Uhr - 04:20
  Uhr)“ (Flughafen Wien–Wien Mitte, Frühjahr 2026) liest „(jeweils an
  einzelnen Tagen, 23:20 Uhr - 04:20 Uhr)“, „von 02.04.2026 bis 07.04.2026
  (an einzelnen Tagen) einige Nachtzüge“ (Wien–Innsbruck) liest „an
  einzelnen Tagen einige Nachtzüge“ (Nachprüfung 25.10.). Eine Liste, die passt, bleibt. Am 24.10.
  brach S1 Leopoldau–Deutsch Wagram nach der Liste ab, ohne zu sagen,
  was nicht fährt; mit dieser Regel allein las sie „… an einzelnen Tagen
  der S1-Zug …“, seit der nächsten steht vorn, was nicht fährt. Über alle
  Cache-Texte seit Juni ändern sich 43 ÖBB-Texte, kein WL-Text.
  **Was nicht fährt, steht vorn (seit 2026-10-25).** ÖBB nennt die Züge und
  „nicht fahren“ am Satzende, hinter Strecken und Tagen („Wegen Bauarbeiten
  kann zwischen … und zwischen … an einzelnen Tagen der S1-Zug 23626 nicht
  fahren.“). Bleibt der Satz auch mit „an einzelnen Tagen“ über 180
  Zeichen, nahm der Schnitt genau das: Am 25.10. las S1 „… an einzelnen
  Tagen der S1-Zug … [Bis 28.11.]“ (Jules-Audit 2026-10-25), 45 ÖBB-Anzeigen
  seit Dezember 2025 endeten so („… von 31.10.2026 bis 30.11.2026 …“, „…
  einige Nahverkehrszüge …“). Ein solcher erster Satz nennt jetzt zuerst die
  Züge und das Verb, Strecken und Tage folgen hinter einem Doppelpunkt:
  „Wegen Bauarbeiten kann der S1-Zug 23626 an einzelnen Tagen nicht fahren:
  zwischen Deutsch Wagram Bahnhof und Gänserndorf Bahnhof und zwischen …“
  (`_oebb_what_does_not_run_first`). Der Schnitt trifft dann das Ende der
  Strecken und Tage, die Titel und Zeitzeile auch nennen. Es werden nur
  ÖBBs eigene Wörter umgestellt; „an einzelnen Tagen“ am Ende der Strecken
  geht mit dem Verb nach vorn. Eine Liste von drei oder mehr Zugnummern
  liest dabei als ihre Anzahl mit ihren Linien („die Züge S1 (28632), …,
  S2 (28153)“ → „11 Züge der Linien S1, S2 und S3“; „die Züge IC 1110, …,
  D 1141“ → „8 Züge (IC und D)“): elf Nummern füllten am 02.08.2026 die
  180 Zeichen samt Strecke und Verb. Die Linien bleiben genannt, weil die
  Zahl allein nicht sagt, welche Züge betroffen sind (Michaels
  Entscheidung vom 25.10.: „Nenne die Linien, nicht die Anzahl an Linien“,
  „11 Züge der Linie S1“ ist in Ordnung, „3 Linien sind betroffen“
  nicht); bis dahin las die Liste „11 Züge“. Eine Liste nackter Nummern
  nennt keine Linie und behält ihr Wort („34 Nahverkehrszüge“, „7
  S4-Züge“). Jeder Zug der Liste zählt einmal, auch einer mit kurzer
  Nummer („EC 71“), und jede Nummer einer Gruppe („REX 1 (2334, 2338 und
  2342)“ sind drei); bis zur Nachprüfung vom 25.10. zählten nur drei- bis
  fünfstellige Nummern, die Südstrecke im Frühjahr 2026 las „11 Züge“
  statt 13. Englisch: „11 trains on lines S1, S2 and S3 cannot run“. **Ein Zeitfenster bleibt (seit 2026-10-26).** ÖBBs „(jeweils …)“ hinter dem Zeitraum sagt, wann die Züge nicht fahren; abgeschnitten las „keine Nahverkehrszüge … von 31.10.2026 bis 30.11.2026 …“ (Wien Hbf–Gramatneusiedl) wie ein Monat ohne Züge, gesperrt sind nur die Wochenendnächte. Passt der umgestellte Satz mit Fenster nicht, fällt zuerst die Strecke weg, wenn der Titel sie nennt, dann der Zeitraum, wenn die Zeitzeile an seinem letzten Tag endet; das Fenster bleibt („… keine Nahverkehrszüge fahren: jeweils von Samstag 23:15 Uhr bis Sonntag 04:40 Uhr und …“, `_oebb_window_kept`). Grundlage ist Michaels Entscheidung vom 26.10. („Fenster statt Strecke und Zeitraum“). „(an einigen Tagen)“ geht wie „an einzelnen Tagen“ mit den Zügen nach vorn (Südstrecke: „können an einigen Tagen 13 Züge (EC, RJ, RJX, IC und D) nicht fahren: von 14.03.2026 (22:00 Uhr) bis 29.05.2026 (05:00 Uhr)“); bis dahin schob die längere Linienangabe aus #2070 diese Angabe ans abgeschnittene Ende. Replay über alle 932 ÖBB-Texte an 13 Stichtagen: acht Anzeigen ändern sich, im heutigen Feed keine; Gramatneusiedl erscheint ab Sa 31.10. mit Fenster. Englische Wochentage stehen nach dem Modell groß (`_english_weekdays_capitalised`, Zweitprüfung 27.10.: das Modell schrieb „(saturday, Sunday and holiday)“). Ein Satz, der passt, behält ÖBBs Reihenfolge (S2 Leopoldau–Mistelbach);
  ebenso einer mit zwei Subjekten („der Zug REX6 (7799) zwischen … der Zug
  S60 …“) oder einem anderen Verb („werden … umgeleitet und können … nicht
  halten“). Grundlage ist Michaels Auftrag vom 25.10. („Wenn Jules echte
  Fehler gefunden hat, sollten sie behoben werden“). Replay über alle 932
  ÖBB-Texte seit Dezember 2025: 57 Anzeigen ändern sich, alle mit diesem
  Satzbau; im heutigen Feed nur S1. Dieselbe Regel für ÖBBs Störungssatz „… sind zwischen … keine Fahrten möglich.“: [Teil 8, §15](architecture-teil-8.md#15-was-nicht-möglich-ist-steht-vorn-seit-2026-11-13).
  **Vollständig umgesetzt seit 2026-10-11** (dieselbe Entscheidung, keine
  neue): Der Feed-Wächter (Regel `oebb-zweiter-satz`) fand Texte, die den
  zweiten Satz trotzdem verloren, ein Replay über alle 563 ÖBB-Texte im
  Cache seit Juni 13. Zwei Ursachen: (1) Der Ort wurde aus den Slots einer
  Vorlage gelesen; schrieb ÖBB den Satz etwas anders („… und Liesing (Wien)
  Zugfahrten voraussichtlich 23:00 Uhr nur eingeschränkt möglich.“, ohne
  „bis“, 13.08.) oder war es eine Bauankündigung („Wegen Bauarbeiten können
  zwischen … am 01.11.2026 … keine S45-Züge fahren.“), blieb der Ort stehen
  und „Ein Schienenersatzverkehr mit Autobussen wird für Sie eingerichtet.“
  fiel weg. Jetzt bestimmt der Satzbau den Ort: Er steht hinter dem Verb
  des Hauptsatzes („Wegen … sind|waren|können|fahren …“), jede Station reicht
  bis zu ÖBBs Zusatz („Bahnhof“, „(U)“, „(Wien)“) oder bis zum ersten Wort,
  das keinen Namen beginnt („bis“, „Zugfahrten“, „am 01.11.2026“), und der
  Titel muss sie ganz nennen. „Brunn am Gebirge-Maria Enzersdorf“ unter dem
  Titel „Wien Liesing ↔ Brunn am Gebirge“ bleibt deshalb stehen, ebenso ein
  Ort in der Ursache oder in einer Aufzählung. (2) Der Satztrenner verlangt
  vier Buchstaben vor dem Punkt (wegen „Gerasdorf b. Wien“) und sah „…
  Reisezeit ein.“ und „… vorerst ab.“ nicht als Satzende; zweiter und
  dritter Satz zusammen passten nicht und fielen gemeinsam weg (Leopoldau ↔
  Süßenbrunn, 09.10., 20:00). Passt das nicht, endet der zweite Satz jetzt
  am ersten Punkt, vor dem keine Abkürzung steht
  (`_oebb_first_two_sentences`); die Stationen fallen erst weg, wenn auch
  dieser Satz allein sonst nicht passt. Alle 13 Änderungen im Replay sind
  „zweiter Satz bleibt“, kein anderer Text ändert sich.
  **Ort in einer Liste (seit 2026-10-11, Gesamtprüfung):** Setzt hinter der
  Station eine Aufzählung fort („… und Flughafen Wien Bahnhof bzw. Wien Hbf
  (U) und Flughafen Wien Bahnhof, bis …“, „in Lind-Rosegg Bahnhst und
  Föderlach Bahnhof …“, „in Wien Hbf (U) bzw Wien Meidling …“) oder folgt
  im selben Satz eine weitere Strecke („… der Zug REX6 (7799) zwischen Wien
  Hbf und Kledering der Zug S60 …“), bleibt der Ort stehen. Ohne diese
  Regel hätte die Kürzung „Wegen Verlängerung der Bauarbeiten sind bzw. Wien
  Hbf (U) und Flughafen Wien Bahnhof, bis …“ erzeugt; der echte Text vom
  07.09. lag zehn Zeichen über der Grenze und kam so nie an den TV. Replay
  über alle 563 ÖBB-Texte: keine Ausgabe ändert sich, zehn verstümmelte
  Kürzungen entfallen (`_OEBB_PLACE_LIST_RE`, Test
  `tests/test_oebb_ort_in_liste.py`).
* **ÖBB: der Text beginnt mit dem Satz, der die Titelstrecke nennt (seit 2026-10-17).**
  Der Titel einer ÖBB-Meldung ist ihre Wien-relevante Strecke, und die kann
  aus einem späteren Abschnitt des Textes stammen (`_apply_route_title`). Die
  ÖBB-Meldung „Salzburg Hbf => Flughafen Wien Bahnhof/Hegyeshalom“ stand am
  17.10. als „Wien Hütteldorf ↔ Wien Hauptbahnhof“ im Feed, der Text darunter
  sprach aber von „Salzburg Hbf und Flughafen Wien Bahnhof bzw. Hegyeshalom“
  und brach nach 180 Zeichen ab, bevor er sagte, was passiert. Die
  Titelstrecke stand nur im Abschnitt „TEILAUSFALL: … kann der Zug RJ 263
  zwischen Wien Hütteldorf Bahnhof (U) und Wien Hbf (U) nicht fahren.“, den
  der Fernseher nie zeigte. Nennt Satz 1 eine Strecke und keine ihrer
  Stationen steht im Titel, rückt jetzt der erste spätere Satz mit einer
  Titelstrecke nach vorn; Satz 1 folgt, wenn beide in die 180 Zeichen passen
  (`_oebb_title_route_first`). Satz 1 bleibt vorn, wenn er keine Strecke
  nennt (er gilt dann für alle) oder eine Station mit dem Titel teilt
  („zwischen Gänserndorf und Deutsch Wagram“ unter „Wien Floridsdorf ↔
  Gänserndorf“ ist derselbe Bau vom Nachbarabschnitt aus, und Satz 1 trägt
  die Daten). ÖBBs Abschnittsnamen in Großbuchstaben („TEILAUSFALL:“,
  „GEÄNDERTE FAHRZEITEN:“, „ACHTUNG:“) stehen im Text normal geschrieben
  („Teilausfall:“), Betreiberwunsch vom 17.10. („„Teilausfall“ gefällt mir
  besser als „TEILAUSFALL““, `_oebb_section_labels_cased`); Kürzel wie
  „RJ 263“ oder „ÖBB“ bleiben. Replay über alle 589 ÖBB-Texte im Cache seit
  Mai: nur diese Meldung ändert sich (drei ihrer Fassungen), Titel und Zahl
  der Items bleiben gleich. Im EN-Feed haben diese Namen feste englische
  Namen („Partial cancellation:“, §8, seit 2026-10-23).
  **Das Ende folgt dem gezeigten Abschnitt (seit 2026-10-19).** Rückt so ein
  späterer Abschnitt nach vorn, gilt sein letztes Datum als Ende der Meldung
  (Tagesende, Wiener Zeit): Zeitzeile und Verbleib im Feed folgen ihm,
  Betreiberentscheidung vom 19.10. (Karte „Ja, als Regel“). Anlass war
  dieselbe Meldung: Der Fernseher zeigte „Teilausfall: Von 18.10.2026 bis
  19.10.2026 kann der Zug RJ 263 … nicht fahren. [Bis Mi 21.10.]“, die Marke
  kam aus dem Zeitraum der ganzen Meldung, und ab 20.10. hätte die Folie einen
  vorbeigegangenen Ausfall gezeigt. `_oebb_lead_section_end` kürzt `ends_at`
  nur, verlängert nie; ein Abschnitt ohne Datum behält das Ende der Meldung,
  ebenso einer, dessen Ende kein Datum ist („von 04.07.2026 bis
  voraussichtlich Ende 2027“, „ab 04.07.2026“ ohne „bis“-Datum,
  `_oebb_section_end_is_open`, seit 2026-10-24): sein letztes Datum ist sein
  Beginn, und eine Sperre über ein Jahr hätte den Feed nach dem ersten Tag
  verlassen (unabhängige Prüfung 24.10., noch kein echter Fall). Ein
  „bis“-Datum irgendwo im Abschnitt beendet ihn immer: ein „bis“ ohne Datum
  zählt nur direkt hinter dem Datum des Zeitraums, nicht im
  Wochentagsfenster („(jeweils von Samstag 23:15 Uhr bis Sonntag 04:40
  Uhr)“) oder in einer Strecke („nur bis Wien Meidling“; Nachprüfung
  24.10.).
  `_end_with_oebb_lead_section` läuft in `_assemble_feed_items` vor dem
  Altersfilter. Prüfung aller 925 ÖBB-Texte im Cache seit Mai: nur diese
  Meldung ändert sich; Nachbau von 241 Feed-Ständen (15.–19.10.): nur ihre
  Zeitzeile („[Heute]“ am 19.10. statt „[Bis Mi 21.10.]“), Reihenfolge und
  State gleich; ein Nachbau für den 20.10. füllt ihren Platz mit der nächsten
  Meldung.
* **„Voraussichtlich bis 14:10 Uhr“ statt „Voraussichtliche Dauer: 14:10
  Uhr“ (seit 2026-10-06, Betreiberentscheidung „Dauer-Text anpassen“).**
  WLs Störungsvorlage nennt das erwartete Ende unter einem Dauer-Label. Auf
  dem TV las sich das wie eine Dauer von 14 Stunden. Hinter dem Label stand
  seit Juli in jedem der 942 verschiedenen Texte im WL-Cache ein
  Endzeitpunkt (863 Uhrzeiten, dazu „Betriebsschluss“ in drei
  Schreibweisen, „Sonntag Betriebsschluss“, „Ende August“, „31.07.2026“,
  „Montag, 03. August 2026, 04:00 Uhr“), außer „Nicht absehbar“ (48).
  `_expected_end_sentence` schreibt in `_format_item_content`
  „Voraussichtlich bis <Ende>“; ein „bis“, das WL selbst davorsetzt, steht
  nur einmal da. Umgeschrieben wird nur ein Wert, der wie ein Ende anfängt
  (Ziffer, „ca.“/„circa“/„etwa“/„gegen“ davor, „Betriebsschluss“,
  Wochentag, „heute“, „morgen“, Monat, „Anfang“/„Mitte“/„Ende“) und keine
  Dauer nennt („2 Stunden“, „etwa zwei Wochen“). „Nicht absehbar“ und jede
  andere Form behalten WLs Label. Dabei werden WLs Tippfehler in dieser
  Uhrzeit behoben („15;40 Uhr“, „12:15 Uht“, „06:30Uhr“). Der Satz ist sechs
  Zeichen kürzer; in 62 der 4.663 nachgerenderten WL-Texte passt dadurch der
  Grund wieder ganz in die 180 Zeichen. Zur Zeitzeile widerspricht er nie:
  Uhrzeiten stehen unter „[Seit hh:mm]“, „[Heute]“ oder „[Am …]“, „Ende
  August“ unter „[Bis 31.08.]“. Im EN-Feed: §8, „Expected until“.
  Ist die Uhrzeit vorbei, fällt der Satz weg (`_drop_passed_expected_end`,
  Betreiberentscheidung „Weglassen“ vom 06.10.2026): In 40 von 184
  Feed-Ständen seit Juli war sie schon vorbei (U6 „bis 19:10“ um 20:31). WL
  meldet die Störung dann weiter, ihr Ende ist offen; Grund und „[Seit
  hh:mm]“ bleiben. Die Uhrzeit gilt für den Tag der jüngsten Zeit der Meldung
  (`pubDate` oder `starts_at`), bis drei Stunden davor noch für diesen Tag
  (N29: „00:30 Uhr“ in einer Meldung von 01:12), sonst für den nächsten
  Morgen („02:45 Uhr“ in einer Meldung von 23:10). Ein Fenster um die
  aktuelle Zeit taugt nicht: WL schätzt bis 17,5 Stunden voraus („ca. 22
  Uhr“ für eine Veranstaltung, angelegt um 04:30). Ein Punkt zwischen
  Stunde und Minute gilt nur mit „Uhr“ als Uhrzeit („10.30 Uhr“), sonst
  wäre „bis 12.10.“ 12:10 Uhr. Ein Datum,
  „Betriebsschluss“ und „Nicht absehbar“ bleiben stehen. Nachgespielt über
  387 Feed-Stände: 49 der 243 angezeigten Sätze mit Uhrzeit fallen weg,
  Reihenfolge, GUIDs und Titel bleiben gleich.
* **Zeitzeile: was für heute zählt (seit 2026-10-02, Betreiberentscheidung).**
  Wer vor dem Display steht, fragt: Gilt das jetzt, und wie lange noch?
  `format_local_times` antwortet darauf statt mit zwei vollen Daten:
  „[Heute]“ (endet heute), „[Bis Sa 03.10.]“ (läuft, der vergangene Beginn
  zählt nicht), „[Seit 30.09.]“ (läuft ohne Ende), „[Am So 04.10.]“ (ein
  künftiger Tag), „[Ab Mo 05.10. bis 11.11.]“ (beginnt später). Das Jahr
  steht nur, wenn es nicht das laufende ist, der Wochentag nur in den
  nächsten sieben Tagen. Eine Uhrzeit für das Ende steht bewusst nicht da
  (den Beginn einer Störung nennt der nächste Punkt): In den
  40 WL-Cache-Ständen bis 02.10. endeten 18 von 44 Störungen mit einer
  Uhrzeit am selben Tag genau eine Stunde nach ihrem Beginn („42:
  Feuerwehreinsatz“ 19:10 bis 20:10). Das sieht nach WLs Standardwert aus,
  nicht nach einer Prognose. Datum und Wochentag sind immer die realen des
  Kalenders nach Wiener Zeit (Europe/Vienna): Ein Ende am 03.10. um 01:00
  heißt „Bis Sa 03.10.“. Eine Verschiebung auf den Betriebstag davor (#1927)
  hat der Betreiber am 2026-10-02 verworfen. Ein sehr fernes Ende (mehr als `ABSOLUTE_MAX_AGE_DAYS` nach heute
  bzw. nach einem künftigen Beginn) fällt weg; gemessen ab dem Beginn verlor
  „N8: Thaliastraße U“ (seit 24.07.2024) sein Ende 16.11.2026. Der EN-Feed
  tauscht die Wörter einzeln aus (`_TIME_WORDS_DE_TO_EN`: „[From Mon 05.10.
  until 11.11.]“), die Daten bleiben. `ext:starts_at` und `ext:ends_at`
  ändern sich nicht. Die Wörter der Zeitzeile verbindet ein geschütztes
  Leerzeichen (U+00A0), damit das Display sie nie in der Mitte umbricht;
  bis 2026-10-06 war es das schmale (U+202F), mit dem „[Ab Mi 07.10. bis
  28.10.]“ in der EasySignage-Vorschau fast wie ein Wort aussah.
* **Zeitzeile einer Störung: „[Seit 10:37]“ (seit 2026-10-03,
  Betreiberwunsch „Störung bitte mit Zeitangabe“).** Bei einer Störung will
  der Leser abschätzen, wie alt die Meldung ist. `_incident_since` liefert
  den Beginn einer ungeplanten Störung (Kategorie „Störung“: WL-`trafficInfos`,
  ÖBB, Stammstrecke), `format_local_times` zeigt ihn als Uhrzeit, wenn er
  heute liegt und die Störung heute endet oder kein Ende hat. Sonst bleiben
  die Zeilen oben.
  - **WL:** Ein Eintrag bündelt oft mehrere WL-Meldungen desselben
    Einsatzes: `pubDate` ist die früheste, `starts_at` die jüngste
    (`wl_fetch` nimmt die jüngste, damit eine wiederverwendete alte
    Kurzmeldung nichts zurückdatiert). Am 03.10. trugen „86A/87A/95A:
    Fahrtbehinderung wegen Rettungseinsatz“ `pubDate` 10:37:00, den von WL
    eingetragenen Beginn, und die Kurzmeldungen der Linien von 10:42:44 bis
    10:54:13 als `starts_at`. Geprüft am 10.10. an den Rohdaten seit 04.10.
    (366 ungeplante WL-Störungen, `reports/seit-zeit-2026-10-10.md`):
    `time.start` einer Störungsmeldung ist der Beginn, den WL bei der
    Erfassung (`time.created`) einträgt, bei der Erstmeldung 0 bis 10
    Minuten davor (177 von 205 binnen einer Minute); Folgemeldungen
    (`-F01` …) erben ihn. Unser Abruf sah keine Meldung vor ihrer
    Erfassung und jede im nächsten Abruf danach. Der Beginn ist daher `pubDate`, wenn es am
    selben Wiener Tag liegt und nicht auf einer vollen Stunde; sonst
    `starts_at`. Von den 18 Bündeln seit Juli, deren früheste Meldung mehr
    als eine Stunde vor der jüngsten lag, trugen 12 eines dieser Merkmale,
    alle aus einem alten Stand („10A: Fahrtbehinderung“ mit 09.07. 00:00:25
    am 11.07.); bei den übrigen dürfte die früheste Meldung der echte
    Beginn sein („66A: Feuerwehreinsatz“ 13:43:00, jüngste Meldung
    15:06:30). Vorab eingetragene Maßnahmen schaltet WL zur vollen Stunde
    ein, wenige Sekunden später („12: Betrieb ab Franz-Josefs-Bahnhof“
    04:00:16), ohne dass der Titel die Ursache nennt; ein Beginn auf einer
    vollen Stunde zeigt deshalb keine Uhrzeit. Nennt die Ursache eine
    Störung, zeigt auch ein Beginn zur vollen Stunde die Uhrzeit (Michaels
    Entscheidung vom 03.11.2026: „Eine Störung kann auch zur vollen Stunde
    gemeldet werden“, „[Seit hh:00] bringt mehr Infos“; `_incident_cause`
    in `src/feed_build/wl_tickers.py`): die Ursache im Titel („N71:
    Polizeieinsatz“ ab 02:00:25) oder das „Grund:“ im Text („U2:
    Verspätungen“ mit „Grund: Schadhaftes Fahrzeug.“). Maßnahmewörter
    („Ersatzverkehr“, „Busse halten“, „Betrieb ab …“) zählen nicht als
    Störung. Ein früheres `pubDate` auf einer vollen Stunde zieht den Beginn
    weiter nicht vor, es ist das Zeichen einer alten Kurzmeldung. Über jeden
    dritten Stand seit Juli (2 056) zeigen 65 Störungen jetzt „[Seit hh:00]“
    statt „[Heute]“, alle 26 Maßnahmen bleiben bei „[Heute]“. Das gilt
    auch um 00:00: Eine Störung, die über Mitternacht läuft, trägt WL um
    00:00 neu ein und zeigt dann „[Seit 00:00]“, auch wenn sie am Vortag
    begann („31: Wasserrohrbruch“, erstmals 02.09.2026 06:43, am 03.09. ab
    00:00:15). Michaels Entscheidung vom 04.11.2026: das bleibt so, keine
    Ausnahme für Mitternacht. Die Beginne passen zur
    Beobachtung: 2 038 der 2 324 WL-Störungen seit Juli tauchten binnen 35
    Minuten nach ihrem `starts_at` im Feed auf (Takt 30 Minuten).
    Eine Kurzmeldung darf den Beginn der Störungsmeldung (WL-Nummer
    `I…`) höchstens `TICKER_LEAD_MAX` = 60 Minuten vorziehen
    (`wl_plausibility.incident_begin`, seit 2026-10-10; Entscheidung von
    Michael am 11.10.2026, „Fenster 60 Minuten“, weil es alle echten
    Vorläufe behält und die umgeschriebene Anzeige korrigiert; das Feld
    `_incident_start` trägt den Beginn der Störungsmeldungen durch Cache
    und Merge). WL schaltet die Anzeigen bis zu 39 Minuten vor der
    Erfassung ein, dann stimmt die frühere Zeit („9: Falschparker“ am
    09.10.: Anzeige 14:21, Meldung 15:00). Ältere stammen von einer
    Anzeige, die WL für den neuen Einsatz umgeschrieben hat und die ihre
    alte Startzeit behielt: Am 09.10. lasen die Anzeigen einer
    Demonstration von 13:08 „Fahrtbehinderung wegen Polizeieinsatz“ für den
    Polizeieinsatz ab 20:08, der Feed zeigte um 20:30 „2/71: Polizeieinsatz
    [Seit 13:08]“. Ohne Störungsmeldung im Eintrag sagt nichts, dass eine
    Anzeige umgeschrieben wurde; so blieb um 21:01 „71: Polizeieinsatz
    [Seit 13:10]“, als WL für die 71 nur noch die Anzeigen führte.
    Fällt die früheste Meldung weg, während die übrigen bleiben (die
    Kurzmeldungen laufen aus, die ausführliche Meldung wird erledigt),
    bleibt der Beginn stehen: Der Build merkt sich das früheste `pubDate`
    eines Eintrags im State-Feld `earliest_published`
    (`_carry_item_identity`, seit 2026-10-06, siehe `docs/development.md`,
    „Eine Meldung, eine GUID“). Vorher sprang „43: Verkehrsunfall“ am
    06.10. um 01:14 ohne neue Daten von „[Seit 00:03]“ auf „[Seit 00:04]“.
  - **ÖBB:** Der Beginn ist die Minute, in der ÖBB die Meldung zum ersten
    Mal veröffentlicht hat (`_first_published`). `starts_at` taugt dafür
    nicht: Seit 2026-09-12 ist es der Tag, ab dem die Meldung gilt (00:00,
    aus dem Zeitraum vor dem Text, „03.10.2026<br/><br/>Wegen …“), und bis
    zur Korrektur vom 2026-10-03 zeigte deshalb keine ÖBB-Störung ein
    „[Seit …]“. Auch `pubDate` allein taugt nicht: ÖBB veröffentlicht die
    Meldung bei jeder Aktualisierung unter derselben GUID neu („Wien
    Floridsdorf ↔ Wien Praterstern“ am 27.09. um 15:48, 17:26 und 20:07; 21
    der 33 ÖBB-Störungen vom 12.09. bis 02.10.). Das früheste `pubDate`
    steht daher im State-Eintrag der Meldung als `first_published`; ein
    Eintrag von vor diesem Feld beginnt beim früheren von `pubDate` und
    `first_seen`. Die Regel der vollen Stunde gilt für ÖBB nicht: Sie
    erkennt von WL vorab eingetragene Maßnahmen, ÖBB veröffentlicht von
    Hand. Die beiden ÖBB-Störungen seit Juli, die zur vollen Stunde
    erschienen, waren echte Vorfälle; „Wien Meidling ↔ Wien Liesing“
    (Polizeieinsatz, veröffentlicht 03.10. 20:00:01, „bis voraussichtlich
    20:30 Uhr keine Fahrten“) zeigte bis zur Korrektur vom selben Abend
    „[Heute]“ statt „[Seit 20:00]“. Baustellen heißen „Bauarbeiten“. ÖBB meldet eine beendete Störung in der
    Vergangenheit („Wegen eines Polizeieinsatzes waren in Mödling Bahnhof
    bis 19:55 Uhr keine Fahrten möglich“, veröffentlicht 19:57) oder als
    „Aufhebung …“ (`_is_all_clear`); 60 der 193 ÖBB-Störungen seit Juli.
    Sie behalten ihre Zeile (`_reports_past_disruption`), „[Seit 19:57]“
    behauptete das Gegenteil.
  - **Stammstrecke:** Der Beginn ist die erste gemessene verspätete
    Abfahrt, auch zur vollen Stunde (`_MEASURED_SOURCES`). Die Beschreibung
    trägt kein eigenes „[Seit …]“ mehr; sie las sich „… in Richtung
    Praterstern [Seit 09.08.2026]“ über der Zeitzeile „[Seit 09.08.2026]“.
  - **Geplant** heißt `_PLANNED_DISRUPTION_RE` in Titel oder Beschreibung:
    Veranstaltung, Demonstration, Kundgebung, „…arbeiten“, Staatsbesuch,
    „…übung“, „…verlegung“, Netzänderung, die geplanten Ursachen unter den
    WL-Störungen seit Juli. Seit 2026-11-03 auch eine verlegte oder
    aufgelassene Haltestelle in den Worten der Anzeigetafel („verlegt“,
    „aufgelassen“, „Auflassung“), ein Bau als Ursache („Straßenbau“,
    „Gleisbau“, „Brückenbau“, „Kanalbau“) und WLs „ÖBB-Ersatzbus“
    (Betreiberentscheidung: „Eine Verlegung der Haltestelle ist zB nur ein
    Hinweis, aber keine Störung.“). Anlass: „62A: Straßenbau Haltestelle
    verlegt zur Wienerbergbrücke vor Breitenfurter Straße [Seit 07:20]“
    stand am 21.10. um 07:31 Uhr auf Platz 1, über vier laufenden
    Störungen, und 33 Stände lang vorn; „S80: ÖBB-Ersatzbus – Bhf.
    Hütteldorf [Seit 00:30]“ (die Haltestelle des Ersatzbusses für die
    ÖBB-Bauarbeiten) vom 06. bis 14.10. 65 Stände lang, sechsmal auf Platz
    1. Unter allen WL- und ÖBB-Störungen vom 20.09. bis 03.11. trafen die
    neuen Wörter fünf geplante Maßnahmen und keinen Vorfall. Dieselbe Liste
    entscheidet über die Pause einer wiederkehrenden Meldung
    (`_PLANNED_OCCURRENCE_GAP`) und die Zuordnung erledigter Kurzmeldungen
    (`wl_resolved`). „Reparaturarbeiten“ (auch WLs „Reperaturarbeiten“)
    zählen nicht dazu, sie folgen auf einen Vorfall („Wegen Reparaturarbeiten
    nach einem Unfall“, ÖBB 27.08.). Stadt-Wien-Baustellen sind keine Störungen.
* **Zeitumstellung (geprüft 2026-10-03 für den 25.10.2026 und den 28.03.2027).**
  Tage, Wochentage und Uhrzeiten sind die von Europe/Vienna; der 25-Stunden-Tag
  des 25.10. ist ein Tag („[Heute]“ bis 23:59 Winterzeit). Die Reihenfolge
  zweier Zeitpunkte und jedes Rechnen mit Stunden laufen in UTC: Python
  vergleicht und subtrahiert zwei Zeiten derselben Zone nach der Wanduhr und
  übergeht den Offset. Geprüft mit der echten Nacht vom 26./27.09.2026, um vier
  Wochen in die Nacht des 25.10. verschoben (33 Feed-Läufe, State fortgeschrieben,
  freezegun mit erhaltenem `fold`), und um 26 Wochen in die des 28.03.2027;
  Reihenfolge, Altersgrenzen, Vorlauf und Zeitzeilen stimmten bis auf diese
  Stellen:
  - **Zeitzeile:** In der doppelten Stunde (02:00 bis 03:00) las sich der
    48A-Unfall von 02:26 Sommerzeit um 02:01 Winterzeit, 35 Minuten danach, als
    „noch nicht begonnen“ und zeigte „[Heute]“ statt „[Seit 02:26]“; umgekehrt
    hätte ein Unfall von 02:40 Winterzeit um 02:50 Sommerzeit schon „[Seit
    02:40]“ gezeigt. `format_local_times` und `_plausible_end` vergleichen
    jetzt in UTC. Die Uhrzeit bleibt die echte Wiener Uhrzeit des Beginns,
    ohne Zusatz für Sommer- oder Winterzeit.
  - **Stammstrecke:** `current - FEED_WINDOW` verschob die Wanduhr: am 25.10.
    reichte das Ein-Stunden-Fenster von 02:00 bis 04:00 Winterzeit zwei Stunden
    zurück, am Morgen der Umstellung auf Sommerzeit war es von 03:00 bis 04:00
    leer, eine laufende Verspätung verschwand und konnte ihren gemerkten Beginn
    verlieren.
    Die Fenster in `compute_stammstrecke_events`, `_has_recent_exceedance` und
    `read_recent_stammstrecke_observations` werden in UTC abgezogen. Der Monitor
    rechnet die Verspätung aus den Wiener Ortszeiten der VAO ebenfalls in UTC
    (`scripts/update_stammstrecke_status.py`): Ein Zug, fällig 01:55, der zehn Minuten später um
    03:05 Sommerzeit fuhr, zählte als 70 Minuten verspätet. Eine VAO-Zeit in
    der doppelten Stunde sagt nicht, welche der beiden gemeint ist; dort
    rechnet der Monitor weiter mit der Wanduhr, richtig, solange beide Zeiten
    in derselben der zwei Stunden liegen.
  - **ÖBB-`pubDate`:** ÖBB versieht jede Veröffentlichungszeit mit dem Offset
    zum Zeitpunkt des Abrufs. Beim ersten Abruf nach dem 29.03.2026 wechselten
    alle laufenden Meldungen bei gleicher Uhrzeit von +01:00 auf +02:00, und
    alle 38 Wintermeldungen im Cache tragen seither +02:00 („19 Dec 2025
    10:07:13 +0200“). Ab dem 25.10. trügen Sommermeldungen +01:00.
    `vienna_wall_clock` (`src/utils/vienna_clock.py`) liest die Uhrzeit als
    Wiener Ortszeit, wenn der Offset nicht zu ihr passt; in der doppelten
    Stunde passen beide und der gesendete gilt.
  - **WL-Zeitstempel (seit 2026-10-10 ebenso):** Solange Winterzeit galt,
    trugen alle Sommerzeiten von WL +01:00 (am 20.03.2026 alle 17, darunter
    die 11:11-Platzhalter „2027-06-30T11:11:00+01:00“ und Datumsenden
    „2026-05-31T23:56:00+01:00“); gemeint war die Uhrzeit. In der Sommerzeit
    stimmen die Offsets beider Jahreszeiten (kein falscher unter 7 775
    Winterzeiten in jedem 20. Cache-Stand von Mai bis Oktober, keiner unter
    den 53 der Rohantworten seit 04.10.). Der Fehler tritt also nur in der
    Winterzeit auf und ist ab dem 25.10. zurückzuerwarten. Ungeschützt
    zählte ein 11:11-Ende dann als 12:11 nicht mehr als Platzhalter (die
    Zeitzeile zeigte „[Bis 30.06.2027]“ für rund 27 Hinweise mit einem
    Sommer-Platzhalter), und ein Ende um 23:56 fiele auf den Folgetag.
    `wl_fetch._iso` und `wl_resolved._moment` lesen deshalb dieselbe
    Wanduhr wie ÖBB. Bis zur Umstellung ändert das nichts (alle Offsets
    passen); ob WL ab 25.10. wieder +01:00 schickt, zeigt der WL-Rohstand
    dieser Nacht.
  - Die Statistik-Spalte `hour` zählt am 25.10. die Stunde 02 doppelt; das ist
    die reale Wiener Stunde und bleibt so.

Weiter in [Teil 3](architecture-teil-3.md).
