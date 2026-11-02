# Changelog Mai 2026, Einträge ohne Tagesdatum

Neueste Einträge zuerst. Übersicht: [CHANGELOG.md](../CHANGELOG.md).

* **Docs/Cleanup (Nachzug zur VOR-Stammstrecke-only-Konsolidierung)** —
  Doku- und Workflow-Drift nach der 2026-05-11-Konsolidierung (VOR
  ist nur noch für den Stammstrecken-Monitor zuständig) bereinigt:
  * Tote Skript-Verweise auf `update_vor_cache.py`,
    `update_vor_stations.py` und `fetch_vor_haltestellen.py` aus
    `docs/development.md`, `docs/architecture.md`,
    `.github/workflows/manual-full-refresh.yml` und
    `.github/workflows/update-stations.yml` entfernt; die Scripts
    existieren seit 2026-05-11 nicht mehr.
  * Verwaiste `cache/vor_929f1c/last_run.json` (kein aktiver Writer
    nach der Konsolidierung; Status seit 2026-05-09 `api_unreachable`)
    plus leeres Parent-Verzeichnis gelöscht.
  * CLI-Help-Text `python -m src.cli cache update …` listet `vor`
    nicht mehr als gültigen Provider-Identifier (der Handler hat es
    ohnehin schon abgewiesen, jetzt ist die Hilfe konsistent).
  * Stale `update-vor-cache.yml`-Workflow-Verweis in
    `src/utils/cache.py` (`write_status`-Sicherheitskommentar) und in
    `tests/test_sentinel_quota_status_trojan_source.py` als historisch
    gekennzeichnet — die Trojan-Source-Defence im Writer bleibt
    unverändert in Kraft.

* **Changed (WL-OGD-Reaktivierungskette, PR #1441-#1453)**: Dreizehn
  konsolidierte PRs reaktivieren den Wiener-Linien-OGD-Merge-Pfad
  vollständig gegen den kanonischen
  `www.wienerlinien.at/ogd_realtime/doku/ogd/`-Endpunkt (der vorherige
  `data.wien.gv.at/csv/`-Proxy wurde in der 60. OGD-Phase im September
  2025 abgeschaltet).
  * **Endpoint + Workflow (#1441, #1442)** — den redundanten
    Inline-curl-Schritt aus `update-stations.yml` entfernt, beide
    Konstanten `OGD_HALTESTELLEN_URL` / `OGD_HALTEPUNKTE_URL` auf den
    kanonischen Wiener-Linien-Host migriert. Soft-Fail auf gepinnte
    lokale CSVs bei Upstream-Ausfall.
  * **Schema-Fuzzy-Keys (#1444)** — Spalten-Aliase ergänzt, sodass der
    Loader sowohl das Legacy-Proxy-CSV
    (`HALTESTELLEN_ID`/`NAME`/`WGS84_*`) als auch das kanonische
    OGD-Echtzeit-CSV (`DIVA`/`PlatformText`/`StopText`/`Latitude`) parst.
  * **WL-only-Einträge im `wl_diva`-Namensraum (#1446)** — synthetische
    `bst_id` (`9{DIVA}`) und synthetischen `bst_code`
    (`WL-{name[:3]}`) bei WL-only-Einträgen entfernt; das kanonische
    `wl_diva`-Feld ist der einzige strukturelle Identifier, und
    Cross-Station-ID-Kollisionen / `WL-ABS`-artige Code-Duplikate
    sind verschwunden.
  * **Pendler-Default für Border-Stops (#1443)** — nicht zugeordnete
    WL-Haltestellen außerhalb des Wien-Polygons werden automatisch auf
    `pendler=True` hochgestuft.
  * **Validator-Identifier (#1447)** — `_format_identifier` schließt
    jetzt `wl_diva` ein, sodass WL-only-Einträge distinkte Schlüssel
    erhalten, statt auf `"source:wl"` zu kollabieren (was 1759 Stationen
    über 30 echte Naming-Gruppen in die Auto-Quarantäne gezogen hatte).
  * **StopID- + Richtungsmarker-Sanitisierung (#1445)** — kurze
    `StopID`-Zählerwerte werden aus den `aliases` herausgefiltert (die
    Legacy-8-stellige RBL bleibt); `<`- und `>`-Richtungsmarker in
    `StopText` werden durch `←` / `→` ersetzt, sodass sie nicht mehr
    `_UNSAFE_CHARS_RE` treffen.
  * **`in_vienna`-Konsistenz (#1449)** — `build_wl_entries` leitet
    `in_vienna` jetzt aus den aggregierten Haltepunkt-Koordinaten ab
    statt nach Any-Stop-wins, sodass Grenzstationen kein Flag mehr
    tragen, das ihren persistierten Koordinaten widerspricht. Gepinnt
    durch `test_coordinates_match_in_vienna_flag`.
  * **ÖBB-Workbook-Soft-Fail (#1450)** — `download_workbook` schreibt
    bei jedem erfolgreichen Lauf atomar einen Snapshot nach
    `data/oebb-verkehrsstationen.xlsx` und liest aus dem Snapshot, wenn
    `data.oebb.at` einen Netzwerkfehler liefert. Schließt den
    asymmetrischen Fehlermodus, in dem ÖBB die einzige Fail-Fast-
    Upstream-Quelle war. Die CodeQL-Config
    (`.github/codeql/codeql-config.yml`) schließt das
    `py/clear-text-storage-sensitive-data`-False-Positive aus, das auf
    jeden Public-Data-Cache-Writer in diesem Projekt zutrifft.
  * **Multi-DIVA-Merge <150 m (#1451)** — `_merge_colocated_dupli
    cates` faltet gleichnamige Haltestellen mit Haltepunkte-Mittel-
    Koordinaten innerhalb von 150 m zueinander zu einem einzigen
    Eintrag zusammen (die lexikographisch kleinste DIVA gewinnt, alle
    Haltepunkte und Aliase werden vereinigt). Entfernt 4 Doppelungen
    aus dem aktuellen `stations.json` (Stock im Weg, Vorgartenstraße,
    Lieblgasse, Altmannsdorfer Straße).
  * **`name` ist Display-Label, kein PK (#1452)** — die
    Kanonische-Namens-Eindeutigkeitsprüfung des Validators wird
    entfernt. Strukturelle Eindeutigkeit lebt in `wl_diva` / `bst_id` /
    `vor_id` / `bst_code`; `name` ist operator-zugewandt. Der
    `_disambiguate_duplicate_names`-DIVA-Suffix-Workaround
    (`Wien Bahnhof (WL 60205022)`) ist abgeschafft — doppelte
    Display-Labels sind jetzt zulässig und der RSS-Feed zeigt die
    saubere `Wien Bahnhof (WL)`-Form.
  * **Aussagekräftige Display-Namen aus `StopText` (#1453)** —
    `_derive_station_label` überschreibt generische transport-typisierte
    `PlatformText`-Tokens der Haltestelle (`Bahnhof`, `Lokalbahn`,
    `Hauptbahnhof`, `Station`, `Halt`, `Bf`, `Hbf`, `Bahn`,
    `U-Bahn`) mit dem Haltepunkte-`StopText`, sofern einer
    verfügbar ist. Sechs Einträge bekamen ein echtes Toponym:
    `Wien Bahnhof (WL)` × 2 → `Wien Tribuswinkel - Josefsthal
    (WL)`, `Wiener Neudorf (WL)`; `Wien Lokalbahn (WL)` × 4 →
    `Wien Guntramsdorf Lokalbahn (WL)`, `Wien Möllersdorf (WL)`,
    `Wien Neu Guntramsdorf (WL)`, `Wien Traiskirchen Lokalbahn
    (WL)`. Nicht-generische PlatformText-Werte bleiben unangetastet,
    sodass ÖBB- / VOR-Name-basierte Joins stabil bleiben.
  * **Test-Daten-Refresh (#1449)** — drei Stationsverzeichnis-Tests
    hatten Legacy-DIVAs hartkodiert, die Wiener Linien seither
    umnummeriert hat (`60201076` war vor PR #1442 Karlsplatz und ist
    jetzt Ratzenhofergasse; `60201002` war Schottentor und ist jetzt
    Pensionsversicherungsanstalt). Auf aktuelle DIVAs aktualisiert.
  * **Ergebnis auf Produktivdaten**: `stations.json` wuchs von 196 auf
    1951 Einträge (4 ko-lokalisierte Doppelungen aus 1803 WL-Einträgen
    herausgemergt), 0 DIVA-Suffixe in kanonischen Namen, 0 generische
    `Wien Bahnhof (WL)` / `Wien Lokalbahn (WL)`-Labels, der Validator
    meldet 0 Alias- / Naming- / Security-Issues, `quarantine.json`
    bleibt über Cron-Ticks hinweg leer.

* **Changed (Auto-Quarantine für `update_all_stations.py`)**: Blockierende
  Validation-Issues (`provider_issues`, `cross_station_id_issues`,
  `naming_issues`, `security_issues`) brechen die Pipeline nicht mehr ab.
  Stattdessen werden die betroffenen Einträge aus dem gemergten
  `tmp_stations_path` herausgefiltert, in `data/quarantine.json`
  persistiert (mit `timestamp` / `count` / pro-Station-Issues) und der
  Rest des Pipelines (Diff, Heartbeat, Atomic-Copy-Back) läuft mit dem
  gültigen Subset weiter. Damit überlebt der Feed eine partielle
  Upstream-Korruption (einzelne kaputte VOR-/OEBB-/WL-Einträge) und
  exitet mit `0`. Der ``<global>``-Sentinel der Provider-Issue-Liste
  (z. B. "Need at least two VOR entries") wird übersprungen — er
  korrespondiert mit keinem einzelnen Eintrag und kann nicht
  quarantänisiert werden. Tests: 5 neue Cases in
  `test_update_all_stations_diff_heartbeat.py` /
  `test_update_all_stations_wrapper.py` decken Identifier-Filterung,
  Partition-Logik, End-to-End-Quarantine-Schreiben und den
  ``<global>``-Skip ab. Mypy `--strict` bleibt clean.

* **Changed (Stammstrecke-Monitor → VOR/VAO ReST API)**: Der S-Bahn-
  Stammstrecken-Verspätungs-Monitor wurde von `pyhafas` (`OEBBProfile`)
  auf die offizielle VOR/VAO ReST `/trip`-API portiert. Hintergrund:
  das auf PyPI veröffentlichte `pyhafas` exportiert kein
  `OEBBProfile`, der Import schlug seit Wochen still fehl und
  `data/stats/stammstrecke_*.csv` blieb leer (siehe Audit-Bericht
  zu PR #1378).
  - **Removed**: `pyhafas` aus `requirements.txt`,
    `from pyhafas import HafasClient` / `_build_client` /
    `_query_journeys` / `_patch_session_timeout` aus
    `scripts/update_stammstrecke_status.py`.
  - **Replaced**: HAFAS-Aufruf durch `fetch_content_safe` gegen
    `${VOR_BASE_URL}trip` mit `originId` / `destId` / `numF=5` /
    `maxChange=0` / `rtMode=SERVER_DEFAULT`. Auth via
    `vor_provider.VorAuth` (gleicher Stack wie Disruption-Provider).
    Quota-Slot wird **vor** jedem Network Call via
    `_charge_one_request` reserviert.
  - **Stabil**: Event-Schema (`source: "ÖBB"`), `first_seen`-
    Persistenz, `DELAY_THRESHOLD_MINUTES = 9`, Self-Healing-Regel,
    Atomic-Write, CSV-Statistik-Logging, Cron-Schedule
    (`*/30 * * * *`). Feed-Reader-Subscribers bemerken den Wechsel
    nicht.
  - **Tests**: Mocks an der `_query_trips`-Boundary statt an einer
    pyhafas-`HafasClient`-Imitation. 64 Tests in
    `tests/scripts/test_update_stammstrecke_status.py` decken
    `_is_sbahn_leg` (3 Signal-Quellen), Direct-Connection-Filter,
    Realtime-Erkennung, Quota-Charge-vor-Fetch, Threshold-Semantik,
    `first_seen`-Persistenz, Self-Healing und Schema-Compliance ab.
  - **Doku**: `docs/reference/oebb_provider_logic.md` enthält jetzt
    nur noch die ÖBB-RSS-Scraper-Logik (`src/providers/oebb.py`); der
    Stammstrecke-Monitor ist nach
    `docs/reference/stammstrecke_provider_logic.md` ausgegliedert.

* **Changed (VOR API quota optimization)**: `DEFAULT_MONITOR_WHITELIST`
  in `src/providers/vor.py` ist jetzt **leer** (vorher
  `"Wien Hauptbahnhof,Flughafen Wien"`). Begründung: das
  Tagesbudget von 100 VAO-Requests wird nach der Stammstrecke-
  Migration von 96 Stammstrecken-Calls (`/trip` × 2 × 48) dominiert;
  parallele Departure-Board-Polls würden das Limit überschreiten.
  Operatoren, die das Legacy-Verhalten brauchen, setzen
  `VOR_MONITOR_STATIONS_WHITELIST` explizit per Umgebungsvariable.

* **Changed (Station-Enrichment-Whitelist)**: `fetch_vor_stops_from_api`
  in `scripts/update_vor_stations.py` macht Live-API-Calls jetzt nur
  noch für die 10 Stammstrecke-Stationen (`STAMMSTRECKE_VOR_IDS`).
  Alle anderen Station-IDs fallen auf die gepinnte
  `data/vor-haltestellen.csv` zurück. Begründung wie oben — bewahrt
  das Tagesbudget für den heißen Pfad. Test-Coverage:
  `test_fetch_vor_stops_from_api_skips_non_stammstrecke_ids`.

* **Added (Statistik-Dashboard)**: Zero-dependency Append-only-CSV-
  Pipeline und Markdown-Dashboard — Architektur-Kontext in
  [`docs/architecture.md` § 6](docs/architecture.md).
  - Producer — `scripts/update_stammstrecke_status.py` hängt nach
    jeder Median-Berechnung eine Zeile an
    `data/stats/stammstrecke_YYYY.csv` an (auch unterhalb der
    RSS-Schwelle, damit das Dashboard die *gesamte* Verteilung
    abbildet).
  - Producer — `src/build_feed.py:_update_item_state` schreibt im
    Strict-New-Pfad (Cache-Miss auf `_identity` *und* `guid`) eine
    Zeile in `data/stats/stoerungen_YYYY.csv`. Lange Streckeninformationen
    werden genau einmal gezählt.
  - Aggregator — `scripts/generate_markdown_stats.py` (Standardlib
    only: `csv`, `collections`, `datetime`, `statistics`, `pathlib`,
    `zoneinfo`, `argparse`) rendert `docs/statistik.md` mit
    ASCII/Emoji-Bars: Verteilung je Wochentag/Stunde, ⌀ Verspätung,
    Top-5-Hotspots mit Tageszeit-Profil.
  - Workflow — `.github/workflows/generate-stats.yml`
    (Cron `15 0 * * *` + `workflow_dispatch`) committet das Dashboard
    plus neue CSV-Dateien via `stefanzweifel/git-auto-commit-action`.

* **Added (Test-Isolation)**: Autouse-Fixture `isolate_stats_writes`
  in `tests/conftest.py` monkeypatcht `src.utils.stats.DEFAULT_STATS_DIR`
  pro Test auf `tmp_path` — verhindert, dass Suite-Läufe synthetische
  Zeilen ins committete Ledger schreiben (PR #1372).

* **Security (Bounded CSV reads)**: Aggregator routet jede CSV durch
  `read_capped_text` + `io.StringIO` (entspricht dem
  `tests/test_sentinel_csv_size_bomb.py`-Sentinel) und schreibt das
  Dashboard atomar via `atomic_write`. Producer-Writer sind best-effort
  (jeder `OSError` wird auf WARNING-Level geschluckt) — Statistik
  kann den Build nie kippen.

* **Changed (Audit-Report)**: Addendum (§ 14) zum bestehenden
  [`docs/archive/audits/oebb_stammstrecke_audit.md`](docs/archive/audits/oebb_stammstrecke_audit.md)
  dokumentiert die Statistik-Pipeline-Integration und bestätigt, dass
  die Audit-Befunde der Sections 1–13 unverändert bestehen
  (Verdict bleibt **0 Findings**, production-ready).

* **Changed (Reference-Doku)**: `docs/reference/oebb_provider_logic.md`
  korrigiert auf `MAX_JOURNEYS_PER_QUERY = 5` (vormals stale `12`)
  und enthält jetzt einen Abschnitt zur Statistik-Logging-Integration
  des Stammstrecke-Skripts.

* **Audit**: Vollständige Audit-Abnahme des S-Bahn Stammstrecke
  Monitors mit Bericht unter
  [`docs/archive/audits/oebb_stammstrecke_audit.md`](docs/archive/audits/oebb_stammstrecke_audit.md).
  Verifiziert: Mypy-Strict 0 Fehler, Bandit 0 Issues, Circuit Breaker
  trippt nach 10 Failures auf 1 h Recovery, HTTP-Timeout via
  Session-Patch, Europe/Vienna an allen 13 datetime-Sites, Schema-
  Compliance gegen `docs/schema/events.schema.json` (3 / 3 Szenarien
  grün), 47 Tests + 95.3 % Coverage. Audit-Resultat: **0 Findings**,
  Feature ist production-ready.

* **Tuning (Stammstrecke)**: `MAX_JOURNEYS_PER_QUERY` von 12 auf
  **5** gesenkt. Damit wird der Median nur über die *unmittelbar
  nächsten 5* anstehenden S-Bahnen pro Richtung gebildet (10 Journeys
  pro Cron-Tick gesamt) — schärferer Median, kleinere HAFAS-Payload,
  bessere Operator-Erwartung („wie ist es jetzt?"). Zwei neue
  Pin-Tests (`test_max_journeys_per_query_is_pinned_to_five` +
  `test_query_journeys_forwards_max_journeys_kwarg`) verhindern
  zukünftige Regressionen.

* **Feat (Stammstrecke)**: Self-Healing + first_seen-Persistenz +
  erweitertes Description-Schema. Konkret:
  - **first_seen-Persistenz**: Jedes Event in
    `cache/stammstrecke/events.json` trägt nun ein eigenes
    `first_seen`-Feld (ISO-8601, Europe/Vienna). Beim nächsten
    Cron-Tick liest das Skript den vorherigen Cache, erkennt für
    jede Richtung das ursprüngliche `first_seen` und behält es bei,
    solange die Episode anhält. Damit bleibt die `guid` für die
    Dauer einer Verspätungs-Episode stabil (Feed-Reader zeigen *eine*
    fortlaufende Meldung statt einer Flut neuer Einträge alle
    30 Minuten).
  - **Description-Format**: `"Durchschnittliche Verspätung von [X]
    Minuten in Richtung [Zielbahnhof] [Seit DD.MM.YYYY]"` —
    DD.MM.YYYY ist das `first_seen`-Datum, lokalisiert auf
    Europe/Vienna.
  - **Self-Healing**: Die Cache-Datei wird *zwingend* auf `[]`
    geleert, sobald (a) die Schnittstelle nicht erreichbar ist
    (jede pyhafas-Exception, ImportError oder offener Circuit
    Breaker) ODER (b) für *alle* Richtungen der Median ≤ 9 ist.
    Dies verhindert veraltete Warnungen im RSS-Feed bei einem
    Recovery oder einem API-Ausfall.
  - **GUID-Stabilität**: `guid` wird jetzt aus
    `(identity_prefix, iso_first_seen)` abgeleitet (statt
    `iso_pubDate`), `starts_at` ist das `first_seen` (statt der
    aktuellen Beobachtungszeit). `pubDate` bleibt als Freshness-
    Indikator dynamisch.
  - **Schema-Pin-Test**: Neuer `test_build_event_validates_against_schema`
    validiert das emittierte Event-Objekt gegen
    `docs/schema/events.schema.json` (via `pytest.importorskip("jsonschema")`).

* **Security/Liveness**: Stammstrecke-Monitor erzwingt jetzt einen
  echten HTTP-Timeout für pyhafas-Aufrufe. Das vorherige Code-Snippet
  versuchte, ``client.profile.requests.timeout`` zu setzen — pyhafas
  kennt diesen Attribut-Pfad nicht (``request_session`` heißt das
  Attribut), und ``requests.Session`` honoriert ``session.timeout``
  als Attribut ohnehin nicht. Resultat: ein hängender HAFAS-Endpoint
  hätte den Cron-Run bis zur GitHub-Actions-Wallclock (6 h) blockiert
  (DoS via Slow Upstream). Neuer ``_patch_session_timeout`` patcht
  ``session.request`` (die Low-Level-Methode, an die ``post/get/...``
  delegieren) und injiziert ``timeout=QUERY_TIMEOUT`` als Default.

* **Consistency**: Stammstrecke-Events nutzen jetzt das kanonische
  Stationsverzeichnis (``src.utils.stations``) für die Auflösung der
  Ziel-Stationsnamen statt sie hartzucodieren. Damit propagiert ein
  Rename in ``data/stations.json`` (z. B. wie zuletzt bei "Wien
  Hauptbahnhof") automatisch in die Beschreibung. Der kompakte
  "in Richtung Meidling"-Stil bleibt erhalten — der ``Wien ``-Präfix
  wird nach der Lookup-Auflösung gestrippt, weil die Beschreibung
  Wien implizit voraussetzt.

* **Feat**: S-Bahn Stammstrecke Monitoring jetzt **richtungsgetrennt**.
  `scripts/update_stammstrecke_status.py` wertet beide Fahrtrichtungen
  (Floridsdorf → Meidling und Meidling → Floridsdorf) strikt
  unabhängig aus und emittiert pro Richtung **separat** ein Event,
  wenn der Median der `departure_delay`-Werte > 9 Minuten liegt
  (Liste mit 0/1/2 Events). Eine Zusammenlegung beider Richtungen
  hatte das Signal verfälscht — eine Störung in eine Richtung läuft
  oft in der Gegenrichtung normal weiter. Pro Richtung eindeutige
  `guid`/`_identity` (`stammstrecke_delay_meidling` bzw.
  `stammstrecke_delay_floridsdorf`) damit Feed-Reader die Meldungen
  als separate Notifications darstellen. Description-Format jetzt
  "Durchschnittliche Verspätung von X Minuten in Richtung
  Meidling/Floridsdorf" (Plain Text, keine HTML-Tags).

* **Feat**: Circuit-Breaker-Konfiguration auf das documented
  10-Requests-pro-Stunde-Budget der ÖBB-Abfragen ausgerichtet:
  `failure_threshold=10`, `recovery_timeout=3600.0` (1 Stunde).
  Im Normalbetrieb produziert die Pipeline 4 Calls/h
  (Cron `*/30` × 2 Richtungen) — komfortabel unter der Schwelle;
  im Fehlermodus deckelt der Breaker zusätzlich auf 10 Versuche/h.

* **Feat**: S-Bahn Stammstrecke Monitoring. Neuer Workflow
  `.github/workflows/update-stammstrecke-status.yml` (Cron `*/30 * * * *`)
  ruft via `pyhafas` mit `OEBBProfile` direkte S-Bahn-Verbindungen
  Wien Floridsdorf (8100518) ↔ Wien Meidling (8100514) ab
  (`max_changes=0`) und schreibt schema-konforme Meldungen in
  `cache/stammstrecke/events.json`. Schreibt atomar via
  `atomic_write` und ist mit dem bestehenden Feed-Build über
  `read_cache_stammstrecke()` (Provider-Flag `STAMMSTRECKE_ENABLE`)
  integriert. Dokumentiert in `docs/reference/oebb_provider_logic.md`.
  Tests mocken `pyhafas` vollständig
  (`tests/scripts/test_update_stammstrecke_status.py`).

* **Security**: Der VOR-Tagesquota-Zähler wird jetzt sowohl in
  `load_request_count` als auch in `save_request_count` (dem
  Disk-Re-Read unter Lock) nach unten auf 0 begrenzt. Vor dem Fix konnte
  eine manipulierte `data/vor_request_count.json` mit
  `{"date": "<today>", "requests": -1000}` die Laufzeit-Quota-Prüfung
  stillschweigend umgehen (`todays_count >= MAX_REQUESTS_PER_DAY` ist für
  jeden negativen Zählerstand False) und wäre durch das nächste Speichern
  fortgeschrieben worden. Defense-in-Depth gegen kompromittierte
  CI-Runner und Korruption durch partielles Flushen.

* **Security**: Der Secret-Scanner erkennt jetzt vier zusätzliche
  Aussteller-Taxonomien, die der Entropie-Fallback verfehlt: JSON Web
  Tokens (`eyJ<base64url>.<base64url>.<base64url>` — drei
  punktgetrennte Segmente umgehen das `[A-Za-z0-9+/=_-]`-Alphabet),
  Hugging Face Access Tokens (`hf_<32+>`), DigitalOcean PATs
  (`dop_v1_<64 hex>`) und OAuth Refresh Tokens (`doo_v1_<64 hex>`)
  sowie GitLab Pipeline Trigger Tokens (`glptt-<40>`). Jeder Fund meldet
  jetzt den ausstellerspezifischen Grund statt eines generischen
  High-Entropy-Treffers, was Triage und Revocation beschleunigt.
