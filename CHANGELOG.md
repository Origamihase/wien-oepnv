# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]
* **Stammstrecke-Feed-Trigger — Legacy-Label-Auflösung im
  Compute-Pfad (2026-05-15)**:
  * Der Trigger-Compute in `src.feed.stammstrecke.compute_
    stammstrecke_events` bucket'te Observations bisher nach
    `obs.direction` (raw CSV value); der Backwards-Compat-Alias in
    `DIRECTIONS_BY_LABEL` (Floridsdorf → Praterstern-`_Direction`)
    war auf dem heißen Pfad nicht aktiv. Folge: CSV-Zeilen mit dem
    Legacy-Label `"Floridsdorf"` (z.B. nach Backup-Restore, Partial-
    Deploy oder Hand-Edit) wären silently im Loop ignoriert worden,
    weil das Loop-Lookup `direction.target_label = "Praterstern"`
    den `by_direction["Floridsdorf"]`-Bucket nicht aufsucht. Fix:
    Observations werden via `DIRECTIONS_BY_LABEL` zur kanonischen
    Direction aufgelöst, bevor sie in den Bucket landen.
  * Neue Test-Suite `tests/test_feed_stammstrecke_trigger.py`
    (9 Tests) pinnt die Trigger-Semantik: Happy-Path (2 Praterstern-
    Zeilen > 9 min), Legacy-Compat (2 Floridsdorf-Zeilen fold-in),
    Mixed (1+1), Threshold-Gate (Single-row + boundary-9.0),
    Window-Cutoff (Beobachtung knapp außerhalb 1h), Empty-Input,
    Direction-Isolation (beide Richtungen feuern parallel),
    Constants-Pinning (`DELAY_THRESHOLD_MINUTES`, `FEED_WINDOW`).
* **Stammstrecke-Monitor — Nord-Richtungs-Label umbenannt:
  "Floridsdorf" → "Praterstern" (2026-05-15)**:
  * Die CSV-Spalte `direction` und das `DIRECTION_LABEL_NORTHBOUND`
    der Schreiber + des Feed-Renderers verwenden ab sofort
    `"Praterstern"` statt `"Floridsdorf"` für nordwärts gerichtete
    Stammstrecken-Beobachtungen. Begründung: Bei kurzen Wendezügen,
    die bereits am Praterstern oder Wien Mitte terminieren (und nicht
    bis Floridsdorf weiterfahren), bezeichnete die alte Beschriftung
    fälschlich einen Endpunkt, den die meisten Züge gar nicht
    erreichen. Die Süd-Beschriftung `"Meidling"` benennt seit jeher
    die nächste Stammstrecken-Haltestelle nach dem Hbf — die
    Umbenennung gibt der Nord-Beschriftung die gleiche Semantik:
    `"Stammstrecken-Züge in Richtung <nächster Stammstrecken-
    Haltestelle nach Hbf>"`.
  * **Datenmigration**: Alle bestehenden Zeilen in
    `data/stats/stammstrecke_2026.csv` wurden mit dem Rename-Commit
    `Floridsdorf` → `Praterstern` umgeschrieben. Die in-flight Pending-
    Trip- und Recently-finalised-Ledger
    (`cache/stammstrecke/pending_trips.json` /
    `cache/stammstrecke/recently_finalised.json`) wurden ebenfalls
    konvertiert — sowohl die `direction`-Feldwerte als auch die
    Identity-Key-Präfixe.
  * **Backwards-Compat-Shim**: Der Feed-Renderer
    (`src/feed/stammstrecke.py`) akzeptiert in
    `DIRECTIONS_BY_LABEL` weiterhin den Legacy-Wert `"Floridsdorf"`
    (alias auf die `Praterstern`-Direction). Der Hbf-Cron-Pfad ruft
    `_finalize_departed` zusätzlich für `LEGACY_DIRECTION_LABEL_
    NORTHBOUND` auf, sodass ein extern wiederhergestellter Pending-
    State mit alten Schlüsseln transparent in den Praterstern-Bucket
    fließt. Das CSV wird stets unter dem neuen Label geschrieben.
  * **Feed-Item-GUID**: Die `identity_prefix` für Nord wurde von
    `stammstrecke_delay_floridsdorf` auf `stammstrecke_delay_praterstern`
    umbenannt. Da der `data/first_seen.json` aktuell keinen aktiven
    Nord-Eintrag enthält, propagiert die Umbenennung als saubere
    "neue Direction" für RSS-Abonnenten, ohne ein laufendes Event
    doppelt zu emittieren. Sollte bei einem zukünftigen Nord-Incident
    ein laufendes Event aus der Zeit vor dem Rename existieren, würde
    es einmalig als „neues" Event in RSS-Readern erscheinen.
* **Stammstrecke-Monitor — Platform-Level Bahnsteig-Filter
  (2026-05-15)**:
  * Der `/departureBoard`-Reader filtert seit dieser Änderung jede
    Abfahrt am Wien Hauptbahnhof nach ihrem effektiven Bahnsteig
    (`rtTrack` mit Fallback auf scheduled `track`). Nur Abfahrten
    auf **Bahnsteig 1** (Stammstrecke nordwärts → Floridsdorf) oder
    **Bahnsteig 2** (Stammstrecke südwärts → Meidling) qualifizieren
    sich für die Stammstrecke-Statistik. Alle anderen Hbf-Bahnsteige
    (3-12, inkl. Halb-Bahnsteige „1A", „10A-B" usw.) tragen
    Fernverkehr (RJ/IC/EC/NJ), Hbf-endende REX-Züge, die Marchegger
    Ostbahn, die Pottendorfer Linie, die Westbahn und weitere
    Korridore, die NICHT die Stammstrecke nutzen — sie werden seit
    diesem Patch deterministisch ausgeschlossen.
  * Begleitend wurden die Substring-Listen für die Richtungsbestimmung
    bereinigt: `marchegg` und `bratislava` entfernt, weil beide
    Termini mehrdeutig waren (Marchegg verkehrt rein östlich über die
    Ostbahn ohne Stammstrecken-Bezug; Bratislava ist sowohl via
    Stammstrecke + Břeclav als auch via Ostbahn erreichbar). Der
    Bahnsteig-Filter macht die Substring-Heuristik nur noch für die
    Richtungsbestimmung notwendig (Nord vs Süd), nicht mehr für die
    Stammstrecke-Zugehörigkeit selbst.
  * Diagnostik: Zwei neue Counter (`dropped_no_track`,
    `dropped_non_stammstrecke_track`) im Tick-Log machen sowohl ein
    VAO-Schema-Drift (Bahnsteig-Info fehlt) als auch das gesunde
    Ausscheiden von Nicht-Stammstrecken-Zügen operativ sichtbar,
    ohne dass die Bahnsteig-Strings zwischen den Filtern wandern.
  * Semantik: Die Hbf-basierte Messung bleibt eine Stammstrecken-
    Messung (am Korridor-Mittelpunkt), aber jetzt mit strenger
    Linien-Eindeutigkeit auf Bahnsteig-Niveau — vergleichbar mit der
    ursprünglichen `/trip`-basierten Floridsdorf-↔-Meidling-Selektion
    der Pre-Hbf-Ära, ohne deren `numF=6`-Sampling-Lücke.
* **Stammstrecke-Monitor — Migration auf `/departureBoard` @ Wien Hbf
  (2026-05-15)**:
  * Der Cron-Pfad ruft seit dem Merge von PR #1496 das neue
    `scripts/update_stammstrecke_hbf.py`-Skript auf, das die
    `/departureBoard`-API einmal pro Tick am Wien Hauptbahnhof
    befragt und die Abfahrten anhand der Endhaltestelle per
    Substring-/Whitelist-Klassifikation in die bestehenden
    Richtungs-Labels (`Meidling`, `Floridsdorf`) einsortiert. Im
    Vergleich zum Vorgänger (`/trip` × 2 Richtungen mit hartem
    `numF=6`-Cap) verdoppelt sich die Coverage bei gleichzeitiger
    Halbierung des API-Budgets (1 statt 2 Requests/Tick).
  * **Semantischer Bruch in der Verspätungs-Messung**: bis
    2026-05-15 wurde die Verspätung **am Ursprungsbahnhof**
    (Floridsdorf für Meidling-Bound-Züge, Meidling für
    Floridsdorf-Bound-Züge) gemessen, ab 2026-05-15 **am Wien
    Hauptbahnhof** — einem Stammstrecken-Mittelpunkt. Beide Zahlen
    sind für denselben physischen Zug nicht identisch (Verspätung
    kann zwischen Ursprung und Hbf akkumulieren oder eingeholt
    werden). Die 30-Tage-Statistik im README überspannt den
    Migrations-Tag und zeigt deshalb für einige Wochen eine
    Diskontinuität, die ein Mess-Semantik-Wechsel ist, kein Bug
    und keine realer Qualitäts-Veränderung. Wer Werte vor und
    nach 2026-05-15 vergleicht, sollte diesen Stichtag im Auge
    behalten.
  * `data/stats/stammstrecke_<YYYY>.csv`-Schema und
    `cache/stammstrecke/*.json`-Ledger-Format bleiben unverändert
    (die README-Dashboard- und Feed-Event-Pipelines lesen byte-
    weise identisch weiter). `manual-full-refresh.yml` ist
    ebenfalls auf das neue Skript umgezogen, damit ein manueller
    Refresh keine konkurrierenden Identity-Key-Formate in den
    geteilten Pending-Trip-Ledger schreibt.
* **Quota-Bug Fix (Phantom-Request pro Skript-Lauf, 2026-05-15)** —
  `_flush_quota_cache` rief `save_request_count` auf, das jeden
  Aufruf als neuen Request zählte: jeder Stammstrecke-Cron-Tick
  buchte 3 Requests statt 2 auf den 100/Tag-VAO-Start-Counter. Bei
  48 Ticks/Tag wurde die Quote nach ~33 Ticks (~16 h) erschöpft und
  der Preflight-Gate übersprang die restlichen Ticks, wodurch sich
  im Ledger eine ~8h-Lücke pro Tag ergab und die README-Statistik
  "Letzte 60 Minuten" zeitweise auf 1-3 Beobachtungen abrutschte.
  Fix in PR #1494: Persist-Logik aus `save_request_count` in einen
  separaten `_persist_quota_to_disk`-Helper ausgegliedert, den der
  atexit-Flush direkt aufruft ohne den Counter zu inkrementieren.
  Regression-Tests pinnen das No-Inflation-Invariant.
* **Docs/Cleanup (VOR-Stammstrecke-only consolidation follow-up)** —
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
* **Changed (WL OGD reactivation chain, PR #1441-#1453)**: thirteen
  consolidated PRs fully reactivate the Wiener-Linien OGD merge path
  against the canonical `www.wienerlinien.at/ogd_realtime/doku/ogd/`
  endpoint (the previous `data.wien.gv.at/csv/` proxy was retired in
  the 60th OGD phase, September 2025).
  * **Endpoint + workflow (#1441, #1442)** — removed the redundant
    inline curl step from `update-stations.yml`, migrated both
    `OGD_HALTESTELLEN_URL` / `OGD_HALTEPUNKTE_URL` constants to the
    canonical Wiener Linien host. Soft-fail to pinned local CSVs on
    upstream outage.
  * **Schema fuzzy-keys (#1444)** — added column aliases so the
    loader parses both the legacy proxy CSV
    (`HALTESTELLEN_ID`/`NAME`/`WGS84_*`) and the canonical
    OGD-Echtzeit CSV (`DIVA`/`PlatformText`/`StopText`/`Latitude`).
  * **WL-only entries im `wl_diva`-Namensraum (#1446)** — removed
    synthetic `bst_id` (`9{DIVA}`) and synthetic `bst_code`
    (`WL-{name[:3]}`) on WL-only entries; the canonical `wl_diva`
    field is the sole structural identifier and cross-station-id
    collisions / `WL-ABS`-style code duplicates are gone.
  * **Pendler-Default für Border-Stops (#1443)** — unmatched WL
    haltestellen outside the Wien polygon auto-promote to
    `pendler=True`.
  * **Validator identifier (#1447)** — `_format_identifier` now
    includes `wl_diva` so WL-only entries get distinct keys instead
    of collapsing onto `"source:wl"` (which had pulled 1759 stations
    into auto-quarantine over 30 genuine naming groups).
  * **StopID + direction-marker sanitisation (#1445)** — short
    `StopID` counter values are filtered out of `aliases` (legacy
    8-digit RBL stays); `<` and `>` direction markers in `StopText`
    are replaced with `←` / `→` so they no longer hit
    `_UNSAFE_CHARS_RE`.
  * **`in_vienna` consistency (#1449)** — `build_wl_entries` now
    derives `in_vienna` from the aggregate haltepunkt coordinates
    instead of any-stop-wins, so boundary stations no longer carry
    a flag that contradicts their persisted coords. Pinned by
    `test_coordinates_match_in_vienna_flag`.
  * **ÖBB workbook soft-fail (#1450)** — `download_workbook` atomic-
    writes a snapshot to `data/oebb-verkehrsstationen.xlsx` on every
    successful run and reads from the snapshot when `data.oebb.at`
    returns a network error. Closes the asymmetric failure mode
    where ÖBB was the only fail-fast upstream source. CodeQL config
    (`.github/codeql/codeql-config.yml`) excludes the
    `py/clear-text-storage-sensitive-data` false-positive that
    matches every public-data cache writer in this project.
  * **Multi-DIVA-Merge <150 m (#1451)** — `_merge_colocated_dupli
    cates` folds same-name haltestellen with haltepunkte-mean
    coordinates within 150 m of each other into a single entry
    (lexicographically lowest DIVA wins, all haltepunkte and
    aliases unioned). Removes 4 doublings from the current
    `stations.json` (Stock im Weg, Vorgartenstraße, Lieblgasse,
    Altmannsdorfer Straße).
  * **`name` ist Display-Label, kein PK (#1452)** — the validator's
    canonical-name uniqueness check is removed. Structural
    uniqueness lives in `wl_diva` / `bst_id` / `vor_id` /
    `bst_code`; `name` is operator-facing. The
    `_disambiguate_duplicate_names` DIVA-suffix workaround
    (`Wien Bahnhof (WL 60205022)`) is retired — duplicate display
    labels are now legal and the RSS feed shows the clean
    `Wien Bahnhof (WL)` form.
  * **Aussagekräftige Display-Namen aus `StopText` (#1453)** —
    `_derive_station_label` overrides generic transport-typed
    haltestelle `PlatformText` tokens (`Bahnhof`, `Lokalbahn`,
    `Hauptbahnhof`, `Station`, `Halt`, `Bf`, `Hbf`, `Bahn`,
    `U-Bahn`) with the haltepunkte `StopText` when one is
    available. Six entries got a real toponym:
    `Wien Bahnhof (WL)` × 2 → `Wien Tribuswinkel - Josefsthal
    (WL)`, `Wiener Neudorf (WL)`; `Wien Lokalbahn (WL)` × 4 →
    `Wien Guntramsdorf Lokalbahn (WL)`, `Wien Möllersdorf (WL)`,
    `Wien Neu Guntramsdorf (WL)`, `Wien Traiskirchen Lokalbahn
    (WL)`. Non-generic PlatformText values stay untouched so
    ÖBB / VOR name-based joins remain stable.
  * **Test data refresh (#1449)** — three station-directory tests
    hard-coded legacy DIVAs that Wiener Linien has since renumbered
    (`60201076` was Karlsplatz pre-PR #1442 and is now
    Ratzenhofergasse; `60201002` was Schottentor and is now
    Pensionsversicherungsanstalt). Updated to current DIVAs.
  * **Outcome on production data**: `stations.json` grew from 196
    to 1951 entries (4 co-located doublings merged out of 1803 WL
    entries), 0 DIVA suffixes in canonical names, 0 generic
    `Wien Bahnhof (WL)` / `Wien Lokalbahn (WL)` labels, validator
    reports 0 alias / naming / security issues, `quarantine.json`
    stays empty across cron ticks.
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
  `data/vor-haltestellen.csv` zurück. Begründung wie oben — preserves
  the daily quota for the hot path. Test-Coverage:
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
  kennt diese Attribut-Pfad nicht (``request_session`` heißt das
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
* **Security**: VOR daily-quota counter is now lower-bound clamped at 0
  inside both `load_request_count` and `save_request_count` (the
  under-lock disk re-read). Pre-fix, a poisoned `data/vor_request_count.json`
  with `{"date": "<today>", "requests": -1000}` would silently bypass the
  runtime quota check (`todays_count >= MAX_REQUESTS_PER_DAY` is False
  for any negative count) and be perpetuated by the next save. Defense-
  in-depth against compromised CI runners and partial-flush corruption.
* **Security**: Secret scanner now detects four additional issuer
  taxonomies that the entropy fallback misses: JSON Web Tokens
  (`eyJ<base64url>.<base64url>.<base64url>` — three dot-separated
  segments bypass the `[A-Za-z0-9+/=_-]` alphabet), Hugging Face Access
  Tokens (`hf_<32+>`), DigitalOcean PATs (`dop_v1_<64 hex>`) and OAuth
  Refresh Tokens (`doo_v1_<64 hex>`), and GitLab Pipeline Trigger
  Tokens (`glptt-<40>`). Each finding now reports the issuer-specific
  reason instead of a generic high-entropy hit, speeding triage and
  revocation.

## [2026-05-05]
* **Data**: Wien-Stadtgrenzen-Polygon ersetzt — neu: offizielle
  `LANDESGRENZEOGD`-Quelle der MA 41 – Stadtvermessung (5.637 Vertices,
  EPSG:4326, CC BY 4.0). Vorher: hand-kuratiertes 31-Vertex-Polygon
  (PR #1190), davor 8-Vertex-Konvex-Hülle (PR #1189). Genauigkeit
  ~200 m → ~1–2 m.
* **Data**: 9 ÖBB-Stationskoordinaten gegen offizielle VOR-Werte
  korrigiert (Aspern Nord 1.160 m, Gersthof 1.694 m, Jedlersdorf 1.219 m,
  Handelskai 543 m, Rennweg 522 m, Breitensee 491 m, Floridsdorf 293 m,
  Kaiserebersdorf 319 m, Mitte-Landstraße 161 m, Liesing 359 m). PR #1188.
* **Data**: Kanonische Namen vereinheitlicht — `Hbf`/`Bf`-Abkürzungen
  durch ausgeschriebene Vollformen ersetzt (Wien Hauptbahnhof, Wien
  Westbahnhof, Wien Franz-Josefs-Bahnhof, Wiener Neustadt Hauptbahnhof,
  St. Pölten Hauptbahnhof, München Hauptbahnhof). Abkürzungen bleiben als
  Aliase erhalten. PR #1188.
* **Data**: Rennweg-Doublette aufgelöst — irreführende Bahnhof-Aliase
  aus dem Google-Places-U3-Eintrag entfernt. PR #1188.
* **Fix**: `_normalize_token` Umlaut-Faltung wird nur ab Token-Länge ≥ 4
  angewendet. Damit bleiben kurze ÖBB-Stellencodes wie `Sue` (Wien
  Süßenbrunn) und `Su` (Stockerau) distinkt im Lookup. PR #1189.
* **Fix**: source-Feld-Format in stations.json vereinheitlicht
  (Komma-getrennt, kein Whitespace); `stations.py`-Tie-Break nutzt
  Token-Set statt String-Equality, sodass Drift toleriert wird. PR #1188.
* **Feat**: NamingIssue-Validator-Kategorie hinzugefügt — prüft
  kanonische Namens-Eindeutigkeit und no-space-Source-Format. PR #1188.
* **Feat**: WL-OGD-Auto-Download in `update_wl_stations.py` —
  haltestellen/haltepunkte werden vor dem Merge live von
  `data.wien.gv.at` geladen, mit graceful Fallback auf lokale Dateien.
  Schließt die `wl_diva`-Lücke beim monatlichen CI-Lauf. PR #1189.
* **Feat**: JSON Schema für `data/stations.json` unter
  `docs/schema/stations.schema.json` plus Pin-Test
  `tests/test_stations_schema.py`.
* **Feat**: `docs/stations_validation_report.md` wird im monatlichen
  `update-stations.yml`-Lauf automatisch regeneriert; veraltete
  Archiv-Kopie entfernt.
* **Docs**: README-Stationsverzeichnis-Abschnitt vollständig überarbeitet
  (alle Felder, alle Quellen mit Lizenzen + Pflicht-Attribution, neue
  CLI-Flags, NamingIssue-Validator).
* **Docs**: Audit-Bericht-Reihe unter
  `docs/archive/audits/stations_data_audit_2026-05-05*.md` mit
  zentralem Index.

## [2026-02-02]
* `Fix`: VOR API auf `departureBoard` umgestellt und authentifizierte Requests repariert.
* `Security`: Rate-Limit-Sperre (max 100 Req/Tag) implementiert.
* `Data`: Stations-IDs auf HAFAS-Format aktualisiert.
* **Feat**: Verbessertes Deep-Parsing für Störungsmeldungen in Abfahrtsdaten.

## Quelle: PDF-Handbuch

- 2026-01-14 – Optimized feed deduplication logic to prioritize VOR provider events (API) over ÖBB provider events (Scraper). Conflicts are now resolved by retaining the VOR event as the master record while merging unique description details from the ÖBB event. This ensures higher data quality and stability.
- 2025-08-11 – Line Info Service ergänzt. (Kapitel 19)
- 2025-07-02 – Aktualisierung 5.9.2 zu Informationstexten bei Störungen.
- 2025-05-22 – Neuer Parameter `includeDrt` im Trip-Service.
- 2025-02-11 – Überarbeitung der Handbuchstruktur.
- 2024-12-10 – Kapitel 13.2 und 14.2 zu Scrolling in DepartureBoard und ArrivalBoard erweitert.
- 2024-11-27 – Kapitel 5 um neue Inhalte (5.4, 5.5, 5.11, 5.13, 5.16) und Meta-Parameter in `location.name` ergänzt.

Weitere Einträge und Detailbeschreibungen finden sich in der Änderungshistorie des PDFs (Kapitel 1.1).
