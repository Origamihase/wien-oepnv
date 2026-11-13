[Architektur-Karte — Wien ÖPNV Feed](architecture.md), Teil 12.

### Überwachung: Abruf kaputt oder keine Züge (seit 2026-10-10)

Betreiberentscheidung (Michael, 2026-10-10): „Der Check soll nur auf
rot, wenn der Abruf nicht funktioniert. Wenn die Technik funktioniert
gehört er auch auf grün. Wenn wirklich keine Züge fahren, gehört dies
auf der Homepage gemeldet, aber der Check bleibt grün.“

Vorher wurde ein komplett ausgefallener Monitor nirgends rot: Die
Richtungsprüfung im Health check wird nie rot (Entscheidung 2026-10-03,
#1939), und ein Korridor ohne Ledger-Zeilen sieht aus wie eine Nacht.
Die Ledger halten nur Züge fest, sie können „kein Zug fuhr“ und „wir
haben nichts gesehen“ nicht unterscheiden. Deshalb schreibt der Monitor
jeden Abruf, auch den gescheiterten, nach
`cache/stammstrecke/poll_status.json` (letzter Versuch und sein
Ergebnis, letzter erfolgreicher Abruf, Abfahrten und Stammstrecken-Züge
darauf, letzter Abruf mit Zug). Die Regel steht einmal in
`src/utils/stammstrecke_poll.py`:

* **Abruf kaputt** heißt: seit `POLL_STALE_HOURS` (3 h) kein
  erfolgreicher Abruf, oder kein lesbarer Status. Darunter fallen ein
  gescheiterter Request (HTTP-Fehler, Timeout), eine unlesbare Antwort
  (kein JSON, `errorCode`-Hülle), eine **unvollständige** Antwort (die
  Tafel listet Abfahrten, aber keine trägt Linie, Planzeit und
  Bahnsteig; strukturell, ohne Wortliste), ein übersprungener Abruf
  (Tageslimit, Circuit Breaker) und ein Monitor, der gar nicht mehr
  läuft. Drei Stunden: Der längste Abstand zwischen zwei Abrufen vom
  09.09. bis 10.10.2026 war 2:00 h (1 492 Abrufe, rekonstruiert aus dem
  VAO-Zähler in der Git-Historie). Ein einzelner gescheiterter Abruf
  macht noch nichts rot.
* **Abruf funktioniert, keine Züge** heißt: eine gültige Antwort ohne
  Stammstrecken-Zug, auch eine leere Tafel. Beispiel 22.09.2026 21:00:
  29 Abfahrten am Hbf, 16 auf anderen Bahnsteigen, kein Zug auf Gleis
  1/2; der Monitor-Schritt lief erfolgreich.

Wer die Regel liest:

* `scripts/health_check.py`, Prüfung `Stammstrecke-Abruf`: rot nur im
  ersten Fall; im zweiten grün mit „derzeit kein Stammstrecken-Zug auf
  der Tafel“. Kein ℹ️-Hinweis, weil die Nachtpause sonst jede Nacht
  einen erzeugte. Die Richtungsprüfung bleibt unverändert nie rot.
* Website (`docs/assets/site-js/`) und README/Statistik
  (`render_direction_coverage_note` in
  `scripts/generate_markdown_stats.py`): Den ganzen Korridor („Aktuell
  keine Fahrten von Wien Hbf Richtung Meidling und Praterstern“) nennen
  sie nur, solange der Abruf funktioniert (`fetch_working`;
  `coverage.poll` in `docs/stats-summary.json`, im Browser gegen die
  Uhr des Lesers geprüft). Hinter einem kaputten Abruf wäre der Satz
  eine Behauptung, die der Monitor nicht machen kann; der Fall gehört
  dem Health check. Die Schwellen des Hinweises (eine Stunde je
  Richtung mit Nachweis aus der Gegenrichtung, acht Stunden für den
  ganzen Korridor) bleiben, ebenso der Hinweis für eine einzelne stille
  Richtung.

Seit 2026-10-28 schreibt der Monitor zu einem gescheiterten Abruf den Grund
in Log und `poll_status.json` (`last_error`), nicht nur den Namen der
Ausnahme: etwa `ValueError: Invalid Content-Type: text/html (expected
('application/json',))` oder die `errorCode`-Hülle mit ihrem `errorText`.
Am 21.10.2026 von 17:31 bis 20:01 und ab 28.10.2026 15:00 stand dort nur
„ValueError“, eine falsche Antwortart, eine `errorCode`-Hülle und ein
kaputter Text sahen gleich aus. `describe_failure` in
`src/providers/vor.py` schickt die Meldung durch den VOR-Filter, der den
`accessId` selbst und jeden Parameter mit Zugangsnamen maskiert, und kürzt
sie auf 240 Zeichen; der Feed-Wächter und der Health check zeigen sie mit.
Dieselbe Funktion nutzen die CI-Probe `scripts/verify_vor_access_id.py`
(dort stand am 28.10.2026 nur „HTTPError“, ein 403 und ein 503 sahen gleich
aus) und der frühere `/trip`-Monitor.

### Überwachung: Nebenschritte (seit 2026-10-28)

Betreiberentscheidung (Michael, 2026-10-28, auf die Frage, ob ein
Nebenschritt, der nicht laufen kann, den Health check rot machen soll):
„Ich tendiere zu ‚er soll rot werden‘, denn ein Fehler sollte auffallen.“
Für Nebenschritte ersetzt das die Regel vom 2026-10-10 („rot nur, wenn der
Abruf nicht funktioniert“, #1939, #1999). Die Schwellen für Quellen bleiben:
Die Stammstrecke wird erst nach drei Stunden ohne erfolgreichen Abruf rot,
die Richtungsprüfung nie, ein Inhaltsfund des Feed-Wächters bleibt ein
Hinweis.

Nebenschritte sind die Schritte mit `continue-on-error`, die die
Veröffentlichung nie aufhalten dürfen: Statistik
(`generate_markdown_stats.py`), Linienprüfung (`check_feed_lines.py`) und
Feed-Wächter (`feed_watch.py`) im Update-Lauf, ÖBB-Stationslinien
(`update_oebb_station_lines.py`) im Stationslauf, seit 2026-10-31 die
Repository-Metadaten (`REPO_ADMIN_TOKEN`) im SEO-Lauf (siehe „Werkzeuge“
unten) und der tägliche KI-Wächter (`ki-waechter.yml`, Grenze 36 h); seit 2026-11-12 meldet der Feed-Build seine KI-Entscheidung selbst als Nebenschritt (Ausgabe `ki_outcome`, Teil 16, §14). Bis 2026-10-28 hinterließ
ihr Scheitern nur eine orange Annotation in einem grünen Lauf, und GitHub
schickte keine Mail. Gemessen ist keiner gescheitert (Update-Läufe seit
01.09.2026, Stationsläufe seit 01.01.2026), der Mangel war latent.

* Der letzte Schritt vor der Veröffentlichung (`Record side-step outcomes`)
  ruft `scripts/record_side_steps.py` mit dem Ergebnis jedes Nebenschritts
  auf. Er liest `steps.<id>.outcome`, nicht `conclusion`: Unter
  `continue-on-error` heißt die `conclusion` eines gescheiterten Schritts
  `success`. Das Ergebnis landet in `data/side_steps/update-cycle.json` bzw.
  `data/side_steps/update-stations.json` (eine Datei je Workflow, damit zwei
  gleichzeitige Veröffentlichungen nie dieselbe Datei schreiben) und fährt
  im Commit des Laufs mit. Ein übersprungener oder abgebrochener Schritt
  ändert seinen Eintrag nicht.
* Die Prüfung „Nebenschritte“ des Health checks (`scripts/health_check.py`,
  Code in `src/health_check/outputs.py`) ist rot, solange
  der letzte Lauf eines Nebenschritts scheiterte, und nennt Schritt,
  Zeitpunkt, letzten sauberen Lauf und den Link zum Lauf. Rot ist sie auch,
  wenn eine Aufzeichnung älter ist als die Grenze ihres Workflows (4 h für
  den Update-Lauf, 192 h für den Stationslauf): Dann konnte die
  Aufzeichnung selbst nicht laufen. Fehlt eine Datei noch (vor dem ersten
  Lauf mit der Aufzeichnung), ist das ein Hinweis.
* Ausnahme seit 2026-11-04 (Michael, Karte „Ja, so bauen“): Bekam der
  KI-Wächter nur deshalb keine Antwort, weil Gemini überlastet war
  (Ergebnis `overloaded`: 500/502/503/504, abgebrochene Verbindung,
  Zeitüberschreitung, 429), ist das ein Hinweis mit der Uhrzeit, ab der es
  rot wird: 30 Stunden nach der letzten gelungenen Prüfung
  (`KI_WAECHTER_OVERLOAD_MAX_H`, `HEALTH_KI_WAECHTER_OVERLOAD_HOURS`), ohne
  gelungene Prüfung 30 Stunden nach dem ersten Fehlschlag (`failing_since`,
  das `record_side_steps.py` beim ersten Fehlschlag nach einem sauberen Lauf
  setzt). Anlass: Vom 01. bis 04.11.2026 antwortete Gemini um 9:00 jeden Tag
  mit 503, 34 Health-check-Läufe waren rot, obwohl der Feed lief und der
  zweite Versuch um 11:31 zweimal durchkam; nachgespielt wäre keiner davon
  rot gewesen. Ein abgelehnter Schlüssel oder ein Fehler im Code bleibt
  sofort rot, so fällt eine Stufe, die wirklich nicht läuft, weiter auf.
  Dieselbe Regel gilt seit 2026-11-04 für die Repository-Metadaten des
  SEO-Laufs (Karte „Beides“): 5xx oder keine Verbindung zu GitHub nach zwei
  Wiederholungen ist `overloaded`, Hinweis bis 48 Stunden ohne gelungenen Lauf
  (`SEO_OVERLOAD_MAX_H`, `HEALTH_SEO_OVERLOAD_HOURS`); siehe
  [„Werkzeuge“](architecture-teil-12.md).
* Die Prüfung `Feed-Wächter` ist rot, wenn der Wächter nicht prüfen konnte
  (Feld `error` in `data/feed_watch.json`); er endet in diesem Fall mit
  Exit-Code 0, damit die Veröffentlichung weiterläuft, und sein Schritt
  zählt deshalb als sauber.
* Der Statistik-Schritt endet ab dem 04.01. in jedem Tick mit 1, solange
  einem abgeschlossenen Jahr der angenommene Rückblick der Baustellen fehlt
  (`--fail-on-missing-review`, nur im Update-Lauf; Teil 15, §12).

### Überwachung: Veröffentlichung (seit 2026-10-31)

Anlass war der Architektur-Review vom 2026-10-31 (Michael: „Ja, A bauen“ auf
der Karte „Soll der Health check die Live-URL der Feeds gegen das Repo
prüfen?“). Die Kette endet nicht bei `docs/feed.xml` im Repository, sondern
am Fernseher: Zwischen dem Commit und der Anzeige liegt der Pages-Build von
GitHub (`pages-build-deployment`, Jekyll mit dem Theme aus
`docs/_config.yml`). Jede andere Prüfung liest den Checkout; ein Pages-Build,
der nicht mehr ausliefert, ließ den Fernseher auf dem alten Stand, während
Update-Lauf, Health check und Feed-Wächter grün blieben. Den einzigen Blick
auf die Live-Adresse hatte der SEO-Lauf einmal am Tag, als Warnung mit
Exit-Code 0 und ohne das Datum zu vergleichen.

Die Prüfung `Veröffentlichung` (`check_published_feeds` in
`scripts/health_check.py`) holt `feed.xml` und `feed.en.xml` von der
Pages-Adresse (`PAGES_BASE_URL`, aus dem Repository-Namen abgeleitet wie in
`build-feed.yml`; über `request_safe`, höchstens 2 MB, nur XML) und
vergleicht ihr `<lastBuildDate>` mit der Kopie im Checkout. Nach der
Alarmlinie unten:

* **Rot**, wenn eine Live-Kopie mehr als 4 Stunden hinter der Repo-Kopie
  liegt (`HEALTH_LIVE_LAG_MAX_HOURS`): Pages liefert anhaltend nicht aus. Die
  Meldung nennt beide Stände.
* **Grün**, wenn Live und Repo gleich sind oder die Live-Kopie neuer ist (ein
  Update-Lauf hat seit dem Checkout dieses Laufs veröffentlicht) oder nur
  Minuten dahinter (der Pages-Build braucht nach dem Commit ein bis zwei
  Minuten; gemessen am 2026-10-31: 65 Sekunden).
* **Hinweis**, wenn der Live-Abruf gerade nicht gelingt oder die Live-Kopie
  kein `<lastBuildDate>` trägt: ein einzelner Abruf sagt nichts über die
  Dauer, und bei einer Pages-Störung bleibt die letzte Auslieferung in der
  Regel erreichbar, sodass der veraltete Stand der Fall ist, der zählt. Eine
  Repo-Kopie, die nicht lesbar ist, ist in `Feed-Build` schon rot und hier nur
  ein Hinweis, damit sie nicht doppelt zählt.

### Überwachung: Pages-Build (seit 2026-11-01)

Betreiberentscheidung (Michael, 2026-11-01, Karte „Soll ein gescheiterter
Pages-Build künftig sofort auffallen?“, Antwort „Beides bauen“). Anlass: Ab
dem Merge von #2092 um 08:43 Uhr Wiener Zeit hielt ein Liquid-Tag in
`docs/reference/ki_waechter.md` jeden Lauf von „pages build and deployment“
an (bis 11:13 Uhr Wiener Zeit 22 gescheitert, 4 abgebrochen). Der Fernseher
blieb auf dem Stand von 08:30 Uhr Wiener Zeit, die Update-Läufe blieben grün,
und der Health check meldete den Ausfall nicht (rot war er ab 10:01 Uhr
Wiener Zeit nur wegen des gescheiterten KI-Wächters, Prüfung
`Nebenschritte`); `Veröffentlichung` hätte erst bei vier Stunden Rückstand
gegen 13:00 Uhr Wiener Zeit angeschlagen. Beides gilt seither:

* **Health check, Prüfung `Pages-Build`** (`check_pages_build` in
  `scripts/health_check.py`). Sie liest über die Actions-API (`actions:
  read`, eigenes `GITHUB_TOKEN` des Laufs) die letzten 20 abgeschlossenen
  Läufe des Pages-Workflows; solange keiner davon sauber war oder zwischen
  dem neuesten und dem neuesten sauberen eine Laufnummer fehlt, bis zu 40
  weitere. Ein Lauf, den zwei Seiten listen, zählt einmal. Er hat keine
  Datei im Repository; seine Id findet sie über den
  Pfad `dynamic/pages/pages-build-deployment`. Läufe, die GitHub abbricht,
  weil ein neuerer Commit kam (165 von 1.972 vom 01.10. bis 01.11.2026),
  zählen nicht. Der letzte übrige Lauf nach **Laufnummer** entscheidet,
  nicht nach GitHubs Zeitstempel: Am 01.11. gab die Actions-API allen
  Läufen, die zwischen 08:00 und 08:59 UTC begannen, eine Stunde zu späte
  Zeiten (Ende der US-Sommerzeit) und listete sie vor neueren Läufen. Eine
  Zeit nach der Prüfung selbst kann nicht stimmen und bleibt weg. Dasselbe
  gilt für die Prüfung `Werkzeuge`.
  * `success`: grün.
  * Gescheitert im Schritt „Build with Jekyll“ (außer an GitHubs API,
    nächster Punkt): **sofort rot**. Der Inhalt unter `docs/` lässt sich
    nicht bauen, und jeder weitere Lauf scheitert gleich, bis die Seite
    korrigiert ist. Die Meldung nennt den Link zum
    Lauf und, solange der erste gescheiterte Lauf unter den gelesenen (bis
    zu 60) liegt, seit wann und wie viele Läufe in Folge und den Commit,
    nach dem der Build zuerst scheiterte; sonst „mindestens N Läufe in
    Folge“. 20 Läufe reichten im Oktober 2026 im Mittel etwa acht Stunden
    zurück, am 01.11. um 11:02 Uhr Wiener Zeit wegen der vielen Merges
    nicht bis zum Beginn um 08:43 Uhr Wiener Zeit.
  * Gescheitert im Schritt „Build with Jekyll“, aber an GitHubs API (die
    Fehlermeldung des Schritts, gelesen über die Annotationen des Jobs mit
    `checks: read`, nennt einen HTTP-Fehler von `api.github.com` oder
    ungültige Zugangsdaten): GitHubs Seite wie im nächsten Punkt. Das Gem
    `jekyll-github-metadata` fragt die API bei jedem Build und fängt nur
    Ratenlimit, 404 und Verbindungsabbruch ab; so scheiterte am 23.05.2026
    um 12:01 Uhr Wiener Zeit ein einzelner Lauf (ungültige Zugangsdaten, der
    nächste baute fünf Minuten später) und in der Nacht auf den 17.07.2026
    von 00:31 bis 01:31 Uhr Wiener Zeit drei in Folge (HTTP 503). Ist die
    Meldung nicht lesbar, bleibt es sofort rot.
  * Gescheitert an anderer Stelle (Image laden, Upload, Auslieferung):
    GitHubs Seite. Ein einzelner Lauf ist ein **Hinweis**, rot wird es,
    wenn auch der Lauf davor scheiterte. Das folgt der Alarmlinie unten
    (ein einzelner Aussetzer schickt keine Mail): Im Oktober 2026 scheiterte
    die Auslieferung viermal, jedes Mal einzeln, und der nächste Lauf
    lieferte wieder aus.
  * Gescheitert, aber die Jobs des Laufs sind nicht lesbar: rot.
  * Actions-API antwortet nicht, kein Pages-Workflow, kein abgeschlossener
    Lauf: Hinweis.

  Nachgerechnet über die Pages-Läufe vom 01.10. bis 01.11.2026, in der
  Reihenfolge, in der die API sie listete, mit einer Prüfung je Stunde (wie
  seit dem 28.10.): rot nur am 01.11. um 9, 10 und 11 Uhr Wiener Zeit, dazu
  drei Hinweise für die Auslieferungsfehler vom 05.10. und 08.10.; die vom
  14.10. und 20.10. endeten erst nach der Prüfung ihrer Stunde und waren vor
  der nächsten behoben. Zu den Zeiten der echten Health-check-Läufe am
  01.11. wäre `Pages-Build` um 09:01, 10:01 und 11:02 Uhr Wiener Zeit rot
  gewesen, jedes Mal mit dem Merge von #2092 als Beginn. Mit den echten
  Läufen der beiden API-Ausfälle: am 23.05. ein Hinweis, am 17.07. ein
  Hinweis nach dem ersten und rot ab dem zweiten gescheiterten Lauf, bis der
  vierte wieder baute. `Veröffentlichung` bleibt als Rückfall für einen
  Ausfall ohne gescheiterten Lauf (Pages abgeschaltet, ein Lauf, der nie
  startet).
* **Pull Requests, Workflow `pages-build-check.yml`.** Jeder Pull Request
  mit Änderungen unter `docs/` baut die Website mit derselben Action wie
  der Pages-Lauf (`actions/jekyll-build-pages` 1.0.13, Jekyll 3.10 mit dem
  Gem `github-pages`, Quelle `./docs`) und liefert nichts aus. Ein Fehler
  zeigt sich als roter Check am PR, bevor er `main` erreicht (der Check ist
  kein Pflicht-Check im Ruleset). Er fängt, was den Build anhält; gibt eine
  Vorlage nur still etwas anderes aus (eine unbekannte Variable, ein
  unbekannter Filter), bleibt er grün. Endet die erste Backtrace-Zeile im
  Protokoll des Schritts „Build with Jekyll“ mit `(Octokit::…)`,
  `(Faraday::…)` oder `(Jekyll::GitHubMetadata::Client::BadCredentialsError)`
  (ungültige Zugangsdaten; das Gem meldet `Octokit::Unauthorized` unter
  dieser Klasse, das Original steht weiter unten als Ursache), lag es an
  GitHubs API, auch wenn darüber `Liquid Exception` steht
  (`jekyll-github-metadata` fragt die API vor dem Rendern und noch einmal im
  ersten Layout); ein neuer Lauf behebt es. Die Annotation am Check nennt
  statt der Klasse den HTTP-Fehler von `api.github.com` oder die ungültigen
  Zugangsdaten. Seiten, die Workflows
  selbst nach `main` schreiben, erreichen keinen Pull Request (ein Push mit
  dem `GITHUB_TOKEN` startet außer dem Pages-Build keinen Workflow); für sie
  gilt der Liquid-Schritt in `scripts/publish_to_main.sh`
  ([`development.md`](development.md), „Veröffentlichen auf `main`“).

### Überwachung: Werkzeuge (seit 2026-10-31)

Fund B des Architektur-Reviews vom 2026-10-31 (Michael: „ja, bau das
bitte“): Zwei Werkzeug-Secrets konnten still sterben. `claude-code-review.yml`
ließ den Review-Schritt mit `continue-on-error` laufen (Entscheidung vom
2026-05-30 gegen rote Checks bei Aussetzern), sodass ein abgelaufener
`CLAUDE_CODE_OAUTH_TOKEN` nur einen grünen Check ohne Kommentare hinterließ;
`seo-guard.yml` beendete den Schritt für Beschreibung und Homepage des
Repositorys bei leerem `REPO_ADMIN_TOKEN` mit einer Zeile auf stderr und
Exit 0. Die Prüfung `Secrets` sieht beide nicht, weil sie als `tooling`
nicht an den Health check gehen ([„Nebenschritte“](architecture-teil-12.md#überwachung-nebenschritte-seit-2026-10-28)
gilt seither auch für sie, Regel vom 2026-10-28: „ein Fehler sollte auffallen“).

* **Repository-Metadaten (`seo-guard.yml`).** Der Schritt scheitert bei
  leerem Token und bei einer abgelehnten Anfrage (jeder HTTP-Code außer
  2xx, etwa 401 bei abgelaufenem Token), läuft mit `continue-on-error` vor der
  Veröffentlichung, und `record_side_steps.py` schreibt sein Ergebnis nach
  `data/side_steps/seo-guard.json`, das mit `sitemap.xml` und `llms.txt` im
  selben Commit mitfährt. Die Prüfung `Nebenschritte` ist rot, solange der
  letzte Lauf scheiterte oder die Aufzeichnung älter als 48 Stunden ist
  (`HEALTH_SEO_MAX_AGE_HOURS`; der tägliche Cron kommt Stunden verspätet,
  zwei Tage ohne Lauf sind ein Ausfall). Seit 2026-11-04 (Michael, Karte
  „Beides“; Anlass: ein 502 von GitHub um 11:03 Wien während eines
  GitHub-Ausfalls, im Log als „Token abgelaufen“ gemeldet) unterscheidet der
  Schritt: Eine Störung bei GitHub (5xx oder keine Verbindung) wiederholt er
  nach 30 und 90 Sekunden; hält sie an, zählt er als `overloaded`, und die
  Prüfung bleibt ein Hinweis, bis 48 Stunden kein Lauf gelang
  (`HEALTH_SEO_OVERLOAD_HOURS`); den nächsten Lauf (Merge mit Änderung unter
  `docs/` oder täglicher Cron) heilt es von selbst. 401, 403, 404 und 422
  bleiben sofort rot und nennen den HTTP-Code.
* **Code-Review (`claude-code-review.yml`).** Der Workflow läuft auf Pull
  Requests und kann nichts nach `main` schreiben, deshalb gibt es keine
  Aufzeichnung. Stattdessen darf der Review-Schritt seit 2026-10-31
  scheitern (kein `continue-on-error` mehr): Der Check am PR wird rot (er ist
  kein Pflicht-Check im Ruleset und hält keinen Merge auf), und die neue
  Prüfung `Werkzeuge` (`check_tooling_runs`) liest über die Actions-API
  (`actions: read`, eigenes `GITHUB_TOKEN` des Laufs) das Ergebnis des
  letzten abgeschlossenen Laufs, der lief (nach Laufnummer, siehe
  „Pages-Build“; übersprungene und abgebrochene Läufe zählen nicht, etwa die
  von Dependabot, am 01.11. neun in Folge): `success` grün, alles andere
  rot mit Link zum Lauf, bis ein Review wieder lief. Nur wenn keiner der
  letzten 30 Läufe lief, ist das ein Hinweis.
  Pull Requests aus Forks und von Dependabot haben keine Secrets und werden
  wie bisher übersprungen. Eine Actions-API, die nicht antwortet, ist ein
  Hinweis, denn sie sagt nichts über das Werkzeug.

### Alarmwege: rot, Issue, Hinweis (seit 2026-10-28)

Betreiberentscheidung (Michael, 2026-10-28, Karte „Linie, Issue als
Frühwarnung“, auf seine Bitte „Finde eine klare Linie, damit dies in der
Zukunft ein klares System hat“). Jede Prüfung beantwortet genau eine der
drei Fragen und hat damit genau einen Weg:

| Weg | Frage | Wer meldet | Fälle |
| --- | --- | --- | --- |
| **Rot** (GitHub-Mail) | Arbeitet die Technik nicht, und zwar anhaltend? | Health check (`scripts/health_check.py`), stündlich | Eine Quelle (WL, ÖBB, Baustellen, Stammstrecke) gab den Update-Läufen seit **3 h** keine brauchbare Antwort; der Feed ist seit **4 h** nicht neu gebaut; die veröffentlichten Feeds auf GitHub Pages liegen seit **4 h** hinter dem Repo; der letzte Pages-Lauf scheiterte im Jekyll-Build (sofort) oder zwei Pages-Läufe in Folge scheiterten bei GitHub; der Stationslauf ist älter als 8 Tage, ein Teilschritt scheiterte oder die Validierung fand Sicherheitsprobleme; eine Quelle des Stationslaufs antwortete seit 15 Tagen nicht; ein Nebenschritt scheiterte im letzten Lauf oder seine Aufzeichnung ist veraltet; der SEO-Lauf konnte die Repository-Metadaten nicht setzen (`REPO_ADMIN_TOKEN`); der letzte Lauf des Code-Reviews scheiterte (`CLAUDE_CODE_OAUTH_TOKEN`); der Feed-Wächter konnte nicht prüfen; das Übersetzungsmodell lud in zwei Builds in Folge nicht (seit 2026-11-01); IFTTT schweigt, und auch der Takt-Fallback brachte seit **80 min** keinen neuen Build (Prüfung `Takt`, seit 2026-11-04; diese Läufe startet der Bot, die Mail kommt deshalb über den Kommentar im Issue `ifttt-ausfall`, siehe unten); ein Pflicht-Secret ist leer. |
| **Issue** (Lauf bleibt grün) | Arbeitet die Technik, aber Feed-Inhalt oder Quelle sehen falsch aus? | Feed-Wächter (`scripts/feed_watch.py`), jeder Update-Lauf; je Regel ein Issue, das sich nach zwei sauberen Läufen selbst schließt | Die Inhaltsregeln (doppelter Eintrag, Anzahl, alte Meldung als neu, „Seit“ zu früh, ÖBB-Zweitsatz, Zeitzeile, Zeit vorbei, Länge, Höflichkeitssatz) und die Quellenregeln (`quelle-leer`, `quelle-einbruch`, `verwurf-sprung`, seit 2026-10-29 `vao-version`: die automatisch gewählte VAO-Version hat gewechselt); seit 2026-11-10 `meldung-uebersprungen`: der Bau hat eine Meldung übersprungen, an der die Feed-Kette scheiterte (§10). |
| **Hinweis** (ℹ️, `::warning`) | Ist die Lage bekannt und verlangt nichts? | Health check | Eine stille Stammstrecken-Richtung, ein Korridor ohne Züge (die Website meldet ihn), eine gescheiterte Stichprobe, während die Update-Läufe noch Antworten bekommen, ein Live-Abruf der veröffentlichten Feeds, der gerade nicht gelang, ein einzelner Pages-Lauf, dessen Auslieferung bei GitHub oder dessen Jekyll-Build an GitHubs API scheiterte, eine Aufzeichnung, die vor dem ersten Lauf noch fehlt, IFTTT schweigt, während der Takt-Fallback den Feed aktuell hält (seit 2026-11-04). |

**Frühwarnung:** Drei Technikfälle melden zusätzlich früher ein Issue, bevor
sie rot werden: `quelle-ohne-antwort` nach 2 h (rot nach 3 h),
`feed-alter` nach 90 min (rot nach 4 h) und seit 2026-11-04 `ifttt-ausfall`:
Kam 40 Minuten lang kein Update-Lauf, startet der Takt-Fallback
(`.github/workflows/takt-fallback.yml`) ihn selbst, alle 40 Minuten, und
öffnet das Issue; der nächste IFTTT-Lauf schließt es. Gewartet wird ohne
Rechner (Wartezeit der Umgebung `takt-wartezeit`). Rot wird
erst, wenn auch der Fallback seit 80 Minuten keinen neuen Build brachte.
Diese roten Läufe startet der Bot, und GitHub mailt einen roten Lauf nur
dem, der ihn startete; deshalb kommentiert der Fallback das Issue, sobald er
scheitert, und noch einmal, wenn er wieder läuft (Michael, 2026-11-04,
Karte „Ja, Kommentar im Issue“).
Betreiberentscheidung (Michael, 2026-11-04): „Sorge aber bitte dafür, dass
der Fallback wirklich nur läuft, wenn IFTTT nicht funktioniert hat. Wenn
IFTTT nicht funktioniert erstelle einen Issue. Wenn der Fallback auch nicht
funktioniert, und der Feed nicht mehr aktualisiert werden kann, mach den
Health Check rot.“ Andere Technikfälle bekommen kein Issue. Ein Inhaltsfund wird nie rot.

**Für eine neue Prüfung:** Fragt sie, ob die Technik läuft, wird sie rot,
mit einer Dauerschwelle, damit ein einzelner Aussetzer keine Mail
auslöst. Fragt sie, ob das Ergebnis stimmt, wird sie eine Feed-Wächter-Regel
mit Issue. Beschreibt sie eine bekannte Lage ohne Handlung, wird sie ein
Hinweis. Die Frühwarnung bleibt den Technikfällen vorbehalten, die später
rot werden.

Umgesetzt mit dieser Entscheidung:

* **Gleiche Regel für alle Quellen.** Vorher wurden WL, ÖBB und Baustellen
  nur rot, wenn die eigene Stichprobe des Health checks im Moment seines
  Laufs scheiterte: ein einzelner Aussetzer reichte, ein siebenstündiger
  Ausfall (WL, 21.10.2026) wurde einmal rot, weil zufällig ein Lauf in ihn
  fiel. Jetzt entscheidet, wann die Update-Läufe zuletzt eine brauchbare
  Antwort bekamen (Exit-Code 0 des Abrufs, festgehalten vom Feed-Wächter in
  `data/feed_watch.json` → `fetch`, `assess_source` in
  `scripts/health_check.py`). Die Stichprobe entscheidet allein nur noch,
  wenn dieser Stand nicht taugt (fehlt, veraltet, Wächter mit Fehler).
* **Stündlicher Health check.** GitHub stellt den Cron des Health checks
  unzuverlässig zu: Am 28.10.2026 lief er zwischen 13:19 und 23:19 nicht,
  der Stammstrecken-Ausfall von 15:00 bis 23:00 wurde deshalb nie rot.
  `health-check.yml` startet jetzt zusätzlich nach jedem Update-Lauf
  (`workflow_run`); die Bedingung des Jobs `health` lässt jeden zweiten
  durch (gerade Laufnummer des Update-Laufs). Bis 2026-11-05 tat das ein
  Job `gate` nach der Uhr (UTC-Minute unter 30) und startete dafür 48-mal
  am Tag einen Rechner; nachgespielt über die 1.607 Update-Läufe vom 05.10.
  bis 04.11.2026 gibt die Laufnummer im Median 26 statt 27 Prüfungen am Tag,
  denselben größten Abstand (120 Minuten, einmal) und keine zwei Prüfungen
  binnen 20 Minuten mehr (die Uhr gab 67, nach einem Backup-Cron um :17).
  Der Cron bleibt als Rückfall, falls der Update-Lauf ausfällt. Während
  eines anhaltenden Ausfalls kommt jede Stunde eine Mail.

Rückprüfung 01.09.–28.10.2026 (2 776 Update-Läufe mit Abruf, 1 388
simulierte stündliche Prüfungen): Nach der neuen Regel wären es zehn rote
Stunden gewesen, alle in den zwei echten langen Ausfällen: WL am 21.10.
von 19:00 bis 23:00 und die Stammstrecke am 28.10. von 18:00 bis 22:00. Eine
stündliche Stichprobe nach der alten Regel hätte 29-mal rot gemeldet, davon
24-mal für Aussetzer, die der nächste Update-Lauf schon hinter sich hatte
(ÖBB 15-mal, WL 8-mal, Baustellen einmal).

**Nächtliche Betriebspause** (auf Michaels Hinweis „Vergiss die
Betriebspause in der Nacht nicht“, 2026-10-28): Sie macht keine Prüfung
rot, denn jede Regel misst die Antwort der Quelle, nie die Zahl der Züge
oder Meldungen. Nachgemessen über die Läufe seit September: Die
Stammstrecken-Abfrage bekam auch nachts alle 30 Minuten eine Antwort; von
01:00 bis 03:00 war die Tafel 35-mal leer, was nach der Entscheidung vom
2026-10-10 ein funktionierender Abruf ist (die Website nennt den Korridor
ohne Züge). Die Update-Läufe kamen in 58 Nächten ohne Lücke über 45
Minuten. WL und ÖBB lieferten nie eine leere Liste (mindestens 68 bzw. 9
Meldungen im Cache). Eine leere Liste zählt beim Abruf als unbrauchbar
(Exit 1) und würde nach 3 h rot; das passt, weil beide Quellen auch nachts
Dauermeldungen führen. Die Richtungsprüfung der Stammstrecke vergleicht die
Richtungen miteinander und ist deshalb nachts ruhig (siehe oben). Fällt eine
Quelle nachts wirklich aus, kommt die Mail wie tagsüber jede Stunde.

---

Weiter in [Teil 13](architecture-teil-13.md).
