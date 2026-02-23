
from src.utils.logging import sanitize_log_message
from src.utils.http import _sanitize_url_for_error

def test_sanitize_log_spaces_in_keys():
    # "Access Token = secret" (Already covered by access[-_.\s]*token)
    msg = "User provided Access Token = supersecretvalue for login"
    sanitized = sanitize_log_message(msg)
    assert "***" in sanitized
    assert "supersecretvalue" not in sanitized

    # "Api Key = secret" (Matches api[-_.\s]*key)
    msg = "Api Key = myapikeyvalue"
    sanitized = sanitize_log_message(msg)
    assert "***" in sanitized
    assert "myapikeyvalue" not in sanitized

    # "Access Token=secret"
    msg = "Access Token=supersecretvalue"
    sanitized = sanitize_log_message(msg)
    assert "***" in sanitized
    assert "supersecretvalue" not in sanitized

def test_sanitize_log_protocol_specific_keys():
    # OAuth/SAML/Cloud
    sensitive_data = {
        "client_assertion": "eyJh...123",
        "SAMLRequest": "PHNhbW...lp",
        "nonce": "n-0S6_WzA2Mj",
        "Ocp-Apim-Subscription-Key": "a1b2c3d4e5",
    }

    for key, value in sensitive_data.items():
        msg = f"Request with {key}={value} failed"
        sanitized = sanitize_log_message(msg)
        assert value not in sanitized, f"Failed to redact {key}"
        assert "***" in sanitized

def test_sanitize_log_json_escaped():
    # Simulate JSON dump with newline in value
    # {"key": "value\n"} -> "{\"key\": \"value\\n\"}"
    # We want to ensure regex handles escaped chars

    # Simple case: quoted value with escaped quote
    msg = '{"password": "secret\\"value"}'
    sanitized = sanitize_log_message(msg)
    assert "secret" not in sanitized

    # Case with escaped newline (JSON style)
    msg = '{"token": "secret\\nvalue"}'
    sanitized = sanitize_log_message(msg)
    assert "secret" not in sanitized

def test_sanitize_url_fragments():
    # URL fragment: #access_token=...
    url = "https://example.com/callback#access_token=secret_token_123&state=xyz"
    sanitized = _sanitize_url_for_error(url)
    assert "secret_token_123" not in sanitized
    assert "access_token=***" in sanitized or "access_token=%2A%2A%2A" in sanitized

    # Mixed query and fragment
    url2 = "https://example.com/api?id=123#id_token=jwt_token_secret"
    sanitized2 = _sanitize_url_for_error(url2)
    assert "jwt_token_secret" not in sanitized2
    assert "id_token=***" in sanitized2 or "id_token=%2A%2A%2A" in sanitized2

def test_sanitize_log_traceback_simulated():
    # Simulate a traceback string which usually contains newlines and paths
    # We want to ensure secrets in the traceback are masked
    tb = (
        'Traceback (most recent call last):\n'
        '  File "app.py", line 10, in <module>\n'
        '    login(password="secret123")\n'
        'ValueError: Invalid token'
    )
    sanitized = sanitize_log_message(tb, strip_control_chars=False)
    assert "secret123" not in sanitized
    assert "Traceback" in sanitized  # Structure preserved
    assert "\n" in sanitized  # Newlines preserved
