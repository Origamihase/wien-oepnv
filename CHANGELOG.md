# CHANGELOG

## [Unreleased]
* **Feat (Statistik-Dashboard)**: Komplett dependenzfreies Statistik-
  und Logging-System für Verspätungen und Störungen — siehe Section 6
  in [`docs/architecture.md`](docs/architecture.md). Konkret:
  - **Append-only CSV-Ledger** unter `data/stats/`:
    `stammstrecke_YYYY.csv` (Spalten `timestamp, weekday, hour,
    direction, delay_minutes`) und `stoerungen_YYYY.csv` (Spalten
    `timestamp, weekday, hour, provider, location_name`). Eine Datei
    pro Kalenderjahr, alle Zeitstempel als ISO 8601 in
    `Europe/Vienna`.
  - **Producer 1**: `scripts/update_stammstrecke_status.py` schreibt
    nach jeder erfolgreichen Median-Berechnung eine Zeile — auch
    *unterhalb* der RSS-Trigger-Schwelle, damit das Dashboard die
    *gesamte* Verteilung zeigt, nicht nur die Eskalationen.
  - **Producer 2**: `src/build_feed.py` schreibt im
    `_update_item_state`-Strict-New-Pfad eine Zeile pro *erstmals
    gesehenem* Identity (über die `data/first_seen.json`-State-Logik
    erkannt). Rerun-stabil: eine lange ÖBB-Streckeninformation, die
    viele Builds überlebt, wird genau *einmal* gezählt.
  - **Aggregator**: Neues Skript `scripts/generate_markdown_stats.py`
    (nur Standardlibrary — `csv`, `collections`, `datetime`,
    `statistics`, `pathlib`, `zoneinfo`, `argparse`) erzeugt
    `docs/statistik.md` mit ASCII/Emoji-Bar-Charts: Beobachtungen je
    Wochentag und Stunde, ⌀ Verspätung je Wochentag und Stunde,
    Top-5-Hotspots mit Tageszeit-Profil pro Hotspot, Zusammenfassungs-
    KPIs.
  - **Workflow**: Neuer GitHub-Actions-Job
    `.github/workflows/generate-stats.yml` (Cron `15 0 * * *` +
    `workflow_dispatch`) führt den Aggregator aus und committet
    `docs/statistik.md` plus etwaige neue CSV-Dateien via
    `stefanzweifel/git-auto-commit-action`.
  - **Resilienz**: Schreiber sind best-effort (jeder I/O-Fehler wird
    geloggt + geschluckt — Statistik darf nie den Build kippen). Der
    Aggregator routet jede CSV über `read_capped_text` + `io.StringIO`
    (entspricht dem CSV-Size-Bomb-Sentinel) und schreibt das Dashboard
    via `atomic_write`.
  - **Lokationsheuristik**: `extract_location_name` versucht
    `zwischen X und Y` → erstes Capture, dann `Wien <Stadtteil>`,
    dann das erste großgeschriebene Multi-Wort-Token außerhalb einer
    kleinen Stopword-Liste (Bauarbeiten, Verspätung, Linie, …).
    Fallback `"unbekannt"`.
  - **Tests**: 60 neue Tests
    (`tests/scripts/test_generate_markdown_stats.py`,
    `tests/test_utils_stats.py`) decken Bar-Skalierung, Aggregation,
    CSV-Lesepfad inkl. oversized-file-skip, malformed-row-tolerance,
    Tie-Breaking im Top-N-Ranking, Idempotenz, Year-Boundary-Rollover
    und Lokations-Heuristik ab.
  - **Quality**: Mypy-Strict 0 Fehler, Bandit 0 Issues, Ruff 0
    Issues, alle 2 418 vorherigen Tests grün — keine Regression.
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
