"""Tests for the dynamic provider plugin loader."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType


def _make_plugin_module(name: str, *, register_callable=None, providers=None) -> ModuleType:
    module = ModuleType(name)
    if register_callable is not None:
        module.register_providers = register_callable
    if providers is not None:
        module.PROVIDERS = providers
    return module


def test_load_provider_plugins_via_callable(monkeypatch):
    from src.feed import providers as provider_mod

    def loader() -> list[str]:
        return []

    def register(register_provider):
        register_provider("PLUGIN_ENABLE", loader, cache_key="plugin")

    module_name = "tests.fake_plugin_callable"
    plugin_module = _make_plugin_module(module_name, register_callable=register)

    monkeypatch.setitem(sys.modules, module_name, plugin_module)
    monkeypatch.setenv("WIEN_OEPNV_PROVIDER_PLUGINS", module_name)

    provider_mod._reset_registry()
    try:
        loaded = provider_mod.load_provider_plugins(force=True)
        assert module_name in loaded
        registered_names = {spec.cache_key for spec in provider_mod.iter_providers()}
        assert "plugin" in registered_names
    finally:
        provider_mod.unregister_provider("PLUGIN_ENABLE")
        provider_mod._reset_registry()
        monkeypatch.delenv("WIEN_OEPNV_PROVIDER_PLUGINS", raising=False)
        sys.modules.pop(module_name, None)


def test_collect_items_uses_plugin_provider(monkeypatch):
    from src.feed import providers as provider_mod

    module_name = "tests.fake_plugin_list"
    plugin_calls: list[str] = []

    def plugin_loader(*_args, **_kwargs):
        plugin_calls.append("invoked")
        return []

    plugin_module = _make_plugin_module(
        module_name,
        providers=[("PLUGIN_ENABLE", plugin_loader, "plugin")],
    )

    monkeypatch.setitem(sys.modules, module_name, plugin_module)
    monkeypatch.setenv("WIEN_OEPNV_PROVIDER_PLUGINS", module_name)

    provider_mod._reset_registry()

    build_feed = importlib.import_module("src.build_feed")
    try:
        build_feed = importlib.reload(build_feed)

        monkeypatch.setenv("WL_ENABLE", "0")
        monkeypatch.setenv("OEBB_ENABLE", "0")
        monkeypatch.setenv("VOR_ENABLE", "0")
        monkeypatch.setenv("BAUSTELLEN_ENABLE", "0")
        monkeypatch.setenv("PLUGIN_ENABLE", "1")

        items = build_feed._collect_items()
        assert items == []
        assert plugin_calls == ["invoked"]
    finally:
        provider_mod.unregister_provider("PLUGIN_ENABLE")
        provider_mod._reset_registry()
        monkeypatch.delenv("WIEN_OEPNV_PROVIDER_PLUGINS", raising=False)
        sys.modules.pop(module_name, None)
        importlib.reload(build_feed)
