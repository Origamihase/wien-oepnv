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
| Auslöser | letzter Schritt von `update-cycle.yml` (`scripts/ki_waechter.py dispatch`, `src/ki_waechter/dispatch.py`; seit 2026-11-05) | nach jedem erfolgreichen Update-Lauf auf `main`; sendet `repository_dispatch` `ki-waechter` nur, wenn es 15:00 Wiener Zeit oder später ist (bis 05.11.2026 9:00), heute noch kein erfolgreicher Prüflauf in `data/side_steps/ki-waechter.json` steht, ein gescheiterter von heute mindestens zwei Stunden zurückliegt, kein Lauf des Wächters läuft oder wartet (Aufzeichnung zuerst aus dem Checkout, bei Fälligkeit frisch von `main`). Bis dahin lief der Job `gate` nach jedem Update-Lauf (`workflow_run`), um das zu prüfen |
| Kontingent | Google (Gemini API, je Projekt und Modell) | zählt selbst und antwortet 429, sobald ein Kontingent erreicht ist; der Wächter zählt nicht: ein Modell mit 429 bleibt im Lauf außen vor, die Kette läuft weiter (Abschnitt „Gratis-Kontingent“) |
| Eingabe | `scripts/ki_waechter.py prepare` | jede Fassung von `docs/feed.xml` und `docs/feed.en.xml` der 24 Stunden vor dem Commit (jeder Update-Lauf schreibt eine) und die `cache/*/events.json` derselben Commits; jeder verschiedene Meldungstext wird eine Nummer, mit der Zeit im Feed, den gleichzeitig sichtbaren Meldungen, dem eigenen Cache-Ereignis (beim ersten und letzten Auftreten) und bis zu sechs Ereignissen derselben Linien; jede Quelle steht einmal ganz da, danach mit ihrer Kennung (Q…); schreibt `.ki-waechter/eingabe.md` und `eingabe.json` |
| Lesen | `scripts/ki_waechter.py ask` | ein Aufruf der Gemini API (Modellkette `KI_WAECHTER_MODEL`: `gemini-3.8-flash`, bei Überlastung sofort `gemini-3.6-flash`, dann `gemini-3.5-flash-lite` und `gemini-3.1-flash-lite`; jedes einmal je Durchgang, höchstens zwei Durchgänge mit einer Minute Pause; ein Modell, dessen Kontingent laut Google erreicht ist (429), bleibt im Lauf außen vor) mit Prompt `scripts/ki_waechter_prompt.md` und Eingabe; keine Werkzeuge; Antwort im festen JSON-Schema; schreibt `antwort.json` |
| Prüfen | `scripts/ki_waechter.py verify` | jedes Zitat wörtlich gegen Feed und Quellen; schreibt den Bericht und `ergebnis.json` |
| Ablage | `scripts/publish_to_main.sh` | `docs/archive/audits/ki-waechter-YYYY-MM-DD.md` und `data/side_steps/ki-waechter.json` nach `main` |
| Meldung | `gh issue create` | nur bei belegten Funden ein Issue, Label `ki-waechter` |

Am 31.10.2026 waren es in 24 Stunden 50 Fassungen des Feeds mit 76
verschiedenen Meldungstexten von 51 Meldungen, rund 94.000 Zeichen Eingabe.

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

## Gratis-Kontingent

Michael, 2026-10-31: „Bitte sorge auch hier dafür, dass das API-Limit nicht
überschritten wird.“ Der Schlüssel `GEMINI_API_KEY` gehört zu einem Projekt
ohne Abrechnung, Kosten können also nicht entstehen. Google zählt je Projekt
und Modell (Anfragen pro Minute, Eingabetokens pro Minute, Anfragen pro Tag;
der Tag beginnt um 0:00 Uhr Pazifikzeit, in Wien 9:00 Uhr, 8:00 Uhr in den
Wochen zwischen den Zeitumstellungen) und antwortet `429 RESOURCE_EXHAUSTED`,
sobald eine Grenze erreicht ist. Die Grenzen in diesem Projekt (AI Studio,
05.11.2026): Gemini 3.8 Flash und 3.6 Flash je 20 Anfragen am Tag, 5 pro
Minute, 250.000 Eingabetokens pro Minute; Gemini 3.5 Flash-Lite und 3.1
Flash-Lite je 500 Anfragen am Tag, 15 pro Minute, 250.000 Eingabetokens pro
Minute. Auch abgelehnte Anfragen (503) zählen. Der Wächter zählt nicht selbst
(Michael, 05.11.2026: „Zähle es bitte so wie Google. Wir brauchen keine eigene
Zähltechnik.“):

- Ein Prüflauf sendet eine Anfrage. Antwortet das Modell 429, ist sein
  Kontingent laut Google erreicht: Es wird in diesem Lauf nicht mehr gefragt,
  die Kette geht sofort zum nächsten Modell. Die Antwort nennt das verletzte
  Kontingent und seinen Wert (`google.rpc.QuotaFailure`: `quotaId`,
  `quotaValue`) und eine Wartezeit (`google.rpc.RetryInfo`); das steht im Log
  und in `antwort.json`, so sieht man Googles Zahlen, nicht unsere. Ändert
  Google das Kontingent, ändert sich hier nichts. Den Verbrauch zeigt nur das
  Dashboard „Rate limits“ in AI Studio; eine Abfrage über die API gibt es laut
  Googles Doku nicht (die Antwort einer Anfrage enthält nur deren Tokens,
  `usageMetadata`), die Cloud Quotas API liefert Limits, nicht den Verbrauch,
  und der Verbrauch über die Cloud Monitoring API bräuchte IAM-Rollen und ein
  Dienstkonto statt des API-Schlüssels (geprüft 05.11.2026:
  aistudio.google.com/docs/rate-limits, docs.cloud.google.com/docs/quotas).
- Ist das Modell überlastet (500, 502, 503, 504; am 31.10.2026 bekamen alle
  zehn Anfragen an Gemini 3.8 Flash binnen einer Stunde „503 … high
  demand“), fragt er sofort das nächste Modell der Kette: Gemini 3.6 Flash
  (eigenes Kontingent, gleiche Grenzen), dann Gemini 3.5 Flash-Lite und
  Gemini 3.1 Flash-Lite (seit 05.11.2026, Betreiberentscheidung: „Baue bitte
  auch einen Modellwechsel mit den Lite-Modellen als Ergänzung.“ Anlass: Vom
  01. bis 05.11.2026 antworteten beide Flash-Modelle jeden Morgen um 9:00 Uhr
  mit 503, die Prüfung des Tages hing am Versuch zwei Stunden später, am
  04.11. fiel sie ganz aus, und der Health check war rot). Hat kein Modell
  geantwortet, wartet er eine Minute und geht die Kette ein zweites Mal
  durch (`MAX_ROUNDS` = 2, `OVERLOAD_WAIT_S` = 60), ohne die Modelle mit
  erreichtem Kontingent: höchstens acht Anfragen je Prüflauf. Bis zum 05.11.
  waren es drei (3.8 Flash, 3.6 Flash, nach einer Minute wieder 3.8 Flash).
  Die Flash-Modelle bleiben vorn, die Lite-Modelle verhindern den verlorenen
  Tag; sie werden mit Denkstufe „high“ gefragt (ihr Standard ist „minimal“,
  die Flash-Modelle behalten „medium“; ein Lite-Modell ohne Denken fand am
  27.10.2026 keinen der vier bekannten Fehler, Abschnitt „Probelauf“).
  Welches Modell geantwortet hat, steht im Bericht. Die Live-API-Modelle (im
  Kontingent „unbegrenzt“) und Gemma 4 kamen nicht in Frage: Die Live API
  antwortet laut Modellseite nur als Audio und über eine WebSocket-Sitzung,
  Gemma 4 erlaubt 16.000 Tokens pro Minute, die Eingabe hat das Drei- bis
  Vierfache. Die HTTP-Sitzung selbst wiederholt nichts. Eine abgebrochene
  Verbindung zählt wie ein überlastetes Modell (seit 02.11.2026: an diesem
  Tag brach die Verbindung nach 261 Sekunden ab, während das Modell noch
  rechnete, und der Lauf endete ohne Bericht, obwohl er drei Anfragen
  hatte). Die Anfrage zählt, sie kann Google erreicht haben. Eine Antwort,
  die nach zehn Minuten noch fehlt (Lesezeitgrenze), wird nicht wiederholt.
- Die Verbindung zu Gemini sendet TCP-Keepalive (erste Probe nach
  60 Sekunden Stille, dann alle 30 Sekunden), damit kein NAT und keine
  Firewall auf dem Weg eine Verbindung vergisst, auf deren Antwort der
  Wächter minutenlang wartet (`keepalive=True` in `session_with_retries`,
  `KEEPALIVE_SOCKET_OPTIONS` in `src/utils/http_safety/adapters.py`). Die anderen Abrufe
  des Projekts warten höchstens Sekunden und bleiben ohne. `request_safe`
  schickt HTTPS über einen eigenen, auf die geprüfte IP festgelegten
  Adapter (`_get_pinned_session`) und gibt ihm die Keepalive-Einstellung
  der Sitzung mit; bis zur unabhängigen Prüfung vom 02.11.2026 erreichte
  sie nur den Adapter der Sitzung, den diese Anfragen nie benutzen, und die
  Verbindung des Wächters lief ohne Keepalive.
- Nach zwölf Minuten (Wartezeit eingerechnet) beginnt keine neue Anfrage
  mehr (`NO_NEW_REQUEST_AFTER_S`). So endet auch die letzte vor der Grenze
  des Schritts von 25 Minuten. Die Anmerkungen „Gemini-Anfragen“ (Zahl) und
  „Gemini-Modelle“ (je Modell) jedes Prüflaufs sind Information für den
  Leser des Laufs, nichts rechnet mit ihnen.
- Bis zum 05.11.2026 zählte der Job `gate` selbst: höchstens zehn Anfragen
  am Tag über alle Modelle, gelesen aus der Anmerkung „Gemini-Anfragen“ jedes
  Prüflaufs seit 0:00 Uhr Pazifikzeit über die GitHub-API; jeder Prüflauf
  bekam seinen Anteil am Rest, Pull Requests und Läufe von Hand rechneten
  nur bis 6, blieb nichts, startete er nichts, und der Update-Lauf startete
  an so einem Tag keinen weiteren Versuch. Das schützte vor dem Kontingent
  eines einzelnen Modells, als die Kette nur die beiden Flash-Modelle hatte,
  kostete aber Probeläufe, Lesezugriffe auf die API und am 01.11.2026 fast
  die Prüfung des Tages (nach 9 Anfragen der Probeläufe blieb ihr eine, die
  mit 503 scheiterte). Mit den Lite-Modellen (500 am Tag) in der Kette kann
  kein Lauf dem täglichen Lauf die Antwort nehmen.
- Der tägliche Lauf beginnt um 15:00 Uhr Wiener Zeit (seit 05.11.2026). Vom
  31.10. bis 05.11.2026 begann er um 9:00 Uhr, gleich nach dem Beginn des
  Kontingent-Tags (Michael, 31.10.2026, nachdem Gemini 3.8 Flash den ganzen
  Nachmittag mit 503 antwortete); um diese Stunde darf aber jeder wieder, und
  an fünf Morgen in Folge antworteten beide Flash-Modelle 503, während der
  Versuch um 11:00 Uhr an drei von vier Tagen gelang (Michael, 05.11.2026:
  „Wenn jeder wieder darf, ist es klar, dass die API eine höhere Last hat …
  wir sollten später abfragen. Wie wärs mit 15:00 Uhr?“). Scheitert er,
  versucht er es frühestens zwei Stunden später noch einmal; nach einem
  erfolgreichen Lauf ruht er bis zum nächsten Tag. Das entscheidet seit
  2026-11-05 der Update-Lauf (Tabelle „Ablauf“, Zeile „Auslöser“; die Stunde
  und der Abstand stehen als `KI_WAECHTER_START_HOUR` und
  `KI_WAECHTER_RETRY_HOURS` in `update-cycle.yml`); bis dahin lief das Gate
  dafür nach jedem Update-Lauf, 48-mal am Tag.
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

Der Wächter unterscheidet zwei Arten, nicht prüfen zu können
(Betreiberentscheidung Michael, 2026-11-04, Karte „Ja, so bauen“; Anlass:
Gemini antwortete vom 01. bis 04.11.2026 jeden Tag um 9:00 mit 503, und der
Health check war jedes Mal rot, obwohl der Feed lief):

- **Gemini überlastet.** Die letzte Anfrage endete mit 500, 502, 503 oder 504,
  mit einer abgebrochenen Verbindung, nach zehn Minuten ohne Antwort oder
  mit 429 (Kontingent laut Google erreicht); seit 05.11.2026 heißt das,
  dass kein Modell der Kette geantwortet hat. `ask` schreibt `"overloaded": true` in
  `antwort.json`, der Lauf endet mit einer Warnung grün, und
  `data/side_steps/ki-waechter.json` hält das Ergebnis `overloaded` fest. Die
  Prüfung `Nebenschritte` im Health check zeigt das als Hinweis mit der
  Uhrzeit, ab der sie rot wird: 30 Stunden nach der letzten gelungenen
  Prüfung (`HEALTH_KI_WAECHTER_OVERLOAD_HOURS`), gab es noch keine, 30 Stunden
  nach dem ersten Fehlschlag (`failing_since`). Rot wird es also erst, wenn
  ein ganzer Tag mit allen Versuchen verloren ist.
- **Alles andere.** Schlüssel leer oder abgelehnt (400, 401, 403), Modell
  nicht gefunden (404), Eingabe zu groß, Antwort ohne JSON, ein Fehler im
  Skript: Der Lauf ist rot, und die Prüfung `Nebenschritte` wird sofort rot.
  Rot wird sie auch, wenn 36 Stunden kein Lauf aufgezeichnet wurde.

In beiden Fällen versucht er es zwei Stunden später von selbst noch einmal;
ein erfolgreicher Lauf macht die Prüfung wieder grün. Von Hand geht es
ebenso. Ein Probelauf im Pull Request bleibt auch bei Überlastung rot, weil
er kein Ergebnis hat.

## Probelauf im Pull Request

Ändert ein Pull Request `scripts/ki_waechter.py`, den Prompt oder den
Workflow, läuft der Wächter ohne Veröffentlichung über die 24 Stunden vor drei
Feed-Ständen:

| Commit | Stand | Erwartet unter anderem |
| --- | --- | --- |
| `001e47c` | 27.10.2026 16:00 | F6 „algae path“ (Algenweg, behoben in #2075); F1 40A „Busse halten Liechtensteinstraße 39, Liechtensteinstraße 38“ neben der Verlegung Bauernfeldplatz; F2 „Liechtensteinstraße 37a gegenüber Bauernfeldplatz → …“ (beide behoben in #2087) |
| `9e60b8d` | 30.10.2026 15:30 | F6 „All Saints' Worship“ (Allerheiligenverkehr, behoben in #2091) |
| `HEAD` | aktueller Feed | keine falschen Funde |

Der Prompt nennt keinen dieser Fälle (`tests/test_ki_waechter/`).

Erster Probelauf (PR #2092, 31.10.2026, noch mit Claude und nur den zehn
aktuellen Meldungen): alle vier bekannten Funde erkannt, auch die zwei, die
die Jules-Audits vom 27. bis 29.10. als „OK“ führten; kein Zitat verworfen.

Probelauf mit Gemini über 24 Stunden (PR #2092, 01.11.2026 ab 8:16 Uhr
Wiener Zeit, [Lauf](https://github.com/Origamihase/wien-oepnv/actions/runs/41271009232)):

| Stand | Modell | Ergebnis |
| --- | --- | --- |
| `001e47c` (27.10.) | Gemini 3.6 Flash (3.8 Flash antwortete 503), 2 Anfragen, 37 s, 35.190 Eingabetokens | 67 Meldungstexte aus 48 Fassungen. Drei Funde, alle zutreffend: F6 „algae path“, F2 „Liechtensteinstraße 37a gegenüber Bauernfeldplatz → …“ und ein neuer F1: „1/2/71/D: Veranstaltung“ (WL-Störungsticker) und „1/2/71/D: Nationalfeiertag“ (WL-Hinweis) standen am 26.10. eine Stunde lang nebeneinander im Feed und beschreiben dieselben Maßnahmen auf der Ringstraße. Nicht erkannt: der bekannte F1 40A (Verlegung Bauernfeldplatz neben „Busse halten Liechtensteinstraße 39, Liechtensteinstraße 38“). |
| `9e60b8d` (30.10.) | – | Zwei Versuche (8:18 und 8:23 Uhr, je drei Anfragen an 3.8, 3.6 und wieder 3.8 Flash): nur „503 … high demand“. Nicht geprüft; von Hand nachholen (Actions → „KI-Wächter“ → „Run workflow“ mit `9e60b8de9dce438906b257e42a8b524e4f191f67`). |
| `HEAD` (24 h bis 01.11. 04:30) | Gemini 3.8 Flash, 1 Anfrage, 77 s, 40.186 Eingabetokens | 77 Meldungstexte aus 49 Fassungen. Ein Fund, zutreffend und bis dahin unbekannt: F6 „Continue via line 37 to High Wait.“ (31.10. 18:30, Linie 38 Polizeieinsatz; der WL-Text sagt „bis Hohen Warte“, die gebeugte Form traf die Maskierung des Haltestellennamens nicht). Keine falschen Funde; die Kranarbeiten-Ticker 29A/33A/N31 neben dem Hinweis „29A/33A/29B/N31: Bauarbeiten“ wurden nicht als Doppelung gemeldet. |

Bilanz: fünf Funde, fünf zutreffend, kein Zitat verworfen; zwei bekannte
Doppelungen (F1) nicht erkannt. Die Überlastung (503) traf an diesem Morgen
beide Modelle unregelmäßig: 3.6 Flash antwortete um 8:17 Uhr, 3.8 Flash um
8:19 Uhr, beide verweigerten um 8:18 und 8:23 Uhr.

Zweiter Probelauf mit dem Code von PR #2124 (02.11.2026 ab 10:06 Uhr
Wiener Zeit, [Lauf](https://github.com/Origamihase/wien-oepnv/actions/runs/41417947865)):

| Stand | Modell | Ergebnis |
| --- | --- | --- |
| `001e47c` (27.10.) | Gemini 3.8 Flash, 1 Anfrage, 242 s | Ein Fund, zutreffend: F6 „algae path“. Die Doppelung 1/2/71/D steht diesmal nur unter den Vermutungen, F2 40A fehlt; dieselbe Eingabe ergibt nicht jedes Mal dieselben Funde. Die Antwort kam nach mehr als vier Minuten, und zwar noch ohne Keepalive (es erreichte die Verbindung erst nach der Prüfung vom 02.11.). |
| `9e60b8d` (30.10.) | Gemini 3.8 Flash, 1 Anfrage, 140 s | Erstmals geprüft. Zwei Funde, beide im Feed belegt: F2 „Liechtensteinstraße 37a gegenüber Bauernfeldplatz → …“ (40A, die neue Haltestelle Liechtensteinstraße 39 fehlt) und F6 „Under-Meidlinger Straße“ (7B, Straßenname „Unter-Meidlinger Straße“ übersetzt). Unter den Vermutungen außerdem „Parlament/ Folktheter“ statt Volkstheater im EN-Feed. F2 und F6 waren mit #2087 schon behoben (30.10., 10:44 Uhr); „Folktheter“ behebt der Schrägstrich-Schutz für Haltestellennamen (CHANGELOG, 02.11.). |
| `HEAD` (24 h bis 02.11.) | – | 3.8 Flash, dann 3.6 Flash mit 503, zusammen 393 s; der Anteil des Probelaufs (2 Anfragen) war damit verbraucht. Welcher Fehler die erste Anfrage beendete, zeigte das Protokoll noch nicht; seither steht jeder Versuch ohne Antwort mit Modell und Dauer im Protokoll und in `antwort.json` (`attempts`). |
