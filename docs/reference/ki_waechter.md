# KI-Wächter

Die tägliche Prüfung der Feed-Anzeige im Projekt selbst: Gemini liest jeden
Meldungstext, der in den letzten 24 Stunden im Feed stand, neben seinen
Quelltexten und meldet, was der TV-Zuschauer falsch gelesen hat. Der Wächter
ändert den Feed nie.

Entscheidungen des Betreibers vom 2026-10-31: Die Regeln bleiben die
Feed-Kette, eine KI meldet nur. Geprüft werden die Meldungen der letzten
24 Stunden, nicht nur die zehn aktuellen („Ein Review der Meldungen der
letzten 24h wäre besser“). Das Modell ist Gemini über das Gratis-Kontingent der
Gemini API, weil das Claude-Abo nur bei Bedarf läuft. Die Gründe und die
Messung dazu stehen in [`architecture.md`](../architecture.md), Abschnitt
„KI-Wächter“.

## Ablauf

| Schritt | Wer | Was |
| --- | --- | --- |
| Auslöser | `.github/workflows/ki-waechter.yml`, Job `gate` | nach jedem Update-Lauf; weiter nur, wenn er erfolgreich war, es 9:00 Wiener Zeit oder später ist, heute noch kein erfolgreicher Prüflauf in `data/side_steps/ki-waechter.json` steht, ein gescheiterter von heute mindestens zwei Stunden zurückliegt und das Tagesbudget eine Anfrage hergibt |
| Eingabe | `scripts/ki_waechter.py prepare` | jede Fassung von `docs/feed.xml` und `docs/feed.en.xml` der 24 Stunden vor dem Commit (jeder Update-Lauf schreibt eine) und die `cache/*/events.json` derselben Commits; jeder verschiedene Meldungstext wird eine Nummer, mit der Zeit im Feed, den gleichzeitig sichtbaren Meldungen, dem eigenen Cache-Ereignis (beim ersten und letzten Auftreten) und bis zu sechs Ereignissen derselben Linien; jede Quelle steht einmal ganz da, danach mit ihrer Kennung (Q…); schreibt `.ki-waechter/eingabe.md` und `eingabe.json` |
| Lesen | `scripts/ki_waechter.py ask` | ein Aufruf der Gemini API (Modell `KI_WAECHTER_MODEL`, derzeit `gemini-3.8-flash`) mit Prompt `scripts/ki_waechter_prompt.md` und Eingabe; keine Werkzeuge; Antwort im festen JSON-Schema; schreibt `antwort.json` |
| Prüfen | `scripts/ki_waechter.py verify` | jedes Zitat wörtlich gegen Feed und Quellen; schreibt den Bericht und `ergebnis.json` |
| Ablage | `scripts/publish_to_main.sh` | `docs/archive/audits/ki-waechter-YYYY-MM-DD.md` und `data/side_steps/ki-waechter.json` nach `main` |
| Meldung | `gh issue create` | nur bei belegten Funden ein Issue, Label `ki-waechter` |

Am 31.10.2026 waren es in 24 Stunden 50 Fassungen des Feeds mit 76
verschiedenen Meldungstexten von 51 Meldungen, rund 94.000 Zeichen Eingabe.

Von Hand: Actions → „KI-Wächter“ → „Run workflow“. Ohne Eingabe prüft er die
24 Stunden bis zum aktuellen Stand und veröffentlicht; mit einem Commit-Hash
prüft er die 24 Stunden bis zu diesem Commit und veröffentlicht nichts
(Bericht in der Zusammenfassung des Laufs und als Artefakt).

## Fundklassen

Geschlossene Liste, gleich wie im letzten Jules-Prompt vom 2026-10-30:

| Klasse | Inhalt | Feed |
| --- | --- | --- |
| F1 | dieselbe Sache zweimal | DE, zwei Meldungen mit je einem Zitat, gleichzeitig im Feed |
| F2 | Kernaussage abgeschnitten | DE oder EN |
| F3 | Fakt weicht von der Quelle ab | DE, mit wörtlichem Quellzitat |
| F4 | Titel widerspricht dem Text | DE oder EN |
| F5 | kaputter Text (geklebte Wörter, HTML-, Platzhalterreste, Doppelungen) | DE oder EN |
| F6 | Englisch unübersetzt, Eigenname übersetzt, Sinn verändert | EN |

Was entschieden ist (Kürzung auf 180 Zeichen, Zeitzeile, Tippfehler der
Betreiber, feste EN-Formen), steht im Prompt als Nicht-Fund. Stil und
Verbesserungsideen meldet der Wächter nie.

## Warum ein Fund belegt sein muss

Michael, 2026-10-30: „Falsche Funde stören mich am meisten.“ Wörtlich heißt
dabei nicht, dass der Feed den Quelltext wiederholen muss: Kürzen und
Umformulieren sind die Aufgabe der Regeln, ob der Sinn stimmt, beurteilt das
Modell. Wörtlich muss sein Beleg sein. Deshalb prüft das Skript jeden Fund,
bevor er zählt:

- Das Feed-Zitat steht wörtlich (nach Leerraum-Angleich) in genau dieser
  Meldung im genannten Feed, mindestens drei Zeichen.
- F3: Das Quellzitat steht wörtlich in den Quellen dieser Meldung.
- F1: Die zweite Meldung hat eine andere GUID, stand gleichzeitig im Feed und
  enthält ihr Zitat.
- Die Klasse passt zum Feed (F6 nur EN, F1 und F3 nur DE), ein erwarteter Text
  ist angegeben.
- Derselbe Fehler in mehreren Textständen einer Meldung zählt einmal.

Ein Fund, der eine Prüfung nicht besteht, steht unter „Nicht belegt“ im
Bericht und öffnet kein Issue. Kopf, Zahlen, GUIDs, Zeiten und die Tabelle der
geprüften Meldungen schreibt das Skript, nicht das Modell. Der Bericht beginnt
immer mit der Überschrift „# KI-Wächter TT.MM.JJJJ“, nie mit Front Matter
(`---`): So kopiert GitHub Pages ihn wie die übrigen Dateien in
`docs/archive/audits/` unverändert, statt ihn mit Jekyll zu rendern, und ein
zitiertes `{{` kann den Pages-Build nicht stören.

## Gratis-Kontingent

Michael, 2026-10-31: „Bitte sorge auch hier dafür, dass das API-Limit nicht
überschritten wird.“ Der Schlüssel `GEMINI_API_KEY` gehört zu einem Projekt
ohne Abrechnung, Kosten können also nicht entstehen. Die Grenzen für
Gemini 3.8 Flash in diesem Projekt (AI Studio, 31.10.2026): 20 Anfragen am
Tag, 5 pro Minute, 250.000 Eingabetokens pro Minute. Auch abgelehnte
Anfragen (503) zählen. Der Wächter nutzt höchstens die Hälfte, über beide
Modelle zusammen gezählt:

- Ein Prüflauf sendet eine Anfrage. Eine Antwort 429 (Kontingent erschöpft)
  wird nie wiederholt und macht den Lauf rot. Nur wenn das Modell überlastet
  ist (500, 502, 503, 504; am 31.10.2026 bekamen alle zehn Anfragen an
  Gemini 3.8 Flash binnen einer Stunde „503 … high demand“), fragt er sofort
  das Ausweichmodell Gemini 3.6 Flash (eigenes Kontingent, gleiche Grenzen)
  und nach einer Minute wieder 3.8 Flash; höchstens drei Anfragen je
  Prüflauf und nie mehr als sein Anteil am Tagesbudget. Welches Modell
  geantwortet hat, steht im Bericht. Die HTTP-Sitzung selbst wiederholt
  nichts.
- Höchstens `KI_WAECHTER_MAX_REQUESTS` (10) Anfragen am Tag. Der Tag des
  Kontingents beginnt um 0:00 Uhr Pazifikzeit, in Wien meist 9:00 Uhr. Jeder
  Prüflauf hinterlässt die Anmerkung „Gemini-Anfragen“ mit seiner Zahl. Der
  Job `gate` zählt sie für alle Prüfläufe dieses Workflows seit diesem
  Zeitpunkt (ein noch laufender zählt mit drei, ein beendeter ohne Anmerkung
  mit einer) und gibt jedem Prüflauf des neuen Laufs den Rest geteilt durch
  ihre Zahl, höchstens drei; bleibt keiner, startet er nichts. Pull Requests
  und Läufe von Hand rechnen nur bis 9; die letzte Anfrage bleibt dem
  täglichen Lauf.
- Der tägliche Lauf beginnt um 9:00 Uhr Wiener Zeit, gleich nach dem Beginn
  des Kontingent-Tags (Michael, 31.10.2026, nachdem Gemini 3.8 Flash den
  ganzen Nachmittag mit 503 antwortete). Scheitert er, versucht er es
  frühestens zwei Stunden später noch einmal, solange das Tagesbudget
  reicht; nach einem erfolgreichen Lauf ruht er bis zum nächsten Tag.
- Die Eingabe ist auf 450.000 Zeichen begrenzt (etwa 130.000 Tokens). Eine
  größere Eingabe wird nicht gesendet, der Lauf ist rot.
- Die Läufe eines Pull Requests laufen nacheinander (`max-parallel: 1`).

Im Gratis-Kontingent darf Google die Eingaben verwenden; sie bestehen nur aus
den öffentlichen Texten des Feeds und der Betreiber.

## Sicherheit

- Das Modell bekommt nur den Prompt und die Eingabe, keine Werkzeuge. Es kann
  nichts lesen, schreiben, ausführen oder veröffentlichen; das tun die
  Schritte danach, mit festem Inhalt.
- Nur der Schritt „Gemini reads and compares“ bekommt `GEMINI_API_KEY`. Der
  Schlüssel geht im Kopf `x-goog-api-key` mit, nie in der URL oder im Log.
- Die Texte der Betreiber sind Daten, keine Anweisungen; der Prompt sagt das,
  und die Eingabe rahmt sie mit `<<<DATEN … DATEN>>>`.
- Freitext des Modells (Erwartet, Begründung, Vermutungen) wird vor dem
  Bericht auf eine Zeile gekürzt; was wie ein Zugangsschlüssel aussieht, wird
  entfernt, Markup unschädlich gemacht.

## Ausfall

Kann der Wächter nicht prüfen (Schlüssel leer oder abgelehnt, Kontingent
erschöpft, Modell bricht ab, Antwort ohne JSON), ist der Lauf rot, und die
Prüfung `Nebenschritte` im Health check wird rot. Rot wird sie auch, wenn
36 Stunden kein Lauf aufgezeichnet wurde. Zwei Stunden später versucht er es
von selbst noch einmal, solange das Tagesbudget reicht; ein erfolgreicher
Lauf macht die Prüfung wieder grün. Von Hand geht es ebenso innerhalb des
Budgets.

## Probelauf im Pull Request

Ändert ein Pull Request `scripts/ki_waechter.py`, den Prompt oder den
Workflow, läuft der Wächter ohne Veröffentlichung über die 24 Stunden vor drei
Feed-Ständen:

| Commit | Stand | Erwartet unter anderem |
| --- | --- | --- |
| `001e47c` | 27.10.2026 16:00 | F6 „algae path“ (Algenweg, behoben in #2075); F1 40A „Busse halten Liechtensteinstraße 39, Liechtensteinstraße 38“ neben der Verlegung Bauernfeldplatz; F2 „Liechtensteinstraße 37a gegenüber Bauernfeldplatz → …“ (beide behoben in #2087) |
| `9e60b8d` | 30.10.2026 15:30 | F6 „All Saints' Worship“ (Allerheiligenverkehr, behoben in #2091) |
| `HEAD` | aktueller Feed | keine falschen Funde |

Der Prompt nennt keinen dieser Fälle (`tests/test_ki_waechter.py`).

Erster Probelauf (PR #2092, 31.10.2026, noch mit Claude und nur den zehn
aktuellen Meldungen): alle vier bekannten Funde erkannt, auch die zwei, die
die Jules-Audits vom 27. bis 29.10. als „OK“ führten; kein Zitat verworfen.
