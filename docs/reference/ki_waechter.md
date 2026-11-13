# KI-Wächter

Die tägliche Prüfung der Feed-Anzeige im Projekt selbst: Gemini liest jeden
Meldungstext, der in den letzten 24 Stunden im Feed stand, neben seinen
Quelltexten und meldet, was der TV-Zuschauer falsch gelesen hat. Der Wächter
ändert den Feed nie.

Entscheidungen des Betreibers vom 2026-10-31: Die Regeln bleiben die Feed-Kette,
eine KI meldet nur (Ausnahme, Betreiberentscheidung vom 10.11.2026: Gemini fasst
die Baustellen eines abgeschlossenen Jahres auf dessen Statistikseite zusammen,
Abschnitt „Jahresrückblick der Baustellen“; Ausnahme vom 12.11.2026: Gemini entscheidet bei unklaren Störungen zwischen „plötzlich“ und „geplant“, [`architecture-teil-16.md`](../architecture-teil-16.md), §14; einen Text des Feeds schreibt weiter keine KI).
Geprüft werden die Meldungen der letzten 24 Stunden, nicht nur die zehn aktuellen
(„Ein Review der Meldungen der letzten 24h wäre besser“). Das Modell ist Gemini
über das Gratis-Kontingent der Gemini API, weil das Claude-Abo nur bei Bedarf
läuft. Die Gründe und die Messung dazu: [`architecture.md`](../architecture.md), „KI-Wächter“.

## Ablauf

| Schritt | Wer | Was |
| --- | --- | --- |
| Auslöser | letzter Schritt von `update-cycle.yml` (`scripts/ki_waechter.py dispatch`, `src/ki_waechter/dispatch.py`; seit 2026-11-05) | nach jedem erfolgreichen Update-Lauf auf `main`; sendet `repository_dispatch` `ki-waechter` nur, wenn es 15:00 Wiener Zeit oder später ist (bis 05.11.2026 9:00), heute noch kein erfolgreicher Prüflauf in `data/side_steps/ki-waechter.json` steht, ein gescheiterter von heute mindestens zwei Stunden zurückliegt, kein Lauf des Wächters läuft oder wartet (Aufzeichnung zuerst aus dem Checkout, bei Fälligkeit frisch von `main`). Bis dahin lief der Job `gate` nach jedem Update-Lauf (`workflow_run`), um das zu prüfen |
| Kontingent | Google (Gemini API, je Projekt und Modell) | zählt selbst und antwortet 429, sobald ein Kontingent erreicht ist; der Wächter zählt nicht: ein Modell mit 429 bleibt im Lauf außen vor, die Kette läuft weiter (Abschnitt „Gratis-Kontingent“) |
| Eingabe | `scripts/ki_waechter.py prepare` | jede Fassung von `docs/feed.xml` und `docs/feed.en.xml` der 24 Stunden vor dem Commit (jeder Update-Lauf schreibt eine) und die `cache/*/events.json` derselben Commits; jeder verschiedene Meldungstext wird eine Nummer, mit der Zeit im Feed, den gleichzeitig sichtbaren Meldungen (seit 05.11.2026 mit der Linie ihres Titels), dem eigenen Cache-Ereignis (beim ersten und letzten Auftreten; sein Text bei 2500 Zeichen abgeschnitten, seit 05.11.2026 mit „[…]“ am Ende) und bis zu sechs Ereignissen derselben Linien (seit 05.11.2026 zuerst die, deren Titel der Text wiederholt: ein zusammengezogener WL-Text besteht aus ihnen); jede Quelle steht einmal ganz da, danach mit ihrer Kennung und ihrem Titel („Q…: siehe oben (Titel)“, seit 05.11.2026); schreibt `.ki-waechter/eingabe.md` und `eingabe.json`, seit 07.11.2026 dazu `eingabe-teil1.md` und `eingabe-teil2.md` (die zwei Hälften, wie das Modell sie bekommt) |
| Lesen | `scripts/ki_waechter.py ask` | die Modellkette `KI_WAECHTER_MODEL` (`gemini-3.8-flash`, bei Überlastung sofort `gemini-3.6-flash`, dann `gemini-3.5-flash-lite` und `gemini-3.1-flash-lite`; jedes einmal je Durchgang, höchstens zwei Durchgänge mit einer Minute Pause; ein Modell, dessen Kontingent laut Google erreicht ist (429), bleibt im Lauf außen vor), bis ein Modell antwortet; zwei Anfragen je Prüflauf, seit 07.11.2026 je eine Hälfte des Tages (Schnitt zwischen zwei Fassungen; die zweite Hälfte zuerst an das Modell, das die erste las, dann an den Rest der Kette; bis dahin zweimal die ganze Eingabe an dasselbe Modell), die Funde beider Antworten werden vereinigt (seit 05.11.2026, Abschnitt „Entschieden-Liste und zweite Antwort“); Prompt `scripts/ki_waechter_prompt.md` und Eingabe, keine Werkzeuge, Antwort im festen JSON-Schema; schreibt `antwort.json` |
| Prüfen | `scripts/ki_waechter.py verify` | jedes Zitat wörtlich gegen Feed und Quellen; schreibt den Bericht und `ergebnis.json` |
| Ablage | `scripts/publish_to_main.sh` | `docs/archive/audits/ki-waechter-YYYY-MM-DD.md` und `data/side_steps/ki-waechter.json` nach `main` |
| Meldung | `gh issue create` | nur bei belegten Funden ein Issue, Label `ki-waechter` |

Am 31.10.2026 waren es in 24 Stunden 50 Fassungen des Feeds mit 76
verschiedenen Meldungstexten von 51 Meldungen, rund 94.000 Zeichen Eingabe.

**Der ganze Tag (seit 10.11.2026).** Der Prüflauf arbeitet in einem flachen
Klon und lädt den Verlauf hinter dem geprüften Commit nach Tiefe: 128 Commits
(`main` hatte vom 05.11. bis 10.11.2026 in 24 Stunden 56 bis 71), und doppelt
so viele, solange `scripts/ki_waechter.py covered` das Ende von `main` noch
im Prüfzeitraum findet (`history_covers`, `src/ki_waechter/inputs.py`;
`main` sind die ersten Eltern des Commits, und nur ihre Commits zählen als
Fassungen). `prepare` lehnt einen Verlauf ab, der im Zeitraum endet: der Lauf
ist dann rot, statt einen Teil des Tages als den Tag zu prüfen. Bis dahin lud
ein `git fetch --shallow-since` 26 Stunden. Der Server schneidet einen
solchen Verlauf an jedem Commit ab, der einen älteren Elternteil hat, und der
Klon sieht diesen Commit ohne Eltern; ein Pull Request, der in diesen Stunden
aus einem älteren Zweig gemergt wurde, verbarg so jede Fassung vor seinem
Merge. Nachgestellt mit den Befehlen des Workflows: der Lauf vom 07.11.2026
las 38 von 51 Fassungen und 50 von 85 Texten (Merge von #2169, dessen Zweig
48 Minuten älter war als die 26 Stunden), der vom 10.11.2026 47 von 51 und 84
von 92 (Merge von #2185); die Läufe vom 06.11., 08.11. und 09.11. waren
vollständig. Der Bericht nannte den kürzeren Zeitraum, sonst fiel nichts auf.
Nachspiel alt gegen neu auf sieben Ständen (27.10., 30.10., 06.11. bis
10.11.): fünf Eingaben byte-gleich, die zwei abgeschnittenen vollständig,
jeder Stand mit einem Abruf der Tiefe 128, der Klon rund 3 MiB größer
(`tests/test_ki_waechter/test_verlauf.py`).

**Geprüft heißt gelesen (seit 10.11.2026).** Eine Hälfte zeigt die Nummern des
ganzen Tages mit Lücken. Jede Antwort zählt deshalb nur mit den Texten ihrer
eigenen Hälfte als geprüft (`_own_texts`, `src/ki_waechter/answers.py`): Eine
Antwort, die die Lücken füllt, macht die Texte einer Hälfte ohne Antwort
nicht zu geprüften.

Von Hand: Actions → „KI-Wächter“ → „Run workflow“. Ohne Eingabe prüft er die
24 Stunden bis zum aktuellen Stand und veröffentlicht; mit einem Commit-Hash
(`rev`) prüft er die 24 Stunden bis zu diesem Commit und veröffentlicht nichts
(Bericht in der Zusammenfassung des Laufs und als Artefakt). Die Eingabe
`model` (Modellnamen, kommagetrennt, seit 05.11.2026) fragt diese Modelle
statt der üblichen Kette; auch ein solcher Probelauf veröffentlicht nichts
und rührt weder die Aufzeichnung noch die Issues an.

## Fundklassen

Geschlossene Liste, gleich wie im letzten Jules-Prompt vom 2026-10-30:

| Klasse | Inhalt | Feed |
| --- | --- | --- |
| F1 | dieselbe Sache zweimal (dieselben Linien vor dem Doppelpunkt; zwei Linien, die ein Vorfall trifft, sind zwei Meldungen) | DE, zwei Meldungen mit je einem Zitat, gleichzeitig im Feed |
| F2 | Kernaussage abgeschnitten (nur bei einem gekürzten oder abgebrochenen Text; die Kernaussage ist die Maßnahme, bei einer Verlegung die neue Haltestelle jeder angefangenen Richtung), oder der Text besteht nur aus der Zeitzeile | DE oder EN |
| F3 | eine Quelle nennt einen Fakt anders als der Feed (Titel und Text der Quelle zählen; ohne eigenes Ereignis kein F3; was der Feed weglässt oder was keine Quelle nennt, ist kein F3) | DE, mit wörtlichem Quellzitat |
| F4 | Titel widerspricht dem Text (Folgen wie „Fahrtbehinderung“ sind keine Ursachen) | DE, zählt einmal für beide Sprachen (seit 05.11.2026) |
| F5 | kaputter Text (geklebte Wörter und fehlende Leerzeichen, auch „46:Umleitung“ und auch, wenn die Quelle so schreibt; HTML-, Platzhalterreste, Doppelungen, deutsche Zahl mit Dezimalpunkt; ein anders geschriebenes Wort, das genauso in der Quelle steht, ist ein Tippfehler des Betreibers) | DE oder EN |
| F6 | Englisch unübersetzt, Eigenname übersetzt, Zahl oder Linie anders, Sinn verändert | EN |

Was entschieden ist (Kürzung auf 180 Zeichen, Zeitzeile, Titel mit zwei
Ursachen, Tippfehler der Betreiber, feste EN-Formen), steht im Prompt unter
„Entschieden, also kein Fund“, seit 05.11.2026 je Code-Entscheidung ein Satz
(Abschnitt „Entschieden-Liste und zweite Antwort“). Stil und
Verbesserungsideen meldet der Wächter nie.

Der Prompt (`scripts/ki_waechter_prompt.md`, seit 05.11.2026 in dieser
Gliederung) erklärt zuerst den Aufbau der Eingabe (Kopf mit der Zahl der
Meldungstexte, Kopfzeile mit GUID und Textstand, „Gleichzeitig im Feed“ mit
der Linie jeder Nummer, die Daten zwischen `<<<DATEN` und `DATEN>>>`, eigenes
Ereignis und gleiche Linie als Quellen, deren Titel und Text beide zählen,
weil bei einem WL-Ticker der Titel die Maßnahme trägt; eine Quelle, die
schon bei einer früheren Meldung steht, als „siehe oben (Titel)“; ein bei
2500 Zeichen abgeschnittener Quelltext endet auf „[…]“; die
Stammstrecken-Meldung hat keine Quelle; woran WL-Störung, Ticker, Hinweis
und Tafel zu erkennen sind), dann den Grundsatz (Zweifel ist eine fehlende
Quelle oder ein mehrdeutiger Sinn, nicht ein bestandener Ja/Nein-Test), die
Fundklassen mit Beispielen und die Zuordnung in fester Reihenfolge, wenn
mehrere passen (ein Text nur aus der Zeitzeile ist F2; an der Kürzung gibt
es nur F2 oder nichts, einzige Ausnahme ein vor dem „…“ falsch übersetztes
Wort als F6; ein Abbruch ist F2 oder F5; eine Stelle, die nur im Englischen
falsch ist, F5 oder F6; kaputtes Deutsch F5; Titel gegen Text F4 vor einem
F3 gegen die Quelle; zuletzt F1), erfundene Beispiele echter Fundklassen
(übersetzter Straßen- oder Haltestellenname, ein von der Kürzung
zerschnittener Begriff, verdoppeltes Wort, Doppelpunkt ohne Leerzeichen,
Dezimalpunkt, Text nur aus der Zeitzeile, „→ …“ ohne neue Haltestelle, Titel
gegen Text), die Entschieden-Liste nach Themen (Kürzung und Kurzform,
Zeitzeile mit ihren Formen, WL-Titel und WL-Ticker, keine Dubletten,
Quelltext der Betreiber, ÖBB, Namen, englische Formen) mit dem Satz, dass sie
keine Vorschrift ist, deren Verletzung ein Fund wäre, während die englischen
Formen die richtige Übersetzung nennen und ein anderes Wort an ihrer Stelle
F6 ist, und ein Vorgehen in neun Schritten je Meldung: DE Text (F2; die Maßnahme zählt als Hauptwort wie als
Verb, „Maßnahmen:“ leitet Einzelheiten ein), Titel
gegen Text (F4; Folgen sind keine Ursachen), Titel und Text gegen die
Quellen, Titel wie Text (F3; Weggelassenes, andere Wörter für dieselbe
Sache und ein Fakt, den keine Quelle nennt, sind kein F3), Kaputtes (F5; geklebte Wörter auch, wenn die Quelle so
schreibt; ein anders geschriebenes Wort, das genauso in der Quelle steht, ist
ein Tippfehler des Betreibers), EN gegen DE Stück für Stück (F6; jeder Name
Wort für Wort, ein deutsches Wort auf -gasse, -straße, -weg, -platz, -gürtel,
-allee oder -brücke ist ein Straßenname und bleibt deutsch), die Nummern
unter „Gleichzeitig im Feed“ (F1), jeden Kandidaten gegen die Liste
widerlegen, ein Fund je Meldung und Klasse mit dem Zitat der ersten Stelle
(dieselbe Form in vielen Meldungen ist fast sicher eine Regel des Feeds),
zuletzt `geprueft` lückenlos bis zur Zahl im Kopf der Eingabe. Das Zitat
legt die Klasse fest: F5 und F6 die fehlerhaften Wörter selbst, F2 die
letzten drei Wörter vor dem „…“ samt dem „…“, F3 der Fakt mit dem Wort
davor, F4 der Titel, F1 die beiden Titel; mindestens drei Zeichen, nichts
dazu, damit es „eindeutig“ wird, denn das Skript sucht wörtlich, nicht
eindeutig. Vermutungen gibt es nur in zwei Fällen (ein F3 ohne eigene Quelle
oder mit abgeschnittenem Quelltext, ein unklares F1), in der Form „Meldung
N, F3: „Zitat“ – fehlt: Quelle.“ Die Beispiele sind erfunden, weil der
Prompt dem Modell keine Antwort der Replay-Stände vorsagen darf
(`tests/test_ki_waechter/test_prompt.py` hält die bekannten Fälle des 27.10.
und 30.10. heraus); die echten Fälle stehen im Abschnitt „Probelauf“.

## Warum ein Fund belegt sein muss

Michael, 2026-10-30: „Falsche Funde stören mich am meisten.“ Wörtlich heißt
dabei nicht, dass der Feed den Quelltext wiederholen muss: Kürzen und
Umformulieren sind die Aufgabe der Regeln, ob der Sinn stimmt, beurteilt das
Modell. Wörtlich muss sein Beleg sein. Deshalb prüft das Skript jeden Fund,
bevor er zählt:

- Das Feed-Zitat steht wörtlich (nach Leerraum-Angleich) in genau dieser
  Meldung im genannten Feed, mindestens drei Zeichen. Ein Zitat, das auf „…“
  endet, gilt nur bei F2: was die Kürzung nimmt, ist höchstens eine verlorene
  Kernaussage (05.11.2026).
- Ein Zitat aus der Zeitzeile (der eckigen Klammer am Textende) gilt nur bei
  F2, denn die Zeitzeile schreibt der Code (05.11.2026; Lauf 302 meldete
  „Di 27.10.“ als F3 gegen einen Wochentag der Quelle).
- Ein F3, dessen Feedzitat so in Titel oder Text einer Quelle der Meldung steht,
  ist verworfen: der Fakt steht dort, wie der Feed ihn schreibt; ein F3 braucht
  eine Quelle, die anders lautet (Lauf 315, 05.11.2026).
- Ein F2 zu einem Text, der vor der Zeitzeile mit einem vollständigen Satz endet, ist
  verworfen: F2 braucht eine Kürzung („…“), einen abgebrochenen Satz oder einen Text
  nur aus der Zeitzeile („Fahrtbehinderung.“ unter „12: Polizeieinsatz“ ist die
  WL-Kurzform, Lauf 318, 05.11.2026).
- Ein F2 an einer Kürzung gilt nur, wenn vor dem „…“ kein Maßnahmenwort steht
  (`CORE_WORDS` in `src/ki_waechter/audit.py`: Umleitung, Kurzführung, Kein
  Betrieb, Fahrtbehinderung, nicht fahren, entfällt, fällt aus, hält nicht,
  Betrieb ab, Ersatzverkehr, Verspätungen, Intervalle, Busse oder Züge halten,
  und ihre englischen Formen). Eine Kürzung hinter der Maßnahme ist
  entschieden („Wortweise bleibt“, 05.11.2026); das Modell meldete sie in vier
  von acht Fassungen des Prompts trotzdem. Ein Zitat auf „→ …“ bleibt F2, weil
  die neue Haltestelle einer angefangenen Richtung die Kernaussage ist.
- F3: Das Quellzitat steht wörtlich in den Quellen dieser Meldung.
- F1: Die zweite Meldung hat eine andere GUID, stand gleichzeitig im Feed,
  enthält ihr Zitat, und ihr Titel nennt dieselben Linien vor dem Doppelpunkt
  (05.11.2026, Lauf 306: zwei Linien, die ein Feuerwehreinsatz traf, sind
  zwei Meldungen, keine Dublette).
- Die Klasse passt zum Feed (F6 nur EN; F1, F3 und F4 nur DE, F4 seit
  05.11.2026, weil ein Widerspruch zwischen Titel und Text in beiden Sprachen
  gleich steht und einmal zählt), ein erwarteter Text ist angegeben (bei F1 setzt das Skript „nur eine Meldung“ ein, wenn er
  fehlt: ein Paar hat keinen, sein Beleg sind die zwei Zitate; 05.11.2026).
- Ein Fund je Meldung (GUID), Klasse und Feed: derselbe Fehler in mehreren
  Textständen einer Meldung, mehrere Stellen einer Klasse in einem Text und
  ein Fehler, den die beiden Antworten verschieden lang zitieren, zählen
  einmal (das erste Zitat bleibt).
- Ein F1 ist ein Paar: nennen beide Antworten oder beide Meldungen dasselbe
  Paar von der jeweils anderen Seite, zählt es einmal (`verify` und `_union`
  schlüsseln die beiden GUIDs ungeordnet).

Ein Fund, der eine Prüfung nicht besteht, steht unter „Nicht belegt“ im
Bericht und öffnet kein Issue. Kopf, Zahlen, GUIDs, Zeiten und die Tabelle der
geprüften Meldungen schreibt das Skript, nicht das Modell.

<!-- {% raw %} -->

GitHub Pages rendert jede Markdown-Datei unter `docs/` mit Jekyll, auch ohne
Front Matter, und lässt Liquid darüberlaufen: Ein zitiertes `{{ … }}`
verschwindet dabei stillschweigend (so geschehen in
`archive/audits/audit-2026-09-13`), ein `{%` ohne gültiges Tag lässt den
Pages-Build scheitern, und Website und Fernseher bleiben stehen. Das Skript
setzt deshalb im Bericht hinter jede öffnende Klammer eines solchen Paars ein
Leerzeichen (`liquid_safe` aus `src/utils/liquid.py`), bevor es ihn schreibt.

<!-- {% endraw %} -->

Von Hand geschriebene Seiten, die solche Zeichenpaare wörtlich zeigen, setzen
sie zwischen zwei HTML-Kommentare mit den Liquid-Tags `raw` bzw. `endraw`
(einfache Form ohne Bindestrich), jeder in einer eigenen Zeile mit einer
Leerzeile davor und danach, so wie im Quelltext dieser Seite um den Absatz
darüber. Mit #2092 kam dieser Absatz am
2026-11-01 ungeschützt auf `main`: Bis zur Korrektur um 11:13 Uhr Wiener Zeit lief kein
Pages-Build mehr durch (22 gescheitert, 4 abgebrochen), und der Feed unter der
Pages-Adresse blieb auf dem Stand von 08:30 Uhr Wiener Zeit stehen.
`tests/test_docs_liquid.py` prüft seitdem jede Seite unter `docs/`, die
Jekyll rendert; `scripts/publish_to_main.sh` macht in jeder Seite, die ein
Workflow zum Commit vormerkt, verbliebene Paare unwirksam (Regel in
`docs/development.md`).

## Entschieden-Liste und zweite Antwort

Michael, 2026-11-05, Karte „Wie soll der KI-Wächter weniger falsche Funde
liefern?“: „Beides“, also die präzise Liste und die zweite Antwort. Anlass war
die Messung mit den Lite-Modellen (Abschnitt „Probelauf“): zwanzig falsche
Funde an zwei Tagen gingen auf zwei knappe Sätze unter „Entschieden“ zurück
(„Ursachen beginnen mit ‚Due to‘“ ließ das Modell jede Zeile „Reason: …“
melden, die der Code aus WLs „Grund: …“ macht; „die Badner Bahn heißt ‚LB‘“
ließ es den Text der Kurzform melden, in dem WLs eigener Name steht), und
derselbe Stand brachte nicht in jedem Lauf dieselben Funde.

**Die Liste.** Der Abschnitt „Entschieden, also kein Fund“ in
`scripts/ki_waechter_prompt.md` nennt jede Entscheidung zu Feed-Texten so,
wie der Code sie umsetzt, mit einem Beispiel und der Klasse, die sie nicht
auslöst: Kürzung und Kurzform, die Zeitzeile mit „[Seit hh:00]“ und
„[Seit 00:00]“ bei einem Störungswort (03. und 04.11.), zwei Ursachen im
WL-Titel (02.11.), die Nicht-Dubletten vom 01.11. (Anzeigetafel neben
„Bauarbeiten“, Hinweis über Wochen neben kurzer Störung), Tippfehler auch
hinter einem Schrägstrich (02.11.), der umgestellte ÖBB-Satz (Züge und „nicht
fahren“ vor dem Doppelpunkt, Datumslisten als „an einzelnen Tagen“, Züge als
Zahl mit Linien), die ÖBB-Kurzform je Linie (05.11., #2165), Umstiegszusatz und „Hauptbahnhof“/„Hbf“ (02. und 04.11.),
die EN-Formen „Reason: …“ und „Due to …“ und die Badner Bahn („LB“ im Titel,
„Badner Bahn“ in der Kurzform); nach dem sechsten Probelauf (05.11.) auch
die ÖBB-Bahnhofsnamen im EN-Feed („Bahnhof“/„Bahnhst“ → „station“,
`_oebb_station_en` in `src/feed_build/en_templates.py`), die Lage einer
Haltestelle an einer Querstraße („nach“ → „after“, „vor“ → „before“,
`_PLACE_AFTER_CROSS_STREET_RE` in `src/feed_build/pretranslate.py`), die
Kürzung vor 180 Zeichen an einer Wortgrenze und hinter der Kernaussage (weder
F2 noch F5, `_truncate_summary_180` in `src/feed_build/summary_text.py`) und
der Zusammenzug gleichzeitiger WL-Ticker zu einer Meldung (kein F3). Ein
falscher Fund ist seither ein Mangel dieser Liste und wird als Klasse
behoben, nicht einzeln. Am Abend des 05.11. (siebter Probelauf, Abschnitt
„Probelauf“) wurde die Liste nach Themen geordnet und um die Entscheidungen
aus `changelog/` seit dem 25.10. ergänzt, die noch fehlten: ein Hinweis eines
Betriebstags tritt hinter einer laufenden Störung zurück, eine Tafel „Busse
halten …“ geht in der Verlegung auf, der Pfeil steht hinter dem Doppelpunkt
der Richtung, DE und EN werden getrennt gekürzt, und was hinter dem „…“
fehlt, ist kein F2, ÖBBs Zeitfenster bleibt und vergangene Tage fallen aus
dem Text, Zugzahlen mit Gattungen, Namen in der Grundform („Hohe Warte“) und
mit Gattungswort („Cobenzl, Parkplatz“), die Reihenfolge der Linien im Titel,
„…verkehr“ als „… service“, „Towards“ und „Discontinued“, die
Verlegungsgründe aus dem Glossar, ein Satz ohne Linienkennung in der Kurzform
gilt für alle Linien des Titels. Aus den Probeläufen des Abends dazu: Titel
und Text einer Quelle zählen beide (die Maßnahme eines WL-Tickers steht im
Titel), ein Wort, das genauso in der Quelle steht, ist ein Tippfehler des
Betreibers, und die Liste ist keine Vorschrift, deren Verletzung ein Fund
wäre.

**Die Regel.** Jede Entscheidung zu Feed-Texten, die in
`changelog/<JJJJ-MM-TT>.md` steht (Wortwahl, Zeitzeile, Titel, Kürzung,
Übersetzung, Reihung, was bleibt und was wegfällt), bekommt im selben Pull
Request ihre Zeile in dieser Liste; der Kopf von `CHANGELOG.md` und
`docs/development-teil-4.md` sagen das. So kennt der Wächter die
Entscheidung, bevor sie im Feed steht.

**Die zweite Antwort.** `ask` fragt die Kette, bis ein Modell antwortet,
und schickt eine zweite Anfrage; `unite_answers` (seit 07.11.2026 in
`src/ki_waechter/answers.py`) vereinigt beide Antworten: jeder Fund einer
der beiden (gleich, wenn Meldung, Klasse und Feed gleich sind, wie in
`verify`; das Zitat zählt nicht mit, weil die beiden Antworten denselben
Fehler verschieden lang zitieren), jede geprüfte Nummer, beide Listen der
Vermutungen, Tokens und Anfragen summiert. Bis zum 07.11.2026 trugen beide
Anfragen die ganze Eingabe, die zweite ging genau einmal an das Modell, das
geantwortet hatte (`ask_twice` in `scripts/ki_waechter.py`, bleibt für eine
Eingabe mit nur einer Fassung). Seit 07.11.2026 liest jede Anfrage eine
Hälfte des Tages (`halves` in `src/ki_waechter/inputs.py`, `ask_parts` in
`answers.py`): Die Eingabe wird zwischen zwei Fassungen geschnitten, dort,
wo die größere Hälfte am wenigsten Texte hat, bei Gleichstand nahe der
Mitte; jede Hälfte hält jeden Text, der in ihren Fassungen im Feed stand
(ein Text über den Schnitt hinweg steht in beiden), mit allen Quellen
einmal ganz, den Nummern des ganzen Tages und „Gleichzeitig im Feed“ nur
aus dieser Hälfte; ihr Kopf sagt „Teil 1 von 2“ oder „Teil 2 von 2“, der
Prompt erklärt das. Die erste Hälfte geht die Kette entlang, die zweite
zuerst an das Modell, das die erste las, dann an den Rest der Kette (so
liest meist ein Modell beide Hälften, und ein überlastetes Modell lässt den
halben Tag nicht ungelesen); sie bekommt die Sekunden, die die erste von
`NO_NEW_REQUEST_AFTER_S` (720 s) übrig ließ, nach einer längeren ersten
Antwort folgt keine zweite Anfrage, so bleibt die längste Anfrage im
Schrittlimit von 25 Minuten. Bei 503, 429, einer abgebrochenen Verbindung
oder einer Antwort ohne JSON gilt die eine Antwort allein, die ungelesene
Hälfte steht im Bericht als nicht geprüft. Der Bericht nennt „Modell …,
zwei Antworten, je eine Hälfte der Fassungen (1–20: 56 Texte,
gemini-3.6-flash; 21–52: 49 Texte, …)“, `antwort.json` trägt `answers` und
`parts` (je Hälfte Modell, Anfragen, Sekunden), `ergebnis.json`
`antworten`; `prepare` legt die Hälften als `eingabe-teil1.md` und
`eingabe-teil2.md` zu den Dateien des Laufs. Gemessen am 07.11.2026 auf der
Eingabe vom 06.11. (Commit 6929f27, 52 Fassungen, 102 Texte, Modell 3.6
Flash): Der Schnitt fällt zwischen die Fassungen von 23:30 und 00:01, wo
die meisten Texte ihre Zeitzeile wechseln, 56 und 49 Texte, 3 in beiden;
die Hälften (Läufe 328 und 329) und die ganze Eingabe zweimal (Lauf 330)
fanden dieselben zwei Fehler, die Hälften mit 81.287 statt 130.242
Eingabetokens je Prüflauf (Michael, 07.11.2026: Die Ersparnis gab den
Ausschlag). Kosten: zwei Anfragen je Prüflauf, bei Überlastung bis zu acht
je Hälfte (zwei Durchgänge über vier Modelle). Ein Fund, den die Antworten
mit verschieden langem Zitat nennen, stand bis zum Abend des 05.11. zweimal
im Bericht (30.10.: „Reason: Harmful“ und „Reason: Harmful …“); seither
zählt er einmal mit dem Zitat der ersten Antwort, und Vermutungen beider
Antworten zur selben Meldung stehen einmal. Tests in
`tests/test_ki_waechter/test_zweite_antwort.py` und `test_haelften.py`.

Weiter in [Teil 2](ki_waechter-teil-2.md).
