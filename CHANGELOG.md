# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden in dieser Datei dokumentiert.

Das Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]
* **WL: erledigte Störungen sofort aus dem Feed (2026-10-05)**: WL markiert
  eine beendete Störung mit `"status": "resolved"` und liefert sie noch
  einen Abruf lang mit. Der Statusfilter kannte das Wort nicht, also stand
  die Meldung bis zu 30 Minuten weiter im Feed, mit altem Text: am 05.10.
  um 11:01 Uhr „Die Linie U6 fährt derzeit nicht zwischen Westbahnhof und
  Längenfeldgasse“, obwohl die U6 wieder fuhr. Teilt sie die Identität mit
  ihrer aktiven Folgemeldung (`…-F01`), verdeckte ihr Text deren aktuellen
  („13A: … Voraussichtliche Dauer: 19:15 Uhr“ statt „Nach einer
  Fahrtbehinderung kommt es zu unterschiedlichen Intervallen“). `resolved`
  zählt jetzt als beendet. Nachgespielt an allen 59 WL-Rohdaten seit
  04.10.: 9 Abrufe ändern sich, nur so wie beschrieben.
* **Englischer Feed: Pfeilzeilen ohne Modell, Ursache immer mit „Due to“
  (2026-10-05)**: Der erste echte Lauf nach „Absichern“ (17:45 Uhr MESZ)
  zeigte zwei Lücken. Die Pfeilzeile einer Verlegung ging durchs Modell
  und kam verklebt mit Platzhalter-Rest heraus („Wienerbergstraße27b-27c
  →Wienerbergstraße27a. … 15A5X in the direction of Enkplatz U“); sie wird
  jetzt wie früher der „Von:“/„Nach:“-Record ohne Modell gesetzt. Und das
  Modell gab „Wegen Sanierung des Bahnsteigs“ als „Renovation of the
  platform“ zurück; eine Ursache ohne „Due to“ bekommt es jetzt davor.
  Epoche 23.
* **Englischer Feed: WL-Sätze mit „Wegen …“ (2026-10-05)**: Nach der
  Textänderung bei „U6: Neue Donau“ wurde der Satz neu übersetzt und verlor
  seinen Grund („The U6 station stops at Neue Donau U …“). Kein Einzelfall:
  Seit Juli erschienen 3 WL-Sätze nach dem Muster „Wegen X wird Linie Y
  umgeleitet“ sinnentstellt („Construction works in the area of Wildbadgasse
  is redirected to line 20A.“). `_render_wl_because_sentence` übersetzt
  Ursache und Hauptsatz getrennt, den Hauptsatz mit „Deshalb“ eröffnet;
  passt die Antwort des Modells nicht in diese Form, bleibt es beim
  bisherigen Weg. Betreiberentscheidung „Absichern“. Epoche 22 (alle
  englischen Texte werden einmal neu übersetzt).
* **Haltestellenverlegungen: Hausnummern und zwei Richtungen (2026-10-05)**:
  Nachprüfung von „neue Lage zuerst, mit Pfeil“ über 17.467 verschiedene
  Meldungen seit Juli (WL, ÖBB, Baustellen) mit altem und neuem Code. Zwei
  Fehler, beide im Live-Feed von 16:31 Uhr (MESZ) bei „15A/7A/N62:
  Eibesbrunnergasse“: (1) „Wienerbergstraße 27 b-27 c“. `html_to_text`
  setzte vor jeden einzelnen Kleinbuchstaben nach einer Ziffer ein
  Leerzeichen; das traf Hausnummern („27b“, „2a“, „63a“, „77a“, nach DIN
  5008 ohne Leerzeichen), die mit dem Pfeil vorn sichtbar wurden. Getrennt
  werden nur noch Einheiten aus einem Buchstaben („2 m“, „5 h“); 10
  Beschreibungen ändern sich, nur um diese Leerzeichen. (2) Eine Meldung
  mit zwei Richtungen und verschiedenen Orten behielt ihre Reihenfolge, die
  neue Lage stand hinter drei Linien und ihren Richtungen und fiel der
  180-Zeichen-Grenze zum Opfer („… Karlsplatz (U). Wienerbergstraße 27 b-27
  c → …“). Jetzt steht jeder Pfeil vor seiner Richtung; 4 Verlegungstexte
  ändern sich. Reihenfolge, Auswahl und Schlüssel der Feed-Einträge bleiben
  in 12 nachgebauten Läufen seit 21.09. gleich.
* **Rebuild nach einem Merge baut aus den aktuellen Caches (2026-10-05)**:
  `build-feed.yml` checkte den gepushten Commit aus. Die Concurrency-Gruppe
  hält den Job zurück, bis ein laufender Update-Tick gepusht hat; danach
  baute er aus Caches, die 30 Minuten älter waren, und behielt beim
  Rebase-Konflikt seine Kopien. Nach dem Merge von #1953 (05.10., 14:30 UTC)
  ersetzte er so den Feed von 14:31 durch einen aus den 14:01-Caches: zwei
  aktuelle Meldungen fehlten bis zum nächsten Tick, ein `first_seen`-Eintrag
  ging verloren. Seit September viermal (18.09. dreimal). Jetzt
  `ref: github.ref`: ausgecheckt wird der Branch beim Jobstart.
* **Haltestellenverlegungen: neue Lage zuerst, mit Pfeil (2026-10-05)**: Auf dem
  TV endete „26E/N20: Fultonstraße“ mit „Von: Donaufelder Straße …“. Die
  neue Lage („Nach: Donaufelder Straße 40-42“) fiel der 180-Zeichen-Grenze
  zum Opfer, weil Linien, Richtungen und ein „Haltestelle:“, das nur den
  Titel wiederholt, davor standen. `_relocation_first` stellt den Weg als
  „Donaufelder Straße 48 → Donaufelder Straße 40-42“ nach vorn (Pfeil statt
  „Von:“/„Nach:“, Betreiberwunsch) und lässt das wiederholte „Haltestelle:“ weg; eine
  Meldung mit zwei Richtungen und verschiedenen Orten behält ihre
  Reihenfolge. Nachbau über 2.339 WL-Meldungen seit Juli: die neue Lage
  sichtbar in 138 statt 93 von 139 Verlegungen, ganz ohne Kürzung 90 statt
  28; alle anderen Meldungen unverändert.
  Betreiberentscheidung „Von/Nach zuerst“.
* **Keine Überschrift mehr vor dem Text ohne Satzende (2026-10-05)**: Auf
  der TV-Vorschau stand „Bahnsteigsanierung Wegen Sanierung des Bahnsteigs
  hält die Linie U6 …“, und der zweite Satz brach bei „mit der …“ ab. Eine
  WL-Überschrift aus einem einzigen Wort fiel nur weg, wenn das Wort in
  `_CATEGORY_PREFIX_WORDS` stand; jede andere lief ohne Punkt in den Text.
  `_close_blocks` lässt jetzt jede solche Überschrift weg, wenn Text
  dahinter folgt. Nachbau über 2.339 verschiedene WL-Meldungen seit Juli:
  16 Beschreibungen ändern sich, alle nur um die Überschrift (bei U6 Neue
  Donau, LCC-Herbstmarathon und Gleiswechselbetrieb U6 passt dadurch der
  ganze zweite Satz), keine anderen; ÖBB und Baustellen unverändert.
* **Feed-Reihenfolge: Aktuelle Störungen zuerst (2026-10-04)**: Laufende,
  ungeplante Störungen („[Seit hh:mm]“, seit höchstens 24 h im Feed,
  `_is_current_incident`) stehen jetzt vor allem anderen, auch vor neuen
  Ankündigungen, Baustellen und Hinweisen. Vorher sortierte allein
  `first_seen`: am 03.10. um 15:01 stand „43A: Veranstaltung“ vor „9A:
  Rettungseinsatz“, am 04.10. um 16:30 „U6: Neue Donau, kein Halt“ vor vier
  laufenden Störungen. Nachbau von 36 Feed-Ständen: in 14 nur andere
  Reihenfolge innerhalb der zehn Plätze, keine Meldung mehr oder weniger.
  Betreiberentscheidung („Aktuelle Störungen sollen oberste Priorität
  haben“). Wächter: `tests/test_current_incidents.py`.
* **Feed-Reihenfolge: Angekündigtes steht an seinem ersten Tag vor älterem Geplanten (2026-10-04)**:
  Der Feed sortiert nach `first_seen`, und eine Ankündigung ist lange vor
  ihrem Beginn bekannt. An ihrem ersten Tag zählte sie deshalb als Wochen
  alt: Die S-Bahn-Stammstrecke Phase 2 (gesehen 10.06.) stand am 07.09. auf
  keinem der zehn Plätze, die ÖBB-Sperre Wien Hbf–Gramatneusiedl (gesehen
  08.07., gültig 03.–05.10.) am 04.10. auf Platz 49 von 94. Jetzt merkt sich
  der Build den Beginn jedes Items, solange er bevorsteht (State-Feld
  `announced_start`, `_note_announced_starts`), und sortiert es ab diesem
  Beginn wie eine neue Meldung (`_sort_moment`), aber hinter den aktuellen
  Störungen; danach altert es wie jedes andere. Was erst ab seinem Beginn in den Daten steht, bleibt unberührt,
  so behalten die nächtlich neu ausgegebenen WL-Maßnahmen (66A) ihren Platz.
  Die Vortag-Regel (`_defer_upcoming_items`) bleibt. Nachbau mit
  weitergetragenem State (Wiener Zeit): Fr 21:00 bis Sa 10:30 durch diese Regel nur die ÖBB-Sperre neu auf
  Platz 1–4 ab Samstag 00:00, Mi 20:00 bis Do 11:30 unverändert.
  Wächter: `tests/test_announced_start.py`.
* **Dashboard auf dem Handy: Kopfzeile zweizeilig, DE | EN wieder erreichbar (2026-10-04)**:
  Die Kopfzeile von `site.html` hatte keine Handy-Darstellung. Unter rund
  1000 CSS-px stapelte die Navigation ihre Links senkrecht, die mitlaufende
  Kopfzeile war auf jedem Handy 232 px hoch (ein Drittel eines 667-px-Schirms,
  an jeder Scrollposition), und unter rund 530 px lag der Sprachumschalter
  rechts außerhalb des Bildschirms, wo `html { overflow-x: clip }` ihn
  abschnitt: Die englische Fassung war auf dem Handy nicht wählbar. Bis
  767 px ist die Kopfzeile jetzt zweizeilig (Marke, Wetter, DE | EN oben,
  die Navigation als eine waagrecht wischbare Zeile darunter), 87 px hoch;
  unter 400 px steht „Wien ÖPNV“ zweizeilig ohne Untertitel, unter 360 px
  nur das Logo. Sprungmarken landen unter der höheren Kopfzeile
  (`scroll-margin-top`). Dazu zwei Kleinigkeiten derselben Seite: Der
  Skip-Link ragte als dunkler 2-px-Streifen oben ins Bild (feste
  `top: -2.5rem` kürzer als der Link), und auf Handys sprang die
  Meldungsliste um eine Zeile, sobald der Datenstand den Platzhalter „–“
  ersetzte (die Zeile reserviert jetzt ihre geladene Höhe). Gemessen mit
  Playwright-Geräteemulation (320–1024 px, DE/EN, hell/dunkel) und Lighthouse
  Mobil: Leistung 97–100 vorher wie nachher, 155 KiB, 11 Anfragen; CLS
  0,018. Wächter: `tests/test_site_css_mobile_header.py`.
* **EN: Halte-Meldungen der WL mit Präposition (2026-10-04, Jules-Audits vom 02. und 04.10.)**:
  Der englische Feed zeigte „66A: Buses stop Salvatorianerplatz“ und „52:
  Trains stop for lines 6 and 18“; das Modell wählte die Präposition nach
  „Busse/Züge halten“ in jedem Lauf neu („at“, keine, „for“, „on“). Vor dem
  Glossar schreibt `_normalise_stop_verbs` die Formen jetzt um: ein Ort
  direkt nach dem Verb oder nach „bei“ wird „… stop at <Ort>“, die
  Haltestelle einer anderen Linie „… stop at the stop(s) of line(s)“, auch
  mit „der“/„den“, ohne „bei“ und in WL-Schreibvarianten („halte“, „LInie“).
  „in“, „auf“, „am“, „vor“, „nach“, „gegenüber“, „Richtung“ bleiben beim
  Modell. „Züge halten nicht“ heißt „trains do not stop“ (vorher „trains
  stop nicht …“). Geprüft an allen 348 „halten“-Formen der WL-Cache-Historie
  seit März. Jules' Vorschlag („Busse halten“ → „buses stop at“) hätte bei
  „bei“, „auf“, „in“ doppelte Präpositionen ergeben und wurde nicht
  übernommen. Übersetzungs-Epoche 21. Nur EN; der deutsche Feed bleibt
  unverändert. Tests: `tests/test_en_stop_verbs.py`.
* **Ausfall einer Quelle: Teilausfälle, kaputte Antworten, Demo-Baustellen (2026-10-04)**:
  Geprüft, indem veränderte Kopien der echten Rohdaten vom 04.10. 16:01 UTC
  (19 Ausfallarten) durch die echten Cache-Updater und den Feed-Build liefen.
  Ein vollständiger Ausfall war schon richtig (Cache bleibt, Feed unverändert,
  `first_seen` bleibt, der nächste gute Abruf stellt den Feed exakt wieder
  her). Drei Fehlerklassen behoben: (1) **Teilausfall**: Fiel eine der zwei
  WL-Listen oder einer der zwei Baustellen-Layer aus, wurde der Rest als
  vollständiger Cache geschrieben, mit Exit-Code 0 und grünem Health check;
  ohne `trafficInfoList` fehlten alle fünf laufenden Störungen auf den zehn
  Plätzen, ohne `newsList` vier Hinweise, ohne Linien-Layer zehn von 15
  Baustellen. Ein ausgefallener oder leerer Teil (wenn er vorher nicht auch
  leer war) kommt jetzt aus seiner letzten guten Antwort unter `data/raw/`
  (`raw_capture.read_snapshot`), Exit-Code 3; ohne letzte gute Antwort oder
  wenn alle Teile ausfallen, bleibt der Cache stehen. (2) **Antwort in anderer
  Form**: Fehlte ein Feld in allen Einträgen, lief der Abruf mit Ersatzwerten
  weiter – WL ohne `title` zeigte „15A: I20261004-0020“ auf allen zehn
  Plätzen, WL ohne `time` stempelte alten Meldungen `first_seen` und ließ fünf
  Plätze auch nach der nächsten guten Antwort falsch, ÖBB ohne `description`
  brachte sechs Baumeldungen aus dem Weinviertel, Baustellen ohne Datum neun
  Plätze. Solche Antworten gelten jetzt als unbrauchbar
  (`src/utils/source_shape.py`). (3) **Demo-Baustellen**: Ein ganz
  gescheiterter WFS schrieb die zwei Beispiel-Baustellen von 2025 über einen
  Cache mit zehn oder weniger Einträgen (heute 15); das Beispiel gilt nur noch
  ohne Cache. Mit dem neuen Code ergibt jeder der Fälle den Feed des guten
  Abrufs. Tests: `tests/test_source_outage.py`. Doku: `docs/architecture.md`
  („Ausfall einer Quelle“), `docs/development.md`, `data/raw/README.md`;
  Workflow-Warnung für Exit-Code 3 in `update-cycle.yml`, eigener Text im
  Health check.
* **WL: alle Haltestellenverlegungen und -auflassungen im Feed (2026-10-04, Betreiberentscheidung „Alle aufnehmen“)**:
  Eine solche Meldung kam nur durch, wenn ihr Grund auf „…arbeiten“ endete
  (13 der 37 Meldungen des Laufs von 14:00 UTC). Jetzt kommen alle: Der
  Stichwort-Filter kennt „verleg“ und „auflass“/„aufgelassen“. Dabei kamen
  drei weitere Fehler ans Licht, jeweils als Klasse behoben: (1) Die Regeln E
  und F entfernten eine Verlegung, deren Haltestelle eine andere Meldung als
  Richtung nennt („3A: Oper, Karlsplatz“ wegen „… in Richtung Oper,
  Karlsplatz“ bei Schellinggasse); sie entfernen eine Verlegung jetzt nur
  noch für eine Meldung derselben Haltestelle (`_covers_stop_notice`).
  (2) Verlegungen nennen ihren Zeitraum unter „Dauer:“, das nur „Zeitraum:“
  kannte: „26E/N20: Fultonstraße“ (ab 05.10., etwa zwei Wochen) hätte „[Bis
  28.09.2027]“ gezeigt, jetzt „[Ab Mo 05.10. bis 26.10.]“; 27 von 37 beginnen
  am Tag aus dem Text, sechs enden früher. Nur in Verlegungen, denn
  Störungen schreiben „Dauer: Bis 30.10.2026 Betriebsschluss“.
  (3) Schreibt WL die Linienliste ohne Doppelpunkt oder mit einem Namen aus
  zwei Wörtern, stand sie doppelt im Titel: „16A/N65: 16A, N65
  Grohnergasse“, „N25: N25, SEV U1: …“, „1/18/62/LB: 1, 18, 62, Badner Bahn:
  Signalstörung“ (10.09.), „3A: 3A Netzänderung …“ (04.10.)
  (`_strip_written_line_list`). Damit 24 neue Meldungen, manche seit 2023
  gültig, nicht alle zehn Plätze auf einmal belegen, zählt eine Verlegung,
  die der Build zum ersten Mal sieht, ab ihrer Veröffentlichung bei WL
  (`_initial_first_seen`). Feed-Nachbau über die zehn echten Läufe vom
  04.10. (09:31–14:00 UTC): zwei Verlegungen unter den ersten zehn
  („16A/N65: Grohnergasse“ bis Mo 05.10., „26E/N20: Fultonstraße“ ab Mo
  05.10.), dafür rückten je Lauf eine oder zwei andere auf Platz 11 und 12,
  meist „62: ÖBB Bauarbeiten“ und „26E: Gleisbauarbeiten“; sonst gleich. Tests: `tests/test_wl_stop_notices.py`
  mit allen 37 echten Meldungen. Doku: `docs/development.md` (WL-Filter,
  Sammel- und Teilmeldungen, Reihenfolge im Feed), `docs/architecture.md`
  (Linienliste, „Dauer:“).
* **Fünfte Prüfung: fälschlich verworfene Meldungen aus den Rohdaten (2026-10-04)**:
  Die Rohdaten des Abrufs (`data/raw/`, seit dem Vormittag) zeigten erstmals,
  was WL und ÖBB beim Abruf verlieren. Zwei Fehlerklassen behoben:
  (1) Der Stichwort-Filter der WL-Hinweise (`newsList`) las die Beschreibung
  als HTML, in dem WL Umlaute als Entities schreibt („kurz gef&uuml;hrt“),
  und kannte die Maßnahmen nur als Substantiv. „18: LCC-Herbstmarathon am
  11.10.2026“ (Linie 18 kurz geführt) und „U6: Neue Donau, kein Halt
  Richtung Floridsdorf“ (seit 14.09. bis Jahresende, die einzige Meldung mit
  Linie dazu) fehlten im Feed. Der Filter liest jetzt den Text ohne HTML
  (`_gate_text`) und kennt die Verben („kurz geführt“, „umgeleitet“,
  „durchfahren“, „kein Halt“, „kein Betrieb“, „eingestellt“, „entfällt“).
  Über alle 78 Hinweise der Rohdaten ändern sich genau diese zwei.
  (2) ÖBB schreibt Orte mit Zusatz („Mistelbach/Zaya“, „Wolkersdorf im
  Weinviertel“, „Traisen NÖ“), das Verzeichnis kennt die bloßen Orte; eine
  Route Wien ↔ Mistelbach galt als Wien ↔ unbekannt, die S2-Meldung „Wien
  Leopoldau ↔ Mistelbach“ (Schienenersatzverkehr 25.10./01.11.) fiel weg.
  `station_info` versucht zuletzt den Ort ohne Zusatz und nimmt ihn nur
  außerhalb Wiens. Über alle Namen der Rohdaten und alle Aliase ändern sich
  nur qualifizierte Namen; die 80 ÖBB-Meldungen im Cache seit Juli bleiben
  gleich. Außerdem: Mit `pytest -n 4` teilten sich die Worker das
  Streckendaten-Sample, einer löschte es, während andere noch liefen
  (4.190 Fehler in einem Lauf); ein Dateilock und ein Zähler lassen jetzt den
  letzten aufräumen. Tests: `tests/test_wl_news_gate_plain_text.py`,
  `tests/test_oebb_qualified_place_names.py`. Doku: `docs/development.md`
  (WL- und ÖBB-Filter, Test-Isolation), `docs/architecture.md` („Ortszusatz“).
* **Test-Suite: besteht in jeder Reihenfolge, ohne Netz und an jedem Datum (Prüfung 2026-10-04)**:
  Die volle Suite (11.002 Tests, seriell grün; jetzt 11.008) zusätzlich in Zufallsreihenfolge
  (`pytest-randomly`), ohne Netz (`unshare -rn`), mit verstellter Uhr
  (`time-machine`: beide Zeitumstellungen, Silvester, Neujahr, Schalttag 2028,
  2030) und mit `TZ` New York und Kiribati laufen lassen und 29 Regeln des
  Audits gezielt verfälscht. Behoben, jeweils als Klasse:
  31 Testdateien laden `src.build_feed` neu und ließen die Kopie in
  `sys.modules` stehen; danach setzten `reset_build_feed_state` und
  `time_line_today` die Kopie zurück statt des Moduls, das die übrigen Tests
  aufrufen. 14 Tests mit fester Zeitzeile hingen so am heutigen Datum (rot ab
  Neujahr), einer bestand nur dank Zustand eines früheren Tests
  (`test_collect_items_timeout_zero`). Zwölf Dateien rufen
  `importlib.reload` auf und ersetzten damit Klassen und Nonce des Moduls
  für alle späteren Tests (`GooglePlacesError` wurde nicht mehr gefangen,
  Platzhalter passten nicht mehr zum Muster); `_restore_replaced_modules`
  stellt Module und ihren Inhalt nach jedem Test wieder her. Ein Test mit
  `MAX_ITEMS=-5` und `refresh_from_env()` ließ `MAX_ITEMS` auf 0 stehen
  (`_feed_config_stays`). `configure_logging` setzte einen `SafeFormatter`
  auf pytests Capture-Handler, danach fehlte jedem Log-Record in
  `caplog.records` das Feld `message`. 23 Tests fragten das echte DNS
  (`example.com`, `safe.com`, `data.wien.gv.at`) und scheiterten ohne Netz;
  `_no_real_dns` beantwortet jede Abfrage fest. Drei SSRF-Tests mockten
  `socket.getaddrinfo`, das der Code nicht benutzt, und bestanden über das
  echte DNS; sie patchen jetzt den Resolver. Der Stationsupdate-Test lief mit
  echtem Netz, holte WL- und ÖBB-Daten und schrieb `cache/*/events.json` ins
  Arbeitsverzeichnis, und übersprang sich bei jedem Fehler; er läuft jetzt
  offline und scheitert, statt sich zu überspringen. Weiter: ein Test ließ den
  Stations-Regex der Testdaten im Cache stehen, Handler am Root-Logger
  blieben über Tests hinweg (`_root_logger_stays_clean`), zwei Hypothesis-Tests scheiterten an der
  200-ms-Frist des ersten Beispiels, ein Test rechnete „vor einer Stunde“ in
  Wiener Wanduhrzeit (rot am Morgen der Sommerzeit-Umstellung), ein Test
  verfiel am 31.12.2026, ein Sitemap-Test verglich das UTC-Datum des Codes
  mit dem lokalen Datum der Maschine (rot in Wien zwischen Mitternacht und
  1/2 Uhr). Der Stationsupdate-Test schrieb nach jedem Test
  `data/stations.json` neu (erst leeren, dann schreiben); ein paralleler Test
  las dabei eine leere Datei. Er ersetzt die Datei jetzt nur bei Änderung und
  in einem Schritt. Die 50-Zeichen-Grenze der Titel war nur relativ
  geprüft (mit 60 blieb die Suite bis auf einen Test grün); sechs Tests
  halten sie jetzt an der Kante. Doku: `docs/development.md`
  („Test-Isolation“). Tests: `tests/conftest.py`,
  `tests/test_display_title_length.py`.
* **Rohdaten und Verwürfe des Abrufs werden mitgeschrieben (2026-10-04)**:
  Die Caches hielten nur, was den Abruf überlebt hatte; ob eine Meldung bei
  WL, ÖBB oder Stadt Wien überhaupt ankam und wo sie verschwand, ließ sich
  nachträglich nicht klären. Der Update-Lauf schreibt jetzt mit
  `RAW_CAPTURE=1` je Quelle die Rohantwort und `verworfen.json` (Meldung und
  Grund) nach `data/raw/<quelle>/`. Die Dateien werden überschrieben, nur bei
  inhaltlicher Änderung, in stabiler Textform und ohne Felder, die sich bei
  jedem Aufruf ändern; die Git-Historie ist das Archiv. Komprimieren wurde
  verworfen: gemessen an einem Monat echter Historie wäre das Repo mit
  gzip-Dateien 1,6- bis 146-mal stärker gewachsen. Doku: `data/raw/README.md`,
  `docs/architecture.md` („Rohdaten und Verwürfe des Abrufs“). Tests:
  `tests/test_raw_capture.py`.
* **Update-Lauf checkt nur noch den letzten Stand aus (2026-10-04)**:
  `update-cycle.yml` lud alle ~30 Minuten die ganze Git-Historie
  (`fetch-depth: 0`, 55 MB, wachsend mit jeder committeten Cache- und
  Ledger-Version); jetzt `fetch-depth: 1` (6,6 MB, unabhängig von der Länge
  der Historie). Der Rebase vor dem Push braucht nur den ausgecheckten
  Commit als Basis, ein paralleler Push wird weiter sauber eingespielt
  (mit zwei flachen Clones getestet). Die Frische-Prüfung des Backup-Crons
  vertieft den Clone selbst um 50 Commits und wertet einen Treffer auf der
  flachen Grenze als „unbekannt“ (der Lauf findet statt), weil ein
  Grenz-Commit scheinbar jede Datei anlegt. Tests:
  `tests/test_update_cycle_shallow_checkout.py`.
* **Englischer Feed: ÖBB-Vorlage nimmt nur Stationsnamen in ihre Felder (2026-10-03, vierte Nachprüfung)**:
  Die ÖBB-Sätze, die seit dem EN-Audit (unten) aus ihren Feldern gesetzt
  werden, gegen alle 317 ÖBB-„Wegen“-Sätze des deutschen Feeds seit Juli
  gerechnet. Bei 10 der 286 erkannten Sätzen landete eine Abweichung von der
  Vorlage im Stationsfeld und stand deutsch im englischen Satz („… and
  Gramatneusiedl Bahnhof Zugfahrten bis voraussichtlich 27.08.2026, 23:59
  Uhr.“, „… at Wien Hbf (U) bzw Wien Meidling Zugfahrten erneut.“); bei 5
  weiteren blieb „Bahnhof“ vor der Gemeinde in Klammern stehen. Die Vorlage
  kennt jetzt Datum vor der Uhrzeit, „voraussichtlich bis“ und
  „[in <Gemeinde>]“; ein Stationsfeld mit Ziffer oder Wörtern wie
  „Zugfahrten“, „bis“, „bzw“ gilt als Abweichung, der Satz geht dann als
  Ganzes durchs Modell (4 Sätze). Keine neue Übersetzungs-Epoche: Unter
  Epoche 20 ist keiner dieser Sätze im Cache. Doku: `docs/architecture.md`
  („ÖBB-Sätze nach Vorlage“). Tests: `tests/test_en_audit_2026_10_03.py`.
* **Englischer Feed: Prüfung aller DE/EN-Paare (EN-Audit 2026-10-03)**: Alle
  6.561 Stände von `docs/feed.en.xml` seit 20.05. Meldung für Meldung neben
  den deutschen gelegt (4.957 Paare). Behoben, jeweils als Klasse:
  WL-Ursachen ohne Glossar-Eintrag („Fremder Verkehrsunfall“ als „foreign“,
  „external“ oder deutsch; „Stromstörung“ als „current disturbance“;
  „Weichenschaden“ als „Soft damage“; „im Haltestellenbereich“ deutsch);
  der `Grund:` eines Label-Records blieb bei jedem unbekannten Wort deutsch,
  weil der Record nie das Modell sah, jetzt rendert ihn das Glossar oder
  das Modell; „Voraussichtliche Dauer:“ ist ein Label („Expected duration:“
  statt mal „Duration:“, mal „Expected Duration:“); volle Stunden („13 Uhr“)
  wurden zur bloßen Zahl („Expected Duration: 13.“), jetzt „13:00“;
  Eigennamen übersetzt („Südtiroler Platz“ → „South Tyrolean square“,
  „Währinger Gürtel“ → „moving belt“, „Schloßhofer“ → „Schlosshofer“,
  „Allianz Stadion“ → „Alliance Stadion“, „Wien Hbf“ → „Vienna Hbf“,
  „Burggasse, Stadthalle“ → „city hall“, „Haltestelle N71“ → „N71“);
  ÖBB-Sätze verloren das Verb („… to 23:48 were not possible.“), jetzt aus
  ihren Feldern gesetzt; die WL-Empfehlung „Weichen Sie ersatzweise auf die
  Linien … aus“ ließ Straßenbahn D am 01.10. 49 Läufe lang deutsch stehen.
  Übersetzungs-Epoche 20 räumt den Cache. Details: `docs/architecture.md`
  („Was am Modell vorbeigeht“, „Die Platzhalter“). Tests:
  `tests/test_en_audit_2026_10_03.py`.
* **Health check: stille Stammstrecken-Richtung nur noch als Hinweis (2026-10-03)**:
  Der Health check war seit 13.09. bei jedem Lauf rot (alle 6 Stunden eine
  Fehler-Mail), weil Richtung Praterstern wegen der bekannten Sperre seit 14.08.
  keine Messwerte liefert (`docs/archive/audits/audit-2026-09-26.md`). Eine echte
  Störung hätte genauso ausgesehen. Betreiberentscheidung: Der Hinweis auf der
  Website genügt; der Richtungs-Check schreibt eine stille Richtung nur noch als
  Hinweis (ℹ️, `::warning`) und lässt den Lauf grün. Rot bleibt er nur, wenn
  die Prüfung selbst nicht laufen kann. Doku: `docs/development.md`. Tests:
  `tests/test_stammstrecke_direction_health.py`.
* **Deutscher Feed: ÖBB-Störung zur vollen Stunde zeigt ihr „[Seit …]“ (2026-10-03, dritte Nachprüfung)**:
  „Wien Meidling ↔ Wien Liesing“ (Polizeieinsatz, veröffentlicht 20:00:01,
  „bis voraussichtlich 20:30 Uhr keine Fahrten“) stand eine Stunde lang mit
  „[Heute]“ im Feed, die erste ÖBB-Störung seit der Korrektur vom Nachmittag (unten). Die Regel
  der vollen Stunde, die von WL vorab eingetragene Maßnahmen erkennt, galt auch
  für die erste ÖBB-Veröffentlichung; ÖBB veröffentlicht von Hand, und beide
  ÖBB-Störungen seit Juli zur vollen Stunde waren echte Vorfälle. Für ÖBB gilt
  sie nicht mehr. WL und Stammstrecke unverändert. Doku: `docs/statistik.md`
  zeigt das Kalenderjahr, nicht 30 Tage (`docs/architecture.md` §6,
  `docs/development.md`, `docs/reference/stammstrecke_provider_logic.md`). Tests: `tests/test_time_line_incident_since.py`.
* **Zeitumstellung bis 2040, Jahreswechsel, Schaltjahre (Prüfung 2026-10-03)**:
  Echte Feed-Läufe auf jede Zeitumstellung bis 2040, jeden Silvester 2026 bis
  2040 und die Schalttage 2028 bis 2040 verschoben (49 Szenarien, deutscher und
  englischer Feed); bis auf die verschobenen Daten unverändert. Behoben: Ein
  WL-Ende ohne Jahr („Ab 01. Mai 2026 bis Ende Dezember“) wurde unabhängig vom
  Beginn auf das nächstgelegene Datum gelegt und konnte vor dem Beginn landen;
  es ist jetzt der erste solche Tag ab dem Beginn im Text.
  `generate_markdown_stats.py` nimmt das Jahr aus `--now-iso` statt von der
  Uhr der Maschine. Neu: Jahresarchiv der Statistik, der erste Dashboard-Lauf
  eines Jahres schreibt das ganze Vorjahr samt Silvester nach
  `docs/statistik-<Vorjahr>.md`. Details: `docs/architecture.md` („Folgejahre,
  Jahreswechsel, Schaltjahre“). Tests: `tests/test_calendar_years.py`.
* **Zeitumstellung am 25.10.2026 (Prüfung 2026-10-03)**: Die echte Nacht vom
  26./27.09. in die Nacht des 25.10. verschoben und durch den Wechsel gespielt,
  dazu die Umstellung auf Sommerzeit. Zeitpunkte werden jetzt in UTC verglichen
  und Stunden in UTC abgezogen: In der doppelten Stunde zeigte eine Störung von
  02:26 Sommerzeit um 02:01 Winterzeit „[Heute]“ statt „[Seit 02:26]“
  (`format_local_times`, `_plausible_end`); das Ein-Stunden-Fenster der
  Stammstrecke umfasste am 25.10. zwei Stunden und war am Morgen der
  Sommerzeit-Umstellung eine Stunde lang leer; der Monitor zählte einen Zug,
  der um 01:55 fällig war und um 03:05 Sommerzeit fuhr, 70 statt 10 Minuten
  verspätet. ÖBB-`pubDate` tragen den Offset des Abrufs statt den der
  Veröffentlichung (alle 38 Wintermeldungen seit 29.03. mit +02:00); die Uhrzeit
  gilt jetzt als Wiener Ortszeit. Details: `docs/architecture.md`
  („Zeitumstellung“). Tests: `tests/test_clock_change.py`.
* **Deutscher Feed: ÖBB-Störungen zeigen ihr „[Seit …]“ (2026-10-03, Nachprüfung von #1930 bis #1934)**:
  Die Seit-Zeit aus #1931 kam bei ÖBB nie an. `_incident_since` las die
  Uhrzeit aus `starts_at`, doch der ÖBB-Provider setzt dort seit 12.09. den
  Gültigkeitstag (00:00), und 00:00 zeigt keine Uhrzeit. Alle 29 Stände
  laufender, heute endender ÖBB-Störungen vom 12.09. bis 02.10. lasen
  „[Heute]“ statt zum Beispiel „[Seit 15:48]“. Der Beginn ist jetzt die erste Veröffentlichung der
  Meldung (`_first_published`), gemerkt im State als `first_published`,
  weil ÖBB eine Meldung bei jeder Aktualisierung mit neuem `pubDate`
  veröffentlicht (21 der 33 Meldungen); sonst würde die Störung mit jeder
  Aktualisierung jünger. „Reparaturarbeiten“ gelten nicht mehr als geplant
  („Wegen Reparaturarbeiten nach einem Unfall“), sie folgen auf einen
  Vorfall. Tests: `tests/test_time_line_incident_since.py`.
* **Deutscher Feed: Filterprüfung (2026-10-03)**: Jede Filterstufe gegen die
  WL-, ÖBB- und Baustellen-Caches seit 15.07. geprüft und alle 3.878 Feed-Stände
  mit altem und neuem Code nachgebaut (State fortgeschrieben).
  - **Badner Bahn fehlte ganz:** `_post_filter_wl` verwirft eine Störung ohne
    erkennbare Linie, und `LB` (bis Juni 2026 `WLB`) galt nicht als Linie. So
    fielen alle 15 Badner-Bahn-Störungen seit 15.07. weg, samt Straßenbahnen
    desselben Vorfalls (`1/18/62/LB: Signalstörung`, 10.09.). Das Linien-Gate
    kennt jetzt `LB`/`WLB` und die Rufbus-Form `44BR`. Nachgerechnet: 213 Plätze
    für Badner-Bahn-Meldungen, keine Doppelten; die nächtlichen Tafelmeldungen der
    Sommerbaustelle gehen weiter in deren Hinweis auf, sobald er erscheint.
  - **Testmeldungen:** Auch „Testfall“ („38: Testfall“, „72A: Testfall“),
    „Testtext“ und beliebig lange „Test Test Tes Test …“ gelten als Testmeldung.
    In der ganzen Cache-Historie trifft die Regel 26 Titel, alle Tests.
  - **WL-Sammel- und Teilmeldungen (E, F in `wl_fetch`):** Beide Regeln
    entfernten eine Meldung allein nach Linien, Kategorie und Zeitraum. Seit
    April verschwanden so 108 Meldungen, die etwas anderes sagten („2A:
    Bauarbeiten Renngasse“ zehn Tage neben der Regenbogenparade, „11A:
    Gleisbauarbeiten Stadion U“ neben einer Veranstaltung). Jetzt fällt eine
    Meldung nur, wenn die andere sie inhaltlich abdeckt oder (F) sie deren Thema
    nennt; das hält die Anzeigetafel-Kurzmeldungen großer Baustellen weiter
    draußen. Nachgerechnet: 640 Plätze in 643 Feed-Ständen gehen an solche
    Meldungen. Tests: `tests/test_wl_aggregate_removal.py`.
  - Ohne Änderung: WL-Störungen ohne jede Linie (67 seit 15.07., 46 ohne andere
    Meldung zum selben Vorfall) bleiben draußen, bis der Betreiber entscheidet;
    Baustellen, die die WL schon melden, Build-Dedupe und Fuzzy-Merge zeigten
    keine fälschlich verworfenen Meldungen.
* **Deutscher Feed: Vorlauf über das Wochenende (2026-10-03, Betreiberentscheidung „Wochenende mit“)**:
  Ankündigungen rücken weiter ab dem Vortag ihres Beginns vor, freitags und
  samstags aber schon alles, was bis einschließlich Montag beginnt
  (`_preview_last_day`). Wer die Anzeige nur werktags sieht, liest die
  Umleitung ab Montag so noch am Freitag. Nachgerechnet über die 3.875
  Feed-Stände seit 15.07.: Ankündigungen belegen 1.064 Plätze, während
  Laufendes auf Platz 11 oder dahinter stand (nur Vortag: 472, drei Tage:
  1.442). Von den 1.442 Plätzen der drei Tage fielen 827 auf Freitag und
  Samstag; die Regel gibt diese fast alle wieder an Ankündigungen (825).
  Tests: `tests/test_upcoming_deferral.py`.
* **Deutscher Feed: Wer die zehn Plätze bekommt (2026-10-03, Audit über die Cache-Historie)**:
  Alle 3.875 Feed-Stände seit 15.07. mit dem Code von `main` aus Cache und
  State des jeweiligen Stands nachgebaut (Abgleich mit dem veröffentlichten
  Feed: 33 der letzten 40 Stände identisch, der Rest durch Änderungen seit
  dem Build) und die zehn Plätze nach Ursache ausgezählt.
  - **Rang 1, Doppelte:** WL schickt auch die ausführliche Meldung einmal je
    Linie („6: Schadhaftes Fahrzeug“ und „18: Schadhaftes Fahrzeug“, gleicher
    Text, gleiche Minute; viermal „Störung an einem Bahnübergang“ für 16A,
    17A, 67A, 67B). Das nahm in 540 Ständen 808 Plätze. Gruppen, die Meldung
    für Meldung dasselbe sagen, verbinden sich jetzt auch mit ausführlicher
    Meldung (`_join_twin_groups`): „6/18: Schadhaftes Fahrzeug“. Danach
    bleiben 9 solche Plätze.
  - **Rang 1, fälschlich verworfen:** Eine Baustelle der Stadt Wien mit
    reinem Enddatum fiel um 00:10 ihres letzten Tages aus dem Feed, die
    Zeitzeile hatte „Bis Fr 04.09.“ gezeigt (alle 13 Baustellen, die bis zu
    ihrem Ende liefen). Sie gilt jetzt bis zum Ende dieses Tages
    (`_valid_until`).
  - **Reihenfolge, Betreiberentscheidung 2026-10-03:** Ankündigungen rücken ab dem
    Vortag ihres Beginns vor statt drei Tage vorher
    (`UPCOMING_PREVIEW_DAYS` 3 → 1). Mit drei Tagen belegten sie 1.442
    Plätze in 1.083 Ständen, jedes Mal mit einer laufenden Meldung auf
    Platz 11 oder dahinter.
  - **Reihenfolge, Betreiberentscheidung 2026-10-03:** Eine geplante WL-Maßnahme,
    die WL nach der Nachtpause neu ausgibt („66A: Busse halten
    Salvatorianerplatz“, täglich 04:40 bis 01:00), behält ihr erstes
    Auftreten, wenn sie binnen 36 Stunden wiederkommt
    (`_PLANNED_OCCURRENCE_GAP`). Mit 2 h zählte sie jeden Morgen als neu und
    stand seit 26.09. in 370 von 387 Ständen vorn. Störungen wie ein
    Rettungseinsatz zählen weiter nach 2 h als neu.
  - Tests: `tests/test_wl_twin_tickers.py`, `tests/test_baustellen_last_day.py`,
    `tests/test_upcoming_deferral.py`, `tests/test_recurring_wl_occurrence.py`.
* **Deutscher Feed: Eine Störung sagt, seit wann sie läuft (2026-10-03, Betreiberwunsch)**:
  „86A/87A/95A: Fahrtbehinderung wegen Rettungseinsatz“ zeigten „[Heute]“;
  jetzt steht „[Seit 10:37]“ da, die Minute, in der WL den Einsatz
  eingetragen hat (`pubDate`, die früheste der gebündelten WL-Meldungen).
  `starts_at` (10:42 bis 10:54) ist die jüngste davon, die Kurzmeldung der
  einzelnen Linie, und bleibt unverändert. Das gilt für jede Störung (WL,
  ÖBB, Stammstrecke), die heute begonnen hat und heute endet oder kein Ende
  hat. Geplantes (Veranstaltung, Demonstration, Arbeiten, Staatsbesuch,
  Übung, verlegte Haltestelle), Maßnahmen, die WL zur vollen Stunde
  einschaltet, ÖBB-Datumsbeginne (00:00; korrigiert, siehe oben: bei ÖBB
  zählt die erste Veröffentlichung) und Meldungen über eine schon
  beendete Störung („… waren bis 19:55 Uhr keine Fahrten möglich“,
  „Aufhebung …“) behalten ihre Zeile. Über die Cache-Stände seit Juli ändern
  sich 1 918 von 2 529 WL- und 75 von 193 ÖBB-Zeitzeilen. Die
  Stammstrecken-Meldung trägt ihr eigenes „[Seit 09.08.2026]“ nicht mehr in
  der Beschreibung, das stand doppelt über der Zeitzeile. EN: „[Since 10:37]“.
* **Feed-Texte: St.-Abkürzung, Leerzeichen vor Satzzeichen, Ortsangabe ohne Ort (2026-10-03, Nachprüfung der Änderungen vom 02.10.)**:
  Alle 2.126 verschiedenen Meldungen aus der Cache-Historie (WL, ÖBB, Stadt
  Wien) neu gerendert und gegen den Stand von `main` verglichen; 23
  Meldungen ändern sich, alle gewollt.
  - **Rang 2, verstümmelte Texte:** ÖBB schreibt „St.Pölten Hbf“,
    „St.Andrä-Wördern“, „St.Marx“ ohne Leerzeichen; der Titel derselben
    Meldung hieß „St. Pölten Hauptbahnhof“. `repair_saint_abbreviation`
    (`src/utils/text.py`) setzt das Leerzeichen in Titel und Text; nur
    „St.“ vor einem Großbuchstaben, „Last.Ende“ bleibt unberührt.
  - **Rang 1 und 2, WL-Kurzmeldungen:** Ein Leerzeichen vor Komma oder
    Semikolon („Betrieb ab Enkplatz , Grillgasse“) entfällt vor jedem
    Vergleich. In einer Meldung machte es Titel-Folge und Text ungleich, und
    derselbe Satz stand zweimal („Betrieb ab Ring , Volkstheater U. Betrieb
    ab Ring, Volkstheater U“). Ein Leerzeichen vor einem Punkt („12:15
    Uhr .“) entfällt ebenso, und „im Bereich .“ ohne Ort fällt ganz weg
    (`_SPACE_BEFORE_COMMA_RE`, `_SPACE_BEFORE_PUNCT_RE`, `_EMPTY_PLACE_RE`).
  - Tests: `tests/test_feed_text_hygiene_2026_10_03.py`.
* **Deutscher Feed: verklebte Wörter, Absatzenden, EN-Platzhalterreste (2026-10-02, Fahrgast-Durchlauf, #1929)**:
  Zeilenumbruch-Steuerzeichen (VT, FF, NEL, U+2028/2029) werden im
  Cache-Scrub und in `_sanitize_text` zu einem Leerzeichen statt gelöscht;
  `repair_glued_words` erkennt auch „Derlinke“, „Außerhalbder“, „zuden“.
  WL-Absatzenden sind Satzenden (`html_to_text(mark_block_ends=True)`,
  `_close_blocks`): Überschrift und Felder laufen nicht mehr ineinander, eine
  Überschrift, die den Titel wiederholt, und „Zeitraum:“/„Dauer:“-Absätze
  entfallen, die Folge einer Kurzmeldung endet mit Punkt, ein doppelter Punkt
  aus der Quelle wird einer. EN: ein `X` vor einem Platzhalter wird wieder das
  Leerzeichen („WipplingerstrX39“ → „Wipplingerstr 39“), Übersetzungs-Epoche 19.
* **Deutscher Feed: Die Zeitzeile zeigt das Kalenderdatum (2026-10-02, Betreiberentscheidung)**:
  Die Regel aus #1927, nach der ein Ende zwischen 00:00 und 05:00 zum
  Betriebstag davor zählte, ist zurückgenommen. Datum und Wochentag sind
  wieder die realen nach Wiener Zeit (Europe/Vienna): „66A: Busse halten
  Salvatorianerplatz“, laut WL bis 03.10. 01:00, zeigt „[Bis Sa 03.10.]“.
* **Deutscher Feed: Was jetzt gilt, steht vorn, und die Zeitzeile sagt, was für heute zählt (2026-10-02, Betreiberentscheidung)**:
  - **Rang 1, verdrängte Meldungen:** Am 02.10. um 18:00 belegten fünf der
    zehn Plätze Meldungen, die noch nicht begonnen hatten („20A:
    Bauarbeiten“ ab 13.10., die R 40 ab 31.10.); „N71: Ersatzverkehr“ und
    „62: ÖBB Bauarbeiten“, beide an diesem Abend gültig, standen auf 11 und
    12. `_defer_upcoming_items` stellt jetzt alles hinter das Feld, was erst
    nach `UPCOMING_PREVIEW_DAYS` (Standard 3) Wiener Kalendertagen beginnt.
    Gelöscht wird nichts.
  - **Zeitzeile:** „[01.10.2026 – 02.10.2026]“ wird „[Heute]“,
    „[30.09.2026 – 15.11.2026]“ wird „[Bis 15.11.]“, „[05.10.2026 –
    11.11.2026]“ wird „[Ab Mo 05.10. bis 11.11.]“. Jahr nur außerhalb des
    laufenden, Wochentag nur in den nächsten sieben Tagen, keine Uhrzeit.
    Ein fernes Ende zählt ab heute statt ab dem Beginn („N8: Thaliastraße U“
    zeigt „[Bis 16.11.]“ statt „[Seit 24.07.2024]“). EN übersetzt Wort für
    Wort („[From Mon 05.10. until 11.11.]“).
* **Deutscher Feed: kürzere Titel, Ziel 50 Zeichen (2026-10-02, Betreiberwunsch, #1923, #1925)**:
  Ein Titel über 50 Zeichen wird beim Rendern an seinen Fugen gekürzt
  (`_display_title`), nie abgeschnitten, und erst nach Dedupe und Merges.
  ÖBB: eine Strecke, die auf einer anderen derselben Meldung liegt, wird
  nicht mehr aufgeführt (`_drop_contained_routes`); Linienlisten stehen ohne
  Leerzeichen („R40/REX41/REX4/S40“). Baustellen: Hausnummern als Adresse
  („Rennweg 33A–37“); ein Titel über 50 Zeichen behält die Straße, der
  Abschnitt steht als Etikett vor der Beschreibung. Ein WL-Titel verliert ein
  ausgeschriebenes Startdatum („ab 07. April 2026“), die Zeitzeile nennt es.
* **WL: Anzeigetafel-Meldungen einer Baustelle gehen im Hinweis auf (2026-10-02, #1920)**:
  „D: Gleisbauarbeiten [Am 01.10.2026]“ stand für Arbeiten bis 07.11. im Feed;
  12A, 25, N20, N49, 37A und 74A ebenso, jede Nacht neu. `_absorb_works_tickers`
  nimmt eine WL-Störung mit einem Vorfall in den Hinweis auf, wenn er ihre
  Ursache nennt, ihre Linien abdeckt, ihre Gültigkeit umfasst und eine ihrer
  Straßen nennt.
* **WL: Dauerangabe statt 11:11-Ende, mit Puffer (2026-10-02, #1919)**:
  Nennt „Zeitraum:“ nur eine Dauer („etwa sechs Wochen“), ersetzt Start plus
  Dauer plus Puffer (halbe Dauer, mindestens eine Woche) ein 11:11-Ende von WL.
  Die Rechnung verkürzt nur, sie verlängert nie.
* **Fehlersuche vom 02.10. (2026-10-02, #1918)**: Eine Anzeigetafel-Meldung,
  die WL je Linie sendet („N65“, „N66“), belegt einen Platz statt zwei. Ein
  Bindestrich-Kompositum mit „Test“ („Test-Fahrten“) ist keine Testmeldung.
  EN: Platzhalterreste im Cache gelten als Cache-Fehlgriff, das Feld wird neu
  übersetzt. Stammstrecke: eine neue Episode nach einer Lücke im Ledger
  bekommt ihren eigenen Beginn.
* **WL: Plausibilitätsprüfung für den Zeitraum (2026-10-02, #1916)**: Die drei
  Zeitangaben einer WL-Meldung (`time.*`, „ab“/„am“ im Titel, „Zeitraum:“)
  werden gegeneinander abgewogen (`src/providers/wl_plausibility.py`);
  Korrekturen landen in `data/wl_plausibility_anomalies.json`. Ein
  Tippfehler wie „am 04.10.2027“ schob eine Meldung ein Jahr in die Zukunft.
* **WL: Zeitraum-Befunde (2026-10-01/02, #1912–#1915)**: Ein Hinweis beginnt
  laut „am“ im Titel oder „Zeitraum:“, nicht am Veröffentlichungstag; ein
  11:11-Ende weicht dem Ende aus „Zeitraum:“; ein gebündelter Hinweis beginnt
  beim frühesten Beginn seiner Phasen; der Fuzzy-Merge behält die Gültigkeit
  beider Meldungen; ohne `time.start` gilt das Ende nicht mehr als Beginn.
* **Stations-Diff-Bericht und Dashboard (2026-10-02, #1921, #1922)**: WL-Einträge
  werden per DIVA statt per Name zugeordnet (acht Namen gehören zwei DIVAs);
  der Abdeckungshinweis steht im Kopf von „Aktuelle Meldungen“.
* **Deutscher Feed: ausführliche WL-Meldung behält die Folgen ihrer Kurzmeldungen (2026-10-01)**:
  Eine ausführliche Meldung steht weiter für ihre Kurzmeldungen. Was
  Kurzmeldungen derselben Ursache (oder ohne Ursache) zusätzlich sagen,
  bleibt jetzt erhalten, innerhalb des 10-Minuten-Fensters und darüber
  hinaus (`_with_consequences`): „64A: Verkehrsunfall“ über „Betrieb ab
  Mühlbreiten. Unregelmäßige Intervalle …“, bei der 48A „Shuttlebus
  eingerichtet, Abfahrtsstelle: Haltestelle Linie 46!“. Die Zuordnung
  `_claimed_by_long_messages`, die solche Kurzmeldungen außerhalb des
  Fensters verwarf, entfällt; „Eine Linie, ein Platz“ deckt sie ab. Ein
  bloßes „Fahrtbehinderung“ hängt nie mehr an (auch ein Fehler aus #1909).
  Audit vom 01.10., offener Punkt 1 (Update 20:04).
* **Deutscher Feed: ÖBB-Präfix nennt alle betroffenen Linien (2026-10-01)**:
  Live stand „REX 41: Wien Franz-Josefs-Bahnhof ↔ …“ über „… keine R
  40-Züge fahren“. Die Meldung betrifft R 40, REX 41, REX 4 und S 40; `R`
  fehlte im Muster, und nur die erste erkannte Linie kam ins Präfix. Jetzt:
  „R 40/REX 41/REX 4/S 40: …“ (`_affected_lines`); eine Meldung nur zur R 95
  bekommt „R 95: …“. Audit vom 01.10., offener Punkt 3.
* **Deutscher Feed: eine Linie, ein Platz (2026-10-01, Betreiberentscheidung)**:
  „Mehrere Störungsmeldungen zur selben Linie sollte so gut wie möglich
  zusammengefasst werden“; verschiedene Linien behalten ihre Einträge.
  - **Rang 2, verstümmelter Titel:** `deduplicate_fuzzy` verbindet zwei
    WL-Störungen derselben Linien nicht mehr mit „&“ (`_may_merge`); die
    WL-Zusammenlegung übernimmt sie. Live am 01.10.: „60: Schadhafter Pkw &
    Schadhafter Pkw Betrieb ab Anschützgasse“, jetzt „60: Schadhafter Pkw“
    über „Fahrtbehinderung; Betrieb ab Anschützgasse.“
  - **Rang 1, Plätze:** WL-Störungen derselben Linien, deren Gültigkeit
    sich überschneidet, werden ein Eintrag. Bei einer Ursache ist das ein
    Vorfall über das 10-Minuten-Fenster hinaus, WLs Synonyme eingeschlossen
    („Schadhafter Zug“ und „Schadhaftes Fahrzeug“). Mehrere Ursachen stehen
    in einem Eintrag, die neueste zuerst und mit deren GUID: „66A:
    Rettungseinsatz, Bauarbeiten“. Rückschau über 750 Cache-Stände: 1.727
    Einträge weniger und kein Stand mit mehr Einträgen.
  - Folgen mit gleichem Anfang nennen ihn einmal: „Busse halten
    Bessemerstraße 1-3, auf Hauptfahrbahn, Hoßplatz 11“.
  - Audit vom 01.10., Update 20:04; `docs/architecture.md` §1, „Eine Linie,
    ein Platz“.
* **Sicherheit: Übersetzungsmodell auf eine Revision festgelegt (2026-10-01)**:
  Der Feed lädt `Helsinki-NLP/opus-mt-de-en` fest in der Revision
  `1a922f3b`, die der Update-Zyklus am 01.10. um 17:01 geloggt hat
  (`_TRANSLATION_MODEL_REVISION`). Ein späterer Push in das
  Modell-Repository erreicht den Build nicht mehr (CVE-2026-80047,
  Schritt 2). Die Übersetzungen ändern sich nicht. Audit vom 01.10., Update
  17:17.
* **Sicherheit: CVE-2026-80047 in `transformers` bewertet (2026-10-01)**:
  `pip-audit` meldet für `transformers` 4.57.6 eine neue Schwachstelle ohne
  korrigierte Version. `load_custom_generate()` schreibt eine Code-Datei
  aus dem Modell-Repository in den Cache, bevor `trust_remote_code` geprüft
  wird; ausgeführt wird sie nicht. Der Pfad läuft bei jedem Laden unseres
  Modells mit. Die Meldung steht mit Begründung auf der Ausnahmeliste in
  `scripts/run_static_checks.py`. Die Logzeile beim Laden nennt jetzt den
  Commit der geladenen Modell-Revision, damit das Modell im nächsten
  Schritt darauf festgelegt werden kann. Audit vom 01.10., Update 13:20.
* **Deutscher Feed: Fehler aus der Feed-Historie (2026-10-01)**: Befunde im
  Audit vom 01.10.
  - **Rang 1, Duplikat:** WL-Kurzmeldungen außerhalb des 10-Minuten-Fensters
    belegen keinen eigenen Platz mehr, wenn eine ausführliche Meldung
    derselben Linien dieselbe Ursache nennt und gleichzeitig gilt
    (`_claimed_by_long_messages`). „D: Gleisbauarbeiten“ stand vom 30.09.
    bis 01.10. zweimal im Feed.
  - **Rang 1, verworfene Meldungen:** ÖBB-Strecken nach „Bruck/Leitha“,
    „Tulln/Donau“ und drei weiteren Pendler-Bahnhöfen in Schrägstrich-Form
    galten als unbekannt, die Meldung fiel weg. `station_info` versucht
    jetzt „an der“/„am“. Die Beschreibungsroute endet vor „Zugfahrten“;
    unter einem Titel ohne Route wären sonst 15 von 73 ÖBB-Meldungen der
    Historie weggefallen.
  - **Rang 2, Titel:** „Wien Hauptbahnhof ↔ Wien Hauptbahnhof“ (29.09.) und
    „Wien Hauptbahnhof ↔ Tullnerfeld“ (13.09., 17.09.) meinten Wien
    Meidling: „Wien Meidling Bahnhof (U)“ traf eine WL-Haltestelle, deren
    „Bhf.“ dann als Satzende gekürzt wurde. Ein WL-Titel nur aus Linien
    („D/1/2/71/1A/3A: D, 1, 2, 71, 1A, 3A“) nimmt die Überschrift der
    Beschreibung („Demonstration“). Strecken von einem Knoten nennen ihn
    einmal (REX 41: 144 → 109 Zeichen).
  - **Rang 3, EN:** ein wiederholter Platzhalter-Index
    („Wien Franz-Josefs-Bahnhof0X“) wird repariert.
* **Dokumentation geprüft (2026-09-27)**: Die Regeln dafür, was im
  deutschen Feed steht (Ticker-Titel, „Ein Vorfall, ein Platz“,
  „Fahrtbehinderung <Ursache>“, „ÖBB-Ersatzbus“), stehen jetzt in
  `docs/architecture.md` §1 statt im Übersetzungskapitel, und das
  Ablaufdiagramm zeigt die WL-Zusammenlegung. Korrigiert:
  - die Herkunft der Secrets (systemd Credentials, Docker Secrets,
    Umgebung);
  - die Liste der Prüfungen von `python -m src.cli checks` und der
    Pre-Commit-Hooks;
  - die Testzahl.

  Neu beschrieben: die Test-Isolation, `health-check.yml` samt
  `scripts/health_check.py`, `probe-hafas-lines.yml` und die
  Claude-Workflows. Befunde und offene Punkte stehen im Audit vom 27.09.,
  Update 12:20.
* **Betrieb: Quota-Pre-Flight im manuellen Full Refresh, Test-Isolation
  (2026-09-27)**: `manual-full-refresh.yml` prüft vor der
  Stammstrecken-Abfrage das VAO-Tagesbudget wie `update-cycle.yml` und
  überspringt die Abfrage, wenn es ausgeschöpft ist (Audit vom 25.09., A.6).
  Tests, die `main()` ausführen, schrieben den Health-Report in das echte
  `docs/`: drei, weil sie die Pfade nicht umleiteten, und vier weitere in
  der vollen Suite, weil sie die Writer über den String-Pfad patchten und
  ein früherer Test das Modul neu geladen hatte. Die drei leiten die Pfade
  jetzt um, alle fünf Tests mit String-Patch patchen mit `patch.object`,
  und ein Wächter in `tests/conftest.py` lässt jeden Test scheitern, der es
  wieder tut (A.7).
  Veraltete Kommentare, der Health-Report werde veröffentlicht, sind
  korrigiert: `.gitignore` schließt ihn aus.
* **EN-Feed: zweiter Übersetzungsversuch mit frischer Nonce (2026-09-27)**:
  Scheitert ein Feld, weil das Modell einen Platzhalter verstümmelt oder
  verloren hat, läuft es einmal mehr unter einer frisch gewürfelten Nonce
  (Audit vom 25.09., A.5). Am 26.09. um 17:01 blieben mit der Nonce
  `c3ed7873665b7570` drei Zusammenfassungen deutsch, darunter WLs
  Standardsatz, den andere Läufe übersetzt hatten. Masken und Zuordnung
  behalten die Nonce des Laufs; nur das Modell sieht die neue. Die Logzeilen
  eines gescheiterten Durchlaufs nennen jetzt dessen Nonce. Der deutsche
  Feed ändert sich nicht.
* **WL-Zusammenlegung: ausführliche Meldung und Beginn korrigiert (2026-09-27)**:
  Die Zusammenlegung vom 26.09. erkannte die ausführliche WL-Meldung am
  Präfix „Linie 48A:“, das der Feed beim Lesen des Caches schon entfernt
  hat. Live stand deshalb „48A: Falschparker“ über „Grund: Fremder
  Verkehrsunfall“. Jetzt wird sie an ihren Sätzen erkannt; sagt sie mehr
  als den Standardsatz, steht sie so, wie sie allein erschiene, und die
  Kurzmeldungen der Gruppe belegen keinen eigenen Platz. Der Eintrag
  übernimmt den Beginn der zuerst veröffentlichten Meldung statt des
  frühesten, sonst stand über einem Schaden vom 27.09. „23.09.2026 –
  27.09.2026“ (die Wiener Linien hatten eine alte Tafel-Meldung
  wiederverwendet).
* **Ein WL-Vorfall belegt nur noch einen Platz im Feed (2026-09-26)**: Die
  Wiener Linien schicken je Folge einer Störung eine eigene
  Anzeigetafel-Meldung; am 26.09. belegte die Linie 62 so drei der zehn
  Plätze. `_merge_wl_ticker_clusters` in `src/build_feed.py` legt
  WL-Störungen derselben Linien, die innerhalb von zehn Minuten erscheinen,
  zu einem Eintrag zusammen: Linie und häufigste Ursache im Titel, alle
  Folgen in der Beschreibung („62: ÖBB Bauarbeiten“ über „Betrieb ab
  Kliebergasse; Züge halte bei Linie 18, Richtung Burggasse; Kein
  Betrieb.“). Rückschau: In 242 von 693 Feed-Ständen standen solche
  Gruppen, zusammen 485 Plätze. Die Störungsstatistik zählt einen
  zusammengelegten Vorfall einmal.
* **EN-Feed: vier Glossar-Fehler behoben (2026-09-26)**: „bis
  voraussichtlich“ wird zu „until approx.“ statt „to expected“ bzw. „until
  expected“ (A.3); ein Glossar-Begriff am Satzanfang mitten im Text wird
  großgeschrieben, „… track 2. Expected duration“ (A.4, beim Rendern, auch
  für gecachte Werte); ÖBB-Entwarnungen heißen „Traffic restriction
  lifted“ bzw. „Line closure lifted“ statt „Removing traffic restrictions“
  (A.9); „ÖBB-Ersatzbus“ wird zu „ÖBB replacement bus“ ohne deutschen
  Bindestrich. Übersetzungs-Epoche 18, damit die betroffenen Einträge neu
  übersetzt werden. Der deutsche Feed ändert sich nicht.
* **Sicherheit: kein stilles Fail-Open mehr hinter einem Proxy (2026-09-26)**:
  Bisher übersprang `verify_response_ip` die DNS-Rebinding-Prüfung, sobald
  eine Proxy-Variable gesetzt war (Audit vom 17.09., B.3). Jetzt gehen
  Anfragen durch einen Proxy nur an die Hosts in `PROXY_TRUSTED_HOSTS` oder
  an eine literale, sichere IP; jeder andere Host wird vor dem Senden
  abgewiesen (`TimeoutHTTPAdapter.send`) und nach der Antwort erneut geprüft
  (`verify_response_ip`). `NO_PROXY` wird beachtet. Ein Test gleicht die
  Liste mit den konfigurierten Upstreams ab. `tests/conftest.py` entfernt
  die Proxy-Variablen des Rechners, damit die Suite überall gleich läuft.
  Die Produktions-Workflows setzen keinen Proxy.
* **Linien-Prüfung: allgemeine Regeln statt Einträge je Bahnhof, Sammlung
  der Auffälligkeiten (2026-09-26)**: Stufe 2 merkt sich eine Linie jetzt
  drei Jahre nach der letzten Sichtung statt 56 Tage, damit lange Sperren
  sie nicht löschen. Stufe 3 bewertet eine Zuglinie nur an Bahnhöfen, an
  denen je ein Zug gesehen wurde („not judged“ statt Fehlalarm; Wiener-
  Linien-Linien werden weiter bewertet). Die gepflegte Liste
  `data/planned_station_lines.json` behält nur die S80 in Hütteldorf; die
  Einträge für Stammstrecke, Speising und Himberg entfallen. Ein
  abgelaufener Eintrag warnt nur, solange HAFAS seine Linien noch nicht
  zeigt. Neu `data/feed_line_anomalies.json`: Befunde, „not judged“ und
  offene abgelaufene Einträge mit erstem und letztem Tag, zur späteren
  Auswertung. Der Schritt läuft dafür vor dem Veröffentlichen, damit die
  Sammlung im selben Commit landet. Der Feed ändert sich nicht.
* **Gepflegte Linienliste mit ÖBB-Unterlagen belegt (2026-09-26)**:
  `data/planned_station_lines.json` nennt jetzt für jeden Eintrag die
  Quelle. Die S80 in Hütteldorf und Speising gilt bis 11.12.2027 statt
  offen (Verbindungsbahn bis Ende 2027, SNNB-Anhang 2.5.1 bis 09.12.2027).
  Die Sperre der Stammstrecke endet laut SNNB-Anhang am 29.10.2027, laut
  Folder „Ende Oktober 2027“; der Eintrag bleibt bis 31.10.2027. Himberg:
  Der Bahnhof wird umgebaut (Rahmenplan, Inbetriebnahme 2026). Im Audit
  ist Befund B.1 (Nordrichtung des Stammstrecken-Monitors ohne Messwerte
  seit 14.08.) geklärt: ein Kabelbrand am Stellwerk Praterstern, danach
  die Hauptsperre ab 07.09.2026.
* **Testmeldungen der Anbieter kommen nicht mehr in den Feed (2026-09-26)**:
  Am 23.09. standen zwei Testmeldungen der Wiener Linien je einen Zyklus im
  deutschen Feed („71/72: Dies ist eine Testmeldung“, „62: F57f Test“) und
  belegten einen der zehn Plätze auf den Info-Displays. `_drop_test_messages`
  in `src/build_feed.py` verwirft sie gleich nach dem Einsammeln, für alle
  Quellen: Titel oder Text enthält „Testmeldung“, oder Titel bzw. Text sind
  höchstens fünf Wörter lang und enthalten das Wort „Test“. Rückschau über
  488 Feed- und 952 Cache-Meldungen: nur diese beiden getroffen.
* **Stufe 3 der Linien-Prüfung: Bericht über den deutschen Feed (2026-09-26)**:
  Neu `scripts/check_feed_lines.py`, als letzter Schritt von
  `update-cycle.yml`. Für jede Meldung mit Linien-Präfix prüft es, ob es die
  Linie gibt und ob sie die genannten ÖBB-Bahnhöfe bedient, und schreibt,
  was es nicht bestätigen kann, ins Log. Der Feed bleibt unverändert. Neu
  `data/planned_station_lines.json`: Linien, die HAFAS wegen Baustellen
  nicht zeigt (Stammstrecke bis Oktober 2027, S80 an der Verbindungsbahn,
  S60 in Himberg). Die Rückschau über 678 Feed-Stände ergibt drei
  berechtigte Befunde, darunter eine Testmeldung der Wiener Linien vom
  23.09.
* **Stufe 2: Halte am selben Ort (2026-09-26)**: Der vierte Lauf ordnete den
  Hauptbahnhof zu („Wien Hbf (U)“, 17 Linien) und den Flughafen dem Bahnhof
  (S7, REX7); 152 Bahnhöfe haben Linien. Simmering und Himberg blieben leer:
  HAFAS führt dort je zwei Halte mit denselben Koordinaten, gewählt war der
  ohne Abfahrten. Zeigt der gewählte Halt keine Linie, fragt
  `scripts/update_oebb_station_lines.py` jetzt bis zu zwei weitere Halte im
  Umkreis von 50 m ab und speichert den ersten mit Linien.
* **Stufe 2: Hauptbahnhof, Flughafen und Bahnhöfe ohne Linie (2026-09-26)**:
  Der Diagnose-Lauf zeigte drei Dinge. Wien Mitte-Landstraße, Rennweg und
  Quartier Belvedere sind zu Recht leer: Die Stammstrecke ist zwischen
  Praterstern und Hauptbahnhof/St. Marx vom 07.09.2026 bis Oktober 2027
  gesperrt. „Wien Hauptbahnhof“ findet in HAFAS den Hauptbahnhof nicht;
  jetzt folgt eine zweite Anfrage als „Wien Hbf“. Deren Treffer muss „Hbf“
  heißen und höchstens 800 m entfernt liegen, ein anderer Hauptbahnhof oder
  Quartier Belvedere (526 m) zählt nicht. Der Flughafen war dem
  Busterminal zugeordnet (Klassen ohne R/REX und S-Bahn); jetzt zählt nur
  ein Halt mit R/REX oder S-Bahn. Bahnhöfe ohne Linie werden bei jedem
  Lauf neu zugeordnet, und ihre Kandidaten stehen im Log (Simmering,
  Himberg).
* **Stufe 2: Diagnose für Bahnhöfe ohne Linien (2026-09-26)**: Der zweite
  Lauf ordnete 157 von 162 Bahnhöfen einem Bahn-Halt zu; 151 haben Linien.
  Wien Mitte-Landstraße, Rennweg, Quartier Belvedere, Simmering und
  Himberg sind richtig zugeordnet, ihre Abfahrtstafeln liefern aber keine
  Linie; für Wien Hauptbahnhof und Siebenhirten fand sich kein Bahn-Halt.
  `scripts/update_oebb_station_lines.py` schreibt dafür jetzt Diagnose ins
  Log: alle `LocMatch`-Kandidaten mit Klassen und Abstand, und je
  Zeitfenster Abfahrten, Produkte und deren Linienfelder. Daten und
  Verhalten sonst unverändert.
* **Stufe 2: ÖBB-Bahnhöfe werden dem richtigen HAFAS-Halt zugeordnet (2026-09-26)**:
  Der erste Lauf (158 von 162 Bahnhöfen mit Antwort, 794 Anfragen, keine
  Fehler) nahm je Bahnhof den ersten HAFAS-Treffer nach Namen im Umkreis
  von 2 km. Wien Mitte-Landstraße, Rennweg und Quartier Belvedere bekamen
  so vermutlich die gleichnamige Straßenbahn- bzw. U-Bahn-Haltestelle und
  keine Bahnlinien. Jetzt: bis zu acht Kandidaten, der nächstgelegene mit
  Bahnklassen (`pCls`) im Umkreis von 800 m; alte Zuordnungen werden
  einmal neu ermittelt, jede Zuordnung steht im Log. Dazu der CodeQL-Befund
  „Modul mit `import` und `import from` eingebunden“ im Skript behoben.
* **Stufe 2 der Linien-Prüfung: Bahnlinien je ÖBB-Bahnhof (2026-09-25)**:
  Neu `scripts/update_oebb_station_lines.py`, wöchentlich in
  `update-stations.yml`. Für 162 ÖBB-Bahnhöfe in Wien und im Pendlerraum
  holt es aus HAFAS-Abfahrtstafeln (nächster Dienstag und fünf Wochen
  später, 06–09 und 15–18 Uhr, nur Bahn) die Linien wie `S45`, `REX51`,
  `CJX5` und speichert sie mit „zuletzt gesehen“ in
  `data/oebb_station_lines.json`; nach 56 Tagen ohne Nachweis fällt eine
  Linie heraus. Fernzüge und Schienenersatzverkehr zählen nicht. Die Datei
  beschreibt, was derzeit fährt: Langjährige Baustellen verstecken Linien,
  Stufe 3 braucht dafür eine gepflegte Liste. Feed unverändert. Tests:
  `tests/test_update_oebb_station_lines.py`.
* **HAFAS-Messlauf, zweite Runde (2026-09-25)**: Der erste Lauf zeigte:
  `LocMatch` liefert je Station nur Produktklassen (`pCls`), keine
  Linien; die Abfahrtstafel kam mit `err=PARSE` zurück. Ursache laut
  `public-transport/hafas-client`: ÖBB unterstützt `getPasslist` nicht.
  `scripts/probe_hafas_lines.py` fragt jetzt die Bahn-Abfahrten eines
  ganzen Tages in der Form ab, die `hafas-client` für ÖBB sendet
  (`stbLoc` `A=1@L=<extId>@`, Produktfilter 4159, kein `getPasslist`),
  meldet `errTxt` und benennt die Produktklassen. Schlägt die Anfrage
  fehl, folgt eine Ersatzanfrage zur Eingrenzung.
* **HAFAS-Messlauf für die ÖBB-Linien je Bahnhof (2026-09-25)**: Vorbereitung
  von Stufe 2 der Linien-Prüfung. `src/places/hafas_client.py` kapselt den
  Mgate-Transport in `post_mgate`; die Koordinaten-Anreicherung nutzt ihn
  unverändert. Neu: `scripts/probe_hafas_lines.py` und der nur manuell
  startbare Workflow `probe-hafas-lines.yml`, der für zwei Bahnhöfe die Form
  der HAFAS-Antworten (`LocMatch`, `StationBoard`) ins Log schreibt. Grund:
  Die Entwicklungs-Sandbox erreicht `fahrplan.oebb.at` nicht. Feed und
  Stationsverzeichnis ändern sich nicht. Tests:
  `tests/test_probe_hafas_lines.py`, `tests/places/test_hafas_client.py`.
* **Stationsverzeichnis: WL-Stationen kennen ihre Linien (`wl_lines`, 2026-09-25)**:
  Stufe 1 der Plausibilitätsprüfung „hält Linie X bei Y?“ (Anlass: A.14,
  Straßenbahn 1 in Hütteldorf). `scripts/update_wl_stations.py` lädt vom
  bestehenden WL-OGD-Endpoint zusätzlich `wienerlinien-ogd-linien.csv` und
  `wienerlinien-ogd-fahrwegverlaeufe.csv` und gibt jeder WL-Station die
  Linien, die an einem ihrer Haltepunkte halten, in natürlicher
  Reihenfolge. Optional: Fehlen die Dateien, läuft der Merge ohne Linien.
  Schema (`docs/schema/stations.schema.json`) und Doku ergänzt. Der Feed
  ändert sich nicht. Tests: `tests/test_wl_station_lines.py`.
* **DE- und EN-Feed: „ÖBB-Ersatzbus für <80“ ist die S80, nicht die Linie 1 (2026-09-25)**:
  Seit dem 12.09. stand täglich „1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80“
  im Feed. Beanstandung des Betreibers: Die Linien 1 und 80 fahren nicht
  zum Bahnhof Hütteldorf, dort halten keine Straßenbahnen. WL liefert die
  Meldung mit `relatedLines` „1“ und dem Text „ÖBB-Ersatzbus für <80“. Das
  „<“ ist das S-Bahn-Logo der Anzeigetafeln, die Titelbereinigung hatte
  es entfernt. Neu: `_attribute_obb_replacement_bus` setzt die S-Bahn-Linie
  aus dem Text ein, nur für Wiener S-Bahn-Nummern. Ergebnis nach
  Entscheidung des Betreibers: Titel „S80: ÖBB-Ersatzbus“, Beschreibung
  „Bhf. Hütteldorf“. Tests: `tests/test_obb_replacement_bus.py`.
* **DE- und EN-Feed: „Fahrtbehinderung <Ursache>“ zeigt die Ursache im Titel (2026-09-25)**:
  Um 17:35 stand „11A: Fahrtbehinderung Verkehrsunfall“ im Feed, zwei
  aneinandergeklebte Substantive über einem nackten Datum (106 Titel dieser
  Form seit Juni). Betreiberentscheidung (Audit A.13, Variante a): Der
  Titel lautet „11A: Verkehrsunfall“, „Fahrtbehinderung“ füllt die sonst
  leere Beschreibung. „31: Fahrtbehinderung wegen Polizeieinsatz“ wird zu
  „31: Polizeieinsatz“. Eine eigene Beschreibung von WL bleibt unverändert.
  Kollisionsregel verfeinert: Bei gleichem Kurztitel wird der am höchsten
  platzierte Ticker kurz, die übrigen bleiben lang (vorher alle lang). Live
  hätte das „18: Verkehrsunfall“ über „Betrieb ab Ernst-Happel-Stadion“
  sonst auf die lange Form mit leerer Beschreibung zurückgeworfen. Tests:
  `tests/test_ticker_title_split.py`.
* **DE- und EN-Feed: WL-Ticker mit Ursache im Titel, Folge in der Beschreibung (2026-09-25)**:
  Wunsch des Betreibers zum Live-Item „14A: Rettungseinsatz – Betrieb ab
  Laxenburger Straße / Gudrunstraße“, dessen Beschreibung nur „[Am
  25.09.2026]“ zeigte: Der Titel lautet jetzt „14A: Rettungseinsatz“, die
  Beschreibung „Betrieb ab Laxenburger Straße / Gudrunstraße [Am
  25.09.2026]“. Das gilt für alle WL-Störungen dieser Form. Die Ursache
  kommt aus `_TITLE_REASON_WORDS` oder, neu, aus dem Anfang der Folge
  („Betrieb ab …“, „Züge halten …“, „Umleitung …“, „Kein Betrieb“). So
  werden auch „O: Schadhafter Zug“, „5: Stromstörung“ oder „6: PKW im
  Gleis“ getrennt (13 weitere Titel seit Juni). Hat die Beschreibung eigenen
  Inhalt, steht die Folge davor, gekürzt wird hinter dem letzten ganzen
  Satz. Hinweise („D: Gleisbauarbeiten – Althanstraße“) behalten den Ort im
  Titel. Würden zwei sichtbare Items denselben kurzen Titel bekommen (drei
  Ticker „49: Gleisschaden …“ zu einem Vorfall), behalten sie die lange
  Form mit Strich. Tests: `tests/test_ticker_title_split.py`.
* **EN-Feed: Titel aus Linie und Glossar-Begriff ohne Modell übersetzt (2026-09-25)**:
  In den Builds um 15:53 und 16:01 standen im EN-Feed „94A:
  Verkehrsunfall“, „U1: Weichenstörung“, „U2: Polizeieinsatz“, „5:
  Falschparker“ und „1: Rettungseinsatz“ auf Deutsch. Nach der
  Maskierung bestehen solche Titel nur noch aus zwei Platzhaltern
  (`XENT…X0X: XGLO…X0X`). Es bleibt also nichts zu übersetzen, und das
  Modell hat die Platzhalter in beiden Builds beschädigt. Der Schnellweg
  für nicht übersetzbare Inhalte (`_is_non_translatable_content`) schloss
  Glossar-Platzhalter bisher aus, weil sie angeblich noch übersetzt werden
  müssten. Tatsächlich stehen sie bereits für den englischen Begriff. Jetzt
  läuft ein Text, der nur aus Platzhaltern und Satzzeichen besteht, ohne
  Modell: „94A: traffic accident“. Texte mit deutschem Rest gehen
  unverändert ans Modell. Tests: `tests/test_en_feed_placeholder_leak.py`.
* **DE- und EN-Feed: Entwarnungen nur noch auf freien Plätzen (2026-09-25)**:
  ÖBB-Entwarnungen („Aufhebung Verkehrseinschränkung: St. Pölten
  Hauptbahnhof“) sind neue Meldungen und standen durch die FIFO-Sortierung
  oben. Seit August belegten drei davon je etwa eine Stunde einen der zehn
  Plätze, statt einer laufenden Störung. Betreiberentscheidung: Eine
  laufende Störung ist wichtiger als eine Entwarnung, eine Entwarnung ist
  besser als ein leerer Platz. Neu: `_defer_all_clear_items` stellt
  Entwarnungen hinter alle anderen Items. Gelöscht wird nichts.
  Tests: `tests/test_all_clear_deferral.py`.
* **DE-Feed: Wiederkehrende WL-Störungen verdrängen nicht mehr sich selbst (2026-09-25)**:
  Um 15:01 fehlten sechs laufende Störungen im Feed: 94A, O, 5, 12, U2 und
  U1. Die zehn Plätze belegten stattdessen Haltestellenverlegungen vom 18.
  bis 24.09. Ursache: Die WL-GUID enthält kein Datum. „94A: Verkehrsunfall“
  vom 25.09. erbte deshalb das `first_seen` des 94A-Unfalls vom 04.07. Die
  FIFO-Sortierung stufte die Meldung als 83 Tage alt ein. Seit der
  State-Aufbewahrung von 600 Tagen (12.09.) überleben solche Einträge
  praktisch unbegrenzt. Neu: `_restart_recurring_occurrences` setzt
  `first_seen` einer WL-Meldung auf ihren `pubDate` (Gültigkeitsbeginn),
  wenn sie vorher länger als 2 h aus den Daten verschwunden war. Das hält
  das neue State-Feld `last_seen` fest. Maßnahmen, die WL täglich mit
  neuem Fenster neu ausgibt, behalten ihren Platz. ÖBB, Baustellen und
  Stammstrecke bleiben unverändert. Tests:
  `tests/test_recurring_wl_occurrence.py`.
* **DE- und EN-Feed: Gedankenstrich auch nach Störungsursachen (2026-09-25)**:
  Platz 1 lautete „6: Fremdunfall Züge halten bei der Linie O". Der
  C.5-Trenner (`_separate_reason_word`) setzt einen Gedankenstrich zwischen
  Grund und Ticker-Fragment, kannte aber nur Wörter für geplante Arbeiten
  und Veranstaltungen. Über 455 WL-Titel seit Juni beginnen 30 mit einer
  Störungsursache: Fremdunfall, Rettungseinsatz, Oberleitungsgebr(echen),
  Polizeieinsatz, Verkehrsunfall, Gleisschaden, Feuerwehreinsatz,
  Polizeiübung. Neu: `_INCIDENT_REASON_WORDS`; Trenner und Zerlegung des
  englischen Titels (`_split_reason_title`) nutzen gemeinsam
  `_TITLE_REASON_WORDS`, sonst reiste der Strich als Platzhalter ins Modell
  (Befund vom 19.09.). Beschreibungs-Dedupe und Themen-Budget bleiben bei
  der bisherigen Liste. Der Ticker-Stumpf „Oberleitungsgebr" wird zu
  „Oberleitungsgebrechen" (`_TICKER_ABBREVIATIONS`), Glossar: „overhead-line
  fault". Tests: `tests/test_reason_word_incidents.py`.
* **DE-Feed: ÖBB-„Update N (…)"-Präfix entfernt, Entwarnungen behalten ihr Label (2026-09-25)**:
  Platz 3 lautete „Update 4 (25.09.2026 09:59) Verkehrseinschränkung:
  St.Pölten". Die Präfix-Schleife in `_clean_title_keep_places` trennt nur an
  einem Doppelpunkt ohne folgende Ziffer (Uhrzeit-Schutz); der erste
  Doppelpunkt stand in „09:59", der Titel blieb deshalb roh. Neu:
  `_strip_update_prefix` entfernt „Update N (TT.MM.JJJJ hh:mm)" vorab, der
  Titel wird „St. Pölten Hauptbahnhof". Drei der vier solchen Titel seit
  August waren Entwarnungen („Aufhebung Verkehrseinschränkung: Wien
  Handelskai"); ein Präfix, das mit „Aufhebung" beginnt, gilt nicht mehr als
  verwerfbare Kategorie, sonst läse sich die Entwarnung wie eine laufende
  Störung. Das Label wird dabei abgetrennt, der Ort dahinter normal
  bereinigt und das Label wieder vorangestellt: „Update 5 (…) Aufhebung
  Verkehrseinschränkung: St.Pölten" wird „Aufhebung Verkehrseinschränkung:
  St. Pölten Hauptbahnhof". `_post_filter_oebb` repariert gecachte Titel
  beim Bauen; die GUID folgt weiter dem Rohtitel. Tests:
  `tests/test_oebb_update_prefix.py`.
* **DE-Feed: abgeschnittene Baustellen-Titel aus der Beschreibung vervollständigt (2026-09-24)**:
  Item 8 lautete „U4: Vordere Zollamtsstraße von Marxergasse und Kleine
  Marxerbrücke bis Unbenannte Verkehrsfläche und Rad…" — die Stadt Wien
  kappt `BEZEICHNUNG` bei 100 Zeichen, der Cache markiert den Schnitt nur
  (`_mark_upstream_truncation`). Die Beschreibung desselben Items nennt den
  Endpunkt aber vollständig („… bis und in Richtung zur Radetzkybrücke …").
  Neu in `_post_filter_baustellen`: `_repair_baustellen_title` ergänzt das
  abgeschnittene Wort aus der eigenen Beschreibung — nur wenn es dort
  eindeutig ist (Fragment ≥ 3 Buchstaben; bei mehreren Kandidaten wie
  „Radetzkybrücke"/„Radweg" zählt allein einer hinter einer
  Bereichspräposition wie „bis", „zur", „Richtung"; sonst bleibt der Titel
  wie geliefert) — und streicht „Unbenannte Verkehrsfläche", den
  Platzhalter der Stadt für ein namenloses Straßenstück, aus einer
  Endpunkt-Liste, die auch einen echten Namen nennt (als einziger Endpunkt
  bleibt er). Ergebnis: „U4: Vordere Zollamtsstraße von Marxergasse und
  Kleine Marxerbrücke bis Radetzkybrücke". Messung über die 22 verschiedenen
  Baustellen-Titel seit Juni: 3 gekappt, 2 davon vervollständigbar
  („Schlachthausgas…" → „Schlachthausgasse"), 1 nicht („Hofpavillon…" ist
  ein ganzes Wort und bleibt). GUID, `first_seen` und Feed-Position sind
  unberührt (Schlüssel ist die GUID aus dem Rohtitel); der englische Titel
  wird über den Quelltext-Digest einmal neu übersetzt. Tests:
  `tests/test_baustellen_title_repair.py`.
* **EN-Feed: Kalenderdaten als ein Platzhalter, Satzanfang groß (2026-09-24)**:
  Der Zyklus 19:31 veröffentlichte „U2: Folding ramps out of service on
  2709.2026" (deutsch: „am 27.09.2026") und cachte dieselbe Verstümmelung
  für „27A/28A/29A: Event on 2709.2026". Ursache: `_LINE_ENTITY_RE`
  maskiert den Tag „27" als linienförmige Zahl allein, das Modell sieht
  `XENT…X3X.09.2026` — einen Platzhalter, der ohne Leerzeichen an einem
  Punkt klebt — und lässt den Punkt fallen; von 8 seit Mitte August
  übersetzten Titeln mit Datum kamen 3 so heraus (auch ÖBB „Update 1
  (1609.2026 07:22)" am 16.09.). Neu: `_DATE_ENTITY_RE` maskiert
  `TT.MM.JJJJ` **vor** dem Linien-Durchlauf als eine wortgleiche Entität;
  ein verlorenes Datum lässt die Übersetzung über
  `_entities_dropped_by_translation` scheitern statt verstümmelt zu
  erscheinen. Cache-Self-Heal (`_cached_translation_defect`): ein
  gecachter EN-Wert, dem ein Datum des deutschen Quelltexts fehlt, gilt
  als Miss und wird neu übersetzt — ohne Epochensprung, der alle Items
  neu durch das Modell schicken würde (siehe Audit 2026-09-24, N.1).
  Zweitens: 5 der 10 EN-Beschreibungen begannen klein („stop relocation
  of line 7A …"), weil das Glossar Substantive als englische Gattungswörter
  klein einsetzt und nur der Titel (C.3) nachträglich groß geschrieben
  wurde; über 169 verschiedene EN-Beschreibungen seit 10.09. waren es 45.
  `_capitalise_sentence_start` hebt den ersten Buchstaben der
  gerenderten EN-Beschreibung an (ein Buchstabe, kein `str.capitalize`);
  `_capitalise_title_body` delegiert daran. Greift beim Rendern, also auch
  für gecachte Werte. Drittens loggt die Pipeline-Ladezeile jetzt das
  Platzhalter-Nonce des Builds, damit Residual-Fehlschläge eines Laufs
  mit dem Nonce korreliert werden können. Tests:
  `tests/test_translation_date_entity.py`,
  `tests/test_en_summary_sentence_start.py`.
* **EN-Feed: „Haltestellenauflassung" im Glossar (2026-09-24)**: Nach dem
  Epochensprung 16 kam die N31-Prosa „Haltestellenauflassung der Linie N31
  in Richtung Schwedenplatz U" aus dem Modell als „Stop stop on line N31
  towards Schwedenplatz U" (vorher „Station departure of line N31").
  „Haltestellenverlegung" stand im Glossar („stop relocation"), das
  Schwesterwort nicht; das Modell zerlegt das Kompositum sinnlos. Neu:
  „Haltestellenauflassung" → „stop closure", Plural → „stop closures"
  (4 bzw. 1 von 563 WL-Items der Cache-Historie). Epoche 16 → 17, damit
  das gecachte N31-Item neu übersetzt wird. Tests:
  `tests/test_glossary_haltestellenauflassung.py`.
* **Betrieb: `build-feed.yml` veröffentlicht die Feed-XMLs wieder (2026-09-24)**:
  Seit Anlage der Datei am 12.09. trug jeder `chore: rebuild feed`-Commit
  nur `data/first_seen.json`. Die `git-auto-commit-action` meldete
  `docs/feed.xml`, `docs/feed.en.xml` und `README.md` als geändert und
  committete dann „1 file changed" (Lauf 36003339176, 24.09. 15:07) — das
  mehrzeilige `file_pattern` stagte offenbar nur den ersten Eintrag. Ein
  Code-Merge erreichte die Öffentlichkeit deshalb erst mit dem nächsten
  Zyklus, bis 30 Minuten später, während der Zustand mit den neuen
  EN-Übersetzungen schon auf `main` lag. Die zwei Schritte „Pull concurrent
  remote changes" und „Commit and push changes" sind durch einen
  Plain-git-Schritt nach dem Muster von `update-cycle.yml` ersetzt:
  XML-Validierung, Staging der Allowlist, Commit, Push mit bis zu vier
  Versuchen und Rebase-Abgleich, nie rot. Rebuild-Commits tragen jetzt wie
  Zyklus-Commits den Bot als Autor.
* **EN-Feed: die Werte eines Label-Records sind jetzt Englisch (2026-09-24)**:
  Drei der zehn EN-Items trugen deutsche Reste im Record: „Duration: Ab 30.
  September 2026, etwa 13:00, bis expected Mai 2027", „To: etwa 20 Meter in
  Richtung …", „Ersatzlos Discontinued". Der Record erreicht das Modell
  bewusst nicht (`_render_label_record`, seit 18.09.); Labels und Nomen
  kommen aus dem Glossar, die Werte blieben wörtlich. Über 557 verschiedene
  WL-Items gemessen (60 mit Record, 40 mit `Dauer:`) folgen die Werte einer
  kleinen Grammatik: Datumspräposition, Tag, Monat, „etwa", Uhrzeit, eine von
  vier Endformeln. `_gloss_record_values` (`src/build_feed.py`) setzt genau
  diese Formen um — nur im Record, nicht in Prosa, wo „ab", „bis", „vor",
  „nach" gewöhnliches Deutsch sind; die Datumspräpositionen sind an die
  folgende Ziffer gebunden, damit „Am Schöpfwerk" ein Haltestellenname
  bleibt. Groß geschrieben wird nur am Wertanfang („Duration: From …", aber
  „…, until further notice"); Monatsnamen behalten ihre Schreibweise; ein
  Hausnummernbereich „12 bis 14" wird zu „12-14", eine Uhrzeitspanne nicht.
  Dazu zwei globale Phrasen: „ersatzlos aufgelassen" → „closed without
  replacement" (bisher „replacementless Discontinued", auch im N31-Titel)
  und „Betriebsschluss" → „end of service". Epoche 15 → 16. Tests:
  `tests/test_label_record_values_en.py`.
* **DE- und EN-Feed: eine WL-Linienliste nach dem Präfix wird erkannt und gestrichen (2026-09-24)**:
  Live im deutschen Feed standen `N66/N68R: N66, Rufbus N68: Quellenplatz`
  (24.09., Platz 2) und `4A/80A/N29: 4A. 80A, N29: Wittelsbachstraße`
  (19./20.09.). WL schreibt die Linien an den Titelanfang, der Provider
  setzt den `relatedLines`-Präfix davor, und `_extract_prefix_lines`
  (`src/providers/wl_lines.py`) soll die Liste des Titels darin aufgehen
  lassen. Zwei Formen fielen durch: `. ` als Trenner und ein einzelner Code
  vor `Rufbus X` (`LINES_COMPLEX_PREFIX_RE` verlangte zwei). Beide werden
  jetzt erkannt; der Punkt zählt nur mit folgendem Leerraum, damit `13.10:`
  keine Linienliste wird, und das strenge Token-Gate lehnt Wörter wie in
  `10. Bezirk:` weiter ab. Zusätzlich gilt `Rufbus N68` im Titel als
  dieselbe Linie wie `N68R` aus `relatedLines` (`_is_rufbus_twin`), sonst
  hieße der Präfix `N66/N68R/N68`. Über 453 WL-Cache-Revisionen (608
  Titel) sind das die einzigen zwei betroffenen Titel. Weil der Feed-Build
  gecachte Titel über `_post_filter_wl` neu parst, wirkt die Korrektur
  schon beim nächsten Bau. Tests:
  `tests/test_wl_line_list_repeated_in_title.py`.
* **DE- und EN-Feed: eine ÖBB-Strecke mit mehreren Bauphasen belegt nur noch einen Platz (2026-09-24)**:
  Der ÖBB-Cache trägt dreimal `Wien Hauptbahnhof ↔ Gramatneusiedl`
  (03.–05.10., 31.10.–30.11., 05.–07.12.2026; drei GUIDs, drei Texte). Alle
  drei überstehen beide Dedupe-Stufen und das Platzbudget (ein Routentitel
  trägt kein Ursachenwort). Bisher hielt allein die Menge neuerer
  WL-Meldungen sie unter den zehn Plätzen; an einem ruhigen Tag stünde
  dieselbe Zeile dreimal auf den Displays und zwei andere Störungen fielen
  weg. `_defer_repeated_route_titles` (`src/build_feed.py`) lässt von
  mehreren ÖBB-Items mit wortgleichem Titel nur das mit dem frühesten
  Zeitfenster an seinem Platz und stellt die übrigen hinter das Feld, wie es
  das Platzbudget tut. Zusammengeführt wird bewusst nicht: Die Phasen sind
  verschiedene Maßnahmen (Einzelzüge im Oktober, nachts keine Nahverkehrszüge
  danach), ein gemeinsamer Text verfälschte zwei davon. Nichts fällt weg:
  Endet die erste Phase, rückt die nächste nach. Tests:
  `tests/test_repeated_route_titles.py`.
* **ÖBB: ein Ortsname mit „im"/„am" bleibt als zweiter Routen-Endpunkt ganz (2026-09-24)**:
  Seit dem 22.09. stand `REX 6: Wien Baumgarten (WL) ↔ Ebenfurth` im
  deutschen Feed (Platz 4). Die Meldung betrifft Baumgarten im Burgenland:
  „zwischen Ebenfurth Bahnhof und Baumgarten im Bgld-Schattendorf Bahnhof".
  `im`/`am` beenden in `_ZWISCHEN_PLAIN_RE` und `_VON_NACH_PLAIN_RE` einen
  Endpunkt (als Zeitpräposition: „Felixdorf Bahnhof am 10.02.2026"), der
  zweite Endpunkt schrumpfte auf „Baumgarten", und das löste auf die
  WL-Haltestelle „Wien Baumgarten (WL)" auf. Eine Route ohne Wiener Ende galt
  damit als Wien ↔ Pendler, bekam einen falschen Titel und belegte einen der
  zehn Plätze. `_with_place_qualifier` hängt ein großgeschriebenes
  Zusatzwort wieder an, wenn direkt danach `Bahnhof`/`Bf`/`Hbf` folgt; die
  Route wird unbekannt und fällt nach der strengen Routenregel heraus.
  Nebenwirkung: „Brunn am Gebirge" und „Neusiedl am See" lösen als zweiter
  Endpunkt jetzt vollständig auf statt als unbekanntes „Brunn"/„Neusiedl".
  In der Cache-Historie (32 ÖBB-Meldungen) war REX 6 der einzige so
  abgeschnittene Endpunkt; die übrigen Schnitte an `im`/`am` sind echte
  Zeitangaben und bleiben unverändert. Tests:
  `tests/test_oebb_place_qualifier_endpoint.py`.
* **EN-Feed: ein Platzhalter ohne führendes „X" gilt jetzt als Rest-Platzhalter (2026-09-19)**:
  Erster Bau nach dem C.5-Epochen-Sprung (14 → 15, siehe unten) zeigte im
  englischen Feed `N6: Buses stop NeilreichgasseENTec2b2350d0e7e61aX2X-22,
  QuellenstraßeENTec2b2350d0e7e61aX4X`. Ursache: „Neilreichgasse 20-22"
  maskiert zu `XENT…X0X XENT…X2X-XENT…X3X` — `_LINE_ENTITY_RE` hält die
  bloßen Hausnummern 20/22 für Linienkennungen und maskiert sie mit —, zwei
  Platzhalter stehen dadurch ohne Leerzeichen an einem Bindestrich
  aneinander. Marian gab das Paar mit fehlendem führenden „X" des zweiten
  Platzhalters zurück; Nonce und Index blieben erhalten, die Form passte
  aber auf keine der beiden bisherigen `_RESIDUAL_PLACEHOLDER_RE`-Varianten
  (beide verlangen ein führendes „X"), und der rohe String erreichte drei
  Bau-Zyklen lang die Abonnenten. Eine dritte Variante verankert auf der
  Form der Nonce selbst (8–32 Kleinbuchstaben-Hex) statt auf dem laxeren
  `[A-Za-z0-9]*` der bestehenden Varianten — ein nacktes „ENT"/„GLO" ist ein
  gewöhnliches Wortfragment, daher die schärfere Verankerung. Der
  Selbstheilungs-Pfad des Caches greift automatisch beim nächsten Zugriff,
  kein Epochen-Sprung nötig. `_LINE_ENTITY_RE`s Übermaskierung von
  Hausnummern bleibt bestehen (eigenes Problem, nicht Ziel dieser Änderung);
  ein Modell, das die Platzhalter jetzt verstümmelt, lässt das Feld
  fehlschlagen und das Item auf Deutsch zurückfallen, statt Rohtext
  auszuliefern. Tests: `tests/test_translation_dropped_leading_x_placeholder.py`.
* **EN-Feed: der C.5-Gedankenstrich reist nicht mehr ins Übersetzungsmodell (2026-09-19)**:
  Der erste Bau nach dem Merge von C.5 (21:20) zeigte im englischen Feed
  `31: Demonstration –Xservice from Wallensteinstraße`, und im Zyklus 21:30
  fielen drei weitere Strich-Titel auf Deutsch zurück. Der Gedankenstrich ist
  ein geschütztes Zeichen und erreichte das Modell als Platzhalter zwischen
  zwei Wörtern; das Modell gab ihn mit Anhängsel („X" am Folgewort, für keine
  Nachkontrolle sichtbar, als Erfolg gecacht) oder verstümmelt zurück. Titel
  der Form `<Linien>: <Ursachenwort> – <Fragment>` werden jetzt in zwei
  Hälften übersetzt (`_translate_title_attempt`, `_split_reason_title` in
  `src/build_feed.py`) und der Strich wird wörtlich wieder eingesetzt; beide
  Hälften sind Texte, die das Modell schon vor dem Trennzeichen beherrschte.
  Nur der Titel-Pfad des Übersetzungs-Caches nimmt diesen Weg, Beschreibungen
  bleiben unberührt. `_TRANSLATION_CACHE_EPOCH` → 15, damit das gecachte
  Anhängsel verschwindet. Tests: `tests/test_reason_title_translated_in_halves.py`.
* **Baustellen: der Stadt-Wien-Verweissatz zählt nicht mehr als ÖPNV-Bezug (2026-09-19)**:
  Stadt-Wien-Baustellen kommen in den Feed, wenn sie nahe einem Bahnhof liegen
  oder ihr Text den öffentlichen Verkehr nennt. Als „Nennung" galt bisher auch
  der Verweissatz „Nähere Informationen zu den betroffenen öffentlichen
  Verkehrsmittel sind der Auskunft der Wiener Linien … zu entnehmen" — derselbe
  Satz, den der Feed-Bau seit 18.09. aus der Beschreibung streicht, weil er
  weder Linie noch Haltestelle nennt. Fünf von 22 Cache-Einträgen erreichten
  den Feed allein über diesen Satz; vier davon belegten in sieben Tagen 151 von
  338 Revisionen einen der zehn Plätze, jedes Mal ohne Verkehrsbezug in Titel
  oder Beschreibung („Ruthnergasse Kreuzung Justgasse", Audit 19.09., F.1).
  Das Muster ist jetzt einmal im Provider definiert
  (`src/providers/baustellen.py`, `REFERRAL_BOILERPLATE_RE`); Relevanzprüfung
  (`mentions_oepnv`, `is_transit_relevant`, Cache-Update und Feed-Bau) und
  Anzeige-Kürzung benutzen dasselbe. Der Satz wird nach der Wortreparatur
  entfernt, und das Muster duldet den fehlenden Leerraum in
  „betroffenenöffentlichen", den die Reparatur nicht sehen kann. `oepnv_lead`
  stellt den Satz nicht mehr an den Anfang der Beschreibung; führt jetzt der
  Satz, der eine Linie oder Haltestelle nennt. Erwartung auf dem Cache vom
  19.09.: 22 → 16 Einträge, kein Eintrag mit konkretem Linien- oder
  Haltestellenbezug betroffen. Tests: `tests/test_baustellen_referral_not_a_signal.py`.
* **DE- und EN-Feed: Gedankenstrich zwischen Ursachenwort und Ticker-Fragment (2026-09-19)**:
  WLs Anzeigetafel-Kurzmeldungen stellen den Grund ohne Fuge vor die Folge:
  `31: Demonstration Betrieb ab Wallensteinstraße`. Aus der Entfernung gelesen
  sind das zwei aneinandergelegte Fetzen, und wortweise übersetzt wurde daraus
  `Demonstration service from Wallensteinstraße` — als wäre „Demonstration
  service" eine Betriebsart (C.5, Audit vom 17.09.). Über 300 veröffentlichte
  Revisionen hatten 26 von 188 verschiedenen Titeln diese Form; der längste hat
  99 Zeichen, die Grenze liegt bei 256 — die Platzsorge ist ausgeräumt.
  `_separate_reason_word` zieht im fertigen deutschen Titel ein ` – ` ein, wenn
  der Titelkörper mit einem Wort aus `_CATEGORY_PREFIX_WORDS` beginnt und ein
  großgeschriebenes Fragment folgt (`Demonstration am 19.09.2026` bleibt ein
  Satz). Es läuft **nach** den Duplikat-Prüfungen, die Beschreibungen gegen den
  Titelkörper vergleichen; der englische Titel wird aus dem Ergebnis übersetzt
  und erbt den Strich: `Demonstration – service from Wallensteinstraße`.

* **DE-Feed: Richtungspfeil und nackte Titel-Wiederholung in der Beschreibung (2026-09-19)**:
  Item 7 des Tages zeigte unter `74A: Demonstration Betrieb ab Landstraße`
  die Beschreibung `Betrieb ab Landstraße <`. Zwei Ursachen: WLs
  Anzeigetafeln schreiben „in beiden Richtungen" als `< >` mit Leerzeichen,
  und die beiden Muster für abschließende Pfeile (`src/build_feed.py`,
  `src/feed/merge.py`) kannten nur zusammenhängende Pfeile — das `>` fiel, das
  `<` blieb. Und der Rest war der Titelkörper ohne sein Ursachenwort; der
  Emitter fing das bisher nur, wenn die Beschreibung das Wort selbst getragen
  hatte. Beide Helfer streichen die Pfeile jetzt per `str.rstrip` über die
  Zeichenmenge „Leerraum und Pfeile" — linear, ohne Regex; der erste Entwurf
  `(?:\s*[<>]+)+` hätte bei langen Pfeilfolgen exponentiell zurückgesetzt
  (CodeQL). `_summary_is_title_without_reason` prüft gegen das Ursachenwort des
  **Titels** — eine Beschreibung mit einem *anderen* Ursachenwort bleibt, das
  ist Information. 10 von 247 veröffentlichten Paaren vom 17.–19.09. waren
  solche Wiederholungen.

* **DE-Feed: Platzbudget je Ursache und Tag (2026-09-19)**:
  Am 19.09. gingen alle zehn Plätze des Feeds an Kurzmeldungen einer
  Demonstration, je Linie eine; über 300 veröffentlichte Revisionen wiederholte
  sich das Bild an zwei von acht Tagen mit je acht gleich begründeten Items in
  den Top 10. Neu läuft nach der Sortierung und vor dem Deckel
  `_apply_topic_budget`: Vom vierten Item desselben Ursachenworts
  (`_CATEGORY_PREFIX_WORDS`, am Titelanfang oder nach `wegen`) am selben
  Wiener Kalendertag an rutschen die weiteren hinter das Feld, in ihrer
  Reihenfolge — nichts wird verworfen, mit größerem `MAX_ITEMS` erscheinen sie
  später. Items ohne Ursachenwort oder Datum sind nie betroffen. Konfiguration
  `MAX_ITEMS_PER_TOPIC` (Standard 3, 0 schaltet ab), dokumentiert in
  `docs/development.md`. Ergänzt die Faltung Kurz→Lang aus dem Vor-PR für
  Ereignisse ohne Langmeldung. Betreiber-Entscheid vom 19.09. (A + G.6, dann C).

* **DE-Feed: ein Ereignis, alle zehn Plätze (2026-09-19)**:
  Wiener Linien schickt zu einem Großereignis **eine** ausführliche Meldung
  für alle betroffenen Linien (`1/2/2A/3A/4A/71/D: Demonstration am
  19.09.2026`, Kategorie `Hinweis`, mit Maßnahme je Linie) und am Tag selbst
  je Linie eine Anzeigetafel-Kurzmeldung (`1: Demonstration Betrieb ab Hintere
  Zollamtsstraße`, Kategorie `Störung`, darunter WLs Textbaustein „Nach einer
  Fahrtbehinderung kommt es zu unterschiedlichen Intervallen."). Die Faltung
  Kurz→Lang (`_ticker_fold_target`) verlangte gleiche Linienmenge, gleiche
  Kategorie und eine Beschreibung ohne eigenen Text — drei Nein, und der Feed
  bestand zu zehn von zehn Items aus Kurzmeldungen eines Ereignisses; die
  Langmeldung lag auf Platz 12. Seither: Die Linien der Kurzmeldung müssen in
  denen der Langmeldung **enthalten** sein; eine `Störung`-Kurzmeldung darf in
  einen `Hinweis` wandern (nur in diese Richtung — die Kategorie erreicht das
  Display nie, sie bricht nur Gleichstände in der Sortierung); der Textbaustein
  zählt als leer. Der Wortvergleich bleibt und löst jetzt HTML-Entities auf,
  weil der Langtext als HTML im Bucket liegt (`Zollamtsstra&szlig;e`). Am Cache
  des 19.09. falten 9 von 47 Kurzmeldungen; die Langmeldung wird sichtbar. Der
  Test, der bisher `Hinweis` und `Störung` auseinanderhielt, pinnt jetzt den
  neuen Vertrag.

* **DE-Feed: die Langmeldung zeigte die Ankündigung statt der Maßnahmen (2026-09-19)**:
  Der 180-Zeichen-Auszug nimmt den ersten Satz und den zweiten nur, wenn beide
  passen. Bei WLs ausführlichen Meldungen ist der erste Satz die Ankündigung
  („… kommt es zu folgenden Verkehrsmaßnahmen."), der zweite der Zeitraum —
  und die Maßnahmen je Linie kamen nie an die Reihe; 20 von 37 Langmeldungen im
  Cache tragen so einen Block. `_prefer_measures` stellt ihn voran: Die
  Ankündigung entfällt (der Titel nennt den Grund), der `Zeitraum:`-Satz
  entfällt (die Datumszeile des Items zeigt ihn), ein Einleitungssatz mit
  eigenem Inhalt bleibt. Die Maßnahmen werden mit `;` verbunden, damit der
  Schnitt mit Auslassungspunkten in der Liste landet statt nach der ersten
  Linie: `Linie D: Derzeit kein Betrieb zwischen Börse und Quartier Belvedere;
  Linie 1: Umleitung in beiden Richtungen zwischen Kliebergasse und Hintere
  Zollamtsstraße über Landstraße …`. Die Satzgrenze ist jetzt eine geteilte
  Konstante (`_SENTENCE_SPLIT_RE`), damit Auszug und Helfer nie verschieden
  trennen.

* **DE-Feed: die zusammengeführte Beschreibung las einen Titelteil noch einmal vor (2026-09-19)**:
  Werden zwei Meldungen derselben Linie zusammengeführt, bringt eine
  Schlagzeilen-Meldung — Beschreibung gleich eigener Titel — diese Wiederholung
  mit, und nach dem Merge steht sie unter dem echten Text der anderen:

  ```
  T: 2: Demonstration Züge halten Steig A & Züge halten bei Linie 46
  D: Nach einer Fahrtbehinderung kommt es zu unterschiedlichen Intervallen.
     Züge halten bei Linie 46
  ```

  Allein hätte `_summary_duplicates_title` (#1836) den zweiten Absatz bei der
  Ausgabe geleert; die Regel sieht aber nur die ganze Beschreibung, und dort
  sind die Absätze längst eine Zeile. Der Merge lässt eine Beschreibung, die
  wortgleich im zusammengeführten Titel steht, jetzt vor dem Stapeln fallen —
  auf beiden Seiten, damit die Reihenfolge der Meldungen keine Rolle spielt.
  Nur ganze Wörter zählen: `Betrieb ab Gersthof` wird von `Betrieb ab
  Gersthofer Straße` nicht wiederholt. Über 300 Feed-Revisionen gemessen: Der
  Fall „ganze Beschreibung = Titel" war seit #1836 geschlossen, der
  Ein-Absatz-Fall ist dieses Item in zwei Varianten.

* **DE-Feed: der Titel fasste zusammen, die Beschreibung wiederholte (2026-09-19)**:
  Werden zwei Meldungen derselben Linie zusammengeführt, faktorisiert der
  **Titel** den gemeinsamen Anfang seit jeher heraus (`_join_merged_names` →
  `_collapse_common_prefix`) und hängt nur die abweichenden Enden an. Die
  **Beschreibung** tat das nicht — sie verkettete beide Quelltexte wortwörtlich,
  gemeinsamen Anfang inklusive. Beides stand nebeneinander im Feed:

  ```
  T: O: Schadhafter Bus Betrieb ab Praterstern, Quartier Belvedere
  D: Schadhafter Bus Betrieb ab Praterstern
     Schadhafter Bus Betrieb ab Quartier Belvedere
  ```

  **Mindestens 6 von 250** veröffentlichten deutschen Items. Der Fix ist keine
  neue Regel, sondern **die Regel des Titels, ein Feld weiter angewandt** —
  mit denselben Schranken: mindestens 10 gemeinsame Zeichen, endend auf einer
  Wortgrenze, neues Suffix höchstens 60 Zeichen, keine ÖBB-`↔`-Kette.

  Vier der sechs Fälle kollabieren damit, zwei lehnen **absichtlich** ab und
  gehören zu anderen Mechanismen — in einen eigenen PR, nicht in diesen:

  * `Fahrtbehinderung PKW im Gleis` + `PKW im Gleis Betrieb ab Raxstraße` —
    die Überlappung ist *Suffix* des ersten und *Präfix* des zweiten.
  * `Linien 25 und 26 Betrieb ab Josef-Baumann-Gasse` + `Ersatzbus ab
    Josef-Baumann-Gasse` — der gemeinsame Teil ist *Suffix* beider.

  Wo der Collapse ablehnt, bleibt die bisherige Leerzeilen-Verkettung: zwei
  lange, unverwandte Sätze lesen sich gestapelt besser als komma-verbunden.
  Eine Schranke braucht die Beschreibung, die der Titel nie brauchte: Texte in
  **Satzform** bleiben gestapelt — sonst landete das Komma hinter dem Punkt
  (`Details about Lauf., Pfad.`), und bei mehreren Sätzen würden zwei
  verschiedene zweite Sätze zu einer Aussage verklebt. Die gemessenen Fälle
  sind alle satzzeichenfrei. Die 10-Zeichen-Schwelle ist dabei kein Detail — `Kein Betrieb ab
  Praterstern` und `Kein Halt in Floridsdorf` teilen nur `Kein `, und sie
  komma-zu-verbinden machte aus zwei Tatsachen eine falsche. Als Test
  festgeschrieben, ebenso die ÖBB-Kette, bei der `↔` eine Route verbindet
  statt zwei Alternativen zu trennen.

  Sechs Mutationen geprüft, alle gefangen: Aufruf entfernt · immer kollabiert ·
  `↔`-Schutz entfernt · Mindest-Präfix auf 1 · Wortgrenzen-Rücksprung entfernt ·
  Enthaltenseins-Prüfung übersprungen. Die vierte entkam im ersten Durchgang,
  weil kein Testfall eine *kurze* gemeinsame Eröffnung hatte — nachgereicht.

* **DE-Feed: dieselbe Meldung stand dreimal untereinander (2026-09-19)**:
  Wiener Linien führt eine Störung, die mehrere Linien trifft, in **einer**
  Beschreibung auf — je Linie ein Segment mit eigenem `Linie X:`-Präfix.
  Trifft es alle gleich, trägt jedes Segment denselben Satz:

  ```
  Linie 5B:  Unregelmäßige Intervalle in beiden Richtungen.
  Linie 49A: Unregelmäßige Intervalle in beiden Richtungen.
  Linie 50A: Unregelmäßige Intervalle in beiden Richtungen.
  Grund: Verkehrsstörung.
  ```

  `_strip_wl_description_line_prefix` entfernt nur das **erste** Präfix — den
  Rest trug der Feed mit. Auf dem Display stand:

  ```
  5B/49A/50A: Verkehrsstörung
  Unregelmäßige Intervalle in beiden Richtungen. Linie 49A:
  Unregelmäßige Intervalle in beiden Richtungen. …
  ```

  Derselbe Satz zweimal, eine von drei Linien willkürlich herausgegriffen,
  als wäre sie besonders betroffen — und `Grund: Verkehrsstörung.`, das
  einzige, was etwas Neues sagt, von der 180-Zeichen-Grenze abgeschnitten.

  **13 von 250** veröffentlichten deutschen Items sahen so aus; bei **7**
  davon fiel der Grund der Kürzung zum Opfer. Nach dem Fix:

  ```
  Unregelmäßige Intervalle in beiden Richtungen. Grund: Verkehrsstörung.
  ```

  Zwei Grenzen sichern die Regel ab:

  1. **Segmente mit unterschiedlichem Text bleiben vollständig**, Präfix
     inklusive — dort ist die Linienzuordnung der ganze Sinn. Der Fall
     `46/49/52: Gleisbauarbeiten` (drei verschiedene Anweisungen) ist als
     Test festgeschrieben.
  2. **Nur der wiederholte Anfangssatz eines Segments fällt**, der Rest
     bleibt stehen. Über 267 Cache-Revisionen gemessen ist dieser Rest
     **immer** das globale `Grund: …`-Feld und nie linienspezifischer Text.

  Dazu ein Nebeneffekt, der mitbehoben werden musste: WL liefert die
  Ortsangabe des Grundes gelegentlich leer (`Grund: Verkehrsüberlastung im
  Bereich .`) oder mit einem Leerzeichen vor dem Punkt (`… Atzgersdorfer
  Straße .`) — 9 von 417 Beschreibungen. Beides lag bisher hinter der
  Kürzung und wäre durch diesen Fix erstmals sichtbar geworden. Ein
  hängendes `im Bereich` fällt jetzt, **wenn nichts darauf folgt**; ein
  benannter Ort behält seine Präposition.

  Die Segmentgrenze verwendet `_WL_DESC_LINE_TOKEN` wieder, dieselbe
  Token-Form wie die beiden Präfix-Muster — keine dritte Liste. Ein Test
  prüft die Übereinstimmung beider Muster an neun Linienkennungen
  (einschließlich `WLB` und `Ersatzbus`, die keine sind), ein zweiter liest
  die Zuweisung im Quelltext, weil eine wortgleiche Kopie sich
  verhaltensmäßig nicht unterscheidet.

  Acht Mutationen geprüft, alle gefangen: beide Aufrufe entfernt · eigene
  Token-Liste · Token-Form driftet · ganzes Segment statt erstem Satz
  verglichen · Rest mitverworfen · Ortsangabe zu gierig entfernt · an jedem
  Satzende statt am Linien-Präfix getrennt.

* **EN-Feed: „to the Karlsplatz" → „to Karlsplatz" (2026-09-19)**:
  Die kleinere Hälfte der Artikel-Familie, deren größere (#1842,
  `the line 17A`) einen PR zuvor fiel. Deutsch artikuliert auch seine
  Straßen- und Platznamen — `in der Althanstraße`, `zum Karlsplatz` —,
  Englisch lässt sie bloß:

  ```
  Because of roadworks in the Kästenbaumgasse, …   → in Kästenbaumgasse
  Trains will be redirected to the Karlsplatz.     → to Karlsplatz
  ```

  **5 Vorkommen über 310** veröffentlichte EN-Items.

  Zwei Entscheidungen, die den Unterschied zur größeren Familie ausmachen:

  1. **Nur der Artikel fällt.** Ob `in Althanstraße` eigentlich
     `on Althanstraße` heißen müsste, ist eine eigene Frage mit deutlich
     unsichererer Antwort. Die Präposition bleibt, wie das Modell sie
     gewählt hat.
  2. **Der Straßentest ist `_STREET_SUFFIX_RE`** — genau das Muster, mit
     dem der Masker diese Namen ohnehin schützt, **wiederverwendet statt
     nachgebaut**. Eine zweite Suffix-Liste könnte von der ersten
     wegdriften; ein Test pinnt die Wiederverwendung. Und genau diese
     Wiederverwendung hält die Regel von den Fällen fern, in denen der
     Artikel vertretbar ist: `the Ernst-Happel-Stadion` und
     `the Wiener Linien` sind keine Straßennamen und bleiben unberührt.

  **Cache-Epoche 13 → 14** — alle fünf Vorkommen sind als Erfolg gecacht;
  der Artikel ist falsch, ohne deutsch zu sein.

  Fünf Mutationen geprüft, alle gefangen: Aufruf entfernt · eigene
  Suffix-Liste statt Wiederverwendung · Wortgrenze entfernt · jedes
  großgeschriebene Wort getroffen · Epoche nicht erhöht.

* **EN-Feed: „the lines 36A and 36B" → „lines 36A and 36B" (2026-09-19)**:
  Deutsch artikuliert seine Linien — `die Linie 17A`, `die Linien 36A und 36B`
  —, englischer Verkehrssprachgebrauch nicht. Die Wiener Linien schreiben auf
  ihren eigenen englischen Seiten „line U4". Das Modell macht dabei **nichts
  falsch**: hinter dem Artikel steht ein maskierter Platzhalter, es hat also
  nichts, woran es die Konvention erkennen könnte.

  Gezählt über **294** veröffentlichte EN-Items ändern sich **23** Texte:

  ```
  the lines 36A and 36B are being redirected     → lines 36A and 36B …
  The line 79B is redirected in both directions  → Line 79B …
  Trains stop on the lines 1, 18, 62 WLB, the line O → … on lines 1, 18, 62 WLB, line O
  ```

  Der Lookahead auf eine Linienkennung hält die Regel ehrlich: `the line is
  divided` und `at the end of the line` sind normales Englisch und bleiben.
  Nur ein Artikel **direkt** vor `line`/`lines` **plus Kennung** fällt weg. In
  `the lines 86A, 87A and the call bus 86A` geht genau der erste.

  Ein satzeröffnendes `The line 79B …` gibt seine Großschreibung an das
  Substantiv weiter statt sie zu verlieren.

  **Cache-Epoche 12 → 13.** Alle 23 Vorkommen sind als Erfolg gecacht — der
  Artikel ist falsch, ohne deutsch zu sein, also greift weder die
  Sticky-German-Bremse noch der Entity-Wächter. Ohne Bump behielten die Items
  ihren Artikel für ihre Lebensdauer; ein U4-Hinweis läuft bis 11/2026. Das
  ist genau die Regel, die einen PR zuvor in `docs/architecture.md` §8
  aufgeschrieben wurde — erste Anwendung.

  Fünf Mutationen geprüft, **alle** gefangen. Eine davon hätte ohne
  Integrationstest niemand bemerkt: den Aufruf aus `_translate_text_attempt`
  zu entfernen, ließ zunächst jeden Test grün. Der Test dafür folgt dem
  Muster aus `test_indefinite_article_agreement.py`, wo dieselbe Lücke schon
  einmal auftrat.

* **Die Architektur-Karte kannte den englischen Feed nicht (2026-09-19)**:
  `docs/architecture.md` beschreibt auf 829 Zeilen die Abrufpipeline, die
  `request_safe`-State-Machine, den Resilienz-Stack, die Stationsanreicherung,
  die Statistik und den VOR-Scope — und erwähnte **Übersetzung, Glossar,
  Masking und Marian mit null Treffern**. Die README nannte
  `docs/feed.en.xml` ebenfalls **kein einziges Mal**, obwohl `AGENTS.md` den
  englischen Feed als Ausgabe Nummer zwei führt.

  Neu ist **§8 „Der zweisprachige Feed (DE → EN)"** mit Mermaid-Diagramm und
  Fließtext, wie das Dokument es für jeden anderen Abschnitt hält. Beschrieben
  sind die Kaskade (`_normalise_for_translation` → `_apply_domain_glossary` →
  `_mask_entities` → Marian → `_unmask_entities`), die beiden Platzhalter-
  Sorten samt Prozess-Nonce, die Nachkontrollen und der Betrieb.

  Zwei Dinge stehen dort, weil sie ein späterer Bearbeiter sonst kaputt macht:

  1. **Ein Item ist ganz englisch oder ganz deutsch.** `_apply_lang_overlay`
     gibt bei einem Fehlschlag in irgendeinem Feld das unveränderte Original
     zurück. Eine „wenigstens teilweise"-Logik bricht die Content-Parität.
  2. **Wer Masking oder Glossar verbessert, erhöht `_TRANSLATION_CACHE_EPOCH`
     im selben PR.** Die Sticky-German-Bremse erzwingt einen neuen Versuch nur,
     wenn der gecachte Wert *gleich dem deutschen Quelltext* ist — eine
     falsche, aber englische Übersetzung wird sonst für die Lebensdauer des
     Items weiter ausgeliefert.

  Dazu die Arbeitsteilung, die beim Lesen des Codes nicht sofort auffällt:
  die **Epoche** invalidiert Änderungen auf *unserer* Seite, die
  **Quell-Fingerprints** (`_SOURCE_DIGEST_KEY`) solche *upstreams*.

  Die Abschnittsnummern sind nicht verschoben: §2–§7 werden aus Code und
  Tests heraus referenziert (`test_vor_ci_quota_gate.py` liest die Datei und
  prüft §7), das bisherige §8 „Querverweise" wandert als **§9** ans Ende.

  Gegen stilles Verrotten sichert `tests/test_architecture_bilingual_section.py`
  den Abschnitt ab: jedes der **17** dort genannten `_symbol`e muss in
  `src/build_feed.py` weiterhin existieren, und die beiden Regeln oben müssen
  benannt bleiben. Das Muster folgt dem bestehenden §7-Test.

  In der README steht der englische Feed jetzt an beiden Stellen, an denen
  schon die Feed-URL steht — außerhalb der auto-generierten Statistikblöcke,
  damit der Generator sie nicht überschreibt.

* **Zusammengeführte Meldungen lasen sich doppelt (2026-09-19)**:
  Wenn zwei Items verschmelzen, verbindet `src/feed/merge.py` die beiden
  Rümpfe für den **Titel** mit `f"{ex_name} & {name}"`, während die
  zusammengeführte Beschreibung dieselben zwei Teile mit einem einfachen
  Leerzeichen trägt:

  ```
  T: 25: Linien 25 und 26 Betrieb ab Josef-Baumann-Gasse & Ersatzbus ab Josef-Baumann-Gasse
  D: Linien 25 und 26 Betrieb ab Josef-Baumann-Gasse Ersatzbus ab Josef-Baumann-Gasse
  ```

  Wort für Wort dasselbe — der wörtliche Vergleich in
  `_summary_duplicates_title` verfehlt es an **einem Zeichen**. Der Vergleich
  gleicht das Trennzeichen jetzt auf **beiden** Seiten an.

  Größenordnung: **8 von 232** eindeutigen veröffentlichten deutschen Items
  tragen ein `&` in Titel oder Rumpf, **6** davon sind diese Wiederholung.
  Die übrigen zwei sind gewöhnliche Prosa — `Die Zufahrt zur Sport & Fun
  Halle Donaustadt ist möglich.` — und bleiben unberührt: nach dem Angleichen
  muss der restliche Satz weiterhin exakt übereinstimmen.

  Der aktuelle Cache enthält keinen zusammengeführten Titel, entsprechend
  ändert sich dort **0 von 106** Items — der Beleg steckt in der
  veröffentlichten Historie, nicht im Tagesbestand.

  Fünf Mutationen geprüft, vier fallen: dritter Vergleich entfernt (3 Tests) ·
  nur die Titelseite angeglichen (1) · `&` statt ` & ` getroffen (3) ·
  zweiter Vergleich zerstört (2). Die fünfte, die Kurzschluss-Bedingung, ist
  nachweislich verhaltensneutral und im Code als solche kommentiert.

* **Ein Item, zwei Aussagen: der Pfeil, den nur der Titel verlor (2026-09-19)**:
  Im Cache des manuellen Full-Refresh:

  ```
  T: 1: Bhf. Hütteldorf ÖBB-Ersatzbus für 80
  D: Bhf. Hütteldorf ÖBB-Ersatzbus für <80
  ```

  Dieselbe Meldung sagt in der Überschrift `für 80` und darunter `für <80`.
  Die Ursache ist eine **Asymmetrie zwischen Titel und Rumpf**: `_tidy_title_wl`
  endet auf `re.sub(r"[<>«»‹›]+", "", t)` und `wl_fetch` wiederholt das auf dem
  zusammengesetzten Titel — die Beschreibung lief nie durch dieselbe Reinigung.

  Behoben wird das im **Vergleich**, nicht im Text: `_summary_duplicates_title`
  ignoriert `<`/`>`, erkennt die Wiederholung und lässt sie fallen. Der Titel
  sagt ohnehin alles; übrig bleibt der Zeitraum.

  **Der erste Anlauf war falsch und ist es wert, festgehalten zu werden.** Er
  entfernte das Zeichen aus der Zusammenfassung selbst — und riss damit
  **fünf Injection-Tests** um: `&lt;b&gt;x&lt;/b&gt;` wird von `html_to_text`
  zu den *Literalzeichen* `<b>x</b>` dekodiert, die die Pipeline bewusst als
  Text weiterträgt und erst am `_emit_item`-Sink erneut escapet. Eine Regel,
  die `<` vor einem Wort streicht, zerstört genau diesen Beleg. Ein Vergleich
  kann nichts verstümmeln — deshalb jetzt dort. Zwei neue Tests
  (`TestLiteralAngleBracketTextIsNeverRewritten`) halten die Lektion fest, und
  eine Mutation, die das Streichen zurückholt, lässt **11** Tests fallen.

  **Was der Fix nicht tut: raten, was `<80` heißen sollte.** `S80` ist eine
  echte Linie in Hütteldorf, das Zeichen könnte ein verstümmeltes `S` sein.
  Daraus `S80` zu machen hieße, eine Liniennummer zu erfinden — der Feed hatte
  schon einmal ein frei erfundenes `14AX`. Wo Titel und Rumpf **nicht**
  dieselbe Aussage sind, bleibt der veröffentlichte Text unverändert.

  Gemessen: **1 von 108** gerenderten Cache-Items ändert sich, null
  Regressionen. Über **232** veröffentlichte deutsche Items trug bisher keines
  einen Marker in der Beschreibung — das Item stand schlicht noch nie unter
  den ersten zehn. Vier Mutationen geprüft; drei fallen, die vierte
  (Kurzschluss-Bedingung) ist nachweislich verhaltensneutral und im Code als
  solche kommentiert.

* **Die Beschreibung, die nur den Titel noch einmal vorliest (2026-09-18)**:
  Item 5 von zehn im deutschen Feed:

  ```
  40/41/9/42: Veranstaltung Linien 40 und 41 Umleitung über Linien 9 und 42
  Linien 40 und 41 Umleitung über Linien 9 und 42 [Am 18.09.2026]
  ```

  Die zweite Zeile steht vollständig in der ersten. Auf einem rotierenden
  Display liest man dieselben Worte zweimal und erfährt beim zweiten Mal
  nichts.

  Der Mechanismus ist eine **Asymmetrie**: das führende WL-Kategoriewort
  (`Veranstaltung`, `Demonstration`, `Kranarbeiten` …) wird aus der
  *Zusammenfassung* gestrichen, nie aus dem *Titel*. Steht es in beiden,
  vergleicht `_summary_duplicates_title` einen gestrichenen String mit einem
  ungestrichenen, findet keine Übereinstimmung und lässt die Wiederholung
  durch. Die Prüfung vergleicht jetzt auf gleicher Grundlage.

  Größenordnung: **12 von 222** eindeutigen veröffentlichten deutschen Items
  hatten genau diese Form — und in jedem einzelnen war das Kategoriewort der
  einzige Unterschied. Die kürzesten sind die deutlichsten:

  | Titel | Beschreibung |
  | --- | --- |
  | `2A: Veranstaltung Kein Betrieb` | `Kein Betrieb` |
  | `1A: Veranstaltung Kein Betrieb` | `Kein Betrieb` |
  | `3A: Veranstaltung Kein Betrieb` | `Kein Betrieb` |

  In diesem Zweig bleibt der Rumpf **leer**, im bestehenden daneben rettet
  `_reason_only_summary` den Grund als `Grund: …`. Das ist kein Widerspruch:
  dort nennt der Titel das Kategoriewort nicht, hier per Definition schon —
  sonst wäre das Streichen wirkungslos gewesen. Ein `Grund: Veranstaltung.`
  unter einer Schlagzeile, die mit „Veranstaltung" beginnt, tauscht nur eine
  Wiederholung gegen eine kürzere.

  Geprüft: **2 von 110** gerenderten Cache-Items ändern sich, beide korrekt,
  null Regressionen. Fünf Mutationen — Zweig entfernt, `Grund:` statt leer,
  Kurzschluss entfernt, Titel ohne Linienpräfix-Strip, Helfer ohne Strip —
  werden bis auf die nachweislich verhaltensneutrale Kurzschluss-Bedingung
  alle von den Tests gefangen.

  Ein bestehender Test hielt die alte Erwartung fest: der Volkstheater-Fall in
  `test_trailing_directional_marker.py` prüfte, dass nach dem Entfernen des
  WL-Richtungspfeils der Text **stehen bleibt** — er blieb aber nur wegen
  genau dieser Lücke stehen. Der Test ist angepasst **und begründet**, nicht
  stillschweigend umgeschrieben, und beweist den Pfeil-Strip jetzt über das
  Greifen der Dedupe-Prüfung: ohne den Strip passt die Zusammenfassung nicht
  auf den Titelrumpf und überlebt. Eine Mutation, die den Strip entfernt,
  lässt ihn weiterhin fehlschlagen. (PR #1836)

* **Die Baustellen-Meldung, die nur „fragen Sie woanders" sagte (2026-09-18)**:
  Item 8 von zehn im deutschen Feed, vollständig:

  ```
  Ruthnergasse Kreuzung Justgasse
  Nähere Informationen zu den betroffenen öffentlichen Verkehrsmittel sind
  der Auskunft der Wiener Linien GmbH & Co KG zu entnehmen. [20.09. – 16.10.]
  ```

  Der Satz ist **130 Zeichen** lang, das Summary-Budget **180**. Die
  Zusammenfassung besteht aus Satz 1 plus Satz 2, letzterer nur, wenn beide
  zusammen noch hineinpassen. Stand der Baustein vorne, **war** er die ganze
  Meldung — und der Satz, der sagt, was auf der Straße passiert, kam nie vor:

  ```
  Im Baustellenbereich wird ein Fahrstreifen freigehalten und der Verkehr
  wechselweise mittels Personal während der Spitzenzeiten oder
  Verkehrszeichen durchgeschleust.
  ```

  Auf einem rotierenden Info-Display kostet das nicht nur einen schlecht
  lesbaren Eintrag: `MaxItems` ist 10, der Platz hat also **eine andere
  Störung verdrängt**, die sonst zu sehen gewesen wäre.

  Der Baustein ist **kein fester String**. Drei Wortlaute stehen im Cache und
  unterscheiden sich nur in der Mitte — `zu den …`, `zur Umleitung sowie
  Haltestellenverlegung der …`, `zur Haltestellenverlegungen der …` — deshalb
  verankert das Muster die beiden invarianten Enden und lässt die Mitte in
  einem begrenzten, punktfreien Lauf variieren. Punktfrei hält einen
  entgleisten Treffer innerhalb eines Satzes.

  Größenordnung, ehrlich: **5 von 22** Baustellen-Beschreibungen im Cache
  tragen den Baustein, **3 von 110** gerenderten Cache-Items ändern sich
  dadurch. Kein systemisches Problem — aber bis 16.10.2026 live auf einem
  öffentlichen Display, im deutschen Feed, den `AGENTS.md` an erste Stelle
  setzt.

  **Gekoppelt dazu ein zweiter Fix in `_GLUED_WORD_RE`.** Der Lookbehind
  verlangt **zwei** Kleinbuchstaben und kann deshalb hinter einem
  zweibuchstabigen großgeschriebenen Funktionswort nicht greifen:
  `DerFußgängerverkehr` (`er`) und `DieArbeiten` (`ie`) wurden repariert,
  `ImBaustellenbereich` (`Im`, das `I` ist groß) nicht. Das fiel bisher nicht
  auf, weil genau dieser Satz nie veröffentlicht wurde — ohne den zweiten Fix
  hätte der erste die Verklebung erstmals **sichtbar gemacht**. Die Ergänzung
  ist eine **geschlossene Liste** von Funktionswörtern, kein gelockerter
  Lookbehind: `(?<=[A-ZÄÖÜ][a-zäöüß])` wäre kürzer und würde auch `McDonalds`
  trennen. Über **1192** Texte geprüft — den gesamten Cache plus die komplette
  veröffentlichte Historie beider Feeds — greift die Liste an genau **einer**
  Stelle und sonst nirgends.

  Das Entfernen tritt zurück, wenn der Baustein alles ist, was die
  Beschreibung hat: ein nutzloser Satz ist immer noch besser als ein Item mit
  Überschrift und nichts darunter.

  Keine Cache-Epoche nötig: der deutsche Quelltext ändert sich, und die
  Quell-Fingerprints (`_SOURCE_DIGEST_KEY`) invalidieren die englische
  Übersetzung von selbst. (PR #1835)

* **Störungsvokabular: aus „Harmful train" wird „defective train" (2026-09-18)**:
  Gewöhnliche deutsche Komposita, die das Übersetzungsmodell wörtlich nimmt
  und dabei zwischen schräg und alarmierend landet. Gezählt über **314**
  veröffentlichte EN-Texte:

  | Deutsch | Wie veröffentlicht | Items |
  | --- | --- | --- |
  | `Schadhafter Zug` | „**Harmful** train" | 4 |
  | `Schadhafter Bus` | „**Harmful** bus" | 3 |
  | `Schadhafter PKW` | „**Harmful** car" | 2 |
  | `Fremdunfall` | „Foreign accident" | 9 |
  | `Falschparker` | „False Parker" / „wrong parker" | 7 |
  | `Verunreinigung` | „Impurity" / „contamination" | 4 |
  | `Wasserrohrgebrechen` | „Water pipe fractures" | 2 |
  | `Klapprampensperre` | „Folding ramp **lock**" | 3 |
  | `Verkehrsstörung` | „Traffic disturbance" | 2 |

  „Harmful train" ist das schlimmste: es sagt einem englischen Leser, der Zug
  sei **gefährlich**, nicht defekt. `Fremdunfall` ist WL-Sprache für einen von
  außen verursachten Unfall — „Foreign accident" liest sich, als wäre er im
  Ausland passiert. `Falschparker` klingt als „False Parker" wie ein Nachname.
  Und `Klapprampensperre` heißt, dass die Rollstuhlrampen nicht ausgefahren
  werden können; „lock" transportiert das nicht — ausgerechnet bei den
  Leser:innen, die am wenigsten ausweichen können.

  Zwei Familien waren **halb** abgedeckt: `Schadhaftes Fahrzeug` und
  `Schadhafter LKW` standen im Glossar, `Zug`/`Bus`/`PKW` nicht;
  `Betriebsstörung` → „service disruption" stand drin, `Verkehrsstörung`
  nicht. Die Ergänzungen folgen dem bestehenden Muster statt einem zweiten.

  Nicht aufgenommen: `Busse`, `Betrieb`, `Linie`, `Fahrzeug` — sie übersetzen
  in ihren Kontexten korrekt, ein Glossareintrag würde das zerstören.
  `Klapprampen` allein ebenfalls nicht: kaputt war nur das Kompositum.

  Derselbe Durchlauf hat einen Befund des automatisierten Tages-Audits
  **widerlegt**. Das dort beschriebene Verkleben durch entferntes HTML
  (`DerFußgängerverkehr`, `werden.Nähere`) existiert — in **32 %** der
  Cache-Beschreibungen — und erreicht **null von 314** veröffentlichten
  Texten. Nachgewiesen, indem eine verklebte Cache-Beschreibung durch
  `_format_item_content` geschickt wurde; die Pipeline trennt bereits
  korrekt. (PR #1833, Cache-Epoche 11 → 12)

* **Der unbestimmte Artikel richtet sich wieder nach dem Wort dahinter
  (2026-09-18)**:
  Im EN-Feed stand zeitweise in **zwei von zehn** Items gleichzeitig:

  ```
  Due to an switch fault between Tullnerfeld … und Wien Meidling …
  Due to an demonstration in the area of Schwarzenbergplatz and Ring
  ```

  **Das Modell macht dabei nichts falsch.** Jeder Platzhalter beginnt mit
  `X`, und der Buchstabenname „ex" fängt mit einem Vokallaut an — `an
  XGLO…X0X` ist korrekt für den Token, den das Modell gesehen hat. Erst das
  Unmasking setzt ein konsonantisch beginnendes Wort ein und lässt den
  Artikel stehen. Ein Nebeneffekt der Architektur, kein Übersetzungsfehler.

  Gemessen über 314 EN-Texte: **15** falsche Artikel, drei Wörter, alle drei
  Glossarwerte — und acht korrekte Paare (`an event`, `a defective`,
  `an interlocking`, `a police`, `an obstacle`), die unangetastet bleiben.

  Bewusst eng gefasst: nur Glossar-Ersetzungen. Die Erstbuchstaben-Regel
  trägt für alle 97 Glossarwerte (ein Test prüft das gegen das echte
  Glossar), allgemein aber nicht — „a U-Bahn" und „an hour" gehen jeweils
  andersherum, und Entity-Platzhalter liefern genau solche Tokens. Prosa,
  die das Modell selbst geschrieben hat, wird nie umgeschrieben. Der Korpus
  enthält zudem `77 a After:`, eine vom Modell zerlegte Hausnummer; eine
  textweite Suche nach einem alleinstehenden `a` hätte sie zerstört.
  (PR #1832, Cache-Epoche 10 → 11)

* **Haltestellenverlegungen im EN-Feed: drei Anläufe, und die ersten zwei
  haben es nicht gelöst (2026-09-18)**:
  Ein Item, drei Runden, jede mit einem anderen Versagensgrund. Der
  Ausgangszustand, veröffentlicht:

  ```
  DE: Von: 1. Haidequerstraße 2     Nach: 1. Haidequerstraße 510
  EN: From: 1. Haidequerstraße 510  Duration: From 1. Haidequerstraße 510
  ```

  Das Modell ließ den Platzhalter mit der `2` weg und loopte auf dem Rest.
  Das Englische **verschwieg die Herkunft nicht nur, es beförderte das Ziel
  an deren Stelle** — der Leser erfuhr, die Haltestelle wandere *von* 510
  weg. Über rund 500 Hausnummern ist das ein langer Weg in die falsche
  Richtung. Dazu im Schwester-Item die Linie `14AX`, die es nicht gibt: das
  Modell hatte das schließende `X` des Platzhalters verdoppelt, der Unmasker
  ersetzte den gültigen Teil und ließ das überzählige Zeichen angeklebt.

  **Runde 1 (PR #1828)** — ein Wächter verwirft eine Übersetzung, die eine
  verbatim maskierte Entität verliert, und fällt auf die deutsche Quelle
  zurück; das verdoppelte `X` wird repariert statt verworfen, weil der
  Platzhalter heil ankam. Kalibriert über 400 DE/EN-Paare: „jede Maske muss
  überleben" schlägt bei 12,5 % an, davon 21 Verluste nur ein `…` oder `–`;
  auf Masken mit Wortzeichen eingeschränkt 7,2 % — exakt drei kaputte Items.
  Ergebnis: keine Falschaussage mehr, aber das Item stand **deutsch** da.

  **Runde 2 (PR #1829)** — die Feldlabels (`Von:`, `Nach:`, `Haltestelle:`,
  `Dauer:`, `Zeitraum:`, `Grund:`) und `Haltestellenverlegung` ins Glossar.
  `Nach:` war zu „**After:**" geworden, neben einem korrekten „From:" — eine
  Verlegung mit Herkunft und ohne Ziel. Der Doppelpunkt im Schlüssel ist
  dabei tragend: `Von` und `Nach` sind gewöhnliche Präpositionen, bloß als
  Wort eingetragen würden sie „Umleitung nach Hofmühlgasse" zerlegen.
  Ergebnis: Labels korrekt — und das Item stand über **fünf Builds hinweg
  weiter deutsch**. Die Vorhersage, es werde damit englisch, war falsch.

  **Runde 3 (PR #1831)** — der Record geht gar nicht mehr ans Modell. Übrig
  geblieben waren elf Platzhalter und drei deutsche Fragmente, kein Satz,
  an dem sich das Modell festhalten kann; den Rest mitzuglossarisieren war
  nicht verfügbar, weil der Korpus `bei der Linie` enthält, wo „of line"
  falsch wäre. Stattdessen: Label-Block abtrennen, aus Glossar und Masking
  deterministisch rendern, nur die Prosa davor übersetzen lassen. Die
  Schwelle ist gemessen — von 157 veröffentlichten Beschreibungen tragen 130
  kein Label, 24 genau eines (der `Grund:`-Schluss, der korrekt übersetzt
  und Prosa bleibt), und **genau 3** zwei oder mehr: die Verlegungs-Items.

  Im Feed seit dem Build um 18:30 nachweisbar:

  ```
  stop relocation of line 72A towards Hasenleitengasse Stop: Kraftwerk
  Simmering From: 1. Haidequerstraße 2 To: 1. Haidequerstraße 510 …
  ```

  Offen geblieben: `nach Hofmühlgasse` innerhalb eines Wertes bleibt
  deutsch — ein bloßes `nach` global zu glossarisieren ist aus demselben
  Grund unsicher wie `der Linie`. (PRs #1828, #1829, #1831, Cache-Epochen
  7 → 10)

* **Der Stationsvalidator meldet die eine Namenskollision, die wirklich
  falsch auflöst (2026-09-18)**:
  `station_info("Heizwerkstraße")` liefert DIVA, Stop-IDs und Koordinaten
  einer **anderen** Haltestelle, 463 m weiter westlich — und
  `station_info("Deutschstraße")`, im Verzeichnis nirgends sonst zu Hause,
  antwortet unter dem Namen `Wien Heizwerkstraße (WL)`.

  ```
  Wien Heizwerkstraße (WL) / wl_diva 60200228 → Haltestellen "Deutschstraße" ×2
  Wien Heizwerkstraße (WL) / wl_diva 60201742 → Haltestellen "Heizwerkstraße" ×2
  ```

  `_station_lookup` nimmt bei gleichem Namen eine Abkürzung, bevor der
  Tie-Break läuft: die erste Registrierung behält den Schlüssel, die spätere
  wird verworfen — und anders als jede andere Kollision auf diesem Pfad
  nicht einmal geloggt.

  **Der auslösende Audit-Befund war in zwei Punkten falsch**, und beides
  ändert, was der Check tun darf. Erstens der Mechanismus: die Duplikat-
  prüfung ist nicht rein koordinatenbasiert, `_find_alias_collision_issues`
  gruppiert alle acht Kollisionen bereits und schweigt nach seinem eigenen
  Kontrakt. Zweitens die Größe: sieben der acht sind **keine** Defekte —
  Wien betreibt verschiedene Haltestellen unter einem Namen (`Märzstraße`
  zweimal, 479 m auseinander), und in diesen sieben trägt jedes Mitglied
  eine Haltestelle des gemeinsamen Namens. Sie zu melden hieße, das am
  2026-05-12 bewusst entfernte Eindeutigkeitsgate wiederherzustellen, das
  einmal 30 Namensbefunde zu **1759 quarantänisierten WL-Einträgen**
  auffächerte.

  Der Check feuert deshalb nur, wenn der Eintrag, der den Namen gewinnt, ihn
  **nicht trägt**, ein Geschwistereintrag aber schon: 1 Treffer live, 0 auf
  den anderen sieben Gruppen, 0 auf den 105 Einträgen ohne Haltestelle
  eigenen Namens. Bewusst **nicht** an den Quarantäne-Pfad gehängt — der
  löscht den beanstandeten Eintrag, und das nähme dem Verzeichnis seine
  einzigen `Deutschstraße`-Haltestellen. Die Reparatur der Daten bleibt
  offen (Audit-Befund D.1). (PR #1827)

* **Straßennamen bleiben Straßennamen — auch im englischen Feed
  (2026-09-17)**:
  Im EN-Feed stand **„rear customs office"**, wo „Hintere Zollamtsstr."
  gemeint war. Aus einem Straßennamen wurde die Bezeichnung eines
  Gebäudetyps: Wer danach sucht, findet die Straße nicht, und wer es liest,
  sucht ein Zollamt.

  ```
  DE: 1: Veranstaltung Umleitung ab Hintere Zollamtsstr. über O und 18
  EN: 1: Event diversion from rear customs office via O and 18
  ```

  Der Masker schützt Straßennamen eigentlich vor der Übersetzung. Zwei
  Lücken in `_STREET_SUFFIX_RE` ließen sie durch, und sie versagen
  **unterschiedlich**:

  * **Abgekürztes Suffix.** WL kürzt `Straße` routinemäßig zu `Str.` ab.
    Die Abkürzung stand nicht in der Alternation, also wurde
    `Hintere Zollamtsstr.` **gar nicht** maskiert und ging vollständig
    durchs Modell.
  * **Vorangestelltes Adjektiv.** Das Muster verlangte, dass das Suffix am
    selben Wort klebt. `Vordere Zollamtsstraße` maskierte daher nur
    `Zollamtsstraße` und ließ `Vordere` los — „Front Zollamtsstraße". Ebenso
    `Kleine Marxerbrücke` → „Small Marxerbrücke".

  **Der Schutz, der bisher griff, griff aus dem falschen Grund.**
  `Hintere Zollamtsstraße` — ausgeschrieben — blieb heil, aber nicht wegen
  des Straßen-Musters: Es existiert zufällig eine Haltestelle
  `Wien Hintere Zollamtsstraße (WL)`, und Pass 2 (Stationsverzeichnis) fing
  sie ab. Ein Test gegen den fertigen Feed hätte aus ebendiesem Zufall
  bestanden und über das Schild nichts ausgesagt; die Tests prüfen deshalb
  `_mask_entities` direkt.

  Gegenprobe über den gesamten Cache (112 Meldungen, 108.034 Zeichen): Neu
  geschützt sind genau die fünf lecken Namen (`Hintere Zollamtsstr.`,
  `Hintere Zollamtsstraße`, `Vordere Zollamtsstraße`, `Kleine
  Marxerbrücke`, `Rechte Wienzeile`). `Zollamtsstraße` und `Marxerbrücke`
  erscheinen nicht mehr als eigene Spannen — sie gehen in den längeren
  Treffer auf, wie es die dokumentierte „longest-matching span"-Regel
  vorsieht; freistehend werden sie weiterhin erfasst.

  **Bewusst nicht mitgefixt:** Freistehende Namen wie `Mariahilfer Straße`
  bleiben ungeschützt. Sie bräuchten einen Zweig für einen bloßen Suffix
  nach einem Attribut, und jeder Kopf, der `Mariahilfer`, `Donaufelder` und
  `Schloßhofer` fasst (sie teilen nur die `-er`-Endung, und die Menge ist
  produktiv), fängt deutsche Determinative mit ein: `Dieser Platz`, `Jeder
  Weg`. Diese Namen sind heute ungeschützt, aber nachweislich **nicht
  kaputt** — das Modell reicht sie unverändert durch. Ein latentes Loch mit
  einer neuen Übergriffs-Klasse zu schließen wäre der falsche Tausch; der
  Fall gehört ins Stationsverzeichnis, das `Hütteldorfer Straße` und
  `Matzleinsdorfer Platz` bereits abdeckt.

  `_TRANSLATION_CACHE_EPOCH` steigt auf **7**. Ohne den Schritt bliebe die
  falsche Übersetzung stehen: `_cached_translation` erzwingt eine
  Neuberechnung nur, wenn der Cache-Wert dem deutschen Quelltext gleicht —
  „rear customs office" ist falsch, aber nicht deutsch, und die U4-Meldung,
  die ihn trägt, läuft bis 30.11.2026.

  Schließt **C.4** aus `docs/archive/audits/audit-2026-09-17.md`.
* **Eine Störung, ein Item: WL-Anzeigetafel-Doppel werden zusammengeführt
  (2026-09-17)**:
  `_fetch_traffic_infos` fragt bewusst **zwei** WL-Feeds in einem Aufruf ab —
  `stoerunglang` (der ausformulierte Meldungstext) und `stoerungkurz` (die
  Kurztexte der Anzeigetafeln). Für dieselbe Störung liefern beide einen
  Eintrag, die Kurzform je Ast sogar einen eigenen. Die Linie 49 stand
  deshalb **dreifach** im Feed:

  ```
  49: Gleisschaden                                ← stoerunglang
  49: Gleisschaden Betrieb ab Hütteldorfer Straße  ← stoerungkurz
  49: Gleisschaden Betrieb ab Urban-Loritz-Platz   ← stoerungkurz
  ```

  Die beiden Kurzformen erschienen **ohne jeden Text**. Ihre Beschreibung
  (`"Gleisschaden\nBetrieb ab Hütteldorfer Straße >"`) wiederholt nur den
  eigenen Titel, und `_summary_duplicates_title` leert sie folgerichtig —
  sichtbar blieb eine Schlagzeile über einem leeren Rumpf. Von den **zehn**
  Plätzen des deutschen Feeds trugen damit **drei** null Information,
  während der Langtext danebenstand und alles sagte: „Kein Betrieb zwischen
  Hütteldorfer Straße U und Urban-Loritz-Platz …“

  Das Bucketing über `topic_key` fasst sie nicht: Ohne Treffer in
  `TITLE_TOPIC_TOKENS` fällt der Schlüssel auf den ganzen Titelkern zurück,
  und der unterscheidet sich je Ast. `gleisschaden` dort nachzutragen wäre
  die **dritte** Runde desselben Spiels gewesen (`demonstration` und
  `feuerwehreinsatz` stehen für die beiden vorigen), und das nächste
  Ursachenwort — Oberleitungsschaden, Weichenstörung, Fahrzeuggebrechen —
  hätte Runde vier eröffnet.

  Die neue Regel braucht **kein Ursachenwort**. Sie stellt zweimal dieselbe
  Frage: *Sagt dieser Text etwas, das jener nicht schon sagt?*

  1. Die Beschreibung fügt ihrem **eigenen** Titel nichts hinzu → eine reine
     Schlagzeile.
  2. Ihr Titel steht bereits vollständig in der Beschreibung einer anderen
     Meldung derselben Linien, derselben Kategorie, mit überlappendem
     Zeitraum → jene sagt alles, was diese sagt.

  Nur wenn **beides** gilt, wandert die Schlagzeile in die andere Meldung:
  Haltestellen und Extras werden übernommen, der Zeitraum geweitet, Titel und
  Text der ausführlichen Meldung bleiben. Verworfen wird nichts, was nicht
  nachweislich woanders steht. Die Linien gehen dabei **nicht** in den
  Textvergleich ein — welche gemeint sind, klärt der Linien-Vergleich
  abschließend; sonst hinge die Regel daran, ob WL den Langtext zufällig mit
  „Linie 49: …“ eröffnet.

  Gegenprobe am gesamten Cache (75 Meldungen, davon 37 Störungen): Die Regel
  entfernt **genau die beiden** reklamierten Items und sonst nichts — auch
  nicht im Härtetest, der alle 75 in eine Kategorie zwingt. 34 der 37
  Störungen sind Schlagzeilen, aber nur bei Linie 49 steht eine ausführliche
  Meldung daneben, die sie abdeckt. `49A/50B: Mondweg` und
  `49A/50B: Hüttergasse` (zwei Straßen, ein Linienpaar) scheitern schon an
  (1) — genau der Fall, an dem das frühere pauschale `_identity`-Dedupe
  scheiterte.

* **Der Grund einer Kurzmeldung geht nicht mehr verloren (2026-09-17)**:
  `_strip_summary_category_prefix` wurde für die WL-*Hinweise* gebaut, deren
  HTML ein `<h2>Gleisbauarbeiten</h2>` vor den Fließtext setzt — dort ist das
  Wort eine durchgesickerte Überschrift, und dahinter geht der Satz weiter.
  Die *Kurzmeldungen* der Anzeigetafeln haben dieselbe Form und meinen etwas
  anderes:

  ```
  T: 12A: Betrieb ab Johnstraße U
  D: Gleisbauarbeiten
     Betrieb ab Johnstraße U
  ```

  Hier ist „Gleisbauarbeiten“ der **Grund** und das Einzige, was der Titel
  nicht ohnehin sagt. Nach dem Streichen blieb exakt der Titel übrig,
  `_summary_duplicates_title` leerte ihn folgerichtig — und das Item stand als
  Schlagzeile über einem nackten `[16.09.2026 – 17.09.2026]` im Feed, ohne zu
  verraten, warum. **11 der 75** Cache-Meldungen waren in diesem Zustand.

  Der Grund wird jetzt gerettet, in WLs eigener Formulierung
  (`Grund: Gleisbauarbeiten.` — der Langtext derselben Störung endet auf
  „Grund: Gleisschaden im Bereich Märzstraße 62.“). Das Streichen selbst
  bleibt **unangetastet**: Die Rettung greift ausschließlich dort, wo der
  Rumpf sonst leer bliebe. Solange nach dem Streichen noch ein Satz steht
  („Wegen Fortschreiten der …“), ändert sich nichts.

  Nur die kuratierten `_CATEGORY_PREFIX_WORDS` werden dabei zum `Grund:`. Der
  Zeilenumbruch in den Ticker-Texten ist nämlich **kein** Trenner, sondern ein
  Display-Umbruch mitten im Satz (`"Ersatzbus ab\nFloridsdorf <"`,
  `"Züge halten in\nTokiostraße"`) — eine Regel, die die erste Zeile blind als
  Grund läse, würde dort Unsinn erfinden. Diese 23 Meldungen bleiben deshalb
  bewusst ohne Rumpf.
* **Abdeckungshinweis: allgemein formuliert, beidseitig, und erst nach einer
  Stunde ohne Fahrt (2026-09-17)**:
  Der Hinweis über den Statistiken trug bis jetzt eine fest verdrahtete
  Ursache („Streckensperre – Bauarbeiten und Kabelbrand-Folgen") und war
  faktisch auf **eine** Richtung zugeschnitten. Beides ist jetzt weg. Er
  nennt **keine Ursache** mehr — das Ledger hält fest, *dass* Fahrten
  ausbleiben, nie *warum* —, funktioniert in **beide Richtungen** und
  fasst sie zusammen, wenn beide still sind:

  > Aktuell keine Fahrten von Wien Hbf Richtung **Praterstern** auf der
  > Stammstrecke (zuletzt am 14.08.2026). …
  >
  > Aktuell keine Fahrten von Wien Hbf Richtung **Meidling** und
  > **Praterstern** auf der Stammstrecke …

  **Auslöser ist eine Stunde ohne Fahrt**, und der Hinweis verschwindet von
  selbst, sobald die Richtung wieder meldet — ohne dass jemand Markdown
  anfasst. Genau das ist der Zweck: Die Sperre ist vorübergehend, der
  Wiederanlauf muss automatisch erfasst werden.

  Die Stunde allein wäre allerdings unbrauchbar gewesen. Beobachtungen
  treffen bestenfalls alle ~30 min ein, die p99-Lücke je Richtung liegt bei
  **3,5 h**, und die Stammstrecke pausiert **jede Nacht rund 3:41 h**
  (01:12 → 04:53, über 2026 hinweg bemerkenswert konstant). Über 4.324
  halbstündliche Ticks des gesunden Betriebs nachgespielt, hätte eine reine
  „seit 1 h still"-Regel den Hinweis bei **10,6 %** davon gezeigt — 87 %
  in den frühen Morgenstunden, wenn planmäßig nichts fährt. Ein Hinweis,
  der jede Nacht erscheint, ist einer, den niemand mehr liest.

  Die Stunde wird deshalb **gegen den Nachweis gemessen, dass überhaupt
  gefahren wird**, nicht gegen die Uhr:
  * **Eine Richtung still, die andere meldet** — die Gegenrichtung ist der
    Nachweis. Die stille Richtung wird genannt, sobald die Gegenrichtung
    seit deren letzter Fahrt **6 Fahrten** protokolliert hat. Diese Belege
    verfallen nicht, weshalb der Hinweis in der nächtlichen Pause einer
    laufenden Störung **nicht flackert**.
  * **Beide Richtungen still** — es gibt keine Gegenrichtung mehr, und ein
    dunkler Korridor um 03:00 ist ein Fahrplan, keine Störung. Erst **8 h**
    sagen etwas anderes: Die längste korridorweite Stille im gesunden
    Betrieb war 7:45 h (06.08.2026, eher ein Erfassungsausfall als ein
    Fahrplan), die Nachtpause 3:41 h. Acht Stunden sind der kleinste volle
    Stundenwert über beidem.

  Ergebnis derselben Nachspielung: **3 von 4.324 Ticks** — und alle drei
  sind dieselbe Episode, 14.08.2026 06:27 → 11:27, eine echte fünfstündige
  Lücke Richtung Praterstern am Morgen des Ausfalls. Im Normalbetrieb
  erscheint **kein** Hinweis. Der Ausfall selbst wird 3:03 h nach der
  letzten Fahrt erkannt und bleibt danach lückenlos erkannt.

  Getrennt davon die zweite Aussage: Dass eine Richtung *jetzt* still ist,
  heißt nicht, dass die **Zahlen darunter** verzerrt sind — eine vor einer
  Stunde verstummte Richtung steuert weiterhin Tausende Zeilen zum
  30-Tage-Fenster bei. Der Zusatz „kein Korridor-Gesamtwert" bzw.
  „ausschließlich ältere Daten" erscheint deshalb erst, wenn die Stille das
  Fenster tatsächlich ausgehöhlt hat.

  Was die Regel **nicht** behaupten kann: Bei dunklem Gesamtkorridor
  unterscheidet das Ledger nicht zwischen „es fuhr nichts" und „wir haben
  nichts beobachtet" — das ist Sache von `scripts/health_check.py`. Die
  Korridor-Formulierung behauptet daher nur, dass die Zahlen alt sind, und
  das stimmt in beiden Fällen.

  `docs/stats-summary.json` liefert der Website die **Belege** statt des
  Urteils (`last_seen`, `peer_rows_since`, dazu die Schwellen), damit
  `site.js` dieselbe Regel gegen die Uhr des Lesers auswertet und eine über
  Nacht offene Seite nicht eine eingefrorene Antwort zeigt.
* **Test-Job lief gegen seine Zeitgrenze (2026-09-17)**:
  Der `Run test suite`-Job in `.github/workflows/test.yml` trug
  `timeout-minutes: 20` mit der Begründung „~8000 Tests, 6–10 min auf einem
  GH-Runner, ~2x Reserve". Beides stimmt nicht mehr: Die Suite zählt
  **9.316 Tests**, `pytest` allein braucht auf dem Runner **18:13**, der
  ganze Job rund **20:12** samt Checkout, Installation, statischen Checks
  und Stations-Validierung. Die Grenze lag damit **unter** dem, was ein
  gesunder Lauf legitim benötigt.

  Die Folge war kein ehrliches Rot, sondern ein Münzwurf: Ob ein Lauf
  durchging, entschied die Tagesform des Runners und nicht die geprüfte
  Änderung. **Vier der zwölf Läufe davor wurden an der Wand abgebrochen**
  (4696, 4703, 4704, 4706 — auf PRs *und* auf `main`, quer über Autoren),
  und Lauf 4702 kam mit vier Sekunden Reserve durch. Am deutlichsten war
  Lauf 4706: `pytest` meldete `9316 passed, 2 skipped in 1093.97s`, und
  eine Sekunde später schlug der Timeout während des Coverage-Uploads zu —
  eine grüne Suite, als roter Check ausgewiesen. Das ist genau die Sorte
  Rauschen, nach der man aufhört hinzusehen.

  Die Grenze steht jetzt auf **35 Minuten**, etwa dem 1,7-fachen des
  langsamsten beobachteten gesunden Laufs. Ihr Zweck bleibt unangetastet:
  einen Hänger (feststeckender Test, Endlosrekursion, entlaufene Fixture)
  abfangen, statt GitHubs 360-Minuten-Vorgabe zu erben. Der feinere Riegel
  — `--timeout=60` pro Test aus der `pyproject.toml` — ist unverändert.

  Ausdrücklich **keine** Dauerlösung für die Laufzeit selbst: Wenn die
  Wandzeit stört, ist Parallelisierung der Hebel, nicht die nächste
  Erhöhung. Das wäre allerdings ein eigenes Vorhaben mit eigener
  Prüfrunde — die `isolate_stats_writes`-Fixture, die Datei-Lock-Tests und
  die Coverage-Erfassung müssten erst auf xdist-Sicherheit geprüft werden.
* **Dashboard lädt ~700 KB CSV weniger — und redet nicht mehr mit einer
  fremden Origin (2026-09-14)**:
  Die drei Statistik-Panels der Website zogen bei **jedem Seitenaufruf** die
  rohen Jahres-Ledger direkt von `raw.githubusercontent.com` —
  `stammstrecke_2026.csv`, `stoerungen_2026.csv`, `ausfaelle_2026.csv`,
  zusammen **705.085 Bytes** (gemessen am 13.09.) — und verdichteten sie im
  Browser zu ein paar KPI-Kacheln und Balken. Zwei Probleme, eines im
  Wachstum und eines in der Herkunft:

  * Die Ledger sind **Jahresdateien**. Ein Besucher im Dezember lädt die
    Januar-Zeilen mit; hochgerechnet ~1,3 MB allein für die Stammstrecke.
    Das Wachstum ist nicht theoretisch: Dieselben drei Dateien wogen am
    17.09. bereits **716.283 Bytes** — 11 KB in vier Tagen, die jeder
    Besucher mitlädt.
  * `raw.githubusercontent.com` ist **kein Auslieferungs-CDN**, hat eigene
    Rate-Limits und liegt außerhalb der GitHub-Pages-Zusage. Drosselte es,
    blieb die Seite erreichbar und jedes Diagramm scheiterte.

  Die Verdichtung lief ohnehin schon — einmal pro Tick, in
  `generate_markdown_stats.py`, für das Markdown-Dashboard. Dieselben Zahlen
  zusätzlich als `docs/stats-summary.json` zu schreiben kostet nichts und
  ersetzt 705 KB durch **~3 KB, die über das Jahr nicht wachsen**: Die
  Dimensionen sind fest (7 Wochentage, 24 Stunden, eine Handvoll Provider,
  Linien und Richtungen).

  **Die Aggregation ist dafür aus dem `--skip-dashboard`-Zweig heraus­gewandert.**
  Der Workflow rendert das Markdown nur im 00:00-Tick; eine Summary, die
  dort mitgehangen hätte, wäre auf einer Seite, die sich alle fünf Minuten
  aktualisiert, bis zu 24 Stunden alt gewesen. Gespart wird jetzt nur noch
  der Markdown-Render — der Teil, der den `_Automatisch erzeugt am ..._`-
  Zeitstempel alle 30 Minuten durch den Commit-Log treibt.

  Nebenwirkungen, alle in dieselbe Richtung:

  * **`connect-src` ist enger.** `raw.githubusercontent.com` steht nicht mehr
    in der CSP der Seite — eine Origin weniger, mit der die Seite überhaupt
    sprechen darf. Die `preconnect`/`dns-prefetch`-Hinweise auf diesen Host
    sind mit ihr entfallen.
  * **Eine Rechenstelle statt zwei.** Die „kritischen Verspätungen" wurden im
    Browser ein zweites Mal gezählt — genau die Doppelung, aus der einmal
    eine echte Drift entstand (`>=` auf der Website, `>` im Backend, eine
    9,0-Minuten-Beobachtung also mal gezählt und mal nicht). Die JS-Fassung
    ist entfallen; die Kachel zeigt, was das Backend gezählt hat.
  * **Die Live-Kachel wurde nicht ungenauer.** Der Mittelwert der letzten
    Stunde wurde bisher im Browser gebildet, mit der Begründung, er würde
    zwischen den 30-Minuten-Läufen sonst veralten. Die Begründung trug
    nicht: Der Ledger, aus dem gerechnet wurde, wird von genau diesem Lauf
    geschrieben. Der Wert kommt jetzt aus dem Tick, der den jüngsten
    Messwert angehängt hat — dieselbe Frische, 705 KB billiger.
  * **Der Abdeckungs-Hinweis bleibt abgeleitet.** Die Summary liefert je
    Richtung die *Belege* (Zeilen im Fenster, zuletzt gesehen), nicht das
    Urteil. Die Regel steht weiterhin nur an einer Stelle, und der Banner
    verschwindet von selbst, sobald die Nordrichtung wieder meldet — ohne
    dass jemand die Seite anfasst.

  `SUMMARY_SCHEMA_VERSION` versioniert das Format. Die Seite verweigert ein
  unbekanntes Format ausdrücklich, statt aus den Feldern zu rendern, die sie
  zufällig noch wiedererkennt — ein halbes Dashboard aus alten Zahlen liest
  sich wie Daten, nicht wie ein Fehler.

  Zwei projektweite Schreib-Regeln greifen bei der neuen Datei
  ausdrücklich, beide von den Sentinel-Walkern eingefordert und beide hier
  nicht bloß formal: `allow_nan=False` verhindert `NaN`/`Infinity` im
  Dokument — `JSON.parse` wirft darauf, ein einzelner nicht-endlicher
  Mittelwert hätte also nicht eine Kachel, sondern das **ganze** Dashboard
  in die Fehlerzeile geschickt (`_round_floats` bildet sie zusätzlich auf
  `null` ab). Und `scrub_trojan_source_primitives` entfernt die
  CVE-2021-42574-Angriffsbytes aus jedem erreichbaren String, bevor
  geschrieben wird: Provider-, Linien- und Richtungsnamen stammen aus
  fremden Feeds, und die Datei wird nach `main` committet und von Pages
  ausgeliefert. `ensure_ascii=False` bleibt, damit deutsche Inhalte im
  30-Minuten-Diff kompakt bleiben.

  `tests/test_dashboard_summary_source.py` (21 Tests) hält beide Seiten
  zusammen: Summary auch bei `--skip-dashboard`, leeres Live-Fenster als
  „n/a" statt „0,0 min", nullgepolsterte Stunden-Schlüssel (`"08"`, nicht
  `"8"` — die einzige Stelle, an der die beiden Schreibweisen auseinander­
  gehen), gerundete Floats gegen Commit-Rauschen, ein Feld-Vertrag gegen die
  ausgelieferte `site.js`, die CSP ohne den Fremd-Host sowie die beiden
  Schreib-Abwehren oben. Zwölf gezielte Mutationen wurden gegengeprüft —
  jede fällt auf.
* **i18n-Gate prüft jetzt auch die JS-eigenen Wörterbücher (2026-09-13)**:
  `check_i18n_coverage.py` verglich bisher ausschließlich die
  `data-i18n*`-Attribute der `site.html` gegen `I18N_EN`. Zeichenketten, die
  das JavaScript selbst erzeugt, haben aber keinen solchen Knoten und liegen
  in DE/EN-Wörterbuchpaaren: `CHART_TEXT_DE`/`_EN`, `WEATHER_TEXT_DE`/`_EN`,
  `COVERAGE_TEXT_DE`/`_EN`, `WEEKDAY_LONG_DE`/`_EN` und `STATUS_TEXT.de`/`.en`
  — zusammen 47 Schlüssel, vom Gate bislang keiner.

  Alle fünf lösen nach demselben Muster auf:

      dict[key] || <DE-Wörterbuch>[key] || key

  Ein Schlüssel, den nur die deutsche Seite führt, zeigt einem englischen
  Besucher also den **deutschen** Text — stumm, ohne Fehler in der Konsole.
  Genau diese Fehlerform hat der Eintrag darüber (Fehlermeldungen) gerade
  behoben; das Gate deckt sie jetzt für alle Wörterbücher ab, in beide
  Richtungen (ein nur-englischer Schlüssel lässt die deutsche Seite auf den
  nackten Schlüsselnamen zurückfallen) sowie leere EN-Werte.

  Die Paare werden **gefunden, nicht aufgezählt**: Ein künftiges
  `FOO_TEXT_DE`/`FOO_TEXT_EN` ist ab dem Tag seiner Einführung abgedeckt.
  Erkannt werden beide Bauformen — zwei Geschwister-Konstanten und ein
  verschachteltes `{ de: {…}, en: {…} }`.

  Schlüssel liest ein kleiner Scanner statt eines Musters. `WEEKDAY_LONG_DE`
  packt mehrere Einträge in eine Zeile; ein zeilenverankertes Muster hätte
  2 von 7 Wochentagen geprüft und das als Abdeckung ausgewiesen — schlimmer
  als gar kein Gate. Ein nicht verankertes Muster wiederum hätte `Hinweis:`
  **innerhalb** eines Werts als Schlüssel gelesen und Fehlalarm geschlagen.
  Der Scanner überspringt Zeichenketten und Kommentare am Stück und erkennt
  Schlüssel nur dort, wo welche stehen können: auf Ebene 0, am Anfang oder
  nach einem Komma.

  Heute sind alle fünf Paare deckungsgleich — der Befund war latent, nicht
  akut. `tests/test_i18n_coverage_gate.py` wächst um 11 Tests: je ein
  einseitiger Schlüssel pro Richtung, leerer EN-Wert, verschachtelte Form,
  mehrere Einträge pro Zeile, und drei Gegenproben gegen Fehlalarm
  (Doppelpunkt im Wert, Kommentare zwischen Einträgen, verschachtelte
  Wertobjekte).
* **Fehlermeldungen der Website blieben deutsch (2026-09-13)**:
  Das Dashboard rendert jede Störung als `<Präfix> <Detail>`. Das Präfix war
  immer ein Übersetzungsschlüssel, das **Detail** dagegen die rohe
  `Error.message` — und die waren fest verdrahtete deutsche Sätze. Ein
  englischsprachiger Besucher, dessen Feed nicht lud, las:

  > Feed could not be loaded: Feed konnte nicht geparst werden

  Schlimmer noch: `showError()` legte genau diesen deutschen Satz in
  `dataset.errorDetail` ab. `applyTranslationsToDom()` baut die Zeile bei
  jedem Sprachwechsel aus diesem Datensatz neu auf — der deutsche Teil blieb
  also für den Rest der Sitzung stehen, egal wie oft umgeschaltet wurde.

  Betroffen waren alle vier Fehlerzeilen (Feed, Störungen, Stammstrecke,
  Ausfälle) und drei Quellen: `Feed konnte nicht geparst werden`,
  `Feed ohne <channel>-Element` und `Keine CSV-Daten für … verfügbar.`

  Geworfen werden jetzt **Schlüssel** statt Sätze (`err-feed-parse`,
  `err-feed-no-channel`, `err-csv-missing`); `resolveErrorDetail()` löst sie
  zur Renderzeit in der aktuellen Sprache auf, und im Datensatz steht der
  Schlüssel — damit übersetzt der Sprachwechsel die Zeile vollständig mit.
  Der CSV-Fall braucht ein Argument (den Datensatznamen), das über einen
  C0-Trenner (`\u0001`) an den Schlüssel gehängt und in die `{name}`-Stelle
  der Vorlage gesetzt wird. Ein druckbares Trennzeichen (`:`, `|`) wäre hier
  falsch gewesen: Es kommt in echten Browser-Meldungen und in URLs vor.

  Meldungen **ohne** Schlüssel (`HTTP 503 – …`, `Failed to fetch`) kann das
  Projekt nicht übersetzen; sie werden unverändert durchgereicht statt
  verschluckt — `statusText()` liefert für einen unbekannten Schlüssel `""`,
  und genau daran hängt der Durchreiche-Zweig.

  `tests/test_site_error_i18n.py` (17 Tests) hält den Vertrag fest: geworfen
  werden Schlüssel und keine deutschen Sätze, beide Sprachen führen jeden
  `err-*`-Schlüssel, die englischen Texte unterscheiden sich von den
  deutschen und enthalten keine Umlaute, der Datensatz speichert den
  Schlüssel statt des fertigen Satzes, und der Sprachwechsel läuft über den
  Resolver. Sieben gezielte Mutationen der `site.js` wurden gegengeprüft —
  jede fällt auf.

  Mit im Modul: ein Abgleich des **ausgelieferten** Bundles gegen die Quelle
  (`optimize_site_assets.py --check`). Dieser Drift-Check lief bisher nur als
  Pre-Commit-Hook; wer ohne Hook committet, hätte eine veraltete
  `site.min.js` ausgeliefert und die Korrektur oben wäre für Besucher
  unsichtbar geblieben. Jetzt läuft er in der Test-Suite und damit in der CI.
* **EN-Feed zeigte Übersetzungen bereits ersetzter Schlagzeilen (2026-09-13)**:
  Der Übersetzungs-Cache in `data/first_seen.json` ist auf `(ident, field)`
  geschlüsselt. Ändert die Quelle den Text einer bestehenden Meldung — bei
  den Wiener Linien der Normalfall, etwa wenn aus einer angekündigten
  Sperre der laufende Betrieb wird —, blieb die alte englische Übersetzung
  stehen: Der deutsche Feed zeigte den neuen Stand, der englische den alten.
  Die Epoche (`_TRANSLATION_CACHE_EPOCH`) fängt das nicht ab, sie erkennt nur
  Änderungen an der Pipeline, nicht an der Eingabe.

  Der Cache merkt sich jetzt zusätzlich einen gekürzten SHA-256 des
  **Quelltexts** (`en_src`). Weicht er beim nächsten Lauf ab, wird neu
  übersetzt. Fehlt er (Alt-Einträge), gilt der Eintrag als gültig — das
  Fehlen bedeutet „vor dieser Änderung geschrieben" und nicht „falsch";
  entwertet werden die Alt-Einträge einmalig über die Epoche 5 → 6.

  Zwei kleinere Mängel an denselben Zeilen: Das Modell lieferte gelegentlich
  einen Titel, dessen Rumpf nach dem Linien-Präfix kleingeschrieben begann
  (`44: event Trains stop …`), und gelegentlich ein einzelnes, unpaariges
  Anführungszeichen. Beides wird jetzt nach der Übersetzung korrigiert.
* **Doku-Nachprüfung der Audit-Runde (2026-09-13)**:
  Eigene Kontrolle, ob die Änderungen aus PR #1790–#1803 vollständig
  dokumentiert sind. Vier Lücken gefunden und geschlossen:

  * **`docs/architecture.md` §5 war der ernste Fall.** Der Abschnitt
    „Namens-Eindeutigkeits-Vertrag" nennt `Lokalbahn` × 4 verteilt über
    5,6 km ausdrücklich als **legitime** WL-Datenlage, deren Validator-Gate
    in PR #1452 bewusst entfernt wurde. Genau diese Gruppe meldet der in
    PR #1801 ergänzte Alias-Kollisions-Check seit 2026-09-12 wieder — ohne
    Notiz las sich das wie eine Rücknahme jener Entscheidung. Der Abschnitt
    trennt beides jetzt: Der Name-Vertrag bleibt aufgehoben, der neue Check
    fragt etwas anderes (beanspruchen mehrere Stationen denselben
    **normalisierten Alias** und sind sich über den Ort uneins?) und meldet,
    statt zu blockieren. In PR #1801 stand „`architecture.md` nicht
    betroffen" — das war falsch.
  * **`docs/development.md`** nannte „zehn Issue-Kategorien" und listete
    zehn; mit `alias-key collisions` sind es elf.
  * **Der Uhr-Einfrier-Fix** (`_FrozenDatetime`, PR #1798) hatte keinen
    eigenen Eintrag, während der strukturgleiche Sentinel-Fix einen bekam —
    nachgetragen.
  * **Das Audit** führte Befund 6 in der Abschnittsüberschrift noch als
    „Alias-Teil behoben, Stammstrecke-Teil offen", obwohl die
    Übersichtstabelle beide Hälften bereits als erledigt zeigte.

  Geprüft und in Ordnung: `AGENTS.md` („Priorität der Ausgaben"), der
  persistierte `docs/stations_validation_report.md` (vom Workflow bereits
  regeneriert, enthält den neuen Abschnitt), keine Platzhalter-Reste, und
  `README.md` berührt keines der geänderten Themen.

  Nicht geändert, aber erwähnenswert: `python -m src.cli stations validate
  --fail-on-issues` bricht ab — schon **vor** dieser Runde, wegen des
  bestehenden `1 geographic duplicates`. Der neue Check hat den Exit-Code
  nicht gekippt.
* **„Nichts zu melden" gilt nicht mehr als Fehler (2026-09-12)**:
  `_merge_result` behandelte jeden Provider mit null Items gleich — eine
  `WARNING`-Zeile plus ein Eintrag in der Warnungsliste des Laufberichts. Zwei
  verschiedene Dinge tragen aber dieselbe Form:

  * Ein leerer **Cache** für Wiener Linien, ÖBB oder Baustellen ist ein
    Problem — der Cache sollte Daten halten und tut es nicht.
  * Ein leeres **Stammstrecke**-Ergebnis ist die S-Bahn-Stammstrecke im
    Normalbetrieb. Die meisten Builds sehen so aus.

  Gebaut wird alle 30 Minuten, der gesunde Fall erzeugte also rund um die Uhr
  eine Warnung. Damit war „läuft" von „defekt" nicht mehr zu unterscheiden —
  das Gegenteil dessen, wozu eine Warnung da ist.

  Welcher Fall vorliegt, wird jetzt **bei der Registrierung erklärt**
  (`register_provider(..., empty_is_normal=True)`) statt hier am Namen
  geraten. Die Vorgabe ist die strenge: Ein Provider, der nichts sagt, behält
  die Warnung — ein künftiger Provider kann also nicht durch Unterlassung in
  den leisen Zweig rutschen. Gesetzt ist das Flag ausschließlich für die
  Stammstrecke.

  Die Unterscheidung überlebt bis in die Zusammenfassungszeile, weil ein
  Dashboard, das nur `:empty` sieht, „nichts zu melden" nicht von „keine
  Daten" trennen kann:

  ```
  oebb:ok(11 Items); stammstrecke:ok-empty(Keine Vorfälle); wl:empty(0 Items, Keine aktuellen Daten)
  ```

  Beide Richtungen sind gepinnt. Ein Fix, der nur das Rauschen abstellt,
  hätte das Signal mit abgestellt: Der leere WL-Cache warnt weiterhin, die
  Cache-Alerts landen unverändert im Detail, und ein Provider ohne Flag wird
  streng behandelt.

  Reine Beobachtbarkeit, kein Feed-Inhalt. Damit ist Audit-Befund 6
  vollständig erledigt — und das Audit vom 2026-09-12 abgearbeitet.
* **Testinfrastruktur: Uhr im WL-Dedupe-Test eingefroren (2026-09-12,
  nachdokumentiert)**:
  `test_two_messages_about_one_demonstration_merge_into_one` lief den ganzen
  Nachmittag grün und kippte um 19:00 Wiener Zeit von selbst. Der Test trägt
  die Live-Zeitstempel vom 2026-09-12, und die **informativere** der beiden
  Meldungen endet um `19:00:00+02:00`. `fetch_events` filtert über
  `_is_active(start, end, datetime.now(UTC))` — ab da fiel genau die Meldung
  weg, deren Titel gewinnen soll, und übrig blieb der dürftige Titel, dessen
  Verdrängung der Test verhindern soll.

  `_FrozenDatetime` hält `now()` auf 2026-09-12 15:00 Wiener Zeit fest: nach
  jedem `start` und vor jedem `end` in dieser Datei. Eingefroren statt relativ
  gerechnet, weil die absoluten Zeitstempel der Beleg sind und weil die
  Tages-Komponente über `D=…` in `_wl_identity` eingeht — ein Lauf kurz vor
  Mitternacht verteilte die Meldungen sonst auf zwei Tage. Entschärft
  zugleich die zweite, noch nicht gezündete Bombe: `_traffic_info` setzte
  `end` auf 2026-09-30, die Datei wäre am 1. Oktober komplett rot geworden.

  Ging in PR #1798 mit, hatte aber keinen eigenen Eintrag — nachgetragen,
  damit die Testinfrastruktur-Fixes dieser Runde vollständig verzeichnet sind.
* **Sentinel-Allowlists brechen nicht mehr an fremden Änderungen (2026-09-12)**:
  Zwei Sentinel-Walker adressierten ihre Ausnahmen über **absolute
  Zeilennummern**. Jede Einfügung oberhalb einer Fundstelle verschob sie, und
  der Test schlug dann mit einer Meldung fehl, die auf einen `json.dumps()`
  zeigte, den die Änderung nie berührt hatte.

  Das ist an einem Nachmittag zweimal passiert: PR #1799 (zwei erweiterte
  Regex-Konstanten) verschob die Fundstellen in `_identity_for_item` von
  2689/2698 auf 2744/2753, PR #1800 (zwei neue Hilfsfunktionen) erneut auf
  2809/2818. Jedes Mal kostete es einen vollen CI-Durchlauf zur Diagnose und
  eine Korrektur an drei Stellen. Beide Walker pinnten zudem dieselbe Zeile
  `src/places/hafas_client.py:289` — eine Einfügung dort brach zwei Sentinels
  gleichzeitig.

  Die ALLOWLIST ist jetzt eine Abbildung von **(Pfad, umschließender Scope)**
  auf die dort erwartete **Anzahl** ausgenommener Fundstellen:

  ```
  ("src/build_feed.py", "_identity_for_item"): 2
  ```

  Die Anzahl hält den gröberen Schlüssel ehrlich: Eine ganze Funktion
  freizugeben würde einen **neuen** ungeschützten Writer durchwinken, der
  später dort hinzukommt. Mit der Anzahl wird genau der gemeldet, während die
  dokumentierten still bleiben. Eine Anzahl anzuheben ist damit eine bewusste
  Handlung, die wie bisher in einen PR mit eigener Begründung gehört.

  Beide Richtungen sind nachgewiesen: 60 Leerzeilen oberhalb der Fundstellen
  lassen die Sentinels grün, ein zusätzlicher ungepinnter `json.dumps()` in
  derselben Funktion lässt sie fehlschlagen.

  Die Fehlermeldung sagt im Überschreitungsfall ausdrücklich, dass die
  genannte Zeile **die überzählige nach Position** ist und nicht zwingend die
  neu hinzugekommene — innerhalb eines Scopes sind die Fundstellen für den
  Walker ununterscheidbar, und das vorzutäuschen wäre schlechter als es zu
  sagen.

  Betrifft nur die Testinfrastruktur: `test_sentinel_allow_nan_writer_audit_walker`
  und `test_sentinel_trojan_source_audit_walker`. Kein Produktionscode, kein
  Feed-Inhalt.
* **Stationsalias-Kollisionen: Log beruhigt, Prüfung nachgerüstet (2026-09-12)**:
  `_station_lookup` protokollierte pro Prozess **111 WARNING-Zeilen** über
  doppelte Stationsaliase — bei vier Provider-Läufen pro Build genug, um eine
  echte Warnung zu begraben. Es waren **22** eindeutige Schlüssel; der Rest
  war Wiederholung, weil die Aliaslisten bis zu sieben Schreibweisen derselben
  Sache tragen (`Bhf. Hütteldorf`, `Bahnhof Bhf. Hütteldorf`,
  `Bhf. Hütteldorf Bahnhof`), die jede für sich auf denselben normalisierten
  Schlüssel kollidieren.

  **Der im Audit vermutete Widerspruch war keiner.** „0 alias issues" des
  Validators und 111 Loader-Warnungen messen Verschiedenes:
  `_find_alias_issues` prüft, ob ein Eintrag überhaupt eine brauchbare
  Aliasliste hat (vorhanden, nicht leer, enthält Name/`bst_code`/`vor_id`).
  Stationsübergreifende Kollisionen hat es nie geprüft — die 0 war also gar
  keine Aussage darüber.

  Nachgemessen an den Live-Daten: Die 111 Zeilen zerfallen in zwei Klassen.
  69 betreffen **dieselbe Station unter zwei Namen** (`Wien Bhf. Hütteldorf
  (WL)` gegen `Wien Hütteldorf`) — harmlos, egal wer gewinnt. 42 betreffen
  **vier verschiedene Badner-Bahn-Stationen**, die alle einen generischen
  `Lokalbahn`-Alias beanspruchen. Das ist eine echte Kollision zwischen
  verschiedenen Orten, und der Lookup ist nicht kosmetisch: `station_info`
  speist `is_in_vienna`, das im ÖBB-Provider darüber entscheidet, ob eine
  Meldung überhaupt in den Feed kommt.

  In allen **44** (Schlüssel, Verlierer, Gewinner)-Tripeln stimmen beide
  Seiten in `in_vienna` überein — heute kann keine Kollision eine Antwort
  ändern. Das ist eine Tatsache über die Daten, keine Eigenschaft des Codes.
  Ausgerechnet `Wien Inzersdorf Lokalbahn (WL)`, die einzige dieser Stationen
  **in** Wien, beansprucht den generischen Schlüssel gar nicht, weil alle ihre
  Aliase `Inzersdorf` tragen. Glück in der Datenlage, nicht Absicht.

  Deshalb zwei Hälften, und die zweite macht die erste vertretbar:

  * **Loader** (`src/utils/stations.py`): `WARNING` → `DEBUG`, und eine Zeile
    je normalisiertem Schlüssel statt je Schreibweise. 111 → 22, und raus aus
    dem Warnungsstrom.
  * **Validator** (`src/utils/stations_validation.py`): neue Prüfung
    `_find_alias_collision_issues`, die eine Kollision nur meldet, wenn die
    Kandidaten sich in `in_vienna` unterscheiden **oder** weiter als 2 km
    auseinanderliegen. Beide Signale werden gebraucht: Eine Station knapp
    außerhalb der Stadtgrenze liegt Meter neben einer innerhalb (Distanz
    allein würde das gekippte Urteil verfehlen), und zwei Stopps mit gleichem
    Urteil können 30 km trennen (das Urteil allein würde die Distanz
    verfehlen). Gruppiert nach Kandidatenmenge, nicht nach Schlüssel — sonst
    stünde dieselbe Tatsache elfmal da, genau das Rauschen, das gerade
    beseitigt wurde.

  Auf dem Live-Verzeichnis meldet der Validator damit **eine** Kollision: die
  vier Lokalbahn-Stationen. Kein Aufrufer nutzt `--fail-on-issues`, die CI
  bleibt also grün; die Kollision steht ab jetzt im Report und in der
  Zusammenfassungszeile. Ein Test pinnt, dass der Validator den Normalisierer des
  Loaders **weiterverwendet** — `stations_validation` importiert
  `_normalize_token` aus `stations`, es ist dieselbe Funktion. Bekommt der
  Validator später einen eigenen, driften beide still auseinander und die
  Prüfung meldet Kollisionen, die der Loader nie hat. Genau diese
  Verwechslung zweier ähnlich benannter Größen hat den Befund ursprünglich
  falsch gerahmt.

  Nicht mitbehoben: der zweite Teil des Befunds (leerer Stammstrecke-Provider
  als Warnung). Er ändert die Empty-Semantik für alle Provider und gehört in
  eine eigene Änderung. Damit ist Audit-Befund 6 zur Hälfte erledigt.
* **Bugfix: Übersetzung lief für Stationstitel bei jedem Build neu
  (2026-09-12)**:
  Im Build-Log stand jedes Mal dieselbe Zeile:

  ```
  Cached EN translation for …TRACKINFO&910806/title equals source; retrying.
  ```

  `_cached_translation` wertet „gespeicherte Übersetzung ist identisch mit der
  Quelle" als Beleg für einen früheren kaputten Build, der den deutschen Text
  als „Übersetzung" abgelegt hat — und übersetzt neu. Für einen Titel, der nur
  aus Stationsnamen besteht (`Wien Hauptbahnhof ↔ Felixdorf`), **ist** die
  korrekte englische Fassung aber die deutsche. Die Bedingung galt damit
  dauerhaft: Das Modell lief bei jedem Build erneut und lieferte nie ein
  anderes Ergebnis. Gemessen vor dem Fix: fünf Builds, fünf Modellläufe. Im
  gespeicherten Zustand tragen 227 Einträge einen solchen Titel; gebaut wird
  alle 30 Minuten.

  Ein gespeicherter String allein kann „ein kaputter Build hat hier die Quelle
  hinterlegt" nicht von „das Modell lief und seine Ausgabe entspricht der
  Quelle" unterscheiden. Ein Marker kann es, weil er **nur dort** geschrieben
  wird, wo sich die beiden Fälle trennen: nachdem `_translate_text_attempt`
  ein Ergebnis ungleich `None` geliefert hat. Ein fehlgeschlagener Versuch
  liefert `None` und speichert nichts — ein Build, der nicht übersetzen
  konnte, kann ein Feld also nie als „identisch ist korrekt" markieren. Der
  Drift-Schutz behält damit seine Zähne; er wird eingegrenzt, nicht entfernt.
  Bestandseinträge ohne Marker werden weiterhin einmal neu übersetzt und dabei
  markiert.
  Der Marker liegt neben `en` statt darin, damit die `en`-Teilstruktur eine
  homogene Feld-zu-String-Abbildung bleibt, und wird bei der Epoch-Eviction
  zusammen mit `en` verworfen: Eine spätere Epoch mit besserem Glossar kann
  denselben Text sehr wohl übersetzen. Er wird außerdem zurückgenommen, sobald
  ein Lauf doch eine echte Übersetzung liefert, und der Self-Heal für
  Rest-Platzhalter schlägt ihn weiterhin — ein rohes Sentinel darf nie aus dem
  Cache kommen.
  Reine Laufzeitkosten, kein Feed-Inhalt: Ein schleifendes Item erzeugt so oder
  so dieselbe englische Ausgabe. Damit ist Audit-Befund 7 erledigt.
* **Bugfix: Die englische Übersetzung erfand Liniennummern (2026-09-12)**:
  Im EN-Feed verschmolzen zwei Linien zu einer Nummer, die es nicht gibt:

  ```
  DE  43A/44A/844/N43/44B: Gleisbauarbeiten (Phase 2)
  EN  43A/44A844/N43/44B: track construction works (phase 2)
  ```

  `_LINE_ENTITY_RE` in `src/build_feed.py` maskiert Linien-Tokens, damit das
  Übersetzungsmodell sie nie zu sehen bekommt. Die alte Form
  `U[1-6]|S[0-9]+|[1-9][0-9]?[A-Z]?` deckte nur **58 der 71** Linien-Tokens
  ab, die in den Live-Caches vorkommen. `844` ging ungeschützt ans Modell,
  und das hat den Schrägstrich daneben verschluckt.
  Die 13 Lücken, davon vier erst beim Nachmessen an den Live-Daten gefunden:

  | Token | Was fehlte |
  | --- | --- |
  | `844` | dreistellige Regionalbusse — nur zwei Stellen erlaubt |
  | `86AR` | zweibuchstabiges Suffix — nur ein Buchstabe erlaubt |
  | `U6E` | U-Bahn-Verstärker — nach `U<n>` war kein Suffix erlaubt |
  | `N8`, `N20`, `N29`, `N43`, `N46`, `N49`, `N65`, `N66`, `N71` | Nachtbusse — das `N`-Präfix fehlte ganz |
  | `D` | Straßenbahn D — ein einzelner Buchstabe hat keine erkennbare Form |

  Jede Alternative ist eine **echte Obermenge** der bisherigen, kein Token
  verliert seinen Schutz; ein Test pinnt das. Insbesondere behält `S[0-9]+`
  seinen unbegrenzten Ziffernlauf, statt auf den real verkehrenden Bereich
  S1–S80 verengt zu werden — Verengen ist die einzige Richtung, die etwas
  ungeschützt lassen könnte.
  Die Straßenbahnlinien `D` und `O` bekommen ein eigenes Muster mit
  Kontext-Gate (`_TRAM_LETTER_LINE_RE`): maskiert wird nur im Linienkontext —
  am Titelanfang vor dem Doppelpunkt, neben einem Schrägstrich oder nach
  „Linie". In „Vitamin D" oder „Ausgang D" bleibt der Buchstabe unangetastet.
  Dass `86AR`, `N71` und `N31` im auditierten Lauf heil durchkamen, war
  Glück, kein Schutz: Ein unmaskiertes Token überlebt nur so lange, wie das
  Modell es zufällig in Ruhe lässt. Die Tests prüfen deshalb die
  **Maskierung**, nicht die Modellausgabe.
  Betrifft ausschließlich `docs/feed.en.xml`; der deutsche Feed entsteht aus
  dem unübersetzten Text. Damit ist Audit-Befund 3 erledigt.
* **Bugfix: Abgeschnittene Baustellen-Titel sahen nach unserem Fehler aus
  (2026-09-12)**:
  Drei von 22 Titeln standen gekappt im deutschen Feed, einer davon mit einem
  Anführungszeichen, das nie schließt:

  ```
  … und Apostelgasse bis Schlachthausgas          (100 Zeichen)
  … bis Unbenannte Verkehrsfläche und Rad         (100 Zeichen)
  … auf Seite "Otto Wagner Hofpavillon             (99 Zeichen)
  ```

  Die Stadt Wien kappt `BEZEICHNUNG` bei 100 Zeichen; das Projekt kürzt Titel
  nicht selbst. Den fehlenden Text kann niemand zurückholen — aber ein
  unangekündigter Abbruch sieht auf einem Info-Display wie ein Defekt auf
  unserer Seite aus.
  `scripts/update_baustellen_cache.py` markiert solche Titel jetzt mit einer
  Ellipse und entfernt ein unpaariges Anführungszeichen. Erkannt wird an zwei
  **unabhängigen** Signalen statt an der Länge allein: `len >= 100` (harte
  Obergrenze erreicht) **oder** ein unpaariges `"`. Das zweite fängt den
  99-Zeichen-Fall — upstream kappt bei 100 und entfernt danach Leerraum, und
  über die Länge allein wäre das nicht von einem echten 99-Zeichen-Titel zu
  unterscheiden.
  Der Text selbst bleibt unangetastet: Ein abgeschnittenes Wort („… bis
  Schlachthausgas…") ist die Wahrheit über das, was die Quelle liefert; es
  wegzukürzen würde auf Verdacht Information vernichten.
  **Wichtig für die Identität:** Die GUID leitet sich weiterhin vom
  **Rohtitel** ab. `_feature_to_event` nutzt den Titel als Fallback, wenn die
  Ebene keine `OGD_ID` liefert — käme die Kosmetik dort an, sähe jede gekappte
  Baustelle beim Deploy schlagartig neu aus und ihr `first_seen` würde
  zurückgesetzt. Ein Test pinnt das.
  Von 22 Live-Titeln ändern sich genau die drei gekappten; der längste
  unbeschädigte (94 Zeichen) bleibt unberührt. Damit ist Audit-Befund 4
  erledigt.
* **Bugfix: ÖBB-Items trugen das Veröffentlichungsdatum statt des Bauzeitraums
  (2026-09-12)**:
  Drei gleichzeitig laufende Sperren auf derselben Strecke standen im Feed
  ununterscheidbar nebeneinander:

  ```
  Wien Hauptbahnhof ↔ Gramatneusiedl   [Seit 24.08.2026]
  Wien Hauptbahnhof ↔ Gramatneusiedl   [Seit 24.08.2026]
  Wien Hauptbahnhof ↔ Gramatneusiedl   [Seit 10.09.2026]
  ```

  Ihr einziger Unterschied — der Bauzeitraum — stand ungeparst am Anfang der
  Beschreibung (`03.10.2026 - 05.10.2026<br/><br/>Wegen Bauarbeiten …`).
  `build_feed` hat dieses Präfix als Metadatum erkannt, aber nur **verworfen**
  (`_DATE_RANGE_PREFIX_RE` / `_DATE_SINGLE_PREFIX_RE`). Drei Folgen:

  * `starts_at` blieb das Veröffentlichungsdatum und `ends_at` leer — die
    Zeitzeile las sich „[Seit 10.09.2026]" für eine Sperre, die erst am
    05.12.2026 beginnt. Auf einem Info-Display, das genau diese Klammer zeigt,
    ist das nicht bloß uninformativ, sondern **falsch**. Betroffen waren
    **alle elf** gecachten ÖBB-Items.
  * Die drei Sperren waren ohne Öffnen der Beschreibung nicht zu unterscheiden.
  * Erledigte Bauarbeiten schieden nie aus: `_drop_old_items` Regel 1 braucht
    ein `ends_at`.

  `src/providers/oebb.py` liest den Zeitraum jetzt über `_parse_period` in
  `starts_at`/`ends_at` (Beginn 00:00, Ende 23:59:59 Europe/Vienna, damit ein
  Item am letzten Tag nicht um Mitternacht verschwindet). Neue Renderlogik
  brauchte es nicht: `format_local_times` erzeugt daraus von sich aus
  `03.10.2026 – 05.10.2026`, `Am 01.11.2026` oder `Ab …`.
  Fehlt das Präfix, bleibt das bisherige Verhalten (Veröffentlichungsdatum als
  Start, kein Ende); unmögliche (`31.02.`) und verdrehte Zeiträume werden
  abgewiesen, statt den Abruf abzubrechen. `pubDate` behält seine
  RSS-Bedeutung. Sortierung und Altersfilter sind nicht betroffen — beide
  richten sich nach `first_seen`, nicht nach `starts_at`.
  Damit ist Audit-Befund 5 erledigt.
* **Prioritätsregel: der deutsche RSS-Feed hat Vorrang (2026-09-12)**:
  `AGENTS.md` hält jetzt unter „Priorität der Ausgaben" fest, dass
  `docs/feed.xml` das Produkt ist und alles andere — englische Übersetzung,
  Statistik, Dashboard, Logs — dahinter zurücktritt. Hintergrund: Der Feed wird
  über **EasySignage auf Full-HD-Fernsehern** ausgespielt. Daraus folgen
  Eigenschaften, die ein Feed-Reader nicht hat und die bei jeder Änderung am
  Feed-Inhalt mitzudenken sind — die Item-Zahl ist hart begrenzt (`MaxItems`,
  aktuell 10), gelesen wird aus Entfernung und ohne Interaktion, und die
  Anzeige rotiert. Ein doppelter Eintrag ist dort kein Schönheitsfehler,
  sondern **verdrängt eine andere Störung vollständig**.
  Das Feed-Audit `docs/archive/audits/audit-2026-09-12-feed-darstellung.md` ist
  entsprechend neu geordnet: Die Übersichtstabelle weist pro Befund die Wirkung
  auf den deutschen Feed aus, und die Reihenfolge richtet sich danach statt
  nach technischem Gewicht. Befund 3 (EN-Übersetzung verstümmelt
  Liniennummern) war der nächste Kandidat und rückt nach hinten; nach vorn
  rücken Befund 5 und 4, die den deutschen Feed betreffen.
* **Bugfix: Ein Feuerwehreinsatz belegte zwei Feed-Plätze (2026-09-12)**:
  Beim Neupriorisieren im live ausgelieferten `docs/feed.xml` gefunden:

  ```
   3. 64A: Fahrtbehinderung wegen Feuerwehreinsatz
   4. 64A: Feuerwehreinsatz Betrieb ab Gregorygasse
  ```

  Zwei der zehn Plätze für **einen** Einsatz — also eine andere Störung, die
  gar nicht erscheint. Dieselbe Ursache wie beim 38A-Fall: `feuerwehreinsatz`
  fehlte in `TITLE_TOPIC_TOKENS` (`src/providers/wl_text.py`), obwohl seine
  Geschwister `polizeieinsatz` und `rettungseinsatz` längst dort standen. Der
  vorige Fix hatte nur die zwei damals belegten Wörter ergänzt und die Reihe
  nicht zu Ende gedacht. Ergänzt; die beiden 64A-Meldungen werden jetzt im
  Bucketing zum informativeren Titel zusammengeführt. Gleiche Einsätze auf
  anderen Linien bleiben getrennt — das Linien-Set ist Teil des Bucket-Keys,
  ein Test hält das fest.
* **Bugfix: Dieselbe Störung stand zweimal im Feed (2026-09-12)**:
  Nach dem Dedupe-Fix (PR #1791) standen zwei Meldungen zur selben Sperre im
  Feed:

  ```
  38A: Demonstration
  38A: Demonstration Haltestelle Kahlenberg wird nicht eingehalten
  ```

  Ursache ist eine Lücke in `TITLE_TOPIC_TOKENS` (`src/providers/wl_text.py`):
  Fehlt das Ursachen-Wort dort, fällt `_topic_key_from_title` auf den ganzen
  Titel-Kern zurück — dann ist jede Formulierungsvariante ein eigenes Topic,
  ein eigener Bucket und am Ende ein eigenes Feed-Item. `demonstration` und
  `veranstaltung` fehlten, obwohl sie in dieselbe Klasse gehören wie
  `polizeieinsatz` oder `rettungseinsatz`.
  Vorher fiel das nicht auf, weil `_dedupe_items` solche Paare über den groben
  `_identity` (Linie + Tag) blind zusammenwarf. Das war keine Lösung, sondern
  eine Maskierung — es traf genauso Meldungen, die wirklich verschieden waren
  (`49A/50B: Mondweg` gegen `49A/50B: Hüttergasse`, zwei Straßen).
  Zusammengeführt wird jetzt an der richtigen Stelle, im Bucketing von
  `fetch_events`: Dort gewinnen der bessere Titel **und** die bessere
  Beschreibung, Haltestellen und Extras werden vereinigt. Aus den beiden
  38A-Meldungen wird ein Item, das beide schlägt — der informative Titel mit
  dem ausführlichen Text.
  **Korrektur am Eintrag zu PR #1791:** Die dort als „drei verschiedene
  Störungen" bezeichnete Linie-44-Trias (`Veranstaltung …`) ist keine — es ist
  ein Ereignis, aus drei Blickwinkeln gemeldet. Mit `veranstaltung` als Token
  wird daraus ebenfalls ein Item. Der belastbare Fall für „wirklich
  verschieden" bleibt `49A/50B`.
* **Bugfix: Verschiedene WL-Störungen wurden als „Duplikat" verworfen
  (2026-09-12)**:
  `python -m src.cli feed lint` meldete `entfernte Duplikate: 4` bei 83
  Rohitems — und es waren keine Duplikate. Drei verschiedene Störungen auf
  Linie 44 am selben Tag teilten den Schlüssel `wl|störung|L=44|D=2026-09-11`,
  ebenso `49A/50B: Mondweg` und `49A/50B: Hüttergasse` (zwei verschiedene
  Straßen). Jeweils eines wurde veröffentlicht, die übrigen verschwanden
  spurlos aus dem Feed.
  `_wl_identity` (`src/providers/wl_fetch.py`) mischte den `topic_key` nur
  dann in den Schlüssel, wenn das Linien-Set **oder** das Startdatum fehlte —
  in der Annahme, Linie + Tag identifizierten eine Störung bereits eindeutig.
  Die Wiener Linien veröffentlichen aber regelmäßig mehrere unabhängige
  Störungen für eine Linie an einem Tag. `_dedupe_items` (`build_feed.py`)
  vergleicht `_identity` zuerst und erreicht den feineren `guid` nie.
  Der `topic_key` wird jetzt **immer** eingemischt. Das kostet keine
  Stabilität, weil er auf dieser Ebene kein neues Signal ist: Der Bucket-Key
  in `fetch_events` und der `guid` jedes Items werden beide bereits aus
  `(category, topic_key, Linien-Set)` gebaut. Daraus folgt zweierlei — echte
  Duplikate erreichen den Vergleich gar nicht erst, weil das Bucketing sie
  vorher zu einem Item verschmilzt; und `first_seen` wandert nicht, weil
  `_state_key_for_item` es am `guid` führt, der sich ohnehin mitbewegt.
  Gemessen am Live-Stand: Von 53 gecachten WL-Items lösen 48 über den `guid`
  auf, 5 sind neu, **0** hingen am Legacy-`_identity`-Fallback.
  Wirkung, mit aufgefrischtem Cache gegengeprüft: `entfernte Duplikate: 4` →
  **0**, „Keine strukturellen Probleme gefunden", `Status=error` → `success`.
* **Bugfix: ÖBB-Doppelstation — der Fix davor griff im Live-Pfad nicht
  (2026-09-12)**:
  Nach dem Merge des vorigen Fixes lief ein voller Refresh, und
  `cache/oebb_c40d21/events.json` blieb **byte-identisch**: `fetch_events()`
  erzeugte `S 4: Bauarbeiten: kein Halt in Lind-Rosegg Föderlach: Lind-Rosegg
  Föderlach` unverändert weiter. Ursache ist die Reihenfolge in
  `_clean_title_keep_places`: Der Redundanz-Check lief nur auf dem **Rohtitel**,
  die Normalisierung der Stationsnamen (`_clean_endpoint`, entfernt
  „Bahnhst"/„Bahnhof") erst danach. Upstream schreibt die beiden Hälften in
  unterschiedlicher Schreibweise — `… Lind-Rosegg Bahnhst Föderlach Bahnhof:
  Lind-Rosegg Föderlach` —, also sind sie zum Prüfzeitpunkt nicht wörtlich
  gleich; erst die Normalisierung macht die Dopplung buchstäblich, und der
  Kategorie-Join-Zweig setzt sie wieder zusammen. Der Check ist jetzt die
  Funktion `_drop_redundant_suffix` und läuft **zweimal**: auf dem Rohtitel wie
  bisher und erneut auf dem fertig zusammengesetzten Titel. Ergebnis:
  `S 4: Bauarbeiten: kein Halt in Lind-Rosegg Föderlach`. Das Herausziehen in
  eine Funktion senkt die Komplexität von `_clean_title_keep_places`, der zweite
  Aufruf fügt keinen Zweig hinzu — C901-Baseline (26) unverändert.
* **Audit: Feed-Quellen, Verarbeitung und Darstellung (2026-09-12)**:
  `docs/archive/audits/audit-2026-09-12-feed-darstellung.md`. Alle fünf Quellen
  antworten fehlerfrei; sieben Befunde in der Darstellung, davon einer (oben)
  behoben. Offen und belegt: vier von 83 Meldungen werden als „Duplikat"
  verworfen, obwohl es verschiedene Störungen sind (`_wl_identity` mischt den
  `topic_key` nur in Randfällen ein); die EN-Übersetzung verstümmelt
  Liniennummern, weil `_LINE_ENTITY_RE` dreistellige Linien (`844`), Nachtbusse
  (`N43`) und die Straßenbahnlinien `O`/`D` nicht maskiert (`43A/44A/844/…` →
  `43A/44A844/…`).
* **Bugfix: ÖBB-Titel nannte die Station doppelt (2026-09-12)**:
  Im Feed stand `S 4: Bauarbeiten: kein Halt in Lind-Rosegg Föderlach:
  Lind-Rosegg Föderlach`. `_clean_title_keep_places` in
  `src/providers/oebb.py` hat für genau dieses Muster einen Redundanz-Check
  („Text: Station" → nur Text, wenn die Station im Text vorkommt), er war
  aber am **ersten** Doppelpunkt verankert. Stellt der Upstream dem Titel ein
  Kategorie-Label voran, liegt das redundante Paar hinter dem zweiten
  Doppelpunkt: Der Check verglich „Bauarbeiten" gegen den gesamten Rest, fand
  keine Redundanz und ließ die Dopplung stehen. Der greedy Kopf
  (`^(.+):\s+([^:]+)$` statt `^([^:]+):\s+(.+)$`) verschiebt den Anker ans
  letzte `: ` und findet das Paar; für Titel mit genau einem Doppelpunkt ist
  das Verhalten unverändert, Uhrzeiten (`17:30`) bleiben unberührt, weil das
  Muster ein Leerzeichen nach dem Doppelpunkt verlangt. Der Titel lautet jetzt
  `S 4: kein Halt in Lind-Rosegg Föderlach`. **Nachtrag 2026-09-12:** Die hier
  ursprünglich behauptete Selbstheilung des Caches ist nicht eingetreten — der
  Live-Titel entsteht über einen anderen Pfad, siehe den folgenden Eintrag.
* **Bugfix: ÖBB-Titel verlor die Stunde einer Uhrzeit (2026-09-12)**:
  Beim Verifizieren des Titel-Fixes aufgefallen. Die Präfix-Schleife in
  `_clean_title_keep_places` entfernt ein führendes `Kategorie: `-Label und
  hat dabei auch den Doppelpunkt einer **Uhrzeit** getroffen: `_is_category`
  prüft nur die Wörter, also gilt `"Sperre 17"` als Kategorie. Aus
  `"Sperre 17:30 Uhr Wien Hbf"` wurde `"30 Uhr Wien"` — die Stunde war weg.
  Die Schleife bricht jetzt ab, wenn der Doppelpunkt zwischen `HH` und `MM`
  steht; das gewöhnliche Label-Stripping (`"Störung: A ↔ B"`) ist unverändert.
* **Bugfix: Dashboard zählte Schwerverspätungen anders als der Feed (2026-09-12)**:
  `docs/assets/site.js` filterte `r.delay >= 9` und beschriftete die Kachel mit
  „≥ 9 Minuten" / „≥ 9 minutes". Das Backend (`src/feed/stammstrecke.py`) und
  alle daraus abgeleiteten Ausgaben — Feed, `docs/statistik.md`, README-Block —
  zählen dagegen **strikt** `obs.delay_minutes > DELAY_THRESHOLD_MINUTES` und
  schreiben `Kritische Verspätungen (> 9 min)`. Eine Beobachtung von exakt
  9.0 min erschien damit auf der Website, aber nirgends sonst: dieselben Daten
  ergaben je nach Ansicht zwei verschiedene Zahlen.
  Die Website zeigt jetzt, was die Logik tut:
  * Neue Konstante `DELAY_THRESHOLD_MIN` (spiegelt `DELAY_THRESHOLD_MINUTES`),
    Filter auf `> DELAY_THRESHOLD_MIN` umgestellt.
  * Das Kachel-Label wird aus derselben Konstante gebaut
    (`` `> ${DELAY_THRESHOLD_MIN} min` ``) statt aus einem fest verdrahteten
    Übersetzungs-String — Anzeige und Filter können nicht mehr auseinanderlaufen.
    Die damit unbenutzte `sub-over-9`-Übersetzung ist entfernt; „min" ist in
    beiden Sprachen identisch.
  * `docs/assets/site.min.js` neu erzeugt (inkl. Cache-Busting-Hash in
    `docs/site.html`).
  * `tests/test_dashboard_delay_threshold.py` pinnt die JS-Konstante an den
    Python-Wert und die Vergleichsrichtung an `>`.
* **Bugfix: Force-Push-Race löschte einen gemergten PR aus `main` (2026-09-12)**:
  Der Merge-Commit von PR #1783 (`a62fa59`) war zwei Sekunden nach dem Merge aus
  `main` verschwunden — überschrieben vom `SEO Verify`-Workflow. Ursache:
  `git-auto-commit-action` führt unmittelbar vor dem Push einen eigenen
  `git fetch` aus und schärft damit das `--force-with-lease` auf genau den
  Commit nach, den es schützen soll; der Push degradiert faktisch zu `--force`
  (die Action protokolliert die Divergenz sogar und pusht trotzdem). Das Option
  wurde in `seo-guard.yml`, `update-stations.yml` und `manual-full-refresh.yml`
  ersatzlos entfernt — nach dem vorgelagerten Rebase ist der Push ein
  Fast-Forward, und bei einem Rennen wird er abgelehnt statt fremde Commits zu
  überschreiben. `update-cycle.yml` ist nicht betroffen (eigene Retry-Schleife
  mit Rebase vor jedem Versuch). Vollständige Analyse samt Job-Log-Nachweis:
  [`docs/archive/audits/audit-2026-09-12-force-push-history-loss.md`](docs/archive/audits/audit-2026-09-12-force-push-history-loss.md).
  Die verlorene Datei `docs/archive/audits/audit-2026-09-11.md` ist aus dem
  verwaisten Commit wiederhergestellt.
* **Bugfix: Anzeigefehler aus `audit-2026-09-11.md` (2026-09-12)**:
  * **Doppelt escapte `&` im `<description>`**: Der Sink escapte `&`, `<` und
    `>`, ElementTree escapte danach ein zweites Mal — veröffentlicht wurde
    `GmbH &amp;amp; Co KG`. Der neue `_escape_description_markup` escapt nur
    noch die spitzen Klammern und überlässt `&` dem XML-Serialisierer. Der
    Injection-Schutz bleibt vollständig: Ein Tag kann ausschließlich aus einem
    **rohen** `<` entstehen, und das passiert diesen Sink weiterhin nicht (der
    End-to-End-PoC in `tests/test_description_html_injection.py` bleibt grün).
  * **`Minuten` statt `min` im Stammstrecken-Event** (`src/feed/stammstrecke.py`):
    Die Description der Feed-Items lautete „Durchschnittliche Verspätung von
    1.5 Minuten …" und widersprach damit der `min`-Konvention. Betraf den Feed
    selbst, nicht nur das Dashboard.
  * **Wiener Bezirke im EN-Feed**: `Bezirk` wurde je nach Satzkontext mal als
    „District", mal als „Area" übersetzt. Glossar-Einträge machen es
    deterministisch; `_TRANSLATION_CACHE_EPOCH` auf 5 angehoben.
* **Bugfix: Anzeigefehler im Feed — Titel-Verstümmelung, doppelte Linienkürzel,
  zusammengeklebte Wörter, EN-Übersetzungsartefakte (2026-09-11)**:
  Sammelbehebung der in den 2026-09-Audits
  (`docs/archive/audits/audit-2026-09-05.md` … `audit-2026-09-10.md`,
  `audit-title-bauarbeiten26-2026-09-09.md`,
  `audit-title-line-deduplication-2026-09-07.md`) dokumentierten
  Darstellungsfehler. Alle Befunde wurden vor dem Fix gegen die live
  ausgelieferten `docs/feed.xml` / `docs/feed.en.xml` und die Provider-Caches
  verifiziert; jeder Fix ist durch Regressionstests in
  `tests/test_feed_display_defects_2026_09.py` abgesichert.
  * **`17A: Bauarbeiten26`** (`src/providers/wl_text.py`): Die Datums-Regex in
    `_tidy_title_wl` kannte nur vierstellige Jahre, entfernte aus
    `"Bauarbeiten ab 14.09.26"` also nur `" ab 14.09."` und ließ die `26`
    stehen — der anschließende Whitespace-Collapse klebte sie an das
    vorangehende Wort. Die Jahres-Alternation deckt jetzt zwei- **und**
    vierstellige Jahre ab (geordnet, mit `(?!\d)`-Guard, damit ein
    missgebildetes Jahr den Titel unangetastet lässt statt ihn halb zu
    strippen). Zusätzlich läuft der Datums-Strip nun **vor** dem generischen
    Label-Strip: sonst verlor `"Bauarbeiten ab 14.09.2026"` zuerst sein Label
    und der Rest `"ab 14.09.2026"` stand am String-Anfang, wo die Regex
    (`\s+ab`) nicht mehr griff — veröffentlicht wurde der inhaltsleere Titel
    `"17A: ab 14.09.2026"`.
  * **`3A: 3A Netzänderung …`** (`src/providers/wl_lines.py`,
    `src/build_feed.py`): WL-Payloads nennen die Linie gelegentlich doppelt
    (einmal als Doppelpunkt-Präfix, einmal als erstes Wort des Titelrumpfs).
    `_extract_prefix_lines` konsumierte nur das Präfix, `_ensure_line_prefix`
    setzte es erneut davor. Neu: `_strip_redundant_line_token` entfernt die
    Wiederholung aus Titelrumpf **und** Beschreibung — mit den im Audit
    geforderten Guards (kanonische **und** einzelne Linien als Kandidaten,
    längste zuerst; Wortgrenze plus Separator-Pflicht, damit `1: 10er
    Garnitur` und `10: 10. Bezirk` unangetastet bleiben; Dauer-Guard für
    `5: 5 Minuten Verspätung`; nie leerer Rumpf).
  * **Zusammengeklebte Wörter** (`src/utils/text.py`, `src/build_feed.py`):
    Die OGD-Baustellen-Beschreibungen verlieren upstream ihre Zeilenumbrüche
    ohne Ersatz-Leerzeichen; ausgeliefert wurde u. a.
    `"DerFußgängerverkehr kann aufrecht gehalten werden.Nähere Informationen"`
    und `"Bezirk(Hadikgasse)"`. `repair_glued_words()` fügt die fehlenden
    Leerzeichen rein additiv wieder ein (Satzzeichen + Großbuchstabe,
    Klammer-Klebung, lower→Upper innerhalb eines Wortes) und schützt dabei
    Ordinalzahlen, Datums-/Zeitangaben, Abkürzungen (`Gerasdorf b. Wien`),
    Firmierungen (`GmbH`) und Binnen-I-Formen (`MitarbeiterInnen`).
    Nebeneffekt: Weil die Satzgrenzen wieder erkennbar sind, endet die
    Summary dieser Items nicht mehr mitten im Satz mit `…`.
  * **Abgeschnittene Label** (`src/build_feed.py`): Endete die
    180-Zeichen-Kürzung direkt hinter einem Label, blieb im Feed
    `"… Zentralfriedhof. Grund …"` stehen — angekündigt, aber
    weggeschnitten. `_trim_truncation_tail` verwirft jetzt das Label
    selbst statt nur seinen Doppelpunkt.
  * **EN-Feed — nackter Sentinel `X4X`** (`src/build_feed.py`): Das
    NMT-Modell verstümmelte `XENT…X4X` bis auf den bloßen Index; der
    Rest-Detektor verlangte das Präfix und sah ihn nicht, sodass
    `"… ↔X4X Wien Stadlau"` gecacht und ausgeliefert wurde.
    `_RESIDUAL_PLACEHOLDER_RE` erkennt jetzt auch die präfixlose Form
    (beidseitig begrenzt, kurzer Index) — die Übersetzung gilt damit als
    fehlgeschlagen und das Item fällt auf den deutschen Quelltext zurück.
  * **EN-Feed — `Uhr` → „clock"/„watch"** (`src/build_feed.py`): Die
    Uhrzeit-Nachsilbe hat kein englisches Gegenstück; jede Modell-Ausgabe war
    falsch. `_normalise_for_translation` entfernt sie (nur zeit-verankert)
    vor Glossar und Masking — der deutsche Feed bleibt unberührt, weil der
    gesamte Übersetzungspfad EN-only ist.
  * **EN-Feed — `Einstieg`, `Bereich`, `Straßenfest`, `Bahnhst`**
    (`src/build_feed.py`): Neue Glossar-Einträge. `Einstieg` endete bislang
    im Straßennamen-Schild (`…stieg`) und blieb deutsch
    (`"Einstieg for Vorgartenstraße"`); `Bereich` wurde als Zahlenbereich
    gelesen (`"Hernalser range Hauptstraße"`); `Straßenfest` zerfiel in
    `Straße` + `fest` (`"maintenance of the road fence"`); `Bahnhst` blieb
    unübersetzt.
  * **EN-Feed — `4. Tor` vs. `4. Gate`** (`src/build_feed.py`): Die
    Tor-Nummer im WL-Haltestellennamen (`Zentralfriedhof, 4. Tor`) wird als
    Einheit maskiert und bleibt verbatim — vorher übersetzte dasselbe Feed
    die gleiche Haltestelle mal mit „Gate", mal mit „Tor". Der Pass läuft
    **vor** dem Linien-Pass, dessen Zahlen-Shape sonst nur die Ziffer
    maskiert und das Substantiv übersetzbar zurücklässt.
  * `_TRANSLATION_CACHE_EPOCH` auf 4 angehoben, damit bereits gecachte
    EN-Übersetzungen durch die verbesserte Pipeline neu berechnet werden.
* **Bugfix: EN-Feed — verstümmelte Masking-Platzhalter beseitigt (2026-06-01)**:
  Im englischen Feed (`docs/feed.en.xml`) erschienen in manchen Item-Titeln rohe
  Masking-Sentinels (z. B. `XENT…X1X/XENT…X2X: XENT…X0X`) statt der übersetzten
  Linien/Stationen, während die Description desselben Items korrekt war. Ursache:
  Das NMT-Modell (Helsinki `opus-mt-de-en`, SentencePiece) **verstümmelt** die
  opaken Platzhalter — beobachtet wurden ein gedropptes Hex-Zeichen im
  Per-Prozess-Nonce, ein kleingeschriebenes Präfix (`XGLO` → `XGLo`), ins
  Englische „übersetzte" Nonce-Fragmente (`…de…` → `…en…`, ein verirrtes `from`)
  und ein abgeschnittener Index. Jede dieser Verstümmelungen lässt das
  **exakt-Nonce**-Unmasking scheitern, sodass der Sentinel verbatim durchrutscht,
  als „Übersetzung" gecacht (`data/first_seen.json`) und auf Cache-Hits ungeprüft
  ausgeliefert wird. Titel sind anfälliger als Summaries, weil sie fast nur dicht
  gepackte Platzhalter (`Linie/Linie: Station`) ohne übersetzbaren Text enthalten.
  Vier Verteidigungslinien in `src/build_feed.py`:
  * **Safety-Net**: Ein Rest-Platzhalter nach dem Unmask (nonce-agnostischer
    Detektor `_RESIDUAL_PLACEHOLDER_RE`) wertet die Übersetzung als
    fehlgeschlagen → sie wird **nicht** gecacht und das Item fällt verbatim auf
    den deutschen Quelltext zurück (DE↔EN-Inhalts-Parität bleibt gewahrt).
  * **Cache-Self-Heal**: Ein bereits vergifteter Cache-Hit wird als Miss
    behandelt und neu übersetzt, sodass Alt-Poison nie mehr ausgeliefert wird.
  * **Qualitäts-Bypass**: Rein-Entity-Titel (`86A/87A: Wiedgasse` — nur
    Platzhalter + Satzzeichen, kein `XGLO`) überspringen das Modell ganz und
    werden direkt entmaskiert — korrektes Englisch ohne Modell-Risiko, ohne
    DE-Fallback.
  * **Daten-Bereinigung**: Die 13 bereits vergifteten Cache-Werte in
    `data/first_seen.json` zurückgesetzt; der nächste Build berechnet sie über
    die gehärtete Pipeline neu.
* **Feed-Filter & Dedup: Korrektheits-Welle (Bugs b1–b14, 2026-06-01)**:
  Eine Reihe verifizierter Filter-Fehler behoben, die echte Wien-Meldungen
  fälschlich verwarfen oder irrelevante Meldungen aufnahmen. Jeder Fix ist
  durch dedizierte Regressionstests abgesichert:
  * **ÖBB-Routen-Erkennung** (`src/providers/oebb.py`): Die Routen-Regexes
    `_ZWISCHEN_`/`_VON_NACH_`/`_STRECKE_PLAIN_RE` haben Grenzwort-Token
    erhalten (`zu`/`zur`, `über`/`via`, Prädikatsverben) — die Standard-
    Formulierung „… kommt es zwischen X und Y **zu** …" überdehnte zuvor
    den zweiten Endpunkt und verwarf die echte Wien-Route (b1). Ein
    beschreibendes Schluss-Substantiv im Einzelstation-Titel
    („Wien Meidling Stellwerk") wird nicht mehr als impliziter unbekannter
    Endpunkt fehlgedeutet (`_TITLE_NOISE_WORDS` um Infrastruktur-Nomen
    ergänzt, b2). Wetter-präfigierte Bahn-Störungen („Hochwasser:
    Gleissperrung …") werden gerettet (`_TRANSIT_KEYWORD_RE` um
    rail-spezifische Compound-Sperrungen + `Zugverspätung`, b3).
  * **Strikter Modus** (`OEBB_ONLY_VIENNA`): Das bloße Arealwort „Wien"
    kanonisiert nicht mehr zu einer Phantom-Station (b10), und eine allein
    stehende Pendler-Erwähnung zählt nicht mehr als relevant (b12) — siehe
    `docs/reference/oebb_provider_logic.md`.
  * **Fuzzy-Dedup** (`src/feed/merge.py`): „Wien"/„Vienna" sind jetzt
    Stoppwörter (verhindert das Verschmelzen verschiedener Routen),
    Bahnhof-Abkürzungen werden vor der Tokenisierung normalisiert
    (`Hbf`/`Bhf`/`Bf` → `…bahnhof`, sodass „Wien Hbf" und „Wien
    Hauptbahnhof" weiter zusammengeführt werden, b6), und unterschiedliche
    Bahnsteig-/Gleis-Nummern (`_platform_numbers`) verhindern eine
    Verschmelzung verschiedener Bahnsteige (b7).
  * **Baustellen** (`src/providers/baustellen.py`): Die `\bu-/s-bahn`-Token
    tragen eine führende Wortgrenze (kein Fehltreffer mehr auf
    „Hochschaubahn", b4), und `oepnv_lead` zerschneidet Sätze
    abkürzungsbewusst (`_split_into_sentences`, kein Bruch an „Nr." /
    „3. März", b5).
  * **Stationsverzeichnis**: Opake Betriebsstellencodes mit ≤ 3 Zeichen
    werden aus dem Wien-Erkennungs-Regex entfernt (Mindest-Alias-Länge
    3 → 4 in `src/utils/stations.py`, b11); der Alias-Generator blockt die
    Müll-Aliase „aug"/„am" an der Wurzel (`_GENERIC_ALIAS_BLOCKLIST` in
    `scripts/enrich_station_aliases.py`), 40 fehlerhafte Aliase aus
    `data/stations.json` entfernt (b8/b9).
  * **Feed-Rendering** (`src/build_feed.py`): Mehrmonatige Enddaten bleiben
    erhalten (180-Tage-Kappe → `feed_config.ABSOLUTE_MAX_AGE_DAYS` = 540,
    b13); doppelte U-Bahn-Linien-Präfixe („U2: U2 …") werden erkannt
    (`_baustellen_title_names_station` akzeptiert `U1`–`U6`, b14).

* **Feed-Pipeline: Robustheits- & Korrektheitsfixes (Review-Funde #1–#8, 2026-06-01)**:
  Projektweite Prüfung jenseits des Relevanzfilters; verifizierte Fehler
  behoben (jeweils mit Regressionstest):
  * **Wiener-Linien-Titel** (`src/providers/wl_fetch.py`): Ein reiner
    Satzzeichen-Titel („---") fällt auf „Meldung" zurück, statt als bloße
    Linien-Codes („U1/U2") ohne Beschreibung zu erscheinen (#1).
  * **Feed-Sortierung** (`src/build_feed.py`): Eine in der Zukunft liegende
    `pubDate` wird auf „jetzt" gekappt und rangiert nicht mehr vor aktuellen
    Items (#2); ein Fehler beim Zusammenführen eines Provider-Ergebnisses
    wird pro Provider isoliert und verwirft nicht mehr alle bereits
    eingesammelten Items (`_drain_completed_futures`, #3).
  * **Stammstrecke** (`scripts/update_stammstrecke_status.py`): Ein leerer
    `rtDate`/`rtDepDate` (statt fehlend) deaktiviert nicht mehr die
    Mitternachts-Heuristik — verhinderte Schein-Verspätungen von ≈ −1430 min
    (#4).
  * **`.env`-Parser** (`src/utils/env.py`): Ein nicht geschlossenes
    Anführungszeichen (`KEY="abc`) liefert den dekodierten Inhalt statt des
    streunenden Quotes — schützt Tokens/Credentials vor Korruption (#5).
  * **Stationsvalidierung** (`src/utils/stations_validation.py`): Die
    Identitätsfelder (`bst_code`) akzeptieren `str | int` wie die
    Geschwister-Validatoren; ein ganzzahliger `bst_code` wird nicht mehr
    stillschweigend übersprungen (#8).

* **Dashboard: Wetter-Widget im Header (Wien, 2026-05-30)**:
  Der Header zeigt jetzt links neben der Marke ein kleines Wetter-Symbol
  plus die aktuelle Temperatur in °C für Wien. Als Abfrage-Koordinaten
  dienen die des **Wiener Hauptbahnhofs aus dem Stationsverzeichnis**
  (`data/stations.json`, Eintrag „Wien Hauptbahnhof", 48.186116 /
  16.374399). Datenquelle ist das **GeoSphere-Austria-Modell AROME**
  über die Open-Meteo-API, direkt im Browser abgefragt und gemeinsam mit
  den Verkehrsdaten über `loadAll()` im 5-Minuten-Takt aktualisiert. Die
  Abfrage nutzt eine **Fallback-Kette** (erster Treffer gewinnt), da der
  offizielle OpenAPI-Spec nur `/v1/forecast` dokumentiert: primär
  `/v1/forecast` mit
  `current=temperature_2m,weather_code,is_day&models=geosphere_arome_austria`,
  dann der dedizierte Endpunkt `/v1/geosphere_arome_austria` mit
  zeitzonensicher (über `timeformat=unixtime`) gewählter aktueller Stunde
  der `hourly`-Reihe, zuletzt Best-Match als Notnagel — so zeigt das
  Widget verlässlich einen Wert, ohne sich auf nur teilweise
  dokumentierte Endpunkte zu verlassen. Das Symbol ist ein monochromes
  Inline-SVG (WMO-Wettercode →
  Sonne/Mond/Wolke/Regen/Schnee/Nebel/Gewitter inkl. Tag-/Nacht-Variante),
  Tooltip und `aria-label` (`role="img"`) werden zweisprachig (DE/EN)
  gesetzt. CSP `connect-src` um `https://api.open-meteo.com` erweitert
  (plus `dns-prefetch`-Hinweis); ein fehlgeschlagener Wetterabruf lässt
  den globalen Feed-Status unberührt — das Widget behält dann seinen
  letzten Wert bzw. den `–`-Platzhalter. Die reservierte Breite des
  Temperatur-Slots verhindert Layout-Shift (kein CLS).
* **Backup-Cron: Freshness-Gate gegen redundante API-Last (2026-05-26)**:
  Der neue stündliche Sicherheits-Cron lief bisher rein additiv zu IFTTT
  und verursachte ~+24 Ticks/Tag (VAO ~48→~72/Tag — innerhalb des durch
  den Preflight hart gedeckelten 100/Tag-Budgets, aber an gesunden Tagen
  komplett redundant). Neu prüft ein **Freshness-Gate** vor den
  Fetch-/Build-/Publish-Schritten bei `schedule`-Läufen, ob bereits ein
  Tick innerhalb der letzten ~35 min committet hat (Signal: Commit-Zeit
  von `data/vor_request_count.json` / `docs/feed.xml`), und überspringt
  dann die gesamte API-/Build-Arbeit. Ergebnis: an gesunden Tagen ~0
  Extra-API-Abfragen, voller Schutz nur bei echtem IFTTT-Ausfall.
  IFTTT- (`repository_dispatch`) und manuelle (`workflow_dispatch`) Läufe
  bleiben ungegated. Unbekannt/Parse-Fehler ⇒ „run" (fail-safe).
* **Robustheit: 30-Minuten-Zyklus durchgehärtet (Tier 1–3, 2026-05-26)**:
  Folgeschritt zur Action-Download-Resilienz — `update-cycle.yml` an allen
  verbleibenden Fehlerstellen abgesichert, damit der wichtigste Workflow
  des Projekts nicht an transienten oder kosmetischen Problemen scheitert:
  * **Statistik-Step entkoppelt** (`continue-on-error`): Der kosmetische
    README-/Dashboard-Render lief vor dem Commit ohne Fehlertoleranz —
    ein Crash dort brach den Job ab und **verhinderte die
    Feed-Veröffentlichung**. Jetzt orange Annotation statt Blockade.
  * **torch-Self-Heal ohne Netzwerk auf dem gesunden Pfad**: Der Step rief
    bei *jedem* Tick unbedingt `pip install torch` auf — ein transienter
    `download.pytorch.org`-Ausfall färbte den Lauf rot. Jetzt
    Import-Check zuerst (null Netzwerk-I/O, wenn torch im Cache liegt),
    sonst Install mit Retry; fehlt torch endgültig, degradiert nur der
    EN-Feed für einen Tick (kein harter Fehler).
  * **Pre-Publish-Sanity-Check**: `feed.xml`/`feed.en.xml` werden vor dem
    Commit auf Wohlgeformtheit + nicht-leer geprüft; bei Defekt wird der
    Publish übersprungen (letzter guter Feed bleibt online).
  * **Reconcile + Commit/Push zu einem Never-Fail-Block konsolidiert**:
    entfernt den letzten harten `exit 1`-Pfad der Publish-Sequenz; das
    Reconcile auf konkurrierende Pushes steckt jetzt im Retry-Loop.
  * **Step-Timeouts** (Build feed 5 min, Statistik 2 min) fangen Hänger
    deutlich vor dem 10-min-Job-Limit ab; **git-Netzwerk-Timeouts**
    (`LOW_SPEED_LIMIT/TIME`) bremsen hängende push/pull-Sockets aus.
  * **Backup-Cron** (`schedule: '17 * * * *'`, off-cadence) als reines
    Sicherheitsnetz gegen verlorene Ticks / IFTTT-Ausfall — IFTTT bleibt
    primärer :00/:30-Treiber; das VAO-100/Tag-Budget ist durch den
    `preflight_quota_check`-Gate weiterhin garantiert (Backup-Ticks bauen
    notfalls nur aus den Free-API-Caches neu).
  * **`PAGES_BASE_URL` schedule-robust** aus `$GITHUB_REPOSITORY` statt
    `${{ github.event.repository.name }}` (auf `schedule`-Events leer) und
    **git-Identität einmalig** direkt nach dem Checkout gesetzt.
* **Robustheit: 30-Minuten-Zyklus übersteht Action-Download-Ausfälle und
  publiziert mit Retry + Never-Fail (2026-05-26)**: Der Lauf vom
  2026-05-26 12:30 UTC von `update-cycle.yml` starb bereits in der
  Setup-Phase („Prepare all required actions"), weil
  `stefanzweifel/git-auto-commit-action` nicht von `codeload.github.com`
  geladen werden konnte (404 „after 1 attempts"). Ein per `uses:`
  referenzierter Action-Download wird vor dem ersten Step aufgelöst —
  ein Retry/Fallback im Workflow kann dort nicht mehr greifen, der Job
  ist tot, bevor irgendein Step läuft. Fix: Der finale Commit-/Push-
  Schritt nutzt jetzt Inline-`git` (auf dem Runner vorinstalliert, kein
  Download) statt der externen Action. `git add -A` bildet das frühere
  `add_options: -A` 1:1 ab; bei „nichts zu committen" ist der Schritt ein
  sauberer No-op. Der Push läuft mit bis zu 4 Versuchen und
  exponentiellem Backoff (2/4/8 s) und re-synchronisiert zwischen den
  Versuchen via `git pull --rebase --autostash` auf konkurrierende Pushes
  (`update-stations.yml` sonntags, `seo-guard.yml` täglich liegen in
  eigenen Concurrency-Lanes); `--force-with-lease` bleibt erhalten.
  Rebase-Konflikte werden zugunsten der lokal gebauten Vollregeneration
  aufgelöst (append-only CSV-Ledger lösen sich ohnehin über
  `merge=union`). Schlägt jeder Versuch fehl, beendet der Schritt mit
  `::warning::` statt rotem Status — die zuletzt veröffentlichten Daten
  bleiben stehen und der nächste ~30-Minuten-Tick baut neu auf. Damit ist
  die wichtigste Pipeline des Projekts gegen den beobachteten transienten
  CDN-Ausfall **und** gegen Push-Fehler abgesichert. Der bestehende
  Vertrag aus `tests/test_en_feed_workflow_deps.py` (torch-CPU-Install,
  HuggingFace-Cache, `feed build`) bleibt unberührt.
* **Doku: HAFAS-Profil wird wöchentlich (nicht pro Tick) aktualisiert
  (2026-05-26)**: Die Docstrings in `src/places/hafas_client.py` und
  `scripts/sync_hafas_profile.py` sagten „before each cron tick" bzw.
  „so the cron pipeline always picks up the freshest credentials" und
  erweckten so den Eindruck, das Profil werde alle 30 Minuten geladen.
  Tatsächlich läuft `sync_hafas_profile.py` ausschließlich im
  *wöchentlichen* `update-stations.yml` (Sonntags 01:00 UTC); der
  30-Minuten-Zyklus liest nur das committete `data/hafas_profile.json`
  und lädt nichts nach (der Laufzeit-Client cached zudem in-process).
  Das Profil wird damit höchstens einmal pro Woche geladen. Docstrings
  entsprechend präzisiert.
* **Sicherheit: Stored-XSS im veröffentlichten `<content:encoded>`-Feld
  geschlossen (2026-05-24)**: `_compose_description` (`src/build_feed.py`)
  bettete den reinen Text aus `summary`/`time_line` un-escaped in den
  RSS-`<content:encoded>`-CDATA-Body ein – das einzige Feed-Feld, das
  Feed-Reader als HTML rendern. Da `html_to_text`
  (`HTMLParser(convert_charrefs=True)`) entity-kodierte Spitzklammern
  dekodiert, konnte eine kompromittierte/MITM-behaftete Upstream-Quelle
  (`&lt;img onerror=…&gt;` bzw. die doppelt-escaped Form bei ÖBB-RSS) ein
  ausführbares `<img onerror=…>`-Tag in jeden Abonnenten-Reader schleusen
  (Stored XSS). Fix: kontextkorrektes HTML-Encoding der Text-Teile via
  `html.escape(part, quote=False)` am gemeinsamen DE/EN-Chokepoint; nur das
  vom Builder selbst erzeugte `<br/>` bleibt aktiv. CDATA-als-Text-Senken
  (`<title>`) bleiben bewusst un-escaped (CDATA dekodiert keine Entities).
  Abgedeckt durch `tests/test_content_encoded_html_injection.py`
  (Reader-genaue `HTMLParser`-PoC, scheitert vor dem Fix). Die Zeilennummern
  der `allow_nan`-Writer-Walker-Allowlist (`_identity_for_item`) wurden an den
  durch den neuen `import html` verschobenen Block angepasst (2359/2368 →
  2360/2369).
* **SEO/GEO: `llms.txt`-Generator, Sitemap-Batching & JSON-LD-Sentinel
  (2026-05-24)**:
  * `scripts/generate_llms_txt.py` (neu) erzeugt `docs/llms.txt` nach dem
    [llms.txt-Standard](https://llmstxt.org/): eine kuratierte,
    Markdown-formatierte Karte der informationsdichtesten Seiten für
    LLM-/KI-Crawler (H1 + Summary-Blockquote, Abschnitte Dokumentation /
    API-Referenz / How-to / Feeds). Titel und Beschreibungen der Referenz-
    und How-to-Seiten stammen aus deren vorhandenem Front-Matter, sodass
    neue Seiten automatisch erscheinen; die übrigen Einträge sind statisch
    kuratiert. URLs werden über `generate_sitemap._to_url` erzeugt und
    teilen den `SITE_BASE_URL`-Host-Pin (`_base_url`), damit `llms.txt`-
    Links nie von ihren `sitemap.xml`-Pendants abweichen. Die Ausgabe ist
    deterministisch (keine Zeitstempel), sodass der tägliche `seo-guard`-
    Lauf nur bei echten Doku-Änderungen committet. Abgedeckt durch
    `tests/scripts/test_generate_llms_txt.py`.
  * `.github/workflows/seo-guard.yml`: neue Schritte „Refresh llms.txt"
    und „Verify llms.txt" (H1-Pflicht, `SITE_BASE`-Referenz, mindestens
    ein Link) analog zur Sitemap-Prüfung; der Auto-Commit erfasst jetzt
    `docs/sitemap.xml` **und** `docs/llms.txt`.
  * **Sitemap-Performance — N+1 aufgelöst**: `scripts/generate_sitemap.py`
    startete in `_last_modified()` pro Datei einen eigenen
    `git log -1`-Subprozess (61 Prozess-Starts ≈ 215 ms im aktuellen
    Baum). Ersetzt durch `_git_lastmod_map()`, das die Historie in **einem**
    gestreamten `git log --name-only`-Aufruf durchläuft und abbricht,
    sobald jede angefragte Datei ihren neuesten Commit gezeigt hat
    (≈ 12 ms, ~18×). Semantik (Commit-Datum → mtime-Fallback →
    Zukunfts-Clamp) bleibt identisch; abgedeckt durch
    `tests/scripts/test_generate_sitemap_lastmod.py` (inkl.
    „genau ein git-Prozess"-Regression).
  * **JSON-LD-Sentinel** (`tests/test_site_html_structured_data.py`):
    stellt sicher, dass der bestehende `application/ld+json`-Block in
    `docs/site.html` erhalten und valide bleibt (Schema.org-`@context`,
    `@type`), damit die KI-/Such-Sichtbarkeit nicht unbemerkt regrediert.
* **Performance: CLS ≈ 0.94 → ≈ 0 und WebP-Varianten für die zwei
  Bild-Assets (2026-05-17)**:
  * Zwei frische Lighthouse-Läufe (13.0.2) gegen
    `https://origamihase.github.io/wien-oepnv/site.html` zeigten, dass
    der Asset-Payload nach dem ersten Optimierungs-Pass (Eintrag oben)
    zwar passte, das mobile Profil aber bei `cumulative-layout-shift =
    0.937` (Desktop 0.598) hängen blieb. Schuld waren drei dynamische
    Ladestellen, die ihren Platzbedarf vor dem CSV-Render nicht
    reservierten: die zehn `.bars`-Container in den drei `chart-grid`
    Sektionen (ohne jedes Skelett), die `[data-year-label]`-Spans
    (Text-Sprung „–" → „YYYY") und die KPI-/Feed-Skelette, die mit
    `min-height: 92/96 px` deutlich unter der Endhöhe der späteren
    Inhalte saßen. Auf Mobil verschob sich daher `section#ausfaelle`
    um 0.937 Layout-Score, auf Desktop traten drei separate Shifts
    auf (chart-grid#ausfaelle, card--wide#stammstrecke-direction und
    der Jahres-Paragraph in `#ausfaelle > p.section__sub`).
  * `docs/assets/site.css`:
    - Pro `#…-hour` / `#…-weekday` / `#…-line` / `#…-providers` /
      `#…-direction` ein expliziter `min-height` (700 / 200 / 290 /
      290 / 140 px), bemessen an „Bar-Anzahl × ~28 px Row + Gap". Die
      Container reservieren damit beim ersten Paint exakt den Platz,
      den `renderBars()` später mit den CSV-Werten füllt — kein
      Push-Down mehr, wenn die Daten ankommen.
    - `.kpi { min-height: 112px }` und `.kpi.skeleton { min-height:
      112px }` (war 92 px) — die Skelette matchen jetzt die echte
      Card-Höhe (Label + clamp-Wert + Sub-Zeile + Padding), so dass
      KPI-Reveals weder schrumpfen noch wachsen.
    - `.skeleton--feed { min-height: 152px }` (war 96 px) — die
      Feed-Items rendern typischerweise Titel + Description + Meta in
      ~150 px, also gleicht die Reserve den Endwert an.
    - `[data-year-label] { display: inline-block; min-width: 4ch;
      text-align: center; font-variant-numeric: tabular-nums }` —
      reserviert die volle „YYYY"-Breite für den „–"-Platzhalter, so
      dass der Jahres-Tausch durch `setYearLabels()` keine
      Zeilenumbruch- oder Word-Spacing-Verschiebung mehr auslöst.
  * `docs/assets/site.html` + `docs/assets/site.css`:
    - `train.png` (63 KB Palette-PNG) bekommt ein verlustfreies
      `train.webp` Geschwister (57 KB, –9 %) und wird via
      `<picture><source type="image/webp" srcset="…webp"><img …></picture>`
      ausgeliefert. Engines ohne WebP-Unterstützung laden weiterhin
      direkt das PNG, die `<img>` behält ihre 1584×224-Attribute und
      damit die identische CLS-Reservierung.
    - `footer-bg.jpg` (195 KB JPEG q=72) bekommt ein lossy
      `footer-bg.webp` Geschwister (108 KB q=75, –45 %, weil die
      78–94 % dunkle Verlaufs-Overlay jegliche WebP-Artefakte
      maskiert). Die `.site-footer::before`-Regel deklariert
      `background-image` zweimal: erst mit JPEG-`url()` als
      Universal-Fallback, dann mit `image-set(url("…webp"),
      url("…jpg"))` — moderne Engines (Chrome 88+, Safari 14+,
      Firefox 88+ ≈ 95 % Global Reach) wählen die WebP, ältere
      ignorieren die zweite Deklaration und behalten die JPEG.
  * `scripts/optimize_site_assets.py`:
    - Neue `TRAIN_WEBP` / `FOOTER_WEBP` Konstanten und zwei
      Pillow-Save-Aufrufe am Ende der bestehenden
      `_optimise_train_png()` / `_optimise_footer_jpg()` Funktionen
      generieren die WebP-Varianten in einem einzigen Skript-Lauf.
      Train.webp ist verlustfrei (`lossless=True`), Footer.webp läuft
      mit `quality=75`, beide nutzen `method=6` (langsame, beste
      Kompression — wird nur bei einer Quell-Änderung neu erzeugt).
      `--skip-images` lässt beide WebP-Pfade unverändert (Test- und
      Pre-commit-Pfad bleiben Pure-Python und brauchen kein libwebp).
  * Erwartete Lighthouse-Wirkung (rechnerisch — neue Reports liefern
    Maintainer nach):
    - **CLS Mobil 0.937 → ≈ 0.00, Desktop 0.598 → ≈ 0.00** durch die
      vier `min-height`-Cluster (Bars, KPIs, Feed, Year).
    - **Performance-Score Mobil 76 → 95+, Desktop 78 → 95+** —
      CLS hatte beide Profile auf den Performance-Schlüsselmetriken
      blockiert, FCP/LCP/TBT lagen schon im grünen Bereich.
    - **Transfer-Gewinn ~92 KB** für moderne Browser (Train WebP
      ‑5 KB, Footer WebP ‑87 KB), bei unverändertem Fallback-Pfad
      für ältere Engines.
  * Was sich **nicht** ändert: Final-State-Rendering (alle
    Reservierungen werden im geladenen Zustand überschrieben oder
    perfekt aufgefüllt), CSP (`img-src 'self' data:` deckt WebP
    aus demselben Origin schon ab), Feed-/CSV-Pfade, Cache-Strategie
    (GitHub Pages liefert weiterhin 10 min `max-age`, was Lighthouse
    via `cache-insight` zwangsläufig als „nicht ideal" markiert —
    außerhalb der Reichweite eines statischen Workflows).
  * `python scripts/optimize_site_assets.py --check` läuft grün;
    `tests/scripts/test_optimize_site_assets.py` (6 Tests) bleibt
    grün, weil der Image-Pfad wie bisher hinter `shutil.which`-
    Guards lebt und die WebP-Save-Calls innerhalb der bestehenden
    `_optimise_*` Funktionen liegen.
  * Marker: SENTINEL_LIGHTHOUSE_2026_05_17_CLS_RESERVATION.

* **Dashboard: „Ausfälle nach Wochentag" als eigener Chart-Block
  (2026-05-17)**:
  * Die Ausfall-Sektion auf `docs/site.html#ausfaelle` zeigt jetzt –
    analog zu „Ø Verspätung nach Wochentag" im Stammstrecke-Block –
    eine eigene Balken-Karte „Nach Wochentag" zwischen „Nach Richtung"
    und „Nach Tageszeit". `renderAusfaelleStats()` hatte die
    Wochentag-Aggregation (`countByKey(rows, r => r.weekday, WEEKDAYS)`)
    schon für die `Stärkster Tag`-KPI berechnet; sie war aber nirgends
    visualisiert. Der neue `renderBars("#ausfaelle-weekday", …)`-Aufruf
    nutzt dieselbe `cancel`-Bar-Variante (`var(--c-danger)`-Fill) wie
    die übrigen Ausfall-Charts und reuse das `WEEKDAY_LONG`-Mapping
    auf die deutschen Vollnamen.
  * Layout: Die neue Karte ist eine reguläre `card` (kein
    `card--wide`); auf breiten Viewports stehen Linie/Richtung/Wochentag
    in einer Reihe, „Nach Tageszeit" bleibt die volle Breite einnehmende
    untere Karte – exakt das Muster aus dem Stammstrecke-Grid (Stunde
    + Wochentag schmal, Richtung wide). Keine CSS-Änderung nötig:
    `.chart-grid` ist `repeat(auto-fit, minmax(min(320px, 100%), 1fr))`,
    so dass die zusätzliche Karte responsive einrastet.
  * `docs/assets/site.min.js` mit
    `python scripts/optimize_site_assets.py --skip-images` regeneriert;
    `--check` läuft grün. Kein neuer CSS-Hook, keine neue Datenquelle,
    keine CSP-Anpassung – die Spalten `weekday`/`hour` waren bereits
    Teil des `data/stats/ausfaelle_<YYYY>.csv`-Schemas seit 2026-05-15.

* **Performance: Asset-Payload der statischen Website um ~86 % reduziert (2026-05-17)**:
  * Zwei Lighthouse-Läufe gegen `docs/site.html` (Mobil + Desktop,
    Lighthouse 13.0.2) flaggten dieselbe Diagnose-Kette: `train.png`
    (992 KiB) und `footer-bg.jpg` (661 KiB) dominierten den Netzwerk-
    Payload und waren die einzigen materiellen Ziele des
    `image-delivery-insight`-Audits (Score 0.5, geschätzte Einsparung ~989 KiB).
    Das handgepflegte `site.css` (19 KiB) und `site.js` (25 KiB)
    lösten zusätzlich `unminified-javascript` (Score 0.5) aus und
    trugen zu einer 429-ms-HTML→CSS-Render-Blocking-Kette bei.
    Accessibility flaggte `label-content-name-mismatch` (Score 0) am
    Marken-Link im Header, dessen `aria-label="Wien ÖPNV – Startseite"`
    das sichtbare „Live-Dashboard"-Sub-Label nicht enthielt.
  * Bild-Assets verlustfrei auf Anzeigegröße neu kodiert:
    - `train.png`: 3168×448 → 1584×224 (identisches 7.07:1-Seiten-
      verhältnis; die per `clamp(1.5rem, 4vw, 2.5rem)` gedeckelte
      Anzeigehöhe unterschritt die neue 224-px-Nativhöhe schon bei 3× DPR),
      pngquant-`--quality 65`-Palettenquantisierung + optipng `-o7 -fix`.
      **1015927 → 63046 bytes (93.8 % Reduktion)**; mittlere
      Pro-Kanal-RGB-Differenz bei Anzeigegröße 0.8–1.1 von 255
      (nicht wahrnehmbar).
    - `footer-bg.jpg`: 2732×1536 → 1920×1080 (16:9 erhalten), JPEG
      Quality 72 + progressiv + `jpegoptim --strip-all`. **677250 →
      194825 bytes (71.2 % Reduktion)**; die Pro-Kanal-Differenz wird
      durch das darübergelegte Dunkel-Gradient-Overlay mit 78–94 %
      Deckkraft weiter abgeschwächt, sodass der sichtbare Unterschied
      deutlich unter 0.2 % liegt.
  * CSS/JS-Pipeline auf eingecheckte minifizierte Bundles umgestellt:
    - `docs/assets/site.css` und `site.js` bleiben die maßgeblichen
      lesbaren Quellen; neue `site.min.css` (15486 bytes, 18.4 %
      kleiner) und `site.min.js` (18628 bytes, 25.9 % kleiner) werden
      daneben erzeugt und von `docs/site.html` referenziert.
    - Pure-Python (`rcssmin` / `rjsmin`) — keine Node-Toolchain
      eingeführt, was die „kein Build-Schritt"-Haltung des Projekts
      für das Dashboard bewahrt. Das neue `scripts/optimize_site_assets.py`
      treibt die Pipeline; der `--check`-Modus schlägt fehl (fail-closed),
      wenn die committeten Bundles aus dem Sync mit ihren Quellen
      geraten, und ist über einen `files:`-Filter so in pre-commit
      verdrahtet, dass er nur läuft, wenn ein
      `site.{css,js,min.css,min.js}`-Blob gestaged ist.
  * HTML-Mikro-Fixes, die das visuelle Layout **nicht** berühren:
    - `aria-label="Wien ÖPNV Live-Dashboard – Startseite"` enthält
      beide sichtbaren Sub-Labels und erfüllt damit die
      `label-content-name-mismatch`-Regel von axe.
    - `width`/`height` des Train-Sprite-`<img>` auf die neue
      1584×224-Nativgröße aktualisiert, sodass die Aspect-Ratio-
      Reservierung des Browsers zur Bitmap passt (CSS `height:100%`
      bestimmt weiterhin die gerenderte Größe — CLS unverändert).
    - `fetchpriority="low"` am dekorativen Train-Sprite gibt Bandbreite
      für höher priorisierte Ressourcen während des frühen Ladens frei.
    - `<link rel="dns-prefetch">` + `<link rel="preconnect" crossorigin>`
      lösen `raw.githubusercontent.com` vorab auf, sodass die von
      `site.min.js` angestoßenen verzögerten CSV-Fetches den
      Cold-DNS-Treffer überspringen.
  * Netto-Effekt auf den dokumentierten Payload: 1693 KiB → 258 KiB
    allein bei den zwei Bild-Assets (1435 KiB gespart, ~86 %); zusammen
    mit dem CSS/JS-Schrumpf sinkt das von Lighthouse gemeldete
    Gesamt-Seitengewicht von `docs/` von 1677 KiB auf ≈ 300 KiB. Visuelles
    Rendering und JS-Verhalten sind bei Anzeigegröße und im
    vollständig geladenen Zustand byte-für-byte identisch; keine
    CSP-Lockerung, kein neuer Third-Party-Request.
  * `requirements-dev.txt` erhält `rcssmin`, `rjsmin` und `Pillow`
    (Pure-Python, wo es zählt; Pillow ist in jeder Umgebung, die zuvor
    bildbezogene Skripte ausgeführt hat, bereits installiert). Die
    Bild-Binaries (`pngquant`, `optipng`, `jpegoptim`) bleiben optional —
    Mitwirkende, die nur CSS/JS bearbeiten, brauchen sie nicht, und das
    Skript degradiert graziös (warnt + fährt fort), wenn eines davon
    nicht im PATH liegt.
  * Marker: SENTINEL_LIGHTHOUSE_2026_05_17_ASSET_PAYLOAD.

* **Security: Secret-Scanner-Drift Runde 14 — Erkennungslücke beim Präfix
  des AWS STS Service Bearer Tokens (`ABIA<16>`) (2026-05-16)**:
  * Schließt das vierte Credential-Präfix in der AWS-Familie der
    4-Zeichen-Unique-Identifier, das `_AWS_ID_RE` ausdrücklich als
    benannt-aber-ungedeckt aufführte: `ABIA` (AWS STS Service Bearer
    Token, ausgestellt von `sts:GetServiceBearerToken` für
    Service-zu-Service-Authentifizierung im Namen eines AWS-Nutzers). Die
    Abschluss-Checkliste von Runde 13 führte nur `AKIA`/`ASIA`/`ACCA`
    auf — `ABIA` war das dokumentierte vierte Credential-Präfix, das ungedeckt blieb.
  * Vor dem Fix rutschten blanke `ABIAV2EXAMPLE12345AB`-Tokens (20 Zeichen:
    4-Zeichen-Präfix + 16-Zeichen-`[A-Z0-9]`-Body) durch jeden Erkennungs-
    zweig in `_scan_content`: `_HIGH_ENTROPY_RE` verlangt `{24,}` Zeichen
    und weist die 20-Zeichen-Form ab; `_AWS_ID_RE` führte nur drei von
    vier Präfixen auf; die Zuweisungs-Heuristik verliert die
    AWS-spezifische Attribution. Netto: stille Nicht-Erkennung in
    Nicht-Zuweisungs-Kontexten (CloudTrail-Debug-Log-Zeilen, AWS-SDK-Debug-
    Traces mit `AWS_DEBUG=true`, JSON-Fixtures ohne sensible Schlüssel,
    Doku-Snippets, feindselige PR-Fragmente) und Attribution-Drift
    in Zuweisungs-Kontexten (nur das generische "Verdächtige Zuweisung"
    feuerte und verlor die AWS-STS-spezifische Revocation-Flow-Attribution).
  * Einzelnes Tupel ergänzt zu `_KNOWN_TOKENS` in
    `src/utils/secret_scanner.py`:
    `re.compile(r"(?<![A-Za-z0-9])ABIA[A-Z0-9]{16}(?![A-Za-z0-9])")`
    mit Begründung `"AWS STS Service Bearer Token gefunden"`. Das strikte
    `[A-Z0-9]{16}`-Body-Alphabet schützt gegen False Positives auf
    Lowercase-/Mixed-Case-Strings, die zufällig mit `ABIA` beginnen;
    das `(?<![A-Za-z0-9])`-Lookbehind verhindert Treffer mitten im Wort.
    Die KNOWN_TOKENS-Verarbeitung läuft vor `_AWS_ID_RE`, sodass
    `is_covered` die spezifischere Aussteller-Attribution korrekt verankert.
  * Umfassende Testabdeckung in
    `tests/test_sentinel_secret_scanner_drift_round14.py` (10 Tests):
    Plaintext-Kontext-PoC (Silent-Undetection-Zweig), JSON-Fixture-
    PoC, Zuweisungs-Kontext-PoC (Attribution-Drift-Zweig), drei
    Negativfälle (kurzer Body / Lowercase-Body / ABIA mitten im Wort),
    drei Regressions-Guards (AKIA/ASIA/ACCA erhalten weiterhin das
    kanonische `AWS Access Key ID gefunden` — keine Kollision) und ein
    Inventar-Invariant-Pin (`ABIA`- + `AWS STS Service Bearer Token`-Strings
    im Quelltext von `secret_scanner.py` vorhanden).
  * Marker: SENTINEL_AWS_ABIA_PREFIX_DRIFT.

* **Security: Schließung der Non-Finite-Literal-Drift für Netzwerk/Env/Sidecar — 18 JSON-
  Parser-Stellen (2026-05-15)**:
  * Schließt das **symmetrische Gegenstück** zur Closure des
    Committed-State-File-Readers aus PR #1503 über drei orthogonale
    Taint-Kanäle hinweg: 13 netzwerk-getaintete HTTP-Antworten
    (`wl_fetch._get_json`,
    `places.client._post`/`_format_error_message`,
    `hafas_client._fetch_hafas_location`,
    `osm_client.OSMOverpassClient._fetch_payload`,
    `reporting._GithubIssueReporter.submit`,
    `check_overpass_status._evaluate_response`,
    `verify_vor_access_id`, `update_baustellen_cache._load_json_from_content`,
    `update_stammstrecke_hbf` + `update_stammstrecke_status` VAO-Endpunkte),
    2 env-getaintete `BOUNDINGBOX_VIENNA`-Parser
    (`fetch_google_places_stations._parse_bounding_box`,
    `update_station_directory._parse_bounding_box`) und 3
    Disk-Sidecar-State-Reader, die PR #1503 übersehen hatte
    (`build_feed._read_state_capped`,
    `update_stammstrecke_status._load_pending_trips` /
    `_load_recently_finalised`).
  * Ohne diese Pins kann ein kompromittierter Upstream / DNS-Hijack /
    MITM / geleaktes CI-Env / feindseliger Operator an jeder dieser
    Parse-Grenzen `NaN`- / `Infinity`- / `-Infinity`- / `1e1000`-Literale
    einschleusen. Der Lenient-Mode-Parser liefert eine Python-Struktur
    mit `float('nan')` / `float('inf')` darin zurück und vergiftet so
    Vergleiche (`nan != nan` ist True — bricht Dedup-Invarianten),
    Arithmetik (`nan + x` ist nan — korrumpiert still Latenz-Mittelwerte
    und Verspätungsberechnungen) und das Zurückschreiben an den
    Writer-Pin (`allow_nan=False` aus Runde 1485/1487/1488/1491) — die
    Cron-Pipeline stürzt beim nächsten Persistieren mitten im Schreiben ab.
  * Neuer kanonischer Helper `loads_finite()` in `src/utils/files.py`
    (dünner Shim über `json.loads`, der die von PR #1503 etablierten
    Hooks `_reject_non_finite_constant` + `_reject_non_finite_float`
    fest einbaut). Neue Aufrufstellen sollten `loads_finite()` nutzen
    statt `json.loads()` direkt aufzurufen; `response.json()`-Stellen
    übergeben die Hooks als kwargs.
  * Umfassende Testabdeckung in
    `tests/test_sentinel_network_tainted_non_finite_drift.py` (38
    Tests): 5 Verhaltenstests für den kanonischen Helper, 18
    Inventar-Pins (Source-Grep jeder aufgezählten Stelle nach dem Hook),
    pro-Stelle-Verhaltens-PoCs über NaN / Infinity / Scientific-Notation-
    Überlauf + Finite-Round-Trip-Regressionsschutz, plus der
    Writer-Reader-Round-Trip-Symmetriebeweis.
  * Marker: SENTINEL_NETWORK_TAINTED_NON_FINITE_DRIFT.

* **Stammstrecke-Ausfälle — Neue Statistik aus bestehenden VAO-Abfragen
  (2026-05-15)**:
  * Der Hbf-`/departureBoard`-Reader (`scripts/update_stammstrecke_hbf.py`)
    und der Legacy-`/trip`-Reader (`scripts/update_stammstrecke_status.py`)
    haben Abfahrten mit `cancelled: true` bislang **stillschweigend
    verworfen**: das `delay_minutes`-Signal war `None`, und die
    Sammelschicht filterte solche Beobachtungen vor dem Ledger
    heraus. Folge: jeder tatsächliche Zugausfall auf der Stammstrecke
    war für die Statistik unsichtbar.
  * Neue Datei `data/stats/ausfaelle_<YYYY>.csv` mit Schema
    `timestamp, weekday, hour, direction, line` — eine Zeile pro
    ausgefallenem Zug. Beide Reader leiten Ausfälle nun durch die
    bestehende Pending-Trip-Identity-Key-Dedup (`(direction, name,
    scheduled)`) und durch den Recently-finalised-Schutz, sodass
    derselbe physische Ausfall NIE über mehrere Cron-Ticks doppelt
    gezählt wird. Der Cancellation-Check läuft jetzt VOR dem
    `rtTime`-Filter, weil VAO bei ausgefallenen Zügen regelmäßig
    keinen Realtime-Wert mehr ausliefert — der frühere Filter hat
    die Cancellation-Signale gemeinsam mit den No-rtTime-
    Beobachtungen verworfen.
  * `_PendingTrip` trägt jetzt ein `cancelled: bool`-Flag, das
    auch im Pending-Ledger (`cache/stammstrecke/pending_trips.json`)
    serialisiert wird. Legacy-Einträge ohne das Feld laden als
    `cancelled=False` (Backwards-Compat). Der Finalize-Pass teilt
    pro Cron-Tick die ausgelaufenen Pending-Trips in zwei Buckets:
    delay-tragende Beobachtungen fließen wie bisher in eine
    aggregierte CSV-Zeile pro Richtung+Jahr
    (`stammstrecke_<YYYY>.csv`); Ausfälle erzeugen jeweils eine
    eigene Zeile in `ausfaelle_<YYYY>.csv`, damit der Dashboard-
    Aggregator sie als diskrete Ereignisse zählen kann.
  * **Dashboard** (`docs/statistik.md`): neue Sektion `## Ausfälle`
    mit Tabellen pro Richtung und pro Linie sowie Wochentag-/
    Stunde-Balken. Die `Kennzahlen auf einen Blick`-Tabelle zeigt
    zusätzlich die Jahressumme. **README**: zwei neue Marker
    `STATS:AUSFAELLE_LIVE` (60-Min-Fenster) und `STATS:AUSFAELLE`
    (30-Tage-Fenster). Die Ausfälle-Marker werden bedingungslos
    aktualisiert, auch bei `0` Beobachtungen — ein explizites
    `0` ist das operationell wertvolle „stabiler Betrieb"-Signal
    und unterscheidet sich klar von „Daten fehlen".
  * **Tests**: neue Pin-Tests in `tests/test_utils_stats.py`
    (Writer + CSV-Formula-Injection-Defang), `tests/scripts/
    test_update_stammstrecke_status.py` (Collector + Pending-Trip-
    JSON-Roundtrip + Backwards-Compat-Loader + End-to-End-Finalize-
    Routing inkl. „mixed delay+cancellation in einem Tick"),
    `tests/scripts/test_update_stammstrecke_hbf.py` (Collector +
    Cancellation-Bool-vs.-String + No-rtTime-mit-Cancellation),
    `tests/scripts/test_generate_markdown_stats.py` (Aggregator,
    Renderer, README-Block) und
    `tests/scripts/test_generate_markdown_stats_readme.py` (volle
    Marker-Integration mit explizitem 0-Render).
* **Stammstrecke-Feed-Trigger — Legacy-Label-Auflösung im
  Compute-Pfad (2026-05-15)**:
  * Der Trigger-Compute in `src.feed.stammstrecke.compute_
    stammstrecke_events` bucket'te Observations bisher nach
    `obs.direction` (raw CSV value); der Backwards-Compat-Alias in
    `DIRECTIONS_BY_LABEL` (Floridsdorf → Praterstern-`_Direction`)
    war auf dem heißen Pfad nicht aktiv. Folge: CSV-Zeilen mit dem
    Legacy-Label `"Floridsdorf"` (z.B. nach Backup-Restore, Partial-
    Deploy oder Hand-Edit) wären silently im Loop ignoriert worden,
    weil das Loop-Lookup `direction.target_label = "Praterstern"`
    den `by_direction["Floridsdorf"]`-Bucket nicht aufsucht. Fix:
    Observations werden via `DIRECTIONS_BY_LABEL` zur kanonischen
    Direction aufgelöst, bevor sie in den Bucket landen.
  * Neue Test-Suite `tests/test_feed_stammstrecke_trigger.py`
    (9 Tests) pinnt die Trigger-Semantik: Happy-Path (2 Praterstern-
    Zeilen > 9 min), Legacy-Compat (2 Floridsdorf-Zeilen fold-in),
    Mixed (1+1), Threshold-Gate (Single-row + boundary-9.0),
    Window-Cutoff (Beobachtung knapp außerhalb 1h), Empty-Input,
    Direction-Isolation (beide Richtungen feuern parallel),
    Constants-Pinning (`DELAY_THRESHOLD_MINUTES`, `FEED_WINDOW`).
* **Stammstrecke-Monitor — Nord-Richtungs-Label umbenannt:
  "Floridsdorf" → "Praterstern" (2026-05-15)**:
  * Die CSV-Spalte `direction` und das `DIRECTION_LABEL_NORTHBOUND`
    der Schreiber + des Feed-Renderers verwenden ab sofort
    `"Praterstern"` statt `"Floridsdorf"` für nordwärts gerichtete
    Stammstrecken-Beobachtungen. Begründung: Bei kurzen Wendezügen,
    die bereits am Praterstern oder Wien Mitte terminieren (und nicht
    bis Floridsdorf weiterfahren), bezeichnete die alte Beschriftung
    fälschlich einen Endpunkt, den die meisten Züge gar nicht
    erreichen. Die Süd-Beschriftung `"Meidling"` benennt seit jeher
    die nächste Stammstrecken-Haltestelle nach dem Hbf — die
    Umbenennung gibt der Nord-Beschriftung die gleiche Semantik:
    `"Stammstrecken-Züge in Richtung <nächster Stammstrecken-
    Haltestelle nach Hbf>"`.
  * **Datenmigration**: Alle bestehenden Zeilen in
    `data/stats/stammstrecke_2026.csv` wurden mit dem Rename-Commit
    `Floridsdorf` → `Praterstern` umgeschrieben. Die in-flight Pending-
    Trip- und Recently-finalised-Ledger
    (`cache/stammstrecke/pending_trips.json` /
    `cache/stammstrecke/recently_finalised.json`) wurden ebenfalls
    konvertiert — sowohl die `direction`-Feldwerte als auch die
    Identity-Key-Präfixe.
  * **Backwards-Compat-Shim**: Der Feed-Renderer
    (`src/feed/stammstrecke.py`) akzeptiert in
    `DIRECTIONS_BY_LABEL` weiterhin den Legacy-Wert `"Floridsdorf"`
    (alias auf die `Praterstern`-Direction). Der Hbf-Cron-Pfad ruft
    `_finalize_departed` zusätzlich für `LEGACY_DIRECTION_LABEL_
    NORTHBOUND` auf, sodass ein extern wiederhergestellter Pending-
    State mit alten Schlüsseln transparent in den Praterstern-Bucket
    fließt. Das CSV wird stets unter dem neuen Label geschrieben.
  * **Feed-Item-GUID**: Die `identity_prefix` für Nord wurde von
    `stammstrecke_delay_floridsdorf` auf `stammstrecke_delay_praterstern`
    umbenannt. Da der `data/first_seen.json` aktuell keinen aktiven
    Nord-Eintrag enthält, propagiert die Umbenennung als saubere
    "neue Direction" für RSS-Abonnenten, ohne ein laufendes Event
    doppelt zu emittieren. Sollte bei einem zukünftigen Nord-Incident
    ein laufendes Event aus der Zeit vor dem Rename existieren, würde
    es einmalig als „neues" Event in RSS-Readern erscheinen.
* **Stammstrecke-Monitor — Platform-Level Bahnsteig-Filter
  (2026-05-15)**:
  * Der `/departureBoard`-Reader filtert seit dieser Änderung jede
    Abfahrt am Wien Hauptbahnhof nach ihrem effektiven Bahnsteig
    (`rtTrack` mit Fallback auf scheduled `track`). Nur Abfahrten
    auf **Bahnsteig 1** (Stammstrecke nordwärts → Floridsdorf) oder
    **Bahnsteig 2** (Stammstrecke südwärts → Meidling) qualifizieren
    sich für die Stammstrecke-Statistik. Alle anderen Hbf-Bahnsteige
    (3-12, inkl. Halb-Bahnsteige „1A", „10A-B" usw.) tragen
    Fernverkehr (RJ/IC/EC/NJ), Hbf-endende REX-Züge, die Marchegger
    Ostbahn, die Pottendorfer Linie, die Westbahn und weitere
    Korridore, die NICHT die Stammstrecke nutzen — sie werden seit
    diesem Patch deterministisch ausgeschlossen.
  * Begleitend wurden die Substring-Listen für die Richtungsbestimmung
    bereinigt: `marchegg` und `bratislava` entfernt, weil beide
    Termini mehrdeutig waren (Marchegg verkehrt rein östlich über die
    Ostbahn ohne Stammstrecken-Bezug; Bratislava ist sowohl via
    Stammstrecke + Břeclav als auch via Ostbahn erreichbar). Der
    Bahnsteig-Filter macht die Substring-Heuristik nur noch für die
    Richtungsbestimmung notwendig (Nord vs Süd), nicht mehr für die
    Stammstrecke-Zugehörigkeit selbst.
  * Diagnostik: Zwei neue Counter (`dropped_no_track`,
    `dropped_non_stammstrecke_track`) im Tick-Log machen sowohl ein
    VAO-Schema-Drift (Bahnsteig-Info fehlt) als auch das gesunde
    Ausscheiden von Nicht-Stammstrecken-Zügen operativ sichtbar,
    ohne dass die Bahnsteig-Strings zwischen den Filtern wandern.
  * Semantik: Die Hbf-basierte Messung bleibt eine Stammstrecken-
    Messung (am Korridor-Mittelpunkt), aber jetzt mit strenger
    Linien-Eindeutigkeit auf Bahnsteig-Niveau — vergleichbar mit der
    ursprünglichen `/trip`-basierten Floridsdorf-↔-Meidling-Selektion
    der Pre-Hbf-Ära, ohne deren `numF=6`-Sampling-Lücke.
* **Stammstrecke-Monitor — Migration auf `/departureBoard` @ Wien Hbf
  (2026-05-15)**:
  * Der Cron-Pfad ruft seit dem Merge von PR #1496 das neue
    `scripts/update_stammstrecke_hbf.py`-Skript auf, das die
    `/departureBoard`-API einmal pro Tick am Wien Hauptbahnhof
    befragt und die Abfahrten anhand der Endhaltestelle per
    Substring-/Whitelist-Klassifikation in die bestehenden
    Richtungs-Labels (`Meidling`, `Floridsdorf`) einsortiert. Im
    Vergleich zum Vorgänger (`/trip` × 2 Richtungen mit hartem
    `numF=6`-Cap) verdoppelt sich die Coverage bei gleichzeitiger
    Halbierung des API-Budgets (1 statt 2 Requests/Tick).
  * **Semantischer Bruch in der Verspätungs-Messung**: bis
    2026-05-15 wurde die Verspätung **am Ursprungsbahnhof**
    (Floridsdorf für Meidling-Bound-Züge, Meidling für
    Floridsdorf-Bound-Züge) gemessen, ab 2026-05-15 **am Wien
    Hauptbahnhof** — einem Stammstrecken-Mittelpunkt. Beide Zahlen
    sind für denselben physischen Zug nicht identisch (Verspätung
    kann zwischen Ursprung und Hbf akkumulieren oder eingeholt
    werden). Die 30-Tage-Statistik im README überspannt den
    Migrations-Tag und zeigt deshalb für einige Wochen eine
    Diskontinuität, die ein Mess-Semantik-Wechsel ist, kein Bug
    und keine reale Qualitäts-Veränderung. Wer Werte vor und
    nach 2026-05-15 vergleicht, sollte diesen Stichtag im Auge
    behalten.
  * `data/stats/stammstrecke_<YYYY>.csv`-Schema und
    `cache/stammstrecke/*.json`-Ledger-Format bleiben unverändert
    (die README-Dashboard- und Feed-Event-Pipelines lesen byte-
    weise identisch weiter). `manual-full-refresh.yml` ist
    ebenfalls auf das neue Skript umgezogen, damit ein manueller
    Refresh keine konkurrierenden Identity-Key-Formate in den
    geteilten Pending-Trip-Ledger schreibt.
* **Quota-Bug Fix (Phantom-Request pro Skript-Lauf, 2026-05-15)** —
  `_flush_quota_cache` rief `save_request_count` auf, das jeden
  Aufruf als neuen Request zählte: jeder Stammstrecke-Cron-Tick
  buchte 3 Requests statt 2 auf den 100/Tag-VAO-Start-Counter. Bei
  48 Ticks/Tag wurde die Quote nach ~33 Ticks (~16 h) erschöpft und
  der Preflight-Gate übersprang die restlichen Ticks, wodurch sich
  im Ledger eine ~8h-Lücke pro Tag ergab und die README-Statistik
  "Letzte 60 Minuten" zeitweise auf 1-3 Beobachtungen abrutschte.
  Fix in PR #1494: Persist-Logik aus `save_request_count` in einen
  separaten `_persist_quota_to_disk`-Helper ausgegliedert, den der
  atexit-Flush direkt aufruft ohne den Counter zu inkrementieren.
  Regression-Tests pinnen das No-Inflation-Invariant.
* **Docs/Cleanup (Nachzug zur VOR-Stammstrecke-only-Konsolidierung)** —
  Doku- und Workflow-Drift nach der 2026-05-11-Konsolidierung (VOR
  ist nur noch für den Stammstrecken-Monitor zuständig) bereinigt:
  * Tote Skript-Verweise auf `update_vor_cache.py`,
    `update_vor_stations.py` und `fetch_vor_haltestellen.py` aus
    `docs/development.md`, `docs/architecture.md`,
    `.github/workflows/manual-full-refresh.yml` und
    `.github/workflows/update-stations.yml` entfernt; die Scripts
    existieren seit 2026-05-11 nicht mehr.
  * Verwaiste `cache/vor_929f1c/last_run.json` (kein aktiver Writer
    nach der Konsolidierung; Status seit 2026-05-09 `api_unreachable`)
    plus leeres Parent-Verzeichnis gelöscht.
  * CLI-Help-Text `python -m src.cli cache update …` listet `vor`
    nicht mehr als gültigen Provider-Identifier (der Handler hat es
    ohnehin schon abgewiesen, jetzt ist die Hilfe konsistent).
  * Stale `update-vor-cache.yml`-Workflow-Verweis in
    `src/utils/cache.py` (`write_status`-Sicherheitskommentar) und in
    `tests/test_sentinel_quota_status_trojan_source.py` als historisch
    gekennzeichnet — die Trojan-Source-Defence im Writer bleibt
    unverändert in Kraft.
* **Changed (WL-OGD-Reaktivierungskette, PR #1441-#1453)**: Dreizehn
  konsolidierte PRs reaktivieren den Wiener-Linien-OGD-Merge-Pfad
  vollständig gegen den kanonischen
  `www.wienerlinien.at/ogd_realtime/doku/ogd/`-Endpunkt (der vorherige
  `data.wien.gv.at/csv/`-Proxy wurde in der 60. OGD-Phase im September
  2025 abgeschaltet).
  * **Endpoint + Workflow (#1441, #1442)** — den redundanten
    Inline-curl-Schritt aus `update-stations.yml` entfernt, beide
    Konstanten `OGD_HALTESTELLEN_URL` / `OGD_HALTEPUNKTE_URL` auf den
    kanonischen Wiener-Linien-Host migriert. Soft-Fail auf gepinnte
    lokale CSVs bei Upstream-Ausfall.
  * **Schema-Fuzzy-Keys (#1444)** — Spalten-Aliase ergänzt, sodass der
    Loader sowohl das Legacy-Proxy-CSV
    (`HALTESTELLEN_ID`/`NAME`/`WGS84_*`) als auch das kanonische
    OGD-Echtzeit-CSV (`DIVA`/`PlatformText`/`StopText`/`Latitude`) parst.
  * **WL-only-Einträge im `wl_diva`-Namensraum (#1446)** — synthetische
    `bst_id` (`9{DIVA}`) und synthetischen `bst_code`
    (`WL-{name[:3]}`) bei WL-only-Einträgen entfernt; das kanonische
    `wl_diva`-Feld ist der einzige strukturelle Identifier, und
    Cross-Station-ID-Kollisionen / `WL-ABS`-artige Code-Duplikate
    sind verschwunden.
  * **Pendler-Default für Border-Stops (#1443)** — nicht zugeordnete
    WL-Haltestellen außerhalb des Wien-Polygons werden automatisch auf
    `pendler=True` hochgestuft.
  * **Validator-Identifier (#1447)** — `_format_identifier` schließt
    jetzt `wl_diva` ein, sodass WL-only-Einträge distinkte Schlüssel
    erhalten, statt auf `"source:wl"` zu kollabieren (was 1759 Stationen
    über 30 echte Naming-Gruppen in die Auto-Quarantäne gezogen hatte).
  * **StopID- + Richtungsmarker-Sanitisierung (#1445)** — kurze
    `StopID`-Zählerwerte werden aus den `aliases` herausgefiltert (die
    Legacy-8-stellige RBL bleibt); `<`- und `>`-Richtungsmarker in
    `StopText` werden durch `←` / `→` ersetzt, sodass sie nicht mehr
    `_UNSAFE_CHARS_RE` treffen.
  * **`in_vienna`-Konsistenz (#1449)** — `build_wl_entries` leitet
    `in_vienna` jetzt aus den aggregierten Haltepunkt-Koordinaten ab
    statt nach Any-Stop-wins, sodass Grenzstationen kein Flag mehr
    tragen, das ihren persistierten Koordinaten widerspricht. Gepinnt
    durch `test_coordinates_match_in_vienna_flag`.
  * **ÖBB-Workbook-Soft-Fail (#1450)** — `download_workbook` schreibt
    bei jedem erfolgreichen Lauf atomar einen Snapshot nach
    `data/oebb-verkehrsstationen.xlsx` und liest aus dem Snapshot, wenn
    `data.oebb.at` einen Netzwerkfehler liefert. Schließt den
    asymmetrischen Fehlermodus, in dem ÖBB die einzige Fail-Fast-
    Upstream-Quelle war. Die CodeQL-Config
    (`.github/codeql/codeql-config.yml`) schließt das
    `py/clear-text-storage-sensitive-data`-False-Positive aus, das auf
    jeden Public-Data-Cache-Writer in diesem Projekt zutrifft.
  * **Multi-DIVA-Merge <150 m (#1451)** — `_merge_colocated_dupli
    cates` faltet gleichnamige Haltestellen mit Haltepunkte-Mittel-
    Koordinaten innerhalb von 150 m zueinander zu einem einzigen
    Eintrag zusammen (die lexikographisch kleinste DIVA gewinnt, alle
    Haltepunkte und Aliase werden vereinigt). Entfernt 4 Doppelungen
    aus dem aktuellen `stations.json` (Stock im Weg, Vorgartenstraße,
    Lieblgasse, Altmannsdorfer Straße).
  * **`name` ist Display-Label, kein PK (#1452)** — die
    Kanonische-Namens-Eindeutigkeitsprüfung des Validators wird
    entfernt. Strukturelle Eindeutigkeit lebt in `wl_diva` / `bst_id` /
    `vor_id` / `bst_code`; `name` ist operator-zugewandt. Der
    `_disambiguate_duplicate_names`-DIVA-Suffix-Workaround
    (`Wien Bahnhof (WL 60205022)`) ist abgeschafft — doppelte
    Display-Labels sind jetzt zulässig und der RSS-Feed zeigt die
    saubere `Wien Bahnhof (WL)`-Form.
  * **Aussagekräftige Display-Namen aus `StopText` (#1453)** —
    `_derive_station_label` überschreibt generische transport-typisierte
    `PlatformText`-Tokens der Haltestelle (`Bahnhof`, `Lokalbahn`,
    `Hauptbahnhof`, `Station`, `Halt`, `Bf`, `Hbf`, `Bahn`,
    `U-Bahn`) mit dem Haltepunkte-`StopText`, sofern einer
    verfügbar ist. Sechs Einträge bekamen ein echtes Toponym:
    `Wien Bahnhof (WL)` × 2 → `Wien Tribuswinkel - Josefsthal
    (WL)`, `Wiener Neudorf (WL)`; `Wien Lokalbahn (WL)` × 4 →
    `Wien Guntramsdorf Lokalbahn (WL)`, `Wien Möllersdorf (WL)`,
    `Wien Neu Guntramsdorf (WL)`, `Wien Traiskirchen Lokalbahn
    (WL)`. Nicht-generische PlatformText-Werte bleiben unangetastet,
    sodass ÖBB- / VOR-Name-basierte Joins stabil bleiben.
  * **Test-Daten-Refresh (#1449)** — drei Stationsverzeichnis-Tests
    hatten Legacy-DIVAs hartkodiert, die Wiener Linien seither
    umnummeriert hat (`60201076` war vor PR #1442 Karlsplatz und ist
    jetzt Ratzenhofergasse; `60201002` war Schottentor und ist jetzt
    Pensionsversicherungsanstalt). Auf aktuelle DIVAs aktualisiert.
  * **Ergebnis auf Produktivdaten**: `stations.json` wuchs von 196 auf
    1951 Einträge (4 ko-lokalisierte Doppelungen aus 1803 WL-Einträgen
    herausgemergt), 0 DIVA-Suffixe in kanonischen Namen, 0 generische
    `Wien Bahnhof (WL)` / `Wien Lokalbahn (WL)`-Labels, der Validator
    meldet 0 Alias- / Naming- / Security-Issues, `quarantine.json`
    bleibt über Cron-Ticks hinweg leer.
* **Changed (Auto-Quarantine für `update_all_stations.py`)**: Blockierende
  Validation-Issues (`provider_issues`, `cross_station_id_issues`,
  `naming_issues`, `security_issues`) brechen die Pipeline nicht mehr ab.
  Stattdessen werden die betroffenen Einträge aus dem gemergten
  `tmp_stations_path` herausgefiltert, in `data/quarantine.json`
  persistiert (mit `timestamp` / `count` / pro-Station-Issues) und der
  Rest des Pipelines (Diff, Heartbeat, Atomic-Copy-Back) läuft mit dem
  gültigen Subset weiter. Damit überlebt der Feed eine partielle
  Upstream-Korruption (einzelne kaputte VOR-/OEBB-/WL-Einträge) und
  exitet mit `0`. Der ``<global>``-Sentinel der Provider-Issue-Liste
  (z. B. "Need at least two VOR entries") wird übersprungen — er
  korrespondiert mit keinem einzelnen Eintrag und kann nicht
  quarantänisiert werden. Tests: 5 neue Cases in
  `test_update_all_stations_diff_heartbeat.py` /
  `test_update_all_stations_wrapper.py` decken Identifier-Filterung,
  Partition-Logik, End-to-End-Quarantine-Schreiben und den
  ``<global>``-Skip ab. Mypy `--strict` bleibt clean.
* **Changed (Stammstrecke-Monitor → VOR/VAO ReST API)**: Der S-Bahn-
  Stammstrecken-Verspätungs-Monitor wurde von `pyhafas` (`OEBBProfile`)
  auf die offizielle VOR/VAO ReST `/trip`-API portiert. Hintergrund:
  das auf PyPI veröffentlichte `pyhafas` exportiert kein
  `OEBBProfile`, der Import schlug seit Wochen still fehl und
  `data/stats/stammstrecke_*.csv` blieb leer (siehe Audit-Bericht
  zu PR #1378).
  - **Removed**: `pyhafas` aus `requirements.txt`,
    `from pyhafas import HafasClient` / `_build_client` /
    `_query_journeys` / `_patch_session_timeout` aus
    `scripts/update_stammstrecke_status.py`.
  - **Replaced**: HAFAS-Aufruf durch `fetch_content_safe` gegen
    `${VOR_BASE_URL}trip` mit `originId` / `destId` / `numF=5` /
    `maxChange=0` / `rtMode=SERVER_DEFAULT`. Auth via
    `vor_provider.VorAuth` (gleicher Stack wie Disruption-Provider).
    Quota-Slot wird **vor** jedem Network Call via
    `_charge_one_request` reserviert.
  - **Stabil**: Event-Schema (`source: "ÖBB"`), `first_seen`-
    Persistenz, `DELAY_THRESHOLD_MINUTES = 9`, Self-Healing-Regel,
    Atomic-Write, CSV-Statistik-Logging, Cron-Schedule
    (`*/30 * * * *`). Feed-Reader-Subscribers bemerken den Wechsel
    nicht.
  - **Tests**: Mocks an der `_query_trips`-Boundary statt an einer
    pyhafas-`HafasClient`-Imitation. 64 Tests in
    `tests/scripts/test_update_stammstrecke_status.py` decken
    `_is_sbahn_leg` (3 Signal-Quellen), Direct-Connection-Filter,
    Realtime-Erkennung, Quota-Charge-vor-Fetch, Threshold-Semantik,
    `first_seen`-Persistenz, Self-Healing und Schema-Compliance ab.
  - **Doku**: `docs/reference/oebb_provider_logic.md` enthält jetzt
    nur noch die ÖBB-RSS-Scraper-Logik (`src/providers/oebb.py`); der
    Stammstrecke-Monitor ist nach
    `docs/reference/stammstrecke_provider_logic.md` ausgegliedert.
* **Changed (VOR API quota optimization)**: `DEFAULT_MONITOR_WHITELIST`
  in `src/providers/vor.py` ist jetzt **leer** (vorher
  `"Wien Hauptbahnhof,Flughafen Wien"`). Begründung: das
  Tagesbudget von 100 VAO-Requests wird nach der Stammstrecke-
  Migration von 96 Stammstrecken-Calls (`/trip` × 2 × 48) dominiert;
  parallele Departure-Board-Polls würden das Limit überschreiten.
  Operatoren, die das Legacy-Verhalten brauchen, setzen
  `VOR_MONITOR_STATIONS_WHITELIST` explizit per Umgebungsvariable.
* **Changed (Station-Enrichment-Whitelist)**: `fetch_vor_stops_from_api`
  in `scripts/update_vor_stations.py` macht Live-API-Calls jetzt nur
  noch für die 10 Stammstrecke-Stationen (`STAMMSTRECKE_VOR_IDS`).
  Alle anderen Station-IDs fallen auf die gepinnte
  `data/vor-haltestellen.csv` zurück. Begründung wie oben — bewahrt
  das Tagesbudget für den heißen Pfad. Test-Coverage:
  `test_fetch_vor_stops_from_api_skips_non_stammstrecke_ids`.
* **Added (Statistik-Dashboard)**: Zero-dependency Append-only-CSV-
  Pipeline und Markdown-Dashboard — Architektur-Kontext in
  [`docs/architecture.md` § 6](docs/architecture.md).
  - Producer — `scripts/update_stammstrecke_status.py` hängt nach
    jeder Median-Berechnung eine Zeile an
    `data/stats/stammstrecke_YYYY.csv` an (auch unterhalb der
    RSS-Schwelle, damit das Dashboard die *gesamte* Verteilung
    abbildet).
  - Producer — `src/build_feed.py:_update_item_state` schreibt im
    Strict-New-Pfad (Cache-Miss auf `_identity` *und* `guid`) eine
    Zeile in `data/stats/stoerungen_YYYY.csv`. Lange Streckeninformationen
    werden genau einmal gezählt.
  - Aggregator — `scripts/generate_markdown_stats.py` (Standardlib
    only: `csv`, `collections`, `datetime`, `statistics`, `pathlib`,
    `zoneinfo`, `argparse`) rendert `docs/statistik.md` mit
    ASCII/Emoji-Bars: Verteilung je Wochentag/Stunde, ⌀ Verspätung,
    Top-5-Hotspots mit Tageszeit-Profil.
  - Workflow — `.github/workflows/generate-stats.yml`
    (Cron `15 0 * * *` + `workflow_dispatch`) committet das Dashboard
    plus neue CSV-Dateien via `stefanzweifel/git-auto-commit-action`.
* **Added (Test-Isolation)**: Autouse-Fixture `isolate_stats_writes`
  in `tests/conftest.py` monkeypatcht `src.utils.stats.DEFAULT_STATS_DIR`
  pro Test auf `tmp_path` — verhindert, dass Suite-Läufe synthetische
  Zeilen ins committete Ledger schreiben (PR #1372).
* **Security (Bounded CSV reads)**: Aggregator routet jede CSV durch
  `read_capped_text` + `io.StringIO` (entspricht dem
  `tests/test_sentinel_csv_size_bomb.py`-Sentinel) und schreibt das
  Dashboard atomar via `atomic_write`. Producer-Writer sind best-effort
  (jeder `OSError` wird auf WARNING-Level geschluckt) — Statistik
  kann den Build nie kippen.
* **Changed (Audit-Report)**: Addendum (§ 14) zum bestehenden
  [`docs/archive/audits/oebb_stammstrecke_audit.md`](docs/archive/audits/oebb_stammstrecke_audit.md)
  dokumentiert die Statistik-Pipeline-Integration und bestätigt, dass
  die Audit-Befunde der Sections 1–13 unverändert bestehen
  (Verdict bleibt **0 Findings**, production-ready).
* **Changed (Reference-Doku)**: `docs/reference/oebb_provider_logic.md`
  korrigiert auf `MAX_JOURNEYS_PER_QUERY = 5` (vormals stale `12`)
  und enthält jetzt einen Abschnitt zur Statistik-Logging-Integration
  des Stammstrecke-Skripts.
* **Audit**: Vollständige Audit-Abnahme des S-Bahn Stammstrecke
  Monitors mit Bericht unter
  [`docs/archive/audits/oebb_stammstrecke_audit.md`](docs/archive/audits/oebb_stammstrecke_audit.md).
  Verifiziert: Mypy-Strict 0 Fehler, Bandit 0 Issues, Circuit Breaker
  trippt nach 10 Failures auf 1 h Recovery, HTTP-Timeout via
  Session-Patch, Europe/Vienna an allen 13 datetime-Sites, Schema-
  Compliance gegen `docs/schema/events.schema.json` (3 / 3 Szenarien
  grün), 47 Tests + 95.3 % Coverage. Audit-Resultat: **0 Findings**,
  Feature ist production-ready.
* **Tuning (Stammstrecke)**: `MAX_JOURNEYS_PER_QUERY` von 12 auf
  **5** gesenkt. Damit wird der Median nur über die *unmittelbar
  nächsten 5* anstehenden S-Bahnen pro Richtung gebildet (10 Journeys
  pro Cron-Tick gesamt) — schärferer Median, kleinere HAFAS-Payload,
  bessere Operator-Erwartung („wie ist es jetzt?"). Zwei neue
  Pin-Tests (`test_max_journeys_per_query_is_pinned_to_five` +
  `test_query_journeys_forwards_max_journeys_kwarg`) verhindern
  zukünftige Regressionen.
* **Feat (Stammstrecke)**: Self-Healing + first_seen-Persistenz +
  erweitertes Description-Schema. Konkret:
  - **first_seen-Persistenz**: Jedes Event in
    `cache/stammstrecke/events.json` trägt nun ein eigenes
    `first_seen`-Feld (ISO-8601, Europe/Vienna). Beim nächsten
    Cron-Tick liest das Skript den vorherigen Cache, erkennt für
    jede Richtung das ursprüngliche `first_seen` und behält es bei,
    solange die Episode anhält. Damit bleibt die `guid` für die
    Dauer einer Verspätungs-Episode stabil (Feed-Reader zeigen *eine*
    fortlaufende Meldung statt einer Flut neuer Einträge alle
    30 Minuten).
  - **Description-Format**: `"Durchschnittliche Verspätung von [X]
    Minuten in Richtung [Zielbahnhof] [Seit DD.MM.YYYY]"` —
    DD.MM.YYYY ist das `first_seen`-Datum, lokalisiert auf
    Europe/Vienna.
  - **Self-Healing**: Die Cache-Datei wird *zwingend* auf `[]`
    geleert, sobald (a) die Schnittstelle nicht erreichbar ist
    (jede pyhafas-Exception, ImportError oder offener Circuit
    Breaker) ODER (b) für *alle* Richtungen der Median ≤ 9 ist.
    Dies verhindert veraltete Warnungen im RSS-Feed bei einem
    Recovery oder einem API-Ausfall.
  - **GUID-Stabilität**: `guid` wird jetzt aus
    `(identity_prefix, iso_first_seen)` abgeleitet (statt
    `iso_pubDate`), `starts_at` ist das `first_seen` (statt der
    aktuellen Beobachtungszeit). `pubDate` bleibt als Freshness-
    Indikator dynamisch.
  - **Schema-Pin-Test**: Neuer `test_build_event_validates_against_schema`
    validiert das emittierte Event-Objekt gegen
    `docs/schema/events.schema.json` (via `pytest.importorskip("jsonschema")`).
* **Security/Liveness**: Stammstrecke-Monitor erzwingt jetzt einen
  echten HTTP-Timeout für pyhafas-Aufrufe. Das vorherige Code-Snippet
  versuchte, ``client.profile.requests.timeout`` zu setzen — pyhafas
  kennt diesen Attribut-Pfad nicht (``request_session`` heißt das
  Attribut), und ``requests.Session`` honoriert ``session.timeout``
  als Attribut ohnehin nicht. Resultat: ein hängender HAFAS-Endpoint
  hätte den Cron-Run bis zur GitHub-Actions-Wallclock (6 h) blockiert
  (DoS via Slow Upstream). Neuer ``_patch_session_timeout`` patcht
  ``session.request`` (die Low-Level-Methode, an die ``post/get/...``
  delegieren) und injiziert ``timeout=QUERY_TIMEOUT`` als Default.
* **Consistency**: Stammstrecke-Events nutzen jetzt das kanonische
  Stationsverzeichnis (``src.utils.stations``) für die Auflösung der
  Ziel-Stationsnamen statt sie hartzucodieren. Damit propagiert ein
  Rename in ``data/stations.json`` (z. B. wie zuletzt bei "Wien
  Hauptbahnhof") automatisch in die Beschreibung. Der kompakte
  "in Richtung Meidling"-Stil bleibt erhalten — der ``Wien ``-Präfix
  wird nach der Lookup-Auflösung gestrippt, weil die Beschreibung
  Wien implizit voraussetzt.
* **Feat**: S-Bahn Stammstrecke Monitoring jetzt **richtungsgetrennt**.
  `scripts/update_stammstrecke_status.py` wertet beide Fahrtrichtungen
  (Floridsdorf → Meidling und Meidling → Floridsdorf) strikt
  unabhängig aus und emittiert pro Richtung **separat** ein Event,
  wenn der Median der `departure_delay`-Werte > 9 Minuten liegt
  (Liste mit 0/1/2 Events). Eine Zusammenlegung beider Richtungen
  hatte das Signal verfälscht — eine Störung in eine Richtung läuft
  oft in der Gegenrichtung normal weiter. Pro Richtung eindeutige
  `guid`/`_identity` (`stammstrecke_delay_meidling` bzw.
  `stammstrecke_delay_floridsdorf`) damit Feed-Reader die Meldungen
  als separate Notifications darstellen. Description-Format jetzt
  "Durchschnittliche Verspätung von X Minuten in Richtung
  Meidling/Floridsdorf" (Plain Text, keine HTML-Tags).
* **Feat**: Circuit-Breaker-Konfiguration auf das documented
  10-Requests-pro-Stunde-Budget der ÖBB-Abfragen ausgerichtet:
  `failure_threshold=10`, `recovery_timeout=3600.0` (1 Stunde).
  Im Normalbetrieb produziert die Pipeline 4 Calls/h
  (Cron `*/30` × 2 Richtungen) — komfortabel unter der Schwelle;
  im Fehlermodus deckelt der Breaker zusätzlich auf 10 Versuche/h.
* **Feat**: S-Bahn Stammstrecke Monitoring. Neuer Workflow
  `.github/workflows/update-stammstrecke-status.yml` (Cron `*/30 * * * *`)
  ruft via `pyhafas` mit `OEBBProfile` direkte S-Bahn-Verbindungen
  Wien Floridsdorf (8100518) ↔ Wien Meidling (8100514) ab
  (`max_changes=0`) und schreibt schema-konforme Meldungen in
  `cache/stammstrecke/events.json`. Schreibt atomar via
  `atomic_write` und ist mit dem bestehenden Feed-Build über
  `read_cache_stammstrecke()` (Provider-Flag `STAMMSTRECKE_ENABLE`)
  integriert. Dokumentiert in `docs/reference/oebb_provider_logic.md`.
  Tests mocken `pyhafas` vollständig
  (`tests/scripts/test_update_stammstrecke_status.py`).
* **Security**: Der VOR-Tagesquota-Zähler wird jetzt sowohl in
  `load_request_count` als auch in `save_request_count` (dem
  Disk-Re-Read unter Lock) nach unten auf 0 begrenzt. Vor dem Fix konnte
  eine manipulierte `data/vor_request_count.json` mit
  `{"date": "<today>", "requests": -1000}` die Laufzeit-Quota-Prüfung
  stillschweigend umgehen (`todays_count >= MAX_REQUESTS_PER_DAY` ist für
  jeden negativen Zählerstand False) und wäre durch das nächste Speichern
  fortgeschrieben worden. Defense-in-Depth gegen kompromittierte
  CI-Runner und Korruption durch partielles Flushen.
* **Security**: Der Secret-Scanner erkennt jetzt vier zusätzliche
  Aussteller-Taxonomien, die der Entropie-Fallback verfehlt: JSON Web
  Tokens (`eyJ<base64url>.<base64url>.<base64url>` — drei
  punktgetrennte Segmente umgehen das `[A-Za-z0-9+/=_-]`-Alphabet),
  Hugging Face Access Tokens (`hf_<32+>`), DigitalOcean PATs
  (`dop_v1_<64 hex>`) und OAuth Refresh Tokens (`doo_v1_<64 hex>`)
  sowie GitLab Pipeline Trigger Tokens (`glptt-<40>`). Jeder Fund meldet
  jetzt den ausstellerspezifischen Grund statt eines generischen
  High-Entropy-Treffers, was Triage und Revocation beschleunigt.

## [2026-05-05]
* **Data**: Wien-Stadtgrenzen-Polygon ersetzt — neu: offizielle
  `LANDESGRENZEOGD`-Quelle der MA 41 – Stadtvermessung (5.637 Vertices,
  EPSG:4326, CC BY 4.0). Vorher: hand-kuratiertes 31-Vertex-Polygon
  (PR #1190), davor 8-Vertex-Konvex-Hülle (PR #1189). Genauigkeit
  ~200 m → ~1–2 m.
* **Data**: 9 ÖBB-Stationskoordinaten gegen offizielle VOR-Werte
  korrigiert (Aspern Nord 1.160 m, Gersthof 1.694 m, Jedlersdorf 1.219 m,
  Handelskai 543 m, Rennweg 522 m, Breitensee 491 m, Floridsdorf 293 m,
  Kaiserebersdorf 319 m, Mitte-Landstraße 161 m, Liesing 359 m). PR #1188.
* **Data**: Kanonische Namen vereinheitlicht — `Hbf`/`Bf`-Abkürzungen
  durch ausgeschriebene Vollformen ersetzt (Wien Hauptbahnhof, Wien
  Westbahnhof, Wien Franz-Josefs-Bahnhof, Wiener Neustadt Hauptbahnhof,
  St. Pölten Hauptbahnhof, München Hauptbahnhof). Abkürzungen bleiben als
  Aliase erhalten. PR #1188.
* **Data**: Rennweg-Doublette aufgelöst — irreführende Bahnhof-Aliase
  aus dem Google-Places-U3-Eintrag entfernt. PR #1188.
* **Fix**: `_normalize_token` Umlaut-Faltung wird nur ab Token-Länge ≥ 4
  angewendet. Damit bleiben kurze ÖBB-Stellencodes wie `Sue` (Wien
  Süßenbrunn) und `Su` (Stockerau) distinkt im Lookup. PR #1189.
* **Fix**: source-Feld-Format in stations.json vereinheitlicht
  (Komma-getrennt, kein Whitespace); `stations.py`-Tie-Break nutzt
  Token-Set statt String-Equality, sodass Drift toleriert wird. PR #1188.
* **Feat**: NamingIssue-Validator-Kategorie hinzugefügt — prüft
  kanonische Namens-Eindeutigkeit und no-space-Source-Format. PR #1188.
* **Feat**: WL-OGD-Auto-Download in `update_wl_stations.py` —
  haltestellen/haltepunkte werden vor dem Merge live von
  `data.wien.gv.at` geladen, mit graceful Fallback auf lokale Dateien.
  Schließt die `wl_diva`-Lücke beim monatlichen CI-Lauf. PR #1189.
* **Feat**: JSON Schema für `data/stations.json` unter
  `docs/schema/stations.schema.json` plus Pin-Test
  `tests/test_stations_schema.py`.
* **Feat**: `docs/stations_validation_report.md` wird im monatlichen
  `update-stations.yml`-Lauf automatisch regeneriert; veraltete
  Archiv-Kopie entfernt.
* **Docs**: README-Stationsverzeichnis-Abschnitt vollständig überarbeitet
  (alle Felder, alle Quellen mit Lizenzen + Pflicht-Attribution, neue
  CLI-Flags, NamingIssue-Validator).
* **Docs**: Audit-Bericht-Reihe unter
  `docs/archive/audits/stations_data_audit_2026-05-05*.md` mit
  zentralem Index.

## [2026-02-02]
* `Fix`: VOR API auf `departureBoard` umgestellt und authentifizierte Requests repariert.
* `Security`: Rate-Limit-Sperre (max 100 Req/Tag) implementiert.
* `Data`: Stations-IDs auf HAFAS-Format aktualisiert.
* **Feat**: Verbessertes Deep-Parsing für Störungsmeldungen in Abfahrtsdaten.

## Quelle: PDF-Handbuch

- 2026-01-14 – Feed-Deduplizierungslogik optimiert, um VOR-Provider-Events (API) gegenüber ÖBB-Provider-Events (Scraper) zu priorisieren. Konflikte werden jetzt aufgelöst, indem das VOR-Event als Master-Record beibehalten und eindeutige Beschreibungsdetails aus dem ÖBB-Event eingemergt werden. Das sichert höhere Datenqualität und Stabilität.
- 2025-08-11 – Line Info Service ergänzt. (Kapitel 19)
- 2025-07-02 – Aktualisierung 5.9.2 zu Informationstexten bei Störungen.
- 2025-05-22 – Neuer Parameter `includeDrt` im Trip-Service.
- 2025-02-11 – Überarbeitung der Handbuchstruktur.
- 2024-12-10 – Kapitel 13.2 und 14.2 zu Scrolling in DepartureBoard und ArrivalBoard erweitert.
- 2024-11-27 – Kapitel 5 um neue Inhalte (5.4, 5.5, 5.11, 5.13, 5.16) und Meta-Parameter in `location.name` ergänzt.

Weitere Einträge und Detailbeschreibungen finden sich in der Änderungshistorie des PDFs (Kapitel 1.1).
