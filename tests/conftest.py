import sys
from pathlib import Path

import pytest

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


@pytest.fixture(autouse=True)
def reset_vor_request_count(tmp_path, monkeypatch):
    import src.providers.vor as vor

    path = tmp_path / "vor_request_count.json"
    monkeypatch.setattr(vor, "REQUEST_COUNT_FILE", path)
    yield
    if path.exists():
        path.unlink()
