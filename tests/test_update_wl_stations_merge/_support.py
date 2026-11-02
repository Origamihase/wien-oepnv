"""Hilfen und Fixtures für die Tests in diesem Paket.

Früher in tests/test_update_wl_stations_merge.py.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from scripts import update_wl_stations


class _DummyResponse:
    """Minimal stand-in for :class:`requests.Response` used by the helper.

    Only exposes the surface ``_download_ogd_csv`` actually reads:
    ``status_code``, ``content`` and ``headers``. Headers default to an
    empty dict so tests that don't care about validators stay terse.
    """

    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b"",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = dict(headers or {})


def _install_fake_http(
    monkeypatch: pytest.MonkeyPatch,
    request_callable: object,
) -> list[dict[str, object]]:
    """Patch ``session_with_retries`` and ``request_safe`` to deterministic doubles.

    Returns a mutable list that captures one entry per ``request_safe``
    invocation, recording the URL and the request kwargs. Tests inspect
    this to assert that conditional headers were (or weren't) sent.
    """

    captured: list[dict[str, object]] = []

    def fake_session_with_retries(_user_agent):  # type: ignore[no-untyped-def]
        class _DummySession:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *_args):  # type: ignore[no-untyped-def]
                return False

        return _DummySession()

    def fake_request_safe(_session, url, **kwargs):  # type: ignore[no-untyped-def]
        captured.append({"url": url, "kwargs": kwargs})
        if callable(request_callable):
            return request_callable(url, kwargs)
        return request_callable

    import src.utils.http as http_utils
    monkeypatch.setattr(http_utils, "session_with_retries", fake_session_with_retries)
    monkeypatch.setattr(http_utils, "request_safe", fake_request_safe)
    return captured


@pytest.fixture()
def stations_path(tmp_path: Path) -> Path:
    path = tmp_path / "stations.json"
    path.write_text("[]", encoding="utf-8")
    return path


def _read_entries(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return cast(list[dict[str, object]], data.get("stations", []))
    return cast(list[dict[str, object]], data)


# ---------------------------------------------------------------------------
# Plausibility gate on WL stop coordinates.
#
# The WL OGD export ships well-formed coordinates that point at the wrong
# place: audit 2026-09-13 found six rows across three consecutive DIVAs
# (60201954 Leopoldine-Padaurek-Straße, 60201955 Halblehenweg, 60201956
# Wassermanngasse) sitting 76-112 km away in the Waldviertel while declaring
# ``Municipality=Wien``. Finiteness and WGS-84 range checks cannot catch that.
#
# Wassermanngasse shows the consequence: its bad value reached the directory
# (its coordinate slot happened to be empty), the station was classified
# ``in_vienna=False``, and "Störung in der Wassermanngasse" stopped counting
# as a Vienna message — a genuine Vienna disruption would drop out of the feed.
# ---------------------------------------------------------------------------


def _stop(
    stop_id: str, latitude: float | None, longitude: float | None
) -> update_wl_stations.Haltepunkt:
    return update_wl_stations.Haltepunkt(
        station_id="60201956",
        stop_id=stop_id,
        name="Wassermanngasse",
        latitude=latitude,
        longitude=longitude,
    )


__all__ = [
    "_DummyResponse",
    "_install_fake_http",
    "_read_entries",
    "_stop",
    "stations_path",
]
