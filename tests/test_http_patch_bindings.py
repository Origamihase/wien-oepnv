"""Wächter für ``tests/_conftest/http_bindings.py``.

``src/utils/http.py`` ist seit 2026-11-02 auf ``src/utils/http_safety/*.py``
verteilt. Ein Test, der ``_resolve_hostname_safe`` oder ``is_ip_safe`` ersetzt,
muss jedes Modul treffen, dessen Code den Namen aus den eigenen Globals liest;
sonst prüft er still weniger (der autouse-Stub ``_no_real_dns`` hält ihn grün).
"""

from __future__ import annotations

import ast
import inspect
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests._conftest.http_bindings import bindings, http_modules, patch_all, setattr_all

PATCHED_NAMES = ("_resolve_hostname_safe", "is_ip_safe")


def _modules_loading(name: str) -> set[str]:
    """Module, deren Funktionen ``name`` als globalen Namen lesen (AST)."""

    found: set[str] = set()
    for module in http_modules():
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
                found.add(module.__name__)
    return found


@pytest.mark.parametrize("name", PATCHED_NAMES)
def test_bindings_cover_every_module_that_reads_the_name(name: str) -> None:
    readers = _modules_loading(name)
    assert readers, name
    assert readers <= {module.__name__ for module in bindings(name)}


@pytest.mark.parametrize("name", PATCHED_NAMES)
def test_patch_all_replaces_every_binding(name: str) -> None:
    with patch_all(name) as replacement:
        assert all(getattr(module, name) is replacement for module in bindings(name))
    assert all(getattr(module, name) is not replacement for module in bindings(name))


@pytest.mark.parametrize("name", PATCHED_NAMES)
def test_setattr_all_replaces_every_binding(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def replacement(*_args: Any, **_kwargs: Any) -> Any:
        return None

    setattr_all(monkeypatch, name, replacement)
    assert all(getattr(module, name) is replacement for module in bindings(name))


def test_validate_http_url_sees_patched_resolver() -> None:
    from src.utils.http import validate_http_url

    with patch_all("_resolve_hostname_safe", return_value=[(2, 1, 6, "", ("127.0.0.1", 80))]) as resolve:
        assert validate_http_url("https://example.com") is None
    resolve.assert_called_once_with("example.com")



@patch_all("_resolve_hostname_safe")
@patch_all("is_ip_safe")
def test_stacked_decorators_pass_mocks_bottom_first(
    mock_is_ip_safe: MagicMock, mock_resolve: MagicMock
) -> None:
    from src.utils import http
    from src.utils.http_safety import url_guard

    expected = {"is_ip_safe": mock_is_ip_safe, "_resolve_hostname_safe": mock_resolve}
    for module in (http, url_guard):
        for name, replacement in expected.items():
            assert getattr(module, name) is replacement
