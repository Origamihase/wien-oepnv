"""Project modules are imported only as ``src.…`` (code audit 2026-10-31, finding A).

With ``src/`` itself on ``sys.path`` a script imported ``utils.cache`` while
the feed imported ``src.utils.cache``: the same file as two module objects
with separate state (the cache-alert hooks, the HTTP session cache and its
cleanup thread), and names mypy could not resolve. The rule is documented in
docs/development.md, "Skripte im Überblick". ``scripts/update_wl_stations.py``
imports its ``src`` helpers lazily inside functions; that is still ``src.…``.

Since 2026-11-01 no workflow puts ``src/`` on the path (``PYTHONPATH: src``
stood in ten workflows and kept the second module object possible), and the
rule is checked for ``src/`` and ``tests/`` too: bare imports, string targets
such as ``mock.patch("utils.cache._CACHE_DIR")``, which import the module
they name, and ``sys.path`` or ``monkeypatch.syspath_prepend`` calls that add
``src/`` (27 test files still did so for no effect until that day).
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
# Trees checked besides ``scripts/``.
PROJECT_TREES = ("src", "tests")
# Calls whose first argument names a module that they import ...
IMPORTING_CALLS = frozenset(
    {"import_module", "__import__", "patch", "patch.dict", "patch.multiple", "setattr", "delattr"}
)
# ... and those whose first argument may be the module itself (no dot needed).
MODULE_TARGET_CALLS = frozenset({"import_module", "__import__", "patch.multiple"})
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


def _call_name(func: ast.expr) -> str | None:
    """``patch``, ``setattr`` …; ``(mock.)patch.dict/.multiple`` as ``patch.dict/.multiple``."""
    if isinstance(func, ast.Attribute):
        receiver = func.value
        if func.attr in {"dict", "multiple"} and (
            (isinstance(receiver, ast.Attribute) and receiver.attr == "patch")
            or (isinstance(receiver, ast.Name) and receiver.id == "patch")
        ):
            return f"patch.{func.attr}"
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _bare_src_targets(tree: ast.AST) -> list[str]:
    """String targets that import a bare ``src`` module (``patch("utils.x.y")``)."""
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args):
            continue
        name = _call_name(node.func)
        first = node.args[0]
        if name not in IMPORTING_CALLS or not (
            isinstance(first, ast.Constant) and isinstance(first.value, str)
        ):
            continue
        head, dot, _rest = first.value.partition(".")
        if head in SRC_TOP_LEVEL and (dot or name in MODULE_TARGET_CALLS):
            found.append(f"{node.lineno}: {name}({first.value!r})")
    return found


def _is_sys_path_call(node: ast.Call) -> bool:
    """``sys.path.insert/append/extend(…)`` and pytest's ``….syspath_prepend(…)``."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "syspath_prepend":
        return True
    return (
        func.attr in {"insert", "append", "extend"}
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
    assert _bare_src_targets(tree) == [], f"{script.name}: string target imports a src module bare"
    assert _src_dir_on_sys_path(tree) == [], f"{script.name}: puts src/ on sys.path"


def _python_files(tree: str) -> list[Path]:
    return [path for path in sorted((ROOT / tree).rglob("*.py")) if "__pycache__" not in path.parts]


def test_the_scans_see_files() -> None:
    # An empty scan would pass every check below without looking at anything.
    assert len(SCRIPTS) > 10
    for tree in PROJECT_TREES:
        assert len(_python_files(tree)) > 10, f"{tree}/: no Python files found"


@pytest.mark.parametrize("tree", PROJECT_TREES)
def test_project_code_imports_project_modules_only_as_src(tree: str) -> None:
    offenders: list[str] = []
    for path in _python_files(tree):
        parsed = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _bare_src_imports(parsed) + _bare_src_targets(parsed) + _src_dir_on_sys_path(parsed)
        offenders.extend(f"{path.relative_to(ROOT)}:{hit}" for hit in hits)
    assert offenders == [], f"{tree}/ imports src modules bare or puts src/ on sys.path:\n" + "\n".join(
        offenders
    )


def test_the_check_sees_string_targets() -> None:
    sample = ast.parse(
        "mock.patch('utils.cache._CACHE_DIR')\n"
        "mock.patch.dict('utils.cache._HOOKS', {})\n"
        "patch.dict('feed.config.DEFAULTS', {})\n"
        "patch.multiple('utils', x=1)\n"
        "importlib.import_module('feed')\n"
        "__import__('build_feed')\n"
        "monkeypatch.setattr('utils.http.DNS_TIMEOUT', 1)\n"
        "monkeypatch.delattr('providers.vor.X')\n"
    )
    assert _bare_src_targets(sample) == [
        "1: patch('utils.cache._CACHE_DIR')",
        "2: patch.dict('utils.cache._HOOKS')",
        "3: patch.dict('feed.config.DEFAULTS')",
        "4: patch.multiple('utils')",
        "5: import_module('feed')",
        "6: __import__('build_feed')",
        "7: setattr('utils.http.DNS_TIMEOUT')",
        "8: delattr('providers.vor.X')",
    ]


def test_the_check_leaves_other_strings_alone() -> None:
    sample = ast.parse(
        "monkeypatch.setattr('src.utils.cache._CACHE_DIR', 1)\n"
        "mock.patch.dict('os.environ', {})\n"
        "settings.setdefault('utils.cache', 1)\n"
        "mock.patch.object(utils, 'cache')\n"
        "patch('utils')\n"
        "monkeypatch.setattr(module, 'feed', 1)\n"
    )
    assert _bare_src_targets(sample) == []


def test_the_check_sees_bare_imports_and_src_on_the_path() -> None:
    imports = ast.parse(
        "import utils.cache\nfrom feed import config\nfrom src.utils import cache\n"
        "from . import helpers\nimport os\n"
    )
    assert _bare_src_imports(imports) == ["1: import utils.cache", "2: from feed import …"]
    paths = ast.parse(
        "sys.path.insert(0, str(ROOT / 'src'))\n"
        "monkeypatch.syspath_prepend(str(root / 'src'))\n"
        "SRC = ROOT / 'src'\n"
        "sys.path.append(str(SRC))\n"
        "sys.path.insert(0, str(ROOT))\n"
        "monkeypatch.syspath_prepend(str(root))\n"
    )
    assert _src_dir_on_sys_path(paths) == [
        "1: sys.path.insert(0, str(ROOT / 'src'))",
        "2: monkeypatch.syspath_prepend(str(root / 'src'))",
        "4: sys.path.append(str(SRC))",
    ]


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
