[Architektur-Karte — Wien ÖPNV Feed](architecture.md), Teil 15.

## 10. Robustheit und Laufzeit des Feed-Baus (seit 2026-11-10)

Aus dem Code-Audit vom 10.11.2026. Betreiberentscheidung (Michael,
2026-11-10, Karte „Soll ich den PR Robustheit und Laufzeit aus dem Audit
bauen?“, Antwort „Ja, alle vier Teile“), zu seinem Ziel vom 04.11.: ein
Feed, der ohne Handgriffe läuft und Rechenzeit schont.

**Datum am Rand des Kalenders.** Ein Zeitstempel, dessen Jahr außerhalb von
2000 bis 2100 liegt, gilt als nicht gesetzt (`PLAUSIBLE_YEARS` und
`plausible_moment` in `src/utils/vienna_clock.py`). Anlass, im Audit
nachgestellt: Eine einzige WL-Störung mit dem Ende
`9999-12-31T23:59:59.000+0100` kam in den Cache, und der Feed-Bau brach
danach in jedem Lauf ab (`OverflowError` beim Rechnen mit dem Datum), solange
die Quelle die Meldung führte; ein Beginn im Jahr 1 beendete schon den
WL-Abruf. Solche Werte stehen in Quellen für „offen“ oder „unbekannt“; mit
ihnen lässt sich weder rechnen noch eine Zeitzeile schreiben. Die Regel
greift dort, wo ein Zeitstempel gelesen wird: bei WL (`_iso` in
`src/providers/wl_item_fields.py`, `_moment` in
`src/providers/wl_resolved.py`, die Daten aus dem Titel in `src/providers/wl_title_dates.py` und aus dem „Zeitraum:“-Text in
`src/providers/wl_period.py`), bei den ÖBB (`_parse_dt_rfc2822` und
`_parse_period` in `src/providers/oebb.py`, jede Seite eines Zeitraums für
sich), bei den Baustellen (`_parse_datetime` in
`src/providers/baustellen_events.py`), beim Lesen der Caches
(`_parse_datetime` in `src/feed_build/base.py`), beim Lesen des Zustands
(`_state_stamp` in `src/feed_build/base.py`: ein solcher Wert in
`data/first_seen.json` gilt als unlesbar, wie bisher ein Wert, der kein
Datum ist: Warnung im Protokoll, als erste Sichtung gilt der Zeitpunkt des
Baus) und im Stammstrecken-Monitor (`_parse_vao_dt` in
`src/stammstrecke/legs.py`). Die Liste führt `READERS` in
`tests/test_plausible_years.py`. Die Meldung bleibt
im Feed; ihr fehlt nur dieses Feld, wie bei einer Meldung, die kein Ende
nennt. Das gilt für jedes Jahr außerhalb der Grenzen, nicht nur für die,
mit denen Python nicht rechnen kann: Ein Ende im Jahr 1970 beendet eine
Meldung nicht mehr, ein Beginn im Jahr 2101 hält sie nicht mehr zurück; sie
gilt wie eine Meldung ohne Ende, bis die Quelle sie nicht mehr führt oder
der Altersfilter greift. Die aufgezeichneten Daten nennen nur die Jahre 2019
bis 2031, keine Meldung ändert sich (Nachspiel im Changelog vom
10.11.2026).

**Eine Meldung hält den Bau nicht an.** Scheitert die Feed-Kette oder das
Erzeugen des deutschen Feeds, sucht `build_isolating`
(`src/feed_build/isolation.py`) die Ursache: Jede gesammelte Meldung läuft
einzeln durch Kette und Ausgabe, auch eine, die es nicht unter die zehn
Plätze geschafft hätte. Scheitert dabei höchstens die Hälfte der Meldungen,
wird der Zustand neu von der Platte gelesen und der Feed ohne diese
Meldungen gebaut; Zähler und Zustand stammen aus diesem zweiten Durchgang.
Der normale Lauf ist unverändert: Die Kette läuft einmal, nur eine Kopie
der Meldungen wird vorher angelegt, 0,3 ms bei den 88 Meldungen vom
10.11.2026 (lassen sie sich nicht kopieren, baut
der Lauf wie vor dem 10.11. ohne Einzelprüfung und schreibt die Warnung
„Meldungen nicht kopierbar“). Scheitert keine Meldung allein,
scheitert mehr als die Hälfte (dann liegt der Fehler im Code, nicht an
einer Meldung, und der Feed käme fast leer heraus) oder scheitert auch der
zweite Durchgang, endet der Bau wie bisher mit dem ersten Fehler, und der
veröffentlichte Feed bleibt, wie er ist; das Protokoll nennt dann, wie
viele Meldungen einzeln scheiterten. Das Schreiben der Feed-Dateien und der
englische Feed sind wie bisher eigene Schritte. Eine übersprungene Meldung
wird gemeldet: als Fehlerzeile im Protokoll des Laufs, im Gesundheitsbericht
des Baus (Abschnitt „Übersprungene Meldungen“ und Fehlerliste; im JSON der
Schlüssel `skipped_items` mit Quelle, Titel und Fehlerklasse samt Stelle im
Code, ohne den Wortlaut des Fehlers) und von dort im Update-Lauf über die
Feed-Wächter-Regel `meldung-uebersprungen` als Issue, also am selben Tag
per Mail ([`reference/feed_watch.md`](reference/feed_watch.md)). Die Läufe
„Build feed“ und „Manual full refresh“ haben keinen Wächter-Schritt: Dort
steht eine übersprungene Meldung nur im Protokoll, das Issue kommt mit dem
nächsten Update-Lauf. Im Gesundheitsbericht zählt die Meldung bei
„Rohdaten“ mit und fehlt ab „Nach Altersfilter“. Der Bericht selbst besteht
im Update-Lauf nur während des Laufs (`.gitignore`; der Wächter-Schritt
liest ihn nach dem Schritt „Build feed“ desselben Jobs,
`tests/test_feed_watch/test_skipped_items_workflow.py`); was der Betreiber
liest, sind das Issue, die Annotation des Laufs und
`data/feed_watch.json`. Der Lauf bleibt grün („Alarmwege: rot, Issue,
Hinweis“, §7): Die Technik arbeitet, dem Feed fehlt eine Meldung. Die
Zeilen der Störungsstatistik (`data/stats/stoerungen_JJJJ.csv`) hält der
Bau zurück, bis der deutsche Feed erzeugt ist (`rows_held_back` in
`src/utils/stats.py`), und schreibt sie vor den Feed-Dateien: Ein
gescheiterter Durchgang und die Einzelproben zählen nichts. Scheitert
danach das Schreiben der Feed-Datei, stehen die Zeilen wie bisher schon in
der Statistik. `lint` prüft nur die Feed-Kette, ohne diese Einzelproben und
ohne die Ausgabe: Scheitert die Kette an einer Meldung, endet `lint` mit
dem Fehler (Exit-Code 2); eine Meldung, an der erst die Ausgabe scheitert,
findet nur der Bau.

**Stationssuche über einen Index.** Ob ein ÖBB-Text eine Wiener Station
nennt, prüfte `text_has_vienna_connection` (`src/utils/stations.py`) mit
einem einzigen Muster aus 159.754 Namen und Aliasen: 7 s zum Kompilieren und
im Mittel rund 1 s je ÖBB-Text, der ÖBB-Abruf brauchte damit 13 bis 16 s.
`WholeTermSearch` (`src/utils/term_search.py`) gibt für die Begriffe des
Verzeichnisses dieselbe Antwort über einen Index nach den ersten vier
Zeichen: Aufbau 0,5 s, ein Text 0,1 bis 0,2 ms, Arbeitsspeicher des
Prozesses 143 statt 583 MB. Welches Zeichen welches trifft und was ein Wortzeichen ist,
fragt der Index Zeichen für Zeichen das Modul `re` selbst, statt dessen
Regeln nachzubauen; käme ein Zeichen vor, das sich so nicht einordnen
lässt, fiele er auf das alte Muster zurück (bei keinem Unicode-Zeichen der
Fall). Die Normalisierung der Stationsnamen (`_normalize_token` in
`src/utils/stations_normalize.py`) merkt sich ihre Ergebnisse, weil die zwei
Indexe des Verzeichnisses dieselben 250.000 Namen nacheinander fragen: der
zweite Index braucht 0,5 statt 1,8 s. Zusammen, gemessen am 10.11.2026 mit
den Daten des Tages, erster Aufruf im Prozess: ÖBB-Abruf 13 bis 17 s →
2,7 s, Feed-Bau 4,2 bis 4,9 s → 2,7 bis 2,9 s.

## 11. Ein Vorfall, ein Platz: gekürzte Ursache und vertauschte Rollen (seit 2026-11-10)

Betreiberentscheidung vom 2026-11-10 (Karte „Zusammenfassen prüfen“), Ergänzung zu
`_join_part_line_tickers` (Teil 2, „Eine zweite Ausnahme“). Am 09.11. von 12:30 bis 14:30 Uhr belegte ein
Oberleitungsschaden vier der zehn Plätze, „2/12/31: Beschädigte
Oberleitung durch einen LKW“ neben „12: Fahrleitungsgebr.“, „31:
Fahrleitungsgebr.“ und „2: Fahrleitungsgebr“. Der Vergleich nahm den
Urheber „durch einen LKW“ mit, und die von der Tafel gekürzte Ursache
kannte er nicht. Jetzt zählt die Ursache so, wie der zusammengefasste
Titel sie nennt (`_title_cause`), und eine gekürzte Ursache wird
ausgeschrieben, wenn sie ohne Schlusspunkt und mit mindestens sechs
Buchstaben genau eine bekannte Ursachengruppe beginnt
(`_cause_key`, „Fahrleitungsgebr.“ → Oberleitungsstörung); beginnt sie
mehrere, bleibt sie, wie WL sie schrieb. Am 23.10. („40/41/42:
Polizeieinsatz“ neben „40/41: Polizeieinsatz“) und 10.11.
(„37/40/41/42: Verkehrsunfall“ neben „41: Verkehrsunfall“) waren die
Rollen vertauscht: Die Tafeln nannten alle Linien, WLs Meldung mit
Nummer nur einen Teil, mit nichts als dem Standardsatz. Jetzt nimmt der
Eintrag mit mehr Linien den mit weniger auf, wenn genau einer der beiden
eine WL-Nummer trägt; zwei Meldungen mit Nummer und zwei reine
Tafel-Kurzmeldungen bleiben getrennt. WLs Standardsatz „Nach einer
Fahrtbehinderung …“ fügt einem Eintrag mit eigenem Text nichts hinzu.
Andere oder keine Ursache behält weiter ihren Platz („60: Falschparker“
neben „10/60: Polizeieinsatz“, 10.11.). Nachgerechnet über alle 2.207
Feed-Stände vom 29.09. bis 10.11.: 11 ändern sich, alle an diesen
Vorfällen und am 05.11. („12: Fahrleitungsgebr, Beschädigte Oberleitung“
wird „12: Beschädigte Oberleitung“); 0 Fehler.

## 12. Die Jahresseite der Statistik: Überblick, Baustellen, Rückblick (seit 2026-11-10)

Jedes abgeschlossene Jahr mit Zeilen in den drei Ledgern des Dashboards hat
eine Seite `docs/statistik-<Jahr>.md` (`src/markdown_stats/year_archive.py`).
Betreiberentscheidung vom 10.11.2026 (Michael, 17:44): Die Seite entsteht
jedes Jahr am 01.01., das Jahr ist auf der Website unter „Aktuelle Meldungen“
wählbar, sie fasst alle Ausfälle und Verspätungen in einer Statistik zusammen,
und die Baustellen des Jahres fasst Gemini zusammen, seine ausdrückliche
Ausnahme von „eine KI liest nur mit und meldet, sie schreibt keinen Text um“
(Teil 6, „KI-Wächter“); einen Text des Feeds schreibt weiter keine KI (zur zweiten Ausnahme vom 12.11.2026 siehe Teil 16, §14).

**Prüfung des Jahresarchivs vom 10.11.2026.** Im Nachspiel mit dem echten
Ledger (100 Ticks um 56 Tage auf den Jahreswechsel verschoben) schreibt der
Lauf um 00:01 am 01.01. die Seite des Vorjahres mit allen Zeilen bis 31.12.
23:59. Seither verweisen Dashboard
(`Frühere Jahre: [2026](statistik-2026.md)`) und Jahresseite („Abgeschlossenes
Jahr · Zur laufenden Statistik · Zur Website“) aufeinander, die Seite trägt
den Abdeckungshinweis ihres Jahres (`render_finished_year_coverage_note`), und
jeder Dashboard-Lauf rendert jedes abgeschlossene Jahr mit Ledger-Datei und
schreibt die Seite, wenn sie fehlt oder anders lauten würde
(`write_year_archives`): Ein Ledger ändert sich auch nach dem 01.01. noch.
Nachspiel: `changelog/2026-11-10.md`.

**Aufbau.** Kopf mit Rückweg, „Kennzahlen auf einen Blick“, „Das Jahr im
Überblick“, die Abschnitte Stammstrecke, Ausfälle und Störungen, „Baustellen
mit Folgen für den öffentlichen Verkehr“, Quellen (auch
`data/stats/baustellen_*.csv` und, mit Rückblick,
`data/stats/baustellen_rueckblick_*.json`). Gezählt werden nur Zeilen, deren
Zeitstempel in Wiener Zeit im Jahr liegt; eine Zeile eines anderen Jahres
fehlt in jeder Zahl („Jahresarchiv <Jahr>: 1 Zeile / <n> Zeilen mit dem
Zeitstempel eines anderen Jahres nicht gezählt.“). Ohne Zeile in den drei
Ledgern des Dashboards gibt es keine Seite, auch mit Baustellen-Ledger.

**Das Jahr im Überblick** (`src/markdown_stats/year_stats.py`). Unter dem
ersten Absatz steht einmal der Abdeckungshinweis, damit „Die Jahreszahlen
unten“ für jede Zahl gilt. Dann „Erfasster Zeitraum“, „Nach Monaten“ (ein
Monat außerhalb des erfassten Zeitraums zeigt „–“), „Verspätungen nach
Richtung“, „Tage mit der höchsten mittleren Verspätung“ und „Tage mit den
meisten Ausfällen“. Die Tagestabelle der Verspätungen nimmt nur Tage mit
mindestens 10 Beobachtungen und ordnet nach dem Mittelwert der Ledger-Werte
(der Float-Mittelwert auf 9 Nachkommastellen gerundet, was nur Rechenrauschen
entfernt); bei gleichem Mittelwert steht der frühere Tag zuerst. Der Überblick
rundet jede Verspätung kaufmännisch auf eine Nachkommastelle. „Kennzahlen auf
einen Blick“ und die Balkendiagramme weiter unten (Verspätungen ⌀ Minuten je
Wochentag und je Stunde) behalten das Float-Format des Dashboards, wie
`docs/statistik.md` und das README; die Tabellen der Abschnitte Stammstrecke,
Ausfälle und Störungen enthalten nur Anzahlen. Ein Mittel genau auf einer
Hälfte kann daher im Überblick 3.3 min und in den Kennzahlen 3.2 min zeigen;
das sagt der letzte Satz des ersten Absatzes.

**Baustellen-Ledger** (`data/stats/baustellen_<Jahr>.csv`,
`src/utils/stats_baustellen.py`). Geschrieben mit dem Baustellen-Cache
(`scripts/update_baustellen_cache.py`), nicht im Feed-Bau, der eine Baustelle
neben einer WL-Meldung weglässt: je guid und Wiener Jahr eine Zeile für jede
Baustelle, die „Nur mit Öffi-Folgen“ besteht (erste Sichtung, Titel, Beginn,
Ende, Bezirk, Maßnahme, Text). Die guid ist ein Hash aus der Kennung `OGD_ID`
der Stadt (fehlt sie, aus dem Titel), Beginn und Ende; ein verschobenes Ende
gibt eine neue Zeile. Eine Baustelle, die vor dem 01.01. des Schreibjahrs
endete, bekommt keine Zeile. Ohne Text der Stadt, auch beim Platzhalter
„Baustelle ohne weitere Angaben“ des Providers, bleibt die Beschreibung leer.
Schreiber und Leser prüfen jede Zeile mit derselben Funktion (`site_line`).
Bytes, die kein UTF-8 sind, werden als U+FFFD gelesen. Eine Zeile, die
trotzdem ein ganzer Datensatz ist, bleibt mit diesem Zeichen auf der Seite und
gilt dem Schreiber als bekannt. Nur eine Zeile, die kein Datensatz ist, wird
übersprungen, in „N unlesbare Zeile(n) übersprungen“ mitgezählt und vom
Schreiber beim nächsten Lauf neu eingetragen, solange die Stadt die Baustelle
liefert. `baustellen_2026.csv` wurde am 10.11.2026 aus der Historie des Caches
nachgetragen: 61 Zeilen, 19 Baustellen, erste Zeile 24.05.2026.

**Abschnitt Baustellen** (`src/markdown_stats/baustellen_year.py`). Anzahl und
Liste kommen immer aus dem Ledger. Der Leser lässt jede Zeile weg, deren
Zeitraum ganz außerhalb des Jahres liegt. Zeilen eines Titels werden in der
Reihenfolge ihrer ersten Sichtung zu Baustellen zusammengefasst. Eine Zeile
gehört zu einer früheren Baustelle desselben Titels, wenn sich ihre Zeiträume
überschneiden oder berühren (kein freier Tag dazwischen). Sie gehört auch
dazu, wenn sie spätestens am letzten Tag des früheren Zeitraums (Wiener Datum)
zum ersten Mal gesehen wurde: Die Stadt hat den Zeitraum dann ersetzt, etwa
verschoben. Nur eine Zeile, die erst nach dem Ende des früheren Zeitraums
auftaucht und deren Zeitraum daran weder anschließt noch ihn überschneidet,
ist eine neue Baustelle. Jede Baustelle zeigt den Zeitraum ihrer neuesten
Zeile mit Beginn oder Ende; ein ersetzter Zeitraum wird weder gezeigt noch
verglichen. Ein verkürzter Zeitraum zieht so eine spätere Arbeit desselben
Titels nicht mehr in die erste Baustelle, und ein offener Zeitraum, den die
Stadt vor seinem Beginn durch einen ganz davor liegenden ersetzt, ist eine
Baustelle mit dem neuen Zeitraum. Grenze: Eine Zeile ohne Ende oder ohne Daten
reicht bis zu jeder späteren Zeile ihres Titels; eine zweite Arbeit, die die
Stadt unter demselben Titel ankündigt, während die erste noch läuft, zählt als
neuer Zeitraum der ersten. Die Seite sagt: „Gezählt wird nach dem Namen, unter
dem die Stadt Wien eine Baustelle führt. Ändert die Stadt den Zeitraum, bevor
der alte zu Ende ist, gilt der neue; trägt sie denselben Namen erst nach dem
Ende des geltenden Zeitraums für einen Zeitraum ein, der an diesen weder
anschließt noch ihn überschneidet, zählt er noch einmal. Trägt sie eine
Baustelle unter einem anderen Namen neu ein, zählt sie unter jedem Namen
einmal.“ 2026: Universitätsring/Schottenring; nichts wird zusammengeführt, der
Ledger wird nicht von Hand geändert.

**Rückblick in Worten** (`src/markdown_stats/year_review.py`,
`src/markdown_stats/review_client.py`). Der Prompt (Regeln, ein vom Skript
gezählter Faktenblock, die Baustellen als Daten zwischen `<<<DATEN` und
`DATEN>>>`) hat mit dem Ledger 2026 vom 11.11.2026 21.642 Zeichen. Eine Regel
sagt: Die Zahlen im Faktenblock zählen die erfassten Baustellen; wo er
„Erfasst seit“ sagt, soll der Text „erfasst“ sagen. Die Anfrage ist die des
KI-Wächters mit dessen Modellkette, aber ein Durchgang ohne Warten, je Anfrage
höchstens 90 s Lese-Timeout (Warten auf Daten, nicht die ganze Antwort); ein
abgelehnter Schlüssel (401, 403, 400 mit `API_KEY_INVALID`) beendet ihn.
`clean_review` aus `review_client.py` macht die Antwort zu einem Absatz ohne
Steuerzeichen, ersetzt Zugangsdaten durch „[entfernt]“ und behält höchstens
4.000 Zeichen; `year_review.py` und die Probe importieren es. Ein angenommener
Text wird in `data/stats/baustellen_rueckblick_<Jahr>.json` gespeichert und
steht mit dem Hinweis auf Gemini und die Prüfung auf der Seite. Jeder
Dashboard-Lauf prüft ihn gegen den Ledger von heute; besteht er nicht mehr,
wird er verworfen (WARNING mit den Gründen, einmal je Lauf), und das Jahr wird
wieder gefragt, ebenso nach Löschen der Datei.

**Prüfung** (`check_review`). (a) 200 bis 1.500 Zeichen, 3 bis 8 Sätze; (b)
keine Zeichen, die als Markdown, HTML oder Liquid wirken, kein
Aufzählungszeichen am Anfang, kein Link; (c) jede Zeichenfolge mit Ziffer
steht genau so in den Daten oder im Faktenblock; (d) jedes großgeschriebene
Wort mit Straßenendung (-straße, -gasse, -platz, -ring, -weg …) steht in den
Daten, mit einem Wort auf „-er“ davor als Name aus zwei Wörtern. Ein
gewöhnliches Wort mit Straßenendung besteht auch am Satzanfang, etwa Radweg,
Gehweg, Parkplatz, Einbahnstraße, Gelände, hinweg, durchweg, vorweg, weg,
gering (feste Liste `_ORDINARY_WORDS`); Ring, Gürtel und Kai allein müssen in
den Daten stehen. Grenze: Ein Wort auf „-er“ vor einem solchen Wort, etwa
„Neuer Radweg“ am Satzanfang, gilt als Straßenname aus zwei Wörtern. (e) Ein
Bezirk ist der einer Baustelle oder steht in den Daten; (f) ein Monat wird vom
Zeitraum einer Baustelle berührt; (g) keine Ich-Form, kein „wir“, keine
Anrede, kein Rat mit „Sie sollten“ oder „bitte“; (h) eine Zahl in Ziffern
direkt vor „Baustelle(n)“ muss eine Anzahl aus dem Faktenblock sein
(`fact_counts`). Das Jahr der Seite und jedes Jahr, das in den Daten steht,
ist eine Zeit, keine Anzahl; ein Jahr in den Daten ist eine vierstellige Zahl
von 2000 bis 2100 oder das Jahr eines vollen Datums wie 31.08.2027. So besteht
„Es gab 2026 Baustellen in 13 Bezirken“. (i) Kein Wort einer geschlossenen
Liste wertender Wörter (leider, ärgerlich, katastrophal, Zumutung …); (j)
mindestens 15 % der Wörter sind deutsche Funktionswörter, genau 15 % besteht,
sonst „Anteil deutscher Funktionswörter unter 15 %: <n> von <m> Wörtern“ (der
gemessene Anteil, nicht die Sprache). Die Prüfung belegt, dass etwas in den
Daten steht, nicht, dass es zusammengehört; deshalb nennt die Seite Gemini als
Verfasser und nimmt Anzahl und Liste aus dem Ledger.

**Wann gefragt wird.** Nur im Dashboard-Lauf des Statistik-Schritts von
`update-cycle.yml` (00:00 bis 00:29 Wiener Zeit), ab dem 01.01. in jedem
dieser Läufe, bis ein Rückblick angenommen und gespeichert ist. Erst schreibt
der Lauf Jahresseiten, Summary, Dashboard und README, zuletzt fragt er und
schreibt geänderte Seiten neu; nichts wartet auf Gemini. Je Jahr mit Seite und
Baustellen ohne angenommenen Rückblick geht eine Anfrage über die Modellkette,
das neueste Jahr zuerst. Alle Anfragen eines Laufs beginnen innerhalb eines
Budgets von 110 s (`ReviewBudget` in `src/markdown_stats/year_archive.py`);
ein Jahr, das deshalb keine bekommt, schreibt „… die Zeit für Anfragen ist
aufgebraucht; ein späterer Dashboard-Lauf fragt erneut.“ Die harte Schranke
ist die Grenze des Schritts von 4 Minuten. Scheitert eine Jahresseite („Konnte
Jahresarchiv nicht schreiben (<Klasse>): …“), endet der Lauf mit 1, der Rest
wird geschrieben. Scheitert das Schreiben der Summary, des Dashboards oder des
README, endet der Lauf sofort mit 1, ohne Anfrage; wartet ein Jahr auf seinen
Rückblick, endet die ERROR-Zeile mit „ — der Lauf endet hier, ohne Anfrage für
den fehlenden Rückblick der Baustellen <Jahr>“ (bei mehreren „die fehlenden
Rückblicke der Baustellen <Jahr>, <Jahr>“) und „; ein späterer Dashboard-Lauf
fragt erneut.“ Einmal je Lauf stehen die Warnung über Zeilen eines anderen
Jahres, die ERROR-Zeile einer Jahresseite, die Warnung über einen verworfenen
Rückblick und die Zeilen des Baustellen-Lesers (je Datei und Zahl); eine
andere Ledger-Warnung kann mehrfach stehen.

**Exit-Code.** `--fail-on-missing-review` geben nur die beiden Aufrufe im
Statistik-Schritt von `update-cycle.yml`. Ab dem 04.01. (`REVIEW_GRACE_DAYS`)
endet der Schritt damit in jedem Tick mit 1, solange ein abgeschlossenes Jahr
mit Seite und Baustellen keinen angenommenen Rückblick hat („Rückblick der
Baustellen fehlt für <Jahr>: …“, bei leerem Schlüssel mit „GEMINI_API_KEY ist
in diesem Lauf leer.“), und der Health check zeigt es unter „Nebenschritte“
(Teil 12), bis ein Dashboard-Lauf einen Rückblick speichert.
`manual-full-refresh.yml` schreibt ebenfalls die Summary und die Jahresseiten,
hat aber keinen `GEMINI_API_KEY` und kein Flag: Es fragt Gemini nie und endet
nie wegen eines fehlenden Rückblicks mit 1.

**Website und Summary.** `docs/stats-summary.json` trägt in jedem Tick
`archive_years`, die abgeschlossenen Jahre mit Seite, neueste zuerst;
`SUMMARY_SCHEMA_VERSION` bleibt 1. Daraus zeichnet `renderYearSwitch`
(`docs/assets/site-js/teil-05-statistik.js`) unter „Aktuelle Meldungen“ die Zeile
„Statistik nach Jahr“: das laufende Jahr markiert (Sprung zu `#stoerungen`), dann
je Jahr ein Link auf `statistik-<Jahr>.html`; ohne abgeschlossenes Jahr bleibt
sie verborgen. Sie zeigt höchstens 30 abgeschlossene Jahre; eine längere Liste
endet in einem Chip „Ältere Jahre“ (englisch „Earlier years“), der zu
`statistik.html` führt, wo die Zeile „Frühere Jahre“ jedes Jahr nennt.

**Aufwand je Tick** (Median, 11.11.2026, Ledger von 2026). Mit dem Flag prüft
jeder Tick, ob ein Rückblick überfällig ist: vor dem 04.01. ohne zu lesen,
danach liest er je abgeschlossenes Jahr Baustellen-Ledger und gespeicherten
Rückblick (rund 8 ms), ohne Rückblick auch die Ledger des Jahres (zusammen
48 ms). Der Dashboard-Lauf rendert jede Jahresseite und vergleicht sie (80 bis
90 ms je Jahr). Solange eine Richtung der Stammstrecke seit vor den geladenen
Jahresdateien still ist, liest jeder Tick das Verspätungs-Ledger des Vorjahres
erneut, rund 30 ms für die Datei von 2026. Eine Zustandsdatei gibt es nicht;
das ist das Ergebnis der Prüfung, keine Betreiberentscheidung.

**Gemessen** am 11.11.2026 am Endstand nach beiden Korrekturrunden gegen main.
Nachspiel echter Ticks: alle 88 Dashboard-Läufe vom 16.08. bis 11.11.2026 und
alle 1.465 übrigen Ticks vom 11.10. bis 11.11.2026 (1.553 Ticks), README und
`docs/statistik.md` byte-gleich, `docs/stats-summary.json` nur mit
`"archive_years": []` mehr. Jahreswechsel im Zeitraffer (echte Ticks vom 04.11.
bis 07.11. auf 30.12.2026 bis 02.01.2027 verschoben, künstliche bis 05.01.2027,
mit `--fail-on-missing-review`) in fünf Läufen (ohne Schlüssel; angenommene
Antwort einer Attrappe, auch die echte vom 10.11.; verworfene; erst am 05.01.
angenommene): Exit-Codes und Anfragen wie vorgesehen, rc 1 ab 04.01. nur ohne
angenommenen Rückblick. Echte Probe am 10.11.2026 um 23:19: gemini-3.5-flash-lite
antwortete mit 634 Zeichen, von der Prüfung angenommen. Tests:
`tests/test_year_*.py`, `test_review_client.py`, `test_baustellen_ledger*.py`,
`test_baustellen_year*.py`, `test_baustellen_rules.py`,
`test_jahresrueckblick_probe.py`, `test_site_year_switch.py`.

Weiter in [Teil 16](architecture-teil-16.md).
