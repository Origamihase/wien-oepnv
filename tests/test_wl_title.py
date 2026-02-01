from datetime import datetime, timezone

from src.providers.wl_fetch import fetch_events
from src.providers.wl_lines import (
    _detect_line_pairs_from_text,
    _ensure_line_prefix,
)
from src.providers.wl_text import _tidy_title_wl


def test_bucket_merge_prefers_informative_title_and_description(monkeypatch):
    now_iso = datetime.now(timezone.utc).isoformat()
    detailed_desc = (
        "Störung zwischen Siebenhirten und Perfektastraße. Ersatzverkehr im Einsatz."
    )

    generic = {
        "title": "Falschparker",
        "description": "Störung",
        "time": {"start": now_iso},
        "attributes": {"relatedLines": ["U6"]},
    }

    detailed = {
        "title": "Falschparker blockiert Linie U6 bei Siebenhirten",
        "description": detailed_desc,
        "time": {"start": now_iso},
        "attributes": {
            "relatedLines": ["U6"],
            "relatedStops": [
                {"name": "Siebenhirten"},
                {"name": "Perfektastraße"},
            ],
            "station": "Siebenhirten (U6)",
            "location": "Bereich Perfektastraße",
        },
    }

    class DummySession:
        def __init__(self):
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "src.providers.wl_fetch._fetch_traffic_infos",
        lambda *a, **kw: [generic, detailed],
    )
    monkeypatch.setattr(
        "src.providers.wl_fetch._fetch_news",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "src.providers.wl_fetch.session_with_retries",
        lambda *a, **kw: DummySession(),
    )

    events = fetch_events(timeout=0)

    assert len(events) == 1
    event = events[0]
    assert "blockiert Linie U6 bei Siebenhirten" in event["title"]
    assert event["description"].startswith(detailed_desc)
    assert "Perfektastraße" in event["description"]
    assert len(event["description"]) > len("Störung")


def test_line_prefix_and_house_number_false_positive():
    assert _ensure_line_prefix("Falschparker", ["5"]) == "5: Falschparker"
    assert _detect_line_pairs_from_text("Neubaugasse 69") == []


def test_line_prefix_empty_title():
    assert _ensure_line_prefix("5:", ["5"]) == "5"
    assert _ensure_line_prefix("5: ", ["5"]) == "5"


def test_tidy_title_wl_strips_label():
    assert _tidy_title_wl("Störung: U1 steht") == "U1 steht"

