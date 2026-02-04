import sys
import logging
import pytest
from unittest.mock import patch

# We need to ensure src.utils.env is NOT already imported, or reload it
if "src.utils.env" in sys.modules:
    del sys.modules["src.utils.env"]
if "src.utils.logging" in sys.modules:
    del sys.modules["src.utils.logging"]

def test_secure_fallback(caplog):
    """Verify that env.py fallback logging masks secrets when utils.logging is missing."""

    # Simulate utils.logging being missing
    with patch.dict(sys.modules, {"src.utils.logging": None, "utils.logging": None}):
        # Import env (it should trigger the ImportError fallback)
        import src.utils.env as env

        # Setup logging capture
        caplog.set_level(logging.WARNING, logger="build_feed")

        # Trigger a warning with sensitive data
        secret_value = "SuperSecretKey123"
        # We simulate a "bad int" env var
        # Note: 'get_int_env' logs the raw value.
        # "Ungültiger Wert für MY_SECRET_KEY='SuperSecretKey123' ..."
        # Our regex should catch MY_SECRET_KEY='...' if we format it right?
        # Wait, get_int_env implementation:
        # logging.warning("... %s=%r ...", name, sanitize_log_message(raw), ...)
        # sanitize_log_message(raw) is called with "SuperSecretKey123".
        # It does NOT see "MY_SECRET_KEY=...".
        # It only sees the value "SuperSecretKey123".

        # If the value itself does not look like "key=value", the regex won't match!
        # The regex I added matches `key=value`.

        # But wait, sanitize_log_message logic:
        # _keys = r"password|token|..."
        # re.sub(rf"(?i)((?:{_keys})[^=:\s]*\s*[:=]\s*)([^&\s]+)", ...)

        # If I pass just "SuperSecretKey123", it won't match anything unless it contains "token=...".

        # However, `read_secret` might be more relevant?
        # No, `read_secret` doesn't log the secret.

        # `get_int_env` logs the value.
        # If the value is "123", it's fine.
        # If the value is "password=123", it will be masked.

        # But what if I have `MY_TOKEN=SecretValue`?
        # `get_int_env("MY_TOKEN", 0)` calls `sanitize_log_message("SecretValue")`.
        # "SecretValue" doesn't match the regex.

        # Ah. The regex relies on seeing the KEY name in the string being sanitized.
        # But `get_int_env` passes the VALUE to be sanitized.
        # So `sanitize_log_message` only sees the value.

        # So my fix only protects against secrets that LOOK like assignments (e.g. connection strings `user=x;pass=y`).
        # It does NOT protect against simple values if the context is lost.

        # However, `get_int_env` logs:
        # "Ungültiger Wert für %s=%r ..." % (name, sanitized_value)
        # So the log message constructed by `warning` contains the name.
        # But `sanitize_log_message` is called on `raw` (the value) BEFORE interpolation.

        # So `raw` is just the value.

        # If I want to protect `MY_TOKEN`, `sanitize_log_message` needs to know it's a secret?
        # But `sanitize_log_message` signature is `(text, secrets)`.
        # `get_int_env` does NOT pass the value as a secret to `sanitize_log_message`.

        # So my fix is partial. It handles structured secrets.
        # But wait, `src/utils/logging.py` also primarily relies on regexes for "key=value" patterns!
        # AND it has `_header_keys` which seem to match header names.

        # Let's check `src/utils/logging.py` again.
        # It has:
        # (rf"(?i)((?:{_keys})(?:%3d|=))([^&\s]+)", r"\1***")

        # So even the "full" logging module wouldn't mask "SuperSecretKey123" if passed as `raw` to `get_int_env`?
        # Unless `SuperSecretKey123` itself matches a pattern?
        # No.

        # But `get_int_env` is for INTEGERS.
        # If I put a secret in an INT env var, I am doing something wrong.
        # But `get_bool_env`? Same.

        # What about `read_secret`?
        # It doesn't log the value.

        # What about `requests` exceptions?
        # `src/utils/http.py`: `_sanitize_url_for_error`.
        # `fetch_content_safe` raises ValueError with `sanitized_url`.
        # `sanitized_url` is result of `_sanitize_url_for_error`.

        # So where is `sanitize_log_message` used critically?
        # In `_log_warning` in `src/providers/vor.py`.
        # `_log_warning(message, *args)`.
        # `sanitized_args = tuple(_sanitize_arg(arg) for arg in args)`.
        # `_sanitize_arg` calls `sanitize_log_message`.

        # If `vor.py` logs an error containing a URL with secrets?
        # `_sanitize_url_for_error` should handle it first.

        # But what if the error message itself contains secrets?
        # e.g. `requests.RequestException` message might contain the URL with secrets if requests decides to put it there?
        # (Usually requests puts URL in property, but str(exc) might have it).

        # If `str(exc)` contains `https://api.vor.at/?accessId=SECRET`.
        # `sanitize_log_message` sees this string.
        # My fallback regex:
        # `_keys = ... accessid ...`
        # `rf"(?i)((?:{_keys})[^=:\s]*\s*[:=]\s*)([^&\s]+)"`
        # Matches `accessId=SECRET`.
        # So it SHOULD mask it.

        # So my test case should use a string that looks like an assignment or URL query param!

        # Let's verify this.

        with patch("os.getenv", return_value="accessId=SuperSecret"):
             # We use get_int_env just to trigger the log call with our string
             env.get_int_env("DUMMY_VAR", 42)

        # Now check logs.
        # We expect "accessId=***"
        assert "accessId=***" in caplog.text
        assert "accessId=SuperSecret" not in caplog.text
