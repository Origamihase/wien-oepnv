"""Was der Wächter über Patches weiß: die Module von src.build_feed, ihre Leser, AST-Hilfen.

Teil des Wächters ``tests/test_build_feed_patch_targets.py`` (Regel und Tests
stehen dort). Seit 2026-11-14 eine eigene Datei: Der Wächter prüft seither
jede Patch-Form, die er kennt, und wuchs dabei über die Zeilengrenze. Hier
stehen die Datensätze einer Erfassung (``Patch``, ``Read``, ``Census``), was
jedes Modul der Fassade bindet, definiert und als Global liest, und die
Hilfen, die Ziele und Namen aus dem AST lesen. ``tests/_conftest/patch_scan.py``
erfasst damit die Patches einer Testdatei.
"""

from __future__ import annotations

import ast
import dataclasses
import functools
import symtable
from pathlib import Path

import src.build_feed as build_feed
from tests._conftest.build_feed_bindings import FACADE, PACKAGE, build_feed_modules, defined_names

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
FACADE_ALIASES = {"build_feed", "bf", "build_feed_mod", "build_feed_module"}
REBOUND_STATE = {"PROVIDERS", "DEFAULT_PROVIDERS", "_PROVIDERS_INITIALIZED"}
PATCH_FUNCS = {"setattr", "delattr", "object"}
MULTI_FUNCS = {"setattr_all", "patch_all"}
# Aufrufe, die ein Modul von ``src.build_feed`` liefern können: ihr Ergebnis ist
# ein unbekanntes Ziel, wenn sie sich nicht auflösen lassen (``part(bf, name)``).
MODULE_LOADERS = {"part", "owner", "import_module", "reload", "getattr", "get"}
# Schlüsselwörter von ``mock.patch.multiple``, die keine Namen ersetzen.
MULTIPLE_OPTIONS = {"target", "spec", "create", "spec_set", "autospec", "new_callable"}
REIMPORT_MARKERS = (
    'sys.modules.pop("src.build_feed"',
    "sys.modules.pop('src.build_feed'",
    'del sys.modules["src.build_feed"]',
    "importlib.reload(",
    "fresh_build_feed(",
)


@dataclasses.dataclass(frozen=True)
class Patch:
    path: str
    line: int
    module: str
    name: str
    multi: bool
    function: str
    alias_is_top_import: bool

    def where(self) -> str:
        return f"{self.path}:{self.line}"


@dataclasses.dataclass(frozen=True)
class Read:
    path: str
    line: int
    module: str
    name: str
    function: str


@dataclasses.dataclass
class Census:
    patches: list[Patch] = dataclasses.field(default_factory=list)
    shared: list[str] = dataclasses.field(default_factory=list)
    reads: list[Read] = dataclasses.field(default_factory=list)
    reimport_files: set[str] = dataclasses.field(default_factory=set)
    unresolved: list[str] = dataclasses.field(default_factory=list)


# ------------------------------------------------------------------ modules


@functools.cache
def module_names() -> tuple[str, ...]:
    return tuple(module.__name__ for module in build_feed_modules(build_feed))


@functools.cache
def module_bindings() -> dict[str, frozenset[str]]:
    return {module.__name__: frozenset(vars(module)) for module in build_feed_modules(build_feed)}


@functools.cache
def module_definitions() -> dict[str, frozenset[str]]:
    return {module.__name__: defined_names(module) for module in build_feed_modules(build_feed)}


def _global_loads(source: str) -> set[str]:
    table = symtable.symtable(source, "<module>", "exec")
    found: set[str] = set()

    def walk(tab: symtable.SymbolTable, top: bool) -> None:
        for sym in tab.get_symbols():
            if not sym.is_referenced():
                continue
            if top or sym.is_global():
                found.add(sym.get_name())
            elif tab.get_type() == "class" and not sym.is_assigned():
                found.add(sym.get_name())
        for child in tab.get_children():
            walk(child, False)

    walk(table, True)
    return found


@functools.cache
def readers_by_module() -> dict[str, frozenset[str]]:
    """Modul -> Namen, die sein Code als Global liest (auch auf Modulebene)."""

    out: dict[str, frozenset[str]] = {}
    for module in build_feed_modules(build_feed):
        path = Path(module.__file__ or "")
        out[module.__name__] = frozenset(_global_loads(path.read_text(encoding="utf-8")))
    return out


def readers(name: str) -> set[str]:
    return {module for module, names in readers_by_module().items() if name in names}


def owner_name(name: str) -> str | None:
    for module, names in module_definitions().items():
        if name in names:
            return module
    return None


# ------------------------------------------------------------------ census


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else None
    return None


def _string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _feed_path(path: str) -> bool:
    """``path`` ist ``src.build_feed``, ``src.feed_build`` oder liegt darunter."""

    return any(path == root or path.startswith(root + ".") for root in (FACADE, PACKAGE))


def _string_prefix(node: ast.AST) -> str | None:
    """Der feste Anfang eines zusammengesetzten Strings (f-String, ``+``, ``.format``)."""

    if isinstance(node, ast.JoinedStr) and node.values:
        return _string(node.values[0]) or ""
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _string(node.left) if _string(node.left) is not None else _string_prefix(node.left)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        text = _string(node.func.value)
        return text.split("{", 1)[0] if text is not None else None
    return None


def _namespace_of(node: ast.AST) -> ast.AST | None:
    """``X`` aus ``X.__dict__`` oder ``vars(X)``: der Namensraum eines Moduls als Dict."""

    if isinstance(node, ast.Attribute) and node.attr == "__dict__":
        return node.value
    if isinstance(node, ast.Call) and _call_name(node) == "vars" and len(node.args) == 1:
        return node.args[0]
    return None


_KEYWORD_SLOTS = {
    # Funktionsname -> Schlüsselwörter der ersten beiden Positionsargumente
    "setattr": (("target",), ("name",)),
    "delattr": (("target",), ("name",)),
    "object": (("target",), ("attribute",)),
    "patch": (("target",), ()),
    "multiple": (("target",), ()),
}


def _call_args(node: ast.Call, name: str) -> list[ast.expr]:
    """Positionsargumente, ergänzt um ``target=``/``name=``/``attribute=``."""

    args = list(node.args)
    by_keyword = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
    for index, slot in enumerate(_KEYWORD_SLOTS.get(name, ())):
        if len(args) > index:
            continue
        value = next((by_keyword[key] for key in slot if key in by_keyword), None)
        if value is None:
            break
        args.append(value)
    return args


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""
