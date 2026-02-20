import sys
import importlib
import types
from pathlib import Path
from datetime import datetime, timezone

def _import_build_feed(monkeypatch):
    module_name = "src.build_feed"
    # Ensure we can import src modules
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / "src"))

    # Mock providers to avoid import errors
    providers = types.ModuleType("providers")
    wl = types.ModuleType("providers.wiener_linien")
    wl.fetch_events = lambda: []
    oebb = types.ModuleType("providers.oebb")
    oebb.fetch_events = lambda: []
    monkeypatch.setitem(sys.modules, "providers", providers)
    monkeypatch.setitem(sys.modules, "providers.wiener_linien", wl)
    monkeypatch.setitem(sys.modules, "providers.oebb", oebb)

    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    module.refresh_from_env()
    return module

def test_sort_key_handles_none_guid(monkeypatch, tmp_path):
    """
    Verify that _sort_key handles cases where 'guid' is explicitly None in the item dict.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OUT_PATH", "docs/feed.xml")
    monkeypatch.setenv("LOG_DIR", "log")

    # Create necessary directories to pass validation if needed (though validate_path checks parents existence mostly)
    (tmp_path / "docs").mkdir()
    (tmp_path / "log").mkdir()

    build_feed = _import_build_feed(monkeypatch)

    # Create an item with explicitly None guid
    item_none_guid = {
        "title": "None Guid",
        "guid": None,
        "pubDate": datetime.now(timezone.utc)
    }

    # Create an item with no guid key
    item_missing_guid = {
        "title": "Missing Guid",
        "pubDate": datetime.now(timezone.utc)
    }

    # Create an item with string guid
    item_str_guid = {
        "title": "String Guid",
        "guid": "some-guid",
        "pubDate": datetime.now(timezone.utc)
    }

    # This should not raise TypeError
    key1 = build_feed._sort_key(item_none_guid)
    key2 = build_feed._sort_key(item_missing_guid)
    key3 = build_feed._sort_key(item_str_guid)

    # Verify that the guid part of the key is a string
    assert isinstance(key1[2], str)
    assert key1[2] == ""

    assert isinstance(key2[2], str)
    assert key2[2] == ""

    assert isinstance(key3[2], str)
    assert key3[2] == "some-guid"
