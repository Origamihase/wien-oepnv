import base64
import importlib
import logging
import pytest
import requests

import src.providers.vor as vor


def test_access_id_env_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    # VOR_ACCESS_ID mit Leerzeichen wird entfernt und deaktiviert den Provider
    monkeypatch.setenv("VOR_ACCESS_ID", "   ")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == ""

    # Fallback auf VAO_ACCESS_ID, ebenfalls Leerzeichen -> deaktiviert
    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    monkeypatch.setenv("VAO_ACCESS_ID", "   ")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == ""

    # VAO_ACCESS_ID mit zusätzlichen Leerzeichen wird getrimmt
    monkeypatch.setenv("VAO_ACCESS_ID", " token ")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == "token"

    # Aufräumen
    monkeypatch.delenv("VAO_ACCESS_ID", raising=False)
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == ""






def test_max_requests_per_day_capped_at_contract_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Security: VOR_MAX_REQUESTS_PER_DAY must never exceed the hard contract
    # cap of 100/day, regardless of an env override (intentional misconfig,
    # leaked CI env, or compromised secret store). Disabling this cap would
    # let the daily quota gate be bypassed in 8+ call sites that read
    # MAX_REQUESTS_PER_DAY, risking suspension of the access ID by VAO.
    monkeypatch.setenv("VOR_MAX_REQUESTS_PER_DAY", "99999")
    importlib.reload(vor)
    assert vor.MAX_REQUESTS_PER_DAY == vor.DEFAULT_MAX_REQUESTS_PER_DAY == 100

    # The env var may still *tighten* the budget below the contract cap.
    monkeypatch.setenv("VOR_MAX_REQUESTS_PER_DAY", "50")
    importlib.reload(vor)
    assert vor.MAX_REQUESTS_PER_DAY == 50

    monkeypatch.delenv("VOR_MAX_REQUESTS_PER_DAY", raising=False)
    importlib.reload(vor)
    assert vor.MAX_REQUESTS_PER_DAY == vor.DEFAULT_MAX_REQUESTS_PER_DAY


def test_quota_flush_batch_size_capped_at_daily_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Security: VOR_QUOTA_FLUSH_BATCH_SIZE controls the in-memory loss
    # window for the request-count quota. The atexit flush does NOT run on
    # SIGKILL / OOM kill / kernel panic, so an env override that raises the
    # batch size above MAX_REQUESTS_PER_DAY would let a single run accumulate
    # the entire daily quota in memory — and a single abnormal kill then
    # loses the count, letting the next run breach the 100/day VAO contract
    # cap. The env var may still *tighten* the batch size for tighter
    # durability, but never raise the loss window above the daily quota.
    monkeypatch.setenv("VOR_QUOTA_FLUSH_BATCH_SIZE", "99999")
    importlib.reload(vor)
    assert vor.QUOTA_FLUSH_BATCH_SIZE == vor.MAX_REQUESTS_PER_DAY == 100

    monkeypatch.setenv("VOR_QUOTA_FLUSH_BATCH_SIZE", "5")
    importlib.reload(vor)
    assert vor.QUOTA_FLUSH_BATCH_SIZE == 5

    monkeypatch.delenv("VOR_QUOTA_FLUSH_BATCH_SIZE", raising=False)
    importlib.reload(vor)
    assert vor.QUOTA_FLUSH_BATCH_SIZE == vor.DEFAULT_QUOTA_FLUSH_BATCH_SIZE


















def test_refresh_access_credentials_reloads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOR_ACCESS_ID", "first")
    importlib.reload(vor)
    assert vor.VOR_ACCESS_ID == "first"

    monkeypatch.setenv("VOR_ACCESS_ID", "second")
    refreshed = vor.refresh_access_credentials()

    assert refreshed == "second"
    assert vor.VOR_ACCESS_ID == "second"

    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    importlib.reload(vor)


def test_base_url_prefers_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both VOR_BASE_URL and VOR_BASE are pinned to the official VAO host
    # (``routenplaner.verkehrsauskunft.at``); see ``_VOR_TRUSTED_HOSTS`` for
    # the rationale. ``VOR_BASE_URL`` wins over ``VOR_BASE`` when both are set.
    monkeypatch.setenv("VOR_BASE", "https://routenplaner.verkehrsauskunft.at/vao/restproxy")
    monkeypatch.setenv(
        "VOR_BASE_URL", "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v2.0.0"
    )

    importlib.reload(vor)

    assert vor.VOR_BASE_URL == "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v2.0.0/"
    assert vor.VOR_VERSION == "v2.0.0"

    monkeypatch.delenv("VOR_BASE_URL", raising=False)
    importlib.reload(vor)
    assert (
        vor.VOR_BASE_URL
        == "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/"
    )

    monkeypatch.delenv("VOR_BASE", raising=False)
    importlib.reload(vor)
    assert (
        vor.VOR_BASE_URL
        == "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/"
    )


def test_base_url_rejects_untrusted_host(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An env override pointing at a non-VAO host must fall back to the default
    so ``VorAuth`` cannot redirect ``VOR_ACCESS_ID`` to an attacker."""

    monkeypatch.setenv("VOR_BASE_URL", "https://attacker.example.com/api/")

    with caplog.at_level(logging.WARNING):
        importlib.reload(vor)

    assert (
        vor.VOR_BASE_URL
        == "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/"
    )
    assert "VOR_BASE_URL" in caplog.text
    assert "VAO-Host" in caplog.text

    monkeypatch.delenv("VOR_BASE_URL", raising=False)
    monkeypatch.setenv("VOR_BASE", "https://attacker.example.com/api/")
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        importlib.reload(vor)

    assert (
        vor.VOR_BASE_URL
        == "https://routenplaner.verkehrsauskunft.at/vao/restproxy/v1.11.0/"
    )
    assert "VOR_BASE" in caplog.text
    assert "VAO-Host" in caplog.text

    monkeypatch.delenv("VOR_BASE", raising=False)
    importlib.reload(vor)


def test_apply_authentication_sets_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOR_ACCESS_ID", "secret")
    importlib.reload(vor)

    session = requests.Session()
    if "Authorization" in session.headers:
        del session.headers["Authorization"]
    # Clear Accept to allow setdefault to work (mimicking DummySession behavior)
    if "Accept" in session.headers:
        del session.headers["Accept"]

    vor.apply_authentication(session)

    assert session.headers["Accept"] == "application/json"
    assert "Authorization" not in session.headers
    assert isinstance(session.auth, vor.VorAuth)

    # Test Auth Application
    req = requests.PreparedRequest()
    # Must use VOR_BASE_URL to trigger injection
    req.prepare("GET", vor.VOR_BASE_URL + "endpoint")
    req = session.auth(req)

    assert req.headers["Authorization"] == "Bearer secret"
    # User requested to inject accessId even if header present
    assert "accessId=secret" in req.url

    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    importlib.reload(vor)


def test_apply_authentication_basic_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOR_ACCESS_ID", "user:secret")
    importlib.reload(vor)

    session = requests.Session()
    if "Authorization" in session.headers:
        del session.headers["Authorization"]

    vor.apply_authentication(session)

    expected = base64.b64encode(b"user:secret").decode("ascii")
    # Verify auth object
    req = requests.PreparedRequest()
    req.prepare("GET", vor.VOR_BASE_URL + "endpoint")
    req = session.auth(req)

    assert req.headers["Authorization"] == f"Basic {expected}"
    assert "accessId=user%3Asecret" in req.url or "accessId=user:secret" in req.url
    assert "accessId=user%3Asecret" in req.url or "accessId=user:secret" in req.url

    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    importlib.reload(vor)


def test_apply_authentication_basic_with_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOR_ACCESS_ID", "Basic user:secret")
    importlib.reload(vor)

    session = requests.Session()
    if "Authorization" in session.headers:
        del session.headers["Authorization"]

    vor.apply_authentication(session)

    expected = base64.b64encode(b"user:secret").decode("ascii")

    req = requests.PreparedRequest()
    req.prepare("GET", vor.VOR_BASE_URL + "endpoint")
    req = session.auth(req)

    assert req.headers["Authorization"] == f"Basic {expected}"
    assert "accessId=user%3Asecret" in req.url or "accessId=user:secret" in req.url

    monkeypatch.delenv("VOR_ACCESS_ID", raising=False)
    importlib.reload(vor)
