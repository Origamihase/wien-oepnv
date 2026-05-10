import logging
import pytest
from unittest.mock import MagicMock
from src.providers import vor

def test_vor_warns_on_insecure_http_with_credentials(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Test that a warning is logged AND credentials are NOT attached when
    ``VOR_BASE_URL`` carries an insecure ``http://`` scheme.

    2026-05-10 (HTTPS-only Provider URL Drift): the previous behaviour
    only warned but proceeded to attach credentials, which is a
    fail-OPEN posture — an on-path attacker on the HTTP hop captures
    the access ID verbatim. The new fail-CLOSED contract refuses to
    attach credentials so the access ID never reaches the wire over
    plaintext HTTP. Both the warning AND the missing-auth invariant
    are pinned by this test.
    """
    # 1. Setup insecure configuration (HTTP)
    monkeypatch.setattr(vor, "VOR_BASE_URL", "http://insecure.vor.at/api/")
    # Ensure we have credentials set in ENV, because apply_authentication calls refresh_access_credentials
    monkeypatch.setenv("VOR_ACCESS_ID", "secret123")

    # Mock session
    mock_session = MagicMock()
    mock_session.headers = {}
    sentinel_auth = object()
    mock_session.auth = sentinel_auth

    # 2. Call apply_authentication
    with caplog.at_level(logging.WARNING):
        vor.apply_authentication(mock_session)

    # 3. Assert a warning about the HTTP scheme is logged.
    assert "http://" in caplog.text.lower() or "klartext" in caplog.text.lower()

    # 4. Assert NO VorAuth was attached — fail-closed contract.
    assert not isinstance(mock_session.auth, vor.VorAuth)


def test_vor_does_not_warn_on_secure_https(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Test that NO warning is logged when using HTTPS.
    """
    # 1. Setup secure configuration (HTTPS)
    monkeypatch.setattr(vor, "VOR_BASE_URL", "https://secure.vor.at/api/")
    monkeypatch.setenv("VOR_ACCESS_ID", "secret123")

    mock_session = MagicMock()
    mock_session.headers = {}

    # 2. Call apply_authentication
    with caplog.at_level(logging.WARNING):
        vor.apply_authentication(mock_session)

    # 3. Assert NO warning about insecure connection
    assert "insecure HTTP connection" not in caplog.text
    assert "klartext" not in caplog.text.lower()
