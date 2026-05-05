# `[attr-defined]`-Cluster — Investigations-Findings (D.1)

Dieses Dokument klärt die Strategie für den verbleibenden
`[attr-defined]`-Cluster (98 Errors) der strict-typing-Migration.
Ziel: per Sub-Kategorie eine konkrete Fix-Empfehlung formulieren,
sodass A.1.5 ohne weitere Investigation gestartet werden kann.

Quelle: `mypy --no-pretty src tests` mit pinned mypy 1.10.x gegen
die post-A.1.2-Baseline (126 Zeilen).

## 1. Verteilung nach Macro-Kategorie

| Kategorie                            | Items | Pattern                                                                                  |
|--------------------------------------|-------|------------------------------------------------------------------------------------------|
| **Module-Attr-Set (dynamic)**        | 57    | `module.X = mock` auf `types.ModuleType`-Instanzen, oder `monkeypatch.setattr(mod, "X")` |
| **Re-Export (`no_implicit_reexport`)** | 34  | `from src.X import Y` wo Y in src.X intern verwendet, nicht expliziter Re-Export         |
| **Mock-Class-Attr / Sonstige**       | 7     | Test-Mock-Klasse hat Attribute via Body-Set, mypy sieht sie nicht; plus 2 Type-Narrowing |
| **Σ**                                | **98**|                                                                                          |

## 2. Kategorie 1 — Module-Attr-Set (57 Items)

### 2.1 Verteilung

| Sub-Kategorie | Items | Beispiel                                                       |
|---------------|-------|----------------------------------------------------------------|
| `fetch_events`| 51    | `wl = types.ModuleType("providers.wiener_linien"); wl.fetch_events = lambda: []` |
| `OUT_PATH`    | 4     | `monkeypatch.setattr(build_feed, "OUT_PATH", tmp_path)` |
| `PROVIDERS`   | 1     | (build_feed test setup) |
| `DEFAULT_PROVIDERS` | 1 | (build_feed test setup) |

### 2.2 Pattern

Über 51 Tests existiert ein `_import_build_feed(monkeypatch)`
Helper, der dynamisch `providers.wiener_linien` /
`providers.oebb` / `providers.vor` als `types.ModuleType` erzeugt
und `fetch_events` als Lambda anhängt:

```python
def _import_build_feed(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    wl.fetch_events = lambda: []  # ← [attr-defined]
    oebb = types.ModuleType("providers.oebb")
    oebb.fetch_events = lambda: []  # ← [attr-defined]
    ...
```

`types.ModuleType` ist in den typeshed-Stubs ohne `__setattr__`-
Override typisiert — beliebige Attribute zu setzen löst
`[attr-defined]` aus. Per `monkeypatch.setattr(real_module, "X")`
auf existierende Module ist das gleiche Problem (mypy kennt das
Attribut auf der Source-Seite nicht).

### 2.3 Empfehlung — Pattern-Helper

Da das gleiche Konstrukt in **>20 Test-Files dupliziert** wird,
ist die produktivste Lösung eine **getypte Helper-Klasse oder
eine Annotation auf dem `types.ModuleType`-Wert via cast(Any, …)**:

**Variante A — `setattr()`** (per Aufruf):

```python
setattr(wl, "fetch_events", lambda: [])
```

`setattr` ist mypy-strict-konform; die dynamische Natur ist
explizit. Kein Cast nötig.

**Variante B — `cast(Any, module)`-Wrapper** (per Test):

```python
from typing import Any, cast
wl = types.ModuleType("providers.wiener_linien")
cast(Any, wl).fetch_events = lambda: []
```

**Variante C — Shared Test-Helper** (DRY):

```python
# tests/_module_factory.py (NEW FILE)
from types import ModuleType
from typing import Any, Callable

def make_provider_module(name: str, fetch_events: Callable[..., Any] | None = None) -> ModuleType:
    module = ModuleType(name)
    if fetch_events is not None:
        setattr(module, "fetch_events", fetch_events)
    return module
```

**Empfohlen: Variante A** für den ersten Sweep, Variante C als
Folge-Refactor (weiteres B.x-Cluster). Variante A löst alle 51
fetch_events-Issues mit minimalen Diff-Edits (jede Zeile ist eine
in-place-Replacement) und keinem zusätzlichen `Any` an
sichtbaren Annotation-Sites.

### 2.4 Sub-Cluster-Vorschlag

- **A.1.5-Sub-1 (51 Items):** `fetch_events`-Pattern in allen
  Tests via `setattr()` auflösen. Mechanisch, replace_all-fähig
  innerhalb jeder Datei (jeder Test hat die `_import_build_feed`-
  Definition lokal).
- **A.1.5-Sub-2 (6 Items):** Übrige `Module has no attribute`
  Fälle (`OUT_PATH`, `PROVIDERS`, `DEFAULT_PROVIDERS`) — meist
  `monkeypatch.setattr` auf real existierenden Modulen, wo das
  Attribut tatsächlich später gesetzt wird; per `setattr()` fixen
  oder per `# type: ignore[attr-defined]`.

## 3. Kategorie 2 — Re-Export (34 Items)

### 3.1 Verteilung

| Sub-Kategorie                                          | Items |
|--------------------------------------------------------|-------|
| `src.build_feed.feed_config`                           | 10    |
| `scripts.update_vor_stations.vor_provider`             | 5     |
| `src.utils.env.sanitize_log_message`                   | 4     |
| `src.build_feed.RunReport`                             | 4     |
| `scripts.update_vor_cache.MAX_REQUESTS_PER_DAY`        | 3     |
| `scripts.update_station_directory.subprocess`          | 3     |
| `src.providers.vor.os`                                 | 2     |
| `src.providers.oebb.station_info`                      | 1     |
| `src.cli.build_feed_module`                            | 1     |
| `src.build_feed.ThreadPoolExecutor`                    | 1     |

### 3.2 Pattern

Mypy strict aktiviert `no_implicit_reexport`. Folge: `from X
import Y` (ohne `as Y`) macht `Y` zu einem privaten Import in X
— Tests können nicht `from src.X import Y` verwenden.

Beispiel (`src/build_feed.py:33`):

```python
from feed import config as feed_config
```

Hier ist der Alias-Name `feed_config` ≠ Original-Name `config`
— mypy behandelt das als impliziten Import. Tests, die
`from src.build_feed import feed_config` machen, schlagen fehl.

### 3.3 Empfehlung — Per-Kategorie

**3.3.1 Library-Imports re-exposed** (subprocess, os,
ThreadPoolExecutor — 6 Items):

Tests greifen auf `module.subprocess` etc. zu, weil sie eine
Funktion von src patchen wollen, die intern subprocess verwendet.
Diese Tests sollten den Original-Library-Import direkt
verwenden:

```python
# Statt:
monkeypatch.setattr("scripts.update_station_directory.subprocess", mock)
# Besser:
import subprocess
monkeypatch.setattr(subprocess, "run", mock)
```

Aber Vorsicht: das ändert das Mock-Scope! Wenn das Modul `import
subprocess` einmal beim Import ausführt, ist der globale
subprocess-Patch nicht effektiv für diesen Modul-spezifischen
Cache. Die existierenden `monkeypatch.setattr("module.subprocess", …)`-
Calls sind oft **deliberat gewählt**.

**Pragmatisch:** in src-Modulen die Library-Imports explizit
re-exportieren via `__all__` oder `as`-Aliasing:

```python
# src/providers/vor.py
import os as os  # explicit re-export für Test-Kompatibilität
# oder:
__all__ = [..., "os"]
```

**3.3.2 Project-Module re-exposed** (feed_config, vor_provider,
RunReport, sanitize_log_message, station_info, build_feed_module,
MAX_REQUESTS_PER_DAY — 28 Items):

Diese sind echte Re-Exports der internen Module-API. Per
`__all__`-Liste explizit re-exportieren:

```python
# src/build_feed.py
__all__ = [
    "feed_config",
    "RunReport",
    "ThreadPoolExecutor",
    # ... bestehende Public-API ...
]

# src/utils/env.py
__all__ = ["read_secret", "sanitize_log_message", ...]

# scripts/update_vor_stations.py
__all__ = ["vor_provider", ...]
```

Mit `__all__` zählt jeder Listed-Name als explizit re-exportiert
(mypy-konform). Keine Test-Side-Änderungen nötig.

**Achtung Side-Effect:** Erstmaliges Hinzufügen von `__all__`
zu einem Modul **scoped** den Modul-Namespace — alle nicht-in-
`__all__`-aufgeführten Top-Level-Namen werden für `from X import
*`-Konsumenten unsichtbar. In diesem Projekt gibt es vermutlich
kein `import *`, aber zur Sicherheit: alle Top-Level-Namen
auflisten, die Tests verwenden.

### 3.4 Sub-Cluster-Vorschlag

- **A.1.5-Sub-3 (28 Items):** `__all__`-Listen in 7 src-Modulen
  hinzufügen. Source-side, low-risk.
- **A.1.5-Sub-4 (6 Items):** Library-Re-Exports
  (subprocess/os/ThreadPoolExecutor). Test-side oder via
  `__all__` mit ausnahmsweisen Library-Re-Exports.

## 4. Kategorie 3 — Mock-Class-Attr und Sonstige (7 Items)

### 4.1 Verteilung

| Item                                                              | Anzahl | Strategie                            |
|-------------------------------------------------------------------|--------|--------------------------------------|
| `"RawMock" has no attribute "_connection"`                        | 2      | Klasse explizit annotieren           |
| `"DummySession" has no attribute "auth"`                          | 2      | DummySession um `auth`-Attribut erweitern |
| `tuple[…] has no attribute "get"`                                 | 1      | Vakuöser Test (A.1.8 dokumentiert) — Suppress oder Fix |
| `"str" has no attribute "tzinfo"`                                 | 1      | Real Type-Narrowing — runtime guard prüfen oder `isinstance` |
| `"object" has no attribute "return_value"`                        | 1      | MagicMock typing — cast |

### 4.2 Empfehlung

**A.1.5-Sub-5 (7 Items):** Heterogen, kein blanket-Pattern. Per
Item:
- RawMock / DummySession: Attribute auf Klassen-Level annotieren
  (z. B. `_connection: Any = ...`, `auth: Any = None`).
- Tuple-Test: Mit existierender A.1.8-Dokumentation als latent-bug
  zusammenführen, oder type:ignore.
- str.tzinfo: Real-bug-Verdacht — Body inspection nötig.
- object.return_value: MagicMock-Annotation oder cast.

## 5. Aggregierte A.1.5-Roadmap

| Sub-PR     | Pattern                              | Items | Risk  | Effort |
|------------|--------------------------------------|-------|-------|--------|
| A.1.5-Sub-1| `fetch_events` via `setattr()`       | 51    | low   | low (mechanisch) |
| A.1.5-Sub-2| Übrige Module-Attr-Set               | 6     | low   | low |
| A.1.5-Sub-3| `__all__` in Project-Modulen         | 28    | low-med | low (source-side) |
| A.1.5-Sub-4| Library-Re-Exports                   | 6     | med   | med (Mock-Scope-Fragen) |
| A.1.5-Sub-5| Mock-Class-Attr / Sonstige           | 7     | med   | med (heterogen) |
| **Σ**      |                                      | **98**|       |        |

**Empfohlene Sub-PR-Reihenfolge:**

1. **Sub-1** zuerst (51 Items, größter mechanischer Win).
2. **Sub-3** als zweites (28 Items, source-side `__all__`).
3. **Sub-2** + **Sub-4** + **Sub-5** in Folge (kleinere, gemischtere).

## 6. Pre-A.1.5 Vorbereitungs-Punkte

1. **B.2-Doc-Update:** Die hier identifizierten Patterns
   (`setattr()` für `types.ModuleType`-Attr-Set, `__all__` für
   Re-Exports) sollten als §3.8 / §3.9 in die Lessons-Learned
   aufgenommen werden, sobald A.1.5 abgeschlossen ist.
2. **`mock_utils.py`:** Wenn der Helper-Refactor (Variante C aus
   §2.3) als Folgearbeit gewünscht ist, könnte `tests/mock_utils.py`
   ein zentraler Ort sein (existiert bereits für andere
   Mock-Helper).

## 7. Out-of-Scope-Notes

- **Library-Stubs für dnspython** (siehe A.1.7 commit-message):
  würde 4 Items aus Kategorie 1 vermeiden, ist aber separater
  Aufwand (`types-dnspython` zu `requirements-dev.txt`
  hinzufügen + erneuter Baseline-Regen).
- **mypy-Upgrade** (siehe §9 strict-typing-migration.md): mypy
  1.11+ könnte das Verhalten von `no_implicit_reexport` ändern
  oder neue Inferenzen bringen, die einige der Items von selbst
  auflösen.
