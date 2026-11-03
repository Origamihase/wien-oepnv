"""Hilfe für Tests, die einen Namen der HTTP-Schicht ersetzen.

Seit 2026-11-02 ist ``src/utils/http.py`` aufgeteilt (700-Zeilen-Regel): der
Code liegt in ``src/utils/http_safety/*.py``, ``src.utils.http`` exportiert
alles weiter. ``_resolve_hostname_safe`` und ``is_ip_safe`` werden seither in
mehreren Modulen aus deren eigenen Globals gelesen (Fassade, ``url_guard``,
``adapters``, ``peer``, ``ip_guard``). Ein ``patch("src.utils.http.<name>")``
erreicht nur die Aufrufer in der Fassade. Vor der Aufteilung erreichte er alle.

``patch_all`` und ``setattr_all`` ersetzen den Namen in **jedem** dieser Module
durch dasselbe Objekt, so wie früher der eine Patch auf ``src.utils.http``.
Die Module werden zur Laufzeit gesucht (jedes Modul der Fassade und des Pakets,
dessen Globals den Namen binden), daher kann die Liste nicht veralten.
"""

from __future__ import annotations

import functools
import importlib
import inspect
import pkgutil
from collections.abc import Callable
from contextlib import ExitStack
from types import ModuleType
from typing import Any
from unittest import mock

import pytest

_FACADE = "src.utils.http"
_PACKAGE = "src.utils.http_safety"


def http_modules() -> list[ModuleType]:
    """Die Fassade und jedes Modul von ``src.utils.http_safety``."""

    facade = importlib.import_module(_FACADE)
    package = importlib.import_module(_PACKAGE)
    parts = [
        importlib.import_module(f"{_PACKAGE}.{info.name}")
        for info in pkgutil.iter_modules(package.__path__)
    ]
    return [facade, *parts]


def bindings(name: str) -> list[ModuleType]:
    """Jedes Modul, dessen Globals ``name`` binden."""

    found = [module for module in http_modules() if name in vars(module)]
    assert found and found[0].__name__ == _FACADE, f"{_FACADE} has no {name!r}"
    return found


def setattr_all(monkeypatch: pytest.MonkeyPatch, name: str, value: Any) -> None:
    """``monkeypatch.setattr`` auf jeder Bindung von ``name``."""

    for module in bindings(name):
        monkeypatch.setattr(module, name, value)


class patch_all:
    """Wie ``unittest.mock.patch("src.utils.http.<name>", ...)``, für jede Bindung.

    Als Kontextmanager liefert es den einen Mock (oder ``new``); als Dekorator
    reicht es ihn wie ``mock.patch`` als zusätzliches Argument weiter, bei
    gestapelten ``patch_all``-Dekoratoren in derselben Reihenfolge (der unterste
    zuerst). Die Argumente ohne ``new`` gehen an ``mock.MagicMock``.
    """

    def __init__(self, name: str, new: Any = mock.DEFAULT, **kwargs: Any) -> None:
        self.name = name
        self.new = new
        self.kwargs = kwargs
        self._stack: ExitStack | None = None

    def _enter(self) -> tuple[Any, ExitStack]:
        value = mock.MagicMock(**self.kwargs) if self.new is mock.DEFAULT else self.new
        stack = ExitStack()
        for module in bindings(self.name):
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
