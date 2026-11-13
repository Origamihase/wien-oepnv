[Architektur-Karte — Wien ÖPNV Feed](architecture.md), Teil 3.

* **Eine Linie, ein Platz (seit 2026-10-01, Betreiberentscheidung).**
  „Wenn mehrere unterschiedliche Linien betroffen sind, soll die Störung
  auch angezeigt werden. Mehrere Störungsmeldungen zur selben Linie sollte
  so gut wie möglich zusammengefasst werden.“ Anlass: Am 01.10. stand
  „60: Schadhafter Pkw & Schadhafter Pkw Betrieb ab Anschützgasse“ im Feed.
  `deduplicate_fuzzy` hatte zwei Kurzmeldungen der Linie 60 vor der
  WL-Zusammenlegung mit „&“ verbunden (21 solche Titel seit 12.09.). Und
  „66A: Rettungseinsatz“ stand neben „66A: Busse halten
  Salvatorianerplatz“. Seit 27.09. hatten 112 von 236 Feed-Ständen eine
  Linie in mehr als einem Eintrag.
  - Nach den Fenstern legt
    `_merge_wl_ticker_clusters` die WL-Störungen derselben Linien
    zusammen, deren Gültigkeit sich überschneidet, auch über mehrere
    Schritte (`_line_runs`).
  - Eine Ursache: ein Vorfall, wie oben zusammengelegt. Beispiel: Die vier
    Kurzmeldungen der Linie 18 vom 01.10. kamen um 16:25, 16:31, 16:33 und
    16:37, über das 10-Minuten-Fenster hinaus. Steht eine ausführliche
    Meldung darin, steht sie für den Vorfall, von mehreren die neueste
    (`_incident_entry`). Was die übrigen Kurzmeldungen ankündigen und ihr
    Text nicht nennt, bleibt (`_with_consequences`), nach diesen Regeln:
    - Was Fahrgäste tun müssen („Kein Betrieb“, „Betrieb ab/nur/…“, „Züge
      halten“, „Busse halten“, „Umleitung“, „Ersatzbus“, „Shuttlebus“,
      „Einstieg“, „benützen“, „ausweichen“), steht vorn. Nennt der Text
      selbst eine Maßnahme, hängt es mit „; “ an deren Satz, vor „Weichen
      Sie …“ und vor „Voraussichtliche Dauer“. Beispiel 60 am 22.09.:
      „Betrieb nur zwischen Westbahnhof S U und Hofwiesengasse; Züge halten
      bei der Linie 62 Fahrtrichtung Lainz. Weichen Sie …“. Ein eigener
      Satz ginge verloren, wenn die Kurzmeldung auf eine Hausnummer endet:
      Hinter „Wipplingerstr 39.“ erkennt die Zusammenfassung keine
      Satzgrenze und nahm bei der Linie D samt der Folge auch „Weichen Sie
      …“ mit. Ohne eigene Maßnahme steht es als erster Satz davor; so
      stehen die Haltestellen des Ersatzbusses 26E seit dem 25.09. neben
      „26E: Gleisbauarbeiten“ („Die Kapazitäten der Ersatzlinie 26E …“)
      und wären sonst verschwunden.
    - Was Fahrgästen nichts zu tun gibt („Derzeit längere Wartezeiten!“),
      kommt ans Ende. Vorn nahm es bei der N29 am 30.09. den zweiten Satz
      der Zusammenfassung und damit die Ursache.
    - Ein bloßes „Fahrtbehinderung“ hängt nie an. Als gesagt gilt eine
      Folge, deren Wörter ab vier Buchstaben der Text schon hat („Betrieb
      ab Hofwiesengasse“ neben „Betrieb nur zwischen Westbahnhof S U und
      Hofwiesengasse“). Eine Beugung zählt ab fünf Buchstaben und bis drei
      Buchstaben länger mit („Verspätung“ in „Verspätungen“, „Betriebsschluss“
      nicht für „Betrieb“); Nomen auf -ung und Partizip eines Verbs sind ein
      Wort (`_word_stem`, seit 2026-11-06: 66A las „… umgeleitet; Umleitung
      über Inzersdorfer Straße“). Verneinungen zählen nicht als Wort: „Kein
      Betrieb“ ist neben „Derzeit ist ein Betrieb nicht möglich“ gesagt (1A am 22.09.).
    - Eine vorangestellte Ursache („Gasrohrgebrechen Shuttlebus …“) und
      WLs Tafel-Auszeichnung („\*\*Umleitung\*\*“) fallen weg.
  - Gleiche Ursache heißt auch WLs Synonym (`_CAUSE_SYNONYMS`): Im selben
    Fenster schrieb WL seit September 14-mal „Schadhafter Zug“ neben
    „Schadhaftes Fahrzeug“, 9-mal „Fremdunfall“ oder „Verkehrsunfall“ neben
    „Fremder Verkehrsunfall“, 6-mal „Beschädigte Oberleitung“ neben
    „Oberleitungsgebrechen“. Nur was WL so nebeneinander schrieb:
    „Bauarbeiten“ und „Gleisbauarbeiten“ können zwei Baustellen sein
    („12A: Bauarbeiten, Gleisbauarbeiten“ über „Bauarbeiten: Busse halten
    Linke Wienzeile 110. Gleisbauarbeiten: Betrieb ab Johnstraße U,
    Schweglerstraße 19-21.“). Seit 2026-11-06 ist auch jedes Paar eine
    Ursache, das das Glossar gleich übersetzt (`_same_translation` in
    `_cause_key`): „13A: Wasserrohrbruch, Wasserrohrgebrechen“ (05.11.) las
    englisch „Burst water pipe, burst water pipe“, jetzt „13A:
    Wasserrohrgebrechen“ (`tests/test_wl_cause_same_translation.py`).
  - Mehrere Ursachen: ein Eintrag, der jede nennt, der neueste Vorfall
    zuerst und mit dessen GUID, damit ein neuer Vorfall den Feed weiter
    anführt (`_combined_incidents`). Je Vorfall ein Satz „Ursache: Folge.“
    (`_incident_sentences`). Das ist die Maßnahme, mit der die
    ausführliche Meldung beginnt, vor „Weichen Sie …“, „Voraussichtliche
    Dauer“ und „Grund:“ (`_LONG_MESSAGE_TAIL_RE`; die Satztrennung sieht das
    Satzende hinter „Stephansplatz U.“ nicht). Sie sagt mehr als die Tafel
    („Betrieb nur zwischen St. Marx S und Landstraße S U“ statt „Betrieb ab
    Landstraße“). Dahinter steht die Folge der Tafel, wenn sie Neues sagt
    („Busse halten bei der Linie 14A“); ein bloßes „Fahrtbehinderung“ sagt
    nichts Neues, und WLs Standardsatz weicht der Folge. Ohne ausführliche
    Meldung ist es die Folge der Tafel. Eine Ursache ohne Folge, oder mit
    dem Standardsatz neben etwas Konkretem, nennt nur der Titel. Über 180 Zeichen gibt die längste
    Angabe zuerst ihre letzte Folge ab. Ein solcher Titel gilt nicht als
    Kurzmeldung (`_is_wl_ticker`), wird also nicht in Ursache und Folge
    geteilt:

    ```
    11A: Falschparker, Schadhafter PKW
    Falschparker: Fahrtbehinderung in Richtung Bahnhof Heiligenstadt S U. Schadhafter PKW: Fahrtbehinderung.
    ```

  - **Eine laufende Störung nie mit geplanten Arbeiten (seit 2026-11-03,
    Betreiberentscheidung „Ja, beide beheben“, mit dem Grundsatz vom
    01.11.: „Störungen haben höchste Priorität im Feed! Hinweise sind
    weniger wichtig.“).** Bis dahin stand eine Störung mit der
    Tafel-Meldung einer Baustelle derselben Linie in einem Eintrag
    („66A: Rettungseinsatz, Bauarbeiten“). Das Wort der Baustelle machte
    den ganzen Eintrag geplant (`_incident_since`): kein „[Seit hh:mm]“,
    nicht vorn, manchmal mit dem alten ersten Erscheinen der Baustelle. Am
    21.10. um 15:31 Uhr stand „1: Rettungseinsatz, Gleisbauarbeiten
    [Heute]“ auf Platz 9, unter zwei Haltestellenverlegungen; am 16.10. las
    „D: Rettungseinsatz, Gleisbauarbeiten“ nur die Haltestellen der
    Baustelle. Im Replay über 890 Stände vom 20.09. bis 03.11. standen 62
    solche Einträge, in 34 Ständen unter einem Hinweis, in 7 außerhalb der
    ersten zehn. Jetzt gibt ein Lauf mit geplanten und laufenden Vorfällen
    zwei Einträge (`_merged_runs` in `src/feed_build/wl_joins.py`): die
    laufenden, und die geplanten mit den älteren Tafel-Meldungen ohne
    Ursache, deren Text eine geplante Maßnahme nennt (den Haltestellen der
    Baustelle, „62A: Straßenbau Haltestelle verlegt …“). Tafel-Meldungen
    ohne Ursache, die keine nennen („25: Züge halten Donaufelder Straße
    175-177“), folgen weiter der Störung (Prüfung vom 09.10.). Geplant ist ein Vorfall, wenn
    seine Ursache eine geplante Maßnahme nennt (`names_planned_measure`)
    oder er keine Störung nennt und jede seiner Meldungen zur vollen Stunde
    begann (`_scheduled_clock`, die Regel der Zeitzeile; „26E: Ersatzbus
    hält Karl-Waldbrunner-Platz …“ ohne Ursache ab 00:00:14 gehört zur
    Baustelle). Ein Störungswort als Ursache („Schadhafter PKW“,
    „Rettungseinsatz“) hat Vorrang vor der vollen Stunde (Michaels
    Entscheidung vom 03.11.2026, `_names_incident` in
    `src/feed_build/wl_tickers.py`): Am 24.10. um 00:31
    stand sonst „11A: Schadhafter PKW [Heute]“ (ab 00:00:28) als zweiter
    Eintrag neben „11A: Falschparker [Seit 00:03]“. „Ersatzverkehr“,
    „Ersatzbus“, „(Derzeit) kein“, „Busse/Züge halten“, „Linie … benützen“,
    „Betrieb ab/über …“ und „Umleitung“ nennen die Maßnahme, keinen Grund,
    und bleiben bei der Regel der vollen Stunde. Der
    Eintrag der Baustelle geht in ihren Hinweis auf
    (`_absorb_works_tickers`) oder behält seinen Platz. Störungen
    untereinander und geplante Maßnahmen untereinander teilen sich weiter
    einen Platz.

  - Eine Meldung ohne eigene Ursache geht zum Vorfall, der ihr zeitlich
    am nächsten veröffentlicht wurde, unter denen, die schon begonnen
    hatten, als sie erschien: Eine Tafel-Meldung kann keinen späteren
    Vorfall ankündigen. Meldungen, die ein paar Minuten vor ihrem Vorfall
    kommen, liegen ohnehin in seinem Zeitfenster
    (`WL_TICKER_CLUSTER_SECONDS`). Eine ältere Meldung ohne Ursache steht
    hinter dem Text des Vorfalls, als eigener Satz nach seinem Grund, und
    gehört nicht zu seinen Mitgliedern (`_with_unowned`, seit 2026-10-09);
    die Linie behält ihren einen Platz. Anlass: Die Haltestellen der
    Gleisbauarbeiten „25: Ersatzbus 26E hält Karl-Waldbrunner-Platz vor
    Schloßhofer Straße“ und „Züge halten Donaufelder Straße 175-177“
    (seit 23.09. im Feed-Zustand) schlossen sich am 09.10. um 09:36 „25:
    Verspätungen“ (Grund: Schadhaftes Fahrzeug) an. Der Feed las die
    Haltestellen vor der Verspätung, ihr Grund fiel aus den 180 Zeichen,
    und als die Verspätung endete, behielten die Haltestellen ihre GUID
    und ihren Beginn: Sie standen von 12:01 bis 22:02 als neue Störung im
    Feed, bis auf Platz 2. Jetzt liest der Eintrag „Unregelmäßige
    Intervalle in beiden Richtungen. Grund: Schadhaftes Fahrzeug.“, und
    danach fällt er auf GUID und Beginn der Haltestellen zurück.
  - Satzende vor jeder angehängten Meldung (seit 2026-11-06,
    `_then_sentence` in `src/feed_build/title_words.py`): Eine lange
    WL-Meldung endet mit einem Punkt, eine Kurzmeldung der Anzeige nicht.
    Jede Stelle, die eine Meldung hinter einen WL-Text hängt
    (`_with_unowned`, `_with_consequences`, `_with_line_tickers`), schließt
    den Text vorher als Satz. Anlass: „26: Rettungseinsatz“ las am 05.11.
    „Fahrtbehinderung wegen Rettungseinsatz Züge halten in Tokiostraße.“
    (ebenso „26: Verkehrsunfall“ am 04.11.); jetzt steht ein Punkt vor
    „Züge“. Der Wortlaut der Quelle bleibt, auch die Ursache des Titels im
    Text (Betreiberentscheidung 2026-11-06, „WL-Wortlaut behalten“).
  - Ausnahmen: Eine Ursache von mehr als `_MAX_LISTED_WORDS` (6) Wörtern
    ist ein Satz. Die Meldung „18: Haltestelle Stadionbrücke … aufgelassen.
    Bitte …“, seit Juli im Cache, behält ihren Eintrag und schließt sich
    keinem Vorfall an. Verschiedene Linienmengen bleiben getrennt, auch
    bei gleicher Ursache („1A: Demonstration“, „3A: Demonstration“).
    Vorfälle ohne gemeinsame Gültigkeit bleiben getrennt.
  - Grenzen: Die Beschreibung übernimmt zwei Sätze; bei drei Ursachen mit
    eigener Folge nennt nur der Titel die dritte. Erscheinen zwei Vorfälle
    im selben Lauf zum ersten Mal, zählt die Störungsstatistik nur den
    neueren.
  - Rückschau über 750 Cache-Stände seit 12.09. (Kurzmeldungen, ÖBB und
    Baustellen, nur Duplikatprüfung und Zusammenlegung): 1.727 Einträge
    weniger. In keinem Stand stehen mehr Einträge als vorher. Paare
    derselben Linie: 4.931 statt 6.958. WL-Titel mit „&“: 126 statt 292. Die
    126 sind ein Hinweis der 63A vom 15. bis 17.09., den `deduplicate_fuzzy`
    mit einer Störung verband.
* **„Fahrtbehinderung <Ursache>“ (seit 2026-09-25).** WL setzt die Art der
  Behinderung vor die Ursache („11A: Fahrtbehinderung Verkehrsunfall“,
  „31: Fahrtbehinderung wegen Polizeieinsatz“; 106 Titel seit Juni).
  Betreiberentscheidung (Audit A.13): Die Ursache kommt in den Titel
  („11A: Verkehrsunfall“), „Fahrtbehinderung“ füllt eine sonst leere
  Beschreibung. Eine eigene Beschreibung von WL bleibt unverändert, weil sie
  mehr sagt (`_HINDRANCE_RE` in `_reason_and_fragment`). Folgt auf die
  Ursache noch eine Folge, gewinnt die Folge die Beschreibung. Die lange
  Form bei Kollisionen lautet `18: Fahrtbehinderung – Verkehrsunfall`;
  „fahrtbehinderung“ steht dafür in `_INCIDENT_REASON_WORDS`.
* **„ÖBB-Ersatzbus für <80“ (seit 2026-09-25).** Die Anzeigetafeln der WL
  zeigen das S-Bahn-Logo als Zeichen, das in den Daten als „<“ ankommt.
  Die Meldung „Bhf. Hütteldorf / ÖBB-Ersatzbus für <80“ kam täglich mit
  `relatedLines` „1“ und erschien als „1: Bhf. Hütteldorf ÖBB-Ersatzbus für
  80“. Die Straßenbahn 1 fährt nicht nach Hütteldorf; gemeint ist die S80.
  `_attribute_obb_replacement_bus` (in `_post_filter_wl`) setzt die
  S-Bahn-Linie aus dem Text als Linie ein und legt den Ort in die
  Beschreibung: `S80: ÖBB-Ersatzbus` über „Bhf. Hütteldorf“. Das gilt nur für
  die Wiener S-Bahn-Nummern (1, 2, 3, 4, 7, 40, 45, 50, 60, 80).
* **Kein Titel aus Liniennummern (seit 2026-10-01).** WL betitelte den
  Hinweis zur Demonstration am 01.10. mit „D, 1, 2, 71, 1A, 3A“; im Feed
  stand „D/1/2/71/1A/3A: D, 1, 2, 71, 1A, 3A“. Besteht ein Titel nur aus
  Linien, nimmt `_title_or_heading` die Überschrift der Beschreibung
  (`<h2>Demonstration</h2>`), ungekürzt: `_tidy_title_wl` hielte
  „Gleisbauarbeiten“ vor einem Ort für ein Etikett. Ohne Überschrift bleibt
  der Titel.
* **ÖBB-Strecken von einem Knoten (seit 2026-10-01).** Nennt eine Meldung
  drei oder mehr Strecken, die alle an einem Bahnhof beginnen, steht der
  Bahnhof einmal vorn (`_try_star_routes`): „R 40/REX 41/REX 4/S 40: Wien
  Franz-Josefs-Bahnhof ↔ St.Andrä-Wördern / Tulln an der Donau / Wien
  Heiligenstadt / Wien Nußdorf“ statt viermal „Wien Franz-Josefs-Bahnhof ↔
  …“. Eine Kette (`_try_chain_routes`) gilt seit 2026-11-06 nur, wenn jede
  Zwischenstation auf dem Weg zwischen ihren Nachbarn liegt; zwei Strecken
  mit gemeinsamem Ende abseits des Wegs stehen als Stern („Wien Praterstern ↔
  Wien Hauptbahnhof / Wien St. Marx“; Teil 9, „Zwei Bahnhöfe in einem Endpunkt“).

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
    einer Abkürzung bleibt, Teil 16, §13): „18: Haltestelle Stadionbrücke
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

Weiter in [Teil 4](architecture-teil-4.md).
