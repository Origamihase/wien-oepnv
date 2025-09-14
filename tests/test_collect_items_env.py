import importlib
import sys
from pathlib import Path
import types
import pytest


def _import_build_feed(monkeypatch):
    module_name = "src.build_feed"
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    wl.fetch_events = lambda: []
    oebb = types.ModuleType("providers.oebb")
    oebb.fetch_events = lambda: []
    vor = types.ModuleType("providers.vor")
    vor.fetch_events = lambda: []
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    monkeypatch.setitem(sys.modules, "providers.vor", vor)
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


@pytest.mark.parametrize(
    "disabled_env,expected",
    [
        ("WL_ENABLE", [{"p": "oebb"}, {"p": "vor"}]),
        ("OEBB_ENABLE", [{"p": "wl"}, {"p": "vor"}]),
        ("VOR_ENABLE", [{"p": "wl"}, {"p": "oebb"}]),
    ],
)
def test_disabling_provider_suppresses_items(monkeypatch, disabled_env, expected):
    build_feed = _import_build_feed(monkeypatch)

    monkeypatch.setattr(
        build_feed,
        "PROVIDERS",
        [
            ("WL_ENABLE", lambda: [{"p": "wl"}]),
            ("OEBB_ENABLE", lambda: [{"p": "oebb"}]),
            ("VOR_ENABLE", lambda: [{"p": "vor"}]),
        ],
    )

    monkeypatch.setenv("WL_ENABLE", "1")
    monkeypatch.setenv("OEBB_ENABLE", "1")
    monkeypatch.setenv("VOR_ENABLE", "1")
    monkeypatch.setenv(disabled_env, "0")

    items = build_feed._collect_items()
    assert items == expected


def test_enabling_vor_yields_items(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)

    monkeypatch.setattr(
        build_feed,
        "PROVIDERS",
        [
            ("WL_ENABLE", lambda: []),
            ("OEBB_ENABLE", lambda: []),
            ("VOR_ENABLE", lambda: [{"p": "vor"}]),
        ],
    )

    monkeypatch.setenv("WL_ENABLE", "0")
    monkeypatch.setenv("OEBB_ENABLE", "0")
    monkeypatch.setenv("VOR_ENABLE", "1")

    items = build_feed._collect_items()
    assert items == [{"p": "vor"}]
