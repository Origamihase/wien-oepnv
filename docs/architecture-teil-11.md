[Architektur-Karte — Wien ÖPNV Feed](architecture.md), Teil 11.

## 7. VOR/VAO-API — Stammstrecke-only-Scope (2026-05-11)

Die VOR/VAO-ReST-API erlaubt **100 Requests pro Tag** (`VAO Start`-
Kontingent). Seit 2026-05-11 (Operator-Policy „VOR nur noch für die
Verspätungen der Stammstrecke") ist der Stammstrecken-Monitor der
**dominierende** automatisierte VOR-Konsument im Projekt; daneben
steht nur noch der CI-Smoke-Test in `test-vor-api.yml` (siehe
Tabelle). Der frühere
wöchentliche Station-Enrichment-Pfad und das optionale
Disruption-Polling sind aus den automatisierten Pfaden entfernt;
die zugehörigen Helper-Skripte (`update_vor_cache.py`,
`update_vor_stations.py`, `fetch_vor_haltestellen.py`) wurden
2026-05-11 aus `scripts/` gelöscht. VOR-Stop-IDs kommen jetzt
ausschließlich aus dem gepinnten
`data/vor-haltestellen.csv`-Snapshot. Seit 2026-05-15 verbraucht der
Monitor zudem nur noch **48 Calls/Tag** (vorher 96): das aktive
`scripts/update_stammstrecke_hbf.py`-Skript setzt pro Cron-Tick
**eine** `/departureBoard`-Query am Wien Hauptbahnhof ab statt zwei
`/trip`-Queries (Floridsdorf ↔ Meidling). Siehe
[`docs/reference/stammstrecke_provider_logic.md`](reference/stammstrecke_provider_logic.md)
für Details zum Stammstrecken-Monitor.

| Konsument | Default-Calls/Tag | Konfigurierbar |
| :--- | ---: | :--- |
| **Stammstrecke `/departureBoard`** (ca. alle 30 Min × 1 Hbf-Call) | 48 | IFTTT-Cadence des `update-cycle.yml`-Triggers |
| **CI-Smoke-Test `location.name`** (`test-vor-api.yml`) | **0–4** | feuert nur bei Push auf VOR-Pfade; Pre-flight-Gate (Reserve 1), eigene Concurrency-Gruppe |
| **Station-Enrichment `location.name`** | **0** (Pfad und Skript 2026-05-11 entfernt) | — |
| **Disruption-Polling `departureBoard`** | **0** (Pfad und Skript 2026-05-11 entfernt) | — |
| **Auth-Diagnose** (`verify_vor_access_id.py`, `check_vor_auth.py`) | **0–2** | nur manueller Operator-Aufruf, einmalige Smoke-Tests |
| **Tagesbudget gesamt** | **48–54 / 100** | — |

> **Korrektur 2026-09-13 (Audit A.1/F.1).** Diese Tabelle führte den
> CI-Smoke-Test zuvor nicht auf und schrieb `location.name` pauschal auf
> **0** Calls/Tag. Tatsächlich setzte `test-vor-api.yml` bei jedem Push auf
> `scripts/**` (ein Filter, der ~40 Dateien traf) einen echten
> `location.name`-Request per rohem `curl` ab — an `save_request_count`
> vorbei, ohne Pre-flight-Gate und außerhalb der
> `external-api-fetch`-Concurrency-Gruppe. Der Verbrauch war damit real,
> aber für den Zähler unsichtbar. Behoben am 2026-09-13: Pfad-Filter auf die
> VOR-relevanten Skripte verengt, Concurrency-Gruppe ergänzt, Pre-flight-Gate
> davorgeschaltet und der `curl` durch `scripts/verify_vor_access_id.py`
> ersetzt, das den Request über `reserve_request_slot()` bucht.

Zwei zusammenwirkende Mechanismen schützen das Budget:

1. **Keine automatisierten Nicht-Stammstrecke-Pfade**: Weder
   `update-cycle.yml` noch `update-stations.yml` noch
   `manual-full-refresh.yml` rufen VOR-Disruption-Polling oder
   VOR-Station-Enrichment auf. Die früheren Helper-Skripte existieren
   nicht mehr; nur die Auth-Diagnose-Helfer
   (`scripts/verify_vor_access_id.py`, `scripts/check_vor_auth.py`)
   bleiben für gezielte manuelle Smoke-Tests verfügbar. Der einzige
   weitere automatisierte Pfad ist `test-vor-api.yml`: er ruft
   `verify_vor_access_id.py` auf, ist seit 2026-09-13 auf die
   VOR-relevanten Pfade verengt, hing bis 2026-11-03 in derselben
   `external-api-fetch`-Concurrency-Gruppe und hat seitdem eine eigene
   Gruppe je Zweig, damit er keinen wartenden Takt oder Rebuild auf `main`
   abbricht (die gemeinsame Gruppe schützte das Budget nicht, weil der
   Workflow den Zähler nie committet). Er wird vom selben
   Pre-flight-Gate abgeschaltet, sobald der Zähler keinen Slot mehr
   lässt.

2. **`_charge_one_request`** (definiert in
   `scripts/update_stammstrecke_status.py`, vom aktiven
   `update_stammstrecke_hbf.py` per Import wiederverwendet). Vor
   jedem `/departureBoard`-Call wird über
   `vor_provider.save_request_count` ein Quota-Slot reserviert; ein
   Lauf, der das Tagesbudget reißen würde, raised `_QuotaExceeded`
   *vor* dem Network-Call. Defense-in-Depth: `update-cycle.yml` und
   `manual-full-refresh.yml` (seit 2026-09-27, Audit A.6) schalten den
   Step zusätzlich über
   `scripts/preflight_quota_check.py --check vor --margin 1` aus, sobald
   der persistierte Counter keinen Slot mehr lässt.

Mit 48 von 100 Calls/Tag bleibt komfortabel ein Puffer von ca. 52
Calls für Operator-Direktaufrufe (`workflow_dispatch` auf
`update-cycle.yml`, manuelle Smoke-Tests). Sollte das Budget in
Zukunft enger werden, sind die niedrig hängenden Hebel:

* IFTTT-Applet von ca. 30-Min- auf Stunden-Cadence umstellen (halbiert
  die Stammstrecke auf 24 Calls/Tag).
* `DEPARTURE_BOARD_DURATION_MIN = 45` → kürzeres Fenster (kein
  API-Effekt, aber kleinere Antwort-Payloads).
* Operating-Hours-Cadence im IFTTT-Applet (nur 04–23 Uhr statt
  ganztägig) — die Stammstrecke fährt zwischen 00:30 und 04:00 nur
  ausgedünnt, das Monitoring liefert dort kaum Signal. Seit 2026-10-28
  hängen daran alle Dauerschwellen, nicht nur `POLL_STALE_HOURS` (unten):
  die drei Stunden aller vier Quellen und die vier Stunden Feed-Alter im
  Health check, `quelle-ohne-antwort` (2 h) und `feed-alter` (90 min) im
  Feed-Wächter. Ohne nächtliche Läufe startet auch der stündliche Health
  check nicht, und der Rückfall-Cron fände einen alten Stand. Jede dieser
  Regeln bräuchte eine Ausnahme für die Pause, sonst färbt sie den Health
  check jede Nacht rot („Alarmwege“ in diesem Abschnitt).

### Der VAO-„Start“-Vertrag (geprüft 2026-10-17)

Michael nutzt das kostenlose Paket „Start“ der Verkehrsauskunft Österreich
VAO GmbH; den Vertrag hat er am 17.10.2026 übergeben, sein Text liegt nicht
im Repository. Was er für dieses Projekt bedeutet:

* **Abfragen:** bis zu 100 pro Kalendertag (Punkt 1.3). Der Zähler
  `data/vor_request_count.json` rechnet nach Wiener Kalendertag, die beiden
  Schutzschichten oben halten den Monitor bei 48.
* **Nutzung:** produktiver Einsatz und das Einbetten der Abfrageergebnisse
  in den eigenen Dienst sind erlaubt (Präambel Punkt 4, Punkt 1.3); Rechte an
  den Daten selbst räumt die VAO nicht ein. Feed und Statistik zeigen deshalb
  nur eigene Auswertungen (Verspätungsepisoden, Ausfallzählungen), nie
  Rohantworten.
* **Zugangsdaten und Doku:** die Access-ID liegt nur im Secret
  `VOR_ACCESS_ID` und wird in Logs maskiert (Punkt 5.4); es gibt genau einen
  Zugang (Punkt 5.5). Das Handbuch stellt die VAO selbst öffentlich bereit;
  das Repository verlinkt es nur (`docs/development.md`).
* **Hinweis auf die Grundlage (Punkt 5.3):** Michael hat am 17.10.2026
  entschieden, den Satz „Stammstrecken-Daten: Grundlage ist der
  VAO-„Start“-Zugang der Verkehrsauskunft Österreich VAO GmbH.“ überall zu
  führen: im `<copyright>` beider Feeds (`_VAO_BASIS_DE`/`_VAO_BASIS_EN` in
  `src/feed_build/rss.py`), in der Fußzeile der Webseite (DE in
  `docs/site.html`, EN in `docs/assets/site-js/`), auf `docs/statistik.md`
  (`VAO_BASIS_NOTE` in `src/markdown_stats/markdown_report.py`), im README und in
  `NOTICE.md`. VAO ist dort Grundlage, kein Lizenzgeber: Die
  Stammstrecken-Meldung ist ein eigener Text. Der Quellen-Tag `<source>`
  VOR/VAO am Item bleibt. Endet der Vertrag, müssen diese Hinweise wieder
  entfernt werden (Punkt 7.7).
* **API-Version:** seit 28.10.2026 v1.12.0 (`DEFAULT_VERSION` in
  `src/providers/vor.py`), in Betrieb bis 30.08.2027. Michael, 24.10.2026: „Wir
  können die VAO-Version gerne anheben“. Gewählt ist die neueste Version, die
  die VAO unter `https://routenplaner.verkehrsauskunft.at/vao/restproxy`
  listet, weil sie am längsten läuft. Das war vom 24.10. an v1.13.0, bis die
  VAO sie am 28.10.2026 zwischen 14:30 und 15:00 zurückzog: Seitdem antwortet
  jede v1.13.0-Anfrage mit HTTP 404, und jeder Stammstrecken-Abruf scheiterte
  bis zum Wechsel. Dieselbe `/departureBoard`-Abfrage an v1.11.0, v1.12.0 und
  v1.13.0 lieferte am 24.10. dieselben Abfahrten und Beobachtungen. Vorgehen beim nächsten
  Umstieg: `docs/how-to/versions.md`, „Version anheben“.
* **Automatische Wahl der Version (seit 29.10.2026):** Michael, 29.10.2026:
  „Eine dynamische und automatische Wahl für ein Fallback klingt für mich
  besser“ (ein Wechsel per PR bräuchte jedes Mal seinen Merge) und „Lies
  automatisch aus, was die neuste Version ist. Wenn sie nicht funktioniert,
  nimm automatisch die davor“. Jeder Stammstrecken-Abruf liest zuerst die
  Versionsliste der VAO (ohne Access-ID, zählt nicht zum Tageskontingent) und
  fragt die neueste Version, deren Abschaltdatum noch nicht vorbei ist.
  Antwortet sie nicht brauchbar, fragt derselbe Abruf die nächstältere. Eine
  gescheiterte Version kommt 24 Stunden lang erst nach den anderen dran:
  Scheitert jede Version (abgelehnter Schlüssel, Ausfall der VAO), kostet ein
  Abruf so einen Platz im Kontingent, nicht einen je Version. Ist die Liste
  nicht lesbar, gilt die zuletzt funktionierende Version, sonst
  `DEFAULT_VERSION`. Die Wahl steht in `cache/stammstrecke/vao_version.json`
  und kommt mit dem Update-Lauf ins Repository. Die CI-Probe
  (`verify_vor_access_id.py`) und `check_vor_auth.py` fragen dieselbe
  Version. Setzt die Umgebung `VOR_VERSION` oder `VOR_BASE_URL`, gilt diese
  Vorgabe ohne Wahl. Jeder Wechsel öffnet die Feed-Wächter-Regel
  `vao-version` als Issue, das sich nach zwei Läufen selbst schließt. Code:
  `src/utils/vao_version.py`, `_poll_listed_versions` in
  `scripts/update_stammstrecke_hbf.py`. Nachgespielt mit den echten
  Abrufzeiten des 28.10.2026: Ab 15:01 hätte jeder Abruf v1.12.0 gefragt und
  funktioniert. Nennt die Liste die kaputte Version weiter, kostet der Wechsel
  einen Platz mehr (20 statt 19 Plätze von 14:00 bis 23:01). Bis dahin
  scheiterten 16 Abrufe von 15:00 bis 22:30.
* **Jede Quelle ihr eigener Hinweis:** Michael, 17.10.2026: „Bitte beachte,
  dass jede Quelle den passenden Quellenhinweis erhält. ÖBB für die ÖBB, WL
  für WL und VOR/VAO für VOR/VAO“, und „Am TV soll sich weiterhin nichts
  ändern“. Der VAO-Satz steht deshalb nur bei den Stammstrecken-Daten, nie
  als Hinweis für den ganzen Dienst: im Feed als eigener Satz im
  `<copyright>` neben den anderen Quellen, auf `docs/statistik.md` unter
  „Stammstrecke“ und „Ausfälle“ (beide aus denselben VAO-Abfragen), im
  Dashboard „Quelle: VOR/VAO“ an diesen Abschnitten. Die Störungsstatistik
  nennt ihre vier Quellen (`STOERUNGEN_SOURCES_NOTE`), jede Meldung zählt
  unter ihrer eigenen; im Feed trägt jede Meldung ihre Quelle im `<source>`
  (siehe §1, „Link und Quelle jeder Meldung“). Titel und Texte der
  Meldungen bleiben unberührt.

Weiter in [Teil 12](architecture-teil-12.md).
