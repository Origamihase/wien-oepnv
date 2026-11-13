# `data/stats/`

Append-only-CSV-Ledger, die von
[`scripts/generate_markdown_stats.py`](../../scripts/generate_markdown_stats.py)
ausgewertet werden.

| Datei | Producer | Schema |
| --- | --- | --- |
| `stammstrecke_YYYY.csv` | [`scripts/update_stammstrecke_hbf.py`](../../scripts/update_stammstrecke_hbf.py) — eine aggregierte Zeile pro Richtung und Cron-Tick (mittlere Verspätung aller in diesem Tick am Wien Hauptbahnhof beobachteten Abfahrten auf der Stammstrecke: S-Bahn, R, REX und CJX) | `timestamp, weekday, hour, direction, delay_minutes` |
| `ausfaelle_YYYY.csv` | [`scripts/update_stammstrecke_hbf.py`](../../scripts/update_stammstrecke_hbf.py) — eine Zeile pro ausgefallenem Zug der Stammstrecke (S-Bahn, R, REX oder CJX; dedupliziert via Pending-Trip-Identity-Ledger, damit derselbe Zug nicht über mehrere Cron-Ticks hinweg doppelt gezählt wird, und je Abfahrt: ein Eintrag mit Zugnummer zur Minute eines Linieneintrags derselben Richtung ist derselbe Zug) | `timestamp, weekday, hour, direction, line` |
| `stoerungen_YYYY.csv` | [`src/build_feed.py:_update_item_state`](../../src/build_feed.py) — hängt eine Zeile an, sobald ein Auftreten einer Meldung zum ersten Mal im Feed steht: eine neue Identität oder eine wiederkehrende WL-Meldung, die `_restart_recurring_occurrences` als neues Auftreten erkannt hat | `timestamp, weekday, hour, provider, location_name` |
| `baustellen_YYYY.csv` | [`scripts/update_baustellen_cache.py`](../../scripts/update_baustellen_cache.py) über `append_baustellen_rows` in `src/utils/stats_baustellen.py` — eine Zeile je Baustelle (guid) und Wiener Jahr, geschrieben beim Schreiben des Baustellen-Caches für jede Baustelle, die die Regel „Nur mit Öffi-Folgen“ besteht (`is_transit_relevant` in `src/providers/baustellen.py`); Abschnitt „Baustellen“ unten | `timestamp, weekday, hour, guid, title, starts_at, ends_at, district, measure, description` |

`line` ist der Linienname von VAO. Nannte VAO nur die Zugnummer (vier oder mehr Ziffern, etwa „S28624“), zählt der Zug in Dashboard, README und `stats-summary.json` unter „ohne Linienangabe“ (`ausfall_line_label` in `scripts/generate_markdown_stats.py`). Vom 19.05. bis 07.07.2026 führte die VAO-Abfahrtstafel viele Züge doppelt, unter der Linie („S2“) und unter der Zugnummer, zur selben Minute in dieselbe Richtung. Weil die Identität den Namen enthält, zählten beide; seit 26.10.2026 steht der Eintrag mit der Linie für den Zug, und der Zwilling mit der Zugnummer fällt weg (`_without_train_number_twins` in `scripts/update_stammstrecke_hbf.py`). Neun Ausfallzeilen dieser Zeit waren solche Zwillinge und sind gestrichen: sechs Züge standen doppelt im Ledger (18.06. 11:32 S3, 30.06. 23:39 REX3, 04.07. 20:32 S2, 05.07. 16:27 S2, 07.07. 14:03 und 17:33 CJX9), drei fuhren laut ihrem Linieneintrag (27.05. 14:57 S4, 05.07. 08:23 CJX9, 06.07. 05:57 S2). Zwei Zugnummern ohne Zwilling tragen die Linie laut Fahrplan (die Linie, die an den übrigen Tagen zur selben Minute in dieselbe Richtung ab Wien Hbf fuhr): S27016 → S2 (04.07. 00:45), CJX2904 → CJX9 (04.07. 07:03).

Eine WL-guid trägt kein Datum: „U6: Rettungseinsatz“ am 25.10. hat die guid
des Einsatzes vom 23.10. Seit 25.09.2026 stellt der Feed eine solche Meldung
als neues Auftreten nach vorn, `stoerungen_2026.csv` zählte sie aber nicht,
weil die guid schon einen Eintrag hatte. Vom 25.09. bis 27.10.2026 erreichten
so 1.060 Auftreten den Feed ohne Zeile; die alte Regel zählte im selben
Nachbau 348. Diese 1.060
Zeilen sind nachgetragen (Entscheidung Michael 27.10.2026 22:04 MEZ: „Nachtragen
aus der Historie“): Nachbau aller 1.690 Feed-Läufe mit ihrem echten Cache, der
Zustand jeweils aus dem Lauf davor, einmal mit alter und einmal mit neuer
Zählregel; jede Zeile trägt die Bauzeit des Laufs, in dem das Auftreten zum
ersten Mal im Feed stand, und den Ort, den `extract_location_name` dort
liefert. Feed und Zustand waren in allen Läufen gleich, nur die Zeilen kamen
hinzu. Zwischen dem Nachtrag und dem Merge der neuen Regel (28.10.2026 02:30
bis 09:00 MEZ) liefen noch 14 Läufe mit der alten; ihr Nachbau auf dieselbe
Weise ergab vier weitere Auftreten ohne Zeile (05:30 bis 06:30 MEZ), die
ebenfalls nachgetragen sind.

In `stammstrecke_2026.csv` fehlt die Tickzeile vom 08.06.2026 20:30 Richtung
Praterstern (395,33 Minuten): VAO gab drei Zügen eine Echtzeit am Folgetag
(1160 bis 1199 Minuten). Seit 27.10.2026 gilt eine Echtzeit zwölf Stunden oder
mehr neben dem Plan als keine Echtzeit (`IMPLAUSIBLE_DELAY` in
`scripts/update_stammstrecke_hbf.py`); die Lesungen der übrigen Züge dieses
Ticks lassen sich nicht wiederherstellen.

Alle Zeitstempel sind ISO-8601 mit Offset, verankert auf `Europe/Vienna`.
Eine Datei pro Kalenderjahr. Die Dateien werden im Append-Modus geöffnet;
die Rotation an der Jahresgrenze übernehmen die Writer in
[`src/utils/stats.py`](../../src/utils/stats.py), für die Baustellen
[`src/utils/stats_baustellen.py`](../../src/utils/stats_baustellen.py) mit
demselben Anhängen.

Die Dateien werden vom Workflow
[`update-cycle.yml`](../../.github/workflows/update-cycle.yml)
(IFTTT-getriggert, ca. 30-Min-Cadence) automatisch zusammen mit dem
regenerierten Dashboard [`docs/statistik.md`](../../docs/statistik.md)
committet. Die Dashboard-Regeneration innerhalb des Workflows ist
auf den ersten Cycle-Tick nach Mitternacht `Europe/Vienna` gekoppelt;
die zugrundeliegenden CSV-Ledger werden bei jedem Cycle-Tick committet.

Die Dateien abgeschlossener Jahre bleiben liegen: Aus ihnen entsteht die
Archivseite `docs/statistik-<Jahr>.md`, und jeder Dashboard-Lauf gleicht sie
mit ihren Dateien ab. Wer eine Zeile eines vergangenen Jahres berichtigt oder
nachträgt, muss die Archivseite deshalb nicht anfassen; der nächste
Dashboard-Lauf schreibt sie neu. Eine Zeile in `stammstrecke_`, `ausfaelle_`
oder `stoerungen_<Jahr>.csv`, deren Zeitstempel in einem anderen Wiener Jahr
liegt, zählt auf keiner Zahl dieser Seite; das Protokoll des Laufs nennt sie
einmal je Lauf („Jahresarchiv <Jahr>: <n> Zeilen mit dem Zeitstempel eines
anderen Jahres nicht gezählt.“, bei einer Zeile „1 Zeile“). Ein Jahr, das nur
eine `baustellen_<Jahr>.csv` hat, bekommt keine Seite.

## Baustellen (`baustellen_<Jahr>.csv`)

Betreiberentscheidung vom 10.11.2026 (17:44): Die Seite eines abgeschlossenen
Jahres fasst die Baustellen des Jahres zusammen. Bis dahin hielt keine Datei
des aktuellen Stands Titel oder Text einer vergangenen Baustelle fest, nur die
Git-Historie des Caches und ihre Zahl in `stoerungen_<Jahr>.csv`. Geschrieben
wird beim Schreiben des Baustellen-Caches, nicht beim Bauen des Feeds: Seit 09.10.2026 fällt eine Baustelle aus dem Feed,
wenn eine laufende WL-Meldung dieselbe Maßnahme meldet (Zwillingsregel), und
der Feed hätte kaum eine verzeichnet. Nur die Daten der Stadt zählen: Das
mitgelieferte Demo-Beispiel (Exit-Code 2) schreibt keine Zeile, ein Layer aus
seiner letzten guten Antwort (Exit-Code 3) hält echte Baustellen und wird wie
jeder andere eingetragen, jede guid einmal. Ein Fehler beim Schreiben ist eine
WARNING im Protokoll des Baustellen-Schritts; Cache und Exit-Code bleiben, der
nächste Lauf versucht es wieder.

- **Eine Zeile je guid und Jahr.** Die Datei ist die des Wiener Jahres, in dem
  geschrieben wird, `timestamp`, `weekday` und `hour` sind der Moment der
  ersten Sichtung in diesem Jahr (Wiener Zeit wie in den anderen Ledgern). Eine
  Baustelle, die nach Neujahr noch im Cache steht, bekommt auch im neuen Jahr
  eine Zeile. Eine Baustelle, deren letzter Tag vor dem 1. Jänner des
  Schreibjahres liegt, bekommt in dessen Datei keine, auch wenn die Stadt sie
  noch liefert (gesehen: eine Baustelle bis 04.09.2026 stand in der Antwort
  vom 05.09.2026, 00:01).
- **Der Text der ersten Sichtung.** Eine Zeile hält den Text, wie er stand,
  als ihre guid im Jahr zum ersten Mal gesehen wurde; ändert die Stadt den
  Text später unter derselben guid, kommt keine neue Zeile. Die guid ist ein
  Hash aus der Kennung `OGD_ID` der Stadt (fehlt sie, aus dem Titel), Beginn
  und Ende (`src/providers/baustellen_events.py`): Ein verschobener Beginn
  oder ein verschobenes Ende gibt eine neue guid und eine neue Zeile mit dem
  Text dieses Tages. Die 61 Zeilen für 2026 (19 Baustellen)
  sind am 10.11.2026 aus der Historie des Caches nachgetragen; erste Zeile
  24.05.2026, der erste Commit des Baustellen-Caches. Jede trägt den letzten
  Text, den der Cache für ihre guid hielt, unter der Zeit des ersten
  Cache-Commits dieser guid.
- **Spalten.** `starts_at` und `ends_at` sind Wiener Kalendertage
  (`JJJJ-MM-TT`) oder leer. `title`, `district`, `measure` und `guid` haben
  höchstens 200 Zeichen; ein längerer Wert endet nach 199 Zeichen mit „…“.
  `description` ist der Text der Stadt auf einer Zeile, ohne die Zeilen, die
  der Provider aus den eigenen Feldern anhängt (Beginn, Geplant bis, Maßnahme,
  Bezirk), auch wenn Maßnahme oder Bezirk einen Zeilenumbruch enthalten;
  höchstens 2.000 Zeichen, die ganze Zeile unter 4.000 Bytes (ein einzelner
  `write()` unter `PIPE_BUF` ist gegen einen zweiten Schreiber atomar). Eine
  Baustelle ohne Text der Stadt hat eine leere Beschreibung, auch wenn der
  Provider nur seinen Platzhalter „Baustelle ohne weitere Angaben“ schreibt
  (`NO_DETAILS_TEXT` in `src/utils/stats_baustellen.py`, aus
  `_format_description` in `src/providers/baustellen_events.py` gelesen). Ein
  Wert, der mit `=`, `+`, `-` oder `@` beginnt, bekommt ein `'` davor
  (Formelschutz wie in den anderen Ledgern). Die Felder der Ansprechpersonen
  im WFS-Layer der Stadt gehören nicht zum Cache-Eintrag und stehen nie hier.

So zählt die Jahresseite (`load_sites` in `src/markdown_stats/baustellen_sites.py`):

- Eine Zeile je guid, die früheste; eine doppelte Zeile (Union-Merge) zählt
  einmal. Eine Zeile, deren Zeitraum ganz außerhalb des Jahres liegt (Ende vor
  dem 1. Jänner oder Beginn nach dem 31. Dezember), fällt weg, je Zeile und vor
  dem Zusammenfassen; die Zahl steht in einer INFO-Zeile des Statistik-Laufs.
  Zeilen ohne Datum bleiben.
- Zeilen eines Titels (Groß- und Kleinschreibung, Leerraum und ein „…“ am Ende
  zählen nicht) werden in der Reihenfolge ihrer ersten Sichtung zu Baustellen
  zusammengefasst. Eine Zeile gehört zu einer früheren Baustelle desselben
  Titels, wenn sich ihre Zeiträume überschneiden oder berühren (kein freier Tag
  dazwischen). Sie gehört auch dazu, wenn sie spätestens am letzten Tag des
  früheren Zeitraums (Wiener Datum) zum ersten Mal gesehen wurde: Die Stadt hat
  den Zeitraum dann ersetzt, etwa verschoben. Nur eine Zeile, die erst nach dem
  Ende des früheren Zeitraums auftaucht und deren Zeitraum daran weder
  anschließt noch ihn überschneidet, ist eine neue Baustelle. Verglichen wird
  mit dem geltenden Zeitraum: Ein Zeitraum, den die Stadt verkürzt hat, zieht
  eine spätere Maßnahme desselben Titels nicht mehr in die erste Baustelle
  (behoben nach der Prüfung vom 10.11.2026). Ein offenes Ende, das die Stadt vor
  seinem Beginn durch einen Zeitraum ganz davor ersetzt, ist eine Baustelle mit
  dem neuen Zeitraum.
- Jede Baustelle zeigt den Zeitraum ihrer neuesten Zeile, die einen Beginn oder
  ein Ende nennt (beide Daten aus dieser Zeile); ein dort offenes Ende bleibt
  offen. Titel, Bezirk und Maßnahme kommen aus der neuesten Zeile, bis zu drei
  verschiedene Texte bleiben, der neueste zuerst.
- Bekannte Grenze: Eine Zeile ohne Ende oder ohne Daten reicht bis zu jeder
  späteren Zeile ihres Titels. Kündigt die Stadt unter demselben Titel eine
  zweite Maßnahme an, während die erste noch läuft, zählt sie als neuer
  Zeitraum der ersten.
- Gezählt wird nach dem Namen der Stadt. Die Seite sagt es so: „Gezählt wird
  nach dem Namen, unter dem die Stadt Wien eine Baustelle führt. Ändert die
  Stadt den Zeitraum, bevor der alte zu Ende ist, gilt der neue; trägt sie
  denselben Namen erst nach dem Ende des geltenden Zeitraums für einen
  Zeitraum ein, der an diesen weder anschließt noch ihn überschneidet, zählt
  er noch einmal. Trägt sie eine Baustelle unter einem anderen Namen neu ein,
  zählt sie unter jedem Namen einmal.“ Ein zurückgezogener Zeitraum steht
  weder in der Liste noch in einem Vergleich. Beispiel 2026: Die Stadt führt
  Arbeiten zwischen Rathausplatz und Heßgasse unter zwei Titeln
  („Universitätsring und Schottenring von Rathausplatz bis Heßgasse“ und
  „Schottenring von Kreuzung Universitätsring … bis Kreuzung Heßgas…“); sie
  stehen zweimal auf der Seite. Zusammengeführt wird nichts, und das Ledger
  wird dafür nicht von Hand geändert.
- Nur die Seite für 2026 sagt, wann die Aufzeichnung begann („Die Aufzeichnung
  für 2026 beginnt am 24.05.2026.“, `FIRST_LEDGER_YEAR`); jedes spätere Jahr
  wird ab 1. Jänner aufgezeichnet.

## Rückblick der Baustellen (`baustellen_rueckblick_<Jahr>.json`)

Der Rückblick in Worten auf der Seite eines abgeschlossenen Jahres
(Betreiberentscheidung vom 10.11.2026: Gemini fasst die Baustellen des Jahres
zusammen; seine ausdrückliche Ausnahme von „KI beobachtet nur, schreibt nie
um“, die für den Feed bleibt). Die Datei schreibt `ensure_review` in
`src/markdown_stats/year_review.py`, und nur dann, wenn der Text von Gemini
die feste Prüfung `check_review` in `src/markdown_stats/year_review_guard.py` besteht: ein JSON-Objekt mit
`year`, `created_at` (Wiener Zeit), `model`, `text` und `sites` (Zahl der
Baustellen beim Speichern). Anzahl und Liste der Seite kommen immer aus
`baustellen_<Jahr>.csv`, nie aus dem Text; der Text steht unter „### Rückblick“
mit dem Hinweis, dass Gemini ihn geschrieben und ein Skript ihn geprüft hat.

- **Wann gefragt wird.** Nicht einmal im Jahr: Der Statistik-Schritt von
  `update-cycle.yml` fragt in seinen Dashboard-Läufen (00:00 bis 00:29 Wiener
  Zeit) ab 1. Jänner, bis ein Rückblick angenommen und gespeichert ist, für
  jedes abgeschlossene Jahr mit Seite und Baustellen ohne angenommenen Text
  einmal über die Modellkette, das neueste Jahr zuerst. Alle Anfragen eines
  Laufs beginnen innerhalb eines Budgets von 110 Sekunden (`ReviewBudget` in
  `src/markdown_stats/year_archive.py`); ein Jahr, das wegen des aufgebrauchten
  Budgets keine Anfrage bekommt, schreibt „… die Zeit für Anfragen ist
  aufgebraucht; ein späterer Dashboard-Lauf fragt erneut.“ 429, jeder 5xx,
  ein ausgemusterter Modellname (404 oder 400) und eine abgebrochene
  Verbindung geben an das nächste Modell weiter; ein abgelehnter Schlüssel
  (401, 403 oder 400 mit `API_KEY_INVALID`) beendet den Durchgang. Schlüssel,
  Modellkette und Kontingent teilt der Rückblick mit dem KI-Wächter
  ([`docs/reference/ki_waechter-teil-2.md`](../../docs/reference/ki_waechter-teil-2.md),
  „Jahresrückblick der Baustellen“); Statistik-Schritt, Exit-Codes und
  Probelauf: [`docs/development-teil-4.md`](../../docs/development-teil-4.md).
- **Der Prompt** (`build_prompt` in `src/markdown_stats/year_review_prompt.py`) gibt
  dem Modell die Regeln (4 bis 6 sachliche Sätze in einem Absatz, nur was in
  den Daten oder im Faktenblock steht, Antwort als `{"rueckblick": "…"}`),
  einen Faktenblock, den das Skript selbst zählt, und je Baustelle Ort,
  Zeitraum, Bezirk, Maßnahme und den neuesten Text zwischen `<<<DATEN` und
  `DATEN>>>`. Der Faktenblock nennt Jahr, Gesamtzahl, Beginn je Monat, vor,
  nach oder ohne Beginn, Baustellen je Bezirk und je Maßnahme; nur für 2026
  steht dort „Erfasst seit: 24.05.2026 (Baustellen, die davor endeten, fehlen
  in den Zahlen)“. Eine Maßnahme wird dort nur genannt, wenn sie aus Wörtern,
  Leerzeichen und Bindestrichen besteht und höchstens 60 Zeichen hat, sonst
  „andere Angabe“, leer „ohne Angabe“. Eine Regel sagt: Die Zahlen im
  Faktenblock zählen die erfassten Baustellen; steht dort „Erfasst seit“, soll
  der Text „erfasst“ schreiben. Mit dem Ledger 2026 vom 11.11.2026 hat der
  Prompt 21.642 Zeichen (Grenze 200.000; darüber werden die Texte gleichmäßig
  gekürzt, und der Faktenblock sagt es).
- **Die Antwort** macht `clean_review` in `src/markdown_stats/review_client.py`
  zu einem Absatz: ohne Steuerzeichen, was wie ein Zugangsschlüssel aussieht
  als „[entfernt]“ (die Klammern fallen dann durch die Prüfung), höchstens
  4.000 Zeichen behalten (mehr, als die Prüfung annimmt, damit eine zu lange
  Antwort an ihrer Länge scheitert und nicht passend gekürzt wird).
  `year_review.py` und der Probelauf nehmen sie von dort. Ein verworfener Text
  steht mit den Gründen und höchstens 400 Zeichen im Protokoll.

Die Prüfung (`check_review` in `src/markdown_stats/year_review_guard.py`) nimmt einen
Text nur an, wenn er alle zehn Regeln erfüllt:

- a: 200 bis 1.500 Zeichen und 3 bis 8 Sätze.
- b: keine Formatierungszeichen (`< > [ ] { } | * _ # \ $` und der Backtick),
  kein Aufzählungszeichen am Anfang („- “, „+ “, „1. “, „1) “), kein Link.
- c: jede Zeichenfolge mit einer Ziffer (Zahl, Datum, Jahr, Linie wie 13A
  oder U4, Hausnummer) steht genau so in den Daten oder im Faktenblock; ein
  volles Datum zählt auch mit seiner Kurzform und seinem Jahr („bis 2027“ für
  eine Baustelle bis 31.08.2027).
- d: jeder Straßenname steht in den Daten. Als Straßenname gilt nur ein
  großgeschriebenes Wort mit einer Wiener Straßenendung (-straße, -gasse,
  -platz, -ring, -gürtel, -brücke, -kai, -weg, -allee, -markt, -steg, -zeile,
  -lände, -damm, -ufer). Ein gewöhnliches Wort einer festen Liste mit einer
  solchen Endung gilt allein nicht als Name, auch am Satzanfang: Straße,
  Gasse, Platz, Weg, Radweg, Gehweg, Einbahnstraße, Gelände, Parkplatz,
  hinweg, durchweg, vorweg, weg, rundweg, schlechtweg, glattweg, kurzweg,
  gering und einige mehr. „Ring“, „Gürtel“ und „Kai“ allein und jeder
  zweiteilige Name müssen in den Daten stehen. Grenze: Ein Wort auf „-er“ vor
  einem solchen Wort, etwa „Neuer Radweg“ am Satzanfang, wird als
  zweiteiliger Straßenname gelesen.
- e: ein genannter Bezirk ist der Bezirk einer Baustelle oder steht in den
  Daten (auch „Wien-Liesing“, „in der Inneren Stadt“).
- f: ein genannter Monat wird im Jahr vom Zeitraum einer Baustelle berührt.
- g: keine Ich-Form, kein „wir“, keine Anrede, kein „Sie sollten“, kein „bitte“.
- h: eine Zahl in Ziffern direkt vor „Baustelle(n)“ muss eine Anzahl aus dem
  Faktenblock sein. Das Jahr der Seite und jedes Jahr, das in den Daten steht,
  ist eine Zeit, keine Anzahl; ein Jahr in den Daten ist eine vierstellige Zahl
  von 2000 bis 2100 oder das Jahr eines vollen Datums wie 31.08.2027. „Es gab
  2026 Baustellen in 13 Bezirken“ wird deshalb angenommen. Zahlwörter werden
  nicht geprüft.
- i: kein Wort einer festen Liste wertender Wörter (leider, bedauerlich,
  erfreulich, glücklicherweise, ärgerlich, vorbildlich, katastrophal, …chaos,
  Zumutung, Skandal, versagt …).
- j: mindestens 15 % der Wörter sind deutsche Funktionswörter; genau 15 %
  besteht. Der Grund im Protokoll und im Bericht des Probelaufs lautet
  „Anteil deutscher Funktionswörter unter 15 %: <n> von <m> Wörtern“. Er nennt
  den gemessenen Anteil, nicht die Sprache.

Grenzen der Prüfung: Sie belegt, dass etwas in den Daten steht, nicht, dass
die Angaben zusammengehören. Eine Anzahl wird nur direkt vor „Baustelle(n)“
und nur gegen die Menge der gezählten Werte geprüft, eine Anzahl in Worten gar
nicht. Den Ton prüfen nur feste Listen: eine Beleidigung, eine Anweisung, ein
Rat in anderen Worten oder ein halb anderssprachiger Text gehen durch. Deshalb
sagt die Seite, dass Gemini den Rückblick geschrieben hat, und nimmt Anzahl und
Liste aus dem Ledger.

Jeder Lauf, der die Seite schreibt oder nach einem fehlenden Rückblick fragt,
prüft den gespeicherten Text gegen das aktuelle Ledger. Besteht er die Prüfung
nicht mehr (oder steht er nicht in der Form, in der er gespeichert wird), gilt
er nicht: Das Protokoll hat dann einmal je Lauf eine WARNING mit den Gründen,
die Seite zeigt „Ein kurzer Rückblick in Worten wird hier ergänzt.“, und der
Dashboard-Lauf fragt Gemini neu. Ein von Hand geänderter Text gilt nur, wenn
er die Prüfung besteht. Wer die Datei löscht, lässt den nächsten Dashboard-Lauf
neu fragen.

## Von Hand berichtigen

Für `baustellen_<Jahr>.csv` gilt zusätzlich:

- Schreiber und Leser des Baustellen-Ledgers prüfen jede Zeile mit derselben
  Funktion (`site_line` in `src/utils/stats_baustellen.py`): ein ganzer
  CSV-Datensatz mit den zehn Spalten, ein lesbarer Zeitstempel aus den Jahren
  2000 bis 2100, guid und Titel vorhanden. Bytes, die kein UTF-8 sind, werden
  als U+FFFD gelesen. Eine Zeile, die trotzdem ein ganzer Datensatz ist,
  bleibt mit diesem Zeichen auf der Seite und gilt dem Schreiber als bekannt.
  Nur eine Zeile, die kein Datensatz ist, wird übersprungen, in „N unlesbare
  Zeile(n) übersprungen“ mitgezählt und vom Schreiber beim nächsten Lauf neu
  eingetragen, solange die Stadt die Baustelle liefert.
- Ein Byte-Order-Mark und leere Zeilen vor der Kopfzeile (so speichert eine
  Tabellenkalkulation) nehmen Schreiber und Leser hin. Ist die erste Zeile mit
  Inhalt nicht die Kopfzeile, wird die Datei weder gelesen noch ergänzt
  (WARNING im Protokoll des Baustellen-Schritts, Exit-Code unverändert; der
  Statistik-Lauf meldet „unerwarteten Header“), bis jemand sie angesehen hat.
  Eine wiederholte Kopfzeile (zwei Zweige legten die Datei an) wird
  übersprungen und nicht gezählt.
- Fehlt der letzten Zeile der Zeilenumbruch, setzt ihn der Schreiber vor der
  nächsten Zeile. Der gemeinsame Schreiber der drei anderen Ledger bleibt, wie
  er ist, und hängt die nächste Zeile weiter an eine offene letzte Zeile an.
- Die Meldungen des Lesers („unlesbare Zeile(n)“, „unerwarteten Header“,
  „Zeile(n) mit Zeitraum außerhalb des Jahres“) stehen je Statistik-Lauf,
  Datei und Zahl einmal im Protokoll, auch wenn der Lauf das Ledger mehrmals
  liest. Ebenso steht „unerwarteten Header“ eines der drei anderen Ledger je
  Lauf einmal.
