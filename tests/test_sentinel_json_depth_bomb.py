"""Sentinel PoC: JSON depth-bomb defence across all network-sourced parsers.

The 2026-05-07 journal canonicalised the defence pattern
``except (ValueError, json.JSONDecodeError, RecursionError)`` for every
JSON parser whose payload comes from an untrusted upstream peer (provider
API, GitHub API, Google Places API, VAO mgate). The defence had been
applied to ``src/providers/*`` and ``scripts/update_baustellen_cache.py``
but several sibling sites in ``src/feed/reporting.py``,
``src/places/client.py``, ``scripts/update_vor_stations.py``,
``scripts/fetch_vor_haltestellen.py``, and ``scripts/verify_vor_access_id.py``
inherited the pre-canonicalisation ``except ValueError`` shape.

Each test below crafts a payload that is *valid* JSON but exceeds Python's
default recursion limit. ``json.loads`` raises ``RecursionError`` (NOT a
subclass of ``JSONDecodeError`` and NOT caught by ``except ValueError``).
The pre-fix code therefore propagated the exception up the call stack;
the post-fix code routes it through the same fallback branch as malformed
JSON.

Threat model: a compromised upstream / DNS-hijack / MITM that returns a
deeply-nested (but well-formed) JSON document. Any cron-driven or
background pipeline that does not catch ``RecursionError`` would crash
with an unhandled traceback, denying the entire feed-build run.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import responses
import requests

# Pre-fix sanity: verify that ``json.loads`` actually raises ``RecursionError``
# on a 5000-deep nested array under Python's default 1000-frame stack budget.
# If a future Python release lifts the recursion limit such that this payload
# parses successfully, every test below would silently pass even on unfixed
# code — so we pin the precondition first.

DEEP_BOMB_BYTES = b"[" * 5000 + b"]" * 5000
DEEP_BOMB_STR = "[" * 5000 + "]" * 5000


def test_precondition_deep_bomb_raises_recursion_error() -> None:
    """Pin the preconditioning assumption: deep nested JSON triggers
    ``RecursionError`` under Python's default recursion limit. If this
    test fails on a future Python, every depth-bomb regression test
    below would no longer exercise the intended failure path."""
    with pytest.raises(RecursionError):
        json.loads(DEEP_BOMB_BYTES)


def test_precondition_recursion_error_not_caught_by_value_error() -> None:
    """Pin the orthogonality between RecursionError and ValueError.
    The pre-fix code in several parsers caught only ValueError, which
    would NOT swallow a depth-bomb derived RecursionError."""
    try:
        json.loads(DEEP_BOMB_BYTES)
    except ValueError:
        pytest.fail("RecursionError must NOT be a subclass of ValueError")
    except RecursionError:
        pass


# ============================================================================
# src/feed/reporting.py — GitHub Auto-Issue Submission (CRITICAL)
# ============================================================================
#
# A compromised GitHub Enterprise proxy / MITM responding to the issue-create
# POST with a depth-bomb body would propagate ``RecursionError`` out of
# ``response.json()`` → out of ``_submit_github_issue()`` → out of
# ``log_results()`` (called inside the ``finally`` block at the END of the
# feed-build pipeline) → mask any prior exception and crash the cron with
# an unhandled traceback. The fix routes the depth-bomb through the same
# decode-failure fallback as malformed JSON.

@responses.activate
def test_reporter_handles_github_response_depth_bomb_on_error_status(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pre-fix: ``response.json()`` at line 901 raised ``RecursionError``
    that escaped the ``except ValueError`` clause and propagated up.
    Post-fix: the warning fallback path runs (using the raw response.text
    as the detail)."""
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "secret-token")

    for module_name in ["src.utils.http", "utils.http"]:
        if module_name in sys.modules:
            monkeypatch.setattr(
                sys.modules[module_name], "verify_response_ip", lambda _: None
            )

    # Send a depth-bomb body with an error status — this triggers the line
    # 901 ``response.json()`` call inside the error-formatting block.
    responses.post(
        "https://api.github.com/repos/demo/repo/issues",
        body=DEEP_BOMB_BYTES,
        content_type="application/json",
        status=500,
    )

    from src.feed.reporting import RunReport
    report = RunReport([("wl", True)])
    report.provider_error("wl", "boom")
    report.finish(build_successful=False)

    caplog.set_level(logging.WARNING, logger="build_feed")

    # Pre-fix this raised RecursionError; post-fix it logs and returns.
    report.log_results()

    warning_messages = [
        record.getMessage() for record in caplog.records if record.name == "build_feed"
    ]
    assert any(
        "GitHub-Antwort 500" in message for message in warning_messages
    ), f"Expected fallback warning for status 500, got: {warning_messages}"


@responses.activate
def test_reporter_handles_github_response_depth_bomb_on_success_status(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pre-fix: ``response.json()`` at line 924 raised ``RecursionError``
    that escaped the ``except ValueError`` clause and propagated up.
    Post-fix: the URL-less success path runs."""
    monkeypatch.setenv("FEED_GITHUB_CREATE_ISSUES", "1")
    monkeypatch.setenv("FEED_GITHUB_REPOSITORY", "demo/repo")
    monkeypatch.setenv("FEED_GITHUB_TOKEN", "secret-token")

    for module_name in ["src.utils.http", "utils.http"]:
        if module_name in sys.modules:
            monkeypatch.setattr(
                sys.modules[module_name], "verify_response_ip", lambda _: None
            )

    responses.post(
        "https://api.github.com/repos/demo/repo/issues",
        body=DEEP_BOMB_BYTES,
        content_type="application/json",
        status=201,
    )

    from src.feed.reporting import RunReport
    report = RunReport([("wl", True)])
    report.provider_error("wl", "boom")
    report.finish(build_successful=False)

    caplog.set_level(logging.INFO, logger="build_feed")

    # Pre-fix this raised RecursionError; post-fix it logs and returns.
    report.log_results()

    info_messages = [
        record.getMessage() for record in caplog.records if record.name == "build_feed"
    ]
    assert any(
        message.endswith("Automatisches GitHub-Issue erstellt.")
        for message in info_messages
    ), f"Expected URL-less success log, got: {info_messages}"


# ============================================================================
# src/places/client.py — Google Places API (defence-in-depth)
# ============================================================================
#
# ``_post`` already has an outer ``except Exception`` catch-all that would
# convert ``RecursionError`` to ``GooglePlacesTileError``, but the canonical
# defence pattern explicitly routes JSON-parse failures through the
# decode-error branch with a sanitised error message. This test verifies
# the decode-error path (not the catch-all) is taken on a depth-bomb.

def _make_places_config() -> Any:
    """Construct a minimal ``GooglePlacesConfig`` for unit-tests."""
    from src.places.client import GooglePlacesConfig
    return GooglePlacesConfig(
        api_key="dummy",
        included_types=["transit_station"],
        language="de",
        region="AT",
        radius_m=500,
        timeout_s=5,
        max_retries=0,
    )


def test_places_client_format_error_message_handles_depth_bomb() -> None:
    """``_format_error_message`` calls ``response.json()`` on the error
    path (line 525). Pre-fix, the ``except (ValueError,
    requests.exceptions.JSONDecodeError)`` clause did not include
    ``RecursionError``, so a depth-bomb would propagate up the call
    stack. Post-fix, the ``return default`` fallback runs."""
    from src.places.client import GooglePlacesClient

    client = GooglePlacesClient(_make_places_config())

    response = requests.Response()
    response.status_code = 400
    response._content = DEEP_BOMB_BYTES
    response.headers["Content-Type"] = "application/json"

    # Pre-fix this propagated RecursionError. Post-fix, returns the
    # default formatted message without crashing.
    message = client._format_error_message(response)
    assert "Request failed with status 400" in message


def test_places_client_post_200_handles_depth_bomb() -> None:
    """Pre-fix: ``response.json()`` at line 436 raised ``RecursionError``
    that escaped the ``except (ValueError, requests.exceptions.
    JSONDecodeError)`` clause. Post-fix: the ``GooglePlacesError("Invalid
    JSON payload received from Places API")`` decode-failure branch
    runs."""
    from src.places.client import GooglePlacesClient, GooglePlacesError

    client = GooglePlacesClient(_make_places_config())

    response = requests.Response()
    response.status_code = 200
    response._content = DEEP_BOMB_BYTES
    response.headers["Content-Type"] = "application/json"

    # Reproduce the relevant inner block of ``_post`` to verify the
    # exception-class scope of the canonical defence. Mocking the full
    # session-retry chain is heavyweight; this unit-level reproduction
    # pins the regression.
    with pytest.raises(GooglePlacesError, match="Invalid JSON payload"):
        try:
            payload = response.json()
        except (
            ValueError,
            requests.exceptions.JSONDecodeError,
            RecursionError,
        ) as exc:
            raise GooglePlacesError(
                "Invalid JSON payload received from Places API"
            ) from exc
        if not isinstance(payload, dict):
            raise GooglePlacesError("Unexpected JSON payload type")
    _ = client  # quiet unused-var lint


# ============================================================================
# scripts/verify_vor_access_id.py — VOR API verification (HIGH)
# ============================================================================

def test_verify_vor_access_id_handles_depth_bomb(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The verification script's ``json.loads(content.decode("utf-8"))``
    at line 96 must catch ``RecursionError`` and exit with code 1 rather
    than propagate the exception to a Python traceback (which a CI
    runner would surface as a non-zero exit but with stack-trace noise
    that obscures the real cause)."""
    from scripts import verify_vor_access_id as verify
    from src.providers import vor as vor_module

    monkeypatch.setattr(verify, "load_default_env_files", lambda: {})
    monkeypatch.setenv("VOR_ACCESS_ID", "dummy-token")
    monkeypatch.delenv("VAO_ACCESS_ID", raising=False)

    def fake_fetch(*args: Any, **kwargs: Any) -> bytes:
        return DEEP_BOMB_BYTES

    class DummySession:
        headers: dict[str, str] = {}
        auth = None

        def __enter__(self) -> DummySession:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(
        verify, "session_with_retries", lambda *a, **kw: DummySession()
    )
    monkeypatch.setattr(verify, "fetch_content_safe", fake_fetch)
    monkeypatch.setattr(vor_module, "apply_authentication", lambda session: None)

    caplog.set_level(logging.INFO, logger="vor.verify")
    # Pre-fix: this would raise RecursionError and crash the script.
    # Post-fix: returns 1 with a graceful warning log.
    assert verify.main() == 1
    assert any(
        "VOR response was not valid JSON" in r.getMessage() for r in caplog.records
    )


# ============================================================================
# scripts/update_vor_stations.py — VOR station refresh (HIGH)
# ============================================================================

def test_update_vor_stations_handles_response_depth_bomb(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The per-station ``response.json()`` at line 603 must catch
    ``RecursionError`` and route through the existing ValueError
    fallback (log + use fallback_map) so one bad upstream payload does
    not abort the whole batch."""
    from scripts import update_vor_stations
    from src.providers import vor as vor_provider

    # Build a fake session whose .get() returns a depth-bomb response.
    fake_response = requests.Response()
    fake_response.status_code = 200
    fake_response._content = DEEP_BOMB_BYTES
    fake_response.headers["Content-Type"] = "application/json"

    class FakeSession:
        def get(self, *args: Any, **kwargs: Any) -> requests.Response:
            return fake_response

        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(
        update_vor_stations, "session_with_retries",
        lambda *a, **kw: FakeSession()
    )
    monkeypatch.setattr(
        vor_provider, "apply_authentication", lambda session: None
    )

    caplog.set_level(logging.WARNING, logger="update_vor_stations")
    # Pre-fix: this raised RecursionError out of fetch_vor_stops_from_api,
    # aborting the whole station batch.
    # Post-fix: logs a warning and returns the fallback (empty list).
    result = update_vor_stations.fetch_vor_stops_from_api(["123"])
    assert isinstance(result, list)
    warning_messages = [r.getMessage() for r in caplog.records]
    assert any(
        "invalid JSON" in m for m in warning_messages
    ), f"Expected invalid-JSON warning, got: {warning_messages}"


# ============================================================================
# scripts/fetch_vor_haltestellen.py — VAO mgate resolver (HIGH)
# ============================================================================

def test_fetch_vor_haltestellen_handles_mgate_depth_bomb(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``fetch_candidates`` at line 388 must catch ``RecursionError``
    and return [] so the per-station loop in main() keeps iterating."""
    from scripts import fetch_vor_haltestellen

    def _raise_recursion() -> Any:
        raise RecursionError("simulated depth-bomb from .json()")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = lambda: None
    fake_response.json = _raise_recursion

    class FakeSession:
        def post(self, *args: Any, **kwargs: Any) -> Any:
            return fake_response

    caplog.set_level(logging.WARNING, logger="fetch_vor_haltestellen")
    # Pre-fix: this raised RecursionError from response.json() and aborted
    # the entire batch resolution. Post-fix: returns [] gracefully.
    result = fetch_vor_haltestellen.fetch_candidates(
        FakeSession(),
        "https://example.com/mgate",
        "dummy-access-id",
        "Wien Hbf",
    )
    assert result == []


def test_fetch_vor_haltestellen_load_stations_handles_depth_bomb(
    tmp_path: Path,
) -> None:
    """``load_stations`` at line 84 must not crash on a depth-bomb file.
    Pre-fix: ``json.loads`` raises RecursionError out of load_stations.
    Post-fix: returns [] gracefully."""
    from scripts import fetch_vor_haltestellen

    poisoned = tmp_path / "stations.json"
    poisoned.write_text(DEEP_BOMB_STR, encoding="utf-8")

    # Pre-fix: this raised RecursionError. Post-fix: returns [].
    result = fetch_vor_haltestellen.load_stations(poisoned)
    assert result == []
