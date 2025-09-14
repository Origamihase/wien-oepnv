from datetime import timezone

import src.providers.vor as vor


def test_parse_dt_converts_vienna_to_utc():
    dt = vor._parse_dt("2024-07-01", "12:00")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.hour == 10
    assert dt.minute == 0
