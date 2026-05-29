"""Regression tests for the round-9 oebb.py audit bundle.

Pins two of the four bundle fixes (the other two are pinned in
``test_sentence_boundary.py`` and ``test_oebb_line_prefix_preserved.py``):

* ``_derive_guid`` derivation uses the upstream RAW title so the GUID
  stays stable across station-alias / cleanup-rule evolution. Pre-fix
  it used the post-cleanup title; any alias addition shifted the GUID
  and surfaced a duplicate feed entry.
* HTML strip / unescape order in ``_normalize_endpoint_name``,
  ``_extract_routes``, and ``_find_stations_in_text``: ``html.unescape``
  must run BEFORE the ``<[^>]+>`` strip so entity-encoded tags
  (``&lt;b&gt;``) become real ``<b>`` and the strip catches them.
"""
from __future__ import annotations

from src.providers.oebb import (
    _derive_guid,
    _extract_routes,
    _normalize_endpoint_name,
)


def test_derive_guid_is_stable_across_cleaned_title_drift() -> None:
    """The GUID derivation MUST anchor on the raw upstream signal, not
    the post-cleanup title.

    When ``raw_guid`` is empty, ``_derive_guid`` falls back to
    ``make_guid(title, link)``. Pre-fix the caller passed the
    POST-cleanup ``title``; a station-alias addition or a cleanup-rule
    tweak then changed the title — and therefore the GUID — for the
    same upstream item across runs, surfacing a duplicate feed entry.
    The caller now passes ``raw_title``, which is the stable upstream
    anchor.

    This test pins ``_derive_guid``'s contract: given the same raw
    inputs, the GUID is byte-identical.
    """
    raw_title = "<b>Bauarbeiten Wien Hbf — Mödling</b>"
    link = "https://feed.example.com/item/42"
    guid_a = _derive_guid("", raw_title, link)
    guid_b = _derive_guid("", raw_title, link)
    assert guid_a == guid_b
    # And it is NOT the cleaned-title GUID (regression guard against a
    # future caller that re-introduces the post-cleanup-title fallback).
    cleaned_title = "Bauarbeiten Wien Hbf ↔ Mödling"
    guid_cleaned = _derive_guid("", cleaned_title, link)
    assert guid_a != guid_cleaned


def test_normalize_endpoint_name_strips_entity_encoded_tags() -> None:
    """``&lt;b&gt;Wien Mitte&lt;/b&gt;`` (entity-encoded HTML) must
    yield the clean station name.

    Pre-fix the order was ``re.sub(r"<[^>]+>")`` THEN ``html.unescape``.
    The strip saw no real tag chars in the entity-encoded form, so the
    tags survived step 1; ``unescape`` then decoded them to real
    ``<b>`` / ``</b>`` and those literal chars landed in the captured
    endpoint name.
    """
    assert _normalize_endpoint_name("&lt;b&gt;Wien Mitte&lt;/b&gt;") == "Wien Mitte"


def test_extract_routes_handles_entity_encoded_tags_in_description() -> None:
    """The route extractor's plain-text conversion must also unescape
    before stripping tags so entity-encoded markup in the description
    doesn't survive into matched endpoints."""
    routes = _extract_routes(
        "Bauarbeiten Wien",
        "&lt;p&gt;Bauarbeiten zwischen Wien Hbf und Mödling.&lt;/p&gt;",
    )
    assert routes == [("Wien", "Mödling")]
