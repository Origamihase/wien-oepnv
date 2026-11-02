[Audit: S-Bahn Stammstrecke Monitor](oebb_stammstrecke_audit.md), Teil 2.

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
