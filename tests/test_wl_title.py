from src.providers.wl_lines import (
    _detect_line_pairs_from_text,
    _ensure_line_prefix,
    _line_tokens_from_pairs,
)
from src.providers.wl_text import _tidy_title_wl, _topic_key_from_title
from src.utils.ids import make_guid


def test_dedupe_topic_shorter_title():
    ev1 = {
        "category": "Störung",
        "title": "5: Falschparker",
        "topic_key": _topic_key_from_title("Falschparker"),
        "lines_pairs": [("5", "5")],
        "desc": "",
        "extras": [],
        "stop_names": set(),
        "pubDate": None,
        "starts_at": None,
        "ends_at": None,
        "_identity": "1",
    }
    ev2 = {
        "category": "Störung",
        "title": "5: Fahrtbehinderung Falschparker",
        "topic_key": _topic_key_from_title("Fahrtbehinderung Falschparker"),
        "lines_pairs": [("5", "5")],
        "desc": "",
        "extras": [],
        "stop_names": set(),
        "pubDate": None,
        "starts_at": None,
        "ends_at": None,
        "_identity": "2",
    }

    buckets = {}
    for ev in (ev1, ev2):
        key = make_guid(
            "wl",
            ev["category"],
            ev["topic_key"],
            ",".join(sorted(_line_tokens_from_pairs(ev["lines_pairs"]))),
        )
        b = buckets.get(key)
        if not b:
            buckets[key] = dict(ev)
        else:
            if len(ev["title"]) < len(b["title"]):
                b["title"] = ev["title"]

    assert len(buckets) == 1
    assert list(buckets.values())[0]["title"] == "5: Falschparker"


def test_line_prefix_and_house_number_false_positive():
    assert _ensure_line_prefix("Falschparker", ["5"]) == "5: Falschparker"
    assert _detect_line_pairs_from_text("Neubaugasse 69") == []


def test_line_prefix_empty_title():
    assert _ensure_line_prefix("5:", ["5"]) == "5"
    assert _ensure_line_prefix("5: ", ["5"]) == "5"


def test_tidy_title_wl_strips_label():
    assert _tidy_title_wl("Störung: U1 steht") == "U1 steht"

