"""Sentinel: per-loader byte cap pinning for the Stammstrecke cache.

The Stammstrecke cron monitor (``scripts/update_stammstrecke_status.py``)
writes ``cache/stammstrecke/events.json`` with at most 2 events. The two
canonical readers — ``src/build_feed.py:read_cache_stammstrecke`` and
``src/feed/providers.py:read_cache_stammstrecke`` — load that file via
:func:`src.utils.files.read_capped_json`. Both readers MUST pass the
:data:`MAX_STAMMSTRECKE_CACHE_BYTES` cap explicitly so the canonical 50
MiB :data:`DEFAULT_MAX_JSON_FILE_BYTES` ceiling does not leave a 50,000x
amplification window for an attacker who can plant a single oversized
cache file (compromised CI runner / partial flush + power loss /
parallel orchestrator process performing an atomic state swap mid-read).

Threat model: identical to ``.jules/sentinel.md`` (entry for 2026-05-09).
A planted-huge cache file size-bombs the build_feed pipeline; the per-
loader cap reduces the worst-case allocation from 50 MiB to 256 KiB.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feed.providers import (  # noqa: E402
    MAX_STAMMSTRECKE_CACHE_BYTES,
    read_cache_stammstrecke as providers_reader,
)


def test_max_stammstrecke_cache_bytes_is_canonical_value() -> None:
    """Pin the per-loader cap so a future "tighten further" change is a
    single search-replace and the cap drift between consumer sites is
    impossible by construction."""
    # Sized at ~128x the largest legitimate state shape (~2 KiB) — see
    # the inline rationale in ``src/feed/providers.py``.
    assert MAX_STAMMSTRECKE_CACHE_BYTES == 256 * 1024


def test_providers_reader_rejects_oversized_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PoC: ``src/feed/providers.py:read_cache_stammstrecke`` must reject a
    cache file larger than ``MAX_STAMMSTRECKE_CACHE_BYTES``. Pre-fix the
    reader used the canonical 50 MiB default, so a 1 MiB poisoned file
    (well above the legitimate ~2 KiB shape, well below the canonical
    ceiling) was happily parsed and ingested."""
    fake_path = tmp_path / "cache" / "stammstrecke" / "events.json"
    fake_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "_identity": "stammstrecke_delay_meidling|2026-05-09T08:00:00+02:00",
            "first_seen": "2026-05-09T08:00:00+02:00",
            "title": "S-Bahn Stammstrecke Verspätungen",
            "description": "test",
            "pad": "A" * (MAX_STAMMSTRECKE_CACHE_BYTES + 1024),
        }
    ]
    fake_path.write_text(json.dumps(payload), encoding="utf-8")
    assert fake_path.stat().st_size > MAX_STAMMSTRECKE_CACHE_BYTES

    import src.feed.providers as providers_mod

    monkeypatch.setattr(providers_mod, "_STAMMSTRECKE_CACHE_PATH", fake_path)
    # Post-fix: oversized file is rejected; reader returns []. Pre-fix
    # (50 MiB default cap): the file would parse and the event flow into
    # the feed.
    assert providers_reader() == []


def test_providers_reader_accepts_legitimate_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sanity: a legitimate ~1 KiB cache must round-trip through the
    tightened reader unchanged. Without this pin the cap could regress
    so low it rejects the production state shape."""
    fake_path = tmp_path / "cache" / "stammstrecke" / "events.json"
    fake_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "_identity": "stammstrecke_delay_meidling|2026-05-09T08:00:00+02:00",
            "first_seen": "2026-05-09T08:00:00+02:00",
            "title": "S-Bahn Stammstrecke Verspätungen",
            "description": "Durchschnittliche Verspätung von 12 Minuten",
            "guid": "abc123",
        }
    ]
    fake_path.write_text(json.dumps(payload), encoding="utf-8")

    import src.feed.providers as providers_mod

    monkeypatch.setattr(providers_mod, "_STAMMSTRECKE_CACHE_PATH", fake_path)
    result = providers_reader()
    assert len(result) == 1
    assert result[0]["_identity"].startswith("stammstrecke_delay_meidling|")


def test_providers_reader_passes_explicit_max_bytes() -> None:
    """Walker: ``read_cache_stammstrecke`` MUST forward the per-loader cap
    to ``read_capped_json`` rather than relying on the default. We verify
    by patching ``read_capped_json`` and asserting the call kwargs.

    This is the structural defence against a future contributor who
    refactors away the explicit ``max_bytes`` keyword and unwittingly
    falls back to the 50 MiB default — at that point the patch fails
    here, before the change can land."""
    import src.feed.providers as providers_mod

    captured_kwargs: dict[str, object] = {}

    def fake_read(*args: object, **kwargs: object) -> object:
        captured_kwargs.update(kwargs)
        return []

    with patch.object(providers_mod, "_STAMMSTRECKE_CACHE_PATH") as path_mock:
        path_mock.exists.return_value = True
        with patch.object(providers_mod, "read_capped_json", fake_read):
            providers_reader()

    assert captured_kwargs.get("max_bytes") == MAX_STAMMSTRECKE_CACHE_BYTES, (
        "read_cache_stammstrecke must forward MAX_STAMMSTRECKE_CACHE_BYTES "
        "explicitly to read_capped_json — relying on the default leaves a "
        "50,000x amplification window for a planted-huge cache file."
    )
