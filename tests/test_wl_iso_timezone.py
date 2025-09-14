from datetime import timezone

from src.providers.wl_fetch import _iso


def test_iso_returns_utc_aware():
    dt = _iso("2024-07-01T12:00:00")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
