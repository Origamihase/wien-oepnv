import logging
import pytest
from unittest.mock import MagicMock
from src.providers import vor

def test_vor_warns_on_insecure_http_with_credentials(monkeypatch, caplog):
    """
    Test that a warning is logged when VOR credentials are sent over an insecure HTTP connection.
    """
    # 1. Setup insecure configuration (HTTP)
    monkeypatch.setattr(vor, "VOR_BASE_URL", "http://insecure.vor.at/api/")
    # Ensure we have credentials set in ENV, because apply_authentication calls refresh_access_credentials
    monkeypatch.setenv("VOR_ACCESS_ID", "secret123")

    # Mock session
    mock_session = MagicMock()
    mock_session.headers = {}

    # 2. Call apply_authentication
    with caplog.at_level(logging.WARNING):
        vor.apply_authentication(mock_session)

    # 3. Assert warning is logged
    assert "Sending VOR credentials over insecure HTTP connection!" in caplog.text


def test_vor_does_not_warn_on_secure_https(monkeypatch, caplog):
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
