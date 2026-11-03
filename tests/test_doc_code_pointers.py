"""Wächter: Ortsangaben „`Name` in `Datei.py`“ der Doku zeigen auf die Datei, die den Namen definiert.

Seit der 700-Zeilen-Regel (Etappen 6 bis 8, 2026-11-02/03) liegt Code der
Skripte und langer Module in neuen Modulen unter ``src/``; Skript und Fassade
reichen die Namen nur weiter. Die unabhängige Prüfung vom 2026-11-03 fand fünf
Stellen der Doku, die noch die alte Datei nannten (etwa ``WORKFLOW_SECRETS``
in ``scripts/health_check.py`` statt ``src/health_check/workflow_secrets.py``).
Wer dort ändern wollte, landete bei einer Weiterreichung.

Geprüft werden die gültigen Seiten (``AGENTS.md``, ``README.md``,
``CONTRIBUTING.md`` und ``docs/**/*.md`` außer ``docs/archive/``) in den zwei
Formen ```Name` in `Datei.py``` (auch „aus“, „von“) und ```Datei.py:Name```.
Die Datei muss existieren und den Namen selbst definieren (Funktion, Klasse
oder Zuweisung, auch in einer Klasse). Das CHANGELOG bleibt außen vor, es
beschreibt den Stand seines Tages.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_IN_FORM = re.compile(r"`(\w+)(?:\(\))?`\s+(?:in|aus|von)\s+`((?:src|scripts)/[\w/]+\.py)`")
_COLON_FORM = re.compile(r"`((?:src|scripts)/[\w/]+\.py):([A-Za-z_]\w*)")


def _pages() -> list[Path]:
    pages = [ROOT / name for name in ("AGENTS.md", "README.md", "CONTRIBUTING.md")]
    pages += [p for p in sorted((ROOT / "docs").rglob("*.md")) if "archive" not in p.relative_to(ROOT).parts]
    return [p for p in pages if p.is_file()]


@functools.cache
def _defined(rel: str) -> frozenset[str]:
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(n.id for target in node.targets for n in ast.walk(target) if isinstance(n, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names)


def _pointers() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for page in _pages():
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            where = f"{page.relative_to(ROOT)}:{number}"
            found += [(where, m.group(2), m.group(1)) for m in _IN_FORM.finditer(line)]
            found += [(where, m.group(1), m.group(2)) for m in _COLON_FORM.finditer(line)]
    return found


def test_doc_pointers_name_the_defining_file() -> None:
    wrong = []
    for where, rel, name in _pointers():
        if not (ROOT / rel).is_file():
            wrong.append(f"{where}: `{rel}` gibt es nicht ({name})")
        elif name not in _defined(rel):
            wrong.append(f"{where}: `{name}` steht nicht in `{rel}`")
    assert not wrong, "\n".join(wrong)


def test_the_guard_sees_pointers() -> None:
    # Plausibilität: die Doku nennt Dutzende solcher Stellen; fällt die Zahl
    # stark, liest der Wächter die Formen nicht mehr.
    assert len(_pointers()) >= 20
