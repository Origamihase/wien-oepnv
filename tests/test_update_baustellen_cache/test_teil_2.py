"""Teil 2 der Tests aus tests/test_update_baustellen_cache.py.

Beschreibung in __init__.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import update_baustellen_cache

from ._support import (
    _real_cache_file,
    _site,
)


def test_collect_events_survives_malformed_features(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End-to-end: a payload whose ``features`` field is a truthy non-list
    must produce zero events without raising. Without the shape guard,
    ``int`` / ``bool`` shapes raised ``TypeError`` out of ``_collect_events``
    and aborted the whole cache update — bypassing the documented
    fallback path used when the network is unreachable."""
    import logging

    caplog.set_level(logging.WARNING, logger="update_baustellen_cache")
    payload = {"type": "FeatureCollection", "features": 99999}
    events = update_baustellen_cache._collect_events(payload)
    assert events == []


def test_fetch_remote_handles_json_depth_bomb(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Resilience: a malicious or pathological upstream serving a deeply-
    nested JSON document must not crash the cron job. ``json.loads`` raises
    ``RecursionError`` (NOT a subclass of ``JSONDecodeError``) when the
    body exceeds Python's recursion limit. The previous
    ``except (UnicodeDecodeError, json.JSONDecodeError)`` clause in
    ``_load_json_from_content`` would let ``RecursionError`` propagate and
    terminate the process — the same drift fixed canonically in
    ``src/providers/wl_fetch.py`` and ``src/providers/vor.py``.
    Verifying the parity here means a future change to the parser cannot
    silently regress the depth-bomb defence."""
    import logging

    deep = b"[" * 5000 + b"]" * 5000

    def fake_fetch(*_args: Any, **_kwargs: Any) -> bytes:
        return deep

    monkeypatch.setattr(update_baustellen_cache, "fetch_content_safe", fake_fetch)
    monkeypatch.setattr(
        update_baustellen_cache, "validate_http_url", lambda url, **_kw: url
    )

    caplog.set_level(logging.WARNING, logger="update_baustellen_cache")
    result = update_baustellen_cache._fetch_remote(
        update_baustellen_cache.DEFAULT_DATA_URL, timeout=5
    )
    assert result is None


def test_load_fallback_handles_json_depth_bomb(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Defence-in-depth: a tampered or accidentally committed fallback
    file containing a deeply-nested JSON document must be rejected with a
    clear error log instead of crashing the cron job via a propagated
    ``RecursionError``. The fallback is the documented offline path when
    the network is unreachable, so a depth-bomb here would deny both
    fetch *and* fallback simultaneously."""
    import logging

    poisoned = tmp_path / "poisoned.geojson"
    # 100,000 levels: Python 3.12+ parses 5,000 without a RecursionError
    # (the fallback then fails as "kein JSON-Objekt"), so the depth must
    # exceed every interpreter's limit for this test to reach its branch.
    poisoned.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")

    caplog.set_level(logging.ERROR, logger="update_baustellen_cache")
    result = update_baustellen_cache._load_fallback(poisoned)
    assert result is None
    assert any(
        "ungültiges JSON" in record.getMessage()
        for record in caplog.records
        if record.name == "update_baustellen_cache"
    )


def test_main_keeps_the_cache_when_the_city_delivers_no_site(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Zero raw records is an outage, not a quiet day (Quellenausfall 2026-10-04).

    Both layers always list long-running works. Only a selection that the
    transit rule empties may empty the cache.
    """
    import logging

    calls: list[Any] = []

    def fake_fetch_remote(url: str, timeout: int) -> dict[str, Any]:
        return {"type": "FeatureCollection", "features": []}

    monkeypatch.setattr(update_baustellen_cache, "_fetch_remote", fake_fetch_remote)
    monkeypatch.setattr(
        update_baustellen_cache, "write_cache", lambda *a, **kw: calls.append(a)
    )
    caplog.set_level(logging.WARNING, logger="update_baustellen_cache")

    assert update_baustellen_cache.main() == 1
    assert calls == []
    assert any(
        "0 Baustellen" in record.getMessage()
        for record in caplog.records
        if record.name == "update_baustellen_cache"
    )


def test_main_lets_a_live_answer_shrink_a_populated_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """15 cached sites, two of them with a transit effect: the real step of 2026-10-06.

    The 20 % guard of ``write_cache`` (2 of 15 = 13 %) would have refused the
    write on every tick, and Rennweg (one lane closed at night) would have
    stayed in the feed until its end on 15.11.
    """
    target = tmp_path / "cache" / "baustellen" / "events.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps([{"guid": f"old-{i}"} for i in range(15)]), encoding="utf-8")
    _real_cache_file(monkeypatch, target)

    features = [
        _site("Rennweg von 33A bis 37", "Der linke Fahrstreifen wird gesperrt."),
        _site("Hauptstraße", "Die Buslinie 7A wird über die Rothenhofgasse umgeleitet."),
        _site("Gasse 1", "Die Haltestelle der Linie 13A wird verlegt."),
    ] + [_site(f"Gasse {i}", "Die Fahrbahn wird eingeengt.") for i in range(2, 8)]

    def fake_fetch_layers(data_url: str, timeout: int) -> list[dict[str, Any]]:
        return update_baustellen_cache._collect_events(
            {"type": "FeatureCollection", "features": features}
        )

    monkeypatch.setattr(update_baustellen_cache, "_fetch_layers", fake_fetch_layers)

    assert update_baustellen_cache.main() == 0
    written = json.loads(target.read_text(encoding="utf-8"))
    assert sorted(item["title"] for item in written) == ["Gasse 1", "Hauptstraße"]


def test_main_empties_the_cache_when_no_site_affects_transit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The city delivers sites, none moves a stop: the cache becomes empty, exit 0."""
    target = tmp_path / "cache" / "baustellen" / "events.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps([{"guid": f"old-{i}"} for i in range(15)]), encoding="utf-8")
    _real_cache_file(monkeypatch, target)
    features = [_site(f"Gasse {i}", "Die Fahrbahn wird eingeengt.") for i in range(6)]
    monkeypatch.setattr(
        update_baustellen_cache,
        "_fetch_layers",
        lambda *_a: update_baustellen_cache._collect_events(
            {"type": "FeatureCollection", "features": features}
        ),
    )

    assert update_baustellen_cache.main() == 0
    assert json.loads(target.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "answer",
    [None, {"type": "FeatureCollection", "features": []}, {"unexpected": True}],
    ids=["http-error-or-invalid-json", "zero-sites", "no-feature-list"],
)
def test_a_failed_or_empty_fetch_never_shrinks_the_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answer: Any
) -> None:
    """Real ``write_cache`` on disk: an outage keeps the old cache and exits non-zero."""
    target = tmp_path / "cache" / "baustellen" / "events.json"
    target.parent.mkdir(parents=True)
    old = [{"guid": f"old-{i}"} for i in range(15)]
    target.write_text(json.dumps(old), encoding="utf-8")
    _real_cache_file(monkeypatch, target)
    # ``_fetch_remote`` returns None on HTTP errors and invalid JSON.
    monkeypatch.setattr(update_baustellen_cache, "_fetch_remote", lambda *_a: answer)
    monkeypatch.setattr(update_baustellen_cache, "_cache_exists", lambda: True)

    assert update_baustellen_cache.main() == 1
    assert json.loads(target.read_text(encoding="utf-8")) == old


def test_demo_sample_still_meets_the_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bundled demo sample never shrinks a populated cache."""
    target = tmp_path / "cache" / "baustellen" / "events.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps([{"guid": f"old-{i}"} for i in range(30)]), encoding="utf-8")
    _real_cache_file(monkeypatch, target)
    monkeypatch.setattr(update_baustellen_cache, "_fetch_layers", lambda *_a: None)
    monkeypatch.setattr(update_baustellen_cache, "_cache_exists", lambda: False)
    monkeypatch.setattr(
        update_baustellen_cache, "_log_endpoint_diagnostic", lambda *a, **k: None
    )

    assert update_baustellen_cache.main() == 1
    assert len(json.loads(target.read_text(encoding="utf-8"))) == 30
