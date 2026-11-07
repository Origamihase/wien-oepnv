"""A WL notice without text waits until WL fills it (decision 2026-11-07, #2170)."""

from __future__ import annotations

from src.feed_build.cache_filters import _is_empty_wl_notice, _post_filter_wl


def _notice(description: object, category: str = "Hinweis") -> dict[str, object]:
    return {
        "category": category,
        "title": "5B: Bauarbeiten",
        "description": description,
        "guid": "7704618ff7f5",
    }


def test_empty_paragraph_notice_is_held_back() -> None:
    # The real 06.11.2026 13:59 shape: "<p></p>".
    assert _post_filter_wl([_notice("<p></p>")]) == []


def test_notice_with_only_tags_spaces_and_nbsp_is_held_back() -> None:
    for desc in ("", "   ", "<p>&nbsp;</p> <p class='MsoNormal'> </p>", "\xa0"):
        assert _is_empty_wl_notice(_notice(desc)), repr(desc)
    assert _post_filter_wl([_notice("<p>&nbsp;</p>")]) == []


def test_notice_without_description_key_is_not_judged() -> None:
    # The WL provider always writes a string; a dict without one is kept.
    item = _notice("")
    del item["description"]
    assert not _is_empty_wl_notice(item)
    assert len(_post_filter_wl([item])) == 1


def test_notice_with_text_stays() -> None:
    text = "<p>Wegen Straßenbauarbeiten in der Wexstraße werden die Busse umgeleitet.</p>"
    out = _post_filter_wl([_notice(text)])
    assert len(out) == 1
    assert out[0]["title"] == "5B: Bauarbeiten"


def test_disruption_without_text_stays() -> None:
    # A Störung names line and cause in its title ("U2: Betriebsstörung").
    item = _notice("", category="Störung")
    item["title"] = "U2: Betriebsstörung"
    out = _post_filter_wl([item])
    assert [i["title"] for i in out] == ["U2: Betriebsstörung"]


def test_filled_notice_on_the_next_fetch_comes_in() -> None:
    first = _post_filter_wl([_notice("<p></p>")])
    second = _post_filter_wl([_notice("<p>Linie 5B: Umleitung ab Klosterneuburger Straße.</p>")])
    assert first == []
    assert [i["guid"] for i in second] == ["7704618ff7f5"]
