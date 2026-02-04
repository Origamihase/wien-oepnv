import pytest
from unittest.mock import MagicMock
import requests
from src.utils.http import fetch_content_safe

@pytest.fixture
def mock_session():
    session = MagicMock(spec=requests.Session)
    return session

@pytest.fixture
def mock_response():
    response = MagicMock(spec=requests.Response)
    response.url = "http://example.com"
    response.status_code = 200
    response.iter_content.return_value = [b"content"]
    # Mock raw.connection.sock.getpeername for verify_response_ip
    # We need to structure it so that getattr(response.raw, "connection") returns an object
    # that has a sock attribute, which has getpeername()

    mock_connection = MagicMock()
    mock_sock = MagicMock()
    mock_sock.getpeername.return_value = ("8.8.8.8", 80) # Safe IP
    mock_connection.sock = mock_sock

    # response.raw is usually a urllib3 response
    response.raw = MagicMock()
    response.raw.connection = mock_connection

    # is_redirect needs to be False for raise_for_status to work typically,
    # but here we mock raise_for_status separately if needed.
    # Actually requests.Response.raise_for_status checks status_code.
    # We mock it to do nothing.
    response.raise_for_status = MagicMock()

    return response

def test_fetch_content_safe_no_validation(mock_session, mock_response):
    """Test that without allowed_content_types, any content type is accepted."""
    mock_response.headers = {"Content-Type": "text/html"}
    mock_session.get.return_value.__enter__.return_value = mock_response

    content = fetch_content_safe(mock_session, "http://example.com")
    assert content == b"content"

def test_fetch_content_safe_valid_json(mock_session, mock_response):
    """Test that matching content type is accepted."""
    mock_response.headers = {"Content-Type": "application/json"}
    mock_session.get.return_value.__enter__.return_value = mock_response

    content = fetch_content_safe(
        mock_session,
        "http://example.com",
        allowed_content_types=["application/json"]
    )
    assert content == b"content"

def test_fetch_content_safe_invalid_type(mock_session, mock_response):
    """Test that mismatching content type raises ValueError."""
    mock_response.headers = {"Content-Type": "text/html"}
    mock_session.get.return_value.__enter__.return_value = mock_response

    with pytest.raises(ValueError, match="Invalid Content-Type"):
        fetch_content_safe(
            mock_session,
            "http://example.com",
            allowed_content_types=["application/json"]
        )

def test_fetch_content_safe_charset(mock_session, mock_response):
    """Test that content type with charset is parsed correctly."""
    mock_response.headers = {"Content-Type": "application/json; charset=utf-8"}
    mock_session.get.return_value.__enter__.return_value = mock_response

    content = fetch_content_safe(
        mock_session,
        "http://example.com",
        allowed_content_types=["application/json"]
    )
    assert content == b"content"

def test_fetch_content_safe_missing_header(mock_session, mock_response):
    """Test that missing header raises ValueError when validation is requested."""
    mock_response.headers = {}
    mock_session.get.return_value.__enter__.return_value = mock_response

    with pytest.raises(ValueError, match="Content-Type header missing"):
        fetch_content_safe(
            mock_session,
            "http://example.com",
            allowed_content_types=["application/json"]
        )

def test_fetch_content_safe_ignore_validation(mock_session, mock_response):
    """Test that missing header is ignored if no validation requested."""
    mock_response.headers = {}
    mock_session.get.return_value.__enter__.return_value = mock_response

    content = fetch_content_safe(mock_session, "http://example.com")
    assert content == b"content"
