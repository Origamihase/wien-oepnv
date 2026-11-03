import sys
from pathlib import Path
from typing import Any
import pytest
import types
from tests._conftest.build_feed_bindings import fresh_build_feed, part

def _import_build_feed(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    setattr(wl, "fetch_events", lambda timeout=None: [])
    oebb = types.ModuleType("providers.oebb")
    setattr(oebb, "fetch_events", lambda timeout=None: [])
    vor = types.ModuleType("providers.vor")
    setattr(vor, "fetch_events", lambda timeout=None: [])
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)
    monkeypatch.setitem(sys.modules, "providers.vor", vor)
    return fresh_build_feed()


def test_collect_items_logs_and_skips_invalid_return(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    build_feed = _import_build_feed(monkeypatch)

    def good_provider(timeout: Any = None) -> list[dict[str, str]]:
        return [{"p": "good"}]

    def bad_provider(timeout: Any = None) -> dict[str, str]:
        return {"p": "bad"}

    monkeypatch.setattr(
        part(build_feed, "providers_setup"),
        "PROVIDERS",
        [
            ("GOOD_ENABLE", good_provider),
            ("BAD_ENABLE", bad_provider),
        ],
    )

    monkeypatch.setenv("GOOD_ENABLE", "1")
    monkeypatch.setenv("BAD_ENABLE", "1")

    with caplog.at_level("ERROR"):
        items = build_feed._collect_items()

    assert items == [{"p": "good"}]
    assert any("bad_provider" in r.getMessage() for r in caplog.records)
