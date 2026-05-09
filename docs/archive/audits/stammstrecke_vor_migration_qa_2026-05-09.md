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
