# Feed-Wächter

Der Feed-Wächter (`scripts/feed_watch.py`, seit 2026-10-10) prüft nach jedem
Update-Lauf den fertigen **deutschen Feed** (`docs/feed.xml`) gegen die Regeln,
die seit September 2026 aus Audits und Entscheidungen des Betreibers entstanden
sind, und vergleicht, was die Quellen geliefert haben, mit den letzten sieben
Tagen. Er meldet nur, wenn eine Regel verletzt ist.

## Warum

Die meisten Fehler des Feeds hat bisher erst ein Audit gefunden, Stunden bis
Wochen nachdem sie auf dem TV standen:

* Am 09.10.2026 übernahm eine Haltestellen-Anzeige der Linie 25 vom 07.10.
  („25: Ersatzbus 26E hält Karl-Waldbrunner-Platz …“) GUID und Beginn einer
  Verspätung von 09:36 und stand von 12:01 bis 20:30 als neue Störung im Feed,
  bis auf Platz 2 (behoben in #1993).
* Bei ÖBB fehlte seit September in 59 von 106 Texten der zweite Satz, der sagt,
  was noch gilt („Planen Sie derzeit noch bis zu 10 Minuten mehr Reisezeit
  ein.“), weil der erste Satz mit beiden Stationen in Langform den Platz nahm
  (behoben in #1993).
* „2/71: Polizeieinsatz [Seit 13:08]“ stand am 09.10. ab 20:30 für einen
  Einsatz ab 20:08: Umgeschriebene Anzeigetafeln behielten ihre alte Startzeit
  (PR #2001).

Ein Wächter fängt solche Fälle am selben Tag, auch neue, wenn Wiener Linien,
ÖBB oder die Stadt Wien ihre Daten so ändern, wie es kein Audit vorhersieht.

## Inhaltsfunde werden nie rot

Entscheidung des Betreibers (Michael, 2026-10-10, für den Health check, hier
übernommen): „Der Check soll nur auf rot, wenn der Abruf nicht funktioniert.
Wenn die Technik funktioniert gehört er auch auf grün.“

Eine verletzte Feed-Regel ist ein Inhaltsfund: Abruf, Bau und Veröffentlichung
haben funktioniert. Darum

* ändert der Wächter den Feed nie und hält die Veröffentlichung nie auf: Er
  läuft vor dem Commit, damit sein Stand im selben Commit landet, endet aber
  immer mit Exit-Code 0, auch wenn er selbst scheitert (dann steht der Fehler
  im Feld `error` seines Stands); der Workflow-Schritt hat zusätzlich
  `continue-on-error` und ein Zeitlimit von zwei Minuten;
* zeigt der Health check seinen Stand nur als Hinweis (ℹ️, `::warning`), nie
  als Fehler (Prüfung `Feed-Wächter` in `scripts/health_check.py`);
* bleibt der Update-Lauf grün; Funde erscheinen als gelbe Annotation.

Wer später einen Wächter-Fund rot machen will, ändert diese Entscheidung und
muss sie mit dem Betreiber klären. Rot bleibt dem Health check vorbehalten,
wenn ein Abruf nicht funktioniert oder der Feed seit vier Stunden nicht neu
gebaut wurde.

## Meldeweg

| Wo | Was |
| --- | --- |
| Job-Protokoll des Update-Laufs | je Fund eine `::warning`-Annotation „Feed-Wächter: <Regel>“ und eine Tabelle in der Job-Zusammenfassung |
| `data/feed_watch.json` | Stand des letzten Laufs: Funde, seit wann jede Regel verletzt ist, Vergleichswerte der Quellen; landet im Commit des Laufs |
| Health check | Prüfung `Feed-Wächter`: „OK — alle Regeln halten“ oder „HINWEIS — n Regel(n) verletzt: …“ |
| GitHub-Issues (Label `feed-waechter`) | je verletzter Regel ein Issue „Feed-Wächter: <Regel>“, damit der Fund am selben Tag per Mail ankommt |

Issues: Der Wächter öffnet je Regel höchstens eines. Steht schon ein offenes
mit demselben Titel, nutzt er es; wurde eines vor weniger als 24 Stunden
geschlossen, öffnet er es wieder, statt ein neues anzulegen. Hält die Regel in
zwei Läufen in Folge, schließt er es mit einem Kommentar. Dafür hat der Job in
`update-cycle.yml` das Recht `issues: write` (nur dieser Schritt nutzt es;
`FEED_WATCH_ISSUES=1` schaltet die Issues ein). Fehlt das Recht oder
antwortet GitHub nicht, steht eine Warnung im Protokoll, und der nächste Lauf
versucht es wieder.

Entscheidung des Betreibers (Michael, 2026-10-11, 01:13 Wiener Zeit, Karte „Darf der
Update-Lauf für Wächter-Funde GitHub-Issues anlegen?“, Antwort „Issues
anlegen“): Der Update-Lauf bekommt `issues: write`. Grund: Ein Fund, den
niemand sieht, wiederholt den Fall vom 09.10., als der Ticker der Linie 25
acht Stunden auf Platz 2 stand. Ein Issue löst die Mail von GitHub am selben
Tag aus; Protokoll und Health check allein sieht nur, wer nachschaut. Das
Recht erlaubt nur Issues, keinen Code. Die Gegenoption „Nur Hinweis“ (kein
neues Recht) ist damit verworfen; wer die Issues abschalten will, entfernt
`FEED_WATCH_ISSUES` und `issues: write` aus `update-cycle.yml`.

## Die Regeln

Rang wie in `AGENTS.md`: Rang 1 verdrängt Meldungen, Rang 2 verfälscht, was
am TV steht.

| Regel | Rang | Verletzt, wenn | Grundlage |
| --- | --- | --- | --- |
| `doppelter-eintrag` | 1 | zwei Einträge dieselbe GUID oder denselben Titel haben | zehn Plätze am TV |
| `anzahl` | 1 | der Feed nicht genau `MAX_ITEMS` (10) Einträge hat | zehn Folien in EasySignage |
| `alte-meldung-als-neu` | 1 | ein Eintrag als heutige Störung erscheint („[Heute]“ oder „[Seit hh:mm]“), dessen Meldung (`pubDate`) und Beginn (`starts_at`) mehr als 24 Stunden vor seiner ersten Sichtung (`first_seen`) liegen | Linie 25, 09.10. (#1993) |
| `seit-zu-frueh` | 2 | eine Störung „[Seit hh:mm]“ zum ersten Mal erscheint, die Zeit mehr als drei Stunden vor dem Bau liegt, keine ihrer Linien im vorigen Bau (höchstens zwei Stunden alt) als Störung stand und der vorige Bau Platz hatte (einen Eintrag, der keine Störung „Seit hh:mm“ war); einmal gefunden, bleiben dieselben Linien an diesem Tag gemeldet, solange ihre „Seit“-Zeit höchstens eine Stunde nach der gefundenen liegt | „2/71 [Seit 13:08]“, 09.10. (PR #2001) |
| `oebb-zweiter-satz` | 2 | der zweite Satz des ÖBB-Texts (aus dem Cache, ohne Höflichkeitssätze) fehlt, obwohl er neben den ersten Satz passen würde, wenn dieser die Stationen weglässt, die der Titel nennt | #1993 |
| `zeitzeile` | 2 | die Beschreibung nicht mit einer Zeitzeile in einer der Formen von `format_local_times` endet (Heute, Seit heute, Seit hh:mm, Seit/Bis/Am/Ab <Tag>, Ab <Tag> bis <Tag>), Wörter mit U+00A0 verbunden | Entscheidungen 02./03.10., #1971 |
| `ende-vorbei` | 2 | die Zeitzeile ein Ende vor heute nennt, eine „Seit“-Zeit nach dem Bau, oder der Text einer heutigen Störung „Voraussichtlich bis <Uhrzeit>“ sagt, obwohl die Uhrzeit vorbei ist | #1975 |
| `textlaenge` | 2 | die Beschreibung ohne Zeitzeile länger als 180 Zeichen ist | Schnitt in EasySignage |
| `hoeflichkeitssatz` | 2 | „Wir bitten um Entschuldigung“ im Text steht | #1993 |
| `quelle-leer` | Quelle | der Cache eines Anbieters leer ist, obwohl er in den letzten sieben Tagen im Median mindestens fünf Meldungen hatte | — |
| `quelle-einbruch` | Quelle | eine Rohantwort (`data/raw/`) weniger als die Hälfte ihres Medians der letzten sieben Tagen hat (Median mindestens 10) | — |
| `verwurf-sprung` | Quelle | der Anteil der beim Abruf verworfenen Meldungen (`data/raw/*/verworfen.json`) um mehr als 25 Prozentpunkte von seinem Median der letzten sieben Tage abweicht | — |
| `feed-alter` | Betrieb | der Feed beim Lauf des Wächters älter als 90 Minuten ist | — |

Die Quellen-Regeln brauchen mindestens 24 Läufe der letzten sieben Tage als
Vergleich; nach dem ersten Lauf schweigen sie also rund zwölf Stunden.
`seit-zu-frueh` braucht den vorigen Bau und greift ab dem zweiten Lauf.

### Bewusst nicht geprüft

* **Frühe Enden aus dem Text** (44A, 93A, 96A, N91, 65A, 66A) sind kein Fehler:
  Eine WL-Meldung endet an dem Ende, das ihr Text nennt (#1914, #1990, #1991).
  Der Wächter prüft Enden nur auf „schon vorbei“.
* **Reihenfolge** der Plätze: Die Regeln dazu (#1952) hängen von Zuständen ab,
  die der Feed nicht trägt (geplant oder ungeplant, Wiederausgabe). Nur ihre
  sichtbare Folge, eine alte Meldung als neue Störung, ist eine Regel.
* **Baustellen-Regeln** (#1972, #1992), **Verwürfe einzelner Meldungen** und
  **Bezug zu Wien** bei ÖBB (#1959): Sie brauchen die Logik des Abrufs selbst;
  ein zweiter Nachbau im Wächter könnte nur denselben Fehler zweimal machen.
* Der **englische Feed** (Rang 3).
* Eine „Seit“-Zeit, die sich bei einer Linie, die schon als Störung im Feed
  stand, um Stunden nach vorne verschiebt: WL bündelt dann oft eine ältere,
  echte Meldung derselben Linie (11 am 08.10.: Rettungseinsatz seit 07:22
  neben einem eigenen ab 12:11). Das ließe sich am Feed allein nicht von der
  Restlücke aus PR #2001 („71 [Seit 13:10]“ ohne Störungsmeldung) unterscheiden.

## Rückprüfung (2026-10-10)

Jede Regel lief über alle 1 975 Stände von `docs/feed.xml` vom 01.09. bis
10.10.2026 (mit dem ÖBB-Cache desselben Commits, der Stand von Lauf zu Lauf
weitergegeben) und die Quellen-Regeln über 720 Stände der Caches und
Rohantworten vom 10.09. bis 10.10. (Rohantworten ab 04.10.). Zeiten in Wiener
Zeit.

| Regel | Laut | Danach still |
| --- | --- | --- |
| `doppelter-eintrag` | 14 Läufe: 03.09. („Wien Hauptbahnhof ↔ Wien Stadlau“ zweimal), 30.09. und 01.10. („D: Gleisbauarbeiten“ zweimal) | ja |
| `alte-meldung-als-neu` | 28 Läufe am 09.10. ab 12:01: „25: Ersatzbus 26E hält …“ | ja |
| `seit-zu-frueh` | 3 Läufe am 09.10. ab 20:30: „2/71: Polizeieinsatz [Seit 13:08]“, dann „2“ und „71“ | ja |
| `oebb-zweiter-satz` | 169 Läufe an 28 Tagen vom 02.09. bis 09.10. | seit #1993 (09.10. 22:00) |
| `zeitzeile` | jeder Lauf bis 06.10. (frühere Formen „01.09.2026 – 02.09.2026“, Abstand U+202F) | seit #1971 (06.10. 17:21) |
| `ende-vorbei` | 3 Läufe: 09.09. 00:00 („[Am 08.09.2026]“), 19.09. 00:02 | ja |
| `textlaenge` | 51 Läufe am 10. und 11.09. (Baustelle Märzstraße, 184 Zeichen) | ja |
| `anzahl`, `hoeflichkeitssatz`, `feed-alter` | nie | — |
| `quelle-leer`, `quelle-einbruch`, `verwurf-sprung` | nie (auch nicht beim Rückgang der Baustellen von 15 auf 2 am 06.10. durch #1972: Leer ist erst 0, und ein Cache mit weniger als fünf Meldungen zählt nicht) | — |

Am 07., 08. und 10.10. war keine Regel verletzt.

Dazu lief `oebb-zweiter-satz` über die mit dem heutigen Code gebauten Texte
aller 224 mehrsätzigen ÖBB-Meldungen im Cache seit Juni: ein Fund. „Wien
Meidling ↔ Wien Liesing“ („… zwischen Wien Meidling Bahnhof (U) und Liesing
(Wien) Zugfahrten voraussichtlich 23:00 Uhr nur eingeschränkt möglich.“):
Die Vorlage des Builders erkennt „voraussichtlich“ ohne „bis“ nicht, behält
die Stationen und lässt den zweiten Satz („Es kommt zu vereinzelten
Zugausfällen und Verspätungen bis zu 10 Minuten.“) weg. Eine echte Lücke,
nicht behoben; der Wächter meldet sie, wenn sie wieder vorkommt.

## Eine Regel ergänzen

1. Regel in `RULES` eintragen (Kennung, Titel, Rang, Begründung) und eine
   Prüffunktion schreiben, die nur den Feed, den ÖBB-Cache oder die
   Quellen-Zählung liest.
2. Vor dem Merge über die Feed-Historie zurückprüfen (alle Stände von
   `docs/feed.xml` aus `git log`, der Stand von Lauf zu Lauf weitergegeben):
   An guten Tagen still, an den Tagen des Fehlers laut. Hält eine Regel an
   einem guten Tag nicht, ist sie keine harte Regel.
3. Tests mit echten Feldformen in `tests/test_feed_watch.py`; Doku hier und
   im CHANGELOG.
