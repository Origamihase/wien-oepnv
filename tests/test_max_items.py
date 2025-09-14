import importlib
import sys
from pathlib import Path


def test_max_items_non_negative(monkeypatch):
    monkeypatch.setenv("MAX_ITEMS", "-5")
    module_name = "src.build_feed"
    # Ensure 'providers' package can be found
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src"))
    sys.modules.pop(module_name, None)
    build_feed = importlib.import_module(module_name)
    assert build_feed.MAX_ITEMS == 0
