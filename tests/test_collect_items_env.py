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
    wl.fetch_events = lambda timeout=None: []
    oebb = types.ModuleType("providers.oebb")
    oebb.fetch_events = lambda timeout=None: []
    vor = types.ModuleType("providers.vor")
    vor.fetch_events = lambda timeout=None: []
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
            ("WL_ENABLE", lambda timeout=None: [{"p": "wl"}]),
            ("OEBB_ENABLE", lambda timeout=None: [{"p": "oebb"}]),
            ("VOR_ENABLE", lambda timeout=None: [{"p": "vor"}]),
        ],
    )

    monkeypatch.setenv("WL_ENABLE", "1")
    monkeypatch.setenv("OEBB_ENABLE", "1")
    monkeypatch.setenv("VOR_ENABLE", "1")
    monkeypatch.setenv(disabled_env, "0")

    items = build_feed._collect_items()
    assert items == expected


@pytest.mark.parametrize(
    "value",
    ["0", " 0 ", "false", " False ", "FALSE", "\t0\n", "\nfalse\t"],
)
def test_env_disabling_ignores_whitespace_and_case(monkeypatch, value):
    build_feed = _import_build_feed(monkeypatch)

    monkeypatch.setattr(
        build_feed,
        "PROVIDERS",
        [
            ("WL_ENABLE", lambda timeout=None: [{"p": "wl"}]),
            ("OEBB_ENABLE", lambda timeout=None: [{"p": "oebb"}]),
        ],
    )

    monkeypatch.setenv("WL_ENABLE", value)
    monkeypatch.setenv("OEBB_ENABLE", "1")

    items = build_feed._collect_items()
    assert items == [{"p": "oebb"}]


def test_enabling_vor_yields_items(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)

    monkeypatch.setattr(
        build_feed,
        "PROVIDERS",
        [
            ("WL_ENABLE", lambda timeout=None: []),
            ("OEBB_ENABLE", lambda timeout=None: []),
            ("VOR_ENABLE", lambda timeout=None: [{"p": "vor"}]),
        ],
    )

    monkeypatch.setenv("WL_ENABLE", "0")
    monkeypatch.setenv("OEBB_ENABLE", "0")
    monkeypatch.setenv("VOR_ENABLE", "1")

    items = build_feed._collect_items()
    assert items == [{"p": "vor"}]


def test_collect_items_logs_provider_summary(monkeypatch, caplog):
    build_feed = _import_build_feed(monkeypatch)

    cache_fetch = lambda timeout=None: []
    setattr(cache_fetch, "_provider_cache_name", "wl")
    monkeypatch.setattr(build_feed, "PROVIDERS", [("WL_ENABLE", cache_fetch)])

    monkeypatch.delenv("WL_ENABLE", raising=False)
    caplog.set_level("INFO", logger="build_feed")

    items = build_feed._collect_items()

    assert items == []
    assert "Aktive Provider (1): wl" in caplog.text
    assert "Provider wl (Cache) erledigt in" in caplog.text


def test_collect_items_warns_when_all_disabled(monkeypatch, caplog):
    build_feed = _import_build_feed(monkeypatch)

    cache_fetch = lambda timeout=None: []
    setattr(cache_fetch, "_provider_cache_name", "wl")
    monkeypatch.setattr(build_feed, "PROVIDERS", [("WL_ENABLE", cache_fetch)])

    monkeypatch.setenv("WL_ENABLE", "0")
    caplog.set_level("INFO", logger="build_feed")

    items = build_feed._collect_items()

    assert items == []
    assert "Keine Provider aktiviert – Feed bleibt leer." in caplog.text
    assert "Deaktivierte Provider: wl" in caplog.text


def test_collect_items_reports_invalid_flags(monkeypatch, caplog):
    build_feed = _import_build_feed(monkeypatch)

    network_fetch = lambda timeout=None: []
    monkeypatch.setattr(build_feed, "PROVIDERS", [("OEBB_ENABLE", network_fetch)])

    monkeypatch.setenv("OEBB_ENABLE", "definitely")
    caplog.set_level("INFO", logger="build_feed")

    build_feed._collect_items()

    assert "Ungültige Provider-Flags: oebb=definitely" in caplog.text
