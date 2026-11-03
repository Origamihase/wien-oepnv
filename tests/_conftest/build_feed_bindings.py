"""Hilfe für Tests, die einen Namen des Feed-Builds ersetzen.

Seit Etappe 8 (700-Zeilen-Regel) liegt der Code von ``src/build_feed.py`` zum
Teil in ``src/feed_build/*.py``; ``src.build_feed`` bleibt die Fassade und
exportiert alles weiter, was src/, scripts/ und die Tests lesen. Ein Name wird
seither in dem Modul ersetzt, dessen Code ihn als globalen Namen liest. Ein
``monkeypatch.setattr(build_feed, "X", ...)`` erreicht nur die Leser in der
Fassade; vor der Aufteilung erreichte er alle.

``setattr_all`` und ``patch_all`` ersetzen den Namen in **jedem** Modul, das ihn
bindet (Fassade und Teile), durch dasselbe Objekt, so wie früher der eine Patch
auf ``src.build_feed``. ``owner`` liefert das Modul, das einen Namen definiert,
``part`` einen Teil nach seinem Kurznamen (``part(bf, "translate_run")``).
Alle lösen die Module zur Patch-Zeit über die Fassade auf, die der Test
tatsächlich benutzt (auch nach ``fresh_build_feed``), nie über ein Modul, das
beim Sammeln oben importiert wurde.

``fresh_build_feed`` importiert die Fassade samt Teilen neu: erst fallen
``src.feed_build.*``, dann das Paket, dann die Fassade aus ``sys.modules``
(optional dazu ``extra_pops``, etwa ``src.feed.config``), dann folgt der Import,
auf Wunsch unter einem ``builtins.__import__``-Wächter. Das Aufräumen danach
übernimmt ``_restore_replaced_modules`` in ``tests/_conftest/zustand.py``.

``tests/test_build_feed_patch_targets.py`` prüft jeden Patch der Tests gegen
diese Regel, ``tests/test_build_feed_bindings.py`` die Hilfen selbst.
"""

from __future__ import annotations

import ast
import builtins
import functools
import gc
import importlib
import inspect
import pkgutil
import sys
from collections.abc import Callable, Iterable
from contextlib import ExitStack
from types import ModuleType
from typing import Any
from unittest import mock

import pytest

FACADE = "src.build_feed"
PACKAGE = "src.feed_build"


def _module_of_globals(namespace: dict[str, Any], name: str) -> ModuleType:
    """Das Modulobjekt zu ``namespace`` (den Globals einer Funktion)."""

    module = sys.modules.get(name)
    if module is not None and vars(module) is namespace:
        return module
    for referrer in gc.get_referrers(namespace):
        if isinstance(referrer, ModuleType) and vars(referrer) is namespace:
            return referrer
    raise LookupError(f"no module object for {name}")  # pragma: no cover


def _facade(bf: ModuleType | None) -> ModuleType:
    return bf if bf is not None else importlib.import_module(FACADE)


def build_feed_modules(bf: ModuleType | None = None) -> list[ModuleType]:
    """Die Fassade und jedes Modul von ``src.feed_build``, das sie benutzt.

    Die Teile werden über die Funktionen und Klassen gefunden, die in den
    Globals der Fassade (und, transitiv, der Teile) liegen; deren
    ``__globals__`` sind die Namensräume genau der Teile, die diese Fassade
    aufruft. Ein Teil ohne Funktionen kommt aus ``sys.modules``.
    """

    facade = _facade(bf)
    found: dict[str, ModuleType] = {}
    pending: list[dict[str, Any]] = [vars(facade)]
    seen: set[int] = {id(vars(facade))}
    while pending:
        namespace = pending.pop()
        for value in list(namespace.values()):
            module_name = getattr(value, "__module__", None)
            if not isinstance(module_name, str) or not module_name.startswith(PACKAGE + "."):
                continue
            func = value
            if inspect.isclass(value):
                func = next(
                    (
                        member
                        for member in vars(value).values()
                        if inspect.isfunction(member) and member.__module__ == module_name
                    ),
                    None,
                )
            func = inspect.unwrap(func) if callable(func) else func
            globals_ = getattr(func, "__globals__", None)
            if not isinstance(globals_, dict) or id(globals_) in seen:
                continue
            seen.add(id(globals_))
            found.setdefault(module_name, _module_of_globals(globals_, module_name))
            pending.append(globals_)
    package = sys.modules.get(PACKAGE) or importlib.import_module(PACKAGE)
    for info in pkgutil.iter_modules(package.__path__):
        full = f"{PACKAGE}.{info.name}"
        if full not in found:
            found[full] = sys.modules.get(full) or importlib.import_module(full)
    return [facade, *(found[name] for name in sorted(found))]


@functools.cache
def _defined_in(path: str, mtime: float) -> frozenset[str]:
    del mtime
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names)


def defined_names(module: ModuleType) -> frozenset[str]:
    """Die Namen, die der Quelltext von ``module`` auf Modulebene definiert."""

    import os

    path = inspect.getfile(module)
    return _defined_in(path, os.path.getmtime(path))


def part(bf: ModuleType | None, short: str) -> ModuleType:
    """Der Teil ``src.feed_build.<short>``, den die Fassade ``bf`` benutzt."""

    wanted = f"{PACKAGE}.{short}"
    for module in build_feed_modules(bf):
        if module.__name__ == wanted:
            return module
    raise LookupError(f"{FACADE} uses no module {wanted}")


def owner(bf: ModuleType | None, name: str) -> ModuleType:
    """Das Modul (Fassade oder Teil), das ``name`` definiert."""

    for module in build_feed_modules(bf):
        if name in defined_names(module):
            return module
    raise LookupError(f"{name!r} is defined in no module of {FACADE}")


def bindings(name: str, bf: ModuleType | None = None) -> list[ModuleType]:
    """Jedes Modul (Fassade zuerst), dessen Globals ``name`` binden."""

    found = [module for module in build_feed_modules(bf) if name in vars(module)]
    assert found, f"no module of {FACADE} binds {name!r}"
    return found


def setattr_all(
    monkeypatch: pytest.MonkeyPatch, bf: ModuleType | None, name: str, value: Any, *, raising: bool = True
) -> None:
    """``monkeypatch.setattr`` auf jeder Bindung von ``name``."""

    for module in bindings(name, bf):
        monkeypatch.setattr(module, name, value, raising=raising)


class patch_all:
    """Wie ``mock.patch.object(build_feed, name, ...)``, für jede Bindung.

    ``bf`` ist die Fassade, die der Test benutzt (``None``: die in
    ``sys.modules`` beim Betreten). Als Kontextmanager liefert es den einen Mock
    (oder ``new``); als Dekorator reicht es ihn wie ``mock.patch`` als
    zusätzliches Argument weiter, bei gestapelten ``patch_all``-Dekoratoren in
    derselben Reihenfolge (der unterste zuerst). Die Argumente ohne ``new``
    gehen an ``mock.MagicMock``.
    """

    def __init__(self, bf: ModuleType | None, name: str, new: Any = mock.DEFAULT, **kwargs: Any) -> None:
        self.bf = bf
        self.name = name
        self.new = new
        self.kwargs = kwargs
        self._stack: ExitStack | None = None

    def _enter(self) -> tuple[Any, ExitStack]:
        value = mock.MagicMock(**self.kwargs) if self.new is mock.DEFAULT else self.new
        stack = ExitStack()
        for module in bindings(self.name, self.bf):
            stack.enter_context(mock.patch.object(module, self.name, value))
        return value, stack

    def __enter__(self) -> Any:
        value, self._stack = self._enter()
        return value

    def __exit__(self, *exc_info: Any) -> None:
        assert self._stack is not None
        self._stack.__exit__(*exc_info)
        self._stack = None

    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        inner = getattr(func, "_patch_all_inner", None)
        target = inner if inner is not None else func
        depth = getattr(func, "_patch_all_depth", 0) + (0 if self.new is not mock.DEFAULT else 1)
        passes_mock = self.new is mock.DEFAULT

        @functools.wraps(target)
        def wrapper(*args: Any, _patch_all_mocks: tuple[Any, ...] = (), **kwargs: Any) -> Any:
            value, stack = self._enter()
            with stack:
                mocks = ((value,) if passes_mock else ()) + _patch_all_mocks
                if inner is not None:
                    return func(*args, _patch_all_mocks=mocks, **kwargs)
                return func(*args, *mocks, **kwargs)

        signature = inspect.signature(target)
        params = list(signature.parameters.values())
        wrapper.__signature__ = signature.replace(  # type: ignore[attr-defined]
            parameters=params[: len(params) - depth]
        )
        wrapper._patch_all_inner = target  # type: ignore[attr-defined]
        wrapper._patch_all_depth = depth  # type: ignore[attr-defined]
        return wrapper


ImportHook = Callable[..., Any]


def fresh_build_feed(extra_pops: Iterable[str] = (), import_guard: ImportHook | None = None) -> ModuleType:
    """``src.build_feed`` samt ``src.feed_build.*`` frisch importieren.

    Reihenfolge: erst die Teile, dann das Paket, dann die Fassade, dann
    ``extra_pops`` (etwa ``src.feed``, ``src.feed.config``) aus
    ``sys.modules``; so importieren alle Teile gegen dieselbe frische
    Konfiguration. ``import_guard`` ersetzt währenddessen
    ``builtins.__import__`` (Signatur wie ``__import__``). Liefert die Fassade.
    """

    for name in sorted(n for n in sys.modules if n.startswith(PACKAGE + ".")):
        sys.modules.pop(name, None)
    sys.modules.pop(PACKAGE, None)
    sys.modules.pop(FACADE, None)
    for name in extra_pops:
        sys.modules.pop(name, None)
    if import_guard is None:
        return importlib.import_module(FACADE)
    real_import = builtins.__import__
    builtins.__import__ = import_guard
    try:
        return importlib.import_module(FACADE)
    finally:
        builtins.__import__ = real_import
