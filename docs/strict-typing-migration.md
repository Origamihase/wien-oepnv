# Strict-Typing-Migration der Test-Suite

> ℹ️ **Status: Historische Referenz / Migration abgeschlossen.**
> Die Cluster-Migration **C25–C33** ist abgeschlossen, das mypy-strict-Gate
> (`.github/workflows/mypy-strict.yml`) läuft mit einer leeren Allowlist
> (`.mypy-baseline.txt` = 0 Zeilen = 0 strict-mode-Errors über `src/` und
> `tests/`). Dieses Dokument bleibt als **Konventions- und Patterns-Referenz**
> für künftige Test-Annotationen erhalten — die in §3 dokumentierten
> Patterns gelten unverändert. Die **„Anschluss-Roadmap" in §9** listet
> historische Folgearbeiten; die dort genannten Backlog-Zahlen
> (`[type-arg]` 22, `[unused-ignore]` 14, `[no-untyped-def]` 173,
> `[attr-defined]` 99) reflektieren den Stand **vor** Abschluss der
> Migration und sind nicht aktuell.

Dieses Dokument konsolidiert die Konventionen, Patterns und Werkzeuge,
die im Zuge der Cluster-Migration **C25–C33** beim strict-typing-konformen
Annotieren der `tests/`-Suite etabliert wurden, sowie das mit **C34**
eingeführte mypy-Allowlist-Gate.

Zielgruppe: Entwickler:innen und KI-Agenten, die weitere Cluster aus
der Backlog-Liste abarbeiten oder neue Tests so schreiben wollen, dass
sie das CI-Gate ohne Baseline-Update passieren.

## 1. Mypy-Konfiguration

Auszug aus `pyproject.toml`:

```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_unreachable = true
show_error_codes = true
pretty = true
ignore_missing_imports = true
explicit_package_bases = false
files = ["src"]
disable_error_code = ["import-untyped"]
enable_error_code = [
    "possibly-undefined",
    "ignore-without-code",
    "truthy-bool",
    "redundant-self",
]

[[tool.mypy.overrides]]
module = "tests.*"
ignore_errors = false
```

Trotz `files = ["src"]` ruft das CI-Gate explizit
`mypy --no-pretty src tests` auf — die override-Klausel zwingt
`tests/` in den strict-Modus. `--no-pretty` flattert jeden Fehler in
eine einzeilige, parsbare Form (Voraussetzung für die Diff-Mechanik
in §5).

## 2. Migrations-Übersicht

Die Migration wurde in **9 zielgerichtete Cluster (C25–C33)** zerlegt,
zusammen 159 Annotations-Items. Jedes Cluster ist homogen und bündelt
Issues mit identischem Annotations-Pattern.

| Cluster | PR    | Bucket                                          | Items |
|---------|-------|-------------------------------------------------|-------|
| C25     | #1143 | monkeypatch / caplog / tmp_path test funcs      | 22    |
| C26     | #1144 | Mock-Helper-Klassen-Methoden                    | 31    |
| C27     | #1145 | class_method residue (gemischte Files)          | 13    |
| C28     | #1146 | @patch-decorated tests (mock-injizierte Params) | 13    |
| C29     | #1147 | test_func A1 (no_params, std-Fixtures)          | 12    |
| C30     | #1148 | @pytest.fixture                                 | 16    |
| C31     | #1149 | test_func A2 (custom-Fixture-abhängig)          | 27    |
| C32     | #1150 | test-class-Methoden                             | 9     |
| C33     | #1151 | helper bucket (FINAL)                           | 16    |
| **Σ**   |       |                                                 | **159** |

Mit C34 (#1152) wurde das CI-Allowlist-Gate eingeführt; siehe §5.

## 3. Pattern-Katalog

### 3.1 Standard-pytest-Fixtures (Parameter-Typen)

| Fixture       | Annotation                  |
|---------------|-----------------------------|
| `monkeypatch` | `pytest.MonkeyPatch`        |
| `caplog`      | `pytest.LogCaptureFixture`  |
| `tmp_path`    | `Path` (aus `pathlib`)      |

Wenn `pytest` noch nicht importiert ist, wird `import pytest`
hinzugefügt. Der Top-Level-Import-Block bleibt sortiert.

Beispiel (C25):

```python
def test_retry_after_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ...
```

### 3.2 @pytest.fixture-Returns

| Fixture-Form               | Return-Annotation |
|----------------------------|-------------------|
| `yield`-only (side-effect) | `Iterator[None]`  |
| `yield <value>`            | `Iterator[T]`     |
| `return <value>`           | `T`               |
| kein Return                | `None`            |

`Iterator` aus `typing` (oder `collections.abc`).

Beispiel (C30, yield-Fixture mit dynamischem Modul-Yield):

```python
@pytest.fixture
def fallback_env() -> Iterator[Any]:  # yields dynamisch importiertes Modul
    ...
```

### 3.3 @patch-Decorator-Pattern

Mock-injizierte Parameter werden als `MagicMock` typisiert; der
Test selbst gibt `-> None` zurück.

```python
@patch("src.utils.http.verify_response_ip")
@patch("src.utils.http.validate_http_url")
def test_strip_headers_on_scheme_downgrade(
    mock_validate_url: MagicMock,
    mock_verify_ip: MagicMock,
) -> None:
    ...
```

Import: `from unittest.mock import MagicMock, patch` — die bestehende
`patch`-Importzeile wird in-place erweitert (alphabetische Sortierung,
kein neuer Import-Eintrag).

### 3.4 Test-Class-Methoden

Sowohl `unittest.TestCase`-Subklassen als auch pytest-Stil-Klassen
folgen demselben Pattern:

```python
class TestAtomicWriteSecurity(unittest.TestCase):
    def setUp(self) -> None:
        ...

    @patch("src.utils.files.os.unlink")
    def test_overwrite_false_uses_link(
        self,
        mock_unlink: MagicMock,
    ) -> None:
        ...
```

Spezialfall: `__init__` einer Klasse mit ausschließlich typisierten
Parametern wird von `mypy --strict` implizit als `-> None` akzeptiert.
Eine explizite Annotation ist trotzdem stilkonform und vermeidet
AST-Inventar-Drift bei künftigen Cluster-Scans.

### 3.5 Mock-Helper-Klassen

`DummySession`-artige Mocks der `requests`-Library-Surface (C26):

```python
class DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def __enter__(self) -> "DummySession":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        pass

    def iter_content(self, chunk_size: int = 1024) -> Iterator[bytes]:
        ...
```

`TracebackType` aus `types`. `Iterator` aus `typing` oder
`collections.abc`. Für Methoden, die die unstubbed `requests`-Surface
spiegeln (`get`, `request`, `merge_environment_settings`, …), ist
`Any` für Parameter zulässig — die Source-Library ist nicht
typisiert, eine konkretere Annotation würde Pseudo-Präzision
vorgaukeln.

Pre-emptive `[var-annotated]`-Fixes auf Klassen-Attribute sind Teil
des Patterns:

```diff
-        self.headers = {}
+        self.headers: dict[str, str] = {}
```

### 3.6 Test-Helper-Funktionen

Top-Level-Utilities, die weder Test, Fixture noch Klassen-Member sind
(C33).

```python
def _setup_fetch(
    monkeypatch: pytest.MonkeyPatch,
    traffic_infos: list[dict[str, Any]] | None = None,
    news: list[dict[str, Any]] | None = None,
) -> None:
    ...
```

Konkrete Typen, wo aus Body oder Aufrufern ableitbar (`MagicMock` für
Mock-Builder, `Path` für Filesystem-Helper, `ModuleType` für
dynamisches Importieren, `threading.Event` für Cross-Process-Sync).
`Any` mit Inline-Justification ist zulässig, wenn ein Source-Refactor
außerhalb des Cluster-Scope läge — z. B. dynamisch gesetzte
Modul-Attribute, `**overrides`-kwargs oder Closures mit dynamisch
zugewiesenem `_provider_cache_name`.

### 3.7 `Any` mit Inline-Justification

Wenn die zur Vollständigkeit nötige Body-Refaktorierung außerhalb des
Cluster-Scope liegt, wird `Any` mit einem Inline-Kommentar auf der
`def`-Zeile begründet:

```python
@pytest.fixture(scope="module")
def station_entries() -> Any:  # JSON-derived list of dicts; full typing requires body refactor
    ...
```

Konvention: Der Kommentar beschreibt die **Ursache** (JSON-Source,
dynamisches Attribut, untyped Library), nicht nur das *Was*. So
bleibt beim späteren Audit klar, warum der Workaround nötig war und
wann er wegfallen kann.

## 4. Mechanik-Regeln

### 4.1 Wrap-Threshold

Eine getypte Signatur wird Black-Style auf mehrere Zeilen umgebrochen,
sobald sie **≥100 Zeichen** wird, inklusive jeder Klassen-Einrückung
(4 Zeichen).

- Jeder Parameter auf eine eigene Zeile.
- **Trailing comma** nach dem letzten Parameter.
- Schließendes `)` plus Return-Annotation auf eigener Zeile.

Beispiel (C28, Wrap):

```python
def test_strip_headers_on_scheme_downgrade(
    mock_validate_url: MagicMock,
    mock_verify_ip: MagicMock,
) -> None:
```

Beispiel (C25, single-line bei < 100 ch):

```python
def test_x(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
```

### 4.2 Single-line Import-Anchor

Neue Imports werden — wenn möglich — durch in-place-Erweiterung einer
existierenden Importzeile angefügt. Vorteil: deterministisches +0/-0-
oder +1/-1-Diff statt zusätzlicher Import-Zeile, keine Sortierungs-
Verschiebung.

**Bevorzugt:**

```diff
-from unittest.mock import patch
+from unittest.mock import MagicMock, patch
```

**Nur wenn kein passender Anchor existiert:**

```diff
+import pytest
```

Innerhalb der erweiterten Importzeile bleibt die alphabetische
Sortierung gewahrt.

### 4.3 Paren-balanced Param-Count

Beim Wrappen einer Signatur werden **alle** Parameter auf eigene
Zeilen geschrieben — nicht nur die annotierten. Das vermeidet
visuell asymmetrische Mischformen wie

```python
def f(self,
    mock_x: MagicMock,
) -> None:
```

und hält den Stil zwischen handgeschriebenen und Black-formatierten
Signaturen einheitlich.

### 4.4 Kein `from __future__ import annotations`

Während der C25–C33-Migration wurde `from __future__ import
annotations` **nicht** hinzugefügt, auch wenn Forward-References
einzelne Cases vereinfachen würden. Begründung: einheitlicher Style
über die gesamte Test-Suite; Forward-Reference-Strings (`"DummySession"`)
decken alle vorgefundenen Cases ab.

### 4.5 Sparsam mit `cast()` und `# type: ignore`

C25–C33 fügen weder `cast()`-Calls noch `# type: ignore`-Kommentare
hinzu. Die einzige Ausnahme war C25 (`tests/test_reporting_github.py`):
drei `type: ignore[untyped-decorator]` auf `@responses.activate`,
weil die `responses`-Library nicht typisiert ist und das Annotieren
des Tests mypy zwingt, den Decorator strikt zu prüfen. Solche
Lokal-Suppressions sind erlaubt, müssen aber

a. den **error code** explizit angeben (`[untyped-decorator]`, nicht
   bloß `# type: ignore`),
b. im Commit-Message begründet werden.

`[ignore-without-code]` ist ohnehin in
`enable_error_code` aktiviert (siehe §1) und würde sonst als neuer
Error im Gate auftauchen.

## 5. Mypy-Strict-Gate (C34)

### 5.1 Workflow

`.github/workflows/mypy-strict.yml` läuft bei jedem PR sowie bei
Push auf `main`. Schritte:

1. Repo-Checkout, Python 3.11, `pip install -r requirements-dev.txt`.
2. `mypy --no-pretty src tests > /tmp/mypy-current.txt 2>&1`. Der
   Exit-Code wird ignoriert; das Gate prüft das Diff selbst.
3. Normalisierung: nur `error:`-Zeilen, Zeilen-/Spalten-Nummern
   gestrippt, sortiert.
4. Diff via `comm` gegen `.mypy-baseline.txt`.

### 5.2 Diff-Mechanik

```bash
new_count=$(comm -13 BASELINE current-normalized | wc -l)
fixed_count=$(comm -23 BASELINE current-normalized | wc -l)
```

- `comm -13`: Zeilen in `current`, nicht in `baseline` → **neue Errors**.
- `comm -23`: Zeilen in `baseline`, nicht in `current` → **gefixte Errors**.

### 5.3 Lenient-Mode

| Diff                  | Verhalten                                |
|-----------------------|------------------------------------------|
| neu > 0               | **Fail** (Liste der neuen Errors zeigen) |
| neu == 0, gefixt > 0  | **Pass** + Notice (Regen empfohlen)      |
| neu == 0, gefixt == 0 | **Pass** silent                          |

Begründung für Lenient: ein Fix ohne sofortigen Baseline-Regen soll
keine PR blockieren. Eine Strict-Mode-Eskalation, die auch *fixed*
Errors als Fail behandelt, ist ein bewusst zurückgehaltener
Roadmap-Punkt (siehe §9).

### 5.4 Pinned mypy-Version

`requirements-dev.txt`: `mypy>=1.10,<1.11`.

Begründung: Spätere mypy-Versionen detektieren mehr Issues. Die
Baseline und alle dokumentierten Counts setzen 1.10.x voraus. Ein
Upgrade verschiebt die Verteilung — siehe §9 für den Upgrade-Pfad.

## 6. Baseline regenerieren

### 6.1 Wann?

Regen ist erforderlich, sobald sich der Set der mypy-Errors
absichtlich ändert:

- Cluster-Fix entfernt mehrere Errors → Notice im CI fragt nach Regen.
- Neuer Code surfaced absichtlich Errors, die allowlistet werden
  sollen (selten — Standardansatz ist "fix in PR").

### 6.2 Prozedur

```bash
bash scripts/regen_mypy_baseline.sh
git add .mypy-baseline.txt
git commit -m "chore: regenerate mypy baseline post-<context>"
```

Das Skript:

1. Installiert `mypy>=1.10,<1.11` über `pip install -q`.
2. Ruft `python3 -m mypy --no-pretty src tests`.
3. Normalisiert (`grep` + `sed` + `sort`) und schreibt
   `.mypy-baseline.txt` neu.

### 6.3 PATH-Shadow-Caveat (lokal)

Lokale Sandboxes mit uv- oder pipx-managed mypy haben oft ein
PATH-früheres `mypy`-Binary, das **nicht** dem pinned 1.10.x
entspricht (z. B. ein uv-managed mypy 1.19). Das Regen-Skript ruft
deshalb explizit `python3 -m mypy` auf, damit der per
`pip install` ins aktuelle Environment gelegte Build benutzt wird.
CI ist davon nicht betroffen (frischer Runner, kein Shadow).

### 6.4 Drift-Caveat: Baseline mit installierten Deps

Die initiale Baseline (Commit `8b37cfc`) wurde versehentlich **ohne**
installierte Projekt-Dependencies generiert. mypy konnte dadurch
viele Imports nicht auflösen, und die Fehler-Verteilung war verzerrt:
476 Zeilen statt der echten **414**. Der Folge-Commit `3541a9b`
("ci: fix baseline drift — capture under installed deps") hat das
korrigiert. Faustregel: Das Regen-Skript läuft nur in einer Umgebung,
in der `pip install -r requirements-dev.txt` bereits durchgelaufen
ist.

## 7. Offene Backlog (historischer Stand C34, Baseline = 414 Zeilen)

> ℹ️ **Aktueller Stand (Mai 2026):** `.mypy-baseline.txt` ist **0 Bytes**
> — alle 414 ursprünglich allowlisteten Errors wurden zwischenzeitlich
> gefixt. `mypy --no-pretty src tests` läuft inzwischen ohne Errors
> durch (siehe `mypy-strict.yml`-Workflow); die Tabelle unten ist als
> Aufstellung des damaligen Backlogs erhalten, gibt aber **nicht** den
> aktuellen Zustand wieder. Wenn künftig wieder Errors allowlistet
> werden müssen (selten — Standardansatz ist „fix in PR"), spiegelt
> §6.2 das Regen-Verfahren.

| Code                    | Anzahl | Hauptklasse                                   |
|-------------------------|--------|-----------------------------------------------|
| `[no-untyped-def]`      | 173    | tests/-Funktionen ohne Annotation             |
| `[attr-defined]`        | 99     | Module-Attr-Set-Pattern, Re-Export-Issues     |
| `[type-arg]`            | 22     | bare `dict`/`list` ohne Typ-Parameter         |
| `[no-untyped-call]`     | 21     | Calls zu untyped Helpern (oft kaskadierend)   |
| `[var-annotated]`       | 16     | fehlende Variablen-Annotationen               |
| `[arg-type]`            | 16     | Type-Mismatches (potentielle echte Bugs)      |
| `[no-any-return]`       | 15     | `Any`-Returns in typisierten Funktionen       |
| `[unused-ignore]`       | 14     | stale `# type: ignore`-Kommentare             |
| `[ignore-without-code]` | 10     | `# type: ignore` ohne Error-Code              |
| `[unreachable]`         | 9      | toter Code (oft post `# type: ignore`-Removal)|
| sonstige                | 19     | union-attr, name-defined, dict-item, …        |
| **Σ**                   | **414**|                                               |

Ungefähr 24 Zeilen entfielen auf `scripts/` (über Imports ins Gate
gezogen), der Rest auf `tests/`.

C25–C33 waren **selektiv** zugeschnitten — nicht jeder Error in den
oben genannten Kategorien war im Cluster-Scope. Insbesondere:

- `[attr-defined]` braucht eine Investigation (Pattern:
  `module.fetch_events = mock_fn`-Style), bevor Test- vs.
  Source-Refactor entschieden werden kann.
- `[type-arg]` und `[unused-ignore]` sind die niedrigschwelligsten
  Folgekandidaten und gut für Validierungs-PRs des
  Baseline-Regen-Workflows.

## 8. Cluster planen — Heuristiken

### 8.1 Granularität

Ein Cluster soll **homogen** sein: identisches Pattern, identische
Annotations-Konvention. Heterogene Cluster (mehrere Patterns in einer
PR) machen Reviews brüchig und Baseline-Diffs unübersichtlich.

Größenrichtwerte aus C25–C33:

- 9–31 Items pro Cluster.
- 4–13 Files.
- 1 PR pro Cluster.

### 8.2 Reihenfolge

Cluster mit unklaren Abhängigkeiten zerlegen:

- **Fixtures vor Tests, die sie konsumieren.** C30 (fixtures) lief
  vor C31 (test_func A2) — letzteres hängt ab von ersterem.
- **Klein vor groß**, wenn das Pattern noch nicht etabliert ist
  (Validierungs-PR). Sobald das Pattern steht: groß-vor-klein für
  maximalen Durchsatz.

### 8.3 Commit-Message-Konvention

```
refactor(types): annotate <bucket-name> (C<N>)

<1–3 Zeilen Kontext: was, warum, scope-Grenze>

Conventions:
- <Pattern-Bullet 1>
- <Pattern-Bullet 2>

<Plan-Abweichungen, falls vorhanden, mit Begründung>

Imports: <Anker-Strategie>

Mypy delta: <pre> -> <post> (delta <N>: …; 0 new errors)
```

Klares **delta-Reporting** im Commit ist Pflicht, damit das Cluster
gegen das Baseline-Diff im Review verifiziert werden kann.

## 9. Anschluss-Roadmap

Bekannte Folgearbeiten nach C34:

- **Any-Audit.** Während C25–C33 eingeführte `Any`-Annotationen mit
  Justification — review, ob inzwischen konkrete Typen ableitbar
  sind (insbesondere die helper-bucket-Fälle aus C33: `_emit_item_str`,
  `_base_event`, `_make_loader`, `_stop`).
- **Mypy-Upgrade.** Pin 1.10.x → 1.11.x → später LTS-Window für 1.19+.
  Pro Schritt: Baseline regenerieren und das Diff als
  PR-Review-Material nutzen.
- **Strict-Mode-Gate.** Lenient → strict eskalieren, sodass auch
  *fixed* Errors ohne Regen die Pipeline brechen. Trade-off: hält
  Baseline tight, kostet einen Regen-Commit pro Fix.
- **Lokaler Gate-Runner.** Den Allowlist-Diff in
  `scripts/run_static_checks.py` integrieren, damit Devs den Check
  vor dem Push laufen können.
- **Backlog-Reduktion.** `[type-arg]` (22) und `[unused-ignore]` (14)
  als nächste Low-Risk-Cluster; `[no-untyped-def]` (173) als größter
  homogener Brocken nach Files aufgeteilt; `[attr-defined]` (99)
  erst nach Investigation, ob Test- oder Source-Refactor.
- **Doku-Sync.** `AGENTS.md` und `CONTRIBUTING.md` ergänzen um den
  Hinweis auf das mypy-strict-Gate, die Regen-Prozedur und die
  pinned-Version.
