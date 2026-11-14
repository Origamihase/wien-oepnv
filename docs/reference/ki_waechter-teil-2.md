[KI-Wächter](ki_waechter.md), Teil 2.

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

- Ein Prüflauf sendet zwei Anfragen, seit 07.11.2026 eine je Hälfte des
  Tages, die zweite zuerst an das Modell, das die erste las (Abschnitt
  „Entschieden-Liste und zweite Antwort“; bis zum 05.11.2026 eine, bis zum
  07.11.2026 zwei an dasselbe Modell). Antwortet ein Modell 429, ist sein
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
  erreichtem Kontingent: höchstens acht Anfragen bis zur ersten Antwort, dazu
  eine für die zweite. Bis zum 05.11.
  waren es drei (3.8 Flash, 3.6 Flash, nach einer Minute wieder 3.8 Flash).
  Die Flash-Modelle bleiben vorn, die Lite-Modelle verhindern den verlorenen
  Tag; sie werden mit Denkstufe „high“ gefragt (ihr Standard ist „minimal“,
  die Flash-Modelle behalten „medium“; ein Lite-Modell ohne Denken fand im
  Feed vom 27.10.2026 keinen der vier bekannten Fehler, Probelauf vom
  05.11., Abschnitt „Probelauf“).
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
  nur bis 7, blieb nichts, startete er nichts, und der Update-Lauf startete
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

## Jahresrückblick der Baustellen

Derselbe Schlüssel und dieselbe Modellkette (`DEFAULT_MODELS` in `src/markdown_stats/review_client.py`,
ein Test hält sie gleich mit `KI_WAECHTER_MODEL`) schreiben den Rückblick der
Baustellen eines abgeschlossenen Jahres, in denselben Kontingenten. Gefragt wird
nicht einmal im Jahr: Der Statistik-Schritt von `update-cycle.yml` fragt in
seinen Dashboard-Läufen ab 1. Jänner, bis ein Rückblick angenommen und
gespeichert ist; je fehlendem Jahr ein Durchgang ohne Pause, alle Anfragen eines
Laufs beginnen innerhalb von 110 Sekunden (`ReviewBudget` in `src/markdown_stats/year_archive.py`).
`jahresrueckblick-probe.yml` fragt zur Probe und speichert nichts. Echte Probe am
10.11.2026, 23:19 Uhr: 3.5 Flash-Lite antwortete mit 634 Zeichen, von der
Prüfung angenommen. Ablauf,
Prüfung und Datei: [`data/stats/README.md`](https://github.com/Origamihase/wien-oepnv/blob/main/data/stats/README.md).

## Sicherheit

- Das Modell bekommt nur den Prompt und die Eingabe, keine Werkzeuge. Es kann
  nichts lesen, schreiben, ausführen oder veröffentlichen; das tun die
  Schritte danach, mit festem Inhalt.
- `GEMINI_API_KEY` bekommen nur der Schritt „Gemini reads and compares“, der
  Statistik-Schritt und seit 2026-11-12 der Schritt „Build feed“ von `update-cycle.yml` sowie die zwei Probeläufe (`jahresrueckblick-probe.yml`, `ki-entscheidung-probe.yml`). Der
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

Weiter in [Teil 3](ki_waechter-teil-3.md).
