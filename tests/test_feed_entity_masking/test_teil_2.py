"""Teil 2 der Tests aus tests/test_feed_entity_masking.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

from typing import Any


from src import build_feed
from tests._conftest.build_feed_bindings import setattr_all


def test_translate_text_handles_dropped_placeholder_gracefully(
    monkeypatch: Any,
) -> None:
    """If the translator drops a placeholder the rest of the output is
    still restored and no stray ``XENT…`` token leaks to subscribers."""
    def fake_pipeline(text: str, **kwargs: Any) -> list[dict[str, str]]:
        # Strip every placeholder from the "translation" — simulates a
        # model that aggressively rewrites unfamiliar tokens.
        return [{"translation_text": build_feed._ENTITY_PLACEHOLDER_RE.sub("", text)}]

    setattr_all(monkeypatch, build_feed, "_get_translation_pipeline", lambda: fake_pipeline)
    text = "Wiener Linien informieren über U6."
    out = build_feed._translate_text(text)
    # No stray placeholders survive into the published feed.
    assert "XENT" not in out
    # The non-entity remainder is still present.
    assert "informieren" in out


def test_translation_state_is_a_single_dict() -> None:
    """CodeQL flagged the dual-global pattern as an unused variable;
    the refactor consolidates state into a single module-level dict
    so the analyser does not emit a false positive on the circuit
    breaker assignment."""
    assert isinstance(build_feed._TRANSLATION_STATE, dict)
    assert "pipeline" in build_feed._TRANSLATION_STATE
    assert "load_failed" in build_feed._TRANSLATION_STATE


def test_get_translation_pipeline_short_circuits_after_failure() -> None:
    """After a failed load the function must short-circuit on the
    ``load_failed`` flag (CodeQL flagged the dual-global precursor as
    an unused-global write; the consolidated dict makes the circuit
    breaker visible to the analyser AND to this test)."""
    # Reset to a known clean state and simulate a previously-failed load.
    saved_pipeline = build_feed._TRANSLATION_STATE["pipeline"]
    saved_load_failed = build_feed._TRANSLATION_STATE["load_failed"]
    try:
        build_feed._TRANSLATION_STATE["pipeline"] = None
        build_feed._TRANSLATION_STATE["load_failed"] = True
        # Must short-circuit and return None without re-attempting import.
        assert build_feed._get_translation_pipeline() is None
        # The load_failed flag is unchanged by the short-circuit.
        assert build_feed._TRANSLATION_STATE["load_failed"] is True
    finally:
        build_feed._TRANSLATION_STATE["pipeline"] = saved_pipeline
        build_feed._TRANSLATION_STATE["load_failed"] = saved_load_failed
