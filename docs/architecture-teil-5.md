[Architektur-Karte — Wien ÖPNV Feed](architecture.md), Teil 5.

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
    (`_carry_item_identity`, seit 2026-10-06, siehe `docs/development-teil-2.md`,
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

* **Folgejahre, Jahreswechsel, Schaltjahre (geprüft 2026-10-03 bis 2040).**
  Dieselbe echte Septembernacht auf jede Umstellung bis 2040 verschoben (letzter
  Sonntag im Oktober und März, darunter Jahre mit dem 25. und dem 31.), dazu
  echte Nächte gleichen Wochentags auf jeden Silvester 2026 bis 2040, eine
  echte Woche über den Jahreswechsel 2026/27 und über den 29.02.2028 sowie die
  Schalttage 2032, 2036 und 2040: 49 Szenarien, 1 854 Feed-Läufe, deutscher
  und englischer Feed. Verschoben wird nach der Wiener Uhrzeit (Datum plus N
  Tage), ein Lauf in der doppelten Stunde läuft zweimal. Bis auf die
  verschobenen Daten stimmten alle Läufe mit dem Original überein: Jahr in der
  Zeitzeile nur außerhalb des laufenden Jahres („[Bis 08.01.2027]“ am 31.12.,
  „[Bis 08.01.]“ ab 01.01.), Wochentag nur bis sechs Tage voraus und richtig,
  „[Seit 31.12.2026]“ für eine Störung von Silvester nach Mitternacht.
  Gefunden und behoben:
  - **WL-Ende ohne Jahr:** WL schreibt das Jahr oft nur beim Beginn („Ab
    24. August 2026, etwa 06:00 Uhr, bis 07. September“). Das Ende wurde
    unabhängig davon auf das nächstgelegene Datum gelegt; „Ab 01. Mai 2026
    bis Ende Dezember“ las sich als Dezember 2025, und das Ende ging verloren.
    Ein Ende ohne Jahr ist jetzt der erste solche Tag ab dem Beginn im Text
    (`_end_day`), auch über den Jahreswechsel („Ab 28. Dezember 2026 bis
    06. Jänner“). In den 228 Texten mit Ende seit Juli änderte sich nichts.
  - **Statistik-Jahr:** `scripts/generate_markdown_stats.py` las das Jahr beim
    Einlesen der Argumente von der Uhr der Maschine; ein Lauf mit `--now-iso`
    in einem anderen Jahr wertete das falsche Jahr aus. Es folgt jetzt der
    Laufzeit.
  - **Statistik-Seite am Jahreswechsel:** `docs/statistik.md` wird nur beim
    Lauf zwischen 00:00 und 00:29 neu geschrieben und zeigt das Kalenderjahr.
    Ihr letzter Stand eines Jahres entstand am 31.12. gegen 00:15, ohne
    Silvester, und am 01.01. ersetzte sie ein fast leeres neues Jahr. Jetzt
    schreibt der erste Dashboard-Lauf eines neuen Jahres zusätzlich das ganze
    Vorjahr samt 31.12. nach `docs/statistik-<Vorjahr>.md` (Michaels Wahl
    „Jahresarchiv“, 2026-10-03); fällt der Lauf am 01.01. aus, schreibt sie
    der nächste. Bis 2026-11-10 wurde die Seite danach nie mehr angefasst;
    seither folgt sie ihrem Ledger, ist vom Dashboard aus verlinkt und trägt
    den Abdeckungshinweis ihres Jahres (Teil 15, §12).
  Tests: `tests/test_calendar_years.py`.

### Zeitraum einer WL-Meldung: Plausibilitätsprüfung (seit 2026-10-02)

Wiener Linien nennen den Zeitraum einer Meldung bis zu dreimal: in
`time.start`/`time.end`, als „ab“/„am“-Datum im Titel und im Abschnitt
„Zeitraum:“ der Beschreibung. Ein Tippfehler steht meist nur an einer
Stelle. Anlass: „47B: Laufveranstaltung am 04.10.2027“ (Text und `time.end`:
04.10.2026) schob den Beginn ein Jahr hinter das Ende, der deutsche Feed
zeigte „[Ab 04.10.2027]“ für einen Lauf am selben Sonntag.

`src/providers/wl_plausibility.py` wägt die Angaben gegeneinander ab, in
dieser Reihenfolge:

1. **Mehrheit bei Widerspruch.** Titel- und Textdatum mit gleichem Tag und
   Monat, aber anderem Jahr: Es gilt das Jahr näher an der Veröffentlichung
   (`year_conflict`). Ein Beginndatum vor der Veröffentlichung, dessen Tag in
   einem späteren Jahr höchstens 90 Tage nach ihr liegt und nicht nach WLs
   echtem Ende oder dem Text-Ende statt eines Platzhalters (so entschieden
   vom Betreiber am 12.11.2026, „Ja, Text-Ende zählt“; nicht einem aus einer
   Dauer gerechneten), trägt das Jahr einer älteren Meldung: Es gilt dieser
   Tag (`stale_year`, seit 2026-11-11). Anlass: 39B Friedhofsverkehr,
   „Am 24. Dezember 2024“, stand ab 11.11.2026 als laufend im Feed.
2. **Harte Grenzen.** Ein Beginn nach dem Ende wird vom Ende überstimmt
   (`begin_after_end`). Ein Beginn mehr als 365 Tage nach der
   Veröffentlichung zählt nur, wenn eine zweite Angabe ihn bestätigt
   (`unconfirmed_lead`). Längster echter Vorlauf im Cache vom 02.10.: 88
   Tage (Stammstrecke Phase 2).
3. **Im Zweifel konservativ.** Was offen bleibt, fällt auf `time.start`
   zurück. Eine Meldung wird wegen eines Widerspruchs nie versteckt; ein
   `time.start` nach `time.end` wird nur gemeldet (`source_start_after_end`).
4. **Jede Korrektur wird gesammelt.** Sie steht als Warnung im Log, und
   `scripts/update_wl_cache.py` führt sie in
   `data/wl_plausibility_anomalies.json` (gleiches Format wie
   `data/feed_line_anomalies.json`: `kind`, `title`, `detail`, `first_seen`,
   `last_seen`, `days_seen`). Die Datei entsteht erst mit der ersten
   Korrektur.

Die bisherigen Regeln gelten unverändert und zählen nicht als Korrektur: Ein
11:11-Ende weicht dem Ende aus „Zeitraum:“, ein Beginndatum verschiebt den
Start nur nach hinten (ein altes Jahr ausgenommen, Regel 1), und ein Textdatum
hinter dem Ende ist das Datum einer späteren Phase. Was die Prüfung nicht
sehen kann: eine Angabe, die an allen Stellen gleich falsch ist.

Ein Ende ohne Jahr („Ab 24. August 2026 … bis 07. September“) ist der erste
solche Tag ab dem Beginn im Text, auch über den Jahreswechsel; ohne Beginn im
Text das Datum, das der Veröffentlichung am nächsten liegt (seit 2026-10-03).

Nennt „Zeitraum:“ kein Ende, sondern eine Dauer („auf Dauer von etwa sechs
Wochen“, seit 2026-10-02), gilt statt eines 11:11-Endes der Start aus dem Text
plus Dauer plus Puffer, auf 23:59 des Tags. Der Puffer ist die halbe Dauer,
mindestens eine Woche (`MIN_DURATION_BUFFER`): WL schreibt „etwa“, und eine
Baustelle, die länger dauert, soll nicht aus dem Feed fallen, solange WL sie
noch ausliefert. Die Rechnung verkürzt ein 11:11-Ende nur, sie verlängert es
nie. Anlass: „65A/66A“ (ab 12.08., „etwa zwei Wochen“) stand am 02.10. noch
mit Ende 31.08.2027 im Cache; 29B/N25 endet jetzt am 07.12. statt 31.12.,
36A/36B am 12.11. statt 16.09.2027, 63A behält sein 11.11.

Eine Haltestellenverlegung oder -auflassung nennt ihren Zeitraum unter
„Dauer:“ statt „Zeitraum:“ („Dauer: Ab 05. Oktober 2026, etwa 07:00 Uhr für
etwa zwei Wochen“); alle 37 solchen Meldungen der Rohdaten vom 2026-10-04
tun das, keine hat „Zeitraum:“. Seit 2026-10-04 gilt „Dauer:“ dort als
Überschrift des Zeitraums (`_period_text`), mit denselben Regeln. Anlass:
„26E/N20: Fultonstraße“, am 28.09. für den 05.10. veröffentlicht, stand mit
„[Bis 28.09.2027]“ (11:11-Ende) im Feed-Nachbau; jetzt „[Ab Mo 05.10. bis
26.10.]“. 27 der 37 Meldungen beginnen dadurch später (der Tag aus dem Text
statt der Veröffentlichung), sechs enden früher (27A, 60A und 76A Ende
Oktober 2026 statt im Herbst 2027). Nur dort: Vier WL-Störungen schreiben „Dauer:“ vor
eine Uhrzeit oder „Bis 30.10.2026 Betriebsschluss“, das die Beginnregel für
einen Beginn am 30.10. hielte.

Eine Dauer in Jahren („für etwa ein Jahr“, „für ca. 1 Jahr“, „für etwa
1,5 Jahre“, „eineinhalb“) zählt seit 2026-10-08 wie Wochen und Monate
(365 Tage je Jahr). Anlass: 1A Habsburgergasse, 57A Haus des Meeres und
26A/N20 Siebeckstraße standen mit ihrem 11:11-Ende zwei und drei Jahre
nach der Veröffentlichung im Feed; jetzt enden sie am 20.07.2027,
31.08.2027 und 24.09.2028 (Start plus Dauer plus halbe Dauer).

**Ein 11:11-Ende ohne Ende und Dauer im Text erscheint nicht in der
Zeitzeile** (seit 2026-10-08, Betreiberentscheidung „Kein Ende“,
`wl_plausibility.end_unknown`). Das 11:11-Ende ist bei WL ein von Hand
gesetztes Ablaufdatum, meist ein Jahr nach der Veröffentlichung, kein
erwartetes Ende: Von den 124 Hinweisen mit 11:11-Ende, die WL zwischen dem
21.02. und dem 08.10.2026 beendete, verschwanden 122 vor diesem Datum, im
Median 336 Tage davor. Die Zeitzeile liest sich deshalb wie bei jeder
Meldung ohne Ende: „[Seit 08.10.]“, angekündigt „[Ab Mo 19.10.]“ statt
„[Bis 08.10.2027]“ (72A Haidestraße S) oder „[Bis 31.12.2027]“ (38A, „auf
derzeit unbekannte Zeit“). Am 08.10. betraf das 25 der 49 WL-Hinweise mit
11:11-Ende. Nennt der Text ein Ende oder eine Dauer, bleibt alles wie oben,
auch ein 11:11-Ende, das eine längere Dauer nicht verkürzt (63A). Ein
vages Ende („bis voraussichtlich Mitte August 2026“, „bis etwa Frühjahr
2027“) nennt keinen Tag und zählt als keines; als Monatsletzter gelesen
hätte es Meldungen beendet, die WL noch auslieferte (85A, „Mitte Mai“, bis
02.08.2026 gelistet). Eine Dauer-Spanne („für etwa zwei bis drei Wochen“,
„2–3 Monate“) zählt ebenso als keine Dauer (Betreiberentscheidung
2026-10-15, „So lassen“): Die Meldung zeigt kein Ende und bleibt, solange
WL sie ausliefert; falsch ist dabei nichts. In den 9.741 verschiedenen
Texten aller Anbieter seit 01.07.2026 kam keine Spanne vor, alle 103
Dauerangaben waren einfache Zahlen. Das 11:11-Datum bleibt das Ablaufdatum der Meldung
(`ends_at`, Altersfilter); geändert ist nur die Zeitzeile, im englischen
Feed ebenso.

**Ein Ende um 23:59 genau ein Jahr nach der Veröffentlichung zählt wie das
11:11-Ende** (seit 2026-11-06, Betreiberentscheidung „Ja, bitte“,
`wl_plausibility.placeholder_end`). Anlass: „5B: Bauarbeiten“, am 06.11.2026
um 14:11 veröffentlicht mit „Ab Montag, den 9. November 2026, etwa 07:00 Uhr
für ca. drei Wochen“, hatte `time.end` 06.11.2027 23:59 und stand ab 14:31
mit „[Ab Mo 09.11. bis 06.11.2027]“ im Feed; von den elf WL-Hinweisen mit
einem 23:59-Ende im Cache dieses Tages war es der einzige mit dem Jahrestag
der Veröffentlichung, die anderen endeten binnen Tagen. Für so ein Ende
gelten dieselben Regeln wie für 11:11: das Ende aus dem Text, sonst Start
plus Dauer plus Puffer (5B: 09.11. + 21 Tage + 10,5 Tage, „bis 10.12.“),
sonst kein Ende in der Zeitzeile. Die Zeitzeile braucht dazu die
Veröffentlichung der Meldung (`pubDate`, `_shown_end` in
`src/feed_build/item_meta.py`); ein 23:59-Ende an jedem anderen Tag ist
ein Ende („bis 08.11.“). Der 29.02. hat seinen Jahrestag am 28.02.

**Ein Text-Ende beendet die Meldung, auch wenn WL sie noch ausliefert**
(Betreiberentscheidung 2026-10-09, „Text-Ende beendet“). Ist das Ende aus
dem Text oder aus Start plus Dauer vorbei, verwirft der Abruf die Meldung
(„außerhalb des Zeitraums“). WL liefert solche Meldungen oft noch Tage bis
Wochen aus (13 von 52 zwischen Februar und Oktober 2026, 1 bis 41 Tage,
z. B. 93A/96A/N91 Schillwasserweg „bis etwa Ende Juli“, gelistet bis 10.09.).
Das ist gewollt und kein Defekt: Bliebe die Meldung, zeigte ihr eigener
Text („bis etwa Ende Juli 2026“), dass sie abgelaufen ist, und das
verwirrt. Ein Versuch, sie bis zum Rückzug durch WL zu behalten (#1989),
wurde mit #1990 zurückgenommen.

#### WL-Hinweis ohne Text (seit 2026-11-07)

Ein WL-Hinweis, dessen Text leer ist (nur Tags, `&nbsp;` und Leerraum),
wartet, bis WL ihn füllt; der nächste Abruf mit Text bringt ihn in den Feed
(`_is_empty_wl_notice` in `_post_filter_wl`,
`src/feed_build/cache_filters.py`). Anlass: WL stellte „5B: Bauarbeiten“ am
06.11.2026 um 13:59 mit dem Text „<p></p>“ online und füllte ihn um 14:11; der
Feed von 14:01 zeigte den Titel nur mit „[Seit heute]“ (KI-Wächter, Issue
#2170). Ein Hinweis-Titel („Bauarbeiten“) sagt nicht, was sich für den
Fahrgast ändert, und Hinweise stehen hinter Störungen. Eine Störung ohne Text
bleibt: Ihr Titel nennt Linie und Ursache („U2: Betriebsstörung“). Gemessen
über alle WL-Cache-Stände seit 01.06.2026 (6.328 Stände, 337 Hinweise): Der
5B-Hinweis ist der einzige leere, dazu sechs Störungen für je einen bis vier
Abrufe. Betreiberentscheidung 2026-11-07 („Zurückhalten bis Text da ist“).

#### WL-Netzänderung als Hinweis (seit 2026-11-07)

Eine WL-Langmeldung kommt nur in den Feed, wenn ihr Text ein
Einschränkungs-Stichwort trägt (`KW_RESTRICTION`, `docs/development-teil-3.md`,
„Wiener Linien“). Eine Netzänderung schränkt nichts ein – eine Linie bekommt
eine Haltestelle – und nennt deshalb keines: „9A: Errichtung einer
Haltestelle“ (ab 07.09.2026) und „57A: Errichtung einer Haltestelle“ (ab
05.09.) fielen in jedem Abruf vom 04. bis 07.10.2026. WL setzt die Art der
Meldung als Überschrift an den Textanfang („<h2>Netzänderung</h2>“);
`is_network_change` (`src/providers/wl_news_kind.py`) liest diese Markierung,
nicht Wörter im Fließtext, und `_news_passes_gate` in
`src/providers/wl_fetch.py` lässt die Meldung als Hinweis durch. Sie steht
ohne Ende im Feed (`_notice_end`): WLs `time.end` ist das Ende des Hinweises,
Wochen nach der Änderung (9A: ein Monat, N67: zehn Tage), nicht der Maßnahme;
als „[Bis Mi 07.10.]“ hätte es die neue Haltestelle befristet. Ihren Beginn
nennt WL unter „Zeitpunkt:“ (`_PERIOD_HEADING_RE`), die Zeitzeile zeigt „Seit
07.09.“. Auch eine Netzänderung, die das Tor am Stichwort passiert („N67:
Geänderte Linienführung ab 05.12.2026“: „Die Fahrt über Inzersdorf, Biróstraße
entfällt“), steht ohne WLs Ende (15.12.). Betreiberentscheidung 2026-11-07
(„Als Hinweis aufnehmen“).

Weiter in [Teil 6](architecture-teil-6.md).
