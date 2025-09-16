import importlib
import sys
from pathlib import Path
import types
from datetime import datetime, timezone


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


def test_identity_distinguishes_items_without_lines(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)

    day = datetime(2024, 1, 1, tzinfo=timezone.utc)
    item1 = {"source": "wl", "category": "störung", "title": "A", "starts_at": day}
    item2 = {"source": "wl", "category": "störung", "title": "B", "starts_at": day}

    ident1 = build_feed._identity_for_item(item1)
    ident2 = build_feed._identity_for_item(item2)

    assert ident1 != ident2
    assert ident1.startswith("wl|störung|L=|D=2024-01-01|T=")
    assert ident2.startswith("wl|störung|L=|D=2024-01-01|T=")


def test_identity_includes_line_tokens_and_is_cosmetic_stable(monkeypatch):
    build_feed = _import_build_feed(monkeypatch)

    day = datetime(2024, 1, 1, tzinfo=timezone.utc)
    base_item = {"source": "wl", "category": "störung", "starts_at": day}

    s45 = {**base_item, "title": "S45: Baustelle"}
    s45_ident = build_feed._identity_for_item(s45)
    assert "|L=S45|" in s45_ident

    def _identity_base(item: dict) -> str:
        ident = build_feed._identity_for_item(item)
        return ident.split("|F=", 1)[0]

    assert _identity_base(s45) == _identity_base({**base_item, "title": "s45: Baustelle"})
    assert _identity_base(s45) == _identity_base({**base_item, "title": "S45: Baustelle (Update)"})

    rjx = {**base_item, "title": "RJX/RJ: Hinweis"}
    rjx_ident = build_feed._identity_for_item(rjx)
    assert "|L=RJX/RJ|" in rjx_ident

    assert _identity_base(rjx) == _identity_base({**base_item, "title": "rjx/rj: Hinweis"})
    assert _identity_base(rjx) == _identity_base({**base_item, "title": "RJX/RJ: Hinweis (Update)"})

