"""Scripts import project modules only as ``src.…`` (code audit 2026-10-31, finding A).

With ``src/`` itself on ``sys.path`` a script imported ``utils.cache`` while
the feed imported ``src.utils.cache``: the same file as two module objects
with separate state (the cache-alert hooks, the HTTP session cache and its
cleanup thread), and names mypy could not resolve. The rule is documented in
docs/development.md, "Skripte im Überblick". ``scripts/update_wl_stations.py``
imports its ``src`` helpers lazily inside functions; that is still ``src.…``.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((ROOT / "scripts").glob("*.py"))
# ``build_feed``, ``cli``, ``config``, ``feed``, ``providers``, ``utils`` ...:
# importing one of these bare means ``src/`` is on the path.
SRC_TOP_LEVEL = frozenset(
    (entry.stem if entry.is_file() else entry.name)
    for entry in (ROOT / "src").iterdir()
    if not entry.name.startswith("__") and (entry.is_dir() or entry.suffix == ".py")
)
# Scripts that put ``src/`` on ``sys.path`` before the audit.
FORMERLY_DOUBLE = (
    "update_baustellen_cache",
    "health_check",
    "configure_feed",
    "scan_secrets",
    "validate_stations",
)


def _bare_src_imports(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(
                f"{node.lineno}: import {alias.name}"
                for alias in node.names
                if alias.name.split(".")[0] in SRC_TOP_LEVEL
            )
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] in SRC_TOP_LEVEL:
                found.append(f"{node.lineno}: from {node.module} import …")
    return found


def _is_sys_path_call(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in {"insert", "append", "extend"}
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "path"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "sys"
    )


def _mentions_src(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Constant) and child.value == "src" for child in ast.walk(node)
    )


def _src_dir_on_sys_path(tree: ast.AST) -> list[str]:
    """``sys.path`` calls that add ``…/src``, directly or through a name bound to it."""
    bound_to_src = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and _mentions_src(node.value)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_sys_path_call(node)):
            continue
        names = {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
        if _mentions_src(node) or names & bound_to_src:
            found.append(f"{node.lineno}: {ast.unparse(node)}")
    return found


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_imports_project_modules_only_as_src(script: Path) -> None:
    tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    assert _bare_src_imports(tree) == [], f"{script.name}: bare import of a src module"
    assert _src_dir_on_sys_path(tree) == [], f"{script.name}: puts src/ on sys.path"


@pytest.mark.parametrize("name", FORMERLY_DOUBLE)
def test_script_loads_every_project_module_once(name: str) -> None:
    """Imported like the tests and the cron jobs do, no module exists twice."""
    code = (
        "import importlib, sys\n"
        f"importlib.import_module('scripts.{name}')\n"
        "print(sorted(n for n in sys.modules"
        " if not n.startswith('src.') and 'src.' + n in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, f"scripts/{name}.py failed to import:\n{result.stderr}"
    assert result.stdout.strip() == "[]", f"scripts/{name}.py loads modules twice: {result.stdout}"
