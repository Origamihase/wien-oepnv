# Stammstrecke VOR-Migration — Senior-Architect-QA-Review (2026-05-09)

Folge-Audit zum Migrations-PR (`pyhafas` → VOR/VAO ReST API). Vier
Bereiche unter Lupe: Workflow-Orchestrierung, VOR-API-Quota,
CSV-Stats-Logger-Integrität, Code-Cleanup. Der Bericht hält explizit
fest, was geprüft, was gefunden und was korrigiert wurde.

## TL;DR

| # | Bereich | Status nach Review | Action taken |
| --- | --- | --- | --- |
| 1 | `manual-full-refresh.yml` Step-Order | ❌ Bug → ✅ Fixed | Stammstrecke-Step **vor** Feed-Build verschoben |
| 2a | Departure-Board-Polling für Flughafen Wien / Wien Hauptbahnhof | ✅ entfernt | `DEFAULT_MONITOR_WHITELIST = ""` |
| 2b | Station-Enrichment-Whitelist | ✅ angewandt | `STAMMSTRECKE_VOR_IDS` (frozenset, 10 IDs) |
| 3 | `append_stammstrecke_row` CSV-Pfad | ✅ intakt | weiterhin pro Direction nach erfolgreichem Median-Compute |
| 4 | Dangling `pyhafas` / `HafasClient` / `OEBBProfile` | ✅ keine Code-Pfade | nur Doku-/CHANGELOG-/Audit-Archivkontext |

## 1 · Workflow-Orchestrierung (CRITICAL)

### Befund

Vor diesem Audit lief der `manual-full-refresh.yml`-Workflow in dieser
Reihenfolge:

```
Caches (Baustellen, WL, ÖBB, VOR)
    ↓
Stationen
    ↓
Build feed                     ← liest cache/stammstrecke/events.json
    ↓
Refresh Stammstrecke status    ← schreibt cache/stammstrecke/events.json
    ↓
Statistik-Dashboard
    ↓
Commit
```

Konsequenz: Der Feed-Builder konsumierte den **alten** Stand der
Stammstrecke-Cache-Datei, weil das Refresh erst danach lief. Bei einem
manuellen Full-Refresh konvergierte das Endergebnis daher erst beim
**übernächsten** Lauf — ein subtiler Konsistenz-Bug, der im Cron-
Betrieb nicht auffällt (dort triggert der eigene
`update-stammstrecke-status.yml`-Cron unabhängig), wohl aber bei jedem
manuellen „alles neu"-Trigger.

Die Provider-Logik wurde in Phase 2 der Migration aus einem
Inline-pyhafas-Schritt zu einem **eigenständigen Daten-Provider**
(VOR `/trip`-Polling, Output → `cache/stammstrecke/events.json`)
refaktoriert. Damit muss er semantisch und ordnungsgemäß als
**Cache-Refresh** behandelt werden — nicht als nachgelagerter
„Statistics-Schritt".

### Fix

`Refresh Stammstrecke status` ist nun direkt nach `Refresh VOR cache`
und vor allen Stations-/Feed-/Stats-Schritten platziert (siehe
`.github/workflows/manual-full-refresh.yml`). Begründung der Position:

* Beide Schritte (`update_vor_cache.py` + `update_stammstrecke_status.py`)
  teilen sich die `external-api-fetch` Concurrency-Group sowie den
  täglichen VAO-Start-Quota-Counter (100 Requests/Tag, harte Grenze).
  Eine adjazente Anordnung macht die Quota-Bilanz pro Run trivial
  nachvollziehbar.
* Die `Validate VOR secrets`-Vorprüfung greift für beide Steps in
  einem Aufruf.
* Der Feed-Build kommt danach und liest den frischen Cache; der
  Statistik-Schritt kommt zuletzt und sieht den frischen CSV-Append.

Neue Reihenfolge:

```
Caches: Baustellen → WL → ÖBB → VOR → STAMMSTRECKE     ← NEU
Stationen
Feed-Build                                              ← liest frischen Cache
Statistik-Dashboard + README-Patch                      ← liest frische CSV
Commit
```

## 2 · VOR-API-Quota-Optimierung

### 2a · Departure-Board-Polling (Flughafen Wien, Wien Hauptbahnhof)

`src/providers/vor.py:88` — `DEFAULT_MONITOR_WHITELIST = ""` mit
ausführlichem Begründungs-Kommentar (Zeilen 74–87). Der historische
Default `"Wien Hauptbahnhof,Flughafen Wien"` ist seit dem 2026-05-09-
Pivot **entfernt**: zwei DepartureBoard-Requests pro Cron-Tick × 24
Cron-Fires/Tag = 48 Requests/Tag, die nach der Migration nicht mehr
ins 100/Tag-Budget passen, weil die Stammstrecke jetzt 96 Requests/Tag
für sich beansprucht (2 `/trip`-Anfragen × 48 Cron-Ticks).

Operatoren, die die Legacy-Polls explizit benötigen, können sie über
die Umgebungsvariable `VOR_MONITOR_STATIONS_WHITELIST` reaktivieren.
Per Default ist „kein DepartureBoard-Polling" eingestellt.

### 2b · Station-Enrichment-Whitelist

`scripts/update_vor_stations.py:77–90` — `STAMMSTRECKE_VOR_IDS`
ist ein `frozenset[str]` mit **genau 10 Einträgen**, exakt dem
Stammstrecke-Trunk:

```
490033400  Wien Floridsdorf
490170500  Wien Handelskai
490138500  Wien Traisengasse
490104000  Wien Praterstern
490074300  Wien Mitte-Landstraße
490109100  Wien Rennweg
490134600  Wien Quartier Belvedere
490134900  Wien Hauptbahnhof
490084900  Wien Matzleinsdorfer Platz
490101500  Wien Meidling
```

Stationen außerhalb dieser Liste fallen auf den gepinten
`data/vor-haltestellen.csv`-Snapshot zurück. Begründung:

* VAO-Start-Budget ist 100 Requests/Tag.
* Stammstrecke-Cron beansprucht 96/Tag.
* ⇒ Nur 4 Requests/Tag verbleiben für die monatliche Stations-
  Anreicherung — dafür reicht die Top-10-Whitelist.

Test-Coverage: `tests/scripts/test_update_vor_stations.py` verifiziert,
dass nicht-gelistete Stationen die API nicht treffen (HTTP-Mock zählt
Requests).

## 3 · CSV-Stats-Logger-Integrität

`scripts/update_stammstrecke_status.py:125` importiert
`append_stammstrecke_row` aus `src.utils.stats`; Aufruf an Zeile 848
innerhalb von `_process_direction(...)`, **nach** der Median-
Berechnung und **bevor** der Threshold-Vergleich entscheidet, ob ein
Feed-Event emittiert wird. Damit landet **jede** erfolgreiche Median-
Beobachtung — unabhängig vom Schwellwert — im append-only-Ledger
`data/stats/stammstrecke_<YYYY>.csv`. Das verhält sich wie in der
pyhafas-Ära; der `/trip`-Endpoint lieferte vor und nach der Migration
denselben Datentyp (Median-Verspätung in Minuten).

Test-Coverage: `test_process_direction_appends_stammstrecke_row_on_success`
und siblings verifizieren den CSV-Append explizit.

## 4 · Code-Cleanup

`grep -rn "pyhafas\|HafasClient\|OEBBProfile"` über `src/`, `scripts/`,
`tests/`, `docs/`, `.github/`, `requirements*.txt`, `pyproject.toml`
zeigt **keinen** dangling Code-Pfad. Alle verbleibenden Vorkommen
fallen in eine der folgenden Kategorien:

* **CHANGELOG.md** — Migration als Release-Note dokumentiert.
* **`docs/reference/stammstrecke_provider_logic.md`** — beschreibt
  explizit die Migration im Abschnitt „Migration: pyhafas → VOR/VAO
  ReST API (2026-05-09)".
* **`docs/architecture.md`** — Querverweis auf den Migrations-Schritt.
* **`docs/archive/audits/oebb_stammstrecke_audit.md`** — historisches
  Audit aus der pyhafas-Ära. Bewusst NICHT geändert; das Dokument
  beschreibt den damaligen Stand und liefert Kontext für die
  Migration. Es darf nicht rückwirkend umgeschrieben werden.
* **`requirements.txt`** — eine erklärende Kommentarzeile, warum
  `pyhafas` nicht mehr in den Dependencies ist. Kein Install-Eintrag.
* **`scripts/update_stammstrecke_status.py` / Tests** — Verweise im
  Modul-Docstring und in Test-Kommentaren, die den Migrations-
  Hintergrund erklären. Unverändert intentional, damit Future-Reader
  die Geschichte verstehen.

## Verifikation

```bash
$ python -m src.cli checks            # ruff + mypy + bandit + secrets + complexity + pip-audit
All checks passed!

$ python -m mypy --strict scripts/update_stammstrecke_status.py \
                          tests/scripts/test_update_stammstrecke_status.py
Success: no issues found in 2 source files

$ python -m pytest tests/scripts/test_update_stammstrecke_status.py -q
65 passed
```

## Folge-Audit · Senior-API-Integration-Review (2026-05-09, gleicher Tag)

Tiefen-Inspektion der drei `/trip`-Datenextraktionsanforderungen. Drei
Befunde — alle in derselben Branch behoben:

### 5a · "Next 6 Trains" — `numF=6` statt `numF=5`

Vor dem Folge-Audit: `MAX_TRIPS_PER_QUERY = 5`. Der VAO-`/trip`-Endpoint
akzeptiert `numF` im Bereich `1..6` (siehe `docs/reference/trip.md`). Da
die `maxChange=0`-Filter-Bedingung in Verbindung mit dem Strict-S-Filter
typischerweise 4-6 S-Bahn-Legs nach Filterung übriglässt, lohnt sich
der eine zusätzliche Datenpunkt — der Median wird stabiler, ohne dass
zusätzliche Quota-Slots verbraucht werden (die VAO-Antwortgröße ist
zwischen `numF=5` und `numF=6` praktisch identisch). Pin auf `6`.

### 5b · S-Bahn-Filter — Strict-S, kein "SB"

Vor dem Folge-Audit akzeptierte `_is_sbahn_leg` sowohl `category == "S"`
als auch `category == "SB"`. `SB` ist im VAO-/ÖBB-Kontext **mehrdeutig**:

* In Wien historisch *Schnellbahn* (Synonym S-Bahn) — auf der
  Stammstrecke laufen aber alle Linien als `S 1`/`S 7`/`S 80` …, **nie**
  als `SB`.
* In manchen VAO-Regional-Dialekten *Schnellbus* — auf einer Stammstrecke-
  Direktverbindung (`maxChange=0` Floridsdorf↔Meidling) wäre ein Bus
  semantisch ausgeschlossen, aber eine zukünftige Reklassifizierung
  könnte unbemerkt durchschlagen.

Der Audit entfernt `"SB"` aus den akzeptierten Categories. Die
`name`/`line`-Regex (`^\s*S\s*\d+\s*$`) fängt jede legitime S-Bahn-
Linie weiterhin auf, falls die Category fehlt. Ein neuer Test
(`test_is_sbahn_leg_rejects_ambiguous_sb_category`) pinnt das
Strict-S-Verhalten und verhindert eine zukünftige Drift.

### 5c · Realtime-Delay — fehlendes `rtTime` ⇒ 0.0 (on-time)

**Kritischer Bug.** Vor dem Folge-Audit:

```python
if not rt_time:
    return None      # ← Leg wird aus dem Median GESTRICHEN
```

Der VAO-Vertrag sagt: wenn `rtTime` **nicht** im Origin-Block steht,
ist der Zug **on-time** (das Backend lässt das Feld weg, statt
``time`` redundant zu echoen — Bandbreitenoptimierung). Die alte Logik
schloss damit jede pünktliche S-Bahn vom Median aus. Konsequenz: in
jedem Cron-Tick mit z. B. 5 S-Bahnen (4 on-time, 1 verspätet 12 min)
wurde der Median nur über die `[12]` der einen verspäteten Bahn
berechnet — `median = 12 > 9` → spuriöses Feed-Event.

Korrekte Logik:

```python
scheduled = _parse_vao_dt(sched_date, sched_time)
if scheduled is None:
    return None      # Schedule unparseable → kein Signal

rt_time = origin.get("rtTime") or origin.get("rtDepTime")
if not rt_time:
    return 0.0       # On-time per VAO-Vertrag
```

Der korrekte Median über `[0, 0, 0, 0, 12]` ist `0` (oder genauer
`(0+0)/2 = 0` bei gerader Anzahl) — kein Feed-Event, weil kein
Stammstrecken-weiter Stau vorliegt. Erst wenn die Mehrheit der Züge
verspätet wäre (z. B. `[8, 10, 11, 9, 12]`), würde der Median `10`
ergeben und das Threshold-Gate auslösen — exakt die gewünschte
Semantik.

**Erweiterte Tests:**

* `test_collect_delays_includes_sbahn_and_treats_missing_rttime_as_on_time`
  — pinnt das `0.0`-Verhalten end-to-end.
* `test_leg_departure_delay_returns_zero_when_rttime_missing` — direkter
  Unit-Test der einen Funktion.
* `test_leg_departure_delay_returns_zero_when_rttime_equals_time` —
  äquivalentes Verhalten bei explizit gleichem rtTime.
* `test_leg_departure_delay_skips_cancelled_leg` — cancelled bleibt
  `None` (kein Signal — Zug ist abwesend, nicht verspätet).
* `test_leg_departure_delay_skips_unparseable_schedule` — Schedule
  parseable Voraussetzung.
* `test_leg_departure_delay_skips_unparseable_realtime` — malformed
  rtTime fällt auf `None` (NICHT auf `0.0`) zurück, damit ein
  korruptes VAO-Feld nicht still als „on-time" durchschlägt.

### Verifikation des Folge-Audits

```bash
$ python -m pytest tests/scripts/test_update_stammstrecke_status.py -q
71 passed

$ python -m mypy --strict --explicit-package-bases \
    scripts/update_stammstrecke_status.py \
    tests/scripts/test_update_stammstrecke_status.py
Success: no issues found in 2 source files

$ python -m src.cli checks
ruff: All checks passed!
mypy: Success
bandit: clean
secrets: clean
complexity: 0 new violations
```

## Verbleibende Beobachtungen (No-Action)

* **VOR-Quota-Konsumenten teilen einen Counter:** `update_vor_cache.py`
  und `update_stammstrecke_status.py` tracken denselben
  `data/.vor_request_count.json` (per `save_request_count` /
  `load_request_count`). Solange beide Skripte das Lock-protected
  Increment nutzen (verifiziert), kann ein Run nicht über das Limit
  hinausschießen.
* **Cron-Trigger sind unverändert:** `update-stammstrecke-status.yml`
  (cron `*/30 * * * *`) und `generate-stats.yml` (cron `15 0 * * *`)
  laufen autonom weiter. Der manual-full-refresh ist additiv — er
  erzeugt im selben Lauf den Endzustand, den die Crons in zwei
  separaten Ticks erreichen würden.
* **Self-Healing-Verhalten:** Bei API-Outage setzt der Stammstrecke-
  Schritt `cache/stammstrecke/events.json` auf `[]` (Selbstheilung,
  vermeidet stale Warnungen). Der Feed-Build hängt das gracefully ab
  (leerer Cache → kein Stammstrecke-Item).
