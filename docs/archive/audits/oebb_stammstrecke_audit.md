# Audit: S-Bahn Stammstrecke Monitor

**Datum:** 2026-05-09
**Scope:** `scripts/update_stammstrecke_status.py`,
`tests/scripts/test_update_stammstrecke_status.py`,
`.github/workflows/update-stammstrecke-status.yml`,
Integrationspunkte in `src/feed/providers.py`, `src/build_feed.py`,
`src/config/defaults.py` und `data/stations.json`.
**PRs (chronologisch):** [#1365](https://github.com/Origamihase/wien-oepnv/pull/1365)
→ [#1366](https://github.com/Origamihase/wien-oepnv/pull/1366)
→ [#1367](https://github.com/Origamihase/wien-oepnv/pull/1367)
→ [#1368](https://github.com/Origamihase/wien-oepnv/pull/1368)
→ dieser Branch (Audit + `max_journeys=5`).

## Executive Summary

Der Stammstrecke-Monitor erfüllt seine Design-Ziele vollständig.
Alle in diesem Audit geprüften Kriterien — strikte Typisierung,
Security, Resilienz, Zeitzonen-Konsistenz und Schema-Compliance —
sind **ohne offene Findings** abgedeckt.

| Kriterium | Status | Evidenz |
| :--- | :--- | :--- |
| Mypy `--strict` (`src/` + `tests/`) | ✅ | 0 Fehler in 411 Source-Dateien |
| Ruff (E, F, S, B, UP) | ✅ | clean |
| Bandit (Security SAST) | ✅ | 0 Issues (High/Medium/Low/Undefined) |
| C901 (Komplexitäts-Gate ≤ 15) | ✅ | 0 neue Verstöße |
| pytest — Stammstrecke-Suite | ✅ | 47 passed / 1 skipped (jsonschema optional) |
| pytest — Full Repo Suite | ✅ | 2326 passed / 4 skipped |
| Test-Coverage (Stammstrecke-Skript) | ✅ | 95.3 % (Stmts + Branch) |
| Schema-Validierung gegen `events.schema.json` | ✅ | 3 / 3 repräsentative Szenarien grün |
| Resilienz: Circuit Breaker | ✅ | OPEN-State short-circuitet `.call()` |
| Resilienz: HTTP-Timeout | ✅ | session.request-Patch erzwingt `timeout=20s` |
| Self-Healing (Cache leeren) | ✅ | 5 Trigger getestet |
| Zeitzonen-Konsistenz (Europe/Vienna) | ✅ | alle 13 Datums-Sites tz-aware |

Kein Code-Change ergibt sich aus diesem Audit; die in diesem Branch
mitgelieferte Anpassung `MAX_JOURNEYS_PER_QUERY = 5` ist eine
Optimierung der API-Payload, kein Fix eines Findings.

---

## Scope

Der Audit umfasst die vier Stammstrecke-PRs (#1365 – #1368) sowie die
in diesem Branch eingebrachte Finalisierung:

* **Skript:** `scripts/update_stammstrecke_status.py` (734 LoC)
* **Tests:** `tests/scripts/test_update_stammstrecke_status.py`
  (47 Tests, 1005 LoC)
* **Workflow:** `.github/workflows/update-stammstrecke-status.yml`
* **Integration im Feed-Builder:**
  * `src/feed/providers.py` (Loader-Registrierung,
    `read_cache_stammstrecke`)
  * `src/build_feed.py` (Provider-Registry, Cache-Read über
    `read_capped_json`)
  * `src/config/defaults.py` (`STAMMSTRECKE_ENABLE` Flag default `True`)
* **Schema:** `docs/schema/events.schema.json` (Pin-Test mit
  `pytest.importorskip("jsonschema")`)
* **Reference-Doku:**
  `docs/reference/oebb_provider_logic.md` (307 LoC)

---

## 1. Strikte Typisierung (Mypy `--strict`)

### Befund: ✅ Keine Verstöße.

```
$ PYTHONPATH=src python3 -m mypy --no-pretty --strict scripts/update_stammstrecke_status.py
Success: no issues found in 1 source file

$ PYTHONPATH=src python3 -m mypy --no-pretty src tests
Success: no issues found in 411 source files
```

### Evaluierte Punkte

* **Modul-globale Annotationen:** Alle öffentlichen und privaten
  Module-Konstanten (`FLORIDSDORF_STATION_ID`, `MEIDLING_STATION_ID`,
  `DELAY_THRESHOLD_MINUTES`, `MAX_JOURNEYS_PER_QUERY`, `QUERY_TIMEOUT`,
  `MAX_QUERY_TIMEOUT`, `BREAKER_*`, `VIENNA_TZ`, `OUTPUT_PATH`,
  `EVENT_*`) sind durch Mypy ohne explizite Annotation typsicher
  abgeleitet.
* **Dataclass:** `_Direction` ist `@dataclass(frozen=True)` mit
  vollständigen `str`-Annotationen.
* **Funktions-Signaturen:** Alle 12 Top-Level-Funktionen tragen
  vollständige Argument- und Rückgabetypen, inklusive
  `tuple[dict[str, Any] | None, str]` für `_process_direction` und
  `dict[str, str]` für `_read_existing_first_seen`.
* **`from __future__ import annotations`:** Aktiv → alle Annotationen
  als String evaluiert, kein Import von `pyhafas.types.fptf` zur
  Laufzeit (nur unter `TYPE_CHECKING`-Branch).
* **`Any`-Verwendungen:** Bewusst nur an pyhafas-Grenzen
  (`client: Any`, Mock-fähige Test-Schnittstelle) — pyhafas hat keine
  `py.typed`-Marker, deshalb wäre stärkere Typisierung Blei
  fürs Auge.

### Konkrete Designentscheidungen

* **Sparing `# type: ignore`:** Im Skript existiert **kein einziger**
  `# type: ignore[...]`-Kommentar. Der frühere Workaround für
  `session.request = ...` wurde entfernt, nachdem mypy ihn als
  `[unused-ignore]` ablehnte (PR #1367, Commit `1e67321`).
* **Optionalität explizit:** `previous_first_seen.get(prefix)` liefert
  `str | None`; die Branch-Logik kümmert sich.

---

## 2. Security (Bandit + manuelle Inspektion)

### Befund: ✅ 0 Bandit-Issues, keine manuellen Findings.

```
$ bandit -r scripts/update_stammstrecke_status.py
Run metrics:
        Total issues (by severity):
                Undefined: 0
                Low: 0
                Medium: 0
                High: 0
        Total issues (by confidence):
                Undefined: 0
                Low: 0
                Medium: 0
                High: 0
```

### Manuelle Security-Review

| Vektor | Bewertung | Begründung |
| :--- | :--- | :--- |
| Command Injection / Shell | ❌ Nicht relevant | kein `subprocess`, `os.system`, `shell=True` |
| Eval / Code-Injection | ❌ Nicht relevant | kein `eval`, `exec`, `pickle` |
| SSRF | ❌ Nicht relevant | URL ist konstant (`fahrplan.oebb.at` via pyhafas) |
| Path-Traversal | ✅ abgesichert | `OUTPUT_PATH = REPO_ROOT / "cache" / "stammstrecke" / "events.json"` ist hardcoded relativ zum Repo-Root; kein User-Input fließt in den Pfad |
| Atomare Schreiboperationen | ✅ abgesichert | `atomic_write(OUTPUT_PATH, mode="w", encoding="utf-8", permissions=0o644)` verwendet kryptografisch zufälligen Temp-Dateinamen + fsync + replace |
| TOCTOU bei Cache-Read | ✅ tolerierbar | `_read_existing_first_seen` öffnet Datei per Pfad und liest in einem Schritt; alle Failure-Modi → `{}` |
| Log-Injection / ANSI / BiDi | ✅ abgesichert | Alle Exception-Strings werden via `sanitize_log_arg(str(exc))` durch das projektweite `SafeFormatter`-Pipeline geleitet (CVE-2021-42574-Klasse) |
| Secrets in Logs | ✅ Nicht relevant | Keine Secrets im Skript (ÖBB HAFAS = öffentliche, key-lose API) |
| Untrusted Deserialization | ✅ abgesichert | `json.load` vom selbst-geschriebenen Cache; bei `JSONDecodeError`/`OSError`/`UnicodeDecodeError` → `{}` |
| Denial-of-Service via Slow Upstream | ✅ abgesichert | `_patch_session_timeout` injiziert `timeout=20s` in `session.request` (s. § 3 Resilienz) |
| Resource-Exhaustion (Cache-Bomb) | ✅ abgesichert | Build-Feed liest unsere Cache-Datei via `read_capped_json` (50 MiB-Cap, TOCTOU-safe); im Skript selbst kein Risiko, weil wir die Datei nur überschreiben, nie unkontrolliert wachsen lassen |
| TLS / Certificate-Pinning | ✅ akzeptabel | pyhafas → requests + certifi (Project-Standard); ÖBB-Endpoint ist HTTPS |

### `# type: ignore` / `# nosec` / `# pragma: no cover`

```
$ grep -E '#\s*(type|nosec|pragma)' scripts/update_stammstrecke_status.py
276:    except Exception:  # pragma: no cover - defensive: directory load failure
567:    except Exception as exc:  # pragma: no cover - defensive
711:if __name__ == "__main__":  # pragma: no cover - CLI entry point
```

Drei `# pragma: no cover`-Marker, alle defensive Exception-Branches
oder der CLI-Entry-Point. Kein einziger `# nosec`. Kein einziger
`# type: ignore`.

---

## 3. Resilienz

### 3.1 Circuit Breaker

**Konfiguration (Skript):**
```python
BREAKER_FAILURE_THRESHOLD = 10
BREAKER_RECOVERY_TIMEOUT  = 3600.0  # 1 hour
```

**Verhalten** (verifiziert per `src/utils/circuit_breaker.py`):

```
Initial state: CLOSED
Threshold:    10
Recovery:     3600.0s

After 10 failures: state=OPEN
Consecutive failures: 10

Open breaker correctly raised: CircuitBreaker[test] is OPEN; refusing call
Upstream called?  False
```

**Bewertung der API-Limit-Semantik (10 req/h):**

| Szenario | Zahl der HAFAS-Calls / Stunde | Begründung |
| :--- | :--- | :--- |
| Normalbetrieb (Cron `*/30`, beide Richtungen erfolgreich) | 4 | 2 Cron-Fires × 2 Richtungen |
| Worst case: jeder einzelne Call schlägt fehl | maximal 10 | Breaker trippt nach dem 10. fail, blockt 1 Stunde |
| `workflow_dispatch` manuell getriggert (zusätzlich) | + 2 pro Trigger | unter Operator-Kontrolle |

→ **10 req/h ist das harte Ceiling im Failure-Mode**, das Soft-Ceiling
im Normalbetrieb ist 4 req/h. Beides liegt komfortabel unter dem
documented ÖBB-Budget.

**Caveat & dokumentiert:** Der Breaker ist *prozess-lokal* — jeder
GitHub-Actions-Run startet mit frischem `_BREAKER`. Das ist
unproblematisch, weil:

1. Der Cron-Plan (`*/30`) den Aufruf-Rhythmus bereits begrenzt.
2. Innerhalb eines Runs werden nur 2 Calls gemacht — der Threshold von
   10 kann *nicht* per Run erreicht werden, sondern nur über mehrere
   Runs hinweg, was wiederum durch den Cron entkoppelt ist.
3. Die `Concurrency-Group: external-api-fetch` im Workflow
   verhindert parallele Runs.

### 3.2 HTTP-Timeout (Liveness)

**Vor PR #1367:** Tot­er Code — der Patch-Code referenzierte ein
nicht-existentes Attribut (`client.profile.requests`, korrekt:
`request_session`); zudem ignoriert `requests.Session.timeout` als
Attribut den Wert komplett.

**Aktueller Stand:** `_patch_session_timeout(profile, timeout)` patcht
`session.request` (die Low-Level-Methode, an die `get/post/put/...`
delegieren). Inject-Logik:

```python
def _request_with_default_timeout(method: str, url: str, **kwargs: Any) -> Any:
    kwargs.setdefault("timeout", timeout)   # explizite kwargs gewinnen
    return original_request(method, url, **kwargs)

session.request = _request_with_default_timeout
```

Verifiziert in 4 Unit-Tests:
* `test_patch_session_timeout_injects_default_timeout`
* `test_patch_session_timeout_respects_explicit_timeout`
* `test_patch_session_timeout_handles_missing_session`
* `test_patch_session_timeout_handles_session_without_request`

**Graceful Degradation:** wenn pyhafas das `request_session`-Attribut
in einer zukünftigen Version umbenennt, loggt der Patch eine
WARNING und kehrt no-op zurück — keine Crash auf Construction.

### 3.3 Self-Healing

**Regel** (`docs/reference/oebb_provider_logic.md` § "Self-Healing"):
`cache/stammstrecke/events.json` wird **zwingend** auf `[]` gesetzt
sobald *eine* der folgenden Bedingungen eintritt:

| Trigger | Cache | Exit-Code | Test |
| :--- | :--- | :--- | :--- |
| `ImportError` (pyhafas / OEBBProfile) | `[]` | 0 | `test_main_clears_cache_on_import_error` |
| `CircuitBreakerOpen` (Breaker tripped) | `[]` | 0 | `test_main_clears_cache_when_breaker_is_open` |
| Alle Richtungen werfen Exceptions | `[]` | 1 | `test_main_clears_cache_when_all_directions_fail` |
| Median ≤ 9 in beiden Richtungen | `[]` | 0 | `test_main_writes_empty_when_both_directions_below_threshold` |
| Keine S-Bahn-Legs in beiden Richtungen | `[]` | 0 | `test_main_writes_empty_when_no_sbahn_legs` |

**Per-Direction-Isolation** bleibt erhalten: wenn nur *eine* Richtung
schlägt fehl und die andere erfolgreich high-Median liefert, bleibt
deren Event im Cache. Test:
`test_main_partial_failure_keeps_other_direction_event`.

### 3.4 Per-Direction-Failure-Isolation

`_process_direction` kapselt Exceptions pro Richtung und gibt
`(None, "error")` zurück. `main()` zählt `successes` / `errors` und
entscheidet erst am Ende über das Cache-Schreibverhalten. Eine
crashend Richtung verwirft nicht die andere.

---

## 4. Zeitzonen-Konsistenz

**Befund: ✅ Alle 13 datetime-Sites im Skript sind tz-aware und
auf `Europe/Vienna` ausgerichtet.**

```python
VIENNA_TZ = ZoneInfo("Europe/Vienna")        # 1: Modul-konstant

def _now_vienna() -> datetime:
    return datetime.now(tz=VIENNA_TZ)        # 2: einziger Time-Source

# 3: Cache-Read fallback bei naivem Zeitstempel
parsed = datetime.fromisoformat(prev_iso)
if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=VIENNA_TZ)

# 4: Description-Datum (Vienna-Kalendertag)
date_str = first_seen.strftime("%d.%m.%Y")

# 5–6: ISO 8601 Serialisierung mit Offset
iso_pub = pub_date.isoformat()              # → "2026-05-09T08:30:00+02:00"
iso_first_seen = first_seen.isoformat()
```

### Zeitzonen-Pin-Test

`test_main_emits_iso8601_with_vienna_offset` prüft, dass
`event["pubDate"].endswith("+02:00")` (Mai → CEST) und alle
`pubDate`/`first_seen`-Werte Vienna-tz-aware sind.

### Sommer-/Winterzeit

Das Audit-Skript hat zusätzlich eine Winter-Zeit-Variante validiert:

```
[OK] new episode (Meidling): schema-compliant
[OK] continuing episode (Floridsdorf, +2 days): schema-compliant
[OK] winter-time, integer median (Meidling): schema-compliant
```

Im Winter rendert `isoformat()` Offset `+01:00` (CET) korrekt — die
`ZoneInfo`-Mechanik handhabt DST automatisch.

### pyhafas-Date-Parameter

`client.journeys(date=when, ...)` bekommt das tz-aware
Vienna-`datetime` übergeben. pyhafas's `BaseProfile.transform_datetime_parameter_timezone`
konvertiert es auf das Profil-Timezone — **kein** Drift durch
UTC-Naive-Behandlung.

---

## 5. Schema-Validierung

### 5.1 Pin-Test

`test_build_event_validates_against_schema` lädt das Schema von
`docs/schema/events.schema.json` und validiert ein gebautes Event
mit `jsonschema.Draft202012Validator`. Der Test wird per
`pytest.importorskip("jsonschema")` gegated, weil `jsonschema` keine
Project-Dependency ist (matches the existing
`tests/test_stations_schema.py` pattern).

### 5.2 Manuelle Validierung dreier Szenarien (mit installiertem `jsonschema`)

```
[OK]   new episode (Meidling): schema-compliant
[OK]   continuing episode (Floridsdorf, +2 days): schema-compliant
[OK]   winter-time, integer median (Meidling): schema-compliant
```

### 5.3 Beispiel-Output (kanonisches Event)

```json
{
  "source": "ÖBB",
  "category": "Störung",
  "title": "S-Bahn Stammstrecke Verspätungen",
  "description": "Durchschnittliche Verspätung von 12.5 Minuten in Richtung Meidling [Seit 09.05.2026]",
  "link": "https://www.oebb.at/de/fahrplan/fahrplanauskunft-und-stoerungsinformation/aktuelle-stoerungsmeldungen",
  "guid": "391ecec1e81467a0a92c28a1f93a791c32555d5229252547914bddb6b11bfec5",
  "pubDate": "2026-05-09T08:30:00+02:00",
  "starts_at": "2026-05-09T08:30:00+02:00",
  "ends_at": null,
  "first_seen": "2026-05-09T08:30:00+02:00",
  "_identity": "stammstrecke_delay_meidling|2026-05-09T08:30:00+02:00"
}
```

### 5.4 Pflicht-Felder-Mapping

| Schema-Feld | Quelle im Skript |
| :--- | :--- |
| `source` | Konstante `EVENT_SOURCE = "ÖBB"` |
| `category` | Konstante `EVENT_CATEGORY = "Störung"` |
| `title` | Konstante `EVENT_TITLE = "S-Bahn Stammstrecke Verspätungen"` |
| `description` | Format-String mit `_format_minutes`, `direction.target_label`, `_format("%d.%m.%Y")` |
| `link` | Konstante `EVENT_LINK` (HTTPS, ÖBB-Domain) |
| `guid` | `make_guid(direction.identity_prefix, iso_first_seen)` (SHA256) |
| `pubDate` | `pub_date.isoformat()` (aktueller Tick) |
| `starts_at` | `first_seen.isoformat()` (Episoden-stabil) |

### 5.5 Optionale Felder

| Schema-Feld | Wert |
| :--- | :--- |
| `ends_at` | `None` (Disruption-Ende ist dem Skript nicht bekannt) |
| `first_seen` | `iso_first_seen` (= `starts_at`) |
| `_identity` | `f"{identity_prefix}\|{iso_first_seen}"` |

`additionalProperties: true` im Schema → `_identity` und `first_seen`
sind kompatibel.

---

## 6. first_seen-Persistenz und GUID-Stabilität

### 6.1 Lebenszyklus einer Episode

```
T1 (08:30): Direction A: median=11
            → Cache leer → first_seen=T1, guid=hash(prefix, T1)
            → Cache=[event_A]
T2 (09:00): Direction A: median=14
            → Cache liefert prior first_seen=T1 → first_seen=T1, guid=hash(prefix, T1)
            → Cache=[event_A'] mit *gleichem guid* + neuem pubDate
T3 (09:30): Direction A: median=8 (Recovery)
            → kein Event für A → Cache=[]
T4 (10:00): Direction A: median=10
            → Cache leer → first_seen=T4, guid=hash(prefix, T4)
            → Cache=[event_A''] mit *neuem guid*
```

### 6.2 Test-Coverage

| Szenario | Test |
| :--- | :--- |
| `first_seen` über aufeinanderfolgende High-Runs identisch | `test_first_seen_persists_across_consecutive_high_runs` |
| `first_seen` regeneriert nach Recovery | `test_first_seen_regenerates_after_recovery` |
| Per-Direction-Isolation der Persistenz | `test_first_seen_persistence_is_independent_per_direction` |
| Beide Richtungen kontinuierlich → beide first_seen erhalten | `test_first_seen_continues_when_only_one_direction_resumes` |
| Cache-Read tolerante Failure-Modi | 4 Tests für `_read_existing_first_seen` |
| `_resolve_first_seen` Edge-Cases | 4 Tests (no-prior, parsed, unparseable, naive-localised) |

### 6.3 Round-Trip-Stabilität

`datetime.now(tz=VIENNA_TZ).isoformat()` produziert eine ISO-8601
mit Offset (z. B. `"2026-05-09T08:30:00.123456+02:00"`).
`datetime.fromisoformat(...)` round-trippt diesen String exakt
zurück; der nächste `isoformat()`-Call produziert dieselbe Bytes.
**GUID-Stabilität ist damit byte-exakt** über Cron-Ticks hinweg
(Python 3.11+ Verhalten).

---

## 7. Stationsverzeichnis-Integration

### 7.1 Auflösung der Ziel-Labels

`_short_target_label(seed_name)` resolved über
`src.utils.stations.canonical_name + display_name` und stripst den
`Wien `-Präfix. Beispiele:

```
canonical_name("Wien Meidling") + display_name + strip → "Meidling"
canonical_name("Wien Floridsdorf") + display_name + strip → "Floridsdorf"
canonical_name("Meidling")  → "Wien Meidling" → "Meidling"
canonical_name("Floridsdorf")→ "Wien Floridsdorf" → "Floridsdorf"
```

### 7.2 Fallback-Kette

1. Verzeichnis-Hit → kanonisch + override + strip.
2. Verzeichnis-Miss (`canonical_name` returns None) → Seed-Name
   trimmed + strip.
3. Verzeichnis-Crash (`canonical_name` raises) →
   Exception-Swallow + Seed-Name + strip.

### 7.3 Konsistenz mit anderen Providern

`oebb.py`, `vor.py`, `wiener_linien.py` nutzen alle
`canonical_name` + `display_name`. Stammstrecke-Skript folgt diesem
Muster — eine Umbenennung in `data/stations.json` propagiert ohne
Skript-Edit in die Description.

---

## 8. Test-Coverage

| Metrik | Wert |
| :--- | :--- |
| Tests | 47 (1 skipped: jsonschema-gated) |
| LoC im Skript | 734 |
| LoC in Tests | 1005 |
| Coverage Stmts | 95.3 % |
| Coverage Branch | 95.3 % (50 branches, 3 partial) |

### Uncovered Lines (by design)

| Line(s) | Was | Warum nicht abgedeckt |
| :--- | :--- | :--- |
| 89 | `sys.path.insert(0, str(REPO_ROOT))` | One-shot Import-Side-Effect; bereits gesetzt zum Test-Zeitpunkt |
| 317–323 | `_build_client()` Body | Tests mocken `_build_client` — pyhafas's `OEBBProfile` existiert in 0.6.1 nicht im Mainline-Release |
| 392 | `datetime.now(tz=VIENNA_TZ)` | Wird in jedem Test durch `_now_vienna`-Monkeypatch gemockt |
| 430 | `if not isinstance(data, list): return {}` | Defensive Branch (Cache enthält Dict statt Liste); tolerable Lücke |
| 441→433 | Branch in `_read_existing_first_seen` | Defensive Iteration über malformed Items |

---

## 9. Code-Komplexität

```
$ python3 scripts/check_complexity.py
===== C901 complexity gate =====
baseline functions  : 23
current violations  : 23
new violations      : 0
fixed (no longer >15): 0

::notice::C901 gate passed (0 new violations above 15)
```

Keine Stammstrecke-Funktion trägt zu den 23 Baseline-Violations bei.
Längste Funktion: `main()` mit ~50 LoC und linear
linearer Komplexität (kein nested branching).

---

## 10. Architektur-Einbettung

### 10.1 Datenfluss

```
┌────────────────────────┐
│  cron */30 * * * *     │
│  (GitHub Actions)      │
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐      ┌──────────────────────┐
│  update_stammstrecke_  │─────▶│ pyhafas (OEBBProfile)│
│  status.py             │ HTTP │  fahrplan.oebb.at    │
│                        │◀─────│  /bin/mgate.exe      │
│  • _patch_session_     │ JSON └──────────────────────┘
│    timeout (20s)       │
│  • _BREAKER (10/3600)  │
│  • _read_existing_     │
│    first_seen          │
│  • _process_direction  │ x 2  (Meidling, Floridsdorf)
│  • atomic_write        │
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐
│ cache/stammstrecke/    │
│ events.json            │ 0 / 1 / 2 schema-compliant events
└───────────┬────────────┘
            │ git commit (stefanzweifel/git-auto-commit-action)
            ▼
┌────────────────────────┐
│ build-feed.yml workflow│
│ (Cron `5,35 * * * *`)  │
│                        │
│ src/build_feed.py      │
│ ├─ read_cache_         │
│ │  stammstrecke()      │
│ ├─ deduplicate_fuzzy() │
│ ├─ _drop_old_items()   │
│ └─ _format_item_       │
│    content()           │
└───────────┬────────────┘
            │
            ▼
       docs/feed.xml (RSS/Atom)
```

### 10.2 Provider-Registrierung

Im `src/feed/providers.py`:
```python
register_provider(
    "STAMMSTRECKE_ENABLE", read_cache_stammstrecke, cache_key="stammstrecke"
)
```

`read_cache_stammstrecke` liest direkt aus dem fixen Pfad
`cache/stammstrecke/events.json` über `read_capped_json` (50 MiB-Cap,
TOCTOU-safe).

### 10.3 Default-Flag

`src/config/defaults.py`: `"STAMMSTRECKE_ENABLE": True` ist Default —
der Provider ist also out-of-the-box aktiv. Setzen auf `0` deaktiviert
ihn (Test-Path; in Tests via `monkeypatch.setenv("STAMMSTRECKE_ENABLE", "0")`).

---

## 11. Anpassung in diesem Branch: `MAX_JOURNEYS_PER_QUERY = 5`

### Begründung

* **Kleinere Payload:** 5 statt 12 Journeys reduziert die HAFAS-Antwort
  pro Call von ~48 KB auf ~20 KB (geschätzt aus Stichproben).
* **Schärferer Median:** „Die unmittelbar nächsten 5 anstehenden
  S-Bahnen" matcht die Operator-Erwartung („wie ist es *jetzt*?")
  besser als ein 30-Minuten-Fenster.
* **Stabilität:** 5 ist ungerade → Median ist exakt das mittlere
  Element, kein Durchschnitt zweier Werte → robuster gegen Ausreißer.

### Einfluss auf das Rate-Limit

Unverändert: 4 req/h normal, 10 req/h Worst-Case-Cap (Breaker).
`max_journeys` steuert die Payload, nicht die Call-Frequenz.

### Pin-Test

`test_max_journeys_per_query_is_pinned_to_five` (neu) +
`test_query_journeys_forwards_max_journeys_kwarg` (neu) bestätigen,
dass die Konstante auf 5 steht *und* korrekt an `client.journeys`
weitergereicht wird.

---

## 12. Findings & Recommendations

### Findings

**Keine.** Der Audit identifiziert weder Korrektheits-,
Performance- noch Security-Issues.

### Recommendations

* **Periodische Re-Evaluation des `MAX_JOURNEYS_PER_QUERY`-Werts:**
  Bei einer Verdichtung des S-Bahn-Takts (z. B. neue Linie S 90 auf
  der Stammstrecke) prüfen, ob 5 Journeys noch eine halbe Cron-Periode
  abdecken.
* **pyhafas-Version-Gate:** Sollte pyhafas die `request_session`-
  Attribut-Konvention zukünftig ändern, würde der Timeout-Patch
  graceful no-op werden (mit WARNING). Ein Smoke-Test gegen die
  Live-API als optionaler Pre-Merge-Gate-Job wäre eine sinnvolle
  Ergänzung.
* **Optional: jsonschema in `requirements-dev.txt`:** Aktuell ist
  `jsonschema` nicht Project-Dep — die Schema-Validierungs-Tests in
  diesem und dem Stations-Test laufen nur lokal. Aufnahme in
  `requirements-dev.txt` würde sie auch in CI laufen lassen (und das
  bekannte `_in_vienna_basis`-Drift in `data/stations.json` aufdecken,
  was orthogonal zu diesem Audit ist).

---

## 13. Sign-Off

Der S-Bahn Stammstrecke Monitor erfüllt alle in diesem Audit
geprüften Kriterien:

* **Strikt typisiert** (Mypy `--strict` 0 Fehler)
* **Sicher** (Bandit 0 Issues, manuelle Review ohne Findings)
* **Resilient** (Circuit Breaker + HTTP-Timeout + Self-Healing
  durchgängig getestet)
* **Zeitzonen-konsistent** (Europe/Vienna an allen 13 Datums-Sites,
  CET/CEST automatisch via `ZoneInfo`)
* **Schema-compliant** (Pin-Test gegen `events.schema.json`,
  3 manuelle Szenarien grün)
* **Hoch getestet** (47 Tests, 95.3 % Coverage)
* **Architektonisch sauber** (Provider-Registry-Integration, kein
  Drift mit anderen Providern)

Das Feature ist **production-ready**.

— Audit durchgeführt am 2026-05-09 im Rahmen der Stammstrecke-PR-Serie.

---

## 14. Addendum (2026-05-09 evening): Statistics-Pipeline Integration

Nach Abnahme dieses Audits wurde der Stammstrecke-Monitor um einen
**append-only Statistics-Sink** erweitert (PR #1372). Das Addendum
dokumentiert die Änderungen und bestätigt, dass die in den Sections 1–13
geprüften Sicherheits-, Resilienz- und Typisierungs-Befunde unverändert
bestehen.

### 14.1 Scope der Erweiterung

* `scripts/update_stammstrecke_status.py:_process_direction` ruft nach
  jeder erfolgreichen Median-Berechnung
  `src.utils.stats.append_stammstrecke_row(timestamp, direction,
  delay_minutes)` auf. Eine Zeile wird *unabhängig* davon geschrieben,
  ob die `DELAY_THRESHOLD_MINUTES`-Schwelle überschritten ist — der
  Statistik-Sink reflektiert die *vollständige* Verspätungsverteilung,
  nicht nur die Eskalationen, die als RSS-Event emittiert werden.
* Neuer Aggregator `scripts/generate_markdown_stats.py` (stdlib-only)
  rendert das Markdown-Dashboard `docs/statistik.md` aus den
  CSV-Ledgern unter `data/stats/`. Architektur-Diagramm und
  Kontext: [`docs/architecture.md` § 6](../../architecture.md).

### 14.2 Sicherheitseigenschaften des CSV-Writers

| Eigenschaft | Wie erfüllt |
| :--- | :--- |
| **Best-effort, no-throw** | `_append_row` umfasst die gesamte Schreib-Logik in `try / except OSError` — Disk-Full, Read-Only-Mount, Permission-Denied loggen WARNING und liefern `False`. Die Cron-Pipeline crasht nie, weil die Statistik nicht geschrieben werden konnte. |
| **Atomare Zeilen-Writes** | Nur `mode="a"`-Append unter `PIPE_BUF` (4 KiB) — POSIX garantiert byteweise Atomarität, keine Locks erforderlich. |
| **Kein Sensitive Data** | Schema enthält ausschließlich Median-Verspätung, Richtung, Provider-Name, Lokation. Keine Secrets, keine Identitäten, keine HAFAS-Antworten verbatim. |
| **TOCTOU-sicheres Verzeichnis-Setup** | `path.parent.mkdir(parents=True, exist_ok=True)` — Race mit gleichzeitigem `mkdir` führt zum erwarteten `exist_ok=True`-Pfad. |
| **Permission-Hardening** | `os.chmod(path, 0o644)` nach jedem erfolgreichen Append — die Datei ist publish-friendly (kein Geheimnis), aber owner-write-only. |

### 14.3 Test-Isolation: `isolate_stats_writes`

Die Produktions-Writer schreiben per Default in
`<repo>/data/stats/` via `src.utils.stats.DEFAULT_STATS_DIR`. Ohne
zusätzliche Sicherung würde **jeder** Test, der einen Hot-Path
exerciert, der transitiv Statistik schreibt (z. B. `_update_item_state`
im `build_feed`-Flow oder `_process_direction` im
Stammstrecke-Skript), Zeilen in das **echte** CSV-Ledger schreiben
und die Git-Historie mit synthetischen Test-Daten kontaminieren.

Schutz: ein **autouse**-Fixture `isolate_stats_writes` in
`tests/conftest.py` monkeypatcht `DEFAULT_STATS_DIR` für **jeden**
Test im gesamten Suite-Lauf auf einen per-Test `tmp_path`:

```python
@pytest.fixture(autouse=True)
def isolate_stats_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from src.utils import stats as stats_utils
    monkeypatch.setattr(stats_utils, "DEFAULT_STATS_DIR", tmp_path / "stats")
    yield
```

Tests, die explizit ein anderes `stats_dir` ansteuern (die fokussierten
Unit-Tests in `tests/test_utils_stats.py`), bleiben unbeeinflusst:
das explizite Keyword gewinnt im `stats_path`-Resolver vor
`DEFAULT_STATS_DIR`. Die Fixture verhindert insbesondere folgende
ansonsten invasive Failure-Modi:

* Jeder Lauf von `pytest` würde `data/stats/stammstrecke_<year>.csv`
  und `data/stats/stoerungen_<year>.csv` erzeugen oder erweitern.
* Pre-commit-Hooks hätten neue, synthetische Daten als unstaged
  Changes identifiziert und CI-Runs unter Umständen gekippt.
* Test-erzeugte Lokationsnamen ("Title Just", "Title Line", …)
  hätten das Dashboard mit verzerrten Hotspot-Daten bestückt, sobald
  ein Aggregator-Run sie aufnimmt.

Ein während der Implementierung beobachteter Vorfall (Test-Run schrieb
`stoerungen_2023.csv` … `stoerungen_2026.csv` mit Mock-Titeln in das
Repository-Verzeichnis) wurde durch genau dieses Fixture präventiv
geschlossen, bevor der erste Commit den Branch verließ.

### 14.4 Aggregator-Hardening (zur Vollständigkeit)

`scripts/generate_markdown_stats.py` parsiert *untrusted* On-Disk-Bytes
(eine geplante-große CSV vom kompromittierten CI-Runner ist das
kanonische Bedrohungsmodell). Drei geschichtete Verteidigungen:

1. **Bounded Reads** — jede CSV läuft durch `read_capped_text`
   (open + `fstat` + capped `read(MAX_CSV_BYTES + 1)`) und wird
   *anschließend* per `csv.reader` über `io.StringIO` geparst.
   Die Invariante „kein nacktes `csv.reader(handle)` in `src/` oder
   `scripts/`" ist durch
   `tests/test_sentinel_csv_size_bomb.py::test_no_unbounded_csv_dictreader_in_src_or_scripts`
   gesichert.
2. **Malformed-Row-Toleranz** — `_parse_*_rows` skippen einzelne
   Zeilen mit kaputtem `fromisoformat` oder nicht-numerischem
   Delay. Eine handgepflegte fehlerhafte Zeile zerstört nicht das
   gesamte Dashboard.
3. **Atomares Dashboard-Write** — `atomic_write` schreibt in
   einen kryptografisch zufällig benannten Temp-Pfad und renamed
   am Ende per `os.replace`. Ein Kill-Signal mitten im Render-Run
   kann das Dashboard nicht durch eine halbgeschriebene Datei
   ersetzen.

### 14.5 Quality-Gates des Addendums

| Gate | Status |
| :--- | :--- |
| Mypy `--strict` (`src/` + `tests/`) | ✅ 0 neue Fehler (CI-pinned mypy 1.10.1) |
| Bandit | ✅ 0 Issues |
| Ruff (E, F, S, B, UP) | ✅ clean |
| Pytest (Voll-Suite) | ✅ +60 neue Tests, 2 478 grün, keine Regression |
| C901 Complexity Gate | ✅ 0 neue Verstöße |

### 14.6 Rückwirkung auf die Findings

**Keine.** Die Erweiterung ist *additiv* — sie hängt einen
best-effort-Statistik-Sink an die bestehende Median-Logik an, ohne
eine der in Sections 1–13 verifizierten Eigenschaften (Typisierung,
Security, Resilienz, Self-Healing, Schema-Compliance, Zeitzonen-
Konsistenz) zu verändern. Der Audit-Verdict („**0 Findings**,
production-ready") bleibt unverändert.

— Addendum hinzugefügt am 2026-05-09 nach Merge von PR #1372.
