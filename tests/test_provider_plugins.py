"""Tests for the dynamic provider plugin loader."""

from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
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
        # Explicitly initialize config/plugins because import no longer does it
        build_feed.refresh_from_env()

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


def test_main_generates_feed_and_health_with_plugin(monkeypatch, tmp_path):
    from src.feed import providers as provider_mod

    module_name = "tests.fake_plugin_e2e"
    now = datetime.now(timezone.utc)

    def plugin_loader(*_args, **_kwargs):
        return [
            {
                "_identity": "plugin|event",
                "guid": "plugin-1",
                "title": "Plugin Ereignis",
                "description": "Ereignis aus Plugin",
                "link": "https://example.com/plugin",
                "source": "Plugin",
                "category": "Info",
                "pubDate": now.isoformat(),
                "starts_at": now.isoformat(),
            }
        ]

    plugin_module = _make_plugin_module(
        module_name,
        providers=[("PLUGIN_ENABLE", plugin_loader, "plugin")],
    )

    monkeypatch.setitem(sys.modules, module_name, plugin_module)
    monkeypatch.setenv("WIEN_OEPNV_PROVIDER_PLUGINS", module_name)

    provider_mod._reset_registry()

    import src.build_feed as build_feed

    build_feed = importlib.reload(build_feed)

    try:
        monkeypatch.setenv("WL_ENABLE", "0")
        monkeypatch.setenv("OEBB_ENABLE", "0")
        monkeypatch.setenv("VOR_ENABLE", "0")
        monkeypatch.setenv("BAUSTELLEN_ENABLE", "0")
        monkeypatch.setenv("PLUGIN_ENABLE", "1")

        out_path = tmp_path / "feed.xml"
        health_path = tmp_path / "feed-health.md"
        health_json_path = tmp_path / "feed-health.json"
        state_path = tmp_path / "state.json"

        # Patch feed_config on build_feed module to ensure we target the right one
        monkeypatch.setattr(build_feed.feed_config, "validate_path", lambda path, name: Path(path))
        monkeypatch.setattr(build_feed, "validate_path", lambda path, name: Path(path))

        monkeypatch.setattr(build_feed.feed_config, "OUT_PATH", out_path)
        monkeypatch.setattr(build_feed.feed_config, "FEED_HEALTH_PATH", health_path)
        monkeypatch.setattr(build_feed.feed_config, "FEED_HEALTH_JSON_PATH", health_json_path)
        monkeypatch.setattr(build_feed.feed_config, "STATE_FILE", state_path)
        monkeypatch.setattr(build_feed, "_load_state", lambda: {})
        monkeypatch.setattr(build_feed, "_save_state", lambda state: None)

        # Patch ENV vars for paths so refresh_from_env uses them
        monkeypatch.setenv("OUT_PATH", str(out_path))
        monkeypatch.setenv("FEED_HEALTH_PATH", str(health_path))
        monkeypatch.setenv("FEED_HEALTH_JSON_PATH", str(health_json_path))
        monkeypatch.setenv("STATE_PATH", str(state_path))

        exit_code = build_feed.main()

        assert exit_code == 0
        assert out_path.exists()
        assert health_path.exists()
        assert health_json_path.exists()

        feed_text = out_path.read_text(encoding="utf-8")
        assert "plugin-1" in feed_text
        health_text = health_path.read_text(encoding="utf-8")
        assert "Feed Health Report" in health_text
        assert "plugin" in health_text.lower()
        assert "disabled" in health_text.lower()

        health_payload = json.loads(health_json_path.read_text(encoding="utf-8"))
        assert health_payload["metrics"]["raw_items"] == 1
        assert health_payload["metrics"]["deduped_items"] == 1
        assert "collect" in health_payload["durations"]
        provider_names = {provider["name"] for provider in health_payload["providers"]}
        assert "plugin" in provider_names
    finally:
        provider_mod.unregister_provider("PLUGIN_ENABLE")
        provider_mod._reset_registry()
        monkeypatch.delenv("WIEN_OEPNV_PROVIDER_PLUGINS", raising=False)
        sys.modules.pop(module_name, None)
        importlib.reload(build_feed)
